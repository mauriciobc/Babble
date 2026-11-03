#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account info and link claiming for plugins, end to end through
libpeas: ``Post.author_handle``, ``Context.account``, and a
``LinkExtension`` claiming links before Babble routes them (in both
``open_link`` and ``open_web``), honouring the user's choice of where
links open, never claiming an origin it did not name, and staying out of
the way of a click inside the plugin's own panel.

Exits 77 (skip) without a display or libpeas 2; the real-column checks
are skipped without WebKitGTK 6.0."""

import dataclasses
import gc
import http.server
import os
import sys
import tempfile
import textwrap
import threading
import time

scratch = tempfile.mkdtemp(prefix='babble-links-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
plugin_dir = os.path.join(scratch, 'plugins')
os.environ['BABBLE_PLUGIN_PATH'] = plugin_dir
os.environ.setdefault('WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS', '1')

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

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
# Ask without importing: a later check is that nothing imported libpeas.
if '2' not in (gi.Repository.get_default().enumerate_versions('Peas')
               or ()):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)

resource_path = os.path.join(builddir, 'src', 'babble.gresource')
if os.path.exists(resource_path):
    Gio.Resource.load(resource_path)._register()

from src import accounts, pages, pluginhost, pluginlinks, textemoji  # noqa: E402,E501
from src import plugin as api  # noqa: E402
from src import webcolumn  # noqa: E402

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


def write_plugin(module, body, extra=''):
    folder = os.path.join(plugin_dir, module)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, module + '.plugin'), 'w') as out:
        out.write('[Plugin]\nModule=%s\nLoader=python\nName=%s\n'
                  'Version=1.0\nX-Babble-Api=1\n' % (module, module.title()))
        out.write(extra)
    with open(os.path.join(folder, module + '.py'), 'w') as out:
        out.write(textwrap.dedent(body))


# A link-only plugin. Its origins come from its settings, read on every
# click; claims() refuses what contains "/private". Two copies share the
# source, so the first one loaded can be told from the second.
LINK_PLUGIN = '''
    from babble import plugin

    contexts = []
    opened = []
    asked = []


    class Links(plugin.LinkExtension):
        def activate(self):
            contexts.append(self.context)

        def origins(self):
            return self.context.settings.get('origins', [])

        def claims(self, url):
            asked.append(url)
            return '/private' not in url

        def open(self, url, widget):
            opened.append((url, widget))
'''
write_plugin('linkone', LINK_PLUGIN)
write_plugin('linktwo', LINK_PLUGIN)
write_plugin('linkboom', '''
    from babble import plugin

    contexts = []


    class Links(plugin.LinkExtension):
        def activate(self):
            contexts.append(self.context)

        def origins(self):
            return ['https://boom.example']

        def open(self, url, widget):
            raise RuntimeError('boom')


    class Menu(plugin.PostMenuExtension):
        pass
''')
write_plugin('linkloop', '''
    from babble import plugin

    contexts = []
    opened = []


    class Links(plugin.LinkExtension):
        def activate(self):
            contexts.append(self.context)

        def origins(self):
            return ['https://loop.example']

        def open(self, url, widget):
            opened.append(url)
            self.context.open_link(widget, url)
''')
write_plugin('accountplug', '''
    from babble import plugin

    contexts = []


    class Menu(plugin.PostMenuExtension):
        def activate(self):
            contexts.append(self.context)
''')
write_plugin('nothingplug', 'x = 1\n')

# Nothing enabled: libpeas is neither imported nor started.
settings = pages.settings()
settings.set_boolean('open-links-in-app', True)
settings.set_boolean('aggressive-resolving', False)
CLAIMED = 'https://claimed.example'
URL = CLAIMED + '/@user@host.tld'
check('no plugin enabled imports no libpeas',
      'gi.repository.Peas' not in sys.modules)
check('and claim_link says no without even creating the host',
      pluginlinks.claim_link(None, URL) is False
      and pluginhost._host is None)
check('is_claimed says no too', pluginlinks.is_claimed(URL) is False
      and pluginhost._host is None)
check('with no plugin enabled, start_if_enabled starts nothing',
      pluginhost.start_if_enabled() is None and pluginhost._host is None)
check('still no libpeas', 'gi.repository.Peas' not in sys.modules)
host = pluginhost.host()
check('a created host that never started claims nothing either',
      pluginlinks.claim_link(None, URL) is False
      and host.engine is None
      and 'gi.repository.Peas' not in sys.modules)

# Post.author_handle.
LOCAL = {'id': '1', 'account': {'acct': 'ana'}}
REMOTE = {'id': '2', 'account': {'acct': 'bob@other.social'}}
check('a local author gets the host of the post instance',
      pluginhost.post_of(LOCAL, 'https://mastodon.social').author_handle
      == '@ana@mastodon.social')
check('a remote author keeps their own server',
      pluginhost.post_of(REMOTE, 'https://mastodon.social').author_handle
      == '@bob@other.social')
check('and author stays the acct',
      pluginhost.post_of(LOCAL, 'https://mastodon.social').author == 'ana'
      and pluginhost.post_of(REMOTE, 'https://x.org').author
      == 'bob@other.social')
check('a post without an author has no handle',
      pluginhost.post_of({'id': '3'}, 'https://x.org').author_handle == '')

# A window with something to anchor to, and what routing would do.


class Host(Gtk.Window):
    def add_toast(self, toast):
        self.toasts.append(toast)

    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pages.window_of looks for
        self.client = object()
        self.toast_overlay = Adw.ToastOverlay()
        self.toasts = []
        self.web_slot = Adw.Bin()
        self.web_split = Adw.OverlaySplitView(
            sidebar=self.web_slot, content=Gtk.Button(label='timeline'),
            show_sidebar=False, sidebar_position=Gtk.PackType.END,
            collapsed=False, min_sidebar_width=360, max_sidebar_width=720,
            sidebar_width_fraction=0.45, enable_show_gesture=False)
        self.set_default_size(1000, 600)
        self.set_child(self.web_split)
        webcolumn.watch_split(self.web_split)


Adw.init()
window = Host()
window.present()
spin(until=lambda: window.get_mapped() and window.get_width() > 0,
     seconds=5)
anchor = window.web_split.get_content()

resolved, launched, shown = [], [], []
real = (pages.resolve_uri, textemoji.launch_uri, webcolumn.present_web,
        webcolumn.available)
pages.resolve_uri = lambda _w, _c, uri: resolved.append(uri)
textemoji.launch_uri = lambda _w, uri: launched.append(uri)
webcolumn.present_web = lambda _w, uri: shown.append(uri)
webcolumn.available = lambda: True


def reset():
    del resolved[:], launched[:], shown[:]


# The accounts.
check('the host starts', host.start())
host.set_enabled('accountplug', True)
check('the plugin loads', 'accountplug' in host.contexts)
import accountplug  # noqa: E402  (libpeas imported it)

ctx = accountplug.contexts[-1]
check('Context.account is None with nobody signed in', ctx.account is None)
accounts.save_account(settings, {
    'id': '987654', 'acct': 'me', 'username': 'me',
    'instance': 'https://mastodon.social', 'token': 'SECRETTOKEN'})
account = ctx.account
check('Context.account is the signed-in one: handle, instance and host',
      account == api.Account('@me@mastodon.social',
                             'https://mastodon.social', 'mastodon.social'))
check('it is an immutable dataclass of exactly those three',
      dataclasses.is_dataclass(account)
      and [f.name for f in dataclasses.fields(account)]
      == ['handle', 'instance', 'host'])
try:
    account.handle = 'x'
    frozen = False
except dataclasses.FrozenInstanceError:
    frozen = True
check('and cannot be changed', frozen)
dump = repr(account) + repr(vars(account)) + ' '.join(dir(account))
check('it carries neither the id nor the token',
      '987654' not in dump and 'SECRETTOKEN' not in dump
      and not hasattr(account, 'id') and not hasattr(account, 'token'))
accounts.save_account(settings, {
    'id': '5', 'acct': 'you@else.org', 'username': 'you',
    'instance': 'https://else.org'})
check('it follows an account switch, read on each access',
      ctx.account == api.Account('@you@else.org', 'https://else.org',
                                 'else.org'))
settings.set_string('active-account', '')
check('and is None again when none is active', ctx.account is None)
host.set_enabled('accountplug', False)

# Link claiming.
host.set_enabled('linkone', True)
import linkone  # noqa: E402

one = linkone.contexts[-1]
check('a plugin with only a link extension loads',
      'linkone' in host.contexts and 'linkone' not in host.errors
      and len(host.link_extensions['linkone']) == 1)
host.set_enabled('nothingplug', True)
check('a plugin with no extension at all still adds nothing',
      'nothingplug' not in host.contexts
      and host.errors.get('nothingplug') == 'Adds nothing Babble can use')
host.set_enabled('nothingplug', False)

one.settings.set('origins', [CLAIMED])
reset()
pages.open_link(anchor, URL, resolve=True)
check('a claimed /@user@host link goes to the plugin, with the widget',
      len(linkone.opened) == 1 and linkone.opened[0] == (URL, anchor))
check('and fediverse routing never ran', resolved == [] and launched == []
      and shown == [])

reset()
other = 'https://other.example/@user@host.tld'
pages.open_link(anchor, other, resolve=True)
check('an unclaimed profile link is routed as before',
      resolved == [other] and len(linkone.opened) == 1)
article = 'https://other.example/blog/post'
pages.open_link(anchor, article)
check('an unclaimed article still goes to the column',
      shown == [article] and len(linkone.opened) == 1)

reset()
pages.open_web(anchor, CLAIMED + '/article')
check('open_web, which explore.py calls directly, claims too',
      len(linkone.opened) == 2 and shown == [] and launched == [])
pages.open_web(anchor, article)
check('and open_web leaves an unclaimed link alone',
      shown == [article] and len(linkone.opened) == 2)

reset()
pages.launch_external(anchor, URL)
check('the explicit Open in Browser is never claimed',
      launched == [URL] and len(linkone.opened) == 2)

del linkone.asked[:]
pages.open_link(anchor, CLAIMED + '/private/x')
check('claims() can refuse a link on a claimed origin',
      len(linkone.opened) == 2 and linkone.asked
      and set(linkone.asked) == {CLAIMED + '/private/x'}
      and shown == [CLAIMED + '/private/x'])
reset()
del linkone.asked[:]
pages.open_link(anchor, 'https://nope.example/a')
pages.open_link(anchor, 'http://claimed.example/a')
check('an origin outside origins() is never claimed, claims() unasked',
      len(linkone.opened) == 2 and linkone.asked == [])

reset()
settings.set_boolean('open-links-in-app', False)
pages.open_link(anchor, URL)
pages.open_web(anchor, CLAIMED + '/article')
check('with open-links-in-app off nothing is claimed',
      len(linkone.opened) == 2 and launched
      == [URL, CLAIMED + '/article'])
check('but is_claimed still says the origin is a plugin\'s',
      pluginlinks.is_claimed(URL) is True)
settings.set_boolean('open-links-in-app', True)

one.settings.set('origins', [])
reset()
pages.open_link(anchor, URL)
check('origins() is read on each click: emptied, nothing is claimed',
      len(linkone.opened) == 2 and pluginlinks.is_claimed(URL) is False)
one.settings.set('origins', [CLAIMED.upper() + '/'])
pages.open_link(anchor, URL)
check('and an origin the plugin spells loosely still matches',
      len(linkone.opened) == 3)
one.settings.set('origins', [CLAIMED])

# First enabled wins.
host.set_enabled('linktwo', True)
import linktwo  # noqa: E402

linktwo.contexts[-1].settings.set('origins', [CLAIMED])
pages.open_link(anchor, URL)
check('the first enabled plugin to claim a link gets it',
      len(linkone.opened) == 4 and linktwo.opened == [])
one.settings.set('origins', [])
pages.open_link(anchor, URL)
check('and the next one gets what the first does not claim',
      len(linkone.opened) == 4 and len(linktwo.opened) == 1)
one.settings.set('origins', [CLAIMED])

# Disabling stops claiming.
host.set_enabled('linkone', False)
check('disabling a plugin deactivates and drops its link extension',
      'linkone' not in host.link_extensions)
pages.open_link(anchor, URL)
check('and its links go to the next claimant',
      len(linkone.opened) == 4 and len(linktwo.opened) == 2)
host.set_enabled('linktwo', False)
reset()
pages.open_link(anchor, URL, resolve=True)
check('with every claimant off the link is routed as before',
      len(linktwo.opened) == 2 and resolved == [URL])
check('and nothing claims it', pluginlinks.is_claimed(URL) is False)

# A plugin that raises is turned off; a plugin that hands the link back
# does not loop.
host.set_enabled('linkboom', True)

reset()
boom = 'https://boom.example/a'
pages.open_link(anchor, boom)
check('a claim whose open() raises does not crash, and the click still '
      'opens as it would have', shown == [boom])
spin(until=lambda: not host.is_enabled('linkboom'), seconds=3)
check('the plugin that raised is turned off, and says why',
      not host.is_enabled('linkboom') and 'linkboom' in host.errors
      and 'linkboom' not in host.contexts)
reset()
pages.open_link(anchor, boom)
check('and its links are routed normally afterwards', shown == [boom])

host.set_enabled('linkloop', True)
import linkloop  # noqa: E402

reset()
loop = 'https://loop.example/a'
pages.open_link(anchor, loop)
check('open_link from inside open() opens the ordinary way, no loop',
      linkloop.opened == [loop] and shown == [loop])
host.set_enabled('linkloop', False)

# Navigation inside the panel.
CTRL_LINK = CLAIMED + '/@user@host.tld'
check('classify: a claimed fediverse link click is not ROUTEd',
      webcolumn.classify_navigation(
          CTRL_LINK, link_clicked=True, resolve=True, claimed=True)
      == (webcolumn.LOAD, CTRL_LINK))
check('classify: the same click unclaimed still ROUTEs',
      webcolumn.classify_navigation(
          CTRL_LINK, link_clicked=True, resolve=True)
      == (webcolumn.ROUTE, CTRL_LINK))
check('classify: claimed does not unblock a middle click',
      webcolumn.classify_navigation(
          CTRL_LINK, link_clicked=True, button=2, claimed=True)[0]
      == webcolumn.EXTERNAL)

webcolumn.available = real[3]
if webcolumn.available():
    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b'<title>t</title><a id="a" href="/@user@host.tld">'
                    b'profile</a>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    def serve():
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Page)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, 'http://127.0.0.1:%d' % httpd.server_address[1]

    own, OWN = serve()
    foreign, FOREIGN = serve()
    settings.set_boolean('web-javascript', True)
    settings.set_boolean('aggressive-resolving', True)
    webcolumn.present_web = real[2]

    def click(column):
        column.view.evaluate_javascript(
            'document.getElementById("a").click()', -1, None, None, None,
            None)

    def settled(column, uri):
        return spin(until=lambda: not column.view.is_loading()
                    and column.view.get_uri() == uri, seconds=10)

    # Control: an unclaimed origin's profile link is routed, not loaded.
    reset()
    webcolumn.present_web(window, FOREIGN + '/')
    column = window.web_slot.get_child()
    settled(column, FOREIGN + '/')
    click(column)
    check('control: a click on an unclaimed profile link is ROUTEd',
          spin(until=lambda: resolved, seconds=10)
          and resolved == [FOREIGN + '/@user@host.tld']
          and column.view.get_uri() == FOREIGN + '/')

    # A claimed origin: the click stays in the column.
    host.set_enabled('linkone', True)
    one.settings.set('origins', [OWN])
    reset()
    webcolumn.present_web(window, OWN + '/')
    settled(column, OWN + '/')
    click(column)
    check('a click inside the column on a claimed origin stays a LOAD',
          settled(column, OWN + '/@user@host.tld') and resolved == [])
    host.set_enabled('linkone', False)
    own.shutdown()
    foreign.shutdown()
else:
    print('SKIP - real column checks: WebKitGTK 6.0 is not available')

(pages.resolve_uri, textemoji.launch_uri, webcolumn.present_web,
 webcolumn.available) = real
window.destroy()
gc.collect()
pluginhost.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
