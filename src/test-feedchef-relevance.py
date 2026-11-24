#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: the relevance sort, the freshness floor and the fading focus.

Display-free. The cases are Java's own tests (`TFIDFCalculatorTest`,
`RelevanceComparatorTest`, `FeedEngineTest`), ported, and the formulas written
out again from Java's source: the smoothed IDF, the augmented TF, the
cosine, the reference vector, and the floor of 0.2 that "Boost Trending
Topics" puts under "Favor Fresh Posts". The relevance runs on the engine
worker, in plain Python (no numpy is imported by it).
"""

import math
import os
import random
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import focus as focusmod  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import relevance as relevancemod  # noqa: E402
from src.feedchef import deps  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import (  # noqa: E402
    KitchenConfig, Modifier, default_recipe, max_per, recipe_from_dict,
    recipe_to_dict, validate_for_import)
from src.feedchef.textutil import tfidf_vector  # noqa: E402

failures = 0
TMP = tempfile.mkdtemp(prefix='feedchef-relevance-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))
NOW = datetime.now(timezone.utc)


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


def close(a, b, tol=1e-12):
    return abs(a - b) <= tol


def post(sid, text, *, fav=0, reblog=0, reply=0, age_min=10, author='a',
         tags=()):
    return {
        'id': sid, 'content': '<p>%s</p>' % text, 'spoiler_text': '',
        'created_at': (NOW - timedelta(minutes=age_min)).isoformat(),
        'favourites_count': fav, 'reblogs_count': reblog,
        'replies_count': reply, 'sensitive': False,
        'account': {'id': author, 'url': 'https://ex.ample/@%s' % author},
        'tags': [{'name': t} for t in tags], 'media_attachments': []}


# --- Java's TFIDFCalculatorTest: the formulas ------------------------------

corpus = [post('1', 'apple banana'), post('2', 'apple orange'),
          post('3', 'banana'), post('4', 'noise apple')]
vectors = relevancemod.vectors_of(corpus)
# N = 4; apple is in 3 documents, banana in 2, orange in 1: the smoothed
# IDF is log(4 / (1 + df)) + 1 (1.0, 1.2877, 1.6931) and every term here
# occurs once, so the augmented TF is 0.5 + 0.5 * 1 / 1 = 1.
check('smoothed IDF: apple log(4/4)+1, orange log(4/2)+1 (Java)',
      close(vectors[1].terms['apple'], 1.0)
      and close(vectors[1].terms['orange'], math.log(2) + 1)
      and close(vectors[0].terms['banana'], math.log(4 / 3) + 1))
check('smoothed IDF: the rarer term scores higher, common ones still '
      'score above zero',
      vectors[1].terms['orange'] > vectors[1].terms['apple'] > 0)
check('the norm is the Euclidean norm of the terms',
      close(vectors[1].norm, math.sqrt(1.0 + (math.log(2) + 1) ** 2)))
# A term in every document keeps a positive weight (Java's "no negative
# values" fix): log(3 / (1 + 3)) + 1 = 0.7123.
cats = relevancemod.vectors_of([post('1', 'cats are cute'),
                                post('2', 'cats are fluffy'),
                                post('3', 'cats are adorable')])
check('a term in every document is positive (%.4f)' % cats[0].terms['cats'],
      close(cats[0].terms['cats'], math.log(3 / 4) + 1)
      and cats[0].terms['cats'] > 0)
check('and the posts still resemble each other',
      relevancemod.cosine(cats[0], cats[1]) > 0)
# The augmented TF: the most frequent term has 1.0, one half as frequent
# 0.75.
tf = tfidf_vector(['rain', 'rain', 'sun'], {'rain': 1, 'sun': 1}, 1)
check('augmented TF: 0.5 + 0.5 * count / most frequent',
      close(tf['rain'], 1.0 * (math.log(1 / 2) + 1))
      and close(tf['sun'], 0.75 * (math.log(1 / 2) + 1)))

words = relevancemod.vectors_of([post('1', 'apple banana'),
                                 post('2', 'cherry date'),
                                 post('3', 'apple cherry'),
                                 post('4', 'noise')])
check('cosine: no shared term is 0 (Java)',
      relevancemod.cosine(words[0], words[1]) == 0.0)
check('cosine: a shared term is above 0',
      relevancemod.cosine(words[0], words[2]) > 0.0)
check('cosine: a vector with itself is 1',
      close(relevancemod.cosine(words[0], words[0]), 1.0, 1e-9))
check('cosine is symmetric to the last bit',
      relevancemod.cosine(words[0], words[2])
      == relevancemod.cosine(words[2], words[0]))
check('cosine with an empty vector is 0',
      relevancemod.cosine(relevancemod.Vector({}), words[0]) == 0.0)
check('a vector keeps at most 100 terms, the best first (Java)',
      len(relevancemod.Vector({'t%03d' % i: float(i)
                               for i in range(150)}, 100).items) == 100
      and 't149' in relevancemod.Vector(
          {'t%03d' % i: float(i) for i in range(150)}, 100).terms
      and 't049' not in relevancemod.Vector(
          {'t%03d' % i: float(i) for i in range(150)}, 100).terms)

# --- Java's RelevanceComparatorTest ----------------------------------------

space = [post('ref1', 'space exploration rockets stars'),
         post('sim1', 'space rockets launch stars'),
         post('mod1', 'space telescope stars'),
         post('unrel1', 'cooking recipe pasta sauce'),
         post('unrel2', 'music concert band guitar')]
space_vectors = relevancemod.vectors_of(space)
scores = {s['id']: relevancemod.cosine(space_vectors[0], v)
          for s, v in zip(space, space_vectors, strict=True)}
order = sorted((s for s in space if s['id'] != 'ref1'),
               key=lambda s: -scores[s['id']])
check('relevance sorts by similarity: sim1, then mod1, then the unrelated',
      [s['id'] for s in order[:2]] == ['sim1', 'mod1']
      and {s['id'] for s in order[2:]} == {'unrel1', 'unrel2'})
check('the reference post is 1.0 to itself (Java: 1.0 +- 0.001)',
      close(scores['ref1'], 1.0, 1e-9))
check('a similar post scores well and an unrelated one 0 (sim1 %.3f)'
      % scores['sim1'], scores['sim1'] > 0.5 and scores['unrel1'] == 0.0)
check('relevance_scores gives one score per post, by id; an unknown id '
      'has none',
      set(relevancemod.relevance_scores(
          space, ['space', 'stars'])) == {s['id'] for s in space})

# --- the reference vector of a focus ---------------------------------------

ref = relevancemod.reference_vector(['coffee', 'tea'])
check('no weights: every keyword is 1.0',
      ref.terms == {'coffee': 1.0, 'tea': 1.0}
      and close(ref.norm, math.sqrt(2)))
ref = relevancemod.reference_vector(['coffee', 'tea', 'milk'],
                                    [0.8, 0, -3])
check('a weight not above zero is 0.1 (Java); the reference vector is '
      'scaled so that its largest weight is 1 (only proportions matter)',
      ref.terms == {'coffee': 1.0, 'tea': 0.125, 'milk': 0.125})
check('weights that do not match the keywords are ignored (Java)',
      relevancemod.reference_vector(
          ['coffee', 'tea'], [0.5]).terms == {'coffee': 1.0, 'tea': 1.0})
check('a keyword is read in the form the words are tokenized in',
      relevancemod.reference_vector(['Café']).terms == {'cafe': 1.0})
check('a blank keyword is no focus',
      relevancemod.focus_keywords(KitchenConfig(
          active_focus_keywords=[' ', ''])) == []
      and relevancemod.focus_keywords(KitchenConfig(
          active_focus_keywords=['coffee'])) == ['coffee'])

# --- no numpy, no dependence on order ---------------------------------------

rng = random.Random(7)
pool = [post(str(i), ' '.join(rng.sample(
    ['coffee', 'morning', 'weather', 'garden', 'music', 'travel', 'books',
     'rain', 'story', 'night'], 4))) for i in range(120)]
start = time.perf_counter()
base = relevancemod.relevance_scores(pool, ['coffee', 'morning'], [1.0, 0.5])
took = (time.perf_counter() - start) * 1000
check('relevance reads plain Python: numpy was not imported (%.1f ms for '
      '120 posts)' % took, 'numpy' not in sys.modules and took < 500)
shuffled = list(pool)
random.Random(3).shuffle(shuffled)
check('the scores do not depend on the order of the posts, to the bit',
      relevancemod.relevance_scores(shuffled, ['coffee', 'morning'],
                                    [1.0, 0.5]) == base)

# --- the focus orders the page, over the engagement (FeedEngineTest) ---------

mock = [post('1', 'Linux is a kernel. Arch uses pacman.', fav=50, reblog=20),
        post('2', 'I hate everything. This is the worst app ever.'),
        post('3', 'Morning coffee is the best.', fav=5, reply=1),
        post('4', 'Just walking the dog.', fav=2)]
recipe = default_recipe()
recipe.kitchen.modifiers = []
recipe.kitchen.active_focus_keywords = ['coffee', 'morning', 'best']
ordered, _scores, meta = kitchenmod.score_statuses(mock, recipe, now=NOW)
check('relevance sorting overrides the score: the coffee post is first '
      '(Java FeedEngineTest)', ordered[0]['id'] == '3')
check('and the ones with no match follow newest first, then by id',
      [s['id'] for s in ordered] == ['3', '4', '2', '1'])
check('the kitchen reports the relevance it used',
      meta['relevance_scores']['3'] > 0
      and meta['relevance_scores']['1'] == 0.0)
recipe.kitchen.active_focus_keywords = None
ordered, _scores, meta = kitchenmod.score_statuses(mock, recipe, now=NOW)
check('without a focus the engagement orders the page again',
      ordered[0]['id'] == '1' and meta['relevance_scores'] is None)

# --- the freshness floor (FeedEngineTest) ----------------------------------

viral = post('viral', 'Trending topic.', fav=100, reblog=50, age_min=240)
recent = post('recent', 'Just posted.', reblog=0, reply=1, age_min=60)
fresh = Modifier(type='freshness', decay_minutes=120)
trend = Modifier(type='trending')
floored = default_recipe()
floored.kitchen.modifiers = [fresh, trend]
ordered, _s, meta = kitchenmod.score_statuses([recent, viral], floored,
                                              now=NOW)
check('trending: an old viral post outranks a recent low one (Java '
      'FeedEngineTest, the floor of 0.2)', ordered[0]['id'] == 'viral')
check('the factor of the old post is the floor, flagged',
      meta['fresh']['viral'] == (0.2, True)
      and meta['fresh']['recent'][1] is False)
plain = default_recipe()
plain.kitchen.modifiers = [fresh]
ordered, _s, meta = kitchenmod.score_statuses([recent, viral], plain,
                                              now=NOW)
check('without trending the old post scores zero and the floor is never '
      'applied', ordered[0]['id'] == 'recent'
      and meta['fresh']['viral'] == (0.0, False))
check('the floor is Java\'s 0.2',
      kitchenmod.FRESHNESS_FLOOR == 0.2
      and kitchenmod.freshness_factor(viral, fresh, NOW, True) == 0.2
      and kitchenmod.freshness_factor(viral, fresh, NOW, False) == 0.0
      and kitchenmod.freshness_factor(
          post('n', 'now', age_min=12), fresh, NOW, True) == 0.9)
check('a post newer than the fade-out is not floored',
      not kitchenmod.floor_applied(post('n', 'now', age_min=12), fresh, NOW)
      and kitchenmod.floor_applied(viral, fresh, NOW))

# --- focus_decay (Babble's definition: Java never applied it) -------------

kit = KitchenConfig(active_focus_keywords=['a1', 'b2'],
                    active_focus_hashtags=['x'], focus_decay=0.5)
check('decay: weights are multiplied and the recipe changes',
      focusmod.decay_focus(kit)
      and kit.active_focus_weights == [0.5, 0.5]
      and kit.active_focus_keywords == ['a1', 'b2'])
focusmod.decay_focus(kit)
focusmod.decay_focus(kit)
check('decay: 0.5 ** 3 = 0.125 is still above 0.1',
      kit.active_focus_weights == [0.125, 0.125])
check('decay: a weight under 0.1 is dropped, and with none left the '
      'focus clears, hashtags too',
      focusmod.decay_focus(kit) and kit.active_focus_keywords is None
      and kit.active_focus_weights is None
      and kit.active_focus_hashtags is None)
kit = KitchenConfig(active_focus_keywords=['a1', 'b2', 'c3'],
                    active_focus_weights=[1.0, 0.15, 0.4], focus_decay=0.5)
focusmod.decay_focus(kit)
check('decay: only the term that fell under 0.1 goes (0.15 * 0.5 = 0.075)',
      kit.active_focus_keywords == ['a1', 'c3']
      and kit.active_focus_weights == [0.5, 0.2])
exact = KitchenConfig(active_focus_keywords=['a1'],
                      active_focus_weights=[0.2], focus_decay=0.5)
focusmod.decay_focus(exact)
check('decay: a weight of exactly 0.1 is kept',
      exact.active_focus_keywords == ['a1']
      and exact.active_focus_weights == [0.1])
check('decay 1.0 (the default) never changes anything',
      not focusmod.decay_focus(KitchenConfig(
          active_focus_keywords=['a1'], focus_decay=1.0))
      and not focusmod.decay_focus(KitchenConfig(
          active_focus_keywords=['a1'])))
hashtags = KitchenConfig(active_focus_hashtags=['x'], focus_decay=0.5)
steps = 0
while hashtags.active_focus_hashtags:
    check_changed = focusmod.decay_focus(hashtags)
    steps += 1
check('a focus of hashtags only fades by its strength: 0.5 ** 4 = 0.0625 '
      'clears it at the 4th refresh (%d)' % steps,
      steps == 4 and check_changed and hashtags.active_focus_strength == 1.0)
kept = KitchenConfig(active_focus_hashtags=['x'], active_focus_keywords=['aaa'],
                     focus_decay=0.5)
focusmod.decay_focus(kept)
check('the strength is multiplied on each refresh and stored',
      kept.active_focus_strength == 0.5)
check('decay reads a weight as the relevance sort does: 0, negative and '
      'NaN are 0.1, so they are gone after one refresh at 0.5',
      all(focusmod.decay_focus(k) and k.active_focus_keywords is None
          for k in (KitchenConfig(active_focus_keywords=['aaa'],
                                  active_focus_weights=[w], focus_decay=0.5)
                    for w in (0, -1, float('nan')))))
check('the strength survives a round trip and an old recipe reads 1.0',
      recipe_from_dict(recipe_to_dict(default_recipe()))
      .kitchen.active_focus_strength == 1.0
      and recipe_from_dict({'kitchen': {'active_focus_strength': 0.4}})
      .kitchen.active_focus_strength == 0.4
      and recipe_from_dict({'kitchen': {'active_focus_strength': 'x'}})
      .kitchen.active_focus_strength == 1.0)
check('a decay of 0 or less clears the focus at the next refresh',
      focusmod.decay_focus(KitchenConfig(
          active_focus_keywords=['a1'], focus_decay=0.0)))
cycles = KitchenConfig(active_focus_keywords=['a1'], focus_decay=0.9)
refreshes = 0
while cycles.active_focus_keywords:
    focusmod.decay_focus(cycles)
    refreshes += 1
check('decay: 0.9 lasts about log(0.1) / log(0.9) = 21.9 refreshes (%d)'
      % refreshes, refreshes == 22)

# --- the order does not change while the keywords remain (S3) ---------------

rng = random.Random(3)
vocab = 'alpha beta gamma delta omega sigma kappa theta lambda zeta'.split()
flips = 0
for _trial in range(400):
    count = rng.randint(5, 30)
    pool_ = [post(str(100 + i), ' '.join(rng.choices(vocab, k=rng.randint(
        1, 4))), age_min=rng.choice([5, 5, 5, 10])) for i in range(count)]
    words = rng.sample(vocab, rng.randint(1, 4))
    weights = [rng.choice([0.3, 0.5, 1.0, 2.0, 0.7]) for _w in words]
    factor = rng.choice([0.9, 0.5, 0.99])
    faded = [w * factor for w in weights]
    one = relevancemod.relevance_scores(pool_, words, weights)
    two = relevancemod.relevance_scores(pool_, words, faded)
    flips += ([p['id'] for p in kitchenmod.order_statuses(pool_, one, 'score')]
              != [p['id'] for p in kitchenmod.order_statuses(
                  pool_, two, 'score')])
check('scaling every weight by one factor never reorders a page (400 '
      'random pages, %d reordered)' % flips, flips == 0)
equal = [post('1', 'alpha beta'), post('2', 'beta alpha'),
         post('3', 'alpha beta')]
scores = relevancemod.relevance_scores(equal, ['alpha', 'beta'], [0.7, 0.3])
check('equally relevant posts tie exactly, whatever the rounding of the '
      'sums (%r)' % sorted(set(scores.values())),
      len(set(scores.values())) == 1)

# --- a focus that matches nothing keeps the recipe's order (N4) -------------

nothing = default_recipe()
nothing.kitchen.modifiers = []
nothing.kitchen.active_focus_keywords = ['zzzzzz']
ordered, _s, meta = kitchenmod.score_statuses(mock, nothing, now=NOW)
check('a focus no post matches does not turn the page into newest first: '
      'the recipe\'s order stays, and the page says so',
      ordered[0]['id'] == '1' and meta['focus_unmatched'] is True
      and meta['relevance_scores'] is None
      and 'relevance' not in meta)

# --- max_per_cluster is never an exception (S1) -----------------------------

check('a malformed cap reads as 1 and never raises',
      [max_per(v) for v in (None, 0, -3, 'x', float('inf'), float('nan'),
                            10 ** 400, 4, '2', True)]
      == [1, 1, 1, 1, 1, 1, 10 ** 400, 4, 2, 1])
bad_caps = []
for value in ('x', float('inf'), 0, -1, None, 1.5, True):
    for kind in ('similarity_cluster', 'diversity'):
        try:
            validate_for_import({'meta': {'name': 'n'}, 'kitchen': {
                'modifiers': [{'type': kind, 'max_per_cluster': value}]}})
            bad_caps.append((kind, value))
        except ValueError:
            pass
check('an imported cap that is not a whole number of 1 or more is '
      'rejected (None, a missing one, is fine; accepted: %r)' % bad_caps,
      bad_caps == [('similarity_cluster', None), ('diversity', None)])
for kind in ('similarity_cluster', 'diversity'):
    cooked = default_recipe()
    cooked.kitchen.modifiers = [Modifier(
        type=kind, max_per_cluster='x', diversity_dimensions=['instance'])]
    ordered, _s, meta = kitchenmod.score_statuses(
        [post(str(i), 'word%d' % i, author='a%d' % i) for i in range(30)],
        cooked, now=NOW)
    check('a cap of "x" on %s cooks a page without raising' % kind,
          len(ordered) >= 1)
try:
    validate_for_import({'meta': {'name': 'n'}, 'kitchen': {'modifiers': [
        {'type': 'diversity', 'max_per_cluster': 0}]}})
except ValueError as error:
    check('the message says what is wrong (%s)' % error,
          'max_per_cluster' in str(error) and '1 or more' in str(error))

# --- the cook works on a copy of the recipe (N6) ---------------------------

fading = default_recipe()
fading.kitchen.modifiers = []
fading.kitchen.active_focus_keywords = ['coffee']
fading.kitchen.focus_decay = 0.5


def mutate_while_fetching(max_id, count):
    focusmod.decay_focus(fading.kitchen)  # what a refresh does meanwhile
    return [dict(p) for p in mock]


copy_engine = FeedEngine(
    account_key='copy#1', self_account_id='me',
    fetch_scope=mutate_while_fetching, search_statuses=None)
cooked = copy_engine.process_feed_sync(fading, count=10)
copy_engine.shutdown()
snapshot = cooked.logger.record['recipe']['snapshot']['kitchen']
check('a recipe changed while its cook runs does not change the cook or '
      'its record (the record says weights None, the recipe now %s)'
      % fading.kitchen.active_focus_weights,
      snapshot['active_focus_weights'] is None
      and fading.kitchen.active_focus_weights == [0.5]
      and cooked.items[0]['id'] == '3')

# --- everything on the engine worker (Principle 5) --------------------------

seen_threads = {}
warned_before = set(deps._warned)
real_scores = relevancemod.relevance_scores


def spy(*args, **kwargs):
    seen_threads['relevance'] = threading.current_thread()
    return real_scores(*args, **kwargs)


relevancemod.relevance_scores = spy
engine = FeedEngine(
    account_key='relevance#1', self_account_id='me',
    fetch_scope=lambda max_id, count: [dict(p) for p in mock],
    search_statuses=lambda query: [])
done = threading.Event()
box = []
focused = default_recipe()
focused.kitchen.modifiers = []
focused.kitchen.active_focus_keywords = ['coffee']
engine.process_feed(focused, on_done=lambda r: (box.append(r), done.set()),
                    on_error=lambda e: (box.append(e), done.set()))
done.wait(30)
engine.shutdown()
worker = seen_threads.get('relevance')
check('the relevance ran on the engine worker, not the main thread (%s)'
      % (worker.name if worker else None),
      worker is not None and worker is not threading.main_thread()
      and worker.name.startswith('feedchef-engine'))
check('and the cooked page is ordered by it',
      box and not isinstance(box[0], BaseException)
      and box[0].items[0]['id'] == '3')
relevancemod.relevance_scores = real_scores
check('no dependency was imported on the main thread by the cook',
      not deps._warned - warned_before)

sys.exit(1 if failures else 0)
