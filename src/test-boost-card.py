#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A boost card has a constant size: its summary is a Gtk.Inscription,
measured from font metrics rather than by laying the text out, and its
thumbnail sits over a fixed slot. A card is the same height whatever its
text or picture, and GTK never measures it at a width. Building cards
was the largest single cost in the card-population idles over budget
(Docs/performance-profiling.md).

With BOOST_CARD_PNG=path, also saves a rendering of three cards.
Exits 77 (skip) without a display."""

import os
import sys

os.environ.setdefault('GTK_A11Y', 'none')

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import mediabox, mediacache  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def settle(seconds=0.5):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1000000)
    while GLib.get_monotonic_time() < deadline:
        if not context.iteration(False):
            GLib.usleep(2000)


def status(n, content, name='Ada Lovelace'):
    return {'id': str(n), 'content': content,
            'account': {'id': str(100 + n), 'display_name': name},
            'media_attachments': []}


def inscriptions(widget):
    found, pending = [], [widget]
    while pending:
        node = pending.pop(0)
        if isinstance(node, Gtk.Inscription):
            found.append(node)
        child = node.get_first_child()
        while child is not None:
            pending.append(child)
            child = child.get_next_sibling()
    return found


Adw.init()
mediacache.AvatarCache.load = staticmethod(lambda avatar, account: None)
mediacache._TextureCache.load = staticmethod(
    lambda url, picture, cover=None: None)

LONG = '<p>' + ' '.join(['A boost long enough to need more than three '
                         'lines in a card of this width.'] * 6) + '</p>'
short = mediabox._BoostCard(1, status(1, '<p>Short.</p>'), None)
long = mediabox._BoostCard(
    2, status(2, LONG, name='A display name far too long for the card '
                            'header to show in full'), None)
texts = [i.get_text() for i in inscriptions(long)]
check('the summary is an inscription',
      len(texts) == 1 and texts[0].startswith('A boost long enough'),
      repr(texts))
check('a card is constant-size, never measured at a width',
      long.get_request_mode() == Gtk.SizeRequestMode.CONSTANT_SIZE)

heights = []
for card in (short, long):
    _min, natural, _b, _nb = card.measure(Gtk.Orientation.VERTICAL,
                                          mediabox.BOOST_CARD_WIDTH)
    heights.append(natural)
check('a card is as tall whatever its text', heights[0] == heights[1],
      repr(heights))
with_media = mediabox._BoostCard(4, dict(
    status(4, '<p>Short.</p>'), media_attachments=[
        {'type': 'image', 'preview_url': 'https://example.invalid/a.png',
         'url': 'https://example.invalid/a.png'}]), None)
check('a card with a thumbnail is constant-size too',
      with_media.get_request_mode() == Gtk.SizeRequestMode.CONSTANT_SIZE)
before = with_media.measure(Gtk.Orientation.VERTICAL, -1)[1]
picture = None
pending = [with_media]
while pending:
    node = pending.pop()
    if isinstance(node, Gtk.Picture):
        picture = node
    child = node.get_first_child()
    while child is not None:
        pending.append(child)
        child = child.get_next_sibling()
picture.set_paintable(Gdk.MemoryTexture.new(
    800, 1200, Gdk.MemoryFormat.R8G8B8A8,
    GLib.Bytes.new(b'\x80' * (800 * 1200 * 4)), 800 * 4))
check('a thumbnail landing does not change the card\'s size',
      with_media.measure(Gtk.Orientation.VERTICAL, -1)[1] == before)
summary = next((i for i in inscriptions(long)
                if i.get_text().startswith('A boost')), None)
check('the summary keeps a three-line budget',
      summary is not None and summary.get_nat_lines() == 3
      and summary.get_text_overflow()
      == Gtk.InscriptionOverflow.ELLIPSIZE_END)
check('the card still names its post for a screen reader',
      long._summary.startswith('A boost long enough'))

# Laid out in a window, nothing is cut off sideways: the card keeps its
# width rather than growing to its text.
window = Gtk.Window(default_width=1200, default_height=300)
strip = Gtk.Box(spacing=mediabox.BOOST_CARD_SPACING, margin_top=12,
                margin_start=12, valign=Gtk.Align.START,
                halign=Gtk.Align.START)
third = mediabox._BoostCard(3, status(3, '<p>Two lines of text, perhaps, '
                                         'at this width.</p>',
                                      name='Grace'), None)
for card in (short, long, third, with_media):
    strip.append(card)
window.set_child(strip)
window.present()
settle()
check('a card keeps its width',
      long.get_width() == mediabox.BOOST_CARD_WIDTH, str(long.get_width()))

target = os.environ.get('BOOST_CARD_PNG')
if target:
    paintable = Gtk.WidgetPaintable.new(strip)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, strip.get_width(), strip.get_height())
    node = snapshot.to_node()
    renderer = window.get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(
        0, 0, strip.get_width(), strip.get_height()))
    texture.save_to_png(target)
    print('# saved %s' % target)

window.destroy()
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
