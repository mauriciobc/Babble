#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The sidebar and web-column breakpoints hold at their boundaries.

The sidebar collapses at 600sp and the web column pins at 1100sp. A
breakpoint that is off by one, or that stops firing because a condition
changed, is invisible in a screenshot at a comfortable size, so the
invariant is asserted around both boundaries at every pixel:

    split_view.collapsed == (width <= 600)
    web_split.collapsed  == (width < 1100)

``width`` is the realised window width (``get_width()``), not the
requested one: the backend may realise a different size (under X11 the
decoration shadow subtracts a constant), and the invariants never use
or assume that inset. Every width is read on the window's frame clock
and only accepted once it holds across several painted frames; a width
that never settles is a failure. The test also requires that 599, 600,
601, 1099, 1100 and 1101 px were each realised, so both sides of both
edges were really checked.

The project is desktop-first for now, so a screen too small to exercise
the wide branch skips rather than fails (exit 77).

Exits 77 without a display or a compiled bundle."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

REQUIRED = (1280, 1024)

Adw.init()
display = Gdk.Display.get_default()
monitors = display.get_monitors() if display is not None else None
if monitors is None or monitors.get_n_items() == 0:
    print('SKIP - no monitor to measure')
    sys.exit(77)
geometry = monitors.get_item(0).get_geometry()
if geometry.width < REQUIRED[0] or geometry.height < REQUIRED[1]:
    print('SKIP - the test needs a %dx%d screen to reach the wide layout, '
          'got %dx%d' % (REQUIRED[0], REQUIRED[1],
                         geometry.width, geometry.height))
    sys.exit(77)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import main as babble_main  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + str(detail)) if detail else ''))


STABLE_FRAMES = 3
DEADLINE_US = 3 * 1000 * 1000
unsettled = []


def settle(width, height=800):
    """Resize, then wait for real painted frames until the width holds.

    Reading the width repeatedly inside one main-loop burst returns the
    previous request's size, because the configure round-trip lands a
    frame or more later. So every width sample is taken on the frame
    clock's after-paint signal, a redraw is queued to keep frames coming,
    and the width counts as settled once it is unchanged across
    STABLE_FRAMES consecutive painted frames. A width that never settles
    before the deadline is recorded as a failure rather than returned as
    if it were good."""
    window.set_default_size(width, height)
    window.present()
    context = GLib.MainContext.default()
    samples = []

    def on_after_paint(_clock):
        samples.append(window.get_width())
        window.queue_draw()

    clock = window.get_frame_clock()
    handler = clock.connect('after-paint', on_after_paint)
    window.queue_draw()
    deadline = GLib.get_monotonic_time() + DEADLINE_US
    try:
        while GLib.get_monotonic_time() < deadline:
            context.iteration(True)
            tail = samples[-STABLE_FRAMES:]
            if len(samples) > STABLE_FRAMES and len(set(tail)) == 1:
                return tail[0]
    finally:
        clock.disconnect(handler)
    unsettled.append((width, samples[-STABLE_FRAMES:]))
    return window.get_width()


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.main_stack.set_visible_child_name('shell')

# The first present() maps the window; settle once so its frame clock
# exists before the scan connects to it.
window.present()
settle(800)

collapsed_mismatches = []
web_mismatches = []
realised = []
for requested in list(range(590, 625)) + list(range(1090, 1125)):
    width = settle(requested)
    realised.append(width)
    collapsed = window.split_view.get_collapsed()
    web_collapsed = window.web_split.get_collapsed()
    if collapsed != (width <= 600):
        collapsed_mismatches.append((requested, width, collapsed))
    if web_collapsed != (width < 1100):
        web_mismatches.append((requested, width, web_collapsed))

check('the scan actually resized the window',
      len(set(realised)) > 50, len(set(realised)))
check('the sidebar collapses at exactly 600sp', not collapsed_mismatches,
      collapsed_mismatches[:3])
check('the web column pins at exactly 1100sp', not web_mismatches,
      web_mismatches[:3])

check('every requested width settled across %d frames' % STABLE_FRAMES,
      not unsettled, unsettled[:3])

# The first request shrinks the window from 800 to 590; from there the
# scan grows one pixel at a time through 600/601 and 1099/1100. Each
# side of each edge must have been realised, otherwise the invariants
# above were never put to the test.
for edge in (599, 600, 601, 1099, 1100, 1101):
    check('the scan realised %d px' % edge, edge in realised, edge)
print('# realised edges: %s' % sorted(
    w for w in set(realised) if w in (599, 600, 601, 1099, 1100, 1101)))

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all breakpoint checks passed')
