# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Babble from the build tree and watch its memory.

    python3 bench/memwatch.py [--interval 2] [--probe-every 0] [--trim]
                              [--tracemalloc 0] [--chain TYPE] [--out DIR]

Every --interval seconds it prints CPU, RSS, peak RSS and threads, and
appends them to DIR/samples.csv. Press Enter (or wait --probe-every
seconds) to run bench/memprobe.py inside the app through
``sys.remote_exec``: object counts, caches, textures, heap and, when
asked, tracemalloc growth and referrer chains, appended to
DIR/probes.jsonl and summarized against the previous probe.

The app is launched as this process's child because Yama's default
``kernel.yama.ptrace_scope = 1`` only lets a process attach to its own
descendants; an already running Babble cannot be probed.

The probe runs when the app's main thread next executes Python. An idle
GTK app can stay in C indefinitely, so the app is started with a no-op
GLib timeout that gives it a Python tick every 250 ms. Babble is
single-instance: quit any running copy first, or this launch hands off
to it and exits.
See MEMORY-LEAKS.md for the protocol this serves.
"""

import argparse
import json
import os
import selectors
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROBE = os.path.join(HERE, 'memprobe.py')


def parse():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--interval', type=float, default=2.0,
                        help='seconds between samples (default 2)')
    parser.add_argument('--probe-every', type=float, default=0.0,
                        help='also probe every N seconds (default: only '
                             'on Enter)')
    parser.add_argument('--trim', action='store_true',
                        help='call malloc_trim(0) in each probe and report '
                             'what it released')
    parser.add_argument('--tracemalloc', type=int, default=0, metavar='N',
                        help='start the app with tracemalloc keeping N '
                             'frames (slow; 0 = off)')
    parser.add_argument('--chain', metavar='TYPE',
                        help='in each probe, print referrer chains of '
                             'live instances of TYPE (e.g. StatusRow)')
    parser.add_argument('--out', default=None,
                        help='output directory (default: a new '
                             'memwatch-<time> under the build dir)')
    parser.add_argument('--build', default=os.path.join(ROOT, 'build'),
                        help='meson build directory (default ./build)')
    return parser.parse_args()


def proc_sample(pid):
    with open('/proc/%d/stat' % pid) as handle:
        fields = handle.read().rsplit(')', 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])
    status = {}
    with open('/proc/%d/status' % pid) as handle:
        for line in handle:
            key, _, value = line.partition(':')
            if key in ('VmRSS', 'VmHWM', 'Threads'):
                status[key] = int(value.split()[0])
    return ticks, status


def summarize(report, previous):
    if 'error' in report:
        print('probe %d failed:\n%s' % (report['id'], report['error']))
        return
    print('-- probe %d: rss %s MB (peak %s), %s threads, gc collected %d'
          % (report['id'], report['rss_mb'], report['peak_mb'],
             report['threads'], report['gc_collected']))
    old = previous or {}

    def delta(section, limit=12):
        now, before = report.get(section) or {}, old.get(section) or {}
        rows = []
        for key in set(now) | set(before):
            a, b = before.get(key, 0), now.get(key, 0)
            if previous is None or a != b:
                rows.append((b - a if previous else b, key, a, b))
        rows.sort(key=lambda row: -abs(row[0]))
        for change, key, a, b in rows[:limit]:
            if previous is None:
                print('   %6d  %s' % (b, key))
            else:
                print('   %+6d  %s  (%d -> %d)' % (change, key, a, b))

    print(' babble objects%s:' % (' changed' if previous else ''))
    delta('babble_types')
    if report.get('widgets_not_in_a_window'):
        print(' babble widgets alive but not in a window%s:'
              % (' changed' if previous else ''))
        delta('widgets_not_in_a_window')
    print(' module/class containers%s:' % (' changed' if previous else ''))
    delta('containers')
    if report.get('list_models'):
        print(' list models: %s' % ', '.join(
            '%s %s' % (name.rsplit('.', 1)[1], sizes)
            for name, sizes in report['list_models'].items()))
    print(' textures with a wrapper: %s (~%s MB of pixels)'
          % (report.get('textures'), report.get('texture_pixels_mb')))
    heap = report.get('malloc')
    if heap:
        print(' malloc: heap %(heap_mb)s MB, in use %(heap_in_use_mb)s, '
              'free %(heap_free_mb)s, mmapped %(mmapped_mb)s' % heap)
    if 'trim_released_mb' in report:
        print(' malloc_trim(0) released %s MB' % report['trim_released_mb'])
    if 'traced_mb' in report:
        print(' tracemalloc: %s MB traced' % report['traced_mb'])
        for entry in report.get('traced_growth', [])[:8]:
            print('   +%s KB in %+d blocks at %s'
                  % (entry['kb'], entry['blocks'],
                     ' <- '.join(entry['where'][:2])))
    chain = report.get('chain')
    if chain:
        print(' %d %s not in a window' % (chain['candidates'],
                                          chain['type']))
        for steps in chain['chains']:
            print('   held by:')
            for step in steps:
                print('     %s' % step)


def main():
    args = parse()
    launcher = os.path.join(args.build, 'src', 'babble')
    if not os.path.exists(launcher):
        sys.exit('%s not found: run meson setup/compile first' % launcher)
    out = args.out or os.path.join(
        args.build, 'memwatch-%s' % time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(out, exist_ok=True)
    probes_path = os.path.join(out, 'probes.jsonl')

    env = dict(os.environ)
    env.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(args.build, 'data'))
    if args.tracemalloc:
        env['PYTHONTRACEMALLOC'] = str(args.tracemalloc)
    # Same interpreter: remote_exec needs a matching CPython version. The
    # probe runs when the main thread next executes Python, and an idle
    # GTK app can stay in C indefinitely, so a no-op GLib timeout gives it
    # a Python tick four times a second.
    wrapper = ('import runpy, sys\n'
               'from gi.repository import GLib\n'
               'GLib.timeout_add(250, lambda: True)\n'
               'sys.argv = [%r]\n'
               'runpy.run_path(%r, run_name="__main__")\n'
               % (launcher, launcher))
    child = subprocess.Popen([sys.executable, '-c', wrapper], env=env)
    started = time.monotonic()
    print('babble pid %d; output in %s' % (child.pid, out))
    print('Enter = probe, Ctrl+C = stop')

    with open(PROBE) as handle:
        probe_source = handle.read()
    selector = selectors.DefaultSelector()
    interactive = sys.stdin is not None and not sys.stdin.closed
    if interactive:
        try:
            selector.register(sys.stdin, selectors.EVENT_READ)
        except (ValueError, PermissionError):
            interactive = False

    hz = os.sysconf('SC_CLK_TCK')
    samples = open(os.path.join(out, 'samples.csv'), 'w')
    samples.write('time,cpu_percent,rss_mb,peak_mb,threads\n')
    print('%-8s %6s %8s %8s %4s' % ('time', 'cpu%', 'rss_mb', 'peak_mb',
                                    'thr'))
    previous_ticks, _ = proc_sample(child.pid)
    previous_time = time.monotonic()
    next_probe = (time.monotonic() + args.probe_every
                  if args.probe_every else None)
    probe_id = 0
    pending = None
    last_report = None
    try:
        while child.poll() is None:
            wanted = False
            for _key, _mask in selector.select(timeout=args.interval):
                if not sys.stdin.readline():
                    selector.unregister(sys.stdin)
                wanted = True
            now = time.monotonic()
            if now - previous_time >= args.interval:
                try:
                    ticks, status = proc_sample(child.pid)
                except FileNotFoundError:
                    break
                cpu = (ticks - previous_ticks) * 100.0 / hz \
                    / (now - previous_time)
                previous_ticks, previous_time = ticks, now
                row = (time.strftime('%H:%M:%S'), round(cpu, 1),
                       round(status['VmRSS'] / 1024, 1),
                       round(status['VmHWM'] / 1024, 1), status['Threads'])
                print('%-8s %6.1f %8.1f %8.1f %4d' % row)
                samples.write(','.join(str(v) for v in row) + '\n')
                samples.flush()
            if next_probe is not None and now >= next_probe:
                wanted = True
                next_probe = now + args.probe_every
            if wanted and pending is None:
                probe_id += 1
                options = {'id': probe_id, 'out': probes_path,
                           'trim': args.trim, 'chain': args.chain}
                # A fresh file per probe: remote_exec reads it later.
                script = os.path.join(out, 'probe-%03d.py' % probe_id)
                with open(script, 'w') as handle:
                    handle.write('OPTIONS = %r\n' % options + probe_source)
                sys.remote_exec(child.pid, script)
                pending = (probe_id, now)
                print('probe %d sent' % probe_id)
            if pending is not None:
                report = None
                if os.path.exists(probes_path):
                    with open(probes_path) as handle:
                        for line in handle:
                            entry = json.loads(line)
                            if entry.get('id') == pending[0]:
                                report = entry
                if report is not None:
                    summarize(report, last_report)
                    if 'error' not in report:
                        last_report = report
                    pending = None
                elif now - pending[1] > 15:
                    print('probe %d still pending: the app has not run '
                          'Python since it was sent' % pending[0])
                    pending = (pending[0], now)
    except KeyboardInterrupt:
        pass
    finally:
        samples.close()
        if child.poll() is None:
            child.terminate()
            child.wait(10)
    print('babble exited (%s); samples and probes in %s'
          % (child.returncode, out))
    if child.returncode == 0 and time.monotonic() - started < 10:
        print('It exited at once: Babble is single-instance, so an already '
              'running copy took this launch. Quit it and run again.')


if __name__ == '__main__':
    main()
