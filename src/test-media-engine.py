#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media engine: preroll, deferred release, and shutdown.

* preroll: ``load()`` reaches PAUSED and reports media info before any
  ``play``; playing produces frames;
* deferred release: the GstPlay is finalized only after STOPPED, every
  handler is disconnected, and ``_releasing`` empties;
* 24 load/release cycles neither crash nor grow the process;
* ``shutdown_all()`` with a playing engine returns after STOPPED.
"""

import faulthandler
import gc
import importlib.util
import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import time

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import mediaengine  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

faulthandler.dump_traceback_later(120, exit=True)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


def rss_mb():
    with open('/proc/self/status') as handle:
        for line in handle:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) // 1024


def wait(condition, timeout=10.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


class Owner:
    def __init__(self):
        self.events = []

    def _engine_event(self, name, *args):
        self.events.append(name)


window = Gtk.Window()
window.present()
URI = 'file://' + fixture.write_clip()

# Preroll, then play.
owner = Owner()
engine = mediaengine.Engine(owner)
engine.load(URI)
check('preroll reports the media info',
      wait(lambda: 'media-info' in owner.events), repr(owner.events))
check('preroll reaches PAUSED',
      wait(lambda: engine.state.value_nick == 'paused'), str(engine.state))
check('nothing plays during preroll', 'position' not in owner.events)
engine.set_audio(True, 0.0)
engine.start()
check('playing produces a frame',
      wait(lambda: 'frame' in owner.events), repr(owner.events))

# Deferred release: the GstPlay is finalized, handlers are gone.
finalized = []
engine.play.weak_ref(lambda *_: finalized.append(True))
handlers = list(engine._handlers)
engine.release()
check('release keeps the engine alive until STOPPED',
      engine in mediaengine._releasing and not finalized)
check('release drops the GstPlay',
      wait(lambda: (gc.collect() or True) and finalized),
      'finalized=%r' % finalized)
check('every handler is disconnected',
      not engine._handlers and len(handlers) >= 7)
check('the releasing set empties', engine not in mediaengine._releasing)
check('the engine let go of its parts',
      engine.play is None and engine.adapter is None
      and engine.sink is None and engine.paintable is None)
check('a playing engine is released once STOPPED arrives',
      engine.how == 'stopped', repr(engine.how))
check('release is safe twice', engine.release() is None)

# An engine that never loaded releases at once.
fresh = mediaengine.Engine()
fresh.release()
check('an engine that never loaded is dropped',
      wait(lambda: fresh.play is None))
check('... and says it never loaded', fresh.how == 'never-loaded',
      repr(fresh.how))


def started(uri=URI):
    owner = Owner()
    engine = mediaengine.Engine(owner)
    engine.load(uri)
    wait(lambda: engine.state.value_nick == 'paused', 5)
    return engine, owner


# stop(), load() and release() in one main-loop turn: the stale STOPPED of
# the first stop must not finish the release before the new load is down.
engine, owner = started()
engine.start()
engine.stop()
engine.load(URI)
check('a load during a stop waits for its STOPPED',
      engine._pending_uri == URI and engine._stopping)
engine.release()
check('stop, load, release in one turn finishes',
      wait(lambda: engine.play is None, 5))
check('... by STOPPED, not the timeout', engine.how == 'stopped',
      repr(engine.how))

# A load that waited runs once the stop is done.
engine, owner = started()
engine.start()
engine.stop()
engine.load(URI)
check('the deferred load runs after STOPPED',
      wait(lambda: engine.state.value_nick == 'paused'
           and engine._pending_uri is None, 5))
engine.release()
wait(lambda: engine.play is None, 5)
check('... and the engine then releases normally',
      engine.how in ('stopped', 'settled'), repr(engine.how))

# Release while a stop is in flight.
engine, owner = started()
engine.start()
engine.stop()
engine.release()
check('release while a stop is in flight finishes',
      wait(lambda: engine.play is None, 5))
check('... by STOPPED, not the timeout', engine.how == 'stopped',
      repr(engine.how))

# A load that errors before any state change still releases promptly.
engine, owner = started('file:///nonexistent/babble.ogg')
check('a bad source reports an error',
      wait(lambda: 'error' in owner.events, 5))
engine.release()
wait(lambda: engine.play is None, 5)
check('release after a failed load does not wait for the timeout',
      engine.how != 'timeout', repr(engine.how))

# Release after EOS of a non-looping clip: no timeout.
engine, owner = started()
engine.start()
check('the clip reaches its end',
      wait(lambda: 'end-of-stream' in owner.events, 10))
wait(lambda: engine.state.value_nick == 'stopped', 2)
t0 = time.monotonic()
engine.release()
wait(lambda: engine.play is None, 5)
check('release after EOS does not wait for the timeout (%.2f s, %s)'
      % (time.monotonic() - t0, engine.how),
      engine.how != 'timeout' and time.monotonic() - t0 < 0.9,
      repr(engine.how))

# Many cycles: no crash, no growth.
hows = []


def one_cycle():
    owner = Owner()
    engine = mediaengine.Engine(owner)
    engine.load(URI)
    engine.set_audio(True, 0.0)
    wait(lambda: engine.state.value_nick == 'paused', 5)
    engine.start()
    wait(lambda: 'frame' in owner.events, 5)
    engine.release()
    hows.append(engine)


samples = []
for _cycle in range(24):
    one_cycle()
    wait(lambda: not mediaengine._releasing, 5)
    gc.collect()
    samples.append(rss_mb())
check('24 load/release cycles leave nothing releasing',
      not mediaengine._releasing and not mediaengine._all,
      '%d left' % len(mediaengine._all))
# Peak against peak: the allocator's arenas make RSS flip between two
# levels (489/524 MB on CI), so endpoint-to-endpoint read 35 MB of
# "growth" where there was none. A leak raises the peak itself.
growth = max(samples[-10:]) - max(samples[-20:-10])
check('RSS plateaus: peak grew %d MB over the last 10 cycles' % growth,
      growth <= 20, 'samples %r' % samples)
check('no cycle hit the release timeout',
      all(e.how != 'timeout' for e in hows),
      repr([e.how for e in hows if e.how == 'timeout']))

# shutdown_all with a playing engine.
owner = Owner()
engine = mediaengine.Engine(owner)
engine.load(URI)
engine.set_audio(True, 0.0)
wait(lambda: engine.state.value_nick == 'paused', 5)
engine.start()
wait(lambda: 'frame' in owner.events, 5)
t0 = time.monotonic()
mediaengine.shutdown_all()
elapsed = time.monotonic() - t0
check('shutdown_all returns within its bound (%.2f s)' % elapsed,
      elapsed < 2.0)
check('shutdown_all drops the playing engine',
      engine.play is None and not mediaengine._all
      and not mediaengine._releasing)
check('... by STOPPED, not the timeout', engine.how == 'stopped',
      repr(engine.how))

faulthandler.cancel_dump_traceback_later()
if failures:
    print('\n%d media engine check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media engine checks passed')
