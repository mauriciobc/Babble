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

if failures:
    print('%d failure(s)' % failures)
    sys.exit(1)
print('All streaming helper checks passed.')
