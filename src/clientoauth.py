# SPDX-License-Identifier: GPL-3.0-or-later
"""App registration, tokens and the authorization exchange.

The OAuth half of the client: registering the app, storing and loading
the token and the client credentials, revoking them on sign-out, and the
three calls that turn a callback URI into a token. The keyring itself is
``keyringstore``; the protocol constants are ``oauthpkce``.
"""

import json
import urllib.parse

import gi

gi.require_version('Soup', '3.0')

try:
    from .i18n import gettext as _  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _  # noqa: E402

from gi.repository import Gio, GLib  # noqa: E402

from .keyringstore import (clear_secret_async,
    load_secret_async,
    store_secret_async)  # noqa: E402
from .oauthpkce import APP_WEBSITE, CLIENT_CREDENTIALS_KIND  # noqa: E402
from .oauthpkce import (
    OAUTH_REDIRECT, OAUTH_SCOPES, REDIRECT_URIS, code_challenge,
    generate_code_verifier, new_state)  # noqa: E402


class OAuthMixin:
    def register_app_async(self, scopes=OAUTH_SCOPES, *, loopback=None,
                           callback=None, cancellable=None):
        """POST /api/v1/apps. *scopes* becomes the ceiling every later
        authorization may ask for: Mastodon refuses to authorize a scope
        the registration did not declare.

        *loopback*, a :func:`loopback_redirect` URI, is registered ahead
        of the ``babble://`` and out-of-band redirects, which stay as the
        fallbacks an authorization can still name.

        *callback* is keyword-only: passing the handler positionally used
        to bind it to *scopes*, which silently left the registration with
        no completion handler and the browser step never opened.
        """
        redirect_uris = REDIRECT_URIS if loopback is None \
            else loopback + ' ' + REDIRECT_URIS
        return self._request_async('POST', '/apps',
                                   form={'client_name': 'Babble',
                                         'redirect_uris': redirect_uris,
                                         'scopes': scopes,
                                         'website': APP_WEBSITE},
                                   callback=callback, cancellable=cancellable)

    def register_app_finish(self, result):
        data = self._finish(result)
        client_id = data.get('client_id') if isinstance(data, dict) else None
        if not isinstance(client_id, str) or not client_id:
            raise GLib.Error(_('The server did not return an application id.'),
                             'mastodon-http-error-quark', 0)
        client_secret = data.get('client_secret')
        if not isinstance(client_secret, str) or not client_secret:
            raise GLib.Error(
                _('The server did not return an application secret.'),
                'mastodon-http-error-quark', 0)
        self.client_id = client_id
        self.client_secret = client_secret
        return data

    def store_secret_token(self, account_id, token, callback=None,
                           cancellable=None):
        store_secret_async(self.instance, account_id, token, callback,
                           cancellable)

    def load_secret_token(self, account_id, callback, cancellable=None):
        """Read the stored token; *callback(value, error=None)*."""
        load_secret_async(self.instance, account_id, callback, cancellable)

    def store_client_credentials(self, account_id, client_id, client_secret,
                                 callback=None, cancellable=None):
        """Keep the OAuth client credentials in the keyring, for
        *account_id*.

        ``POST /oauth/revoke`` authenticates the client, so signing a
        token out later needs them again -- and a logout can happen in a
        later process than the one that registered the app. They are keyed
        by account because each authorization registers its own
        application: one entry per instance would be overwritten by the
        second login, leaving the first token unrevokable.
        """
        payload = json.dumps({'client_id': client_id,
                              'client_secret': client_secret})
        store_secret_async(self.instance,
                           CLIENT_CREDENTIALS_KIND + account_id, payload,
                           callback, cancellable)

    def load_client_credentials(self, account_id, callback,
                                cancellable=None):
        """Read the stored client credentials for *account_id* into
        ``client_id`` and ``client_secret``; *callback* receives None
        when there are none."""
        def loaded(text, error=None):
            if error is not None:
                callback(False)
                return
            if text:
                try:
                    stored = json.loads(text)
                    self.client_id = stored.get('client_id')
                    self.client_secret = stored.get('client_secret')
                except ValueError:
                    self.client_id = self.client_secret = None
            callback(text is not None and self.client_id is not None)
        load_secret_async(self.instance, CLIENT_CREDENTIALS_KIND + account_id,
                          loaded, cancellable)

    def clear_client_credentials(self, account_id, callback=None,
                                 cancellable=None):
        clear_secret_async(self.instance, CLIENT_CREDENTIALS_KIND + account_id,
                           callback, cancellable)

    def adopt_client_credentials(self, other):
        """Copy *other*'s OAuth client credentials onto this client.

        Each authorization registers its own application, so the client
        that will revoke a token has to be the one the token was issued
        to. A client built to read an existing token back has none of its
        own, and without them the revoke is refused with a 403.
        """
        self.client_id = other.client_id
        self.client_secret = other.client_secret
        return bool(self.client_id and self.client_secret)

    def revoke_token_async(self, token, callback=None, cancellable=None):
        """POST /oauth/revoke: the token stops being accepted by the
        instance.

        The endpoint is POST only (a DELETE 404s) and authenticates the
        client, so ``client_id``/``client_secret``/``token`` all travel
        as form fields -- a bearer header alone is refused. Mastodon
        answers 200 for a token that was already gone, which is exactly
        what a logout wants.
        """
        if not token or not self.client_id or not self.client_secret:
            task = Gio.Task.new(self, cancellable, callback)
            task.return_error(GLib.Error(
                _('This login cannot be signed out on the server.'),
                'mastodon-http-error-quark', 0))
            return task
        return self._request_async(
            'POST', '/oauth/revoke', auth=False,
            form={'client_id': self.client_id,
                  'client_secret': self.client_secret,
                  'token': token},
            callback=callback, cancellable=cancellable)

    def revoke_token_finish(self, result):
        """Revocation is idempotent server-side: a 200 means the token is
        gone, whether or not it was still live."""
        return self._finish(result)

    def get_authorize_url(self, redirect_uri=OAUTH_REDIRECT,
                          scopes=OAUTH_SCOPES):
        """The browser URL that starts one authorization.

        A fresh PKCE verifier and ``state`` are minted per call and kept
        on the client until the matching callback arrives. PKCE binds the
        code to this process: an authorization code intercepted on the
        ``babble://`` scheme by any other application cannot be redeemed
        without the verifier, which never leaves this process.

        *scopes* must be a subset of what :meth:`register_app_async`
        declared; Mastodon refuses the authorization otherwise.
        """
        self._code_verifier = generate_code_verifier()
        self._state = new_state()
        self._redirect_uri = redirect_uri
        query = urllib.parse.urlencode({
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': scopes,
            'state': self._state,
            'code_challenge': code_challenge(self._code_verifier),
            'code_challenge_method': 'S256',
        })
        return self.instance + '/oauth/authorize?' + query

    def parse_callback_uri(self, uri):
        """Return a code, raise a validated OAuth denial, or return None.

        A redirect callback must carry the ``state`` this client minted
        in :meth:`get_authorize_url`. A callback without one, or with
        someone else's, is refused rather than spent against our own
        PKCE verifier: without the check, any application able to hand
        Babble a ``babble://oauth/callback?code=...`` URL could redeem
        a code of its own choosing into the signed-in session (login
        CSRF).

        The out-of-band flow never reaches this method; the user pastes
        the code directly and :meth:`request_token_async` binds it to
        the verifier on its own.

        Only the redirect this authorization named is read: a URL with
        another scheme, host, port or path is not an answer to it.
        """
        try:
            parts = urllib.parse.urlsplit(uri)
        except ValueError:  # a malformed authority, such as an unmatched '['
            return None
        expected = urllib.parse.urlsplit(self._redirect_uri)
        # The scheme and host are case-insensitive (urlsplit already
        # lowercases the scheme); the path is not.
        if (parts.scheme, parts.netloc.lower(), parts.path) \
                != (expected.scheme, expected.netloc, expected.path):
            return None
        params = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
        codes = params.get('code')
        errors = params.get('error')
        states = params.get('state')
        if states is None or len(states) != 1 or not states[0] \
                or states[0] != self._state:
            return None
        if codes is not None and errors is not None:
            return None
        if errors is not None:
            if len(errors) != 1 or not errors[0]:
                return None
            message = _('Authorization was denied.') \
                if errors[0] == 'access_denied' else _(
                    'The server could not authorize this request.')
            raise GLib.Error(message, 'mastodon-oauth-error-quark', 0)
        if codes is None or len(codes) != 1 or not codes[0]:
            return None
        return codes[0]

    def request_token_async(self, code, callback=None, cancellable=None,
                            redirect_uri=OAUTH_REDIRECT):
        # /oauth/token lives at the instance root, not under /api/v1.
        form = {'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
                'code': code}
        if self._code_verifier is not None:
            form['code_verifier'] = self._code_verifier
        return self._request_async('POST', self.instance + '/oauth/token',
                                   form=form,
                                   callback=callback, cancellable=cancellable)

    def request_token_finish(self, result):
        data = self._finish(result)
        token = data.get('access_token') if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise GLib.Error(_('The server did not return an access token.'),
                             'mastodon-http-error-quark', 0)
        return token

    def invalid_response(self, what):
        """The error for a 2xx answer whose body is not what the endpoint
        promises, for the `api` modules, which cannot import GLib or
        translate. *what* is ``account``."""
        messages = {
            'account': _('The server did not return a valid account.'),
        }
        return GLib.Error(messages[what], 'mastodon-http-error-quark', 0)
