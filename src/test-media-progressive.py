#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media viewer streams a remote video instead of downloading it.

A loopback server stands in for a CDN: Range support, an optional
throttle, a 302, a source with no Range support, a 404, and a record of
the bytes it sent and the User-Agent of every request. The real viewer
(``present_media``) plays from it, and the test checks:

* the first frame arrives before the whole file was served;
* the read stops soon after the viewer closes;
* a redirect plays;
* a source without Range plays, reports itself as not seekable, and a
  seek on it does not error;
* a 404 shows the viewer's error page;
* every request carried Babble's User-Agent.
"""

import faulthandler
import importlib.util
import os
# Never the user's real dconf (see the settings isolation rule).
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

gi.require_version('GstPlay', '1.0')

from gi.repository import Adw, GLib, GObject, GstPlay, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import mediaengine, mediaplayback, mediaviewer  # noqa: E402
from src import mediapolicy, sysstate  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

faulthandler.dump_traceback_later(150, exit=True)
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


def settle(seconds):
    wait(lambda: False, seconds)


class FakeMonitor(GObject.Object):
    """The system state as plain properties: the real monitor asks
    Gio.NetworkMonitor, which can hang on a CI runner."""
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


# Not metered, not saving power: a video streams at once.
monitor = FakeMonitor()
sysstate._monitor = monitor

directory = os.path.dirname(fixture.write_clip())
media_path = fixture.write_long_clip(directory)
TMP_BEFORE = set(n for n in os.listdir(GLib.get_tmp_dir())
                 if n.startswith('babble-media-'))
SIZE = os.path.getsize(media_path)
check('the clip is much larger than the server burst',
      SIZE > 4 * 65536, str(SIZE))
RATE = 50000
slow = fixture.MediaServer(media_path, rate=RATE)
fast = fixture.MediaServer(media_path)

window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()


def video(url):
    return {'type': 'video', 'url': url, 'description': 'v'}


def frame_drawn(viewer):
    stream = viewer._stream
    return (stream is not None and stream.is_prepared()
            and stream.get_intrinsic_width() > 0)


# (a) The first frame comes before the whole file was sent.
started = time.monotonic()
viewer = mediaviewer.present_media(
    parent, [video(slow.base + '/media.ogg')])
check('the first frame is drawn', wait(lambda: frame_drawn(viewer), 8),
      repr(viewer._stream and viewer._stream.get_error()))
at_frame = slow.bytes_served
elapsed = time.monotonic() - started
check('the first frame comes before the whole file was served',
      at_frame < SIZE and elapsed < SIZE / RATE / 2,
      '%d of %d bytes after %.1f s' % (at_frame, SIZE, elapsed))
check('the video has graphics offload enabled',
      isinstance(viewer.media_bin.get_child(), Gtk.Video)
      and viewer.media_bin.get_child().get_graphics_offload()
      == Gtk.GraphicsOffloadEnabled.ENABLED)
check('the viewer shows the media page',
      viewer.stack.get_visible_child_name() == 'media',
      viewer.stack.get_visible_child_name())
check('no temporary copy is made',
      not [n for n in os.listdir(GLib.get_tmp_dir())
           if n.startswith('babble-media-') and n not in TMP_BEFORE],
      repr(os.listdir(GLib.get_tmp_dir())))

# Buffering: a spinner after the delay, hidden by an error and by close.
mediaplayback.BUFFERING_DELAY_MS = 50
viewer._stream._engine_event('state-changed', GstPlay.PlayState.BUFFERING)
check('the buffering spinner is not shown at once',
      not viewer.buffering_spinner.get_visible())
check('the buffering spinner shows after the delay',
      wait(viewer.buffering_spinner.get_visible, 2))
viewer._stream._engine_event('state-changed', GstPlay.PlayState.PLAYING)
check('another state hides the spinner',
      not viewer.buffering_spinner.get_visible())
viewer._stream._engine_event('state-changed', GstPlay.PlayState.BUFFERING)
wait(viewer.buffering_spinner.get_visible, 2)
viewer._stream._engine_event('error', GLib.Error.new_literal(
    GLib.quark_from_string('test'), 'boom', 0))
check('an error hides the spinner and shows the error page',
      not viewer.buffering_spinner.get_visible()
      and viewer.stack.get_visible_child_name() == 'error')
viewer._stream._engine_event('state-changed', GstPlay.PlayState.PLAYING)
viewer.show_index(0)
wait(lambda: frame_drawn(viewer), 8)
viewer._stream._engine_event('state-changed', GstPlay.PlayState.BUFFERING)
wait(viewer.buffering_spinner.get_visible, 2)

# (b) Closing the viewer ends the read.
engine = viewer._engine
at_close = slow.bytes_served
viewer._stream._engine_event('state-changed', GstPlay.PlayState.BUFFERING)
viewer.close()
check('closing hides the spinner and cancels its timer',
      not viewer.buffering_spinner.get_visible()
      and viewer._buffering_timeout == 0)
settle(1.2)
after_close = slow.bytes_served
settle(1.5)
check('the bytes served stop growing after the viewer closes',
      slow.bytes_served == after_close and at_close < SIZE,
      '%d at close, %d a second later, then %d; file is %d'
      % (at_close, after_close, slow.bytes_served, SIZE))
check('the engine is released',
      wait(lambda: not mediaengine._releasing, 5) and engine.released)

# (c) A 302 is followed.
viewer = mediaviewer.present_media(
    parent, [video(fast.base + '/redirect.ogg')])
check('a redirected source plays',
      wait(lambda: frame_drawn(viewer), 8),
      repr(viewer._stream and viewer._stream.get_error()))
check('... through the redirect target',
      '/media.ogg' in fast.requests and '/redirect.ogg' in fast.requests,
      repr(fast.requests))
viewer.close()
wait(lambda: not mediaengine._releasing, 5)

# (d) A source with no Range support plays, and cannot seek.
viewer = mediaviewer.present_media(
    parent, [video(fast.base + '/norange.ogg')])
check('a source without Range plays', wait(lambda: frame_drawn(viewer), 8),
      repr(viewer._stream and viewer._stream.get_error()))
stream = viewer._stream
check('... and reports itself as not seekable',
      stream is not None and not stream.is_seekable())
if stream is not None:
    stream.seek(0)
    settle(1.0)
    check('a seek on it does not error',
          stream.get_error() is None
          and not stream.get_property('seeking')
          and viewer.stack.get_visible_child_name() == 'media',
          repr(stream.get_error()))
viewer.close()
wait(lambda: not mediaengine._releasing, 5)

# (e) A 404 shows the error page, with Retry.
viewer = mediaviewer.present_media(
    parent, [video(fast.base + '/missing.ogg')])
check('a 404 shows the error page',
      wait(lambda: viewer.stack.get_visible_child_name() == 'error', 8),
      viewer.stack.get_visible_child_name())
check('... with Retry', viewer.retry_button.get_visible())
before = len(fast.requests)
viewer.retry_button.emit('clicked')
check('Retry streams again and lands on the error page again',
      wait(lambda: len(fast.requests) > before, 5)
      and wait(lambda: viewer.stack.get_visible_child_name() == 'error', 8))
viewer.close()
wait(lambda: not mediaengine._releasing, 5)

# (e2) A GIFV obeys the live autoplay check; a video never does.
gtk_settings = Gtk.Settings.get_default()
saved_motion = gtk_settings.get_property('gtk-interface-reduced-motion')
saved_anim = gtk_settings.get_property('gtk-enable-animations')


def loop_allowed():
    return mediapolicy.may_loop(mediapolicy.read_state())


def gifv(url):
    return {'type': 'gifv', 'url': url, 'description': 'g'}


def open_gifv():
    v = mediaviewer.present_media(parent, [gifv(fast.base + '/media.ogg')])
    wait(lambda: frame_drawn(v), 8)
    return v


try:
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)
    gtk_settings.set_property('gtk-enable-animations', True)
    check('the check is True with nothing against motion',
          loop_allowed())

    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.REDUCE)
    check('the check is False under reduced motion',
          not loop_allowed())
    viewer = open_gifv()
    settle(0.5)
    check('a GIFV does not autoplay under reduced motion',
          viewer._stream.is_prepared() and not viewer._stream.get_playing())
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)
    check('... and it starts when the gate opens (case B)',
          viewer._stream.is_prepared() and wait(viewer._stream.get_playing, 5))
    viewer.close()
    wait(lambda: not mediaengine._releasing, 5)

    # Case A: open at start, closed before the stream is prepared.
    viewer = mediaviewer.present_media(
        parent, [gifv(fast.base + '/media.ogg')])
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.REDUCE)
    wait(lambda: frame_drawn(viewer), 8)
    settle(0.5)
    check('a gate closed while loading holds the GIFV (case A)',
          viewer._stream.is_prepared() and not viewer._stream.get_playing())
    # A click starts it; an unrelated notify must not pause it.
    viewer._on_gifv_clicked(None, 1, 0, 0, viewer._stream)
    check('a click starts a held GIFV', viewer._stream.get_playing())
    sysstate.monitor().notify('power-saver')
    check('an unrelated notify leaves a clicked GIFV playing',
          viewer._stream.get_playing())
    viewer.close()
    wait(lambda: not mediaengine._releasing, 5)
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)

    viewer = open_gifv()
    check('a GIFV autoplays', wait(viewer._stream.get_playing, 5))
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.REDUCE)
    check('reduced motion pauses a playing GIFV',
          not viewer._stream.get_playing())
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)
    check('... and clearing it resumes the GIFV it paused',
          wait(viewer._stream.get_playing, 5))

    gtk_settings.set_property('gtk-enable-animations', False)
    check('turning animations off pauses it',
          not viewer._stream.get_playing())
    gtk_settings.set_property('gtk-enable-animations', True)
    check('... and on resumes it', wait(viewer._stream.get_playing, 5))

    monitor.set_property('power-saver', True)
    sysstate.monitor().notify('power-saver')
    check('power saving pauses the GIFV', not viewer._stream.get_playing())
    monitor.set_property('power-saver', False)
    sysstate.monitor().notify('power-saver')
    check('... and leaving it resumes the GIFV',
          wait(viewer._stream.get_playing, 5))

    # A GIFV the user paused is the user's.
    viewer._on_gifv_clicked(None, 1, 0, 0, viewer._stream)
    check('a click pauses the GIFV', not viewer._stream.get_playing())
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.REDUCE)
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)
    settle(0.3)
    check('a GIFV the user paused is not resumed',
          not viewer._stream.get_playing())
    viewer.close()
    wait(lambda: not mediaengine._releasing, 5)

    # Another attachment: the gate leaves a video alone.
    viewer = mediaviewer.present_media(
        parent, [video(fast.base + '/media.ogg')])
    wait(lambda: frame_drawn(viewer), 8)
    check('a video autoplays', wait(viewer._stream.get_playing, 5))
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.REDUCE)
    monitor.set_property('power-saver', True)
    sysstate.monitor().notify('power-saver')
    check('a video is never paused by the gate',
          viewer._stream.get_playing())
    viewer.close()
    wait(lambda: not mediaengine._releasing, 5)
finally:
    gtk_settings.set_property('gtk-interface-reduced-motion', saved_motion)
    gtk_settings.set_property('gtk-enable-animations', saved_anim)
    monitor.set_property('power-saver', False)

# (e3) The viewer remembers the volume of video and audio, not a GIFV's.
from src import pages  # noqa: E402

store = pages.settings()
store.reset('media-viewer-volume')
viewer = mediaviewer.present_media(parent, [video(fast.base + '/media.ogg')])
wait(lambda: frame_drawn(viewer), 8)
check('a video starts at the default volume',
      abs(viewer._stream.get_volume() - 1.0) < 1e-6,
      str(viewer._stream.get_volume()))
viewer._stream.set_volume(0.4)
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
check('closing saves the changed volume',
      abs(store.get_double('media-viewer-volume') - 0.4) < 1e-6)
viewer = mediaviewer.present_media(parent, [video(fast.base + '/media.ogg')])
wait(lambda: frame_drawn(viewer), 8)
check('the next video opens at that volume',
      abs(viewer._stream.get_volume() - 0.4) < 1e-6,
      str(viewer._stream.get_volume()))
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
viewer = open_gifv()
viewer._stream.set_volume(0.9)
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
check('a GIFV does not touch the saved volume',
      abs(store.get_double('media-viewer-volume') - 0.4) < 1e-6)
# A near-silent volume is not remembered.
store.set_double('media-viewer-volume', 0.7)
viewer = mediaviewer.present_media(parent, [video(fast.base + '/media.ogg')])
wait(lambda: frame_drawn(viewer), 8)
viewer._stream.set_volume(0.01)
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
check('a near-silent volume is not saved',
      abs(store.get_double('media-viewer-volume') - 0.7) < 1e-6)

# A reused engine does not stay muted after a GIFV.
store.reset('media-viewer-volume')
viewer = mediaviewer.present_media(
    parent, [gifv(fast.base + '/media.ogg'), video(fast.base + '/media.ogg')])
wait(lambda: frame_drawn(viewer), 8)
engine = viewer._engine
check('a GIFV mutes the engine', engine.play.get_mute())
viewer.show_index(1)
wait(lambda: frame_drawn(viewer), 8)
check('the same engine is reused', viewer._engine is engine)
check('a video after a GIFV is not muted', not engine.play.get_mute())
check('... and has the stream volume',
      abs(engine.play.get_volume() - viewer._stream.get_volume()) < 1e-6,
      str(engine.play.get_volume()))
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
store.reset('media-viewer-volume')

# (f) Every request carried Babble's User-Agent.
agents = slow.agents + fast.agents
check('the requests were recorded', len(agents) >= 4, repr(agents))
bad = [pair for pair in slow.pairs + fast.pairs
       if not (pair[2] and pair[2].startswith('Babble/'))]
check('every request carried Babble\'s User-Agent', not bad,
      'offending (method, path, agent, range): %r of %d requests' % (bad, len(agents)))

check('no engine is left behind', wait(lambda: not mediaengine._all, 5),
      '%d left' % len(mediaengine._all))

slow.stop()
fast.stop()
faulthandler.cancel_dump_traceback_later()
if failures:
    print('\n%d media progressive check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media progressive checks passed')
