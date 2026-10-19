#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The sidebar and web-column breakpoints hold at their boundaries.

The sidebar collapses at 600sp and the web column pins at 1100sp. A
breakpoint that is off by one, or that stops firing because a condition
changed, is invisible in a screenshot at a comfortable size, so the
invariant is asserted around both boundaries at every pixel:

    split_view.collapsed == (width <= 600)
    web_split.collapsed  == (width < 1100)

``width`` is the realised window width, not the requested one: under
X11 the decoration shadow makes the window a constant number of pixels
smaller, and that constant is the backend's business, not the test's.

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


def settle(width, height=800):
    """Resize and pump until the realised width stops changing."""
    window.set_default_size(width, height)
    window.present()
    context = GLib.MainContext.default()
    last, stable = None, 0
    for _ in range(400):
        while context.iteration(False):
            pass
        current = window.get_width()
        if current == last:
            stable += 1
            if stable >= 5:
                return current
        else:
            last, stable = current, 0
        GLib.usleep(2000)
    return window.get_width()


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.main_stack.set_visible_child_name('shell')

# The requested size is not the realised one (the backend subtracts a
# constant), so the constant is measured once while the window is small,
# where its minimum size cannot mask the request.
offset = 800 - settle(800)

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

# Both edges were crossed during the ascending scan (the window cannot be
# asked to shrink once its content has grown, so the scan only grows).
for edge in (600, 601, 1100):
    check('the scan realised %d px' % edge, edge in realised, edge)
check('the scan sampled a collapsed web column',
      any(width < 1100 for width in realised),
      min(realised) if realised else None)
check('the scan sampled a pinned web column',
      any(width > 1100 for width in realised),
      max(realised) if realised else None)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all breakpoint checks passed')
