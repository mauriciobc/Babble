import json
import re
import urllib.parse

import gi

gi.require_version('Soup', '3.0')
gi.require_version('Secret', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import GLib, GObject, Gio, Secret, Soup  # noqa: E402

# Reverse proxies routinely refuse the default libsoup User-Agent
# ("libsoup/3.0") outright: ursal.zone's nginx answers POST /api/v1/apps
# with 403 to it while the very same request carrying an application
# User-Agent returns 200. Announcing the client on the session fixes every
# call at once, uploads included, since they share the one session.
USER_AGENT = 'Babble/%s (+https://github.com/mauriciobc/babble)'

# Adw.Toast renders its title as Pango markup, and a markup-parse failure
# leaves the toast empty — the user then cannot even see that an error
# happened. Server response bodies are untrusted, so every error text goes
# through this summariser: HTML stripped to a bounded plain-text line,
# ready to be passed to a markup-rendering widget untouched.
MAX_ERROR_SUMMARY = 240

# <script>/<style> payloads first: stripping only the tags would leave
# their source in the middle of the sentence.
_UNPRINTABLE_BLOCK = re.compile(r'(?is)<(script|style)\b.*?</\1\s*>')
_TAG = re.compile(r'(?s)<[^>]*>')
_SPACES = re.compile(r'\s+')


def summarize_error(text):
    """HTML from a server response to a safe, bounded plain-text line."""
    text = _UNPRINTABLE_BLOCK.sub(' ', text or '').replace('&', '&amp;')
    text = _TAG.sub(' ', text)
    text = _SPACES.sub(' ', text).strip()
    if len(text) > MAX_ERROR_SUMMARY:
        text = text[:MAX_ERROR_SUMMARY - 1].rstrip() + '…'
    return text

OAUTH_REDIRECT = 'babble://oauth/callback'
OOB_REDIRECT = 'urn:ietf:wg:oauth:2.0:oob'
REDIRECT_URIS = OAUTH_REDIRECT + ' ' + OOB_REDIRECT
OAUTH_SCOPES = 'read write follow'
SECRET_SCHEMA = Secret.Schema.new('io.github.mauriciobc.Babble',
                                  Secret.SchemaFlags.NONE,
                                  {
                                      'instance': Secret.SchemaAttributeType.STRING,
                                      'kind': Secret.SchemaAttributeType.STRING,
                                  })


def normalize_instance(url):
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    return url.rstrip('/')


def store_secret(instance, kind, payload):
    Secret.password_store_sync(SECRET_SCHEMA,
                               {'instance': instance, 'kind': kind},
                               Secret.COLLECTION_DEFAULT,
                               'Babble: %s' % instance,
                               payload,
                               None)


def load_secret(instance, kind):
    return Secret.password_lookup_sync(SECRET_SCHEMA,
                                       {'instance': instance, 'kind': kind},
                                       None)


def clear_secret(instance, kind):
    Secret.password_clear_sync(SECRET_SCHEMA,
                               {'instance': instance, 'kind': kind},
                               None)


_LINK_PART = re.compile(r'<([^>]*)>\s*;\s*rel="?([a-z]+)"?')


def parse_link_header(value):
    """``{'next': {...query params}, 'prev': {...}}`` from an RFC 8288
    Link header, the way Mastodon paginates bookmarks, favourites,
    follow lists and everything else not keyed by status id."""
    links = {}
    for url, rel in _LINK_PART.findall(value or ''):
        query = urllib.parse.urlsplit(url).query
        links[rel] = dict(urllib.parse.parse_qsl(query))
    return links


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
        self._session = Soup.Session()
        try:
            import babble as _app_meta
            _version = getattr(_app_meta, 'VERSION', '0.1')
        except ImportError:
            _version = '0.1'
        self._session.props.user_agent = USER_AGENT % _version
        self._pending = {}

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

    def _on_response(self, _session, result, task):
        message = self._pending.pop(task, None)
        try:
            body = self._session.send_and_read_finish(result)
        except GLib.Error as e:
            task.return_error(e)
            return
        text = body.get_data().decode('utf-8', 'replace')
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {'error': text}
        status = message.get_status()
        if 200 <= status < 300 and status != Soup.Status.NONE:
            link = message.get_response_headers().get_one('Link') or ''
            task.return_value(GLib.Variant('s', json.dumps(
                {'data': data, 'link': link})))
        else:
            error = data.get('error') if isinstance(data, dict) else None
            task.return_error(GLib.Error(
                str(error or Soup.status_get_phrase(status)),
                'mastodon-http-error-quark', status))

    def _envelope(self, result):
        value = result.propagate_value()
        if isinstance(value, tuple):
            value = value[1]
        return json.loads(value.get_string())

    def _finish(self, result):
        return self._envelope(result)['data']

    def _request_async(self, method, path, params=None, form=None,
                       auth=True, callback=None, cancellable=None,
                       json_body=None):
        """Queue one API call. ``path`` is relative to /api/v1 unless it
        starts with ``/api/`` or ``/oauth/`` (instance root) or ``http``.
        The bearer token goes along whenever one is set, so every
        response carries the viewer's state (favourited, filtered…)."""
        task = Gio.Task.new(self, cancellable, callback)
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
            task.return_error(GLib.Error(_('Invalid address: %s') % uri,
                                         'mastodon-http-error-quark', 0))
            return task
        if auth and self.token:
            message.get_request_headers().append('Authorization',
                                                 'Bearer ' + self.token)
        if json_body is not None:
            message.set_request_body_from_bytes(
                'application/json',
                GLib.Bytes.new(json.dumps(json_body).encode()))
        elif form is not None:
            # doseq=True encodes list values (e.g. media_ids) as
            # repeated keys, the format Mastodon expects for arrays.
            body = urllib.parse.urlencode(form, doseq=True)
            message.set_request_body_from_bytes(
                'application/x-www-form-urlencoded', GLib.Bytes.new(body.encode()))
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
        envelope = self._envelope(result)
        links = parse_link_header(envelope.get('link', ''))
        return envelope['data'], links.get('next'), links.get('prev')

    def instance_info_async(self, callback=None, cancellable=None):
        """GET /api/v2/instance; the result is kept on
        ``self.instance_info`` by :meth:`instance_info_finish`."""
        return self._request_async('GET', '/api/v2/instance',
                                   callback=callback, cancellable=cancellable)

    def instance_info_finish(self, result):
        self.instance_info = self._finish(result)
        return self.instance_info

    def max_characters(self):
        """Post length limit of the instance, 500 until it is known."""
        info = self.instance_info or {}
        statuses = (info.get('configuration') or {}).get('statuses') or {}
        return statuses.get('max_characters') or 500

    # OAuth

    def register_app_async(self, callback=None, cancellable=None):
        return self._request_async('POST', '/apps',
                                   form={'client_name': 'Babble',
                                         'redirect_uris': REDIRECT_URIS,
                                         'scopes': OAUTH_SCOPES,
                                         'website': self.instance},
                                   callback=callback, cancellable=cancellable)

    def register_app_finish(self, result):
        data = self._finish(result)
        self.client_id = data['client_id']
        self.client_secret = data['client_secret']
        store_secret(self.instance, 'app',
                     json.dumps({'client_id': self.client_id,
                                 'client_secret': self.client_secret}))
        return data

    def load_registered_app(self):
        stored = load_secret(self.instance, 'app')
        if not stored:
            return False
        data = json.loads(stored)
        self.client_id = data['client_id']
        self.client_secret = data['client_secret']
        return True

    def store_secret_token(self, account_id, token):
        store_secret(self.instance, account_id, token)

    def load_secret_token(self, account_id):
        return load_secret(self.instance, account_id)

    def get_authorize_url(self, redirect_uri=OAUTH_REDIRECT):
        query = urllib.parse.urlencode({
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': OAUTH_SCOPES,
        })
        return self.instance + '/oauth/authorize?' + query

    @staticmethod
    def parse_callback_uri(uri):
        query = urllib.parse.urlparse(uri).query
        params = urllib.parse.parse_qs(query)
        codes = params.get('code')
        return codes[0] if codes else None

    def request_token_async(self, code, callback=None, cancellable=None,
                            redirect_uri=OAUTH_REDIRECT):
        # /oauth/token lives at the instance root, not under /api/v1.
        return self._request_async('POST', self.instance + '/oauth/token',
                                   form={'client_id': self.client_id,
                                         'client_secret': self.client_secret,
                                         'redirect_uri': redirect_uri,
                                         'grant_type': 'authorization_code',
                                         'code': code},
                                   callback=callback, cancellable=cancellable)

    def request_token_finish(self, result):
        return self._finish(result)['access_token']

    # Accounts

    def verify_credentials_async(self, callback=None, cancellable=None):
        return self._request_async('GET', '/accounts/verify_credentials',
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def verify_credentials_finish(self, result):
        return self._finish(result)

    def lookup_account_async(self, acct, callback=None, cancellable=None):
        return self._request_async('GET', '/accounts/lookup',
                                   params={'acct': acct},
                                   callback=callback, cancellable=cancellable)

    def lookup_account_finish(self, result):
        return self._finish(result)

    def follow_account_async(self, account_id, callback=None, cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/follow' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def follow_account_finish(self, result):
        return self._finish(result)

    def unfollow_account_async(self, account_id, callback=None,
                               cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/unfollow' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def unfollow_account_finish(self, result):
        return self._finish(result)

    def mute_account_async(self, account_id, callback=None,
                           cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/mute' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def mute_account_finish(self, result):
        return self._finish(result)

    def unmute_account_async(self, account_id, callback=None,
                             cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/unmute' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def unmute_account_finish(self, result):
        return self._finish(result)

    def block_account_async(self, account_id, callback=None,
                            cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/block' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def block_account_finish(self, result):
        return self._finish(result)

    def unblock_account_async(self, account_id, callback=None,
                              cancellable=None):
        return self._request_async('POST',
                                   '/accounts/%s/unblock' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def unblock_account_finish(self, result):
        return self._finish(result)

    def account_async(self, account_id, callback=None, cancellable=None):
        return self._request_async('GET', '/accounts/%s' % account_id,
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def account_finish(self, result):
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
            params={'id[]': account_ids}, auth=True, callback=callback,
            cancellable=cancellable)

    def relationships_finish(self, result):
        data = self._finish(result)
        return data[0] if data else {}


    # Timelines and notifications

    def timelines_home_async(self, params=None, callback=None, cancellable=None):
        return self._request_async('GET', '/timelines/home', params=params,
                                   auth=True, callback=callback,
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
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def notifications_finish(self, result):
        return self._finish(result)

    def notifications_unread_count_async(self, params=None, callback=None,
                                         cancellable=None):
        """GET /api/v1/notifications/unread_count (Mastodon 4.0+).
        ``params`` accepts the same ``types[]``/``exclude_types[]`` as the
        list endpoint, so the badge counts what the list shows."""
        return self._request_async('GET', '/notifications/unread_count',
                                   params=params, auth=True,
                                   callback=callback,
                                   cancellable=cancellable)

    def notifications_unread_count_finish(self, result):
        return self._finish(result).get('count', 0)

    def notifications_clear_async(self, callback=None, cancellable=None):
        """POST /api/v1/notifications/clear: delete every notification."""
        return self._request_async('POST', '/notifications/clear',
                                   auth=True, callback=callback,
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
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def notifications_mark_read_finish(self, result):
        return self._finish(result)

    # Statuses

    def statuses_async(self, status_id, callback=None, cancellable=None):
        return self._request_async('GET', '/statuses/%s' % status_id,
                                   callback=callback, cancellable=cancellable)

    def statuses_finish(self, result):
        return self._finish(result)

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
        options, ``media_ids`` arrays) keep their structure."""
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
        :meth:`request_finish`."""
        task = Gio.Task.new(self, cancellable, callback)
        uri = self.instance + path if path.startswith('/api/') \
            else self.api_base + path
        message = Soup.Message.new(method, uri)
        multipart = Soup.Multipart.new('multipart/form-data')
        for name, (data, filename, content_type) in (files or {}).items():
            multipart.append_form_file(name, filename, content_type,
                                       GLib.Bytes.new(data))
        for name, value in (fields or {}).items():
            for item in value if isinstance(value, (list, tuple)) \
                    else [value]:
                multipart.append_form_string(name, str(item))
        multipart.finish_message(message)
        if self.token:
            message.get_request_headers().append('Authorization',
                                                 'Bearer ' + self.token)
        self._spawn(task, message)
        return task

    def media_upload_async(self, file_bytes, filename, content_type,
                           description=None, callback=None,
                           cancellable=None):
        """Upload one attachment to ``POST /api/v1/media`` (docs.
        joinmastodon.org/methods/media), multipart/form-data with a
        ``file`` part and optional ``description`` alt text."""
        fields = {'description': description} if description else None
        return self.multipart_async(
            'POST', '/media', fields=fields,
            files={'file': (file_bytes, filename, content_type)},
            callback=callback, cancellable=cancellable)

    def media_upload_finish(self, result):
        return self._finish(result)

    def _status_action_async(self, action, status_id, callback=None,
                             cancellable=None):
        return self._request_async('POST',
                                   '/statuses/%s/%s' % (status_id, action),
                                   auth=True, callback=callback,
                                   cancellable=cancellable)

    def favourite_async(self, status_id, callback=None, cancellable=None):
        return self._status_action_async('favourite', status_id, callback,
                                         cancellable)

    def favourite_finish(self, result):
        return self._finish(result)

    def reblog_async(self, status_id, callback=None, cancellable=None):
        return self._status_action_async('reblog', status_id, callback,
                                         cancellable)

    def reblog_finish(self, result):
        return self._finish(result)

    def bookmark_async(self, status_id, callback=None, cancellable=None):
        return self._status_action_async('bookmark', status_id, callback,
                                         cancellable)

    def bookmark_finish(self, result):
        return self._finish(result)
