# SPDX-License-Identifier: GPL-3.0-or-later

import urllib.parse

import gi

from .i18n import gettext as _, ngettext

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, GObject, Gio, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import auth  # noqa: E402
from . import drafts  # noqa: E402
from . import emojichooser  # noqa: E402
from . import pages  # noqa: E402
from . import portals  # noqa: E402
from .mastodon import (OAUTH_REDIRECT, MastodonClient,  # noqa: E402
                       clear_secret_async, normalize_instance,
                       summarize_error)
from .statustext import relative_time  # noqa: E402
from .timeline import ApiTimelineSource, AvatarCache, TimelinePage  # noqa: E402

# How long the loopback listener waits for the browser to come back.
LOOPBACK_TIMEOUT_SECONDS = 10 * 60


COPIED_LINK_MAX = 2048


def copied_link_kind(text):
    """``'post'``, ``'profile'`` or ``'tag'`` when *text* (already
    stripped) is a single fediverse link, else None."""
    if not text or len(text) > COPIED_LINK_MAX or any(
            c.isspace() for c in text):
        return None
    return pages.fedi_link_kind(text)


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

# What an authorization in flight is for.
AUTH_LOGIN = 'login'
AUTH_ADMIN = 'admin'

# Sidebar places in Tuba's order: (tag, icon, group). Titles are looked
# up at runtime so they translate.
PLACES = (
    ('home', 'user-home-symbolic', 0),
    ('catch-up', 'babble-catch-up-symbolic', 0),
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
HIDE_KEYS = {'catch-up': 'hide-catch-up', 'explore': 'hide-trending',
             'local': 'hide-local', 'federated': 'hide-federated'}

# Opt-in for public timelines the server has closed to most accounts but
# still serves to this one (the "View live and topic feeds" permission).
SHOW_RESTRICTED_KEY = 'show-restricted-feeds'


def place_title(tag):
    return {
        'home': _('Home'), 'catch-up': _('Catch Up'),
        'notifications': _('Notifications'),
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


def _swallow(finish, result):
    """Take delivery of a reply nobody is waiting for, discarding any
    failure. Used where the outcome is already decided and the request
    was only ever best-effort."""
    try:
        finish(result)
    except GLib.Error:
        pass


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
    draft_banner = Gtk.Template.Child()
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
        # Files handed over by Open With (or a bare compose request)
        # before an account was signed in; delivered once one is.
        self._pending_compose = None
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
        self._background_allowed = False
        # Mentions (ids) that arrived while the window was hidden, the
        # status line last sent to the portal and its pending refresh.
        self._background_mentions = set()
        self._background_status = ''
        self._status_source = 0
        self._shortcut_session = None
        self._shortcut_pending = False
        # Reading the keyring is an out-of-process round trip, so a
        # restore or switch can still be in flight when the next one
        # starts. This id tells the replies apart: only the newest
        # lookup is allowed to touch the session.
        self._session_lookup = 0
        # The OAuth state machine. It owns the attempt bookkeeping, the
        # loopback listener and the keyring writes, and answers in
        # signals; the window keeps only the widgets.
        self.auth = auth.LoginFlow(
            current_account=pages.weak_method(self._signed_in_account))
        self.auth.connect('succeeded', self._on_auth_succeeded)
        self.auth.connect('failed', self._on_auth_failed)
        self.auth.connect('message', self._on_auth_message)
        self.auth.connect('browser', self._on_auth_browser)
        self.auth.connect('changed', self._on_auth_changed)
        self.auth.connect('attended', self._on_auth_attended)
        self.settings = pages.settings()
        self.settings.bind('window-width', self, 'default-width',
                            Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-height', self, 'default-height',
                            Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-maximized', self, 'maximized',
                            Gio.SettingsBindFlags.DEFAULT)
        # The composer saves a draft when it closes; the banner reacts
        # live instead of waiting for the next account sync.
        pages.connect_weak(self.settings, 'changed::local-drafts',
                           self._refresh_draft_banner)
        self._setup_copied_link_banner()
        self._setup_offline_banner()

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
        # The stream went down after it had been up: whatever was posted
        # meanwhile never came through it.
        self._stream_dropped = False

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
        pages.connect_weak(self.split_view, 'notify::collapsed',
                           self._sync_translucent_sidebar)
        pages.connect_weak(self.main_stack, 'notify::visible-child-name',
                           self._sync_translucent_sidebar)
        self._sync_translucent_sidebar()
        self.connect('close-request', self._on_close_request)
        pages.connect_weak(self, 'notify::visible', self._on_visible_changed)
        self._sync_global_shortcut()

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
                or key in ('favorite-lists-ids', 'favorite-tags-ids',
                           SHOW_RESTRICTED_KEY):
            self._refresh_sidebar()
        elif key in ('live-updates', 'public-live-updates'):
            self._sync_streaming()
        elif key == 'translucent-sidebar':
            self._sync_translucent_sidebar()
        elif key == 'global-shortcut':
            self._sync_global_shortcut()

    def _sync_translucent_sidebar(self, *_args):
        # The window background goes transparent so the sidebar can show
        # the blurred desktop (style.css), which only suits the expanded
        # shell: the login page and the collapsed layout are full-width.
        if self.settings.get_boolean('translucent-sidebar') \
                and self.main_stack.get_visible_child_name() == 'shell' \
                and not self.split_view.get_collapsed():
            self.add_css_class('translucent-sidebar')
        else:
            self.remove_css_class('translucent-sidebar')

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
                    # The whole account, not just an id: another
                    # instance may hold the same id.
                    self.remove_account(account)
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
                # The paired key, so a second account with the same id on
                # another instance cannot take the selection with it.
                self.settings.set_string(
                    'active-account', account_store.account_key(account))
            self.show_shell()

        # The keyring is an out-of-process round trip; the token arrives
        # later and the shell is built then.
        client.load_secret_token(account_id, loaded)

    def get_stored_account(self):
        return account_store.active_account(self.settings)

    def store_account(self, account):
        account_store.save_account(self.settings, account, activate=True)

    def remove_account(self, account):
        """Forget *account* -- the id together with its instance.

        Forgetting by id alone would delete every stored account carrying
        that id, including another instance's.
        """
        account_store.forget_account(self.settings, account['id'],
                                      account['instance'])

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
        self._refresh_draft_banner()

    def _local_drafts(self):
        """The signed-in account's restorable drafts, newest first, as
        ``(raw, draft)`` pairs."""
        if self.account is None or self.client is None:
            return []
        return drafts.stored_drafts(
            pages.settings().get_strv('local-drafts'),
            account_store.account_key(self.account))

    def _refresh_draft_banner(self, *_args):
        drafts_ = self._local_drafts()
        self.draft_banner.set_revealed(bool(drafts_))
        when = relative_time(drafts_[0][1].saved_at) if drafts_ else ''
        if when:
            # Translators: a draft saved earlier is offered for
            # restoring; %s is a compact age like "5m"
            self.draft_banner.set_title(_('Draft from %s') % when)
        else:
            self.draft_banner.set_title(_('You have an unsent draft'))

    # Copied link banner

    def _setup_copied_link_banner(self):
        """Add the 'Open copied post?' banner below the draft banner and
        watch focus and the clipboard for links worth offering."""
        self.copied_link_banner = Adw.Banner(button_label=_('_Open'),
                                             use_markup=False)
        self.draft_banner.get_parent().insert_child_after(
            self.copied_link_banner, self.draft_banner)
        pages.connect_weak(self.copied_link_banner, 'button-clicked',
                           self._on_copied_link_open)
        # Links already offered, opened or not: none is offered twice.
        self._offered_links = set()
        self._copied_link = None
        self._clipboard_read = 0
        pages.connect_weak(self, 'notify::is-active',
                           self._on_active_changed)
        pages.connect_weak(self.get_clipboard(), 'changed',
                           self._on_clipboard_changed)

    def _on_active_changed(self, *_args):
        if self.is_active():
            self._check_clipboard()

    def _on_clipboard_changed(self, *_args):
        if self.is_active():
            self._check_clipboard()
        elif self.copied_link_banner.get_revealed():
            self._hide_copied_link()

    def _hide_copied_link(self):
        self._clipboard_read += 1
        self._copied_link = None
        self.copied_link_banner.set_revealed(False)

    def _check_clipboard(self):
        if (not self.is_active() or self.client is None
                or not self.settings.get_boolean('offer-copied-links')):
            self._hide_copied_link()
            return
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        if not (formats.contain_gtype(GObject.TYPE_STRING)
                or formats.contain_mime_type('text/plain')
                or formats.contain_mime_type('text/plain;charset=utf-8')):
            self._hide_copied_link()
            return
        self._clipboard_read += 1
        clipboard.read_text_async(None, pages.weak_method(
            self._on_clipboard_text), self._clipboard_read)

    def _on_clipboard_text(self, clipboard, result, serial):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            text = None
        if serial != self._clipboard_read:
            return  # a newer read (or a hide) superseded this one
        if not self.is_active() or self.client is None \
                or not self.settings.get_boolean('offer-copied-links'):
            self._hide_copied_link()
            return
        url = (text or '').strip()
        kind = copied_link_kind(url)
        if kind is None:
            self._hide_copied_link()
            return
        if url in self._offered_links:
            if url != self._copied_link:
                self._hide_copied_link()
            return  # already offered: never nag twice for the same link
        self._offered_links.add(url)
        self._copied_link = url
        self.copied_link_banner.set_title({
            'post': _('Open copied post?'),
            'profile': _('Open copied profile?'),
            'tag': _('Open copied hashtag?'),
            'collection': _('Open copied collection?')}[kind])
        self.copied_link_banner.set_revealed(True)

    def _on_copied_link_open(self, _banner):
        url = self._copied_link
        self._hide_copied_link()
        if url:
            pages.open_link(self, url, resolve=True)

    # Offline banner

    def _setup_offline_banner(self):
        """Add a 'You're offline' banner below the copied-link banner,
        shown for as long as the system reports no network."""
        from . import sysstate
        # Translators: banner shown while the computer has no network.
        self.offline_banner = Adw.Banner(title=_('You’re offline'),
                                         use_markup=False)
        self.draft_banner.get_parent().insert_child_after(
            self.offline_banner, self.copied_link_banner)
        system = sysstate.monitor()
        pages.connect_weak(system, 'notify::online', self._on_online_changed)
        self._on_online_changed(system)

    def _on_online_changed(self, system, *_args):
        self.offline_banner.set_revealed(not system.props.online)

    @Gtk.Template.Callback()
    def on_draft_restore(self, _banner):
        """Reopen the newest unsent draft saved when the composer was
        closed; the entry leaves the list once it is back in the
        composer."""
        drafts = self._local_drafts()
        if not drafts:
            self.draft_banner.set_revealed(False)
            return
        raw, draft = drafts[0]
        dialog = self.present_composer()
        if dialog is None:
            return
        dialog.view.restore_stashing(draft)
        settings = pages.settings()
        settings.set_strv('local-drafts',
                          [r for r in settings.get_strv('local-drafts')
                           if r != raw])

    def _teardown_session(self):
        """Drop every page, stream and dialog tied to the current
        client, before another account takes over."""
        # An authorization in flight belongs to the session being
        # dropped, so its requests are cancelled rather than left to
        # land on the account replacing this one.
        self.auth.cancel()
        self.streaming.stop()
        self._stream_dropped = False
        emojichooser.deactivate_instance_emojis()
        self.notifier.reset()
        if self.compose_dialog is not None:
            self.compose_dialog.close()
        self.compose_dialog = None
        catch_up = self._place_children.get('catch-up')
        if catch_up is not None:
            catch_up.cancel()           # a catch-up in flight stops here
        self.timeline_page = None
        self.timeline_model = None
        self.home_bin.set_child(None)
        self._clear_notifications_page()
        self.public_pages.clear()
        self._place_pages.clear()
        self._place_children.clear()
        self._pinned_lists = {}
        self.navigation_view.replace([self.page_home])

    def switch_account(self, account_key):
        account = account_store.find_account_by_key(self.settings, account_key)
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
        self.auth.restart()
        self.login_back_button.set_visible(self.client is not None)
        self.main_stack.set_visible_child_name('login')
        self._grab_focus(self.instance_entry)

    @Gtk.Template.Callback()
    def on_login_back_clicked(self, _button):
        self.auth.restart()
        if self.client is not None and self.account is not None:
            self.main_stack.set_visible_child_name('shell')

    def on_remove_account_requested(self, _switcher, account_key):
        account = account_store.find_account_by_key(self.settings, account_key)
        if account is None:
            return
        pages.confirm(
            self, _('Remove Account?'),
            _('The login for %s will be removed from this device.')
            % account_store.full_handle(account),
            _('_Remove'), lambda: self._remove_account_now(account))

    def _remove_account_now(self, account):
        """Sign *account* out and stop using it on this device.

        The token is read back, the local removal happens at once, and
        the server is told afterwards. The read has to come first -- the
        revoke needs the token, and the keyring entry is deleted on the
        way out -- but it is local IPC, so the window never waits on the
        network to finish logging you out.
        """
        self._revoke_then_remove(account)

    def _revoke_then_remove(self, account):
        instance, account_id = account['instance'], account['id']
        # Whether this is the account being removed *now* is decided when
        # the removal completes, not when it starts: the keyring read is
        # asynchronous, and the user can switch accounts in between.
        was_active = self._is_active(account)
        # The session that signed in already holds this account's token
        # and client credentials; removing another account needs a client
        # that reads them back.
        client = self.client if was_active and self.client is not None \
            else MastodonClient(instance)
        live = client is self.client
        have_token = live and bool(client.token)
        have_credentials = live and bool(client.client_id
                                         and client.client_secret)

        def revoke_then_forget():
            """Start the revoke, then drop everything locally.

            The revoke runs on its own from here: a server that has
            already forgotten the token answers 200, and one that cannot
            be reached must not keep the window signed in.
            """
            if client.token and client.client_id and client.client_secret:
                client.revoke_token_async(
                    client.token,
                    lambda _c, result, _d=None: _swallow(
                        client.revoke_token_finish, result))
            self._finish_account_removal(account)

        def on_credentials(_known):
            revoke_then_forget()

        def on_token(token):
            client.token = token
            client.load_client_credentials(account_id, on_credentials)

        if have_token and have_credentials:
            revoke_then_forget()
        elif have_token:
            client.load_client_credentials(account_id, on_credentials)
        else:
            client.load_secret_token(account_id, on_token)

    def _is_active(self, account):
        """Whether *account* is the one currently signed in."""
        return self.account is not None \
            and account_store.same_account(self.account, account)

    def _finish_account_removal(self, account):
        clear_secret_async(account['instance'], account['id'])
        remaining = account_store.forget_account(self.settings,
                                                 account['id'],
                                                 account['instance'])
        # Re-read now that the keyring has answered: the user may have
        # switched accounts while it was in flight, and tearing down a
        # session that is no longer this account would sign the wrong one
        # out.
        if not self._is_active(account):
            return
        self._teardown_session()
        self.account = None
        self.client = None
        if remaining is not None:
            self.switch_account(account_store.account_key(remaining))
        else:
            self._sign_out_to_login()

    def _sign_out_to_login(self):
        self.account_avatar.set_custom_image(None)
        self._update_session_actions()
        self.login_back_button.set_visible(False)
        self.auth.cancel()
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
        self._flush_pending_compose()

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
                self._refresh_sidebar()
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
            self._refresh_sidebar()

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
            if self._stream_dropped and self.timeline_page is not None:
                # Fetch what was posted while the stream was down.
                self.timeline_page.refresh()
            self._stream_dropped = False
        elif self.client is not None:
            self._stream_dropped = True
            self.notifier.start_polling()
        self._schedule_background_status()

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
        if name == 'filters_changed':
            # Filters were changed (here or in another client). Their
            # effect is baked into each status's `filtered` list by the
            # server, so only a refetch shows it.
            if self.timeline_page is not None:
                self.timeline_page.refresh()
            if self.notifications_page is not None:
                self.notifications_page.refresh()
            return
        if name == 'notifications_merged':
            # Accepted notification requests finished merging into the
            # stream: the loaded list is stale.
            if self.notifications_page is not None:
                self.notifications_page.refresh()
            return
        if name == 'notification':
            if self.notifications_page is not None:
                self.notifications_page.on_stream_notification(payload)
            self.notifier.handle(payload)
            self.note_background_notification(payload)
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
        ref = self.weak_ref()
        portals.session_bus(
            lambda bus: portals.request_background(ref, bus))

    def _background_done(self, allowed):
        self._background_pending = False
        self._background_allowed = allowed
        if allowed:
            self._schedule_background_status()
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

    # Background status

    def note_background_notification(self, notification):
        """Count a mention that arrived while the window is hidden, for
        the background status line. Any notification may be passed, the
        same one twice included."""
        if self.get_visible() or not isinstance(notification, dict) \
                or notification.get('type') != 'mention':
            return
        nid = str(notification.get('id') or '')
        if not nid or nid in self._background_mentions:
            return
        self._background_mentions.add(nid)
        self._schedule_background_status()

    def _on_visible_changed(self, _window, _pspec):
        if not self.get_visible():
            self._schedule_background_status()
            return
        self._background_mentions.clear()
        if self._status_source:
            GLib.source_remove(self._status_source)
            self._status_source = 0
        if self._background_status:
            self._background_status = ''
            portals.set_background_status('')

    def _schedule_background_status(self):
        """Refresh the line GNOME Shell shows for the app running in the
        background, once the changes of the next second have landed."""
        if not self._background_allowed or self.get_visible() \
                or self._status_source:
            return
        self._status_source = GLib.timeout_add(
            portals.STATUS_DELAY_MS,
            pages.weak_method(self._flush_background_status))

    def _flush_background_status(self):
        self._status_source = 0
        if self._background_allowed and not self.get_visible():
            message = self._background_status_text()[
                :portals.STATUS_MAX_CHARS]
            if message != self._background_status:
                self._background_status = message
                portals.set_background_status(message)
        return GLib.SOURCE_REMOVE

    def _background_status_text(self):
        count = len(self._background_mentions)
        if count:
            # Translators: status GNOME Shell shows for Babble running in
            # the background with its window closed.
            return ngettext('%d new mention', '%d new mentions',
                            count) % count
        if self.streaming.connected:
            # Translators: background status: live updates are streaming.
            return _('Connected')
        # Translators: background status: no live stream, so Babble polls
        # the server for new notifications.
        return _('Checking for notifications')

    # Global shortcut portal

    def _sync_global_shortcut(self):
        """Bind the quick post shortcut through the GlobalShortcuts portal
        while ``global-shortcut`` is on; close the session once it is
        off. A missing portal is silently ignored."""
        if not self.settings.get_boolean('global-shortcut'):
            if self._shortcut_session is not None:
                self._shortcut_session.close()
                self._shortcut_session = None
            return
        if self._shortcut_session is not None or self._shortcut_pending:
            return
        self._shortcut_pending = True
        ref = self.weak_ref()
        portals.session_bus(
            lambda bus: portals.create_shortcut_session(ref, bus))

    def _shortcut_ready(self, session, refused):
        self._shortcut_pending = False
        if refused:
            self.settings.set_boolean('global-shortcut', False)
            pages.show_toast(self,
                             _('The quick post shortcut was not allowed'))
            return
        if session is None:
            return
        if not self.settings.get_boolean('global-shortcut'):
            session.close()  # switched off while it was being bound
            return
        self._shortcut_session = session

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
        was_shown = self.notifications_badge.get_visible()
        self.notifications_badge.set_visible(bool(count))
        if count and not was_shown:
            pages.flash_class(self.notifications_badge, 'just-toggled')
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
        if tag == 'catch-up':
            from .catchup import CatchUpPage
            return CatchUpPage(client, account=self.account)
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
            if tag in self._restricted_public_places():
                return None
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
        self.compose_dialog.view.local_draft_key = (
            account_store.account_key(self.account)
            if self.account is not None else None)
        return self.compose_dialog

    def present_composer(self):
        dialog = self.ensure_composer()
        if dialog is not None and dialog.get_parent() is None:
            dialog.present(self)
        return dialog

    def open_compose_reply(self, status_id, handle, visibility='public'):
        """Called by timeline rows to reply to a status while keeping
        any draft in progress."""
        dialog = self.ensure_composer()
        if dialog is None:
            return
        dialog.start_reply(status_id, handle, visibility)
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

    # Sidebar

    def _restricted_public_places(self):
        """The public timelines this account must not see in the sidebar.

        A feed the server closes to everyone but privileged accounts is
        hidden unless the reader turned the opt-in on; a feed it closes
        to this account outright is always hidden.

        The topic settings (``hashtag_feeds``/``trending_link_feeds``)
        are deliberately not consulted here: the server never closes them
        to a signed-in account, it just drops the remote posts, so the
        page still has local content and hiding it would lose read."""
        client = self.client
        if client is None:
            return set()
        from .streaming import (FEED_RESTRICTED, FEED_UNAVAILABLE,
                                public_feed_access)
        access = public_feed_access(client, self.account)
        opted_in = self.settings.get_boolean(SHOW_RESTRICTED_KEY)
        closed = set()
        for place in ('local', 'federated'):
            state = access.get(place)
            if state == FEED_UNAVAILABLE \
                    or (state == FEED_RESTRICTED and not opted_in):
                closed.add(place)
        return closed

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
        closed = self._restricted_public_places()
        hidden |= closed
        for tag in closed:
            # A feed the server will not serve, or the reader has not
            # opted into, keeps neither a page nor a stream.
            self.public_pages.pop(tag, None)
            self._place_pages.pop(tag, None)
            self._place_children.pop(tag, None)
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
        waiting = self.auth.waiting
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

    # The OAuth flow

    # LoginFlow owns the attempt bookkeeping, the loopback listener and
    # the keyring writes. What arrives here is a handful of things to
    # show, plus the one event that matters: an account is signed in.

    def _signed_in_account(self):
        """The account this window has signed in, or None.

        Handed to the login flow as a weak method, so a moderation
        authorization can tell whether the session it was started for is
        still the current one without the flow holding the window.
        """
        return self.account

    def _on_auth_succeeded(self, _flow, account):
        """Adopt the account a completed authorization belongs to."""
        client = self.auth.client
        if self.auth.admin:
            # A moderation authorization replaces the token of the
            # account already signed in, asking for the admin scopes on
            # top, instead of installing a second session.
            if self.client is not None and client is not None:
                self.client.token = client.token
                self.client.client_id = client.client_id
                self.client.client_secret = client.client_secret
        else:
            self.client = client
            self.instance_entry.set_text('')
        self.account = account
        self.store_account(account)
        if not self.auth.admin:
            self.show_shell()
        else:
            self._update_session_actions()

    def _on_auth_failed(self, _flow, heading, body):
        # The pending "finish in the browser" toast gives way to the
        # outcome instead of queueing behind it.
        self.toast_overlay.dismiss_all()
        self.show_error(heading, body)

    def _on_auth_message(self, _flow, text):
        self.toast_overlay.dismiss_all()
        self.show_toast(text)

    def _on_auth_browser(self, _flow, uri):
        self.open_url(uri)

    def _on_auth_attended(self, _flow):
        self.present()

    def _on_auth_changed(self, _flow):
        """The login widgets follow the flow.

        The code-entry affordances belong to an authorization nobody is
        running any more once the flow is idle and has not fallen back to
        a pasted code, so they go away with it.
        """
        if not self.auth.waiting and not self.auth.fallback:
            self.paste_code_button.set_visible(False)
            self._hide_code_entry()
        self.refresh_login_widgets()

    @Gtk.Template.Callback()
    def on_login_clicked(self, _widget):
        typed = self.instance_entry.get_text()
        domain = self._instance_domain(typed)
        if self.auth.waiting:
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
        self.auth.start_login(normalize_instance(domain))

    @Gtk.Template.Callback()
    def on_cancel_login_clicked(self, _button):
        # A reply still in flight for the abandoned attempt is ignored.
        self.auth.restart()
        self.toast_overlay.dismiss_all()
        self._grab_focus(self.instance_entry)

    @Gtk.Template.Callback()
    def on_paste_code_clicked(self, _button):
        # The authorization is named again for the out-of-band redirect,
        # so nothing can answer on the loopback listener any more.
        self.paste_code_button.set_visible(False)
        if not self.auth.use_pasted_code():
            self._show_code_entry()

    def _show_code_entry(self):
        self.code_entry.set_visible(True)
        self._grab_focus(self.code_entry)

    def _hide_code_entry(self):
        self.code_entry.set_text('')
        self.code_entry.set_visible(False)

    @Gtk.Template.Callback()
    def on_code_entered(self, entry):
        self.auth.enter_code(entry.get_text())

    def on_oauth_callback(self, uri, redirect_uri=OAUTH_REDIRECT):
        """Exchange the code a redirect to *redirect_uri* carries."""
        return self.auth.on_oauth_callback(uri, redirect_uri)

    def on_url_opened(self):
        if self.auth.fallback:
            self._show_code_entry()
        else:
            self.paste_code_button.set_visible(True)

    def authorize_admin(self, callback=None):
        """Ask the instance for the admin scopes on the signed-in account.

        *callback(granted)* runs with the outcome. A refusal is the
        dashboard's cue to go back to plain moderation, so it always
        gets one, even when there was nothing to authorize.
        """
        if self.account is None or self.client is None:
            if callback is not None:
                callback(False)
            return
        # Bound to the account that asked for it: a switch or a logout
        # while the browser step is open must not let the reply install a
        # token for the account that replaced it.
        self.auth.start_admin(self.account['instance'],
                              account_store.account_key(self.account),
                              callback)


    def on_logout_requested(self, _action, _param):
        if self.account is None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Log Out?'),
            body=_('The login for @%s will be signed out on the server '
                   'and removed from this device.')
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

