# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare a v2 benchmark run against baseline.json, for CI.

The point of this file is that ``bench/timeline_bench.py`` never had one.
Every figure in PERFORMANCE.md was measured by hand, on one machine, and
nothing failed when one got worse. A measurement nobody checks is a
diary entry.

Design
------

``BUDGETS`` is the single source of truth: one entry per gated metric,
saying which direction is bad, how much relative drift to forgive, and
how much absolute slack to forgive. Recording and comparing both read it,
so a metric cannot be recorded one way and judged another. A metric with
no entry is informational and never gates.

Three failure modes this file is built to avoid, each of which bit an
earlier version of it:

* **A metric whose good value is 0.** Dividing by the baseline turns
  0 -> 0 into "regressed inf%". Every comparison is therefore an
  absolute allowance (``baseline * (1 + tolerance) + slack``) rather
  than a ratio, and small counts carry slack because a relative band is
  meaningless at 0-2.
* **A direction nobody wrote down.** An earlier version treated every
  metric as lower-is-better, so a run that painted *more* frames was
  reported as a regression. Direction is now explicit, and a metric
  whose direction is unstated does not gate.
* **A floor quietly turning back into a ceiling.** A floor-only metric
  (the decode-path proof) returns immediately once satisfied; it never
  reaches the drift check.

Comparability is a precondition, not a footnote: a run only counts
against its baseline if the post count, image sizes, latency, renderer
and frame budget all match, because those choose which code path runs.
A baseline recorded against 400 posts with 400px avatars says nothing
about a 80-post run with 96px ones, and reporting that run as a 5x
improvement would be worse than reporting nothing.

Usage::

    BENCH_GATE=1 BENCH_SCENARIO=scroll ./bench/v2/run.sh    # via run.sh
    python3 bench/v2/gate.py /path/to/rundir scroll         # directly
    python3 bench/v2/gate.py --record /path/to/rundir scroll

Exit status: 0 in band, 1 regressed, 2 not comparable / no baseline.
"""

import datetime
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, 'baseline.json')

# Conditions that change which code runs. All must match for a run to be
# compared with its baseline. 'git' is deliberately not here: it changes
# every commit, and comparing it would make the gate useless. A dirty
# tree is recorded and warned about instead.
COMPARED_CONDITIONS = ('posts', 'avatar_px', 'media_px', 'image_format',
                       'latency_ms', 'renderer', 'frame_budget_ms',
                       'soak_pages', 'soak_steps', 'scroll_steps',
                       'stream_events')

# Recorded and reported, never compared.
ADVISORY_CONDITIONS = ('git', 'dirty', 'scenario')

# --- the contract -----------------------------------------------------
#
# direction: 'lower'  -> a rise is a regression
#           'higher' -> a fall is a regression
#           'zero'   -> must stay exactly 0 (a leak; no tolerance)
# tolerance: relative drift forgiven
# slack:    absolute drift forgiven, for metrics whose scale is small
#           enough that a percentage is meaningless

_MS, _COUNT = 0.0, 2.0

BUDGETS = {
    # cost, in ms
    'cpu_ms':               {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'main_ms':              {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'load_ms':              {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'scroll_down_ms':       {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'scroll_up_ms':         {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'stream_ms':            {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'teardown_ms':          {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'first_card_ms':        {'direction': 'lower', 'tolerance': 0.25, 'slack': _MS},
    'stall_p99_ms':         {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    'stall_max_ms':         {'direction': 'lower', 'tolerance': 0.20, 'slack': _MS},
    # latency a reader sees, in ms
    'popin_ms_median':      {'direction': 'lower', 'tolerance': 0.25, 'slack': _MS},
    'popin_ms_p90':         {'direction': 'lower', 'tolerance': 0.25, 'slack': _MS},
    # memory
    'retained_mb':          {'direction': 'lower', 'tolerance': 0.20, 'slack': 5.0},
    # counts: a few are genuinely noisy at small magnitudes
    'stalls_over_budget':   {'direction': 'lower', 'tolerance': 0.20, 'slack': _COUNT},
    'frames_with_empty_shell': {'direction': 'lower', 'tolerance': 0.20,
                                'slack': _COUNT * 10},
    'duplicate_requests':   {'direction': 'lower', 'tolerance': 0.20, 'slack': _COUNT},
    # leaks: no tolerance at all. MEMORY-LEAKS.md.
    'live_rows':            {'direction': 'zero'},
    'live_items':           {'direction': 'zero'},
    # A fetcher key still outstanding once the loop is quiet is a request
    # that will never complete: that image can never be fetched again, and
    # every later row queues another waiter on the dead key. It is the
    # signature of the stranded-_inflight bug, and no widget count shows
    # it -- which is why the harness had no way to see that class of bug.
    'inflight_requests':    {'direction': 'zero'},
}

# Floors: proof that the run exercised what it claims to. Satisfied ->
# done, never compared for drift. See classify() in timeline_bench2.py for
# why this is per source and not an aggregate.
FLOORS = {
    'decode_avatar_slow':   {'fraction': 0.8},
}


def median_of(runs, key):
    values = [r[key] for r in runs
              if isinstance(r.get(key), (int, float))
              and not isinstance(r.get(key), bool)]
    return statistics.median(values) if values else None


def load_runs(workdir):
    runs = []
    for name in sorted(os.listdir(workdir)):
        if not (name.startswith('run') and name.endswith('.json')):
            continue
        with open(os.path.join(workdir, name)) as handle:
            text = handle.read().strip()
        if text.startswith('{'):
            runs.append(json.loads(text.splitlines()[-1]))
    return runs


def check_comparable(entry, runs, scenario):
    """Refuse to judge a run that did not do the baseline's work."""
    want = entry.get('conditions', {})
    got = runs[0].get('conditions', {})
    if not want:
        return 'baseline predates condition recording; re-record it'
    problems = []
    for key in COMPARED_CONDITIONS:
        if key not in got:
            problems.append('%s missing from this run' % key)
        elif key in want and got[key] != want[key]:
            problems.append('%s=%r but the baseline recorded %r'
                            % (key, got[key], want[key]))
    if problems:
        print('gate: %s @ %s is not comparable to its baseline:'
              % (scenario, got.get('posts')), file=sys.stderr)
        for line in problems:
            print('  - %s' % line, file=sys.stderr)
        print('  Re-record with the same conditions to start a new'
              ' baseline:\n    BENCH_RECORD=1 BENCH_SCENARIO=%s ./bench/v2/run.sh'
              % scenario, file=sys.stderr)
        return ', '.join(problems)
    if got.get('dirty'):
        print('gate: warning -- the working tree was dirty when this ran'
              ' (%s), so the figures include uncommitted changes.'
              % got.get('git', '?'), file=sys.stderr)
    return None


def record(runs, scenario):
    """Write this run into baseline.json as the new reference.

    Re-recording is a normal documented step rather than a hand edit of
    the JSON, so a changed number is always accompanied by the conditions
    that produced it.
    """
    try:
        with open(BASELINE) as handle:
            baseline = json.load(handle)
    except FileNotFoundError:
        baseline = {
            'note': 'Reference figures for bench/v2, generated with'
                    ' BENCH_RECORD=1. See bench/v2/README.md.',
            'machine_note': 'Absolute values are machine-specific. Compare'
                            ' only figures recorded on the same machine;'
                            ' the tolerances in gate.py are the contract.',
            'scenarios': {},
        }
    conditions = runs[0].get('conditions', {})
    baseline.setdefault('scenarios', {})
    budgets = {}
    for key, spec in BUDGETS.items():
        value = median_of(runs, key)
        if value is None:
            continue
        if spec['direction'] == 'zero':
            budgets[key] = {'value': value, 'max': 0}
        else:
            budgets[key] = {
                'value': value,
                'direction': spec['direction'],
                'tolerance': spec['tolerance'],
                'slack': spec['slack'],
            }
    for key, spec in FLOORS.items():
        value = median_of(runs, key)
        if value is None:
            continue
        budgets[key] = {'value': value,
                        'floor': max(1.0, value * spec['fraction'])}
    baseline['scenarios'][scenario] = {
        'conditions': {k: conditions.get(k) for k in COMPARED_CONDITIONS},
        'recorded_on': datetime.date.today().isoformat(),
        'recorded_at': {
            'git': conditions.get('git'),
            'dirty': conditions.get('dirty'),
            'runs': len(runs),
        },
        'budgets': budgets,
    }
    with open(BASELINE, 'w') as handle:
        json.dump(baseline, handle, indent=2, sort_keys=True)
        handle.write('\n')
    print('gate: recorded %s -> %s (%d gated metrics)'
          % (scenario, os.path.basename(BASELINE), len(budgets)))
    print('gate: conditions %s' % json.dumps(
        {k: conditions.get(k) for k in COMPARED_CONDITIONS}, sort_keys=True))


def compare(entry, runs, scenario):
    failures = []
    recorded = entry.get('recorded_on', '?')
    print('gate: %s (baseline recorded %s)'
          % (scenario, recorded))
    print('gate: conditions %s'
          % json.dumps(entry.get('conditions', {}), sort_keys=True))

    for key, budget in sorted(entry.get('budgets', {}).items()):
        current = median_of(runs, key)
        base = budget.get('value')
        if current is None or base is None:
            print('  %-24s baseline %-10s current %-10s  not measured'
                  % (key, base, current))
            continue

        if 'max' in budget:                      # the zero gates
            verdict = 'ok'
            if current > budget['max']:
                verdict = 'FAILED'
                failures.append('%s = %s, must be %s'
                                % (key, current, budget['max']))
            print('  %-24s %-10s -> %-10s  must be %s   %s'
                  % (key, base, current, budget['max'], verdict))
            continue

        if 'floor' in budget:                    # proof-of-regime metrics
            verdict = 'ok'
            if current < budget['floor']:
                verdict = 'FAILED'
                failures.append(
                    '%s = %s is below floor %s, so this run did not exercise'
                    ' the code path its baseline describes'
                    % (key, current, budget['floor']))
            print('  %-24s %-10s -> %-10s  floor %-8s %s'
                  % (key, base, current, budget['floor'], verdict))
            continue                            # floors never drift-check

        direction = budget.get('direction')
        tolerance = float(budget.get('tolerance', 0.20))
        slack = float(budget.get('slack', 0.0))
        verdict = 'ok'
        if direction == 'lower':
            allowed = base * (1.0 + tolerance) + slack
            if current > allowed:
                verdict = 'REGRESSED'
                failures.append('%s = %s, above the %s allowance for %s'
                                % (key, current, round(allowed, 1), base))
        elif direction == 'higher':
            allowed = base * (1.0 - tolerance) - slack
            if current < allowed:
                verdict = 'REGRESSED'
                failures.append('%s = %s, below the %s allowance for %s'
                                % (key, current, round(allowed, 1), base))
        else:
            # A budget with no direction recorded is not a budget.
            verdict = 'NO DIRECTION (not judged)'
        delta = current - base
        shown = ('%+.1f%%' % (100.0 * delta / base)) if base else \
                ('%+.0f (baseline 0)' % delta)
        print('  %-24s %-10s -> %-10s  %-16s %s'
              % (key, base, current, shown, verdict))

    if failures:
        print('\ngate: FAILED (%d)' % len(failures), file=sys.stderr)
        for line in failures:
            print('  - %s' % line, file=sys.stderr)
        print('\nIf the change was deliberate, re-record the baseline:\n'
              '  BENCH_RECORD=1 BENCH_SCENARIO=%s ./bench/v2/run.sh'
              % scenario, file=sys.stderr)
        return 1
    print('\ngate: passed')
    return 0


def main():
    argv = [a for a in sys.argv[1:]]
    do_record = '--record' in argv
    argv = [a for a in argv if a != '--record']
    if not argv:
        raise SystemExit(__doc__)
    workdir = argv[0]
    runs = load_runs(workdir)
    if not runs:
        print('gate: no run output in %s' % workdir, file=sys.stderr)
        return 2
    scenario = argv[1] if len(argv) > 1 else runs[0].get('scenario')

    if do_record:
        record(runs, scenario)
        return 0

    with open(BASELINE) as handle:
        baseline = json.load(handle)
    entry = baseline.get('scenarios', {}).get(scenario)
    if entry is None:
        print('gate: no baseline for scenario %r -- nothing to compare, so'
              ' nothing is proven' % scenario, file=sys.stderr)
        return 2
    problem = check_comparable(entry, runs, scenario)
    if problem:
        return 2
    return compare(entry, runs, scenario)


if __name__ == '__main__':
    sys.exit(main())
