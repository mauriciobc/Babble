#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: the promises of the recipe builder that need a crafted case.

Display-free. The fuzz in test-feedchef-contract.py covers each filter on
generated data; this file holds what needs a built situation: sentiment per
language, the freshness and trending properties, the length count, the
person cap across pages, "Only People I Follow" failing closed (loading,
failure, timeout, retry, empty, disk cache), the final guard with a stage
broken on purpose, included hashtags over a focus, and the order ties.
"""

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

from src.feedchef import contract  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import diet as dietmod  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import pantry as pantrymod  # noqa: E402
from src.feedchef import sentiment as sentmod  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import (  # noqa: E402
    Modifier, NetworkReachConfig, default_recipe,
)

failures = 0
NOW = datetime.now(timezone.utc)
ME = 'me'

TMP = tempfile.mkdtemp(prefix='feedchef-promises-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


# --- per-language sentiment --------------------------------------------

def post(sid, text, lang, author=ME, **extra):
    body = {
        'id': sid, 'created_at': (NOW - timedelta(minutes=5)).isoformat(),
        'content': '<p>%s</p>' % text, 'spoiler_text': '', 'sensitive': False,
        'language': lang, 'favourites_count': 4, 'reblogs_count': 0,
        'replies_count': 0, 'account': {'id': author}, 'tags': [],
        'media_attachments': [],
    }
    body.update(extra)
    return body


NEG = {'en': 'This is terrible, I hate it so much',
       'pt': 'Eu odeio isso, é horrível e terrível',
       'pt-BR': 'Eu odeio isso, é horrível e terrível'}
POS = {'en': 'I love this wonderful amazing day!',
       'pt': 'Eu adoro este dia maravilhoso, estou muito feliz!'}
check('sentiment: English is judged', sentmod.score_status(
    post('1', POS['en'], 'en')) > 0.2 and sentmod.score_status(
    post('1', NEG['en'], 'en-US')) < -0.2)
check('sentiment: Portuguese is judged', sentmod.score_status(
    post('1', POS['pt'], 'pt')) > 0.2 and sentmod.score_status(
    post('1', NEG['pt'], 'pt-BR')) < -0.2)
check('sentiment: other languages and no language are not judged',
      all(sentmod.score_status(post('1', NEG['en'], lang)) is None
          for lang in ('es', 'fr', 'de', 'ja', None, '', 'xx')))
boost = {'id': '9', 'account': {'id': 'x'}, 'reblog': post(
    '8', NEG['pt'], 'pt'), 'created_at': NOW.isoformat()}
check('sentiment: a boost is judged by the boosted post\'s language',
      sentmod.score_status(boost) < -0.2)


def run_engine(statuses, recipe, follows=()):
    engine = FeedEngine(
        account_key='lang#%s' % id(statuses), self_account_id=ME,
        fetch_scope=lambda max_id, count: list(statuses),
        fetch_following_page=lambda max_id: (
            [{'id': a} for a in follows], None))
    engine.init_preload()
    try:
        return engine.process_feed_sync(recipe, count=40)
    finally:
        engine.shutdown()


positive = default_recipe('positive')
positive.kitchen.modifiers = []
positive.diet.hard_filters.positive_only = True
mixed = [post('11', NEG['en'], 'en'), post('12', NEG['pt'], 'pt-BR'),
         post('13', 'Esto es terrible, lo odio', 'es'),
         post('14', 'Ceci est terrible, je déteste', 'fr'),
         post('15', 'This is terrible, I hate it so much', None),
         post('16', POS['en'], 'en'), post('17', POS['pt'], 'pt')]
kept = {i['id'] for i in run_engine(mixed, positive).items}
check('only positive: hides negative English and Portuguese, keeps the '
      'rest', kept == {'13', '14', '15', '16', '17'})

boosted = default_recipe('boost')
boosted.kitchen.modifiers = [Modifier(type='sentiment_boost',
                                      sentiment_boost=1.0)]
ranked = run_engine([post('21', 'a plain note', 'en'),
                     post('22', POS['en'], 'en'),
                     post('23', 'Me encanta este día maravilloso', 'es'),
                     post('24', 'a plain note', None)], boosted)
scores = ranked.score_map
check('favor positive: a positive English post ranks above a neutral one',
      scores['22'] > scores['21'])
check('favor positive: posts in other languages are not boosted',
      scores['23'] == scores['21'] == scores['24'])
check('favor positive: Portuguese is boosted too', run_engine(
    [post('31', POS['pt'], 'pt'), post('32', 'uma nota', 'pt')],
    boosted).score_map['31'] > 4.0)


# --- recipe-level properties ------------------------------------------------

def pair_scores(recipe, first, second, extra=(), server=None):
    ordered, scores, _meta = kitchenmod.score_statuses(
        [first, second] + list(extra), recipe, server=server)
    return scores[first['id']], scores[second['id']]


strict_fresh = 0
for index in range(300):
    rng = random.Random(5000 + index)
    recipe = default_recipe('fresh')
    recipe.kitchen.modifiers = [Modifier(
        type='freshness', decay_minutes=rng.choice([30, 360, 2000]))]
    young = rng.randint(1, 1500)
    old = young + rng.randint(1, 1500)
    fav = rng.randint(0, 40)
    a = post('a', 'same', 'en', favourites_count=fav,
             created_at=(NOW - timedelta(minutes=young)).isoformat())
    b = post('b', 'same', 'en', favourites_count=fav,
             created_at=(NOW - timedelta(minutes=old)).isoformat())
    extras = [post('x%d' % i, 'other', 'en',
                   favourites_count=rng.randint(0, 20))
              for i in range(rng.randint(0, 4))]
    newer, older = pair_scores(recipe, a, b, extras)
    if newer < older:
        failures += 1
        print('FAIL - freshness: newer ranked below older, seed %d' % (5000 + index))
        break
    strict_fresh += newer > older
else:
    check('favor fresh posts: of 300 equal pairs, the newer never ranks '
          'below the older (%d strictly above)' % strict_fresh,
          strict_fresh > 50)

strict_trend = 0
for index in range(300):
    rng = random.Random(7000 + index)
    recipe = default_recipe('trend')
    recipe.kitchen.modifiers = [Modifier(
        type='trending', growth_threshold=rng.choice([0.1, 0.3, 1.0]))]
    when = (NOW - timedelta(minutes=rng.randint(1, 600))).isoformat()
    fav = rng.randint(0, 40)
    hot = post('hot', 'same', 'en', favourites_count=fav, created_at=when,
               tags=[{'name': 'rising'}])
    cold = post('cold', 'same', 'en', favourites_count=fav, created_at=when)
    crowd = [post('c%d' % i, 'crowd', 'en', author='c%d' % i,
                  created_at=when, tags=[{'name': 'rising'}])
             for i in range(rng.randint(0, 4))]
    server = kitchenmod.ServerTrendContext(
        trending_hashtags={'rising': 1.0} if rng.random() < 0.5 else {},
        trending_status_ids=set())
    boosted_score, plain_score = pair_scores(
        recipe, hot, cold, crowd, server)
    if boosted_score < plain_score:
        failures += 1
        print('FAIL - trending: trending ranked below plain, seed %d'
              % (7000 + index))
        break
    strict_trend += boosted_score > plain_score
else:
    check('boost trending: of 300 equal pairs a trending post never ranks '
          'below a plain one (%d strictly above)' % strict_trend,
          strict_trend > 30)

trending_post = post('t1', 'same', 'en', favourites_count=3)
other_post = post('t2', 'same', 'en', favourites_count=3)
trend_recipe = default_recipe('t')
trend_recipe.kitchen.modifiers = [Modifier(type='trending')]
lifted, flat = pair_scores(trend_recipe, trending_post, other_post, (),
                           kitchenmod.ServerTrendContext(
                               trending_status_ids={'t1'}))
check('boost trending: a post trending on the server ranks above its twin',
      lifted > flat)


# --- the minimum length counts what the post says ---------------------------

length_recipe = default_recipe('len')
length_recipe.kitchen.modifiers = []
length_recipe.diet.hard_filters.min_content_length = 15
link_post = post(
    'l1', '<a href="https://e.org/x"><span class="invisible">https://'
    '</span><span class="ellipsis">e.org/x</span><span class="invisible">'
    'yzyz</span></a>', 'en')
link_post['content'] = link_post['content'].replace('<p><p>', '<p>')
words_post = post('l2', 'short', 'en')
padded = post('l3', '<p>     abc     </p>', 'en')
kept = {i['id'] for i in run_engine(
    [link_post, words_post, padded], length_recipe).items}
check('minimum length: a link counts in full, HTML and padding do not',
      kept == {'l1'} and contract.text_length(link_post) == len(
          'https://e.org/xyzyz'))
check('minimum length: a boost is measured on the boosted post',
      contract.text_length({'reblog': post('b', 'twelve chars', 'en'),
                            'content': ''}) == 12)


# --- the person cap across pages, keeping the best-ranked ------------------

cap = default_recipe('cap')
cap.kitchen.modifiers = [Modifier(type='diversity', max_per_cluster=2,
                                  diversity_dimensions=['author'])]
cap_pool = []
for n in range(60):
    cap_pool.append(post(
        str(9000 - n), 'p%d' % n, 'en', author='a' if n % 2 == 0 else 'b',
        favourites_count=(n * 7) % 23, replies_count=n % 3))
cap_engine = FeedEngine(
    account_key='cap#1', self_account_id=ME,
    fetch_scope=lambda max_id, count: [
        s for s in cap_pool if max_id is None or int(s['id']) < int(max_id)
    ][:count])
counts, cursor, shown, expected = {}, None, [], []
sim = {}
for _page in range(3):
    page_posts = [s for s in cap_pool
                  if cursor is None or int(s['id']) < int(cursor)][:20]
    result = cap_engine.process_feed_sync(
        cap, max_id=cursor, count=20, author_counts=counts)
    counts = result.author_counts
    cursor = result.scope_oldest_id
    shown.extend(i['id'] for i in result.items)
    ranked_page = sorted(page_posts, key=lambda s: (
        kitchenmod.base_score(s), int(s['id'])), reverse=True)
    for item in ranked_page:
        who = item['account']['id']
        if sim.get(who, 0) < 2:
            sim[who] = sim.get(who, 0) + 1
            expected.append(item['id'])
cap_engine.shutdown()
per = {}
for sid in shown:
    who = next(s for s in cap_pool if s['id'] == sid)['account']['id']
    per[who] = per.get(who, 0) + 1
check('limit per person: 3 pages, no author over the cap (%r)' % per,
      max(per.values()) <= 2 and len(shown) == 4)
check('limit per person: the kept posts are the best-ranked of each '
      'author, not the first fetched', sorted(shown) == sorted(expected))
fresh_engine = FeedEngine(
    account_key='cap#2', self_account_id=ME,
    fetch_scope=lambda max_id, count: cap_pool[:count])
again = fresh_engine.process_feed_sync(cap, count=20, author_counts={})
check('limit per person: a refresh starts the session again',
      len(again.items) == 4)
fresh_engine.shutdown()


# --- only people I follow: fail closed ------------------------------------

def reach_recipe(**kw):
    recipe = default_recipe('reach')
    recipe.kitchen.modifiers = []
    recipe.pantry.network_reach = NetworkReachConfig(
        enabled=True, max_degrees=1, **kw)
    return recipe


feed = [post('r1', 'mine', 'en', author=ME),
        post('r2', 'followed', 'en', author='f1'),
        post('r3', 'stranger', 'en', author='u1'),
        {'id': 'r4', 'created_at': NOW.isoformat(),
         'account': {'id': 'u2'}, 'content': '', 'tags': [],
         'reblog': post('r4c', 'boosted followed', 'en', author='f1')},
        {'id': 'r5', 'created_at': NOW.isoformat(),
         'account': {'id': 'f1'}, 'content': '', 'tags': [],
         'reblog': post('r5c', 'boosted stranger', 'en', author='u3')}]
gate = threading.Event()
state = {'calls': 0}


def slow_following(max_id):
    state['calls'] += 1
    gate.wait(5)
    return [{'id': 'f1'}], None


engine = FeedEngine(
    account_key='reach#loading', self_account_id=ME,
    fetch_scope=lambda max_id, count: list(feed),
    fetch_following_page=slow_following)
engine.init_preload()
box = {}
worker = threading.Thread(target=lambda: box.update(
    result=engine.process_feed_sync(reach_recipe(), count=40)))
begun = time.time()
worker.start()
time.sleep(0.5)
check('reach: while the list loads the cook waits, nothing is shown',
      worker.is_alive() and 'result' not in box)
gate.set()
worker.join(5)
shown = {i['id'] for i in box['result'].items}
check('reach: once loaded, only own and followed authors show; a boost '
      'is judged by the original author (%s)' % sorted(shown),
      shown == {'r1', 'r2', 'r4'})
engine.shutdown()

for label, make in (
        ('an error', lambda max_id: (_ for _ in ()).throw(
            RuntimeError('network down'))),):
    engine = FeedEngine(
        account_key='reach#failed', self_account_id=ME,
        fetch_scope=lambda max_id, count: list(feed),
        fetch_following_page=make)
    engine.init_preload()
    try:
        engine.process_feed_sync(reach_recipe(), count=40)
        raised = None
    except reachmod.FollowListError as error:
        raised = error
    check('reach: %s while loading fails the cook, it shows no strangers'
          % label, raised is not None)
    # The page's Retry cooks again: a list that loads now is used.
    engine.fetch_following_page = lambda max_id: ([{'id': 'f1'}], None)
    again = engine.process_feed_sync(reach_recipe(), count=40)
    check('reach: a retry after a failure loads the list again',
          {i['id'] for i in again.items} == {'r1', 'r2', 'r4'})
    engine.shutdown()

errors = []
engine = FeedEngine(
    account_key='reach#timeout', self_account_id=ME,
    fetch_scope=lambda max_id, count: list(feed),
    fetch_following_page=lambda max_id: (time.sleep(2), ([], None))[1])
engine.reach_wait_timeout = 0.3
engine.init_preload()
finished = threading.Event()
engine.ui_idle = lambda cb: cb()
engine.process_feed(reach_recipe(), count=40,
                    on_done=lambda r: finished.set(),
                    on_error=lambda e: (errors.append(e), finished.set()))
finished.wait(3)
check('reach: a list that does not load in time is an error, not '
      'strangers (%r)' % (errors,),
      len(errors) == 1 and isinstance(errors[0], reachmod.FollowListError))
engine.shutdown()

empty = run_engine(feed, reach_recipe(), follows=())
check('reach: following nobody is a valid answer, only own posts',
      [i['id'] for i in empty.items] == ['r1'])

nosource = FeedEngine(account_key='reach#none', self_account_id=ME,
                      fetch_scope=lambda max_id, count: list(feed))
try:
    nosource.process_feed_sync(reach_recipe(), count=40)
    raised = None
except reachmod.FollowListError as error:
    raised = error
check('reach: no way to load the list fails closed', raised is not None)
nosource.shutdown()

lenient = FeedEngine(account_key='reach#unknown', self_account_id=ME,
                     fetch_scope=lambda max_id, count: list(feed))
open_result = lenient.process_feed_sync(
    reach_recipe(include_unknown=True), count=40)
check('reach: include_unknown in an imported recipe is honoured as written',
      len(open_result.items) == 5)
lenient.shutdown()

# A list on disk is used at once (stale), and the refresh replaces it.
disk = reachmod.NetworkReachCalculator('reach#disk', ME)
disk.set_following({'f1'})
hold = threading.Event()
engine = FeedEngine(
    account_key='reach#disk', self_account_id=ME,
    fetch_scope=lambda max_id, count: list(feed),
    fetch_following_page=lambda max_id: (hold.wait(3), ([], None))[1])
engine.init_preload()
begun = time.time()
stale = engine.process_feed_sync(reach_recipe(), count=40)
check('reach: a cache from disk is used without waiting (stale until '
      'the refresh)', time.time() - begun < 1.5
      and {i['id'] for i in stale.items} == {'r1', 'r2', 'r4'}
      and engine.reach.cache_snapshot().stale)
hold.set()
engine.shutdown()


# --- the guard with a broken stage -------------------------------------------

violators = {
    'keyword_blocklist': post('g1', 'buy spam now', 'en', author='f1'),
    'must_have_media': post('g2', 'no media here at all ok', 'en',
                            author='f1'),
    'must_have_alt_text': post('g3', 'has media without alt text', 'en',
                               author='f1', media_attachments=[
                                   {'id': '1', 'description': ''}]),
    'no_sensitive_media': post('g4', 'sensitive but long enough', 'en',
                               author='f1', sensitive=True),
    'min_content_length': post('g5', 'tiny', 'en', author='f1'),
    'positive_only': post('g6', NEG['en'], 'en', author='f1'),
    'hashtags_exclude': post('g7', 'excluded tag long enough here', 'en',
                             author='f1', tags=[{'name': 'tea'}]),
    'hashtags_include': post('g8', 'no included tag, long enough', 'en',
                             author='f1'),
    'network_reach': post('g9', 'a stranger wrote this long enough', 'en',
                          author='u1', tags=[{'name': 'coffee'}]),
}
good = post('g0', 'a perfectly good and long enough post', 'en',
            author='f1', tags=[{'name': 'coffee'}],
            media_attachments=[{'id': '2', 'description': 'ok'}])
for status in violators.values():
    if not status['tags']:
        status['tags'] = [{'name': 'coffee'}]
    if not status['media_attachments'] and status['id'] != 'g2':
        status['media_attachments'] = [{'id': 'm', 'description': 'ok'}]
violators['hashtags_include']['tags'] = [{'name': 'art'}]
violators['must_have_media']['media_attachments'] = []
violators['must_have_alt_text']['media_attachments'] = [
    {'id': '1', 'description': ''}]
everything = default_recipe('everything')
everything.kitchen.modifiers = []
everything.pantry.hashtags_include = ['coffee']
everything.pantry.hashtags_exclude = ['tea']
everything.pantry.network_reach = NetworkReachConfig(
    enabled=True, max_degrees=1)
everything.diet.keyword_blocklist = ['spam']
hard = everything.diet.hard_filters
hard.must_have_media = hard.must_have_alt_text = True
hard.no_sensitive_media = hard.positive_only = True
hard.min_content_length = 10
scope = [good] + list(violators.values())
# The violators carry the one flaw each; the media row of g2 has none.
clean = run_engine(scope, everything, follows=['f1'])
check('guard: with every stage working it drops nothing and the stages '
      'removed each violator (%s)' % sorted(i['id'] for i in clean.items),
      [i['id'] for i in clean.items] == ['g0']
      and not clean.guard['dropped'])

real = (dietmod.apply_diet, reachmod.apply_network_reach,
        pantrymod.merge_pantry)
dietmod.apply_diet = lambda statuses, recipe, **kw: (list(statuses), [])
reachmod.apply_network_reach = lambda statuses, recipe, calc: (
    list(statuses), {'cache_status': 'broken'})
pantrymod.merge_pantry = lambda **kw: list(kw['scope_posts'])
try:
    broken = run_engine(scope, everything, follows=['f1'])
finally:
    (dietmod.apply_diet, reachmod.apply_network_reach,
     pantrymod.merge_pantry) = real
check('guard: with broken stages it still returns only the good post (%s)'
      % [i['id'] for i in broken.items],
      [i['id'] for i in broken.items] == ['g0'])
check('guard: it counts what it checked and what it dropped by promise '
      '(%r)' % (broken.guard,),
      broken.guard['checked'] == len(scope)
      # language_guess has its own broken-judge case in feedchef langguess:
      # the stages cannot break it with a violator, only a judge can.
      and set(broken.guard['dropped'])
      == set(contract.PROMISE_IDS) - {'language_guess'})
logged = broken.logger.log.reliability['errors']
check('guard: each violation is in the decision log as '
      'contract:<promise>:<post id>',
      all('contract:%s:%s' % (name, status['id']) in logged
          for name, status in violators.items()))
check('guard: the decision log carries the guard stats',
      broken.logger.log.to_dict()['stages']['guard']['checked']
      == len(scope))

# Hashtag timelines match by construction; the guard asserts it.
tagged = default_recipe('tagged')
tagged.kitchen.modifiers = []
tagged.pantry.hashtags_include = ['coffee']
engine = FeedEngine(
    account_key='tagged#1', self_account_id=ME,
    fetch_scope=lambda max_id, count: [],
    fetch_hashtag=lambda tag, count: [
        post('h1', 'x', 'en', tags=[{'name': 'coffee'}]),
        post('h2', 'x', 'en', tags=[{'name': 'tea'}])])
lying = engine.process_feed_sync(tagged, count=40)
check('include hashtags: a hashtag timeline that returns a post without '
      'the tag is caught by the guard',
      [i['id'] for i in lying.items] == ['h1']
      and lying.guard['dropped'] == {'hashtags_include': 1})
engine.shutdown()

# A focus narrows within the recipe: it does not escape the included tags.
focus_recipe = default_recipe('focus')
focus_recipe.kitchen.modifiers = []
focus_recipe.pantry.hashtags_include = ['coffee']
focus_recipe.kitchen.active_focus_hashtags = ['cats']
pantrymod._mlt_cache.clear()
engine = FeedEngine(
    account_key='focus#1', self_account_id=ME,
    fetch_scope=lambda max_id, count: [
        post('s1', 'scope', 'en', tags=[{'name': 'coffee'}])],
    search_statuses=lambda query: [
        post('m1', 'focus', 'en', tags=[{'name': 'cats'}]),
        post('m2', 'focus', 'en', tags=[{'name': 'cats'},
                                       {'name': 'coffee'}])])
focused = engine.process_feed_sync(focus_recipe, count=40)
check('include hashtags: More Like This results obey them (%s)'
      % [i['id'] for i in focused.items],
      {i['id'] for i in focused.items} == {'s1', 'm2'})
engine.shutdown()

# Order: newest first by the boosted post's date, ties by id; with a focus.
chrono = default_recipe('chrono')
chrono.kitchen.modifiers = [Modifier(type='diversity', max_per_cluster=5,
                                     diversity_dimensions=['author'])]
chrono.kitchen.base_sort = 'chronological'
same_time = NOW - timedelta(hours=1)
items = [post('40', 'a', 'en', favourites_count=1,
              created_at=same_time.isoformat()),
         post('41', 'b', 'en', favourites_count=2,
              created_at=same_time.isoformat()),
         post('39', 'c', 'en', favourites_count=3,
              created_at=(NOW - timedelta(minutes=5)).isoformat()),
         post('42', 'd', 'en', favourites_count=9,
              created_at=(NOW - timedelta(hours=9)).isoformat())]
ordered = run_engine(items, chrono)
check('order: newest first, ties by id (%s)'
      % [i['id'] for i in ordered.items],
      [i['id'] for i in ordered.items] == ['39', '41', '40', '42'])

sys.exit(1 if failures else 0)
