# SPDX-License-Identifier: GPL-3.0-or-later
"""``self-test``: the audit harness against worlds whose answer is known.

Synthetic cooks (the seeded fuzz world of ``fuzz.py``) are replayed and
evaluated; then four records are tampered with, one way each, and each
must be caught by the check that exists for it. No network, no display,
and the real log folder is never touched.
"""

import contextlib
import copy
import io
import json
import os
import random
import tempfile

from feedchefaudit import evaluate as evalmod
from feedchefaudit import fuzz
from feedchefaudit import replay as replaymod
from feedchefaudit.collect import collect
from feedchefaudit.records import LogError, Loaded, expand, load
from feedchefaudit.show import show
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


def _by_id(record):
    return {entry['id']: entry for entry in record['posts']}


def _swap_order(record):
    order = record['outputs']['order']
    order[0], order[1] = order[1], order[0]
    # The ranks follow the order, so only the order check can tell.
    posts = _by_id(record)
    for rank, sid in enumerate(order):
        posts[sid]['rank'] = rank


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


def _rereason(record, entry, reason):
    """Give a removed post another reason, the summary kept in step: a
    record that is consistent with itself and wrong about the recipe."""
    counts = record['summary']['by_reason']
    old = entry['removal']['reason']
    counts[old] -= 1
    if not counts[old]:
        del counts[old]
    counts[reason] = counts.get(reason, 0) + 1
    entry['removal']['reason'] = reason


def _wrong_reason(record):
    for entry in record['posts']:
        if entry['removal'] and entry['removal']['reason'].startswith(
                'diet:'):
            _rereason(record, entry, 'diet:must_have_media')
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


def _write(folder, name, loaded, record=None, sidecar=True):
    """A log file of *loaded* (its record replaced by *record*) with its
    sidecar (replaced by *sidecar* when that is not True, none if None)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    payload = {**loaded.payload, 'audit': loaded.record
               if record is None else record}
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)
    side = loaded.sidecar if sidecar is True else sidecar
    if side is not None:
        with open(auditmod.sidecar_path(path), 'w',
                  encoding='utf-8') as handle:
            json.dump(side, handle)
    return path


def _cli(*argv):
    """(exit code, what it printed) of the command line."""
    from feedchefaudit import cli  # cli imports this module
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


def _removed(record, prefix=''):
    return next(entry for entry in record['posts']
                if entry['removal'] and entry['removal']['reason']
                != 'viewer_hide' and entry['removal']['reason'].startswith(
                    prefix))


def _forged_viewer(record):
    _rereason(record, _removed(record), 'viewer_hide')


def _uncounted(record):
    _removed(record)['removal'] = None


def _failed_with_output(record):
    record['outcome'] = {'status': 'failed', 'error': 'x'}


def _verdict(loaded):
    """The evaluate verdict of one record, and the reasons it gave."""
    tally = evalmod.evaluate_record(loaded)
    return tally, ' '.join(tally.examples['record'])


MALFORMED = (
    ('no outputs', lambda r: r.pop('outputs')),
    ('facts of a post is null', lambda r: r['posts'][0].update(facts=None)),
    ('a selected id that is not a post', lambda r: r['outputs'][
        'order'].append('ghost')),
    ('a post without its rules', lambda r: r['posts'][0].pop('rules')),
    ('the summary is a list', lambda r: r.update(summary=[])),
    ('a recipe that does not hash', lambda r: r['recipe'].update(
        hash='0' * 64)),
)
INCONSISTENT = (
    ('pantry_in off by one', lambda r: r['summary'].update(
        pantry_in=r['summary']['pantry_in'] + 1)),
    ('by_reason not the removals', lambda r: r['summary'].update(
        by_reason={'heat_cut': 99})),
    ('a post neither selected nor removed', _uncounted),
    ('guard_checked off by one', lambda r: r['summary'].update(
        guard_checked=r['summary']['guard_checked'] + 1)),
    ('a failed cook with output', _failed_with_output),
    ('a viewer_hide the record never shows', _forged_viewer),
    ('a removal reason out of the closed set', lambda r: _removed(r)[
        'removal'].update(reason='because')),
)


def malformed_cases(checks, loaded, tmp):
    """A record that is not one, or that contradicts itself, is a FAIL with
    a reason (evaluate, show, collect and replay never raise)."""
    for label, mutate in MALFORMED + INCONSISTENT:
        bad = _copy(loaded, mutate)
        try:
            tally, why = _verdict(bad)
        except Exception as error:
            checks('%s: evaluate does not raise (%s)'
                   % (label, type(error).__name__), False)
            continue
        checks('%s: FAIL with a reason (%s)' % (label, why[:60]),
               tally.verdict('record') == 'FAIL' and bool(why))
    hashed = _verdict(_copy(loaded, MALFORMED[-1][1]))[1]
    checks('a snapshot that does not hash says so',
           'recipe snapshot does not match its hash' in hashed)
    folder = os.path.join(tmp, 'bad')
    for number, (label, mutate) in enumerate(MALFORMED[:5]):
        record = copy.deepcopy(loaded.record)
        mutate(record)
        path = _write(folder, 'session_%d_bad.json' % number, loaded, record)
        lines = []
        try:
            code = show(path, out=lines.append)
            evaluated = _cli('evaluate', path)[0]
            collected = collect([path], out=lines.append)
            replayed = _cli('replay', path)[0]
        except Exception as error:
            checks('%s: show, evaluate, collect, replay do not raise (%s)'
                   % (label, type(error).__name__), False)
            continue
        checks('%s: show 2, evaluate 1, collect 1, replay not 0' % label,
               (code, evaluated, collected) == (2, 1, 1) and replayed)
    path = _write(folder, 'session_9_bad.json', loaded)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('{not json')
    checks('a log that is not JSON fails evaluate and collect (1), no '
           'traceback', _cli('evaluate', path)[0] == 1
           and collect([path], out=lambda _line: None) == 1)


def _page_twice(loaded, folder):
    """Two logs of one page: the first, and a later retry of it."""
    again = _copy(loaded, lambda record: None)
    again.path = os.path.join(folder, 'session_9_retry.json')
    again.payload = {**loaded.payload,
                     'timestamp': loaded.timestamp + 5.0}
    return again


def dedupe_and_cancel_cases(checks, loaded, tmp):
    """A page cooked twice counts once; a cancelled cook is no page."""
    once = evalmod.evaluate_cook([loaded])
    twice = evalmod.evaluate_cook([loaded, _page_twice(loaded, tmp)])
    checks('a page cooked twice counts once (%d promises checked, once '
           'is %d)' % (twice.checked['promises'], once.checked['promises']),
           twice.checked == once.checked and twice.ok())
    grouped = evalmod.group_cooks([loaded, _page_twice(loaded, tmp)])
    checks('and the cook groups it once', all(
        len(pages) == 1 for pages in grouped.values()))
    cancelled = _copy(loaded, lambda record: record.update(
        outcome={'status': 'cancelled', 'error': ''}))
    tally = evalmod.evaluate_cook([cancelled])
    checks('a cancelled page judges nothing and is not a failure',
           tally.ok() and not tally.checked['promises'])
    result = replaymod.replay(cancelled)
    checks('and is not replayed (%s)' % result.status,
           result.status == 'skipped')


def text_cases(checks, loaded, tmp):
    """Text that cannot be checked against its hash is said so."""
    clean = evalmod.evaluate_cook([loaded])
    checks('a record with its sidecar is a plain PASS (%s)'
           % clean.overall(), clean.overall() == 'PASS')
    folder = os.path.join(tmp, 'text')
    sid = loaded.record['outputs']['order'][0]
    ref = _by_id(loaded.record)[sid]['facts']['text_ref']
    changed = copy.deepcopy(loaded.sidecar)
    changed[ref]['text'] += ' and more'
    cases = (('a sidecar with a post changed', changed),
             ('a sidecar that is gone', None),
             ('a sidecar with the post missing', {
                 k: v for k, v in loaded.sidecar.items() if k != ref}),
             ('a sidecar of the wrong type', {ref: ['x']}))
    for number, (label, sidecar) in enumerate(cases):
        path = _write(folder, 'session_%d_t.json' % number, loaded,
                      sidecar=sidecar)
        again = load(path)
        tally = evalmod.evaluate_cook([again])
        code, printed = _cli('evaluate', path)
        replayed = _cli('replay', path)
        allowed = _cli('replay', '--allow-partial', path)
        checks('%s: %s, never a plain PASS' % (label, tally.overall()),
               tally.overall().startswith('PASS (text unverified: ')
               and code == 0 and 'overall: PASS (text unverified' in printed)
        checks('%s: replay exits 3, 0 with --allow-partial' % label,
               replayed[0] == 3 and allowed[0] == 0
               and 'text unverified' in replayed[1])
    lines = []
    path = os.path.join(folder, 'session_0_t.json')
    checks('collect says it too and exits 3, 0 with --allow-partial',
           collect([path], out=lines.append) == 3
           and collect([path], out=lines.append, allow_partial=True) == 0
           and any('PASS (text unverified' in line for line in lines))


def _tamper_fact(record):
    sid = record['outputs']['order'][0]
    _by_id(record)[sid]['facts']['text_len'] += 1000


def report_cases(checks, loaded, tmp):
    """A replay's report names a stage, a post and a field: no facts."""
    record = copy.deepcopy(loaded.record)
    _tamper_fact(record)
    path = _write(os.path.join(tmp, 'report'), 'session_0_r.json', loaded,
                  record)
    code, printed = _cli('replay', path)
    result = replaymod.replay(load(path))
    checks('a replay mismatch names the field only (%s)' % result.detail,
           code == 1 and result.detail.startswith('facts (post ')
           and 'facts.text_len differs' in result.detail
           and '{' not in printed and 'recorded' not in printed
           and '1000' not in printed)
    lines = []
    collect([path], out=lines.append)
    notes = [line for line in lines if 'replay mismatch' in line]
    checks('collect prints none of the facts either (%s)' % notes[:1],
           len(notes) == 1 and notes[0].endswith(
               'facts (post %s): facts.text_len differs' % notes[0][
                   notes[0].index('(post ') + 6:][:6])
           and not any('{' in line for line in lines))


def negative_cases_more(checks, loaded, tmp):
    malformed_cases(checks, loaded, tmp)
    dedupe_and_cancel_cases(checks, loaded, tmp)
    text_cases(checks, loaded, tmp)
    report_cases(checks, loaded, tmp)


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
    negative_cases_more(checks, loaded, tmp)
