#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wallabag plugin against a fake Wallabag on loopback: signing in
with the password grant, saving a post's link, refreshing an expired or
revoked token, signing in again when the refresh is refused, and that
the password is never kept. The keyring is replaced with a dict: the
test environment has no secret service. Exits 77 (skip) without a
display, or without libpeas 2."""

import gc
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import weakref
from urllib.parse import parse_qs

scratch = tempfile.mkdtemp(prefix='babble-wallabag-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
os.environ.pop('BABBLE_PLUGIN_PATH', None)

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

from src import pluginhost  # noqa: E402

Adw.init()
failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def spin(until, seconds=5.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if until():
            return True
        time.sleep(0.01)
    return until()


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        gc.collect()
        spin(lambda: False, 0.05)
        if ref() is None:
            return True
    return ref() is None


# The fake server.

class Server:
    password = 'hunter2'
    tokens = 0
    grants = []  # each grant's form, as received
    entries = []  # each saved URL
    valid_access = set()
    valid_refresh = set()
    revoke_next_entry = False
    refuse_refresh = False


class _Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/oauth/v2/token':
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            Server.grants.append(form)
            ok_client = (form.get('client_id') == 'cid'
                         and form.get('client_secret') == 'secret')
            if form.get('grant_type') == 'password':
                ok = ok_client and form.get('username') == 'ana' \
                    and form.get('password') == Server.password
            elif form.get('grant_type') == 'refresh_token':
                ok = ok_client and not Server.refuse_refresh \
                    and form.get('refresh_token') in Server.valid_refresh
            else:
                ok = False
            if not ok:
                self.reply(400, {'error': 'invalid_grant',
                                 'error_description': 'Invalid credentials'})
                return
            Server.tokens += 1
            access, refresh = 'a%d' % Server.tokens, 'r%d' % Server.tokens
            Server.valid_access.add(access)
            Server.valid_refresh.discard(form.get('refresh_token'))
            Server.valid_refresh.add(refresh)
            self.reply(200, {'access_token': access, 'expires_in': 3600,
                             'refresh_token': refresh,
                             'token_type': 'bearer'})
        elif self.path == '/api/entries.json':
            token = self.headers.get('Authorization', '')[len('Bearer '):]
            if Server.revoke_next_entry:
                Server.revoke_next_entry = False
                Server.valid_access.discard(token)
            if token not in Server.valid_access:
                self.reply(401, {'error': 'invalid_grant'})
                return
            url = json.loads(body).get('url')
            Server.entries.append(url)
            self.reply(200, {'id': len(Server.entries), 'url': url})
        else:
            self.reply(404, {})

    def log_message(self, *_args):
        pass


httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
SERVER = 'http://127.0.0.1:%d' % httpd.server_address[1]


class _NotFound(http.server.BaseHTTPRequestHandler):
    """A web server with no Wallabag on it."""

    def do_POST(self):
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args):
        pass


nowhere = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _NotFound)
threading.Thread(target=nowhere.serve_forever, daemon=True).start()
NOWHERE = 'http://127.0.0.1:%d' % nowhere.server_address[1]

keyring = {}


def fake_keyring(context):
    def store(name, value, callback=None):
        keyring[name] = value
        if callback is not None:
            callback(None)

    def lookup(name, callback):
        callback(keyring.get(name), None)

    def clear(name, callback=None):
        keyring.pop(name, None)
        if callback is not None:
            callback(None)
    context.store_secret = store
    context.lookup_secret = lookup
    context.clear_secret = clear


host = pluginhost.host()
check('the host starts', host.start())
check('the Wallabag plugin is shipped and found',
      host.engine.get_plugin_info('babble_wallabag') is not None)
host.set_enabled('babble_wallabag', True)
check('it loads', 'babble_wallabag' in host.contexts)
module = sys.modules['babble_wallabag']
fake_keyring(host.contexts['babble_wallabag'])


def session():
    return module.session_of(host.contexts['babble_wallabag'])


STATUS = {'id': '1', 'url': 'https://example.org/@ana/1',
          'uri': 'https://example.org/users/ana/statuses/1',
          'content': '<p>read this</p>', 'account': {'acct': 'ana'}}

window = Adw.Window(default_width=600, default_height=600)
anchor = Gtk.Button(label='anchor')
window.set_content(anchor)
window.present()


def menu():
    return pluginhost.menu_items(anchor, STATUS, 'https://example.org')


def visible_dialog():
    return window.get_visible_dialog()


# normalize_server.
check('a bare host is taken as https',
      module.normalize_server(' wallabag.example/ ')
      == 'https://wallabag.example')
check('plain http to another machine is refused',
      module.normalize_server('http://wallabag.example') is None)
check("the hosted service's website means its app",
      module.normalize_server('wallabag.it') == 'https://app.wallabag.it'
      and module.normalize_server('https://www.wallabag.it/en')
      == 'https://app.wallabag.it')
check('plain http on this machine is allowed',
      module.normalize_server(SERVER) == SERVER)

# Not signed in: the entry asks to sign in first.
entries = menu()
check('signed out, the entry ends in an ellipsis',
      [label for label, _run in entries] == ['Save to _Wallabag…'])
entries[0][1]()
dialog = visible_dialog()
check('activating it opens the sign-in dialog',
      isinstance(dialog, module.SignInDialog))
check('Sign In is off until every field is filled',
      not dialog.sign_in_button.get_sensitive())
# A page address pasted from the browser, as a user would.
dialog.server.set_text(SERVER + '/unread/list')
dialog.client_id.set_text('cid')
dialog.client_secret.set_text('secret')
dialog.username.set_text('ana')
dialog.password.set_text('wrong')
check('and on once they are', dialog.sign_in_button.get_sensitive())
dialog.sign_in_button.emit('clicked')
spin(lambda: dialog.error.get_visible())
check('a refused password shows the server\'s reason in the dialog',
      dialog.error.get_visible()
      and dialog.error.get_label() == 'Invalid credentials'
      and visible_dialog() is dialog)
check('and keeps nothing', host.contexts['babble_wallabag'].settings.get(
    'server') is None and not keyring)

dialog.password.set_text(Server.password)
dialog.sign_in_button.emit('clicked')
spin(lambda: Server.entries)
check('signing in saves the post right away',
      Server.entries == ['https://example.org/@ana/1'])
check('the dialog closes', visible_dialog() is None)
check('a pasted page address signs in at the server\'s root',
      host.contexts['babble_wallabag'].settings.get('server') == SERVER)
check('the password grant carried the form fields',
      Server.grants[-1]['grant_type'] == 'password'
      and Server.grants[-1]['username'] == 'ana')
stored = json.loads(keyring.get(module.SECRET, '{}'))
check('the keyring holds the client secret and refresh token only',
      set(stored) == {'client_secret', 'refresh_token'})
settings_path = os.path.join(scratch, 'config', 'babble', 'plugins',
                             'babble_wallabag.json')
with open(settings_path, encoding='utf-8') as source:
    saved_settings = source.read()
check('the password is stored nowhere',
      Server.password not in saved_settings
      and Server.password not in json.dumps(keyring))
dialog_ref = dialog.weak_ref()
del dialog
check('the closed dialog is freed', freed(dialog_ref))

# Signed in.
check('signed in, the entry has no ellipsis',
      [label for label, _run in menu()] == ['Save to _Wallabag'])
grants = len(Server.grants)
menu()[0][1]()
spin(lambda: len(Server.entries) == 2)
check('a second save reuses the access token',
      len(Server.entries) == 2 and len(Server.grants) == grants)

session().expires_at = 0
menu()[0][1]()
spin(lambda: len(Server.entries) == 3)
check('an expired token is refreshed first',
      len(Server.entries) == 3
      and Server.grants[-1]['grant_type'] == 'refresh_token')
check('and the new refresh token is kept',
      json.loads(keyring[module.SECRET])['refresh_token']
      in Server.valid_refresh)

Server.revoke_next_entry = True
grants = len(Server.grants)
menu()[0][1]()
spin(lambda: len(Server.entries) == 4)
check('a revoked token is refreshed once and the save retried',
      len(Server.entries) == 4 and len(Server.grants) == grants + 1)

# A new session: nothing in memory, the keyring still has the tokens.
host.set_enabled('babble_wallabag', False)
host.set_enabled('babble_wallabag', True)
fake_keyring(host.contexts['babble_wallabag'])
check('after a restart the plugin holds no token',
      session().access_token is None)
menu()[0][1]()
spin(lambda: len(Server.entries) == 5)
check('it refreshes from the keyring and saves',
      len(Server.entries) == 5
      and Server.grants[-1]['grant_type'] == 'refresh_token')

# The refresh token is refused: sign in again.
Server.refuse_refresh = True
session().expires_at = 0
menu()[0][1]()
spin(lambda: visible_dialog() is not None)
dialog = visible_dialog()
check('a refused refresh asks to sign in again',
      isinstance(dialog, module.SignInDialog))
check('with the server, client and username filled in',
      dialog.server.get_text() == SERVER
      and dialog.client_id.get_text() == 'cid'
      and dialog.username.get_text() == 'ana'
      and dialog.password.get_text() == '')
dialog.server.set_text(NOWHERE)
dialog.client_secret.set_text('secret')
dialog.password.set_text(Server.password)
dialog.sign_in_button.emit('clicked')
spin(lambda: dialog.error.get_visible())
check('an address with no Wallabag says so',
      dialog.error.get_label() == 'No Wallabag server answered at this '
      'address')
dialog.server.set_text(SERVER)
Server.refuse_refresh = False
dialog.client_secret.set_text('secret')
dialog.password.set_text(Server.password)
dialog.sign_in_button.emit('clicked')
spin(lambda: len(Server.entries) == 6)
check('signing in again finishes the save that asked for it',
      len(Server.entries) == 6 and visible_dialog() is None)

# The plugin's settings, as the Plugins page opens them.
from src import pluginspage  # noqa: E402

plugins = pluginspage.present_plugins(window)
row = [r for r in plugins.rows if r.plugin_id == 'babble_wallabag'][0]
check('the Plugins page shows Wallabag on, with a settings button',
      row.switch.get_active() and row.settings_button.get_visible())
check('shipped with Babble', pluginspage.is_shipped(
    host.engine.get_plugin_info('babble_wallabag')))
row.settings_button.emit('clicked')
preferences = [ext for slot in host.slots if slot.plugin_id
               for ext, *_rest in slot.extensions.get('babble_wallabag', [])]
check('opening its settings creates the preferences extension',
      len(preferences) == 1)
account = preferences[0]
check('they name the signed-in account',
      account.row.get_subtitle() == 'ana on 127.0.0.1'
      and account.button.get_label() == 'Sign _Out')
account.button.emit('clicked')
check('Sign Out forgets the client and the tokens',
      not session().signed_in() and session().access_token is None
      and module.SECRET not in keyring)
check('and keeps the server and username for next time',
      host.contexts['babble_wallabag'].settings.get('server') == SERVER
      and host.contexts['babble_wallabag'].settings.get('username')
      == 'ana')
check('the settings now offer to sign in',
      account.row.get_subtitle() == 'Not signed in'
      and account.button.get_label() == '_Sign In…')
check('and the menu entry asks to sign in first',
      [label for label, _run in menu()] == ['Save to _Wallabag…'])
account_ref = weakref.ref(account)
del preferences, account
plugins.pop_subpage()
plugins.close()
plugins_ref = plugins.weak_ref()
del plugins, row
check('the closed Plugins page is freed', freed(plugins_ref))
check('and its settings extension with it', freed(account_ref))

host.shutdown()
window.destroy()
httpd.shutdown()
nowhere.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
