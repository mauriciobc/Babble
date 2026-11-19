# SPDX-License-Identifier: GPL-3.0-or-later

"""Picture-in-Picture: one floating card per window playing a video or
animated GIFV the media viewer handed over.

:func:`open_pip` takes the viewer's engine and stream (still playing, at
the current position), closes the viewer without releasing anything, and
raises a :class:`PiPCard` over the window; :func:`restore` hands the same
engine and stream back to a freshly opened viewer; :func:`close_card`
pauses, detaches and releases them. While a card exists the inline GIFV
player of the same window stands down, and a second media viewer closes
the card: one playing surface per window, the rule
:mod:`mediaviewer` already enforces.

Teardown discipline: pause, ``stream.detach()``, then ``engine.release()``
(deferred, see :mod:`mediaengine`). The card never touches
:mod:`mediaengine` or :mod:`mediastream` beyond that.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Gio, Gtk  # noqa: E402

from . import mediainline  # noqa: E402
from . import uihelpers  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

CARD_WIDTH = 240
MIN_CARD_HEIGHT = 135
MAX_CARD_HEIGHT = 360
CORNER_MARGIN = 24


def _playing_reason(stream):
    """Whether the card must keep the session from going idle."""
    return stream.get_playing() and not stream.get_ended()


def _set_inhibited(card, inhibited):
    """Keep the session from going idle while *inhibited*.

    Mirrors :meth:`mediaplayback.MediaPlayback._set_inhibited`, but
    module-local: only a video in the card holds it, armed while it
    plays, cleared on pause, end and every close path.
    """
    if card.kind != 'video':
        inhibited = False
    if inhibited == bool(card._inhibit_cookie):
        return
    if not inhibited:
        app, cookie = card._inhibit_app, card._inhibit_cookie
        card._inhibit_app, card._inhibit_cookie = None, 0
        app.uninhibit(cookie)
        return
    app = (card.window.get_application()
           if card.window is not None else None)
    if app is None:
        app = Gio.Application.get_default()
    if not isinstance(app, Gtk.Application):
        return
    # Translators: why the session is kept from going idle, shown by the
    # desktop where it lists apps blocking suspend or screen lock.
    cookie = app.inhibit(card.window, Gtk.ApplicationInhibitFlags.IDLE,
                         _('Playing media'))
    if cookie:
        card._inhibit_app, card._inhibit_cookie = app, cookie


class PiPCard(Gtk.Overlay):
    """The Picture-in-Picture card: see the module docstring.

    Built in code, like :meth:`mediaviewerchrome.MediaViewerChrome._build`.
    The card is an overlay child of the window, never a dialog: it
    declares no window role and never takes focus when it appears.
    """

    __gtype_name__ = 'BabblePiPCard'

    def __init__(self, window, engine, stream, kind, attachments, index,
                 status_url):
        super().__init__()
        self.window = window
        self.engine = engine
        self.stream = stream
        self.kind = kind
        self.attachments = attachments
        self.index = index
        self.status_url = status_url
        # Set by MediaViewer._show_adopted when the viewer took the
        # engine and stream back; a False after restore means the dead
        # engine case, and the card closes itself.
        self.adopted = False
        self._closed = False
        self._inhibit_app = None
        self._inhibit_cookie = 0
        # Position as the four margins, in pixels. halign/valign are END
        # (the bottom-right corner), so extra space and dragging are
        # read through margin_top/bottom/start/end. Without END the
        # overlay child would fill the whole window.
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self._m_top = 0.0
        self._m_bottom = float(CORNER_MARGIN)
        self._m_start = 0.0
        self._m_end = float(CORNER_MARGIN)
        # Margins captured when a drag starts, so the updates are a
        # whole-drag delta rather than per-frame steps.
        self._drag_base = None
        self._window_handlers = []

        self.add_css_class('pip-card')
        self._build()
        self._apply_margins()
        self._on_size(stream)

    # Construction

    def _build(self):
        picture = Gtk.Picture(paintable=self.stream,
                              content_fit=Gtk.ContentFit.CONTAIN,
                              can_shrink=True, hexpand=True, vexpand=True)
        picture.add_css_class('pip-video')
        # The video area is not a control: never focusable, and a click
        # on it does nothing.
        picture.set_focusable(False)
        self.set_child(picture)

        controls = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                           spacing=6, margin_bottom=6)
        controls.add_css_class('pip-controls')
        controls.add_css_class('osd')

        # Flat: the island paints the surface; a plain button paints its
        # own light background and the light icon on it disappears.
        self.play_button = Gtk.ToggleButton()
        self.play_button.add_css_class('flat')
        self.play_button.add_css_class('circular')
        uihelpers.set_accessible_label(self.play_button, _('Pause'))
        # connect_weak wraps the handler in a closure, so the sync can
        # only block it by id, never by function.
        self._toggled_handler = connect_weak(
            self.play_button, 'toggled', self._on_play_toggled)
        controls.append(self.play_button)

        self.expand_button = Gtk.Button(
            icon_name='babble-pip-out-symbolic')
        self.expand_button.add_css_class('flat')
        self.expand_button.add_css_class('circular')
        # Translators: the Picture-in-Picture card button that puts the
        # media back into the full-size viewer.
        uihelpers.set_accessible_label(self.expand_button, _('Expand'))
        connect_weak(self.expand_button, 'clicked', self._on_expand_clicked)
        controls.append(self.expand_button)

        self.close_button = Gtk.Button(icon_name='window-close-symbolic')
        self.close_button.add_css_class('flat')
        self.close_button.add_css_class('circular')
        uihelpers.set_accessible_label(self.close_button, _('Close'))
        connect_weak(self.close_button, 'clicked', self._on_close_clicked)
        controls.append(self.close_button)
        self.add_overlay(controls)

        # Drag from the video area only, so the buttons stay where they
        # are under the pointer.
        drag = Gtk.GestureDrag()
        connect_weak(drag, 'drag-begin', self._on_drag_begin)
        connect_weak(drag, 'drag-update', self._on_drag_update)
        connect_weak(drag, 'drag-end', self._on_drag_end)
        picture.add_controller(drag)

        connect_weak(self.stream, 'invalidate-size', self._on_size)
        connect_weak(self.stream, 'notify::playing', self._on_playing)
        connect_weak(self.stream, 'notify::ended', self._on_playing)
        # GObject properties do not re-emit on connect and the stream
        # arrives playing: seed the icon and the idle inhibition from
        # the state it is in right now.
        self._on_playing(self.stream, None)
        if self.window is not None:
            self._window_handlers = [
                connect_weak(self.window, 'close-request',
                             self._on_window_close),
                connect_weak(self.window, 'notify::default-width',
                             self._on_window_resized),
                connect_weak(self.window, 'notify::default-height',
                             self._on_window_resized)]

    # State

    def _sync_playing(self):
        """The play/pause button mirrors the stream: its checked state,
        icon and label for playing, paused and ended."""
        stream = self.stream
        playing = _playing_reason(stream)
        self.play_button.handler_block(self._toggled_handler)
        self.play_button.set_active(playing)
        self.play_button.handler_unblock(self._toggled_handler)
        if stream.get_ended():
            self.play_button.set_icon_name('media-playback-play-symbolic')
            # Translators: the stream ended; this button starts it from
            # the beginning again.
            uihelpers.set_accessible_label(self.play_button, _('Replay'))
        elif playing:
            self.play_button.set_icon_name('media-playback-pause-symbolic')
            uihelpers.set_accessible_label(self.play_button, _('Pause'))
        else:
            self.play_button.set_icon_name('media-playback-play-symbolic')
            uihelpers.set_accessible_label(self.play_button, _('Play'))
        _set_inhibited(self, playing)

    def _on_playing(self, stream, _pspec):
        if stream is self.stream:
            self._sync_playing()

    def _on_play_toggled(self, _button):
        if self.stream.get_playing():
            self.stream.pause()
        else:
            self.stream.play()
        self._sync_playing()

    def _on_size(self, stream):
        """The card is CARD_WIDTH wide; the height follows the stream's
        aspect ratio, between the 16:9 floor and a ceiling."""
        ratio = stream.get_intrinsic_aspect_ratio()
        if not ratio or ratio <= 0:
            ratio = 16.0 / 9.0
        height = min(MAX_CARD_HEIGHT, max(MIN_CARD_HEIGHT,
                                          round(CARD_WIDTH / ratio)))
        self.set_size_request(CARD_WIDTH, height)

    # Dragging

    def _apply_margins(self):
        self.set_margin_top(round(self._m_top))
        self.set_margin_bottom(round(self._m_bottom))
        self.set_margin_start(round(self._m_start))
        self.set_margin_end(round(self._m_end))

    def _on_drag_begin(self, _gesture, _x, _y):
        self._drag_base = (self._m_top, self._m_bottom,
                           self._m_start, self._m_end)

    def _on_drag_update(self, _gesture, dx, dy):
        if self._drag_base is None:
            return
        t, b, s, e = self._drag_base
        # Offsets are relative to the press point, so the whole delta
        # lands on the captured margins. Margins are start/end, so they
        # flip with the direction on their own: dragging toward a window
        # edge always shrinks that edge's margin.
        free_v = t + b
        free_h = s + e
        self._m_top = min(free_v, max(0.0, t + dy))
        self._m_bottom = free_v - self._m_top
        self._m_end = min(free_h, max(0.0, e - dx))
        self._m_start = free_h - self._m_end
        self._apply_margins()

    def _on_drag_end(self, _gesture, _dx, _dy):
        base = self._drag_base
        self._drag_base = None
        if base is None:
            return
        # Snap to the nearest corner: for each of the four corners the
        # two margins that put the card there, the other two at 0, and
        # the pair with the smaller sum wins. RTL only changes which
        # visual corner start/end name; the maths is the same.
        t, b, s, e = (self._m_top, self._m_bottom,
                      self._m_start, self._m_end)
        free_v, free_h = t + b, s + e
        self._m_top, self._m_bottom = \
            (0.0, free_v) if t <= b else (free_v, 0.0)
        self._m_start, self._m_end = \
            (0.0, free_h) if s <= e else (free_h, 0.0)
        self._apply_margins()

    def _on_window_resized(self, *_args):
        """Clamp into the window and keep the corner: margins that no
        longer fit are recomputed at the same corner."""
        if self._drag_base is not None or self.window is None:
            return
        width, height = self.get_size_request()
        if not width or not height:
            return
        free_v = max(0.0, float(self.window.get_height() - height))
        free_h = max(0.0, float(self.window.get_width() - width))
        self._m_top, self._m_bottom = \
            (0.0, free_v) if self._m_top <= self._m_bottom \
            else (free_v, 0.0)
        self._m_start, self._m_end = \
            (0.0, free_h) if self._m_start <= self._m_end \
            else (free_h, 0.0)
        self._apply_margins()

    # Buttons and the window

    def _on_expand_clicked(self, _button):
        restore(self)

    def _on_close_clicked(self, _button):
        close_card(self)

    def _on_window_close(self, _window):
        close_card(self)
        return False

    def _disconnect_window(self):
        handlers, self._window_handlers = self._window_handlers, []
        if self.window is not None:
            for handler in handlers:
                self.window.disconnect(handler)


def card_for(window):
    """The Picture-in-Picture card of *window*, or None. Kept as an
    attribute of the window, which drops it itself -- the same pattern
    :func:`mediainline.player_for` uses."""
    return getattr(window, '_babble_pip_card', None)


def open_pip(viewer):
    """Move the viewer's playing video or GIFV into a card; returns the
    card, or None when there is nothing eligible to move."""
    from .mediaviewer import media_overlay
    # The overlay check comes first: without a card surface the viewer
    # is not touched at all.
    overlay = media_overlay(viewer.window)
    if overlay is None:
        return None
    # A failed stream makes a dead card: EngineStream.do_play returns
    # False once failed, and the viewer's error path never clears it.
    if viewer._engine is None or viewer._stream is None \
            or viewer._stream.get_error() is not None \
            or viewer._stream_kind not in ('video', 'gifv'):
        return None
    window = viewer.window
    old = card_for(window)
    if old is not None:
        close_card(old)
    # Captured before the close: the handover path severs the viewer's
    # own references (that severing is what makes its weak stream
    # handlers inert), so the card takes the state the viewer just gave
    # up.
    engine = viewer._engine
    stream = viewer._stream
    kind = viewer._stream_kind
    # The viewer closes through the handover path: the widget goes, the
    # engine and stream stay alive and keep playing.
    viewer._pip_handover = True
    viewer.close()
    card = PiPCard(window, engine, stream, kind, viewer.attachments,
                   viewer.index, viewer.status_url)
    overlay.add_overlay(card)
    card.set_visible(True)
    window._babble_pip_card = card
    mediainline.pip_opened(window)
    return card


def restore(card):
    """Expand: put the same engine and stream back into the viewer."""
    window = card.window
    card._disconnect_window()
    _set_inhibited(card, False)
    window._babble_pip_card = None
    mediainline.pip_closed(window)
    # The card leaves the overlay unparented but untouched: no pause, no
    # detach, no release. present_media adopts it through the viewer.
    card.unparent()
    from .mediaviewer import present_media
    present_media(window, card.attachments, index=card.index,
                  status_url=card.status_url, adopt=card)
    if not card.adopted:
        # The engine can no longer serve: release it and leave the
        # viewer's own fresh load on screen. No error dialog.
        close_card(card)


def close_card(card):
    """Stop the card's playback and release its engine."""
    if card._closed:
        return
    card._closed = True
    window = card.window
    if card.stream is not None:
        card.stream.pause()
        card.stream.detach()
    engine, card.engine = card.engine, None
    if engine is not None:
        engine.release()
    _set_inhibited(card, False)
    card._disconnect_window()
    if card.get_parent() is not None:
        card.unparent()
    if window is not None and card_for(window) is card:
        window._babble_pip_card = None
        mediainline.pip_closed(window)
