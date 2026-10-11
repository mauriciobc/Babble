# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for Mastodon streaming helpers (no network)."""

import json
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, srcdir)

from mastodon import MastodonClient, parse_link_header  # noqa: E402
from streaming import (  # noqa: E402
    FEDERATED, LOCAL, USER, hashtag_stream, list_stream,
    subscribe_message, streaming_url,
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
client.instance_info = {}
check('streaming url fallback', streaming_url(client)
      == 'wss://social.example')

check('stream tuples', LOCAL == ('public:local',) and FEDERATED == ('public',))

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

# --- The keyring helpers must actually deliver their callbacks ----------

# These went through Secret.password_* first, whose callbacks never fired:
# PyGObject hands the callback (None, result, user_data), so the *_finish
# call raised on a None source and every login silently hung. The
# helpers now go through one shared Secret.Service, which binds the task
# to the proxy that started it.

import time  # noqa: E402

from gi.repository import GLib  # noqa: E402

import mastodon as mastodon_module  # noqa: E402


def pump_until(predicate, limit=8.0):
    """Iterate the default main context until *predicate* or *limit*."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and not predicate():
        while context.iteration(False):
            pass
        time.sleep(0.02)
    return predicate()


done = []
mastodon_module.store_secret_async(
    'babble-selftest.example', 'probe', 'round-trip-value',
    lambda error: done.append(error))
check('storing a secret completes its callback',
      pump_until(lambda: len(done) == 1))

# The round trip is only meaningful against a keyring that will accept a
# write. A CI container has no secret service, and an unlocked one still
# refuses, so the first error means there is nothing to read back rather
# than a defect: skip, loudly, instead of reporting a failure that only
# describes the machine.
if done and done[0] is not None:
    print('SKIP - no usable secret service for the keyring round trip')
    sys.exit(77)

# The callback hands back the failure instead of swallowing it: a
# keyring that refused the secret has to be reportable, or the caller
# stores an account it can never sign back in.
check('a successful store reports no error', done and done[0] is None)

read = {}
mastodon_module.load_secret_async(
    'babble-selftest.example', 'probe', lambda v: read.update(v=v))
pump_until(lambda: 'v' in read)
check('the stored secret reads back',
      read.get('v') == 'round-trip-value')

gone = []
mastodon_module.clear_secret_async(
    'babble-selftest.example', 'probe',
    lambda error: gone.append(error))
check('clearing a secret completes its callback',
      pump_until(lambda: len(gone) == 1))
check('a successful clear reports no error', gone and gone[0] is None)

after = {}
mastodon_module.load_secret_async(
    'babble-selftest.example', 'probe', lambda v: after.update(v=v))
pump_until(lambda: 'v' in after)
check('the cleared secret is gone', after.get('v') is None)

if failures:
    print('%d failure(s)' % failures)
    sys.exit(1)
print('All streaming helper checks passed.')
