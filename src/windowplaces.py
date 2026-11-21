# SPDX-License-Identifier: GPL-3.0-or-later
"""Places: the timeline pages, the composer and the navigation stack.

Which page a place tag resolves to, which places are restricted, and the
composer the window holds. A mixin of ``BabbleWindow``: the window owns
the page containers and the navigation controller, and this module owns
the rules that decide what goes in them.
"""
import gi

from .i18n import gettext as _, ngettext

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import pagenav  # noqa: E402
from . import uihelpers  # noqa: E402
from . import webcolumn  # noqa: E402
from .api import timelines as api_timelines  # noqa: E402
from .api.notifications import unread_badge_text  # noqa: E402
from .timelinepage import ApiTimelineSource, TimelinePage  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



# Opt-in for public timelines the server has closed to most accounts but
# still serves to this one (the "View live and topic feeds" permission).
SHOW_RESTRICTED_KEY = 'show-restricted-feeds'


class WindowPlaces:
    def _rebuild_home_timeline(self):
        """Throw the home page and its items away and build it again, for
        a setting that decides which posts it may show."""
        if self.timeline_page is None:
            return
        self.navigation.unregister_external('home')
        self.timeline_model = None
        self.ensure_home_timeline()

    def _mount_home(self):
        """PlaceHost: ensure home and return the template NavPage."""
        self.ensure_home_timeline()
        return self.page_home

    def _mount_notifications(self):
        """PlaceHost: ensure notifications and return the template NavPage.

        ``on_shown()`` is not called here: the page may already be cached
        in Navigation, so visit-time work runs from :meth:`show_page`.
        """
        self.ensure_notifications_page()
        return self.page_notifications

    def _clear_home_bin(self):
        """PlaceHost clear for home: bins only (maps already dropped)."""
        self.timeline_model = None
        self.home_bin.set_child(None)

    def ensure_home_timeline(self):
        """Create (or reuse, cached per window) the home timeline page."""
        if self.client is None:
            return
        current = self.timeline_page
        if current is not None and current.client is self.client:
            return
        if current is not None:
            self.navigation._stop_work(current)
            self.timeline_model = None
        # Cache the GListModel per window so navigation back preserves
        # scroll position and items.
        self.home_bin.set_child(None)
        page = TimelinePage(
            self.client, mode='home', model=self.timeline_model)
        self.timeline_model = page.model
        self.home_bin.set_child(page)
        self.navigation.register_external('home', self.page_home, page)
        self._apply_auto_refresh()

    def ensure_notifications_page(self):
        """Create (or reuse, cached per window) the notifications page.
        Built eagerly after login so unread counts reach the sidebar
        badge before the user opens the view."""
        from .notifications import NotificationsPage
        if self.client is None:
            return
        current = self.notifications_page
        if current is not None:
            if current.client is self.client:
                return
            self.navigation.unregister_external('notifications')
        page = NotificationsPage(self.client)
        self._unread_handler = page.connect(
            'unread-changed', self.on_unread_changed)
        self.notifications_bin.set_child(page)
        self.notifications_header_slot.append(page.header_widget)
        self.navigation.register_external(
            'notifications', self.page_notifications, page)

    def _clear_notifications_page(self, page=None):
        """PlaceHost clear for notifications: badge/bin only.

        *page* is the content widget when maps were already popped.
        """
        if page is not None and self._unread_handler is not None:
            # Otherwise an in-flight count from the old account lands on
            # the new one's badge.
            page.disconnect(self._unread_handler)
        self._unread_handler = None
        self.notifications_bin.set_child(None)
        for child in list(self.notifications_header_slot):
            self.notifications_header_slot.remove(child)
        self.on_unread_changed(None, 0)

    def on_unread_changed(self, page, count):
        if page is not None and page is not self.notifications_page:
            return
        text = unread_badge_text(count)
        self.notifications_badge.set_label(text)
        was_shown = self.notifications_badge.get_visible()
        self.notifications_badge.set_visible(bool(count))
        if count and not was_shown:
            uihelpers.flash_class(self.notifications_badge, 'just-toggled')
        if not count:
            description = ''
        elif text.endswith('+'):
            # Translators: accessible label of the notifications badge when
            # the server counted only up to its limit; %s is such as "100+".
            description = _('%s unread notifications') % text
        else:
            description = ngettext('%d unread notification',
                                   '%d unread notifications', count) % count
        self.notifications_badge.update_property(
            [Gtk.AccessibleProperty.LABEL], [description])
        self.props.unread_text = text
        self.props.unread_visible = bool(count)
        self.sidebar_rail.set_badge(count, text)

    def _timeline_place(self, kind, params, icon, title, description):
        page = TimelinePage(self.client, source=ApiTimelineSource(
            self.client, api_timelines.list_path(kind), params,
            paging='link' if kind in api_timelines.LINK_PAGED else 'id'))
        uihelpers.configure_empty_page(page.empty_page, icon, title, description)
        return page

    def _public_place_built(self, tag, page):
        self._apply_auto_refresh()
        self._sync_streaming()

    def ensure_composer(self):
        """The composer dialog, one per client, so a closed dialog keeps
        its draft until the next time it is presented."""
        from .composedialog import ComposeDialog
        if self.client is None:
            return None
        if self.compose_dialog is None \
                or self.compose_dialog.view.client is not self.client:
            self.compose_dialog = ComposeDialog(self.client)
        self.compose_dialog.view.local_draft_key = (
            account_store.account_key(self.account)
            if self.account is not None else None)
        return self.compose_dialog

    def present_composer(self):
        dialog = self.ensure_composer()
        if dialog is not None and dialog.get_parent() is None:
            dialog.present(self)
        return dialog

    def open_compose_reply(self, status_id, handle, visibility='public',
                           parent=None):
        """Called by timeline rows to reply to a status while keeping
        any draft in progress."""
        dialog = self.ensure_composer()
        if dialog is None:
            return
        dialog.start_reply(status_id, handle, visibility, parent=parent)
        self.present_composer()

    def compose_with_files(self, paths=()):
        """Open the composer with *paths* attached: what "Open With
        Babble" on an image or video does. Before a session exists the
        request waits, and is handed over once an account is signed in."""
        if self.client is None or self.account is None:
            pending = self._pending_compose or []
            pending.extend(p for p in paths if p not in pending)
            self._pending_compose = pending
            if paths and self.main_stack.get_visible_child_name() == 'login':
                self.show_toast(ngettext(
                    'Sign in to post the file',
                    'Sign in to post the files', len(paths)))
            return
        dialog = self.present_composer()
        if dialog is None:
            return
        if paths:
            # The composer's own intake: media only, up to the limit,
            # with a toast for whatever was left out.
            dialog.view.add_attachment_files(list(paths))

    def _flush_pending_compose(self):
        pending = self._pending_compose
        if pending is None:
            return
        self._pending_compose = None
        self.compose_with_files(pending)

    def _restricted_public_places(self):
        """The public timelines this account must not see in the sidebar.

        A feed the server closes to everyone but privileged accounts is
        hidden unless the reader turned the opt-in on; a feed it closes
        to this account outright is always hidden.

        The topic settings (``hashtag_feeds``/``trending_link_feeds``)
        are deliberately not consulted here: the server never closes them
        to a signed-in account, it just drops the remote posts, so the
        page still has local content and hiding it would lose read.

        Stays on the window (PlaceHost callback + feed policy), not on
        the sidebar host: refresh looks it up here at call time.
        """
        client = self.client
        if client is None:
            return set()
        from .streaming import FEED_RESTRICTED, FEED_UNAVAILABLE, public_feed_access
        access = public_feed_access(client, self.account)
        opted_in = self.settings.get_boolean(SHOW_RESTRICTED_KEY)
        closed = set()
        for place in ('local', 'federated'):
            state = access.get(place)
            if state == FEED_UNAVAILABLE \
                    or (state == FEED_RESTRICTED and not opted_in):
                closed.add(place)
        return closed

    def _nav_page_for(self, tag):
        return self.navigation.place_page(tag)

    def show_page(self, tag):
        """Make the page registered for ``tag`` the root of the content
        view and select its sidebar item. Pages are built once per
        session, which keeps their scroll position and loaded items."""
        if self.client is None:
            return
        previous = self.visible_tag()
        page = self._nav_page_for(tag)
        if page is None:
            return
        if previous == 'notifications' and tag != 'notifications' \
                and self.notifications_page is not None:
            self.notifications_page.on_hidden()
        # Visit-time catch-up / mark-read, including when Navigation
        # returned a cached notifications page (mount is skipped).
        if tag == 'notifications' and self.notifications_page is not None:
            self.notifications_page.on_shown()
            # GNOME keeps what it was shown; the page marks what it
            # displays as read, so anything left there is stale.
            self.notifier.withdraw_all()
        self.navigation_view.replace([page])
        self._select_item(tag)
        self.dismiss_sidebar_overlay()
        self._sync_sidebar_mode()

    def visible_tag(self):
        visible = self.navigation_view.get_visible_page()
        if visible is self.page_home:
            return 'home'
        if visible is self.page_notifications:
            return 'notifications'
        for tag, page in self.navigation.pages.items():
            if page is visible:
                return tag
        return None

    def go_back(self):
        if self.navigation_view.get_navigation_stack().get_n_items() > 1:
            self.navigation_view.pop()
        self.dismiss_sidebar_overlay()

    def go_home(self):
        if self.client is not None:
            self.show_page('home')

    def open_search(self):
        if self.client is None:
            return
        self.show_page('search')
        child = self.navigation.children.get('search')
        entry = getattr(child, 'entry', None)
        if entry is not None:
            entry.grab_focus()

    def _push_secondary(self, name, title):
        """Open one of the pages of the main menu on top of the current
        one (or back to it when it is already open)."""
        client = self.client
        if client is None:
            return
        if name == 'announcements':
            from .announcements import AnnouncementsPage as factory
        elif name == 'follow-requests':
            from .relations import FollowRequestsPage as factory
        elif name == 'mutes-blocks':
            from .relations import MutesBlocksPage as factory
        elif name == 'drafts':
            from .scheduled import DraftPostsPage as factory
        elif name == 'scheduled':
            from .scheduled import ScheduledPostsPage as factory
        else:
            from .collectionactions import CollectionsPage as factory
        pagenav.push_page(self, title, factory(client), tag='menu-%s' % name)

    def on_wrapped(self, _action, _param):
        from .wrapped import present_wrapped
        if self.client is not None:
            present_wrapped(self, self.client)

    def on_admin(self, _action, _param):
        from .admin import present_admin
        if self.client is not None:
            self._track_admin(present_admin(self, self.client))

    def _track_admin(self, dialog):
        """Remember the open admin dashboard so a streaming ``report``
        can refresh its reports list. Forgotten when it closes, so the
        window never keeps a closed dialog alive."""
        self._admin_dialog = None
        if dialog is None:
            return
        connect_weak(dialog, 'close-request', self._on_admin_closed)
        self._admin_dialog = dialog

    def _on_admin_closed(self, _dialog):
        self._admin_dialog = None

    def _refresh_admin_tool(self, tag):
        dialog = self._admin_dialog
        if dialog is not None:
            dialog.refresh_tool(tag)

    def on_view_profile(self, _action, _param):
        from .profile import push_profile
        if self.client is None or self.account is None:
            return
        push_profile(self.navigation_view, self.client, account=self.account)
        self.dismiss_sidebar_overlay()

    def _refreshable(self, nav_page):
        """The content widget of *nav_page* that knows how to refresh."""
        widget = nav_page.get_child()
        for _depth in range(4):
            if widget is None:
                return None
            if hasattr(widget, 'refresh'):
                return widget
            if isinstance(widget, Adw.ToolbarView):
                widget = widget.get_content()
            elif isinstance(widget, Adw.Bin):
                widget = widget.get_child()
            else:
                return None
        return None

    def on_close_pane(self, _action, _param):
        if not webcolumn.forward_action(self, 'close'):
            self.close()

    def on_back(self, _action, _param):
        if not webcolumn.forward_action(self, 'back'):
            self.go_back()

    def on_reload(self, _action, _param):
        if webcolumn.forward_action(self, 'reload'):
            return
        visible = self.navigation_view.get_visible_page()
        if visible is None:
            return
        if visible is self.page_home:
            target = self.timeline_page
        elif visible is self.page_notifications:
            target = self.notifications_page
        else:
            target = self._refreshable(visible)
        if target is not None:
            target.refresh()
