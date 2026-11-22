# SPDX-License-Identifier: GPL-3.0-or-later

"""Open and classify navigations for the web column.

Kept beside ``webcolumn`` so the widget module stays under the line limit.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gdk, GLib  # noqa: E402

from . import linkrouting, pagenav  # noqa: E402
from .statustext import is_web_link  # noqa: E402

LOAD = 'load'
EXTERNAL = 'external'
ROUTE = 'route'
IGNORE = 'ignore'


def available():
    """Whether WebKitGTK 6.0 can be loaded here. Cached on webcolumn."""
    from . import webcolumn
    return webcolumn.available()


def classify_navigation(uri, *, new_window=False, link_clicked=False,
                        user_gesture=False, button=0, modifiers=0,
                        strip=False, resolve=False, claimed=False):
    """Decide what a navigation request should do.

    Returns ``(action, uri)``: LOAD in the view, hand to the EXTERNAL
    launcher, ROUTE inside the app, or IGNORE. *uri* comes back changed
    only when tracking parameters were stripped. A *claimed* link (one a
    plugin owns the origin of) is never ROUTEd: it stays in the view, as
    a click inside the plugin's own panel should."""
    if not uri:
        return IGNORE, uri
    scheme = uri.split(':', 1)[0].lower() if ':' in uri else ''
    if scheme in ('about', 'data', 'blob'):
        # Frames need these; WebKit's own settings already refuse a
        # top-level data: navigation.
        return (IGNORE if new_window else LOAD), uri
    if not is_web_link(uri):
        return (EXTERNAL if user_gesture else IGNORE), uri
    if link_clicked and (button == 2
                         or modifiers & Gdk.ModifierType.CONTROL_MASK):
        return EXTERNAL, uri
    if link_clicked and resolve and not claimed \
            and linkrouting.is_fediverse_link(uri):
        return ROUTE, uri
    if new_window and not user_gesture:
        return IGNORE, uri  # popups are blocked
    if link_clicked and strip:
        uri = linkrouting.strip_tracking(uri)
    return LOAD, uri


def present_web(widget, uri):
    """Open *uri* in the web column of *widget*'s window, or in the
    system browser when there is no column to show it in."""
    window = pagenav.window_of(widget)
    if window is None or not hasattr(window, 'web_split') \
            or not available():
        linkrouting.launch_external(widget, uri)
        return
    _show(window, uri)


def _open_in_column(widget, uri):
    """The web opener link routing asks for (see ``linkhandlers``): show
    *uri* in the column and return True, or return False when WebKit is
    missing and the link belongs in the browser."""
    if not available():
        return False
    present_web(widget, uri)
    return True


def present_panel(widget, uri, owner, title=None):
    """Open *uri* in the web column as the page of *owner*, a plugin's
    panel: whatever panel the column had is closed, and the column is
    shown whatever the user chose for opening links. Returns the column,
    or None, after opening the system browser, when there is none."""
    window = pagenav.window_of(widget)
    if window is None or not hasattr(window, 'web_split') \
            or not available():
        linkrouting.launch_external(widget, uri)
        return None
    return _show(window, uri, owner, title)


def _show(window, uri, owner=None, title=None):
    from .webcolumn import WebColumn
    column = window.web_slot.get_child()
    if column is None:
        # The column may live in a webpip card: it comes back first, so
        # there is never a second WebColumn.
        from . import webpip
        card = webpip.card_for(window)
        if card is not None:
            webpip.restore(card)
            column = window.web_slot.get_child()
        else:
            column = WebColumn()
            window.web_slot.set_child(column)
    column.load_uri(uri, owner=owner, title=title)
    window.web_split.set_show_sidebar(True)
    column.view.grab_focus()
    return column


def watch_split(split):
    """Keep the column in step with *split*, the window's
    AdwOverlaySplitView. Call it once, when the window is built.

    AdwOverlaySplitView hides its sidebar when it collapses and shows it
    again when it expands. notify::collapsed comes first, so a hide that
    follows within the same main-loop turn is the layout's doing and
    keeps the page; any other hide is the user dismissing the column."""
    collapsing = [False]

    def end_collapse():
        collapsing[0] = False
        return GLib.SOURCE_REMOVE

    def on_collapsed(split, _pspec):
        collapsing[0] = True
        GLib.idle_add(end_collapse)
        _hide_if_empty(split)

    def on_shown(split, _pspec):
        if split.get_show_sidebar():
            _hide_if_empty(split)
            return
        column = split.get_sidebar().get_child()
        if column is not None and not collapsing[0]:
            column.dispose_column()  # dismissed over the content

    split.connect('notify::collapsed', on_collapsed)
    split.connect('notify::show-sidebar', on_shown)


def _hide_if_empty(split):
    # The sidebar comes back on expanding even when the column was
    # closed meanwhile; there is nothing to show then. A column whose
    # teardown is deferred (see WebColumn.dispose_column) is closed.
    if split.get_show_sidebar() and _live(split.get_sidebar().get_child()) \
            is None:
        split.set_show_sidebar(False)


def _live(child):
    """*child* if it is a column in use, else None: a disposed column may
    sit in the slot for a moment while its page settles."""
    from .webcolumn import WebColumn
    if isinstance(child, WebColumn) and not child._disposed:
        return child
    return None


def live_column(window):
    """The web column *window* is showing, or None. Read the slot through
    this, not ``web_slot.get_child()``."""
    slot = getattr(window, 'web_slot', None)
    return _live(slot.get_child()) if slot is not None else None


_column_of = live_column


def forward_action(window, name):
    """Run window action *name* ('close', 'reload', 'back') on the web
    column when it is shown and holds the focus; return whether it did.

    Application accelerators are matched before any widget's controller,
    so the window's own Ctrl+W, Ctrl+R, F5 and Alt+Left handlers hand
    over here first."""
    from . import webpip
    card = webpip.card_for(window)
    column = _column_of(window)
    if column is None:
        # The column may live in a webpip card: found through the card,
        # and the sidebar check does not apply to it.
        column = card.column if card is not None else None
    elif not window.web_split.get_show_sidebar():
        return False
    if column is None:
        return False
    # A carded column's focus scope is the card, so its OSD controls
    # count: Ctrl+W on the focused Expand button closes the card.
    scope = card if card is not None else column
    focus = window.get_focus()
    if focus is None or not (focus is scope or focus.is_ancestor(scope)):
        return False
    {'close': column._on_close_clicked,
     'reload': column._on_reload,
     'back': column._on_back,
     'forward': column._on_forward}[name]()
    return True


