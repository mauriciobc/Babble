#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart New Posts (Java section 12): the LRU of scores, the threshold of
the pill (mean plus one standard deviation of the posts on screen), the
merge of a peek into the top 50 by minimal splices, the audit record of a
peek (outcome peek, then shown) with its replay and its evaluation, and the
rules of the timer: it exists only while a recipe page is on screen with
Feed Chef on, and pauses for a hidden window, power saver and a metered
connection. The engine of the page tests is a fake; the audit tests run the
real engine. Exits 77 (skip) without a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-peek-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import copy  # noqa: E402
import gc  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from datetime import timedelta  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

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
from feedchefaudit import replay as replaymod  # noqa: E402
from feedchefaudit.records import Loaded, expand, load  # noqa: E402
from src import (appsettings, feedback, sysstate)
from src.testkit import peekkit  # noqa: E402
from src.testkit.peekkit import (Client, FakeMonitor,  # noqa: E402
                                 PeekEngine, freed, pump, pump_until,
                                 status)
from src.feedchef import auditreport, smartmerge  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import pantry as pantrymod  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# --- the cache of scores: an LRU of 100 ---------------------------------

cache = smartmerge.ScoreCache()
for number in range(100):
    cache.put('p%d' % number, number / 100.0, relevance=number / 10.0)
check('cache: it holds 100 entries', len(cache) == smartmerge.SCORE_CACHE_SIZE
      == 100)
cache.get('p0')
cache.put('p100', 1.0)
check('cache: the 101st put evicts the least recently used, not the oldest '
      'one that was read', 'p1' not in cache and 'p0' in cache
      and len(cache) == 100)
check('cache: the relevance of an evicted post leaves with its score',
      cache.relevance('p1') is None and cache.relevance('p0') == 0.0)
cache.put('p5', 0.5)
check('cache: a score put without a relevance drops the old relevance',
      cache.relevance('p5') is None)

# --- the threshold: mean plus one population standard deviation ---------

scores = [1.0, 2.0, 3.0, 4.0, 5.0]
limit = smartmerge.threshold(scores)
check('threshold: mean plus the population SD (%.4f)' % limit,
      abs(limit - (statistics.mean(scores)
                   + statistics.pstdev(scores))) < 1e-12
      and abs(limit - (3 + 2 ** 0.5)) < 1e-12)
check('threshold: a post just under it is not worth it, just over is',
      not smartmerge.worth_interrupting(scores, [limit - 0.01, 0.0])
      and smartmerge.worth_interrupting(scores, [0.0, limit + 0.01]))
check('threshold: equal to it is not above it',
      not smartmerge.worth_interrupting(scores, [limit]))
check('threshold: with no visible score it is 0.0, but nothing is worth '
      'showing against nothing (no pill)',
      smartmerge.threshold([]) == 0.0
      and not smartmerge.worth_interrupting([], [0.1, 9.0]))

# --- the merge: only the top 50 is ordered again --------------------------

recipe = default_recipe('Merge')
cache = smartmerge.ScoreCache(size=500)
current = [status(n) for n in range(1, 61)]
for index, post in enumerate(current):
    # Descending by position, so the list is in the recipe's order.
    cache.put(post['id'], 1.0 - index / 100.0)
beyond = current[54]
cache.put(beyond['id'], 9.0)
fresh = [status(100), status(101)]
cache.put('00100', 1.5)
cache.put('00101', 0.205)
merged = smartmerge.merge_head(recipe, current, fresh, cache)
ids = [post['id'] for post in merged]
check('merge: no post is lost or doubled', len(ids) == 62
      and len(set(ids)) == 62)
check('merge: the top is ordered by score, the best first',
      ids[0] == '00100' and ids[1] == '00001')
check('merge: the new post with a low score lands where its score puts it',
      ids.index('00101') > 40)
check('merge: a post beyond the window is not promoted, however high its '
      'score', ids.index(beyond['id']) >= 50)
check('merge: what lies beyond the window keeps its order',
      ids[50:] == [post['id'] for post in (fresh + current)[50:]])
broken = smartmerge.merge_violations(
    recipe, before=[p['id'] for p in current], inserted=['00100', '00101'],
    after=ids, peek_order=['00100', '00101'],
    statuses={p['id']: p for p in [*current, *fresh]}, cache=cache)
check('merge: the predicate finds nothing wrong with it', broken == [])


def violations(**change):
    arguments = dict(
        before=[p['id'] for p in current], inserted=['00100', '00101'],
        after=list(ids), peek_order=['00100', '00101'],
        statuses={p['id']: p for p in [*current, *fresh]}, cache=cache)
    arguments.update(change)
    return smartmerge.merge_violations(recipe, **arguments)


check('merge predicate: an id that no peek page selected is refused',
      'merge_foreign' in violations(peek_order=['00100']))
check('merge predicate: a post twice is refused',
      'merge_duplicate' in violations(after=ids[:-1] + [ids[0]]))
check('merge predicate: an inserted id that was already shown is refused',
      'merge_duplicate' in violations(inserted=['00100', '00003']))
check('merge predicate: the window ordered wrongly is refused',
      'merge_order' in violations(after=[ids[1], ids[0], *ids[2:]]))
swapped = list(ids)
swapped[49], swapped[55] = swapped[55], swapped[49]
check('merge predicate: a post that crosses the window is refused',
      {'merge_window', 'merge_tail'} & set(violations(after=swapped)))
check('merge predicate: a post that went missing is refused',
      'merge_members' in violations(after=ids[:-1]))
record_merge = smartmerge.merge_record(
    recipe, inserted=['00100', '00101'], after=ids,
    statuses={p['id']: p for p in [*current, *fresh]}, cache=cache)
check('merge record: window, ids and the values it was ordered by',
      record_merge['window'] == 50 and len(record_merge['head']) == 50
      and len(record_merge['keys']) == 50
      and record_merge['order'] == 'score'
      and smartmerge.record_violations(
          record_merge, ['00100', '00101']) == [])
check('merge record: an inserted id outside the peek page fails',
      'merge_foreign' in smartmerge.record_violations(
          record_merge, ['00100']))
rising = copy.deepcopy(record_merge)
rising['keys'][3], rising['keys'][4] = rising['keys'][4], rising['keys'][3]
check('merge record: a rising order fails',
      'merge_order' in smartmerge.record_violations(
          rising, ['00100', '00101']))
doubled = copy.deepcopy(record_merge)
doubled['head'][2] = doubled['head'][1]
check('merge record: a post twice in the window fails',
      'merge_duplicate' in smartmerge.record_violations(
          doubled, ['00100', '00101']))

# --- the audit: a real engine peeks, the record says so ------------------


def post(sid, author, fav=1, minutes=30):
    return {'id': sid, 'created_at': (
        fuzz.NOW - timedelta(minutes=minutes)).isoformat(),
        'content': '<p>a plain note about the weather today</p>',
        'spoiler_text': '', 'sensitive': False, 'language': 'en',
        'favourites_count': fav, 'reblogs_count': 0, 'replies_count': 0,
        'account': {'id': author}, 'tags': [], 'media_attachments': []}


world = {'scope': [post('p1', 'a1', 4), post('p2', 'a2', 3),
                   post('p3', 'a3', 2)]}
engine = FeedEngine(
    account_key='h#1', self_account_id='me',
    fetch_scope=lambda max_id, count: list(world['scope']),
    fetch_trends=lambda: kitchenmod.ServerTrendContext(
        trending_hashtags={}, trending_status_ids=set()),
    fetch_following_page=lambda max_id: ([], None))
engine.clock = lambda: fuzz.NOW
cap_recipe = fuzz.default_recipe('caps')
cap_recipe.kitchen.modifiers = [Modifier(
    type='diversity', max_per_cluster=1, diversity_dimensions=['author'])]
root = os.path.join(_TMP, 'logs')
logmod.set_root(root)
pantrymod._mlt_cache.clear()
first = engine.process_feed_sync(
    cap_recipe, count=40, cook_id='cx', page_index=0)
shown = {item['id'] for item in first.items}
check('engine: the first page shows three posts, one per person',
      shown == {'p1', 'p2', 'p3'} and first.author_counts == {
          'a1': 1, 'a2': 1, 'a3': 1})
world['scope'] = [post('p4', 'a1', 9), post('p1', 'a1', 4),
                  post('p2', 'a2', 3), post('p5', 'a4', 2)]
done = threading.Event()
peeked = {}


def on_done(result):
    peeked['result'] = result
    peeked['thread'] = threading.current_thread()
    done.set()


engine.process_feed(
    cap_recipe, count=40, on_done=on_done,
    on_error=lambda error: (peeked.update(error=error), done.set()),
    author_counts=first.author_counts, cook_id='cx', page_index=1001,
    peek=True, exclude=frozenset(shown))
done.wait(10)
result = peeked['result']
check('engine: the peek cooked on the engine worker, not the main thread',
      peeked['thread'] is not threading.main_thread()
      and peeked['thread'].name.startswith('feedchef-engine'))
record = result.logger.record
check('engine: its audit outcome is peek', record['outcome']['status']
      == 'peek')
posts = {entry['id']: entry for entry in record['posts']}
check('engine: the posts already shown are not in the peek at all',
      not shown & set(posts))
check('engine: the cap holds across the merged list (a1 had its one post)',
      [i['id'] for i in result.items] == ['p5']
      and posts['p4']['removal']['reason'] == 'author_cap')
check('engine: the peek starts from the session counts',
      record['params']['author_counts_in'] == first.author_counts
      and record['page_index'] == 1001 and record['cook_id'] == 'cx')
pages = [load(path) for path in expand([root])]
peek_log = next(p for p in pages
                if p.record['outcome']['status'] == 'peek')
tally = evalmod.evaluate_record(peek_log)
check('evaluate: a peek page keeps every promise (%s)' % tally.overall(),
      tally.ok() and tally.checked['promises'] >= 1)
again = replaymod.replay(peek_log)
check('replay: a peek page replays identically (%s %s)' % (
    again.status, again.detail), again.status == 'identical')
cook_before = evalmod.evaluate_cook(pages)
check('evaluate: a peek that was not shown does not count toward the cap',
      cook_before.ok())

# The reader asks for it: the merge is recorded and the outcome is shown.
statuses = {p['id']: p for p in [*first.items, *result.items]}
cache = smartmerge.ScoreCache()
for sid, score in {**first.score_map, **result.score_map}.items():
    cache.put(sid, score)
merge_ids = [i['id'] for i in smartmerge.merge_head(
    cap_recipe, first.items, result.items, cache)]
merge = smartmerge.merge_record(
    cap_recipe, inserted=['p5'], after=merge_ids, statuses=statuses,
    cache=cache)
check('audit: the logger marks a peek as shown with its merge',
      result.logger.mark_shown(merge)
      and record['outcome']['status'] == 'shown'
      and record['merge']['inserted'] == ['p5'])
check('audit: only a peek can be marked shown',
      not result.logger.mark_shown(merge))
reloaded = [load(path) for path in expand([root])]
shown_log = next(p for p in reloaded
                 if p.record['outcome']['status'] == 'shown')
tally = evalmod.evaluate_record(shown_log)
check('evaluate: a shown peek is judged with its merge (%s)'
      % tally.overall(), tally.ok() and tally.checked['merge'] == 1)
cook = evalmod.evaluate_cook(reloaded)
check('evaluate: its posts count toward the cap of the cook (%s)'
      % cook.overall(), cook.ok() and cook.checked['cap'] >= 1)
forged = copy.deepcopy(shown_log.record)
forged['merge']['inserted'] = ['ghost']
forged_log = Loaded(shown_log.path, {**shown_log.payload, 'audit': forged},
                    forged, shown_log.sidecar)
check('evaluate: an inserted id from no peek page is a FAIL',
      evalmod.evaluate_record(forged_log).violations['merge'] == 1)
forged = copy.deepcopy(shown_log.record)
forged['merge']['head'] = [forged['merge']['head'][0]] * 2
forged_log = Loaded(shown_log.path, {**shown_log.payload, 'audit': forged},
                    forged, shown_log.sidecar)
check('evaluate: a post twice in the merged window is a FAIL',
      evalmod.evaluate_record(forged_log).violations['merge'] >= 1)
entry = next(e for e in shown_log.record['posts'] if e['id'] == 'p5')
sections = auditreport.explain_post(entry, shown_log.record)
arrival = [row for section in sections for row in section.rows
           if row.title == 'Arrived']
check('Why This Post: a post that was inserted says it arrived later',
      arrival and arrival[0].subtitle == 'It arrived later as a new post')
plain = auditreport.explain_post(
    next(e for e in first.logger.record['posts'] if e['id'] == 'p1'),
    first.logger.record)
check('Why This Post: a post of a shown page says nothing of the kind',
      not [row for section in plain for row in section.rows
           if row.title == 'Arrived'])
report = auditreport.cook_report([first.logger.record, record])
check('Kitchen Report: a shown peek counts as a page of the cook',
      report.pages == 2 and report.selected == 4)
unseen = copy.deepcopy(record)
unseen['outcome'] = {'status': 'peek', 'error': ''}
check('Kitchen Report: a peek nobody was shown is not a page of the cook',
      auditreport.cook_report([first.logger.record, unseen]).pages == 1)
engine.shutdown()
engine._reach_executor.shutdown(wait=True)
logmod.set_root(os.path.join(_TMP, 'logs-ui'))

Adw.init()
storagemod.set_root(os.path.join(_TMP, 'recipes'))
feedback.show_toast = lambda *_args, **_kwargs: None
monitor = FakeMonitor()
sysstate._monitor = monitor
settings = appsettings.settings()
settings.set_boolean('feed-chef-enabled', True)

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
client = Client()
window = Gtk.Window(default_width=900, default_height=700)
window.present()


def make_page(first_posts=6):
    return peekkit.make_page(window, account, client, first_posts)


def ids(page):
    return [page.model.get_item(i).status_id
            for i in range(page.model.get_n_items())]


page, engine, view = make_page()
source = page.source
check('page: the first page fills the cache with the shown scores',
      len(source.scores) == 6 and abs(source.scores.get('00001') - 0.9)
      < 1e-12)
check('page: the Feed Chef page is mapped with the window shown',
      page.get_mapped())

# --- the timer ----------------------------------------------------------

context = GLib.MainContext.default()
check('timer: a mapped recipe page has the one timer, five minutes apart',
      page._auto_refresh_id != 0 and smartmerge.PEEK_INTERVAL_S == 300
      and context.find_source_by_id(page._auto_refresh_id) is not None)
asked = []
original = page.set_auto_refresh_interval


def spy(seconds):
    asked.append(seconds)
    original(seconds)


page.set_auto_refresh_interval = spy
page._peek_sync()
check('timer: syncing again keeps the timer (no restart)',
      asked == [] and page._auto_refresh_id != 0)
monitor.metered = True
check('pause: a metered connection removes the timer',
      page._auto_refresh_id == 0 and asked[-1] == 0)
page._on_auto_refresh()
check('pause: a tick under a metered connection peeks at nothing',
      engine.peeks == [])
monitor.metered = False
check('pause: it comes back with the connection', page._auto_refresh_id != 0
      and asked[-1] == 300)
monitor.power_saver = True
check('pause: power saver removes the timer',
      page._auto_refresh_id == 0)
page._on_auto_refresh()
check('pause: a tick in power saver peeks at nothing', engine.peeks == [])
monitor.power_saver = False
check('pause: it comes back with the battery', page._auto_refresh_id != 0)
window.set_visible(False)
pump_until(lambda: not page.get_mapped())
check('pause: a hidden window removes the timer',
      page._auto_refresh_id == 0)
page._on_auto_refresh()
check('pause: a tick of a hidden window peeks at nothing',
      engine.peeks == [])
window.present()
pump_until(page.get_mapped)
check('pause: it comes back with the window', page._auto_refresh_id != 0)
settings.set_boolean('feed-chef-enabled', False)
page._peek_sync()
check('off: with Feed Chef off there is no timer at all',
      page._auto_refresh_id == 0)
settings.set_boolean('feed-chef-enabled', True)
page._peek_sync()
page.close_requests()
check('off: closing the requests of the page removes the timer too',
      page._auto_refresh_id == 0)
page._peek_sync()
check('off: a closed page does not get the timer back',
      page._auto_refresh_id == 0)
del page.set_auto_refresh_interval
original = spy = None

# --- the peek, the pill and the merge, on a fresh page ------------------

view.pop_to_page(view.get_visible_page())
page, engine, view = make_page()
source = page.source
engine.peek_queue = [([status(10)], {'00010': 0.1}, {'user10': 1})]
page._on_auto_refresh()
check('peek: a tick asks the engine for a peek (outcome peek, page one)',
      len(engine.peeks) == 1)
pump_until(lambda: source.peeked is not None)
entry = engine.peeks[0]
check('peek: it asks with the ids on screen to leave out',
      entry['exclude'] >= {'00001', '00006'} and len(entry['exclude']) >= 6)
check('peek: it asks with the cook session counts, so caps keep holding',
      entry['author_counts'] == {'user%d' % n: 1 for n in range(1, 7)})
check('peek: it asks inside the cook session, past the pages\' own indexes',
      entry['cook_id'] == source._cook_id
      and entry['page_index'] >= smartmerge.PEEK_PAGE_BASE)
check('peek: the cook runs on a worker thread, never the main thread',
      entry['thread'] is None or entry['thread'] is not entry['main'])
engine.peeks.clear()

# A peek with nothing better: held silently, no pill.
engine.peek_queue = [([status(20), status(21)],
                      {'00020': 0.30, '00021': 0.25}, {'user20': 1})]
page._on_auto_refresh()
pump_until(lambda: source.peeked is not None
           and source.peeked.order == ['00020', '00021'])
check('pill: new posts that score under the viewport are kept silently',
      source.peeked is not None
      and not page.new_posts_revealer.get_reveal_child())
check('pill: the model did not change', len(ids(page)) == 6)
visible = page._visible_scores()
limit = smartmerge.threshold(visible)
check('pill: the threshold is mean + SD of the cached scores on screen '
      '(%.3f of %d posts)' % (limit, len(visible)),
      len(visible) >= 5 and abs(limit - (statistics.fmean(visible)
                                         + statistics.pstdev(visible)))
      < 1e-12)

# A later peek with one post above it: the pill appears, with the count.
engine.peek_queue = [([status(30), status(31), status(32)],
                      {'00030': 0.99, '00031': 0.75, '00032': 0.05},
                      {'user30': 1, 'user31': 1, 'user32': 1})]
page._on_auto_refresh()
pump_until(lambda: page.new_posts_revealer.get_reveal_child())
check('pill: a post above mean + SD shows it',
      page.new_posts_revealer.get_reveal_child())
check('pill: it says how many, with ngettext',
      page.new_pill.get_label() == '3 New Posts Worth Seeing')
pump(10)
check('pill: the silent peek this one replaced had its log deleted, on the '
      'worker', ('discard', 'fake-engine_0') in engine.loggers[-2].log)

# Clicking: minimal splices, the top 50 ordered again, the items kept.
before_items = [page.model.get_item(i) for i in range(6)]
changes = []
page.model.connect('items-changed',
                   lambda _m, pos, removed, added:
                   changes.append((pos, removed, added)))
page._on_new_pill_clicked(None)
after = ids(page)
check('click: the new posts are in, ordered by cached score with the old '
      'ones', after == ['00030', '00001', '00002', '00031', '00003', '00004',
                        '00005', '00006', '00032'])
check('click: the posts that were there are the same StatusItem objects',
      all(page.model.get_item(after.index(item.status_id)) is item
          for item in before_items))
check('click: one splice for each place a post went in, none removing a '
      'row (%s)' % changes,
      sorted(changes) == [(0, 0, 1), (2, 0, 1), (6, 0, 1)])
check('click: the pill is gone and the count reset',
      not page.new_posts_revealer.get_reveal_child()
      and page._pill_count == 0)
check('click: the cache has the scores of what was inserted',
      abs(source.scores.get('00030') - 0.99) < 1e-12)
check('click: the cook session counts are the peek\'s now',
      source._author_counts == {'user30': 1, 'user31': 1, 'user32': 1})
check('click: scrolled to the top',
      page._scrolled.get_vadjustment().get_value() == 0.0)
record = source.cook_audits()[-1]
check('click: the audit record is a shown page with its merge',
      record['outcome']['status'] == 'shown'
      and record['merge']['inserted'] == ['00030', '00031', '00032']
      and record['merge']['head'][0] == '00030')
check('click: Why This Post? finds the inserted post in that record',
      source.audit_for('00030') is not None
      and source.audit_for('00030')[1] is record)
pump(20)
check('click: the record is marked on the main thread and the file is '
      'written by the worker', engine.loggers[-1].log[0][0] == 'shown'
      and engine.loggers[-1].log[0][1] == threading.main_thread().name
      and ('flush', 'fake-engine_0') in engine.loggers[-1].log)
check('click: nothing held is left', source.peeked is None)

# A single post on top is a plain insertion at the head.
engine.peek_queue = [([status(40)], {'00040': 0.999}, {'user40': 1})]
page._on_auto_refresh()
pump_until(lambda: page.new_posts_revealer.get_reveal_child())
changes.clear()
page._on_new_pill_clicked(None)
check('click: one new post on top is one splice at the head (%s)' % changes,
      changes == [(0, 0, 1)] and ids(page)[0] == '00040')
check('pill: the label for one post is singular',
      page.new_pill.get_label() == '1 New Post Worth Seeing')

# An older page makes a held peek stale: it is dropped, the pill hidden.
engine.peek_queue = [([status(50)], {'00050': 0.9999}, {'user50': 1})]
page._on_auto_refresh()
pump_until(lambda: source.peeked is not None)
check('stale: a held peek exists', source.peeked is not None)
# The fake's first page was the last one; an older page exists for this.
source._has_more, source._cursor = True, '00777'
page.model.exhausted = False
engine.queue = [([status(8)], False, None, {'00008': 0.1}, {'user8': 1})]
page.load_older()
pump_until(lambda: source.peeked is None and not engine.queue)
pump(10)
check('stale: an older page makes the held peek stale, so it is dropped',
      source.peeked is None
      and not page.new_posts_revealer.get_reveal_child())
check('stale: the cache holds only what was shown',
      '00050' not in source.scores)

# A refresh forgets the whole session.
engine.queue = [([status(60)], False, None, {'00060': 0.5},
                 {'user60': 1})]
page.refresh()
pump_until(lambda: ids(page) == ['00060'])
check('refresh: a new cook session starts the cache again',
      source.scores.ids() == ['00060'])

# --- a dropped page is freed and its timer is gone ----------------------

check('free: the timer exists before the page goes',
      page._auto_refresh_id != 0)
timer = page._auto_refresh_id
ref = page.weak_ref()
view.pop_to_page(view.get_visible_page())
window.set_child(Gtk.Label(label='elsewhere'))
pump(20)
check('free: leaving the page removes its timer at once, not after a tick',
      context.find_source_by_id(timer) is None)
page = engine = source = view = None
check('free: the page is freed', freed(ref))
check('free: no peek is left in flight on any engine',
      all(not e.peek_queue for e in PeekEngine.made))

# Feed Chef off: no engine is touched, no timer exists.
page2, engine2, view2 = make_page()
settings.set_boolean('feed-chef-enabled', False)
page2._peek_sync()
page2._on_auto_refresh()
check('off: a page with Feed Chef off has no timer', page2._auto_refresh_id
      == 0)

# Drop the pages before the window, as the other page tests do, so no
# ListView is freed by the interpreter after the rest is gone.
page = page2 = view = view2 = engine = engine2 = None
window.set_child(None)
window.destroy()
pump(20)
gc.collect()
pump(20)
print()
print('%d failure(s)' % len(failures) if failures else 'all passed')
sys.exit(1 if failures else 0)
