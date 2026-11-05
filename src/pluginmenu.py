# SPDX-License-Identifier: GPL-3.0-or-later

"""Two small places plugins reach the window: the plugin section of the
main menu, and the composer.

The section is refilled from the enabled plugins' ``MainMenuExtension``s
whenever the host says they changed. With no plugin enabled the host is
never created and libpeas is never imported: :func:`entries` finds
``pluginhost._host`` empty and returns nothing.
"""

from gi.repository import Gio, Gtk

from . import plugin as api
from . import pluginhost

ACTION_PREFIX = 'plugin-menu-'


def entries(window):
    """``[(label, activate)]`` from every enabled plugin's main menu
    extension. *activate* holds *window* weakly."""
    host = pluginhost._host
    if host is None or not host.main_extensions:
        return []
    found = []
    for plugin_id, extensions in list(host.main_extensions.items()):
        context = host.contexts.get(plugin_id)
        for extension in extensions:
            if context is None or not context.active:
                break
            items = context.guard(extension.menu_items, widget=window,
                                  default=None) or []
            for item in items:
                if isinstance(item, api.MenuItem):
                    found.append((item.label, pluginhost._menu_activate(
                        context, item, None, window)))
    return found


def compose(widget, text):
    """Open the composer in *widget*'s window with *text*, as the
    ``share`` target does; a no-op when there is no window, no signed-in
    session, or *text* is not a str. Never posts, returns no widget."""
    if not isinstance(text, str) or not isinstance(widget, Gtk.Widget):
        return
    from . import pages
    window = pages.window_of(widget)
    if window is None:
        return
    dialog = window.present_composer()
    if dialog is not None:
        dialog.view.prefill_text(text)


class MainMenu:
    """Keeps *section*, a Gio.Menu inside *window*'s main menu, in step
    with the plugins. It holds the window weakly (the window owns it) and
    the host holds it weakly, so neither keeps the window alive."""

    def __init__(self, window, section):
        self._window_ref = window.weak_ref()
        self._section = section
        self._names = []
        pluginhost.on_menu_changed(self.refresh)
        self.refresh()

    def refresh(self):
        window = self._window_ref()
        if window is None:
            return
        found = entries(window)
        if not found and not self._names:
            return
        for name in self._names:
            window.remove_action(name)
        self._names = []
        self._section.remove_all()
        for number, (label, activate) in enumerate(found):
            name = '%s%d' % (ACTION_PREFIX, number)
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', activate)  # holds the window weakly
            window.add_action(action)
            self._names.append(name)
            self._section.append(label, 'win.' + name)
