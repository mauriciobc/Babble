# SPDX-License-Identifier: GPL-3.0-or-later

"""The OAuth callback test's harness: the stub clients and the window pump.

Every stub answers the way the real client would for the one call under
test, and ``fresh_window`` builds the window against a memory settings
backend. Test-only; never installed.
"""

import json
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Gio, GLib  # noqa: E402

from src.mastodon import MastodonClient  # noqa: E402
from src.oauthpkce import OAUTH_REDIRECT  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

STATE = 'state-for-this-attempt'
SCHEMA = 'io.github.mauriciobc.Babble'
REDIRECT_URI = OAUTH_REDIRECT + '?code=code-once&state=' + STATE
SECOND_URI = OAUTH_REDIRECT + '?code=code-two&state=' + STATE
launched = []
uris = []
failures = []

_real_client = MastodonClient('https://example.social')

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

    def invalid_response(self, what):
        return MastodonClient.invalid_response(self, what)

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

    # Requests the pages the shell builds after a successful login send.
    UNANSWERED = ('/timelines/home', '/notifications',
                  '/notifications/unread_count')

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        if path == '/accounts/verify_credentials':
            # The account check, from `src.api.accounts`.
            self.reply(callback, StubTask(
                error=http_error('The access token is invalid'))
                if self.fail_verify else StubTask(self.ACCOUNT))
            return
        if path in self.UNANSWERED:
            # The pages the shell builds after a login stay loading: the
            # test is about the login, not the timeline behind it.
            return
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

class FakeComposeView:
    """Records what reaches the composer's intake; its filtering and
    limit are the compose test's to check."""

    def __init__(self):
        self.batches = []

    def add_attachment_files(self, files):
        self.batches.append(list(files))
        return []

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

def successful_clear(instance, kind, callback=None, cancellable=None):
    if callback is not None:
        callback(None)

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

class RecordingLauncher:
    can = True

    @staticmethod
    def new(uri):
        launched.append(uri)
        return RecordingLauncher()

    def can_launch(self, _parent):
        return RecordingLauncher.can

    def launch(self, *_args):
        pass

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
