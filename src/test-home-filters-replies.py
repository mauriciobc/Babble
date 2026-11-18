#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The home timeline's filter of replies from people the reader does not
follow: authors asked about in batches of 40, and streamed replies held
until their authors are known, in arrival order, through deletes, edits,
a superseding refresh and a page freed before the answer. Exits 77 (skip)
without a display."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import gc
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import GLib  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import appsettings  # noqa: E402

# check() is the kit's, so a failed check lands in kit.failures, the list
# the exit gate reads.
from testkit import homefilterskit as kit  # noqa: E402
from testkit.homefilterskit import (  # noqa: E402
    _RelationshipClient, boost, check, home_page, ids, reply, status)

appsettings.settings().reset('hide-replies-non-follows')


# More unknown authors than one call takes: /accounts/relationships answers
# 40 ids a call, so a page of 45 strangers needs two, and an author past
# the first 40 must still be asked about (a followed one stays visible).
class _BatchedClient(_RelationshipClient):
    # Everyone but the first author. The server asks about a set, so which
    # 40 an unbatched lookup would pick is arbitrary: with all but one
    # followed, any author left unasked is a reply wrongly hidden.
    followed = {str(a) for a in range(102, 146)}

    def __init__(self, posts):
        super().__init__(posts)
        self.batches = []

    def request_async(self, method, path, params=None, callback=None, **_kw):
        if path != '/accounts/relationships':
            return super().request_async(method, path, params=params,
                                         callback=callback, **_kw)
        self.batches.append(list(params['id[]']))
        callback(self, [{'id': i, 'following': i in self.followed}
                        for i in params['id[]']])


appsettings.settings().set_boolean('hide-replies-non-follows', True)
authors = list(range(101, 146))                       # 45 distinct
client = _BatchedClient([[reply(200 - n, a) for n, a in enumerate(authors)]])
page = home_page(client, False)
asked = [i for batch in client.batches for i in batch]
check('45 unknown authors are asked about in batches of at most 40',
      client.batches and all(len(b) <= 40 for b in client.batches)
      and len(client.batches) == 2)
check('every one of them was asked about, once',
      sorted(asked) == sorted(str(a) for a in authors))
check('every followed author keeps their reply, whichever batch they were in',
      len(ids(page)) == 44)
check('the one stranger is hidden', '%05d' % 200 not in ids(page))
check('the page left the loading state (batched)', not page.model.loading)
appsettings.settings().reset('hide-replies-non-follows')


# The same filter, on the stream. A reply that comes off the stream has to
# be kept out of the timeline as well, and the only way to know whether the
# reader follows its author is to ask the server, as the paged path does.
# The post waits for the answer, and so do the posts behind it, so that the
# timeline keeps the order the posts arrived in.
class _HeldRelationships(_RelationshipClient):
    """A server that holds each /accounts/relationships answer."""

    def __init__(self, posts):
        super().__init__(posts)
        self.held = []

    def request_async(self, method, path, params=None, callback=None, **_kw):
        if path != '/accounts/relationships':
            return super().request_async(method, path, params=params,
                                         callback=callback, **_kw)
        self.held.append((list(params['id[]']), callback))

    def request_finish(self, result):
        # The real client raises the error it was handed; a fake that
        # returned it would hide the error path.
        if isinstance(result, GLib.Error):
            raise result
        return result

    def answer(self, index, following=(), error=False):
        if index >= len(self.held):
            # A scenario that never sent its lookup fails here, by name,
            # and the checks after it still run.
            check('lookup %d was sent before it was answered' % index, False)
            return
        ids_, callback = self.held[index]
        if error:
            callback(self, GLib.Error('lookup failed'))
        else:
            callback(self, [{'id': i, 'following': i in following}
                            for i in ids_])


def held_page(posts=None):
    client = _HeldRelationships([posts or [status(9)]])
    return client, home_page(client, False)


appsettings.settings().set_boolean('hide-replies-non-follows', True)
client, page = held_page()
page.on_stream_update(reply(10, 21))
check('a streamed reply waits for the server\'s answer',
      '00010' not in ids(page) and len(client.held) == 1)
page.on_stream_update(status(11))
page.on_stream_update(status(12))
check('the posts behind it wait too, so the order is kept',
      '00011' not in ids(page) and '00012' not in ids(page))
client.answer(0)
check('a reply from a stranger is dropped from the stream',
      '00010' not in ids(page))
check('the posts behind it land, in the order they arrived',
      [int(i) for i in ids(page)][:3] == [12, 11, 9])
check('nothing is left queued',
      not page._stream_pending and not page._stream_inflight)

client, page = held_page()
page.on_stream_update(reply(10, 21))
client.answer(0, following=('21',))
check('a streamed reply from someone the reader follows is kept',
      '00010' in ids(page))
page.on_stream_update(reply(11, 21))
check('and the next one needs no lookup: the author is known',
      '00011' in ids(page) and len(client.held) == 1)

client, page = held_page()
page.on_stream_update(boost(10, 1))
page.on_stream_update(status(11))
check('a streamed boost and a plain post are not asked about',
      not client.held and '00011' in ids(page))

# A lookup is out, and a reply by someone else arrives: its author is asked
# about next, and nothing lands before every author is known.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(reply(11, 22))
check('the lookup asks about the author it knows about',
      len(client.held) == 1 and client.held[0][0] == ['21'])
client.answer(0, following=('21', '22'))
check('and the author that arrived while it was out is asked about next',
      len(client.held) == 2 and client.held[1][0] == ['22'])
check('the post queued behind the first still waits',
      '00010' in ids(page) and '00011' not in ids(page))
client.answer(1, following=('22',))
check('then both land, in order',
      [int(i) for i in ids(page)][:3] == [11, 10, 9])

# More authors than one call takes: sent in forties, landing once.
client, page = held_page()
for n in range(45):
    page._stream_pending.append(reply(300 + n, 400 + n))
page._ask_about_stream_authors()
check('streamed authors are asked about in batches of at most 40',
      len(client.held) == 1 and len(client.held[0][0]) == 40)
client.answer(0, following=('444',))
check('the next batch goes out when the first one is answered',
      len(client.held) == 2 and len(client.held[1][0]) == 5
      and '00344' not in ids(page))
client.answer(1, following=('444',))
check('a follow beyond the first 40 keeps its streamed reply',
      [i for i in ids(page) if i != '00009'] == ['00344'])

# A failed lookup drops the reply, as on the paged path.
client, page = held_page()
page.on_stream_update(reply(10, 21))
client.answer(0, error=True)
check('a streamed reply whose lookup failed is dropped',
      '00010' not in ids(page) and not page._stream_pending
      and not page._stream_inflight)
page.on_stream_update(status(11))
check('and the next post is not held up behind it', '00011' in ids(page))

# Superseded before the answer: nothing is inserted and nothing raises.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page._stream_scope.begin()
raised = None
try:
    client.answer(0, following=('21',))
except Exception as error:      # noqa: BLE001
    raised = error
check('a superseded lookup raises nothing', raised is None)
check('and inserts nothing', '00010' not in ids(page))
check('and the queue is released',
      not page._stream_pending and not page._stream_inflight)
page.on_stream_update(reply(12, 21))
check('a later reply asks again', len(client.held) == 2)
client.answer(1, following=('21',))
check('and lands once that lookup is current', '00012' in ids(page))

# The page freed before the answer: nothing raises, nothing is inserted.
client, page = held_page()
page.on_stream_update(reply(10, 21))
model = page.model
page_ref = page.weak_ref()
del page
gc.collect()
raised = None
try:
    client.answer(0, following=('21',))
except Exception as error:      # noqa: BLE001
    raised = error
check('a lookup answered after its page was freed raises nothing',
      raised is None)
check('the page was freed', page_ref() is None)
check('and nothing reached its model',
      not any(hasattr(model.get_item(i), 'raw')
              and model.get_item(i).raw['id'] == '00010'
              for i in range(model.get_n_items())))

def content_of(page, number):
    for i in range(page.model.get_n_items()):
        item = page.model.get_item(i)
        if hasattr(item, 'raw') and item.raw['id'] == '%05d' % number:
            return item.raw['content']
    return None


# A held post that is deleted before the lookup answers must not land.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(status(11))
page.on_stream_delete('00010')
page.on_stream_delete('00011')
client.answer(0, following=('21',))
check('held posts deleted before the answer never land',
      ids(page) == ['00009'])
check('and the queues are empty',
      not page._stream_pending and not page._stream_inflight)

# Deleted while still queued behind the lookup (not yet asked about).
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(reply(11, 22))
page.on_stream_delete('00011')
client.answer(0, following=('21',))
check('a post deleted while queued behind the lookup is dropped too',
      ids(page) == ['00010', '00009'] and len(client.held) == 1)

# A held boost whose original is deleted goes with it.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(boost(11, 5))
page.on_stream_delete('00005')
client.answer(0, following=('21',))
check('a held boost of a deleted post is dropped',
      '00011' not in ids(page) and '00010' in ids(page))

# An edit while held: the edit wins.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(status(11))
edited = status(11)
edited['content'] = '<p>EDITED</p>'
page.on_stream_status_update(edited)
client.answer(0, following=('21',))
check('a held post edited before it lands lands as edited',
      content_of(page, 11) == '<p>EDITED</p>')

# An edit that turns a held reply into a plain post: landing decides on the
# latest version, so a stranger's former reply is kept.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(reply(11, 22))
page.on_stream_status_update(status(11))
client.answer(0, following=('21',))
check('a held reply edited into a plain post lands',
      '00011' in ids(page))

# And one that turns a plain post into a reply by someone nobody asked
# about: it goes to a lookup of its own and is not let through.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page.on_stream_update(status(11))
page.on_stream_status_update(reply(11, 23))
client.answer(0, following=('21',))
check('a held post edited into a reply asks about its new author',
      len(client.held) == 2 and client.held[1][0] == ['23']
      and '00011' not in ids(page) and '00010' in ids(page))
client.answer(1)
check('and a stranger\'s edited reply is dropped',
      '00011' not in ids(page))

# Held, then the page starts catching up: nothing lands, the cursor stays.
for flag in ('_catching', '_resumed', '_resume_pending'):
    client, page = held_page()
    page.on_stream_update(reply(10, 21))
    cursor = page.model.newest_id
    setattr(page, flag, True)
    client.answer(0, following=('21',))
    check('held posts are dropped when %s is set by the answer' % flag,
          '00010' not in ids(page) and page.model.newest_id == cursor)
    setattr(page, flag, False)

# The filters are read again at landing.
client, page = held_page()
page.on_stream_update(reply(10, 21))
page._hide_replies_non_follows = False
client.answer(0)
check('a reply held while the filter was on lands once it is off',
      '00010' in ids(page))

# A burst lands in one update.
client, page = held_page()
calls = {'insert': 0, 'announce': 0}
real_insert = page.model.insert_new
real_announce = page._announce_new_posts


def counting_insert(statuses):
    calls['insert'] += 1
    return real_insert(statuses)


def counting_announce(count):
    calls['announce'] += 1
    return real_announce(count)


page.model.insert_new = counting_insert
page._announce_new_posts = counting_announce
page.on_stream_update(reply(10, 21))
page.on_stream_update(status(11))
page.on_stream_update(status(12))
client.answer(0, following=('21',))
check('three held posts land in arrival order',
      [int(i) for i in ids(page)][:4] == [12, 11, 10, 9])
check('with one insert and one announcement (%r)' % calls,
      calls == {'insert': 1, 'announce': 1})

# The queue is bounded: past the limit it is dropped and the page refreshes
# (min_id above what it shows), so the posts are fetched and not lost.
client, page = held_page()
page.STREAM_HOLD_LIMIT = 3
asked_before = len(client.params)
for n in range(5):
    page.on_stream_update(reply(10 + n, 21 + n))
check('past the limit the queue is dropped',
      not page._stream_pending)
check('and the page refreshes from what it shows',
      len(client.params) == asked_before + 1
      and client.params[-1].get('min_id') == '00009')
client.answer(0, following=('21',))
check('the lookup already out still lands its own post',
      ids(page)[:2] == ['00010', '00009'])

# Filter off: the reply lands without a lookup.
appsettings.settings().set_boolean('hide-replies-non-follows', False)
client, page = held_page()
page.on_stream_update(reply(10, 21))
check('with the filter off the reply lands without a lookup',
      '00010' in ids(page) and not client.held)
appsettings.settings().reset('hide-replies-non-follows')

appsettings.settings().reset('hide-boosts-home')
if kit.failures:
    print('%d failed' % len(kit.failures))
    sys.exit(1)
print('all home filter reply checks passed')
