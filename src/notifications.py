# SPDX-License-Identifier: GPL-3.0-or-later
"""Notifications view: grouped notification rows with embedded post
snippets, all/mentions filtering, infinite scroll and an unread badge
signal consumed by the window sidebar."""

import datetime
import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, GLib, Gio, GObject, Gtk  # noqa: E402

from . import filterrules  # noqa: E402
from . import pages  # noqa: E402
from .api import notifications as api_notifications  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402
from .notificationcopy import type_offered  # noqa: E402
from .notificationctrl import (NotificationController,  # noqa: E402
                               filtering_supported)
from .notificationmodel import (  # noqa: E402
    NotificationModel, _clean_name, notification_links)
from .notificationpolicy import (  # noqa: E402
    EXCLUDED_TYPES_KEY, NOTIFICATION_TYPES, _store_type_visibility,
    present_notification_policy)
from .pages import (PagedList, PagedPosts, connect_weak,  # noqa: E402
                    launch_external,
                    push_page, settings)
from .statustext import parse_content  # noqa: E402
from .timelinebase import COLUMN_WIDTH, status_filter  # noqa: E402
from .timelinepage import _RefreshGesture  # noqa: E402


# Menu-only labels: SwitchRow titles keep the unadorned strings above.
_FILTER_MENU_LABELS = {
    'mention': _('_Mentions'),
    'status': _('_New Posts'),
    'reblog': _('_Boosts'),
    'favourite': _('_Favorites'),
    'follow': _('Foll_ows'),
    'follow_request': _('Follow _Requests'),
    'poll': _('_Polls'),
    'update': _('Post _Edits'),
    'quote': _('_Quotes'),
    'quoted_update': _('Quote E_dits'),
    'admin.sign_up': _('_Sign-Ups'),
    'admin.report': _('Repor_ts'),
    'severed_relationships': _('Se_vered Relationships'),
    'moderation_warning': _('Moderation _Warnings'),
    'annual_report': _('_Annual Reports'),
    'added_to_collection': _('Added to _Collections'),
    'collection_update': _('Collection _Updates'),
}


def _slug(kind):
    """Gio action names allow letters, digits and '-' only."""
    return re.sub(r'[^a-z0-9]+', '-', kind)


class NotificationsPage(NotificationController, PagedPosts, Adw.Bin):
    """Scrollable notifications list with all/mentions chips, a header
    menu (type filter, notification settings, announcements), infinite
    scroll, refresh, clear, unread badge signal, streaming and status
    pages.

    ``account_id`` restricts the list to one sender, including filtered
    notifications (the detail of a notification request).

    Emits ``unread-changed`` with the current unread count whenever it
    changes; the window shows the count as a sidebar badge.
    """

    __gtype_name__ = 'BabbleNotificationsPage'
    __gsignals__ = {
        'unread-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    FILTERS = {
        'all': None,
        'mentions': ['mention'],
    }

    def __init__(self, client, model=None, account_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.account_id = account_id
        self.filter = 'all'
        self.model = model if model is not None else NotificationModel()
        # Paging flags and cursors; min_id is what this endpoint wants
        # for "newer than". See PagedPosts.
        self._paging_init(newer_cursor='min_id')
        self._shown = False
        self._marked_read = False
        # The id of the marker POST in flight, and whether another was
        # asked for meanwhile. See NotificationController.mark_read.
        self._marking = None
        self._mark_again = False
        # The generation of a catch-up request, whose failure is quiet.
        # See NotificationController.catch_up.
        self._quiet_generation = None
        self._pin_top = False
        self._unread = 0
        self._applied_excluded = self._excluded_key()
        # Accounts whose relationship is unknown, waiting to be asked for
        # in one batch. See NotificationController._request_relationships.
        self._relationship_queue = {}
        self._relationship_busy = False

        self.loading_page = pages.make_loading_page(_('Loading Notifications'))
        # pages' own button says "_Try Again" with a mnemonic; this one
        # did not, so the same action read as two different strings in
        # the catalogue and only one of them had an underline.
        self.error_page = pages.make_error_page(
            _('Could Not Load Notifications'), self.refresh)
        self.empty_page = Adw.StatusPage(
            icon_name='babble-notifications-symbolic',
            title=_('No Notifications'),
            description=_('Mentions, boosts and follows will appear here'))

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.loading_page, 'loading')
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(self.empty_page, 'empty')

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class('notification-list-scroller')
        # No model until the list is first shown. The page is built at
        # sign-in for the sidebar badge and loads while hidden; with its
        # model attached, every page and streamed notification made the
        # view build and bind rows inside the HTTP callback, for a list
        # nobody was looking at. The model itself (and the badge, and the
        # sections) keeps up as before.
        # Lazy: notificationrow imports helpers from this module.
        from .notificationrow import NotificationFactory
        self.list_view = Gtk.ListView(
            factory=NotificationFactory(client),
            header_factory=NotificationFactory(client),
            single_click_activate=True)
        self.list_view.add_css_class('canvas-list')
        connect_weak(self.list_view, 'activate', self.on_row_activated)
        connect_weak(self.list_view, 'map', self._attach_model)
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')

        # Filtered notifications (Mastodon 4.3+): pending requests.
        self.requests_banner = Adw.Banner(button_label=_('View'),
                                          use_markup=False)
        self.requests_banner.set_button_style(
            Adw.BannerButtonStyle.SUGGESTED)
        connect_weak(self.requests_banner, 'button-clicked',
                     self.on_requests_banner_clicked)

        # Instance announcements: the button opens the Announcements page.
        self.banner = Adw.Banner(button_label=_('View'), use_markup=False)
        self.banner.set_button_style(Adw.BannerButtonStyle.SUGGESTED)
        connect_weak(self.banner, 'button-clicked', self.on_announcement_view)

        self.filter_group = Adw.ToggleGroup(
            halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        for name, label in (('all', _('All')), ('mentions', _('Mentions'))):
            self.filter_group.add(Adw.Toggle(name=name, label=label))
        self.filter_group.set_active_name('all')
        self.filter_group.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Filter Notifications')])
        connect_weak(self.filter_group, 'notify::active-name',
                     self.on_filter_changed)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.requests_banner)
        column.append(self.banner)
        column.append(self.filter_group)
        column.append(self.stack)
        self.set_child(column)

        _RefreshGesture(self, scrolled)
        adjustment = scrolled.get_vadjustment()
        connect_weak(adjustment, 'value-changed', self.on_scroll_position)

        self.header_widget = None
        if account_id is None:
            self._build_header()
            connect_weak(settings(), 'changed::' + EXCLUDED_TYPES_KEY,
                         self._on_excluded_changed)
        else:
            self.filter_group.set_visible(False)
        connect_weak(settings(), 'changed::dim-trivial-notifications',
                     self._update_dim)
        self._update_dim()

        if client.token:
            if account_id is None:
                self.fetch_unread()
                self.load_announcements()
                self.fetch_policy()
            self.load()

    # Header: type filter menu and clear button

    def _build_header(self):
        """Widgets packed into the page's header bar by the window: the
        options menu (Filter submenu with one check item per type) and
        the clear button."""
        self.actions = Gio.SimpleActionGroup()
        excluded = set(settings().get_strv(EXCLUDED_TYPES_KEY))
        filter_menu = Gio.Menu()
        for kind, label in NOTIFICATION_TYPES:
            name = 'show-' + _slug(kind)
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(kind not in excluded))
            connect_weak(action, 'change-state', self._on_type_toggled, kind)
            self.actions.add_action(action)
            if type_offered(self.client, kind):
                filter_menu.append(_FILTER_MENU_LABELS.get(kind, label),
                                   'notifs.' + name)
        for name, handler in (('policy', self.on_open_policy),
                              ('announcements', self.on_announcement_view)):
            action = Gio.SimpleAction.new(name, None)
            connect_weak(action, 'activate', handler)
            self.actions.add_action(action)

        menu = Gio.Menu()
        filters = Gio.Menu()
        filters.append_submenu(_('_Filter'), filter_menu)
        menu.append_section(None, filters)
        more = Gio.Menu()
        if filtering_supported(self.client):
            more.append(_('Notification _Settings'), 'notifs.policy')
        more.append(_('_Announcements'), 'notifs.announcements')
        menu.append_section(None, more)
        self.menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                          menu_model=menu)
        self.menu_button.add_css_class('flat')
        pages.set_accessible_label(self.menu_button,
                                   _('Notification Options'))
        self.menu_button.insert_action_group('notifs', self.actions)

        clear_button = Gtk.Button(icon_name='edit-clear-all-symbolic')
        clear_button.add_css_class('flat')
        pages.set_accessible_label(clear_button, _('Clear Notifications'))
        connect_weak(clear_button, 'clicked', self.on_clear_requested)

        box = Gtk.Box(spacing=0)
        box.append(self.menu_button)
        box.append(clear_button)
        self.header_widget = box

    def _excluded_key(self):
        if getattr(self, 'account_id', None) is not None:
            return ()
        return tuple(sorted(settings().get_strv(EXCLUDED_TYPES_KEY)))

    def _on_type_toggled(self, action, value, kind):
        """Check items show a type: unchecking adds it to the persisted
        exclusion list (sent as ``exclude_types[]``)."""
        action.set_state(value)
        _store_type_visibility(kind, value.get_boolean())

    def _on_excluded_changed(self, *_args):
        """The exclusion list changed (here or in Preferences): sync the
        check items and reload the list."""
        key = self._excluded_key()
        if key == self._applied_excluded:
            return
        self._applied_excluded = key
        for kind, _label in NOTIFICATION_TYPES:
            action = self.actions.lookup_action('show-' + _slug(kind))
            action.set_state(GLib.Variant.new_boolean(kind not in key))
        self._reload()

    def _update_dim(self, *_args):
        if settings().get_boolean('dim-trivial-notifications'):
            self.add_css_class('dim-enabled')
        else:
            self.remove_css_class('dim-enabled')

    def on_open_policy(self, *_args):
        present_notification_policy(self, self.client)

    # Activation

    def _attach_model(self, view):
        if view.get_model() is None:
            view.set_model(Gtk.NoSelection.new(self.model))

    def on_row_activated(self, _view, position):
        item = self.model.get_item(position)
        if item is None:
            return
        kind = item.kind
        if kind == 'admin.report':
            from .admin import present_admin
            present_admin(self, self.client)
            return
        if kind == 'annual_report':
            from .wrapped import present_wrapped
            report = item.payload.get('annual_report') or {}
            present_wrapped(self, self.client, report.get('year'))
            return
        if kind in ('severed_relationships', 'moderation_warning'):
            links = notification_links(item, self.client.instance)
            if links:
                launch_external(self, links[0][1])
            return
        if item.status_item is None and len(item.actors) > 1:
            # A merged group with no post (new followers, sign-ups) has no
            # single account to open; picking actors[0] opened a profile
            # nobody chose. Activating the card toggles its people list,
            # like the expander itself.
            factory = self.list_view.get_factory()
            row = factory.row_at(position) if factory is not None else None
            if row is not None and row.cluster_expander is not None:
                row.cluster_expander.set_expanded(
                    not row.cluster_expander.get_expanded())
            return
        root = self.get_root()
        view = getattr(root, 'navigation_view', None) if root else None
        if view is None:
            return
        if item.status_item is not None:
            from .thread import push_thread
            push_thread(view, self.client, item.status_item)
        else:
            from .profile import push_profile
            push_profile(view, self.client, item.actors[0])


# Filtered notification requests

class NotificationRequestRow(Adw.ActionRow):
    """One sender whose notifications were filtered: avatar, handle,
    notification count and the newest post, with Accept and Dismiss
    buttons; activating lists the sender's notifications."""

    __gtype_name__ = 'BabbleNotificationRequestRow'

    def __init__(self, page, request):
        super().__init__(activatable=True, use_markup=False)
        self.page = page
        self.request = request
        account = request['account']
        name = _clean_name(account)
        self.set_title(name)
        subtitle = '@' + account.get('acct', '')
        preview = self._preview(request.get('last_status'))
        if preview:
            subtitle += '\n' + preview
        self.set_subtitle(subtitle)
        self.set_title_lines(1)
        self.set_subtitle_lines(3)
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)

        count = int(request.get('notifications_count') or 0)
        count_label = Gtk.Label(
            label=ngettext('%d notification', '%d notifications', count)
            % count, valign=Gtk.Align.CENTER)
        count_label.add_css_class('caption')
        count_label.add_css_class('dimmed')
        self.add_suffix(count_label)

        self._answer_button('user-trash-symbolic', _('Dismiss'), 'dismiss')
        self._answer_button('object-select-symbolic', _('Accept'), 'accept')

    def _answer_button(self, icon_name, label, action):
        """Flat circular suffix button answering the request."""
        button = pages.icon_button(icon_name, label, css=('circular',))
        pages.connect_weak(button, 'clicked', self._on_answer_clicked, action)
        self.add_suffix(button)

    def _on_answer_clicked(self, _button, action):
        self.answer(action)

    @staticmethod
    def _preview(status):
        """One-line plain text of the newest post (content warning
        instead of the body when there is one)."""
        if not status:
            return ''
        rule = status_filter(status, filterrules.NOTIFICATIONS,
                             datetime.datetime.now(datetime.timezone.utc))
        if rule is not None and rule[0] != 'blur':
            return _('Filtered: %s') % rule[1] if rule[1] else _('Filtered')
        if status.get('spoiler_text'):
            return _('CW: %s') % status['spoiler_text']
        text = ' '.join(s['text'] for s in
                        parse_content(status.get('content') or '')
                        if s['text'] != '\n')
        return text.strip()

    def on_activate(self):
        title = _('Notifications from %s') % ('@' + self.request['account']
                                              .get('acct', ''))
        push_page(self, title,
                  RequestNotificationsPage(self.page.client, self.request,
                                           self.answer))

    def answer(self, action):
        """POST /api/v1/notifications/requests/:id/(accept|dismiss).

        mutate_row_api with remove=False: the page decides what leaving the
        list means here (a toast, and the request list refreshes), so the
        row stays until request_answered takes it.
        """
        self.page.mutate_row_api(
            self, lambda callback: api_notifications.answer_request_async(
                self.page.client, self.request['id'], action,
                callback=callback),
            on_done=lambda: self.page.request_answered(self, action),
            error_heading=_('Could not update the request'), remove=False,
            finish=api_notifications.answer_request_finish)


class NotificationRequestsPage(PagedList):
    """GET /api/v1/notifications/requests: senders whose notifications
    were filtered by the notification policy."""

    __gtype_name__ = 'BabbleNotificationRequestsPage'

    def __init__(self, client, on_changed=None, **kwargs):
        self._on_changed = on_changed
        intro = Gtk.Label(
            label=_('Notifications from people matching your filters wait '
                    'here until you accept or dismiss them'),
            xalign=0.0, wrap=True)
        intro.add_css_class('dimmed')
        super().__init__(
            client, api_notifications.list_path('requests'), self._make_row,
            empty_title=_('No Filtered Notifications'),
            empty_icon='babble-notifications-symbolic',
            empty_description=_('Notifications held back by your filters '
                                'will appear here'),
            header=intro, **kwargs)

    def _make_row(self, request):
        return NotificationRequestRow(self, request)

    def request_answered(self, row, action):
        from .pages import show_toast
        self.remove_row(row)
        show_toast(self, _('Notification request accepted')
                   if action == 'accept'
                   else _('Notification request dismissed'))
        if self._on_changed is not None:
            self._on_changed()


class RequestNotificationsPage(Adw.Bin):
    """The notifications of one filtered sender, with Accept and Dismiss
    in the header; either answer returns to the request list."""

    __gtype_name__ = 'BabbleRequestNotificationsPage'

    def __init__(self, client, request, on_answer):
        super().__init__()
        self._on_answer = on_answer
        self.inner = NotificationsPage(
            client, account_id=request['account']['id'])
        self.set_child(self.inner)
        dismiss = pages.icon_button('user-trash-symbolic', _('Dismiss'))
        pages.connect_weak(dismiss, 'clicked', self._on_answer_clicked,
                           'dismiss')
        accept = pages.icon_button('object-select-symbolic', _('Accept'))
        pages.connect_weak(accept, 'clicked', self._on_answer_clicked,
                           'accept')
        self.header_widgets = [dismiss, accept]

    def _on_answer_clicked(self, _button, action):
        self._answer(action)

    def _answer(self, action):
        from .pages import window_of
        self._on_answer(action)
        window = window_of(self)
        if window is not None:
            window.navigation_view.pop()

    def refresh(self):
        self.inner.refresh()
