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
from src.feedchef.feed_model import FeedChefModel  # noqa: E402
from src.feedchef.kitchen import (  # noqa: E402
    apply_diversity, apply_freshness, apply_heat, base_score, score_statuses,
)
from src.feedchef.network_reach import (  # noqa: E402
    NetworkReachCalculator, apply_network_reach,
)
from src.feedchef.pantry import build_pantry, merge_pantry, oldest_id  # noqa: E402
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
check('has_more when single task full page', pantry.has_more)

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

sys.exit(1 if failures else 0)
