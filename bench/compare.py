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
exceeded. A gated metric missing from either file fails: it never passes
by being absent. `cpu_ms` and `wall_ms` are printed but not gated (cpu_ms
counts every thread). Figures are machine-specific: compare two files
measured on the same machine. Standard library only.

`--self-test` checks the rules on synthetic data (no display needed).
"""
import argparse
import json
import os
import sys

REPORT_ONLY = ('cpu_ms', 'wall_ms')


def load(path):
    with open(path) as f:
        data = json.load(f)
    return data.get('median', data)


def judge(name, base, cand, limit):
    """Return (delta, limit text, ok) for one gated metric."""
    delta = cand - base
    pct = 100.0 * delta / base if base else (0.0 if delta <= 0 else float('inf'))
    over = []
    text = []
    if 'max_increase_pct' in limit:
        over.append(pct > limit['max_increase_pct'])
        text.append('+%g%%' % limit['max_increase_pct'])
    if 'max_increase' in limit:
        over.append(delta > limit['max_increase'])
        text.append('+%g' % limit['max_increase'])
    if not over:
        raise ValueError('%s: limit has neither max_increase_pct nor '
                         'max_increase' % name)
    return delta, ' and '.join(text), not all(over)


def compare(base, cand, limits, out=print):
    """Print the table; return the names of the failing metrics."""
    failed = []
    for name, limit in limits['metrics'].items():
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
    }}
    base = {'both': 100, 'pct_only': 100, 'abs_only': 10, 'cpu_ms': 1}
    problems = []

    def check(label, cand, want, base=base):
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
    cand = dict(base)
    del cand['pct_only']
    check('missing candidate metric fails', cand, ['pct_only'])
    nobase = dict(base)
    del nobase['abs_only']
    check('missing baseline metric fails', dict(base), ['abs_only'], base=nobase)
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
    if set(inline['metrics']) != set(shipped['metrics']):
        problems.append('limits-inline.json gates a different metric set')
    memory = ('peak_rss_mb', 'rss_growth_mb')
    for name in shipped['metrics']:
        same = inline['metrics'].get(name) == shipped['metrics'][name]
        if (name in memory) == same:
            problems.append('limits-inline.json %s %s' % (
                name, 'must differ' if same else 'must equal limits.json'))
    for name in memory:
        if inline['metrics'].get(name, {}).get('max_increase', 0) < 74:
            problems.append('limits-inline.json %s allows less than one '
                            'engine (74 MB)' % name)
    if not problems:
        print('ok - limits.json gates every metric; limits-inline.json '
              'differs only in peak_rss_mb and rss_growth_mb')
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
