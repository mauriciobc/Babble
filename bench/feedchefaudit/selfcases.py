# SPDX-License-Identifier: GPL-3.0-or-later
"""The cases of the audit self-test: the recipes it cooks, and the records
it tampers with, one way each, so that each must be caught by the check
that exists for it (negative cases), plus the positive cases for what each
stage of the engine records (the fallback, the variant, the heat, the peek).

``selfcheck`` runs them; this module holds no runner and no state.
"""

import contextlib
import copy
import io
import json
import os
import random

from feedchefaudit import evaluate as evalmod
from feedchefaudit import fuzz
from feedchefaudit import replay as replaymod
from feedchefaudit.collect import collect
from feedchefaudit.records import Loaded, expand, load
from feedchefaudit.show import show
from src.feedchef import audit as auditmod
from src.feedchef import deps
from src.feedchef.recipe import Modifier, default_recipe


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


def focus_recipe():
    """A More Like This focus: the posts are ordered by relevance."""
    recipe = default_recipe('focus')
    recipe.kitchen.modifiers = []
    recipe.kitchen.active_focus_keywords = ['wonderful', 'weather', 'story']
    recipe.kitchen.active_focus_weights = [1.0, 0.5, 0.5]
    return recipe


def grouped_recipe():
    """Similar posts grouped: one post of each group stays."""
    recipe = default_recipe('grouped')
    recipe.diet.hard_filters.no_sensitive_media = True
    recipe.kitchen.modifiers = [Modifier(
        type='similarity_cluster', max_per_cluster=1,
        cluster_strategy='best_score')]
    return recipe


def floor_recipe():
    """Freshness with trending: the factor of an old post has a floor."""
    recipe = default_recipe('floor')
    recipe.kitchen.modifiers = [
        Modifier(type='freshness', decay_minutes=120),
        Modifier(type='trending', growth_threshold=0.3)]
    return recipe


def _cook_recipe(recipe, seed, case, folder, variant=None):
    """The Loaded log of one page of *recipe*."""
    before = set(expand([folder]))
    fuzz.cook(fuzz.World(random.Random(seed), recipe), recipe, case,
              pages=1, now=fuzz.NOW, variant=variant)
    return load((set(expand([folder])) - before).pop())


def _cook_strict(seed, case, folder):
    """The Loaded log of one page of the strict recipe."""
    return _cook_recipe(strict_recipe(), seed, case, folder)


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


def _swap_relevance(record):
    """Swap two neighbours of different relevance, ranks following."""
    order = record['outputs']['order']
    posts = _by_id(record)
    for at in range(len(order) - 1):
        if posts[order[at]]['score']['relevance'] \
                != posts[order[at + 1]]['score']['relevance']:
            order[at], order[at + 1] = order[at + 1], order[at]
            for rank, sid in enumerate(order):
                posts[sid]['rank'] = rank
            return


def _regroup(record):
    """Put two posts the grouping kept into one group, so that it seems
    to keep more than it may."""
    kept = [e for e in record['posts'] if 'cluster' in e and not (
        e['removal'] and e['removal']['reason'] == 'cluster_cap')]
    other = next(e for e in kept
                 if e['cluster']['id'] != kept[0]['cluster']['id'])
    other['cluster'] = dict(kept[0]['cluster'])


def _wrong_cluster_cut(record):
    """Say a post the grouping never saw was cut by it."""
    entry = next(e for e in record['posts']
                 if e['removal'] and e['removal']['reason'] != 'cluster_cap'
                 and e['score'] is None)
    _rereason(record, entry, 'cluster_cap')


def _drop_floor(record):
    """Forget that the floor raised a factor."""
    entry = next(e for e in record['posts']
                 if (e['score'] or {}).get('fresh_floor'))
    del entry['score']['fresh_floor']


def guess_recipe():
    """Favor Positive Posts: posts that declare no language are read on a
    guess."""
    recipe = default_recipe('guess')
    recipe.kitchen.modifiers = [Modifier(type='sentiment_boost',
                                         sentiment_boost=0.5)]
    return recipe


def _weak_guess(record):
    """Make a judged guess less sure than the gate allows."""
    entry = next(e for e in record['posts'] if e['sent']
                 and e['sent']['score'] is not None
                 and not e['facts']['lang'] and e['facts'].get('lang_guess'))
    entry['facts']['lang_guess']['p'] = 0.5


def new_cases(checks, folder):
    if not deps.unavailable_reason('py3langid'):
        guessed = _cook_recipe(guess_recipe(), 44, 913, folder)
        tally = evalmod.evaluate_record(guessed)
        checks('a page with posts judged on a guess passes the language '
               'check (%d)' % tally.checked['language'],
               tally.verdict('language') == 'PASS')
        weak = _copy(guessed, _weak_guess)
        checks('a guess under the gate fails the language check',
               evalmod.evaluate_record(weak).verdict('language') == 'FAIL')
        checks('and is a replay mismatch: the guard drops the post, so the '
               'selection changes',
               replaymod.replay(weak).status == 'mismatch')
    """The relevance order, the grouping and the freshness floor, each
    tampered with one way and caught by the check made for it."""
    ranked = _cook_recipe(focus_recipe(), 41, 910, folder)
    checks('the focus recipe records the relevance of each post (%d)'
           % ranked.record['summary']['selected'],
           ranked.record['summary']['selected'] >= 3 and all(
               'relevance' in e['score'] for e in ranked.record['posts']
               if e['score']))
    tally = evalmod.evaluate_record(ranked)
    checks('an untouched focus page passes the relevance and order checks',
           tally.verdict('relevance') == 'PASS'
           and tally.verdict('order') == 'PASS')
    swapped = _copy(ranked, _swap_relevance)
    tally = evalmod.evaluate_record(swapped)
    checks('a tampered relevance order fails the relevance and order checks',
           tally.verdict('relevance') == 'FAIL'
           and tally.verdict('order') == 'FAIL')
    checks('and is a replay mismatch',
           replaymod.replay(swapped).status == 'mismatch')
    if deps.unavailable_reason('numpy', 'kmedoids'):
        out = checks.out
        out('skip - kmedoids is not installed: the grouping cases are skipped')
        _floor_cases(checks, folder)
        return
    for seed in range(42, 60):
        # A world with at least two groups, so one can be moved.
        grouped = _cook_recipe(grouped_recipe(), seed, 911, folder)
        if grouped.record['cluster'].get('k', 0) >= 2:
            break
    page = grouped.record['cluster']
    checks('the grouping recorded its groups, medoids and loss (%s)'
           % {k: page[k] for k in ('status', 'k', 'loss')},
           page['status'] == 'ok' and page['loss'] >= 0 and any(
               e['removal'] and e['removal']['reason'] == 'cluster_cap'
               for e in grouped.record['posts']))
    tally = evalmod.evaluate_record(grouped)
    checks('an untouched grouping passes the cluster check',
           tally.verdict('cluster') == 'PASS'
           and tally.verdict('removal_reasons') == 'PASS')
    wrong = evalmod.evaluate_record(_copy(grouped, _wrong_cluster_cut))
    checks('a cluster_cap removal of a post that was never grouped fails '
           'the justification',
           wrong.verdict('removal_reasons') == 'FAIL')
    regrouped = _copy(grouped, _regroup)
    checks('a group that seems to keep two posts fails the cluster check',
           evalmod.evaluate_record(regrouped).verdict('cluster') == 'FAIL')
    checks('and is a replay mismatch at the kitchen',
           replaymod.replay(regrouped).stage == 'kitchen')
    _floor_cases(checks, folder)


def _floor_cases(checks, folder):
    floored = _cook_recipe(floor_recipe(), 43, 912, folder)
    tally = evalmod.evaluate_record(floored)
    checks('a freshness page with trending passes the floor check (%d '
           'posts)' % tally.checked['floor'],
           tally.verdict('floor') == 'PASS'
           and any((e['score'] or {}).get('fresh_floor')
                   for e in floored.record['posts']))
    missing = _copy(floored, _drop_floor)
    checks('a floor that is not recorded fails the floor check',
           evalmod.evaluate_record(missing).verdict('floor') == 'FAIL')
    checks('and is a replay mismatch at the kitchen',
           replaymod.replay(missing).stage == 'kitchen')
    fresh_cases(checks, folder)
    variant_cases(checks, folder)


def fade_recipe(decay=1):
    """A fade-out of one minute: freshness zeroes every score of the
    page, and with no trending it is the page's fallback that holds them
    at the floor."""
    recipe = default_recipe('fade')
    recipe.kitchen.modifiers = [Modifier(type='freshness',
                                         decay_minutes=decay)]
    return recipe


def _drop_fallback(record):
    """Forget that the page used the floor."""
    del record['fresh_fallback']


def _claim_fallback(record):
    """Say the page used the floor when its scores were not all zero."""
    record['fresh_fallback'] = True


def fresh_cases(checks, folder):
    """The freshness fallback: a page whose scores are all zero because of
    the fade-out uses the floor and records it; a record that hides it, or
    claims it for a page that did not need it, is caught."""
    faded = _cook_recipe(fade_recipe(), 45, 914, folder)
    tally = evalmod.evaluate_record(faded)
    checks('a page whose scores freshness zeroed used the floor and says '
           'so (%d posts), and passes the floor check'
           % faded.record['summary']['selected'],
           faded.record.get('fresh_fallback') is True
           and faded.record['summary']['selected'] > 0
           and tally.verdict('floor') == 'PASS' and tally.ok()
           and all((e['score'] or {}).get('fresh_floor')
                   for e in faded.record['posts'] if e['score']))
    checks('and replays identically',
           replaymod.replay(faded).status == 'identical')
    missing = _copy(faded, _drop_fallback)
    checks('a fallback that is not recorded fails the floor check',
           evalmod.evaluate_record(missing).verdict('floor') == 'FAIL')
    checks('and is a replay mismatch at the kitchen',
           replaymod.replay(missing).stage == 'kitchen')
    calm = _cook_recipe(fade_recipe(100000), 45, 915, folder)
    checks('a page whose scores are not zero used no fallback, and passes',
           'fresh_fallback' not in calm.record
           and evalmod.evaluate_record(calm).verdict('floor') == 'PASS')
    claimed = _copy(calm, _claim_fallback)
    checks('a fallback claimed for such a page fails the floor check',
           evalmod.evaluate_record(claimed).verdict('floor') == 'FAIL')
    checks('and is a replay mismatch at the kitchen',
           replaymod.replay(claimed).stage == 'kitchen')


def _variant_tag(recipe, arm, other='f' * 64):
    mine = auditmod.recipe_hash(recipe)
    return {'id': arm, 'seed': 7, 'n': 0,
            'original': mine if arm == 'A' else other,
            'proposed': mine if arm == 'B' else other}


def variant_cases(checks, folder):
    """A cook of the A/B optimizer records which recipe it cooked and the
    seed; the record's own recipe is the one the variant names."""
    recipe = strict_recipe()
    for index, arm in enumerate(('A', 'B')):
        cooked = _cook_recipe(recipe, 46, 960 + index, folder,
                              variant=_variant_tag(recipe, arm))
        tally = evalmod.evaluate_record(cooked)
        checks('a cook of variant %s records it with its seed, and the '
               'recipe is the one it names' % arm,
               cooked.record['variant']['id'] == arm
               and cooked.record['variant']['seed'] == 7
               and tally.verdict('variant') == 'PASS' and tally.ok()
               and replaymod.replay(cooked).status == 'identical')
    wrong = _copy(cooked, lambda record: record['variant'].update(id='A'))
    checks('a record that says variant A for the recipe of B fails the '
           'variant check',
           evalmod.evaluate_record(wrong).verdict('variant') == 'FAIL')
    same = _copy(cooked, lambda record: record['variant'].update(
        original=record['variant']['proposed']))
    checks('and so does one whose two recipes are the same',
           evalmod.evaluate_record(same).verdict('variant') == 'FAIL')
    plain = _cook_recipe(recipe, 46, 970, folder)
    checks('a cook that is not one of the optimizer has no variant to '
           'judge',
           'variant' not in plain.record
           and evalmod.evaluate_record(plain).verdict('variant') == 'n/a')


def heat_recipe():
    """Fire heat on a page of 40 posts: it would leave 4, so the page steps
    down to Hot (75), which leaves 10."""
    recipe = default_recipe('heat')
    recipe.kitchen.modifiers = [Modifier(type='heat',
                                         percentile_threshold=90)]
    return recipe


def _unjustified_step(record):
    """Claim a step lower than the first that leaves 5 posts."""
    row = record['heat'][0]
    row['used'] = 50


def _skipped_fallback(record):
    """Claim the cut was made at Fire and left 4: no fallback."""
    row = record['heat'][0]
    row['used'] = row['requested']
    row['after'] = row['before'] - int(row['requested'] * row['before']
                                       / 100)


def _fire_at_the_line(record):
    """Give the Hot badge to the weakest post of the page."""
    record_order = record['outputs']['order']
    _by_id(record)[record_order[-1]]['score']['fire'] = True


def _forget_fire(record):
    """Take the Hot badge off the strongest post of the page."""
    _by_id(record)[record['outputs']['order'][0]]['score']['fire'] = False


def heat_cases(checks, folder):
    """The heat fallback and the Hot badge, each tampered with one way and
    caught by the check made for it (evaluate, and a replay mismatch)."""
    page = _cook_recipe(heat_recipe(), 45, 914, folder)
    row = (page.record.get('heat') or [{}])[0]
    checks('a fire-heat page of 40 posts falls back to Hot and records both '
           'steps (%s)' % row, row.get('requested') == 90
           and row.get('used') == 75 and row.get('before') == 40
           and row.get('after') == 10)
    tally = evalmod.evaluate_record(page)
    checks('an untouched page passes the heat and the Hot badge checks '
           '(%d, %d checked)' % (tally.checked['heat'], tally.checked['fire']),
           tally.verdict('heat') == 'PASS' and tally.verdict('fire') == 'PASS'
           and tally.ok())
    for label, mutate in (('a step lower than needed', _unjustified_step),
                          ('a fallback that was not made',
                           _skipped_fallback)):
        bad = _copy(page, mutate)
        checks('%s fails the heat check' % label,
               evalmod.evaluate_record(bad).verdict('heat') == 'FAIL')
        checks('and is a replay mismatch at the kitchen',
               replaymod.replay(bad).stage == 'kitchen')
    for label, mutate in (('the Hot badge on the weakest post',
                           _fire_at_the_line),
                          ('no Hot badge on the strongest post',
                           _forget_fire)):
        bad = _copy(page, mutate)
        checks('%s fails the fire check' % label,
               evalmod.evaluate_record(bad).verdict('fire') == 'FAIL')
        checks('and is a replay mismatch at the kitchen',
               replaymod.replay(bad).stage == 'kitchen')


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


def peek_cases(checks, base):
    """Smart New Posts: a peek page is judged like any page, a shown one
    also by its merge; a merge that names an id no peek page selected, a
    post twice or a rising order is a FAIL. The shown copy is a page of
    a cook of its own (the cap counts each post once). Returns it."""
    order = base.record['outputs']['order']
    posts = _by_id(base.record)
    keys = sorted((posts[sid]['score']['final'] for sid in order),
                  reverse=True)

    def peeked(record):
        record['cook_id'] = 'peekcopy'
        record['page_index'] = 1001
        record['outcome'] = {'status': 'peek', 'error': ''}

    def shown(record):
        peeked(record)
        record['outcome'] = {'status': 'shown', 'error': ''}
        record['merge'] = {'window': 50, 'size': len(order),
                           'inserted': order[:1], 'head': list(order),
                           'order': 'score', 'keys': list(keys)}

    peek = _copy(base, peeked)
    kept = _copy(base, shown)
    tally = evalmod.evaluate_record(peek)
    checks('a peek page keeps every promise (%s)' % tally.overall(),
           tally.ok() and tally.checked['promises'])
    checks('and replays identically',
           replaymod.replay(peek).status == 'identical')
    tally = evalmod.evaluate_record(kept)
    checks('a shown peek is judged with its merge (%s)' % tally.overall(),
           tally.ok() and tally.checked['merge'] == 1)

    def merged(change):
        def mutate(record):
            shown(record)
            change(record['merge'])
        return evalmod.evaluate_record(_copy(base, mutate))

    foreign = merged(lambda merge: merge.update(inserted=['ghost']))
    checks('an inserted id from no peek page is a FAIL (%s)'
           % ' '.join(foreign.examples['merge']),
           foreign.violations['merge'] == 1)
    twice = merged(lambda merge: merge.update(
        head=merge['head'][:1] + merge['head'][:1] + merge['head'][2:]))
    checks('a post twice in the merged window is a FAIL (%s)'
           % ' '.join(twice.examples['merge']),
           twice.violations['merge'] == 1)
    inserted_twice = merged(lambda merge: merge.update(
        inserted=order[:1] * 2))
    checks('a post inserted twice is a FAIL',
           inserted_twice.violations['merge'] == 1)
    if len(keys) > 1 and keys[0] > keys[-1]:
        rising = merged(lambda merge: merge.update(keys=keys[::-1]))
        checks('a window ordered the wrong way round is a FAIL',
               rising.violations['merge'] == 1)
    wide = merged(lambda merge: merge.update(window=len(order) - 1))
    checks('a window longer than it says is a FAIL',
           wide.violations['merge'] == 1)
    for label, change in (
            ('an order the recipe cannot make', lambda merge: merge.update(
                order='chronological', keys=[],
                head=list(reversed(merge['head'])))),
            ('an order that is not one of the three', lambda merge:
             merge.update(order='bogus')),
            ('a window that is not the window', lambda merge: merge.update(
                window=10 ** 9)),
            ('keys that are not finite', lambda merge: merge.update(
                keys=[float('nan')] * len(merge['head']))),
            ('a head cut short of the list it says', lambda merge:
             merge.update(head=merge['head'][:1])),
            ('nothing inserted by a shown peek', lambda merge: merge.update(
                inserted=[])),
            ('a size smaller than what was inserted', lambda merge:
             merge.update(size=0))):
        tampered = merged(change)
        checks('%s is a FAIL (%s)' % (label, ' '.join(
            tampered.examples['merge'])), tampered.violations['merge'] == 1)
    unmarked = evalmod.evaluate_record(_copy(base, lambda record: (
        shown(record), record.pop('merge'))))
    checks('a shown peek with no merge is a malformed record',
           unmarked.violations['record'] == 1)
    misplaced = evalmod.evaluate_record(_copy(base, lambda record: (
        shown(record), record.update(
            outcome={'status': 'ok', 'error': ''}))))
    checks('a merge on a page that was not a peek is a malformed record',
           misplaced.violations['record'] == 1)
    return kept
