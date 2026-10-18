# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread CPU inside three kinds of timeline window, across captures.

Usage: window_cpu.py CAPTURE [CAPTURE ...]
"""
import bisect
import collections
import struct
import subprocess
import sys

import syscap

BUDGET = 1000 / 144
# Frames on every stack (main loop, signal and FFI plumbing): listing them
# as "inclusive" hot spots says nothing.
PLUMBING = ('g_main', 'g_signal_emit', 'signal_emit', 'closure_invoke',
            'accumulate', 'ffi_', 'g_closure', '_g_closure', 'g_application',
            '??', '_start', '__libc', 'main', 'Py_', 'pymain',
            'gdk_frame_clock', 'g_timeout', 'g_idle_dispatch', 'sysprof')


def collect(path):
    procs, counts = {}, collections.Counter()
    for kind, _c, pid, _t, p in syscap.frames(path):
        if kind == 4:
            procs[pid] = syscap.cstr(p)
        elif kind == 10:
            counts[pid] += 1
    pid = max((p for p in counts if procs.get(p, '').startswith('python')),
              key=lambda p: counts[p])
    maps, marks, samples = [], [], []
    for kind, _c, fpid, t, p in syscap.frames(path):
        if fpid != pid:
            continue
        if kind == 3:
            s, e, off, _i = struct.unpack_from('<QQQQ', p, 0)
            maps.append((s, e, off, syscap.cstr(p[32:])))
        elif kind == 10:
            d, g, n, m = syscap.mark(p)
            marks.append((t, d, g, n, m))
        elif kind == 2:
            tid, addrs = syscap.sample(p)
            if tid == pid:
                samples.append((t, addrs))
    maps.sort()
    marks.sort()
    samples.sort()
    return pid, maps, marks, samples


def windows(marks):
    out = collections.defaultdict(list)
    starts = [m[0] for m in marks]
    for t, d, _g, n, m in marks:
        if n == 'Frameclock cycle' and d / 1e6 > BUDGET:
            i = bisect.bisect_left(starts, t)
            layout = 0
            while i < len(marks) and marks[i][0] < t + d:
                if marks[i][3] == 'Frameclock layout':
                    layout = max(layout, marks[i][1])
                i += 1
            if layout > 0.4 * d:
                out['slow layout frames'].append((t, d))
        elif n == 'GSource.dispatch':
            if ('complete_in_idle_cb' in m or 'read_async' in m) and d / 1e6 > 5:
                out['HTTP completions > 5 ms'].append((t, d))
            elif m.startswith('GIdleSource') and d / 1e6 > BUDGET:
                out['idle callbacks > budget'].append((t, d))
    return out


def main(paths):
    agg = collections.defaultdict(
        lambda: [collections.Counter(), collections.Counter(), 0, 0, 0.0])
    for path in paths:
        pid, maps, marks, samples = collect(path)
        mstarts = [m[0] for m in maps]
        stimes = [s[0] for s in samples]
        names = {}

        def where(a, maps=maps, mstarts=mstarts):
            i = bisect.bisect_right(mstarts, a) - 1
            if i >= 0 and a < maps[i][1]:
                s, e, off, p = maps[i]
                return p, a - s + off
            return None, None

        wins = windows(marks)
        picked = collections.defaultdict(list)
        want = collections.defaultdict(set)
        for label, ws in wins.items():
            for t, d in ws:
                lo = bisect.bisect_left(stimes, t)
                hi = bisect.bisect_left(stimes, t + d)
                for _, addrs in samples[lo:hi]:
                    frames = []
                    for a in addrs:
                        if a >= 0xffff800000000000:
                            continue
                        p, off = where(a)
                        if p and p.startswith('/usr'):
                            frames.append((p, off))
                            want[p].add(off)
                    picked[label].append(frames)
        for p, offs in want.items():
            offs = sorted(offs)
            for i in range(0, len(offs), 3000):
                chunk = offs[i:i + 3000]
                argv = ['eu-addr2line', '-f', '-e', p] + [hex(o) for o in chunk]
                res = subprocess.run(argv, capture_output=True,
                                     text=True).stdout.split('\n')
                lib = p.rsplit('/', 1)[-1].split('.so')[0]
                for j, off in enumerate(chunk):
                    fn = res[2 * j] if 2 * j < len(res) else '??'
                    names[(p, off)] = '%s:%s' % (lib, fn.split(' inlined')[0])
        unresolved = sum(1 for _, addrs in samples
                         if addrs and addrs[-1] < 0xffff800000000000
                         and where(next((x for x in addrs
                                         if x < 0xffff800000000000), 0))[0] is None)
        if samples and unresolved / len(samples) > 0.05:
            # The innermost frame is unmapped: its self time would be
            # credited to the first resolvable caller instead.
            print('WARNING: %s: %.0f%% of samples have an unmapped innermost'
                  ' frame; self and inclusive figures are unreliable'
                  % (path, 100 * unresolved / len(samples)), file=sys.stderr)
        for label, stacks in picked.items():
            a = agg[label]
            a[2] += len(stacks)
            a[3] += len(wins[label])
            a[4] += sum(d for _, d in wins[label]) / 1e6
            for frames in stacks:
                if not frames:
                    continue
                a[0][names[frames[0]]] += 1
                for f in set(names[x] for x in frames):
                    a[1][f] += 1
    for label, (self_c, incl, n, nwin, ms) in agg.items():
        print('\n=== %s: %d windows, %.0f ms total, %d main-thread samples (all runs)'
              % (label, nwin, ms, n))
        if not n:
            continue
        print('  inclusive:')
        shown = 0
        for f, c in incl.most_common(400):
            fn = f.split(':', 1)[1]
            if fn.startswith(PLUMBING) or c / n < 0.04:
                continue
            print('    %5.1f%%  %s' % (100 * c / n, f[:95]))
            shown += 1
            if shown >= 28:
                break
        print('  self:')
        for f, c in self_c.most_common(10):
            print('    %5.1f%%  %s' % (100 * c / n, f[:95]))


if __name__ == '__main__':
    main(sys.argv[1:])
