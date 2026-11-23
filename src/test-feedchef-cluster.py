#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: similarity grouping, the cap per server and the search stagger.

Display-free. Java's `PostSimilarityCalculatorTest`, `KMeansClusteringTest` and
`ClusterSelector` are ported, and the hybrid similarity, the number of groups,
the three strategies and the round robin are held to Java's formulas. A group
of posts is chosen by FasterPAM from a fixed start, so the same posts always
give the same groups. It needs the optional `kmedoids` package: without it the
checks that group posts are skipped (exit 77), and the ones that say what
happens when it is missing still run. Everything runs on the engine worker.
"""

import os
import random
import sys
import tempfile
import threading
import time
from datetime import timedelta

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from feedchefaudit.fuzz import NOW as FUZZ_NOW  # noqa: E402
from src.feedchef import cluster as clustermod  # noqa: E402
from src.feedchef import contract  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import deps  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import pantry as pantrymod  # noqa: E402
from src.feedchef.audit import Recorder, is_reason  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402

failures = 0
TMP = tempfile.mkdtemp(prefix='feedchef-cluster-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))
NOW = FUZZ_NOW
HAVE_KMEDOIDS = not deps.unavailable_reason('numpy', 'kmedoids')


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


def close(a, b, tol=1e-12):
    return abs(a - b) <= tol


def post(sid, text, *, fav=0, reblog=0, reply=0, age_min=10, author=None,
         tags=(), host='ex.ample'):
    author = author or 'u' + sid
    return {
        'id': sid, 'content': '<p>%s</p>' % text, 'spoiler_text': '',
        'created_at': (NOW - timedelta(minutes=age_min)).isoformat(),
        'favourites_count': fav, 'reblogs_count': reblog,
        'replies_count': reply, 'sensitive': False, 'language': 'en',
        'account': {'id': author, 'url': 'https://%s/@%s' % (host, author)},
        'tags': [{'name': t} for t in tags], 'media_attachments': []}


def ids(statuses):
    return [s['id'] for s in statuses]


# --- everything runs on the engine worker (Principle 5) ------------------------
# First, before anything below groups posts on the main thread: numpy and
# kmedoids must be imported by the worker, never by the main thread.

topics = ['coffee morning cup', 'garden rain flowers', 'music night band',
          'travel train station']
posts40 = [post(str(100 + i), '%s %d' % (topics[i % 4], i), fav=i % 7,
                age_min=5 + i, author='a%d' % (i % 9),
                host=['ex.ample', 'b.example', 'c.example'][i % 3],
                tags=['t%d' % (i % 4)]) for i in range(40)]
group_recipe = default_recipe()
group_recipe.kitchen.modifiers = [Modifier(type='similarity_cluster',
                                           max_per_cluster=1)]
if HAVE_KMEDOIDS:
    seen_threads = {}
    real_fit = clustermod.fit


    def spy(*args, **kwargs):
        seen_threads['fit'] = threading.current_thread()
        return real_fit(*args, **kwargs)


    clustermod.fit = spy
    engine = FeedEngine(
        account_key='cluster#1', self_account_id='me',
        fetch_scope=lambda max_id, count: [dict(p) for p in posts40],
        search_statuses=lambda q: [])
    done = threading.Event()
    box = []
    engine.process_feed(group_recipe, count=40,
                        on_done=lambda r: (box.append(r), done.set()),
                        on_error=lambda e: (box.append(e), done.set()))
    done.wait(60)
    engine.shutdown()
    clustermod.fit = real_fit
    worker = seen_threads.get('fit')
    check('the grouping ran on the engine worker, not the main thread (%s)'
          % (worker.name if worker else None),
          worker is not None and worker is not threading.main_thread()
          and worker.name.startswith('feedchef-engine'))
    check('numpy and kmedoids were imported there, never on the main thread',
          not {'numpy', 'kmedoids'} & deps._warned
          and box and not isinstance(box[0], BaseException)
          and len(box[0].items) == 4)



# --- Java's PostSimilarityCalculatorTest -------------------------------------

jcorpus = [post('1', 'hello world', tags=['tag1'], author='user1'),
           post('2', 'hello world', tags=['tag1'], author='user1'),
           post('3', 'different content', tags=['tag2'], author='user2'),
           post('4', 'noise', tags=['tag3'], author='user3')]
rows = clustermod.similarity_rows(jcorpus)
check('similarity: identical content, tag and author is 1.0 (Java +- 0.001)',
      close(rows[0][1], 1.0, 0.001))
check('similarity: nothing in common is 0 (Java)', close(rows[0][2], 0.0,
                                                         0.001))
check('similarity: the diagonal is 1 and the matrix is exactly symmetric',
      all(rows[i][i] == 1.0 for i in range(4))
      and all(rows[i][j] == rows[j][i] for i in range(4) for j in range(4)))

# --- the hybrid formula --------------------------------------------------------

check('the weights are Java\'s (0.1 hashtags, 0.7 text, 0.2 person) and '
      'add up to 1', clustermod.WEIGHTS == (0.1, 0.7, 0.2)
      and close(sum(clustermod.WEIGHTS), 1.0))
both = clustermod.similarity(
    clustermod.WEIGHTS, (frozenset('ab'), frozenset('bc')), 0.5, ('u', 'u'))
check('similarity = hashtags * w + text * w + person * w (1/3, 0.5, same)',
      close(both, (1 / 3) * 0.1 + 0.5 * 0.7 + 1.0 * 0.2, 1e-15))
check('another person adds nothing, and no person id is never "the same"',
      close(clustermod.similarity(clustermod.WEIGHTS, (frozenset(),
            frozenset()), 0.0, ('u', 'v')), 0.0)
      and clustermod.similarity(clustermod.WEIGHTS, (frozenset(),
                                frozenset()), 0.0, ('', '')) == 0.0)
check('the Jaccard index of the hashtags: 1/3, 1, and 0 when one is empty',
      close(clustermod.hashtag_similarity(
          frozenset('ab'), frozenset('bc')), 1 / 3)
      and clustermod.hashtag_similarity(
          frozenset('ab'), frozenset('ab')) == 1.0
      and clustermod.hashtag_similarity(frozenset(), frozenset('a')) == 0.0)
boost = {'id': 'w1', 'created_at': NOW.isoformat(), 'content': '',
         'account': {'id': 'booster', 'url': 'https://b.example/@booster'},
         'reblog': post('b1', 'coffee morning', tags=['Coffee'])}
check('a boost is read as the post it shows (tags, words), and counts for '
      'the person who boosted it',
      clustermod._tags(boost) == {'coffee'}
      and clustermod._person(boost) == 'booster')

# --- the number of groups (Java: min(5, max(1, n / 10))) ----------------------

check('k = min(5, max(1, n // 10)): 10 -> 1, 19 -> 1, 20 -> 2, 49 -> 4, '
      '50 -> 5, 500 -> 5',
      [clustermod.group_count(n) for n in (10, 19, 20, 49, 50, 500)]
      == [1, 1, 2, 4, 5, 5])
mod = Modifier(type='similarity_cluster')
few = clustermod.run([post(str(i), 'word%d' % i) for i in range(9)], mod,
                     score_of=kitchenmod.base_score,
                     tie_key=kitchenmod._tie_key)
check('fewer than ten posts are not grouped, and the result says so',
      few.status == 'skipped' and few.reason == 'fewer than 10 posts'
      and len(few.kept) == 9 and few.record()['status'] == 'skipped')
check('max_per_cluster: missing is 1 (Java), and never below 1 (Java '
      'returned no post at all for 0)',
      [clustermod.max_per_cluster(v) for v in (None, 0, -2, 3, '2', 'x')]
      == [1, 1, 1, 3, 2, 1])
check('strategies: best_score is the default, centroid_then_score is '
      'centroid',
      [clustermod.strategy_of(v) for v in (
          None, 'best_score', 'most_recent', 'centroid',
          'centroid_then_score', 'nonsense')]
      == ['best_score', 'best_score', 'most_recent', 'centroid', 'centroid',
          'best_score'])

# --- the selection (Java's ClusterSelector) -----------------------------------

# Three groups of 5, 4 and 3 posts; engagement and age are all different.
labels = [0] * 5 + [1] * 4 + [2] * 3
members = [post('p%02d' % i, 'x', fav=10 * (i % 5) + i, age_min=100 - i)
           for i in range(12)]
# The medoids are the 3rd post of each group.
medoids = [2, 7, 10]


def pick(strategy, per):
    return ids([members[i] for i in clustermod.select(
        members, labels, medoids, strategy, per, kitchenmod.base_score,
        kitchenmod._tie_key)])


def best_of(group, count, key):
    inside = [m for m, g in zip(members, labels, strict=True) if g == group]
    return [m['id'] for m in sorted(inside, key=key, reverse=True)[:count]]


engagement = lambda m: (kitchenmod.base_score(m), kitchenmod._tie_key(m))  # noqa: E731
newest = lambda m: kitchenmod._tie_key(m)  # noqa: E731
want = sorted(best_of(0, 1, engagement) + best_of(1, 1, engagement)
              + best_of(2, 1, engagement))
check('best_score keeps the most engaging post of each group',
      pick('best_score', 1) == want)
want = sorted(best_of(0, 2, engagement) + best_of(1, 2, engagement)
              + best_of(2, 2, engagement))
check('best_score with 2 per group keeps the two best of each (%s)'
      % pick('best_score', 2), pick('best_score', 2) == want)
want = sorted(best_of(0, 1, newest) + best_of(1, 1, newest)
              + best_of(2, 1, newest))
check('most_recent keeps the newest post of each group',
      pick('most_recent', 1) == want)
inside = lambda g: [m for m, lab in zip(members, labels, strict=True) if lab == g]  # noqa: E731
expected = []
for group, medoid in zip((0, 1, 2), medoids, strict=True):
    others = sorted((m for m in inside(group) if m is not members[medoid]),
                    key=engagement, reverse=True)
    expected += [members[medoid]['id'], others[0]['id']]
check('centroid with 2 per group keeps the middle post of each group and '
      'then the best of the others', pick('centroid', 2) == sorted(expected))
check('centroid with 1 per group keeps only the middle posts',
      pick('centroid', 1) == sorted(members[m]['id'] for m in medoids))
check('a group smaller than the cap keeps all its posts (3 posts, 5 per '
      'group)', len(pick('best_score', 5)) == 5 + 4 + 3)
check('a group keeps at most max_per_cluster posts, whatever the strategy',
      all(len(pick(s, n)) == sum(min(n, size) for size in (5, 4, 3))
          for s in clustermod.STRATEGIES for n in (1, 2, 3, 4)))
check('the posts kept stay in the order of the page',
      pick('best_score', 2) == sorted(pick('best_score', 2)))
tied = [post('t%d' % i, 'x', fav=5, age_min=10 + i) for i in range(4)]
check('equal engagement: the newest, then the greatest id (Babble breaks '
      'ties, Java kept the order of arrival)',
      ids([tied[i] for i in clustermod.select(
          tied, [0, 0, 0, 0], [0], 'best_score', 2, kitchenmod.base_score,
          kitchenmod._tie_key)]) == ['t0', 't1'])

# --- the missing package is said, never a quiet no-op ---------------------------

saved = (deps._modules.get('kmedoids', 'unset'),
         deps._reasons.get('kmedoids', 'unset'))
deps._modules['kmedoids'] = None
deps._reasons['kmedoids'] = 'ImportError: simulated'
down = clustermod.run(posts40, group_recipe.kitchen.modifiers[0],
                      score_of=kitchenmod.base_score,
                      tie_key=kitchenmod._tie_key)
check('without kmedoids the result is unavailable, says why and keeps '
      'every post (%s)' % down.reason,
      down.status == 'unavailable' and 'simulated' in down.reason
      and len(down.kept) == 40)
recorder = Recorder(group_recipe, cook_id='x')
recorder.clock(NOW)
ordered, _s, meta = kitchenmod.score_statuses(
    posts40, group_recipe, now=NOW, audit=recorder)
check('and the kitchen reports it, with nothing removed (meta, audit)',
      meta['unavailable'] == {'similarity_cluster': down.reason}
      and meta['cluster']['status'] == 'unavailable'
      and len(ordered) == 40 and recorder.grouping.status == 'unavailable'
      and not recorder.kitchen_removed)
check('the reason is what the builder shows (no import needed)',
      'simulated' in deps.unavailable_reason('numpy', 'kmedoids'))
engine = FeedEngine(
    account_key='down#1', self_account_id='me',
    fetch_scope=lambda max_id, count: [dict(p) for p in posts40],
    search_statuses=lambda q: [])
result = engine.process_feed_sync(group_recipe, count=40)
check('the cook keeps every post, logs the fallback, and says it in the '
      'kitchen meta and the decision log',
      len(result.items) == 40
      and 'similarity_cluster_unavailable'
      in result.logger.log.reliability['fallbacks']
      and result.logger.log.kitchen['unavailable'] == {
          'similarity_cluster': down.reason}
      and result.logger.record['cluster']['status'] == 'unavailable')
engine.shutdown()
deps._modules['kmedoids'] = saved[0] if saved[0] != 'unset' else None
if saved[0] == 'unset':
    del deps._modules['kmedoids']
if saved[1] == 'unset':
    del deps._reasons['kmedoids']
else:
    deps._reasons['kmedoids'] = saved[1]

# --- the cap per server ---------------------------------------------------------

server_mod = Modifier(type='diversity', max_per_cluster=2,
                      diversity_dimensions=['author', 'instance'])
mixed = [post('s%d' % i, 'x', author='a%d' % i,
              host=['one.example', 'two.example'][i % 2])
         for i in range(8)]
kept, removed = kitchenmod.diversity_pass(mixed, server_mod)
check('the instance dimension keeps 2 posts per server (%s)' % ids(kept),
      ids(kept) == ['s0', 's1', 's2', 's3']
      and set(removed.values()) == {'instance_cap'} and len(removed) == 4)
kept, removed = kitchenmod.diversity_pass(
    mixed, server_mod, instance_counts={'one.example': 1})
check('the counts of the earlier pages of the session count',
      ids(kept) == ['s0', 's1', 's3'])
person_mod = Modifier(type='diversity', max_per_cluster=1,
                      diversity_dimensions=['author', 'instance'])
same_person = [post('q1', 'x', author='a', host='one.example'),
               post('q2', 'x', author='a', host='two.example'),
               post('q3', 'x', author='b', host='one.example')]
kept, removed = kitchenmod.diversity_pass(same_person, person_mod)
check('both dimensions apply: the person first, then the server, each '
      'with its own reason, and a refused post counts for neither',
      ids(kept) == ['q1'] and [removed[id(s)] for s in same_person[1:]]
      == ['author_cap', 'instance_cap'])
only = Modifier(type='diversity', max_per_cluster=1,
                diversity_dimensions=['instance'])
check('the instance dimension alone does not cap people',
      ids(kitchenmod.apply_diversity(same_person, only)) == ['q1', 'q2'])
check('no dimensions, or only the author, is what it was: the person (Java)',
      ids(kitchenmod.apply_diversity(
          same_person, Modifier(type='diversity', max_per_cluster=1))) ==
      ['q1', 'q3']
      and ids(kitchenmod.apply_diversity(
          same_person, Modifier(type='diversity', max_per_cluster=1,
                                diversity_dimensions=['author']))) ==
      ['q1', 'q3'])
nohost = [{**post('n1', 'x'), 'account': {'id': 'x'}},
          {**post('n2', 'x'), 'account': {'id': 'y'}}]
check('a post whose account has no server is never capped by it (Java)',
      len(kitchenmod.apply_diversity(
          nohost, Modifier(type='diversity', max_per_cluster=1,
                           diversity_dimensions=['instance']))) == 2)
check('tally_instances counts the servers of a page on top of the session',
      kitchenmod.tally_instances(mixed[:3], {'one.example': 2})
      == {'one.example': 4, 'two.example': 1})

# --- the stagger of the searches of More Like This --------------------------------

pauses = []
searched = []
focus = default_recipe()
focus.kitchen.active_focus_hashtags = ['one', 'two', 'three']
pantrymod._mlt_cache.clear()
found = pantrymod.fetch_more_like_this(
    focus, lambda q: (searched.append(q), [])[1],
    pause=lambda: pauses.append(len(searched)))
check('one pause between two searches, none before the first (3 searches: '
      '%s)' % pauses, searched == ['#one', '#two', '#three']
      and pauses == [1, 2])
check('Java\'s stagger is 450 ms', FeedEngine.search_stagger_ms == 450)


def stopping():
    raise pantrymod.Cancelled('cancelled')


pantrymod._mlt_cache.clear()
try:
    pantrymod.fetch_more_like_this(focus, lambda q: [], pause=stopping)
    stopped = False
except InterruptedError:
    stopped = True
check('a pause that stops is not a failed search: it propagates and '
      'nothing is cached', stopped and not pantrymod._mlt_cache)


def interrupted(query):
    raise InterruptedError('from the search itself')


pantrymod._mlt_cache.clear()
check('an InterruptedError from a search is a soft failure (nothing '
      'found, no cancel), not the cook\'s own cancel',
      pantrymod.fetch_more_like_this(focus, interrupted) == [])

calls = []
engine = FeedEngine(
    account_key='stagger#1', self_account_id='me',
    fetch_scope=lambda max_id, count: [],
    search_statuses=lambda q: (calls.append((q, time.monotonic())), [])[1])
engine.search_stagger_ms = 120
pantrymod._mlt_cache.clear()
start = time.monotonic()
engine.process_feed_sync(focus, count=10)
spent = time.monotonic() - start
check('the engine staggers its searches (3 searches took %.2f s, two '
      'pauses of 0.12 s)' % spent, spent >= 0.24
      and calls[1][1] - calls[0][1] >= 0.11)
engine.shutdown()

# A cancel ends the wait at once: no sleep that ignores it.
calls.clear()
engine = FeedEngine(
    account_key='stagger#2', self_account_id='me',
    fetch_scope=lambda max_id, count: [],
    search_statuses=lambda q: (calls.append(q), [])[1])
engine.search_stagger_ms = 5000
pantrymod._mlt_cache.clear()
finished = threading.Event()
engine.process_feed(focus, count=10, on_done=lambda r: finished.set(),
                    on_error=lambda e: finished.set())
time.sleep(0.3)
begun = time.monotonic()
engine.cancel()
future = engine._current
while not future.done() and time.monotonic() - begun < 3:
    time.sleep(0.01)
check('a cancelled cook stops in the middle of the stagger (%.2f s, %d '
      'search asked)' % (time.monotonic() - begun, len(calls)),
      future.done() and time.monotonic() - begun < 1.0 and len(calls) == 1)
engine.shutdown()

# --- the guard: a stage that breaks a promise about the page ----------------------

# A page whose posts are checked by the guard when a stage is broken on
# purpose: every violator is dropped and recorded, the others stay.


def world_posts():
    return [post(str(200 + i), '%s %d' % (topics[i % 4], i), fav=i % 6,
                 reply=i % 3, age_min=30 + 40 * i, author='b%d' % (i % 20),
                 host=['one.example', 'two.example', 'three.example'][i % 3],
                 tags=['t%d' % (i % 4)]) for i in range(60)]


def cook(recipe, count=60):
    engine = FeedEngine(
        account_key='guard#1', self_account_id='me',
        fetch_scope=lambda max_id, count: world_posts(),
        search_statuses=lambda q: [])
    engine.clock = lambda: NOW
    try:
        return engine.process_feed_sync(recipe, count=count)
    finally:
        engine.shutdown()


def broken(recipe, name, replacement, module):
    real = getattr(module, name)
    setattr(module, name, replacement(real))
    try:
        return cook(recipe)
    finally:
        setattr(module, name, real)


focus_recipe = default_recipe()
focus_recipe.kitchen.modifiers = []
focus_recipe.kitchen.active_focus_keywords = ['coffee', 'morning']
good = cook(focus_recipe)
check('relevance: an honest page drops nothing (%d posts)' % len(good.items),
      not good.guard['dropped'] and len(good.items) == 60)
bad = broken(focus_recipe, 'order_statuses',
             lambda real: lambda s, sc, b: list(reversed(real(s, sc, b))),
             kitchenmod)
shown = [bad.kitchen_meta['relevance_scores'][i['id']] for i in bad.items]
check('relevance: a stage that reverses the order is caught by the guard '
      '(%s dropped), what is left is non-increasing'
      % bad.guard['dropped'], bad.guard['dropped'].get(
          'relevance_order', 0) > 0
      and all(a >= b for a, b in zip(shown, shown[1:], strict=False)))
instance_recipe = default_recipe()
instance_recipe.kitchen.modifiers = [Modifier(
    type='diversity', max_per_cluster=3, diversity_dimensions=['instance'])]
good = cook(instance_recipe)
check('instance cap: an honest page has 3 per server (%d posts)'
      % len(good.items), len(good.items) == 9 and not good.guard['dropped']
      and good.instance_counts == {'one.example': 3, 'two.example': 3,
                                   'three.example': 3})
bad = broken(instance_recipe, 'diversity_pass',
             lambda real: lambda st, mod, c=None, i=None: (list(st), {}),
             kitchenmod)
check('instance cap: a stage that lets every post through is caught (%s)'
      % bad.guard['dropped'], bad.guard['dropped'].get(
          'instance_cap', 0) == 60 - 9 and len(bad.items) == 9)
floor_recipe = default_recipe()
floor_recipe.kitchen.modifiers = [Modifier(type='freshness',
                                           decay_minutes=600),
                                  Modifier(type='trending')]
good = cook(floor_recipe)
check('fresh floor: an honest page with trending drops nothing',
      not good.guard['dropped'] and any(
          lifted for _f, lifted in good.kitchen_meta['fresh'].values()))
bad = broken(floor_recipe, 'freshness_factor',
             lambda real: lambda s, m, now=None, floor=False: real(
                 s, m, now, False), kitchenmod)
check('fresh floor: a stage that forgets the floor is caught (%s)'
      % bad.guard['dropped'], bad.guard['dropped'].get('fresh_floor', 0) > 0)
check('the guard\'s reasons are in the closed set of removal reasons',
      all(is_reason('guard:' + promise) for promise in contract.LIST_IDS)
      and not is_reason('guard:made_up'))
# Without trending the floor must never be applied, and a floor that is
# applied anyway is caught too.
plain_floor = default_recipe()
plain_floor.kitchen.modifiers = [Modifier(type='freshness',
                                          decay_minutes=600)]
check('fresh floor: without trending nothing is raised',
      not any(lifted for _f, lifted in
              cook(plain_floor).kitchen_meta['fresh'].values()))
check('fresh_floor promise: iff trending',
      contract.floor_holds(True, 0.2, True)
      and not contract.floor_holds(True, 0.1, False)
      and contract.floor_holds(False, 0.1, False)
      and not contract.floor_holds(False, 0.2, True))

# --- the scope is fetched while More Like This staggers (page one is not
# delayed), and the searches stay 450 ms apart -----------------------------

stamps = {}
engine = FeedEngine(
    account_key='order#1', self_account_id='me',
    fetch_scope=lambda max_id, count: (
        stamps.setdefault('scope', time.monotonic()), [])[1],
    search_statuses=lambda q: (
        stamps.setdefault(q, time.monotonic()), [])[1])
engine.search_stagger_ms = 200
pantrymod._mlt_cache.clear()
engine.process_feed_sync(focus, count=10)
engine.shutdown()
check('the scope fetch starts before the second search, and the searches '
      'are still spaced (scope %.3f, searches %.3f and %.3f s after the '
      'first)' % (stamps['scope'] - stamps['#one'],
                  stamps['#two'] - stamps['#one'],
                  stamps['#three'] - stamps['#two']),
      stamps['scope'] <= stamps['#one'] + 0.05
      and stamps['#two'] - stamps['#one'] >= 0.19
      and stamps['#three'] - stamps['#two'] >= 0.19)

# --- the guard's predicates (review) ----------------------------------------

nan_recipe = default_recipe()
nan_recipe.kitchen.modifiers = []
nan_recipe.kitchen.active_focus_keywords = ['coffee']
pair = [post('1', 'coffee morning'), post('2', 'tea time')]
check('relevance_order: a relevance that is not a number breaks it',
      contract.list_violations(nan_recipe, pair, contract.ListFacts(
          relevance={'1': float('nan'), '2': 0.5})) == {
              '1': ['relevance_order']}
      and contract.list_violations(nan_recipe, pair, contract.ListFacts(
          relevance={'1': 0.9, '2': 0.5})) == {})
floor_recipe_ = default_recipe()
floor_recipe_.kitchen.modifiers = [Modifier(type='freshness',
                                            decay_minutes=120)]
old_post = [post('o', 'old news', age_min=600)]
facts_ = lambda factor, now=NOW: contract.ListFacts(  # noqa: E731
    fresh={'o': (factor, factor == 0.2)}, now=now)
check('fresh_floor: without trending a floored factor is a violation (a '
      'stage that lifts it anyway), and 0.0 is right',
      contract.list_violations(floor_recipe_, old_post, facts_(0.2))
      == {'o': ['fresh_floor']}
      and contract.list_violations(floor_recipe_, old_post, facts_(0.0))
      == {})
floor_recipe_.kitchen.modifiers.append(Modifier(type='trending'))
check('fresh_floor: with trending 0.0 is a violation and 0.2 is right',
      contract.list_violations(floor_recipe_, old_post, facts_(0.0))
      == {'o': ['fresh_floor']}
      and contract.list_violations(floor_recipe_, old_post, facts_(0.2))
      == {})
two_caps = default_recipe()
two_caps.kitchen.modifiers = [
    Modifier(type='diversity', max_per_cluster=3,
             diversity_dimensions=['instance']),
    Modifier(type='diversity', max_per_cluster=1,
             diversity_dimensions=['author', 'instance'])]
same_server = [post('a', 'x', author='p', host='s.example'),
               post('b', 'x', author='q', host='s.example'),
               post('c', 'x', author='r', host='t.example')]
check('instance_cap: every diversity modifier with the dimension holds, '
      'the tightest included (caps %s)' % contract.instance_caps(two_caps),
      contract.instance_caps(two_caps) == [3, 1]
      and contract.list_violations(two_caps, same_server,
                                   contract.ListFacts())
      == {'b': ['instance_cap']})

# --- the library result is checked (N2) ---------------------------------------

if HAVE_KMEDOIDS:
    spread = [post(str(300 + i), 'w%d x' % i, age_min=i + 1)
              for i in range(30)]
    real_get = deps.get

    def with_library(result_class):
        class Library:
            @staticmethod
            def fasterpam(distance, groups, **kwargs):
                return result_class
        deps.get = lambda name, **kw: (Library if name == 'kmedoids'
                                       else real_get(name, **kw))
        try:
            return clustermod.run(
                spread, Modifier(type='similarity_cluster'),
                score_of=kitchenmod.base_score, tie_key=kitchenmod._tie_key)
        finally:
            deps.get = real_get

    class Raises:
        @staticmethod
        def fasterpam(*args, **kwargs):
            raise RuntimeError('boom')

    for label, shape in (
            ('labels out of range', type('R', (), {
                'labels': [0] * 29 + [7], 'medoids': [0, 1, 2],
                'loss': 1.0})),
            ('too few medoids', type('R', (), {
                'labels': [0] * 30, 'medoids': [0], 'loss': 1.0})),
            ('a medoid that is not a post', type('R', (), {
                'labels': [0] * 30, 'medoids': [0, 1, 99], 'loss': 1.0})),
            ('a medoid twice', type('R', (), {
                'labels': [0] * 30, 'medoids': [0, 0, 0], 'loss': 1.0})),
            ('a loss that is not a number', type('R', (), {
                'labels': [0] * 30, 'medoids': [0, 1, 2],
                'loss': float('nan')}))):
        outcome = with_library(shape)
        check('a malformed result (%s) is unavailable with a reason and '
              'keeps every post' % label,
              outcome.status == 'unavailable' and outcome.reason
              and len(outcome.kept) == 30)
    saved_get = deps.get
    deps.get = lambda name, **kw: (Raises if name == 'kmedoids'
                                   else saved_get(name, **kw))
    outcome = clustermod.run(
        spread, Modifier(type='similarity_cluster'),
        score_of=kitchenmod.base_score, tie_key=kitchenmod._tie_key)
    deps.get = saved_get
    check('a library that raises is unavailable (%s)' % outcome.reason,
          outcome.status == 'unavailable' and len(outcome.kept) == 30)
    page_record = clustermod.run(
        spread, Modifier(type='similarity_cluster'),
        score_of=kitchenmod.base_score, tie_key=kitchenmod._tie_key
    ).record()
    check('the page record holds MAX_ITER and the library versions',
          page_record['max_iter'] == clustermod.MAX_ITER
          and page_record['versions']['kmedoids']
          and page_record['versions']['numpy'])

    # --- replay without the library, and with another version ------------------

    import copy  # noqa: E402

    from feedchefaudit import fuzz as fuzzmod  # noqa: E402
    from feedchefaudit import replay as replaymod  # noqa: E402
    from feedchefaudit.records import (LogError, Loaded,  # noqa: E402
                                       expand, load)
    logmod.clear_all()
    grouped_ = default_recipe('replayed')
    grouped_.kitchen.modifiers = [Modifier(type='similarity_cluster',
                                           max_per_cluster=1)]
    fuzzmod.cook(fuzzmod.World(random.Random(5), grouped_), grouped_, 1,
                 pages=1, now=fuzzmod.NOW)
    saved_log = load(expand([os.path.join(TMP, 'logs')])[0])
    check('a cluster record replays identically here',
          saved_log.record['cluster']['status'] == 'ok'
          and replaymod.replay(saved_log).status == 'identical')
    real_reason = deps.unavailable_reason
    deps.unavailable_reason = lambda *names: (
        'kmedoids is not installed' if 'kmedoids' in names
        else real_reason(*names))
    try:
        replaymod.replay(saved_log)
        said = ''
    except LogError as error:
        said = str(error)
    deps.unavailable_reason = real_reason
    check('replaying it without kmedoids is "cannot be replayed here" '
          '(%s), not a mismatch' % said,
          said == 'cannot be replayed here: kmedoids missing')

    def other_library(record):
        record['cluster']['versions']['kmedoids'] = '0.0.1'

    def changed_loss(record):
        record['cluster']['loss'] += 1.0

    def copy_of(change):
        record = copy.deepcopy(saved_log.record)
        change(record)
        return Loaded(saved_log.path, {**saved_log.payload,
                                       'audit': record}, record,
                      saved_log.sidecar)

    check('another library version alone changes nothing, with the same '
          'version a changed loss is a mismatch',
          replaymod.replay(copy_of(other_library)).status == 'identical'
          and replaymod.replay(copy_of(changed_loss)).status == 'mismatch')

    def both(record):
        other_library(record)
        changed_loss(record)

    skipped = replaymod.replay(copy_of(both))
    check('a difference with another library version is skipped, with the '
          'versions named, not a mismatch (%s)' % skipped.detail,
          skipped.status == 'skipped' and 'kmedoids 0.0.1 recorded'
          in skipped.detail)

if not HAVE_KMEDOIDS:
    print('skip - kmedoids is not installed (%s): grouping checks skipped'
          % deps.unavailable_reason('numpy', 'kmedoids'))
    sys.exit(1 if failures else 77)

# --- Java's KMeansClusteringTest, on FasterPAM ----------------------------------------

apple = [post('a%d' % i, 'apple') for i in range(5)]
orange = [post('b%d' % i, 'orange') for i in range(5)]
two = apple + orange
got_labels, got_medoids, loss = clustermod.fit(two, 2)
check('two groups of posts that read alike come out pure (labels %s)'
      % got_labels, len(set(got_labels)) == 2
      and len(set(got_labels[:5])) == 1 and len(set(got_labels[5:])) == 1
      and got_labels[0] != got_labels[5])
check('each medoid is a post of its own group, and groups are numbered by '
      'where their medoid is in the page',
      all(got_labels[m] == g for g, m in enumerate(got_medoids))
      and got_medoids == sorted(got_medoids))
matrix = clustermod.similarity_rows(two)
check('the loss is the sum of the distances of the posts to their medoid '
      '(%.6f)' % loss,
      close(loss, sum(max(0.0, 1.0 - matrix[i][got_medoids[got_labels[i]]])
                      for i in range(10)), 1e-9))
repeats = {tuple(map(tuple, clustermod.fit(posts40, 4)[:2]))
           for _ in range(30)}
random.seed(1234)
again = tuple(map(tuple, clustermod.fit(posts40, 4)[:2]))
check('the groups are deterministic: 30 runs, and another random seed, '
      'give the same ones', len(repeats) == 1 and again in repeats)
check('the start and the seed are recorded with the page',
      clustermod.run(posts40, group_recipe.kitchen.modifiers[0],
                     score_of=kitchenmod.base_score,
                     tie_key=kitchenmod._tie_key).record()
      ['init'] == 'build')

# --- the kitchen and the audit --------------------------------------------------------

start = time.perf_counter()
outcome = clustermod.run(posts40, group_recipe.kitchen.modifiers[0],
                         score_of=kitchenmod.base_score,
                         tie_key=kitchenmod._tie_key)
took = (time.perf_counter() - start) * 1000
check('40 posts are grouped into k = 4 groups, one post of each stays '
      '(%.1f ms)' % took,
      outcome.status == 'ok' and outcome.k == 4 and len(outcome.kept) == 4
      and len({outcome.groups[s['id']][0] for s in outcome.kept}) == 4
      and took < 2000)
recorder = Recorder(group_recipe, cook_id='y')
recorder.clock(NOW)
ordered, _s, meta = kitchenmod.score_statuses(
    posts40, group_recipe, now=NOW, audit=recorder)
cut = {sid for sid, why in recorder.kitchen_removed.items()
       if why == 'cluster_cap'}
check('the others are removed as cluster_cap (36), and the page says how '
      'it grouped (k, loss)',
      len(cut) == 36 and len(ordered) == 4 and meta['cluster']['k'] == 4
      and meta['cluster']['loss'] >= 0 and meta['groups'] is not None)
record = recorder.build()
check('the record holds each post\'s group and medoid, and the loss, '
      'start, seed, weights and strategy of the page',
      record['cluster']['status'] == 'ok'
      and record['cluster']['weights'] == [0.1, 0.7, 0.2]
      and record['cluster']['seed'] == 0
      and all('cluster' in e and e['cluster']['medoid']
              for e in record['posts'] if e['score'])
      and record['engine_rev'] == 3)
heat_recipe = default_recipe()
heat_recipe.kitchen.modifiers = [
    Modifier(type='similarity_cluster', max_per_cluster=2),
    Modifier(type='heat', percentile_threshold=50)]
_o, _s, hmeta = kitchenmod.score_statuses(posts40, heat_recipe, now=NOW)
check('the grouping runs before heat: heat judges what is left of it '
      '(8 posts, 4 hidden)', hmeta['heat']['before'] == 8
      and hmeta['heat']['after'] == 4)

sys.exit(1 if failures else 0)
