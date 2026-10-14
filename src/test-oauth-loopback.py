#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The login redirect comes back on a 127.0.0.1 listener.

A ``babble://`` redirect only reaches the app once the desktop maps the
scheme to it (``xdg-mime default``), so the login now listens on a
loopback port, registers that exact redirect and names it in the
authorization. This drives the real login code against a stub client
(no network) and talks to the listener over HTTP: the registration and
the authorize URL carry the loopback URI, only ``GET /callback`` with
the attempt's own ``state`` starts the one token exchange, and the
listener is gone once it has answered, cancelled or timed out. A port
that cannot be bound leaves the login on ``babble://``.

Exits 77 (skip) when no display is available."""

import json
import os
import sys
import time
import urllib.parse

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio, GLib, Gtk, Soup  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()

from src import auth as auth_module  # noqa: E402
from src.main import BabbleApplication  # noqa: E402, F401
from src.mastodon import (MastodonClient, OAUTH_REDIRECT,  # noqa: E402
                          OOB_REDIRECT)
from src.window import BabbleWindow  # noqa: E402

SCHEMA = 'io.github.mauriciobc.Babble'

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def pump(seconds=0.2):
    deadline = time.monotonic() + seconds
    ctx = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while ctx.iteration(False):
            pass
        time.sleep(0.005)


class StubClient(MastodonClient):
    """The real client minus the network: the registration and the token
    exchange answer from here, everything else never completes."""

    instances = []

    def __init__(self, instance):
        super().__init__(instance)
        self.forms = []
        self.exchanges = []
        StubClient.instances.append(self)

    def _request_async(self, method, path, params=None, form=None,
                       auth=True, callback=None, cancellable=None,
                       json_body=None):
        if path == '/apps':
            self.forms.append(form)
            data = {'client_id': 'client-id', 'client_secret': 'secret'}
        elif path.endswith('/oauth/token'):
            self.exchanges.append((form['code'], form['redirect_uri']))
            data = {'access_token': 'test-token'}
        else:
            return None
        task = Gio.Task.new(self, cancellable, callback)
        task.return_value(GLib.Variant('s', json.dumps(
            {'data': data, 'link': ''})))
        return task


def fresh_window():
    settings = Gio.Settings.new(SCHEMA)
    for key in ('accounts', 'active-account'):
        settings.reset(key)
    win = BabbleWindow(application=None)
    opened = []
    win.open_url = opened.append  # no browser
    return win, opened


def start_login(win):
    StubClient.instances.clear()
    win.instance_entry.set_text('example.social')
    win.on_login_clicked(None)
    pump()
    return StubClient.instances[-1] if StubClient.instances else None


session = Soup.Session()
session.set_proxy_resolver(None)


def get(url, method='GET'):
    """``(status, body, headers)`` of one request, or None when the
    connection is refused."""
    msg = Soup.Message.new(method, url)
    done = []

    def finished(source, result):
        try:
            done.append(source.send_and_read_finish(result).get_data())
        except GLib.Error:
            done.append(None)

    session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, None, finished)
    deadline = time.monotonic() + 5
    ctx = GLib.MainContext.default()
    while not done and time.monotonic() < deadline:
        ctx.iteration(True)
    if not done or done[0] is None:
        return None
    return msg.get_status(), done[0].decode('utf-8'), \
        msg.get_response_headers()


def closed(server):
    return server is not None and server.get_listeners() == []


Adw.init()
Gtk.init()
auth_module.MastodonClient = StubClient

# --- A login registers and names the loopback redirect -----------------

win, opened = fresh_window()
client = start_login(win)
server = win.auth.loopback
redirect = win.auth.loopback_uri
check('the login listens on loopback', server is not None and redirect)
port = urllib.parse.urlsplit(redirect or '').port
check('the redirect is http://127.0.0.1:<port>/callback',
      redirect == 'http://127.0.0.1:%s/callback' % port and port)
registered = client.forms[0]['redirect_uris'].split() if client.forms else []
check('the registration carries loopback, babble:// and out of band',
      registered == [redirect, OAUTH_REDIRECT, OOB_REDIRECT])
query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query) \
    if opened else {}
check('the authorize URL names the loopback redirect',
      query.get('redirect_uri') == [redirect])
check('a timeout is armed', win.auth.loopback_timeout > 0)
state = client._state
base = 'http://127.0.0.1:%d' % port

reply = get(base + '/elsewhere?code=x&state=' + state)
check('another path is 404', reply and reply[0] == 404)
reply = get(base + '/callback/deeper?code=x&state=' + state)
check('a path under /callback is 404', reply and reply[0] == 404)
reply = get(base + '/callback?code=x&state=' + state, 'POST')
check('a POST to /callback is refused', reply and reply[0] == 405)
check('no exchange from the wrong requests', client.exchanges == [])

reply = get(base + '/callback?code=x&state=someone-elses')
check('a foreign state is answered 400', reply and reply[0] == 400)
reply = get(base + '/callback?code=x')
check('a missing state is answered 400', reply and reply[0] == 400)
check('a bad state starts no exchange', client.exchanges == [])
check('a bad state keeps the listener', win.auth.loopback is server
      and not closed(server))

reply = get(base + '/callback?code=x&state=' + state)
check('the callback is answered 200', reply and reply[0] == 200)
check('the page is HTML that says to close the tab',
      reply and 'text/html' in (reply[2].get_content_type()[0] or '')
      and 'You can close this tab and return to Babble.' in reply[1]
      and '<script' not in reply[1] and 'src=' not in reply[1])
check('the page is not cached',
      reply and reply[2].get_one('Cache-Control') == 'no-store')
pump()
check('the callback exchanges the code once, on the loopback redirect',
      client.exchanges == [('x', redirect)])
check('the listener is dropped', win.auth.loopback is None
      and win.auth.loopback_timeout == 0)
check('the listener is closed after answering', closed(server))
reply = get(base + '/callback?code=x&state=' + state)
pump()
check('a second hit reaches nothing', reply is None)
check('a second hit starts no exchange', client.exchanges == [('x', redirect)])

# --- Cancel, leaving the page and the timeout close it -----------------

win.on_cancel_login_clicked(None)
client = start_login(win)
server = win.auth.loopback
win.on_cancel_login_clicked(None)
check('cancel closes the listener', win.auth.loopback is None and closed(server))

client = start_login(win)
server = win.auth.loopback
win.on_login_back_clicked(None)
check('leaving the login page closes the listener',
      win.auth.loopback is None and closed(server))

client = start_login(win)
server = win.auth.loopback
win.auth._on_loopback_expired()
check('the timeout closes the listener',
      win.auth.loopback is None and win.auth.loopback_timeout == 0 and closed(server))
win.on_cancel_login_clicked(None)

client = start_login(win)
server = win.auth.loopback
win.on_paste_code_clicked(None)
check('switching to a pasted code closes the listener',
      win.auth.loopback is None and closed(server))
query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[-1]).query)
check('a pasted code asks for the out of band redirect',
      query.get('redirect_uri') == [OOB_REDIRECT])
win.on_cancel_login_clicked(None)

# --- The moderation authorization registers its own loopback too --------

StubClient.instances.clear()
opened.clear()
outcomes = []
win.account = {'id': '42', 'instance': 'https://example.social',
               'acct': 'me', 'username': 'me'}
win.client = StubClient('https://example.social')
win.authorize_admin(outcomes.append)
pump()
admin = StubClient.instances[-1]
server = win.auth.loopback
registered = admin.forms[0]['redirect_uris'].split() if admin.forms else []
check('a moderation authorization registers its loopback redirect',
      server is not None and registered[:1] == [win.auth.loopback_uri]
      and 'admin:read' in admin.forms[0]['scopes'])
query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query) \
    if opened else {}
check('a moderation authorization names it',
      query.get('redirect_uri') == [win.auth.loopback_uri])
win.on_cancel_login_clicked(None)
check('a cancelled moderation authorization closes the listener',
      win.auth.loopback is None and closed(server) and outcomes == [False])
win.account = win.client = None

# --- No port: the login stays on babble:// ------------------------------

real_listen = Soup.Server.listen_local


def refuse(self, port, options):
    raise GLib.Error('no port for you')


Soup.Server.listen_local = refuse
try:
    opened.clear()
    client = start_login(win)
finally:
    Soup.Server.listen_local = real_listen
check('a refused bind leaves no listener', win.auth.loopback is None)
check('a refused bind registers only babble:// and out of band',
      client is not None and client.forms
      and client.forms[0]['redirect_uris'].split()
      == [OAUTH_REDIRECT, OOB_REDIRECT])
query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query) \
    if opened else {}
check('a refused bind authorizes on babble://',
      query.get('redirect_uri') == [OAUTH_REDIRECT])
win.on_oauth_callback(OAUTH_REDIRECT + '?code=y&state=' + client._state)
pump()
check('the babble:// callback still exchanges on babble://',
      client.exchanges == [('y', OAUTH_REDIRECT)])

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all oauth loopback checks passed')
