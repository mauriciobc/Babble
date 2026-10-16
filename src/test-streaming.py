# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for Mastodon streaming helpers (no network)."""

import json
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.mastodon import MastodonClient, parse_link_header  # noqa: E402
from src.streaming import (  # noqa: E402
    FEED_AVAILABLE, FEED_RESTRICTED, FEED_UNAVAILABLE,
    FEDERATED, LOCAL, USER, hashtag_stream, list_stream,
    public_feed_access, subscribe_message, streaming_url,
)

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL', label)
    else:
        print('ok', label)


check('parse_link next', parse_link_header(
    '<https://x.test/api/v1/favourites?max_id=1>; rel="next"').get('next')
    == {'max_id': '1'})

_paged = parse_link_header(
    '<https://x.test/a?max_id=7>; rel="next", '
    '<https://x.test/a?min_id=9>; rel="prev"')
check('parse_link next and prev', _paged == {'next': {'max_id': '7'},
                                              'prev': {'min_id': '9'}})
# RFC 8288: rel is case-insensitive and may follow other parameters.
check('parse_link rel is case-insensitive', parse_link_header(
    '<https://x.test/a?max_id=7>; rel="NEXT"') == {'next': {'max_id': '7'}})
check('parse_link rel after another parameter', parse_link_header(
    '<https://x.test/a?max_id=7>; title="x"; rel="next"')
    == {'next': {'max_id': '7'}})
check('parse_link rel next-page is not next', 'next' not in parse_link_header(
    '<https://x.test/a?max_id=7>; rel="next-page"'))
check('parse_link without rel is skipped',
      parse_link_header('<https://x.test/a?max_id=7>') == {})

check('user subscribe', json.loads(subscribe_message(USER)) == {
    'type': 'subscribe', 'stream': 'user'})
check('list subscribe', json.loads(subscribe_message(list_stream('9'))) == {
    'type': 'subscribe', 'stream': 'list', 'list': '9'})
check('hashtag unsubscribe', json.loads(
    subscribe_message(hashtag_stream('#Rust'), 'unsubscribe')) == {
    'type': 'unsubscribe', 'stream': 'hashtag', 'tag': 'rust'})

client = MastodonClient('https://social.example/')
client.instance_info = {
    'configuration': {'urls': {'streaming': 'https://stream.example'}}}
check('streaming url from instance', streaming_url(client)
      == 'wss://stream.example')
client.instance_info = {
    'configuration': {'urls': {'streaming': 'wss://stream.example/'}}}
check('an advertised wss url is kept', streaming_url(client)
      == 'wss://stream.example')
# The socket carries the bearer token: an unencrypted address the
# instance advertises must never be used.
for advertised in ('http://stream.example', 'ws://stream.example',
                   'gopher://stream.example', 'stream.example',
                   'wss://[stream.example'):
    client.instance_info = {
        'configuration': {'urls': {'streaming': advertised}}}
    check('an advertised %s url falls back to the instance' % advertised,
          streaming_url(client) == 'wss://social.example')
client.instance_info = {}
check('streaming url fallback', streaming_url(client)
      == 'wss://social.example')

check('stream tuples', LOCAL == ('public:local',) and FEDERATED == ('public',))

# --- Public feed access: Mastodon 4.5+ advertises in /api/v2/instance
# which of its public timelines it serves, and to whom.


def access(live_local=None, live_remote=None, permissions=None):
    info = {}
    if live_local is not None or live_remote is not None:
        info = {'configuration': {'timelines_access': {'live_feeds': {
            'local': live_local, 'remote': live_remote}}}}
    client.instance_info = info
    account = {} if permissions is None else {
        'role': {'permissions': permissions}}
    return public_feed_access(client, account)


check('an instance that says nothing keeps both feeds',
      access() == {'local': FEED_AVAILABLE, 'federated': FEED_AVAILABLE})
check('an open feed is available',
      access('public', 'public') == {'local': FEED_AVAILABLE,
                                     'federated': FEED_AVAILABLE})
check('an authenticated feed is available to the signed-in account',
      access('authenticated', 'authenticated')
      == {'local': FEED_AVAILABLE, 'federated': FEED_AVAILABLE})
check('a disabled feed is unavailable without the permission',
      access('disabled', 'disabled', '0')
      == {'local': FEED_UNAVAILABLE, 'federated': FEED_UNAVAILABLE})
check('the live-feeds permission turns a disabled feed into a restricted one',
      access('disabled', 'disabled', str(0x100000))
      == {'local': FEED_RESTRICTED, 'federated': FEED_RESTRICTED})
check('an unrelated permission does not count',
      access('disabled', 'disabled', str(0x200000))
      == {'local': FEED_UNAVAILABLE, 'federated': FEED_UNAVAILABLE})
check('each feed is decided on its own',
      access('disabled', 'public', '0')
      == {'local': FEED_UNAVAILABLE, 'federated': FEED_AVAILABLE})
check('an unknown role is not denied the feed',
      access('disabled', 'disabled')
      == {'local': FEED_RESTRICTED, 'federated': FEED_RESTRICTED})

# --- Rate limiting: a 429 must pause this client, not just fail one call.

check('a fresh client is not rate limited', not client.rate_limited())


class _Headers:
    """Just enough of SoupMessageHeaders for _note_rate_limit."""

    def __init__(self, **values):
        self._values = values

    def get_one(self, name):
        return self._values.get(name)

client._note_rate_limit(_Headers(**{'Retry-After': '30'}))
check('a 429 marks the client rate limited', client.rate_limited())
check('the retry delay comes from Retry-After',
      25 <= client.retry_after_seconds() <= 30)
check('the limit expires', not client.rate_limited(
    client._rate_limited_until + 1))

client._rate_limited_until = 0.0
client._note_rate_limit(_Headers())
check('a 429 without a header still pauses the client',
      client.rate_limited())
check('the fallback delay is the default',
      55 <= client.retry_after_seconds() <= 60)
client._rate_limited_until = 0.0

# --- Instance addresses: plaintext http would carry the token in clear.

try:
    MastodonClient('http://plain.example')
    check('a plaintext instance is refused', False)
except ValueError:
    check('a plaintext instance is refused', True)

check('a bare host becomes https',
      MastodonClient('example.social').instance == 'https://example.social')
check('a trailing slash is trimmed',
      MastodonClient('https://example.social/').instance
      == 'https://example.social')

import time  # noqa: E402

from gi.repository import GLib, GObject  # noqa: E402

import src.mastodon as mastodon_module  # noqa: E402
from src import desktopnotify, sysstate  # noqa: E402
from src import streaming as streaming_module  # noqa: E402


def pump_until(predicate, limit=8.0):
    """Iterate the default main context until *predicate* or *limit*."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and not predicate():
        while context.iteration(False):
            pass
        time.sleep(0.02)
    return predicate()


# --- System state: offline, back online, network changes, power saver ---


class FakeMonitor(GObject.Object):
    """sysstate.SystemState with writable properties."""
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


fake = FakeMonitor()
_real_monitor = sysstate.monitor
sysstate.monitor = lambda: fake


class StubSession:
    def __init__(self):
        self.attempts = 0

    def websocket_connect_async(self, *_args):
        self.attempts += 1


class StubConnection:
    """Just enough of Soup.WebsocketConnection for _drop_connection."""
    def __init__(self):
        self.closed = False

    def get_state(self):
        return streaming_module.Soup.WebsocketState.OPEN

    def close(self, *_args):
        self.closed = True


stream_client = MastodonClient('https://social.example/')
stream_client.token = 'token'
manager = streaming_module.StreamingManager()
session = manager._session = StubSession()
manager.start(stream_client)
check('online, start connects', session.attempts == 1)
# Three failed handshakes push the backoff up.
for n in range(3):
    # What _on_connected does with a failed handshake, then the timer
    # firing at once.
    manager._cancellable = None
    manager._schedule_retry()
    if n < 2:
        GLib.source_remove(manager._retry_source)
        manager._retry_source = 0
check('failures back off', manager._attempt == 3 and manager._retry_source)

fake.props.online = False
check('going offline drops the retry timer', manager._retry_source == 0)
manager._schedule_retry()
check('offline, no retry is scheduled', manager._retry_source == 0)
manager._connect()
check('offline, no handshake is attempted', session.attempts == 1)
fake.emit('network-changed')
check('offline, a network change attempts nothing', session.attempts == 1)

fake.props.online = True
check('back online connects at once, once', session.attempts == 2)
check('back online resets the backoff', manager._attempt == 0
      and manager._retry_source == 0)
fake.props.online = True
check('a repeated online notify does not connect again',
      session.attempts == 2)

# A socket believed open is replaced after a network change.
manager._cancellable = None
connection = StubConnection()
manager._connection = connection
manager._set_connected(True)
fake.emit('network-changed')
check('a network change reconnects an open socket',
      connection.closed and session.attempts == 3
      and not manager.connected)

fake.props.online = False
check('offline cancels the handshake in flight',
      manager._cancellable is None)
manager.stop()


class StubPollClient:
    token = 'token'

    def __init__(self):
        self.polls = []

    def rate_limited(self):
        return False

    def request_async(self, method, path, params=None, callback=None):
        self.polls.append(params)


class StubPollWindow:
    def __init__(self):
        self.client = StubPollClient()


armed = []
_real_timeout_add_seconds = GLib.timeout_add_seconds


def _record_timeout(seconds, *args):
    armed.append(seconds)
    return _real_timeout_add_seconds(seconds, *args)


desktopnotify.GLib.timeout_add_seconds = _record_timeout
poll_window = StubPollWindow()
polls = poll_window.client.polls
notifier = desktopnotify.DesktopNotifier(poll_window)

notifier.start_polling()
check('offline, polling skips the first poll', polls == [])
check('polling is scheduled at the normal rate',
      armed[-1] == desktopnotify.POLL_SECONDS)
notifier._on_poll_tick()
check('offline, a poll tick sends nothing', polls == [])

fake.props.online = True
check('back online polls at once', len(polls) == 1
      and polls[0] == {'limit': 1})
notifier._last_id = '10'
notifier._on_poll_tick()
check('online, a tick polls from the cursor',
      polls[-1] == {'limit': 20, 'since_id': '10'})

fake.props.power_saver = True
check('power saver doubles the poll interval',
      notifier.poll_interval() == 2 * desktopnotify.POLL_SECONDS
      and armed[-1] == 2 * desktopnotify.POLL_SECONDS)
fake.props.power_saver = False
check('leaving power saver restores the interval',
      armed[-1] == desktopnotify.POLL_SECONDS)
notifier.stop_polling()

desktopnotify.GLib.timeout_add_seconds = _real_timeout_add_seconds
sysstate.monitor = _real_monitor

# The real monitor coalesces a burst of network signals into one change.
real = sysstate.SystemState()
bursts = []
real.connect('network-changed', lambda *_a: bursts.append(1))
for _ in range(5):
    real._on_network_signal()
pump_until(lambda: bursts, limit=2.0)
check('a burst of network signals settles into one change',
      bursts == [1] and not real._settle_source)
check('the real monitor exposes the contract properties',
      all(isinstance(real.get_property(name), bool)
          for name in ('online', 'metered', 'power-saver')))

# --- The keyring helpers must actually deliver their callbacks ----------

# These went through Secret.password_* first, whose callbacks never fired:
# PyGObject hands the callback (None, result, user_data), so the *_finish
# call raised on a None source and every login silently hung. The
# helpers now go through one shared Secret.Service, which binds the task
# to the proxy that started it.

done = []
try:
    mastodon_module.store_secret_async(
        'babble-selftest.example', 'probe', 'round-trip-value',
        lambda error: done.append(error))
except GLib.Error:
    # get_sync raises before the callback when the session has no
    # secret service at all. That is the same "nothing to read back"
    # the callback path skips on below.
    done.append(GLib.Error('no secret service'))
check('storing a secret completes its callback',
      pump_until(lambda: len(done) == 1))

# The round trip is only meaningful against a keyring that will accept a
# write. A CI container has no secret service, and an unlocked one still
# refuses, so the first error means there is nothing to read back rather
# than a defect: skip, loudly, instead of reporting a failure that only
# describes the machine.
if done and done[0] is not None:
    if failures:
        print('%d failure(s)' % failures)
        sys.exit(1)
    print('SKIP - no usable secret service for the keyring round trip')
    sys.exit(77)

# The callback hands back the failure instead of swallowing it: a
# keyring that refused the secret has to be reportable, or the caller
# stores an account it can never sign back in.
check('a successful store reports no error', done and done[0] is None)

read = {}
mastodon_module.load_secret_async(
    'babble-selftest.example', 'probe',
    lambda v, error=None: read.update(v=v, e=error))
pump_until(lambda: 'v' in read)
check('the stored secret reads back',
      read.get('v') == 'round-trip-value' and read.get('e') is None)

gone = []
mastodon_module.clear_secret_async(
    'babble-selftest.example', 'probe',
    lambda error: gone.append(error))
check('clearing a secret completes its callback',
      pump_until(lambda: len(gone) == 1))
check('a successful clear reports no error', gone and gone[0] is None)

after = {}
mastodon_module.load_secret_async(
    'babble-selftest.example', 'probe',
    lambda v, error=None: after.update(v=v, e=error))
pump_until(lambda: 'v' in after)
check('the cleared secret is gone',
      after.get('v') is None and after.get('e') is None)

if failures:
    print('%d failure(s)' % failures)
    sys.exit(1)
print('All streaming helper checks passed.')
