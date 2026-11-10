#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Judge a timeline benchmark run against a baseline.

    BENCH_JSON=base.json ./autoresearch.sh          # on main
    BENCH_JSON=cand.json ./autoresearch.sh          # on the candidate
    python3 bench/compare.py base.json cand.json [--limits bench/limits.json]

Prints one line per gated metric, `metric baseline candidate delta limit
PASS|FAIL`, and exits 1 if any gated metric breaches its limit, 2 on a
usage or input error. Higher is worse for every gated metric. A metric
with both `max_increase_pct` and `max_increase` fails only if BOTH are
exceeded. A limit with `must_exceed` / `must_be_below` judges the candidate
alone (the baseline need not have the metric). A gated metric missing from
either file fails: it never passes by being absent. `cpu_ms` and `wall_ms` are printed but not gated (cpu_ms
counts every thread). Figures are machine-specific: compare two files
measured on the same machine. Standard library only.

`--self-test` checks the rules on synthetic data (no display needed).
"""
import argparse
import json
import math
import os
import sys

REPORT_ONLY = ('cpu_ms', 'wall_ms')


def load(path):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and 'median' in data:
        data = data['median']
    if not isinstance(data, dict):
        raise ValueError('%s: expected a JSON object of metrics' % path)
    return data


def number(value, what):
    """*value* as a finite float, or ValueError: a NaN compares False
    with everything, so it would otherwise pass every limit silently."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError('%s is %r, not a finite number' % (what, value))
    return float(value)


def judge(name, base, cand, limit):
    """Return (delta, limit text, ok) for one gated metric."""
    base = number(base, '%s baseline' % name)
    cand = number(cand, '%s candidate' % name)
    delta = cand - base
    pct = 100.0 * delta / base if base else (0.0 if delta <= 0 else float('inf'))
    over = []
    text = []
    if 'max_increase_pct' in limit:
        bound = number(limit['max_increase_pct'], '%s limit' % name)
        over.append(pct > bound)
        text.append('+%g%%' % bound)
    if 'max_increase' in limit:
        bound = number(limit['max_increase'], '%s limit' % name)
        over.append(delta > bound)
        text.append('+%g' % bound)
    if not over:
        raise ValueError('%s: limit has neither max_increase_pct nor '
                         'max_increase' % name)
    return delta, ' and '.join(text), not all(over)


ABSOLUTE = ('must_exceed', 'must_be_below')


def judge_absolute(name, cand, limit):
    """Return (limit text, ok) for a rule on the candidate alone:
    `must_exceed` (candidate > bound) and `must_be_below` (candidate <
    bound). The baseline plays no part, so the rule holds even where the
    baseline never measured the metric."""
    cand = number(cand, '%s candidate' % name)
    ok = True
    text = []
    if 'must_exceed' in limit:
        bound = number(limit['must_exceed'], '%s limit' % name)
        ok = ok and cand > bound
        text.append('>%g' % bound)
    if 'must_be_below' in limit:
        bound = number(limit['must_be_below'], '%s limit' % name)
        ok = ok and cand < bound
        text.append('<%g' % bound)
    return ' and '.join(text), ok


def compare(base, cand, limits, out=print):
    """Print the table; return the names of the failing metrics."""
    failed = []
    if not isinstance(limits.get('metrics'), dict) or not limits['metrics']:
        raise ValueError('the limits file gates no metrics')
    for name, limit in limits['metrics'].items():
        if any(k in limit for k in ABSOLUTE):
            if name not in cand:
                out('%-24s MISSING from candidate FAIL' % name)
                failed.append(name)
                continue
            text, ok = judge_absolute(name, cand[name], limit)
            out('%-24s %10s %10g %10s %-14s %s'
                % (name, '-', cand[name], '-', text,
                   'PASS' if ok else 'FAIL'))
            if not ok:
                failed.append(name)
            continue
        if name not in base or name not in cand:
            where = 'baseline' if name not in base else 'candidate'
            out('%-24s MISSING from %s FAIL' % (name, where))
            failed.append(name)
            continue
        delta, text, ok = judge(name, base[name], cand[name], limit)
        out('%-24s %10g %10g %+10.1f %-14s %s'
            % (name, base[name], cand[name], delta, text,
               'PASS' if ok else 'FAIL'))
        if not ok:
            failed.append(name)
    for name in REPORT_ONLY:
        if name in base and name in cand:
            out('%-24s %10g %10g %+10.1f %-14s report-only'
                % (name, base[name], cand[name], cand[name] - base[name], '-'))
    return failed


def self_test():
    limits = {'metrics': {
        'both': {'max_increase_pct': 20, 'max_increase': 5},
        'pct_only': {'max_increase_pct': 10},
        'abs_only': {'max_increase': 3},
        'shown': {'must_exceed': 0},
        'leak': {'must_be_below': 1.0},
    }}
    base = {'both': 100, 'pct_only': 100, 'abs_only': 10, 'cpu_ms': 1}
    # The absolute rules judge the candidate alone; the baseline (an
    # inline-off run) has inline_frames_shown 0 and no cycle figure.
    healthy = {'shown': 3, 'leak': 0.2}
    problems = []

    def check(label, cand, want, base=base):
        cand = dict(healthy, **cand)
        got = compare(base, cand, limits, out=lambda s: None)
        if sorted(got) != sorted(want):
            problems.append('%s: failed %s, wanted %s' % (label, got, want))
        else:
            print('ok - %s' % label)

    check('identical passes', dict(base), [])
    check('breach on one metric names it',
          dict(base, pct_only=111), ['pct_only'])
    check('cpu_ms is not gated', dict(base, cpu_ms=999), [])
    # 130% of 100 is over 20% and over +5: fails; 104 is over 20%? no.
    check('over both limits fails', dict(base, both=130), ['both'])
    check('over relative, under absolute passes (small metric)',
          dict(base, both=119, abs_only=10), [])
    small = dict(base, both=2)
    check('tiny metric: huge percent, tiny absolute passes',
          dict(small, both=4), [], base=small)
    check('over absolute, under relative passes',
          dict(base, both=104), [])
    check('absolute-only breach fails', dict(base, abs_only=14), ['abs_only'])
    check('a run that shows no inline frame fails', dict(base, shown=0),
          ['shown'])
    check('one shown frame passes', dict(base, shown=1), [])
    check('a leak of 1 MB per cycle fails', dict(base, leak=1.0), ['leak'])
    check('a leak just under the bound passes', dict(base, leak=0.99), [])
    check('a negative growth passes', dict(base, leak=-0.5), [])
    gone = dict(healthy, **base)
    del gone['shown']
    if compare(base, gone, limits, out=lambda s: None) != ['shown']:
        problems.append('a missing candidate figure for shown did not fail')
    else:
        print('ok - a missing absolute metric fails')
    nan = dict(healthy, **base)
    nan['leak'] = float('nan')
    try:
        compare(base, nan, limits, out=lambda s: None)
        problems.append('NaN leak was accepted')
    except ValueError:
        print('ok - a NaN absolute metric is an input error')
    cand = dict(base)
    del cand['pct_only']
    check('missing candidate metric fails', cand, ['pct_only'])
    nobase = dict(base)
    del nobase['abs_only']
    check('missing baseline metric fails', dict(base), ['abs_only'], base=nobase)
    # Bad input is an error (exit 2), never a pass and never a "breach".
    for label, bad in (('NaN candidate', dict(base, both=float('nan'))),
                       ('string candidate', dict(base, both='12')),
                       ('null candidate', dict(base, both=None)),
                       ('bool candidate', dict(base, both=True))):
        try:
            compare(base, bad, limits, out=lambda s: None)
            problems.append('%s was accepted' % label)
        except ValueError:
            print('ok - %s is an input error' % label)
    try:
        compare(base, dict(base), {'metrics': {}}, out=lambda s: None)
        problems.append('empty limits passed')
    except ValueError:
        print('ok - an empty limits file is an input error')
    here = os.path.dirname(os.path.abspath(__file__))
    shipped = json.load(open(os.path.join(here, 'limits.json')))
    inline = json.load(open(os.path.join(here, 'limits-inline.json')))
    for name in ('main_ms', 'stall_max_ms', 'stall_p99_ms',
                 'stalls_over_16ms', 'wall_stall_max_ms',
                 'wall_stall_p99_ms', 'wall_stalls_over_16ms',
                 'frames_with_empty_shell', 'peak_rss_mb', 'rss_growth_mb',
                 'retained_mb'):
        if name not in shipped['metrics']:
            problems.append('limits.json does not gate %s' % name)
    extra = ('inline_frames_shown', 'inline_cycle_growth_mb')
    if set(inline['metrics']) != set(shipped['metrics']) | set(extra):
        problems.append('limits-inline.json gates a different metric set')
    if inline['metrics'].get('inline_frames_shown') != {'must_exceed': 0}:
        problems.append('limits-inline.json inline_frames_shown must be '
                        'must_exceed 0')
    if inline['metrics'].get('inline_cycle_growth_mb') != {
            'must_be_below': 1.0}:
        problems.append('limits-inline.json inline_cycle_growth_mb must be '
                        'must_be_below 1.0')
    if inline['metrics'].get('retained_mb') != {'max_increase': 20}:
        problems.append('limits-inline.json retained_mb must be the entry '
                        'bound, max_increase 20')
    memory = ('peak_rss_mb', 'rss_growth_mb', 'retained_mb')
    for name in shipped['metrics']:
        same = inline['metrics'].get(name) == shipped['metrics'][name]
        if (name in memory) == same:
            problems.append('limits-inline.json %s %s' % (
                name, 'must differ' if same else 'must equal limits.json'))
    for name in memory[:2]:
        if inline['metrics'].get(name, {}).get('max_increase', 0) < 74:
            problems.append('limits-inline.json %s allows less than one '
                            'engine (74 MB)' % name)
    if not problems:
        print('ok - limits.json gates every metric; limits-inline.json '
              'differs only in the memory limits and the two inline rules')
    for problem in problems:
        print('FAIL - %s' % problem)
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('baseline', nargs='?')
    ap.add_argument('candidate', nargs='?')
    ap.add_argument('--limits', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'limits.json'))
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.baseline or not args.candidate:
        ap.error('BASELINE.json and CANDIDATE.json are required')
    try:
        with open(args.limits) as f:
            limits = json.load(f)
        failed = compare(load(args.baseline), load(args.candidate), limits)
    except (OSError, ValueError, KeyError) as e:
        print('compare: %s' % e, file=sys.stderr)
        return 2
    if failed:
        print('FAIL: %s' % ', '.join(failed))
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
