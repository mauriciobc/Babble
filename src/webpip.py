# SPDX-License-Identifier: GPL-3.0-or-later

"""Picture-in-Picture for the web column: one floating card per window
hosting the column's live WebKit view.

:func:`open_card` takes the column out of ``web_slot`` (before the
sidebar hides, so ``watch_split`` never mistakes the handover for a
dismissal), reparents it into a :class:`WebPiPCard` over the window, and
asks a page script to bring its playing video to the front of the card.
:func:`restore` puts the column back into the sidebar at the same page
-- no reload; :func:`close_card` disposes the column through its normal
deferred teardown, keeping it mapped while a load settles (the
pre-first-load unmapping crash is why).

The page area belongs to the page: clicks, scroll and keys are its own.
The card's drag comes from its OSD bar only, and the card has no
arrow-key movement -- the geometry's corner keys are unused here.
Position, flick and resize clamp come from :mod:`pipgeometry`.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GLib, Gtk  # noqa: E402

from . import appsettings  # noqa: E402
from . import uihelpers  # noqa: E402
from .pipgeometry import CardGeometry  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

CARD_WIDTH = 320
CARD_HEIGHT = 180
# The video-focus script runs in an isolated script world, the way the
# plugin panels get one: page script can see its DOM effects but not its
# JavaScript, and vice versa.
WORLD = 'babble-webpip'
# Autoplay in the column is muted, and players are built by the page's
# own script, which may start well after the load commits. The pick runs
# again on each committed and finished load and once more two seconds
# later to catch a player that boots late.
PICK_RETRY_SECONDS = 2

# The pick: the largest playing video (visible ones preferred; a playing
# one below the fold is taken when that is all there is, because the
# fill below brings it into view and out of WebKit's visibility
# suspend), filtered by the Firefox toggle heuristics -- at least 45 s
# (an unknown duration counts; live streams report NaN) and at least
# 160px in one dimension. The fill is an inline style on the element,
# with the original kept in a data attribute the undo script restores.
PICK_SCRIPT = """(() => {
  const videos = Array.from(document.querySelectorAll('video'));
  const playing = videos.filter(v => !v.paused && !v.ended);
  if (!playing.length) return false;
  const area = v => { const r = v.getBoundingClientRect();
                      return r.width * r.height; };
  const big = v => { const r = v.getBoundingClientRect();
                     return r.width >= 160 || r.height >= 160; };
  const long = v => isNaN(v.duration) || v.duration >= 45;
  let cands = playing.filter(long).filter(big);
  if (!cands.length) cands = playing.filter(long);
  if (!cands.length) return false;
  let best = cands[0];
  for (const v of cands) if (area(v) > area(best)) best = v;
  if (best.hasAttribute('data-babble-pip-style')) return true;
  const prev = best.getAttribute('style') || '';
  best.setAttribute('data-babble-pip-style', prev);
  best.setAttribute('style', prev
    + ';position:fixed !important;inset:0 !important'
    + ';width:100% !important;height:100% !important'
    + ';object-fit:contain !important;z-index:2147483647 !important'
    + ';background:#000 !important');
  best.scrollIntoView({block: 'center'});
  return true;
})()"""

UNDO_SCRIPT = """(() => {
  for (const v of document.querySelectorAll(
           'video[data-babble-pip-style]')) {
    const prev = v.getAttribute('data-babble-pip-style');
    if (prev === '') v.removeAttribute('style');
    else v.setAttribute('style', prev);
    v.removeAttribute('data-babble-pip-style');
  }
  return true;
})()"""


def card_for(window):
    """The web column's PiP card of *window*, or None. Kept as an
    attribute of the window, the same pattern the engine card uses."""
    return getattr(window, '_babble_web_pip_card', None)


def open_card(window):
    """Move the window's web column into a floating card; returns the
    card, or None when there is no live plain page to move."""
    from .mediaviewer import media_overlay
    overlay = media_overlay(window)
    if overlay is None:
        return None
    column = window.web_slot.get_child()
    if column is None or not hasattr(column, 'view') \
            or column._disposed or column.panel is not None:
        return None
    if card_for(window) is not None:
        return None  # already out; the button is hidden in card mode
    # One floating card per window: the engine card is closed first,
    # through its own tested release path.
    from . import mediapip
    engine_card = mediapip.card_for(window)
    if engine_card is not None:
        mediapip.close_card(engine_card)
    # The column leaves the slot before the sidebar hides: watch_split's
    # on_shown then finds an empty slot and never disposes the page.
    window.web_slot.set_child(None)
    column.card_mode = True
    column.add_css_class('pip-web-view')
    card = WebPiPCard(window, column)
    overlay.add_overlay(card)
    card.set_visible(True)
    window._babble_web_pip_card = card
    window.web_split.set_show_sidebar(False)
    if appsettings.settings().get_boolean('web-javascript'):
        card._focus_video()
    # The pressed header button is hidden with the top bars: focus moves
    # to the card's expand button, the control that will still exist.
    card.expand_button.grab_focus()
    return card


def restore(card, show_sidebar=True):
    """Expand: put the column back into the sidebar at the same page.
    The sidebar is shown unless the caller asks otherwise (the
    viewer-opens case)."""
    if card._closed:
        return
    window = card.window
    card._disconnect_window()
    window._babble_web_pip_card = None
    column = card.column
    card._undo_video_focus()
    column.remove_css_class('pip-web-view')
    column.card_mode = False
    card.set_child(None)
    window.web_slot.set_child(column)
    card.unparent()
    if show_sidebar:
        window.web_split.set_show_sidebar(True)
    column.view.grab_focus()


def close_card(card):
    """Close the card and dispose the column's page, through the
    column's deferred teardown: the card keeps the column mapped while a
    load settles, then leaves the overlay."""
    if card._closed:
        return
    card._closed = True
    window = card.window
    card._disconnect_window()
    window._babble_web_pip_card = None
    card._undo_video_focus()
    column = card.column
    column.dispose_column()
    if column._disposed and column._teardown_timer:
        # Still loading: the card stays (mapped, visible) until the
        # column's own teardown drops it, then the card follows.
        def poll():
            if not column._disposed or not column._teardown_timer:
                _drop(card, window)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        GLib.timeout_add(100, poll)
        return
    _drop(card, window)


def _drop(card, window):
    """The card leaves the overlay; the sidebar hides over an empty
    slot."""
    if card.get_parent() is not None:
        card.unparent()
    if window is not None and window.web_slot.get_child() is None \
            and window.web_split.get_show_sidebar():
        window.web_split.set_show_sidebar(False)


class WebPiPCard(Gtk.Overlay, CardGeometry):
    """The web column's floating card: the column itself, with a top OSD
    bar. The page area is the page: never a control surface of ours."""

    __gtype_name__ = 'BabbleWebPiPCard'

    def __init__(self, window, column):
        super().__init__()
        self.window = window
        self.column = column
        self._closed = False
        self._retry_source = 0
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_size_request(CARD_WIDTH, CARD_HEIGHT)
        self.add_css_class('pip-card')
        self.add_css_class('web-pip-card')
        self.set_child(column)
        self.init_geometry(window)
        self._build()

    # Construction

    def _build(self):
        # Top-center, so a player's own bottom controls stay clickable.
        controls = Gtk.Box(halign=Gtk.Align.CENTER,
                           valign=Gtk.Align.START,
                           spacing=6, margin_top=6)
        controls.add_css_class('osd')
        controls.add_css_class('pip-controls')

        # Translators: the web column's Picture-in-Picture card button
        # that puts the column back into the sidebar.
        self.expand_button = uihelpers.icon_button(
            'babble-pip-out-symbolic', _('Expand'))
        connect_weak(self.expand_button, 'clicked',
                     self._on_expand_clicked)
        controls.append(self.expand_button)

        self.close_button = uihelpers.icon_button(
            'window-close-symbolic', _('Close'))
        connect_weak(self.close_button, 'clicked', self._on_close_clicked)
        controls.append(self.close_button)
        self.add_overlay(controls)

        # Drag from the bar only: the page area belongs to the page.
        drag = Gtk.GestureDrag()
        connect_weak(drag, 'drag-begin', self._on_drag_begin)
        connect_weak(drag, 'drag-update', self._on_drag_update)
        connect_weak(drag, 'drag-end', self._on_drag_end)
        controls.add_controller(drag)

        # The pick re-runs as the page's own script builds its player.
        connect_weak(self.column.view, 'load-changed',
                     self._on_page_load)

    def _on_expand_clicked(self, _button):
        restore(self)

    def _on_close_clicked(self, _button):
        close_card(self)

    def _on_window_close(self, _window):
        close_card(self)
        return False

    # Video focus

    def _focus_video(self):
        self.column.evaluate(WORLD, PICK_SCRIPT, self._on_picked)

    def _on_picked(self, _value, _error):
        # A page without a qualifying video (or an error) simply stays
        # as it is; nothing is announced.
        pass

    def _on_page_load(self, view, event):
        if not appsettings.settings().get_boolean('web-javascript'):
            return
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        WebKit = self.column._webkit
        if event not in (WebKit.LoadEvent.COMMITTED,
                         WebKit.LoadEvent.FINISHED):
            return
        self._focus_video()
        if event == WebKit.LoadEvent.FINISHED:
            self._retry_source = GLib.timeout_add_seconds(
                PICK_RETRY_SECONDS, self._on_retry)

    def _on_retry(self):
        self._retry_source = 0
        if not self._closed and self.column.get_parent() is self \
                and appsettings.settings().get_boolean('web-javascript'):
            self._focus_video()
        return GLib.SOURCE_REMOVE

    def _undo_video_focus(self):
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        if not appsettings.settings().get_boolean('web-javascript'):
            return
        self.column.evaluate(WORLD, UNDO_SCRIPT, lambda _v, _e: None)
