# SPDX-License-Identifier: GPL-3.0-or-later

import os
import urllib.parse

import gi

from .i18n import gettext as _, ngettext

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gio, GObject, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import auth  # noqa: E402
from . import clipboard  # noqa: E402
from . import drafts  # noqa: E402
from . import emojichooser  # noqa: E402
from . import pages  # noqa: E402
from . import navigation  # noqa: E402
from . import portals  # noqa: E402
from . import sidebarmode  # noqa: E402
from . import sidebarrail  # noqa: E402
from . import webcolumn  # noqa: E402
from .capabilities import COLLECTIONS_API_VERSION  # noqa: E402
from .mastodon import (OAUTH_REDIRECT, MastodonClient, clear_secret_async,  # noqa: E402
                       keyring_problem,
                       normalize_instance, summarize_error)
from .sidebarcontrol import SidebarControl  # noqa: E402
from .sidebarrail import SidebarRail  # noqa: E402
from .statustext import relative_time  # noqa: E402
from .timelinepage import ApiTimelineSource, TimelinePage  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402

# One-click alternatives for the login suggestions row, ranked from the
# public JoinMastodon server directory (api.joinmastodon.org/servers)
# by weekly active users, largest communities first.
SUGGESTED_SERVERS = (
    'mastodon.social', 'mstdn.social', 'infosec.exchange', 'mstdn.jp',
    'hachyderm.io', 'piaille.fr', 'mastodon.world', 'social.vivaldi.net',
    'epsilon.social', 'troet.cafe', 'mastodon.uno', 'mathstodon.xyz')


# Settings the home page reads once when it is built, so a change rebuilds it.
HOME_FILTER_KEYS = ('hide-boosts-home', 'hide-replies-non-follows')

# Opt-in for public timelines the server has closed to most accounts but
# still serves to this one (the "View live and topic feeds" permission).
SHOW_RESTRICTED_KEY = 'show-restricted-feeds'

# The help pages, and where they live when no help viewer answers.
#
# The scheme is `help:`, from the Desktop Help System Specification, and
# not `yelp://`: Yelp registers x-scheme-handler/{ghelp,help,info,man}
# and no yelp handler at all, so a yelp:// URI is dispatched to nothing
# and the launch always fails. Yelp's URI resolver likewise dispatches
# only on those four plus help-list:, file: and xref:, so `yelp:` would
# not be understood either. The document id is the application id, which
# is what share/help/<lang>/<app id>/ is keyed by.
APP_ID = 'io.github.mauriciobc.Babble'
HELP_SCHEME = 'help'
HELP_URI = HELP_SCHEME + ':' + APP_ID
PROJECT_URL = 'https://github.com/mauriciobc/babble'
ISSUES_URL = PROJECT_URL + '/issues'
DOCS_URL = PROJECT_URL + '/blob/main/Docs/README.md'


def help_viewer():
    """The application registered for ``help:`` URIs, or None."""
    return Gio.AppInfo.get_default_for_uri_scheme(HELP_SCHEME)


def help_pages_installed(data_dirs=None):
    """Whether the Mallard pages are installed where a viewer looks.

    *data_dirs* defaults to the XDG data directories: a viewer resolves
    the document through the same XDG_DATA_DIRS the desktop file was
    found under, so an install whose prefix is not on that list is not
    one the viewer can serve either. Nothing is assumed about a build
    tree, which has no installed pages and no desktop file."""
    if data_dirs is None:
        data_dirs = [GLib.get_user_data_dir(),
                     *GLib.get_system_data_dirs()]
    for data_dir in data_dirs:
        base = GLib.build_filenamev([data_dir, 'help'])
        try:
            languages = os.listdir(base)
        except OSError:
            continue
        for language in languages:
            page = GLib.build_filenamev(
                [base, language, APP_ID, 'index.page'])
            if os.path.isfile(page):
                return True
    return False


def help_available():
    """Whether ``HELP_URI`` can actually reach the pages on this system.

    A launch is not the test. With the OpenURI portal in the way, a
    system with no viewer for the scheme answers with its own "no apps
    available" dialog and calls the launch a success; only cancelling
    that dialog makes it fail. So the error path never runs and F1
    stays a dead end. Asking the handler table, and the install tree,
    is what tells the two apart."""
    return help_viewer() is not None and help_pages_installed()




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

    # Read by the header controls and the icon rail through property
    # bindings, so a widget never needs a signal back to the window.
    sidebar_visible = GObject.Property(type=bool, default=False)
    sidebar_collapsed = GObject.Property(type=bool, default=True)
    unread_text = GObject.Property(type=str, default='')
    unread_visible = GObject.Property(type=bool, default=False)

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
    web_split = Gtk.Template.Child()
    web_slot = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    sidebar_header = Gtk.Template.Child()
    sidebar_compose_button = Gtk.Template.Child()
    sidebar_mode_button = Gtk.Template.Child()
    primary_menu_button = Gtk.Template.Child()
    sidebar_stack = Gtk.Template.Child()
    sidebar_rail_slot = Gtk.Template.Child()
    draft_banner = Gtk.Template.Child()
    sidebar_list = Gtk.Template.Child()
    account_button = Gtk.Template.Child()
    account_avatar = Gtk.Template.Child()
    account_name_label = Gtk.Template.Child()
    account_handle_label = Gtk.Template.Child()
    account_text_box = Gtk.Template.Child()
    account_chevron = Gtk.Template.Child()
    navigation_view = Gtk.Template.Child()
    home_header = Gtk.Template.Child()
    notifications_header = Gtk.Template.Child()
    home_bin = Gtk.Template.Child()
    notifications_bin = Gtk.Template.Child()
    notifications_header_slot = Gtk.Template.Child()
    page_home = Gtk.Template.Child()
    page_notifications = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = None
        self.account = None
        self._no_keyring_shown = False
        self.timeline_page = None
        self.timeline_model = None
        self.compose_dialog = None
        # Files handed over by Open With (or a bare compose request)
        # before an account was signed in; delivered once one is.
        self._pending_compose = None
        self.notifications_page = None
        self._unread_handler = None
        self.public_pages = {}
        self.navigation = navigation.Navigation(self)
        self._items = {}
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
        # The sidebar's runtime state: whether the collapsed layout has
        # its overlay open, whether the account button currently lives
        # in the rail, and the guard that keeps a show-sidebar reply from
        # re-entering its own sync.
        self._overlay_open = False
        self._rail_accounts = False
        self._applying_sidebar_visibility = False
        self._sidebar_visibility_id = 0
        self._applying_sidebar_mode = False
        # The OAuth state machine. It owns the attempt bookkeeping, the
        # loopback listener and the keyring writes, and answers in
        # signals; the window keeps only the widgets.
        self.auth = auth.LoginFlow(
            current_account=pages.weak_method(self._signed_in_account))
        pages.connect_weak(self.auth, 'succeeded', self._on_auth_succeeded)
        pages.connect_weak(self.auth, 'failed', self._on_auth_failed)
        pages.connect_weak(self.auth, 'message', self._on_auth_message)
        pages.connect_weak(self.auth, 'browser', self._on_auth_browser)
        pages.connect_weak(self.auth, 'changed', self._on_auth_changed)
        pages.connect_weak(self.auth, 'attended', self._on_auth_attended)
        # Local scope: a focused TextView/Editable keeps Page Up/Down.
        # An application accelerator would steal them first.
        page_keys = Gtk.ShortcutController()
        for action, keyval in (
                ('app.scroll-page-down', Gdk.KEY_Page_Down),
                ('app.scroll-page-up', Gdk.KEY_Page_Up)):
            page_keys.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.KeyvalTrigger(keyval=keyval, modifiers=0),
                action=Gtk.NamedAction(action_name=action)))
        self.add_controller(page_keys)
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
        # The banners exist to offer their action, so their buttons are
        # the suggested ones (AdwBanner:button-style, since 1.7). Set
        # once, here: it never changes, so doing it on every draft
        # change was work for nothing.
        self.draft_banner.set_button_style(Adw.BannerButtonStyle.SUGGESTED)
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
        pages.connect_weak(self.streaming, 'event', self._on_stream_event)
        pages.connect_weak(self.streaming, 'connection-changed',
                           self._on_stream_connection_changed)
        # The stream went down after it had been up: whatever was posted
        # meanwhile never came through it.
        self._stream_dropped = False
        # The open admin dashboard, so a streaming report can refresh it.
        self._admin_dialog = None

        actions = (
            ('logout', self.on_logout_requested),
            ('show-notifications',
             lambda *a: self.show_page('notifications')),
            ('compose', lambda *a: self.present_composer()),
            ('view-profile', self.on_view_profile),
            ('reload', self.on_reload),
            ('back', self.on_back),
            ('close-pane', self.on_close_pane),
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
        # The sidebar's three states share one stateful action, so the
        # menu's indicator follows a hide made elsewhere (F9, a gesture)
        # instead of remembering what the menu last picked.
        sidebar_mode = Gio.SimpleAction.new_stateful(
            'sidebar-mode', GLib.VariantType.new('s'),
            GLib.Variant.new_string(sidebarmode.SIDEBAR_STYLES[0]))
        sidebar_mode.connect('change-state', self._on_sidebar_mode_changed)
        self.add_action(sidebar_mode)
        toggle_sidebar = Gio.SimpleAction.new('toggle-sidebar', None)
        toggle_sidebar.connect('activate', self._on_toggle_sidebar)
        self.add_action(toggle_sidebar)
        self._update_session_actions()

        self.switcher = account_store.AccountSwitcher(self.settings)
        self.account_button.set_popover(self.switcher)
        pages.connect_weak(self.switcher, 'switch', self._on_switcher_switch)
        pages.connect_weak(self.switcher, 'remove',
                           self.on_remove_account_requested)
        pages.connect_weak(self.switcher, 'add', self._on_switcher_add)

        # The icon rail is the narrow face of the sidebar, and shares
        # its mode menu and the account button.
        self.sidebar_rail = SidebarRail()
        self.sidebar_rail_slot.append(self.sidebar_rail)
        pages.connect_weak(self.sidebar_rail, 'activated',
                           self._on_rail_activated)
        # Each page header owns its control strip; the strip binds to
        # this window's properties rather than the window updating it.
        self.home_header.pack_start(self.make_sidebar_control())
        self.notifications_header.pack_start(
            self.make_sidebar_control(include_bell=False))

        from . import preferences
        self._preferences = preferences
        pages.connect_weak(self.settings, 'changed', self._on_setting_changed)
        pages.connect_weak(Adw.StyleManager.get_default(), 'notify::dark',
                           self._on_dark_changed)
        preferences.apply_style(self, self.settings)
        pages.connect_weak(self.settings, 'changed::sidebar-style',
                           self._on_sidebar_state_changed)
        pages.connect_weak(self.settings, 'changed::sidebar-hidden',
                           self._on_sidebar_state_changed)
        pages.connect_weak(self.split_view, 'notify::collapsed',
                           self._on_sidebar_collapsed)
        pages.connect_weak(self.split_view, 'notify::show-sidebar',
                           self._on_sidebar_shown)
        pages.connect_weak(self.main_stack, 'notify::visible-child-name',
                           self._sync_translucent_sidebar)
        self._sync_sidebar_visibility()
        webcolumn.watch_split(self.web_split)
        pages.connect_weak(self, 'close-request', self._on_close_request)
        # 'destroy' alone is not enough: PyGObject can keep the wrapper
        # (and with it the window) alive past gtk_window_destroy(), and
        # the signal only fires on dispose. Unrealize always comes first.
        pages.connect_weak(self, 'destroy', self._cancel_sidebar_visibility)
        pages.connect_weak(self, 'unrealize',
                           self._cancel_sidebar_visibility)
        pages.connect_weak(self, 'map', self._defer_sidebar_visibility)
        pages.connect_weak(self, 'notify::visible', self._on_visible_changed)
        self._sync_global_shortcut()

        self._refresh_sidebar()
        self.refresh_login_widgets()
        self.restore_account()

    # Settings

    def _on_switcher_switch(self, _switcher, account_id):
        self.switch_account(account_id)

    def _on_switcher_add(self, _switcher):
        self.begin_add_account()

    def _on_dark_changed(self, *_args):
        self._preferences.apply_style(self, self.settings)

    def _on_setting_changed(self, settings, key):
        if key in self._preferences.STYLE_KEYS:
            self._preferences.apply_style(self, settings)
        elif key == 'auto-refresh':
            self._apply_auto_refresh()
        elif key in HOME_FILTER_KEYS:
            self._rebuild_home_timeline()
        elif key in navigation.HIDE_KEYS.values() \
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
        # shell with the sidebar on screen: the login page, the collapsed
        # layout and a hidden sidebar are full-width.
        if self.settings.get_boolean('translucent-sidebar') \
                and self.main_stack.get_visible_child_name() == 'shell' \
                and not self.split_view.get_collapsed() \
                and self.split_view.get_show_sidebar():
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

        def loaded(token, error=None):
            if lookup != self._session_lookup:
                return
            if error is not None:
                # A locked or unreachable keyring is not a signed-out
                # account. Keep the stored entry (and its Catch-Up
                # snapshots) and ask the user to try again.
                if error.matches(Gio.io_error_quark(),
                                 Gio.IOErrorEnum.CANCELLED):
                    return
                self.main_stack.set_visible_child_name('login')
                if keyring_problem(error) == 'missing':
                    self.present_no_keyring()
                    return
                self.show_toast(
                    _('Could not read the login for @%s from the keyring')
                    % account.get('acct', ''))
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

    def present_no_keyring(self):
        """Say once per session that no keyring is running, and what to
        do. Returns the dialog, or None when it was already shown."""
        if self._no_keyring_shown:
            return None
        self._no_keyring_shown = True
        dialog = Adw.AlertDialog.new(
            _('No Keyring Found'),
            _('Babble keeps your login in the system keyring, and no '
              'keyring service is running. Start one, such as GNOME '
              'Keyring or KeePassXC, then restart Babble.'))
        dialog.add_response('close', _('_Close'))
        dialog.add_response('help', _('_Learn More'))
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        pages.connect_weak(dialog, 'response', self._on_no_keyring_response)
        dialog.present(self)
        return dialog

    def _on_no_keyring_response(self, _dialog, response):
        if response == 'help':
            self.open_help()

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
        # A server that is not Mastodon, or an older one, is told no
        # rather than being assumed yes: Collections is Mastodon API
        # version 10, and an unproven server is not evidence that it has
        # it. The admin switch is about the signed-in role, not the
        # server's version.
        self.lookup_action('collections').set_enabled(
            signed_in and self.client.is_supported(
                COLLECTIONS_API_VERSION))
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
        """Add the 'Open copied post?' banner below the draft banner."""
        self.copied_link_banner = clipboard.CopiedLinkBanner(self)
        self.draft_banner.get_parent().insert_child_after(
            self.copied_link_banner, self.draft_banner)

    # Offline banner
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
        from .drafts import drop_local_draft
        drop_local_draft(pages.settings(), raw)

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
        # The composer is kept after it closes, and closing a dialog that
        # is not presented is an Adwaita critical: only an open one.
        if self.compose_dialog is not None \
                and self.compose_dialog.get_parent() is not None:
            self.compose_dialog.close()
        self.compose_dialog = None
        catch_up = self.navigation.children.get('catch-up')
        if catch_up is not None:
            catch_up.cancel()           # a catch-up in flight stops here
        self.timeline_page = None
        self.timeline_model = None
        self.home_bin.set_child(None)
        self._clear_notifications_page()
        self.public_pages.clear()
        self.navigation.clear()
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

        def on_token(token, error=None):
            if error is None:
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
        self.account_button.set_tooltip_text(_('Switch Account'))
        self._update_session_actions()
        self.login_back_button.set_visible(False)
        self.auth.cancel()
        self.main_stack.set_visible_child_name('login')

    def show_shell(self):
        name = account_store.plain_name(self.account)
        handle = account_store.full_handle(self.account)
        self.account_name_label.set_label(name)
        self.account_handle_label.set_label(handle)
        # The label ellipsizes when narrow or large-text; keep the full
        # handle (with instance) on the button for hover and Orca.
        self.account_button.set_tooltip_text(handle)
        self.account_avatar.set_text(name)
        AvatarCache.load(self.account_avatar, self.account)
        self._update_session_actions()
        self._refresh_sidebar()
        self.ensure_home_timeline()
        self.ensure_notifications_page()
        self.show_page('home')
        # Settle the sidebar before the shell's first allocation: setting
        # show-sidebar from inside the collapsed notification makes the
        # split view allocate its content pane with a negative width.
        self._sync_sidebar_mode()
        self._sync_sidebar_visibility()
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
        if 'conversations' in self.navigation.children:
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
            if self._stream_dropped:
                # Fetch what was posted while the stream was down. The
                # notifications page has a list to catch up as well: the
                # stream does not replay, and a poll may not have run
                # since the last notification arrived.
                if self.timeline_page is not None:
                    self.timeline_page.refresh()
                if self.notifications_page is not None:
                    self.notifications_page.catch_up()
            self._stream_dropped = False
        elif self.client is not None:
            self._stream_dropped = True
            self.notifier.start_polling()
        self._schedule_background_status()

    def _timeline_pages(self):
        found = [self.timeline_page, *self.public_pages.values()]
        for child in self.navigation.children.values():
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
            conversations = self.navigation.children.get('conversations')
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
        if name == 'follow_request':
            # A new request. It arrives on the user stream and becomes a
            # notification only once the server has written it, so the
            # list is refetched rather than patched -- the same reason
            # notifications_merged reloads.
            if self.notifications_page is not None:
                self.notifications_page.refresh()
            return
        if name in ('announcement', 'announcement.delete'):
            # A new announcement, or one withdrawn. The banner counts
            # unread ones, so it has to be asked again.
            if self.notifications_page is not None:
                self.notifications_page.load_announcements()
            return
        if name == 'announcement.reaction':
            # Someone else's reaction on an announcement. Babble shows
            # only your own reactions, which are applied optimistically,
            # so there is nothing here to redraw.
            return
        if name == 'report':
            # A new report, for a moderator with the dashboard open.
            self._refresh_admin_tool('reports')
            return
        if name in ('add', 'remove'):
            # Collection membership (Mastodon 4.6+). The payload is the
            # bare string "collection": membership changes a Collection
            # page's member list, which it refetches when reopened.
            return
        if name == 'notification':
            if self.notifications_page is not None:
                self.notifications_page.on_stream_notification(payload)
            self.notifier.handle(payload)
            self.note_background_notification(payload)
            return
        if name == 'conversation':
            conversations = self.navigation.children.get('conversations')
            if conversations is not None:
                conversations.on_stream_conversation(payload)
            return
        if name == 'encrypted_message':
            # An end-to-end encrypted direct message. Babble reads
            # conversations over /conversations and holds no keys for
            # them, so there is nothing to open -- but the event says a
            # message arrived, and the list refetches to show it.
            conversations = self.navigation.children.get('conversations')
            if conversations is not None:
                conversations.refresh()
            return

    def _on_close_request(self, _window):
        """With background mode on, closing hides the window and keeps
        the application (and its notifications) running."""
        self._cancel_sidebar_visibility()
        if self.client is None or not self.settings.get_boolean(
                'work-in-background'):
            # A hold left from an earlier hide would outlive the window.
            self._release_hold()
            pages.flush_deferred()
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

    def _rebuild_home_timeline(self):
        """Throw the home page and its items away and build it again, for
        a setting that decides which posts it may show."""
        if self.timeline_page is None:
            return
        self.timeline_page = None
        self.timeline_model = None
        self.ensure_home_timeline()

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
        self.timeline_page = TimelinePage(
            self.client, mode='home', model=self.timeline_model)
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
        self.props.unread_text = str(count) if count else ''
        self.props.unread_visible = bool(count)
        self.sidebar_rail.set_badge(count)

    def _timeline_place(self, path, params, icon, title, description):
        from .timelinepage import link_paged
        page = TimelinePage(self.client, source=ApiTimelineSource(
            self.client, path, params,
            paging='link' if link_paged(path) else 'id'))
        pages.configure_empty_page(page.empty_page, icon, title, description)
        return page

    def _public_place_built(self, tag, page):
        self.public_pages[tag] = page
        self._apply_auto_refresh()
        self._sync_streaming()

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

    def make_sidebar_control(self, include_bell=True):
        """A control strip for a page header, bound to this window.

        Every header gets its own: the strip follows the window's
        sidebar properties through bindings, so nothing has to keep a
        list of controls alive or walk them when the state changes."""
        return SidebarControl(
            self, self.sidebar_mode_button.get_menu_model(),
            self.primary_menu_button.get_menu_model(),
            include_bell=include_bell)

    def _on_rail_activated(self, _rail, tag):
        self.show_page(tag)

    def _on_sidebar_state_changed(self, _settings, _key):
        if self._applying_sidebar_mode:
            return
        self._sync_sidebar_mode()
        self._sync_sidebar_visibility()

    def _on_sidebar_collapsed(self, split_view, _param):
        collapsed = split_view.get_collapsed()
        if collapsed:
            self._overlay_open = False
        self._sync_sidebar_mode()
        # Collapsing: the compact breakpoint has already closed the
        # sidebar in this pass. Expanding: whether it comes back depends
        # on the stored state, and setting show-sidebar from inside this
        # notification re-enters the split view's allocation (libadwaita
        # then allocates the content pane with a negative width), so the
        # split view waits for an idle. The window's own properties only
        # drive the header controls, so they can be right straight away.
        self.props.sidebar_collapsed = collapsed
        self.props.sidebar_visible = sidebarmode.sidebar_visible(
            self.settings.get_boolean('sidebar-hidden'), collapsed,
            self._overlay_open)
        self._defer_sidebar_visibility()

    def _defer_sidebar_visibility(self, *_args):
        self._cancel_sidebar_visibility()
        self._sidebar_visibility_id = GLib.idle_add(
            self._apply_deferred_sidebar_visibility)

    def _cancel_sidebar_visibility(self, *_args):
        if self._sidebar_visibility_id:
            GLib.source_remove(self._sidebar_visibility_id)
            self._sidebar_visibility_id = 0

    def _apply_deferred_sidebar_visibility(self):
        self._sidebar_visibility_id = 0
        self._sync_sidebar_visibility()
        return GLib.SOURCE_REMOVE

    def _on_sidebar_shown(self, split_view, _param):
        if self._applying_sidebar_visibility:
            return
        shown = split_view.get_show_sidebar()
        if split_view.get_collapsed():
            self._overlay_open = shown
        self.props.sidebar_visible = shown
        self._sync_translucent_sidebar()

    def _on_toggle_sidebar(self, _action, _param):
        if self.split_view.get_collapsed():
            self._overlay_open = not self._overlay_open
            self._sync_sidebar_visibility()
        else:
            # The settings handler does the syncing.
            self.settings.set_boolean(
                'sidebar-hidden',
                not self.settings.get_boolean('sidebar-hidden'))

    def _on_sidebar_mode_changed(self, action, value):
        state = value.get_string()
        # The overlay flag first, so the syncs below already see it.
        if state == 'hidden':
            self._overlay_open = False
        elif state in sidebarmode.SIDEBAR_STYLES:
            if self.split_view.get_collapsed():
                self._overlay_open = True
        # Each write emits its own changed:: signal, and an unchanged
        # value emits one too, so the handler stands aside and the
        # syncs run once, whichever keys actually changed.
        self._applying_sidebar_mode = True
        try:
            if state == 'hidden':
                self.settings.set_boolean('sidebar-hidden', True)
            elif state in sidebarmode.SIDEBAR_STYLES:
                self.settings.set_string('sidebar-style', state)
                self.settings.set_boolean('sidebar-hidden', False)
        finally:
            self._applying_sidebar_mode = False
        self._sync_sidebar_mode()
        self._sync_sidebar_visibility()
        action.set_state(value)

    def _sync_sidebar_mode(self):
        """Show the full sidebar or the icon rail, and hand the account
        button to whichever of them has room for its text."""
        style = self.settings.get_string('sidebar-style')
        collapsed = self.split_view.get_collapsed()
        rail = sidebarmode.icon_rail(style, collapsed)
        # Collapsed, the sidebar is an overlay and never a pushable
        # page, so AdwSidebar's sidebar mode holds at every width.
        self.sidebar_list.set_mode(Adw.SidebarMode.SIDEBAR)
        self.sidebar_stack.set_visible_child_name('icons' if rail else 'full')
        self.sidebar_compose_button.set_visible(not rail)
        # The mode button serves the full sidebar and the rail alike. A
        # dropdown arrow adds about 16px, which a 64px rail has no room
        # for, so the rail gets the bare icon, centred.
        self.sidebar_mode_button.set_always_show_arrow(not rail)
        self.sidebar_mode_button.set_halign(
            Gtk.Align.CENTER if rail else Gtk.Align.START)
        minimum, maximum = sidebarmode.widths(style, collapsed)
        if maximum < self.split_view.get_max_sidebar_width():
            # Narrowing: lower the minimum first, so the pair is never
            # left with a maximum below it.
            self.split_view.set_min_sidebar_width(minimum)
            self.split_view.set_max_sidebar_width(maximum)
        else:
            self.split_view.set_max_sidebar_width(maximum)
            self.split_view.set_min_sidebar_width(minimum)
        self.account_text_box.set_visible(not rail)
        self.account_chevron.set_visible(not rail)
        # The menu button's own padding plus the template margins are
        # what would make a 64px rail wider than its pane.
        for edge in ('start', 'end', 'top', 'bottom'):
            getattr(self.account_button, 'set_margin_' + edge)(0 if rail else 6)
        if rail and not self._rail_accounts:
            self.sidebar_list.set_prefix(None)
            self.sidebar_rail.set_account_button(self.account_button)
            self._rail_accounts = True
        elif not rail and self._rail_accounts:
            sidebarrail.detach(self.account_button)
            self.sidebar_list.set_prefix(self.account_button)
            self._rail_accounts = False
        self.sidebar_rail.set_selected(self.visible_tag())
        # The menu reports the stored state, not the last item picked:
        # F9 hides the sidebar without touching the menu.
        action = self.lookup_action('sidebar-mode')
        if action is not None:
            state = 'hidden' if self.settings.get_boolean('sidebar-hidden') \
                else style
            current = action.get_state()
            if current is None or current.get_string() != state:
                action.set_state(GLib.Variant.new_string(state))

    def _sync_sidebar_visibility(self):
        """Put the sidebar on screen or away, from the stored state and
        the collapsed layout's overlay."""
        collapsed = self.split_view.get_collapsed()
        shown = sidebarmode.sidebar_visible(
            self.settings.get_boolean('sidebar-hidden'),
            collapsed, self._overlay_open)
        self._applying_sidebar_visibility = True
        try:
            self.split_view.set_show_sidebar(shown)
        finally:
            self._applying_sidebar_visibility = False
        self.props.sidebar_visible = shown
        self.props.sidebar_collapsed = collapsed
        # Collapsed, a swipe is the way in and out; pinned there is
        # nothing to swipe.
        self.split_view.set_enable_show_gesture(collapsed)
        self.split_view.set_enable_hide_gesture(collapsed)
        self._sync_translucent_sidebar()

    def dismiss_sidebar_overlay(self):
        """Close the collapsed layout's sidebar overlay, if it is open."""
        if not self.split_view.get_collapsed():
            return
        self._overlay_open = False
        self.split_view.set_show_sidebar(False)
        self._sync_sidebar_visibility()

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
        active account. The icon rail is filled in the same pass, so
        the two never disagree about what exists."""
        current = self.visible_tag()
        sidebar = self.sidebar_list
        sidebar.remove_all()
        self._items = {}
        sections = {}
        rail_groups = {}
        hidden = {tag for tag, key in navigation.HIDE_KEYS.items()
                  if self.settings.get_boolean(key)}
        closed = self._restricted_public_places()
        hidden |= closed
        for tag in closed:
            # A feed the server will not serve, or the reader has not
            # opted into, keeps neither a page nor a stream.
            self.public_pages.pop(tag, None)
            self.navigation.forget(tag)
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
                item.set_suffix(self.notifications_badge)
            section.append(item)
            self._items[tag] = item
            rail_groups.setdefault(group, []).append((tag, icon, title))
        rail_sections = [rail_groups[group] for group in sorted(rail_groups)]
        if self.account is not None:
            self._append_pinned(sidebar, rail_sections)
        self.sidebar_rail.set_sections(rail_sections)
        if current is not None and current in hidden:
            self.show_page('home')
        elif current is not None:
            self._select_item(current)
        self._sync_sidebar_mode()

    def _append_pinned(self, sidebar, rail_sections):
        routes = self.navigation.pinned_routes(
            self.settings, self.account['id'])
        if not routes:
            return
        section = Adw.SidebarSection(title=_('Pinned'))
        sidebar.append(section)
        rail_entries = []
        list_ids = []
        for tag, title, icon in routes:
            if tag.startswith('list:'):
                list_id = tag[5:]
                list_ids.append(list_id)
                title = self.navigation.pinned_lists.get(list_id) or title
            item = Adw.SidebarItem(title=title, icon_name=icon)
            section.append(item)
            self._items[tag] = item
            rail_entries.append((tag, icon, title))
        if rail_entries:
            rail_sections.append(rail_entries)
        if list_ids and not all(i in self.navigation.pinned_lists
                                for i in list_ids):
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
            self.navigation.set_pinned_lists(lists)
            for list_id, title in self.navigation.pinned_lists.items():
                item = self._items.get('list:%s' % list_id)
                if item is not None and item.get_title() != title:
                    item.set_title(title)
                page = self.navigation.pages.get('list:%s' % list_id)
                if page is not None:
                    page.set_title(title)
                self.sidebar_rail.set_title('list:%s' % list_id, title)

        client.request_async('GET', '/lists', callback=done)

    def _select_item(self, tag):
        self.sidebar_rail.set_selected(tag)
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
            from .fedicollections import CollectionsPage as factory
        pages.push_page(self, title, factory(client), tag='menu-%s' % name)

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
        pages.connect_weak(dialog, 'close-request', self._on_admin_closed)
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
        # The manual-code escape hatch belongs to an authorization in
        # flight, because that is the only state a pasted code can be
        # redeemed against -- and the only state where a browser that
        # never came back leaves the user stuck. It disappears once the
        # flow has already been switched to a pasted code.
        self.paste_code_button.set_visible(waiting
                                           and not self.auth.fallback)
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

    def open_help(self):
        """Open the installed help pages (F1, or the primary menu).

        The Mallard pages ship in the bundle, under
        share/help/<lang>/<app id>/, which is where ``HELP_URI`` points.

        Inside a Flatpak that does not work, and it fails quietly: the
        ``help:`` URI is resolved host-side by the OpenURI portal, so the
        viewer's lookup misses ``/app/share/help`` and shows "document
        not found" -- a successful launch, so the fallback never fires.
        Hence the explicit check: sandboxed, go straight to the online
        documentation rather than open a viewer that cannot find
        anything.

        Unsandboxed, the same trap waits wherever no viewer is installed
        or Babble itself is not (running from a build tree, say): the
        portal puts up "no apps available" and reports the launch as
        successful, so F1 opens nothing useful and the online fallback
        never runs either. So ask ``help_available`` first, and treat
        "cannot resolve" as the answer it is -- not as a launch error to
        discover afterwards."""
        if GLib.getenv('FLATPAK_ID') or not help_available():
            self.open_url(DOCS_URL)
            return

        def launched(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                self.open_url(DOCS_URL)

        launcher = Gtk.UriLauncher.new(HELP_URI)
        launcher.launch(self, None, launched)

    def open_url(self, uri):
        """Open *uri* in the web browser through the portal, falling back
        to the default handler. The login depends on this: without it the
        authorization page never opens. It was dropped when the flow moved
        into auth.py, which still emits the URI for the window to open."""

        def launched(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                self._open_url_fallback(uri)

        Gtk.UriLauncher.new(uri).launch(self, None, launched)

    def _open_url_fallback(self, uri):
        def opened(source, result):
            try:
                source.launch_default_for_uri_finish(result)
            except GLib.Error as error:
                self.show_error(_('Could not open the web browser'),
                                error.message)

        app_info = Gio.AppInfo.get_default_for_uri(uri)
        if app_info is None:
            self.show_error(_('Could not open the web browser'),
                            _('No app is set to open web links'))
            return
        app_info.launch_default_for_uri_async(uri, None, None, opened)

    def _on_auth_attended(self, _flow):
        self.present()

    def _on_auth_changed(self, _flow):
        """The login widgets follow the flow.

        The code entry belongs to an authorization nobody is running any
        more once the flow is idle and has not fallen back to a pasted
        code, so it goes away with it. The manual-code button has its
        own rule, in :meth:`refresh_login_widgets`, which runs next.
        """
        if not self.auth.waiting and not self.auth.fallback:
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
        self.auth.use_pasted_code()
        # The field is shown either way: when the authorization could be
        # renamed the browser is on its way to showing a code, and when
        # it could not the user is still here holding one. Hiding it on
        # the first branch left them with a code and nowhere to put it.
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

