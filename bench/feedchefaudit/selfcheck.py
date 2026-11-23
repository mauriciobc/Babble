# SPDX-License-Identifier: GPL-3.0-or-later
"""``self-test``: the audit harness against worlds whose answer is known.

Synthetic cooks (the seeded fuzz world of ``fuzz.py``) are replayed and
evaluated; then four records are tampered with, one way each, and each
must be caught by the check that exists for it. No network, no display,
and the real log folder is never touched.
"""

import copy
import os
import random
import tempfile

from feedchefaudit import evaluate as evalmod
from feedchefaudit import fuzz
from feedchefaudit import replay as replaymod
from feedchefaudit.records import LogError, Loaded, expand, load
from src.feedchef import audit as auditmod
from src.feedchef import decisionlog
from src.feedchef import network_reach as reachmod
from src.feedchef.recipe import Modifier, default_recipe

SEEDS = 40


class Checks:
    def __init__(self, out):
        self.out = out
        self.failures = 0

    def __call__(self, label, condition):
        if not condition:
            self.failures += 1
        self.out('%s - %s' % ('ok' if condition else 'FAIL', label))


def strict_recipe():
    """A recipe that exercises every stage: hard filters, a hashtag
    exclusion, a score order, heat and a cap per person."""
    recipe = default_recipe('strict')
    recipe.pantry.hashtags_exclude = ['news']
    recipe.diet.hard_filters.no_sensitive_media = True
    recipe.diet.hard_filters.min_content_length = 20
    recipe.kitchen.modifiers = [
        Modifier(type='freshness', decay_minutes=2000),
        Modifier(type='heat', percentile_threshold=25),
        Modifier(type='diversity', max_per_cluster=2,
                 diversity_dimensions=['author'])]
    return recipe


def _cook_strict(seed, case, folder):
    """The Loaded log of one page of the strict recipe."""
    before = set(expand([folder]))
    recipe = strict_recipe()
    fuzz.cook(fuzz.World(random.Random(seed), recipe), recipe, case,
              pages=1, now=fuzz.NOW)
    return load((set(expand([folder])) - before).pop())


def _copy(loaded, mutate):
    """A Loaded with a deep copy of the record, *mutate*d."""
    record = copy.deepcopy(loaded.record)
    mutate(record)
    return Loaded(loaded.path, {**loaded.payload, 'audit': record}, record,
                  loaded.sidecar)


def _swap_order(record):
    order = record['outputs']['order']
    order[0], order[1] = order[1], order[0]


def _bump_score(record):
    sid = record['outputs']['order'][0]
    for entry in record['posts']:
        if entry['id'] == sid:
            entry['score']['final'] += 1.0


def _break_diet(record):
    sid = record['outputs']['order'][0]
    for entry in record['posts']:
        if entry['id'] == sid:
            entry['facts']['sensitive'] = True


def _wrong_reason(record):
    for entry in record['posts']:
        if entry['removal'] and entry['removal']['reason'].startswith(
                'diet:'):
            entry['removal']['reason'] = 'diet:must_have_media'
            return


def negative_cases(checks, loaded):
    """Four tampered copies of *loaded*; each caught by its own check."""
    swapped = _copy(loaded, _swap_order)
    tally = evalmod.evaluate_record(swapped)
    checks('a tampered order fails the order check',
           tally.verdict('order') == 'FAIL')
    result = replaymod.replay(swapped)
    checks('a tampered order is a replay mismatch (%s)' % result.stage,
           result.status == 'mismatch')
    bumped = replaymod.replay(_copy(loaded, _bump_score))
    checks('a tampered score is a replay mismatch at the kitchen',
           bumped.status == 'mismatch' and bumped.stage == 'kitchen')
    broken = _copy(loaded, _break_diet)
    checks('a selected post that breaks the diet fails the promises',
           evalmod.evaluate_record(broken).verdict('promises') == 'FAIL')
    checks('and replays as a mismatch',
           replaymod.replay(broken).status == 'mismatch')
    wrong = evalmod.evaluate_record(_copy(loaded, _wrong_reason))
    checks('a removal whose reason is wrong fails the justification',
           wrong.verdict('removal_reasons') == 'FAIL')


def run_self_test(out=print):
    checks = Checks(out)
    old = (decisionlog._root, reachmod._root)
    with tempfile.TemporaryDirectory(prefix='feedchef-selftest-') as tmp:
        decisionlog.set_root(os.path.join(tmp, 'logs'))
        reachmod.set_root(os.path.join(tmp, 'reach'))
        try:
            _run(checks, tmp)
        finally:
            decisionlog.set_root(old[0])
            reachmod.set_root(old[1])
    out('%s - the audit self-test' % ('FAIL' if checks.failures else 'ok'))
    return 1 if checks.failures else 0


def _run(checks, tmp):
    for seed in range(SEEDS):
        rng = random.Random(7000 + seed)
        recipe = fuzz.random_recipe(rng)
        fuzz.cook(fuzz.World(rng, recipe), recipe, seed, now=fuzz.NOW)
    logs = [load(path) for path in expand([os.path.join(tmp, 'logs')])]
    checks('%d synthetic pages were recorded' % len(logs), len(logs) > SEEDS)
    results = [replaymod.replay(item) for item in logs]
    bad = [r for r in results if r.status != 'identical']
    checks('every synthetic record replays identically (%d)' % len(results),
           not bad)
    checks('no sentiment drift on a replay',
           not any(r.drift for r in results))
    tally = evalmod.Tally()
    for pages in evalmod.group_cooks(logs).values():
        tally.merge(evalmod.evaluate_cook(pages))
    for rule in evalmod.RULES:
        checks('evaluate %s: %s (%d checked)' % (
            rule, tally.verdict(rule), tally.checked[rule]),
            tally.verdict(rule) == 'PASS')
    # Without the sidecar the text-reading rules fall back on the
    # recorded verdicts: still identical, and said to be partial.
    bare = Loaded(logs[0].path, logs[0].payload, logs[0].record, None)
    result = replaymod.replay(bare)
    checks('a record without its text sidecar replays on recorded '
           'verdicts (%d posts)' % result.partial,
           result.status == 'identical' and result.partial > 0)
    stale = _copy(logs[0], lambda record: record.update(
        engine_rev=auditmod.ENGINE_REV + 1))
    try:
        replaymod.replay(stale)
        refused = False
    except LogError:
        refused = True
    checks('another engine revision is refused', refused)
    checks('and replays when forced',
           replaymod.replay(stale, force=True).status == 'identical')
    loaded = _cook_strict(31, 900, os.path.join(tmp, 'logs'))
    checks('the strict recipe selects enough posts to tamper with (%d)'
           % loaded.record['summary']['selected'],
           loaded.record['summary']['selected'] >= 3)
    negative_cases(checks, loaded)
