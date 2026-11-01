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
import types

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

from src import emojichooser, pages, textemoji  # noqa: E402
from src.main import BabbleApplication  # noqa: E402
from src.mastodon import (ADMIN_SCOPES, MastodonClient,  # noqa: E402
                          OAUTH_REDIRECT, OOB_REDIRECT)
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

    def mastodon_api_version(self):
        """What the stand-in answers about api_versions: nothing."""
        return None

    def is_supported(self, min_version, when_unknown=False):
        """The capability policy a real instance would also honour."""
        return when_unknown
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

    def get_authorize_url(self, redirect_uri=None, scopes=None):
        self.scopes = scopes
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
        return MastodonClient.request_token_finish(self, result)

    def verify_credentials_async(self, callback=None, cancellable=None):
        task = StubTask(error=http_error('The access token is invalid')) \
            if self.fail_verify else StubTask(self.ACCOUNT)
        self.reply(callback, task)

    def verify_credentials_finish(self, result):
        return MastodonClient.verify_credentials_finish(self, result)

    def store_secret_token(self, account_id, token, callback=None,
                           cancellable=None):
        self.stored = (account_id, token)
        # The real client reports a keyring that refused the secret
        # instead of swallowing it; the login only commits once this
        # answers.
        if callback is not None:
            callback(None)

    def store_client_credentials(self, account_id, client_id, client_secret,
                                 callback=None, cancellable=None):
        # Keyed by account: each authorization registers its own app, so
        # two accounts on one instance hold different credentials.
        self.client_credentials = (account_id, client_id, client_secret)
        if callback is not None:
            callback(None)

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

    def markers_async(self, timelines, callback=None, cancellable=None):
        self.reply(callback, StubTask('{}'))

    def markers_finish(self, result):
        return self._finish(result)

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
    win.auth._client = client
    win.auth.begin()
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

for field, payload in (('TOKEN', '{}'), ('TOKEN', '[]'), ('TOKEN', 'null'),
                       ('TOKEN', '{"access_token": 7}'),
                       ('ACCOUNT', '{}'), ('ACCOUNT', '{"id": 7}')):
    win, toasts = fresh_window()
    client = start_attempt(win, StubClient())
    setattr(client, field, payload)
    win.auth.on_oauth_callback(REDIRECT_URI)
    pump()
    check('invalid OAuth %s %s settles with no keyring persistence' % (field, payload),
          not win.auth.waiting and client.stored is None and len(toasts) == 1
          and win.account is None)

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
win.auth.begin()
first.flush()
pump()
check('a reply from a superseded attempt is silent',
      toasts == [] and win.client is None)

# State-validated denial settles once without spending a token code.
for error_code, message in (('access_denied', 'Authorization was denied.'),
                            ('server_error',
                             'The server could not authorize this request.')):
    win, toasts = fresh_window()
    client = start_attempt(win, StubClient())
    uri = OAUTH_REDIRECT + '?error=' + error_code + '&state=' + STATE
    check('scheme denial %s is accepted' % error_code,
          win.auth.on_oauth_callback(uri))
    win.auth.on_oauth_callback(uri)
    check('scheme denial %s settles once with no exchange' % error_code,
          not win.auth.waiting and client.exchanges == []
          and toasts == ['Authorization failed: ' + message])

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
for query in ('error=access_denied&state=wrong',
              'error=access_denied&state=' + STATE + '&state=' + STATE,
              'error=access_denied&error=access_denied&state=' + STATE,
              'code=c&error=access_denied&state=' + STATE,
              'code=c&code=d&state=' + STATE):
    check('malformed or foreign OAuth answer is ignored: ' + query,
          not win.auth.on_oauth_callback(OAUTH_REDIRECT + '?' + query)
          and win.auth.waiting and client.exchanges == [])
win.auth.on_oauth_callback(REDIRECT_URI)
check('denial cannot override a pending code exchange',
      not win.auth.on_oauth_callback(
          OAUTH_REDIRECT + '?error=access_denied&state=' + STATE)
      and win.auth.waiting and len(client.exchanges) == 1 and toasts == [])
client.flush()
pump()

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
win.auth._fallback = True
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

# Only the registered redirect is read, even with the right state.
for wrong in ('babble://elsewhere/callback', 'babble://oauth/other',
              'https://oauth/callback'):
    win.on_oauth_callback(wrong + '?code=stolen&state=' + STATE)
pump()
check('a callback to another host or path is not exchanged',
      client.exchanges == [] and toasts == [])

# A malformed authority (urlsplit raises on an unmatched '[') is
# refused, not raised out of the open handler.
win.on_oauth_callback('babble://[oauth/callback?code=stolen&state=' + STATE)
pump()
check('a malformed callback is refused without raising',
      client.exchanges == [] and toasts == [])

# The host of a URI is case-insensitive; the registered redirect still
# matches when a handler hands it back in another case.
check('the redirect host matches in any case',
      _real_client.parse_callback_uri(
          'babble://OAuth/callback?code=c&state=' + STATE) == 'c')

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
      win.auth.client is None
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

# --- Open With: local files go to the composer, web URIs nowhere --------

composed = []
win.compose_with_files = lambda paths=(): composed.append(list(paths))
delivered.clear()
png = Gio.File.new_for_uri('file:///tmp/babble-open-with/picture.png')
app.on_open(None, [png], 1, None)
check('an opened image is handed to the composer',
      composed == [['/tmp/babble-open-with/picture.png']] and delivered == [])
composed.clear()
app.on_open(None, [Gio.File.new_for_uri('http://example.com/x.png')], 1, None)
check('a web URI handed to open is ignored',
      composed == [] and delivered == [])
app.on_open(None, [uri, png], 2, None)
check('a redirect next to a file still reaches the OAuth handler',
      delivered == [REDIRECT_URI]
      and composed == [['/tmp/babble-open-with/picture.png']])

# --- The jump-list and routing actions ---------------------------------

check('the application exports the desktop actions',
      all(app.lookup_action(name) is not None
          for name in ('compose', 'show-notifications', 'open-uri',
                       'open-notifications')))
composed.clear()
# Activated on the action itself: through the application, a Babble
# already running on the session bus would be the one to receive it.
app.lookup_action('compose').activate(None)
check('app.compose opens the composer', composed == [[]])
routed = []
real_open_link = pages.open_link
pages.open_link = lambda widget, link: routed.append((widget, link))
try:
    app.lookup_action('open-uri').activate(
        GLib.Variant('s', 'https://example.social/@me/1'))
finally:
    pages.open_link = real_open_link
check('app.open-uri routes the link through the window',
      routed == [(win, 'https://example.social/@me/1')])

# --- web+ap and web+mastodon links open inside the window --------------

import src.main as main_module  # noqa: E402


class SearchClient:
    """A session whose /api/v2/search answers *answer*, recording each
    query it is asked."""

    def __init__(self, answer):
        self.answer = answer
        self.searches = []

    def request_async(self, method, path, params=None, callback=None, **_kw):
        self.searches.append((method, path, dict(params or {})))
        callback(self, StubTask(json.dumps(self.answer)))

    def request_finish(self, result):
        return json.loads(result.propagate_value()[1].get_string())


class PrefillView:
    def __init__(self):
        self.texts = []

    def prefill_text(self, text):
        self.texts.append(text)


parse = main_module.parse_web_uri
check('web+ap names the object at its https address',
      parse('web+ap://mastodon.social/@Gargron/1')
      == ('resolve', 'https://mastodon.social/@Gargron/1'))
check('the web+ap scheme and host match in any case, the query is kept',
      parse('WEB+AP://Mastodon.Social/@Gargron/1?x=1#top')
      == ('resolve', 'https://mastodon.social/@Gargron/1?x=1'))
check('a follow intent names the account',
      parse('web+mastodon://follow?uri=acct:a@b.c') == ('account', 'a@b.c'))
check('a follow intent may name a profile by its https address',
      parse('web+mastodon://follow?uri=https://b.c/@a')
      == ('resolve', 'https://b.c/@a'))
check('a share intent carries its text',
      parse('web+mastodon://share?text=hi%20there')
      == ('share', 'hi there'))
malformed = ('web+ap:///@Gargron/1', 'web+ap:@Gargron/1',
             'web+ap://user@evil.example/@a/1', 'web+ap://[bad/@a',
             'web+ap://javascript:alert(1)', 'web+ap:javascript:alert(1)',
             # What GVfs makes of the line above.
             'web+ap://javascript:0/', 'web+ap://localhost/@a/1',
             'web+ap://evil.example:99999/@a',
             'web+mastodon://follow?uri=javascript:alert(1)',
             'web+mastodon://follow?uri=http://plain.example/@a',
             'web+mastodon://follow?uri=https://u@evil.example/@a',
             'web+mastodon://follow', 'web+mastodon://evil?uri=acct:a@b.c',
             'web+mastodon:share?text=hi', 'https://mastodon.social/@a/1')
check('malformed links name nothing',
      [u for u in malformed if parse(u) is not None] == [])

launched_links = []
real_launch_external = pages.launch_external
pages.launch_external = lambda widget, link: launched_links.append(link)
real_open_link, real_open_profile = pages.open_link, pages.open_profile
linked, profiles = [], []
pages.open_link = lambda w, link, resolve=False: linked.append(
    (w, link, resolve))
pages.open_profile = lambda w, c, account=None, acct=None: profiles.append(
    (w, c, account, acct))
app_toasts = []
win.show_toast = app_toasts.append
win.account = {'id': '42', 'acct': 'me', 'instance': 'https://example.social'}
win.client = SearchClient({})
try:
    app.on_open(None, [Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1')], 1, None)
    check('web+ap reaches the resolver with the https address',
          linked == [(win, 'https://mastodon.social/@Gargron/1', True)])
    linked.clear()
    app.on_open(None, [Gio.File.new_for_uri(
        'web+mastodon://follow?uri=acct:a@b.c')], 1, None)
    check('a follow intent resolves the account',
          profiles == [(win, win.client, None, 'a@b.c')] and linked == [])
    profiles.clear()
    app.on_open(None, [Gio.File.new_for_uri(
        'web+mastodon://follow?uri=https://b.c/@a')], 1, None)
    check('a follow intent by address resolves it',
          linked == [(win, 'https://b.c/@a', True)])
    linked.clear()
    delivered.clear()
    composed.clear()
    app.on_open(None, [Gio.File.new_for_uri(u) for u in malformed], 1, None)
    check('malformed links are ignored',
          linked == [] and profiles == [] and app_toasts == []
          and delivered == [] and composed == [] and launched_links == [])
    app.on_open(None, [uri, Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1'), png], 3, None)
    check('babble:// and file:// still work next to a web+ap link',
          delivered == [REDIRECT_URI]
          and composed == [['/tmp/babble-open-with/picture.png']]
          and len(linked) == 1)
finally:
    pages.open_link, pages.open_profile = real_open_link, real_open_profile

prefill = PrefillView()
win.present_composer = lambda: types.SimpleNamespace(view=prefill)
app.on_open(None, [Gio.File.new_for_uri('web+mastodon://share?text=hi')],
            1, None)
check('a share intent opens the composer with its text',
      prefill.texts == ['hi'])

# End to end: a web+ap post is looked up on the instance even with
# aggressive resolving off, and opened in the app.
opened = []
real_open_status = pages.open_status
pages.open_status = lambda w, c, status: opened.append(status)
resolving = pages.settings().get_boolean('aggressive-resolving')
pages.settings().set_boolean('aggressive-resolving', False)
try:
    win.client = SearchClient({'statuses': [{'id': '1'}]})
    app.on_open(None, [Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1')], 1, None)
    check('a web+ap link is searched for with resolve',
          [s[2] for s in win.client.searches]
          == [{'q': 'https://mastodon.social/@Gargron/1', 'resolve': 'true',
               'limit': 1}])
    check('the found post is opened in the app',
          opened == [{'id': '1'}] and launched_links == [])
finally:
    pages.open_status = real_open_status
    pages.settings().set_boolean('aggressive-resolving', resolving)
    pages.launch_external = real_launch_external

# Before a session exists the link waits, and is opened once signed in.
app_toasts.clear()
prefill.texts.clear()
win.client = win.account = None
win.main_stack.set_visible_child_name('login')
app.on_open(None, [Gio.File.new_for_uri('web+mastodon://share?text=hi')],
            1, None)
check('a link opened while signed out waits for a session',
      prefill.texts == [] and app_toasts == [_('Sign in to open this link')])
win.client = SearchClient({})
win.account = {'id': '42', 'acct': 'me', 'instance': 'https://example.social'}
win.main_stack.set_visible_child_name('shell')
check('signing in opens the waiting link', prefill.texts == ['hi'])
win.main_stack.set_visible_child_name('login')
win.main_stack.set_visible_child_name('shell')
check('the waiting link is opened once', prefill.texts == ['hi'])
win.client = win.account = None
win.main_stack.set_visible_child_name('login')

# --- Files opened before signing in wait for the session ----------------


class FakeComposeView:
    """Records what reaches the composer's intake; its filtering and
    limit are the compose test's to check."""

    def __init__(self):
        self.batches = []

    def add_attachment_files(self, files):
        self.batches.append(list(files))
        return []


win, toasts = fresh_window()
fake_view = FakeComposeView()
presented = []
win.present_composer = lambda: (presented.append(True)
                                or types.SimpleNamespace(view=fake_view))
win.compose_with_files(['/tmp/a.png', '/tmp/b.png'])
win.compose_with_files(['/tmp/b.png', '/tmp/c.mp4'])
check('files opened while signed out wait for a session',
      presented == [] and fake_view.batches == []
      and toasts == [_('Sign in to post the files')] * 2)
toasts.clear()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
pump()
check('signing in hands the waiting files to the composer, once each',
      presented == [True]
      and fake_view.batches == [['/tmp/a.png', '/tmp/b.png',
                                  '/tmp/c.mp4']])
presented.clear()
win.show_shell()
check('the waiting files are handed over once', presented == [])

# --- A stale keyring reply must not touch the session -------------------

# Reading the token is an out-of-process round trip, so a switch can be
# started before an earlier restore answers. The late reply used to win,
# showing the account the user had already switched away from.


class AdminScopeClient(StubClient):
    """A client the window builds for the moderation authorization."""

    created = []

    def __init__(self, instance, **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.registered_with = None
        self.held_store = None
        self.defer_store = False
        AdminScopeClient.created.append(self)

    def register_app_async(self, scopes=None, *, loopback=None,
                           callback=None, cancellable=None):
        self.registered_with = scopes
        self.reply(callback, StubTask(
            '{"client_id": "admin-client", "client_secret": "admin-secret"}'))

    def register_app_finish(self, result):
        data = self._finish(result)
        self.client_id = data['client_id']
        self.client_secret = data['client_secret']
        return data

    def store_secret_token(self, account_id, token, callback=None,
                           cancellable=None):
        self.stored = (account_id, token)
        if callback is not None:
            if self.defer_store:
                self.held_store = callback
            else:
                callback(None)


win, toasts = fresh_window()
import src.auth as auth_module  # noqa: E402
_real_client_class = auth_module.MastodonClient
auth_module.MastodonClient = AdminScopeClient
try:
    # Sign in, so there is an account whose token the moderation
    # authorization has to replace.
    session = AdminScopeClient('https://mod.example')
    session.token = 'login-token'
    win.account = {'id': '42', 'acct': 'mod@mod.example',
                   'instance': 'https://mod.example',
                   'role': {'permissions': 16}}
    win.client = session
    win.store_account(win.account)
    opened = []
    win.open_url = lambda uri: opened.append(uri)

    granted = []
    win.authorize_admin(granted.append)
    pump()
    admin = AdminScopeClient.created[-1]
    check('the moderation authorization asks for the admin scopes',
          admin.registered_with == ADMIN_SCOPES)
    check('the moderation authorization opens the browser',
          len(opened) == 1)

    # Answer it with a redirect carrying that attempt's state.
    win.on_oauth_callback(OAUTH_REDIRECT + '?code=admin-once&state='
                          + admin._state)
    pump()
    check('the moderation token replaces the login token',
          session.token == 'test-token')
    check('the new token is what the keyring keeps',
          admin.stored == ('42', 'test-token'))
    check('the caller is told it was granted', granted == [True])
    check('a granted attempt leaves no authorization in flight',
          win.auth.client is None and not win.auth.admin)
    check('same-identity moderation retains the current role',
          win.account['role'] == {'permissions': 16})

    for verified_id, verified_instance in (('99', 'https://mod.example'),
                                           ('42', 'https://other.example')):
        outcome = []
        win.authorize_admin(outcome.append)
        pump()
        mismatch = AdminScopeClient.created[-1]
        mismatch.ACCOUNT = json.dumps({'id': verified_id, 'acct': 'other'})
        mismatch.instance = verified_instance
        previous_account = win.account
        previous_pages = win.page_home
        win.on_oauth_callback(OAUTH_REDIRECT + '?code=mismatch-'
                              + verified_id + verified_instance.rsplit('/', 1)[-1]
                              + '&state=' + mismatch._state)
        pump()
        check('moderation identity %s %s cannot persist or adopt' % (
            verified_id, verified_instance), mismatch.stored is None
              and not hasattr(mismatch, 'client_credentials')
              and win.account is previous_account and win.client is session
              and win.page_home is previous_pages and outcome == [False])

    outcome = []
    win.authorize_admin(outcome.append)
    pump()
    held = AdminScopeClient.created[-1]
    held.defer_store = True
    win.on_oauth_callback(OAUTH_REDIRECT + '?code=held&state=' + held._state)
    pump()
    old_account = win.account
    new_session = AdminScopeClient('https://next.example')
    new_session.token = 'next-token'
    win.account = {'id': '42', 'instance': 'https://next.example'}
    win.client = new_session
    held.held_store(None)
    held.held_store(None)
    check('a switch during the moderation keyring write keeps the pair '
          'and adopts nothing',
          outcome == [False] and win.client is new_session
          and new_session.token == 'next-token'
          and held.stored == ('42', 'test-token')
          and held.client_credentials
          == ('42', 'admin-client', 'admin-secret'))
    check('the switch is reported as such',
          toasts[-1:] == ['Moderation access was saved for the account '
                          'you switched away from.'])
    win.account, win.client = old_account, session

    # A second attempt that fails must still answer its caller, or the
    # dashboard waits on a button that will never reply.
    refused = []
    win.authorize_admin(refused.append)
    pump()
    retry = AdminScopeClient.created[-1]
    retry.fail_token = True
    win.auth._client = retry
    attempt = win.auth.begin()
    retry.request_token_async(
        'bad', auth_module._replying(win.auth._on_token_received, attempt))
    pump()
    check('a refused moderation attempt answers its caller',
          refused == [False])
finally:
    auth_module.MastodonClient = _real_client_class


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

    def answer(self, token, error=None):
        self.pending.pop(0)(token, error)


win, toasts = fresh_window()
import src.window as window_module  # noqa: E402
_real_client_class = window_module.MastodonClient
window_module.MastodonClient = DeferredTokenClient
try:
    # Which accounts the window decided to forget, if any. The whole
    # account, so another instance's account with the same id survives.
    forgotten = []
    win.remove_account = lambda acc: forgotten.append(acc)
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

    # A locked or unreachable keyring is not "no login". Deleting the
    # account (and its Catch-Up snapshots) on GError was the bug.
    forgotten.clear()
    toast_count = len(toasts)
    win._activate_account(
        {'id': '9', 'instance': 'https://k.example', 'acct': 'kept'},
        login_only=True)
    DeferredTokenClient.created[-1].answer(
        None,
        GLib.Error('keyring locked', 'g-io-error-quark',
                   int(Gio.IOErrorEnum.FAILED)))
    pump()
    check('a keyring error does not forget the account',
          forgotten == [])
    check('a keyring error reports that the keyring failed',
          any('keyring' in t.lower() for t in toasts[toast_count:]))
    check('a keyring error leaves the login page up',
          win.main_stack.get_visible_child_name() == 'login')
finally:
    window_module.MastodonClient = _real_client_class

# --- Logging out revokes the token, not just forgets it ----------------

# Removal also clears the keyring. This section stubs that call: the
# checks are about revoke-then-forget, and a session with no secret
# service raises out of the real clear before the window can finish.
import src.mastodon as mastodon_module
_real_clear_secret = window_module.clear_secret_async
_real_mastodon_clear = mastodon_module.clear_secret_async


def successful_clear(instance, kind, callback=None, cancellable=None):
    if callback is not None:
        callback(None)


window_module.clear_secret_async = successful_clear
mastodon_module.clear_secret_async = successful_clear

# Deleting the keyring entry leaves the token valid on the instance: a
# copy of it, from a backup or a shared machine, would keep working. So
# the removal has to ask the server, and must not wait for the answer --
# the local state has to be settled either way.

win, toasts = fresh_window()


class RevokeClient(StubClient):
    """A signed-in session whose logout requests are recorded."""

    revokes = []
    unreachable = False

    def __init__(self, instance='https://example.social', **kwargs):
        super().__init__(**kwargs)
        self.instance = instance
        self.client_id = 'cid'
        self.client_secret = 'csecret'
        self.token = 'live-token'

    def load_client_credentials(self, account_id, callback,
                                cancellable=None):
        callback(True)

    def revoke_token_async(self, token, callback=None, cancellable=None):
        if not RevokeClient.unreachable:
            RevokeClient.revokes.append((token, self.client_id,
                                         self.client_secret))
        if callback is not None:
            pump()
            callback(self,
                     StubTask(error=http_error('Connection refused'))
                     if RevokeClient.unreachable else StubTask('{}'))

    def revoke_token_finish(self, result):
        return self._finish(result)


try:
    win.account = {'id': '42', 'acct': 'me@example.social',
                   'instance': 'https://example.social'}
    win.client = RevokeClient()
    win.store_account(win.account)
    win._remove_account_now(win.account)
    pump()
    check('logging out revokes the token it was using',
          RevokeClient.revokes == [('live-token', 'cid', 'csecret')])
    check('logging out forgets the account',
          win.account is None
          and win.main_stack.get_visible_child_name() == 'login')

    # A server that cannot be reached must not strand the window signed in.
    RevokeClient.revokes = []
    RevokeClient.unreachable = True
    win, toasts = fresh_window()
    win.account = {'id': '43', 'acct': 'other@example.social',
                   'instance': 'https://example.social'}
    win.client = RevokeClient()
    win.store_account(win.account)
    win._remove_account_now(win.account)
    pump()
    check('an unreachable server does not keep the window signed in',
          win.account is None
          and win.main_stack.get_visible_child_name() == 'login')
finally:
    window_module.clear_secret_async = _real_clear_secret
    mastodon_module.clear_secret_async = _real_mastodon_clear

# Both exact account-bound keys are removed; deferred results cannot sign out B.
from src.mastodon import CLIENT_CREDENTIALS_KIND
for failed_kind in (None, '42', CLIENT_CREDENTIALS_KIND + '42'):
    win, toasts = fresh_window()
    removed = {'id': '42', 'instance': 'https://a.example', 'acct': 'removed'}
    other = {'id': '43', 'instance': 'https://a.example', 'acct': 'other'}
    remote = {'id': '42', 'instance': 'https://b.example', 'acct': 'remote'}
    secrets = {(a['instance'], prefix + a['id']): 'synthetic'
               for a in (removed, other, remote)
               for prefix in ('', CLIENT_CREDENTIALS_KIND)}
    pending_deletes = []

    def deferred_clear(instance, kind, callback=None, cancellable=None,
                       pending_deletes=pending_deletes):
        pending_deletes.append((instance, kind, callback))

    def answer_clear(pending_deletes=pending_deletes, failed_kind=failed_kind,
                     secrets=secrets):
        instance, kind, callback = pending_deletes.pop(0)
        error = None
        if kind == failed_kind:
            error = GLib.Error('synthetic keyring deletion failed',
                               'g-io-error-quark', 0)
        else:
            secrets.pop((instance, kind), None)
        callback(error)

    window_module.clear_secret_async = deferred_clear
    mastodon_module.clear_secret_async = deferred_clear
    try:
        win.account = removed
        win.client = RevokeClient(removed['instance'])
        win.store_account(removed)
        win._finish_account_removal(removed)
        check('local account removal completes before either keyring callback',
              win.account is None and len(pending_deletes) == 2
              and win.settings.get_strv('accounts') == [])
        win.account = other
        new_client = win.client = RevokeClient(other['instance'])
        answer_clear()
        check('first deletion result emits no premature warning', toasts == [])
        answer_clear()
        check('credential deletion is scoped to exact instance and account',
              all((a['instance'], prefix + a['id']) in secrets
                  for a in (other, remote) for prefix in ('', CLIENT_CREDENTIALS_KIND))
              and all(((removed['instance'], prefix + '42') in secrets)
                      == (prefix + '42' == failed_kind)
                      for prefix in ('', CLIENT_CREDENTIALS_KIND)))
        check('late keyring result preserves new session and reports one warning',
              win.account is other and win.client is new_client
              and len(toasts) == (0 if failed_kind is None else 1)
              and (not toasts or toasts[0].startswith(
                  'Could not remove all saved credentials')))
        if failed_kind is None:
            win._finish_account_removal(removed)
            answer_clear()
            answer_clear()
            check('deleting absent credential entries remains successful', toasts == [])
    finally:
        window_module.clear_secret_async = _real_clear_secret
        mastodon_module.clear_secret_async = _real_mastodon_clear

# --- Only web links reach the system launcher -------------------------
#
# Every link a server supplies ends in textemoji.launch_uri; the GTK
# launcher is swapped for one that records what it was handed.

launched = []


class RecordingLauncher:
    @staticmethod
    def new(uri):
        launched.append(uri)
        return RecordingLauncher()

    def launch(self, *_args):
        pass


win, toasts = fresh_window()
real_gtk = textemoji.Gtk
textemoji.Gtk = types.SimpleNamespace(UriLauncher=RecordingLauncher)
try:
    for uri in ('javascript:alert(1)', 'file:///etc/passwd',
                'smb://host/share', 'babble://oauth/callback?code=x',
                'https://[oops/x'):
        textemoji.launch_uri(win, uri)
    check('non-web links never reach the launcher, with a toast each',
          launched == [] and toasts == [_('Could not open link')] * 5)
    toasts.clear()
    pages.open_link(win, 'https://[oops/x')
    check('a malformed link clicked in a post is refused, not raised',
          launched == [] and toasts == [_('Could not open link')])
    textemoji.launch_uri(win, 'https://example.social/@me')
    check('a web link is launched',
          launched == ['https://example.social/@me'])
finally:
    textemoji.Gtk = real_gtk

# --- The login opens the browser through the window's own open_url -----
#
# Every other test here replaces win.open_url, so the method itself went
# missing without a single failure and a login never left the app.

uris = []


class BrowserLauncher:
    refuse = False

    @staticmethod
    def new(uri):
        uris.append(uri)
        return BrowserLauncher()

    def launch(self, _parent, _cancellable, callback):
        callback(self, None)

    def launch_finish(self, _result):
        if BrowserLauncher.refuse:
            raise GLib.Error('no portal')


win, toasts = fresh_window()
fallbacks = []
win._open_url_fallback = fallbacks.append
real_window_gtk = window_module.Gtk
window_module.Gtk = types.SimpleNamespace(UriLauncher=BrowserLauncher)
try:
    check('the window has an open_url for the login to call',
          callable(getattr(BabbleWindow, 'open_url', None)))
    BabbleWindow.open_url(win, 'https://example.social/oauth/authorize?x=1')
    check('the authorization page is handed to the launcher',
          uris == ['https://example.social/oauth/authorize?x=1']
          and fallbacks == [])
    BrowserLauncher.refuse = True
    BabbleWindow.open_url(win, 'https://example.social/oauth/authorize?x=2')
    check('a refusing portal falls back to the default handler',
          fallbacks == ['https://example.social/oauth/authorize?x=2'])
finally:
    window_module.Gtk = real_window_gtk

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all oauth callback checks passed')
