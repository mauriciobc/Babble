#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's quality metrics and advice, ported from moshidon: per-stage
metrics from audit records (checked against moshidon's own example numbers),
the bands and the recipe score, recipe health and its 10-cook alert, and the
recommendations (rule, quality, analyzer and comparison) with their apply and
undo and the things they never suggest. No display, no network."""

import copy
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from src.feedchef import advice, health  # noqa: E402
from src.feedchef import quality, recipeedit  # noqa: E402
from src.feedchef import audit as auditmod  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef.recipe import (Modifier, recipe_to_dict,  # noqa: E402
                                 validate_for_import)

# What the quality layer needs is pure: nothing here may have loaded numpy
# (the clustering worker's, not the metrics').
check_modules = {'numpy': 'numpy' in sys.modules}
from feedchefaudit import fuzz  # noqa: E402
from feedchefaudit.qualitykit import (MARKED, by_id, heats,  # noqa: E402
                                      metrics, plain, stored)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


TMP = tempfile.mkdtemp(prefix='feedchef-quality-')
reachmod.set_root(os.path.join(TMP, 'reach'))


def fresh_logs(name):
    root = os.path.join(TMP, name)
    logmod.set_root(root)
    return root


import ast  # noqa: E402
imports = set()
for name in ('advice', 'health', 'optimizer', 'quality', 'recipeedit',
             'templates'):
    path = os.path.join(srcdir, 'feedchef', name + '.py')
    with open(path, encoding='utf-8') as handle:
        for node in ast.walk(ast.parse(handle.read())):
            if isinstance(node, ast.Import):
                imports.update(item.name.split('.')[0] for item in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                imports.add((node.module or '').split('.')[0])
check('the quality modules import the standard library and the package, '
      'no GTK and no numpy (they run on a worker and need neither); '
      'importing them loaded no numpy',
      not imports & {'gi', 'numpy', 'kmedoids', 'py3langid', 'segno'}
      and not check_modules['numpy'])

# --- moshidon's own example (metrics_with_quality.json, recipe "Pets") -----

# The kitchen stage of that file: 47 toots, all valid, mean quality
# 14.319148936170214, mean relevance 0.4855957446808511 -> score 0.572.
KITCHEN = {'total_toots': 47, 'validity_rate': 100,
           'avg_quality_score': 14.319148936170214,
           'avg_relevance_score': 0.4855957446808511}
check('the recipe score of moshidon\'s kitchen numbers is its 0.572 '
      '(validity 0.4, engagement 0.35, relevance 0.25)',
      quality.recipe_score(KITCHEN) == 0.572
      and quality.WEIGHTS == (0.4, 0.35, 0.25))
check('the pantry numbers of that file (78 toots, 9.0705 and 0.45423) give '
      '0.545 by the same formula; no toots score 0',
      quality.recipe_score({'total_toots': 78, 'validity_rate': 100,
                            'avg_quality_score': 9.070512820512821,
                            'avg_relevance_score': 0.45423076923076927})
      == round(0.4 + 0.35 * 0.09070512820512821
               + 0.25 * 0.45423076923076927, 3)
      and quality.recipe_score({'total_toots': 0}) == 0.0)
# Its relevance percentiles are validity x 0.4 + quality/100 x 0.6 of its
# quality percentiles (p25 2, p50 4, p75 13, p90 20, p95 32).
valid = {'rules': {}, 'facts': {}}
mine = [round(0.4 * 1.0 + min(q / 100.0, 1.0) * 0.6, 3)
        for q in (2, 4, 13, 20, 32)]
check('the relevance of a valid post with quality 2, 4, 13, 20 and 32 is '
      'moshidon\'s 0.412, 0.424, 0.478, 0.52 and 0.592',
      mine == [0.412, 0.424, 0.478, 0.52, 0.592])
for q in (2, 4, 13, 20, 32):
    one = quality.assess_entry(
        {'rules': {}, 'facts': {'fav': q}}, [], None)
    check('quality %d through assess_entry: relevance %.3f, valid, '
          'validation 1.0' % (q, one['relevance_score']),
          abs(one['relevance_score'] - (0.4 + 0.006 * q)) < 1e-12
          and one['is_valid'] and one['validation_score'] == 1.0
          and one['quality_score'] == q)

# moshidon's percentile is the sorted value at index int(p x n / 100).
def moshidon_percentile(scores, percent):
    ordered = sorted(scores)
    index = int((percent / 100.0) * len(ordered))
    return ordered[min(index, len(ordered) - 1)]


rng = random.Random(7)
agree = True
for _ in range(200):
    scores = [rng.choice([0, 0.5, 1, 2.5, 4, 9, 30, 102.5])
              for _i in range(rng.randint(1, 90))]
    found = quality.percentiles(scores)
    agree = agree and all(found['p%d' % p] == moshidon_percentile(scores, p)
                          for p in (25, 50, 75, 90, 95))
check('the percentiles P25 to P95 are moshidon\'s on 200 random lists', agree)
check('20 scores: P25 6, P50 11, P75 16, P90 19, P95 20 (index int(p n / '
      '100)), and none: nothing',
      quality.percentiles(list(range(1, 21))) == {
          'p25': 6, 'p50': 11, 'p75': 16, 'p90': 19, 'p95': 20}
      and quality.percentiles([]) == {})
# The bands: under 25, 50 and 75 percent of the best score.
check('the bands cut at 25, 50 and 75 percent of the best score, each '
      'boundary in the band above',
      quality.distribution([0, 24.9, 25, 49.9, 50, 74.9, 75, 100])
      == {'low': 2, 'medium': 2, 'high': 2, 'very_high': 2}
      and quality.distribution([100, 25]) == {
          'low': 0, 'medium': 1, 'high': 0, 'very_high': 1})
check('scores that are all zero are all Low; none is empty',
      quality.distribution([0, 0, 0]) == {
          'low': 3, 'medium': 0, 'high': 0, 'very_high': 0}
      and quality.distribution([]) == {})
check('a recipe score is excellent from 0.7, good from 0.5, moderate from '
      '0.3, else poor',
      [quality.state_of(s) for s in (0.7, 0.699, 0.5, 0.499, 0.3, 0.299)]
      == ['excellent', 'good', 'good', 'moderate', 'moderate', 'poor'])
check('the empty aggregate is moshidon\'s (zeros, nothing to measure) and '
      'every aggregate says validity is enforced',
      quality.aggregate([])['total_toots'] == 0
      and quality.aggregate([])['validity_rate'] == 0.0
      and quality.aggregate([quality.assess_entry(
          {'rules': {}, 'facts': {}}, [], None)])['caveat']
      == 'validity_enforced')
sample = quality.aggregate([quality.assess_entry(
    {'rules': {'hashtags_include': 'fail'}, 'facts': {'fav': 10}}, [], None),
    quality.assess_entry({'rules': {'must_have_alt_text': 'fail',
                                    'hashtags_exclude': 'fail'},
                          'facts': {'fav': 10}}, [], None),
    quality.assess_entry({'rules': {'keyword_blocklist': 'pass'},
                          'facts': {'fav': 40}}, [], None)])
check('validation: a failed pantry rule halves it and a failed diet rule '
      'zeroes it (moshidon), the reasons are ids, validity is 1 of 3',
      sample['valid_toots'] == 1 and abs(sample['validity_rate']
                                         - 100 / 3) < 1e-9
      and sample['rejection_reasons'] == {
          'pantry:hashtags_include': 1, 'pantry:hashtags_exclude': 1,
          'diet:must_have_alt_text': 1}
      and quality.assess_entry({'rules': {'hashtags_include': 'fail'},
                                'facts': {}}, [], None
                               )['validation_score'] == 0.5
      and quality.assess_entry({'rules': {'hashtags_include': 'fail',
                                          'positive_only': 'fail'},
                                'facts': {}}, [], None
                               )['validation_score'] == 0.0)
check('the caveat is said in the words of the reader, and the bands have '
      'names',
      'engaged' in quality.caveat_text()
      and quality.band_names()['very_high'] == 'Very High')


# --- the stages, from the records the engine writes ---------------------------

fresh_logs('stages')
records = []
for case in range(60):
    rng = random.Random(3000 + case)
    recipe = fuzz.random_recipe(rng)
    world = fuzz.World(rng, recipe)
    for result in fuzz.cook(world, recipe, case, pages=2, now=fuzz.NOW):
        records.append(result.logger.record)
shown = [r for r in records if r['outcome']['status'] in auditmod.SHOWN]
check('the fuzz wrote records to measure (%d pages)' % len(shown),
      len(shown) >= 60)
funnel = enforced = pantry_invalid = ordered_ok = base_ok = 0
fades = 0
for record in shown:
    found = quality.assess_pages([record])
    p, d, k = found['pantry'], found['diet'], found['kitchen']
    funnel += (p['total_toots'] >= d['total_toots'] >= k['total_toots']
               and k['total_toots'] == len(record['outputs']['order']))
    # Every filter is enforced: what the diet lets on and what is shown are
    # valid by construction; the pantry stage shows what the diet then cut.
    enforced += (d['validity_rate'] in (100, 0.0) and k['validity_rate']
                 in (100, 0.0) and (d['total_toots'] == 0
                                    or d['validity_rate'] == 100)
                 and (k['total_toots'] == 0 or k['validity_rate'] == 100))
    pantry_invalid += p['validity_rate'] < 100 and p['total_toots'] > 0
    scores = [r for r in (found['kitchen']['avg_relevance_score'],
                          found['recipe_quality_score']) if not 0 <= r <= 1]
    ordered_ok += not scores and found['caveat'] == 'validity_enforced'
    # The engagement the stage reads is the one the kitchen scored with
    # (and its freshness, when no floor changed it).
    mods = [m for m in record['recipe']['snapshot']['kitchen']['modifiers']
            if m['type'] == 'freshness']
    snapshot_has_trending = any(
        m['type'] == 'trending'
        for m in record['recipe']['snapshot']['kitchen']['modifiers'])
    entries = [e for e in record['posts'] if e['score']
               and e['removal'] is None]
    if not record.get('fresh_fallback') and not snapshot_has_trending:
        now = datetime.fromisoformat(record['clock']['now_utc'])
        decays = quality.fresh_mods(record['recipe']['snapshot'])
        fades += bool(decays)
        base_ok += all(
            abs(quality.assess_entry(e, decays, now)['quality_score']
                - e['score']['base'] * e['score']['fresh']) < 1e-9
            for e in entries)
    else:
        base_ok += 1
check('the funnel narrows pantry >= diet >= kitchen, and the kitchen stage '
      'is the page shown (%d of %d)' % (funnel, len(shown)),
      funnel == len(shown))
check('validity is 100%% at the diet and kitchen stages by construction '
      '(%d of %d), and the pantry stage shows what the diet cut (%d pages '
      'under 100%%)' % (enforced, len(shown), pantry_invalid),
      enforced == len(shown) and pantry_invalid > 5)
check('every score and the caveat are in range and present (%d)'
      % ordered_ok, ordered_ok == len(shown))
check('the quality of a post is what the kitchen scored it (engagement x '
      'freshness) on %d pages, %d with a fade-out' % (base_ok, fades),
      base_ok == len(shown) and fades > 5)
cooks = {}
for record in shown:
    cooks.setdefault((record['recipe']['hash'], record['cook_id']),
                     []).append(record)
pooled = quality.assess_pages(next(iter(cooks.values())))
check('a cook of two pages pools their posts, and a peek or a failed page '
      'is not a page of it',
      pooled['pages'] == len(next(iter(cooks.values())))
      and quality.assess_pages([{**shown[0], 'outcome': {
          'status': 'peek', 'error': ''}}])['pages'] == 0
      and quality.assess_pages([])['recipe_quality_score'] == 0.0)
summary = quality.cook_summary(next(iter(cooks.values())))
check('a cook summary has numbers and flags only',
      set(summary) == {'score', 'state', 'posts', 'pages', 'avg_quality',
                       'low_pct', 'zero_scores', 'fresh_fallback',
                       'heat_stepped'}
      and all(isinstance(v, (int, float, bool, str))
              for v in summary.values()))
check('the cooks of a list of records are found by recipe and cook id',
      len(quality.group_cooks(shown)) == len(cooks)
      and len(quality.summaries(shown)) <= len(cooks))


# --- health ---------------------------------------------------------------------

def made(score, posts=20, zero=False, low=40.0):
    return {'score': score, 'state': quality.state_of(score), 'posts': posts,
            'pages': 1, 'avg_quality': 0.0 if zero else 9.0, 'low_pct': low,
            'zero_scores': zero, 'fresh_fallback': False,
            'heat_stepped': False}


check('no cook yet: unknown, no alert',
      health.assess([]) == health.Health('unknown', (), None, 0, False))
good = [made(0.62) for _i in range(5)]
check('a recipe that does fine is Working Well, with no reasons',
      health.assess(good).state == 'working_well'
      and health.assess(good).reasons == ()
      and health.state_text('working_well') == 'Working Well')
check('the score is judged from 3 cooks on: 2 poor cooks are not a trend',
      health.assess([made(0.2), made(0.2)]).state == 'working_well'
      and health.assess([made(0.2)] * 3).reasons == ('low_score',)
      and health.assess([made(0.2)] * 3).state == 'needs_attention')
check('the latest cook speaks for itself: few posts, zero scores, mostly '
      'Low',
      health.assess(good + [made(0.62, posts=4)]).reasons == ('few_posts',)
      and health.assess(good + [made(0.62, zero=True)]).reasons
      == ('zero_scores',)
      and health.assess(good + [made(0.62, low=86.0)]).reasons
      == ('mostly_low',)
      and health.assess(good + [made(0.62, low=85.0)]).reasons == ())
check('the alert: exactly the last 10 cooks, mean under 0.5',
      not health.assess([made(0.1)] * 9).alert
      and health.assess([made(0.1)] * 10).alert
      and not health.assess([made(0.5)] * 10).alert
      and health.assess([made(0.9)] * 5 + [made(0.1)] * 5).alert is False
      and health.assess([made(0.9)] * 20 + [made(0.1)] * 10).alert is True
      and health.assess([made(0.1)] * 20 + [made(0.9)] * 10).alert is False
      and health.assess([made(0.1)] * 3 + [made(0.55)] * 7).alert is True)
check('only the last 10 cooks count',
      abs(health.mean_score([made(0.0)] * 5 + [made(0.8)] * 10) - 0.8) < 1e-9
      and health.mean_score([]) is None)
words = MARKED
check('the states and reasons are translatable text',
      health.state_text('needs_attention', words) == '<Needs Attention>'
      and all(health.reason_text(r, words).startswith('<')
              for r in health.REASONS))

# --- advice ----------------------------------------------------------------------

# Quality: more than 80% Low -> a hotter step, a fade-out, hashtags.
found = advice.recommend(plain(), metrics(low=90, score=0.35))
ids = by_id(found)
check('most posts Low: Hot (moshidon 75), a 6-hour fade-out (360) and '
      'hashtags to choose; the most severe come first',
      ids['heat_up'].suggested == 75 and ids['heat_up'].current == 0
      and ids['fade_add'].suggested == 360
      and ids['hashtags_add'].manual and not ids['hashtags_add'].edits
      and found[0].severity == 'high'
      and [r.severity for r in found] == sorted(
          [r.severity for r in found], key=advice.SEVERITIES.index))
check('every impact is labelled an estimate, in the words and in the data',
      all(r.impact_kind == 'estimate' for r in found if r.impact)
      and all(advice.describe(r).impact.startswith('Estimate: ')
              for r in found if r.impact))
check('moshidon\'s 60-80% Low suggests 60, which is the Warm step (50); a '
      'low share of High posts suggests 85, which is Fire (90)',
      heats(advice.recommend(plain(), metrics(low=70))) == [50]
      and heats(advice.recommend(plain(), metrics(
          total=60, low=30, high=0))) == [90]
      and heats(advice.recommend(plain(), metrics(
          total=30, low=30, high=0))) == []
      and heats(advice.recommend(plain(), metrics(
          total=10, low=30, high=0))) == [])
check('a step the page would fall back from is not suggested (6 posts '
      'entering: Hot leaves 2, so the engine would use Warm)',
      'heat_up' not in by_id(advice.recommend(
          plain(), metrics(total=6, low=90, entering=6))))
check('the heat only moves the way the reason says, and never to where it '
      'is: a recipe already at Hot is not told to go to Hot (Fire, the '
      'analyzer\'s 20 more, is)',
      heats(advice.recommend(plain(mods=[Modifier(
          type='heat', percentile_threshold=75)]), metrics(low=90)))
      == [90])
# Scores near zero: with a fade-out, it is the fade-out (and the order).
fading = plain(mods=[Modifier(type='freshness', decay_minutes=300)])
found = by_id(advice.recommend(fading, metrics(avg=1, median=1)))
check('very low scores with a fade-out: a longer one (doubled) and newest '
      'first, never hashtags or a hotter step for it',
      found['fade_longer'].suggested == 600
      and found['sort_newest'].suggested == 'chronological'
      and 'heat_up' not in found and 'hashtags_add' in found)
found = by_id(advice.recommend(
    plain(mods=[Modifier(type='freshness', decay_minutes=1000)]),
    metrics(avg=0, median=0)))
check('every score zero with a fade-out: doubled (and capped at a day), '
      'removed, or newest first',
      found['fade_longer'].suggested == 1440
      and found['fade_remove'].suggested is None
      and found['sort_newest'].suggested == 'chronological'
      and 'fade_longer' not in by_id(advice.recommend(
          plain(mods=[Modifier(type='freshness', decay_minutes=1440)]),
          metrics(avg=0, median=0))))
found = by_id(advice.recommend(plain(), metrics(avg=1, median=1)))
check('very low scores without a fade-out: a hotter step, hashtags, the '
      'Home source; low scores alone suggest a 12-hour fade-out',
      found['heat_up'].suggested == 75 and found['scope_home'].suggested
      == 'home' and 'fade_add' in by_id(advice.recommend(
          plain(), metrics(avg=8, median=8)))
      and by_id(advice.recommend(plain(), metrics(avg=8, median=8))
                )['fade_add'].suggested == 720
      and 'scope_home' not in by_id(advice.recommend(
          plain(scope='list'), metrics(avg=1, median=1))))
check('a low recipe score is a thing to review, not a change',
      by_id(advice.recommend(plain(), metrics(score=0.2)))[
          'review_criteria'].manual
      and 'review_criteria' not in by_id(advice.recommend(
          plain(), metrics(score=0.5))))
check('a heat that does not pay, and one that shows too few',
      by_id(advice.recommend(plain(mods=[Modifier(
          type='heat', percentile_threshold=50)]), metrics(
              total=20, high=10)))['heat_up'].suggested == 75
      and by_id(advice.recommend(plain(mods=[Modifier(
          type='heat', percentile_threshold=95)]), metrics(total=8)))[
          'heat_down'].suggested == 75)
check('an unmet rule (validity under 90) points at what to review',
      {r.field for r in advice.recommend(plain(), metrics(
          validity=70, reasons={'diet:must_have_alt_text': 5,
                                'pantry:hashtags_include': 2}))
       if r.id.startswith('review_')} >= {'hard.must_have_alt_text',
                                          'hashtags_include'})
# The analyzer: 20 more, to 90 at most.
check('the analyzer steps the heat up by 20 (to 90 at most)',
      by_id(advice.recommend(plain(mods=[Modifier(
          type='heat', percentile_threshold=50)]), metrics(low=90)))[
          'heat_up'].suggested == 75
      and advice.analyzer(advice.context(plain(mods=[Modifier(
          type='heat', percentile_threshold=75)]), metrics(low=90)))[
          0].suggested == 90)
# Rule based.
check('moshidon\'s complexity (1 + scope + 0.2 per tag + 0.1 per excluded) '
      'and aggressiveness (0.3 + 0.4 x heat + the fade-out) numbers',
      advice.complexity(plain(tags=list('abcdef'))) == 2.7
      and advice.complexity(plain(scope='home')) == 1.0
      and advice.complexity(plain(scope='local', tags=['a'])) == 1.5
      and advice.aggressiveness(plain(mods=[
          Modifier(type='heat', percentile_threshold=90),
          Modifier(type='freshness', decay_minutes=60)])) == 0.91
      and advice.aggressiveness(plain(sort='chronological')) == 0.0)
found = by_id(advice.recommend(plain(tags=list('abcdef')), None))
check('with no cook yet the settings are judged: six hashtags, of which '
      'the engine searches three; a bare federated recipe wants hashtags',
      found['hashtags_fewer'].suggested == list('abc')
      and 'hashtags_add' not in found
      and by_id(advice.recommend(plain(), None))['hashtags_add'].manual)
found = by_id(advice.recommend(plain(mods=[
    Modifier(type='heat', percentile_threshold=90),
    Modifier(type='freshness', decay_minutes=60)]), None))
check('too aggressive: a 6-hour fade-out and a gentler heat; and the '
      '"too conservative" rule is not made for a newest-first recipe',
      found['fade_longer'].suggested == 360
      and found['heat_down'].suggested == 75
      and advice.recommend(plain(sort='chronological', scope='home'),
                           None) == [])
found = by_id(advice.recommend(
    plain(scope='home', mods=[]), metrics(pantry=100, entering=20,
                                          total=10)))
check('the diet removing most posts (80 of 100): a hint to relax filters '
      'and, with a long minimum, a shorter one',
      found['filters_relax'].manual and found['filters_relax'
                                              ].severity == 'high')
longer = plain(scope='home')
longer.diet.hard_filters.min_content_length = 60
found = by_id(advice.recommend(longer, metrics(pantry=100, entering=40)))
check('and a 60% rate is medium, with a minimum of 60 -> 50',
      found['length_down'].suggested == 50
      and found['length_down'].severity == 'medium')
strict = plain(scope='home')
strict.diet.hard_filters.must_have_alt_text = True
strict.diet.keyword_blocklist = list('abcde')
check('what would loosen what the reader asked for is not suggested: alt '
      'text is never switched off, blocked words are never cut',
      not any(r.field in ('hard.must_have_alt_text', 'keyword_blocklist')
              and not r.manual for r in advice.recommend(
                  strict, metrics(pantry=100, entering=20, low=90))))
focus = plain(scope='home')
focus.kitchen.active_focus_keywords = ['cats']
check('a focus (More Like This) is never replaced by a newest-first order',
      'sort_newest' not in by_id(advice.recommend(
          focus, metrics(avg=0, median=0, low=90))))
# Comparison.
hot = lambda: plain(mods=[Modifier(type='heat', percentile_threshold=75)])  # noqa: E731
others = [hot(), hot(), hot()]
for index, other in enumerate(others):
    other.id = 'other%d' % index
mine = plain()
mine.id = 'mine'
found = by_id(advice.recommend(mine, metrics(), others))
check('similar recipes that hide the least engaging suggest it (Warm, '
      'moshidon\'s 50), and 2 of 3 that fade older posts suggest a fade-out',
      found['heat_up'].suggested == 50 and found['heat_up'].source
      == 'comparison'
      and by_id(advice.recommend(mine, metrics(), [
          plain(mods=[Modifier(type='freshness', decay_minutes=60)])
          for _i in range(2)] + [plain()]))['fade_add'].suggested == 360)
check('similarity is 1 / (1 + the euclidean distance) over moshidon\'s '
      'five features, and a recipe is not its own neighbour',
      advice.similarity(mine, mine) == 1.0
      and abs(advice.similarity(plain(tags=['a']), plain())
              - 0.5) < 1e-12
      and abs(advice.similarity(plain(scope='home'), plain())
              - 1 / 3) < 1e-12
      and mine not in [r for r, _s in advice.most_similar(
          mine, [mine, *others])]
      and len(advice.most_similar(mine, others * 3, top=3)) == 3)
check('a very low score points at similar recipes',
      by_id(advice.recommend(mine, metrics(score=0.2), others))[
          'review_similar'].manual)


# --- applying a recommendation, and undoing it ---------------------------------------

rng = random.Random(11)
applied_n = manual_n = steps_ok = 0
exact = valid_ok = changed = untouched = engine_ok = True
seen_fields = set()
for _case in range(240):
    recipe = fuzz.random_recipe(rng)
    recipe.pantry.scope = rng.choice(['home', 'local', 'federated', 'list'])
    total = rng.choice([8, 20, 40, 80])
    sample = metrics(total=total, low=rng.choice([0, 30, 65, 90]),
                     high=rng.choice([0, 5, 20]),
                     avg=rng.choice([0, 1, 4, 12, 40]),
                     median=rng.choice([0, 2, 9]),
                     validity=rng.choice([100, 70]),
                     pantry=total * rng.choice([1, 2, 5]),
                     entering=total, score=rng.choice([0.2, 0.4, 0.7]),
                     reasons={'diet:must_have_alt_text': 1})
    before = stored(recipe)
    for rec in advice.recommend(recipe, sample, [fuzz.random_recipe(rng)
                                                 for _i in range(3)]):
        seen_fields.add(rec.field)
        if rec.manual:
            manual_n += 1
            try:
                rec.apply(recipe)
                exact = False
            except ValueError:
                pass
            continue
        applied_n += 1
        done = rec.apply(recipe)
        try:
            validate_for_import(recipe_to_dict(done.recipe))
        except ValueError:
            valid_ok = False
        changed = changed and stored(done.recipe) != before
        untouched = untouched and stored(recipe) == before
        if rec.field != 'hashtags_include':
            engine_ok = engine_ok and (recipeedit.current(
                done.recipe, rec.field) == rec.suggested)
        if rec.field == 'heat':
            steps_ok += rec.suggested in (0, 50, 75, 90)
        back = recipeedit.restore(done.recipe, done.undo)
        exact = exact and stored(back) == before
        # Nothing the engine cannot do: a heat the page keeps 5 posts at,
        # a fade-out within a day, a sort that is not a focus's.
        if rec.field == 'freshness':
            engine_ok = engine_ok and (rec.suggested is None
                                       or 0 < rec.suggested <= 1440)
        if rec.field == 'base_sort':
            engine_ok = engine_ok and not (
                recipe.kitchen.active_focus_keywords
                or recipe.kitchen.active_focus_hashtags)
check('%d recommendations applied to a copy (%d to choose): each leaves '
      'the original alone, changes the recipe and leaves one the import '
      'accepts' % (applied_n, manual_n),
      applied_n > 150 and manual_n > 20 and untouched and changed
      and valid_ok)
check('undo gives back the stored recipe exactly, field for field, for '
      'every one', exact)
check('every one sets the field it names to the value it names, every '
      'heat is one of Cool, Warm, Hot and Fire, nothing is a fade-out over '
      'a day, none replaces a focus (fields: %s)' % ', '.join(
          sorted(seen_fields)), engine_ok and steps_ok > 20
      and {'heat', 'freshness', 'base_sort', 'scope',
           'hashtags_include'} <= seen_fields)
check('a recommendation is data: ids and numbers, a list of hashtags '
      'only as it was',
      all(set(rec.to_json()) == {
          'id', 'source', 'kind', 'severity', 'field', 'current',
          'suggested', 'reason', 'impact', 'impact_kind', 'manual'}
          for rec in advice.recommend(plain(), metrics(low=90))))
words = MARKED
check('the words of every recommendation go through the caller\'s '
      'translation',
      all(advice.describe(rec, words).title.startswith('<')
          and advice.describe(rec, words).reason.startswith('<')
          and (rec.manual or advice.describe(rec, words).change)
          for rec in advice.recommend(plain(), metrics(low=90, avg=1,
                                                       median=1))))
check('a value is shown as the builder shows it (Warm to Hot, 6 hours, '
      'Custom, newest first, hashtags)',
      advice.value_text('heat', 50) == 'Warm'
      and advice.value_text('heat', 60) == 'Custom (60)'
      and advice.value_text('freshness', 360) == '6 hours'
      and advice.value_text('freshness', 60) == '1 hour'
      and advice.value_text('base_sort', 'chronological') == 'Newest first'
      and advice.value_text('hashtags_include', ['a', 'b']) == '#a #b'
      and advice.value_text('heat', None) == 'Off')


# --- per page, not per cook; counts that are not numbers; clocks; order ---------

def paged(**kwargs):
    sizes = kwargs.pop('sizes')
    found = metrics(**kwargs)
    found['page_sizes'] = sizes
    return found


check('moshidon\'s absolute thresholds are judged on the smallest page, and '
      'so is the heat fallback: 60 posts over pages of 40 and 6 would let '
      'Hot stand, but the page of 6 would fall back, so it is not suggested',
      heats(advice.recommend(plain(), metrics(total=60, low=90))) == [75]
      and heats(advice.recommend(plain(), paged(
          total=60, low=90, sizes={'diet': [54, 6], 'kitchen': [54, 6]})))
      == []
      and heats(advice.recommend(plain(), paged(
          total=60, low=90, sizes={'diet': [30, 30], 'kitchen': [30, 30]}))
      ) == [75])
page_ctx = advice.context(plain(), paged(
    total=40, sizes={'diet': [20, 0, 12], 'kitchen': [9, 0, 30]}))
check('the smallest page that is not empty is the one judged',
      page_ctx.entering == 12 and page_ctx.page == 9
      and advice.context(plain(), metrics(total=40)).page == 40
      and advice.context(plain(), None).page == 0)
hot_recipe = plain(mods=[Modifier(type='heat', percentile_threshold=95)])
check('"too few posts" (under 10) is about a page: 40 posts in 5 pages of 8 '
      'show too few; 40 posts in one page do not',
      heats(advice.recommend(hot_recipe, paged(
          total=40, sizes={'diet': [8] * 5, 'kitchen': [8] * 5}))) == [75]
      and heats(advice.recommend(hot_recipe, metrics(total=40))) == [])
check('more than 20 posts (the sample for "few high quality") is a page '
      'too: 60 posts in pages of 12 are not enough',
      heats(advice.recommend(plain(), paged(
          total=60, high=0, sizes={'diet': [12] * 5, 'kitchen': [12] * 5})
      )) == []
      and heats(advice.recommend(plain(), paged(
          total=60, high=0, sizes={'diet': [60], 'kitchen': [60]}))) == [90])
real = quality.assess_pages([shown[0], shown[1]])
check('assess_pages says how many posts each stage had on each page',
      set(real['page_sizes']) == {'pantry', 'diet', 'kitchen'}
      and all(len(real['page_sizes'][s]) == 2 for s in quality.STAGES)
      and real['page_sizes']['kitchen'] == [
          quality.assess_pages([shown[0]])['kitchen']['total_toots'],
          quality.assess_pages([shown[1]])['kitchen']['total_toots']])
# Counts that are not numbers never reach a score or a file.
odd = quality.assess_entry({'rules': {}, 'facts': {
    'fav': float('nan'), 'reblog': 'lots', 'reply': -3}}, [], None)
fine = quality.assess_entry({'rules': {}, 'facts': {'fav': 2, 'reblog': 1}},
                            [], None)
aggregated = quality.aggregate([odd, fine])
check('a count that is NaN, text or negative is read as 0 and flagged; one '
      'that is missing is 0 and not; nothing is NaN, and the aggregate '
      'serialises strictly',
      odd['quality_score'] == 0.0 and odd['bad_counts']
      and not fine['bad_counts'] and fine['quality_score'] == 4.5
      and aggregated['bad_counts'] == 1
      and json.dumps(aggregated, allow_nan=False)
      and quality.count_of(float('inf')) == (0.0, True)
      and quality.count_of(True) == (0.0, True)
      and quality.count_of(None) == (0.0, False)
      and quality.count_of(7) == (7.0, False)
      and quality.aggregate([])['bad_counts'] == 0)
future = (datetime(2026, 1, 2, tzinfo=timezone.utc)).isoformat()
check('a post dated after the record\'s clock keeps its engagement (the '
      'kitchen leaves its freshness be)',
      quality.quality_score({'fav': 10, 'created_c': future}, [60.0],
                            datetime(2026, 1, 1, tzinfo=timezone.utc)) == 10)
naive = copy.deepcopy(shown[0])
naive['clock']['now_utc'] = naive['clock']['now_utc'][:19]
check('a clock with no zone is read as UTC, so the score is the same and '
      'nothing raises; one that is not a date leaves posts unfaded',
      quality.assess_pages([naive])['recipe_quality_score']
      == quality.assess_pages([shown[0]])['recipe_quality_score']
      and quality.clock_of(naive).tzinfo is not None
      and quality.clock_of({'clock': {'now_utc': 'soon'}}) is None
      and quality.clock_of({}) is None)
# Cooks are in the order of their clocks, not of the list.
base = next(r for r in shown if r['summary']['selected'] >= 26)
variants = []
for index, keep in enumerate((6, 14, 26)):
    one = copy.deepcopy(base)
    one['cook_id'] = 'order%d' % index
    one['posts'] = [e for e in one['posts']
                    if e['removal'] is None][:keep] + [
        e for e in one['posts'] if e['removal'] is not None]
    when = datetime.fromisoformat(base['clock']['now_utc']) + timedelta(
        hours=index)
    one['clock']['now_utc'] = when.isoformat()
    variants.append(one)
in_order = [quality.cook_summary([r]) for r in variants]
shuffled = [variants[2], variants[0], variants[1]]
check('summaries are oldest first by the clock of the records, whatever '
      'order they come in (%s)' % [s['posts'] for s in in_order],
      len({s['posts'] for s in in_order}) == 3
      and quality.summaries(shuffled) == in_order
      and quality.summaries(variants[::-1]) == in_order)

sys.exit(1 if failures else 0)
