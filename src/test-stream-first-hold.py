#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Evidence for the stream-before-first-page race, and the hold that
fixes it. Needs no Adw widgets: the model alone shows the corruption,
and a bare HomeFilters stand-in exercises the hold/flush helpers.
"""

import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('GObject', '2.0')

from gi.repository import GObject  # noqa: E402

# GObject type registration needs a main type system; Gtk pulls it in.
from src.timelinedata import TimelineModel  # noqa: E402
from src.homefilters import HomeFilters  # noqa: E402
from src.timelinebase import id_key  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


def status(number):
    return {'id': '%05d' % number, 'content': '<p>post %d</p>' % number,
            'account': {'acct': 'user', 'id': '1'},
            'reblog': None, 'media_attachments': [], 'emojis': [],
            'mentions': [], 'tags': [], 'card': None, 'poll': None,
            'spoiler_text': '', 'sensitive': False, 'visibility': 'public',
            'created_at': '2026-01-01T00:00:00.000Z',
            'favourited': False, 'reblogged': False, 'bookmarked': False,
            'muted': False, 'pinned': False, 'filtered': [],
            'replies_count': 0, 'reblogs_count': 0, 'favourites_count': 0,
            'url': 'https://example.org/%d' % number,
            'uri': 'https://example.org/%d' % number,
            'in_reply_to_id': None, 'in_reply_to_account_id': None,
            'language': 'en', 'text': None}


# --- Evidence: what the race did before the hold -------------------------
model = TimelineModel()
streamed = [status(99)]
first = [status(5), status(4), status(3), status(2), status(1)]
# Stream lands first on an empty model (the old _insert_streamed path).
model.insert_new(streamed)
if model.oldest_id is None:
    model.oldest_id = min((s['id'] for s in streamed), key=id_key)
# First page then takes the non-empty path (insert_new, not _fill_first).
model.insert_new(first)
ids = [model.get_item(i).raw['id'] for i in range(model.get_n_items())]
check('evidence: without a hold, streamed id becomes oldest_id',
      model.oldest_id == '00099', repr(model.oldest_id))
check('evidence: without a hold, streamed post sits at the bottom',
      ids[-1] == '00099' and ids[0] == '00005', repr(ids))


# --- Hold + flush restores order and oldest_id ---------------------------
class StandIn(HomeFilters):
    def __init__(self):
        self.model = TimelineModel()
        self.model.loading = True
        self._hide_boosts = False
        self._first_stream = []
        self._stream_pending = []
        self._stream_inflight = []
        self._stream_asked = set()
        self.inserted = []

    def _queue_streamed(self, status):
        return False

    def _insert_streamed(self, statuses):
        self.inserted.extend(statuses)
        self.model.insert_new(statuses)
        if self.model.oldest_id is None:
            self.model.oldest_id = min((s['id'] for s in statuses),
                                       key=id_key)

    def _fill_first(self, statuses):
        self.model.append_old(statuses)
        self.model.oldest_id = min((s['id'] for s in statuses), key=id_key)
        self.model.newest_id = max((s['id'] for s in statuses), key=id_key)


page = StandIn()
check('hold: empty loading list accepts the stream',
      page._hold_first_stream(status(99)) and len(page._first_stream) == 1)
check('hold: nothing landed yet',
      page.model.get_n_items() == 0 and page.model.oldest_id is None)
page.model.loading = False
page._fill_first(first)
page._flush_first_stream()
ids = [page.model.get_item(i).raw['id'] for i in range(page.model.get_n_items())]
check('flush: first page owns oldest_id',
      page.model.oldest_id == '00001', repr(page.model.oldest_id))
check('flush: streamed post lands on top',
      ids[0] == '00099' and ids[-1] == '00001', repr(ids))
check('flush: hold is empty', not page._first_stream)

page = StandIn()
page._hold_first_stream(status(70))
page._discard_first_stream()
check('discard: clears the hold', not page._first_stream)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
