# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up grid container and shared tile-rendering widgets."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')

from gi.repository import (  # noqa: E402
    Adw, Gdk, GObject, Graphene, Gsk, Gtk, Pango)

from ..i18n import gettext as _
from .. import mediacache, pages, timelinedata
from . import digest_layout
from .pipeline import Digest
from ..accounts import plain_name
from .geometry import (
    MIN_COLUMN, MIN_TILES, NATURAL_COLUMNS, NATURAL_WIDTH,
    _soft_breaks, cell_rect, columns_for, grid_height, tile_key,
)
from ..pages import connect_weak, weak_method

def _first_icon(names):
    """The first of *names* the icon theme has (the last one otherwise)."""
    display = Gdk.Display.get_default()
    if display is None:
        return names[-1]
    theme = Gtk.IconTheme.get_for_display(display)
    for name in names:
        if theme.has_icon(name):
            return name
    return names[-1]


def _icon(name, size=12):
    image = Gtk.Image(icon_name=name, pixel_size=size,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    return image


def _label(text, css=(), ellipsize=Pango.EllipsizeMode.END, **kwargs):
    label = Gtk.Label(label=text, xalign=0.0, ellipsize=ellipsize, **kwargs)
    for name in css:
        label.add_css_class(name)
    return label


def _pill(text, icon_name, css, compact=False):
    """A small rounded badge ("Trending", "Following"). *compact* keeps
    only the icon, for a header too narrow for the words."""
    box = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
    box.add_css_class('catchup-pill')
    box.add_css_class(css)
    if icon_name:
        box.append(_icon(icon_name))
    if compact and icon_name:
        pages.set_accessible_label(box, text)
        return box
    label = Gtk.Label(label=text)
    label.add_css_class('caption-heading')
    box.append(label)
    return box


def _trending_pill(compact=False):
    return _pill(_('Trending'),
                 _first_icon(('babble-trending-symbolic',
                              'weather-clear-symbolic')),
                 'catchup-pill-trending', compact)


class _FitText(Gtk.Widget):
    """Wrapped text drawn with Pango, ellipsized to at most *max_lines*
    and to the height it is allocated. It can shrink to nothing
    (*min_lines* 0), so a tile that is short on room loses the end of its
    text instead of growing past its cell, and it changes no property
    while laying out."""

    __gtype_name__ = 'BabbleCatchUpFitText'

    def __init__(self, text='', max_lines=3, min_lines=0, css=(),
                 markup=False):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION,
                         halign=Gtk.Align.FILL)
        self._text = _soft_breaks(text or '')
        self._markup = markup
        self._max_lines = max_lines
        self._min_lines = min_lines
        # The chosen layout, keyed by the size it was shaped for. A tile
        # is snapshotted every frame while the grid scrolls, and re-shaping
        # Pango up to max_lines times per tile per frame is exactly the
        # main-loop work PERFORMANCE.md tracks. The text is set once here.
        self._shaped = None
        for name in css:
            self.add_css_class(name)

    def _layout(self, width, lines=None):
        layout = self.create_pango_layout(None)
        if self._markup:
            layout.set_markup(self._text, -1)
        else:
            layout.set_text(self._text, -1)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        if width >= 0:
            layout.set_width(int(width * Pango.SCALE))
        layout.set_height(-(lines or self._max_lines))
        return layout

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if not self._text:
            return (0, 0, -1, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            layout = self._layout(-1, lines=1)
            return (0, min(layout.get_pixel_size()[0], 2000), -1, -1)
        width = for_size if for_size >= 0 else 400
        natural = self._layout(width).get_pixel_size()[1]
        minimum = 0
        if self._min_lines:
            minimum = min(natural, self._layout(
                width, self._min_lines).get_pixel_size()[1])
        return (minimum, natural, -1, -1)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if not self._text or width <= 0 or height <= 0:
            return
        shaped = self._shaped
        if shaped is not None and shaped[0] == width and shaped[1] == height:
            snapshot.append_layout(shaped[2], self.get_color())
            return
        layout = self._layout(width)
        # Fewer lines until it fits; Pango's own height limit is picky
        # about a pixel and would drop a line that does fit.
        lines = self._max_lines
        while layout.get_pixel_size()[1] > height + 3 and lines > 1:
            lines -= 1
            layout = self._layout(width, lines)
        # Not even one line fits: the tile is over-subscribed and there is
        # no room for the text. Drawing the line anyway puts it on top of
        # whatever is below, which is what "Body lines are limited by the
        # tile's height" (5.3) rules out.
        if layout.get_pixel_size()[1] > height + 3:
            return
        self._shaped = (width, height, layout)
        snapshot.append_layout(layout, self.get_color())


class _AvatarStack(Gtk.Widget):
    """Overlapping round avatars, the first on top of none: sharers of a
    link, or accounts that used a tag."""

    __gtype_name__ = 'BabbleCatchUpAvatarStack'

    def __init__(self, accounts, size=24, step=16):
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.START,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self._size = size
        self._step = step
        for account in accounts:
            avatar = Adw.Avatar(size=size, show_initials=True,
                                text=plain_name(account))
            avatar.add_css_class('catchup-stack')
            mediacache.AvatarCache.load(avatar, account)
            avatar.set_parent(self)

    def _count(self):
        count = 0
        child = self.get_first_child()
        while child is not None:
            count += 1
            child = child.get_next_sibling()
        return count

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return (self._size, self._size, -1, -1)
        count = self._count()
        extent = self._size + max(0, count - 1) * self._step
        return (extent, extent, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        index = 0
        child = self.get_first_child()
        while child is not None:
            child.measure(Gtk.Orientation.HORIZONTAL, -1)
            child.allocate(self._size, self._size, -1,
                           Gsk.Transform().translate(
                               Graphene.Point().init(index * self._step, 0)))
            index += 1
            child = child.get_next_sibling()

    def do_dispose(self):
        while (child := self.get_first_child()) is not None:
            child.unparent()
        Gtk.Widget.do_dispose(self)


class _Context:
    """What tiles need from the digest, with no widget in it."""

    def __init__(self, client, digest):
        self.client = client
        if not isinstance(digest, Digest):
            raise TypeError('expected catchup.Digest, got %s'
                            % type(digest).__name__)
        self.items = dict(digest.items)
        self.boosters = digest.boosters
        self.trending_keys = digest.trending_keys

    def item_for(self, post):
        item = self.items.get(post['id'])
        if item is None:
            item = self.items[post['id']] = timelinedata.StatusItem(post)
        return item
class BentoGrid(Gtk.Widget):
    """Tiles in cells (5.1). ``make_tile(placed)`` creates a tile for a
    ``digest_layout.Placed``; tiles are reused across re-packs, by kind
    and id. The packing is computed once per column count."""

    __gtype_name__ = 'BabbleBentoGrid'
    has_more = GObject.Property(type=bool, default=False)

    def __init__(self, make_tile, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.LIST, **kwargs)
        self._make_tile = make_tile
        self._data = ([], {}, [], [])
        self._pages = digest_layout.INITIAL_PAGES
        self._layouts = {}
        self._tiles = {}
        self._columns = 0
        self._placed = []
        self._dirty = False
        self.pack_count = 0

    @property
    def columns(self):
        return self._columns

    @property
    def placed(self):
        return list(self._placed)

    @property
    def tiles(self):
        return dict(self._tiles)

    def set_content(self, posts, scores, news, tags):
        self._data = (list(posts or []), dict(scores or {}),
                      list(news or []), list(tags or []))
        self._invalidate()

    def set_pages(self, pages_shown):
        if pages_shown != self._pages:
            self._pages = pages_shown
            self._invalidate()
            self._apply_pending_layout()

    @property
    def pages_shown(self):
        return self._pages

    def clear(self):
        self._data = ([], {}, [], [])
        self._pages = digest_layout.INITIAL_PAGES
        self._invalidate()
        self._drop_all()

    def _invalidate(self):
        self._layouts.clear()
        self._dirty = True
        self.queue_resize()

    def _apply_pending_layout(self):
        """Re-pack when already allocated (e.g. Show more at fixed width)."""
        width = self.get_width()
        if width <= 0 or not self._dirty:
            return
        columns = columns_for(width)
        self._columns = columns
        self._dirty = False
        self._placed = self._layout_for(columns)
        self._sync()
        self.queue_resize()

    def _drop_all(self):
        for tile in self._tiles.values():
            tile.unparent()
        self._tiles.clear()
        self._placed = []
        self._set_more(False)

    def _layout_for(self, columns):
        placed = self._layouts.get(columns)
        if placed is None:
            posts, scores, news, tags = self._data
            placed = digest_layout.layout(posts, scores, news, tags,
                                          columns, self._pages)
            self.pack_count += 1
            self._layouts[columns] = placed
        return placed

    def _set_more(self, more):
        if self.has_more != more:
            self.has_more = more

    def _sync(self):
        """Tiles for the current packing: keep the ones still placed
        (their widgets survive a resize), make the new ones, drop the
        rest, and order the children by score rank for the focus
        chain."""
        wanted = {tile_key(p): p for p in self._placed}
        for key in [k for k in self._tiles if k not in wanted]:
            self._tiles.pop(key).unparent()
        for key, placed in wanted.items():
            tile = self._tiles.get(key)
            if tile is None:
                tile = self._make_tile(placed)
                tile.set_parent(self)
                self._tiles[key] = tile
            else:
                tile.set_placed(placed)
        # Focus order follows score rank (2.6), and the children are in
        # the order pack() emitted, which is score rank: it fills holes
        # with later compact posts, so sorting by (row, col) here would
        # replace it with visual order.
        previous = None
        for placed in self._placed:
            tile = self._tiles[tile_key(placed)]
            if tile.get_prev_sibling() is not previous:
                tile.insert_after(self, previous)
            previous = tile
        shown = sum(1 for p in self._placed if p.kind == 'post')
        self._set_more(shown < len(self._data[0]))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (MIN_COLUMN, NATURAL_WIDTH, -1, -1)
        width = for_size if for_size >= 0 else NATURAL_WIDTH
        height = grid_height(self._layout_for(columns_for(width)))
        return (height, height, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        columns = columns_for(width)
        if columns != self._columns or self._dirty:
            self._columns = columns
            self._dirty = False
            self._placed = self._layout_for(columns)
            self._sync()
        for placed in self._placed:
            tile = self._tiles[tile_key(placed)]
            x, y, w, h = cell_rect(width, columns, placed.col, placed.row,
                                   placed.colspan, placed.rowspan)
            tile.measure(Gtk.Orientation.HORIZONTAL, -1)
            tile.measure(Gtk.Orientation.VERTICAL, w)
            tile.allocate(w, h, -1, Gsk.Transform().translate(
                Graphene.Point().init(x, y)))

    def do_dispose(self):
        self._drop_all()
        Gtk.Widget.do_dispose(self)


class HighlightsView(Gtk.Box):
    """The Highlights block of Catch Up: the grid and "Show more".

    ``set_digest(digest)`` replaces everything; ``None`` clears. The view
    hides itself when fewer than 6 tiles would be shown (8)."""

    __gtype_name__ = 'BabbleHighlightsView'

    def __init__(self, client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         visible=False)
        self.client = client
        self.context = None
        self.eligible = 0
        self._digest = None
        # Set by the page, which keeps the expanded pages per snapshot.
        self.on_pages_changed = None
        self.grid = BentoGrid(weak_method(self._make_tile))
        self.more_button = Gtk.Button(
            label=_('Show More'), halign=Gtk.Align.CENTER, visible=False)
        self.more_button.add_css_class('pill')
        connect_weak(self.more_button, 'clicked', self._on_more)
        connect_weak(self.grid, 'notify::has-more', self._on_has_more)
        self.append(self.grid)
        self.append(self.more_button)

    def set_digest(self, digest, pages=None):
        self.grid.clear()
        self._digest = digest
        self.context = None
        self.eligible = 0
        if digest is None:
            self.set_visible(False)
            return
        posts = list(digest.posts or [])
        scores = dict(digest.scores or {})
        news = list(digest.news or [])
        tags = list(digest.tags or [])
        self.eligible = len(digest_layout.allocate(
            posts, scores, news, tags, NATURAL_COLUMNS))
        self.set_visible(self.eligible >= MIN_TILES)
        if self.eligible < MIN_TILES:
            return
        self.context = _Context(self.client, digest)
        self.grid.set_content(posts, scores, news, tags)
        if pages and pages > 1:
            # Reopening a snapshot starts again at the first pages unless
            # it says otherwise; CATCH-UP.md 7 keeps the show-more pages
            # per snapshot id.
            self.grid.set_pages(pages)

    def show_more(self):
        self.grid.set_pages(self.grid.pages_shown + digest_layout.MORE_PAGES)
        if self.on_pages_changed is not None:
            self.on_pages_changed(self.pages_shown)

    @property
    def pages_shown(self):
        return self.grid.pages_shown

    def _make_tile(self, placed):
        from .tiles_render import HashtagTile, NewsTile, PostTile
        kind = {'post': PostTile, 'news': NewsTile,
                'hashtag': HashtagTile}[placed.kind]
        return kind(self.context, placed)

    def _on_more(self, _button):
        self.show_more()

    def _on_has_more(self, grid, _pspec):
        self.more_button.set_visible(grid.has_more)
