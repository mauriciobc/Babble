# SPDX-License-Identifier: GPL-3.0-or-later

"""The OAuth login flow: registration, the browser round trip, the
loopback redirect, the token exchange and the keyring writes.

The whole state machine lives here, not in the window. What comes out of
it is one account and a handful of things to tell the user about, so a
:class:`LoginFlow` speaks in signals instead of reaching for widgets:

``succeeded(account)``
    The one domain event. *account* is the account now signed in, with
    its token already in the keyring. The window adopts it, shows the
    shell, and says who signed in.
``failed(heading, body)``
    An attempt ended badly; *body* is a plain-text reason.
``message(text)``
    An outcome worth a toast.
``browser(url)``
    Open *url* and let the user authorize.
``changed()``
    The login widgets' spinner, buttons and code entry have to follow
    the flow.
``attended()``
    The browser handed the login back; the window should take focus.

Everything else about an authorization -- which attempt is in flight,
which codes have been spent, whether a loopback listener is open -- is
bookkeeping no widget has any business knowing.
"""

import html

import gi

gi.require_version('Soup', '3.0')

try:
    from .i18n import gettext as _  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _  # noqa: E402

from gi.repository import Gio, GLib, GObject, Soup  # noqa: E402

from . import pages  # noqa: E402
from .accounts import account_key  # noqa: E402
from .mastodon import (ADMIN_SCOPES, LOOPBACK_CALLBACK_PATH,  # noqa: E402
                       MastodonClient, OAUTH_REDIRECT, OAUTH_SCOPES,
                       OOB_REDIRECT, loopback_redirect)

# How long the loopback listener waits for the browser to come back.
LOOPBACK_TIMEOUT_SECONDS = 10 * 60

# Authorization codes are handed out by the instance and spent by
# /oauth/token, so a ledger of this many recent codes outlives every
# redelivery a browser can be trusted to make of one redirect.
MAX_CONSUMED_CODES = 32

# What an authorization in flight is for.
AUTH_LOGIN = 'login'
AUTH_ADMIN = 'admin'


def _close_loopback(_msg, server):
    """Close a spent loopback listener once its last page is out.

    Closing it from the request handler would drop the page the browser
    is still waiting for."""
    server.disconnect()


def discard_reply(finish, result):
    """Take delivery of a reply nobody is waiting for, silently."""
    try:
        finish(result)
    except GLib.Error:
        pass


def _replying(handler, attempt):
    """Wrap a Gio callback so the handler learns its attempt.

    Gio calls the callback as ``(source, result, *user_data)``, so the
    attempt travels as user data and arrives last.
    """
    return lambda client, result: handler(client, result, attempt)


def _reply(msg, status, heading, body):
    """Answer one loopback request with a small standalone page."""
    page = ('<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width">'
            '<title>%s</title><style>body{font-family:system-ui,'
            'sans-serif;text-align:center;margin:4em 1em}</style>'
            '</head><body><h1>%s</h1><p>%s</p></body></html>\n'
            % (html.escape(heading), html.escape(heading),
               html.escape(body)))
    msg.set_status(status, None)
    msg.get_response_headers().replace('Cache-Control', 'no-store')
    msg.set_response('text/html; charset=utf-8', Soup.MemoryUse.COPY,
                     page.encode('utf-8'))


class LoginFlow(GObject.Object):
    """One OAuth authorization at a time.

    Attempt ids increase monotonically. They label the redirect that
    answers an attempt and invalidate any reply still in flight for a
    superseded one, so a late reply can never install a session.

    *current_account* is a callable returning the account the window has
    signed in, or None. The flow consults it only where the session
    itself is the thing in question: a moderation authorization is bound
    to the account that asked for it, so a reply arriving after an
    account switch is refused rather than merged into the new session.
    """

    __gtype_name__ = 'BabbleLoginFlow'

    # The signals are declarations, not handlers: none of these bodies
    # runs, because every one of them is handled by the window.
    @GObject.Signal(arg_types=(object,))
    def succeeded(self, account):
        """The account now signed in, its token already in the keyring."""

    @GObject.Signal(arg_types=(str, str))
    def failed(self, heading, body):
        """An attempt ended badly; *body* is a plain-text reason."""

    @GObject.Signal(arg_types=(str,))
    def message(self, text):
        """An outcome worth a toast."""

    @GObject.Signal(arg_types=(str,))
    def browser(self, url):
        """Open *url* and let the user authorize."""

    @GObject.Signal
    def changed(self):
        """The login widgets have to follow the flow."""

    @GObject.Signal
    def attended(self):
        """The browser handed the login back; take focus."""

    def __init__(self, current_account=None, **kwargs):
        super().__init__(**kwargs)
        # Weak, and answering None once the window is gone: a reply
        # arriving after it closed must find no session to merge into.
        self._current_account = current_account or (lambda: None)
        # Every request the flow starts is cancellable, so closing the
        # window does not leave the loopback listener or a pending keyring
        # write outliving it.
        self._cancellable = Gio.Cancellable()
        self._client = None
        # The 127.0.0.1 listener the login in flight is redirected to.
        self._loopback = None
        self._loopback_uri = None
        self._loopback_timeout = 0
        self._fallback = False
        self._mode = AUTH_LOGIN
        self._admin_callback = None
        # The account a moderation authorization was started for.
        self._admin_owner = None
        self._attempt = 0
        self._settled_attempt = -1
        self._pending_code = None
        self._consumed_codes = {}

    # What the window reads

    @GObject.Property(type=object)
    def client(self):
        """The client of the authorization in flight, or None."""
        return self._client

    @GObject.Property(type=bool, default=False)
    def waiting(self):
        """Whether an authorization is in flight, spinner included."""
        return self._client is not None

    @GObject.Property(type=bool, default=False)
    def fallback(self):
        """Whether the user fell back to pasting the code by hand."""
        return self._fallback

    @GObject.Property(type=bool, default=False)
    def admin(self):
        """Whether the authorization in flight asks for admin scopes."""
        return self._mode == AUTH_ADMIN

    @property
    def loopback(self):
        """The open :class:`Soup.Server`, or None."""
        return self._loopback

    @property
    def loopback_uri(self):
        """The redirect URI the open listener answers, or None."""
        return self._loopback_uri

    @property
    def loopback_timeout(self):
        """The id of the expiry timeout, 0 when none is armed."""
        return self._loopback_timeout

    # Starting and stopping

    def begin(self):
        """Open the next authorization attempt, returning its id.

        Each attempt gets its own ``GCancellable``. A cancelled
        cancellable stays cancelled, and the window calls ``cancel()`` on
        every account switch and sign-out; reusing that object would make
        the next login fail instantly with "Operation was cancelled".
        """
        self._attempt += 1
        self._pending_code = None
        self.stop_loopback()
        self._cancellable = Gio.Cancellable()
        return self._attempt

    def restart(self):
        """Abandon whatever is in flight and stand ready for a new one.

        What an authorization in flight is for is forgotten, so a login
        cancelled mid-moderation-authorization also settles the callback
        its caller is waiting on.
        """
        self.begin()
        pending, self._admin_callback = self._admin_callback, None
        self._client = None
        self._fallback = False
        self._mode = AUTH_LOGIN
        self.emit('changed')
        if pending is not None:
            pending(False)

    def cancel(self):
        """Cancel requests in flight, then stand ready for a new login.

        The window calls this when it tears the session down. Cancelling
        is what makes a late reply harmless rather than merely ignored:
        the callback fires with ``G_IO_ERROR_CANCELLED``. ``restart()``
        (via ``begin()``) then installs a fresh cancellable so the next
        login is not stuck on the cancelled one.
        """
        self._cancellable.cancel()
        self.restart()

    def start_login(self, instance_url):
        """Register an application on *instance_url* and open the
        browser for the authorization."""
        self._client = MastodonClient(instance_url)
        self._mode = AUTH_LOGIN
        self.begin()
        self.emit('changed')
        self._client.register_app_async(
            loopback=self.start_loopback(), callback=self._on_app_registered,
            cancellable=self._cancellable)

    def start_admin(self, instance_url, owner, callback=None):
        """Authorize the admin scopes on top of the signed-in session.

        The scopes are not part of the login the app asks for by default:
        Mastodon lists every requested scope on its consent screen, and
        "all administrative functions" is not something to ask of
        everyone. *owner* is the ``instance#id`` key of the account that
        asked, and *callback(granted)* runs once with the outcome.
        """
        self._admin_callback = callback
        self._admin_owner = owner
        self._client = MastodonClient(instance_url)
        self._mode = AUTH_ADMIN
        self.begin()
        self.emit('changed')
        self._client.register_app_async(
            scopes=ADMIN_SCOPES, loopback=self.start_loopback(),
            callback=self._on_app_registered, cancellable=self._cancellable)

    def _on_app_registered(self, client, result):
        if client is not self._client:
            discard_reply(client.register_app_finish, result)
            return
        try:
            client.register_app_finish(result)
        except GLib.Error as e:
            self._fail(self._attempt,
                       _('Could not register with %s') % client.instance,
                       e.message)
            return
        redirect = OOB_REDIRECT if self._fallback \
            else self._loopback_uri or OAUTH_REDIRECT
        scopes = ADMIN_SCOPES if self.admin else OAUTH_SCOPES
        self.emit('browser', client.get_authorize_url(redirect, scopes))
        if not self._fallback:
            self.emit('message',
                      _('Finish authorizing moderation in the web browser')
                      if self.admin
                      else _('Finish logging in with the web browser'))

    # The redirect

    def on_oauth_callback(self, uri, redirect_uri=OAUTH_REDIRECT):
        """Exchange the code a redirect to *redirect_uri* carries.

        Returns whether the exchange started. *uri* arrives on the
        ``babble://`` scheme or, rebuilt from the request, on the
        loopback listener; either way it must answer the authorization
        in flight.
        """
        client = self._client
        if client is None:
            return False
        # parse_callback_uri drops a callback whose state belongs to a
        # different authorization than the one in flight.
        code = client.parse_callback_uri(uri)
        if code is None:
            return False
        return self.start_token_exchange(code, redirect_uri)

    def use_pasted_code(self):
        """Fall back to the out-of-band redirect, for a user who cannot
        get a loopback or a ``babble://`` handler working.

        Returns whether the authorization was named again; False leaves
        it to the caller to ask for the code by hand.
        """
        self.stop_loopback()
        self._fallback = True
        self.emit('changed')
        if self._client is not None and self._client.client_id is not None:
            # The scopes the client was registered for, not the default:
            # an admin authorization that falls back to the pasted code
            # must still ask for the admin scopes, or Mastodon grants a
            # token that cannot moderate and the dashboard reports the
            # authorization as having succeeded.
            self.emit('browser', self._client.get_authorize_url(
                OOB_REDIRECT,
                ADMIN_SCOPES if self.admin else OAUTH_SCOPES))
            return True
        return False

    def enter_code(self, code):
        """Redeem a code the user pasted in."""
        code = (code or '').strip()
        if not code:
            return False
        return self.start_token_exchange(code, OOB_REDIRECT)

    # Loopback redirect

    def start_loopback(self):
        """Listen on 127.0.0.1 for the redirect of the authorization
        starting now, returning its ``http://127.0.0.1:<port>/callback``
        URI.

        The browser hands a loopback redirect straight back, so the login
        needs no ``babble://`` handler registered on the desktop. When no
        port can be bound this returns None and the authorization names
        ``babble://`` as before.
        """
        self.stop_loopback()
        server = Soup.Server()
        try:
            server.listen_local(0, Soup.ServerListenOptions.IPV4_ONLY)
        except GLib.Error:
            return None
        uris = server.get_uris()
        if not uris or uris[0].get_port() <= 0:
            server.disconnect()
            return None
        # Held weakly: the flow owns the server, and a strong handler
        # would close a cycle through C that the collector cannot see.
        server.add_handler(None, pages.weak_method(self._on_loopback_request))
        self._loopback = server
        self._loopback_uri = loopback_redirect(uris[0].get_port())
        self._loopback_timeout = GLib.timeout_add_seconds(
            LOOPBACK_TIMEOUT_SECONDS,
            pages.weak_method(self._on_loopback_expired))
        return self._loopback_uri

    def detach_loopback(self):
        """Forget the listener without closing it, returning it."""
        server, self._loopback = self._loopback, None
        self._loopback_uri = None
        if self._loopback_timeout:
            GLib.source_remove(self._loopback_timeout)
            self._loopback_timeout = 0
        return server

    def stop_loopback(self):
        server = self.detach_loopback()
        if server is not None:
            server.disconnect()

    def _on_loopback_expired(self):
        self._loopback_timeout = 0
        self.stop_loopback()
        return GLib.SOURCE_REMOVE

    def _on_loopback_request(self, server, msg, path, _query):
        """Answer one request to the loopback listener.

        Only ``GET /callback`` answering the authorization in flight is
        taken, through the same state check a ``babble://`` redirect
        gets. The first one to start the exchange spends the listener:
        it closes as soon as the browser has the page.
        """
        if server is not self._loopback or path != LOOPBACK_CALLBACK_PATH:
            msg.set_status(404, None)
            return
        if msg.get_method() != 'GET':
            msg.get_response_headers().replace('Allow', 'GET')
            msg.set_status(405, None)
            return
        redirect = self._loopback_uri
        query = msg.get_uri().get_query()
        uri = redirect + '?' + query if query else redirect
        if not self.on_oauth_callback(uri, redirect):
            _reply(msg, 400,
                   _('Babble could not use this login'),
                   _('Return to Babble and try logging in again.'))
            return
        _reply(msg, 200,
               # Translators: heading of the web page the browser shows
               # once it has handed the login back to the app
               _('Logged in with Babble'),
               _('You can close this tab and return to Babble.'))
        self.detach_loopback()
        msg.connect('finished', _close_loopback, server)
        msg.connect('disconnected', _close_loopback, server)
        # No activation token comes back from the browser, so this may
        # only mark the window as wanting attention.
        self.emit('attended')

    # The exchange

    def _consume_code(self, code):
        """Spend an authorization code, returning False if already spent.

        The instance accepts a code once, so a redelivered redirect must
        not start a second exchange: doing so raced the success of the
        first one against an ``invalid_grant`` toast. The ledger only
        has to outlive the retries of the redirect it is answering.
        """
        if code in self._consumed_codes:
            return False
        self._consumed_codes[code] = self._attempt
        while len(self._consumed_codes) > MAX_CONSUMED_CODES:
            del self._consumed_codes[next(iter(self._consumed_codes))]
        return True

    def start_token_exchange(self, code, redirect_uri):
        """Run the single token exchange this authorization allows.

        The code is spent and the exchange is marked in flight *before*
        the request leaves, so a redirect that arrives again -- in the
        same main loop turn or long after the browser retried it --
        finds this attempt busy and does nothing at all.
        """
        if self._client is None or self._pending_code is not None:
            return False
        if not self._consume_code(code):
            return False
        attempt = self._attempt
        self._pending_code = code
        self.emit('changed')
        self._client.request_token_async(
            code, _replying(self._on_token_received, attempt),
            redirect_uri=redirect_uri, cancellable=self._cancellable)
        return True

    def settle(self, attempt, heading=None, body=None, message=None):
        """Close out one authorization attempt with a single outcome.

        The first outcome wins, so a late failure can neither follow the
        success toast nor replace it, and a duplicate reply for the same
        attempt stays silent.
        """
        if attempt != self._attempt or attempt <= self._settled_attempt:
            return False
        self._settled_attempt = attempt
        self._pending_code = None
        self.stop_loopback()
        if heading is not None:
            self._client = None
            self._fallback = False
            # What the authorization in flight is for. A login installs a
            # new account; a moderation authorization replaces the token
            # of the one already signed in, asking for the admin scopes
            # on top.
            self._mode = AUTH_LOGIN
            self._admin_callback = None
            self.emit('changed')
            self.emit('failed', heading, body)
        elif message is not None:
            self.emit('message', message)
        return True

    def _fail(self, attempt, heading, body):
        """Close out a failed authorization on the channel it started on.

        A moderation authorization owes its caller an outcome, so it must
        not settle through the login path -- that one reports to a toast
        and would leave the dashboard waiting forever.
        """
        if self.admin:
            self._settle_admin(False, _('Authorization failed: %s') % body)
        else:
            self.settle(attempt, heading, body)

    def _on_token_received(self, client, result, attempt):
        if attempt != self._attempt:
            discard_reply(client.request_token_finish, result)
            return
        try:
            token = client.request_token_finish(result)
        except GLib.Error as e:
            self._fail(attempt, _('Authorization failed'), e.message)
            return
        client.token = token
        client.verify_credentials_async(
            _replying(self._on_credentials_verified, attempt),
            cancellable=self._cancellable)

    def _on_credentials_verified(self, client, result, attempt):
        if attempt != self._attempt:
            discard_reply(client.verify_credentials_finish, result)
            return
        try:
            account = client.verify_credentials_finish(result)
        except GLib.Error as e:
            self._fail(attempt, _('Could not verify the account'),
                       e.message)
            return
        account['instance'] = client.instance
        if self.admin:
            self._on_admin_token(client, account, attempt)
            return
        client.store_secret_token(
            account['id'], client.token,
            lambda error: self._on_token_stored(client, account, attempt,
                                                error),
            cancellable=self._cancellable)

    def _on_token_stored(self, client, account, attempt, error):
        """Commit the login once the token is really in the keyring.

        Recording an account whose token never landed leaves an entry
        that signs the user out on the next start, with no explanation
        here or then. A keyring that refuses the secret is reported
        instead, and nothing is stored.
        """
        if attempt != self._attempt:
            return
        if error is not None:
            self._report_keyring_failure(attempt, account)
            return
        # The client credentials have to land before the account does.
        # Without them a later logout cannot authenticate the revoke, so
        # it drops the local account while the server token stays live.
        client.store_client_credentials(
            account['id'], client.client_id, client.client_secret,
            lambda cred_error: self._on_credentials_stored(
                client, account, attempt, cred_error),
            cancellable=self._cancellable)

    def _on_credentials_stored(self, client, account, attempt, error):
        """The last keyring write answered: commit, or report."""
        if attempt != self._attempt:
            return
        if error is not None:
            self._report_keyring_failure(attempt, account)
            return
        # Emitted with the client still on the flow: the window needs it
        # to adopt the session the account just authorized.
        self.emit('succeeded', account)
        pending, self._admin_callback = self._admin_callback, None
        self._client = None
        self._fallback = False
        self._mode = AUTH_LOGIN
        self.emit('changed')
        if pending is not None:
            pending(False)
        self.settle(attempt, message=_('Logged in as @%s')
                    % account.get('acct', ''))

    def _report_keyring_failure(self, attempt, account):
        self.settle(
            attempt, _('Could not save the login'),
            _('The system keyring refused to store the login for '
              '@%s. Check that a keyring service is running, then '
              'try again.') % account.get('acct', ''))

    # Moderation authorization

    def _on_admin_token(self, client, account, attempt):
        """Keep the moderation token as the signed-in account's token."""
        client.store_secret_token(
            account['id'], client.token,
            lambda error: self._on_admin_token_stored(client, account,
                                                      attempt, error),
            cancellable=self._cancellable)

    def _on_admin_token_stored(self, client, account, attempt, error):
        if attempt != self._attempt or self._stale_admin_owner():
            return
        if error is not None:
            self._settle_admin(False,
                               _('The system keyring refused to store the '
                                 'login.'))
            return
        # The credentials have to land before the token does, or a
        # restart leaves an account that cannot be revoked on logout.
        client.store_client_credentials(
            account['id'], client.client_id, client.client_secret,
            lambda cred_error: self._on_admin_credentials_stored(
                client, account, attempt, cred_error),
            cancellable=self._cancellable)

    def _on_admin_credentials_stored(self, client, account, attempt, error):
        if attempt != self._attempt or self._stale_admin_owner():
            return
        if error is not None:
            self._settle_admin(False,
                               _('The system keyring refused to store the '
                                 'login.'))
            return
        # The freshly verified entity carries no role, and losing it
        # would take the admin action with it; keep the one the session
        # still has.
        account['role'] = (self._current_account() or {}).get('role')
        self.emit('succeeded', account)
        self._settle_admin(True, _('Moderation access granted'))

    def _stale_admin_owner(self):
        """Whether the session moved on while the browser was open.

        The attempt id alone cannot tell: switching accounts does not
        bump it, so a reply from the previous session would still match
        and would merge a moderation token into the wrong account.
        """
        current = self._current_account()
        return current is None or self._admin_owner != account_key(current)

    def _settle_admin(self, granted, message=None):
        """Report the outcome once, and clear the admin attempt."""
        callback = self._admin_callback
        self._admin_callback = None
        self._admin_owner = None
        self._attempt += 1
        self.stop_loopback()
        self._client = None
        self._mode = AUTH_LOGIN
        self._fallback = False
        self.emit('changed')
        if callback is not None:
            callback(granted)
        if message is not None:
            self.emit('message', message)
