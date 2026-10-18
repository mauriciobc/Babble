# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for bench/v2/gate.py.

The gate is the only thing standing between a performance regression and
the commit log, and every bug it has had was in its comparison arithmetic
rather than in the benchmark. These cases pin the arithmetic down, using
synthetic runs so they are fast and deterministic.

Run it directly::

    python3 bench/v2/test-gate.py

It follows the project's test conventions (ok - / FAIL - lines, exit 77
for a skip) so it can be registered in src/meson.build as a normal suite.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gate  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))
        FAILURES.append(name)


def conditions(**over):
    base = {key: None for key in gate.COMPARED_CONDITIONS}
    base.update({'posts': 100, 'avatar_px': '400', 'media_px': '1600x1200',
                 'image_format': 'jpeg', 'latency_ms': 0.0,
                 'renderer': 'cairo', 'frame_budget_ms': 16.67,
                 'scroll_steps': 24, 'stream_events': 12, 'soak_pages': 3,
                 'soak_steps': 40})
    base.update(over)
    return base


def run(**metrics):
    base = {'scenario': 'scroll', 'posts': 100, 'conditions': conditions()}
    base.update(metrics)
    return base


def build(budgets, cond=None):
    return {'scenarios': {'scroll': {
        'conditions': cond or conditions(),
        'recorded_on': '2026-01-01',
        'recorded_at': {'git': 'deadbee', 'dirty': False, 'runs': 3},
        'budgets': budgets}}}


def write_baseline(entry):
    gate.BASELINE = os.path.join(tempfile.mkdtemp(), 'baseline.json')
    with open(gate.BASELINE, 'w') as handle:
        json.dump(entry, handle)


def write_runs(runs):
    work = tempfile.mkdtemp()
    for i, r in enumerate(runs, 1):
        with open(os.path.join(work, 'run%d.json' % i), 'w') as handle:
            handle.write(json.dumps(r))
    return work


def verdict(entry, runs):
    """Run the comparison, returning (exit_code, printed output).

    Output is captured with contextlib.redirect_* rather than by
    assigning sys.stdout/sys.stderr: swapping the globals leaves the
    interpreter's buffers attached to the wrong object, and the whole run
    then exits silently. The gate reports its reasons as text rather than
    a return value, so the text is what gets asserted on.
    """
    write_baseline(entry)
    work = write_runs(runs)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), \
                contextlib.redirect_stderr(sink):
            problem = gate.check_comparable(entry['scenarios']['scroll'],
                                            runs, 'scroll')
            if problem:
                return 2, sink.getvalue()
            code = gate.compare(entry['scenarios']['scroll'], runs, 'scroll')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return code, sink.getvalue()


# --- the bug that started this: a zero baseline must not divide --------

entry = build({'duplicate_requests': {'value': 0, 'direction': 'lower',
                                      'tolerance': 0.20, 'slack': 2.0},
               'stalls_over_budget': {'value': 0, 'direction': 'lower',
                                      'tolerance': 0.20, 'slack': 2.0}})
code, _ = verdict(entry, [run(duplicate_requests=0, stalls_over_budget=0)])
check('zero baseline with zero current passes (was inf% regression)', code == 0)

code, _ = verdict(entry, [run(duplicate_requests=2, stalls_over_budget=0)])
check('zero baseline tolerates slack up to the allowance', code == 0)

code, fails = verdict(entry, [run(duplicate_requests=3, stalls_over_budget=0)])
check('zero baseline still fails past its slack', code == 1,
      'got exit %s' % code)

# --- direction is explicit, not assumed --------------------------------

entry = build({'frames': {'value': 100, 'direction': 'lower',
                          'tolerance': 0.20, 'slack': 0}})
code, _ = verdict(entry, [run(frames=90)])
check('a metric recorded lower-is-better accepts a fall', code == 0)

code, fails = verdict(entry, [run(frames=130)])
check('a metric recorded lower-is-better rejects a rise', code == 1)

entry = build({'duplicate_requests': {'value': 10, 'direction': 'higher',
                                      'tolerance': 0.20, 'slack': 0}})
code, _ = verdict(entry, [run(duplicate_requests=12)])
check('a higher-is-better metric accepts a rise', code == 0)
code, _ = verdict(entry, [run(duplicate_requests=4)])
check('a higher-is-better metric rejects a fall', code == 1)

# --- small counts need slack, not a percentage -------------------------

entry = build({'stalls_over_budget': {'value': 1, 'direction': 'lower',
                                      'tolerance': 0.20, 'slack': 2.0}})
code, _ = verdict(entry, [run(stalls_over_budget=3)])
check('a small count inside its slack passes even though 200% over',
      code == 0)
code, _ = verdict(entry, [run(stalls_over_budget=9)])
check('a small count past its slack fails', code == 1)

# --- a floor must not turn back into a ceiling -------------------------

entry = build({'decode_avatar_slow': {'value': 20, 'floor': 16.0}})
code, _ = verdict(entry, [run(decode_avatar_slow=40)])
check('a floor-only metric accepts a large rise', code == 0)

code, output = verdict(entry, [run(decode_avatar_slow=8)])
check('a floor-only metric rejects a fall below the floor', code == 1)
check('the floor failure explains the regime, not the number',
      'did not exercise' in output)

# --- the leak gates have no tolerance ----------------------------------

entry = build({'live_rows': {'value': 0, 'max': 0},
               'live_items': {'value': 0, 'max': 0}})
code, _ = verdict(entry, [run(live_rows=0, live_items=0)])
check('zero live rows passes', code == 0)
code, fails = verdict(entry, [run(live_rows=1, live_items=0)])
check('a single surviving row fails the leak gate', code == 1)

# --- comparability ------------------------------------------------------

entry = build({'cpu_ms': {'value': 1000, 'direction': 'lower',
                          'tolerance': 0.20, 'slack': 0.0}})
with contextlib.redirect_stdout(io.StringIO()), \
        contextlib.redirect_stderr(io.StringIO()):
    problem = gate.check_comparable(
        entry['scenarios']['scroll'],
        [run(conditions=conditions(posts=80))], 'scroll')
    same = gate.check_comparable(
        entry['scenarios']['scroll'], [run()], 'scroll')
    avatars = gate.check_comparable(
        entry['scenarios']['scroll'],
        [run(conditions=conditions(avatar_px='96'))], 'scroll')
    renderer = gate.check_comparable(
        entry['scenarios']['scroll'],
        [run(conditions=conditions(renderer='ngl'))], 'scroll')
    latency = gate.check_comparable(
        entry['scenarios']['scroll'],
        [run(conditions=conditions(latency_ms=40.0))], 'scroll')
    fmt = gate.check_comparable(
        entry['scenarios']['scroll'],
        [run(conditions=conditions(image_format='png'))], 'scroll')
    no_conditions = gate.check_comparable(
        {'conditions': {}}, [run()], 'scroll')

check('a different post count is not comparable', bool(problem))
check('a different avatar size is not comparable', bool(avatars))
check('a different renderer is not comparable', bool(renderer))
check('a different network latency is not comparable', bool(latency))
check('a different image format is not comparable', bool(fmt))
check('identical conditions are comparable', same is None)
check('a baseline without conditions is refused, not assumed',
      bool(no_conditions))

# --- the recorded contract ---------------------------------------------

check('every gated metric declares a direction',
      all('direction' in spec or 'max' in spec
          for spec in gate.BUDGETS.values()))
check('the leak gates are present',
      gate.BUDGETS['live_rows']['direction'] == 'zero'
      and gate.BUDGETS['live_items']['direction'] == 'zero')
check('a stranded fetcher request is gated at zero',
      gate.BUDGETS.get('inflight_requests', {}).get('direction') == 'zero')
check('post count is among the compared conditions',
      'posts' in gate.COMPARED_CONDITIONS)
check('the revision is recorded but never compared',
      'git' in gate.ADVISORY_CONDITIONS
      and 'git' not in gate.COMPARED_CONDITIONS)
check('a floor-only metric is not also in the drift-checked set',
      not (set(gate.FLOORS) & set(gate.BUDGETS)))

if FAILURES:
    print('\n%d failure(s)' % len(FAILURES))
    sys.exit(1)
print('\nall gate tests passed')
