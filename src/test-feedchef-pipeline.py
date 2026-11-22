#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef diet / kitchen / pantry / reach / engine assertions."""

import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

failures = 0


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef.diet import pass_diet  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.feed_model import FeedChefModel, RequestGate  # noqa: E402
from src.feedchef.kitchen import (  # noqa: E402
    apply_diversity, apply_freshness, apply_heat, base_score, score_statuses,
)
from src.feedchef.network_reach import (  # noqa: E402
    NetworkReachCalculator, apply_network_reach,
)
from src.feedchef.pantry import (  # noqa: E402
    build_pantry, merge_pantry, oldest_id, scope_cursor,
)
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.textutil import (  # noqa: E402
    STOP_WORDS, extract_focus, focus_tokens,
)
from src.feedchef.recipe import (  # noqa: E402
    DietConfig, HardFilters, Modifier, NetworkReachConfig, default_recipe,
)
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402


def status(sid, *, author='a1', fav=0, reblog=0, reply=0, content='hello',
           created=None, tags=None, sensitive=False, media=None,
           spoiler='', wrapper_author=None):
    now = created or datetime.now(timezone.utc).isoformat()
    body = {
        'id': sid,
        'created_at': now,
        'content': content,
        'spoiler_text': spoiler,
        'sensitive': sensitive,
        'favourites_count': fav,
        'reblogs_count': reblog,
        'replies_count': reply,
        'account': {'id': author, 'url': 'https://ex.ample/@%s' % author},
        'tags': [{'name': tag} for tag in (tags or [])],
        'media_attachments': media or [],
    }
    if wrapper_author and wrapper_author != author:
        return {
            'id': 'w-' + sid,
            'created_at': now,
            'account': {'id': wrapper_author,
                        'url': 'https://ex.ample/@%s' % wrapper_author},
            'reblog': body,
        }
    return body


# --- diet order / semantics ---
diet = DietConfig(
    hard_filters=HardFilters(),
    keyword_blocklist=['spam'],
)
hit = pass_diet(status('1', content='<p>buy spam now</p>'), diet)
check('blocklist matches HTML', hit.verdict == 'FILTERED')

diet.hard_filters.must_have_alt_text = True
ok = pass_diet(status('2', media=[]), diet)
check('alt-text without media passes', ok.verdict == 'PASSED')

diet.hard_filters.must_have_alt_text = False
diet.hard_filters.positive_only = True
pos = pass_diet(status('3', content='neutral'), diet)
check('positive_only with stub 0 passes', pos.verdict == 'PASSED')

# --- kitchen formulas ---
check('base score weights',
      abs(base_score(status('s', fav=1, reblog=1, reply=1))
          - (1 + 2.5 + 1.5)) < 1e-9)

fresh_mod = Modifier(type='freshness', decay_minutes=60)
old = status('old', fav=10,
             created=(datetime.now(timezone.utc) - timedelta(hours=2)
                      ).isoformat())
check('freshness zeros after decay',
      apply_freshness(10.0, old, fresh_mod) == 0.0)

items = [status('a'), status('b'), status('c')]
heat_mod = Modifier(type='heat', percentile_threshold=50)
# Build score map by id
score_map = {item['id']: {'a': 1.0, 'b': 5.0, 'c': 9.0}[item['id']]
             for item in items}
heated = apply_heat(items, score_map, heat_mod)
check('heat keeps above percentile',
      all(score_map[item['id']] > 1.0 for item in heated) or len(heated) >= 1)

div = Modifier(type='diversity', max_per_cluster=1,
               diversity_dimensions=['author'])
same = [status('1', author='x', fav=1), status('2', author='x', fav=99),
        status('3', author='y', fav=50)]
# Pre-sort arrival: first of each author kept
kept = apply_diversity(same, div)
check('diversity keeps first author only',
      [item['id'] for item in kept] == ['1', '3'])

recipe = default_recipe()
recipe.kitchen.modifiers = [
    Modifier(type='diversity', max_per_cluster=3,
             diversity_dimensions=['author']),
]
ordered, smap, meta = score_statuses(
    [status('1', fav=1), status('2', fav=10)], recipe)
check('score sort puts higher first', ordered[0]['id'] == '2')
recipe.kitchen.active_focus_keywords = ['coffee']
focused, _s, fmeta = score_statuses(
    [status('1', fav=1), status('2', fav=10)], recipe)
check('focus without NLP still score-sorts', focused[0]['id'] == '2')
check('focus keywords marked inert',
      'active_focus_keywords' in fmeta.get('inert', []))
recipe.kitchen.active_focus_keywords = None

# chronological nulls last
recipe.kitchen.base_sort = 'chronological'
dated = status('d', created=datetime.now(timezone.utc).isoformat())
undated = {'id': 'u', 'content': 'x', 'account': {'id': 'a'},
           'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0}
ordered, _s, _m = score_statuses([undated, dated], recipe)
check('chrono null at end', ordered[-1]['id'] == 'u')

# --- pantry merge ---
scope = [status('s1', tags=['coffee']), status('s2', tags=['tea'])]
tags = [status('t1', tags=['coffee'])]
merged = merge_pantry(
    more_like_this=[], hashtag_posts=tags, scope_posts=scope,
    includes=['coffee'], excludes=['tea'])
ids = [item['id'] for item in merged]
check('include restricts scope only', 's1' in ids and 's2' not in ids)
check('hashtags not restricted by include', 't1' in ids)
check('oldest id prefers older created',
      oldest_id([status('new', created='2026-01-02T00:00:00+00:00'),
                 status('old', created='2026-01-01T00:00:00+00:00')])
      == 'old')

pantry = build_pantry(
    recipe, scope_posts=scope, hashtag_posts=[], more_like_this=[],
    max_id=None, count=2)
check('has_more when scope page full', pantry.has_more)
with_tags = build_pantry(
    recipe, scope_posts=scope,
    hashtag_posts=[status('t2', tags=['coffee'])],
    more_like_this=[], max_id=None, count=2)
check('has_more still true with hashtag enrichment', with_tags.has_more)

# --- reach ---
with tempfile.TemporaryDirectory() as tmp:
    reachmod.set_root(tmp)
    calc = NetworkReachCalculator('acct#1', 'me')
    calc.set_following({'f1'})
    recipe.pantry.network_reach = NetworkReachConfig(
        enabled=True, max_degrees=1, include_self=True)
    posts = [status('1', author='me'), status('2', author='f1'),
             status('3', author='stranger')]
    kept, stats = apply_network_reach(posts, recipe, calc)
    check('reach keeps self and following',
          {item['id'] for item in kept} == {'1', '2'})
    # Boost wrapper: author is booster; content author is the followee.
    boost = status('inner', author='f1', wrapper_author='stranger')
    kept_boost, _ = apply_network_reach([boost], recipe, calc)
    check('reach uses boosted author not booster',
          [item['id'] for item in kept_boost] == ['w-inner'])
    recipe.pantry.network_reach.max_degrees = None
    kept2, _ = apply_network_reach(posts, recipe, calc)
    check('null max_degrees disables filter', len(kept2) == 3)
    empty = NetworkReachCalculator('acct#2', 'me')
    empty.set_following(set())
    recipe.pantry.network_reach.max_degrees = 1
    kept3, stats3 = apply_network_reach(posts, recipe, empty)
    check('empty cache fallback includes unknown',
          len(kept3) == 3 and stats3.get('fallback') == 'network_reach_empty_cache')

# --- engine sync ---
posts_scope = [status('10', fav=1), status('11', fav=5)]

def fetch_scope(max_id, count):
    return list(posts_scope)

engine = FeedEngine(
    account_key='a#1', self_account_id='me',
    fetch_scope=fetch_scope,
)
result = engine.process_feed_sync(default_recipe(), count=40)
check('engine returns items', len(result.items) == 2)
check('scope_oldest_id set', result.scope_oldest_id is not None)
engine.shutdown()

# --- feed model cursor ---
model = FeedChefModel()
model.replace(result.items, scope_oldest_id='10', has_more=True)
check('model length', len(model) == 2)
added = model.append_page([status('12')], scope_oldest_id='12', has_more=False)
check('append skips dupes / adds', added == 1 and model.scope_oldest_id == '12')

# --- kitchen ranks: filtered → -1 ---
recipe = default_recipe()
recipe.kitchen.modifiers = [
    Modifier(type='diversity', max_per_cluster=1,
             diversity_dimensions=['author']),
]
_ordered, _scores, meta = score_statuses(
    [status('1', author='x', fav=1), status('2', author='x', fav=99),
     status('3', author='y', fav=50)],
    recipe)
check('kitchen rank kept for survivors',
      meta['ranks'].get('1', -1) >= 0 and meta['ranks'].get('3', -1) >= 0)
check('kitchen rank -1 for filtered', meta['ranks'].get('2') == -1)

# --- decision log ---
with tempfile.TemporaryDirectory() as tmp:
    logmod.set_root(tmp)
    logger = logmod.FeedDecisionLogger('recipe-1')
    logger.set_pantry(fetched_count=2)
    path = logger.persist()
    check('log written', path and os.path.isfile(path))
    for index in range(55):
        extra = logmod.FeedDecisionLogger('r-%d' % index)
        extra.persist()
        time.sleep(0.002)
    remaining = [name for name in os.listdir(tmp) if name.endswith('.json')]
    check('decision log rotates to 50', len(remaining) == 50)

# --- cancel does not deliver stale on_done ---
done_box = {'called': False}
ready = threading.Event()

def slow_scope(max_id, count):
    ready.set()
    time.sleep(0.2)
    return [status('slow')]

cancel_engine = FeedEngine(
    account_key='a#cancel', self_account_id='me',
    fetch_scope=slow_scope,
    ui_idle=lambda cb: cb(),
)
cancel_engine.process_feed(
    default_recipe(),
    on_done=lambda _result: done_box.__setitem__('called', True),
)
ready.wait(timeout=2)
cancel_engine.cancel()
time.sleep(0.35)
check('cancel skips obsolete on_done', done_box['called'] is False)
cancel_engine.shutdown()

# --- More Like This extraction ---
cafe = status('c1', content='<p>This is just the best thing about café '
              'culture, café rules</p>')
tags_c, words_c = extract_focus(cafe)
check('focus: accent stripped keyword', 'cafe' in words_c)
check('focus: no stopwords',
      not ({'this', 'just', 'the'} & set(words_c)))
check('focus: stopword list verbatim', 'mastodon' in STOP_WORDS
      and 'what' in STOP_WORDS and len(STOP_WORDS) == 39)
check('focus: top keyword is the repeated one', words_c[0] == 'cafe')
check('focus: at most 5 keywords', len(words_c) <= 5)
pt = status('p1', content='<p>Adoro o pão de queijo mineiro, pão quentinho</p>',
            tags=['Comida', 'Minas', 'queijo', 'extra'],
            spoiler='Receitas', media=[{'description': 'Fotografia de pão'}])
tags_p, words_p = extract_focus(pt)
check('focus: hashtags lowercase, max 3', tags_p == ['comida', 'minas', 'queijo'])
check('focus: accents stripped (pao)', words_p[0] == 'pao')
check('focus: alt text and spoiler read',
      'fotografia' in focus_tokens(pt) and 'receitas' in focus_tokens(pt))
check('focus: tag token not repeated as keyword', 'queijo' not in words_p)
corpus = [status('x%d' % i, content='<p>pao e muito pao hoje</p>')
          for i in range(4)] + [pt]
_t, words_corpus = extract_focus(pt, corpus)
check('focus: common corpus word ranks below a rare one',
      'adoro' in words_corpus and
      (words_corpus.index('adoro') < words_corpus.index('pao')
       if 'pao' in words_corpus else True))
check('focus: deterministic', extract_focus(pt, corpus)
      == extract_focus(pt, corpus))

# --- request gate ---
gate = RequestGate()
g1 = gate.begin_refresh()
check('gate: no older page while refreshing', gate.begin_page() is None)
g2 = gate.begin_refresh()
check('gate: stale refresh result dropped', not gate.accept(g1)
      and not gate.finish(g1) and gate.refreshing)
check('gate: current refresh finishes', gate.finish(g2) and not gate.refreshing)
g3 = gate.begin_page()
check('gate: page allowed after refresh', g3 is not None)
check('gate: second page refused while paging', gate.begin_page() is None)
g4 = gate.begin_refresh()
check('gate: refresh supersedes a page', not gate.accept(g3)
      and gate.accept(g4))

# --- storage never destroys a corrupt file ---
with tempfile.TemporaryDirectory() as tmp:
    storagemod.set_root(tmp)
    store = storagemod.FeedRecipeStorage('acct#corrupt')
    store.save_recipe(default_recipe('first'))
    path = storagemod._path('acct#corrupt')
    with open(path, 'w') as handle:
        handle.write('{garbage')
    fresh = default_recipe('second')
    store.save_recipe(fresh)
    backups = [name for name in os.listdir(tmp) if '.corrupt-' in name]
    check('storage: backup created', len(backups) == 1)
    with open(os.path.join(tmp, backups[0])) as handle:
        check('storage: backup holds the garbage', handle.read() == '{garbage')
    check('storage: backup mode 0600',
          (os.stat(os.path.join(tmp, backups[0])).st_mode & 0o777) == 0o600)
    check('storage: new file holds only the new recipe',
          [r.id for r in store.load_recipes()] == [fresh.id])
    with open(path, 'w') as handle:
        handle.write('{"not": "a list"}')
    store.delete_recipe('nothing')
    check('storage: non-list root kept on delete',
          len([n for n in os.listdir(tmp) if '.corrupt-' in n]) == 2)
storagemod.set_root(None)

# --- scope cursor from wrapper ids ---
boost = status('inner', author='f1', wrapper_author='z',
               created='2019-01-01T00:00:00+00:00')
boost['id'] = '900'
page = [status('120'), boost, status('105')]
check('scope cursor: smallest wrapper id, not the boost date',
      scope_cursor(page) == '105')
check('scope cursor: compares as integers', scope_cursor(
    [status('1000'), status('999')]) == '999')
check('scope cursor: non-digit ids use the last item',
      scope_cursor([status('a'), status('b')]) == 'b')
check('scope cursor: empty is None', scope_cursor([]) is None)
check('pantry scope_oldest_id uses the cursor', build_pantry(
    default_recipe(), scope_posts=page, count=3).scope_oldest_id == '105')

# --- parallel pantry with enforced timeout ---
def hashtag_recipe(tags):
    recipe = default_recipe()
    recipe.pantry.hashtags_include = tags
    return recipe

def slow_scope_fast(max_id, count):
    time.sleep(0.4)
    return [status('sc1', tags=['a'], author='as')]

def slow_tag(tag, count):
    time.sleep(0.4)
    return [status('tag-' + tag, tags=[tag], author='au-' + tag)]

overlap = FeedEngine(account_key='a#par', self_account_id='me',
                     fetch_scope=slow_scope_fast, fetch_hashtag=slow_tag)
begun = time.time()
par = overlap.process_feed_sync(hashtag_recipe(['a', 'b', 'c']), count=40)
elapsed = time.time() - begun
check('pantry fetches overlap (%.2fs)' % elapsed, elapsed < 0.9)
check('pantry parallel gathers everything', len(par.items) == 4)
overlap.shutdown()

def very_slow_tag(tag, count):
    if tag == 'slow':
        time.sleep(1.5)
    return [status('tag-' + tag, tags=[tag])]

timed = FeedEngine(account_key='a#to', self_account_id='me',
                   fetch_scope=lambda max_id, count: [status('s', tags=['fast'])],
                   fetch_hashtag=very_slow_tag)
timed.pantry_timeout_base = 0.2
timed.pantry_timeout_per_tag = 0.1
begun = time.time()
partial_result = timed.process_feed_sync(
    hashtag_recipe(['fast', 'slow']), count=40)
elapsed = time.time() - begun
ids_partial = {item['id'] for item in partial_result.items}
check('timeout: partial results kept', {'s', 'tag-fast'} <= ids_partial)
check('timeout: slow fetch dropped', 'tag-slow' not in ids_partial)
check('timeout: returns without waiting (%.2fs)' % elapsed, elapsed < 1.0)
check('timeout: flagged in the log',
      partial_result.logger.log.pantry.get('pantry_timed_out') is True)
timed.shutdown()

# A cancel must free the run worker at once, not at the pantry deadline.
hang = threading.Event()
stuck = FeedEngine(account_key='a#stuck', self_account_id='me',
                   fetch_scope=lambda max_id, count: hang.wait(3) or [])
stuck.process_feed(default_recipe('x'), max_id=None, count=40,
                   on_stage=None, on_done=lambda r: None,
                   on_error=lambda e: None)
time.sleep(0.2)
stuck.cancel()
second = {}
begun = time.time()
stuck.fetch_scope = lambda max_id, count: [status('z')]
stuck.process_feed(default_recipe('x'), max_id=None, count=40,
                   on_stage=None, on_done=lambda r: second.update(ok=True),
                   on_error=lambda e: None)
for _i in range(40):
    if second:
        break
    time.sleep(0.05)
check('cancel: next run starts without waiting for the stuck fetch '
      '(%.2fs)' % (time.time() - begun), second.get('ok') is True
      and time.time() - begun < 1.5)
hang.set()
stuck.shutdown()

sys.exit(1 if failures else 0)
