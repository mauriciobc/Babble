#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inline playback of GIFV tiles (mediainline.py).

Real MediaBoxes with GIFV tiles in a scrolled list in a presented window,
playing from the Range-capable fixture server through the real engine.
Checks that one tile at a time plays, the 50% rule, that switching reuses
the engine, that scrolling away restores the poster and the engine is
released later, that every gate closes playback live and opens it again,
that nothing plays and no engine exists while Play Animated GIFs in
Timelines is off (its default), the Pause Animation button (keyboard,
kept across a rebind), the viewer taking over, the remote-media rule, a
window closed while playing, and that nothing is left alive. The sysstate
monitor is a fake
of plain properties: the real one asks Gio.NetworkMonitor, which can hang
on a CI runner.
"""

import faulthandler
import gc
import http.server
import importlib.util
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import tempfile
import threading
import time

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('GstPlay', '1.0')

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

bundle = os.path.join(os.environ.get('BABBLE_BUILDDIR') or os.getcwd(),
                      'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import mediaengine, mediainline, mediaviewer  # noqa: E402
from src import pages, sysstate  # noqa: E402
from src.mediabox import MediaBox  # noqa: E402
from src.mediastream import EngineStream  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

faulthandler.dump_traceback_later(240, exit=True)
failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name, flush=True)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''),
              flush=True)


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
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


monitor = FakeMonitor()
sysstate._monitor = monitor
settings = pages.settings()
gtk_settings = Gtk.Settings.get_default()

# A shorter wait than the shipped 2 s, so the suite stays quick; the
# shipped value is asserted below.
SHIPPED_DELAY = mediainline.RELEASE_DELAY_MS
mediainline.RELEASE_DELAY_MS = 600

# Longer than any check waits: GStreamer 1.28.8 cannot seek an Ogg stream
# back to its start over HTTP once it has ended (a loop fails with "Failed
# to seek"; MP4 and WebM loop fine), and what the loop does is the media
# stream test's business, over file://.
server = fixture.MediaServer(
    fixture.write_long_clip(tempfile.mkdtemp(prefix='babble-inline-'),
                            seconds=60, kbps=100))


def poster_png():
    texture = Gdk.MemoryTexture.new(
        8, 6, Gdk.MemoryFormat.R8G8B8A8,
        GLib.Bytes.new(bytes([90, 120, 200, 255]) * 48), 32)
    return texture.save_to_png_bytes().get_data()


POSTER = poster_png()


class PosterHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(POSTER)))
        self.end_headers()
        self.wfile.write(POSTER)


poster_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
                                                PosterHandler)
poster_server.daemon_threads = True
threading.Thread(target=poster_server.serve_forever, daemon=True).start()
POSTER_URL = 'http://127.0.0.1:%d/poster.png' % poster_server.server_address[1]


def reset_environment(inline=True):
    for key in ('autoplay-animated-media', 'media-autoload', 'remote-media',
                'inline-animated-media'):
        settings.reset(key)
    # Off is the default; every check below needs playback, so it is on.
    settings.set_boolean('inline-animated-media', inline)
    monitor.set_property('metered', False)
    monitor.set_property('power-saver', False)
    gtk_settings.set_property('gtk-enable-animations', True)
    gtk_settings.set_property('gtk-interface-reduced-motion',
                              Gtk.ReducedMotion.NO_PREFERENCE)


def attachment(index, remote=False):
    media = {'id': 'att%d' % index, 'type': 'gifv', 'description': 'g%d' % index,
             'preview_url': POSTER_URL}
    if remote:
        media.update(url=None, remote_url=server.base + '/media.ogg')
    else:
        media.update(url=server.base + '/media.ogg')
    return media


def tile_of(box):
    return box.get_first_child().get_first_child().get_first_child() \
        .get_child_at(0, 0)


def widget_children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def shows_stream(tile):
    return isinstance(tile.picture.get_paintable(), EngineStream)


class Scene:
    """An Adw window over a scrolled column of MediaBoxes, each with one
    GIFV, optionally between two tall spacers."""

    def __init__(self, count=5, spacers=False, remote=False, paused=()):
        self.overlay = Gtk.Overlay()
        self.scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=12, margin_top=6, margin_bottom=6)
        self.scrolled.set_child(self.content)
        self.overlay.set_child(self.scrolled)
        if spacers:
            self.content.append(Gtk.Box(height_request=1500))
        self.uis = []
        self.boxes = []
        for index in range(count):
            ui = {mediainline.PAUSED_KEY: {'att%d' % index}} \
                if index in paused else {}
            box = MediaBox()
            box.set_media([attachment(index, remote)], False, ui)
            self.content.append(box)
            self.uis.append(ui)
            self.boxes.append(box)
        if spacers:
            self.content.append(Gtk.Box(height_request=1500))
        self.window = Adw.ApplicationWindow(
            content=self.overlay, default_width=600, default_height=700)
        self.window.present()
        self.tiles = [tile_of(box) for box in self.boxes]
        wait(lambda: all(isinstance(t.picture.get_paintable(), Gdk.Texture)
                         for t in self.tiles), 10)
        settle(0.1)

    @property
    def player(self):
        return getattr(self.window, '_babble_inline_player', None)

    def shown(self):
        return [i for i, t in enumerate(self.tiles) if shows_stream(t)]

    def playing(self):
        player = self.player
        return (player is not None and player._stream is not None
                and player._stream.get_playing() and bool(self.shown()))

    def scroll(self, value):
        adjustment = self.scrolled.get_vadjustment()
        adjustment.set_value(value)
        settle(0.1)

    def show_fraction(self, index, fraction):
        """Scroll so that *fraction* of tile *index* is in view, at the
        bottom edge."""
        tile = self.tiles[index]
        found, rect = tile.compute_bounds(self.content)
        height = self.scrolled.get_height()
        self.scroll(rect.get_y() - (height - fraction * rect.get_height()))

    def close(self):
        self.window.destroy()
        wait(lambda: not mediaengine._all and not mediaengine._releasing, 10)


def stopped(scene):
    return not scene.shown()


# 0. Off by default: nothing plays and no engine is made; the key is live.
reset_environment(inline=False)
check('Play Animated GIFs in Timelines is off by default',
      settings.get_boolean('inline-animated-media') is False)
check('the shipped release delay is 2 s', SHIPPED_DELAY == 2000,
      repr(SHIPPED_DELAY))
scene = Scene(count=3)
settle(1.5)
check('with the key off no tile plays and no engine is created',
      stopped(scene) and not mediaengine._all
      and scene.player._engine is None and scene.player._stream is None,
      repr(scene.shown()))
check('with the key off the tiles carry no pause button',
      all(t._button is None for t in scene.tiles))
settings.set_boolean('inline-animated-media', True)
check('turning the key on plays live', wait(lambda: scene.playing(), 10))
settings.set_boolean('inline-animated-media', False)
check('turning it off stops live and restores the poster',
      wait(lambda: stopped(scene), 5) and isinstance(
          scene.tiles[0].picture.get_paintable(), Gdk.Texture))
check('and releases the engine',
      wait(lambda: scene.player._engine is None and not mediaengine._all, 5),
      repr(mediaengine._all))
scene.close()

# 0b. Off means inert: no per-scroll work, no dead button.
evaluations = []
real_evaluate = mediainline.InlinePlayer._evaluate


def counting_evaluate(self):
    evaluations.append(1)
    return real_evaluate(self)


mediainline.InlinePlayer._evaluate = counting_evaluate
reset_environment(inline=False)
scene = Scene(count=3, spacers=True, paused=(0,))
settle(0.5)
player = scene.player
del evaluations[:]
for value in (100, 400, 900, 1500, 2000, 0):
    scene.scroll(value)
check('with the key off scrolling does no evaluation',
      not evaluations, repr(len(evaluations)))
check('with the key off no after-paint or idle handler is pending and no '
      'adjustment is watched',
      player._painted == 0 and player._idle == 0 and not player._watched,
      '%r %r %r' % (player._painted, player._idle, player._watched))
check('with the key off a user-paused tile has no button',
      scene.tiles[0].user_paused and scene.tiles[0]._button is None)
settings.set_boolean('inline-animated-media', True)
check('turning the key on watches the scrolling and shows the Play '
      'Animation button of a paused tile',
      bool(player._watched) and scene.tiles[0]._button is not None
      and scene.tiles[0]._button.get_visible()
      and scene.tiles[0]._button.get_tooltip_text() == 'Play Animation')
settings.set_boolean('inline-animated-media', False)
check('turning it off unwatches the scrolling and hides that button',
      not player._watched and not scene.tiles[0]._button.get_visible())
del evaluations[:]
scene.scroll(500)
scene.scroll(0)
check('and scrolling does no evaluation again', not evaluations)
scene.close()
del player

# 1. One tile at a time, the poster until the first frame.
reset_environment(inline=False)
scene = Scene()
log = []
first = scene.tiles[0]
first.picture.connect(
    'notify::paintable',
    lambda p, _s: log.append(type(p.get_paintable()).__name__))
settings.set_boolean('inline-animated-media', True)
check('a visible GIFV starts playing by itself',
      wait(lambda: scene.playing(), 10))
check('the poster was replaced by the stream in one step, never blank',
      log and log[0] == 'EngineStream' and 'NoneType' not in log, log)
check('the topmost fully visible tile is the one that plays',
      scene.shown() == [0], scene.shown())
check('only one tile plays and only one engine exists',
      len(scene.shown()) == 1 and len(mediaengine._all) == 1,
      '%s %d' % (scene.shown(), len(mediaengine._all)))
check('the stream loops, muted',
      scene.player._stream.get_loop() and scene.player._stream.get_muted())
check('only the playing tile has a Pause Animation button, and it says so',
      first._button is not None and first._button.get_visible()
      and first._button.get_icon_name() == 'media-playback-pause-symbolic'
      and first._button.get_tooltip_text() == 'Pause Animation'
      and all(t._button is None for t in scene.tiles[1:])
      and not any(isinstance(c, Gtk.Button)
                  for t in scene.tiles[1:] for c in widget_children(t)))
opened = []
scene.boxes[0]._open = opened.append
scene.boxes[0]._on_tile_clicked(Gtk.GestureClick(), 1, 0, 0, 0)
check('clicking the tile still opens the viewer', opened == [0])
scene.close()

# 2. The 50% rule.
scene = Scene(count=1, spacers=True)
scene.show_fraction(0, 0.4)
settle(0.5)
check('a tile 40% in view does not play', stopped(scene) and
      not mediaengine._all, repr(scene.shown()))
scene.show_fraction(0, 0.7)
check('a tile 70% in view plays', wait(lambda: scene.playing(), 10))
scene.show_fraction(0, 0.35)
check('scrolling it below half restores its poster at once',
      wait(lambda: stopped(scene), 3))
check('the poster is a texture again',
      isinstance(scene.tiles[0].picture.get_paintable(), Gdk.Texture))
scene.close()

# 3. Switching reuses the engine; scroll-away restores the poster and the
# release is deferred.
scene = Scene(count=5)
check('start', wait(lambda: scene.playing(), 10))
engine = scene.player._engine
scene.show_fraction(3, 1.0)
scene.scroll(scene.scrolled.get_vadjustment().get_value() + 300)
check('another tile plays after the scroll',
      wait(lambda: scene.playing() and scene.shown() != [0], 10),
      repr(scene.shown()))
check('the first tile has its poster back',
      isinstance(scene.tiles[0].picture.get_paintable(), Gdk.Texture))
check('switching reuses the same Engine object',
      scene.player._engine is engine and len(mediaengine._all) == 1)
playing_index = scene.shown()[0]
scene.window.get_content().get_child().get_vadjustment().set_value(0)
scene.scroll(0)
check('back on top the first tile plays again',
      wait(lambda: scene.shown() == [0], 10), repr(scene.shown()))
scene.close()

scene = Scene(count=1, spacers=True)
scene.show_fraction(0, 1.0)
check('start for the release test', wait(lambda: scene.playing(), 10))
engine = scene.player._engine
scene.scroll(0)
check('scroll-away: the poster is restored',
      wait(lambda: stopped(scene), 3))
settle(0.2)
check('scroll-away: the engine is not released at once',
      scene.player._engine is engine and not engine.released)
check('scroll-away: after the delay the engine is released (deferred)',
      wait(lambda: scene.player._engine is None, 5)
      and engine.released)
check('scroll-away: the released engine is dropped',
      wait(lambda: not mediaengine._all, 5), repr(mediaengine._all))
scene.show_fraction(0, 1.0)
check('scrolling back creates a fresh engine and plays',
      wait(lambda: scene.playing(), 10)
      and scene.player._engine is not engine)
scene.close()

# 4. Each gate closes playback live and opening it again resumes.
scene = Scene(count=1)
check('start for the gates', wait(lambda: scene.playing(), 10))


def gate(name, close, open_):
    close()
    closed = wait(lambda: stopped(scene), 5)
    check('gate %s: closing it stops the tile and restores the poster'
          % name, closed and isinstance(
              scene.tiles[0].picture.get_paintable(), Gdk.Texture))
    open_()
    check('gate %s: opening it resumes playback' % name,
          wait(lambda: scene.playing(), 10))


gate('autoplay-animated-media',
     lambda: settings.set_boolean('autoplay-animated-media', False),
     lambda: settings.set_boolean('autoplay-animated-media', True))
gate('gtk-interface-reduced-motion',
     lambda: gtk_settings.set_property('gtk-interface-reduced-motion',
                                       Gtk.ReducedMotion.REDUCE),
     lambda: gtk_settings.set_property('gtk-interface-reduced-motion',
                                       Gtk.ReducedMotion.NO_PREFERENCE))
gate('gtk-enable-animations',
     lambda: gtk_settings.set_property('gtk-enable-animations', False),
     lambda: gtk_settings.set_property('gtk-enable-animations', True))
gate('power saver',
     lambda: monitor.set_property('power-saver', True),
     lambda: monitor.set_property('power-saver', False))
gate('media-autoload never',
     lambda: settings.set_string('media-autoload', 'never'),
     lambda: settings.set_string('media-autoload', 'unmetered'))
gate('metered with media-autoload unmetered',
     lambda: monitor.set_property('metered', True),
     lambda: monitor.set_property('metered', False))
settings.set_string('media-autoload', 'always')
monitor.set_property('metered', True)
settle(0.5)
check('metered with media-autoload always keeps playing',
      scene.playing())
monitor.set_property('metered', False)
settings.reset('media-autoload')
scene.close()

# 5. A copy on another server: never played under ask or never, and the
# user is never asked from the timeline.
scene = Scene(count=1, remote=True)
settle(1.0)
check('remote-media ask: a remote GIFV is not played',
      stopped(scene) and scene.player is not None
      and scene.player._engine is None and not mediaengine._all)
check('remote-media ask: no dialog is shown',
      scene.window.get_visible_dialog() is None)
settings.set_string('remote-media', 'load')
check('remote-media load: it plays', wait(lambda: scene.playing(), 10))
settings.set_string('remote-media', 'never')
check('remote-media never: playing stops live',
      wait(lambda: stopped(scene), 5))
settings.set_string('remote-media', 'ask')
settle(0.5)
check('remote-media ask again: it stays stopped', stopped(scene))
scene.close()
settings.reset('remote-media')

# 6. The Pause Animation button.
scene = Scene(count=3)
check('start for pause', wait(lambda: scene.playing(), 10))
tile = scene.tiles[0]
button = tile._button
tile.grab_focus()
reached = False
for _step in range(12):
    scene.window.child_focus(Gtk.DirectionType.TAB_FORWARD)
    if scene.window.get_focus() is button:
        reached = True
        break
check('Tab reaches the Pause Animation button inside the tile', reached)
button.activate()
# A button's activate shows it pressed for a moment before it clicks.
check('activating it pauses: the stream stops and the choice is stored',
      wait(lambda: 'att0' in scene.uis[0].get(mediainline.PAUSED_KEY, ()), 3)
      and wait(lambda: not scene.player._stream.get_playing(), 3))
check('the button now says Play Animation',
      button.get_tooltip_text() == 'Play Animation'
      and button.get_icon_name() == 'media-playback-start-symbolic')
settle(0.5)
check('a paused tile is not resumed by itself, and keeps its frozen '
      'frame while the other tiles wait',
      scene.shown() == [0] and not scene.player._stream.get_playing())
# Rows recycle: the same ui, the same attachment, a new tile.
scene.boxes[0].set_media([attachment(0)], False, scene.uis[0])
new_tile = tile_of(scene.boxes[0])
wait(lambda t=new_tile: isinstance(t.picture.get_paintable(), Gdk.Texture),
     10)
settle(1.0)
check('the choice survives a rebind: the new tile is paused, with its '
      'poster and a Play Animation button',
      new_tile.user_paused and not shows_stream(new_tile)
      and new_tile._button is not None and new_tile._button.get_visible()
      and new_tile._button.get_tooltip_text() == 'Play Animation')
check('a paused tile is not picked while it is paused',
      0 not in scene.shown())
new_tile._button.activate()
check('pressing Play Animation resumes it even beside another full tile',
      wait(lambda t=new_tile: shows_stream(t)
           and scene.player._stream.get_playing(), 10))
check('and the stored choice is gone',
      'att0' not in scene.uis[0].get(mediainline.PAUSED_KEY, ()))
scene.close()

# 6b. The playing tile goes away (its row is removed): playback stops,
# nothing dangles, and the release is armed.
reset_environment()
scene = Scene(count=1)
check('start for the removal', wait(lambda: scene.playing(), 10))
player = scene.player
engine = player._engine
old_tile = scene.tiles[0]
scene.content.remove(scene.boxes[0])
# At once, with the main loop not turned: it is the unmap that stops it,
# not a later evaluation.
check('removing the playing tile stops it at once: no active tile, no '
      'stream',
      player._active is None and player._stream is None,
      '%r %r' % (player._active, player._stream))
check('its poster is back on the dropped picture',
      isinstance(old_tile.picture.get_paintable(), Gdk.Texture))
check('and the release is armed, with the engine still there for now',
      wait(lambda: player._release_source != 0, 3)
      and player._engine is engine)
check('the engine is then released and dropped',
      wait(lambda: player._engine is None and not mediaengine._all, 5))
del old_tile
scene.close()

# 6c. Rows replaced while the window and the player live are freed.
scene = Scene(count=2)
check('start for the replacement', wait(lambda: scene.playing(), 10))
weak_window = scene.window.weak_ref()
weak_player = scene.player.weak_ref()
weak_old = [scene.tiles[0].weak_ref(), scene.boxes[0].weak_ref(),
            scene.tiles[0].picture.weak_ref()]
scene.content.remove(scene.boxes[0])
replacement = MediaBox()
replacement.set_media([attachment(9)], False, {})
scene.content.prepend(replacement)
scene.boxes[0] = replacement
scene.tiles[0] = tile_of(replacement)
for _round in range(5):
    gc.collect()
    settle(0.1)
check('the replaced tile, its picture and its box are freed while the '
      'window and the player live',
      wait(lambda: all(r() is None for r in weak_old), 5)
      and weak_window() is not None and weak_player() is not None,
      repr([r() for r in weak_old]))
check('and playback goes on with the rest',
      wait(lambda: scene.player._stream is not None, 10))
scene.close()
del replacement

# 7. The viewer takes over.
scene = Scene(count=2)
check('start for the viewer', wait(lambda: scene.playing(), 10))
inline_engine = scene.player._engine
viewer = mediaviewer.present_media(scene.boxes[0], [attachment(0)])
check('opening the viewer stops inline playback and restores the poster',
      scene.player._engine is None and stopped(scene)
      and inline_engine.released)
settle(1.5)
check('while the viewer is open the inline player stays idle',
      stopped(scene) and scene.player._engine is None)
wait(lambda: viewer._stream is not None and viewer._stream.get_playing(), 10)
viewer.close()
wait(lambda: not mediaengine._releasing, 5)
check('after the viewer closes inline playback resumes',
      wait(lambda: scene.playing(), 10))
scene.close()

# 8. A window closed while a tile plays.
scene = Scene(count=2)
check('start for the close', wait(lambda: scene.playing(), 10))
weak_window = scene.window.weak_ref()
weak_player = scene.player.weak_ref()
weak_tiles = [t.weak_ref() for t in scene.tiles]
weak_boxes = [b.weak_ref() for b in scene.boxes]
scene.window.destroy()
check('closing the window leaves no engine once the release is done',
      wait(lambda: not mediaengine._all and not mediaengine._releasing, 10),
      repr(mediaengine._all))
scene.__dict__.clear()
del scene, first, tile, button, new_tile
for _round in range(5):
    gc.collect()
    settle(0.1)
check('the window, the player, the tiles and the boxes are freed',
      wait(lambda: weak_window() is None and weak_player() is None
           and all(r() is None for r in weak_tiles + weak_boxes), 5),
      '%s %s %s' % (weak_window(), weak_player(),
                    [r() for r in weak_tiles + weak_boxes]))

# 9. Hiding the window releases at once; showing it plays again.
scene = Scene(count=1)
check('start for the hide', wait(lambda: scene.playing(), 10))
scene.window.set_visible(False)
check('hiding the window stops and releases the engine',
      wait(lambda: scene.player._engine is None, 3) and stopped(scene))
scene.window.set_visible(True)
check('showing it again plays again', wait(lambda: scene.playing(), 10))
scene.close()

reset_environment()
server.stop()
poster_server.shutdown()
print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
