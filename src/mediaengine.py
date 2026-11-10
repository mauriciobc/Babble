# SPDX-License-Identifier: GPL-3.0-or-later

"""The playback engine of one media viewer: a GstPlay and its video sink.

Freeing a GstPlay can crash: SIGSEGV in
``g_type_check_instance_is_fundamentally_a`` under ``gst_message_new_custom``
in libgstplay. Measured, not explained: freeing it right after
``pause()`` then ``stop()`` under a ``Gtk.MediaStream`` in a ``Gtk.Video``
crashed 6 of 7 twelve-cycle runs, while a plain ``stop()`` then free
survived 500 cycles; the real viewer on ``main`` crashed 4 times in about
415 open/close cycles. The precise trigger is not identified, and waiting
for STOPPED removed the crash in every variant tested. So an
:class:`Engine` is never dropped directly:

* ``release()`` stops the pipeline, waits for the adapter to report
  ``STOPPED``, disconnects every handler and drops the references from an
  idle callback;
* until then the engine sits in ``_releasing``, so it outlives the viewer
  that closed it;
* at quit :func:`shutdown_all` does the same synchronously, turning the
  main context until ``STOPPED``, because the interpreter would otherwise
  free a playing GstPlay during teardown.

Handlers are connected once, in ``__init__``, and their ids are kept so
that release can undo every one of them. Events reach the owner by a
method call, ``owner._engine_event(name, *args)``; a closure that
captured the owner would leak it (see MEMORY-LEAKS.md).

This module imports only ctypes (for ``malloc_trim``), Gst, GstPlay,
GstPbutils, GLib and Gtk.
"""

import ctypes
import gc
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
gi.require_version('GstPbutils', '1.0')

from gi.repository import GLib, Gst, GstPbutils, GstPlay, Gtk  # noqa: E402,F401

# How long release waits for STOPPED before dropping the engine anyway.
STOP_TIMEOUT_MS = 1000

# The idle source that returns freed heap to the system, or 0.
_trim_source = 0


def _trim_heap():
    global _trim_source
    _trim_source = 0
    try:
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass
    return False


def schedule_trim():
    """Give the heap an engine freed back to the system, once, from an
    idle. A pipeline's streaming threads leave their memory in glibc
    arenas, and each load and stop on the same engine added about 16 MB
    of resident memory that ``malloc_trim`` takes back (measured; about
    2 ms)."""
    global _trim_source
    if not _trim_source:
        _trim_source = GLib.idle_add(_trim_heap)


# Engines that were asked to release and have not been dropped yet. A
# strong reference: the engine must outlive the viewer that closed it.
_releasing = set()
# Every engine not yet dropped, for shutdown_all().
_all = set()


class Engine:
    """One GstPlay with its ``gtk4paintablesink``, owned by one viewer.

    ``owner`` gets ``_engine_event(name, *args)`` for: ``state-changed``
    (a ``GstPlay.PlayState``), ``position`` / ``duration`` (nanoseconds),
    ``media-info`` (a ``PlayMediaInfo``), ``seek-done`` (position),
    ``end-of-stream``, ``error`` (a ``GLib.Error``), ``missing-plugin``
    (description, installer detail), ``frame`` and ``size``.

    *user_agent*, when given, is set on the HTTP source of a streamed
    URI: reverse proxies refuse the library default. *gl* is false for
    inline GIFVs, which keep their frames in system memory.
    """

    def __init__(self, owner=None, user_agent=None, gl=True):
        Gst.init(None)
        sink = Gst.ElementFactory.make('gtk4paintablesink')
        if sink is None:
            raise RuntimeError('gtk4paintablesink is not available')
        self.owner = owner
        self.user_agent = user_agent
        self.sink = sink
        self.paintable = sink.props.paintable
        outer = sink
        # *gl* false keeps frames in system memory (no glsinkbin): a GIFV
        # is small and decodes in software, and the GL path measured
        # about 45 MB more resident memory in the timeline benchmark.
        if gl and self.paintable.props.gl_context:
            gl_sink = Gst.ElementFactory.make('glsinkbin')
            if gl_sink is not None:
                gl_sink.props.sink = sink
                outer = gl_sink
        self.play = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(
                None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play)
        self.uri = None
        self.state = GstPlay.PlayState.STOPPED
        self.released = False
        self._ever_loaded = False
        # stop() asked and its STOPPED not reported yet.
        self._stopping = False
        # load() done and no state change reported since.
        self._fresh = False
        # A load that arrived while a stop was in flight.
        self._pending_uri = None
        self._finished = False
        # How the engine got dropped: 'stopped' (STOPPED arrived),
        # 'settled' (already STOPPED), 'timeout' or 'never-loaded'.
        self.how = None
        self._timeout = 0
        self._stop_timeout = 0
        self._bus = None
        self._handlers = []
        self._ua_handlers = []
        _all.add(self)

        self._connect(self.adapter, 'state-changed', self._on_state)
        self._connect(self.adapter, 'position-updated', self._on_position)
        self._connect(self.adapter, 'duration-changed', self._on_duration)
        self._connect(self.adapter, 'media-info-updated', self._on_media_info)
        self._connect(self.adapter, 'seek-done', self._on_seek_done)
        self._connect(self.adapter, 'end-of-stream', self._on_end)
        self._connect(self.adapter, 'error', self._on_error)
        self._connect(self.paintable, 'invalidate-contents', self._on_frame)
        self._connect(self.paintable, 'invalidate-size', self._on_size)
        pipeline = self.play.get_pipeline()
        # These two outlive _disconnect_all: a source created while the
        # engine winds down (a load in flight when it is released) must
        # still be named. They set one property from a string and reach
        # neither the owner nor anything else, so they cannot leak.
        self._ua_handlers = [
            (pipeline, pipeline.connect('source-setup',
                                        self._on_source_setup)),
            (pipeline, pipeline.connect('deep-element-added',
                                        self._on_deep_element))]
        bus = pipeline.get_bus()
        if bus is not None:
            bus.add_signal_watch()
            self._bus = bus
            self._connect(bus, 'message', self._on_bus_message)

    def _connect(self, obj, signal, callback):
        self._handlers.append((obj, obj.connect(signal, callback)))

    def _disconnect_all(self):
        handlers, self._handlers = self._handlers, []
        for obj, handler_id in handlers:
            try:
                obj.disconnect(handler_id)
            except (TypeError, RuntimeError):
                pass
        if self._bus is not None:
            self._bus.remove_signal_watch()
            self._bus = None

    def _emit(self, name, *args):
        owner = self.owner
        if owner is not None:
            owner._engine_event(name, *args)

    # Handlers: each only relays, so none holds the owner.

    def _on_state(self, _adapter, state):
        self.state = state
        stopped = state == GstPlay.PlayState.STOPPED
        if stopped:
            self._stopping = False
            self._clear_stop_timeout()
            schedule_trim()
        self._fresh = False
        if self.released:
            if stopped:
                self._finish('stopped')
            return
        if stopped and self._pending_uri is not None:
            # The stop this load waited for is done; a stale STOPPED can
            # never arrive after the new load, because it started here.
            uri, self._pending_uri = self._pending_uri, None
            self._start_load(uri)
            return
        self._emit('state-changed', state)

    def _on_position(self, _adapter, position):
        self._emit('position', position)

    def _on_duration(self, _adapter, duration):
        self._emit('duration', duration)

    def _on_media_info(self, _adapter, info):
        self._emit('media-info', info)

    def _on_seek_done(self, _adapter, position):
        self._emit('seek-done', position)

    def _on_end(self, _adapter):
        self._emit('end-of-stream')

    def _on_error(self, _adapter, error, _details):
        # A load that fails may never report a state change.
        self._fresh = False
        self._emit('error', error)

    def _on_frame(self, _paintable):
        self._emit('frame')

    def _on_size(self, _paintable):
        self._emit('size')

    def _on_source_setup(self, _pipeline, source):
        # Runs on a GStreamer thread: set a property and touch nothing else.
        agent = self.user_agent
        if agent and source.find_property('user-agent') is not None:
            source.set_property('user-agent', agent)

    def _on_deep_element(self, _pipeline, _bin, element):
        # A GStreamer thread again: only set the property.
        agent = self.user_agent
        if agent and element.find_property('user-agent') is not None:
            element.set_property('user-agent', agent)

    def _on_bus_message(self, _bus, message):
        # GstPlay watches this bus from its own thread, so this handler
        # can run there: extract what is needed and hand the rest to the
        # main loop, like Showtime does.
        if GstPbutils.is_missing_plugin_message(message):
            GLib.idle_add(
                self._emit_missing,
                GstPbutils.missing_plugin_message_get_description(message),
                GstPbutils.missing_plugin_message_get_installer_detail(
                    message))

    def _emit_missing(self, description, detail):
        if not self._finished:
            self._emit('missing-plugin', description, detail)
        return False

    # Control

    @property
    def active(self):
        return not self.released and self.play is not None

    def _settled(self):
        """STOPPED is reported and nothing is in flight: no stop pending
        and no load whose first state change has not arrived."""
        return (self.state == GstPlay.PlayState.STOPPED
                and not self._stopping and not self._fresh)

    def load(self, uri):
        """Prepare *uri* and preroll it: ``pause()`` after ``set_uri``,
        because the stream only becomes prepared from the preroll. While a
        stop is in flight the load waits for its STOPPED."""
        if not self.active:
            return
        # Set now, so a message still queued from the previous media no
        # longer matches the URI a new stream checks against.
        self.uri = uri
        if self._stopping:
            self._pending_uri = uri
            return
        self._start_load(uri)

    def _start_load(self, uri):
        if self.released or self.play is None:
            return
        self.uri = uri
        self._ever_loaded = True
        self._fresh = True
        self.play.set_uri(uri)
        self.play.pause()

    def start(self):
        if self.active:
            self.play.play()

    def pause(self):
        if self.active:
            self.play.pause()

    def seek(self, position_ns):
        if self.active:
            self.play.seek(position_ns)

    def set_audio(self, muted, volume):
        if self.active:
            self.play.set_mute(muted)
            self.play.set_volume(volume)

    def _request_stop(self):
        if self._stopping or self._settled():
            return
        self._stopping = True
        self.play.stop()

    def stop(self):
        """Stop the pipeline but keep the engine, for the next ``load``."""
        if not self.active:
            return
        self.owner = None
        self._request_stop()
        if self._stopping and not self._stop_timeout:
            self._stop_timeout = GLib.timeout_add(
                STOP_TIMEOUT_MS, self._on_stop_timeout)

    def _clear_stop_timeout(self):
        if self._stop_timeout:
            GLib.source_remove(self._stop_timeout)
            self._stop_timeout = 0

    def _on_stop_timeout(self):
        # STOPPED never came: do not hold a waiting load forever.
        self._stop_timeout = 0
        self._stopping = False
        self._fresh = False
        if self._pending_uri is not None and not self.released:
            uri, self._pending_uri = self._pending_uri, None
            self._start_load(uri)
        return False

    # Deferred release

    def release(self):
        """Stop, wait for STOPPED, disconnect, and drop the references
        from an idle callback. Safe to call twice."""
        if self.released:
            return
        self.released = True
        self.owner = None
        self._pending_uri = None
        self._clear_stop_timeout()
        _releasing.add(self)
        if self._settled():
            self._finish('settled' if self._ever_loaded else 'never-loaded')
            return
        self._timeout = GLib.timeout_add(STOP_TIMEOUT_MS, self._on_timeout)
        self._request_stop()

    def _on_timeout(self):
        self._timeout = 0
        self._finish('timeout')
        return False

    def _finish(self, how, immediate=False):
        if self._finished:
            if immediate:
                self._drop()
            return
        self._finished = True
        self.how = how
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        self._clear_stop_timeout()
        self._disconnect_all()
        if immediate:
            self._drop()
        else:
            GLib.idle_add(self._drop_idle)

    def _drop_idle(self):
        self._drop()
        return False

    def _drop(self):
        handlers, self._ua_handlers = self._ua_handlers, []
        for obj, handler_id in handlers:
            try:
                obj.disconnect(handler_id)
            except (TypeError, RuntimeError):
                pass
        self.play = self.adapter = self.sink = self.paintable = None
        _releasing.discard(self)
        _all.discard(self)
        schedule_trim()


def shutdown_all(timeout_s=1.0):
    """Stop every engine and wait (bounded) for STOPPED, turning the main
    context so the adapter can report it, then drop them all. Called from
    ``do_shutdown``: a playing GstPlay freed by interpreter teardown
    crashed 17 quits in 50."""
    engines = [e for e in _all if e.play is not None]
    for engine in engines:
        engine.released = True
        engine.owner = None
        engine._pending_uri = None
        engine._clear_stop_timeout()
        engine._request_stop()
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # An engine that reports STOPPED finishes itself in _on_state.
        if all(e._finished or e._settled() for e in engines):
            break
        if not context.iteration(False):
            time.sleep(0.002)
    for engine in engines:
        engine._finish(
            'stopped' if engine._settled() else 'timeout', immediate=True)
    gc.collect()
