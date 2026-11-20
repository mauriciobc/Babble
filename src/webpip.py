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
from .requestscope import RequestScope  # noqa: E402
from .pipgeometry import CardGeometry  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

CARD_WIDTH = 320
CARD_HEIGHT = 180
# The video-focus script runs in an isolated script world, the way the
# plugin panels get one: page script can see its DOM effects but not its
# JavaScript, and vice versa. On WebKitGTK 2.54 every evaluate in the
# world is a fresh context -- state set in one call is gone in the
# next -- so anything persistent has to be a user script.
WORLD = 'babble-webpip'
PICK_POLL_SECONDS = 3

# The pick follows Google's Picture-in-Picture extension detection
# (Apache-2.0; github.com/GoogleChromeLabs/
# picture-in-picture-chrome-extension, src/script.js): largest video
# with data, unless the site explicitly opts out. A user-origin
# stylesheet provides the fill, so player scripts cannot clear it by
# rewriting the video's inline style. The marker is removed on restore.
# Detection is top-frame-only; cross-origin embeds remain untouched.
_PICK = """const pick = () => {
  const area = v => { const r = v.getClientRects()[0]
                                    || {width: 0, height: 0};
                      return r.width * r.height; };
  const videos = [...document.querySelectorAll('video')]
    .filter(v => v.readyState != 0)
    .filter(v => v.disablePictureInPicture !== true)
    .filter(v => { const r = v.getClientRects()[0] || {width: 0};
                   return r.width >= 160 || r.height >= 160; })
    .sort((a, b) => area(b) - area(a));
  if (!videos.length) return false;
  const v = videos[0];
  if (v.hasAttribute('data-babble-pip-style')) return true;
  v.setAttribute('data-babble-pip-style', '');
  v.scrollIntoView({block: 'center'});
  return true;
};
"""
PICK_ONCE = '(() => {\n' + _PICK + 'return pick();\n})()'
WATCH_SCRIPT = '(() => {\n' + _PICK + """const repick = () => setTimeout(() => {
  const filled = document.querySelector('video[data-babble-pip-style]');
  if (!filled || !filled.getClientRects()[0]
      || filled.getClientRects()[0].width < 10) pick();
}, 200);
for (const type of ['play', 'emptied', 'loadedmetadata'])
  document.addEventListener(type, repick, true);
pick();
})()"""

UNDO_SCRIPT = """(() => {
  for (const v of document.querySelectorAll(
           'video[data-babble-pip-style]')) {
    v.removeAttribute('data-babble-pip-style');
  }
  return true;
})()"""

# While a video is marked for the PiP card, the page is reduced to that
# video. A plain hidden body kept the page's scrollbars, because hidden
# boxes still take space, so the document and every ancestor of the
# video lose their scrollable overflow too. Ancestors also lose
# transforms, filters and containment: any of them turns into the
# containing block of a `position: fixed` box and leaves the fill short
# of the viewport. These are user-origin declarations, which beat author
# `!important` rules.
PIP_STYLE = (
    'html:has(video[data-babble-pip-style])'
    ' { overflow: hidden !important; background: #000 !important; }'
    'html:has(video[data-babble-pip-style])'
    ' *:has(video[data-babble-pip-style])'
    ' { overflow: hidden !important; transform: none !important;'
    ' filter: none !important; contain: none !important;'
    ' will-change: auto !important; }'
    'html:has(video[data-babble-pip-style]) body'
    ' { visibility: hidden !important; margin: 0 !important; }'
    'html:has(video[data-babble-pip-style])'
    ' video[data-babble-pip-style]'
    ' { visibility: visible !important; position: fixed !important;'
    ' inset: 0 !important; width: 100vw !important;'
    ' height: 100vh !important; object-fit: contain !important;'
    ' z-index: 2147483647 !important; background: #000 !important; }'
    'html:has(video[data-babble-pip-style]) ::-webkit-scrollbar'
    ' { display: none !important; }')


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
    card._closed = True
    window = card.window
    card._disconnect_window()
    window._babble_web_pip_card = None
    column = card.column
    view = column.view
    view.set_opacity(0)
    view.set_sensitive(False)

    def reveal():
        view.set_opacity(1)
        view.set_sensitive(True)

    card._undo_video_focus(reveal)
    column.remove_css_class('pip-web-view')
    column.card_mode = False
    card._viewport.set_child(None)
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
        self._closed = False
        self.window = window
        self.column = column
        self._watch_script = None
        self._poll_source = 0
        # The card's page reads (watch, undo) run through one scope:
        # restore and close supersede what is in flight, so a view torn
        # down mid-evaluate cannot keep the callback (and through it the
        # card) alive for an answer that is no longer wanted.
        self._scope = RequestScope()
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.set_size_request(CARD_WIDTH, CARD_HEIGHT)
        self._viewport = Gtk.ScrolledWindow()
        self._viewport.set_policy(Gtk.PolicyType.NEVER,
                                  Gtk.PolicyType.NEVER)
        self._viewport.set_min_content_width(CARD_WIDTH)
        self._viewport.set_max_content_width(CARD_WIDTH)
        self._viewport.set_min_content_height(CARD_HEIGHT)
        self._viewport.set_max_content_height(CARD_HEIGHT)
        self._viewport.set_propagate_natural_width(False)
        self._viewport.set_propagate_natural_height(False)
        self._viewport.set_child(column)
        self.add_css_class('pip-card')
        self.add_css_class('web-pip-card')
        self.set_child(self._viewport)
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
            'babble-pip-out-symbolic', _('Expand'), css=('circular',))
        connect_weak(self.expand_button, 'clicked',
                     self._on_expand_clicked)
        controls.append(self.expand_button)

        self.close_button = uihelpers.icon_button(
            'window-close-symbolic', _('Close'), css=('circular',))
        connect_weak(self.close_button, 'clicked', self._on_close_clicked)
        controls.append(self.close_button)
        self.add_overlay(controls)

        # Drag from the bar only: the page area belongs to the page.
        drag = Gtk.GestureDrag()
        connect_weak(drag, 'drag-begin', self._on_drag_begin)
        connect_weak(drag, 'drag-update', self._on_drag_update)
        connect_weak(drag, 'drag-end', self._on_drag_end)
        controls.add_controller(drag)

        # Escape with an OSD control focused (the column's own shortcut
        # covers the page); Ctrl+W goes through forward_action.
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string('Escape'),
            Gtk.CallbackAction.new(
                self.column._shortcut_callback(self._on_close_clicked))))
        self.add_controller(escape)

        # The watcher handles later loads; the current page is evaluated
        # directly because a user script only applies to future loads.
        if appsettings.settings().get_boolean('web-javascript'):
            WebKit = self.column._webkit
            content = self.column.view.get_user_content_manager()
            script = WebKit.UserScript.new_for_world(
                WATCH_SCRIPT, WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.END, WORLD, None, None)
            content.add_script(script)
            self._watch_script = script
            self._focus_video()

    def _on_expand_clicked(self, _button):
        restore(self)

    def _on_close_clicked(self, *_args):
        close_card(self)

    def _on_window_close(self, _window):
        close_card(self)
        return False

    # Video focus

    def _focus_video(self):
        """Poll the page already showing at minimize for a video that
        plays: each evaluate is a fresh context on WebKitGTK 2.54, so a
        one-shot pick runs now and again until it fills (globo's
        countdown and user-pressed play both arrive after the load).
        Loads that happen later get the persistent watcher from the
        injected user script instead. The poll stops at the first
        fill, or when the card goes away (the scope is superseded by
        restore and close, and the ticket drops those answers)."""
        self._pick()
        if self._poll_source == 0:
            self._poll_source = GLib.timeout_add_seconds(
                PICK_POLL_SECONDS, self._on_poll)

    def _stop_polling(self):
        if self._poll_source != 0:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _on_poll(self):
        if not appsettings.settings().get_boolean('web-javascript') \
                or self.column.get_parent() is not self._viewport.get_child():
            self._poll_source = 0
            return GLib.SOURCE_REMOVE
        self._pick()
        return GLib.SOURCE_CONTINUE

    def _pick(self):
        # evaluate's (value, error) answer cannot take ticket.callback,
        # so the callback asks wanted() itself: an answer restore or
        # close superseded never stops a poll.
        ticket = self._scope.start()
        stop = weak_method(self._stop_polling)

        def picked(value, _error):
            # True once a fill exists: the poll stops.
            if ticket.wanted() and value is not None and value.to_boolean():
                stop()
        self.column.evaluate(WORLD, PICK_ONCE, picked, ticket.cancellable)

    def _undo_video_focus(self, on_done=None):
        self._stop_polling()
        # A restore or close supersedes the watch in flight; the undo
        # below is the new generation's read.
        self._scope.begin()
        content = self.column.view.get_user_content_manager()
        if self._watch_script is not None:
            content.remove_script(self._watch_script)
            self._watch_script = None
        if not appsettings.settings().get_boolean('web-javascript'):
            if on_done is not None:
                on_done()
            return
        ticket = self._scope.start()
        callback = None if on_done is None else (lambda _v, _e: on_done())
        self.column.evaluate(WORLD, UNDO_SCRIPT, callback,
                             ticket.cancellable)
