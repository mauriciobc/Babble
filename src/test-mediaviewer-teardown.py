#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media viewer's open, play and close, over and over.

The shipping viewer on ``main`` crashed 4 times in about 415 open/close
cycles (SIGSEGV in ``g_type_check_instance_is_fundamentally_a`` under
``gst_message_new_custom``, a GstPlay freed while it was still alive).
The precise trigger is not identified; waiting for STOPPED removed it in
every variant tested. The crash is a race and cannot be asserted on
directly, so this drives the real path
-- ``present_media()``, the stream from a loopback server (no download,
no temporary copy), play,
``viewer.close()``, ``gc`` -- 50 times, alternating a video and a GIFV,
and checks what a correct teardown leaves behind:

* the viewer is disposed (a GObject weak ref fires);
* the stream is detached and not playing, and the child is gone;
* the engine was released, its GstPlay finalized and ``_releasing`` empty;
* moving between attachments reuses one engine.

Surviving the loop is the other half of the pass condition. The clip is
Theora in Ogg, which only GStreamer's base plugins decode, so the test
needs no decoder CI does not install.
"""

import faulthandler
import gc
import importlib.util
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
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

from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import mediaengine, mediaexport, mediaviewer  # noqa: E402
from src import sysstate  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

CYCLES = 50
# A hang is the failure this test cannot otherwise report: Meson kills it
# at its timeout with nothing printed. Dump every thread's stack well
# before that.
faulthandler.dump_traceback_later(240, exit=True)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


def wait(condition, timeout=10.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


# The connection is not metered and the machine not saving power, so a
# video downloads at once and a GIFV autoplays. The monitor is plain
# properties: the real one asks Gio.NetworkMonitor, which can hang on a CI
# runner.
class FakeMonitor(GObject.Object):
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


sysstate._monitor = FakeMonitor()


def stray_dirs():
    root = GLib.get_tmp_dir()
    return sorted(set(n for n in os.listdir(root)
                      if n.startswith('babble-media-')) - BEFORE)


BEFORE = set()
# The viewer streams: a download here would be a regression.
downloads = []
mediaexport.download = lambda *args, **kwargs: downloads.append(args)

clip = fixture.write_clip()
BEFORE.update(n for n in os.listdir(GLib.get_tmp_dir())
              if n.startswith('babble-media-'))
server = fixture.MediaServer(clip)
base = server.base
stop_server = server.stop
VIDEO = {'type': 'video', 'url': base + '/media.ogg', 'description': 'v'}
GIFV = {'type': 'gifv', 'url': base + '/media.ogg', 'description': 'g'}

window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()

problems = {}
released = []


def note(name, condition, detail=''):
    if not condition:
        problems.setdefault(name, detail or 'cycle %d' % problems.get(
            '_n', 0))


def playing(held):
    stream = held[0]._stream
    return stream is not None and stream.get_playing()


def one_cycle(cycle):
    problems['_n'] = cycle
    held = [mediaviewer.present_media(parent, [VIDEO, GIFV], cycle % 2)]
    finalized = []
    held[0].weak_ref(lambda *_: finalized.append(True))
    note('the clip starts playing', wait(lambda: playing(held), 10),
         'cycle %d: %r' % (cycle, held[0]._stream
                           and held[0]._stream.get_error()))
    if cycle == 0:
        # Moving on reuses the engine instead of building a new one.
        first = held[0]._engine
        held[0].go_next()
        wait(lambda: playing(held), 10)
        check('moving between attachments reuses the engine',
              first is not None and held[0]._engine is first
              and held[0]._stream is not None)
        held[0].go_previous()
        wait(lambda: playing(held), 10)
    viewer = held[0]
    engine = viewer._engine
    released.append(engine)
    stream = viewer._stream
    play_finalized = []
    if engine is not None:
        engine.play.weak_ref(lambda *_: play_finalized.append(True))

    viewer.close()
    note('the viewer lets go of the stream', viewer._stream is None)
    note('the stream is detached',
         stream is not None and stream.engine is None)
    note('the stream is not playing',
         stream is not None and not stream.get_playing())
    note('the child is taken out of the bin',
         viewer.media_bin.get_child() is None)
    note('the engine is released',
         engine is not None and engine.released and viewer._engine is None)
    note('the engine is kept until STOPPED',
         engine is not None and (engine in mediaengine._releasing
                                 or engine.play is None))
    del engine, stream, viewer
    held.clear()
    wait(lambda: not mediaengine._releasing, 5)
    note('the GstPlay is finalized',
         wait(lambda: bool(gc.collect() or play_finalized), 5))
    note('the releasing set empties', not mediaengine._releasing)
    note('the viewer is disposed',
         wait(lambda: bool(gc.collect() or finalized), 5))


for cycle in range(CYCLES):
    one_cycle(cycle)


def key_controller(viewer):
    controllers = viewer.observe_controllers()
    return next((controllers.get_item(i) for i in range(len(controllers))
                 if isinstance(controllers.get_item(i),
                               Gtk.EventControllerKey)), None)


# Keys reach the viewer: Right moves on, Escape closes.
keyed = mediaviewer.present_media(parent, [VIDEO, GIFV], 0)
keys = key_controller(keyed)
check('the viewer has a key controller', keys is not None)
if keys is not None:
    check('Right is handled',
          keys.emit('key-pressed', Gdk.KEY_Right, 0, 0) is True)
    check('Right shows the next attachment',
          keyed.index == 1 and keyed.current() is GIFV,
          'index %r' % keyed.index)
    check('Escape is handled',
          keys.emit('key-pressed', Gdk.KEY_Escape, 0, 0) is True)
    check('Escape closes the viewer', keyed._closed)
keyed = keys = None
wait(lambda: not mediaengine._releasing, 5)

problems.pop('_n')
for name in ('the clip starts playing', 'the viewer lets go of the stream',
             'the stream is detached', 'the stream is not playing',
             'the child is taken out of the bin', 'the engine is released',
             'the engine is kept until STOPPED', 'the GstPlay is finalized',
             'the releasing set empties', 'the viewer is disposed'):
    check('%s (%d cycles)' % (name, CYCLES), name not in problems,
          str(problems.get(name)))
check('no cycle hit the release timeout',
      not [e for e in released if e.how == 'timeout'],
      repr([e.how for e in released]))
check('no engine is left behind', not mediaengine._all,
      '%d left' % len(mediaengine._all))
check('nothing was downloaded and no temporary copy kept',
      not downloads and not stray_dirs(),
      repr(downloads))
check('the viewer streamed the URL', '/media.ogg' in server.requests)
check('%d open/close cycles of a playing video and GIFV' % CYCLES, True)

stop_server()
faulthandler.cancel_dump_traceback_later()
if failures:
    print('\n%d media teardown check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media teardown checks passed')
