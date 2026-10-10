#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the Babble timeline widgets with REAL data from
mastodon.social's anonymous public timeline, proving the row rendering
path used by the home timeline. Exits 77 (skip) when the network is
unreachable."""

import os
import sys
import time as time_module

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gtk

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src.textemoji import InlineEmojiText, status_emoji_bank  # noqa: E402
from src.timeline import (BoostCarouselItem, StatusItem, StatusRow,  # noqa: E402
                          TimelineModel, TimelinePage, COLUMN_WIDTH,
                          parse_content, relative_time)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# Unit checks (no display needed)
segments = parse_content('<p>Hello <a href="https://example.org">'
                         'world</a> &amp; friends</p><p>Second</p>')
check('content text preserved', any('Hello' in s['text'] for s in segments))
check('link href extracted',
      any(s['href'] == 'https://example.org' for s in segments))
check('no raw html leaks', all('<p>' not in s['text'] for s in segments))
check('relative time', relative_time('2020-01-01T00:00:00.000Z') != '')

model = TimelineModel()
items = model.append_old([{'id': '2', 'content': 'b',
                           'account': {'acct': 'b'},
                           'created_at': '2026-01-01T00:00:00.000Z'},
                          {'id': '1', 'content': 'a',
                           'account': {'acct': 'a'},
                           'created_at': '2026-01-01T00:00:00.000Z'}])
check('model appends page', model.get_n_items() == 2)
check('model is a Gio.ListModel', model.get_item_type() == StatusItem.__gtype__)
check('oldest id tracked', model.oldest_id == '1')
model.insert_new([{'id': '3', 'content': 'c', 'account': {'acct': 'c'},
                   'created_at': '2026-01-01T00:00:00.000Z'}])
check('insert_new prepends', model.get_item(0).status_id == '3')
check('no duplicates', model.insert_new(
    [{'id': '3', 'content': 'c', 'account': {'acct': 'c'}}]) == 0)

# Stream handlers (delete / status.update) call these model methods; a
# missing one was an AttributeError in the live-update path.
check('remove_status drops the row',
      model.remove_status('2') and model.get_n_items() == 2)
check('remove_status is a no-op for an unknown id',
      model.remove_status('999') is False)
_before = model.get_n_items()
check('replace_status updates in place',
      model.replace_status({'id': '3', 'content': 'edited',
                            'account': {'acct': 'c'},
                            'created_at': '2026-01-01T00:00:00.000Z'})
      and model.get_n_items() == _before
      and 'edited' in model.get_item(0).status['content'])
model.get_item(0).ui['expanded'] = True
model.replace_status({'id': '3', 'content': 'again',
                      'account': {'acct': 'c'},
                      'created_at': '2026-01-01T00:00:00.000Z'})
check('replace_status keeps per-post view state',
      model.get_item(0).ui.get('expanded') is True)
check('replace_status ignores an unknown id',
      model.replace_status({'id': '999'}) is False)
check('replace_status needs an id', model.replace_status({}) is False)
model.insert_new([{'id': '7', 'account': {'acct': 'booster'},
                   'created_at': '2026-01-01T00:00:00.000Z',
                   'reblog': {'id': '3', 'content': 'x',
                              'account': {'acct': 'c'}}}])
model.remove_status('3')
check('a boost of a deleted post goes too',
      all(model.get_item(i).status_id != '3'
          for i in range(model.get_n_items())))

# --- Inline emoji gap fixtures (unit level, no network) ----------------
# Post bodies now render :shortcode: custom emojis declared in the
# status payload as inline images; undeclared shortcodes stay literal.

EMOJI_BODY = '<p>Riding :ride4: past the :python: and the :gopher:</p>'
EMOJI_URL_BASE = 'https://emojis.example.org/custom/'

fixture_status = {
    'id': '41',
    'content': EMOJI_BODY,
    'account': {'acct': 'dash'},
    'created_at': '2026-10-01T00:00:00.000Z',
    'emojis': [{'shortcode': 'ride4', 'static_url': EMOJI_URL_BASE + 'ride4.png'},
               {'shortcode': 'python', 'static_url': EMOJI_URL_BASE + 'snake.png'},
               {'shortcode': 'gopher', 'static_url': EMOJI_URL_BASE + 'gopher.png'}],
}
bank = status_emoji_bank(fixture_status)
check('bank merges declared emojis', bank['ride4'] == EMOJI_URL_BASE + 'ride4.png')
check('bank keeps dash-prefixed verbatim',
      status_emoji_bank({
          'account': {'acct': 'dash',
                      'emojis': [{'shortcode': 'dash-gopher',
                                  'static_url': EMOJI_URL_BASE + 'dg.png'}]},
          'emojis': []})['dash-gopher'] == EMOJI_URL_BASE + 'dg.png')

body = InlineEmojiText(shorten_links=True, paragraph_gap=True)
body.set_segments(parse_content(EMOJI_BODY), bank)
def _images_of(widget, out):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Image):
            out.append(child)
        _images_of(child, out)
        child = child.get_next_sibling()
    return out

anchor_images = _images_of(body, [])
buffer_text = body.get_buffer().get_text(
    body.get_buffer().get_start_iter(), body.get_buffer().get_end_iter(),
    True)
check('declared shortcodes render as images',
      len(anchor_images) == 3)
check('labeled shortcodes leave the buffer',
      ':ride4:' not in buffer_text and ':gopher:' not in buffer_text)
check('a11y label keeps raw shortcodes',
      ':ride4:' in body._raw and body._raw is not None)

missing = InlineEmojiText()
missing.set_segments(parse_content(EMOJI_BODY), {})
missing_text = missing.get_buffer().get_text(
    missing.get_buffer().get_start_iter(),
    missing.get_buffer().get_end_iter(), True)
check('undeclared shortcodes stay literal',
      ':ride4:' in missing_text and ':python:' in missing_text)
check('undeclared shortcodes draw no images',
      len(_images_of(missing, [])) == 0)

# Clicking a link must resolve the href: Gtk.TextView.get_iter_at_location
# returns (found, iter), so unpacking it wrongly raised AttributeError and
# links only worked from the keyboard.
link_body = InlineEmojiText()
link_body.set_segments([{'text': 'see ', 'href': None},
                        {'text': 'example.com',
                         'href': 'https://example.com/x'},
                        {'text': ' now', 'href': None}])
check('link segment registers a hit range',
      link_body._href_at_offset(6) == 'https://example.com/x')
check('plain text resolves to no link',
      link_body._href_at_offset(0) is None)
link_window = Gtk.Window()
link_window.set_child(link_body)
link_window.set_default_size(400, 100)
link_window.present()
for _ in range(60):
    while GLib.main_context_default().pending():
        GLib.main_context_default().iteration(False)
_iter = link_body.get_buffer().get_iter_at_offset(6)
_rect = link_body.get_iter_location(_iter)
check('a click on the link text resolves the href',
      link_body._href_at(_rect.x + 2, _rect.y + 2)
      == 'https://example.com/x')
link_window.set_child(None)

# Fixture-render probe: a bound StatusRow (the same path used by new,
# paginated and thread posts) must show inline Gtk.Images in its body.
Adw.init()
Gtk.init()

class _DummyClient:
    token = None

row = StatusRow()
row.bind(StatusItem(fixture_status), _DummyClient())

def collect_emojis(widget, out):
    if isinstance(widget, Gtk.Image) and widget.get_pixel_size() == 20:
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect_emojis(child, out)
        child = child.get_next_sibling()
    return out

emoji_window = Gtk.Window()
emoji_window.set_child(row)
emoji_window.set_default_size(1400, 950)
emoji_window.present()

deadline = time_module.monotonic() + 5.0
while time_module.monotonic() < deadline:
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
in_row = collect_emojis(row, [])
check('bound row inlines emoji images', len(in_row) == 3)
row_buffer = row.content.get_buffer().get_text(
    row.content.get_buffer().get_start_iter(),
    row.content.get_buffer().get_end_iter(), True)
check('bound row body has no literal declared shortcodes',
      ':ride4:' not in row_buffer and ':python:' not in row_buffer)

poll_status = {**fixture_status, 'poll': {
    'id': '7',
    'options': [{'title': 'Vote :ride4:',
                 'votes_count': 2},
                {'title': 'Abstain', 'votes_count': 1}],
    'votes_count': 3, 'expired': True, 'closed': True, 'own_votes': []}}
poll_row = StatusRow()
poll_row.bind(StatusItem(poll_status), _DummyClient())

def collect_poll_emojis(widget, out):
    if isinstance(widget, Gtk.Image) and widget.get_pixel_size() == 15:
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect_poll_emojis(child, out)
        child = child.get_next_sibling()
    return out

check('poll option titles inline declared emoji',
      len(collect_poll_emojis(poll_row, [])) == 1)

emoji_window.set_child(None)

# Live rendering check
Adw.init()
Gtk.init()

client = MastodonClient('https://fosstodon.org')
page = TimelinePage(client, mode='public')
window = Gtk.Window()
window.set_child(page)
window.set_default_size(600, 900)
window.present()

loop = GLib.MainLoop()
result = {'done': False}


def on_timeout():
    result['done'] = True
    loop.quit()


timeout_id = GLib.timeout_add_seconds(45, on_timeout)


def poll():
    if result['done']:
        return False
    if page.model.get_n_items() > 0 and page.stack.get_visible_child_name() == 'list':
        loop.quit()
    return True


GLib.timeout_add(200, poll)
loop.run()
if not result['done']:
    GLib.source_remove(timeout_id)

if page.stack.get_visible_child_name() != 'list':
    print('SKIP - public timeline unreachable (network error)')
    sys.exit(77)

check('real statuses loaded', page.model.get_n_items() > 0)
item = next(page.model.get_item(i) for i in range(page.model.get_n_items())
            if not isinstance(page.model.get_item(i), BoostCarouselItem))
check('status has author', bool(item.handle))

# Give the list view a moment to map and bind visible rows.
import time as time_module

deadline = time_module.monotonic() + 5.0
while time_module.monotonic() < deadline:
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
    if page.list_view.get_first_child() is not None:
        inner = page.list_view.get_first_child()
        if inner.get_first_child() is not None:
            break
row_widgets = []

def collect(widget):
    row_widgets.append(widget)
    if isinstance(widget, Gtk.Widget):
        child = widget.get_first_child()
        while child is not None:
            collect(child)
            child = child.get_next_sibling()

collect(page.list_view)
check('avatars rendered', any(isinstance(w, Adw.Avatar) for w in row_widgets))
check('content labels rendered',
      any(isinstance(w, Gtk.Label) for w in row_widgets))
check('action buttons rendered',
      sum(1 for w in row_widgets if isinstance(w, Gtk.Button)) >= 4)
pictures = [w for w in row_widgets if isinstance(w, Gtk.Picture)]
print('note - %d GtkPicture media widgets instantiated' % len(pictures))

# The column follows the window: at every width it is min(viewport, 660),
# centered, and never wider than the window. A large one-step shrink
# (1270 -> 500) used to leave margin-inflated minimums behind and push
# the cards off the right edge.
def settle(seconds):
    end = time_module.monotonic() + seconds
    while time_module.monotonic() < end:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass


for width in (1270, 500, 1270, 800):
    window.set_default_size(width, 900)
    settle(1.5)
    scroller = page.list_view.get_parent().get_parent()
    ok_s, outer = scroller.compute_bounds(window)
    ok_l, inner = page.list_view.compute_bounds(scroller)
    if not (ok_s and ok_l):
        check('column measurable at %d' % width, False)
        continue
    viewport = outer.size.width
    left = inner.origin.x
    right = viewport - inner.origin.x - inner.size.width
    print('note - window %d: viewport %d, column %d at %d' % (
        window.get_width(), viewport, inner.size.width, left))
    check('viewport fits the window at %d' % width,
          outer.origin.x + viewport <= window.get_width() + 0.5)
    check('column is min(viewport, %d) at %d' % (COLUMN_WIDTH, width),
          abs(inner.size.width - min(viewport, COLUMN_WIDTH)) <= 1)
    check('column centered at %d' % width, abs(left - right) <= 1)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all timeline rendering checks passed')
