# SPDX-License-Identifier: GPL-3.0-or-later
"""Leak check for inline playback: engine cycles in one process.

Creates, plays for about 0.5 s and releases an inline engine
(``Engine(gl=False)`` under an ``EngineStream``, exactly what
``InlinePlayer._start`` builds) N times (BENCH_CYCLES, default 10), reading
RSS after each release once the deferred drop and ``malloc_trim`` have
run. The first cycle pays for loading GStreamer and its plugins, which stay
mapped after the engine is gone and are not a leak; a leak shows in every
later cycle. Prints one JSON line:

    inline_cycle_growth_mb  mean RSS growth per cycle over cycles 2..N
    inline_cycle_first_mb   the first cycle's growth (reported, not gated)
    inline_cycle_frames     cycles that decoded at least one frame

Served by bench/fixture_server.py (an Ogg/Theora clip with Range answers).
"""

import ctypes
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CYCLES = int(os.environ.get('BENCH_CYCLES', '10'))
PLAY_S = float(os.environ.get('BENCH_CYCLE_PLAY_S', '0.5'))

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk  # noqa: E402


def rss_kb():
    with open('/proc/self/statm') as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf('SC_PAGE_SIZE') // 1024


def pump(seconds=None, until=None, limit=5.0):
    """Turn the main loop for *seconds*, or until *until*() (bounded)."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + (seconds if seconds is not None else limit)
    while time.monotonic() < end:
        if not ctx.iteration(False):
            if until is not None and until():
                return True
            time.sleep(0.005)
    return until() if until is not None else True


def main():
    if not Gtk.init_check():
        raise SystemExit('inline_cycles: no display')
    anchor = datetime.datetime.now(datetime.timezone.utc).replace(
        second=0, microsecond=0, tzinfo=None)
    server = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'fixture_server.py'),
         '5', anchor.isoformat()], stdout=subprocess.PIPE, text=True)
    try:
        port = int(server.stdout.readline())
        url = 'http://127.0.0.1:%d/clip/x.ogg' % port
        print(json.dumps(run(url)))
    finally:
        server.kill()
        server.wait()


def run(url):
    from src import mediaengine
    from src.mediastream import EngineStream

    libc = ctypes.CDLL('libc.so.6')
    pump(0.3)
    samples = []
    shown = 0
    start = rss_kb()
    for _ in range(CYCLES):
        engine = mediaengine.Engine(user_agent='babble-bench', gl=False)
        stream = EngineStream(engine)
        frames = []
        stream.connect('invalidate-contents', lambda *_a: frames.append(1))
        engine.load(url)
        pump(until=stream.is_prepared, limit=3.0)
        stream.set_loop(True)
        stream.set_muted(True)
        stream.play()
        pump(PLAY_S)
        shown += 1 if frames else 0
        stream.pause()
        stream.detach()
        engine.stop()
        engine.release()
        del stream, engine
        pump(until=lambda: not mediaengine._releasing, limit=3.0)
        pump(0.1)
        libc.malloc_trim(0)
        pump(0.1)
        samples.append(rss_kb())
    print('inline cycles rss_mb: %s' % [round(s / 1024.0, 1) for s in samples],
          file=sys.stderr)
    first = (samples[0] - start) / 1024.0
    later = (samples[-1] - samples[0]) / 1024.0 / max(1, len(samples) - 1)
    return {
        'inline_cycle_growth_mb': round(later, 2),
        'inline_cycle_first_mb': round(first, 1),
        'inline_cycle_frames': shown,
    }


if __name__ == '__main__':
    main()
