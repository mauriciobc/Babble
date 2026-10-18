# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline-focused report for one or more controlled sysprof captures.

Usage: timeline_report.py CAPTURE [CAPTURE ...]

Per run: frame times against the 144 Hz budget, what made slow frames
slow, main-loop dispatch latency by source (response handlers, card
population idles, frame clock, input), scroll handling, and long tasks.
Then the same figures side by side, with the spread across runs.
"""
import bisect
import collections
import statistics
import sys

import syscap

BUDGET = 1000 / 144          # ms, from the GTK fps counter (144 Hz panel)
LONG = 50                    # ms, a "long task"
PHASES = ('Create Vulkan pipeline', 'Validate CSS', 'Frameclock layout',
          'Widget render', 'Widget snapshot', 'Frameclock update',
          'Frameclock paint')
SKIP = {'g_main_context_iterate', 'g_main_context_iteration',
        'GSource.prepare', 'GSource.check', 'g_source_attach'}


def pct(values, q):
    if not values:
        return float('nan')
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))]


def source_kind(message):
    name = message.split(' ⇒')[0]
    if 'complete_in_idle_cb' in name or 'read_async' in name:
        return 'I/O completion (HTTP body read, then Python handler)'
    if name.startswith('GIdleSource'):
        return 'idle callback (card population, deferred work)'
    if 'gdk_frame_clock' in name:
        return 'frame clock'
    if 'Wayland Event' in name:
        return 'input / Wayland events'
    if name.startswith(('Soup', 'GSocket', 'GPollable', 'SoupMessage')):
        return 'libsoup / sockets'
    return 'other'


def load(path):
    procs, marks, counts = {}, [], collections.Counter()
    for kind, _cpu, pid, t, p in syscap.frames(path):
        if kind == 4:
            procs[pid] = syscap.cstr(p)
        elif kind == 10:
            counts[pid] += 1
            marks.append((pid, t, p))
    pid = max((p for p in counts if procs.get(p, '').startswith('python')),
              key=lambda p: counts[p])
    out = []
    for mpid, t, p in marks:
        if mpid == pid:
            d, g, n, m = syscap.mark(p)
            if n not in SKIP:
                out.append((t, d / 1e6, g, n, m))
    out.sort()
    return pid, out


def analyse(path):
    pid, marks = load(path)
    t0 = marks[0][0]
    span = (marks[-1][0] - t0) / 1e9
    starts = [m[0] for m in marks]
    cycles = [(t, d) for t, d, g, n, m in marks if n == 'Frameclock cycle']
    frame_ms = [d for _, d in cycles]

    causes = collections.Counter()
    for t, d in cycles:
        if d <= BUDGET:
            continue
        i = bisect.bisect_left(starts, t)
        inside = []
        while i < len(marks) and marks[i][0] < t + d * 1e6:
            inside.append(marks[i])
            i += 1
        cause = 'unattributed'
        for phase in PHASES:
            if any(x[3] == phase and x[1] > 0.4 * d for x in inside):
                cause = phase
                break
        causes[cause] += 1

    by_kind = collections.defaultdict(list)
    for _t, d, _g, n, m in marks:
        if n == 'GSource.dispatch':
            by_kind[source_kind(m)].append(d)
    scroll = [d for _t, d, _g, n, m in marks
              if n == 'Event' and m.startswith('scroll')]
    longs = [(t, d, m) for t, d, _g, n, m in marks
             if n == 'GSource.dispatch' and d >= LONG]
    return {
        'path': path, 'pid': pid, 'span': span,
        'frames': len(frame_ms),
        'frame_p50': pct(frame_ms, .5), 'frame_p95': pct(frame_ms, .95),
        'frame_p99': pct(frame_ms, .99), 'frame_max': max(frame_ms),
        'over_budget_pct': 100 * sum(f > BUDGET for f in frame_ms) / len(frame_ms),
        'causes': causes, 'by_kind': by_kind, 'scroll': scroll,
        'longs': longs, 't0': t0,
    }


def main(paths):
    runs = [analyse(p) for p in paths]
    for r in runs:
        print('\n=== %s  (pid %d, %.0f s, %d frames)'
              % (r['path'].rsplit('/', 1)[-1], r['pid'], r['span'], r['frames']))
        print('  frames: p50 %.2f  p95 %.2f  p99 %.2f  max %.1f ms;'
              ' over %.2f ms budget: %.2f%%'
              % (r['frame_p50'], r['frame_p95'], r['frame_p99'],
                 r['frame_max'], BUDGET, r['over_budget_pct']))
        total = sum(r['causes'].values())
        print('  slow frames by dominant phase:',
              ', '.join('%s %d (%.0f%%)' % (k, v, 100 * v / total)
                        for k, v in r['causes'].most_common()))
        print('  dispatch latency by source (ms):')
        for kind, ds in sorted(r['by_kind'].items(), key=lambda kv: -sum(kv[1])):
            print('    %-52s n=%6d p95=%6.2f p99=%7.2f max=%7.1f >%dms=%d total=%7.0f'
                  % (kind, len(ds), pct(ds, .95), pct(ds, .99), max(ds),
                     LONG, sum(d >= LONG for d in ds), sum(ds)))
        if r['scroll']:
            print('  scroll events: n=%d p95=%.2f max=%.1f ms'
                  % (len(r['scroll']), pct(r['scroll'], .95), max(r['scroll'])))
        print('  long tasks (>= %d ms): %d' % (LONG, len(r['longs'])))
        for t, d, m in sorted(r['longs'], key=lambda x: -x[1])[:8]:
            print('    t+%6.1fs %7.1f ms  %s' % ((t - r['t0']) / 1e9, d, m[:70]))

    if len(runs) > 1:
        print('\n=== across runs (median [min-max])')
        def row(label, values, fmt='%.2f'):
            print('  %-34s %s  [%s - %s]' % (label, fmt % statistics.median(values),
                                            fmt % min(values), fmt % max(values)))
        row('frame p95 (ms)', [r['frame_p95'] for r in runs])
        row('frame p99 (ms)', [r['frame_p99'] for r in runs])
        row('frames over budget (%)', [r['over_budget_pct'] for r in runs])
        row('long tasks per minute', [60 * len(r['longs']) / r['span'] for r in runs])
        kinds = set().union(*(r['by_kind'] for r in runs))
        for kind in sorted(kinds):
            vals = [pct(r['by_kind'][kind], .99) for r in runs
                    if r['by_kind'].get(kind)]
            if len(vals) == len(runs):
                row('p99 %s' % kind[:28], vals)


if __name__ == '__main__':
    main(sys.argv[1:])
