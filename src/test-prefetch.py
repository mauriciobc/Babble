#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Card prefetch: the shells just past the visible ones get their cards
built and their height measured while they are off screen, so a run that
scrolls into view shows cards it already has, at a height the list
already knows. Building cards after a shell is shown lays the shown row
out again on the next frame (Docs/performance-profiling.md).
Exits 77 (skip) without a display."""

import gc
import os
import sys

os.environ.setdefault('GTK_A11Y', 'none')

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import mediacache, statusrun, timelinedata, timelinepage  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def busy():
    return bool(statusrun._pending_shells
                or statusrun._prefetch_source is not None
                or statusrun._prefetch_wait is not None)


def settle(seconds=5.0, quiet=0.2):
    context = GLib.MainContext.default()
    now = GLib.get_monotonic_time
    deadline = now() + int(seconds * 1000000)
    last = now()
    while now() < deadline:
        if context.iteration(False):
            last = now()
        elif not busy() and now() - last >= quiet * 1000000:
            return
        else:
            GLib.usleep(2000)


def status(number):
    words = ' '.join(['word'] * (5 + number % 40))
    return {'id': '%05d' % number, 'content': '<p>%s</p>' % words,
            'account': {'acct': 'user%d' % number, 'id': str(number),
                        'display_name': 'User %d' % number},
            'created_at': '2026-01-01T00:00:00.000Z',
            'media_attachments': []}


def shells():
    found = []
    for ref in statusrun._shells:
        shell = ref()
        if shell is not None and shell._position() is not None:
            found.append((shell._position(), shell))
    return sorted(found, key=lambda entry: entry[0])


Adw.init()
mediacache.AvatarCache.load = staticmethod(lambda avatar, account: None)

model = timelinedata.TimelineModel()
model.append_old([status(n) for n in range(80, 0, -1)])
runs = statusrun.RunModel(model)
view = Gtk.ListView(model=Gtk.NoSelection(model=runs),
                    factory=timelinepage.TimelineFactory(None))
scroller = Gtk.ScrolledWindow(child=view)
window = Gtk.Window(default_width=600, default_height=700, child=scroller)
window.present()
settle()

entries = shells()
shown = [position for position, shell in entries if shell.get_mapped()]
check('some runs are shown', bool(shown))
last = max(shown)
ahead = [shell for position, shell in entries
         if last < position <= last + statusrun.PREFETCH_AHEAD]
check('the runs just past the visible ones are instantiated',
      len(ahead) == statusrun.PREFETCH_AHEAD)
check('their cards are built while they are off screen',
      all(len(shell._cards) == len(shell._run.items) for shell in ahead))
check('and kept hidden',
      all(not row.get_visible() for shell in ahead
          for row in shell._cards.values()))
check('their height is measured and pinned',
      all(statusrun._known_shell_height(shell, shell._run) > 64
          for shell in ahead))
far = [shell for position, shell in entries
       if position > last + statusrun.PREFETCH_AHEAD + 2]
check('runs further away are left alone',
      far and all(not shell._cards for shell in far))

# Scroll the first prefetched run into view: it shows its cards at once,
# with no placeholder and no idle build, at the height pinned for it.
target = ahead[0]
pinned = statusrun._known_shell_height(target, target._run)
built = dict(target._cards)
adjustment = scroller.get_vadjustment()
adjustment.set_value(adjustment.get_value() + adjustment.get_page_size())
for _attempt in range(200):
    if target.get_mapped() and target.get_height() > 0:
        break
    GLib.MainContext.default().iteration(False)
check('a prefetched run shows its cards as soon as it is mapped',
      target.get_mapped() and target.rows
      and not target.has_css_class('status-run-pending'))
check('the cards it shows are the ones prefetched',
      all(built[item] is row for item, row in target._cards.items()))
check('it is as tall as the height pinned for it',
      abs(target.get_height() - pinned) <= 1,
      '%d vs %d' % (target.get_height(), pinned))
settle()

refs = [shell.weak_ref() for _position, shell in shells()]
del entries, ahead, far, target, built
window.destroy()
view = scroller = window = None
settle()
gc.collect()
gc.collect()
check('every shell is freed with its list', all(ref() is None for ref in refs))
check('and prefetch stops', not busy())

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
