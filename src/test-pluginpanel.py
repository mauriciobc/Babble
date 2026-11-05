#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A plugin driving a web page in the column, modelled on Na Real: a
login form with one ``instance`` field that a Preact-style island reads
from an ``input`` event. Scripting must be bound to the origin the plugin
opened, run in a world the page cannot see, follow the JavaScript
setting, and stop at the OAuth detour to another origin.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import gc
import http.server
import os
import socket
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
from src import plugin as api  # noqa: E402

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
            if self.path == '/redir':
                self.send_response(302)
                self.send_header('Location', '/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
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

for module in ('realplug', 'otherplug'):
    os.makedirs(os.path.join(plugin_dir, module))
    with open(os.path.join(plugin_dir, module, module + '.plugin'),
              'w') as f:
        f.write('[Plugin]\nModule=%s\nLoader=python\nName=%s\n'
                'Version=1.0\nX-Babble-Api=1\n' % (module, module.title()))
    with open(os.path.join(plugin_dir, module, module + '.py'), 'w') as f:
        f.write(textwrap.dedent('''
            from babble import plugin

            contexts = []


            class Menu(plugin.PostMenuExtension):
                def activate(self):
                    contexts.append(self.context)
        '''))


class Host(Gtk.Window):
    def present_composer(self):
        """What BabbleWindow does: None without a session, else the
        composer whose ``view.prefill_text`` is what compose() calls."""
        if not self.session:
            return None
        composed = self.composed

        class View:
            def prefill_text(self, text):
                composed.append(text)
                return True

        class Dialog:
            view = View()
        return Dialog()

    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pages.window_of looks for
        self.session = True  # False: nobody signed in
        self.composed = []
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
result, error = run(panel, 'throw new Error("nope")')
check('a script that throws reports ERROR_SCRIPT, a str with the message',
      isinstance(error, str) and isinstance(error, api.PanelError)
      and error.code == api.ERROR_SCRIPT and 'nope' in error)

# The OAuth detour: another origin. A user's link, here done directly.
count = len(messages)
column.view.load_uri(OTHER_URL)
check('the page moved to another origin', settled(column, OTHER_URL))
check('the panel is still open, on its origin', panel.is_open
      and panel.origin == LOGIN_URL.rstrip('/') and panel.uri == OTHER_URL)
result, error = run(panel, '1')
check('run_script errors while the page is on another origin',
      result is None and error is not None)
check('the host refusal carries ERROR_WRONG_ORIGIN, and is a str',
      isinstance(error, str) and error.code == api.ERROR_WRONG_ORIGIN)
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
      result is None and error is not None and 'origin' in error)
check('and its error is ERROR_WRONG_ORIGIN, not the sentinel text',
      isinstance(error, str) and error.code == api.ERROR_WRONG_ORIGIN
      and 'babble:' not in error)
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
check('and its code is ERROR_JAVASCRIPT_OFF, on a str',
      isinstance(error, str) and error.code == api.ERROR_JAVASCRIPT_OFF)
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

# on_load: every finished full load, not a failed one, not after a close.
events = []
loads = []
column.view.connect('load-changed', lambda _v, e: events.append(e.value_nick))
panel.on_load(loads.append)
panel.load_uri(LOGIN_URL)
check('on_load fires once the page finished loading',
      spin(until=lambda: loads, seconds=10) and spin(0.3)
      and loads == [LOGIN_URL])
panel.load_uri(LOGIN_URL + 'redir')
check('on_load fires after a redirect, with where the page ended up',
      spin(until=lambda: len(loads) == 2, seconds=10)
      and loads[1] == LOGIN_URL)
panel.load_uri(LOGIN_URL + 'again')
check('and fires for every further load',
      spin(until=lambda: len(loads) == 3, seconds=10)
      and loads[2] == LOGIN_URL + 'again')

# Failure: a port nothing listens on.
probe = socket.socket()
probe.bind(('127.0.0.1', 0))
dead = 'http://127.0.0.1:%d/' % probe.getsockname()[1]
probe.close()
del events[:]
panel.load_uri(dead)
check('a load that failed shows the error page',
      spin(until=lambda: column.stack.get_visible_child_name() == 'error',
           seconds=10))
spin(1.0)
print('# events after the failed load: %s' % events)
check('on_load is not called for a load that failed',
      len(loads) == 3)
check('(WebKit does emit FINISHED after the failure: the flag is what '
      'stops the callback)', 'finished' in events)
panel.load_uri(LOGIN_URL)
check('on_load fires again for the next good load',
      spin(until=lambda: len(loads) == 4, seconds=10))

# on_load is not delivered once the handle is closed, nor after own close().
panel.close()
panel._on_loaded(LOGIN_URL)
check('a closed handle gets no on_load', spin(0.3) and len(loads) == 4)

# on_close.
def fresh(ctx, url=None, record=None):
    p = ctx.open_panel(window, url or LOGIN_URL)
    if record is not None:
        p.on_close(record.append)
    return p


other_ctx = None
host.set_enabled('otherplug', True)
import otherplug  # noqa: E402

other_ctx = otherplug.contexts[-1]

closes = []
p1 = fresh(context, record=closes)
check('on_close is not called while the panel is open',
      spin(0.2) and closes == [])
window.web_split.set_show_sidebar(False)  # the user dismisses the column
check('on_close is never synchronous', closes == [])
check("hiding the column reports 'hidden', once",
      spin(until=lambda: closes, seconds=5) and spin(0.3)
      and closes == ['hidden'])
check('and the handle is closed', not p1.is_open)

closes = []
p1 = fresh(context, record=closes)
window.web_slot.get_child().close_column()  # the header's close button
check("close_column reports 'hidden'",
      spin(until=lambda: closes, seconds=5) and closes == ['hidden'])

closes, theirs = [], []
p1 = fresh(context, record=closes)
p2 = fresh(other_ctx, record=theirs)
check("another plugin's panel replaces it: 'replaced'",
      spin(until=lambda: closes, seconds=5) and closes == ['replaced'])
check('and the new panel is not told anything', spin(0.3) and theirs == [])
webcolumn.present_web(window, OTHER_URL)  # an ordinary link in the column
check("an ordinary link in the column ends the panel: 'replaced'",
      spin(until=lambda: theirs, seconds=5) and theirs == ['replaced'])

closes = []
p1 = fresh(context, record=closes)
column_loading = window.web_slot.get_child()
p1.close()
# WebKitGTK crashes (SIGSEGV, about 1 run in 4) when a view is cancelled and
# unmapped before its first load has started, so the column keeps the view
# until the page settled and only then goes.
check('closing a column that is still loading defers its teardown',
      window.web_slot.get_child() is column_loading
      and column_loading is not None)
check("a plugin's own close() is not reported", spin(0.5) and closes == [])
check('and the column goes once the page settled',
      spin(until=lambda: window.web_slot.get_child() is None, seconds=8))

# A new load in the meantime takes the column back.
p1 = fresh(context)
column_loading = window.web_slot.get_child()
p1.close()
p2 = fresh(context)
check('a load while the teardown is deferred reuses the column',
      window.web_slot.get_child() is column_loading and p2.is_open)
check('and the column stays', spin(0.5)
      and window.web_slot.get_child() is column_loading and p2.is_open)
p2.close()
spin(until=lambda: window.web_slot.get_child() is None, seconds=8)

closes = []
p1 = fresh(context, record=closes)
p1._end('replaced')
p1._end('hidden')
check('on_close is called once, whatever ends the handle after',
      spin(until=lambda: closes, seconds=5) and spin(0.3)
      and closes == ['replaced'])

# Disabling the plugin is not reported either, nor is a report queued
# before it, once the plugin is off.
closes = []
host.set_enabled('otherplug', False)
host.set_enabled('otherplug', True)
other_ctx = otherplug.contexts[-1]
p3 = fresh(other_ctx, record=closes)
host.set_enabled('otherplug', False)
check('disabling the plugin closes its panel without on_close',
      not p3.is_open and spin(0.5) and closes == [])
host.set_enabled('otherplug', True)
other_ctx = otherplug.contexts[-1]
p3 = fresh(other_ctx, record=closes)
p4 = fresh(context)  # replaces p3: the report is queued...
host.set_enabled('otherplug', False)  # ...and the plugin goes before it runs
check('a report queued before the plugin was disabled is dropped',
      spin(0.5) and closes == [])

# Deferred: a plugin can open a panel from on_close.
reopened = []


def reopen(reason):
    reopened.append((reason, context.open_panel(window, LOGIN_URL)))


host.set_enabled('otherplug', True)
other_ctx = otherplug.contexts[-1]
p5 = fresh(context)
p5.on_close(reopen)
fresh(other_ctx)
check('open_panel from on_close works',
      spin(until=lambda: reopened, seconds=5)
      and reopened[0][0] == 'replaced' and reopened[0][1].is_open
      and window.web_slot.get_child().panel is reopened[0][1])
reopened[0][1].close()

# toast() and open_link(): anchored on the panel's own column, which the
# plugin never sees, and nothing once the handle is closed.
calls = []
real_pages = (pages.show_toast, pages.open_link)
pages.show_toast = lambda *a, **k: calls.append(('toast', a, k))
pages.open_link = lambda *a, **k: calls.append(('link', a, k))
LINK = 'https://example.org/@someone'
tp = fresh(context)
tcolumn = window.web_slot.get_child()
returned = [tp.toast('Hello'), tp.open_link(LINK)]
check('toast and open_link work while the panel is open, anchored on its '
      'column with the plain text and url',
      [c[0] for c in calls] == ['toast', 'link']
      and calls[0][1][1:] == ('Hello',)
      and calls[1][1][1:] == (LINK,) and calls[1][2] == {'resolve': True})
check("the anchor is the panel's own column, a widget inside the window",
      all(c[1][0] is tcolumn for c in calls)
      and isinstance(calls[0][1][0], Gtk.Widget)
      and calls[0][1][0].get_root() is window)
check('and the plugin gets nothing back: no widget reaches it',
      returned == [None, None]
      and not any(isinstance(getattr(tp, n), Gtk.Widget)
                  for n in ('uri', 'origin', 'is_open')))
del calls[:]
tp.close()
tp.toast('late')
tp.open_link(LINK)
check('both are no-ops after close()', calls == [])

p1 = fresh(context)
fresh(other_ctx)  # replaces p1
spin(0.2)
p1.toast('late')
p1.open_link(LINK)
check('both are no-ops once another panel replaced this one', calls == []
      and not p1.is_open)
window.web_slot.get_child().panel.close()

# The column is freed after the panel closes, though both were called.
freed = []
fp = fresh(context)
fcolumn = window.web_slot.get_child()
fcolumn.weak_ref(lambda: freed.append(True))
fp.toast('x')
fp.open_link(LINK)
fp.close()
del fcolumn
del calls[:]  # the stubs kept the column they were handed
for _ in range(5):
    gc.collect()
    spin(0.1)
check('the column is freed after the panel closes, though the plugin '
      'called toast and open_link', freed == [True])

gone_calls = []
window3 = Host()
window3.present()
spin(until=lambda w=window3: w.get_mapped(), seconds=5)
pw3 = context.open_panel(window3, LOGIN_URL)
pw3.on_close(lambda reason: None)
window3.destroy()
del window3
gc.collect()
spin(until=lambda: not pw3.is_open, seconds=5)
del calls[:]
pw3.toast('late')
pw3.open_link(LINK)
check('both are no-ops after the window was destroyed',
      calls == [] and not pw3.is_open)

# compose(): the composer opens in the panel's own window with the text,
# nothing is returned, and it does nothing once the handle is closed,
# replaced or its window gone, without a session, or for a non-str.
cp = fresh(context)
check('compose opens the composer in the panel\'s window with the text, '
      'returning nothing',
      cp.compose('Ask me anything') is None
      and window.composed == ['Ask me anything'])
cp.compose(42)
cp.compose(None)
cp.compose(b'bytes')
check('a non-str text does nothing', window.composed == ['Ask me anything'])
window.session = False
cp.compose('nobody home')
check('without a session it does nothing and raises nothing',
      window.composed == ['Ask me anything'])
window.session = True
cp.close()
cp.compose('late')
check('after close() compose does nothing', window.composed
      == ['Ask me anything'])
cp1 = fresh(context)
fresh(other_ctx)  # replaces cp1
spin(0.2)
cp1.compose('late')
check('once another panel replaced this one compose does nothing',
      window.composed == ['Ask me anything'] and not cp1.is_open)
window.web_slot.get_child().panel.close()
window4 = Host()
window4.present()
spin(until=lambda w=window4: w.get_mapped(), seconds=5)
cp4 = context.open_panel(window4, LOGIN_URL)
check('a live panel composes in its own window, not another',
      cp4.compose('four') is None and window4.composed == ['four']
      and window.composed == ['Ask me anything'])
window4.destroy()
del window4
gc.collect()
spin(until=lambda: not cp4.is_open, seconds=5)
cp4.compose('after destroy')
check('after the window was destroyed compose does nothing, no exception',
      not cp4.is_open)
pages.show_toast, pages.open_link = real_pages

# A window destroyed without dispose_column still ends the handle.
gone = []
window2 = Host()
window2.present()
spin(until=lambda w=window2: w.get_mapped(), seconds=5)
pw = context.open_panel(window2, LOGIN_URL)
pw.on_close(gone.append)
window2.destroy()
del window2
gc.collect()
check("destroying the window ends the handle with 'hidden'",
      spin(until=lambda: gone, seconds=5) and gone == ['hidden']
      and not pw.is_open)

# Disabling the plugin ends it.
panel = context.open_panel(window, LOGIN_URL)
settled(window.web_slot.get_child(), LOGIN_URL)
after = []
panel.run_script('1', lambda *a: after.append(a))
host.set_enabled('realplug', False)
check('disabling the plugin closes the panel',
      not panel.is_open and webcolumn.live_column(window) is None)
check('and nothing calls back afterwards', spin(0.5) and after == [])

window.destroy()
realreal.shutdown()
detour.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
