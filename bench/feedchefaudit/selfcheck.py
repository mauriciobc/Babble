# SPDX-License-Identifier: GPL-3.0-or-later
"""``self-test``: the audit harness against worlds whose answer is known.

Synthetic cooks (the seeded fuzz world of ``fuzz.py``) are replayed and
evaluated; then four records are tampered with, one way each, and each
must be caught by the check that exists for it. No network, no display,
and the real log folder is never touched. The recipes and the tampered
records are in ``selfcases``; this is the runner.
"""

import os
import random
import tempfile

from feedchefaudit import evaluate as evalmod
from feedchefaudit import fuzz
from feedchefaudit import replay as replaymod
from feedchefaudit.records import LogError, Loaded, expand, load
from feedchefaudit.selfcases import (_cook_strict, _copy, _variant_tag,
                                     heat_cases, negative_cases,
                                     negative_cases_more, new_cases,
                                     peek_cases)
from src.feedchef import audit as auditmod
from src.feedchef import decisionlog, deps
from src.feedchef import network_reach as reachmod

SEEDS = 40


class Checks:
    def __init__(self, out):
        self.out = out
        self.failures = 0

    def __call__(self, label, condition):
        if not condition:
            self.failures += 1
        self.out('%s - %s' % ('ok' if condition else 'FAIL', label))


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
        # Every fifth cook is one of the A/B optimizer's (alternating the
        # arm): the variant it records is judged by the variant rule.
        arm = 'AB'[seed // 5 % 2]
        variant = None if seed % 5 else _variant_tag(recipe, arm)
        fuzz.cook(fuzz.World(rng, recipe), recipe, seed, now=fuzz.NOW,
                  variant=variant)
    logs = [load(path) for path in expand([os.path.join(tmp, 'logs')])]
    checks('%d synthetic pages were recorded' % len(logs), len(logs) > SEEDS)
    base = next(item for item in logs
                if item.record['summary']['selected'] >= 3
                and item.record['outcome']['status'] == 'ok')
    logs.append(peek_cases(checks, base))
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
            tally.verdict(rule) == 'PASS' or (
                rule == 'language' and deps.unavailable_reason('py3langid')))
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
    old_rev = _copy(logs[0], lambda record: record.update(engine_rev=3))
    try:
        replaymod.replay(old_rev)
        said = ''
    except LogError as error:
        said = str(error)
    checks('a record of revision 3 is refused cleanly (%s)' % said[:40],
           said.startswith('made by engine revision 3'))
    checks('and replays when forced',
           replaymod.replay(stale, force=True).status == 'identical')
    loaded = _cook_strict(31, 900, os.path.join(tmp, 'logs'))
    checks('the strict recipe selects enough posts to tamper with (%d)'
           % loaded.record['summary']['selected'],
           loaded.record['summary']['selected'] >= 3)
    negative_cases(checks, loaded)
    negative_cases_more(checks, loaded, tmp)
    new_cases(checks, os.path.join(tmp, 'logs'))
    heat_cases(checks, os.path.join(tmp, 'logs'))
