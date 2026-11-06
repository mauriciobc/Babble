# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar rebuild, mode/visibility sync and pinned entries.

One level above :mod:`sidebarmode`, :mod:`sidebarrail` and
:mod:`sidebarcontrol`. The window keeps the Gtk.Template widgets, the
stream-event dispatch and thin forwards the tests and PlaceHost already
call; this object owns the runtime overlay/rail state and the rebuild.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import navigation  # noqa: E402
from . import sidebarmode  # noqa: E402
from . import sidebarrail  # noqa: E402
from .api import lists as api_lists  # noqa: E402
from .sidebarcontrol import SidebarControl  # noqa: E402


class SidebarHost:
    """Owns sidebar rebuild, mode/visibility sync and pinned append/load.

    *window* is the BabbleWindow whose template children and settings
    this host drives. Call sites on the window stay as one-line
    forwards so PlaceHost identity checks and ``connect_weak`` targets
    keep working.
    """

    def __init__(self, window):
        self.window = window
        self._items = {}
        self._overlay_open = False
        self._rail_accounts = False
        self._applying_sidebar_visibility = False
        self._sidebar_visibility_id = 0
        self._applying_sidebar_mode = False

    def make_control(self, include_bell=True):
        """A control strip for a page header, bound to the window.

        Every header gets its own: the strip follows the window's
        sidebar properties through bindings, so nothing has to keep a
        list of controls alive or walk them when the state changes."""
        window = self.window
        return SidebarControl(
            window, window.sidebar_mode_button.get_menu_model(),
            window.primary_menu_button.get_menu_model(),
            include_bell=include_bell)

    def on_rail_activated(self, _rail, tag):
        self.window.show_page(tag)

    def on_state_changed(self, _settings, _key):
        if self._applying_sidebar_mode:
            return
        self.sync_mode()
        self.sync_visibility()

    def on_collapsed(self, split_view, _param):
        window = self.window
        collapsed = split_view.get_collapsed()
        if collapsed:
            self._overlay_open = False
        self.sync_mode()
        # Collapsing: the compact breakpoint has already closed the
        # sidebar in this pass. Expanding: whether it comes back depends
        # on the stored state, and setting show-sidebar from inside this
        # notification re-enters the split view's allocation (libadwaita
        # then allocates the content pane with a negative width), so the
        # split view waits for an idle. The window's own properties only
        # drive the header controls, so they can be right straight away.
        window.props.sidebar_collapsed = collapsed
        window.props.sidebar_visible = sidebarmode.sidebar_visible(
            window.settings.get_boolean('sidebar-hidden'), collapsed,
            self._overlay_open)
        self.defer_visibility()

    def defer_visibility(self, *_args):
        self.cancel_visibility()
        self._sidebar_visibility_id = GLib.idle_add(
            self._apply_deferred_visibility)

    def cancel_visibility(self, *_args):
        if self._sidebar_visibility_id:
            GLib.source_remove(self._sidebar_visibility_id)
            self._sidebar_visibility_id = 0

    def _apply_deferred_visibility(self):
        self._sidebar_visibility_id = 0
        self.sync_visibility()
        return GLib.SOURCE_REMOVE

    def on_shown(self, split_view, _param):
        if self._applying_sidebar_visibility:
            return
        window = self.window
        shown = split_view.get_show_sidebar()
        if split_view.get_collapsed():
            self._overlay_open = shown
        window.props.sidebar_visible = shown
        window._sync_translucent_sidebar()

    def on_toggle(self, _action, _param):
        window = self.window
        if window.split_view.get_collapsed():
            self._overlay_open = not self._overlay_open
            self.sync_visibility()
        else:
            # The settings handler does the syncing.
            window.settings.set_boolean(
                'sidebar-hidden',
                not window.settings.get_boolean('sidebar-hidden'))

    def on_mode_changed(self, action, value):
        window = self.window
        state = value.get_string()
        # The overlay flag first, so the syncs below already see it.
        if state == 'hidden':
            self._overlay_open = False
        elif state in sidebarmode.SIDEBAR_STYLES:
            if window.split_view.get_collapsed():
                self._overlay_open = True
        # Each write emits its own changed:: signal, and an unchanged
        # value emits one too, so the handler stands aside and the
        # syncs run once, whichever keys actually changed.
        self._applying_sidebar_mode = True
        try:
            if state == 'hidden':
                window.settings.set_boolean('sidebar-hidden', True)
            elif state in sidebarmode.SIDEBAR_STYLES:
                window.settings.set_string('sidebar-style', state)
                window.settings.set_boolean('sidebar-hidden', False)
        finally:
            self._applying_sidebar_mode = False
        self.sync_mode()
        self.sync_visibility()
        action.set_state(value)

    def sync_mode(self):
        """Show the full sidebar or the icon rail, and hand the account
        button to whichever of them has room for its text."""
        window = self.window
        style = window.settings.get_string('sidebar-style')
        collapsed = window.split_view.get_collapsed()
        rail = sidebarmode.icon_rail(style, collapsed)
        # Collapsed, the sidebar is an overlay and never a pushable
        # page, so AdwSidebar's sidebar mode holds at every width.
        window.sidebar_list.set_mode(Adw.SidebarMode.SIDEBAR)
        window.sidebar_stack.set_visible_child_name(
            'icons' if rail else 'full')
        window.sidebar_compose_button.set_visible(not rail)
        # The mode button serves the full sidebar and the rail alike. A
        # dropdown arrow adds about 16px, which a 64px rail has no room
        # for, so the rail gets the bare icon, centred.
        window.sidebar_mode_button.set_always_show_arrow(not rail)
        window.sidebar_mode_button.set_halign(
            Gtk.Align.CENTER if rail else Gtk.Align.START)
        minimum, maximum = sidebarmode.widths(style, collapsed)
        if maximum < window.split_view.get_max_sidebar_width():
            # Narrowing: lower the minimum first, so the pair is never
            # left with a maximum below it.
            window.split_view.set_min_sidebar_width(minimum)
            window.split_view.set_max_sidebar_width(maximum)
        else:
            window.split_view.set_max_sidebar_width(maximum)
            window.split_view.set_min_sidebar_width(minimum)
        window.account_text_box.set_visible(not rail)
        window.account_chevron.set_visible(not rail)
        # The menu button's own padding plus the template margins are
        # what would make a 64px rail wider than its pane.
        for edge in ('start', 'end', 'top', 'bottom'):
            getattr(window.account_button, 'set_margin_' + edge)(
                0 if rail else 6)
        if rail and not self._rail_accounts:
            window.sidebar_list.set_prefix(None)
            window.sidebar_rail.set_account_button(window.account_button)
            self._rail_accounts = True
        elif not rail and self._rail_accounts:
            sidebarrail.detach(window.account_button)
            window.sidebar_list.set_prefix(window.account_button)
            self._rail_accounts = False
        window.sidebar_rail.set_selected(window.visible_tag())
        # The menu reports the stored state, not the last item picked:
        # F9 hides the sidebar without touching the menu.
        action = window.lookup_action('sidebar-mode')
        if action is not None:
            state = 'hidden' if window.settings.get_boolean('sidebar-hidden') \
                else style
            current = action.get_state()
            if current is None or current.get_string() != state:
                action.set_state(GLib.Variant.new_string(state))

    def sync_visibility(self):
        """Put the sidebar on screen or away, from the stored state and
        the collapsed layout's overlay."""
        window = self.window
        collapsed = window.split_view.get_collapsed()
        shown = sidebarmode.sidebar_visible(
            window.settings.get_boolean('sidebar-hidden'),
            collapsed, self._overlay_open)
        self._applying_sidebar_visibility = True
        try:
            window.split_view.set_show_sidebar(shown)
        finally:
            self._applying_sidebar_visibility = False
        window.props.sidebar_visible = shown
        window.props.sidebar_collapsed = collapsed
        # Collapsed, a swipe is the way in and out; pinned there is
        # nothing to swipe.
        window.split_view.set_enable_show_gesture(collapsed)
        window.split_view.set_enable_hide_gesture(collapsed)
        window._sync_translucent_sidebar()

    def dismiss_overlay(self):
        """Close the collapsed layout's sidebar overlay, if it is open."""
        window = self.window
        if not window.split_view.get_collapsed():
            return
        self._overlay_open = False
        window.split_view.set_show_sidebar(False)
        self.sync_visibility()

    def refresh(self, *_args):
        """Rebuild the sidebar: the built-in places (minus the ones the
        preferences hide), then the lists and hashtags pinned for the
        active account. The icon rail is filled in the same pass, so
        the two never disagree about what exists."""
        window = self.window
        current = window.visible_tag()
        sidebar = window.sidebar_list
        sidebar.remove_all()
        self._items = {}
        sections = {}
        rail_groups = {}
        hidden = {tag for tag, key in navigation.HIDE_KEYS.items()
                  if window.settings.get_boolean(key)}
        # Looked up on the window at call time so a test can rebind
        # window._restricted_public_places and still hit this path.
        closed = window._restricted_public_places()
        hidden |= closed
        for tag in closed:
            # A feed the server will not serve, or the reader has not
            # opted into, keeps neither a page nor a stream.
            window.navigation.forget(tag)
        for tag, icon, group in navigation.PLACES:
            if tag in hidden:
                continue
            section = sections.get(group)
            if section is None:
                section = sections[group] = Adw.SidebarSection()
                sidebar.append(section)
            title = navigation.place_title(tag)
            item = Adw.SidebarItem(title=title, icon_name=icon)
            if tag == 'notifications':
                item.set_suffix(window.notifications_badge)
            section.append(item)
            self._items[tag] = item
            rail_groups.setdefault(group, []).append((tag, icon, title))
        rail_sections = [rail_groups[group] for group in sorted(rail_groups)]
        if window.account is not None:
            self._append_pinned(sidebar, rail_sections)
        window.sidebar_rail.set_sections(rail_sections)
        if current is not None and current in hidden:
            window.show_page('home')
        elif current is not None:
            self.select_item(current)
        self.sync_mode()

    def _append_pinned(self, sidebar, rail_sections):
        window = self.window
        routes = window.navigation.pinned_routes(
            window.settings, window.account['id'])
        if not routes:
            return
        section = Adw.SidebarSection(title=_('Pinned'))
        sidebar.append(section)
        rail_entries = []
        list_ids = []
        for tag, title, icon in routes:
            if tag.startswith('list:'):
                list_ids.append(tag[5:])
            item = Adw.SidebarItem(title=title, icon_name=icon)
            section.append(item)
            self._items[tag] = item
            rail_entries.append((tag, icon, title))
        if rail_entries:
            rail_sections.append(rail_entries)
        if list_ids and not all(i in window.navigation.pinned_lists
                                for i in list_ids):
            self._load_pinned_list_titles()

    def _load_pinned_list_titles(self):
        window = self.window
        client = window.client

        def done(_client, result, _data=None):
            try:
                lists = api_lists.index_finish(client, result)
            except GLib.Error:
                return
            if client is not window.client:
                return
            window.navigation.set_pinned_lists(lists)
            for list_id, title in window.navigation.pinned_lists.items():
                item = self._items.get('list:%s' % list_id)
                if item is not None and item.get_title() != title:
                    item.set_title(title)
                page = window.navigation.pages.get('list:%s' % list_id)
                if page is not None:
                    page.set_title(title)
                window.sidebar_rail.set_title('list:%s' % list_id, title)

        api_lists.index_async(client, callback=done)

    def select_item(self, tag):
        window = self.window
        window.sidebar_rail.set_selected(tag)
        item = self._items.get(tag)
        if item is None:
            return
        for index in range(window.sidebar_list.get_items().get_n_items()):
            if window.sidebar_list.get_item(index) is item:
                window.sidebar_list.set_selected(index)
                return

    def on_activated(self, sidebar, index):
        item = sidebar.get_item(index)
        for tag, candidate in self._items.items():
            if candidate is item:
                self.window.show_page(tag)
                return
