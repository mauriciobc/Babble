# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import gi

from .i18n import gettext as _

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .window import BabbleWindow


class BabbleApplication(Adw.Application):
    def __init__(self, version):
        super().__init__(application_id='io.github.mauriciobc.Babble',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS |
                         Gio.ApplicationFlags.HANDLES_OPEN,
                         resource_base_path='/io/github/mauriciobc/Babble')
        self.version = version
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
        for file in files:
            uri = file.get_uri()
            # A scheme handler can be entered more than once for the
            # same redirect (a retry from the browser, a spawned handler
            # racing a D-Bus Open); the window spends each code once
            # regardless, but a duplicate never reaches it either.
            if uri in delivered:
                continue
            delivered.add(uri)
            win.on_oauth_callback(uri)

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
