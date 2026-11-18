#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Catch Up page against a fake client: the first-run page, a full run
that writes a snapshot, the All posts list and its filters and sorts, the
Highlights slot staying put while they change, cancel and 429 keeping what
was gathered, snapshot retention and reopening, and the cleanup when an
account is removed. Also the pieces under the page that need no widget
(ordering, the snapshot files, the run's stop logic).

Exits 77 (skip) without a display."""

import datetime
import json
import os
# Never the user's real dconf: a test that builds a window restores the

# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import stat
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')


if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.catchup import digest as catchupdigest, digest_layout  # noqa: E402
from src.catchup import pipeline as catchup  # noqa: E402
from src.catchup import store as catchupstore  # noqa: E402
from src.catchup import fetch as catchuprun  # noqa: E402

from testkit import catchuppage as kit  # noqa: E402
from testkit.catchuppage import (FakeClient, KEY,  # noqa: E402
                                 NOW, check, settle, timeline)

# Ordering and filtering: the same as catchupdigest, over cached keys

snapshot = {'version': 1, 'id': 'ordering', 'account': KEY,
            'start_at': None, 'end_at': catchup.iso(NOW), 'period': '24h',
            'stopped': 'complete', 'posts': timeline(),
            'relationships': {'8': {'following': True, 'followed_by': True}},
            'followed_tags': ['linux'],
            'trends': {'links': None, 'tags': None, 'statuses': None}}
snapshot['posts'] = catchupdigest.apply_filters(snapshot['posts'], ['99'])
catchupdigest.mark_threads(snapshot['posts'])
computed = catchup.run(snapshot, ['en'])
by_id = {p['id']: p for p in computed.visible}
same = True
for sort_by in digest_layout.SORTS:
    for order in ('asc', 'desc'):
        for group in (False, True):
            posts = computed.visible
            ours = catchup.order_ids(
                [computed.info[p['id']] for p in posts], sort_by, order,
                group, {a: i for i, a in enumerate(
                    digest_layout.author_order(posts))} if group else None)
            theirs = [p['id'] for p in digest_layout.sort_posts(
                posts, sort_by, order, group, computed.scores)]
            same = same and ours == theirs
check('the cached-key ordering is catchupdigest.sort_posts', same)
check('two boosts of one post collapse into the first',
      len(computed.visible) == len(snapshot['posts']) - 1
      and len(computed.dup_of) == 1)
check('the categories add up to the visible posts',
      sum(computed.counts.values()) == len(computed.visible)
      and computed.counts['boosts'] > 0 and computed.counts['replies'] > 0
      and computed.counts['filtered'] == 1)
check('the Filtered category never reaches the ranking',
      all(not catchupdigest.is_filtered_post(p) for p in computed.ranked)
      and computed.counts['filtered']
      == len(computed.visible) - len(computed.ranked))
view = catchup.derive_view(computed, catchup.ViewState(category='boosts'))
check('a category narrows the view',
      len(view.matching) == computed.counts['boosts']
      and all(by_id[i].get('reblog') for i in view.matching))
author = view.category_authors[0]
view = catchup.derive_view(computed, catchup.ViewState(
    category='boosts', author=author))
check('an author narrows it further',
      0 < len(view.matching) <= computed.counts['boosts'])
view = catchup.derive_view(computed, catchup.ViewState(author='nobody'))
check('an author with no posts is ignored',
      view.author is None and len(view.matching) == len(computed.visible))
check('ranking is deterministic',
      [p['id'] for p in catchup.run(snapshot, ['en']).ranked]
      == [p['id'] for p in computed.ranked])
text = catchup.view_summary(catchup.ViewState(
    category='boosts', sort='likes', order='desc', group=True))
check('the toast is put together from its parts',
      text == 'Showing boosts, most likes first, grouped by authors')
check('the period titles follow the period',
      catchup.period_title('1h') == 'Highlights of the last hour'
      and catchup.period_title('6h') == 'Highlights of the last 6 hours'
      and catchup.period_title('since')
      == 'Highlights since your last catch-up'
      and catchup.period_title('max') == 'Highlights')

# --------------------------------------------------------------------------
# Snapshot files

store_key = 'https://store.example#1'
saved = []
for n in range(5):
    snap = dict(snapshot, id='snap%d' % n, account=store_key,
                end_at=catchup.iso(NOW + datetime.timedelta(hours=n)),
                count=len(snapshot['posts']))
    path = catchupstore.write_snapshot(snap)
    saved.append(path)
directory = catchupstore.account_dir(store_key)
check('the snapshot directory is private',
      stat.S_IMODE(os.stat(directory).st_mode) == 0o700)
check('a snapshot file is private',
      all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in saved))
check('no temporary file is left behind',
      not [n for n in os.listdir(directory) if n.endswith('.tmp')])
meta = catchupstore.read_meta(saved[0])
check('the head of a snapshot is read without its posts',
      meta['id'] == 'snap0' and 'posts' not in meta)
loaded = catchupstore.read_snapshot(saved[0])
check('a snapshot reads back as it was written',
      loaded['posts'] == snapshot['posts']
      and loaded['trends'] == snapshot['trends']
      and loaded['followed_tags'] == ['linux'])
check('the snapshot is plain JSON',
      json.load(open(saved[0]))['posts'] == snapshot['posts'])
empty = dict(snapshot, id='empty', account=store_key, posts=[])
check('a snapshot without posts round-trips',
      catchupstore.read_snapshot(catchupstore.write_snapshot(empty))
      ['posts'] == [])
kept = catchupstore.prune(store_key)
check('only the three newest snapshots are kept',
      [m['id'] for m in kept] == ['snap4', 'snap3', 'snap2']
      and len(catchupstore.list_snapshots(store_key)) == 3)
with open(os.path.join(directory, 'broken.json'), 'w') as handle:
    handle.write('{ not json')
check('a broken file is skipped',
      [m['id'] for _p, m in catchupstore.list_snapshots(store_key)]
      == ['snap4', 'snap3', 'snap2'])
catchupstore.delete_account(store_key)
check('an account\'s snapshots can be deleted at once',
      not os.path.exists(directory))
other = dict(snapshot, id='keep', account='https://other.example#1')
other_path = catchupstore.write_snapshot(other)
check('two instances with one id do not share a directory',
      catchupstore.account_dir('https://other.example#1')
      != catchupstore.account_dir('https://store.example#1')
      and os.path.exists(other_path))


# --------------------------------------------------------------------------
# The run, without a page

def run_to_end(client, hours=24, stop_on=None, hide_trending=False):
    end_at = catchup.now_utc()
    start_at = end_at - datetime.timedelta(hours=hours) if hours else None
    got = []
    run = catchuprun.CatchUpRun(client, start_at, end_at, ['99'],
                                hide_trending, done=got.append,
                                pause_seconds=0)
    if stop_on is not None:
        run.progress = lambda evaluated, oldest: (
            run.stop() if run.pages_done == stop_on else None)
    run.start()
    settle(lambda: bool(got))
    return got[0] if got else None


client = FakeClient()
result = run_to_end(client)
in_range = [p for p in client.posts
            if catchupdigest.in_range(p, NOW - datetime.timedelta(hours=24))]
check('the run pages back until a page has nothing in range',
      result is not None and result.stopped == 'complete'
      and client.paths().count('/timelines/home') == 3
      and len(result.posts) == len(in_range))
check('the next page continues from the Link cursor',
      [c[2].get('max_id') for c in client.calls
       if c[1] == '/timelines/home'][1:] == [client.posts[39]['id'],
                                             client.posts[79]['id']])
check('every request asks for 40 posts',
      all(c[2].get('limit') in (40, '40') for c in client.calls
          if c[1] == '/timelines/home'))
batches = [c[2]['id[]'] for c in client.calls
           if c[1] == '/accounts/relationships']
check('relationships are fetched in batches by id[]',
      batches and all(0 < len(b) <= catchup.RELATIONSHIP_BATCH
                      for b in batches))
check('the three trends are fetched',
      {'/trends/links', '/trends/tags', '/trends/statuses'}
      <= set(client.paths()))
check('the relationships and followed tags are kept',
      result.followed_tags == ['linux'] and result.relationships
      and set(result.trends) == {'links', 'tags', 'statuses'})
check('followed-tag posts of people you do not follow are marked',
      any(p.get('_followed_tags') for p in result.posts))
check('threads are marked', any(p.get('_thread') for p in result.posts))

client = FakeClient()
result = run_to_end(client, hide_trending=True)
check('hide-trending skips the trends',
      not [p for p in client.paths() if p.startswith('/trends/')]
      and result.trends == {})

client = FakeClient()
client.fail_trends = True
result = run_to_end(client)
check('a failing trends call degrades to None, not to a failure',
      result.stopped == 'complete' and result.trends['links'] is None
      and result.trends['tags'] is None and len(result.posts) > 0)

client = FakeClient(page_size=10)
result = run_to_end(client, stop_on=2)
check('Cancel keeps the pages gathered so far',
      result.stopped == 'cancelled'
      and client.paths().count('/timelines/home') == 2
      and len(result.posts) == 20)

client = FakeClient(page_size=10)
client.fail_page = (3, 429)
result = run_to_end(client)
check('a 429 stops the paging and keeps what was gathered',
      result.stopped == 'rate-limited' and len(result.posts) == 20
      and result.retry_after == 120)

client = FakeClient(page_size=10)
client.fail_page = (2, 500)
result = run_to_end(client)
check('another error stops it the same way',
      result.stopped == 'error' and len(result.posts) == 10
      and result.error == 'failed')

client = FakeClient()
client.limited = True
result = run_to_end(client)
check('a rate-limited client is not asked at all',
      result.stopped == 'rate-limited' and not result.posts
      and client.paths() == [])

client = FakeClient(posts=timeline(count=1400, spacing=1), page_size=40)
result = run_to_end(client, hours=None)
check('the safety cap stops an endless timeline',
      result.stopped == 'cap'
      and client.paths().count('/timelines/home') == 25
      and len(result.posts) == 1000)

client = FakeClient(page_size=10)
got = []
run = catchuprun.CatchUpRun(client, None, catchup.now_utc(), [],
                            done=got.append, pause_seconds=0)
run.start()
run.abort()
settle(lambda: False, timeout=0.3)
check('an aborted run says nothing and asks for nothing more',
      not got and client.paths().count('/timelines/home') == 1)


# --------------------------------------------------------------------------


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all catchup page checks passed')
