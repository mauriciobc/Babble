#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugins, end to end through libpeas: real plugins written to a
temporary directory are found, refused or loaded, extend a post's menu
and a hashtag page, are turned off when they raise, and leave nothing
behind once their page or the plugin itself is gone. Exits 77 (skip)
without a display, or without libpeas 2 and its Python loader."""

import builtins
import gc
import http.server
import os
import sys
import tempfile
import textwrap
import threading
import time
import weakref

# GLib reads the user directories once; point them at a scratch tree
# before anything asks.
scratch = tempfile.mkdtemp(prefix='babble-plugins-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
plugin_dir = os.path.join(scratch, 'plugins')
os.environ['BABBLE_PLUGIN_PATH'] = plugin_dir
os.environ.setdefault('WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS', '1')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
try:
    gi.require_version('Peas', '2')
    from gi.repository import Peas  # noqa: E402, F401
except (ImportError, ValueError):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import hashtags, pages, pluginhost, pluginspage, statusmenu  # noqa: E402,E501
from src import plugin as api  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        gc.collect()
        if ref() is None:
            return True
    return ref() is None


def spin(seconds=0.0, until=None):
    """Run the main loop for *seconds*, or until *until()* holds."""
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


# A server that answers after a pause, so a request can be in flight
# while its plugin is turned off.

class _Slow(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.3)
        body = b'{"tootCount": 12}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Slow)
threading.Thread(target=server.serve_forever, daemon=True).start()
SERVER = 'http://127.0.0.1:%d/stats' % server.server_address[1]


def write_plugin(module, body, api_version='1', extra=''):
    folder = os.path.join(plugin_dir, module)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, module + '.plugin'), 'w') as out:
        out.write('[Plugin]\nModule=%s\nLoader=python\nName=%s\n'
                  'Version=1.0\n' % (module, module.title()))
        if api_version is not None:
            out.write('X-Babble-Api=%s\n' % api_version)
        out.write(extra)
    with open(os.path.join(folder, module + '.py'), 'w') as out:
        out.write(textwrap.dedent(body))


write_plugin('goodplug', '''
    import weakref

    from gi.repository import Gtk
    from babble import plugin

    events = []
    extensions = []


    class Banner(Gtk.Box):
        """Holds its extension and connects to it: the cycle a bare
        connect would leak."""

        def __init__(self, extension):
            super().__init__()
            self.extension = extension
            self.button = Gtk.Button(label='Stats')
            self.append(self.button)
            extension.connect(self.button, 'clicked', extension.clicked)


    class Menu(plugin.PostMenuExtension):
        def activate(self):
            events.append('menu activate')

        def deactivate(self):
            events.append('menu deactivate')

        def menu_items(self, post):
            return [plugin.MenuItem('Save to Test', self.save)]

        def save(self, post, widget):
            events.append(('saved', post))


    class Page(plugin.HashtagPageExtension):
        def activate(self):
            events.append('page activate %s' % self.hashtag)
            extensions.append(weakref.ref(self))

        def deactivate(self):
            events.append('page deactivate %s' % self.hashtag)

        def create_banner(self):
            self.banner = Banner(self)
            return self.banner

        def clicked(self, button):
            events.append('clicked')
''')
write_plugin('netplug', '''
    from babble import plugin

    answers = []


    class Page(plugin.HashtagPageExtension):
        def activate(self):
            self.context.settings.set('last', self.hashtag)
            self.request('GET', self.context.settings.get('url'), self.got)

        def got(self, response):
            answers.append(response)
''')
PANEL_PLUGIN = '''
    from babble import plugin

    contexts = []
    calls = []


    class Menu(plugin.PostMenuExtension):
        def activate(self):
            contexts.append(self.context)
'''
write_plugin('panelplug', PANEL_PLUGIN)
write_plugin('panelplug2', PANEL_PLUGIN)
write_plugin('brokenplug', 'import does_not_exist_anywhere\n')
write_plugin('oldplug', 'raise SystemExit("must never be imported")\n',
             api_version='0')
write_plugin('noversion', 'x = 1\n', api_version=None)
write_plugin('emptyplug', 'x = 1\n')
write_plugin('preferencesplug', '''
    from gi.repository import Gtk
    from babble import plugin

    events = []


    class Preferences(plugin.PreferencesExtension):
        def activate(self):
            events.append('activate')

        def deactivate(self):
            events.append('deactivate')

        def create_preferences(self):
            events.append('create')
            return Gtk.Label(label='Preferences test')
''')
MENU_ONLY = '''
    from babble import plugin


    class Menu(plugin.PostMenuExtension):
        pass
'''
IMPORTED = 'import builtins\nbuiltins.REVISION_IMPORTED = True\n'
write_plugin('revfuture', IMPORTED,
             extra='X-Babble-Api-Revision=99\n')
write_plugin('revbad', IMPORTED,
             extra='X-Babble-Api-Revision=abc\n')
write_plugin('revabsent', MENU_ONLY)
write_plugin('revtwo', MENU_ONLY, extra='X-Babble-Api-Revision=2\n')
write_plugin('raiseplug', '''
    from babble import plugin


    class Menu(plugin.PostMenuExtension):
        def menu_items(self, post):
            raise RuntimeError('boom')
''')


class _ApiClient:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def request_async(self, *_args, **_kwargs):
        pass

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


STATUS = {
    'id': '42', 'url': 'https://example.org/@ana/42',
    'uri': 'https://example.org/users/ana/statuses/42',
    'content': '<p>Hello <a href="https://example.org/tags/SegundaFicha" '
               'class="mention hashtag">#<span>SegundaFicha</span></a></p>'
               '<p>second paragraph</p>',
    'account': {'id': '7', 'acct': 'ana', 'display_name': 'Ana'},
    'tags': [{'name': 'SegundaFicha'}], 'language': 'pt',
    'created_at': '2026-10-26T10:00:00.000Z', 'visibility': 'public',
    'media_attachments': [{'type': 'image', 'url': 'https://e.org/a.png',
                           'description': 'a cat'}],
}

settings = pages.settings()
host = pluginhost.host()

# Nothing was enabled: the app never starts the engine.
check('no plugin enabled leaves libpeas unstarted',
      pluginhost.start_if_enabled() is None and host.engine is None)
check('the menu asks nothing of an unstarted host',
      pluginhost.menu_items(None, STATUS, '') == [])

check('the host starts', host.start())
found = {info.get_module_name() for info in host.plugins()}
check('every plugin in the search path is found',
      {'goodplug', 'netplug', 'brokenplug', 'oldplug', 'emptyplug',
       'raiseplug', 'noversion'} <= found)

# Refusals.
host.set_enabled('oldplug', True)
check('a plugin for another API version is refused',
      'oldplug' not in host.contexts and 'oldplug' in host.errors)
check('and is never imported', 'oldplug' not in sys.modules)
host.set_enabled('noversion', True)
check('a plugin that names no API version is refused',
      'noversion' not in host.contexts and 'noversion' not in sys.modules)
host.set_enabled('revfuture', True)
check('a plugin needing a newer API revision is refused, never imported',
      'revfuture' not in host.contexts and 'revfuture' not in sys.modules
      and host.errors.get('revfuture') == 'Needs a newer version of Babble')
host.set_enabled('revbad', True)
check('a revision that is not an integer is refused, never imported',
      'revbad' not in host.contexts and 'revbad' not in sys.modules
      and host.errors.get('revbad') == 'Needs a newer version of Babble')
check('neither module was imported',
      not hasattr(builtins, 'REVISION_IMPORTED'))
host.set_enabled('revabsent', True)
check('no revision means 1, which loads',
      'revabsent' in host.contexts and 'revabsent' not in host.errors)
host.set_enabled('revtwo', True)
check('revision 2 loads', 'revtwo' in host.contexts)
check('the API revision is 2', api.API_REVISION == 2)
for plugin_id in ('revabsent', 'revtwo'):
    host.set_enabled(plugin_id, False)
host.set_enabled('brokenplug', True)
check('a plugin that fails to import is not loaded, and says why',
      'brokenplug' not in host.contexts
      and 'brokenplug' in host.errors)
host.set_enabled('emptyplug', True)
check('a plugin with no extensions is not kept loaded',
      'emptyplug' not in host.contexts
      and not host.engine.get_plugin_info('emptyplug').is_loaded())
for plugin_id in ('oldplug', 'noversion', 'brokenplug', 'emptyplug',
                  'revfuture', 'revbad'):
    host.set_enabled(plugin_id, False)

# A preferences-only plugin is a valid extension.
host.set_enabled('preferencesplug', True)
preferences = sys.modules['preferencesplug']
check('a preferences-only plugin is accepted',
      'preferencesplug' in host.contexts
      and 'preferencesplug' not in host.errors)
check('preferences-only extension is discovered',
      host.has_preferences('preferencesplug'))
settings_page = Gtk.Box()
settings_box = Gtk.Box()
host.attach_preferences('preferencesplug', settings_page, settings_box)
check('preferences widget is instantiated and attached',
      preferences.events == ['activate', 'create']
      and settings_box.get_first_child() is not None)
host.set_enabled('preferencesplug', False)
check('disabling preferences-only plugin deactivates it and removes UI',
      preferences.events == ['activate', 'create', 'deactivate']
      and settings_box.get_first_child() is None
      and 'preferencesplug' not in host.contexts)

# The post menu.
host.set_enabled('goodplug', True)
good = sys.modules['goodplug']
check('enabling loads the plugin and activates its menu extension',
      'goodplug' in host.contexts and good.events == ['menu activate'])
check('it is recorded in active-plugins',
      list(settings.get_strv('active-plugins')) == ['goodplug'])

button = statusmenu.StatusMenuButton()
button.bind(StatusItem(STATUS), client=_ApiClient())
button.prepare()
model = button.get_menu_model()
labels = []
for i in range(model.get_n_items()):
    section = model.get_item_link(i, 'section')
    for j in range(section.get_n_items() if section is not None else 0):
        labels.append(section.get_item_attribute_value(
            j, 'label', None))
labels = [label.get_string() for label in labels if label is not None]
check("the post menu carries the plugin's entry", 'Save to Test' in labels)
button.activate_action('plugin.item0', None)
saved = [e for e in good.events if isinstance(e, tuple)]
post = saved[0][1] if saved else None
check('activating it hands the plugin the post',
      post is not None and post.id == '42' and post.author == 'ana'
      and post.instance == 'https://example.org')
check('the post is plain text, its tags lower-case',
      post is not None and post.text.startswith('Hello #SegundaFicha')
      and 'second paragraph' in post.text and '<' not in post.text
      and post.tags == ('segundaficha',))
check('the post carries its media',
      post is not None and post.media == (
          api.Media('image', 'https://e.org/a.png', 'a cat'),))
button_ref = button.weak_ref()
del button, model
check('a menu button with plugin entries is freed', freed(button_ref))

# A hashtag page and its banner.
client = _ApiClient()
page = hashtags.HashtagPage(client, ['SegundaFicha'])
banner_box = page.plugin_banners
check('the page shows the banner', banner_box.get_visible()
      and banner_box.get_first_child() is not None)
check('the extension is told its tag, lower-case',
      'page activate segundaficha' in good.events)
banner = banner_box.get_first_child()
banner.button.emit('clicked')
check("a handler connected through the extension runs",
      'clicked' in good.events)
extension_ref = good.extensions[-1]
banner_ref = banner.weak_ref()
page_ref = page.weak_ref()
del banner, banner_box, page
check('a dropped hashtag page with a banner is freed', freed(page_ref))
check('its banner is freed', freed(banner_ref))
check('its extension is freed', freed(extension_ref))
check('and was deactivated',
      good.events.count('page deactivate segundaficha') == 1)

# Turning a plugin off takes its banner off an open page.
page = hashtags.HashtagPage(client, ['python'])
check('a second page gets its own banner',
      page.plugin_banners.get_first_child() is not None)
host.set_enabled('goodplug', False)
check('disabling removes the banner and hides the box',
      page.plugin_banners.get_first_child() is None
      and not page.plugin_banners.get_visible())
check('and deactivates both extensions',
      'page deactivate python' in good.events
      and 'menu deactivate' in good.events)
check('the menu has no plugin entries any more',
      pluginhost.menu_items(Gtk.Button(), STATUS, '') == [])
host.set_enabled('goodplug', True)
check('enabling again reuses the module and fills the open page',
      sys.modules['goodplug'] is good
      and page.plugin_banners.get_first_child() is not None)
page_ref = page.weak_ref()
del page
check('that page is freed too', freed(page_ref))

# A plugin that raises is turned off; the others carry on.
host.set_enabled('raiseplug', True)
entries = pluginhost.menu_items(Gtk.Button(), STATUS, '')
check("a raising plugin does not take the others' entries with it",
      [label for label, _run in entries] == ['Save to Test'])
spin(until=lambda: not host.is_enabled('raiseplug'), seconds=2)
check('a plugin that raised is turned off',
      not host.is_enabled('raiseplug') and 'raiseplug' not in host.contexts)
check('and says why', 'raiseplug' in host.errors)
check('the plugin beside it is still on', 'goodplug' in host.contexts)

# HTTP, and a request in flight when its owner goes.
host.set_enabled('netplug', True)
net = sys.modules['netplug']
host.contexts['netplug'].settings.set('url', SERVER)
page = hashtags.HashtagPage(client, ['answered'])
spin(until=lambda: net.answers, seconds=5)
check('a request reaches the plugin as parsed JSON',
      len(net.answers) == 1 and net.answers[0].ok
      and net.answers[0].data == {'tootCount': 12})
page_ref = page.weak_ref()
del page
check('the page with an answered request is freed', freed(page_ref))
page = hashtags.HashtagPage(client, ['dropped'])
page_ref = page.weak_ref()
del page
check('a page whose request is in flight is freed', freed(page_ref))
page = hashtags.HashtagPage(client, ['disabled'])
host.set_enabled('netplug', False)
spin(1.0)
check('no callback reaches an extension or plugin that went',
      len(net.answers) == 1)
del page
check('the settings are kept on disk', os.path.exists(os.path.join(
    scratch, 'config', 'babble', 'plugins', 'netplug.json')))
check('and read back', pluginhost._Settings('netplug').get('last')
      == 'disabled')

# The Plugins page.
window = Adw.Window(default_width=600, default_height=700)
window.set_content(Gtk.Label())
window.present()
dialog = pluginspage.present_plugins(window)
check('the Plugins page opens on its own', isinstance(
    window.get_visible_dialog(), pluginspage.PluginsDialog))
check('opening it again raises the same page',
      pluginspage.present_plugins(window) is dialog)
rows = {row.plugin_id: row for row in dialog.rows}
check('every plugin found has a row',
      {'goodplug', 'netplug', 'brokenplug', 'babble_wallabag'} <= set(rows))
check('plugins from elsewhere are not counted as shipped',
      not pluginspage.is_shipped(host.engine.get_plugin_info('goodplug')))
check('a plugin that would not load says why, with a warning',
      rows['brokenplug'].get_subtitle() == host.errors['brokenplug']
      and rows['brokenplug'].warning.get_visible())
check("an enabled plugin's switch is on",
      rows['goodplug'].switch.get_active())
check('a plugin with no settings has no settings button',
      not rows['goodplug'].settings_button.get_visible())
rows['goodplug'].switch.set_active(False)
check('turning its switch off disables the plugin',
      not host.is_enabled('goodplug') and 'goodplug' not in host.contexts)
host.set_enabled('goodplug', True)
check('and the row follows a change made elsewhere',
      rows['goodplug'].switch.get_active())
dialog.close()
spin(until=lambda: window.get_visible_dialog() is None, seconds=3)
dialog_ref = dialog.weak_ref()
del dialog, rows
check('the closed Plugins page is freed', freed(dialog_ref))
window.destroy()

# Plugin panels: the shared web column, loaded by a plugin. Needs WebKit.
from src import webcolumn  # noqa: E402

if webcolumn.available():
    from src import pluginpanel  # noqa: E402

    class _PanelPage(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<title>panel</title><p>hello'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    panel_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
                                                   _PanelPage)
    threading.Thread(target=panel_server.serve_forever, daemon=True).start()
    PANEL = 'http://127.0.0.1:%d/' % panel_server.server_address[1]

    class PanelHost(Gtk.Window):
        def __init__(self):
            super().__init__()
            self.navigation_view = object()  # what pages.window_of wants
            self.web_slot = Adw.Bin()
            self.web_split = Adw.OverlaySplitView(
                sidebar=self.web_slot, content=Gtk.Button(label='timeline'),
                show_sidebar=False, sidebar_position=Gtk.PackType.END,
                collapsed=False, min_sidebar_width=360,
                max_sidebar_width=720, sidebar_width_fraction=0.45,
                enable_show_gesture=False)
            self.set_default_size(1000, 600)
            self.set_child(self.web_split)
            webcolumn.watch_split(self.web_split)

    def loaded(column):
        return not column.view.is_loading() \
            and (column.view.get_uri() or '').startswith('http')

    panel_host = PanelHost()
    panel_host.present()
    spin(until=lambda: panel_host.get_mapped()
         and panel_host.get_width() > 0, seconds=5)
    settings.set_boolean('open-links-in-app', False)

    host.set_enabled('panelplug', True)
    host.set_enabled('panelplug2', True)
    first_context = sys.modules['panelplug'].contexts[-1]
    second_context = sys.modules['panelplug2'].contexts[-1]

    panel = first_context.open_panel(panel_host, PANEL, title='Fixture')
    column = panel_host.web_slot.get_child()
    check('open_panel shows the column although links open outside',
          column is not None and panel_host.web_split.get_show_sidebar()
          and not settings.get_boolean('open-links-in-app'))
    check('the panel is open on the URL, origin bound',
          panel.is_open and panel.uri == PANEL
          and panel.origin == PANEL.rstrip('/'))
    check('the column loads the panel URL',
          spin(until=lambda: loaded(column), seconds=10)
          and column.view.get_uri() == PANEL)
    check('the given title shows until the page has its own',
          column.title.get_title() in ('panel', 'Fixture'))

    results = []
    panel.run_script('1 + 1', lambda *a: results.append(a))
    check('run_script answers with plain data',
          spin(until=lambda: results, seconds=5) and results[0] == (2, None))

    # A second panel, from another plugin, ends the first handle.
    got = []
    panel.on_message(lambda data: got.append(data))
    other = second_context.open_panel(panel_host, PANEL)
    check('a second panel closes the first handle',
          not panel.is_open and other.is_open
          and panel_host.web_slot.get_child() is column
          and column.panel is other)
    stale = []
    panel.run_script('1', lambda *a: stale.append(a))
    panel.load_uri(PANEL + 'x')
    check('a closed handle does nothing', spin(0.3)
          and not stale and column.view.get_uri() != PANEL + 'x')

    # An ordinary link in the column ends the panel too.
    spin(until=lambda: loaded(column), seconds=10)
    webcolumn.present_web(panel_host, PANEL)
    check('an ordinary link closes the previous handle', not other.is_open
          and column.panel is None)

    # Script posted just before the handle closes never calls back.
    third = first_context.open_panel(panel_host, PANEL)
    spin(until=lambda: loaded(column), seconds=10)
    late = []
    third.on_message(lambda data: late.append(data))
    third.run_script('window.webkit.messageHandlers.babble.postMessage(1);'
                     ' 5', lambda *a: late.append(a))
    third.close()
    check('closing hides the column', not panel_host.web_split
          .get_show_sidebar() and webcolumn.live_column(panel_host) is None)
    check('a callback in flight when the handle closed is dropped',
          spin(0.5) and late == [])
    del column

    # Disabling the plugin closes its panel and hides the column.
    fourth = first_context.open_panel(panel_host, PANEL)
    column = panel_host.web_slot.get_child()
    column_ref = column.weak_ref()
    spin(until=lambda: loaded(column), seconds=10)
    pending = []
    fourth.run_script('9', lambda *a: pending.append(a))
    host.set_enabled('panelplug', False)
    check('disabling the plugin closes its panel and hides the column',
          not fourth.is_open and not panel_host.web_split.get_show_sidebar()
          and webcolumn.live_column(panel_host) is None)
    check('and nothing is called back afterwards',
          spin(0.5) and pending == [])
    panel_ref = weakref.ref(fourth)
    del fourth, column, third, panel, other, stale
    check("the panel's page is freed", freed(column_ref))
    check('and the handle', freed(panel_ref))

    # No column to show it in: the browser opens and the handle is shut.
    launched = []
    real_launch = pages.launch_external
    pages.launch_external = lambda _widget, uri: launched.append(uri)
    closed = second_context.open_panel(Gtk.Button(), PANEL)
    pages.launch_external = real_launch
    check('without a column the browser opens and the panel is closed',
          launched == [PANEL] and not closed.is_open)
    check('a URL that is not http(s) is refused',
          pluginpanel.origin_of('file:///etc/passwd') == ''
          and pluginpanel.origin_of('javascript:alert(1)') == '')
    host.set_enabled('panelplug2', False)
    panel_host.destroy()
    panel_server.shutdown()
else:
    print('SKIP - panel checks: WebKitGTK 6.0 is not available')

# Quitting deactivates what is left.
before = good.events.count('menu deactivate')
pluginhost.shutdown()
check('shutdown deactivates every extension',
      good.events.count('menu deactivate') == before + 1
      and not host.contexts)

server.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
