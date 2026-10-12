# SPDX-License-Identifier: GPL-3.0-or-later

import re
import sys
import urllib.parse

import gi

from .i18n import gettext as _

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import desktopnotify
from .statustext import is_web_link
from .window import BabbleWindow

# Links the desktop hands over by scheme: FEP-07d7 ``web+ap://`` object
# links, and the ``web+mastodon://`` intents Mastodon's web UI registers
# (``follow?uri=…`` and ``share?text=…``, as its IntentsController reads
# them). Each is turned into something opened inside the window; none of
# them is ever passed on to another handler.
WEB_SCHEMES = ('web+ap', 'web+mastodon')

# A server name has a dot (``javascript``, ``localhost`` name no
# instance), or is a bracketed IPv6 literal.
_HOST = re.compile(r'[\w\-]+(?:\.[\w\-]+)+|\[[0-9A-Fa-f:.]+\]')
_ACCT = re.compile(r'@?([^@\s/\\:?#]+)@([\w.\-]+\.[\w\-]+)')


def _https_url(parts):
    """The https URL for the split *parts*: their host, path and query.
    None when there is no host, a ``user@`` part or a malformed port."""
    if '@' in parts.netloc or '\\' in parts.netloc:
        return None
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host or port == 0:
        return None
    host = '[%s]' % host if ':' in host else host
    if not _HOST.fullmatch(host):
        return None
    netloc = host + (':%d' % port if port is not None else '')
    url = urllib.parse.urlunsplit(
        ('https', netloc, parts.path or '/', parts.query, ''))
    return url if is_web_link(url) else None


def parse_web_uri(uri):
    """What a ``web+ap://`` or ``web+mastodon://`` *uri* asks for.

    ``('resolve', https_url)`` for an object to look up, ``('account',
    'user@host')`` for a profile, ``('share', text)`` for a new post;
    None for anything malformed or not understood."""
    try:
        parts = urllib.parse.urlsplit(uri or '')
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme == 'web+ap':
        # FEP-07d7: the object's https address, scheme swapped.
        url = _https_url(parts)
        return ('resolve', url) if url else None
    if scheme != 'web+mastodon':
        return None
    params = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    action = parts.netloc.lower()
    if action == 'share':
        return ('share', params.get('text', [''])[0])
    if action != 'follow':
        return None
    target = params.get('uri', [''])[0].strip()
    if target[:5].lower() == 'acct:':
        target = target[5:]
    acct = _ACCT.fullmatch(target)
    if acct:
        return ('account', '%s@%s' % (acct.group(1), acct.group(2).lower()))
    if target[:8].lower() != 'https://':
        return None
    try:
        url = _https_url(urllib.parse.urlsplit(target))
    except ValueError:
        return None
    return ('resolve', url) if url else None


def open_web_target(win, target):
    """Open a :func:`parse_web_uri` *target* in the signed-in *win*."""
    from . import pages
    kind, value = target
    if kind == 'share':
        dialog = win.present_composer()
        if dialog is not None:
            dialog.view.prefill_text(value)
    elif kind == 'account':
        # The profile sheet looks the handle up, resolving it remotely.
        pages.open_profile(win, win.client, acct=value)
    else:
        # Asked for by name: resolved in the app whatever the
        # aggressive-resolving preference says.
        pages.open_link(win, value, resolve=True)


class BabbleApplication(Adw.Application):
    def __init__(self, version):
        super().__init__(application_id='io.github.mauriciobc.Babble',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS |
                         Gio.ApplicationFlags.HANDLES_OPEN,
                         resource_base_path='/io/github/mauriciobc/Babble')
        self.version = version
        self._search_provider = None
        # web+ap/web+mastodon links opened before a session exists, and
        # the (stack, handler id) waiting for the shell to deliver them.
        self._pending_web = []
        self._pending_web_handler = None
        # Started by D-Bus for a shell search there is no window to keep
        # the service alive between Shell's calls; linger for a while.
        self.set_inactivity_timeout(10000)
        # Buttons on desktop notifications activate these app actions.
        desktopnotify.install_app_actions(self)
        self.connect('open', self.on_open)
        self.create_action('quit', lambda *args: self.quit(), ['<primary>q'])
        self.create_action('about', self.on_about_action)
        self.create_action('preferences', self.on_preferences_action,
                           ['<primary>comma'])
        self.create_action('shortcuts', self.on_shortcuts_action,
                           ['<primary>question', '<primary>F1'])
        self.create_action('increase-font-size',
                           lambda *a: self.step_text_size(1),
                           ['<primary>plus', '<primary>equal',
                            '<primary>KP_Add'])
        self.create_action('decrease-font-size',
                           lambda *a: self.step_text_size(-1),
                           ['<primary>minus', '<primary>KP_Subtract'])
        self.create_action('reset-font-size',
                           lambda *a: self.step_text_size(0),
                           ['<primary>0', '<primary>KP_0'])
        self.create_action('scroll-page-down',
                           lambda *a: self.scroll_page(1), ['Page_Down'])
        self.create_action('scroll-page-up',
                           lambda *a: self.scroll_page(-1), ['Page_Up'])
        opener = Gio.SimpleAction.new('open-notifications',
                                      GLib.VariantType.new('s'))
        opener.connect('activate', self.on_open_notifications)
        self.add_action(opener)
        # The desktop file's jump-list entries: with DBusActivatable the
        # shell activates the app action named like the [Desktop Action].
        self.create_action('compose', self.on_compose_action)
        self.create_action('show-notifications', self.on_open_notifications)
        open_uri = Gio.SimpleAction.new('open-uri', GLib.VariantType.new('s'))
        open_uri.connect('activate', self.on_open_uri)
        self.add_action(open_uri)
        self.set_accels_for_action('window.close', ['<primary>w'])
        self.set_accels_for_action('win.compose', ['<primary>n'])
        self.set_accels_for_action('win.reload', ['<primary>r', 'F5'])
        self.set_accels_for_action('win.back', ['<alt>Left', 'Escape'])
        self.set_accels_for_action('win.back-home', ['<alt>Home'])
        self.set_accels_for_action('win.search',
                                   ['<primary>f', '<primary>k'])
        self.set_accels_for_action('win.show-notifications',
                                   ['<primary><shift>n'])
        for number, tag in enumerate(
                ('home', 'notifications', 'conversations', 'search',
                 'favourites', 'bookmarks', 'hashtags', 'explore',
                 'local'), start=1):
            self.set_accels_for_action(
                "win.goto-place('%s')" % tag, ['<primary>%d' % number])

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Bundled icons (sidebar, bell, compose) live in the gresource;
        # GTK only finds them once the icons tree is registered.
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()) \
            .add_resource_path('/io/github/mauriciobc/Babble/icons')

    def do_activate(self):
        self.ensure_window().present()

    def ensure_window(self):
        """The one window callbacks are routed to.

        ``activate`` and ``open`` can both arrive for the same launch,
        and creating a window per dispatch would hand the redirect to a
        window nobody is looking at.
        """
        windows = self.get_windows()
        return windows[0] if windows else BabbleWindow(application=self)

    def do_shutdown(self):
        for win in self.get_windows():
            if hasattr(win, 'streaming'):
                win.streaming.stop()
        Adw.Application.do_shutdown(self)

    def do_dbus_register(self, connection, object_path):
        if not Adw.Application.do_dbus_register(self, connection,
                                                object_path):
            return False
        # GNOME Shell's search provider lives on the application's own
        # bus name, exported as long as the name is held.
        from . import searchprovider
        self._search_provider = searchprovider.SearchProvider(self)
        self._search_provider.register(connection,
                                       searchprovider.OBJECT_PATH)
        return True

    def do_dbus_unregister(self, connection, object_path):
        if self._search_provider is not None:
            self._search_provider.unregister(connection)
            self._search_provider = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def on_open_notifications(self, _action, _param):
        win = self.ensure_window()
        win.present()
        win.show_page('notifications')

    def on_shortcuts_action(self, *args):
        builder = Gtk.Builder.new_from_resource(
            '/io/github/mauriciobc/Babble/shortcuts-dialog.ui')
        builder.get_object('shortcuts_dialog').present(
            self.props.active_window)

    def step_text_size(self, direction):
        """Ctrl+plus/minus/0: move the text-size preference."""
        from . import pages
        settings = pages.settings()
        order = ('small', 'normal', 'large')
        current = order.index(settings.get_string('text-size'))
        index = 1 if direction == 0 else max(
            0, min(len(order) - 1, current + direction))
        settings.set_string('text-size', order[index])

    def scroll_page(self, direction):
        """Page Up/Down scroll the list the focus is not inside of."""
        win = self.props.active_window
        if win is None or isinstance(win.get_focus(), Gtk.Editable) \
                or isinstance(win.get_focus(), Gtk.TextView):
            return
        scrolled = self._find_scrolled(win.navigation_view.get_visible_page())
        if scrolled is None:
            return
        adjustment = scrolled.get_vadjustment()
        adjustment.set_value(adjustment.get_value() + direction
                             * adjustment.get_page_size() * 0.9)

    def _find_scrolled(self, widget):
        if isinstance(widget, Gtk.ScrolledWindow):
            return widget
        child = widget.get_first_child() if widget is not None else None
        while child is not None:
            found = self._find_scrolled(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def on_open(self, _app, files, _n_files, _hint):
        win = self.ensure_window()
        win.present()
        delivered = set()
        paths = []
        for file in files:
            uri = file.get_uri()
            # A scheme handler can be entered more than once for the
            # same redirect (a retry from the browser, a spawned handler
            # racing a D-Bus Open); the window spends each code once
            # regardless, but a duplicate never reaches it either.
            if uri in delivered:
                continue
            delivered.add(uri)
            if file.has_uri_scheme('babble'):
                win.on_oauth_callback(uri)
            elif (GLib.Uri.peek_scheme(uri) or '') in WEB_SCHEMES:
                self.open_web_uri(win, uri)
            elif file.is_native() and file.get_path() is not None:
                # "Open With Babble" on an image or a video.
                paths.append(file.get_path())
        if paths:
            win.compose_with_files(paths)

    def open_web_uri(self, win, uri):
        """Route a ``web+ap://``/``web+mastodon://`` *uri* inside *win*;
        before a session exists it waits for the shell to come up."""
        target = parse_web_uri(uri)
        if target is None:
            return
        if win.client is not None and win.account is not None:
            open_web_target(win, target)
            return
        if target not in self._pending_web:
            self._pending_web.append(target)
        stack = win.main_stack
        waiting = self._pending_web_handler
        if waiting is not None and waiting[0] is not stack:
            waiting[0].disconnect(waiting[1])
            waiting = None
        if waiting is None:
            self._pending_web_handler = (stack, stack.connect(
                'notify::visible-child-name', self._on_session_page))
        if stack.get_visible_child_name() == 'login':
            # Translators: toast when a web+ap:// or web+mastodon:// link
            # is opened before any account is signed in.
            win.show_toast(_('Sign in to open this link'))

    def _on_session_page(self, stack, _pspec):
        win = stack.get_root()
        if stack.get_visible_child_name() != 'shell' \
                or getattr(win, 'client', None) is None \
                or getattr(win, 'account', None) is None:
            return
        stack.disconnect(self._pending_web_handler[1])
        self._pending_web_handler = None
        pending, self._pending_web = self._pending_web, []
        for target in pending:
            open_web_target(win, target)

    def on_compose_action(self, *_args):
        win = self.ensure_window()
        win.present()
        win.compose_with_files()

    def on_open_uri(self, _action, param):
        """Open a post, profile or hashtag link inside the window, the
        way a link clicked in a post is routed."""
        from . import pages
        win = self.ensure_window()
        win.present()
        pages.open_link(win, param.get_string())

    def on_about_action(self, *args):
        about = Adw.AboutDialog(application_name='Babble',
                                application_icon='io.github.mauriciobc.Babble',
                                developer_name='Mauricio BC',
                                developers=['Mauricio BC'],
                                version=self.version,
                                copyright='© 2026 Mauricio BC',
                                translator_credits=_('translator-credits'),
                                license_type=Gtk.License.GPL_3_0)
        about.present(self.props.active_window)

    def on_preferences_action(self, *args):
        win = self.props.active_window
        if win is None:
            return
        win.show_preferences()

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f'app.{name}', shortcuts)


def main(version):
    app = BabbleApplication(version)
    return app.run(sys.argv)
