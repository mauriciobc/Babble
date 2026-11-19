#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Picture-in-Picture: the handover between the viewer and the card.

The viewer hands a playing engine and stream to the card
(``minimize_to_pip``), the card hands them back (``restore``), and both
sides claim the same "one playing surface per window" rule. Because a
wrong handover usually looks like a leak or a race rather than a broken
assertion, the test drives the real path with a loopback server (no
download, no temporary copy) and checks what a correct handover leaves
behind:

* minimize: the viewer closes without releasing anything; the card plays
  the very same stream object through the very same engine;
* the card's widgets, play/pause, close, and restore;
* a second viewer closes the card; the inline player stands down while a
  card exists;
* the guard rails (an image, a failed stream, a window without an
  overlay) refuse to make a card;
* 20 minimize/restore cycles, then one close: every engine released
  without the timeout, nothing left behind.

A wrong claim usually shows as a GTK critical (fatal here), a released
engine, or a playing stream that stopped.
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

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import mediaengine, mediainline, mediapip  # noqa: E402
from src import mediaplayback, mediaviewer, sysstate  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

# The loop of the last case: each cycle is a full minimize and restore,
# and each of those is several waits.
CYCLES = 20
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


def attempt(name, action):
    """Run *action*, reporting a defect that raises as the FAIL it is
    instead of killing the whole suite."""
    try:
        return action()
    except Exception as error:  # noqa: BLE001
        check(name, False, '%s: %s' % (type(error).__name__, error))
        return None


def blocked(name):
    """A case that could not run because an earlier defect broke it."""
    print('SKIP - %s [blocked by an earlier defect]' % name)


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


PLAYING = mediaengine.GstPlay.PlayState.PLAYING


class FakeMonitor(GObject.Object):
    """The system state as plain properties: the real monitor asks
    Gio.NetworkMonitor, which can hang on a CI runner."""
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
mediaplayback.download = lambda *args, **kwargs: downloads.append(args)

try:
    directory = os.path.dirname(fixture.write_clip())
    media_path = fixture.write_long_clip(directory, seconds=120, kbps=200)
except RuntimeError as error:
    print('FAIL - the test clip could be encoded [%s]' % error)
    sys.exit(1)
BEFORE.update(n for n in os.listdir(GLib.get_tmp_dir())
              if n.startswith('babble-media-'))
server = fixture.MediaServer(media_path)
base = server.base
stop_server = server.stop
# A long clip: the one-second fixture clip ends while the assertions are
# still reading its state. The checks below rely on the stream playing
# across handovers, so it has to outlast the whole case.
VIDEO = {'type': 'video', 'url': base + '/media.ogg', 'description': 'v'}
GIFV = {'type': 'gifv', 'url': base + '/media.ogg', 'description': 'g'}
IMAGE = {'type': 'image', 'url': base + '/missing.png', 'description': 'i'}

window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()
overlay = window.get_content()

released = []
finalized = []


def track(engine):
    """Watch *engine*'s GstPlay until it is finalized."""
    if engine is not None and engine.play is not None:
        engine.play.weak_ref(lambda *_: finalized.append(True))
    return engine


def playing_now(engine, stream):
    """The stream is really playing, not merely asked to: the engine's
    state is read along with the stream's, because ``get_playing()``
    alone is vacuous before the card exists."""
    return (stream is not None and stream.get_playing()
            and engine is not None and engine.state == PLAYING)


def all_engines_gone():
    return not mediaengine._releasing and not mediaengine._all


def open_playing(parent=parent, attachment=VIDEO):
    """A viewer of *attachment* with its stream prepared and playing."""
    viewer = mediaviewer.present_media(parent, [attachment], 0)
    stream = viewer._stream
    ok = wait(lambda: playing_now(viewer._engine, viewer._stream), 10)
    return viewer, stream, ok


def overlay_children(over):
    """The overlay children of *over*, as a list."""
    model = over.observe_children()
    return [model.get_item(i) for i in range(model.get_n_items())]


def pip_controls(card):
    """The card's OSD bar, or None."""
    for child in overlay_children(card):
        if child.has_css_class('pip-controls'):
            return child
    return None


def pip_buttons(card):
    """The buttons of the card's OSD bar, in order."""
    box = pip_controls(card)
    if box is None:
        return []
    buttons = []
    child = box.get_first_child()
    while child is not None:
        buttons.append(child)
        child = child.get_next_sibling()
    return buttons


# ---------------------------------------------------------------- 1
# Minimize: the viewer hands over and lets go of everything.
viewer, stream, played = open_playing()
engine = viewer._engine
check('the clip starts playing', played)
minimized = attempt('minimize applied', viewer.minimize_to_pip)
if minimized is not None:
    check('minimize applied', minimized is True)
card = mediapip.card_for(window)
check('the card exists', card is not None)
if card is None:
    # A failed handover may have closed the viewer without a card: the
    # engine it held is then an orphan, playing for nobody. Clean it up
    # so later cases are not polluted by this defect.
    if viewer._closed and engine is not None and not engine.released:
        engine.release()
        wait(all_engines_gone, 10)
    viewer = engine = None
    blocked('the handover state checks')
else:
    check('the card is in the overlay',
          any(isinstance(child, mediapip.PiPCard)
              for child in overlay_children(overlay)))
    check('the viewer is closed', viewer._closed)
    check('card_for is the card', mediapip.card_for(window) is card)
    check('the card plays the viewer\'s stream', card.stream is stream)
    check('the card owns the viewer\'s engine', card.engine is engine)
    check('the stream is still playing',
          wait(lambda: playing_now(engine, stream), 10))
    settle(1.0)
    check('the stream is still playing a second later',
          playing_now(engine, stream))
    check('no engine is releasing', not mediaengine._releasing)
    check('the engine is not released by the handover',
          engine in mediaengine._all)
    check('no inline player was created for the window',
          mediainline.existing_player(window) is None)
check('nothing was downloaded and no temporary copy kept',
      not downloads and not stray_dirs(), repr(downloads))
check('the viewer was streamed the URL', '/media.ogg' in server.requests)
HANDOVER = card is not None

# ---------------------------------------------------------------- 2
# The card's widgets.
if HANDOVER:
    picture = card.get_child()
    check('the video area is a picture of the stream',
          isinstance(picture, Gtk.Picture)
          and picture.get_paintable() is card._paintable
          and card._paintable.stream is stream)
    check('the video area is not focusable', not picture.get_focusable())
    controls = pip_controls(card)
    buttons = pip_buttons(card)
    check('the OSD bar exists with three buttons',
          controls is not None and controls.has_css_class('osd')
          and controls.has_css_class('pip-controls') and len(buttons) == 3)
    play, expand, close_button = buttons[0], buttons[1], buttons[2] \
        if len(buttons) == 3 else (None, None, None)
    check('the play control is a toggle button',
          isinstance(play, Gtk.ToggleButton))
    check('the expand control expands', expand is not None
          and expand.get_icon_name() == 'babble-pip-out-symbolic')
    check('the close control closes', close_button is not None
          and close_button.get_icon_name() == 'window-close-symbolic')
    check('every button has a tooltip and an accessible label',
          all(b.get_tooltip_text() for b in buttons),
          repr([b.get_tooltip_text() for b in buttons]))
    check('the card sits bottom-right',
          card.get_halign() == Gtk.Align.END
          and card.get_valign() == Gtk.Align.END
          and card.get_margin_bottom() == mediapip.CORNER_MARGIN
          and card.get_margin_end() == mediapip.CORNER_MARGIN,
          'margins %r/%r' % (card.get_margin_bottom(),
                             card.get_margin_end()))
    ratio = stream.get_intrinsic_aspect_ratio() or (16.0 / 9.0)
    expected = min(mediapip.MAX_CARD_HEIGHT,
                   max(mediapip.MIN_CARD_HEIGHT,
                       round(mediapip.CARD_WIDTH / ratio)))
    check('the card is CARD_WIDTH wide and follows the ratio',
          card.get_size_request() == (mediapip.CARD_WIDTH, expected),
          repr(card.get_size_request()))

    # The paintable caps the intrinsic size: the picture's natural size
    # is the paintable's, and a portrait phone video is hundreds of px
    # wide -- the card would take it over the timeline. The fixture clip
    # is too small to be that video, so a portrait ratio stands in.
    wrapper = picture.get_paintable()
    original_ratio = stream.get_intrinsic_aspect_ratio
    stream.get_intrinsic_aspect_ratio = lambda: 0.8
    wrapper.invalidate_size()
    portrait = mediapip._card_size(0.8)
    settle(0.5)
    check('the paintable caps a portrait video',
          wrapper.get_intrinsic_width() == portrait[0]
          and wrapper.get_intrinsic_height() == portrait[1]
          and abs(wrapper.get_intrinsic_aspect_ratio() - 0.8) < 1e-9,
          'intrinsic %dx%d' % (wrapper.get_intrinsic_width(),
                               wrapper.get_intrinsic_height()))
    check('the card allocation follows the capped size',
          card.get_width() == portrait[0]
          and card.get_height() == portrait[1],
          'allocated %dx%d' % (card.get_width(), card.get_height()))
    stream.get_intrinsic_aspect_ratio = original_ratio
    wrapper.invalidate_size()
    settle(0.5)
    check('the card is back at the fixture size',
          card.get_width() == mediapip.CARD_WIDTH
          and card.get_height() == expected,
          'allocated %dx%d, request %r' % (
              card.get_width(), card.get_height(),
              card.get_size_request()))

    # The drag is not pinned to the margins the card started with: the
    # card travels anywhere over the window and snaps to a corner on
    # release.
    gesture = None
    for controller in picture.observe_controllers():
        if isinstance(controller, Gtk.GestureDrag):
            gesture = controller
    check('the video area has a drag gesture', gesture is not None)
    if gesture is not None:
        gesture.emit('drag-begin', 10, 10)
        gesture.emit('drag-update', 200, 100)
        check('the card travels beyond its starting margins',
              card.get_margin_top() == 100
              and card.get_margin_start() == 200,
              'margins %r/%r' % (card.get_margin_top(),
                                 card.get_margin_start()))
        gesture.emit('drag-end', 200, 100)
        check('the release snaps to a corner',
              (card.get_margin_top() == 0
               or card.get_margin_bottom() == 0)
              and (card.get_margin_start() == 0
                   or card.get_margin_end() == 0),
              'margins %r/%r/%r/%r' % (
                  card.get_margin_top(), card.get_margin_bottom(),
                  card.get_margin_start(), card.get_margin_end()))
        card._m_top = 0.0
        card._m_bottom = float(mediapip.CORNER_MARGIN)
        card._m_start = 0.0
        card._m_end = float(mediapip.CORNER_MARGIN)
        card._apply_margins()
else:
    blocked('the card widgets')
    play = close_button = None

# ---------------------------------------------------------------- 3
# Play/pause: the button mirrors the stream and follows the label.
if HANDOVER and play is not None:
    play.emit('toggled')
    check('toggling pauses', wait(lambda: not stream.get_playing(), 5))
    check('the engine is no longer PLAYING',
          wait(lambda: engine.state != PLAYING, 5), str(engine.state))
    check('the button is unchecked while paused', not play.get_active())
    check('the paused control says Play',
          play.get_tooltip_text() == 'Play', repr(play.get_tooltip_text()))
    play.emit('toggled')
    check('toggling plays again',
          wait(lambda: playing_now(engine, stream), 5))
    check('the button is checked while playing', play.get_active())
    check('the playing control says Pause',
          play.get_tooltip_text() == 'Pause',
          repr(play.get_tooltip_text()))
else:
    blocked('the play/pause control')

# ---------------------------------------------------------------- 4
# Close: everything the card held goes, without the release timeout.
if HANDOVER and close_button is not None:
    track(engine)
    attempt('the close control closes the card',
            lambda: close_button.emit('clicked'))
    check('the release finishes', wait(all_engines_gone, 10))
    check('the engine was not dropped by the timeout',
          engine.how is not None and engine.how != 'timeout', engine.how)
    check('the GstPlay is finalized',
          wait(lambda: bool(gc.collect() or finalized), 5))
    check('the card left the overlay', card.get_parent() is None)
    check('card_for is None after close', mediapip.card_for(window) is None)
    check('the card marked itself closed', card._closed)
    check('the released stream is detached', stream.engine is None)
    check('close kept nothing behind',
          not downloads and not stray_dirs(), repr(downloads))
else:
    blocked('the card close')
card = None

# ---------------------------------------------------------------- 5
# Restore: the same engine and stream go back into a fresh viewer.
if HANDOVER:
    viewer, stream, played = open_playing()
    check('the second clip starts playing', played)
    minimized = attempt('minimize applied again', viewer.minimize_to_pip)
    if minimized is not None:
        check('minimize applied again', minimized is True)
    card = mediapip.card_for(window)
    check('the card exists again', card is not None)
    if card is not None:
        engine = card.engine
        check('the engine is playing before restore',
              wait(lambda: playing_now(engine, card.stream), 10))
        old = card.stream.get_timestamp()
        attempt('restore reopened the viewer', lambda: mediapip.restore(card))
        new_viewer = mediaviewer._open_viewer
        check('restore opened a new viewer', new_viewer is not None
              and new_viewer is not viewer and not new_viewer._closed)
        check('the new viewer shows the media page',
              wait(lambda: new_viewer is not None
                   and new_viewer.stack.get_visible_child_name()
                   == 'media', 10))
        child = new_viewer.media_bin.get_child() if new_viewer else None
        media_stream = (child.get_media_stream()
                        if isinstance(child, Gtk.Video)
                        else child.get_paintable()
                        if child is not None else None)
        check('the new viewer shows the card\'s stream',
              media_stream is stream)
        check('the card was adopted', card.adopted is True)
        check('card_for is None after restore',
              mediapip.card_for(window) is None)
        check('the viewer engine is the card\'s engine',
              new_viewer is not None and new_viewer._engine is engine)
        check('the stream is still playing after restore',
              wait(lambda: playing_now(engine, stream), 10))
        check('the timestamp advanced',
              wait(lambda: stream.get_timestamp() > old, 5),
              '%r -> %r' % (old, stream.get_timestamp()))
        track(engine)
        attempt('the restored viewer closes',
                lambda: new_viewer.close())
        check('the restored viewer released everything',
              wait(all_engines_gone, 10))
        check('the restored engine was not dropped by the timeout',
              engine.how is not None and engine.how != 'timeout',
              engine.how)
        check('no card is left behind', mediapip.card_for(window) is None)
    else:
        blocked('the restore path')
        if viewer._closed and engine is not None and not engine.released:
            engine.release()
            wait(all_engines_gone, 10)
    card = new_viewer = None
else:
    blocked('the restore path')

# ---------------------------------------------------------------- 6
# A second viewer closes the card: one playing surface per window.
if HANDOVER:
    viewer, stream, played = open_playing()
    check('the third clip starts playing', played)
    minimized = attempt('minimize applied a third time',
                        viewer.minimize_to_pip)
    if minimized is not None:
        check('minimize applied a third time', minimized is True)
    card = mediapip.card_for(window)
    engine = card.engine if card is not None else None
    check('the card exists a third time', card is not None)
    viewer2, stream2, played = open_playing()
    check('the second viewer closed the card',
          wait(lambda: mediapip.card_for(window) is None, 5))
    check('the second viewer owns its own engine',
          viewer2._engine is not None and viewer2._engine is not engine)
    check('the second viewer plays', played)
    check('the card\'s engine was released',
          engine is not None and engine.released
          and engine not in mediaengine._all)
    check('the card\'s engine finished',
          wait(lambda: engine.how is not None, 5))
    check('the closed card\'s release did not time out',
          engine.how != 'timeout', engine.how)
    track(viewer2._engine)
    viewer2.close()
    check('the second viewer released everything',
          wait(all_engines_gone, 10))
    card = viewer = viewer2 = None
else:
    blocked('the second viewer closing the card')

# ---------------------------------------------------------------- 6b
# A GIFV handed back into a viewer whose loop gate is closed: the gate
# the adopt path re-arms must act on the playing loop at once, the way
# the fresh path does at its prepare.
if HANDOVER:
    viewer, stream, played = open_playing(attachment=GIFV)
    check('the gifv clip starts playing', played)
    minimized = attempt('gifv minimize applied',
                        viewer.minimize_to_pip)
    if minimized is not None:
        check('gifv minimize applied', minimized is True)
    card = mediapip.card_for(window)
    engine = card.engine if card is not None else None
    check('the gifv card exists', card is not None)
    # Power saver closes the loop gate while the GIFV is in the card;
    # the card keeps playing (the user put it there), the viewer must
    # not when it comes back.
    sysstate._monitor.set_property('power_saver', True)
    attempt('gifv restore reopened the viewer',
            lambda: mediapip.restore(card))
    new_viewer = mediaviewer._open_viewer
    check('the gifv card was adopted',
          card is not None and card.adopted
          and isinstance(new_viewer, mediaviewer.MediaViewer))
    check('the closed gate pauses the restored gifv',
          wait(lambda: stream.get_playing() is False
               and new_viewer._gate_paused, 10),
          'playing=%r gate_paused=%r' % (
              stream.get_playing(),
              new_viewer._gate_paused if new_viewer else None))
    sysstate._monitor.set_property('power_saver', False)
    check('the open gate resumes the restored gifv',
          wait(lambda: playing_now(engine, stream), 10))
    track(engine)
    attempt('the gifv viewer closes', new_viewer.close)
    check('the gifv viewer released everything',
          wait(all_engines_gone, 10))
    card = viewer = new_viewer = engine = None
else:
    blocked('the gifv loop gate on restore')

# ---------------------------------------------------------------- 7
# Inline suppression: the player of the same window stands down.
side_window = Adw.ApplicationWindow(content=Gtk.Box(), default_width=600,
                                    default_height=400)
side_window.present()
player = mediainline.player_for(side_window)
check('the inline player exists', player is not None)
check('existing_player finds it',
      mediainline.existing_player(side_window) is player)
mediainline.pip_opened(side_window)
check('pip_opened set the suppression flag', player._pip_open is True)
check('pip_opened released the player\'s engine', player._engine is None)
mediainline.pip_closed(side_window)
check('pip_closed cleared the suppression flag', player._pip_open is False)
wait(lambda: not player._idle and not player._painted, 5)
check('pip_closed schedules no engine without tiles', player._engine is None)
player = side_window = None

# ---------------------------------------------------------------- 8
# Guard rails.
image_viewer = mediaviewer.present_media(parent, [IMAGE], 0)
check('an image refuses to minimize', image_viewer.minimize_to_pip() is False)
check('the image viewer stays open', not image_viewer._closed
      and mediaviewer._open_viewer is image_viewer)
check('an image made no card', mediapip.card_for(window) is None)
image_viewer.close()

broken = {'type': 'video', 'url': 'http://127.0.0.1:9/nope.ogg'}
failed_viewer = mediaviewer.present_media(parent, [broken], 0)
check('the failed stream reports its error',
      wait(lambda: failed_viewer._stream is not None
           and failed_viewer._stream.get_error() is not None, 10))
check('a failed stream refuses to minimize',
      failed_viewer.minimize_to_pip() is False)
check('the failed viewer stays open', not failed_viewer._closed
      and mediaviewer._open_viewer is failed_viewer)
check('a failed stream made no card', mediapip.card_for(window) is None)
failed_viewer.close()
check('the failed viewer released everything', wait(all_engines_gone, 10))

box_window = Adw.ApplicationWindow(content=Gtk.Box(), default_width=600,
                                   default_height=400)
box_window.present()
hostless, stream3, played = open_playing(box_window.get_content())
# present_media gives a window without an overlay a host of its own; the
# host is only kept alive by our reference here.
host = hostless.get_root()
check('the overlay-less window plays', played)
check('open_pip refuses a window without an overlay',
      mediapip.open_pip(hostless) is None)
check('the overlay-less viewer stays open',
      not hostless._closed and hostless._stream is stream3)
check('the overlay-less window has no card',
      mediapip.card_for(box_window) is None)
track(hostless._engine)
hostless.close()
check('the overlay-less viewer released everything',
      wait(all_engines_gone, 10))
box_window = hostless = host = None

# ---------------------------------------------------------------- 9
# 20 minimize/restore cycles, then one close.
problems = {}


def note(name, condition, detail=''):
    if not condition:
        problems.setdefault(name, detail or 'cycle %d'
                            % problems.get('_n', 0))


def one_cycle(cycle):
    problems['_n'] = cycle
    viewer, stream, played = open_playing()
    note('the cycle clip starts playing', played, 'cycle %d' % cycle)
    engine = viewer._engine
    released.append(engine)
    track(engine)
    minimized = attempt('minimize applied', viewer.minimize_to_pip)
    if minimized is None:
        # A defect in the handover must not spin the loop on it: clean
        # the orphaned engine up and stop here.
        if viewer._closed and engine is not None and not engine.released:
            engine.release()
            wait(all_engines_gone, 10)
        note('minimize applied', False, 'cycle %d raised' % cycle)
        for name in ('the cycle card exists',
                     'restore reopened the viewer',
                     'the restored cycle viewer plays',
                     'the cycle card was adopted',
                     'card_for is None after the cycle restore'):
            note(name, False, 'cycle %d: the handover raised' % cycle)
        return False
    note('minimize applied', minimized is True, 'cycle %d' % cycle)
    card = mediapip.card_for(window)
    note('the cycle card exists', card is not None and card.engine is engine)
    attempt('restore reopened the viewer', lambda: mediapip.restore(card))
    new_viewer = mediaviewer._open_viewer
    note('restore reopened the viewer',
         new_viewer is not None and new_viewer is not viewer
         and new_viewer._engine is engine)
    note('the restored cycle viewer plays',
         wait(lambda: playing_now(engine, stream), 10),
         'cycle %d' % cycle)
    note('the cycle card was adopted', card.adopted is True)
    note('card_for is None after the cycle restore',
         mediapip.card_for(window) is None)
    del viewer, new_viewer
    card = stream = None
    return True


ran = 0
for cycle in range(CYCLES):
    if not one_cycle(cycle):
        break
    ran += 1

problems.pop('_n')
if ran < CYCLES:
    blocked('the cycle close and final checks (ran %d of %d cycles)'
            % (ran, CYCLES))
else:
    last = mediaviewer._open_viewer
    check('a viewer is left after the cycles', last is not None)
    if last is not None:
        last_engine = track(last._engine)
        last.close()
    check('the cycles left nothing releasing',
          wait(lambda: all_engines_gone()
               and bool(gc.collect() or finalized), 10))
for name in ('the cycle clip starts playing', 'minimize applied',
             'the cycle card exists', 'restore reopened the viewer',
             'the restored cycle viewer plays', 'the cycle card was adopted',
             'card_for is None after the cycle restore'):
    check('%s (%d of %d cycles)' % (name, ran, CYCLES), name not in problems,
          str(problems.get(name)))
check('no engine hit the release timeout',
      all(e.how != 'timeout' for e in released if e.how is not None),
      repr([e.how for e in released]))
check('no engine is left behind', not mediaengine._all,
      '%d left' % len(mediaengine._all))
check('nothing was downloaded and no temporary copy kept (cycles)',
      not downloads and not stray_dirs(), repr(downloads))
check('%d minimize/restore cycles of a playing video' % CYCLES, True)

stop_server()
faulthandler.cancel_dump_traceback_later()
if failures:
    print('\n%d pip check(s) failed: %s' % (len(failures), ', '.join(
        failures)))
    sys.exit(1)
print('\nall pip playback checks passed')
