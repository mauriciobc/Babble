#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""OAuth callback delivery is idempotent.

A scheme handler can be entered more than once for the same redirect: the
browser retries the navigation, a spawned ``babble %u`` races a D-Bus
Open, or the shell hands the URI over twice in one dispatch. Every
delivery used to start a token exchange, and since /oauth/token spends the
authorization code, the second one came back invalid_grant and toasted an
error on top of the login that had just succeeded.

Drives the same redirect twice and asserts one exchange and one toast per
auth attempt, plus the routes that could leak a second attempt: a stale
reply, a fresh login and a duplicated dispatch.

Exits 77 (skip) when no display is available."""

import json
import os
import subprocess
import sys
import time

# Own Xvfb + X11 backend: the window under test is a real one, so the
# session's WAYLAND_DISPLAY must not leak in.
_xvfb = None
if not os.environ.get('BABBLE_KEEP_DISPLAY'):
    for _n in range(81, 90):
        if os.path.exists('/tmp/.X11-unix/X%d' % _n):
            continue
        _xvfb = subprocess.Popen(
            ['Xvfb', ':%d' % _n, '-screen', '0', '1280x1024x24'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline \
                and not os.path.exists('/tmp/.X11-unix/X%d' % _n):
            time.sleep(0.05)
        if os.path.exists('/tmp/.X11-unix/X%d' % _n):
            os.environ['DISPLAY'] = ':%d' % _n
            break
        _xvfb.terminate()
        _xvfb = None
os.environ.setdefault('GDK_BACKEND', 'x11')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

# The window reads the account list on startup; keep the test off the
# user's real settings.
os.environ['GSETTINGS_SCHEMA_DIR'] = os.path.join(builddir, 'data')
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()

from src import emojichooser, textemoji  # noqa: E402
from src.main import BabbleApplication  # noqa: E402
from src.mastodon import (MastodonClient, OAUTH_REDIRECT,  # noqa: E402
                          OOB_REDIRECT)
from src.window import BabbleWindow  # noqa: E402

from gettext import gettext as _  # noqa: E402

STATE = 'state-for-this-attempt'
SCHEMA = 'io.github.mauriciobc.Babble'
REDIRECT_URI = OAUTH_REDIRECT + '?code=code-once&state=' + STATE
SECOND_URI = OAUTH_REDIRECT + '?code=code-two&state=' + STATE

# The stub answers with the real parse_callback_uri, so the state under
# test has to live on a real client.
_real_client = MastodonClient('https://example.social')
_real_client.client_id = 'client-id'
_real_client.client_secret = 'client-secret'

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def pump(seconds=0.3):
    """Iterate the default main context for about *seconds*."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass


def http_error(message):
    return GLib.Error.new_literal(
        GLib.quark_from_string('babble-test-http-error'), message, 400)


class StubTask:
    """A finished Gio.Task: the client only hands it to a *_finish call."""

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def propagate_value(self):
        if self.error is not None:
            raise self.error
        return (True, GLib.Variant('s', self.value))


class StubClient:
    """MastodonClient stand-in for the token exchange.

    An authorization code is good for exactly one exchange, so only the
    first one answers with a token and any further one comes back
    invalid_grant, like a real instance. Nothing here reaches the
    network; the pages the shell builds after a login get an unfinished
    task instead.
    """

    TOKEN = '{"access_token": "test-token"}'
    ACCOUNT = ('{"id": "42", "acct": "me@example.social",'
               ' "display_name": "Me"}')
    EMOJIS = ('[{"shortcode": "blobwave", "visible_in_picker": true,'
              ' "url": "https://example.social/custom/blobwave.png"},'
              ' {"shortcode": "hushpup", "visible_in_picker": false,'
              ' "url": "https://example.social/custom/hushpup.png"}]')

    def __init__(self, fail_token=False, fail_verify=False, deferred=False,
                 state=STATE):
        self.instance = 'https://example.social'
        self.client_id = 'client-id'
        self.client_secret = 'client-secret'
        self.token = None
        self.exchanges = []
        self.stored = None
        self.fail_token = fail_token
        self.fail_verify = fail_verify
        self.deferred = deferred
        self.pending = []
        # What get_authorize_url would have minted for this attempt.
        self._state = state
        _real_client._state = state

    def parse_callback_uri(self, uri):
        # The real implementation, not a copy of it: a stub mirroring
        # the production code cannot catch a defect in that code.
        return _real_client.parse_callback_uri(uri)

    def get_authorize_url(self, redirect_uri=None):
        return 'https://example.social/oauth/authorize?state=%s' % self._state

    # OAuth

    def request_token_async(self, code, callback=None, cancellable=None,
                            redirect_uri=None):
        self.exchanges.append((code, redirect_uri))
        spent = self.exchanges.count((code, redirect_uri)) > 1
        task = StubTask(error=http_error('invalid_grant')) \
            if self.fail_token or spent else StubTask(self.TOKEN)
        self.reply(callback, task)

    def request_token_finish(self, result):
        return self._finish(result)['access_token']

    def verify_credentials_async(self, callback=None, cancellable=None):
        task = StubTask(error=http_error('The access token is invalid')) \
            if self.fail_verify else StubTask(self.ACCOUNT)
        self.reply(callback, task)

    def verify_credentials_finish(self, result):
        return self._finish(result)

    def store_secret_token(self, account_id, token):
        self.stored = (account_id, token)

    # Pages the shell builds after a successful login.

    def timelines_home_async(self, params=None, callback=None,
                             cancellable=None):
        return StubTask('[]')

    def notifications_async(self, params=None, callback=None,
                            cancellable=None):
        return StubTask('[]')

    def notifications_unread_count_async(self, params=None, callback=None,
                                         cancellable=None):
        return StubTask('{"count": 0}')

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.reply(callback, StubTask(
            self.EMOJIS if path == '/custom_emojis' else '{}'))

    def request_finish(self, result):
        return self._finish(result)

    # Plumbing

    def reply(self, callback, task):
        """Deliver like Gio does -- on the main context -- or hold it."""
        if self.deferred:
            self.pending.append((callback, task))
            return
        GLib.idle_add(self._run, callback, task)

    def flush(self):
        while self.pending:
            callback, task = self.pending.pop(0)
            self._run(callback, task)

    def _run(self, callback, task):
        callback(self, task)
        return False

    @staticmethod
    def _finish(result):
        value = result.propagate_value()
        return json.loads(value[1].get_string())


def fresh_window():
    """A window with no stored account, no auth in flight and toasts
    recorded instead of shown."""
    settings = Gio.Settings.new(SCHEMA)
    for key in ('accounts', 'active-account'):
        settings.reset(key)
    win = BabbleWindow(application=None)
    toasts = []
    win.toast_overlay.add_toast = lambda toast: toasts.append(  # type: ignore
        toast.get_title())
    return win, toasts


def start_attempt(win, client):
    """What a click on the login button leaves behind: a client waiting
    for the redirect that answers it."""
    win._auth_client = client
    win.begin_auth_attempt()
    return client


Adw.init()
Gtk.init()

# --- The same redirect twice: one exchange, one toast -------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('delivered redirect exchanges the code once', len(client.exchanges) == 1)
check('delivered redirect redirects to the app scheme',
      client.exchanges and client.exchanges[0][1] == OAUTH_REDIRECT)
check('delivered redirect settles one toast', len(toasts) == 1)
check('the toast reports the success',
      toasts == [_('Logged in as @me@example.social')])
check('no error toast after a success',
      not any('Authorization failed' in text for text in toasts))
check('the account is signed in',
      win.main_stack.get_visible_child_name() == 'shell'
      and win.client is client)
check('the token reached the secret store',
      client.stored == ('42', 'test-token'))
check('signing in activates the instance emoji bank',
      textemoji.instance_emoji_bank() == {
          'blobwave': 'https://example.social/custom/blobwave.png',
          'hushpup': 'https://example.social/custom/hushpup.png'})
check('emoji hidden from the picker still resolve when rendered',
      textemoji.lookup_emoji({}, 'hushpup')
      == 'https://example.social/custom/hushpup.png')
check('the chooser only offers picker-visible emoji',
      [e['shortcode'] for e in emojichooser.cached_custom_emojis(client)]
      == ['blobwave'])
check('a shortcode no layer knows stays literal',
      textemoji.lookup_emoji({}, 'nosuchemoji') is None)

# A third delivery, once the first has settled, must stay silent too.
toasts.clear()
win.on_oauth_callback(REDIRECT_URI)
pump()
check('a late redelivery does nothing', client.exchanges
      and len(client.exchanges) == 1 and toasts == [])

# --- The same redirect twice, refused by the instance -------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(fail_token=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('refused redirect is exchanged once', len(client.exchanges) == 1)
check('refused redirect settles one toast', len(toasts) == 1)
check('the toast reports the failure',
      toasts == [(_('Authorization failed') + ': invalid_grant')])
check('no shell without an account',
      win.main_stack.get_visible_child_name() == 'login'
      and win.client is None)
toasts.clear()
win.on_oauth_callback(REDIRECT_URI)
pump()
check('the spent code is not offered again',
      len(client.exchanges) == 1 and toasts == [])

# --- A failed verification still settles exactly one toast --------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(fail_verify=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('refused verification is exchanged once', len(client.exchanges) == 1)
check('refused verification settles one toast',
      toasts == [(_('Could not verify the account') + ': '
                 + _('The access token is invalid'))])
check('no shell without a verified account',
      win.main_stack.get_visible_child_name() == 'login')

# --- A reply for a superseded attempt is dropped ------------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_cancel_login_clicked(None)
client.flush()
pump()
check('an abandoned attempt never signs the account back in',
      len(client.exchanges) == 1 and toasts == [] and win.client is None
      and win.main_stack.get_visible_child_name() == 'login')

# A second exchange for a superseded attempt cannot toast either.
win, toasts = fresh_window()
first = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.begin_auth_attempt()
first.flush()
pump()
check('a reply from a superseded attempt is silent',
      toasts == [] and win.client is None)

# --- A new attempt is not blocked by the spent code --------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
pump()
second = start_attempt(win, StubClient())
win.on_oauth_callback(SECOND_URI)
pump()
check('a second login with a new code works',
      len(second.exchanges) == 1
      and [code for code, _ in second.exchanges] == ['code-two'])
check('the second login settles one toast', len(toasts) == 2)

# --- The pasted fallback code is spent once too ------------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win._fallback = True
win.code_entry.set_text('pasted-code')
win.on_code_entered(win.code_entry)
win.on_code_entered(win.code_entry)
pump()
check('a pasted code is exchanged once', len(client.exchanges) == 1)
check('a pasted code answers the out of band redirect',
      client.exchanges[0][1] == OOB_REDIRECT)
check('a pasted code settles one toast', len(toasts) == 1)

# --- One attempt, one exchange: a second code waits for the first ------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(SECOND_URI)
check('a busy attempt takes no second code',
      [code for code, _ in client.exchanges] == ['code-once'])
client.flush()
pump()
check('the first code still settles the attempt', len(toasts) == 1
      and toasts == [_('Logged in as @me@example.social')])
win.on_oauth_callback(SECOND_URI)
client.flush()
pump()
check('the rejected code is not exchanged after the attempt',
      [code for code, _ in client.exchanges] == ['code-once']
      and len(toasts) == 1)

# --- A callback for another authorization is refused ------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(OAUTH_REDIRECT + '?code=stolen&state=someone-elses')
pump()
check('a callback whose state belongs elsewhere is not exchanged',
      client.exchanges == [] and toasts == [])

# A callback with the state stripped must be refused too: without the
# check, anything able to hand Babble a babble:// URL could redeem a
# code of its own choosing into the signed-in session.
win.on_oauth_callback(OAUTH_REDIRECT + '?code=stolen-no-state')
pump()
check('a callback with no state is not exchanged',
      client.exchanges == [] and toasts == [])

# The real attempt still works afterwards.
win.on_oauth_callback(REDIRECT_URI)
pump()
check('the matching state is still accepted',
      [code for code, _ in client.exchanges] == ['code-once']
      and len(toasts) == 1)

# --- The login field refuses a plaintext server -------------------------

win, toasts = fresh_window()
check('a bare host is accepted',
      win._instance_domain('mastodon.social') == 'mastodon.social')
check('an https address is accepted',
      win._instance_domain('https://fosstodon.org/') == 'fosstodon.org')
check('a plaintext address is refused',
      win._instance_domain('http://plain.example') == ''
      and win._instance_domain('HTTP://Plain.Example') == '')

win.instance_entry.set_text('http://plain.example')
win.on_login_clicked(None)
pump()
check('a plaintext login never starts an attempt',
      win._auth_client is None
      and win.main_stack.get_visible_child_name() == 'login')

# --- The application delivers a duplicated dispatch once ---------------

app = BabbleApplication('0.1.0')
# Windows may only join the application after it has started up.
app.register(None)
app.emit('startup')
uri = Gio.File.new_for_uri(REDIRECT_URI)
app.on_open(None, [uri], 1, None)
win = app.props.active_window
check('the redirect opened the window', isinstance(win, BabbleWindow))

delivered = []
win.on_oauth_callback = delivered.append
app.on_open(None, [uri, uri], 2, None)
check('a duplicated dispatch is delivered once',
      delivered == [REDIRECT_URI])
check('a later dispatch reuses that window',
      app.props.active_window is win)

# --- A stale keyring reply must not touch the session -------------------

# Reading the token is an out-of-process round trip, so a switch can be
# started before an earlier restore answers. The late reply used to win,
# showing the account the user had already switched away from.


class DeferredTokenClient(StubClient):
    """Holds the token lookup back so the test can answer it late.

    Everything else comes from StubClient, because answering a lookup
    builds the shell, and the shell asks the client for pages.
    """

    created = []

    def __init__(self, instance, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.pending = []
        DeferredTokenClient.created.append(self)

    def load_secret_token(self, account_id, callback, cancellable=None):
        self.pending.append(callback)

    def answer(self, token):
        self.pending.pop(0)(token)


win, toasts = fresh_window()
import src.window as window_module  # noqa: E402
_real_client_class = window_module.MastodonClient
window_module.MastodonClient = DeferredTokenClient
try:
    # Which account ids the window decided to forget, if any.
    forgotten = []
    win.remove_account = lambda aid: forgotten.append(aid)
    win._activate_account(
        {'id': '1', 'instance': 'https://a.example', 'acct': 'first'},
        login_only=True)
    win._activate_account(
        {'id': '2', 'instance': 'https://b.example', 'acct': 'second'})
    check('two keyring lookups are in flight',
          len(DeferredTokenClient.created) == 2)
    # The newer switch answers first; the older restore lands late.
    DeferredTokenClient.created[1].answer('token-b')
    pump()
    check('the newest lookup wins',
          win.account is not None and win.account['id'] == '2')
    DeferredTokenClient.created[0].answer('token-a')
    pump()
    check('a stale keyring reply does not change the account',
          win.account is not None and win.account['id'] == '2')
    check('a stale keyring reply does not replace the client',
          getattr(win.client, 'instance', None) == 'https://b.example')

    # A stale reply carrying no token must not report a missing login,
    # forget the account, or drop a signed-in window back to the login
    # page. This one is a restore, which is the case that used to do all
    # three.
    win._activate_account(
        {'id': '3', 'instance': 'https://c.example', 'acct': 'third'},
        login_only=True)
    win._activate_account(
        {'id': '4', 'instance': 'https://d.example', 'acct': 'fourth'})
    DeferredTokenClient.created[3].answer('token-d')
    pump()
    toast_count = len(toasts)
    DeferredTokenClient.created[2].answer(None)
    pump()
    check('a stale empty reply does not report a missing login',
          toasts[toast_count:] == [])
    check('a stale empty reply keeps the signed-in account',
          win.account is not None and win.account['id'] == '4')
    check('a stale empty reply keeps the shell visible',
          win.main_stack.get_visible_child_name() == 'shell')
    check('a stale empty reply does not forget the account',
          forgotten == [])
finally:
    window_module.MastodonClient = _real_client_class

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all oauth callback checks passed')
