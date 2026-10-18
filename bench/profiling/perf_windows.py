# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread CPU, from perf, inside windows found in sysprof's marks.

Usage: perf_windows.py RUN_PREFIX [RUN_PREFIX ...]

Each RUN_PREFIX names a pair written by controlled-run.sh: PREFIX.syscap
(GTK/GLib marks) and PREFIX.perf.data (stacks, recorded with
``-k CLOCK_MONOTONIC`` so its timestamps share the marks' clock). Windows
are the ones ``window_cpu.windows`` defines: slow layout frames, HTTP
completions over 5 ms, idle callbacks over budget. Inside each, samples of
Babble's main thread are attributed to their innermost Babble Python
function and to the C functions on the stack.

perf's own names are used where it has them; stripped libraries' frames
are resolved with eu-addr2line, so set
DEBUGINFOD_URLS=https://debuginfod.archlinux.org (it reads sysprof's and
debuginfod's shared cache).
"""
import bisect
import collections
import os
import re
import subprocess
import sys

import window_cpu

FRAME = re.compile(r'^\s+([0-9a-f]+) (.*?) \((.*)\)$')
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Frames on every stack; as "inclusive" hot spots they say nothing.
PLUMBING = window_cpu.PLUMBING + (
    '_Py', 'Py', 'py_', 'pygi', '_pygi', 'pyg', 'method_', 'type_call',
    'vectorcall', 'slot_', 'cfunction', 'call_', '[unknown]',
    'surface_layout_cb', 'gdk_surface', 'g_object_new', 'g_type_create',
    'object_set_property', '_invoke', 'complete_in_idle_cb', 'g_task')


def perf_stacks(path, pid):
    out = subprocess.run(['perf', 'script', '-i', path, '--tid', str(pid),
                          '-F', 'time,ip,sym,dsoff', '--no-inline'],
                         capture_output=True, text=True).stdout
    stacks, cur = [], None
    for line in out.splitlines():
        if line and not line[0].isspace():
            cur = []
            stacks.append((int(float(line.split(':')[0]) * 1e9), cur))
            continue
        m = FRAME.match(line)
        if m and cur is not None:
            sym, where = m.group(2), m.group(3)
            path_, _, off = where.rpartition('+0x')
            try:
                offset = int(off, 16)
            except ValueError:          # "[unknown]": no mapping at all
                offset = None
            cur.append([sym, path_ or where, offset])
    want = collections.defaultdict(set)
    for _, s in stacks:
        for sym, p, off in s:
            if sym == '[unknown]' and off is not None and os.path.exists(p):
                want[p].add(off)
    names = {}
    for p, offs in want.items():
        offs = sorted(offs)
        for i in range(0, len(offs), 3000):
            chunk = offs[i:i + 3000]
            res = subprocess.run(['eu-addr2line', '-f', '-e', p]
                                 + [hex(o) for o in chunk],
                                 capture_output=True, text=True).stdout.split('\n')
            for j, off in enumerate(chunk):
                fn = res[2 * j] if 2 * j < len(res) else '??'
                if fn != '??':
                    names[(p, off)] = fn.split(' inlined')[0]
    result = []
    for t, s in stacks:
        frames = []
        for sym, p, off in s:
            if sym == '[unknown]':
                sym = names.get((p, off), sym)
            frames.append(sym.split('+0x')[0].replace('py::', '')
                          .replace(REPO + '/', ''))
        result.append((t, frames))
    return result


def main(prefixes):
    agg = collections.defaultdict(lambda: {
        'n': 0, 'win': 0, 'ms': 0.0, 'py': collections.Counter(),
        'c': collections.Counter(), 'self': collections.Counter()})
    for prefix in prefixes:
        pid, _maps, marks, _samples = window_cpu.collect(prefix + '.syscap')
        stacks = perf_stacks(prefix + '.perf.data', pid)
        if not stacks:
            print('WARNING: %s: no perf samples for pid %d' % (prefix, pid),
                  file=sys.stderr)
            continue
        times = [t for t, _ in stacks]
        for label, ws in window_cpu.windows(marks).items():
            a = agg[label]
            a['win'] += len(ws)
            a['ms'] += sum(d for _, d in ws) / 1e6
            for t, d in ws:
                lo, hi = bisect.bisect_left(times, t), bisect.bisect_left(times, t + d)
                for _, frames in stacks[lo:hi]:
                    a['n'] += 1
                    py = [f for f in frames if ':src/' in f]
                    a['py'][py[0] if py else '(no Babble Python on the stack)'] += 1
                    for f in set(frames):
                        if ':' not in f and not f.startswith(PLUMBING):
                            a['c'][f] += 1
                    if frames:
                        a['self'][frames[0]] += 1
    for label, a in agg.items():
        n = a['n']
        print('\n=== %s: %d windows, %.0f ms, %d main-thread samples'
              % (label, a['win'], a['ms'], n))
        if not n:
            continue
        print('  innermost Babble Python function:')
        for f, c in a['py'].most_common(12):
            print('    %5.1f%%  %s' % (100 * c / n, f[:95]))
        print('  C, inclusive:')
        for f, c in a['c'].most_common(22):
            print('    %5.1f%%  %s' % (100 * c / n, f[:95]))
        print('  self:')
        for f, c in a['self'].most_common(10):
            print('    %5.1f%%  %s' % (100 * c / n, f[:95]))


if __name__ == '__main__':
    main(sys.argv[1:])
