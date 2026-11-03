#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A plugin driving a web page in the column, modelled on Na Real: a
login form with one ``instance`` field that a Preact-style island reads
from an ``input`` event. Scripting must be bound to the origin the plugin
opened, run in a world the page cannot see, follow the JavaScript
setting, and stop at the OAuth detour to another origin.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import http.server
import os
import sys
import tempfile
import textwrap
import threading
import time

scratch = tempfile.mkdtemp(prefix='babble-panel-')
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

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
try:
    gi.require_version('Peas', '2')
    from gi.repository import Peas  # noqa: E402, F401
except (ImportError, ValueError):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)

resource_path = os.path.join(builddir, 'src', 'babble.gresource')
if os.path.exists(resource_path):
    Gio.Resource.load(resource_path)._register()

from src import pages, pluginhost, webcolumn  # noqa: E402

if not webcolumn.available():
    print('SKIP - WebKitGTK 6.0 is not available')
    sys.exit(77)

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


# The fixture: Na Real's login form, in the shape that matters. The
# island reads the field on `input`; the page also leaves a variable and
# looks for a Babble channel in its own world.

LOGIN = b"""<!doctype html><title>Na Real</title>
<form method="post" action="/login"><input id="instance" name="instance">
<button>Log in</button></form>
<script>
window.pageVar = 1;
document.body.dataset.channel = String(!!(window.webkit
  && window.webkit.messageHandlers && window.webkit.messageHandlers.babble));
document.getElementById('instance').addEventListener('input', function (e) {
  document.body.dataset.heard = e.target.value;
});
</script>"""
OTHER = b'<!doctype html><title>Your instance</title><p>Authorize?'


def serve(body):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1]


realreal, LOGIN_URL = serve(LOGIN)
detour, OTHER_URL = serve(OTHER)

os.makedirs(os.path.join(plugin_dir, 'realplug'))
with open(os.path.join(plugin_dir, 'realplug', 'realplug.plugin'), 'w') as f:
    f.write('[Plugin]\nModule=realplug\nLoader=python\nName=Realplug\n'
            'Version=1.0\nX-Babble-Api=1\n')
with open(os.path.join(plugin_dir, 'realplug', 'realplug.py'), 'w') as f:
    f.write(textwrap.dedent('''
        from babble import plugin

        contexts = []


        class Menu(plugin.PostMenuExtension):
            def activate(self):
                contexts.append(self.context)
    '''))


class Host(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pages.window_of looks for
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

settings = pages.settings()
settings.set_boolean('open-links-in-app', False)
settings.set_boolean('web-javascript', True)
host = pluginhost.host()
check('the host starts', host.start())
host.set_enabled('realplug', True)
check('the plugin loads', 'realplug' in host.contexts)
import realplug  # noqa: E402  (libpeas imported it)

context = realplug.contexts[-1]


def run(panel, script):
    """Run *script*, wait for the callback: (result, error)."""
    out = []
    panel.run_script(script, lambda result, error: out.append(
        (result, error)))
    spin(until=lambda: out, seconds=10)
    return out[0] if out else ('no callback', None)


def settled(column, uri):
    return spin(until=lambda: not column.view.is_loading()
                and column.view.get_uri() == uri, seconds=10)


messages = []
panel = context.open_panel(window, LOGIN_URL, title='Na Real')
panel.on_message(messages.append)
column = window.web_slot.get_child()
check('the login page loads in the column', settled(column, LOGIN_URL))
check('scripting is bound to the opened origin',
      panel.origin == LOGIN_URL.rstrip('/'))

result, error = run(panel, '''
    var i = document.getElementById('instance');
    i.value = 'mastodon.social';
    i.dispatchEvent(new Event('input', {bubbles: true}));
    window.webkit.messageHandlers.babble.postMessage(
        {filled: i.value, n: [1, 2]});
    i.value
''')
check('run_script fills the field and returns its value as plain data',
      (result, error) == ('mastodon.social', None))
check('the message the script posted reaches the plugin',
      spin(until=lambda: messages, seconds=5)
      and messages == [{'filled': 'mastodon.social', 'n': [1, 2]}])
check("the page's own handler heard the input event",
      run(panel, 'document.body.dataset.heard')
      == ('mastodon.social', None))

# Isolation: the page never sees the channel, nor the plugin's globals.
check("the page's own script has no message channel",
      run(panel, 'document.body.dataset.channel') == ('false', None))
run(panel, 'window.pluginVar = 7')
check("the plugin's world keeps its state, and not the page's",
      run(panel, 'typeof window.pluginVar') == ('number', None)
      and run(panel, 'typeof window.pageVar') == ('undefined', None))
check('an object result comes back as JSON data',
      run(panel, '({a: [1, "x", null], b: true})')
      == ({'a': [1, 'x', None], 'b': True}, None))
check('a script error comes back as an error',
      run(panel, 'throw new Error("nope")')[1] is not None)

# The OAuth detour: another origin. A user's link, here done directly.
count = len(messages)
column.view.load_uri(OTHER_URL)
check('the page moved to another origin', settled(column, OTHER_URL))
check('the panel is still open, on its origin', panel.is_open
      and panel.origin == LOGIN_URL.rstrip('/') and panel.uri == OTHER_URL)
result, error = run(panel, '1')
check('run_script errors while the page is on another origin',
      result is None and error is not None)
panel._on_message(None)
check('messages are ignored on another origin', len(messages) == count)
column.view.load_uri(LOGIN_URL)
check('back on the origin', settled(column, LOGIN_URL))
check('scripting works again', run(panel, '2 * 3') == (6, None))
panel._on_message(None)
check('and messages arrive again', len(messages) == count + 1)

# The in-page guard. A navigation can land between the host's origin check
# and the script running, and that window cannot be hit reliably from a
# test. So take the host's check out of the way and put the page on
# another origin: only the guard inside the page is left to stop it.
column.view.load_uri(OTHER_URL)
settled(column, OTHER_URL)
panel._refusal = lambda _column: None  # the host believes it is fine
result, error = run(panel, 'document.body.dataset.ran = "yes"; 1')
del panel._refusal
check('the guard in the page refuses a script on another origin',
      result is None and error is not None and 'wrong origin' in error)
# Read the marker from a world of the test's own, which has no guard.
marker = []
column.evaluate('test-reader', 'String(document.body.dataset.ran)',
                lambda value, error: marker.append(
                    value.to_string() if value is not None else error))
spin(until=lambda: marker, seconds=10)
check('and the script never ran on that document', marker == ['undefined'])
column.view.load_uri(LOGIN_URL)
settled(column, LOGIN_URL)

# JavaScript off.
settings.set_boolean('web-javascript', False)
result, error = run(panel, '1')
check('with web-javascript off run_script reports an error',
      result is None and error is not None and 'JavaScript' in error)
settings.set_boolean('web-javascript', True)
check('and works again with it on', run(panel, '4') == (4, None))

# load_uri rebinds the origin.
panel.load_uri(OTHER_URL)
check('load_uri rebinds scripting to the new origin',
      panel.origin == OTHER_URL.rstrip('/') and settled(column, OTHER_URL)
      and run(panel, 'location.origin')
      == (OTHER_URL.rstrip('/'), None))
try:
    panel.load_uri('file:///etc/passwd')
    refused = False
except ValueError:
    refused = True
check('load_uri refuses a URL that is not http(s)', refused)

# Disabling the plugin ends it.
after = []
panel.run_script('1', lambda *a: after.append(a))
host.set_enabled('realplug', False)
check('disabling the plugin closes the panel',
      not panel.is_open and window.web_slot.get_child() is None)
check('and nothing calls back afterwards', spin(0.5) and after == [])

window.destroy()
realreal.shutdown()
detour.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
