#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's freshness fallback (a page whose scores freshness zeroed uses
the 0.2 floor, and says so) through the kitchen, the contract, the audit,
replay and evaluate, and the quality section of the audit CLI
(bench/feedchef_audit.py quality), whose JSON carries numbers, ids, hashes
and dates only. No display, no network."""

import json
import os
import random
import re
import subprocess
import sys
import tempfile
from datetime import timedelta

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from src.feedchef import heat  # noqa: E402
from src.feedchef import quality  # noqa: E402
from src.feedchef import audit as auditmod  # noqa: E402
from src.feedchef import contract, kitchen  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef.recipe import Modifier  # noqa: E402

from feedchefaudit import evaluate as evalmod  # noqa: E402
from feedchefaudit import fuzz, qualityreport, selfcheck  # noqa: E402
from feedchefaudit.collect import collect  # noqa: E402
from feedchefaudit.qualitykit import metrics, plain  # noqa: E402
from feedchefaudit.records import expand, load  # noqa: E402

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


# --- the freshness fallback --------------------------------------------------------

NOW = fuzz.NOW


def post(sid, minutes, fav=5):
    return {'id': sid, 'created_at': (NOW - timedelta(minutes=minutes)
                                      ).isoformat(),
            'content': '<p>hello there</p>', 'favourites_count': fav,
            'reblogs_count': 0, 'replies_count': 0,
            'account': {'id': 'a' + sid, 'url': 'https://ex.ample/@a'},
            'tags': [], 'media_attachments': []}


faded = plain(scope='home', mods=[Modifier(type='freshness',
                                           decay_minutes=60)])
old = [post('1', 120), post('2', 300, fav=9), post('3', 600, fav=2)]
ordered, scores, meta = kitchen.score_statuses(old, faded, now=NOW)
check('every post faded to zero: the page uses the floor of 0.2 (and '
      'says so), so the scores are 0.2 x engagement and the order is '
      'by them',
      meta['fresh_fallback'] is True
      and all(f == 0.2 and lifted for f, lifted in meta['fresh'].values())
      and abs(scores['2'] - 1.8) < 1e-12 and abs(scores['1'] - 1.0) < 1e-12
      and [p['id'] for p in ordered] == ['2', '1', '3'])
ordered, scores, meta = kitchen.score_statuses(
    old + [post('4', 10)], faded, now=NOW)
check('one fresh post: not every score is zero, so no fallback, and the '
      'old ones stay at zero',
      meta['fresh_fallback'] is False and scores['1'] == 0.0
      and scores['4'] > 0
      and not any(lifted for _f, lifted in meta['fresh'].values()))
ordered, scores, meta = kitchen.score_statuses(
    [post('1', 120, fav=0), post('2', 300, fav=0)], faded, now=NOW)
check('nobody has any engagement: freshness is not why the scores are '
      'zero, so no fallback',
      meta['fresh_fallback'] is False and set(scores.values()) == {0.0})
trending = plain(scope='home', mods=[
    Modifier(type='freshness', decay_minutes=60),
    Modifier(type='trending', growth_threshold=0.3)])
ordered, scores, meta = kitchen.score_statuses(old, trending, now=NOW)
check('with Boost Trending Topics the floor is already there: no fallback, '
      'and the scores are the floor\'s',
      meta['fresh_fallback'] is False and abs(scores['2'] - 1.8) < 1e-12)
check('no Favor Fresh Posts, no posts: never a fallback',
      kitchen.score_statuses(old, plain(scope='home'), now=NOW)[2][
          'fresh_fallback'] is False
      and kitchen.score_statuses([], faded, now=NOW)[2][
          'fresh_fallback'] is False)
r = faded
check('the rule is the contract\'s: freshness, no trending, some '
      'engagement, every score zero',
      contract.fresh_fallback_wanted(r, [(5, 0.0), (3, 0.0)])
      and not contract.fresh_fallback_wanted(r, [(5, 0.0), (3, 0.4)])
      and not contract.fresh_fallback_wanted(r, [(0, 0.0)])
      and not contract.fresh_fallback_wanted(r, [(5, 0.5), (3, 0.0)])
      and not contract.fresh_fallback_wanted(r, [(5, None)])
      and not contract.fresh_fallback_wanted(r, [])
      and not contract.fresh_fallback_wanted(trending, [(5, 0.0)])
      and not contract.fresh_fallback_wanted(plain(), [(5, 0.0)]))
check('a page that says it used the fallback when the recipe cannot have '
      'needed it breaks fresh_fallback; one that did not say, or needed '
      'it, does not',
      contract.fresh_fallback_violations(plain(), True)
      == ['fresh_fallback']
      and contract.fresh_fallback_violations(trending, True)
      == ['fresh_fallback']
      and contract.fresh_fallback_violations(faded, True) == []
      and contract.fresh_fallback_violations(plain(), False) == []
      and 'fresh_fallback' in contract.PAGE_IDS)
one = post('1', 600)
facts = dict(fresh={'1': (0.2, True)}, now=NOW)
check('the guard\'s freshness check allows the floor on a fallback page and '
      'only there',
      contract.list_violations(faded, [one], contract.ListFacts(
          fresh_fallback=True, **facts)) == {}
      and contract.list_violations(faded, [one], contract.ListFacts(
          **facts)) == {'1': ['fresh_floor']}
      and contract.list_violations(faded, [one], contract.ListFacts(
          fresh={'1': (0.0, False)}, now=NOW, fresh_fallback=True))
      == {'1': ['fresh_floor']})
check('the heat half of moshidon\'s auto_fallback stays heat.py\'s: the '
      'step down to a page of 5 posts (no second rule)',
      not hasattr(kitchen, 'reduce_heat_threshold')
      and heat.used(90, 40) == 75 and heat.MIN_LEFT == 5)

# Through the real engine: the record, the guard, the audit's checks.
fresh_logs('fallback')
rng = random.Random(81)
fade = selfcheck.fade_recipe()
results = fuzz.cook(fuzz.World(rng, fade), fade, 1, pages=2, now=NOW)
page = results[0]
record = page.logger.record
check('a real page of one-minute fade-out: the fallback is in the kitchen '
      'meta, the record, the log\'s fallbacks, and every shown post has '
      'a score above zero',
      page.kitchen_meta['fresh_fallback'] is True
      and record['fresh_fallback'] is True and len(page.items) > 5
      and 'fresh_floor_fallback' in page.logger.log.reliability['fallbacks']
      and page.guard['dropped'] == {}
      and all(page.score_map[i['id']] > 0 for i in page.items
              if kitchen.base_score(i) > 0)
      and record['engine_rev'] == auditmod.ENGINE_REV == 5)


class Collect:
    failures = 0

    def __init__(self):
        self.out = lambda line: print(line)

    def __call__(self, label, condition):
        if not condition:
            self.failures += 1
            failures.append(label)
        print('%s - %s' % ('ok' if condition else 'FAIL', label))


collector = Collect()
selfcheck.fresh_cases(collector, fresh_logs('selfcheck-fresh'))
selfcheck.variant_cases(collector, fresh_logs('selfcheck-variant'))
# A kitchen that claims the fallback for a recipe without freshness: the
# guard records it and evaluate fails it.
saved = kitchen.fresh_fallback_wanted
kitchen.fresh_fallback_wanted = lambda recipe, rows: True
try:
    bad_recipe_ = plain(scope='home')
    fresh_logs('lying')
    bad = fuzz.cook(fuzz.World(random.Random(5), bad_recipe_), bad_recipe_,
                    2, pages=1, now=NOW)[0]
finally:
    kitchen.fresh_fallback_wanted = saved
check('a stage that claims the fallback for a recipe that cannot need it '
      'is caught by the guard (recorded in its stats) and fails evaluate',
      bad.guard['dropped'].get('fresh_fallback') == 1
      and bad.logger.record['fresh_fallback'] is True
      and evalmod.evaluate_record(load(expand([logmod._dir()])[0])
                                  ).verdict('floor') == 'FAIL')

# --- a claim of the fallback with nothing scored ------------------------------------

from types import SimpleNamespace  # noqa: E402

empty_page = SimpleNamespace(
    recipe=selfcheck.fade_recipe(), statuses={}, entries=lambda: [],
    record={'fresh_fallback': True, 'clock': {'now_utc': NOW.isoformat()}})
claimed = evalmod.Tally()
evalmod._check_floor(claimed, empty_page)
silent = evalmod.Tally()
empty_page.record = {'clock': {'now_utc': NOW.isoformat()}}
evalmod._check_floor(silent, empty_page)
bare = SimpleNamespace(recipe=plain(), statuses={}, entries=lambda: [],
                       record={'fresh_fallback': True,
                               'clock': {'now_utc': NOW.isoformat()}})
bare_tally = evalmod.Tally()
evalmod._check_floor(bare_tally, bare)
check('a page that says it used the freshness fallback and scored no post '
      'fails the floor check (a recipe without Favor Fresh Posts does too); '
      'one that says nothing has nothing to judge',
      claimed.verdict('floor') == 'FAIL' and silent.verdict('floor') == 'n/a'
      and bare_tally.verdict('floor') == 'FAIL')

# --- the CLI: bench/feedchef_audit.py quality, and the section of collect -----------

cli_logs = fresh_logs('cli')
cli_recipe = fuzz.random_recipe(random.Random(2))
cli_recipe.meta.name = 'My Very Private Recipe'
cli_recipe.kitchen.modifiers = [
    Modifier(type='freshness', decay_minutes=720),
    Modifier(type='heat', percentile_threshold=50),
    Modifier(type='diversity', max_per_cluster=3,
             diversity_dimensions=['author'])]
cli_recipe.pantry.hashtags_include = ['coffee']
all_ids = set()
for case in range(12):
    world = fuzz.World(random.Random(900 + case), cli_recipe)
    for page in fuzz.cook(world, cli_recipe, 500 + case, pages=1,
                          now=NOW - timedelta(hours=12 - case)):
        all_ids.update(e['id'] for e in page.logger.record['posts'])
lines = []
json_path = os.path.join(TMP, 'quality.json')
code = qualityreport.run([cli_logs], json_path=json_path, out=lines.append)
text = '\n'.join(lines)
check('quality prints the caveat, each stage, the health and the advice, '
      'and exits 0 (%d lines)' % len(lines),
      code == 0 and quality.caveat_text() in text
      and all('  %-8s ' % name in text for name in quality.STAGES)
      and 'health:' in text and 'cook scores:' in text
      and 'latest cook: recipe score' in text)
with open(json_path, encoding='utf-8') as handle:
    exported = json.load(handle)
row = exported['recipes'][0]
check('--json: one recipe, the 12 cooks\' scores, the stages, the health, '
      'the advice, and the caveat in the data',
      exported['schema'] == 1 and len(exported['recipes']) == 1
      and len(row['cooks']) == 12 and row['caveat'] == 'validity_enforced'
      and set(row['stages']) == {'pantry', 'diet', 'kitchen'}
      and row['health']['state'] in ('working_well', 'needs_attention')
      and row['health']['cooks'] == 10
      and row['recipe_hash'] and row['first_day'] <= row['last_day'])
# Privacy: numbers, ids, verdicts, hashes and dates only.
strings = []


def walk(value, key=''):
    if isinstance(value, dict):
        for k, v in value.items():
            strings.append(('key', k))
            walk(v, k)
    elif isinstance(value, list):
        for v in value:
            walk(v, key)
    elif isinstance(value, str):
        strings.append(('value', value))


walk(exported)
allowed = re.compile(r'^([a-z0-9_:.]+|[0-9]{4}-[0-9]{2}-[0-9]{2}|'
                     r'[0-9a-f]{64})$')
raw = json.dumps(exported)
check('the export has no recipe name, hashtag, post id or text: every '
      'string is an id, a date or a hash',
      all(allowed.match(s) for _kind, s in strings)
      and 'Private' not in raw and 'coffee' not in raw
      and not any(sid in raw for sid in all_ids if len(sid) > 5)
      and not any(word in raw for word in ('wonderful', 'weather', 'fuzz')))
code = qualityreport.run([cli_logs], recipe_id='nothing', out=lines.append)
check('a recipe with no logs, or no logs at all: nothing to report on, '
      'exit 2',
      code == 2 and qualityreport.run([os.path.join(TMP, 'void')],
                                      out=lines.append) == 2)
since = qualityreport.run([cli_logs], since='2999-01-01', out=lines.append)
check('--since after every log: exit 2', since == 2)
lines = []
code = collect([cli_logs], json_path=os.path.join(TMP, 'collect.json'),
               out=lines.append)
with open(os.path.join(TMP, 'collect.json'), encoding='utf-8') as handle:
    collected = json.load(handle)
check('collect has a quality section per recipe, in the text and in the '
      'JSON, and still passes (exit %d)' % code,
      code == 0 and any(line.strip().startswith('quality:') for line in lines)
      and 'quality' in collected['recipes'][0]
      and collected['recipes'][0]['quality']['caveat'] == 'validity_enforced'
      and not any(sid in json.dumps(collected) for sid in all_ids
                  if len(sid) > 5)
      and 'Private' not in json.dumps(collected))
done = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(srcdir), 'bench',
                                  'feedchef_audit.py'), 'quality', cli_logs,
     '--json', os.path.join(TMP, 'cli.json')],
    capture_output=True, text=True, timeout=60,
    env={**os.environ, 'GSETTINGS_BACKEND': 'memory'})
check('python3 bench/feedchef_audit.py quality runs from the command line',
      done.returncode == 0 and 'recipe score' in done.stdout
      and os.path.isfile(os.path.join(TMP, 'cli.json')))
evaluated = evalmod.Tally()
for pages in evalmod.group_cooks([load(p) for p in expand([cli_logs])
                                  ]).values():
    evaluated.merge(evalmod.evaluate_cook(pages))
check('the logs of that CLI pass every rule of evaluate (the variant rule '
      'has nothing to judge)', evaluated.ok()
      and evaluated.verdict('variant') == 'n/a')

# --- an A/B experiment in the logs is one line, not two recipes --------------------

from src.feedchef import advice, optimizer  # noqa: E402

experiment_logs = fresh_logs('experiment')
exp_recipe = plain(scope='home', mods=[Modifier(
    type='freshness', decay_minutes=300)])
exp_recipe.id = 'exp'
exp_state = optimizer.accept(optimizer.propose(exp_recipe, advice.recommend(
    exp_recipe, metrics(total=20, avg=1, median=1)), seed=424242,
    trials=6))
for index in range(6):
    exp_state, plan = optimizer.next_cook(exp_state, exp_recipe)
    fuzz.cook(fuzz.World(random.Random(index), plan.recipe), plan.recipe,
              800 + index, pages=1, now=NOW, variant=plan.tag)
    exp_state = optimizer.record_result(exp_state, plan.tag,
                                        0.7 if plan.arm == 'B' else 0.3)
lines = []
exp_json = os.path.join(TMP, 'experiment.json')
code = qualityreport.run([experiment_logs], json_path=exp_json,
                         out=lines.append)
text = '\n'.join(lines)
with open(exp_json, encoding='utf-8') as handle:
    raw = handle.read()
exported = json.loads(raw)
row = exported['experiments'][0]
check('the cooks of an experiment are one line with the cooks of each arm '
      'and their mean score (B is not reported as a recipe of its own); '
      'the seed is nowhere',
      code == 0 and len(exported['experiments']) == 1
      and row['cooks_a'] + row['cooks_b'] == 6 and row['cooks_a'] >= 2
      and row['cooks_b'] >= 2 and 'experiment (A/B): A ' in text
      and text.count('recipe (hash') == 1
      and len(exported['recipes']) == 1
      and len(exported['recipes'][0]['cooks']) == row['cooks_a']
      and '424242' not in raw and '424242' not in text
      and set(row) == {'cooks_a', 'cooks_b', 'mean_a', 'mean_b'})
lines = []
collect([experiment_logs], out=lines.append)
check('collect says it too, and still passes',
      any(l_.startswith('experiment (A/B)') for l_ in lines))

sys.exit(1 if failures else 0)
