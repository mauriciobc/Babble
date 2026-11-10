# SPDX-License-Identifier: GPL-3.0-or-later

"""A ``Gtk.MediaStream`` that plays through a :class:`mediaengine.Engine`.

``Gtk.Video``, ``Gtk.MediaControls`` and a ``Gtk.Picture`` showing a GIFV
all take a ``Gtk.MediaStream``. This one forwards play, pause, seek and
audio to the engine's GstPlay and draws the engine's
``gtk4paintablesink`` paintable, so the widgets stay as they were.

* **Prepared from the preroll**, once: ``load()`` prerolls, and the first
  media info says which streams exist and whether the source is seekable.
  A streamed source may not know its duration yet, so the stream waits for
  it up to ``DURATION_WAIT_MS`` after the media info, then prepares with 0.
  A clip prepared with an unknown duration keeps duration 0, so the seek
  bar is inactive for it. It is never prepared again; a later duration
  only updates the stored value (the end position), not
  ``Gtk.MediaStream.get_duration()``.
* **Buffering** is a property: true while the engine reports BUFFERING
  after the stream is prepared.
* **No seek on a source that cannot seek**: GstPlay would error out
  ("Could not seek in resource"), so ``do_seek`` calls ``seek_failed()``
  and never reaches the engine.
* **After ``detach()`` every vfunc is a no-op**, so a widget that still
  holds the stream cannot reach a GstPlay that is being released.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('GstPlay', '1.0')

from .i18n import gettext as _  # noqa: E402


from gi.repository import Gio, GLib, GObject, GstPlay, Gtk  # noqa: E402

# A seek that never reports completion is given up on after this long.
SEEK_FALLBACK_MS = 2000

# How long after the first media info the stream waits for a duration
# before it is prepared with none.
DURATION_WAIT_MS = 300

# A position within this of the seek target counts as the seek having landed.
SEEK_TOLERANCE_US = 300000

# Gst.CLOCK_TIME_NONE: the duration is not known (yet).
_CLOCK_TIME_NONE = 0xFFFFFFFFFFFFFFFF

_CODECS_EXTRA_MARKERS = ('h264', 'h.264', 'avc', 'hevc', 'h265', 'h.265')


def missing_plugin_message(description, detail):
    """The user-facing text for a missing codec. H.264 and HEVC need the
    ``codecs-extra`` extension of the Flatpak runtime."""
    text = ('%s %s' % (description or '', detail or '')).lower()
    if any(marker in text for marker in _CODECS_EXTRA_MARKERS):
        # Translators: shown when a video cannot be played because the
        # system lacks its decoder. "codecs-extra" is the name of the
        # Flatpak extension and is not translated.
        return _('This video uses a format that needs the codecs-extra '
                 'extension. Install it and try again.')
    # Translators: {codec} is the name of the missing decoder, as GStreamer
    # words it, for example "Vorbis audio decoder".
    return _('This media needs a codec that is not installed: {codec}').format(
        codec=description or '?')


def _to_us(nanoseconds):
    """Nanoseconds from GstPlay as microseconds; 0 when unknown (the
    unsigned CLOCK_TIME_NONE, or anything negative)."""
    if nanoseconds is None or nanoseconds < 0 \
            or nanoseconds >= _CLOCK_TIME_NONE:
        return 0
    return nanoseconds // 1000


class EngineStream(Gtk.MediaStream):
    __gtype_name__ = 'BabbleEngineStream'

    # The engine is rebuffering a prepared stream.
    buffering = GObject.Property(type=bool, default=False)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._seekable = False
        self._seeking = False
        self._seek_target_us = 0
        self._seek_timeout = 0
        self._at_end = False
        self._duration_us = 0
        self._failed = False
        self._has_audio = False
        self._has_video = False
        # The first media info arrived and the prepare is waiting for a
        # duration.
        self._pending_prepare = False
        self._prepare_timeout = 0
        # A media info for this stream's URI arrived: only then can a
        # duration event be tied to it (one has no URI of its own).
        self._info_seen = False
        engine.owner = self
        # A reused engine keeps the last stream's audio (a GIFV mutes
        # it); a setter that changes nothing emits nothing, so start from
        # this stream's own state.
        engine.set_audio(self.get_muted(), self.get_volume())

    # Lifetime

    def detach(self):
        """Let go of the engine. The owner of the engine releases it."""
        engine, self.engine = self.engine, None
        if self._seek_timeout:
            GLib.source_remove(self._seek_timeout)
            self._seek_timeout = 0
        self._cancel_prepare_timeout()
        self.buffering = False
        if engine is not None and engine.owner is self:
            engine.owner = None

    # Events from the engine

    def _engine_event(self, name, *args):
        if self.engine is None:
            return
        getattr(self, '_on_' + name.replace('-', '_'))(*args)

    def _on_media_info(self, info):
        # A stale message from the media loaded before this one.
        if info.get_uri() != self.engine.uri:
            return
        self._info_seen = True
        duration = _to_us(info.get_duration()) or self._duration_us
        if self.is_prepared():
            self._set_duration(duration)
            return
        self._seekable = bool(info.is_seekable())
        self._has_audio = bool(info.get_audio_streams())
        self._has_video = bool(info.get_video_streams())
        if duration > 0:
            self._duration_us = duration
            self._prepare()
        elif not self._pending_prepare:
            self._pending_prepare = True
            self._prepare_timeout = GLib.timeout_add(
                DURATION_WAIT_MS, self._on_prepare_timeout)

    def _prepare(self):
        self._pending_prepare = False
        self._cancel_prepare_timeout()
        self.stream_prepared(self._has_audio, self._has_video,
                             self._seekable, self._duration_us)
        if self.engine is not None:
            # A slow start is already buffering.
            self.buffering = (
                self.engine.state == GstPlay.PlayState.BUFFERING)

    def _cancel_prepare_timeout(self):
        if self._prepare_timeout:
            GLib.source_remove(self._prepare_timeout)
            self._prepare_timeout = 0

    def _on_prepare_timeout(self):
        self._prepare_timeout = 0
        if self._pending_prepare and not self.is_prepared():
            self._prepare()
        return False

    def _on_state_changed(self, state):
        self.buffering = (self.is_prepared()
                          and state == GstPlay.PlayState.BUFFERING)

    def _on_duration(self, duration):
        if not self._info_seen:
            return
        self._set_duration(_to_us(duration))

    def _set_duration(self, duration):
        """A duration reported by the engine. Before the prepare it ends
        the wait; after it, it only updates the stored value."""
        if duration <= 0:
            return
        self._duration_us = duration
        if self._pending_prepare and not self.is_prepared():
            self._prepare()

    def _on_position(self, position):
        # The fallback for a seek-done that never comes: only a position
        # near the target says the seek landed, not one from before it.
        if self._seeking and abs(position // 1000
                                 - self._seek_target_us) <= SEEK_TOLERANCE_US:
            self._seek_done(position)
        # update() needs a prepared stream; a streamed source may report
        # positions before its media info.
        if self.is_prepared():
            self.update(position // 1000)

    def _on_seek_done(self, position):
        self._seek_done(position)

    def _seek_done(self, position):
        if not self._seeking:
            return
        self._seeking = False
        if self._seek_timeout:
            GLib.source_remove(self._seek_timeout)
            self._seek_timeout = 0
        self.seek_success()
        if self.is_prepared():
            self.update(position // 1000)

    def _on_seek_timeout(self):
        self._seek_timeout = 0
        if self._seeking:
            self._seeking = False
            self.seek_success()
        return False

    def _on_end_of_stream(self):
        if not self.is_prepared():
            # A clip shorter than the duration wait: prepare, then end.
            self._prepare()
        if self.get_loop():
            self.engine.seek(0)
            self.engine.start()
            return
        self._at_end = True
        self.update(self._duration_us)
        self.stream_ended()

    def _on_error(self, error):
        if not self._failed:
            self._failed = True
            self.gerror(error)

    def _on_missing_plugin(self, description, detail):
        if self._failed:
            return
        self._failed = True
        self.gerror(GLib.Error.new_literal(
            Gio.io_error_quark(),
            missing_plugin_message(description, detail),
            Gio.IOErrorEnum.NOT_SUPPORTED))

    def _on_frame(self):
        self.invalidate_contents()

    def _on_size(self):
        self.invalidate_size()

    # Gtk.MediaStream vfuncs

    def do_play(self):
        if self.engine is None or self._failed:
            return False
        if self._at_end:
            self._at_end = False
            self.engine.seek(0)
        self.engine.start()
        return True

    def do_pause(self):
        if self.engine is not None:
            self.engine.pause()

    def do_seek(self, timestamp):
        if self.engine is None:
            return
        if not self._seekable:
            self.seek_failed()
            return
        self._at_end = False
        self._seeking = True
        self._seek_target_us = timestamp
        if self._seek_timeout:
            GLib.source_remove(self._seek_timeout)
        self._seek_timeout = GLib.timeout_add(
            SEEK_FALLBACK_MS, self._on_seek_timeout)
        self.engine.seek(timestamp * 1000)

    def do_update_audio(self, muted, volume):
        if self.engine is not None:
            self.engine.set_audio(muted, volume)

    # Gdk.Paintable vfuncs: forwarded to the sink's paintable

    def do_snapshot(self, snapshot, width, height):
        if self.engine is not None and self.engine.paintable is not None:
            self.engine.paintable.snapshot(snapshot, width, height)

    def do_get_intrinsic_width(self):
        if self.engine is None or self.engine.paintable is None:
            return 0
        return self.engine.paintable.get_intrinsic_width()

    def do_get_intrinsic_height(self):
        if self.engine is None or self.engine.paintable is None:
            return 0
        return self.engine.paintable.get_intrinsic_height()

    def do_get_intrinsic_aspect_ratio(self):
        if self.engine is None or self.engine.paintable is None:
            return 0.0
        return self.engine.paintable.get_intrinsic_aspect_ratio()
