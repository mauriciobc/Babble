import base64
import hashlib
import json
import math
import random
import re
import secrets as _secrets
import time
import urllib.parse
from email.utils import parsedate_to_datetime


import gi

gi.require_version('Soup', '3.0')
gi.require_version('Secret', '1')

try:
    from .i18n import gettext as _, ngettext  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import GLib, GObject, Gio, Secret, Soup  # noqa: E402

# Reverse proxies routinely refuse the default libsoup User-Agent
# ("libsoup/3.0") outright: ursal.zone's nginx answers POST /api/v1/apps
# with 403 to it while the very same request carrying an application
# User-Agent returns 200. Announcing the client on the session fixes every
# call at once, uploads included, since they share the one session.
USER_AGENT = 'Babble/%s (+https://github.com/mauriciobc/babble)'

# Adw.Toast renders its title as plain text, so an untrusted server body
# must reach it with nothing that Pango or a widget could interpret and
# nothing long enough to blow out the toast. HTML is stripped and the
# result is bounded to a single short line; no markup escaping happens
# here, because escaping would show a literal '&amp;' to the reader.
MAX_ERROR_SUMMARY = 240

# RFC 7636 code_verifier: 43-128 chars from the unreserved set. 32
# random bytes encode to 43 characters, the shortest legal value.
CODE_VERIFIER_BYTES = 32

# libsoup's session timeout defaults to 0, meaning "wait forever". A
# stalled peer would then leave the Gio.Task pending for the life of the
# process, so every session that talks to a server sets this instead.
HTTP_TIMEOUT_SECONDS = 30

# A 429 without a usable Retry-After still pauses the pollers, so the
# app stops deepening the limit instead of retrying into it.
DEFAULT_RATE_LIMIT_SECONDS = 60
MAX_RATE_LIMIT_SECONDS = 3600

# Mastodon's own default per-post attachment count, used when an
# instance does not advertise one (Status::MEDIA_ATTACHMENTS_LIMIT).
DEFAULT_MAX_MEDIA_ATTACHMENTS = 4

# libsoup 3 does not name HTTP 429, so the wire value is spelled out.
HTTP_TOO_MANY_REQUESTS = 429

# Transient REST failures: same backoff shape as streaming.py.
REST_RETRY_MAX = 3
REST_BACKOFF_START = 1.0
REST_BACKOFF_MAX = 30.0

# RFC 9110 9.2.2: PUT, DELETE and the safe methods (GET, HEAD, OPTIONS,
# TRACE) are idempotent -- repeating one has the same effect on the server
# as sending it once. POST is neither, and the same section says a client
# SHOULD NOT retry a non-idempotent request automatically. That is the
# difference between a harmless repeat and a duplicate: a
# POST /statuses whose response was lost *after* the server committed it
# looked exactly like a failure, so retrying asked for the post a second
# time and the reader got one status while the instance held two.
IDEMPOTENT_METHODS = ('GET', 'HEAD', 'OPTIONS', 'TRACE', 'PUT', 'DELETE')

# A parsed number below this is not an instant: 2000-01-01T00:00:00Z. It
# rejects the handful of strings that read as floats without being times
# -- 'nan', 'inf', '-1', '0' -- which would otherwise reach int() in the
# error path and raise there.
EARLIEST_PLAUSIBLE_EPOCH = 946684800

# How far the server's Date header may claim to run from ours before it
# is ignored (RFC 9110 6.6.1 does not require a server's Date to be
# true). Two days is far past any real skew and far short of letting a
# broken header stall the client for hours.
MAX_CLOCK_SKEW_SECONDS = 172800


def rest_should_retry(method, status, transport_error=False):
    """Whether a failed request may be retried automatically.

    *method* decides it first; see :data:`IDEMPOTENT_METHODS`.
    """
    if str(method).upper() not in IDEMPOTENT_METHODS:
        return False
    if transport_error:
        return True
    if status is None:
        return False
    return 500 <= status < 600


def rest_backoff_seconds(attempt):
    """Seconds to wait before retry *attempt* (0-based), with jitter."""
    delay = min(REST_BACKOFF_START * 2 ** attempt, REST_BACKOFF_MAX)
    return delay + random.uniform(0, delay / 4)

# <script>/<style> payloads first: stripping only the tags would leave
# their source in the middle of the sentence.
_UNPRINTABLE_BLOCK = re.compile(r'(?is)<(script|style)\b.*?</\1\s*>')
_TAG = re.compile(r'(?s)<[^>]*>')
_SPACES = re.compile(r'\s+')


def summarize_error(error):
    """A server failure as a safe, bounded plain-text line.

    Takes either a :class:`GLib.Error` or the raw response text, because
    both are what callers have: a failed request arrives as an error, and
    a body that could not be parsed arrives as a string. The four
    ``error_text`` helpers in lists.py and admin.py were each doing this
    unwrapping themselves.

    The result is literal text: tags are stripped and it is truncated,
    but every other character is left as the server sent it. Consumers
    rendering plain text (``Adw.Toast`` with ``use_markup=False``) use
    it as-is; those rendering Pango markup must pass it through
    :func:`summarize_error_markup`, or a bare ``&`` in the body fails
    the markup parse and blanks the label.
    """
    if error is not None and not isinstance(error, str):
        error = getattr(error, 'message', None) or str(error)
    text = _UNPRINTABLE_BLOCK.sub(' ', error or '')
    text = _TAG.sub(' ', text)
    text = _SPACES.sub(' ', text).strip()
    if len(text) > MAX_ERROR_SUMMARY:
        text = text[:MAX_ERROR_SUMMARY - 1].rstrip() + '…'
    return text


def summarize_error_markup(error):
    """:func:`summarize_error` escaped for a markup-rendering widget.

    ``Adw.StatusPage`` descriptions and descriptions built on them are
    Pango markup, so an unescaped ``&`` makes the parse fail and the
    whole label come out empty -- an error the user cannot see.
    """
    return GLib.markup_escape_text(summarize_error(error))


def log_deprecation(method, path, headers):
    """Surface an RFC 9745 ``Deprecation`` notice in development.

    Mastodon 4.4+ marks endpoints it intends to retire with a
    ``Deprecation`` header (and sometimes ``Sunset``). Pointing them out
    under ``G_MESSAGES_DEBUG=Babble`` lets the client be migrated before
    a release removes the route; an ordinary run stays quiet. Returns
    the message it logged, or None when the response carried no notice.
    """
    notice = headers.get_one('Deprecation')
    if not notice:
        return None
    message = '%s %s is deprecated by the server (migrate before %s)' % (
        method, path, headers.get_one('Sunset') or notice)
    GLib.log_variant('Babble', GLib.LogLevelFlags.LEVEL_DEBUG,
                     GLib.Variant('a{sv}', {'MESSAGE': GLib.Variant(
                         's', message)}))
    return message


def generate_code_verifier():
    """A fresh RFC 7636 ``code_verifier`` (43 chars, unreserved set)."""
    return _secrets.token_urlsafe(CODE_VERIFIER_BYTES)


def code_challenge(verifier):
    """The S256 ``code_challenge`` for *verifier*."""
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def new_state():
    """A fresh ``state`` value, used to bind the callback to this attempt."""
    return _secrets.token_urlsafe(CODE_VERIFIER_BYTES)

OAUTH_REDIRECT = 'babble://oauth/callback'
OOB_REDIRECT = 'urn:ietf:wg:oauth:2.0:oob'
REDIRECT_URIS = OAUTH_REDIRECT + ' ' + OOB_REDIRECT
# Where a loopback listener answers the redirect. Mastodon accepts a
# plain-http redirect URI and compares it exactly against the registered
# set, so the port the listener got is registered for that one login.
LOOPBACK_CALLBACK_PATH = '/callback'


def loopback_redirect(port):
    """The redirect URI of a listener on 127.0.0.1 *port*."""
    return 'http://127.0.0.1:%d%s' % (port, LOOPBACK_CALLBACK_PATH)


# The one scope set the ordinary login asks for. ``follow`` was the
# third member until Mastodon 3.5 deprecated it: every granular scope it
# granted (read/write of follows, blocks and mutes) is reached through
# ``read``/``write``, so naming it now only puts a deprecated scope on
# the instance's consent screen.
OAUTH_SCOPES = 'read write'

# The moderation scopes, asked for only by the admin dashboard and only
# once the account's role says it could use them. A registration that
# carries them is harmless -- Mastodon grants nothing until an
# authorization names a scope -- but an authorization that names them
# puts "All administrative functions" on the consent screen, so every
# ordinary login must stay on OAUTH_SCOPES.
ADMIN_SCOPES = OAUTH_SCOPES + ' admin:read admin:write'

# Shown as the application's own homepage on the instance's consent
# screen. It is Babble's address, never the server the user is signing
# in to.
APP_WEBSITE = 'https://github.com/mauriciobc/babble'

# Keyring kind of the OAuth client credentials. They are keyed per
# account as well as per instance: every authorization registers its own
# application, so two accounts on one instance hold different credential
# pairs and sharing one entry would make the older token unrevokable.
CLIENT_CREDENTIALS_KIND = 'oauth-client:'

SECRET_SCHEMA = Secret.Schema.new('io.github.mauriciobc.Babble',
                                  Secret.SchemaFlags.NONE,
                                  {
                                      'instance': Secret.SchemaAttributeType.STRING,
                                      'kind': Secret.SchemaAttributeType.STRING,
                                  })


def normalize_instance(url):
    """The canonical https origin for *url*.

    A plaintext ``http://`` instance is refused outright: the bearer
    token and the client secret would cross the network in the clear.
    """
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    url = url.rstrip('/')
    scheme, sep, rest = url.partition('://')
    if not sep:
        raise ValueError(_('Invalid server address: %s') % url)
    if scheme.lower() != 'https':
        raise ValueError(_('Babble only connects over HTTPS: %s') % url)
    return url

# Lazily built by _secret_service(); see there for why one proxy is
# shared rather than created per call.
_service = None


def _secret_service():
    """The session-wide keyring proxy.

    Built once and reused: the async calls must be finished on the very
    proxy that started them, and the module-level ``Secret.password_*``
    helpers hide theirs, so the calls go through the service directly.
    """
    global _service
    if _service is None:
        _service = Secret.Service.get_sync(Secret.ServiceFlags.NONE, None)
    return _service


def store_secret_async(instance, kind, payload, callback=None,
                       cancellable=None):
    """Write *payload* to the keyring without blocking the main loop.

    *callback* receives None once the entry is stored, or the GLib.Error
    that stopped it. A keyring that will not take the secret is not a
    transport failure, but the caller has to learn about it: an account
    recorded without a recoverable token is an account that silently
    signs the user out on the next start.
    """

    def done(service, result, _user_data):
        error = None
        try:
            service.store_finish(result)
        except GLib.Error as failure:
            error = failure
        if callback is not None:
            callback(error)

    _secret_service().store(SECRET_SCHEMA,
                            {'instance': instance, 'kind': kind},
                            Secret.COLLECTION_DEFAULT,
                            'Babble: %s' % instance,
                            Secret.Value.new(payload, len(payload),
                                              'text/plain'),
                            cancellable, done, None)


def load_secret_async(instance, kind, callback, cancellable=None):
    """Read a keyring entry on the main loop.

    *callback* is ``callback(value, error=None)``. ``value`` is the secret
    text, or None when the entry is absent. A keyring that cannot be read
    (locked, missing service, D-Bus failure) is not an absence: *error* is
    the ``GLib.Error`` and *value* is None. Callers that only care about
    the value may keep a one-argument callback; the error defaults to
    None when omitted by a stub.
    """

    def done(service, result, _user_data):
        try:
            secret = service.lookup_finish(result)
        except GLib.Error as failure:
            callback(None, failure)
            return
        callback(secret.get_text() if secret is not None else None, None)

    _secret_service().lookup(SECRET_SCHEMA,
                             {'instance': instance, 'kind': kind},
                             cancellable, done, None)


def clear_secret_async(instance, kind, callback=None, cancellable=None):
    """Remove a keyring entry without blocking the main loop.

    *callback* receives None, or the GLib.Error that stopped it. An
    entry that will not delete leaves a live token behind, so the caller
    is told rather than left to assume the removal took.
    """

    def done(service, result, _user_data):
        error = None
        try:
            service.clear_finish(result)
        except GLib.Error as failure:
            error = failure
        if callback is not None:
            callback(error)

    _secret_service().clear(SECRET_SCHEMA,
                            {'instance': instance, 'kind': kind},
                            cancellable, done, None)



_LINK_PART = re.compile(r'<([^>]*)>([^<]*)')
_LINK_REL = re.compile(r'(?:^|[;\s])rel\s*=\s*(?:"([^"]*)"|([^\s;,]+))',
                       re.IGNORECASE)


def parse_link_header(value):
    """``{'next': {...query params}, 'prev': {...}}`` from an RFC 8288
    Link header, the way Mastodon paginates bookmarks, favourites,
    follow lists and everything else not keyed by status id."""
    links = {}
    for url, params in _LINK_PART.findall(value or ''):
        found = _LINK_REL.search(params)
        if not found:
            continue
        query = urllib.parse.urlsplit(url).query
        # RFC 8288: rel is case-insensitive, may sit after other
        # parameters, and may hold several space-separated relations.
        for rel in (found.group(1) or found.group(2)).lower().split():
            links[rel] = dict(urllib.parse.parse_qsl(query))
    return links


def parse_reset_instant(value):
    """``X-RateLimit-Reset`` as a POSIX timestamp, or None.

    Mastodon builds it with ``(request_time + reset_period_offset)
    .iso8601(6)`` -- app/controllers/concerns/api/rate_limit_headers.rb --
    so it arrives as an ISO-8601 instant (``2026-10-16T11:20:00.701542Z``,
    the form observed on mastodon.social), which ``int()`` can never read.
    Every 429 therefore fell through to :data:`DEFAULT_RATE_LIMIT_SECONDS`,
    and a client that ignored the server's real window deepened the limit
    it had just been told about. Older servers and forks send a Unix epoch
    and some send a fractional one, so both are read; the docs call this
    only "Timestamp when your rate limit will reset" and never say which
    format. None means the header was absent or is not a timestamp.
    """
    text = (value or '').strip()
    if not text:
        return None
    try:
        epoch = float(text)
    except ValueError:
        pass
    else:
        # 'nan' and 'inf' are floats and not instants; math.isfinite keeps
        # both out, and int(inf) would raise inside the error path.
        return epoch if math.isfinite(epoch) \
            and epoch >= EARLIEST_PLAUSIBLE_EPOCH else None
    # PyGObject raises TypeError from the constructor when GLib returns
    # NULL, rather than handing back None, so an unparsable string has to
    # be caught: this runs on a header a server chose, inside the error
    # path of _on_response, where an exception escapes into libsoup and
    # leaves the caller's Gio.Task unfinished.
    try:
        stamp = GLib.DateTime.new_from_iso8601(text)
    except TypeError:
        return None
    return stamp.to_unix() if stamp is not None else None


def server_clock_offset(date_header, now):
    """Seconds the server's clock runs ahead of ours, from its ``Date``.

    RFC 9110 6.6.1 requires an origin server to send ``Date`` on every
    2xx, 3xx and 4xx, and the rate-limit reset is an instant on the
    *server's* clock, so the difference between the two is one
    subtraction. Zero when the header is missing or unparsable, and zero
    when it claims more than :data:`MAX_CLOCK_SKEW_SECONDS` of drift,
    both of which leave the caller no worse off than not correcting.
    """
    try:
        server_now = parsedate_to_datetime(date_header).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0
    offset = server_now - now
    if abs(offset) > MAX_CLOCK_SKEW_SECONDS:
        return 0.0
    return offset


class MastodonClient(GObject.Object):
    """Async Mastodon REST client for a single instance.

    Every operation is a pair ``<name>_async``/``<name>_finish`` following
    the Gio.Task convention; results are the JSON dictionaries documented
    at docs.joinmastodon.org, errors are raised as GLib.Error.
    """

    __gtype_name__ = 'MastodonClient'

    def __init__(self, instance_url, token=None, **kwargs):
        super().__init__(**kwargs)
        self.instance = normalize_instance(instance_url)
        self.api_base = self.instance + '/api/v1'
        self.token = token
        self.client_id = None
        self.client_secret = None
        self.instance_info = None
        self._instance_info_waiters = []
        self._instance_info_in_flight = False
        self._code_verifier = None
        self._state = None
        # The redirect the authorization in flight named; only a callback
        # on that one answers it.
        self._redirect_uri = OAUTH_REDIRECT
        self._rate_limited_until = 0.0
        self._session = Soup.Session()
        try:
            import babble as _app_meta
            _version = getattr(_app_meta, 'VERSION', '0.1')
        except ImportError:
            _version = '0.1'
        self._session.props.user_agent = USER_AGENT % _version
        self._session.set_timeout(HTTP_TIMEOUT_SECONDS)
        self._pending = {}

    def rate_limited(self, now=None):
        """True while a 429 from the instance is still in force."""
        return (self._rate_limited_until or 0) > (
            GLib.get_monotonic_time() / 1000000.0
            if now is None else now)

    def retry_after_seconds(self):
        """How much longer this client is rate limited, in seconds."""
        remaining = self._rate_limited_until - (
            GLib.get_monotonic_time() / 1000000.0)
        return int(max(0, remaining) + 0.5)

    @GObject.Property(type=str, default='')
    def instance_url(self):
        return self.instance

    # Transport

    def _spawn(self, task, message):
        self._pending[task] = message
        self._session.send_and_read_async(message,
                                          GLib.PRIORITY_DEFAULT,
                                          task.get_cancellable(),
                                          self._on_response,
                                          task)

    def _note_rate_limit(self, headers):
        """Record how long a 429 says to stop asking for.

        Mastodon's throttled_responder
        (config/initializers/rack_attack.rb) builds the 429 from
        Content-Type and the three X-RateLimit-* headers and nothing
        else, so ``Retry-After`` never arrives and the reset branch is
        the only one that runs. That branch used to read an ISO-8601
        instant as an integer, which never parses, so the wait was
        always :data:`DEFAULT_RATE_LIMIT_SECONDS` whatever the server
        said. :func:`parse_reset_instant` takes either spelling, and
        :func:`server_clock_offset` corrects the instant against the
        response's own ``Date``, without which a clock two hours out
        turns a ten-minute window into a one-second one.
        """
        now = time.time()
        retry = headers.get_one('Retry-After') or ''
        seconds = None
        try:
            # RFC 9110 10.2.3 also allows an HTTP-date here. Mastodon's
            # throttled_responder never sends the header at all, so the
            # date form is not read: an unparsable value falls through
            # to the reset Mastodon does send, which is the more precise
            # of the two anyway.
            seconds = max(1, min(int(retry.strip()), MAX_RATE_LIMIT_SECONDS))
        except (TypeError, ValueError):
            reset = parse_reset_instant(headers.get_one('X-RateLimit-Reset'))
            if reset is not None:
                offset = server_clock_offset(headers.get_one('Date'), now)
                seconds = int(reset - offset - now)
                seconds = max(1, min(seconds, MAX_RATE_LIMIT_SECONDS))
        if seconds is None:
            seconds = DEFAULT_RATE_LIMIT_SECONDS
        self._rate_limited_until = (GLib.get_monotonic_time() / 1000000.0
                                    + seconds)
        return seconds

    def _build_message(self, method, path, params=None, form=None, auth=True,
                       json_body=None):
        if path.startswith('http'):
            uri = path
        elif path.startswith(('/api/', '/oauth/')):
            uri = self.instance + path
        else:
            uri = self.api_base + path
        if params:
            uri += ('&' if '?' in uri else '?') + urllib.parse.urlencode(
                params, doseq=True)
        message = Soup.Message.new(method, uri)
        if message is None:
            return None
        if auth and self.token:
            message.get_request_headers().append('Authorization',
                                                 'Bearer ' + self.token)
        if json_body is not None:
            message.set_request_body_from_bytes(
                'application/json',
                GLib.Bytes.new(json.dumps(json_body).encode()))
        elif form is not None:
            # None is dropped rather than encoded: urlencode renders it as
            # the four letters "None", which a server reads as a secret
            # that happens to be that word.
            body = urllib.parse.urlencode(
                {name: value for name, value in form.items()
                 if value is not None}, doseq=True)
            message.set_request_body_from_bytes(
                'application/x-www-form-urlencoded',
                GLib.Bytes.new(body.encode()))
        return message

    def _rest_retry(self, task):
        cancellable = task.get_cancellable()
        if cancellable is not None and cancellable.is_cancelled():
            task.return_error(GLib.Error(
                _('Operation was cancelled'), 'g-io-error-quark',
                int(Gio.IOErrorEnum.CANCELLED)))
            return GLib.SOURCE_REMOVE
        spec = getattr(task, '_rest_spec', None)
        if spec is None:
            task.return_error(GLib.Error(_('Request failed'),
                                         'mastodon-http-error-quark', 0))
            return GLib.SOURCE_REMOVE
        method, path, params, form, auth, json_body = spec
        message = self._build_message(method, path, params, form, auth,
                                      json_body)
        if message is None:
            task.return_error(GLib.Error(
                _('Invalid address'), 'mastodon-http-error-quark', 0))
            return GLib.SOURCE_REMOVE
        self._spawn(task, message)
        return GLib.SOURCE_REMOVE

    def _maybe_retry(self, task, status=None, transport_error=False):
        # Only real Gio.Tasks from _request_async carry a spec; fakes in
        # unit tests must fail through without a scheduled retry.
        spec = getattr(task, '_rest_spec', None)
        if spec is None:
            return False
        cancellable = task.get_cancellable()
        if cancellable is not None and cancellable.is_cancelled():
            return False
        attempt = getattr(task, '_rest_attempt', 0)
        if not rest_should_retry(spec[0], status, transport_error) \
                or attempt >= REST_RETRY_MAX:
            return False
        task._rest_attempt = attempt + 1
        delay_ms = int(rest_backoff_seconds(attempt) * 1000)
        GLib.timeout_add(delay_ms, self._rest_retry, task)
        return True

    def _on_response(self, _session, result, task):
        message = self._pending.pop(task, None)
        try:
            body = self._session.send_and_read_finish(result)
        except GLib.Error as e:
            # Cancellation is not a transport blip: retrying only delays
            # the error while each attempt aborts instantly.
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                task.return_error(e)
                return
            if self._maybe_retry(task, transport_error=True):
                return
            task.return_error(e)
            return
        text = body.get_data().decode('utf-8', 'replace')
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {'error': text}
        status = message.get_status()
        headers = message.get_response_headers()
        log_deprecation(message.get_method(), message.get_uri().get_path(),
                        headers)
        if 200 <= status < 300 and status != Soup.Status.NONE:
            link = headers.get_one('Link') or ''
            task.return_value(GLib.Variant('s', json.dumps(
                {'data': data, 'link': link})))
        else:
            error = data.get('error') if isinstance(data, dict) else None
            if status == HTTP_TOO_MANY_REQUESTS:
                seconds = self._note_rate_limit(headers)
                task.return_error(GLib.Error(
                    ngettext('Rate limited by this server. Trying again in '
                             '%d second.',
                             'Rate limited by this server. Trying again in '
                             '%d seconds.', seconds) % seconds,
                    'mastodon-http-error-quark', status))
                return
            # Summarised here, at the one place an error body enters the
            # app, so every consumer inherits the bound: a non-JSON
            # response (an nginx or CDN error page) would otherwise
            # travel whole inside the GLib.Error, and a single consumer
            # that renders it as markup blanks its label or renders the
            # server's markup. summarize_error is idempotent, so the
            # consumers that still sanitize are unaffected.
            if self._maybe_retry(task, status=status):
                return
            task.return_error(GLib.Error(
                summarize_error(error) or Soup.status_get_phrase(status),
                'mastodon-http-error-quark', status))

    def _envelope(self, result):
        value = result.propagate_value()
        if isinstance(value, tuple):
            value = value[1]
        return json.loads(value.get_string())

    def _finish(self, result):
        from .entities import lift
        return lift(self._envelope(result)['data'])

    def _request_async(self, method, path, params=None, form=None,
                       auth=True, callback=None, cancellable=None,
                       json_body=None):
        """Queue one API call. ``path`` is relative to /api/v1 unless it
        starts with ``/api/`` or ``/oauth/`` (instance root) or ``http``.
        The bearer token goes along whenever one is set, so every
        response carries the viewer's state (favourited, filtered…)."""
        task = Gio.Task.new(self, cancellable, callback)
        task._rest_spec = (method, path, params, form, auth, json_body)
        task._rest_attempt = 0
        message = self._build_message(method, path, params, form, auth,
                                      json_body)
        if message is None:
            task.return_error(GLib.Error(_('Invalid address: %s') % path,
                                         'mastodon-http-error-quark', 0))
            return task
        self._spawn(task, message)
        return task

    # Generic API access

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        """Any REST call, e.g. ``request_async('GET', '/bookmarks',
        params={'limit': 20}, callback=cb)``. ``path`` is relative to
        /api/v1; pass '/api/v2/…' for v2 routes. Finish with
        :meth:`request_finish` or :meth:`request_finish_paged`."""
        return self._request_async(method, path, params=params, form=form,
                                   json_body=json_body, callback=callback,
                                   cancellable=cancellable)

    def request_finish(self, result):
        """The decoded JSON body; raises GLib.Error on HTTP errors."""
        return self._finish(result)

    def request_finish_paged(self, result):
        """``(data, next_params, prev_params)``: the JSON body plus the
        query parameters of the ``Link`` header's ``rel="next"`` (older
        page) and ``rel="prev"`` (newer page) URLs, None when absent."""
        from .entities import lift
        envelope = self._envelope(result)
        links = parse_link_header(envelope.get('link', ''))
        return lift(envelope['data']), links.get('next'), links.get('prev')

    def instance_info_async(self, callback=None, cancellable=None):
        """GET /api/v2/instance; the result is kept on
        ``self.instance_info`` by :meth:`instance_info_finish`.

        Concurrent callers share one in-flight request (ARCH-09)."""
        if callback is not None:
            self._instance_info_waiters.append((callback, cancellable))
        if self.instance_info is not None:
            self._complete_instance_info_waiters(None)
            return None
        if self._instance_info_in_flight:
            return None
        self._instance_info_in_flight = True

        def loaded(client, result, _user_data=None):
            error = None
            try:
                client.instance_info_finish(result)
            except GLib.Error as failure:
                error = failure
            client._instance_info_in_flight = False
            client._complete_instance_info_waiters(error)

        return self._request_async('GET', '/api/v2/instance',
                                   callback=loaded, cancellable=cancellable)

    def _complete_instance_info_waiters(self, error):
        waiters = self._instance_info_waiters
        self._instance_info_waiters = []
        payload = None
        if error is None and self.instance_info is not None:
            payload = GLib.Variant(
                's', json.dumps({'data': self.instance_info, 'link': ''}))
        for callback, cancellable in waiters:
            task = Gio.Task.new(self, cancellable, callback)
            if error is not None:
                task.return_error(error)
            else:
                task.return_value(payload)

    def instance_info_finish(self, result):
        self.instance_info = self._finish(result)
        return self.instance_info

    def markers_async(self, timelines, callback=None, cancellable=None):
        """GET /api/v1/markers for the named timelines."""
        params = [('timeline[]', name) for name in timelines]
        return self._request_async('GET', '/markers', params=params,
                                   callback=callback, cancellable=cancellable)

    def markers_finish(self, result):
        return self._finish(result)

    def max_characters(self):
        """Post length limit of the instance, 500 until it is known."""
        info = self.instance_info or {}
        statuses = (info.get('configuration') or {}).get('statuses') or {}
        return statuses.get('max_characters') or 500

    def _media_configuration(self):
        """``configuration.media_attachments`` or {} when unsaid."""
        config = (self.instance_info or {}).get('configuration') or {}
        return config.get('media_attachments') or {}

    def max_media_attachments(self):
        """How many attachments one post may carry here.

        Mastodon's own default when the instance is silent about it:
        Status::MEDIA_ATTACHMENTS_LIMIT is 4, so an instance that raises
        or lowers it is honoured and one that says nothing is not
        blocked.
        """
        statuses = (self.instance_info or {}).get('configuration') or {}
        statuses = (statuses.get('statuses') or {})
        try:
            limit = int(statuses.get('max_media_attachments'))
        except (TypeError, ValueError):
            return DEFAULT_MAX_MEDIA_ATTACHMENTS
        return limit if limit > 0 else DEFAULT_MAX_MEDIA_ATTACHMENTS

    def media_size_limits(self):
        """``(image_bytes, video_bytes)`` from the instance, either None
        where it advertised nothing.

        Both are enforced server-side: MediaAttachment validates a file
        against VIDEO_LIMIT for video, gifv and audio and IMAGE_LIMIT
        otherwise, and strictly, so a file of exactly the limit is
        refused. None means no limit to enforce, which is the safe
        direction to fail in.
        """
        media = self._media_configuration()

        def limit(name):
            try:
                value = int(media[name])
            except (KeyError, TypeError, ValueError):
                return None
            return value if value > 0 else None
        return limit('image_size_limit'), limit('video_size_limit')

    def supported_media_types(self):
        """``configuration.media_attachments.supported_mime_types``, or
        None when there is nothing worth filtering a file chooser on.

        None also covers the instance that advertises the single type
        ``application/octet-stream``: as a chooser filter that hides
        every file a reader actually has.
        """
        advertised = self._media_configuration().get('supported_mime_types')
        if not isinstance(advertised, list):
            return None
        types = [t for t in advertised
                 if isinstance(t, str) and t != 'application/octet-stream']
        return types or None

    def mastodon_api_version(self):
        """The instance's ``api_versions.mastodon``, or None when it says
        nothing at all.

        Three things are conflated by reading this as an int and getting
        0 on a server that does not carry the key: a Mastodon older than
        4.3 (where the key first appears), a Mastodon that old and then
        some, and a server that is not Mastodon at all. A None keeps
        those apart -- "no answer" versus "answered 0".
        """
        versions = (self.instance_info or {}).get('api_versions') or {}
        try:
            return int(versions.get('mastodon'))
        except (TypeError, ValueError):
            return None

    def is_supported(self, min_version, when_unknown=False):
        """Whether the instance has the Mastodon *min_version* and later
        feature the caller is about to use.

        The answer is None when the server says nothing about
        api_versions.mastodon: without it, the feature is unproven rather
        than absent. *when_unknown* is what a caller does with that, and
        lives here rather than at every call site so a caller states its
        policy once, in the natural reading order, instead of spelling
        out ``is True`` and a comment.

        A Mastodon feature a fork does not ship, a fork feature Mastodon
        does not, and an old Mastodon are three different answers, and
        only the first two are ones the instance can tell us about.
        """
        version = self.mastodon_api_version()
        if version is None:
            return when_unknown
        return version >= min_version

    # OAuth

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
        client_id = data.get('client_id')
        # A GLib.Error, not a KeyError: the contract this class documents
        # is that every finish raises GLib.Error, and an index into the
        # payload raised through the GObject callback instead, leaving
        # the login waiting on a browser step that was never going to
        # arrive. REST::CredentialApplicationSerializer declares
        # client_id, client_secret and client_secret_expires_at and
        # fills them from Doorkeeper's uid and secret, and the docs say
        # "Mastodon only supports provisioning confidential clients, i.e.,
        # you will always receive a client_secret" -- so this guards
        # against a server that does not, not a shape to expect.
        if not client_id:
            raise GLib.Error(_('The server did not return an application id.'),
                             'mastodon-http-error-quark', 0)
        self.client_id = client_id
        self.client_secret = data.get('client_secret')
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
        """The authorization *code* from a redirect, or None.

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
        params = urllib.parse.parse_qs(parts.query)
        codes = params.get('code')
        states = params.get('state')
        code = codes[0] if codes else None
        state = states[0] if states else None
        if code is None or state is None or state != self._state:
            return None
        return code

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
        return self._finish(result)['access_token']

    # Accounts

    def verify_credentials_async(self, callback=None, cancellable=None):
        return self._request_async('GET', '/accounts/verify_credentials',
                                   callback=callback,
                                   cancellable=cancellable)

    def verify_credentials_finish(self, result):
        return self._finish(result)

    def lookup_account_async(self, acct, callback=None, cancellable=None):
        return self._request_async('GET', '/accounts/lookup',
                                   params={'acct': acct},
                                   callback=callback, cancellable=cancellable)

    def lookup_account_finish(self, result):
        return self._finish(result)

    def account_statuses_async(self, account_id, params=None, callback=None,
                               cancellable=None):
        return self._request_async('GET', '/accounts/%s/statuses'
                                   % account_id, params=params,
                                   callback=callback,
                                   cancellable=cancellable)

    def account_statuses_finish(self, result):
        return self._finish(result)

    def relationships_async(self, account_ids, callback=None,
                            cancellable=None):
        return self._request_async(
            'GET', '/accounts/relationships',
            params={'id[]': account_ids}, callback=callback,
            cancellable=cancellable)

    def relationships_finish(self, result):
        data = self._finish(result)
        return data[0] if data else {}


    # Timelines and notifications

    def timelines_home_async(self, params=None, callback=None, cancellable=None):
        return self._request_async('GET', '/timelines/home', params=params,
                                   callback=callback,
                                   cancellable=cancellable)

    def timelines_home_finish(self, result):
        return self._finish(result)

    def timelines_public_async(self, params=None, callback=None, cancellable=None):
        return self._request_async('GET', '/timelines/public', params=params,
                                   callback=callback, cancellable=cancellable)

    def timelines_public_finish(self, result):
        return self._finish(result)

    def trends_statuses_async(self, params=None, callback=None,
                              cancellable=None):
        return self._request_async('GET', '/trends/statuses', params=params,
                                   callback=callback,
                                   cancellable=cancellable)

    def trends_statuses_finish(self, result):
        return self._finish(result)

    def notifications_async(self, params=None, callback=None, cancellable=None):
        """GET /api/v1/notifications.

        Pagination params: ``max_id`` (older page), ``min_id`` (newer
        page), ``limit``; ``types`` filters by notification type.
        """
        return self._request_async('GET', '/notifications', params=params,
                                   callback=callback,
                                   cancellable=cancellable)

    def notifications_finish(self, result):
        return self._finish(result)

    def notifications_unread_count_async(self, params=None, callback=None,
                                         cancellable=None):
        """GET /api/v1/notifications/unread_count (Mastodon 4.0+).
        ``params`` accepts the same ``types[]``/``exclude_types[]`` as the
        list endpoint, so the badge counts what the list shows."""
        return self._request_async('GET', '/notifications/unread_count',
                                   params=params,
                                   callback=callback,
                                   cancellable=cancellable)

    def notifications_unread_count_finish(self, result):
        return self._finish(result).get('count', 0)

    def notifications_clear_async(self, callback=None, cancellable=None):
        """POST /api/v1/notifications/clear: delete every notification."""
        return self._request_async('POST', '/notifications/clear',
                                   callback=callback,
                                   cancellable=cancellable)

    def notifications_clear_finish(self, result):
        return self._finish(result)

    def notifications_mark_read_async(self, last_read_id, callback=None,
                                      cancellable=None):
        """POST /api/v1/markers: store ``notifications.last_read_id`` so
        unread_count drops to 0."""
        return self._request_async('POST', '/markers',
                                   form={'notifications[last_read_id]':
                                         last_read_id},
                                   callback=callback,
                                   cancellable=cancellable)

    def notifications_mark_read_finish(self, result):
        return self._finish(result)

    # Statuses

    def status_context_async(self, status_id, callback=None,
                             cancellable=None):
        return self._request_async('GET', '/statuses/%s/context' % status_id,
                                   callback=callback,
                                   cancellable=cancellable)

    def status_context_finish(self, result):
        data = self._finish(result)
        return data.get('ancestors', []), data.get('descendants', [])

    def post_status_async(self, text, params=None, callback=None,
                          cancellable=None):
        """POST /api/v1/statuses as JSON, so nested values (``poll``
        options, ``media_ids`` arrays) keep their structure.

        No ``Idempotency-Key`` is sent, and that is deliberate.
        Mastodon reads one (api/v1/statuses_controller.rb) and keys it
        per account in Redis for an hour (PostStatusService
        #with_idempotency), but on a repeat it raises IdempotencyError
        carrying the *id string*, which the controller then hands to
        the status serializer. So the dedup does not make a retry safe --
        it makes the retry return something that is not a status. The
        reply is left to :data:`IDEMPOTENT_METHODS` instead.
        """
        body = {'status': text}
        if params:
            body.update(params)
        return self._request_async('POST', '/statuses', json_body=body,
                                   callback=callback, cancellable=cancellable)

    def post_status_finish(self, result):
        return self._finish(result)

    def edit_status_async(self, status_id, text, params=None, callback=None,
                          cancellable=None):
        """PUT /api/v1/statuses/:id as JSON, mirroring
        :meth:`post_status_async` (docs.joinmastodon.org/methods/statuses)."""
        body = {'status': text}
        if params:
            body.update(params)
        return self._request_async('PUT', '/statuses/%s' % status_id,
                                   json_body=body, callback=callback,
                                   cancellable=cancellable)

    def edit_status_finish(self, result):
        return self._finish(result)

    def multipart_async(self, method, path, fields=None, files=None,
                        callback=None, cancellable=None):
        """multipart/form-data request: ``fields`` maps names to strings
        (lists repeat the key), ``files`` maps names to
        ``(bytes, filename, content_type)``. Finish with
        :meth:`request_finish`.

        A file's bytes may be given as a :class:`GLib.Bytes` as well as a
        ``bytes``, which is what an attachment read by the caller should
        be: :meth:`GLib.Bytes.new` copies, so passing raw bytes holds the
        payload twice before libsoup makes its own copy."""
        task = Gio.Task.new(self, cancellable, callback)
        uri = self.instance + path if path.startswith('/api/') \
            else self.api_base + path
        message = Soup.Message.new(method, uri)
        multipart = Soup.Multipart.new('multipart/form-data')
        for name, (data, filename, content_type) in (files or {}).items():
            multipart.append_form_file(
                name, filename, content_type,
                data if isinstance(data, GLib.Bytes) else GLib.Bytes.new(data))
        for name, value in (fields or {}).items():
            for item in value if isinstance(value, (list, tuple)) \
                    else [value]:
                multipart.append_form_string(name, str(item))
        # libsoup 3 names this to_message, and it returns the encoded
        # body rather than attaching it. It writes the chosen boundary
        # into the headers it is handed, so the Content-Type has to be
        # read back off them: handing it the bare type strips the
        # boundary, and a server cannot split a part it has no boundary
        # for. (Calling it finish_message, as this did, raised
        # AttributeError on every attachment and nothing caught it: the
        # composer tests replace this whole method.)
        body = multipart.to_message(message.get_request_headers())
        message.set_request_body_from_bytes(
            message.get_request_headers().get_one('Content-Type'), body)
        if self.token:
            message.get_request_headers().append('Authorization',
                                                 'Bearer ' + self.token)
        self._spawn(task, message)
        return task

    def media_upload_async(self, file_bytes, filename, content_type,
                           description=None, callback=None,
                           cancellable=None):
        """Upload one attachment to ``POST /api/v2/media`` (docs.
        joinmastodon.org/methods/media), multipart/form-data with a
        ``file`` part and optional ``description`` alt text.

        *file_bytes* may be a :class:`GLib.Bytes` or a ``bytes``; pass
        the former when the caller already holds one, so the attachment
        is not copied twice on the way out.

        The v2 route answers HTTP 202 with a ``url`` of null while the
        full-size file is processed in the background; the composer polls
        ``GET /api/v1/media/:id`` until it appears. The v1 route it
        replaced has been deprecated since Mastodon 3.1.3."""
        fields = {'description': description} if description else None
        return self.multipart_async(
            'POST', '/api/v2/media', fields=fields,
            files={'file': (file_bytes, filename, content_type)},
            callback=callback, cancellable=cancellable)

    def media_upload_finish(self, result):
        return self._finish(result)

    def media_status_async(self, media_id, callback=None, cancellable=None):
        """GET /api/v1/media/:id, to poll an upload being processed."""
        return self._request_async('GET', '/media/%s' % media_id,
                                   callback=callback, cancellable=cancellable)

    def media_status_finish(self, result):
        return self._finish(result)

