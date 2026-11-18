#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Catch Up page against a fake client: the first-run page, a full run
that writes a snapshot, the All posts list and its filters and sorts, the
Highlights slot staying put while they change, cancel and 429 keeping what
was gathered, snapshot retention and reopening, and the cleanup when an
account is removed. Also the pieces under the page that need no widget
(ordering, the snapshot files, the run's stop logic).

Exits 77 (skip) without a display."""

import json
import os
# Never the user's real dconf: a test that builds a window restores the

# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import shutil
import stat
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import GLib, Gtk  # noqa: E402

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

from testkit import catchuppage as kit  # noqa: E402
from testkit.catchuppage import (CACHE, FakeClient, KEY,  # noqa: E402
                                 StubHighlights,
                                 check, in_range, listed_ids, make_page,
                                 posts_listed, settle)

# The page













shutil.rmtree(CACHE, ignore_errors=True)
os.makedirs(CACHE)
catchupstore.set_root(os.path.join(CACHE, 'catch-up'))

# Opening the page pre-warms the ranker's tokenizer patterns on a worker
# thread (rank_text.prewarm), so the first ranking does not pay for them.
import threading                                       # noqa: E402
from src.catchup import rank_text                      # noqa: E402

prewarm_calls = []
prewarm_saved = rank_text.prewarm
rank_text.prewarm = lambda: prewarm_calls.append(
    threading.current_thread() is threading.main_thread())
try:
    probe_page, probe_host = make_page(FakeClient())
    settle(lambda p=probe_page: prewarm_calls and p.state != 'loading')
finally:
    rank_text.prewarm = prewarm_saved
check('opening the page pre-warms the patterns once, off the main thread',
      prewarm_calls == [False])
del probe_page, probe_host

client = FakeClient()
page, host = make_page(client)
settle(lambda: page.state != 'loading')
check('a first run shows the Catch Up page, not a fetch',
      page.state == 'first-run' and client.calls == [])
check('the first-run page has the period menu and a Catch Up button',
      page.catch_up_button.has_css_class('suggested-action')
      and page.first_run_page.get_title() == 'Catch Up'
      and page._period_buttons[0].get_label() == 'Last 24 hours')
page._period_action.change_state(GLib.Variant('s', '6h'))
check('choosing a period on the first run only selects it',
      page.state == 'first-run' and client.calls == []
      and page._period_buttons[0].get_label() == 'Last 6 hours')
page._period_action.change_state(GLib.Variant('s', '24h'))

page.catch_up_button.emit('clicked')
check('pressing Catch Up scans the timeline', page.state == 'scanning')
settle(lambda: page.state == 'results')
check('a finished run shows the results', page.state == 'results')
files = catchupstore.list_snapshots(KEY)
check('it saved one snapshot, readable only by the user',
      len(files) == 1
      and stat.S_IMODE(os.stat(files[0][0]).st_mode) == 0o600
      and stat.S_IMODE(os.stat(os.path.dirname(files[0][0])).st_mode)
      == 0o700)
check('the snapshot carries the period and the stop reason',
      files[0][1]['period'] == '24h' and files[0][1]['stopped'] == 'complete'
      and files[0][1]['count'] == len(in_range(client)))
check('the list holds one row per visible post',
      posts_listed(page) == len(page._computed.visible)
      and page.store.get_n_items() == len(page._computed.visible) + 1)
check('the title follows the period',
      page.title_label.get_label() == 'Highlights of the last 24 hours')
check('the posts bar has a dot per post',
      page.bar.bins is None
      and len(page.bar.dots) == len(page._computed.snapshot['posts']))
check('author chips list the authors, most active first',
      sum(1 for c in page.chips if c.get_visible()) == 6
      and page.chips[0].account_id == page._view.category_authors[0])
check('the category counts are on the toggles',
      page.category_group.get_toggle(0).get_label()
      == 'All (%d)' % len(page._computed.visible))
check('the Highlights slot got the digest once',
      len(StubHighlights.instances) == 1
      and len(StubHighlights.instances[0].digests) == 1)
digest = StubHighlights.instances[0].digests[0]
# 7: the expanded pages are kept per snapshot id.
check('the Highlights starts at the first pages',
      StubHighlights.instances[0].pages[-1] == 1)

check('the digest is ranked, filtered and shares the list\'s items',
      isinstance(digest, catchup.Digest)
      and all(not catchupdigest.is_filtered_post(p) for p in digest.posts)
      and digest.items[digest.posts[0]['id']]
      is page._items[digest.posts[0]['id']]
      and digest.end_at == page._computed.end_at
      and digest.scores[digest.posts[0]['id']] is not None)
check('the digest carries the trends and the boosters',
      digest.trending_keys and digest.news and digest.tags
      and digest.boosters)

total = posts_listed(page)
page.category_group.set_active_name('boosts')
check('a category filters the list',
      0 < posts_listed(page) == page._computed.counts['boosts'])
check('the filter only touched the list models',
      len(StubHighlights.instances[0].digests) == 1
      and StubHighlights.instances[0].digests[0] is digest)
check('the posts bar highlights the matching posts',
      page.bar.matching is not None
      and len(page.bar.matching) >= posts_listed(page))
page.category_group.set_active_name('all')
check('All brings every post back', posts_listed(page) == total)

author = page._author_ids[1]
page.author_dropdown.set_selected(1)
check('an author filters the list',
      0 < posts_listed(page) < total and page._view_state.author == author)
check('the chip of the author is down',
      [c for c in page.chips if c.get_active()][0].account_id == author)
page._on_reset(None)
check('Reset filters restores everything',
      posts_listed(page) == total and page._view_state.is_default()
      and page.bar.matching is None)

ids = listed_ids(page)
check('by default the list is oldest first',
      ids == sorted(ids))
page.sort_dropdown.set_selected(digest_layout.SORTS.index('likes'))
likes = [page._items[i].favourite_count for i in listed_ids(page)]
check('sorting by likes puts the most liked first',
      likes == sorted(likes, reverse=True)
      and page._view_state.order == 'desc')
page.order_group.set_active_name('asc')
likes = [page._items[i].favourite_count for i in listed_ids(page)]
check('the order can be flipped', likes == sorted(likes))
page.sort_dropdown.set_selected(digest_layout.SORTS.index('relevance'))
scores = [page._computed.info[i].values[5] for i in listed_ids(page)]
check('relevance sorts by the score',
      scores == sorted(scores, reverse=True))
page._on_reset(None)

page.group_switch.set_active(True)
authors_listed = [page._computed.info[i].author for i in listed_ids(page)]
runs = 1 + sum(1 for a, b in zip(authors_listed, authors_listed[1:],
                                  strict=False) if a != b)
check('grouping puts each author\'s posts together',
      runs == len(set(authors_listed)))
check('the groups are ordered by post count',
      authors_listed[0] == page._view.category_authors[0])
check('the list has section headers while grouped',
      page.sorted.get_section_sorter() is page.section_sorter)
page.group_switch.set_active(False)
check('ungrouping drops them', page.sorted.get_section_sorter() is None)
check('the Highlights digest never changed',
      len(StubHighlights.instances[0].digests) == 1)

page._toast_id = 0
page.category_group.set_active_name('replies')
page.sort_dropdown.set_selected(digest_layout.SORTS.index('boosts'))
settle(lambda: page._toast_text != '')
check('the filter change is announced from its parts',
      page._toast_text.startswith('Showing replies, most boosts first'))
page._on_reset(None)
settle(lambda: False, timeout=0.6)

# Keys
page.category_group.set_active_name('all')
page.list_view.scroll_to(1, Gtk.ListScrollFlags.NONE, None)
check('j and k need a post in focus to move from, and stop at the ends',
      page._key_previous_post() in (True, False))

# Reopening: a new page opens the newest snapshot straight away
page2, host2 = make_page(FakeClient())
settle(lambda: page2.state != 'loading')
check('later visits open the newest snapshot',
      page2.state == 'results'
      and page2._snapshot_id == files[0][1]['id']
      and posts_listed(page2) == total)
check('and give the same ranking',
      [p['id'] for p in page2._computed.ranked]
      == [p['id'] for p in page._computed.ranked])
check('the menu now offers the last catch-up',
      'since' in page2._period_choices())
page2.cancel()

# Choosing the period already shown does nothing; another one runs
client = page.client
before = len(client.calls)
page._period_action.change_state(GLib.Variant('s', '24h'))
check('the period already shown does nothing',
      page.state == 'results' and len(client.calls) == before)
page._period_action.change_state(GLib.Variant('s', 'since'))
check('choosing another period runs a new catch-up',
      page.state == 'scanning')
settle(lambda: page.state == 'results')
check('"since the last catch-up" ends at the last snapshot',
      page._period == 'since'
      and page.title_label.get_label()
      == 'Highlights since your last catch-up')
check('it is a second snapshot',
      len(catchupstore.list_snapshots(KEY)) == 2)
check('and the digest was rebuilt for it',
      len(StubHighlights.instances[0].digests) == 2)
check('few posts raise the low-activity banner',
      page.low_banner.get_revealed()
      and 'Only' in page.low_banner.get_title()
      and page.low_banner.get_button_label() == 'Catch up on 24 hours')

# Retention
for hours in ('1h', '3h', '6h'):
    page._period_action.change_state(GLib.Variant('s', hours))
    settle(lambda hours=hours: page.state == 'results'
           and page._period == hours)
    settle(lambda: len(page._metas) <= 3 and page.state == 'results')
check('only three snapshots are kept per account',
      len(catchupstore.list_snapshots(KEY)) == 3 and len(page._metas) == 3)
check('the previous catch-ups are in the menu',
      page._metas[0]['id'] == page._snapshot_id)

# Showing an older one, removing one
shown_pages = page._snapshot_id
older = page._metas[1]['id']
# 7: the pages the user had expanded come back with the snapshot, and
# another snapshot's do not leak into it.
page._on_pages_changed(4)
check('expanding the Highlights is remembered for the snapshot',
      page._view_state.pages == 4
      and page._view_states[shown_pages].pages == 4)
page._period = '1h'
page._on_show_snapshot(None, GLib.Variant('s', older))
settle(lambda: page.state == 'results' and page._snapshot_id == older)
check('a previous catch-up can be shown', page._snapshot_id == older)
check('period state comes from the opened snapshot',
      page._period == next(m['period'] for m in page._metas
                           if m['id'] == older)
      and page._period_action.get_state().get_string() == page._period)
check('the older one starts at its own pages',
      StubHighlights.instances[0].pages[-1] == 1)
page._period = '1h'
page._on_show_snapshot(None, GLib.Variant('s', shown_pages))
settle(lambda: page.state == 'results' and page._snapshot_id == shown_pages)
check('period state restores independently for the other snapshot',
      page._period == next(m['period'] for m in page._metas
                           if m['id'] == shown_pages)
      and page._period_action.get_state().get_string() == page._period)
check('and they come back when that snapshot is shown again',
      StubHighlights.instances[0].pages[-1] == 4)
page._on_pages_changed(1)
page._remove_snapshot(older)
settle(lambda: page.state == 'results' and page._snapshot_id != older)
check('removing the shown one opens the newest left',
      len(catchupstore.list_snapshots(KEY)) == 2
      and page._snapshot_id == page._metas[0]['id'])

# A snapshot that will not open is the one dropped from the menu, not the
# one still on screen.
shown = page._snapshot_id
broken = [m['id'] for m in page._metas if m['id'] != shown][0]
with open(catchupstore.snapshot_path(KEY, broken), 'w') as handle:
    handle.write('{ this is not a snapshot')
before = [m['id'] for m in page._metas]
page._on_show_snapshot(None, GLib.Variant('s', broken))
settle(lambda: page.state == 'error')
after = [m['id'] for m in page._metas]
check('a snapshot that will not open drops itself, not the shown one',
      shown in after and broken not in after
      and set(before) - set(after) == {broken})
page._on_show_snapshot(None, GLib.Variant('s', shown))
settle(lambda: page.state == 'results' and page._snapshot_id == shown)

# What is on disk is JSON a third party can read, including the minimal
# snapshot with nothing but metadata and posts.

minimal = {'version': 1, 'id': 'min', 'account': KEY,
           'posts': [{'id': '1'}, {'id': '2'}]}
minimal_path = catchupstore.write_snapshot(minimal)
try:
    json.loads(open(minimal_path, encoding='utf-8').read())
    parses = True
except ValueError:
    parses = False
check('a minimal snapshot on disk is valid JSON', parses)
check('and reads back unchanged',
      catchupstore.read_snapshot(minimal_path) == minimal)


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all catchup snapshots checks passed')
