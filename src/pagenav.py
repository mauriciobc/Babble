# SPDX-License-Identifier: GPL-3.0-or-later

"""Where a widget lives and how a page is pushed: the window it belongs
to, navigation pages, and the places any view may send the reader (a
profile, a thread)."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio  # noqa: E402


def window_of(widget):
    """The BabbleWindow *widget* lives in (dialogs included), or None."""
    root = widget.get_root() if widget is not None else None
    if root is not None and not hasattr(root, 'navigation_view'):
        # Adw.Dialog content: the dialog's parent window owns navigation.
        app = Gio.Application.get_default()
        root = app.get_active_window() if app is not None else None
    if root is None or not hasattr(root, 'navigation_view'):
        return None
    return root


def client_of(widget):
    window = window_of(widget)
    return getattr(window, 'client', None)


def make_nav_page(title, child, tag=None, sidebar_control=None):
    """An Adw.NavigationPage with a header bar around *child*.

    *child* may carry optional attributes the header picks up:
    ``header_start_widgets`` / ``header_widgets`` (end side) lists of
    widgets, ``title_widget`` (replaces the title), ``top_bar`` (an
    extra top bar, e.g. a view switcher or search bar) and
    ``bottom_bar``. *sidebar_control*, when given, is a callable
    returning the window's sidebar control, packed at the start of the
    header before the page's own start widgets."""
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    if sidebar_control is not None:
        header.pack_start(sidebar_control())
    for widget in getattr(child, 'header_start_widgets', ()) or ():
        header.pack_start(widget)
    for widget in reversed(getattr(child, 'header_widgets', ()) or ()):
        header.pack_end(widget)
    title_widget = getattr(child, 'title_widget', None)
    if title_widget is not None:
        header.set_title_widget(title_widget)
    toolbar.add_top_bar(header)
    top_bar = getattr(child, 'top_bar', None)
    if top_bar is not None:
        toolbar.add_top_bar(top_bar)
    bottom_bar = getattr(child, 'bottom_bar', None)
    if bottom_bar is not None:
        toolbar.add_bottom_bar(bottom_bar)
    toolbar.set_content(child)
    page = Adw.NavigationPage(title=title, child=toolbar)
    if tag:
        page.set_tag(tag)
    return page


def push_page(widget, title, child, tag=None):
    """Push *child* (wrapped by :func:`make_nav_page`) onto the window's
    navigation view. A page with the same *tag* already on the stack is
    popped back to instead of stacked twice. Returns the page."""
    window = window_of(widget)
    if window is None:
        return None
    view = window.navigation_view
    if tag:
        existing = view.find_page(tag)
        if existing is not None:
            view.pop_to_page(existing)
            return existing
    page = make_nav_page(
        title, child, tag,
        sidebar_control=getattr(window, 'make_sidebar_control', None))
    view.push(page)
    dismiss = getattr(window, 'dismiss_sidebar_overlay', None)
    if dismiss is not None:
        dismiss()
    return page


def open_profile(widget, client, account=None, acct=None):
    from .profile import push_profile
    window = window_of(widget)
    if window is None:
        return
    push_profile(window.navigation_view, client, account, acct=acct)


def open_status(widget, client, status):
    from .thread import push_thread
    from .timelinedata import StatusItem
    window = window_of(widget)
    if window is None:
        return
    push_thread(window.navigation_view, client, StatusItem(status))
    dismiss = getattr(window, 'dismiss_sidebar_overlay', None)
    if dismiss is not None:
        dismiss()
