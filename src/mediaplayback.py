# SPDX-License-Identifier: GPL-3.0-or-later

"""Download and stream lifetime for the media viewer.

Owns libsoup file downloads (Save As, Open With) and the playback path the
viewer uses for video, audio and GIFV, which streams the remote URL
straight into the engine (souphttpsrc, Range requests, no temporary
copy): one :class:`mediaengine.Engine` (a GstPlay and its
sink) per viewer, created on the first stream, wrapped for each attachment
in a :class:`mediastream.EngineStream`. Moving between attachments reuses
the engine (``stop()`` then ``load()``); closing the viewer releases it,
deferred (see :mod:`mediaengine`). The chrome and ``present_media`` stay
on :mod:`mediaviewer`; zoom/pan is :mod:`mediazoom`.

Never free the engine directly: ``test-mediaviewer-teardown.py`` drives
the real open/close path to hold that.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import imagecache  # noqa: E402
from . import mastodon  # noqa: E402
from . import pages  # noqa: E402

CHUNK_SIZE = 64 * 1024

# Buffering shows a spinner only once it has lasted this long.
BUFFERING_DELAY_MS = 1000


def _http_session():
    """The session every media request shares, with the app's timeout."""
    return imagecache.session()


def _check_status(message):
    """Raise a GLib.Error when *message* ended in a non-2xx status."""
    status = message.get_status()
    if status < 200 or status >= 300:
        raise GLib.Error.new_literal(
            Gio.io_error_quark(),
            _('The server answered with HTTP {status}').format(
                status=status),
            Gio.IOErrorEnum.FAILED)


def download(url, destination, on_progress=None, on_done=None,
             cancellable=None):
    """Stream *url* into the Gio.File *destination*.

    ``on_progress(fraction)`` gets 0..1 when the size is known, else -1.
    ``on_done(error)`` gets None on success. The destination is replaced
    atomically, so a failed or cancelled download leaves nothing behind.
    """
    _Download(url, destination, on_progress, on_done, cancellable).start()


class _Download:
    def __init__(self, url, destination, on_progress, on_done, cancellable):
        self.url = url
        self.destination = destination
        self.on_progress = on_progress
        self.on_done = on_done
        self.cancellable = cancellable
        self.message = None
        self.source = None
        self.output = None
        self.received = 0
        self.total = 0
        self.finished = False

    def start(self):
        self.message = imagecache.get_message('GET', self.url)
        if self.message is None:
            self.fail(GLib.Error.new_literal(
                Gio.io_error_quark(), _('Invalid media address'),
                Gio.IOErrorEnum.INVALID_ARGUMENT))
            return
        _http_session().send_async(
            self.message, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_sent)

    def fail(self, error):
        if self.finished:
            return
        self.finished = True
        if self.output is not None:
            # Closing with a cancelled cancellable discards the temporary
            # file instead of renaming it over the destination.
            discard = Gio.Cancellable()
            discard.cancel()
            self.output.close_async(GLib.PRIORITY_DEFAULT, discard, None)
        if self.source is not None:
            self.source.close_async(GLib.PRIORITY_DEFAULT, None, None)
        if self.on_done is not None:
            self.on_done(error)

    def succeed(self):
        if self.finished:
            return
        self.finished = True
        if self.on_done is not None:
            self.on_done(None)

    def on_sent(self, session, result):
        try:
            self.source = session.send_finish(result)
            _check_status(self.message)
        except GLib.Error as error:
            self.fail(error)
            return
        self.total = self.message.get_response_headers().get_content_length()
        self.destination.replace_async(
            None, False, Gio.FileCreateFlags.REPLACE_DESTINATION,
            GLib.PRIORITY_DEFAULT, self.cancellable, self.on_opened)

    def on_opened(self, destination, result):
        try:
            self.output = destination.replace_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        self.read()

    def read(self):
        self.source.read_bytes_async(
            CHUNK_SIZE, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_read)

    def on_read(self, source, result):
        try:
            chunk = source.read_bytes_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        if chunk.get_size() == 0:
            self.output.close_async(
                GLib.PRIORITY_DEFAULT, self.cancellable, self.on_closed)
            return
        self.received += chunk.get_size()
        if self.on_progress is not None:
            self.on_progress(self.received / self.total
                             if self.total > 0 else -1)
        self.write(chunk)

    def write(self, chunk):
        self.output.write_bytes_async(
            chunk, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_written, chunk)

    def on_written(self, output, result, chunk):
        try:
            written = output.write_bytes_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        if written < chunk.get_size():
            data = chunk.get_data()[written:]
            self.write(GLib.Bytes.new(data))
            return
        self.read()

    def on_closed(self, output, result):
        try:
            output.close_finish(result)
        except GLib.Error as error:
            self.output = None
            self.fail(error)
            return
        self.output = None
        self.source.close_async(GLib.PRIORITY_DEFAULT, None, None)
        self.succeed()


def _system_state(name):
    """The ``sysstate`` monitor's boolean property *name* ('metered',
    'power-saver'); False when the monitor is unavailable."""
    from . import sysstate
    return bool(sysstate.monitor().get_property(name))


def may_autoplay_loop(widget=None):
    """Whether looping, muted media (a GIFV) may run by itself: animations
    are on, the user has not asked for reduced motion, and the system is
    not saving power. Read live, so the viewer can follow a change."""
    gtk_settings = (Gtk.Settings.get_for_display(widget.get_display())
                    if widget is not None else Gtk.Settings.get_default())
    if not gtk_settings.get_property('gtk-enable-animations'):
        return False
    if gtk_settings.get_property('gtk-interface-reduced-motion') \
            == Gtk.ReducedMotion.REDUCE:
        return False
    return not _system_state('power-saver')


def release_media(bin_widget, stream):
    """Pause *stream*, detach it from its engine and take the media widget
    out of *bin_widget*. The engine stays alive: the viewer stops it
    (navigation) or releases it (close), and it is never freed here."""
    child = bin_widget.get_child()
    if stream is not None:
        stream.pause()
        stream.detach()
    bin_widget.set_child(None)
    del child


# The lowest volume the viewer remembers.
MIN_SAVED_VOLUME = 0.05


class MediaPlayback:
    """Mixin: streaming, GstPlay lifetime and idle inhibition.

    :class:`mediaviewer.MediaViewer` is the widget. These methods need it
    as ``self`` (stack, media bin, spinners, stream state) the same way
    :class:`profileactions.ProfileActions` needs the profile page.
    """

    # The viewer's engine, created by the first stream and released on
    # close.
    _engine = None
    # The current stream has already autoplayed.
    _autoplayed = False
    # The pending source that shows the buffering spinner.
    _buffering_timeout = 0
    # The GIFV playing was paused by the autoplay gate (not by the user),
    # and the gate resumes it when it opens again.
    _gate_paused = False
    # The gate's signals are connected once per viewer.
    _gate_connected = False
    # The last answer of the autoplay check, so only a change acts.
    _gate_open = True
    # The volume the current video or audio stream started at.
    _loaded_volume = None

    def _connect_loop_gate(self):
        """Follow the autoplay check while a GIFV is shown. Connected once
        (the handlers only act on a GIFV, so a later attachment of another
        kind is untouched) and released with the viewer."""
        if self._gate_connected:
            return
        self._gate_connected = True
        from . import sysstate
        pages.connect_weak(sysstate.monitor(), 'notify::power-saver',
                           self._on_loop_gate_changed)
        gtk_settings = Gtk.Settings.get_for_display(self.get_display())
        for name in ('gtk-enable-animations', 'gtk-interface-reduced-motion'):
            pages.connect_weak(gtk_settings, 'notify::' + name,
                               self._on_loop_gate_changed)

    def _on_loop_gate_changed(self, *_args):
        stream = self._stream
        if stream is None or self._stream_kind != 'gifv':
            return
        was_open, self._gate_open = self._gate_open, may_autoplay_loop(self)
        if not stream.is_prepared() or was_open == self._gate_open:
            return
        if self._gate_open:
            if self._gate_paused:
                self._gate_paused = False
                stream.play()
        elif stream.get_playing():
            self._gate_paused = True
            stream.pause()

    def _restore_volume(self, stream):
        """Start a video or audio stream at the remembered volume."""
        volume = pages.settings().get_double('media-viewer-volume')
        self._loaded_volume = volume
        stream.set_volume(volume)

    def _save_volume(self, stream):
        """Remember the volume the user left a video or audio stream at,
        when they changed it. The muted state is not kept."""
        if stream is None or self._stream_kind not in ('video', 'audio') \
                or self._loaded_volume is None:
            return
        volume = min(1.0, max(0.0, stream.get_volume()))
        # A near-silent slider position is not a preference: it would
        # make every later video open silent.
        if volume >= MIN_SAVED_VOLUME \
                and abs(volume - self._loaded_volume) > 1e-6:
            pages.settings().set_double('media-viewer-volume', volume)
        self._loaded_volume = None

    def _unload_media(self):
        self._gate_paused = False
        self._save_volume(self._stream)
        self._set_inhibited(False)
        stream = self._stream
        self._stream = None
        self._stream_kind = None
        release_media(self.media_bin, stream)
        if self._engine is not None:
            # Navigation: keep the GstPlay, the next stream loads into it.
            self._engine.stop()
        self._hide_buffering()
        self.deferred_picture.set_paintable(None)
        self.picture.set_texture(None)
        self._actions['copy-image'].set_enabled(False)

    def _release_engine(self):
        """Closing the viewer: hand the engine to its deferred release."""
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.release()

    def _show_stream(self, attachment, url):
        if _system_state('metered'):
            self._show_deferred(attachment)
        else:
            self._start_stream(attachment, url)

    def _show_deferred(self, attachment):
        """On a metered connection: the preview and a button that starts
        the stream, instead of fetching one nobody asked for. Only the
        start is gated: playback the user began goes on if metering turns
        on, and closing the viewer ends the read."""
        from .mediaviewer import load_texture
        if attachment.get('type') == 'audio':
            self.load_button.set_label(_('Load _Audio'))
        else:
            self.load_button.set_label(_('Load _Video'))
        self.deferred_picture.update_property(
            [Gtk.AccessibleProperty.LABEL], [self._description(attachment)])
        preview = attachment.get('preview_url')
        if preview:
            load_texture(preview, self._on_deferred_preview, self._cancellable)
        self.stack.set_visible_child_name('deferred')
        if self._first_focus:
            self._first_focus = False
            self.load_button.grab_focus()

    def _on_deferred_preview(self, texture, error):
        if error is None:
            self.deferred_picture.set_paintable(texture)

    def _on_load_clicked(self, _button):
        attachment = self.current()
        url = attachment.get('url') or attachment.get('remote_url')
        if url:
            self._start_stream(attachment, url)

    def _start_stream(self, attachment, url):
        """Load the remote *url* into the engine: no download first. The
        spinner page stays until the stream is prepared; an error before
        that shows the error page."""
        self._show_loading()
        try:
            # Imported here: GStreamer is not loaded until media plays.
            from . import mediaengine
            from .mediastream import EngineStream
            if self._engine is None:
                self._engine = mediaengine.Engine(
                    user_agent=mastodon.user_agent())
            stream = EngineStream(self._engine)
            self._engine.load(url)
        except (ImportError, ValueError, RuntimeError):
            self._show_error(
                _('Could Not Load Media'),
                _('Playback is not available on this system.'))
            return
        self._stream = stream
        self._autoplayed = False
        kind = attachment.get('type')
        self._stream_kind = kind
        if kind == 'gifv':
            stream.set_loop(True)
            stream.set_muted(True)
            view = Gtk.Picture(
                paintable=stream, content_fit=Gtk.ContentFit.CONTAIN,
                can_shrink=True, hexpand=True, vexpand=True,
                accessible_role=Gtk.AccessibleRole.IMG)
            view.add_css_class('media-viewer-gifv')
            view.update_property([Gtk.AccessibleProperty.LABEL],
                                 [self._description(attachment)])
            # On release, so dragging the GIFV out leaves it playing.
            click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
            pages.connect_weak(click, 'released', self._on_gifv_clicked,
                               stream)
            view.add_controller(click)
            # A looping animation is decoration: it waits for a click
            # when animations are off or the system saves power.
            # Decided when the stream is prepared, not now.
            autoplay = True
            self._gate_open = may_autoplay_loop(self)
            self._connect_loop_gate()
        elif kind == 'video':
            # Offload lets the compositor scan the video out directly.
            # It applies on Wayland with dmabuf-capable frames; GTK
            # renders normally otherwise.
            view = Gtk.Video(
                hexpand=True, vexpand=True,
                graphics_offload=Gtk.GraphicsOffloadEnabled.ENABLED)
            view.set_media_stream(stream)
            view.add_css_class('media-viewer-video')
            self._restore_volume(stream)
            autoplay = True
        else:
            view = self._audio_view(attachment, stream)
            self._restore_volume(stream)
            autoplay = True
        self.media_bin.set_child(view)
        pages.connect_weak(stream, 'notify::error', self._on_stream_error)
        pages.connect_weak(stream, 'notify::prepared', self._on_stream_prepared,
                           autoplay)
        pages.connect_weak(stream, 'notify::playing', self._on_stream_playing)
        pages.connect_weak(stream, 'notify::ended', self._on_stream_playing)
        pages.connect_weak(stream, 'notify::buffering',
                           self._on_stream_buffering)
        if stream.get_error() is not None:
            self._on_stream_error(stream, None)
        elif stream.is_prepared():
            self._on_stream_prepared(stream, None, autoplay)

    def _audio_view(self, attachment, stream):
        from .mediaviewer import load_texture
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.add_css_class('media-viewer-audio')
        # No width-request anywhere in this pane. The window declares a
        # 360 px floor (window.ui), and a hard minimum above it is not
        # something the clamp can shrink: the control bar was asking for
        # 420 px and was simply clipped on a narrow window. Letting the
        # cover and the icon size from their paintable, and the controls
        # from the clamp's maximum, is what makes the pane fit 360 px and
        # still fill a wide one.
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
            accessible_role=Gtk.AccessibleRole.IMG)
        cover.add_css_class('media-viewer-cover')
        icon = Gtk.Image(icon_name='audio-x-generic-symbolic',
                         pixel_size=128)
        icon.add_css_class('media-viewer-audio-icon')
        icon.set_visible(True)
        cover.set_visible(False)
        preview = attachment.get('preview_url')
        if preview:
            def on_cover(texture, error):
                if error is None:
                    cover.set_paintable(texture)
                    cover.set_visible(True)
                    icon.set_visible(False)
            load_texture(preview, on_cover, self._cancellable)
        controls = Gtk.MediaControls(media_stream=stream)
        clamp = Adw.Clamp(maximum_size=520, tightening_threshold=400,
                          child=controls)
        box.append(cover)
        box.append(icon)
        box.append(clamp)
        return box

    def _on_gifv_clicked(self, gesture, n_press, x, y, stream):
        # The user decides now: whatever the gate paused stays theirs.
        self._gate_paused = False
        if stream.get_playing():
            stream.pause()
        else:
            stream.play()

    def _on_stream_error(self, stream, _pspec):
        if stream is not self._stream:
            return
        self._hide_buffering()
        self._set_inhibited(False)
        error = stream.get_error()
        self._show_error(
            _('Could Not Load Media'),
            error.message if error is not None else '')

    def _on_stream_buffering(self, stream, _pspec):
        if stream is not self._stream:
            return
        if not stream.get_property('buffering'):
            self._hide_buffering()
        elif not self._buffering_timeout:
            # A short stall is not worth a spinner.
            self._buffering_timeout = GLib.timeout_add(
                BUFFERING_DELAY_MS, self._on_buffering_timeout)

    def _on_buffering_timeout(self):
        self._buffering_timeout = 0
        stream = self._stream
        if stream is not None and stream.get_property('buffering'):
            self.buffering_spinner.set_visible(True)
        return False

    def _hide_buffering(self):
        if self._buffering_timeout:
            GLib.source_remove(self._buffering_timeout)
            self._buffering_timeout = 0
        self.buffering_spinner.set_visible(False)

    def _on_stream_playing(self, stream, _pspec):
        if stream is not self._stream:
            return
        # A muted, looping GIFV is no reason to keep the screen on.
        self._set_inhibited(
            self._stream_kind in ('video', 'audio')
            and stream.get_playing() and not stream.get_ended())

    def _application(self):
        app = self.window.get_application() if self.window is not None \
            else None
        return app if app is not None else Gio.Application.get_default()

    def _set_inhibited(self, inhibited):
        """Keep the session from going idle (dimming, locking, suspend)
        while *inhibited*; every path that stops playback or drops the
        stream clears it, so the cookie never outlives the playback."""
        if inhibited == bool(self._inhibit_cookie):
            return
        if not inhibited:
            app, cookie = self._inhibit_app, self._inhibit_cookie
            self._inhibit_app, self._inhibit_cookie = None, 0
            app.uninhibit(cookie)
            return
        app = self._application()
        if not isinstance(app, Gtk.Application):
            return
        # Translators: why the session is kept from going idle, shown by
        # the desktop where it lists apps blocking suspend or screen lock.
        reason = _('Playing media')
        cookie = app.inhibit(self.window, Gtk.ApplicationInhibitFlags.IDLE,
                             reason)
        if cookie:
            self._inhibit_app, self._inhibit_cookie = app, cookie

    def _on_stream_prepared(self, stream, _pspec, autoplay):
        # gerror() also prepares a stream; an error is not a media page.
        if stream is not self._stream or not stream.is_prepared() \
                or stream.get_error() is not None:
            return
        self.stack.set_visible_child_name('media')
        # Only the first prepare autoplays: a stream prepared again (a
        # duration that arrived late) must not restart a paused video.
        if autoplay and not self._autoplayed:
            self._autoplayed = True
            if self._stream_kind != 'gifv':
                stream.play()
            else:
                self._gate_open = may_autoplay_loop(self)
                if self._gate_open:
                    stream.play()
                else:
                    # Held by the gate: it starts when the gate opens.
                    self._gate_paused = True
        if self._first_focus:
            self._first_focus = False
            child = self.media_bin.get_child()
            if child is not None:
                child.grab_focus()
