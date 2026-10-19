#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wrapped Style menu changes the saved image.

The menu once swapped a CSS class on the card that no rule matched, so
every style rendered the same. The card is what Save as Image exports
(WrappedDialog._screenshot), so the card is rendered for each style and
its pixels are read back: the four styles must look different from each
other, Accent must actually be the accent, and on Accent the chart bars
must stop using the accent colour, or they would vanish into it.

Exits 77 (skip) without a display or a compiled bundle."""

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

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mediacache, wrapped  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + str(detail)) if detail else ''))


def pump(iterations=40):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(3000)


class _Client:
    token = None
    instance_info = None
    instance = 'https://example.org'

    def request_async(self, *_args, **_kwargs):
        pass


def pixels(texture):
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    return data.get_data(), stride


def sample(texture, fx, fy):
    """RGBA at a fraction (fx, fy) of the texture."""
    data, stride = pixels(texture)
    x = int(texture.get_width() * fx)
    y = int(texture.get_height() * fy)
    offset = y * stride + x * 4
    return tuple(data[offset:offset + 4])


def distance(a, b):
    return sum(abs(p - q) for p, q in zip(a[:3], b[:3], strict=True))


mediacache.AvatarCache.load = staticmethod(lambda avatar, account: None)
Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.present()
pump()

report = {'year': 2024, 'data': {
    'archetype': 'lurker',
    'time_series': [{'month': month, 'statuses': month % 4,
                     'followers': month % 2, 'following': 1}
                    for month in range(1, 13)],
    'top_hashtags': [], 'most_used_apps': [],
    'commonly_interacted_with_accounts': [], 'top_statuses': {}}}
dialog = wrapped.WrappedDialog(window, _Client(), year=2024)
dialog.data = {'annual_reports': [report], 'accounts': []}
dialog.present(window)
dialog._render(report)
pump(80)

# The padding strip along the top edge is card background with no
# content over it; the middle of the left padding shows the gradient
# away from its ends.
POINTS = ((0.5, 0.004), (0.02, 0.5))
looks = {}
for key, _label in wrapped.STYLES:
    dialog.activate_action('wrapped.style', GLib.Variant.new_string(key))
    pump()
    texture = dialog._screenshot()
    check('the %s style renders an image' % key, texture is not None)
    if texture is not None:
        looks[key] = [sample(texture, fx, fy) for fx, fy in POINTS]

if len(looks) == len(wrapped.STYLES):
    keys = [key for key, _label in wrapped.STYLES]
    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            gap = max(distance(a, b)
                      for a, b in zip(looks[first], looks[second], strict=True))
            check('%s and %s look different' % (first, second), gap > 30,
                  '%s %s' % (looks[first], looks[second]))

    accent = Adw.StyleManager.get_default().get_accent_color_rgba()
    accent_rgb = tuple(round(c * 255) for c in
                       (accent.red, accent.green, accent.blue))
    window_rgb = looks['window'][0][:3]
    check('Accent is closer to the accent colour than Default is',
          distance(looks['accent'][0], accent_rgb)
          < distance(window_rgb, accent_rgb),
          '%s vs accent %s' % (looks['accent'][0], accent_rgb))

charts = []
pending = [dialog.card]
while pending:
    node = pending.pop()
    if isinstance(node, wrapped.MonthlyChart):
        charts.append(node)
    child = node.get_first_child()
    while child is not None:
        pending.append(child)
        child = child.get_next_sibling()
accented = [chart for chart in charts if chart.accent]
check('the report has an accent chart', bool(accented))
if accented:
    chart = accented[0]
    accent = Adw.StyleManager.get_default().get_accent_color_rgba()
    dialog.activate_action('wrapped.style', GLib.Variant.new_string('window'))
    pump()
    check('on Default the bars use the accent colour',
          chart._color().equal(accent))
    dialog.activate_action('wrapped.style', GLib.Variant.new_string('accent'))
    pump()
    check('on Accent the bars leave the accent colour',
          not chart._color().equal(accent))
    check('on Accent the bars use the card foreground',
          chart._color().equal(chart.get_color()))

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all Wrapped style checks passed')
