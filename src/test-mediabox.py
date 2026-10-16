#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Attachment tiles must be reachable without a pointer.

Exits 77 when no display is available."""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()

from src.mediabox import MediaBox  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


Adw.init()
Gtk.init()

box = MediaBox()
opened = []
box._open = opened.append
box.set_media([{
    'type': 'image',
    'url': 'https://example.social/a.png',
    'preview_url': 'https://example.social/a.png',
    'description': 'A cat',
}], sensitive=False)

# The grid is the first child of the content box.
content = box.get_first_child()
grid_box = content.get_first_child()
grid = grid_box.get_first_child()
tile = grid.get_child_at(0, 0)

check('the image tile is focusable',
      tile is not None and tile.get_focusable())
check('the image tile is a button for assistive tech',
      tile.get_accessible_role() == Gtk.AccessibleRole.BUTTON)

controllers = [c for c in tile.observe_controllers()]
check('the image tile has a key controller',
      any(isinstance(c, Gtk.EventControllerKey) for c in controllers))

# Activate the key path the same way EventControllerKey would.
box._on_tile_key(None, Gdk.KEY_Return, 0, 0, 0)
check('Return on the tile opens the attachment', opened == [0])

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
print('all mediabox checks passed')
