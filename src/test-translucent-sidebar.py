#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The translucent sidebar stays translucent at every shell width.

The web column is an AdwOverlaySplitView wrapping the shell's own split
view. Once it pins beside the timeline (1100sp) its content pane holds
the whole shell, and an unscoped `.content-pane` rule painted the window
background behind the sidebar, so the sidebar turned opaque from that
width up. Nothing about that shows in the widget tree, so the window is
rendered and the sidebar's pixels are read back.

Checks, with the preference on: the sidebar is see-through with the web
column collapsed and with it pinned; the timeline stays opaque. With the
preference off, the sidebar is opaque (proof that the measurement can
tell the two apart).

Needs a 1280x1024 screen to reach the pinned layout; skips (exit 77)
when the screen is smaller, without a display, or without a bundle."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Graphene', '1.0')

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

Adw.init()
display = Gdk.Display.get_default()
monitors = display.get_monitors() if display is not None else None
if monitors is None or monitors.get_n_items() == 0:
    print('SKIP - no monitor to measure')
    sys.exit(77)
geometry = monitors.get_item(0).get_geometry()
if geometry.width < 1280 or geometry.height < 1024:
    print('SKIP - needs a 1280x1024 screen to pin the web column, got '
          '%dx%d' % (geometry.width, geometry.height))
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
    window.set_default_size(width, height)
    window.present()
    context = GLib.MainContext.default()
    for _ in range(80):
        while context.iteration(False):
            pass
        GLib.usleep(3000)


def alpha_at(widget):
    """Alpha of the rendered window at the centre of *widget*."""
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    renderer = window.get_native().get_renderer()
    texture = renderer.render_texture(
        snapshot.to_node(), Graphene.Rect().init(0, 0, width, height))
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    found, point = widget.compute_point(
        window, Graphene.Point().init(widget.get_width() / 2,
                                      widget.get_height() / 2))
    if not found:
        return None
    x, y = int(point.x), int(point.y)
    return data.get_data()[y * stride + x * 4 + 3]


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.main_stack.set_visible_child_name('shell')
window.settings.set_boolean('translucent-sidebar', True)
sidebar = window.split_view.get_sidebar()
content = window.split_view.get_content()

settle(900)
check('the web column is collapsed at 900 px',
      window.web_split.get_collapsed())
narrow = alpha_at(sidebar)
check('the sidebar is translucent with the web column collapsed',
      narrow is not None and narrow < 255, narrow)

settle(1200)
check('the web column is pinned at 1200 px',
      not window.web_split.get_collapsed())
wide = alpha_at(sidebar)
check('the sidebar is translucent with the web column pinned',
      wide is not None and wide < 255, wide)
check('the timeline stays opaque', alpha_at(content) == 255,
      alpha_at(content))

window.settings.set_boolean('translucent-sidebar', False)
settle(1200)
check('with the preference off the sidebar is opaque',
      alpha_at(sidebar) == 255, alpha_at(sidebar))

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all translucent sidebar checks passed')
