#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up highlights grid: columns and cells, re-packing, tiles, Show
more, content warnings, text handling, accessible labels and freeing.
Exits 77 (skip) without a display."""

import datetime
import gc
import locale
import os
import sys

# No accessibility bus in a test run; GTK would warn about it.
os.environ.setdefault('GTK_A11Y', 'none')

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import catchupdigest as cd  # noqa: E402
from src import (catchupgrid, explore, hashtags, mediacache, mediabox,  # noqa: E402
                  pages, statusactions, statusmenu, timelinedata)
from src.catchup import Digest  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def freed(ref):
    gc.collect()
    return ref() is None


def settle(ready=lambda: True, seconds=5.0, quiet=0.1):
    context = GLib.MainContext.default()
    now = GLib.get_monotonic_time
    deadline = now() + int(seconds * 1000000)
    last = now()
    while now() < deadline:
        if context.iteration(False):
            last = now()
        elif ready() and now() - last >= quiet * 1000000:
            return
        else:
            GLib.usleep(2000)


Adw.init()

# No network: pictures and avatars are counted, not fetched.
texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8A8,
                                GLib.Bytes.new(b'\x80' * 64), 16)
loads = []


def fake_media(url, picture, cover=None):
    loads.append((url, cover))
    picture.set_paintable(texture)


def fake_avatar(avatar, account):
    loads.append(('avatar', account.get('id')))


mediacache._TextureCache.load = staticmethod(fake_media)
mediacache.AvatarCache.load = staticmethod(fake_avatar)

NOW = datetime.datetime.now(datetime.timezone.utc)


def iso(hours):
    return (NOW - datetime.timedelta(hours=hours)).isoformat()


def account(n):
    return {'id': str(n), 'acct': 'user%d@e.example' % n,
            'username': 'user%d' % n, 'display_name': 'User %d' % n,
            'avatar': 'https://e.example/a%d.png' % n, 'emojis': []}


def media(idx, kind='image', aspect=1.6):
    return {'id': str(idx), 'type': kind,
            'url': 'https://e.example/m%d.png' % idx,
            'preview_url': 'https://e.example/p%d.png' % idx,
            'description': '', 'meta': {'original': {
                'width': int(400 * aspect), 'height': 400}}}


def post(n, html, attachments=None, **extra):
    data = {'id': str(n), 'created_at': iso(3), 'account': account(n),
            'content': html, 'replies_count': 2, 'reblogs_count': 1234,
            'favourites_count': 7, 'url': 'https://e.example/%d' % n,
            'uri': 'https://e.example/%d' % n,
            'media_attachments': attachments or [], 'emojis': [],
            'tags': [], 'mentions': [], 'sensitive': False,
            'spoiler_text': ''}
    data.update(extra)
    return data


def fixture(count=60):
    posts = [
        post(1, '<p>Headline here.</p><p>Body text of the first post.</p>',
             [media(1)]),
        post(2, '<p>Short.</p>'),
        post(3, '<p>One landscape</p>', [media(3)]),
        post(4, '<p>Short text</p>', [media(4, aspect=0.7)]),
        post(5, '<p>%s</p>' % ('long ' * 60),
             [media(5), media(6)]),
        post(6, '<p>Linked</p>', card={
            'url': 'https://e.example/c', 'title': 'Card', 'image': None,
            'description': '', 'type': 'link'}),
    ]
    boost = {'id': '900', 'created_at': iso(2), 'account': account(77),
             'reblog': post(7, '<p>Boosted</p>'), 'content': '',
             'media_attachments': [], 'emojis': []}
    posts.insert(3, boost)
    for n in range(10, 10 + count):
        posts.append(post(n, '<p>Plain post %d with some words.</p>' % n))
    scores = {p['id']: 1000 - i for i, p in enumerate(posts)}
    news = [{'key': 'https://n.example/%d' % i,
             'url': 'https://n.example/%d' % i, 'trending': i == 0,
             'shared': 1 if i == 0 else 0,
             'sharers': [account(40 + k) for k in range(7)] if i == 0
             else [], 'likes': 0, 'boosts': 0, 'post_id': None,
             'card': {'url': 'https://n.example/%d' % i,
                      'title': 'News title %d' % i,
                      'description': 'Description %d' % i,
                      'image': 'https://e.example/n%d.png' % i if i != 1
                      else None, 'provider_name': 'Provider',
                      'author_name': 'Author', 'published_at': iso(4),
                      'history': [{'accounts': '12', 'uses': '20'}]}}
            for i in range(6)]
    tags = [{'key': 'gnome', 'name': 'GNOME', 'trending': True,
             'following': False,
             'tag': {'name': 'GNOME', 'history': [
                 {'uses': '10', 'accounts': '5'},
                 {'uses': '20', 'accounts': '8'},
                 {'uses': '5', 'accounts': '3'}]},
             'timeline_posts': 3, 'timeline_accounts': 2,
             'accounts': [account(60), account(61)], 'buckets': None},
            {'key': 'linux', 'name': 'Linux', 'trending': False,
             'following': True, 'tag': None, 'timeline_posts': 1,
             'timeline_accounts': 2, 'accounts': [account(62)],
             'buckets': [0, 1, 3, 2, 0, 4, 5, 1, 0, 2, 3, 6]},
            {'key': 'a', 'name': 'A', 'trending': True, 'following': False,
             'tag': {'name': 'A', 'history': []}, 'timeline_posts': 0,
             'timeline_accounts': 0, 'accounts': [], 'buckets': None}]
    return Digest(
        posts=posts, scores=scores,
        items={p['id']: timelinedata.StatusItem(p) for p in posts},
        news=news, tags=tags,
        trending_keys=frozenset(['1', 'https://e.example/2']),
        boosters={'900': [account(78), account(79)]}, end_at=NOW)


class Client:
    token = 'x'
    instance_info = None


class Host(Gtk.Widget):
    """Gives its child exactly the width asked for."""

    __gtype_name__ = 'TestHost'

    def __init__(self, child):
        super().__init__()
        self.width = 400
        self.child = child
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        return (1, 1, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        h = self.child.measure(Gtk.Orientation.VERTICAL, self.width)[0]
        self.child.measure(Gtk.Orientation.HORIZONTAL, -1)
        self.child.allocate(self.width, h, -1, None)

    def do_dispose(self):
        self.child.unparent()
        Gtk.Widget.do_dispose(self)


def layout_host(host):
    """Run a full measure/allocate pass at ``host.width`` (headless tests)."""
    width = host.width
    child = host.child
    height = child.measure(Gtk.Orientation.VERTICAL, width)[1]
    child.allocate(width, max(height, 1), -1, None)


def at_width(host, width, grid=None):
    """Put *host* at *width* and wait until the grid re-packed for it.

    ``BentoGrid`` updates ``columns`` in ``do_size_allocate``, which can
    run a frame after the host's width lands — assertions that only waited
    on ``get_width()`` read the previous column count under load.
    """
    host.width = width
    host.queue_resize()
    target_cols = catchupgrid.columns_for(width)
    context = GLib.MainContext.default()

    def ready():
        layout_host(host)
        while context.iteration(False):
            pass
        if host.child.get_width() != width:
            return False
        if grid is None:
            return True
        return grid.get_width() == width and grid.columns == target_cols

    settle(ready, 10.0, 0.05)


def wait_show_more(view, grid, tiles_before, host=None):
    """``show_more()`` invalidates the packing; wait for pages and tiles."""
    target_pages = grid.pages_shown + cd.MORE_PAGES
    view.show_more()
    if host is not None:
        layout_host(host)

    def ready():
        if host is not None:
            layout_host(host)
        return grid.pages_shown == target_pages \
            and len(grid.tiles) > tiles_before

    settle(ready, 10.0, 0.05)


# --------------------------------------------------------------------------
# Geometry

expected = {180: 1, 371: 1, 372: 2, 563: 2, 564: 3, 400: 2, 756: 4,
            1400: 7}
check('columns follow max(1, (width + 12) // 192)',
      all(catchupgrid.columns_for(w) == c for w, c in expected.items())
      and all(catchupgrid.columns_for(w) == max(1, (w + 12) // 192)
              for w in range(180, 1401)))

digest = fixture()
ok_bounds = ok_overlap = True
for width in range(180, 1401, 37):
    columns = catchupgrid.columns_for(width)
    placed = cd.layout(digest.posts, digest.scores, digest.news,
                       digest.tags, columns, cd.INITIAL_PAGES)
    rects = [catchupgrid.cell_rect(width, columns, p.col, p.row, p.colspan,
                                   p.rowspan) for p in placed]
    for x, _y, w, h in rects:
        ok_bounds = ok_bounds and x >= 0 and w > 0 and x + w <= width \
            and h >= catchupgrid.ROW_HEIGHT
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a[0] < b[0] + b[2] and b[0] < a[0] + a[2] \
                    and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]:
                ok_overlap = False
check('cells stay inside the width from 180 to 1400 px', ok_bounds)
check('cells never overlap', ok_overlap)
rect = catchupgrid.cell_rect(1000, 5, 4, 0, 1, 1)
check('the last column ends at the width', rect[0] + rect[2] == 1000)
check('a row is 160 px and a gap 12',
      catchupgrid.cell_rect(1000, 5, 0, 2, 1, 2)[1::2]
      == (2 * 172, 160 * 2 + 12))
check('grid height is rows * 160 + 12 * (rows - 1)',
      catchupgrid.grid_height(cd.layout(
          digest.posts, digest.scores, digest.news, digest.tags, 4, 3))
      == (lambda r: r * 160 + 12 * (r - 1))(cd.grid_rows(cd.layout(
          digest.posts, digest.scores, digest.news, digest.tags, 4, 3))))

# --------------------------------------------------------------------------
# Text

# 5.3: "Body lines are limited by the tile's height". A tile squeezed
# below one line used to draw that line anyway, on top of the action bar.
_fit = catchupgrid._FitText('word ' * 80, max_lines=6, min_lines=1,
                            css=('body',))
_fit.allocate(240, 40, -1, None)
_fit.do_snapshot(Gtk.Snapshot())
check('text fits a short allocation by dropping lines',
      _fit._shaped is not None
      and _fit._shaped[2].get_pixel_size()[1] <= 43)
_fit._shaped = None
_fit.allocate(240, 6, -1, None)
_fit.do_snapshot(Gtk.Snapshot())
check('and draws nothing at all when even one line will not fit',
      _fit._shaped is None)

# A post whose <br>s arrive as newlines: Pango counts a hard break as a
# line whatever the height limit says, so such a post used to measure far
# taller than its card and was dropped instead of shown short.
_breaks = catchupgrid._FitText('one\ntwo\nthree\nfour\nfive', max_lines=4,
                                min_lines=1, css=('body',))
_breaks.allocate(200, 120, -1, None)
_breaks.do_snapshot(Gtk.Snapshot())
check('hard line breaks do not defeat the line budget',
      _breaks._shaped is not None
      and _breaks._shaped[2].get_pixel_size()[1] <= 123
      and '\n' not in _breaks._text)

segments = catchupgrid.statustext.parse_content(
    '<p>Fim de tarde.</p><p>Mais texto aqui.</p>')
head, rest = catchupgrid.split_headline(segments)
check('a short first paragraph with more text is the headline',
      catchupgrid.segments_text(head) == 'Fim de tarde.'
      and catchupgrid.segments_text(rest) == 'Mais texto aqui.')
head, rest = catchupgrid.split_headline(
    catchupgrid.statustext.parse_content('<p>Only one paragraph.</p>'))
check('a single paragraph is all body', not head and rest)
head, rest = catchupgrid.split_headline(
    catchupgrid.statustext.parse_content(
        '<p>%s</p><p>more</p>' % ('x' * 81)))
check('a first paragraph over 80 characters is no headline', not head)
head, rest = catchupgrid.split_headline(
    catchupgrid.statustext.parse_content(
        '<p>%s</p><p>more</p>' % ('x' * 80)))
check('80 characters still make a headline', bool(head))
head, rest = catchupgrid.split_headline(
    catchupgrid.statustext.parse_content('<p>a<br>b</p><p>c</p>'))
check('a line break stays inside the headline',
      catchupgrid.segments_text(head, lines=False) == 'a b')
check('known custom emoji shortcodes are dropped, others kept',
      catchupgrid.segments_text(
          [{'text': 'hi :blob: at 12:30:45', 'href': None}], {'blob'})
      == 'hi at 12:30:45')

# --------------------------------------------------------------------------
# The view in a host of a known width

client = Client()
view = catchupgrid.HighlightsView(client)
host = Host(view)
window = Gtk.Window(default_width=600, default_height=400, child=host)
window.present()
view.set_digest(digest)
check('a digest with enough tiles shows the view',
      view.get_visible() and view.eligible >= catchupgrid.MIN_TILES)
at_width(host, 1000, view.grid)
check('1000 px gives 5 columns', view.grid.columns == 5)
settle(lambda: view.grid.get_height() > 0)

grid = view.grid


def tile_rect(tile):
    ok, bounds = tile.compute_bounds(grid)
    return (round(bounds.get_x()), round(bounds.get_y()),
            round(bounds.get_width()), round(bounds.get_height()))


def check_layout(label):
    width = grid.get_width()
    columns = grid.columns
    placed = grid.placed
    good = True
    for p in placed:
        tile = grid.tiles[catchupgrid.tile_key(p)]
        want = catchupgrid.cell_rect(width, columns, p.col, p.row,
                                     p.colspan, p.rowspan)
        good = good and tile_rect(tile) == want
    check('%s: tiles sit in their cells' % label, good)
    rects = [tile_rect(t) for t in grid.tiles.values()]
    inside = all(x >= 0 and x + w <= width and y + h <= grid.get_height()
                 for x, y, w, h in rects)
    check('%s: tiles are inside the grid' % label, inside)
    clear = True
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a[0] < b[0] + b[2] and b[0] < a[0] + a[2] \
                    and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]:
                clear = False
    check('%s: tiles do not overlap' % label, clear)


check_layout('5 columns')
expected_tiles = cd.layout(digest.posts, digest.scores, digest.news,
                           digest.tags, 5, cd.INITIAL_PAGES)
check('tile kinds and spans match catchupdigest.layout',
      [(p.kind, p.colspan, p.rowspan) for p in grid.placed]
      == [(p.kind, p.colspan, p.rowspan) for p in expected_tiles]
      and {(p.kind, catchupgrid.tile_key(p)[1]) for p in expected_tiles}
      == set(grid.tiles))
kinds = {type(t).__name__ for t in grid.tiles.values()}
check('there are post, news and hashtag tiles',
      kinds == {'PostTile', 'NewsTile', 'HashtagTile'})

# 2.6: the focus chain follows score rank, not visual position. pack()
# fills holes with later compact posts, so the two orders differ here.
_children = []
_child = grid.get_first_child()
while _child is not None:
    _children.append((_child.placed.row, _child.placed.col))
    _child = _child.get_next_sibling()
_score_rank = [(p.row, p.col) for p in grid.placed]
check('the tiles are in score order for the focus chain',
      _children == _score_rank)
check('and that is not the same as visual order',
      _score_rank != sorted(_score_rank))

# Re-packing only when the column count changes, tiles survive.
packs = grid.pack_count
tiles_before = dict(grid.tiles)
at_width(host, 1050, grid)
check('a width change inside one column count does not re-pack',
      grid.columns == 5 and grid.pack_count == packs)
at_width(host, 1300, grid)
check('a new column count re-packs once',
      grid.columns == 6 and grid.pack_count == packs + 1)
shared = [k for k in grid.tiles if k in tiles_before]
check('tiles shared by both packings are the same widgets',
      shared and all(grid.tiles[k] is tiles_before[k] for k in shared))
check_layout('6 columns')
at_width(host, 1000, grid)
check('going back reuses the cached packing',
      grid.columns == 5 and grid.pack_count == packs + 1)
at_width(host, 400, grid)
check('400 px gives 2 columns', grid.columns == 2)
check_layout('2 columns')
at_width(host, 180, grid)
check('180 px gives one column', grid.columns == 1)
check_layout('1 column')
at_width(host, 400, grid)

# Heights: height for width.
natural = grid.measure(Gtk.Orientation.VERTICAL, 400)[0]
check('height for width matches the packing',
      natural == catchupgrid.grid_height(cd.layout(
          digest.posts, digest.scores, digest.news, digest.tags, 2,
          grid.pages_shown))
      and grid.get_request_mode() == Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH)

# Content is built lazily, one tile per idle dispatch, then complete.
at_width(host, 1000, grid)
settle(lambda: all(t._child is not None for t in grid.tiles.values()), 20)
check('every tile builds its content when shown',
      all(t._child is not None for t in grid.tiles.values()))
check_layout('built, 5 columns')

# A span change after a re-pack rebuilds only that tile.
at_width(host, 180, grid)
settle(lambda: all(t._child is not None for t in grid.tiles.values()), 20)
check('tiles rebuild after a span change',
      all(t._child is not None for t in grid.tiles.values()))
check_layout('built, 1 column')
at_width(host, 1000, grid)

# Accessible labels.
post_tiles = [t for t in grid.tiles.values()
              if isinstance(t, catchupgrid.PostTile)]
first = post_tiles[0]
label = first.describe()
check('a post tile is described by author, text and counts',
      'User' in label and 'repl' in label and 'boost' in label
      and 'favorite' in label)
news_tile = next(t for t in grid.tiles.values()
                 if isinstance(t, catchupgrid.NewsTile))
tag_tile = next(t for t in grid.tiles.values()
                if isinstance(t, catchupgrid.HashtagTile))
check('news and hashtag tiles are described',
      'News title' in news_tile.describe()
      and '#' in tag_tile.describe())
check('tiles are focusable cards',
      all(t.get_focusable() and t.has_css_class('card')
          for t in grid.tiles.values()))

# Activation.
opened = []
statusactions._open_thread = lambda w, c, item: opened.append(('thread', item))
explore.open_link_timeline = lambda w, c, card: opened.append(('news', card))
hashtags.push_hashtag = lambda w, c, name: opened.append(('tag', name))
first.open()
news_tile.open()
tag_tile.open()
check('tiles open the thread, the link timeline and the hashtag',
      [o[0] for o in opened] == ['thread', 'news', 'tag']
      and opened[0][1] is first.item and opened[2][1] == tag_tile.entry['name'])

def labels_of(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child)
        found.extend(labels_of(child))
        child = child.get_next_sibling()
    return found


# Boost line and Trending badge.
boost_context = catchupgrid._Context(client, digest)
boost_tile = catchupgrid.PostTile(boost_context, cd.Placed(
    'post', digest.posts[3], 0, 0, 1, 1, 'compact', 0))
boost_tile.ensure_built()
check('a boost shows who boosted it and how many others',
      boost_tile.item.is_boost
      and len(boost_context.boosters['900']) == 2
      and any('and 2 others boosted' in getattr(w, 'get_label', lambda: '')()
              for w in labels_of(boost_tile)))
check('a trending post is flagged', any(t.trending for t in post_tiles)
      and not all(t.trending for t in post_tiles))

# Show more.
shown = len(grid.tiles)
check('Show more is offered while posts remain',
      grid.has_more and view.more_button.get_visible())
wait_show_more(view, grid, shown, host)
check('Show more adds three pages',
      grid.pages_shown == cd.INITIAL_PAGES + cd.MORE_PAGES
      and len(grid.tiles) > shown)
for _ in range(20):
    if not grid.has_more:
        break
    before = len(grid.tiles)
    wait_show_more(view, grid, before, host)


def no_more_posts():
    layout_host(host)
    return not grid.has_more


settle(no_more_posts, 10.0, 0.05)
check('Show more hides when everything is shown',
      not grid.has_more and not view.more_button.get_visible()
      and sum(1 for p in grid.placed if p.kind == 'post')
      == len(digest.posts))

# set_digest replaces everything.
old_keys = set(grid.tiles)
view.set_digest(fixture(30))
settle()
check('set_digest starts again at the first pages',
      grid.pages_shown == cd.INITIAL_PAGES)
view.set_digest(None)
check('set_digest(None) clears and hides',
      not grid.tiles and not view.get_visible())
small = fixture(0)
small.posts = small.posts[:3]
small.news, small.tags = [], []
view.set_digest(small)
check('fewer than 6 tiles hide the view',
      not view.get_visible() and not grid.tiles)

# --------------------------------------------------------------------------
# Content warnings, sensitive media, markup

settings = pages.settings()
settings.set_boolean('show-spoilers', False)
settings.set_boolean('show-sensitive-media', False)
cw = post(300, '<p>Secret body</p>', [media(300)],
          spoiler_text='Spoilers', sensitive=True)
sensitive = post(301, '<p>Open text</p>', [media(301)], sensitive=True)
plain = post(302, '<p>Fine</p>', [media(302)])
context = catchupgrid._Context(client, Digest(
    posts=[], scores={}, items={}, news=[], tags=[],
    trending_keys=frozenset(), boosters={}, end_at=NOW))


def make(status, colspan=2, rowspan=1, tier='compact'):
    placed = cd.Placed('post', status, 0, 0, colspan, rowspan, tier, 0)
    tile = catchupgrid.PostTile(context, placed)
    tile.ensure_built()
    return tile


def pictures(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Picture):
            found.append(child)
        found.extend(pictures(child))
        child = child.get_next_sibling()
    return found


def find_of(widget, kind):
    """The first widget of *kind* in the tree under *widget*, or None."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            return child
        found = find_of(child, kind)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def texts(widget):
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, catchupgrid._FitText):
            found.append(child._text)
        found.extend(texts(child))
        child = child.get_next_sibling()
    return found


tile = make(cw)
check('a content warning shows its text instead of the post',
      tile.cw_hidden and tile.body == 'Spoilers'
      and any('Spoilers' in t for t in texts(tile))
      and not any('Secret' in t for t in texts(tile)))
check('its media is blurred',
      tile.blur and pictures(tile)
      and all(isinstance(p, mediabox.BlurredPicture) for p in pictures(tile)))
tile = make(sensitive)
check('sensitive media is blurred by default',
      tile.blur and not tile.cw_hidden
      and any('Open text' in t for t in texts(tile)))
tile = make(plain)
check('ordinary media is not blurred',
      not tile.blur and pictures(tile)
      and not any(isinstance(p, mediabox.BlurredPicture)
                  for p in pictures(tile)))
settings.set_boolean('show-spoilers', True)
settings.set_boolean('show-sensitive-media', True)
tile = make(cw)
check('show-spoilers and show-sensitive-media lift both',
      not tile.cw_hidden and not tile.blur
      and any('Secret' in t for t in texts(tile)))
settings.set_boolean('show-spoilers', False)
settings.set_boolean('show-sensitive-media', False)

# A tile already on screen follows the preference, the way
# show-preview-cards does, instead of keeping what it was built with.
tile = make(cw, 1, 1)
hidden_first = tile.cw_hidden
settings.set_boolean('show-spoilers', True)
tile.set_placed(tile.placed)
check('a tile already on screen follows the spoiler preference',
      hidden_first and not tile.cw_hidden)
settings.set_boolean('show-spoilers', False)
tile.set_placed(tile.placed)

nasty = post(303, '<p>&lt;b&gt;bold&lt;/b&gt; &amp;amp; &lt;span foreground='
             '"red"&gt; x</p><p>second &amp; &lt;i&gt;</p>')
tile = make(nasty, 1, 2)
text = ' '.join(texts(tile))
check('markup in post text is escaped, not interpreted',
      '&amp;' in text and '&lt;b&gt;' in text)
check('escaped text lays out',
      all(w.measure(Gtk.Orientation.VERTICAL, 150)[1] > 0
          for w in [tile]))

# Forms.
check('forms: overlay, inset, inline and plain',
      make(post(310, '<p>x</p>', [media(310)]), 2, 1).form == 'overlay'
      and make(post(311, '<p>x</p>', [media(311)]), 2, 2, 'hero').form
      == 'overlay'
      and make(post(312, '<p>x</p>', [media(312)]), 1, 2, 'medium').form
      == 'inset'
      and make(post(313, '<p>%s</p>' % ('y' * 200), [media(313)]), 1, 2,
               'medium').form == 'inline'
      and make(post(314, '<p>x</p>', [media(314), media(315)]), 1, 2,
               'medium').form == 'inline'
      and make(post(316, '<p>x</p>'), 1, 1).form == 'plain')
video = make(post(320, '<p>x</p>', [media(320, 'video')]), 1, 1)
check('a video shows its preview with a play icon',
      pictures(video) and any(
          u.endswith('p320.png') for u, _c in loads))
settings.set_boolean('show-preview-cards', False)
carded = make(post(321, '<p>x</p>', card={
    'url': 'https://e.example/c', 'title': 'T', 'image': None,
    'description': '', 'type': 'link'}), 1, 2, 'medium')
check('show-preview-cards off hides the link card',
      not any(isinstance(w, mediabox.CardPreview)
              for w in [carded.get_first_child()]))
settings.set_boolean('show-preview-cards', True)

# The action bar counts are locale-aware; the expectations below are
# written in the C locale, so pin it the way test-statustext.py does.
_numeric = locale.setlocale(locale.LC_NUMERIC)
locale.setlocale(locale.LC_NUMERIC, 'C')

# A click on one of the tile's own controls is the control's, not the
# tile's: GtkButton claims the sequence in its own release handler, which
# runs after the tile's, so the tile has to check what is under the point
# itself. Clicking the text still opens the post.
clickable = make(post(340, '<p>a post worth opening</p>'), 2, 2)
host_window = Gtk.Window(default_width=400, default_height=300,
                         child=clickable)
host_window.present()
settle()
opened = []
clickable.open = lambda: opened.append(1)

def point_over(widget):
    ok, area = widget.compute_bounds(clickable)
    return area.get_x() + area.get_width() / 2, \
        area.get_y() + area.get_height() / 2

bx, by = point_over(find_of(clickable, statusactions.StatusActions)
                    .boost_button)
clickable._on_released(None, 1, bx, by)
check('a click on a tile control does not also open the tile',
      not opened)
# The tile's own background and its text are not controls, so they
# still open the post.
clickable._on_released(None, 1, 6, 6)
text_point = find_of(clickable, catchupgrid._FitText)
if text_point is not None:
    tx, ty = point_over(text_point)
    clickable._on_released(None, 1, tx, ty)
check('a click on the tile itself still opens the post',
      len(opened) >= 1)
window.set_child(host)
host_window.set_child(None)

# The action bar comes from the timeline component when it exists.
bar = catchupgrid._build_actions(first.item, False, client)
check('the action bar is the timeline component',
      isinstance(bar, statusactions.StatusActions)
      and bar.favourite_button.get_child().get_label() == '7'
      and bar.boost_button.get_child().get_label() == '1.2K')
check('the menu button is the timeline component',
      isinstance(catchupgrid._build_menu_button(first.item, bar, False,
                                                client),
                 statusmenu.StatusMenuButton))

# --------------------------------------------------------------------------
# Freeing

view.set_digest(fixture(30))
at_width(host, 1000, grid)
settle(lambda: all(t._child is not None for t in grid.tiles.values()), 20)
refs = [view.weak_ref(), grid.weak_ref()]
refs += [t.weak_ref() for t in grid.tiles.values()]
refs += [t._child.weak_ref() for t in grid.tiles.values()]
view.more_button.emit('clicked')
settle()
refs += [t.weak_ref() for t in grid.tiles.values()]
del tile, first, news_tile, tag_tile, post_tiles, boost_tile, video, carded
del bar, tiles_before, shared
window.set_child(None)
host = grid = view = window = None
settle()
check('a highlights view, its grid and its tiles are freed',
      all(freed(ref) for ref in refs))

if failures:
    locale.setlocale(locale.LC_NUMERIC, _numeric)
    print('%d failed' % len(failures))
    sys.exit(1)
