#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the media viewer is reached from, beyond the timeline.

A thumbnail in a notification, a Catch Up tile and a Catch Up list row
opens the viewer at that attachment, by click and by Enter; covered
(sensitive) media does not. A PeerTube video card goes to the browser,
not the web column or a fediverse post. Media the server is still
processing falls back to the original through the remote-media gate, or
says so. Exits 77 (skip) without a display."""

import datetime
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
os.environ.setdefault('GTK_A11Y', 'none')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)
os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()
Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource')
                  )._register()

from src import (mediabox, mediaviewer, pages, remotemedia,  # noqa: E402
                 textemoji)
from src import notificationrow  # noqa: E402
from src.catchup import digest_layout as cd  # noqa: E402
from src.catchup import tiles as _tiles  # noqa: E402
from src.catchup import tiles_render  # noqa: E402
from src.catchup.pipeline import Digest  # noqa: E402
from src.catchup.rows import CatchUpRow  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def thumbnails(root):
    """The focusable button-role widgets that are media tiles."""
    return [w for w in walk(root)
            if w.has_css_class('media-tile')
            and w.get_accessible_role() == Gtk.AccessibleRole.BUTTON]


def click(widget):
    gesture = next(c for c in widget.observe_controllers()
                   if isinstance(c, Gtk.GestureClick))
    gesture.emit('released', 1, 1.0, 1.0)


def press(widget, keyval):
    keys = next(c for c in widget.observe_controllers()
                if isinstance(c, Gtk.EventControllerKey))
    return keys.emit('key-pressed', keyval, 0, 0)


NOW = datetime.datetime.now(datetime.timezone.utc)


def media(n, **extra):
    data = {'id': str(n), 'type': 'image',
            'url': 'https://e.example/m%d.png' % n,
            'preview_url': 'https://e.example/p%d.png' % n,
            'remote_url': None, 'description': 'Picture %d' % n,
            'meta': {'original': {'width': 640, 'height': 400}}}
    data.update(extra)
    return data


def post(n, attachments, **extra):
    data = {'id': str(n), 'created_at': NOW.isoformat(),
            'account': {'id': str(n), 'acct': 'u%d@e.example' % n,
                        'username': 'u%d' % n, 'display_name': 'U %d' % n,
                        'avatar': 'https://e.example/a.png', 'emojis': []},
            'content': '<p>Hello</p>', 'replies_count': 0,
            'reblogs_count': 0, 'favourites_count': 0,
            'url': 'https://e.example/@u%d/%d' % (n, n),
            'uri': 'https://e.example/%d' % n,
            'media_attachments': attachments, 'emojis': [], 'tags': [],
            'mentions': [], 'sensitive': False, 'spoiler_text': ''}
    data.update(extra)
    return data


class Client:
    token = 'x'
    instance_info = None


window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()

# The viewer is recorded rather than opened for the three surfaces.
calls = []
real_present = mediaviewer.present_media


def record(widget, attachments, index=0, status_url=None):
    calls.append((list(attachments), index, status_url))


mediaviewer.present_media = record

# ---------------------------------------------------------------------
# Notification post

attachments = [media(1), media(2), media(3)]
item = StatusItem(post(1, attachments))
note = notificationrow.NotificationPost()
parent.set_child(note)
note.bind(item)
tiles = thumbnails(note)
check('a notification shows one button per thumbnail',
      len(tiles) == len(attachments))
check('a notification thumbnail is focusable and labelled',
      all(t.get_focusable() for t in tiles))
calls.clear()
click(tiles[1])
check('clicking the second notification thumbnail opens the viewer at 1',
      len(calls) == 1 and calls[0][1] == 1
      and [a['id'] for a in calls[0][0]] == ['1', '2', '3']
      and calls[0][2] == item.status['url'])
calls.clear()
check('Enter on a notification thumbnail is handled',
      press(tiles[2], Gdk.KEY_Return) is True)
check('Enter on a notification thumbnail opens the viewer at 2',
      len(calls) == 1 and calls[0][1] == 2)
calls.clear()
press(tiles[0], Gdk.KEY_space)
check('Space opens it too', len(calls) == 1 and calls[0][1] == 0)
calls.clear()
check('another key is not handled', press(tiles[0], Gdk.KEY_a) is False
      and not calls)

covered = StatusItem(post(2, [media(4)], sensitive=True))
note.bind(covered)
check('a sensitive notification offers no thumbnail to open',
      not thumbnails(note))
note.clear()

# A processing attachment: no picture, still a button, with the message.
pending = StatusItem(post(3, [media(5, url=None, preview_url=None)]))
note.bind(pending)
labels = [w.get_label() for w in walk(note) if isinstance(w, Gtk.Label)]
check('a notification thumbnail with no picture says it is processing',
      'Processing…' in labels)
calls.clear()
click(thumbnails(note)[0])
check('and it still opens the viewer', len(calls) == 1)

# ---------------------------------------------------------------------
# Catch Up tile

context = _tiles._Context(Client(), Digest(
    posts=[], scores={}, items={}, news=[], tags=[],
    trending_keys=frozenset(), boosters={}, end_at=NOW))
settings = pages.settings()
settings.set_boolean('show-sensitive-media', False)


def make_tile(status, colspan=2, rowspan=2, tier='compact'):
    placed = cd.Placed('post', status, 0, 0, colspan, rowspan, tier, 0)
    tile = tiles_render.PostTile(context, placed)
    tile.ensure_built()
    return tile


tile = make_tile(post(10, [media(11), media(12, type='video')]))
parent.set_child(tile)
buttons = thumbnails(tile)
check('a Catch Up tile with media has a thumbnail button',
      len(buttons) >= 1)
calls.clear()
click(buttons[0])
check('clicking a Catch Up thumbnail opens the viewer at its attachment',
      len(calls) == 1 and calls[0][1] == 0
      and [a['id'] for a in calls[0][0]] == ['11', '12'])
calls.clear()
check('Enter on a Catch Up thumbnail is handled',
      press(buttons[0], Gdk.KEY_Return) is True and len(calls) == 1)

one = make_tile(post(13, [media(14), media(15)]), colspan=1, rowspan=1)
second = [b for b in thumbnails(one)]
if len(second) >= 2:
    calls.clear()
    click(second[1])
    check('the second Catch Up thumbnail opens the viewer at 1',
          len(calls) == 1 and calls[0][1] == 1)

blurred = make_tile(post(16, [media(17)], sensitive=True))
parent.set_child(blurred)
check('a covered Catch Up tile has no thumbnail to open',
      not thumbnails(blurred))
calls.clear()
check('and opening is refused while covered',
      blurred.open_media(0) is False and not calls)

processing_tile = make_tile(
    post(18, [media(19, url=None, preview_url=None)]))
labels = [w.get_label() for w in walk(processing_tile)
          if isinstance(w, Gtk.Label)]
check('a Catch Up tile with no picture builds and says processing',
      'Processing…' in labels)

# ---------------------------------------------------------------------
# Catch Up list row

row = CatchUpRow()
parent.set_child(row)
row_item = StatusItem(post(20, [media(21), media(22)]))
row.bind(row_item)
holders = [w for w in thumbnails(row) if w.get_visible()]
check('a Catch Up row shows a button per thumbnail', len(holders) == 2)
calls.clear()
click(holders[1])
check('clicking a Catch Up row thumbnail opens the viewer at 1',
      len(calls) == 1 and calls[0][1] == 1)
calls.clear()
check('Space on a Catch Up row thumbnail opens it',
      press(holders[0], Gdk.KEY_space) is True and calls[0][1] == 0)
check('an open Catch Up row thumbnail is focusable with a pointer',
      holders[0].get_focusable() and holders[0].get_cursor() is not None)
row.bind(StatusItem(post(23, [media(24)], sensitive=True)))
calls.clear()
check('a covered Catch Up row thumbnail does not open',
      row.open_media(0) is False and not calls)
covered_holder = row.pictures[0]
check('a covered Catch Up row thumbnail is out of the tab order',
      not covered_holder.get_focusable())
check('and has no pointer cursor', covered_holder.get_cursor() is None)
row.bind(row_item)
check('binding an open post makes the holder a button again',
      row.pictures[0].get_focusable()
      and row.pictures[0].get_cursor() is not None)

# The click must be claimed, or the list's single-click activation opens
# the thread of the row as well. A recording stand-in for the gesture.


class FakeGesture:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


mediaviewer.present_media = record
note.bind(item)
fake = FakeGesture()
note._on_media_released(fake, 1, 0.0, 0.0, 1)
check('a click that opens the viewer claims the sequence',
      fake.states == [Gtk.EventSequenceState.CLAIMED])
note.bind(StatusItem(post(30, [media(31)], sensitive=True)))
fake = FakeGesture()
note._on_media_released(fake, 1, 0.0, 0.0, 0)
check('a click on covered media is not claimed, so the row still opens',
      fake.states == [])

# ---------------------------------------------------------------------
# PeerTube cards

PEER = [
    ('watch page', {'type': 'video',
                    'url': 'https://tube.example/videos/watch/0b1c2d3e'}),
    ('short page', {'type': 'video', 'url': 'https://tube.example/w/abc123'}),
    ('playlist page', {'type': 'video',
                       'url': 'https://tube.example/w/p/abc123'}),
    ('embed in html', {'type': 'video', 'url': 'https://x.example/page',
                       'html': '<iframe width="560" src='
                               '"https://tube.example/videos/embed/9f8e" '
                               'frameborder="0"></iframe>'}),
    ('short page in embed', {'type': 'video', 'url': 'https://x.example/',
                             'html': "<iframe src='https://t.example/w/zz9'>"
                                     '</iframe>'}),
]
NOT_PEER = [
    ('youtube', {'type': 'video',
                 'url': 'https://www.youtube.com/watch?v=abc',
                 'html': '<iframe src="https://www.youtube.com/embed/abc">'
                         '</iframe>'}),
    ('/w/ on a link card', {'type': 'link',
                            'url': 'https://tube.example/w/abc123'}),
    ('wikipedia', {'type': 'link',
                   'url': 'https://en.wikipedia.org/w/index.php?title=A'}),
    ('wikipedia as video', {'type': 'video',
                            'url': 'https://en.wikipedia.org/w/a/b'}),
    ('mastodon post', {'type': 'video',
                       'url': 'https://m.example/@alice/1234'}),
    ('empty', {}),
    ('none', None),
]
for label, card in PEER:
    check('PeerTube card detected: %s' % label,
          mediabox.is_peertube_card(card))
for label, card in NOT_PEER:
    check('not a PeerTube card: %s' % label,
          not mediabox.is_peertube_card(card))

opened_in_browser = []
routed = []
real_external, real_open_link = pages.launch_external, pages.open_link
pages.launch_external = lambda widget, uri: opened_in_browser.append(uri)
pages.open_link = lambda widget, uri, *a, **k: routed.append(uri)

peer = dict(PEER[1][1], title='A video', description='', provider_name='T')
preview = mediabox.CardPreview(peer)
parent.set_child(preview)
preview.emit('clicked')
check('a PeerTube card opens its link in the browser',
      opened_in_browser == [peer['url']])
check('and is not routed in the app', routed == [])
opened_in_browser.clear()
other = dict(NOT_PEER[1][1], title='An article', description='')
mediabox.CardPreview(other).emit('clicked')
check('an ordinary card still goes through open_link',
      routed == [other['url']] and opened_in_browser == [])
pages.launch_external, pages.open_link = real_external, real_open_link

# ---------------------------------------------------------------------
# Media still processing

mediaviewer.present_media = real_present
asked = []
real_request = remotemedia.request


def fake_request(widget, url, is_remote, on_allowed, on_denied):
    asked.append((url, is_remote))


remotemedia.request = fake_request
original = 'https://far.example/media/x.png'
viewer = real_present(parent, [
    {'type': 'image', 'url': None, 'remote_url': original,
     'description': 'd'}], 0)
check('no url but a remote_url goes through the consent gate for it',
      asked == [(original, True)])
viewer.close()

nothing = {'type': 'video', 'url': None, 'remote_url': None,
           'preview_url': None, 'description': 'd'}
viewer = real_present(parent, [nothing], 0,
                      status_url='https://e.example/@a/1')
check('neither address: the viewer shows its error page',
      viewer.stack.get_visible_child_name() == 'error')
check('and says the server is still processing it',
      'still being processed by your server'
      in viewer.error_page.get_description())
check('with Open in Browser when the post has an address',
      viewer.error_browser_button.get_visible())
check('and no Retry, which could only show the same stale attachment',
      not viewer.retry_button.get_visible())
check('the message tells the reader to reopen the post',
      'Open the post again' in viewer.error_page.get_description())
launched = []
real_launch = textemoji.launch_uri
textemoji.launch_uri = lambda widget, uri: launched.append(uri)
viewer.open_in_browser()
check('Open in Browser opens the post', launched == [
    'https://e.example/@a/1'])
textemoji.launch_uri = real_launch
viewer.close()

viewer = real_present(parent, [nothing], 0)
check('without a post address there is no Open in Browser',
      not viewer.error_browser_button.get_visible()
      and 'still being processed' in viewer.error_page.get_description())
viewer.close()

unknown = {'type': 'unknown', 'url': None, 'remote_url': None}
viewer = real_present(parent, [unknown], 0)
check('an unknown attachment with no address is also "processing"',
      'still being processed' in viewer.error_page.get_description())
viewer.close()
remotemedia.request = real_request

# A processing tile in the timeline's media grid.
box = mediabox.MediaBox()
parent.set_child(box)
box.set_media([
    {'type': 'image', 'url': None, 'preview_url': None, 'remote_url': None},
    {'type': 'video', 'url': None, 'preview_url': None,
     'remote_url': 'https://far.example/v.mp4', 'description': 'clip'}],
    sensitive=False)
labels = [w.get_label() for w in walk(box) if isinstance(w, Gtk.Label)]
check('a MediaBox tile with no picture builds and says processing',
      labels.count('Processing…') == 2)
opened = []
box._open = opened.append
tile_buttons = [w for w in walk(box) if w.has_css_class('media-tile')]
click(tile_buttons[0])
check('and a click on it still opens the viewer', opened == [0])

GLib.idle_add(lambda: False)
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
print('ok - all viewer entry point checks passed')
