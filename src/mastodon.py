import json
import random
import re
import time
import urllib.parse

import gi
gi.require_version('Soup', '3.0')
gi.require_version('Secret', '1')
from gi.repository import GLib, GObject, Gio, Secret, Soup  # noqa: E402
from .clientinstance import InstanceMixin  # noqa: E402
from .clientoauth import OAuthMixin  # noqa: E402
from .httpheaders import (parse_link_header,  # noqa: E402
                          parse_reset_instant, server_clock_offset)
try:
    from .i18n import gettext as _, ngettext  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _, ngettext  # noqa: E402
from .oauthpkce import OAUTH_REDIRECT, normalize_instance  # noqa: E402

# Reverse proxies routinely refuse the default libsoup User-Agent
# ("libsoup/3.0") outright: ursal.zone's nginx answers POST /api/v1/apps
# with 403 to it while the very same request carrying an application
# User-Agent returns 200. Announcing the client on the session fixes every
# call at once, uploads included, since they share the one session.
USER_AGENT = 'Babble/%s (+https://github.com/mauriciobc/Babble)'


def user_agent():
    """Babble's User-Agent, with the running version."""
    try:
        import babble as _app_meta
        version = getattr(_app_meta, 'VERSION', '0.1')
    except ImportError:
        version = '0.1'
    return USER_AGENT % version


# Adw.Toast renders its title as plain text, so an untrusted server body
# must reach it with nothing that Pango or a widget could interpret and
# nothing long enough to blow out the toast. HTML is stripped and the
# result is bounded to a single short line; no markup escaping happens
# here, because escaping would show a literal '&amp;' to the reader.
MAX_ERROR_SUMMARY = 240

# libsoup's session timeout defaults to 0, meaning "wait forever". A
# stalled peer would then leave the Gio.Task pending for the life of the
# process, so every session that talks to a server sets this instead.
HTTP_TIMEOUT_SECONDS = 30

# A 429 without a usable Retry-After still pauses the pollers, so the
# app stops deepening the limit instead of retrying into it.
DEFAULT_RATE_LIMIT_SECONDS = 60
MAX_RATE_LIMIT_SECONDS = 3600

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
# Known and accepted (audit 5f): a DELETE whose first answer was lost, then
# repeated, finds the object gone and the caller sees a 404 for what did
# succeed. Only a transport error or a 5xx repeats a request, so the window
# is narrow, and the 404 is not turned into a success on purpose: a
# successful DELETE /statuses/:id answers with the deleted status, whose text
# a redraft needs, and a 404 cannot supply it, so a made-up success would
# open an empty composer where an error now says what happened. The state
# the reader wanted is the state they have.
IDEMPOTENT_METHODS = ('GET', 'HEAD', 'OPTIONS', 'TRACE', 'PUT', 'DELETE')

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


def keyring_problem(error):
    """What a failed keyring call means: ``'missing'``, ``'locked'`` or
    ``'other'`` (``None`` for no error).

    Missing is a Secret Service that is not there to answer, which
    libsecret reports as a D-Bus error: ``g-dbus-error-quark`` code 2
    (``SERVICE_UNKNOWN``, "The name org.freedesktop.secrets was not
    provided by any .service files"), observed on a session bus with no
    activatable keyring, from both ``Secret.Service.get`` and
    ``Secret.password_lookup_sync``. ``NAME_HAS_NO_OWNER`` is the same
    absence after the service has quit. A locked collection is a
    different problem with a different fix, so it is not 'missing'."""
    if error is None:
        return None
    for code in (Gio.DBusError.SERVICE_UNKNOWN,
                 Gio.DBusError.NAME_HAS_NO_OWNER):
        if error.matches(Gio.DBusError.quark(), code):
            return 'missing'
    if error.matches(Secret.error_get_quark(), Secret.Error.IS_LOCKED):
        return 'locked'
    return 'other'


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


class MastodonClient(OAuthMixin, InstanceMixin, GObject.Object):
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
        self._session.props.user_agent = user_agent()
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
                _('Operation canceled'), 'g-io-error-quark',
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
        invalid_json = False
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            invalid_json = True
            data = {'error': text}
        status = message.get_status()
        headers = message.get_response_headers()
        log_deprecation(message.get_method(), message.get_uri().get_path(),
                        headers)
        if 200 <= status < 300 and status != Soup.Status.NONE:
            if invalid_json:
                task.return_error(GLib.Error(
                    _('The server returned an invalid JSON response.'),
                    'mastodon-http-error-quark', 0))
                return
            link = headers.get_one('Link') or ''
            # The parsed object itself, not a re-encoded string: dumping
            # it into a GLib.Variant only for _envelope to parse it again
            # was two thirds of each response's JSON work.
            task.return_value(GObject.Value(GObject.TYPE_PYOBJECT,
                                            {'data': data, 'link': link}))
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
        # Test fakes still hand back the JSON string form.
        if isinstance(value, GLib.Variant):
            return json.loads(value.get_string())
        return value

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

    # OAuth

    # Accounts live in src/api/accounts.py and src/api/relationships.py.

    # Statuses live in src/api/statuses.py.

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


