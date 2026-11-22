# SPDX-License-Identifier: GPL-3.0-or-later

import os

import gi

from .i18n import gettext as _

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gio, GObject, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import clipboard  # noqa: E402
from . import appsettings
from .weakconnect import connect_weak  # noqa: E402
from . import pluginmenu  # noqa: E402
from . import navigation  # noqa: E402
from . import sidebarmode  # noqa: E402
from . import webcolumn  # noqa: E402
from .windowbackground import WindowBackground  # noqa: E402
from .windowlogin import WindowLogin  # noqa: E402
from .windowplaces import SHOW_RESTRICTED_KEY, WindowPlaces  # noqa: E402
from .windowsession import WindowSession  # noqa: E402
from .mastodon import summarize_error
from .oauthpkce import OAUTH_REDIRECT  # noqa: E402
from .sidebarhost import SidebarHost  # noqa: E402
from .sidebarrail import SidebarRail  # noqa: E402

# Settings the home page reads once when it is built, so a change rebuilds it.
HOME_FILTER_KEYS = ('hide-boosts-home', 'hide-replies-non-follows')

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
PROJECT_URL = 'https://github.com/mauriciobc/Babble'
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




@Gtk.Template(resource_path='/io/github/mauriciobc/Babble/window.ui')
class BabbleWindow(WindowLogin, WindowSession, WindowPlaces,
                   WindowBackground, Adw.ApplicationWindow):
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
    plugin_menu_section = Gtk.Template.Child()
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
        self._setup_state()
        self._setup_auth()
        self._setup_settings_and_banners()
        self._setup_streaming()
        self._setup_actions()
        self._setup_account_ui()
        self._setup_sidebar_and_lifecycle()

        self._refresh_sidebar()
        self.refresh_login_widgets()
        self.restore_account()

    def _setup_state(self):
        self.client = None
        self.account = None
        self._no_keyring_shown = False
        self.timeline_model = None
        self.compose_dialog = None
        # Files handed over by Open With (or a bare compose request)
        # before an account was signed in; delivered once one is.
        self._pending_compose = None
        self._unread_handler = None
        self.navigation = navigation.Navigation(navigation.PlaceHost(
            client=lambda: self.client,
            account=lambda: self.account,
            make_sidebar_control=self.make_sidebar_control,
            timeline_place=self._timeline_place,
            restricted_public_places=self._restricted_public_places,
            notify_place_built=self._public_place_built,
            sync_streaming=self._sync_streaming,
            mount_home=self._mount_home,
            mount_notifications=self._mount_notifications,
            clear_home=self._clear_home_bin,
            clear_notifications=self._clear_notifications_page,
        ))
        # Sidebar rebuild, mode/visibility sync and pinned entries.
        # Runtime overlay/rail state lives on the host (ARCH-15).
        self.sidebar_host = SidebarHost(self)
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

    def _setup_settings_and_banners(self):
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
        self.settings = appsettings.settings()
        self.settings.bind('window-width', self, 'default-width',
                            Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-height', self, 'default-height',
                            Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind('window-maximized', self, 'maximized',
                            Gio.SettingsBindFlags.DEFAULT)
        # The composer saves a draft when it closes; the banner reacts
        # live instead of waiting for the next account sync.
        connect_weak(self.settings, 'changed::local-drafts',
                           self._refresh_draft_banner)
        # The banners exist to offer their action, so their buttons are
        # the suggested ones (AdwBanner:button-style, since 1.7). Set
        # once, here: it never changes, so doing it on every draft
        # change was work for nothing.
        self.draft_banner.set_button_style(Adw.BannerButtonStyle.SUGGESTED)
        self._setup_copied_link_banner()
        self._setup_offline_banner()

    def _setup_actions(self):
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
        self._plugin_menu = pluginmenu.MainMenu(self, self.plugin_menu_section)

    def _setup_account_ui(self):
        self.switcher = account_store.AccountSwitcher(self.settings)
        self.account_button.set_popover(self.switcher)
        connect_weak(self.switcher, 'switch', self._on_switcher_switch)
        connect_weak(self.switcher, 'remove',
                           self.on_remove_account_requested)
        connect_weak(self.switcher, 'add', self._on_switcher_add)

        # The icon rail is the narrow face of the sidebar, and shares
        # its mode menu and the account button.
        self.sidebar_rail = SidebarRail()
        self.sidebar_rail_slot.append(self.sidebar_rail)
        connect_weak(self.sidebar_rail, 'activated',
                           self._on_rail_activated)
        # Each page header owns its control strip; the strip binds to
        # this window's properties rather than the window updating it.
        self.home_header.pack_start(self.make_sidebar_control())
        self.notifications_header.pack_start(
            self.make_sidebar_control(include_bell=False))

    def _setup_sidebar_and_lifecycle(self):
        from . import preferences
        self._preferences = preferences
        connect_weak(self.settings, 'changed', self._on_setting_changed)
        connect_weak(Adw.StyleManager.get_default(), 'notify::dark',
                           self._on_dark_changed)
        preferences.apply_style(self, self.settings)
        connect_weak(self.settings, 'changed::sidebar-style',
                           self._on_sidebar_state_changed)
        connect_weak(self.settings, 'changed::sidebar-hidden',
                           self._on_sidebar_state_changed)
        connect_weak(self.split_view, 'notify::collapsed',
                           self._on_sidebar_collapsed)
        connect_weak(self.split_view, 'notify::show-sidebar',
                           self._on_sidebar_shown)
        connect_weak(self.main_stack, 'notify::visible-child-name',
                           self._sync_translucent_sidebar)
        self._sync_sidebar_visibility()
        webcolumn.watch_split(self.web_split)
        connect_weak(self, 'close-request', self._on_close_request)
        # 'destroy' alone is not enough: PyGObject can keep the wrapper
        # (and with it the window) alive past gtk_window_destroy(), and
        # the signal only fires on dispose. Unrealize always comes first.
        connect_weak(self, 'destroy', self._cancel_sidebar_visibility)
        connect_weak(self, 'unrealize',
                           self._cancel_sidebar_visibility)
        connect_weak(self, 'map', self._defer_sidebar_visibility)
        connect_weak(self, 'notify::visible', self._on_visible_changed)
        self._sync_global_shortcut()

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
                           'favorite-feedchef-ids', SHOW_RESTRICTED_KEY):
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
        connect_weak(system, 'notify::online', self._on_online_changed)
        self._on_online_changed(system)

    def _on_online_changed(self, system, *_args):
        self.offline_banner.set_revealed(not system.props.online)

    @staticmethod
    def _grab_focus(widget):
        """Focus *widget* only when it is mapped (unit tests skip
        :meth:`Gtk.Window.present`)."""
        if widget is not None and widget.get_mapped():
            widget.grab_focus()

    # Streaming and desktop notifications

    # Background portal

    # Background status

    # Global shortcut portal

    # Pages

    # Composer

    # Sidebar — logic lives on SidebarHost; forwards keep PlaceHost
    # identity, connect_weak targets and the template callback stable.

    def make_sidebar_control(self, include_bell=True):
        """A control strip for a page header, bound to this window."""
        return self.sidebar_host.make_control(include_bell=include_bell)

    def _on_rail_activated(self, rail, tag):
        self.sidebar_host.on_rail_activated(rail, tag)

    def _on_sidebar_state_changed(self, settings, key):
        self.sidebar_host.on_state_changed(settings, key)

    def _on_sidebar_collapsed(self, split_view, param):
        self.sidebar_host.on_collapsed(split_view, param)

    def _defer_sidebar_visibility(self, *_args):
        self.sidebar_host.defer_visibility()

    def _cancel_sidebar_visibility(self, *_args):
        self.sidebar_host.cancel_visibility()

    def _on_sidebar_shown(self, split_view, param):
        self.sidebar_host.on_shown(split_view, param)

    def _on_toggle_sidebar(self, action, param):
        self.sidebar_host.on_toggle(action, param)

    def _on_sidebar_mode_changed(self, action, value):
        self.sidebar_host.on_mode_changed(action, value)

    def _sync_sidebar_mode(self):
        self.sidebar_host.sync_mode()

    def _sync_sidebar_visibility(self):
        self.sidebar_host.sync_visibility()

    def dismiss_sidebar_overlay(self):
        """Close the collapsed layout's sidebar overlay, if it is open."""
        self.sidebar_host.dismiss_overlay()

    def _refresh_sidebar(self, *_args):
        self.sidebar_host.refresh()

    def _select_item(self, tag):
        self.sidebar_host.select_item(tag)

    @Gtk.Template.Callback()
    def on_sidebar_activated(self, sidebar, index):
        self.sidebar_host.on_activated(sidebar, index)

    # Navigation

    # Login

    # The OAuth flow

    # LoginFlow owns the attempt bookkeeping, the loopback listener and
    # the keyring writes. What arrives here is a handful of things to
    # show, plus the one event that matters: an account is signed in.

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

    # Preferences

    def show_preferences(self):
        from .preferences import present_preferences
        return present_preferences(self)

    @property
    def timeline_page(self):
        """Home timeline content: single source in navigation.children."""
        return self.navigation.children.get('home')

    @timeline_page.setter
    def timeline_page(self, page):
        """Register or drop the home content child (tests and rebuilds)."""
        if page is None:
            self.navigation.children.pop('home', None)
            self.navigation.pages.pop('home', None)
            return
        self.navigation.register_external('home', self.page_home, page)

    @property
    def notifications_page(self):
        """Notifications content: single source in navigation.children."""
        return self.navigation.children.get('notifications')

    @notifications_page.setter
    def notifications_page(self, page):
        """Register or drop the notifications content child."""
        if page is None:
            self.navigation.children.pop('notifications', None)
            self.navigation.pages.pop('notifications', None)
            return
        self.navigation.register_external(
            'notifications', self.page_notifications, page)

    def _apply_auto_refresh(self):
        seconds = int(self.settings.get_string('auto-refresh'))
        public = self.navigation.public_pages.values()
        for page in [self.timeline_page, *public]:
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


    # The templates bind a handler only when it lives on this class, so
    # each of these is the decorated entry point and the body is on the
    # mixin named beside it.

    @Gtk.Template.Callback()
    def on_draft_restore(self, *args):
        self._draft_restore(*args)

    @Gtk.Template.Callback()
    def on_login_back_clicked(self, *args):
        self._login_back_clicked(*args)

    @Gtk.Template.Callback()
    def on_instance_changed(self, *args):
        self._instance_changed(*args)

    @Gtk.Template.Callback()
    def on_login_clicked(self, *args):
        self._login_clicked(*args)

    @Gtk.Template.Callback()
    def on_cancel_login_clicked(self, *args):
        self._cancel_login_clicked(*args)

    @Gtk.Template.Callback()
    def on_paste_code_clicked(self, *args):
        self._paste_code_clicked(*args)

    @Gtk.Template.Callback()
    def on_code_entered(self, *args):
        self._on_code_entered(*args)

    def on_oauth_callback(self, uri, redirect_uri=OAUTH_REDIRECT):
        self._oauth_callback(uri, redirect_uri)
