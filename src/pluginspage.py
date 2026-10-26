# SPDX-License-Identifier: GPL-3.0-or-later

"""The Plugins page: every plugin found, a switch for each, why one is
not running, and the plugin's own settings when it has them.

Opened from the main menu (``app.plugins``) rather than as a tab of
Preferences, which has no room for another. It is an
Adw.PreferencesDialog with a single page, so it reads as a page of its
own, with no view switcher. Opening it is what starts the plugin host:
until a plugin is enabled, or this page opened, libpeas is not loaded.
"""

import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import pages, pluginhost  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .pages import connect_weak  # noqa: E402


def user_plugins_dir():
    return os.path.join(GLib.get_user_data_dir(), 'babble', 'plugins')


def is_shipped(info):
    """Whether *info*'s plugin came with Babble, rather than being
    installed by the user."""
    shipped = pluginhost.search_paths()[-1]
    module_dir = os.path.normpath(info.get_module_dir() or '')
    return os.path.commonpath([module_dir, shipped]) == shipped


class PluginRow(Adw.ActionRow):
    """One plugin: name, description or why it is not running, a switch,
    and a button for its settings while it is on and has any."""

    __gtype_name__ = 'BabblePluginRow'

    def __init__(self, dialog, info):
        super().__init__(title=info.get_name() or info.get_module_name(),
                         title_lines=1)
        self.plugin_id = info.get_module_name()
        self.description = info.get_description() or ''
        self.dialog_ref = dialog.weak_ref()
        self.settings_button = pages.icon_button(
            'emblem-system-symbolic',
            # Translators: %s is a plugin's name.
            _('%s Settings') % self.get_title())
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        pages.set_accessible_label(self.switch, self.get_title())
        self.add_suffix(self.settings_button)
        self.add_suffix(self.switch)
        self.set_activatable_widget(self.switch)
        self.warning = Gtk.Image(icon_name='dialog-warning-symbolic',
                                 visible=False)
        self.warning.add_css_class('error')
        self.add_prefix(self.warning)
        self.sync()
        connect_weak(self.switch, 'notify::active', self._on_switched)
        connect_weak(self.settings_button, 'clicked', self._on_settings)

    def sync(self):
        host = pluginhost.host()
        enabled = host.is_enabled(self.plugin_id)
        if self.switch.get_active() != enabled:
            self.switch.set_active(enabled)
        error = host.errors.get(self.plugin_id)
        self.set_subtitle(error or self.description)
        self.warning.set_visible(bool(error))
        self.settings_button.set_visible(
            self.plugin_id in host.contexts
            and host.has_preferences(self.plugin_id))

    def _on_switched(self, switch, _pspec):
        host = pluginhost.host()
        if switch.get_active() != host.is_enabled(self.plugin_id):
            host.set_enabled(self.plugin_id, switch.get_active())
        self.sync()

    def _on_settings(self, _button):
        dialog = self.dialog_ref()
        if dialog is not None:
            dialog.show_plugin_settings(self.plugin_id, self.get_title())


class PluginsDialog(Adw.PreferencesDialog):

    __gtype_name__ = 'BabblePluginsDialog'

    def __init__(self):
        super().__init__(title=_('Plugins'), search_enabled=False)
        self.rows = []
        # A plugin that fails is turned off from an idle: follow the key
        # so its row says so while the page is open.
        connect_weak(pages.settings(), 'changed::' + pluginhost.ACTIVE_KEY,
                     self._on_active_changed)
        page = Adw.PreferencesPage(title=_('Plugins'),
                                   icon_name='application-x-addon-symbolic')
        self.add(page)
        host = pluginhost.host()
        if not host.start():
            status = Adw.StatusPage(
                icon_name='application-x-addon-symbolic',
                title=_('Plugins Are Unavailable'),
                description=_('Babble needs libpeas 2 and its Python loader '
                              'to load plugins'))
            group = Adw.PreferencesGroup()
            group.add(status)
            page.add(group)
            return
        shipped, installed = [], []
        for info in sorted(host.plugins(),
                           key=lambda i: (i.get_name() or '').lower()):
            (shipped if is_shipped(info) else installed).append(info)

        group = Adw.PreferencesGroup(title=_('Included With Babble'))
        for info in shipped:
            self._add_row(group, info)
        if shipped:
            page.add(group)

        group = Adw.PreferencesGroup(
            title=_('Installed by You'),
            description=_('Plugins you install can do anything Babble can, '
                          'including reading and posting with your account. '
                          'Only turn on plugins you trust.'))
        for info in installed:
            self._add_row(group, info)
        folder = Adw.ButtonRow(title=_('_Open Plugins Folder'),
                               use_underline=True,
                               end_icon_name='adw-external-link-symbolic')
        connect_weak(folder, 'activated', self._on_open_folder)
        group.add(folder)
        page.add(group)

    def _add_row(self, group, info):
        row = PluginRow(self, info)
        group.add(row)
        self.rows.append(row)

    def sync(self):
        for row in self.rows:
            row.sync()

    def _on_active_changed(self, _settings, _key):
        self.sync()

    def show_plugin_settings(self, plugin_id, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                      margin_top=24, margin_bottom=24,
                      margin_start=12, margin_end=12)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            child=Adw.Clamp(child=box))
        view = Adw.ToolbarView(content=scrolled)
        view.add_top_bar(Adw.HeaderBar())
        subpage = Adw.NavigationPage(title=title, child=view)
        pluginhost.host().attach_preferences(plugin_id, subpage, box)
        self.push_subpage(subpage)

    def _on_open_folder(self, _row):
        path = user_plugins_dir()
        os.makedirs(path, exist_ok=True)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
        launcher.launch(self.get_root(), None, None)


def present_plugins(window):
    """The Plugins page for *window*, reusing the open one, as
    ``present_preferences`` does."""
    visible = window.get_visible_dialog()
    if isinstance(visible, PluginsDialog):
        visible.sync()
        visible.present(window)
        return visible
    dialog = PluginsDialog()
    dialog.present(window)
    return dialog
