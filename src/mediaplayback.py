# SPDX-License-Identifier: GPL-3.0-or-later

"""Stream lifetime for the media viewer.

Owns the playback path the viewer uses for video, audio and GIFV, which
streams the remote URL straight into the engine (souphttpsrc, Range
requests, no temporary copy): one :class:`mediaengine.Engine` (a GstPlay
and its sink) per viewer, created on the first stream, wrapped for each
attachment in a :class:`mediastream.EngineStream`. Moving between
attachments reuses the engine (``stop()`` then ``load()``); closing the
viewer releases it, deferred (see :mod:`mediaengine`). Libsoup file
downloads (Save As, Open With) live in :mod:`mediaexport`. The chrome and
``present_media`` stay on :mod:`mediaviewer`; zoom/pan is :mod:`mediazoom`.

Whether a stream may start by itself and whether a GIFV may loop are the
questions of :mod:`mediapolicy`; this module asks it and acts on the
answer (the reason strings are the viewer's own).

Never free the engine directly: ``test-mediaviewer-teardown.py`` drives
the real open/close path to hold that.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import mastodon  # noqa: E402
from . import mediapolicy  # noqa: E402
from . import pages  # noqa: E402
from .remotemedia import media_url  # noqa: E402

# Buffering shows a spinner only once it has lasted this long.
BUFFERING_DELAY_MS = 1000


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
        mediapolicy.connect_changes(self._on_loop_gate_changed, self)

    def _loop_gate_open(self):
        """The loop rule of the media policy, for this viewer's display."""
        return mediapolicy.may_loop(mediapolicy.read_state(self))

    def _on_loop_gate_changed(self, *_args):
        # Called for every signal the policy lists, not only the loop
        # rule's: it acts on a GIFV only, recomputes, and does nothing
        # unless the answer changed.
        stream = self._stream
        if stream is None or self._stream_kind != 'gifv':
            return
        was_open, self._gate_open = self._gate_open, self._loop_gate_open()
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

    def _show_stream(self, attachment, url, is_remote):
        deferral = mediapolicy.stream_deferral(mediapolicy.read_state(self))
        if deferral == mediapolicy.DEFER_NEVER:
            self._show_deferred(attachment, False)
        elif deferral == mediapolicy.DEFER_METERED:
            self._show_deferred(attachment, True)
        else:
            self._gate_remote('show', attachment, url, is_remote)

    def _show_deferred(self, attachment, metered=True):
        """The preview and a button that starts the stream, instead of
        fetching one nobody asked for: on a metered connection
        (*metered*), or because the user chose never to load media
        automatically. Only the start is gated: playback the user began
        goes on if metering turns on, and closing the viewer ends the
        read."""
        from .mediaviewer import load_texture
        if metered:
            reason = _('Media is not downloaded automatically on a metered '
                       'connection')
        else:
            reason = _('You chose not to load media automatically')
        # The tooltip is all a sighted user sees of the reason; a screen
        # reader hears it as the button's description.
        self.load_button.set_tooltip_text(reason)
        self.load_button.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], [reason])
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
        url, is_remote = media_url(attachment)
        if url:
            self._gate_remote('show', attachment, url, is_remote)

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
            self._gate_open = self._loop_gate_open()
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
                self._gate_open = self._loop_gate_open()
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
