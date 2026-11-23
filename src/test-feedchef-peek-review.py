#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart New Posts, the review round: the cache never evicts the scores of
posts in the list, a window with an unscored post is refused, a peek of
nothing new makes no pill, a recipe page covered by a pushed page does not
peek, the setting alone starts and stops the timer, boost grouping, the
model's head-only diff, and the logs of peeks (discarded when cancelled,
written from copies, a note when a shown peek was never marked). Exits 77
(skip) without a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-peek-review-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import copy  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from datetime import timedelta  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(srcdir)),
                                'bench'))
sys.path.insert(0, srcdir)

from feedchefaudit import evaluate as evalmod  # noqa: E402
from feedchefaudit import fuzz  # noqa: E402
from feedchefaudit.records import Loaded, expand, load  # noqa: E402
from src import (appsettings, feedback, feedchefpeek, sysstate)
from src.statusrun import StatusRunRow  # noqa: E402
from src.testkit import peekkit  # noqa: E402
from src.testkit.peekkit import (Client, FakeMonitor,  # noqa: E402
                                 pump, pump_until,
                                 status)
from src.feedchef import smartmerge  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)



cap_recipe = fuzz.default_recipe('caps')
cap_recipe.kitchen.modifiers = [Modifier(
    type='diversity', max_per_cluster=1, diversity_dimensions=['author'])]


def post(sid, author, fav=1, minutes=30):
    return {'id': sid, 'created_at': (
        fuzz.NOW - timedelta(minutes=minutes)).isoformat(),
        'content': '<p>a plain note about the weather today</p>',
        'spoiler_text': '', 'sensitive': False, 'language': 'en',
        'favourites_count': fav, 'reblogs_count': 0, 'replies_count': 0,
        'account': {'id': author}, 'tags': [], 'media_attachments': []}


Adw.init()
storagemod.set_root(os.path.join(_TMP, 'recipes'))
feedback.show_toast = lambda *_args, **_kwargs: None
monitor = FakeMonitor()
sysstate._monitor = monitor
settings = appsettings.settings()
account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
client = Client()
window = Gtk.Window(default_width=900, default_height=700)
window.present()


def make_page(first_posts=6):
    return peekkit.make_page(window, account, client, first_posts)


# --- review round: eviction, scoreless windows, covered pages, ... --------

settings.set_boolean('feed-chef-enabled', True)

# QA1: three pages of 40 and a peek; the list's scores are never evicted.
recipe = default_recipe('Evict')
shown_ids = set()
cache = smartmerge.ScoreCache(keep=shown_ids.__contains__)
posts120 = [status(n) for n in range(1, 121)]
for index, item in enumerate(posts120):
    shown_ids.add(item['id'])
    cache.put(item['id'], 2.0 - index * 0.01)
check('evict: the scores of every post in the list are still cached (%d)'
      % len(cache), len(cache) == 120 and all(
          p['id'] in cache for p in posts120))
newcomer = status(500)
shown_ids.add('00500')
cache.put('00500', 5.0)
merged = smartmerge.merge_head(recipe, posts120, [newcomer], cache)
mids = [p['id'] for p in merged]
check('evict: the old top 50 keep their relative order after the merge',
      mids[:51] == ['00500'] + [p['id'] for p in posts120[:50]]
      and mids[51:] == [p['id'] for p in posts120[50:]])
stale = smartmerge.ScoreCache()
for index, item in enumerate(posts120):
    stale.put(item['id'], 2.0 - index * 0.01)
check('evict: a cache with no keep still forgets the oldest (the bug)',
      len(stale) == 100 and '00001' not in stale)
check('evict: ids no longer shown are evicted when the cache is full',
      (lambda c: ([c.put('x%d' % n, 1.0) for n in range(120)],
                  len(c) == 100 and 'x0' not in c)[1])(
          smartmerge.ScoreCache(keep=lambda sid: False)))
unscored = smartmerge.merge_violations(
    recipe, before=[p['id'] for p in posts120], inserted=['00500'],
    after=mids, peek_order=['00500'],
    statuses={p['id']: p for p in [*posts120, newcomer]}, cache=stale)
check('evict: a merge whose window has an unscored post is refused',
      'merge_unscored' in unscored)

# QA1 on the page: scores of three pages stay while a peek lands.
page, engine, view = make_page()
source = page.source
for number in range(7, 125):
    source.shown.add('%05d' % number)
    source.scores.put('%05d' % number, 0.01)
check('evict: the source keeps what the list shows past 100 entries',
      all(('%05d' % n) in source.scores for n in range(1, 125)))

# Pill: nothing visible has a score, so no pill.
source.scores.clear()
engine.peek_queue = [([status(70)], {'00070': 9.0}, {'user70': 1})]
page._on_auto_refresh()
pump_until(lambda: source.peeked is not None)
pump(10)
check('pill: with no visible post that has a score there is no pill',
      not page.new_posts_revealer.get_reveal_child())
source.refuse_peek()

# QA3: a peek of posts the list already has: no pill, the peek is dropped.
page, engine, view = make_page()
source = page.source
engine.peek_queue = [([status(1), status(2)], {'00001': 9.0, '00002': 9.0},
                      {'user1': 1})]
page._on_auto_refresh()
pump_until(lambda: engine.loggers and any(
    ('discard', 'fake-engine_0') in each.log for each in engine.loggers))
pump(10)
check('peek: posts the list already has make no pill and the peek is '
      'dropped', not page.new_posts_revealer.get_reveal_child()
      and source.peeked is None)

# The announcement says what the pill says.
announced = []
feedback.announce = lambda _widget, text: announced.append(text)
engine.peek_queue = [([status(80), status(81)],
                      {'00080': 0.99, '00081': 0.98}, {'user80': 1})]
page._on_auto_refresh()
pump_until(lambda: page.new_posts_revealer.get_reveal_child())
check('pill: the announcement is the label (%s)' % announced[-1:],
      announced and announced[-1] == page.new_pill.get_label()
      == '2 New Posts Worth Seeing')
check('pill: the count is of posts the list would take',
      page._pill_count == 2)
page._on_new_pill_clicked(None)

# The rows the list reports on screen.
rows = feedchefpeek._visible_rows(page.list_view)
check('rows: the shells under the list are found (%d)' % len(rows),
      rows and all(isinstance(r, StatusRunRow) for r in rows)
      and any(r.get_mapped() for r in rows))
check('rows: the viewport statistics read their posts',
      len(page._visible_scores()) >= 1)

# QA4: a page pushed over the recipe covers it.
page, engine, view = make_page()
check('covered: the timer runs while the page is the visible one',
      page._auto_refresh_id != 0 and feedchefpeek.paused_by(page) == '')
other = Adw.NavigationPage(title='Other', child=Gtk.Label(label='other'))
view.push(other)
pump(20)
check('covered: a page pushed over it removes the timer (%s)'
      % feedchefpeek.paused_by(page), page._auto_refresh_id == 0)
page._on_auto_refresh()
check('covered: a tick while covered peeks at nothing', engine.peeks == [])
view.pop()
pump(20)
check('covered: popping brings the timer back', page._auto_refresh_id != 0)

# Nit 5: the setting alone starts and stops the timer.
settings.set_boolean('feed-chef-enabled', False)
check('setting: turning Feed Chef off removes the timer at once',
      page._auto_refresh_id == 0)
settings.set_boolean('feed-chef-enabled', True)
check('setting: turning it on while the page is visible starts it',
      page._auto_refresh_id != 0)

# TL1: boost grouping makes carousel rows; the merge must not raise.
page, engine, view = make_page()
page.model.group_boosts_enabled = True
boosted = []
for number in range(200, 212):
    post_ = status(number)
    if number % 3 == 0:
        post_['reblog'] = status(number + 1000)
    boosted.append(post_)
engine.peek_queue = [(boosted, {p['id']: 0.99 for p in boosted},
                      {'user200': 1})]
page._on_auto_refresh()
pump_until(lambda: page.new_posts_revealer.get_reveal_child()
           or page.source.peeked is not None)
pump(10)
held_before = page.source.peeked is not None
before_n = page.model.get_n_items()
try:
    page._on_new_pill_clicked(None)
    raised = ''
except Exception as error:  # the bug: KeyError from a carousel row
    raised = repr(error)
check('boosts: a peek with a boosts carousel does not raise (%s)' % raised,
      not raised)
check('boosts: a window with a carousel row (no score) is refused whole',
      held_before and page.model.get_n_items() == before_n
      and page.source.peeked is None)

# TL2: only the head window is diffed; the tail is never touched.
from src.timelinedata import TimelineModel  # noqa: E402
model = TimelineModel()
model.append_old([status(n) for n in range(1, 71)])
tail_before = [model.get_item(i) for i in range(50, 70)]
splices = []
model.connect('items-changed', lambda _m, pos, rem, add: splices.append(
    (pos, rem, add)))
fresh_ = model.unseen([status(900)])
model.merge_head(fresh_, lambda items: items[::-1], 50)
check('model: only the window is announced (%s)' % splices[:3],
      splices and all(pos + rem <= 50 for pos, rem, _a in splices))
check('model: the rows beyond the window are the same objects, in place',
      [model.get_item(i + 1) for i in range(50, 70)] == tail_before)

# Nit 6 / TL3 / TL4: the logs, with the real engine.
logmod.set_root(os.path.join(_TMP, 'logs-review'))
review_world = {'scope': [post('q1', 'b1', 4), post('q2', 'b2', 3)]}
engine = FeedEngine(
    account_key='h#1', self_account_id='me',
    fetch_scope=lambda max_id, count: list(review_world['scope']),
    fetch_trends=lambda: kitchenmod.ServerTrendContext(
        trending_hashtags={}, trending_status_ids=set()),
    fetch_following_page=lambda max_id: ([], None))
engine.clock = lambda: fuzz.NOW
review_first = engine.process_feed_sync(
    cap_recipe, count=40, cook_id='cr', page_index=0)
review_dir = os.path.join(_TMP, 'logs-review')
files_before = set(os.listdir(review_dir))
review_world['scope'] = [post('q3', 'b3', 9)]
cancelled = threading.Event()
engine.process_feed(
    cap_recipe, count=40, on_done=lambda r: None,
    on_error=lambda e: None, author_counts=review_first.author_counts,
    cook_id='cr', page_index=1001, peek=True, exclude=frozenset(),
    hide=lambda s: engine.cancel() or False)
engine.run_background(cancelled.set)
cancelled.wait(10)
check('discard: a peek cancelled while it cooks leaves no log or sidecar '
      '(%s)' % sorted(set(os.listdir(review_dir)) - files_before),
      set(os.listdir(review_dir)) == files_before)
engine.shutdown()
ran = threading.Event()
engine.run_background(ran.set)
check('discard: work for a shut-down engine still runs (a thread does it)',
      ran.wait(5))

review_world['scope'] = [post('q3', 'b3', 9), post('q4', 'b4', 8)]
engine2 = FeedEngine(
    account_key='h#1', self_account_id='me',
    fetch_scope=lambda max_id, count: list(review_world['scope']),
    fetch_trends=lambda: kitchenmod.ServerTrendContext(
        trending_hashtags={}, trending_status_ids=set()),
    fetch_following_page=lambda max_id: ([], None))
engine2.clock = lambda: fuzz.NOW
got = threading.Event()
box = {}
engine2.process_feed(
    cap_recipe, count=40, on_done=lambda r: (box.update(r=r), got.set()),
    on_error=lambda e: got.set(), author_counts=review_first.author_counts,
    cook_id='cr', page_index=1002, peek=True, exclude=frozenset())
got.wait(10)
res = box['r']
logger = res.logger
statuses_r = {p['id']: p for p in res.items}
cache_r = smartmerge.ScoreCache()
for sid, score in res.score_map.items():
    cache_r.put(sid, score)
merge_r = smartmerge.merge_record(
    cap_recipe, inserted=[p['id'] for p in res.items],
    after=[p['id'] for p in res.items], statuses=statuses_r, cache=cache_r)
check('logger: marked shown in memory without writing',
      logger.mark_shown(merge_r, write=False))
write = logger.writer()
logger.record['late'] = 'changed after the copy was taken'
logger.record['merge'] = {'window': 1}
write()
written = load(logger._path).record
check('logger: the worker writes the copy taken before the page moved on',
      written['outcome']['status'] == 'shown'
      and 'late' not in written and written['merge']['window'] == 50)
engine2.shutdown()
engine2._reach_executor.shutdown(wait=True)

# TL4: a peek whose posts were shown but whose log still says peek.
pages_r = [load(path) for path in expand([review_dir])]
peek_pages = [p for p in pages_r if p.record['outcome']['status'] == 'peek'
              or p.record['page_index'] == 1002]
mark = next(p for p in peek_pages if p.record['page_index'] == 1002)
unmarked = copy.deepcopy(mark.record)
unmarked['outcome'] = {'status': 'peek', 'error': ''}
unmarked.pop('merge', None)
unmarked['outputs']['author_counts_out'] = {'b1': 1, 'b3': 1, 'b4': 1}
unmarked['params']['author_counts_in'] = {'b1': 1}
later = copy.deepcopy(review_first.logger.record)
later['page_index'] = 1
later['params']['author_counts_in'] = {'b1': 1, 'b3': 1, 'b4': 1}
note_tally = evalmod.Tally()
evalmod._note_unmarked_peeks(note_tally, [
    Loaded(mark.path, {'audit': unmarked}, unmarked, None),
    Loaded(mark.path, {'audit': later}, later, None)])
check('audit: a peek whose posts look shown but is not marked is noted '
      '(%s)' % note_tally.notes, len(note_tally.notes) == 1)
plain_tally = evalmod.Tally()
evalmod._note_unmarked_peeks(plain_tally, [
    Loaded(mark.path, {'audit': unmarked}, unmarked, None)])
check('audit: a peek nobody built on is not noted',
      not plain_tally.notes)

print()
print('%d failure(s)' % len(failures) if failures else 'all passed')
sys.exit(1 if failures else 0)
