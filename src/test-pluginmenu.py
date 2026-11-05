#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A plugin's entries in the app's main menu, and ``Context.compose``,
end to end on a real window and through libpeas.

The entries must exist only while the plugin is enabled, come back when
it is enabled again, reach the plugin once as ``activate(None, window)``,
survive another plugin raising, and not keep the window alive. With no
plugin enabled nothing is added and libpeas is never imported.
``Context.compose`` opens the real composer with the text, keeps a draft
in progress on the draft stack, and does nothing without a session.

Exits 77 (skip) without a display, a compiled gresource or libpeas 2."""

import gc
import os
import sys
import tempfile
import textwrap
import time

scratch = tempfile.mkdtemp(prefix='babble-menu-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
plugin_dir = os.path.join(scratch, 'plugins')
os.environ['BABBLE_PLUGIN_PATH'] = plugin_dir

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)
os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
# Ask without importing: a later check is that nothing imported libpeas.
if '2' not in (gi.Repository.get_default().enumerate_versions('Peas')
               or ()):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)
bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Adw.init()
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mastodon, pages, pluginhost  # noqa: E402
from src import pluginmenu  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def spin(seconds=0.0, until=None):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while True:
        while context.pending():
            context.iteration(False)
        if until is not None and until():
            return True
        if time.monotonic() >= deadline:
            return until is None
        context.iteration(False)
        time.sleep(0.01)


def write_plugin(module, body):
    folder = os.path.join(plugin_dir, module)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, module + '.plugin'), 'w') as out:
        out.write('[Plugin]\nModule=%s\nLoader=python\nName=%s\n'
                  'Version=1.0\nX-Babble-Api=1\n' % (module, module.title()))
    with open(os.path.join(folder, module + '.py'), 'w') as out:
        out.write(textwrap.dedent(body))


write_plugin('menuone', '''
    from babble import plugin

    contexts = []
    calls = []
    asked = []


    class Main(plugin.MainMenuExtension):
        def activate(self):
            contexts.append(self.context)

        def menu_items(self):
            asked.append(1)
            return [plugin.MenuItem('_Alpha', self.alpha),
                    plugin.MenuItem('Beta', self.beta),
                    'not an item']

        def alpha(self, subject, widget):
            calls.append(('alpha', subject, widget))

        def beta(self, subject, widget):
            calls.append(('beta', subject, widget))
''')
write_plugin('menutwo', '''
    from babble import plugin

    calls = []


    class Main(plugin.MainMenuExtension):
        def menu_items(self):
            return [plugin.MenuItem('_Gamma', lambda s, w: calls.append(s))]
''')
write_plugin('menuboom', '''
    from babble import plugin


    class Main(plugin.MainMenuExtension):
        def menu_items(self):
            raise RuntimeError('boom')
''')
write_plugin('actboom', '''
    from babble import plugin


    def explode(subject, widget):
        raise RuntimeError('boom')


    class Main(plugin.MainMenuExtension):
        def menu_items(self):
            return [plugin.MenuItem('_Boom', explode)]
''')

settings = pages.settings()

Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()


def new_window():
    win = BabbleWindow(application=app)
    win.main_stack.set_visible_child_name('shell')
    win.client = mastodon.MastodonClient('https://example.org')
    win.client.token = 'token'
    win.account = {'id': '1', 'acct': 'me',
                   'instance': 'https://example.org'}
    win.notifier.start_polling = lambda: None
    win.streaming.start = lambda client: None
    return win


def menu_rows(model, found=None):
    """``[(label, action)]`` of every item in *model*, sections flattened."""
    found = [] if found is None else found
    for i in range(model.get_n_items()):
        link = model.iterate_item_links(i)
        section = link.get_value() if link.next() else None
        if section is not None:
            menu_rows(section, found)
        else:
            found.append((
                model.get_item_attribute_value(i, 'label', None).get_string(),
                model.get_item_attribute_value(i, 'action', None)
                .get_string()))
    return found


def plugin_rows(win):
    return [r for r in menu_rows(win.primary_menu_button.get_menu_model())
            if r[1].startswith('win.plugin-menu-')]


def labels(win):
    return [label for label, _action in plugin_rows(win)]


window = new_window()
window.present()
spin(until=lambda: window.get_mapped(), seconds=5)

# Nothing enabled: the window builds, adds nothing, imports no libpeas.
check('no plugin enabled imports no libpeas',
      'gi.repository.Peas' not in sys.modules)
check('and creates no host', pluginhost._host is None)
check('the main menu has no plugin entries', plugin_rows(window) == [])
check('entries() is empty without creating a host',
      pluginmenu.entries(window) == [] and pluginhost._host is None)
check('no plugin action exists on the window',
      not any(n.startswith('plugin-menu-') for n in window.list_actions()))

host = pluginhost.host()
check('the host starts', host.start())
host.set_enabled('menuone', True)
import menuone  # noqa: E402  (libpeas imported it)

check('a plugin with only a MainMenuExtension loads',
      'menuone' in host.contexts and 'menuone' not in host.errors
      and len(host.main_extensions['menuone']) == 1
      and host.menu_extensions['menuone'] == [])
check('its entries appear in the main menu, labels and order kept, '
      'a non-item skipped', labels(window) == ['_Alpha', 'Beta'])
rows = menu_rows(window.primary_menu_button.get_menu_model())
actions = [a for _l, a in rows]
check('they sit after the Collections group and before Preferences, '
      'Plugins stays with Preferences',
      actions.index('win.collections') < actions.index('win.plugin-menu-0')
      < actions.index('win.plugin-menu-1') < actions.index('app.preferences')
      and actions.index('app.preferences') + 1 == actions.index('app.plugins'))

lookup = window.lookup_action('plugin-menu-0')
check('each is a Gio action on the window', lookup is not None
      and window.lookup_action('plugin-menu-1') is not None)
lookup.activate(None)
check('activating one reaches the plugin once, as (None, window)',
      menuone.calls == [('alpha', None, window)])
window.lookup_action('plugin-menu-1').activate(None)
check('and the second reaches its own callback',
      [c[0] for c in menuone.calls] == ['alpha', 'beta']
      and menuone.calls[1][1:] == (None, window))

host.set_enabled('menutwo', True)
import menutwo  # noqa: E402

check("a second plugin's entries coexist, in load order",
      labels(window) == ['_Alpha', 'Beta', '_Gamma'])
window.lookup_action('plugin-menu-2').activate(None)
check('and activate their own plugin', menutwo.calls == [None])

host.set_enabled('menuone', False)
check('disabling a plugin removes only its entries',
      labels(window) == ['_Gamma'])
check('and its actions', window.lookup_action('plugin-menu-1') is None
      and window.lookup_action('plugin-menu-0') is not None)
host.set_enabled('menutwo', False)
check('disabling every plugin leaves the menu without a plugin section',
      plugin_rows(window) == []
      and not any(n.startswith('plugin-menu-') for n in window.list_actions()))
before = len(menuone.asked)
host.set_enabled('menuone', True)
check('enabling again brings the entries back', labels(window)
      == ['_Alpha', 'Beta'] and len(menuone.asked) > before)
check('the module was reused, not re-imported', sys.modules['menuone']
      is menuone)

# A second window is filled too, and a window opened later is as well.
second = new_window()
check('a window opened while a plugin is enabled gets its entries',
      labels(second) == ['_Alpha', 'Beta'])
second.lookup_action('plugin-menu-0').activate(None)
check("and activation anchors on that window",
      menuone.calls[-1] == ('alpha', None, second))

# A raising menu_items: the plugin is turned off, the menu keeps working.
host.set_enabled('menutwo', True)
host.set_enabled('menuboom', True)
check('a raising menu_items turns its plugin off',
      spin(until=lambda: 'menuboom' not in host.contexts, seconds=5)
      and 'menuboom' not in settings.get_strv('active-plugins')
      and 'menuboom' in host.errors)
check('the others keep their entries and the menu still builds',
      labels(window) == ['_Alpha', 'Beta', '_Gamma'])
# A raising activate: also turned off, and the rest still work.
host.set_enabled('actboom', True)
check('a raising plugin shows its entry until it fails',
      '_Boom' in labels(window))
window.lookup_action('plugin-menu-3').activate(None)
check('a raising activate turns its plugin off',
      spin(until=lambda: 'actboom' not in host.contexts, seconds=5)
      and 'actboom' in host.errors)
check('its entry goes and the others stay',
      labels(window) == ['_Alpha', 'Beta', '_Gamma'])
window.lookup_action('plugin-menu-0').activate(None)
check('and they still activate', menuone.calls[-1][0] == 'alpha')

# Context.compose.
ctx = menuone.contexts[-1]
check('compose returns nothing (no widget reaches the plugin)',
      ctx.compose(window, 'Hello from a plugin') is None)
dialog = window.compose_dialog
check('it opens the composer with the text in its buffer',
      dialog is not None and dialog.get_root() is window
      and dialog.view.buffer.get_text(
          dialog.view.buffer.get_start_iter(),
          dialog.view.buffer.get_end_iter(), False)
      == 'Hello from a plugin')
check('and posts nothing', not hasattr(dialog.view, 'last_posted')
      and dialog.view._draft_stack == [])
ctx.compose(window, 'Second text')
buf = dialog.view.buffer
check('a draft in progress is kept on the draft stack, the new text '
      'replaces it in the buffer',
      len(dialog.view._draft_stack) == 1
      and buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
      == 'Second text')
stack = list(dialog.view._draft_stack)
ctx.compose(window, 42)
ctx.compose(window, None)
ctx.compose(window, b'bytes')
ctx.compose(None, 'x')
ctx.compose(object(), 'x')
check('a non-str text, or no widget, does nothing and raises nothing',
      buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
      == 'Second text' and dialog.view._draft_stack == stack)

nosession = new_window()
nosession.client = None
nosession.account = None
nosession.compose_dialog = None
ctx.compose(nosession, 'nobody home')
check('without a signed-in session it does nothing',
      nosession.compose_dialog is None)
nosession.destroy()

host.set_enabled('menuone', False)
window.compose_dialog.view.buffer.set_text('')
ctx.compose(window, 'late')
check('a disabled plugin cannot compose',
      window.compose_dialog.view.buffer.get_text(
          window.compose_dialog.view.buffer.get_start_iter(),
          window.compose_dialog.view.buffer.get_end_iter(), False) == '')

# The window is not kept alive by the refill callback. BabbleWindow
# itself is not freed in a test process for reasons of its own (a bare
# one is not, with or without plugins), so the callback is judged on a
# plain Adw window with its own section: neither the section's owner nor
# the host's listener list may keep it.
host.set_enabled('menuone', True)
freed = []
probe = Adw.ApplicationWindow(application=app)
probe.weak_ref(lambda: freed.append(True))
section = Gio.Menu()
probe_menu = pluginmenu.MainMenu(probe, section)
probe._probe_menu = probe_menu  # owned by the window, as in the app
listeners = len(pluginhost._menu_listeners)
check('the probe window shows the entries', section.get_n_items() == 3
      and probe.lookup_action('plugin-menu-0') is not None)
probe.destroy()
probe = probe_menu = None
for _ in range(20):
    gc.collect()
    spin(0.05)
    if freed:
        break
check('a closed window is freed though plugins refill menus', freed == [True])
host.set_enabled('menuone', False)
check('and the host drops the listener of the freed window',
      len(pluginhost._menu_listeners) == listeners - 1)

host.shutdown()
check('shutdown removes every plugin entry', plugin_rows(window) == [])

window.destroy()
second.destroy()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
