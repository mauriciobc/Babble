#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Animated custom emoji (emojianim.py).

Decodes a tiny APNG and GIF (three frames, 50/80/120 ms) and a still PNG
through glycin, shows them in real EmojiImages served by a loopback HTTP
server, and checks that: an animation decodes to its frames and delays and
a still image stays static; two images of one URL share one paintable and
one clock; the clock stops when both unmap and resumes on map; the gates
(a replaced one, and the real reduced-motion setting) stop and restart
every image live; images are freed after removal and the byte-budgeted
store evicts only what nothing shows; and without glycin everything
stays static.
"""

import gc
import http.server
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import threading

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import emojianim, textemoji  # noqa: E402

emojianim.DEFER_MS = 20

FIXTURES = os.path.join(srcdir, 'fixtures', 'emoji')
failures = []


def check(ok, label):
    print(('ok - ' if ok else 'FAIL - ') + label)
    if not ok:
        failures.append(label)


def pump(condition=None, seconds=5.0):
    """Turn the main loop until *condition* holds (or *seconds* pass)."""
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1e6)
    while GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        if condition is not None and condition():
            return True
        if condition is None:
            continue
        GLib.usleep(2000)
    return condition is None or condition()


def settle(seconds=0.3):
    pump(None, seconds)


hits = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        hits[self.path] = hits.get(self.path, 0) + 1
        path = os.path.join(FIXTURES,
                            os.path.basename(self.path.split('?')[0]))
        if not os.path.isfile(path):
            self.send_error(404)
            return
        body = open(path, 'rb').read()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d/' % server.server_address[1]


def read(name):
    return GLib.Bytes.new(open(os.path.join(FIXTURES, name), 'rb').read())


def decode(name):
    out = []
    emojianim._decode(read(name), None, None,
                      lambda value, reason: out.append((value, reason)))
    pump(lambda: bool(out))
    return out[0] if out else (None, 'timeout')


# Decoding

for name in ('a.png', 'a.gif'):
    anim, reason = decode(name)
    check(isinstance(anim, emojianim.AnimatedEmoji)
          and len(anim.frames) == 3 and anim.delays == [50, 80, 120],
          '%s decodes to 3 frames with delays 50/80/120 (%r)'
          % (name, reason))
anim, reason = decode('s.png')
check(anim is emojianim._STATIC, 'a still PNG decodes to no animation')

real_frames = emojianim.MAX_FRAMES
emojianim.MAX_FRAMES = 2
anim, _reason = decode('a.png')
check(anim is emojianim._STATIC, 'more frames than the cap stays static')
emojianim.MAX_FRAMES = real_frames
real_dimension = emojianim.MAX_DIMENSION
emojianim.MAX_DIMENSION = 8
anim, _reason = decode('a.png')
check(anim is emojianim._STATIC, 'a side over the cap stays static')
emojianim.MAX_DIMENSION = real_dimension

# The bank keeps static URLs and learns the originals

bank = textemoji.emoji_bank_from([
    {'shortcode': 'moving', 'static_url': BASE + 's.png',
     'url': BASE + 'a.png'},
    {'shortcode': 'still', 'static_url': BASE + 's2.png',
     'url': BASE + 's2.png'}])
check(bank['moving'] == BASE + 's.png'
      and emojianim.animated_url(BASE + 's.png') == BASE + 'a.png',
      'emoji_bank_from keeps the static URL and registers the original')
check(emojianim.animated_url(BASE + 's2.png') is None,
      'an emoji whose url equals its static_url is not animated')

# Widgets

window = Gtk.Window()
box = Gtk.Box()
window.set_child(box)
window.set_default_size(200, 100)
window.present()


def make(url=BASE + 'a.png', static=BASE + 's.png'):
    image = emojianim.EmojiImage(pixel_size=16)
    image.bind(static, url)
    box.append(image)
    return image


gate = {'open': True}
real_gate = emojianim.gate_open
emojianim.gate_open = lambda _widget: gate['open']

first, second = make(), make()
manager = emojianim.manager()
check(pump(lambda: first.anim is not None and second.anim is not None),
      'both images show the animation once decoded')
check(first.anim is second.anim and first.get_paintable() is first.anim,
      'two images of one URL share one paintable')
check(first.anim.users == 2 and manager.clock.active == {first.anim}
      and manager.clock.source != 0,
      'one animation and one clock source serve both images')

frames = []
first.anim.connect('invalidate-contents', lambda _p: frames.append(1))
check(pump(lambda: len(frames) >= 4), 'the paintable advances frames')

box.set_visible(False)
settle(0.05)
check(first.anim is None and second.anim is None
      and not manager.clock.active and manager.clock.source == 0,
      'unmapping both stops the clock and detaches the images')
check(isinstance(first.get_paintable(), type(first._static))
      and first._static is not None,
      'an unmapped image shows its static texture')
count = len(frames)
settle(0.4)
check(len(frames) == count, 'no frames advance while nothing is mapped')
box.set_visible(True)
check(pump(lambda: first.anim is not None and manager.clock.source != 0),
      'mapping again resumes the animation')

# Gates, replaced and real

gate['open'] = False
emojianim._on_gate_changed()
check(first.anim is None and second.anim is None
      and not manager.clock.active and manager.clock.source == 0,
      'a closed gate shows the static frame and stops the clock')
gate['open'] = True
emojianim._on_gate_changed()
check(first.anim is not None and manager.clock.source != 0,
      'an opened gate starts the animation again')

emojianim.gate_open = real_gate
settings = Gtk.Settings.get_for_display(window.get_display())
real_motion = settings.get_property('gtk-interface-reduced-motion')
settings.set_property('gtk-interface-reduced-motion',
                      Gtk.ReducedMotion.REDUCE)
check(pump(lambda: first.anim is None and second.anim is None),
      'reduced motion stops every image live')
check(manager.clock.source == 0, 'reduced motion leaves no clock running')
settings.set_property('gtk-interface-reduced-motion', real_motion)
check(pump(lambda: first.anim is not None and second.anim is not None),
      'leaving reduced motion starts them again')
animations = settings.get_property('gtk-enable-animations')
settings.set_property('gtk-enable-animations', False)
check(pump(lambda: first.anim is None),
      'animations disabled stops them live')
settings.set_property('gtk-enable-animations', animations)
check(pump(lambda: first.anim is not None), 'animations enabled restarts')
emojianim.gate_open = lambda _widget: gate['open']

# A still image under an animated URL, and a failing one

still = make(url=BASE + 's.png', static=BASE + 'a.png')
settle(0.5)
check(still.anim is None and manager.store.get(BASE + 's.png')
      is emojianim._STATIC and still.animated_url is not None,
      'a still image behind an animated URL stays static and is remembered')
broken = make(url=BASE + 'nothing.png')
settle(0.5)
check(broken.anim is None and not manager.fetcher.inflight(),
      'a failing fetch leaves the static image and nothing in flight')

# A failure is remembered: unmapping and mapping again asks no more

check(hits.get('/nothing.png') == 1 and not broken.anim
      and manager._has_failed(BASE + 'nothing.png'),
      'the failing URL was asked for once and is remembered as failed')
broken.set_visible(False)
settle(0.05)
broken.set_visible(True)
settle(0.4)
check(hits.get('/nothing.png') == 1,
      'a failed URL is not fetched again on the next map')

# A late decode for A never lands on an image rebound to B

calls = []
real_fetch = manager.fetcher.fetch
manager.fetcher.fetch = lambda url, waiter, cancellable=None, key=None: \
    calls.append((url, waiter, cancellable))
late = make(url=BASE + 'a.png?late-a', static=BASE + 's.png')
check(pump(lambda: bool(calls)), 'the held fetch for A was requested')
late.bind(BASE + 's.png', BASE + 'a.png?late-b')
check(calls[0][2].is_cancelled(),
      'rebinding cancels the previous URL\'s interest')
done_a, _r = decode('a.png')
calls[0][1](done_a, None)
check(late.anim is None, 'A completing after a rebind to B does not show A')
pump(lambda: len(calls) > 1)
calls[-1][1](done_a, None)
check(late.anim is done_a, 'the current URL\'s result is shown')
manager.fetcher.fetch = real_fetch
box.remove(late)
late = None

# Unmapping before the deferral fires starts no fetch

emojianim.DEFER_MS = 300
quick = make(url=BASE + 'a.png?quick')
pump(lambda: quick.get_mapped())
quick.set_visible(False)
settle(0.7)
check(hits.get('/a.png?quick') is None and not quick._defer,
      'unmapping before the deferral fires starts no fetch')
emojianim.DEFER_MS = 20
box.remove(quick)
quick = None

# The watchdog gives up on a hung glycin load and clears the request


class HungLoader:
    started = []

    @staticmethod
    def new_for_bytes(_data):
        return HungLoader()

    def load_async(self, cancellable, _callback):
        HungLoader.started.append(cancellable)


class FakeGly:
    Loader = HungLoader


real_gly, real_watchdog = emojianim.Gly, emojianim.WATCHDOG_MS
emojianim.Gly, emojianim.WATCHDOG_MS = FakeGly, 150
hung = make(url=BASE + 'a.png?hung')
check(pump(lambda: bool(HungLoader.started)), 'the hung load started')
check(pump(lambda: BASE + 'a.png?hung' in manager.failed)
      and not manager.fetcher.inflight() and emojianim._decoding == 0,
      'the watchdog fails the load, clears it and frees the decode slot')
box.remove(hung)
hung = None

# Decodes are capped, the rest wait

HungLoader.started.clear()
results = []
real_max = emojianim.MAX_DECODES
for _n in range(5):
    emojianim._decode(read('a.png'), None, None,
                      lambda value, reason: results.append(reason))
check(len(HungLoader.started) == real_max,
      'only MAX_DECODES decodes run at once')
check(pump(lambda: len(results) == 5, 3),
      'the queued decodes run as slots free up')
emojianim.Gly, emojianim.WATCHDOG_MS = real_gly, real_watchdog

# Freed

shared = first.anim
refs = []
for image in (first, second, still, broken):
    refs.append(image.weak_ref())
    box.remove(image)
del image
first = second = still = broken = None
gc.collect()
settle(0.1)
check(all(ref() is None for ref in refs), 'removed images are freed')
if not all(ref() is None for ref in refs):
    for ref in refs:
        obj = ref()
        if obj is not None:
            print([type(r).__name__ + ':' + repr(r)[:100]
                   for r in gc.get_referrers(obj)])
check(shared.users == 0 and not manager.clock.active
      and manager.clock.source == 0 and not manager.mapped,
      'no user, clock entry or registration outlives its image')

# The renderer: InlineEmojiText anchors EmojiImages that animate and are
# freed with the text

body = textemoji.InlineEmojiText(emoji_size=16)
body.set_segments([{'text': 'hi :moving: and :moving:', 'href': None}],
                  bank)
box.append(body)


def body_images():
    found, child = [], body.get_first_child()
    while child is not None:
        if isinstance(child, emojianim.EmojiImage):
            found.append(child)
        child = child.get_next_sibling()
    return found


images = body_images()
check(len(images) == 2, 'InlineEmojiText draws each emoji as an EmojiImage')
check(pump(lambda: all(i.anim is not None for i in images))
      and images[0].anim is images[1].anim,
      'post-body emoji animate and share one paintable')
body_ref = body.weak_ref()
image_refs = [i.weak_ref() for i in images]
users = images[0].anim
box.remove(body)
images = body = None
gc.collect()
settle(0.1)
check(body_ref() is None and all(r() is None for r in image_refs),
      'the text and its emoji images are freed after removal')
check(users.users == 0 and manager.clock.source == 0,
      'removing the text stops its clock')

# The store's budget

store = emojianim.BudgetStore(100)


class Entry:
    def __init__(self, cost, users=0):
        self.cost = cost
        self.users = users


store.put('a', Entry(60))
store.put('b', Entry(60, users=1))
store.put('c', Entry(60))
check(not store.has('a') and store.has('b') and store.has('c'),
      'the store evicts the oldest idle entry past the budget')
store.put('d', Entry(60))
check(store.has('b') and not store.has('c') and store.has('d'),
      'the store never evicts an entry an image shows')
store = emojianim.BudgetStore(100)
store.put('a', Entry(60))
store.put('e', emojianim._STATIC)
check(store.has('a') and store.has('e') and store.bytes == 60,
      'a still marker costs nothing')

# No glycin

real_gly = emojianim.Gly
emojianim.Gly = None
check(not emojianim.available(), 'available() is False without Gly')
lone = make(url=BASE + 'a-other.png')
settle(0.3)
check(lone.anim is None and not manager.fetcher.inflight(),
      'without Gly the image stays static and nothing is fetched')
box.remove(lone)
emojianim.Gly = real_gly

window.destroy()
server.shutdown()

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
print('all ok')
