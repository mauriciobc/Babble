# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared floating-card geometry for the PiP cards.

:class:`mediapip.PiPCard` and :class:`webpip.WebPiPCard` are both
END/END-aligned overlay children of the window content: their whole
position model is margin_end and margin_bottom, moved between the
window's corners by a flick drag and clamped back into the window when
it resizes. This module holds that geometry so the cards share it by
import, not by copy. Content, controls and sizing stay per-card.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk  # noqa: E402

from .weakconnect import connect_weak  # noqa: E402

CORNER_MARGIN = 24
# A drag shorter than this in both axes is a tap, not a flick: the
# card does not move.
FLICK_THRESHOLD = 12


def corner_inset(free, near):
    """The END or BOTTOM inset on one axis with *free* (>= 0) spare
    pixels: CORNER_MARGIN from the near or far edge, shrunk to fit."""
    margin = min(CORNER_MARGIN, free)
    return float(margin if near else free - margin)


class CardGeometry:
    """Mix-in for a floating card: margins, flick, clamp, corner keys
    and the window plumbing.

    The host is an END/END-aligned overlay child that sets ``.window``
    before calling :meth:`init_geometry`, keeps a size request in
    place, and defines ``_on_window_close(window)`` for the window's
    ``close-request``. The window handlers are connected here and
    dropped by :meth:`_disconnect_window`, which every close path
    calls."""

    def init_geometry(self, window):
        # Resting position. halign/valign are END (the bottom-right
        # corner), and an END-aligned child is placed by margin_end and
        # margin_bottom alone -- the start/top pair is inert for it --
        # so those two margins are the card's whole position model.
        # Without END the overlay child would fill the whole window.
        self._m_bottom = float(CORNER_MARGIN)
        self._m_end = float(CORNER_MARGIN)
        # The gesture offsets of a drag in progress, from its start
        # point; only the release acts on them.
        self._drag_offset = (0.0, 0.0)
        self._window_handlers = []
        if window is not None:
            self._window_handlers = [
                connect_weak(window, 'close-request',
                             self._on_window_close),
                connect_weak(window, 'notify::default-width',
                             self._on_window_resized),
                connect_weak(window, 'notify::default-height',
                             self._on_window_resized)]
        self._apply_margins()

    # Position

    def _apply_margins(self):
        """Rest the card at its position: END/END alignment places it
        by margin_end and margin_bottom alone; the other pair is zeroed
        so it can never disagree."""
        self.set_margin_top(0)
        self.set_margin_bottom(round(self._m_bottom))
        self.set_margin_start(0)
        self.set_margin_end(round(self._m_end))

    # Dragging

    def _on_drag_begin(self, _gesture, _x, _y):
        # The card does not follow the pointer: moving it live would
        # queue a resize per frame that relays out all of the content
        # behind it (a Gtk.Fixed layer cannot take over, because
        # reparenting a mapped widget mid-gesture is fatal in GTK and
        # unmapping one resets the gesture), and GTK 4.24 has neither a
        # widget transform nor an overridable pick to move it without
        # allocation. The drag accumulates direction only; the release
        # moves the card once, to the corner of that direction.
        self._drag_offset = (0.0, 0.0)

    def _on_drag_update(self, _gesture, dx, dy):
        self._drag_offset = (dx, dy)

    def _on_drag_end(self, _gesture, _dx, _dy):
        dx, dy = self._drag_offset
        self._drag_offset = (0.0, 0.0)
        if self.window is None:
            return
        self._flick(dx, dy)

    def _flick(self, dx, dy):
        """The release of a drag past the flick threshold moves the
        card once, to the corner of that direction."""
        # Each side changes only when its axis moved past the flick
        # threshold: with the card resting at a window edge, the pointer
        # cannot move further toward that edge, so a raw sign read would
        # send every flick to the opposite corner. A straight vertical
        # flick keeps the current side, and so on.
        width, height = self.get_size_request()
        if not width or not height:
            return
        moved_x = abs(dx) >= FLICK_THRESHOLD
        moved_y = abs(dy) >= FLICK_THRESHOLD
        if not moved_x and not moved_y:
            return
        free_h = max(0, self.window.get_width() - width)
        free_v = max(0, self.window.get_height() - height)
        if moved_x:
            self._m_end = corner_inset(free_h, near=(dx >= 0))
        if moved_y:
            self._m_bottom = corner_inset(free_v, near=(dy >= 0))
        self._apply_margins()

    def _corner_key(self, keyval):
        """The arrows move the card between the corners, the keyboard
        path of what the flick does with the pointer. True when the
        key was one of the four and the card moved."""
        if self.window is None:
            return False
        width, height = self.get_size_request()
        if not width or not height:
            return False
        free_h = max(0, self.window.get_width() - width)
        free_v = max(0, self.window.get_height() - height)
        if keyval == Gdk.KEY_Left:
            self._m_end = corner_inset(free_h, False)
        elif keyval == Gdk.KEY_Right:
            self._m_end = corner_inset(free_h, True)
        elif keyval == Gdk.KEY_Up:
            self._m_bottom = corner_inset(free_v, False)
        elif keyval == Gdk.KEY_Down:
            self._m_bottom = corner_inset(free_v, True)
        else:
            return False
        self._apply_margins()
        return True

    def _on_window_resized(self, *_args):
        """Clamp into the window and keep the nearest corner: the card
        stays where it was, as far as the new size allows. A drag in
        progress holds the position its release will place.

        The notify arrives when the requested default size changes,
        before the compositor applies it, so the clamp reads the
        default properties, not the not-yet-updated allocation; when
        the applied size differs (a compositor minimum), GTK syncs the
        default properties to it and the clamp runs again."""
        if any(self._drag_offset) or self.window is None:
            return
        width, height = self.get_size_request()
        if not width or not height:
            return
        window_w = self.window.get_property('default-width')
        if window_w is None or window_w <= 0:
            window_w = self.window.get_width()
        window_h = self.window.get_property('default-height')
        if window_h is None or window_h <= 0:
            window_h = self.window.get_height()
        free_h = max(0, window_w - width)
        free_v = max(0, window_h - height)
        # The corner is the side the current inset is closer to.
        right = self._m_end <= free_h / 2.0
        bottom = self._m_bottom <= free_v / 2.0
        self._m_end = corner_inset(free_h, right)
        self._m_bottom = corner_inset(free_v, bottom)
        self._apply_margins()

    # The window

    def _disconnect_window(self):
        handlers, self._window_handlers = self._window_handlers, []
        if self.window is not None:
            for handler in handlers:
                self.window.disconnect(handler)
