# SPDX-License-Identifier: GPL-3.0-or-later

"""Mastodon streaming API over one WebSocket.

Connects to ``<streaming url>/api/v1/streaming?stream=user`` (the token
goes in the ``Authorization`` header, the documented way) and
multiplexes further streams (``public:local``, ``public``, ``list``,
``hashtag``, ``direct``) over the same socket with
``subscribe``/``unsubscribe`` messages (docs.joinmastodon.org/methods/streaming).
The manager reconnects with exponential backoff and re-subscribes; the
owner decides what to do with the decoded events through the ``event``
signal, and learns about outages from ``connection-changed`` so it can
keep polling while the socket is down.
"""

import json
import random
import urllib.parse

import gi

gi.require_version('Soup', '3.0')

from gi.repository import Gio, GLib, GObject, Soup  # noqa: E402

BACKOFF_START = 2
BACKOFF_MAX = 60
STABLE_SECONDS = 30
KEEPALIVE_SECONDS = 30
MAX_PAYLOAD = 4 * 1024 * 1024

# The upgrade request is a normal HTTP request; libsoup's default of no
# timeout would leave it hanging against a host that accepts the TCP
# connection and then goes quiet.
STREAMING_TIMEOUT_SECONDS = 30

USER = ('user',)
DIRECT = ('direct',)
LOCAL = ('public:local',)
FEDERATED = ('public',)

# Events whose payload is a JSON document; everything else carries a
# bare id (``delete``, ``announcement.delete``) or nothing.
JSON_EVENTS = {'update', 'status.update', 'notification', 'conversation',
               'announcement', 'announcement.reaction'}


def list_stream(list_id):
    return ('list', str(list_id))


def hashtag_stream(tag):
    return ('hashtag', tag.lstrip('#').lower())


def streaming_url(client):
    """Base WebSocket URL of the client's server: the one the instance
    advertises, or the instance itself.

    The socket carries the bearer token, so an advertised address that is
    not encrypted (``http``, ``ws``, or anything else) is ignored in
    favour of the instance, which is always https."""
    info = getattr(client, 'instance_info', None) or {}
    advertised = ((info.get('configuration') or {}).get('urls') or {}) \
        .get('streaming')
    parts = urllib.parse.urlsplit(advertised or '')
    if parts.scheme not in ('https', 'wss') or not parts.netloc:
        parts = urllib.parse.urlsplit(client.instance)
    return urllib.parse.urlunsplit(
        ('wss', parts.netloc, parts.path.rstrip('/'), '', ''))


def subscribe_message(stream, kind='subscribe'):
    """The JSON frame (un)subscribing *stream*, a tuple such as
    ``('list', '12')`` or ``('public:local',)``."""
    message = {'type': kind, 'stream': stream[0]}
    if stream[0] == 'list':
        message['list'] = stream[1]
    elif stream[0].startswith('hashtag'):
        message['tag'] = stream[1]
    return json.dumps(message)


class StreamingManager(GObject.Object):
    """Reconnecting multiplexed streaming socket.

    ``event(stream, name, payload)``: *stream* is the tuple the server
    names (e.g. ``('user',)``), *name* the event (``update``…), *payload*
    the decoded JSON or id string.
    ``connection-changed(connected)``: the socket opened or went away.
    """

    __gtype_name__ = 'BabbleStreamingManager'
    __gsignals__ = {
        'event': (GObject.SignalFlags.RUN_FIRST, None,
                  (object, str, object)),
        'connection-changed': (GObject.SignalFlags.RUN_FIRST, None,
                               (bool,)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._session = Soup.Session()
        self._session.set_timeout(STREAMING_TIMEOUT_SECONDS)
        # Same reason as the REST client: reverse proxies reject the
        # default libsoup User-Agent on the upgrade request.
        try:
            from .mastodon import USER_AGENT
            import babble as _app_meta
            self._session.props.user_agent = USER_AGENT % getattr(
                _app_meta, 'VERSION', '0.1')
        except ImportError:
            pass
        self._client = None
        self._connection = None
        self._cancellable = None
        self._retry_source = 0
        self._attempt = 0
        self._opened_at = 0
        self._desired = {USER}
        self._subscribed = set()
        self.connected = False

    # Lifecycle

    def start(self, client):
        """(Re)connect for *client*, dropping any previous socket.
        Idempotent: a repeat call for the client already connected (or
        connecting) is a no-op, so callers can sync freely."""
        if client is not None and client is self._client \
                and (self._connection is not None
                     or self._cancellable is not None
                     or self._retry_source):
            return
        self.stop()
        if client is None or not client.token:
            return
        self._client = client
        self._attempt = 0
        self._connect()

    def stop(self):
        self._client = None
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        self._drop_connection(close=True)

    def _drop_connection(self, close=False):
        connection, self._connection = self._connection, None
        if connection is not None:
            for handler in getattr(connection, '_babble_handlers', ()):
                connection.disconnect(handler)
            if close and connection.get_state() == Soup.WebsocketState.OPEN:
                connection.close(Soup.WebsocketCloseCode.NORMAL, None)
        self._subscribed = set()
        self._set_connected(False)

    def _set_connected(self, connected):
        if connected != self.connected:
            self.connected = connected
            self.emit('connection-changed', connected)

    # Subscriptions

    def set_streams(self, streams):
        """The streams wanted besides the always-on user stream."""
        self._desired = {USER} | set(streams)
        self._sync_subscriptions()

    def _sync_subscriptions(self):
        connection = self._connection
        if connection is None \
                or connection.get_state() != Soup.WebsocketState.OPEN:
            return
        for stream in sorted(self._desired - self._subscribed):
            connection.send_text(subscribe_message(stream))
            self._subscribed.add(stream)
        for stream in sorted(self._subscribed - self._desired):
            connection.send_text(subscribe_message(stream, 'unsubscribe'))
            self._subscribed.discard(stream)

    # Connection

    def build_url(self):
        return '%s/api/v1/streaming?stream=user' % streaming_url(self._client)

    def _connect(self):
        self._retry_source = 0
        client = self._client
        if client is None:
            return GLib.SOURCE_REMOVE
        message = Soup.Message.new('GET', self.build_url())
        if message is None:
            self._schedule_retry()
            return GLib.SOURCE_REMOVE
        # The Authorization header is the documented way in; a token in
        # the query string ends up in proxy and access logs.
        message.get_request_headers().append('Authorization',
                                             'Bearer ' + client.token)
        self._cancellable = Gio.Cancellable()
        self._session.websocket_connect_async(
            message, None, None, GLib.PRIORITY_DEFAULT, self._cancellable,
            self._on_connected, self._cancellable)
        return GLib.SOURCE_REMOVE

    def _on_connected(self, session, result, cancellable):
        # A superseded or cancelled attempt must not touch the live
        # socket, and must not count as a connection failure.
        if cancellable is not self._cancellable or cancellable.is_cancelled():
            try:
                session.websocket_connect_finish(result).close(
                    Soup.WebsocketCloseCode.NORMAL, None)
            except GLib.Error:
                pass
            return
        try:
            connection = session.websocket_connect_finish(result)
        except GLib.Error as error:
            if not error.matches(Gio.io_error_quark(),
                                 Gio.IOErrorEnum.CANCELLED):
                self._schedule_retry()
            return
        connection.set_keepalive_interval(KEEPALIVE_SECONDS)
        connection.set_max_incoming_payload_size(MAX_PAYLOAD)
        connection._babble_handlers = [
            connection.connect('message', self._on_message),
            connection.connect('closed', self._on_closed),
            connection.connect('error', self._on_error),
        ]
        self._connection = connection
        self._opened_at = GLib.get_monotonic_time()
        # The URL's stream=user subscribed the user stream already.
        self._subscribed = {USER}
        self._set_connected(True)
        self._sync_subscriptions()

    def _on_error(self, _connection, _error):
        # 'closed' follows every error; nothing to do until then.
        pass

    def _on_closed(self, connection, *_args):
        if connection is not self._connection:
            return
        # Only a connection that stayed up counts as healthy: a server
        # that accepts and immediately closes (revoked token, limited
        # federation) must back off exponentially, not retry every 2s.
        lived = GLib.get_monotonic_time() - self._opened_at
        if lived > STABLE_SECONDS * 1_000_000:
            self._attempt = 0
        self._drop_connection()
        self._schedule_retry()

    def _schedule_retry(self):
        if self._client is None or self._retry_source:
            return
        delay = min(BACKOFF_START * 2 ** self._attempt, BACKOFF_MAX)
        self._attempt += 1
        delay += random.uniform(0, delay / 4)
        self._retry_source = GLib.timeout_add(int(delay * 1000),
                                              self._connect)

    # Messages

    def _on_message(self, _connection, data_type, data):
        if data_type != Soup.WebsocketDataType.TEXT:
            return
        try:
            frame = json.loads(data.get_data().decode('utf-8'))
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        event = frame.get('event')
        if not event:
            return
        payload = frame.get('payload')
        if event in JSON_EVENTS and isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return
        stream = tuple(frame.get('stream') or ())
        self.emit('event', stream, event, payload)
