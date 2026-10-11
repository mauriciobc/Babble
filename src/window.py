# SPDX-License-Identifier: GPL-3.0-or-later

import secrets
import urllib.parse

import gi

from .i18n import gettext as _, ngettext

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import emojichooser  # noqa: E402
from . import pages  # noqa: E402
from .mastodon import (MastodonClient, OAUTH_REDIRECT, OOB_REDIRECT,  # noqa: E402
                       clear_secret_async, normalize_instance,
                       summarize_error)
from .timeline import ApiTimelineSource, AvatarCache, TimelinePage  # noqa: E402

# One-click alternatives for the login suggestions row, ranked from the
# public JoinMastodon server directory (api.joinmastodon.org/servers)
# by weekly active users, largest communities first.
SUGGESTED_SERVERS = (
    'mastodon.social', 'mstdn.social', 'infosec.exchange', 'mstdn.jp',
    'hachyderm.io', 'piaille.fr', 'mastodon.world', 'social.vivaldi.net',
    'epsilon.social', 'troet.cafe', 'mastodon.uno', 'mathstodon.xyz')

# Authorization codes are handed out by the instance and spent by
# /oauth/token, so a ledger of this many recent codes outlives every
# redelivery a browser can be trusted to make of one redirect.
MAX_CONSUMED_CODES = 32

# Sidebar places in Tuba's order: (tag, icon, group). Titles are looked
# up at runtime so they translate.
PLACES = (
    ('home', 'user-home-symbolic', 0),
    ('notifications', 'babble-notifications-symbolic', 0),
    ('conversations', 'mail-unread-symbolic', 0),
    ('search', 'system-search-symbolic', 0),
    ('favourites', 'starred-symbolic', 0),
    ('bookmarks', 'babble-bookmarks-symbolic', 0),
    ('hashtags', 'babble-hashtag-symbolic', 0),
    ('explore', 'babble-explore-symbolic', 1),
    ('local', 'network-server-symbolic', 1),
    ('federated', 'babble-compose-globe-symbolic', 1),
    ('lists', 'view-list-symbolic', 1),
)

# Settings that hide a place from the sidebar.
HIDE_KEYS = {'explore': 'hide-trending', 'local': 'hide-local',
             'federated': 'hide-federated'}


def place_title(tag):
    return {
        'home': _('Home'), 'notifications': _('Notifications'),
        'conversations': _('Conversations'), 'search': _('Search'),
        'favourites': _('Favorites'), 'bookmarks': _('Bookmarks'),
        'hashtags': _('Hashtags'), 'explore': _('Explore'),
        'local': _('Local'), 'federated': _('Federated'),
        'lists': _('Lists'),
    }.get(tag, tag)


def pinned_entries(settings, key, account_id):
    """Values of the ``account-id/value`` entries of *key* that belong to
    the account."""
    prefix = '%s/' % account_id
    return [entry[len(prefix):] for entry in settings.get_strv(key)
            if entry.startswith(prefix)]


@Gtk.Template(resource_path='/io/github/mauriciobc/Babble/window.ui')
class BabbleWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'BabbleWindow'

    toast_overlay = Gtk.Template.Child()
    main_stack = Gtk.Template.Child()
    instance_entry = Gtk.Template.Child()
    instance_hint = Gtk.Template.Child()
    code_entry = Gtk.Template.Child()
    suggestions_section = Gtk.Template.Child()
    suggestions_box = Gtk.Template.Child()
    login_button = Gtk.Template.Child()
    login_button_label = Gtk.Template.Child()
    login_spinner = Gtk.Template.Child()
    cancel_login_button = Gtk.Template.Child()
    paste_code_button = Gtk.Template.Child()
    sign_up_link = Gtk.Template.Child()
    login_back_button = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar_list = Gtk.Template.Child()
    account_button = Gtk.Template.Child()
    account_avatar = Gtk.Template.Child()
    account_name_label = Gtk.Template.Child()
    account_handle_label = Gtk.Template.Child()
    content_page = Gtk.Template.Child()
    navigation_view = Gtk.Template.Child()
    home_bin = Gtk.Template.Child()
    notifications_bin = Gtk.Template.Child()
    notifications_header_slot = Gtk.Template.Child()
    page_home = Gtk.Template.Child()
    page_notifications = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = None
        self.account = None
        self.timeline_page = None
        self.timeline_model = None
        self.compose_dialog = None
        self.notifications_page = None
        self._unread_handler = None
        self.public_pages = {}
        self._place_pages = {}
        self._place_children = {}
        self._items = {}
        self._pinned_lists = {}
        self._held = False
        self._background_asked = False
        self._background_pending = False
        self._auth_client = None
        self._fallback = False
        # One authorization attempt is one click on the login button and
        # the redirect (or pasted code) that answers it. The attempt id
        # tells replies apart, the ledger spends each code exactly once.
        self._auth_attempt = 0
        self._settled_attempt = -1
        self._pending_code = None
        self._consumed_codes = {}
        # Reading the keyring is an out-of-process round trip, so a
        # restore or switch can still be in flight when the next one
        # starts. This id tells the replies apart: only the newest
        # lookup is allowed to touch the session.
        self._session_lookup = 0
        self.settings = pages.settings()
        self.settings.bind('window-width', self, 'default-width', Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-height', self, 'default-height', Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-maximized', self, 'maximized', Gio.SettingsBindFlags.DEFAULT)

        self.notifications_badge = Gtk.Label(visible=False)
        self.notifications_badge.add_css_class('notifications-badge')
        self.notifications_badge.add_css_class('caption-heading')
        self.notifications_badge.add_css_class('numeric')

        from .desktopnotify import DesktopNotifier
        from .streaming import StreamingManager
        self.notifier = DesktopNotifier(self)
        self.streaming = StreamingManager()
        self.streaming.connect('event', self._on_stream_event)
        self.streaming.connect('connection-changed',
                               self._on_stream_connection_changed)

        actions = (
            ('logout', self.on_logout_requested),
            ('show-notifications',
             lambda *a: self.show_page('notifications')),
            ('compose', lambda *a: self.present_composer()),
            ('view-profile', self.on_view_profile),
            ('reload', self.on_reload),
            ('back', lambda *a: self.go_back()),
            ('back-home', lambda *a: self.go_home()),
            ('search', lambda *a: self.open_search()),
            ('announcements', lambda *a: self._push_secondary(
                'announcements', _('Announcements'))),
            ('follow-requests', lambda *a: self._push_secondary(
                'follow-requests', _('Follow Requests'))),
            ('mutes-blocks', lambda *a: self._push_secondary(
                'mutes-blocks', _('Mutes & Blocks'))),
            ('drafts', lambda *a: self._push_secondary(
                'drafts', _('Draft Posts'))),
            ('scheduled', lambda *a: self._push_secondary(
                'scheduled', _('Scheduled Posts'))),
            ('collections', lambda *a: self._push_secondary(
                'collections', _('Collections'))),
            ('wrapped', self.on_wrapped),
            ('admin', self.on_admin))
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
        goto = Gio.SimpleAction.new('goto-place', GLib.VariantType.new('s'))
        goto.connect('activate', lambda _a, param: self.show_page(
            param.get_string()))
        self.add_action(goto)
        self._update_session_actions()

        self.switcher = account_store.AccountSwitcher(self.settings)
        self.account_button.set_popover(self.switcher)
        self.switcher.connect('switch', lambda _s, account_id:
                              self.switch_account(account_id))
        self.switcher.connect('remove', self.on_remove_account_requested)
        self.switcher.connect('add', lambda _s: self.begin_add_account())

        from . import preferences
        self._preferences = preferences
        self.settings.connect('changed', self._on_setting_changed)
        Adw.StyleManager.get_default().connect(
            'notify::dark', lambda *a: preferences.apply_style(
                self, self.settings))
        preferences.apply_style(self, self.settings)
        self.connect('close-request', self._on_close_request)

        self._refresh_sidebar()
        self.refresh_login_widgets()
        self.restore_account()

    # Settings

    def _on_setting_changed(self, settings, key):
        if key in self._preferences.STYLE_KEYS:
            self._preferences.apply_style(self, settings)
        elif key == 'auto-refresh':
            self._apply_auto_refresh()
        elif key in HIDE_KEYS.values() \
                or key in ('favorite-lists-ids', 'favorite-tags-ids'):
            self._refresh_sidebar()
        elif key in ('live-updates', 'public-live-updates'):
            self._sync_streaming()

    # Session

    def restore_account(self):
        account = self.get_stored_account()
        if account is None:
            self.main_stack.set_visible_child_name('login')
            return
        self._activate_account(account, login_only=True)

    def _activate_account(self, account, login_only=False):
        """Read *account*'s token from the keyring and make it the
        session.

        The read is an out-of-process round trip, so a second restore or
        switch can start before the first answers. Only the newest
        lookup may touch the session: a stale reply that returned a
        token would show the wrong account, and one that returned None
        would forget an account that is in fact signed in.
        """
        account_id = account['id']
        client = MastodonClient(account['instance'])
        lookup = self._session_lookup = self._session_lookup + 1

        def loaded(token):
            if lookup != self._session_lookup:
                return
            if token is None:
                if login_only:
                    self.remove_account(account_id)
                    self.main_stack.set_visible_child_name('login')
                else:
                    self.show_toast(
                        _('The login for @%s is gone from the keyring')
                        % account.get('acct', ''))
                return
            if not login_only:
                self._teardown_session()
            client.token = token
            self.client = client
            self.account = account
            if not login_only:
                self.settings.set_string('active-account', account_id)
            self.show_shell()

        # The keyring is an out-of-process round trip; the token arrives
        # later and the shell is built then.
        client.load_secret_token(account_id, loaded)

    def get_stored_account(self):
        return account_store.active_account(self.settings)

    def store_account(self, account):
        account_store.save_account(self.settings, account, activate=True)

    def remove_account(self, account_id):
        account_store.forget_account(self.settings, account_id)

    def _update_session_actions(self):
        signed_in = self.client is not None and self.account is not None
        for name in ('logout', 'show-notifications', 'compose',
                     'view-profile', 'reload', 'back-home', 'search',
                     'announcements', 'follow-requests', 'mutes-blocks',
                     'drafts', 'scheduled', 'wrapped', 'goto-place'):
            self.lookup_action(name).set_enabled(signed_in)
        info = (getattr(self.client, 'instance_info', None)
                if signed_in else None) or {}
        versions = info.get('api_versions') or {}
        self.lookup_action('collections').set_enabled(
            signed_in and (versions.get('mastodon') or 0) >= 10)
        self.lookup_action('admin').set_enabled(
            signed_in and account_store.role_is_admin(self.account))

    def _teardown_session(self):
        """Drop every page, stream and dialog tied to the current
        client, before another account takes over."""
        self.streaming.stop()
        emojichooser.deactivate_instance_emojis()
        self.notifier.reset()
        if self.compose_dialog is not None:
            self.compose_dialog.close()
        self.compose_dialog = None
        self.timeline_page = None
        self.timeline_model = None
        self.home_bin.set_child(None)
        self._clear_notifications_page()
        self.public_pages.clear()
        self._place_pages.clear()
        self._place_children.clear()
        self._pinned_lists = {}
        self.navigation_view.replace([self.page_home])

    def switch_account(self, account_id):
        account = account_store.find_account(self.settings, account_id)
        if account is None:
            return
        self._activate_account(account)

    @staticmethod
    def _grab_focus(widget):
        """Focus *widget* only when it is mapped (unit tests skip
        :meth:`Gtk.Window.present`)."""
        if widget is not None and widget.get_mapped():
            widget.grab_focus()

    def begin_add_account(self):
        """Show the login page on top of the running session; Back
        returns to it."""
        self.begin_auth_attempt()
        self._reset_login()
        self.login_back_button.set_visible(self.client is not None)
        self.main_stack.set_visible_child_name('login')
        self._grab_focus(self.instance_entry)

    @Gtk.Template.Callback()
    def on_login_back_clicked(self, _button):
        self.begin_auth_attempt()
        self._reset_login()
        if self.client is not None and self.account is not None:
            self.main_stack.set_visible_child_name('shell')

    def on_remove_account_requested(self, _switcher, account_id):
        account = account_store.find_account(self.settings, account_id)
        if account is None:
            return
        pages.confirm(
            self, _('Remove Account?'),
            _('The login for %s will be removed from this device.')
            % account_store.full_handle(account),
            _('_Remove'), lambda: self._remove_account_now(account))

    def _remove_account_now(self, account):
        clear_secret_async(account['instance'], account['id'])
        was_active = self.account is not None \
            and self.account['id'] == account['id']
        remaining = account_store.forget_account(self.settings, account['id'])
        if not was_active:
            return
        self._teardown_session()
        self.account = None
        self.client = None
        if remaining is not None:
            self.switch_account(remaining['id'])
        else:
            self._sign_out_to_login()

    def _sign_out_to_login(self):
        self.account_avatar.set_custom_image(None)
        self._update_session_actions()
        self.login_back_button.set_visible(False)
        self.begin_auth_attempt()
        self._reset_login()
        self.main_stack.set_visible_child_name('login')

    def show_shell(self):
        name = account_store.plain_name(self.account)
        self.account_name_label.set_label(name)
        self.account_handle_label.set_label(
            account_store.full_handle(self.account))
        self.account_avatar.set_text(name)
        AvatarCache.load(self.account_avatar, self.account)
        self._update_session_actions()
        self._refresh_sidebar()
        self.ensure_home_timeline()
        self.ensure_notifications_page()
        self.show_page('home')
        self.split_view.set_show_content(True)
        self.login_back_button.set_visible(False)
        self.main_stack.set_visible_child_name('shell')
        self._load_session_info()
        self._load_instance_emojis()

    def _load_instance_emojis(self):
        """The instance's whole custom-emoji set, fetched once per
        session: it is the fallback layer every rendered ``:shortcode:``
        resolves against, so it cannot wait for the composer to ask
        for it (Tuba gathers these as the account comes up)."""
        client = self.client
        emojichooser.activate_instance_emojis(client)

        def loaded(_emojis):
            if client is self.client:
                emojichooser.activate_instance_emojis(client)

        emojichooser.load_custom_emojis(client, loaded)

    def _load_session_info(self):
        """Instance details (limits, streaming URL, API level) and the
        fresh account entity (role, display name)."""
        client = self.client
        if not hasattr(client, 'instance_info_async'):
            return

        def info_done(_client, result, _data=None):
            try:
                client.instance_info_finish(result)
            except GLib.Error:
                pass
            if client is self.client:
                self._update_session_actions()
                self._sync_streaming()

        client.instance_info_async(callback=info_done)

        def account_done(_client, result, _data=None):
            try:
                fresh = client.verify_credentials_finish(result)
            except GLib.Error:
                return
            if client is not self.client or self.account is None \
                    or fresh.get('id') != self.account.get('id'):
                return
            fresh['instance'] = self.account['instance']
            self.account = fresh
            account_store.save_account(self.settings, fresh, activate=False)
            self._update_session_actions()

        client.verify_credentials_async(callback=account_done)

    def on_account_updated(self, account):
        """The profile editor saved: show the new name and avatar."""
        account = dict(account, instance=self.account['instance'])
        self.account = account
        account_store.save_account(self.settings, account, activate=False)
        self.account_name_label.set_label(account_store.plain_name(account))
        AvatarCache.load(self.account_avatar, account)

    # Streaming and desktop notifications

    def _sync_streaming(self):
        if self.client is None or not self.client.token:
            return
        if not self.settings.get_boolean('live-updates'):
            self.streaming.stop()
            self.notifier.start_polling()
            return
        from .streaming import DIRECT, FEDERATED, LOCAL
        wanted = set()
        if 'conversations' in self._place_children:
            # Direct messages only arrive on the ``direct`` stream.
            wanted.add(DIRECT)
        if self.settings.get_boolean('public-live-updates'):
            if 'local' in self.public_pages:
                wanted.add(LOCAL)
            if 'federated' in self.public_pages:
                wanted.add(FEDERATED)
        self.streaming.set_streams(wanted)
        if not self.streaming.connected:
            self.streaming.start(self.client)
            # Many instances sit behind CDNs that reject the upgrade;
            # poll until the socket actually opens.
            self.notifier.start_polling()

    def _on_stream_connection_changed(self, _manager, connected):
        if connected:
            self.notifier.stop_polling()
        elif self.client is not None:
            self.notifier.start_polling()

    def _timeline_pages(self):
        found = [self.timeline_page, *self.public_pages.values()]
        for child in self._place_children.values():
            found.append(getattr(child, 'timeline', child))
        return [page for page in found if isinstance(page, TimelinePage)]

    def _on_stream_event(self, _manager, stream, name, payload):
        from .streaming import FEDERATED, LOCAL
        if name == 'update':
            if stream == LOCAL:
                target = self.public_pages.get('local')
            elif stream == FEDERATED:
                target = self.public_pages.get('federated')
            else:
                target = self.timeline_page
            if target is not None:
                target.on_stream_update(payload)
            return
        if name == 'delete':
            for page in self._timeline_pages():
                page.on_stream_delete(payload)
            conversations = self._place_children.get('conversations')
            if conversations is not None:
                conversations.on_stream_delete(payload)
            return
        if name == 'status.update':
            for page in self._timeline_pages():
                page.on_stream_status_update(payload)
            return
        if name == 'notification':
            if self.notifications_page is not None:
                self.notifications_page.on_stream_notification(payload)
            self.notifier.handle(payload)
            return
        if name == 'conversation':
            conversations = self._place_children.get('conversations')
            if conversations is not None:
                conversations.on_stream_conversation(payload)

    def _on_close_request(self, _window):
        """With background mode on, closing hides the window and keeps
        the application (and its notifications) running."""
        if self.client is None or not self.settings.get_boolean(
                'work-in-background'):
            # A hold left from an earlier hide would outlive the window.
            self._release_hold()
            return False
        app = self.get_application()
        if app is not None and not self._held:
            app.hold()
            self._held = True
        self.set_visible(False)
        if not self._background_asked:
            self.request_background()
        return True

    # Background portal

    def request_background(self):
        """Ask the XDG Background portal to let the app run with its
        window closed. A refusal turns ``work-in-background`` off and
        says so; no portal (unsandboxed host, D-Bus error) is silently
        ignored. Asked at most once at a time; fully async."""
        if self._background_pending:
            return
        self._background_pending = True
        self._background_asked = True
        Gio.bus_get(Gio.BusType.SESSION, None, _on_background_bus,
                    self.weak_ref())

    def _background_done(self, allowed):
        self._background_pending = False
        if allowed:
            return
        self.settings.set_boolean('work-in-background', False)
        pages.show_toast(self, _('Running in the background was not allowed'))
        # Released even when the window was shown again meanwhile, so
        # its next close is not kept alive by a stale hold.
        self._release_hold()
        if not self.get_visible():
            # Hidden by a close the portal now refuses: finish closing.
            self.close()

    def _background_failed(self):
        # No portal or a D-Bus error: nothing refused, so stay in the
        # background, but ask again on the next close in case the
        # failure was transient.
        self._background_pending = False
        self._background_asked = False

    def _release_hold(self):
        app = self.get_application()
        if app is not None and self._held:
            app.release()
            self._held = False

    # Pages

    def ensure_home_timeline(self):
        """Create (or reuse, cached per window) the home timeline page."""
        if self.client is None:
            return
        if self.timeline_page is not None \
                and self.timeline_page.client is self.client:
            return
        if self.timeline_page is not None:
            self.timeline_model = None
        # Cache the GListModel per window so navigation back preserves
        # scroll position and items.
        self.home_bin.set_child(None)
        self.timeline_page = TimelinePage(self.client, mode='home',
                                          model=self.timeline_model)
        self.timeline_model = self.timeline_page.model
        self.home_bin.set_child(self.timeline_page)
        self._apply_auto_refresh()

    def ensure_notifications_page(self):
        """Create (or reuse, cached per window) the notifications page.
        Built eagerly after login so unread counts reach the sidebar
        badge before the user opens the view."""
        from .notifications import NotificationsPage
        if self.client is None:
            return
        if self.notifications_page is not None:
            if self.notifications_page.client is self.client:
                return
            self._clear_notifications_page()
        self.notifications_page = NotificationsPage(self.client)
        self._unread_handler = self.notifications_page.connect(
            'unread-changed', self.on_unread_changed)
        self.notifications_bin.set_child(self.notifications_page)
        self.notifications_header_slot.append(
            self.notifications_page.header_widget)

    def _clear_notifications_page(self):
        page = self.notifications_page
        self.notifications_page = None
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
        self.notifications_badge.set_label(str(count) if count else '')
        self.notifications_badge.set_visible(bool(count))
        description = ngettext('%d unread notification',
                               '%d unread notifications', count) % count \
            if count else ''
        self.notifications_badge.update_property(
            [Gtk.AccessibleProperty.LABEL], [description])

    def _timeline_place(self, path, params, icon, title, description):
        page = TimelinePage(self.client, source=ApiTimelineSource(
            self.client, path, params,
            paging='link' if path in ('/favourites', '/bookmarks') else 'id'))
        page.empty_page.set_icon_name(icon)
        page.empty_page.set_title(title)
        page.empty_page.set_description(description)
        return page

    def _build_place(self, tag):
        """The content widget of a sidebar place other than Home and
        Notifications, or None for an unknown tag."""
        client = self.client
        if tag == 'conversations':
            from .conversations import ConversationsPage
            return ConversationsPage(client)
        if tag == 'search':
            from .search import SearchPage
            return SearchPage(client)
        if tag == 'favourites':
            return self._timeline_place(
                '/favourites', None, 'starred-symbolic',
                _('No Favorites Yet'),
                _('Posts you favorite will appear here'))
        if tag == 'bookmarks':
            return self._timeline_place(
                '/bookmarks', None, 'babble-bookmarks-symbolic',
                _('No Bookmarks Yet'),
                _('Posts you bookmark will appear here'))
        if tag == 'hashtags':
            from .hashtags import FollowedHashtagsPage
            return FollowedHashtagsPage(client)
        if tag == 'explore':
            from .explore import ExplorePage
            return ExplorePage(client)
        if tag in ('local', 'federated'):
            params = {'local': 'true'} if tag == 'local' else None
            page = self._timeline_place(
                '/timelines/public', params, 'network-server-symbolic',
                _('No Posts Yet'), _('New posts will appear here'))
            self.public_pages[tag] = page
            self._apply_auto_refresh()
            self._sync_streaming()
            return page
        if tag == 'lists':
            from .lists import ListsPage
            return ListsPage(client)
        if tag.startswith('list:'):
            from .lists import ListTimelinePage
            list_id = tag[5:]
            return ListTimelinePage(client, {
                'id': list_id,
                'title': self._pinned_lists.get(list_id, _('List'))})
        if tag.startswith('tag:'):
            from .hashtags import HashtagPage
            return HashtagPage(client, [tag[4:]])
        return None

    def _place_page(self, tag):
        page = self._place_pages.get(tag)
        if page is not None:
            return page
        child = self._build_place(tag)
        if child is None:
            return None
        title = self._place_title(tag)
        page = pages.make_nav_page(title, child, tag='place-%s' % tag)
        self._place_pages[tag] = page
        self._place_children[tag] = child
        if tag == 'conversations':
            self._sync_streaming()
        return page

    def _place_title(self, tag):
        if tag.startswith('list:'):
            return self._pinned_lists.get(tag[5:], _('List'))
        if tag.startswith('tag:'):
            return '#' + tag[4:]
        return place_title(tag)

    # Composer

    def ensure_composer(self):
        """The composer dialog, one per client, so a closed dialog keeps
        its draft until the next time it is presented."""
        from .compose import ComposeDialog
        if self.client is None:
            return None
        if self.compose_dialog is None \
                or self.compose_dialog.view.client is not self.client:
            self.compose_dialog = ComposeDialog(self.client)
        return self.compose_dialog

    def present_composer(self):
        dialog = self.ensure_composer()
        if dialog is not None and dialog.get_parent() is None:
            dialog.present(self)

    def open_compose_reply(self, status_id, handle, visibility='public'):
        """Called by timeline rows to reply to a status while keeping
        any draft in progress."""
        dialog = self.ensure_composer()
        if dialog is None:
            return
        dialog.start_reply(status_id, handle, visibility)
        self.present_composer()

    # Sidebar

    def _refresh_sidebar(self, *_args):
        """Rebuild the sidebar: the built-in places (minus the ones the
        preferences hide), then the lists and hashtags pinned for the
        active account."""
        current = self.visible_tag()
        sidebar = self.sidebar_list
        sidebar.remove_all()
        self._items = {}
        sections = {}
        hidden = {tag for tag, key in HIDE_KEYS.items()
                  if self.settings.get_boolean(key)}
        for tag, icon, group in PLACES:
            if tag in hidden:
                continue
            section = sections.get(group)
            if section is None:
                section = sections[group] = Adw.SidebarSection()
                sidebar.append(section)
            item = Adw.SidebarItem(title=place_title(tag), icon_name=icon)
            if tag == 'notifications':
                item.set_suffix(self.notifications_badge)
            section.append(item)
            self._items[tag] = item
        if self.account is not None:
            self._append_pinned(sidebar)
        if current is not None and current in hidden:
            self.show_page('home')
        elif current is not None:
            self._select_item(current)

    def _append_pinned(self, sidebar):
        account_id = self.account['id']
        list_ids = pinned_entries(self.settings, 'favorite-lists-ids',
                                  account_id)
        tags = pinned_entries(self.settings, 'favorite-tags-ids', account_id)
        if not list_ids and not tags:
            return
        section = Adw.SidebarSection(title=_('Pinned'))
        sidebar.append(section)
        for list_id in list_ids:
            tag = 'list:%s' % list_id
            title = self._pinned_lists.get(list_id) or _('List')
            item = Adw.SidebarItem(title=title, icon_name='view-list-symbolic')
            section.append(item)
            self._items[tag] = item
        for name in tags:
            tag = 'tag:%s' % name
            item = Adw.SidebarItem(title='#' + name,
                                   icon_name='babble-hashtag-symbolic')
            section.append(item)
            self._items[tag] = item
        if list_ids and not all(i in self._pinned_lists for i in list_ids):
            self._load_pinned_list_titles()

    def _load_pinned_list_titles(self):
        client = self.client

        def done(_client, result, _data=None):
            try:
                lists = client.request_finish(result)
            except GLib.Error:
                return
            if client is not self.client:
                return
            self._pinned_lists = {str(item['id']): item.get('title', '')
                                  for item in lists}
            for list_id, title in self._pinned_lists.items():
                item = self._items.get('list:%s' % list_id)
                if item is not None and item.get_title() != title:
                    item.set_title(title)
                page = self._place_pages.get('list:%s' % list_id)
                if page is not None:
                    page.set_title(title)

        client.request_async('GET', '/lists', callback=done)

    def _select_item(self, tag):
        item = self._items.get(tag)
        if item is None:
            return
        for index in range(self.sidebar_list.get_items().get_n_items()):
            if self.sidebar_list.get_item(index) is item:
                self.sidebar_list.set_selected(index)
                return

    @Gtk.Template.Callback()
    def on_sidebar_activated(self, sidebar, index):
        item = sidebar.get_item(index)
        for tag, candidate in self._items.items():
            if candidate is item:
                self.show_page(tag)
                return

    # Navigation

    def _nav_page_for(self, tag):
        if tag == 'home':
            self.ensure_home_timeline()
            return self.page_home
        if tag == 'notifications':
            self.ensure_notifications_page()
            if self.notifications_page is not None:
                self.notifications_page.on_shown()
            return self.page_notifications
        return self._place_page(tag)

    def show_page(self, tag):
        """Make the page registered for ``tag`` the root of the content
        view and select its sidebar item. Pages are built once per
        session, which keeps their scroll position and loaded items."""
        if self.client is None:
            return
        page = self._nav_page_for(tag)
        if page is None:
            return
        self.navigation_view.replace([page])
        self.content_page.set_title(page.get_title())
        self._select_item(tag)
        self.split_view.set_show_content(True)

    def visible_tag(self):
        visible = self.navigation_view.get_visible_page()
        if visible is self.page_home:
            return 'home'
        if visible is self.page_notifications:
            return 'notifications'
        for tag, page in self._place_pages.items():
            if page is visible:
                return tag
        return None

    def go_back(self):
        if self.navigation_view.get_navigation_stack().get_n_items() > 1:
            self.navigation_view.pop()

    def go_home(self):
        if self.client is not None:
            self.show_page('home')

    def open_search(self):
        if self.client is None:
            return
        self.show_page('search')
        child = self._place_children.get('search')
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
            from .fedicollections import CollectionsPage as factory
        pages.push_page(self, title, factory(client), tag='menu-%s' % name)

    def on_wrapped(self, _action, _param):
        from .wrapped import present_wrapped
        if self.client is not None:
            present_wrapped(self, self.client)

    def on_admin(self, _action, _param):
        from .admin import present_admin
        if self.client is not None:
            present_admin(self, self.client)

    def on_view_profile(self, _action, _param):
        from .profile import push_profile
        if self.client is None or self.account is None:
            return
        push_profile(self.navigation_view, self.client, account=self.account)
        self.split_view.set_show_content(True)

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

    def on_reload(self, _action, _param):
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
    # Login

    @Gtk.Template.Callback()
    def on_instance_changed(self, entry):
        self.refresh_login_widgets()

    def refresh_login_widgets(self):
        """Sync the login column with the typed server and the flow.

        With no server typed it shows the example hint and a plain
        Continue button; once a server name is in, the hint gives way to
        the suggestions and the button names the chosen server. While an
        authorization is in flight the button shows a spinner and a
        Cancel button offers a way out.
        """
        waiting = self._auth_client is not None
        domain = self._instance_domain(self.instance_entry.get_text())
        self.instance_entry.set_sensitive(not waiting)
        self.login_button.set_sensitive(not waiting and bool(domain))
        self.login_spinner.set_visible(waiting)
        self.cancel_login_button.set_visible(waiting)
        if waiting:
            label = _('Waiting for Authorization')
        elif domain:
            label = _('_Continue with %s') % domain.replace('_', '__')
        else:
            label = _('_Continue')
        self.login_button_label.set_label(label)
        self.instance_hint.set_visible(not domain and not waiting)
        self.suggestions_section.set_visible(bool(domain) and not waiting)
        self._update_suggestions(domain)
        if domain:
            self.sign_up_link.set_uri('https://%s/auth/sign_up' % domain)
        else:
            self.sign_up_link.set_uri('https://joinmastodon.org/servers')

    @staticmethod
    def _instance_domain(text):
        """The bare server name typed, without scheme, path or case.

        An explicit ``http://`` is refused rather than quietly upgraded,
        so a user who typed it is told the connection would be
        plaintext instead of being sent somewhere they did not name.
        """
        text = (text or '').strip()
        if '://' in text:
            scheme, _, rest = text.partition('://')
            if scheme.lower() != 'https':
                return ''
            text = rest
        try:
            url = urllib.parse.urlsplit(normalize_instance(text))
        except ValueError:
            return ''
        return (url.netloc or url.path).lower().strip('.')

    def _update_suggestions(self, typed):
        """Refill the one-click alternatives.

        Servers matching the typed text rank first (prefix before
        substring), the typed server itself never repeats under the
        entry, and the directory order keeps everything else.
        """
        box = self.suggestions_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        if not typed:
            return
        ranked = sorted(
            (domain for domain in SUGGESTED_SERVERS if domain != typed),
            key=lambda domain: (not domain.startswith(typed),
                                typed not in domain,
                                SUGGESTED_SERVERS.index(domain)))
        for domain in ranked[:5]:
            button = Gtk.Button(label=domain)
            button.connect('clicked', self.on_suggestion_clicked, domain)
            box.append(button)

    def on_suggestion_clicked(self, _button, domain):
        """A suggestion is one click: adopt it as the chosen server."""
        self.instance_entry.set_text(domain)
        self.instance_entry.set_position(-1)
        self.refresh_login_widgets()
        self._grab_focus(self.login_button)

    def begin_auth_attempt(self):
        """Open the next authorization attempt.

        Ids increase monotonically, which both labels the redirect that
        answers this attempt and invalidates any reply still in flight
        for a superseded one.
        """
        self._auth_attempt += 1
        self._pending_code = None
        return self._auth_attempt

    def consume_code(self, code):
        """Spend an authorization code, returning False if already spent.

        The instance accepts a code once, so a redelivered redirect must
        not start a second exchange: doing so raced the success of the
        first one against an ``invalid_grant`` toast. The ledger only
        has to outlive the retries of the redirect it is answering.
        """
        if code in self._consumed_codes:
            return False
        self._consumed_codes[code] = self._auth_attempt
        while len(self._consumed_codes) > MAX_CONSUMED_CODES:
            del self._consumed_codes[next(iter(self._consumed_codes))]
        return True

    @Gtk.Template.Callback()
    def on_login_clicked(self, _widget):
        typed = self.instance_entry.get_text()
        domain = self._instance_domain(typed)
        if self._auth_client is not None:
            return
        if not domain:
            if '://' in (typed or '') \
                    and not typed.strip().lower().startswith('https://'):
                self.show_error(
                    _('Could not connect to the server'),
                    # Translators: keep "https://" untranslated
                    _('Babble only connects over HTTPS. Enter the server '
                      'address without a scheme, or with https://.'))
            return
        self._auth_client = MastodonClient(normalize_instance(domain))
        self.begin_auth_attempt()
        self.refresh_login_widgets()
        self._auth_client.register_app_async(self.on_app_registered)

    @Gtk.Template.Callback()
    def on_cancel_login_clicked(self, _button):
        # A reply still in flight for the abandoned attempt is ignored.
        self.begin_auth_attempt()
        self._reset_login()
        self.toast_overlay.dismiss_all()
        self._grab_focus(self.instance_entry)

    def _reset_login(self):
        self._auth_client = None
        self._fallback = False
        self.paste_code_button.set_visible(False)
        self._hide_code_entry()
        self.refresh_login_widgets()

    def on_app_registered(self, client, result):
        if client is not self._auth_client:
            self.discard_reply(client.register_app_finish, result)
            return
        try:
            client.register_app_finish(result)
        except GLib.Error as e:
            self._auth_client = None
            self.refresh_login_widgets()
            self.show_error(_('Could not register with %s') % client.instance, e.message)
            return
        redirect = OOB_REDIRECT if self._fallback else OAUTH_REDIRECT
        self.open_url(client.get_authorize_url(redirect))
        if not self._fallback:
            self.show_toast(_('Finish logging in with the web browser'))

    def open_url(self, uri):
        def launched(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                self.open_url_fallback(uri)
                return
            self.on_url_opened()

        launcher = Gtk.UriLauncher.new(uri)
        launcher.launch(self, None, launched)

    def open_url_fallback(self, uri):
        def opened(source, result):
            try:
                source.launch_default_for_uri_finish(result, None)
            except GLib.Error as e:
                self.show_error(_('Could not open the web browser'), e.message)
                return
            self.on_url_opened()

        app_info = Gio.AppInfo.get_default_for_uri(uri)
        if app_info is None:
            self.show_error(_('Could not open the web browser'),
                            _('No app is set to open web links'))
            return
        app_info.launch_default_for_uri_async(uri, None, None, opened)

    def on_url_opened(self):
        if self._fallback:
            self._show_code_entry()
        else:
            self.paste_code_button.set_visible(True)

    def on_oauth_callback(self, uri):
        client = self._auth_client
        if client is None:
            return
        # parse_callback_uri drops a callback whose state belongs to a
        # different authorization than the one in flight.
        code = client.parse_callback_uri(uri)
        if code is None:
            return
        self.start_token_exchange(code, OAUTH_REDIRECT)

    @Gtk.Template.Callback()
    def on_paste_code_clicked(self, button):
        self._fallback = True
        self.paste_code_button.set_visible(False)
        if self._auth_client is not None and self._auth_client.client_id is not None:
            self.open_url(
                self._auth_client.get_authorize_url(OOB_REDIRECT))
        else:
            self._show_code_entry()

    def _show_code_entry(self):
        self.code_entry.set_visible(True)
        self._grab_focus(self.code_entry)

    def _hide_code_entry(self):
        self.code_entry.set_text('')
        self.code_entry.set_visible(False)

    @Gtk.Template.Callback()
    def on_code_entered(self, entry):
        code = self.code_entry.get_text().strip()
        if not code:
            return
        self.start_token_exchange(code, OOB_REDIRECT)

    def start_token_exchange(self, code, redirect_uri):
        """Run the single token exchange this authorization allows.

        The code is spent and the exchange is marked in flight *before*
        the request leaves, so a redirect that arrives again -- in the
        same main loop turn or long after the browser retried it --
        finds this attempt busy and does nothing at all.
        """
        if self._auth_client is None or self._pending_code is not None:
            return False
        if not self.consume_code(code):
            return False
        attempt = self._auth_attempt
        self._pending_code = code
        self.refresh_login_widgets()
        self._auth_client.request_token_async(
            code, self.replying(self.on_token_received, attempt),
            redirect_uri=redirect_uri)
        return True

    def replying(self, handler, attempt):
        """Wrap a Gio callback so the handler learns its attempt.

        Gio calls the callback as ``(source, result, *user_data)``, so
        the attempt travels as user data and arrives last.
        """
        return lambda client, result: handler(client, result, attempt)

    @staticmethod
    def discard_reply(finish, result):
        """Take delivery of a reply nobody is waiting for, silently."""
        try:
            finish(result)
        except GLib.Error:
            pass

    def settle_auth_attempt(self, attempt, heading=None, body=None,
                            message=None):
        """Close out one authorization attempt with a single outcome.

        The first outcome wins, so a late failure can neither follow the
        success toast nor replace it, and a duplicate reply for the same
        attempt stays silent.
        """
        if attempt != self._auth_attempt or attempt <= self._settled_attempt:
            return False
        self._settled_attempt = attempt
        self._pending_code = None
        # The pending "finish in the browser" toast gives way to the
        # outcome instead of queueing behind it.
        self.toast_overlay.dismiss_all()
        if heading is not None:
            self._auth_client = None
            self._fallback = False
            self.refresh_login_widgets()
            self.show_error(heading, body)
        elif message is not None:
            self.show_toast(message)
        return True

    def on_token_received(self, client, result, attempt):
        if attempt != self._auth_attempt:
            self.discard_reply(client.request_token_finish, result)
            return
        try:
            token = client.request_token_finish(result)
        except GLib.Error as e:
            self.settle_auth_attempt(attempt, _('Authorization failed'),
                                     e.message)
            return
        client.token = token
        client.verify_credentials_async(
            self.replying(self.on_credentials_verified, attempt))

    def on_credentials_verified(self, client, result, attempt):
        if attempt != self._auth_attempt:
            self.discard_reply(client.verify_credentials_finish, result)
            return
        try:
            account = client.verify_credentials_finish(result)
        except GLib.Error as e:
            self.settle_auth_attempt(attempt, _('Could not verify the account'),
                                     e.message)
            return
        account['instance'] = client.instance
        client.store_secret_token(account['id'], client.token)
        self.account = account
        self.store_account(account)
        self.client = client
        self.instance_entry.set_text('')
        self._reset_login()
        self.show_shell()
        self.settle_auth_attempt(attempt, message=_('Logged in as @%s')
                                 % account.get('acct', ''))

    def on_logout_requested(self, _action, _param):
        if self.account is None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Log Out?'),
            body=_('The login for @%s will be removed from this device.')
            % self.account.get('acct', ''))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('logout', _('_Log Out'))
        dialog.set_response_appearance('logout',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self, None, self._on_logout_chosen)

    def _on_logout_chosen(self, dialog, result):
        if dialog.choose_finish(result) == 'logout':
            self.on_logout(None, None)

    def on_logout(self, _action, _param):
        if self.account is not None:
            self._remove_account_now(self.account)

    # Preferences

    def show_preferences(self):
        from .preferences import present_preferences
        return present_preferences(self)

    def _apply_auto_refresh(self):
        seconds = int(self.settings.get_string('auto-refresh'))
        for page in [self.timeline_page, *self.public_pages.values()]:
            if page is not None:
                page.set_auto_refresh_interval(seconds)

    # Feedback

    def show_toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast(title=text, use_markup=False))

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)

    def show_error(self, heading, body):
        # Server bodies can be whole HTML pages; the summariser strips
        # them to one short plain line.
        self.show_toast('%s: %s' % (heading, summarize_error(body)))


# XDG Background portal plumbing. The callbacks hold the window only
# through a GObject weak reference, so a pending request never keeps a
# closed window alive.

_PORTAL_NAME = 'org.freedesktop.portal.Desktop'
_PORTAL_PATH = '/org/freedesktop/portal/desktop'


def _background_finish(window_ref, allowed):
    window = window_ref()
    if window is None:
        return
    if allowed is None:  # no portal: nothing to ask, nothing to undo
        window._background_failed()
    else:
        window._background_done(allowed)


def _on_background_bus(_source, result, window_ref):
    try:
        bus = Gio.bus_get_finish(result)
    except GLib.Error:
        _background_finish(window_ref, None)
        return
    if window_ref() is None:
        return
    token = 'babble_%s' % secrets.token_hex(8)
    sender = (bus.get_unique_name() or '').lstrip(':').replace('.', '_')
    request_path = '/org/freedesktop/portal/desktop/request/%s/%s' % (
        sender, token)
    state = {'id': 0}

    def on_response(connection, _sender, _path, _iface, _signal, params):
        unsubscribe()
        code, results = params.unpack()
        _background_finish(
            window_ref, code == 0 and bool(results.get('background')))

    def subscribe(path):
        state['id'] = bus.signal_subscribe(
            _PORTAL_NAME, 'org.freedesktop.portal.Request', 'Response',
            path, None, Gio.DBusSignalFlags.NONE, on_response)

    def unsubscribe():
        if state['id']:
            bus.signal_unsubscribe(state['id'])
            state['id'] = 0

    # Subscribe before calling so the response cannot slip past.
    subscribe(request_path)

    def on_called(connection, call_result):
        try:
            handle, = connection.call_finish(call_result).unpack()
        except GLib.Error:
            unsubscribe()
            _background_finish(window_ref, None)
            return
        if handle != request_path and state['id']:
            # Pre-0.9 portals pick their own request path.
            unsubscribe()
            subscribe(handle)

    options = {
        'handle_token': GLib.Variant('s', token),
        'reason': GLib.Variant(
            's', _('Deliver notifications while the window is closed')),
        'autostart': GLib.Variant('b', False),
    }
    bus.call(_PORTAL_NAME, _PORTAL_PATH, 'org.freedesktop.portal.Background',
             'RequestBackground', GLib.Variant('(sa{sv})', ('', options)),
             GLib.VariantType('(o)'), Gio.DBusCallFlags.NONE, -1, None,
             on_called)
