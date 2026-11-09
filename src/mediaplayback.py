# SPDX-License-Identifier: GPL-3.0-or-later

"""Download and stream lifetime for the media viewer.

Owns libsoup file downloads and the playback path the viewer uses for
video, audio and GIFV: one :class:`mediaengine.Engine` (a GstPlay and its
sink) per viewer, created on the first stream, wrapped for each attachment
in a :class:`mediastream.EngineStream`. Moving between attachments reuses
the engine (``stop()`` then ``load()``); closing the viewer releases it,
deferred (see :mod:`mediaengine`). The chrome and ``present_media`` stay
on :mod:`mediaviewer`; zoom/pan is :mod:`mediazoom`.

Never free the engine directly: ``test-mediaviewer-teardown.py`` drives
the real open/close path to hold that.
"""

import os
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import imagecache  # noqa: E402
from . import pages  # noqa: E402

CHUNK_SIZE = 64 * 1024


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


class MediaPlayback:
    """Mixin: stream download, GstPlay lifetime and idle inhibition.

    :class:`mediaviewer.MediaViewer` is the widget. These methods need it
    as ``self`` (stack, media bin, progress, stream state) the same way
    :class:`profileactions.ProfileActions` needs the profile page.
    """

    # The viewer's engine, created by the first stream and released on
    # close.
    _engine = None
    # The current stream has already autoplayed.
    _autoplayed = False

    def _unload_media(self):
        self._set_inhibited(False)
        stream = self._stream
        self._stream = None
        self._stream_kind = None
        release_media(self.media_bin, stream)
        if self._engine is not None:
            # Navigation: keep the GstPlay, the next stream loads into it.
            self._engine.stop()
        media = self._temp_media
        self._temp_media = None
        if media is not None:
            self._discard_temp(media)
        self.deferred_picture.set_paintable(None)
        self.picture.set_texture(None)
        self._actions['copy-image'].set_enabled(False)

    def _release_engine(self):
        """Closing the viewer: hand the engine to its deferred release."""
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.release()

    def _discard_temp(self, media):
        # Fire and forget: deleting a cancelled download's copy fails
        # silently and the empty directory is gone with the sandbox.
        media.delete_async(GLib.PRIORITY_DEFAULT, None, None)
        directory = media.get_parent()
        if directory is not None:
            directory.delete_async(GLib.PRIORITY_DEFAULT, None, None)

    def _show_stream(self, attachment, url):
        if _system_state('metered'):
            self._show_deferred(attachment)
        else:
            self._download_stream(attachment, url)

    def _show_deferred(self, attachment):
        """On a metered connection: the preview and a button that starts
        the download, instead of fetching a stream nobody asked for."""
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
            self._download_stream(attachment, url)

    def _download_stream(self, attachment, url):
        self._show_loading()
        cancellable = self._cancellable
        try:
            directory = GLib.dir_make_tmp('babble-media-XXXXXX')
        except GLib.Error as error:
            self._show_error(_('Could Not Load Media'), error.message)
            return
        # Download the media with libsoup, like every other fetch in the
        # app, and play the local, seekable copy: gvfs's http backend is
        # unavailable inside a Flatpak sandbox, and a downloaded file
        # always seeks.
        path = os.path.basename(urllib.parse.unquote(
            urllib.parse.urlsplit(url).path)).replace(os.sep, '_').strip()
        destination = Gio.File.new_for_path(
            os.path.join(directory, path or 'media'))
        self._temp_media = destination
        self.progress.set_fraction(0)
        self.progress.set_visible(True)

        def progress(fraction):
            if self._closed:
                return
            if fraction < 0:
                self.progress.pulse()
            else:
                self.progress.set_fraction(fraction)

        def download_done(error):
            if cancellable is not self._cancellable \
                    or cancellable.is_cancelled():
                # The media was replaced or the viewer closed while the
                # download ran; the temp copy is discarded by the unload.
                return
            self.progress.set_visible(False)
            if error is None:
                self._start_stream(attachment, destination)
            else:
                self._show_error(_('Could Not Load Media'), error.message)

        download(url, destination, progress, download_done, cancellable)

    def _start_stream(self, attachment, destination):
        try:
            # Imported here: GStreamer is not loaded until media plays.
            from . import mediaengine
            from .mediastream import EngineStream
            if self._engine is None:
                self._engine = mediaengine.Engine()
            stream = EngineStream(self._engine)
            self._engine.load(destination.get_uri())
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
            autoplay = Gtk.Settings.get_default().get_property(
                'gtk-enable-animations') and not _system_state('power-saver')
        elif kind == 'video':
            view = Gtk.Video(hexpand=True, vexpand=True)
            view.set_media_stream(stream)
            view.add_css_class('media-viewer-video')
            autoplay = True
        else:
            view = self._audio_view(attachment, stream)
            autoplay = True
        self.media_bin.set_child(view)
        pages.connect_weak(stream, 'notify::error', self._on_stream_error)
        pages.connect_weak(stream, 'notify::prepared', self._on_stream_prepared,
                           autoplay)
        pages.connect_weak(stream, 'notify::playing', self._on_stream_playing)
        pages.connect_weak(stream, 'notify::ended', self._on_stream_playing)
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
        if stream.get_playing():
            stream.pause()
        else:
            stream.play()

    def _on_stream_error(self, stream, _pspec):
        if stream is not self._stream:
            return
        self._set_inhibited(False)
        error = stream.get_error()
        self._show_error(
            _('Could Not Load Media'),
            error.message if error is not None else '')

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
        if stream is not self._stream or not stream.is_prepared():
            return
        self.stack.set_visible_child_name('media')
        # Only the first prepare autoplays: a stream prepared again (a
        # duration that arrived late) must not restart a paused video.
        if autoplay and not self._autoplayed:
            self._autoplayed = True
            stream.play()
        if self._first_focus:
            self._first_focus = False
            child = self.media_bin.get_child()
            if child is not None:
                child.grab_focus()
