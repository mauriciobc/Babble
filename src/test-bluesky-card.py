#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A bridged Bluesky quote renders natively in the timeline from the
Mastodon card alone, and its thread page builds from an AppView fixture.

No network: the Bluesky module is replaced by recorders, and texture loads
are replaced so they can be completed by hand.  Exits 77 without a
display."""

import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import copy
import gc
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    'bskyfixtures', os.path.join(srcdir, 'test-bskyfixtures.py'))
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

Adw.init()

from src import bluesky, blueskycard, blueskythread  # noqa: E402
from src import mediacache, pages, statusrow, timelinedata  # noqa: E402
from src.mediabox import CardPreview  # noqa: E402

failures = []


def check(name, condition, detail=''):
    print(('ok - ' if condition else 'FAIL - ') + name
          + ('' if condition else ' %s' % (detail,)))
    if not condition:
        failures.append(name)


class _Client:
    token = None
    instance_info = None


# Recorders: any call into the Bluesky module is a network request.
calls = []


def _record(name):
    def recorder(*args, **kwargs):
        calls.append(name)
        return {'cancellable': None}
    return recorder


for _name in ('get_thread_async', 'resolve_handle_async', '_get_json'):
    setattr(bluesky, _name, _record(_name))

# Texture loads are held until the test completes them.
loads = []
avatar_loads = []
mediacache._TextureCache.load = classmethod(
    lambda cls, url, picture, cover=None: loads.append((url, picture)))
mediacache.AvatarCache.load = classmethod(
    lambda cls, avatar, account: avatar_loads.append((avatar, account)))
blueskycard._TextureCache.load = mediacache._TextureCache.load
blueskycard.AvatarCache.load = mediacache.AvatarCache.load


def texture(size=64):
    return Gdk.MemoryTexture.new(
        size, size, Gdk.MemoryFormat.R8G8B8A8,
        GLib.Bytes.new(bytes(size * size * 4)), size * 4)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def text_of(view):
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def height(widget, width=600):
    return widget.measure(Gtk.Orientation.VERTICAL, width)[1]


def bind(status):
    row = statusrow.StatusRow()
    item = timelinedata.StatusItem(copy.deepcopy(status))
    row.bind(item, _Client())
    return row, item


settings = pages.settings()
settings.set_boolean('bluesky-native', True)

# AC1: the live-shaped status.
row, item = bind(fx.STATUS)
card = row.card_box.get_first_child()
check('card box is visible', row.card_box.get_visible())
check('live status renders a BlueskyQuoteCard',
      isinstance(card, blueskycard.BlueskyQuoteCard), type(card))
labels = [w.get_label() for w in walk(card) if isinstance(w, Gtk.Label)]
check('card shows the author name', 'o que ta acontecendo aqui' in labels,
      labels)
check('card shows the handle', '@acordeitarde.bsky.social' in labels,
      labels)
check('card shows the description', fx.CARD['description'] in labels)
check('avatar is loaded from card.image',
      [a[1] for a in avatar_loads if a[1].get('avatar') == fx.CARD['image']],
      avatar_loads)
check('body has no RE: line', 'RE:' not in text_of(row.content),
      text_of(row.content))
check('body keeps the text', 'Orkut' in text_of(row.content))

# AC5: indicator and menu item.
check('bridged post shows the Bluesky indicator',
      row.bluesky_label is not None and row.bluesky_label.get_visible())


def menu_labels(menu):
    out = []
    for index in range(menu.get_n_items()):
        label = menu.get_item_attribute_value(index, 'label', None)
        if label is not None:
            out.append(label.get_string())
        link = menu.get_item_link(index, 'section')
        if link is not None:
            out += menu_labels(link)
    return out


entries = menu_labels(row._overflow._build_menu(item))
check('bridged post has View on Bluesky in its menu',
      'View on Blue_sky' in entries, entries)
mnemonics = [e.split('_', 1)[1][0].lower() for e in entries if '_' in e]
check('View on Blue_sky access key is unique in the menu',
      mnemonics.count('s') == 1, mnemonics)

native = copy.deepcopy(fx.STATUS)
native['account']['acct'] = 'someone@mastodon.social'
native['uri'] = 'https://mastodon.social/users/someone/statuses/1'
native_row, native_item = bind(native)
check('non-bridged post has no indicator',
      getattr(native_row, 'bluesky_label', None) is None
      or not native_row.bluesky_label.get_visible())
check('non-bridged post has no menu item',
      'View on Blue_sky' not in menu_labels(
          native_row._overflow._build_menu(native_item)))

# A changed emission keeps the card widget.
item.emit('changed')
check('a changed emission keeps the same card',
      row.card_box.get_first_child() is card)

# AC2: nothing so far touched Bluesky.
check('binding rows made no Bluesky call', calls == [], calls)

# The row height is final at bind time.
before = height(row)
for avatar, _account in avatar_loads:
    avatar.set_custom_image(texture())
check('row height unchanged after the avatar loads',
      height(row) == before, (before, height(row)))
wide = copy.deepcopy(fx.STATUS)
wide['card'].update(width=1200, height=630)
wide_row, _wide_item = bind(wide)
wide_card = wide_row.card_box.get_first_child()
check('a wide card image makes a picture card',
      isinstance(wide_card, blueskycard.BlueskyQuoteCard)
      and wide_card.data['picture'] == fx.CARD['image'])
before = height(wide_row)
for _url, picture in loads:
    picture.set_paintable(texture(300))
check('row height unchanged after the picture loads',
      height(wide_row) == before, (before, height(wide_row)))

# Posts that show attachments keep their line and get no card.
with_media = copy.deepcopy(fx.STATUS)
with_media['media_attachments'] = [
    {'id': '1', 'type': 'image', 'url': 'https://example.org/a.png'}]
media_row, _ = bind(with_media)
check('with attachments the RE: line stays',
      'RE:' in text_of(media_row.content))

# AC3: click with the setting on pushes the thread; off opens the link.
pushed, opened = [], []
blueskythread.push_bluesky_thread = lambda widget, actor, rkey: pushed.append(
    (actor, rkey))
pages.open_link = lambda widget, uri, *a, **k: opened.append(uri)
card.emit('clicked')
check('click pushes the native thread',
      pushed == [('did:plc:6zulsu3flxsklsaoxdsx52h4', '3mzuxm3z6nk24')]
      and not opened, (pushed, opened))

settings.set_boolean('bluesky-native', False)
off_row, _ = bind(fx.STATUS)
off_card = off_row.card_box.get_first_child()
check('setting off gives a CardPreview',
      isinstance(off_card, CardPreview), type(off_card))
check('setting off keeps the RE: line', 'RE:' in text_of(off_row.content))
off_card.emit('clicked')
check('setting off opens the link',
      opened == [fx.QUOTED_URL] and len(pushed) == 1, (pushed, opened))
settings.set_boolean('bluesky-native', True)

# Toggling the setting rebuilds the card on the next bind.
check('card flips back with the setting',
      isinstance(bind(fx.STATUS)[0].card_box.get_first_child(),
                 blueskycard.BlueskyQuoteCard))

# AC4: a handle is resolved before any AppView post call.
import importlib  # noqa: E402
real = importlib.reload(bluesky)
sent = []


def fake_get_json(path, params, callback):
    sent.append((path, dict(params)))
    if path.endswith('resolveHandle'):
        GLib.idle_add(callback, {'did': 'did:plc:resolved'}, None)
    else:
        GLib.idle_add(callback, {'thread': fx.thread()}, None)
    return Gio.Cancellable.new()


real._get_json = fake_get_json
results = []
real.get_thread_async('alice.bsky.social', '3abc',
                      lambda thread, error: results.append((thread, error)))
context = GLib.MainContext.default()
for _ in range(50):
    context.iteration(False)
check('a handle is resolved first',
      [p.rsplit('.', 1)[-1] for p, _ in sent] == ['resolveHandle',
                                                   'getPostThread'], sent)
check('the thread call carries the DID, never the handle',
      sent[-1][1]['uri'] == 'at://did:plc:resolved/app.bsky.feed.post/3abc'
      and 'alice' not in sent[-1][1]['uri'], sent)
check('the thread call asks for depth and parent height',
      sent[-1][1]['depth'] == 6 and sent[-1][1]['parentHeight'] == 20)
check('the callback got the thread', results and results[0][1] is None)
sent.clear()
real.get_thread_async('did:plc:ubji6txf3gdzwztlt2zegzdi', '3abc',
                      lambda thread, error: None)
for _ in range(20):
    context.iteration(False)
check('a DID is not resolved', len(sent) == 1
      and sent[0][0].endswith('getPostThread'), sent)

# The thread page, with the Bluesky module faked.
bluesky.get_thread_async = lambda actor, rkey, callback: (
    callback(fx.thread(), None) or {'cancellable': None})
page = blueskythread.BlueskyThreadPage(fx.DID, fx.RKEY)
views = [w for w in walk(page.thread_box)
         if isinstance(w, blueskythread.BlueskyPostView)]
check('thread page shows parent, focus and two replies', len(views) == 4,
      len(views))
check('thread page is on the list', page.stack.get_visible_child_name()
      == 'list')
chain = page.thread_box.get_first_child()
check('parent chain and focus share one card',
      chain.has_css_class('thread-card')
      and isinstance(chain.get_first_child(), blueskythread.BlueskyPostView)
      and chain.get_last_child().has_css_class('thread-focused'))
check('the nested reply is nested',
      any(w.has_css_class('thread-nested') for w in walk(page.thread_box)))
check('a notFoundPost reply is a notice',
      any(isinstance(w, Gtk.Label) and w.get_label() == 'Post not found'
          for w in walk(page.thread_box)))
counts = [w.get_label() for w in walk(page.thread_box)
          if isinstance(w, Gtk.Label) and 'likes' in w.get_label()]
check('the focused post shows its counts',
      counts and '1.5K likes' in counts[0], counts)

many = fx.node(fx.post('Focus', rkey='3f'), replies=[
    fx.node(fx.post('r%d' % n, rkey='3r%d' % n)) for n in range(70)])
bluesky.get_thread_async = lambda actor, rkey, callback: (
    callback(many, None) or {'cancellable': None})
big = blueskythread.BlueskyThreadPage(fx.DID, fx.RKEY)
shown = [w for w in walk(big.thread_box)
         if isinstance(w, blueskythread.BlueskyPostView)]
check('replies are capped at 60 and a link follows',
      len(shown) == 61 and any(
          isinstance(w, Gtk.Button)
          and w.get_label() == 'More Replies on Bluesky'
          for w in walk(big.thread_box)), len(shown))

bluesky.get_thread_async = lambda actor, rkey, callback: (
    callback(None, 'HTTP 500') or {'cancellable': None})
failed = blueskythread.BlueskyThreadPage(fx.DID, fx.RKEY)
check('a 500 shows the error state',
      failed.stack.get_visible_child_name() == 'error')

# Memory: a page popped while its fetch is pending is freed.
pending = []
bluesky.get_thread_async = lambda actor, rkey, callback: (
    pending.append(callback) or {'cancellable': None})
page = blueskythread.BlueskyThreadPage(fx.DID, fx.RKEY)
ref = page.weak_ref()
del page


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        if ref() is None:
            return True
    return ref() is None


check('a thread page popped mid-fetch is freed', freed(ref))
pending[0](fx.thread(), None)  # a late reply must be a no-op
check('a late reply to a freed page is ignored', True)

row, item = bind(fx.STATUS)
row_ref, card_ref = row.weak_ref(), row.card_box.get_first_child().weak_ref()
del row, item
check('a row with a Bluesky card is freed', freed(row_ref))
check('its Bluesky card is freed', freed(card_ref))

check('only the thread page reached Bluesky',
      all(name == 'get_thread_async' for name in calls) or calls == [],
      calls)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
