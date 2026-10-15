# SPDX-License-Identifier: GPL-3.0-or-later

"""Catch Up Highlights: the bento grid of post, news and hashtag tiles.

``BentoGrid`` is a custom layout (CATCH-UP.md 5.1): as many columns of at
least 180 px as fit, 160 px rows, 12 px gaps. The packing itself is
``catchupdigest.layout``; the grid only asks it for one packing per column
count, keeps the tile widgets across re-packs and puts each one in its cell.
``HighlightsView`` is the block a page shows: the grid and "Show more".

Tiles are cheap to create and build their content later, one tile per
idle dispatch (the same idea as ``StatusRunRow``), so a page of 36 tiles
does not stall the main loop. They never ask for more than their cell:
every tile is clipped, its text is drawn by ``_FitText`` (ellipsized to the
height it is given) and its pictures cover and may shrink.
"""

import collections
import re
from .i18n import gettext as _, ngettext

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')

from gi.repository import (  # noqa: E402
    Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango)

from . import catchupdigest, explore, hashtags, pages, statustext  # noqa: E402
from . import mediacache, mediabox, statusactions, statusmenu  # noqa: E402
from . import timelinedata, timelinebase  # noqa: E402
from .accounts import plain_name  # noqa: E402
from .pages import connect_weak, weak_method  # noqa: E402
from .textemoji import status_emoji_bank  # noqa: E402

MIN_COLUMN = 180
ROW_HEIGHT = 160
GAP = 12
# A column is never wider than this (a one-column grid just below two).
MAX_COLUMN = 2 * MIN_COLUMN + GAP - 1
NATURAL_COLUMNS = 4
NATURAL_WIDTH = NATURAL_COLUMNS * MIN_COLUMN + (NATURAL_COLUMNS - 1) * GAP
MIN_TILES = 6                # below this Highlights is hidden (8)
HEADLINE_CHARS = 80
QUOTE_SHORT_CHARS = 120      # a shorter text-only tile centres its text
INSET_TEXT_CHARS = 140
SHARER_AVATARS = 5
TAG_AVATARS = 3
FOOTER_HEIGHT = 34
NEWS_IMAGE_SHARE = 0.55


# --------------------------------------------------------------------------
# Geometry (pure)

def columns_for(width):
    """Columns of a grid *width* px wide: auto-fill, 180 px minimum."""
    return max(1, (int(width) + GAP) // (MIN_COLUMN + GAP))


def cell_rect(width, columns, col, row, colspan, rowspan):
    """``(x, y, w, h)`` of a cell block in a grid of *width* and *columns*.
    The edges are rounded, not the sizes, so neighbours never overlap or
    leave a pixel gap and the last column ends at *width*."""
    step = (width - GAP * (columns - 1)) / columns + GAP
    left = round(col * step)
    right = round((col + colspan) * step - GAP)
    return (left, row * (ROW_HEIGHT + GAP), right - left,
            rowspan * ROW_HEIGHT + (rowspan - 1) * GAP)


def grid_height(placed):
    """Height of a packing: ``rows * 160 + 12 * (rows - 1)``."""
    rows = catchupdigest.grid_rows(placed)
    return rows * ROW_HEIGHT + (rows - 1) * GAP if rows else 0


def span_cover(colspan, rowspan):
    """The largest box a tile of this span can fill, the size its
    pictures are decoded for. Fixed per span, so a resize keeps the
    cached texture."""
    return (colspan * MAX_COLUMN + (colspan - 1) * GAP,
            rowspan * ROW_HEIGHT + (rowspan - 1) * GAP)


def tile_key(placed):
    ref = placed.ref
    return (placed.kind, ref['id'] if placed.kind == 'post' else ref['key'])


# --------------------------------------------------------------------------
# Text

def _strip_known(text, shortcodes):
    if not shortcodes or ':' not in text:
        return text
    clean = re.sub(r':([A-Za-z0-9_+\-]+):',
                   lambda m: '' if m.group(1) in shortcodes else m.group(0),
                   text)
    return re.sub(r'\s{2,}', ' ', clean) if clean != text else text


def segments_text(segments, shortcodes=(), lines=True):
    """Plain text of ``parse_content`` segments: links shortened, the
    custom emoji in *shortcodes* dropped (a label cannot draw them).
    Line and paragraph breaks are one newline, or a space when *lines* is
    false."""
    parts = []
    last = None
    for segment in segments:
        text = segment['text']
        if text == '\n':
            if parts and parts[-1] != '\n':
                parts.append('\n' if lines else ' ')
            last = None
            continue
        if segment.get('href'):
            text = statustext.shorten_url_text(text)
        text = _strip_known(text, shortcodes).strip()
        if not text:
            continue
        if last is not None and (last.get('sp_after')
                                 or segment.get('sp_before')):
            parts.append(' ')
        parts.append(text)
        last = segment
    return ''.join(parts).strip()


def split_headline(segments):
    """``(headline, body)`` segments (5.3): when the first paragraph is at
    most 80 characters and more text follows, it is the headline;
    otherwise there is none and everything is the body."""
    for index, segment in enumerate(segments):
        if segment['text'] == '\n' and not segment.get('br'):
            first, rest = segments[:index], segments[index + 1:]
            break
    else:
        return [], list(segments)
    while rest and rest[0]['text'] == '\n':
        rest = rest[1:]
    head = segments_text(first, lines=False)
    if head and len(head) <= HEADLINE_CHARS and rest:
        return first, rest
    return [], list(segments)


def _soft_breaks(text):
    """Hard line breaks in a tile's text become spaces.

    Mastodon sends ``<br>`` for the line breaks in a list, and
    ``statustext.parse_content`` hands them on as ``\\n``. Pango counts a
    hard break as a line whatever ``set_height`` says, so a post with
    many of them measures far taller than the card no matter how few
    lines the tile asked for: one line of the sample post measures 305 px
    against a 232 px card, and the text is dropped rather than shown
    short. A tile re-flows its text to the card anyway, so the breaks
    are handed to the wrap instead.
    """
    return re.sub(r'[ \t]*\n[ \t]*', ' ', text).strip()


def _markup(text):
    return GLib.markup_escape_text(text)


# --------------------------------------------------------------------------
# Small widgets

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
        box.set_tooltip_text(text)
        box.update_property([Gtk.AccessibleProperty.LABEL], [text])
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


# --------------------------------------------------------------------------
# Action bar and menu: the only place that knows the timeline components

def _build_actions(item, on_image, client=None, large=False):
    """The action bar of a post tile, bound to *item*: reply, boost,
    favourite and bookmark with their counts. Pill buttons over an image
    on a hero, ``osd`` buttons over any other image."""
    classes = (('osd', 'pill') if large else ('osd',)) if on_image \
        else ('flat',)
    bar = statusactions.StatusActions(
        quote=False, button_classes=classes, tinted=True,
        count_format=statustext.compact_count)
    bar.add_css_class('catchup-actions')
    bar.set_child_spacing(2)
    bar.set_line_spacing(0)
    if client is None:
        bar.bind(item)
    else:
        bar.bind(item, client)
    return bar


def _build_menu_button(item, actions, on_image, client=None):
    """The "⋯" button of a post tile with StatusRow's menu."""
    button = statusmenu.StatusMenuButton(actions=actions, button_classes=(
        ('osd', 'circular') if on_image else ('flat', 'circular')))
    if client is None:
        button.bind(item)
    else:
        button.bind(item, client)
    return button


# --------------------------------------------------------------------------
# Tiles

_pending = collections.deque()
_idle = [0]


def _drain():
    """Build one waiting tile per main-loop dispatch, below redraw."""
    while _pending:
        tile = _pending.popleft()()
        if tile is not None and tile.get_mapped() and tile._child is None:
            tile.ensure_built()
            break
    if _pending:
        return GLib.SOURCE_CONTINUE
    _idle[0] = 0
    return GLib.SOURCE_REMOVE


def _queue_build(tile):
    _pending.append(tile.weak_ref())
    if not _idle[0]:
        _idle[0] = GLib.idle_add(_drain, priority=GLib.PRIORITY_DEFAULT_IDLE)


class _Context:
    """What tiles need from the digest, with no widget in it."""

    def __init__(self, client, digest):
        from .catchup import Digest
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


class _Tile(Gtk.Widget):
    """A card in one cell: focusable, labelled for assistive tech, opens
    what it shows. The content is built when the tile is first mapped (one
    per idle dispatch) and again if a re-pack changes its span."""

    __gtype_name__ = 'BabbleCatchUpTile'

    def __init__(self, context, placed):
        super().__init__(focusable=True, overflow=Gtk.Overflow.HIDDEN,
                         accessible_role=Gtk.AccessibleRole.LIST_ITEM)
        self.add_css_class('card')
        self.add_css_class('activatable')
        self.add_css_class('catchup-tile')
        self.context = context
        self.placed = placed
        self._child = None
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        connect_weak(click, 'released', self._on_released)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        connect_weak(keys, 'key-pressed', self._on_key)
        self.add_controller(keys)

    # To be provided

    def describe(self):
        return ''

    def build_content(self):
        raise NotImplementedError

    def open(self):
        pass

    # Life cycle

    def set_label(self, text):
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _refresh_prefs(self):
        """Re-read the preferences a tile reads while it is on screen.
        Only the post tiles show spoiler-hidden and blurred content."""

    def set_placed(self, placed):
        before = self.span_key()
        self.placed = placed
        # A re-pack is also when the spoiler/media preferences get read
        # again, so turning them on in Preferences shows on the tiles
        # already on screen.
        self._refresh_prefs()
        if self._child is not None and self.span_key() != before:
            self._drop_content()
            if self.get_mapped():
                _queue_build(self)

    def span_key(self):
        placed = self.placed
        return (placed.colspan, placed.rowspan, placed.tier)

    def ensure_built(self):
        if self._child is None:
            child = self.build_content()
            child.set_parent(self)
            self._child = child
            self.set_label(self.describe())

    def _drop_content(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None

    def do_map(self):
        Gtk.Widget.do_map(self)
        if self._child is None:
            _queue_build(self)

    def do_dispose(self):
        self._drop_content()
        Gtk.Widget.do_dispose(self)

    # Layout: the tile asks for nothing and clips whatever is inside

    def do_measure(self, orientation, for_size):
        if self._child is None:
            return (0, 0, -1, -1)
        natural = self._child.measure(orientation, for_size)[1]
        return (0, natural, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        child = self._child
        if child is None:
            return
        wide = max(width, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        high = max(height, child.measure(Gtk.Orientation.VERTICAL, wide)[0])
        child.allocate(wide, high, -1, None)

    # Activation

    # What under a point handles its own click: the action bar, the
    # overflow menu, a link card's button. Labels and other non-target
    # widgets are skipped by pick() and so land on the tile itself,
    # which is what we want: clicking the text opens the thread.
    _CONTROLS = (Gtk.Button,)

    def _on_released(self, _gesture, _n_press, x, y):
        if not self.contains(x, y):
            return
        # GtkButton claims the sequence in its own *release* handler,
        # which runs after this one, so the sequence is still unclaimed
        # here and the claim cannot be relied on to keep the tile shut.
        # Claiming from the tile would be worse still: GTK cancels the
        # gestures underneath, which would stop the control working at
        # all. Ask what is under the point instead.
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not self:
            if isinstance(target, self._CONTROLS):
                return
            target = target.get_parent()
        self.open()

    def _on_key(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.open()
            return True
        return False

    # Helpers for the three kinds

    def picture(self, url, blur=False, alt='', cover=None):
        picture = (mediabox.BlurredPicture(alternative_text=alt) if blur
                   else Gtk.Picture(alternative_text=alt,
                                    accessible_role=Gtk.AccessibleRole
                                    .PRESENTATION))
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_can_shrink(True)
        picture.add_css_class('darkenable')
        picture.set_hexpand(True)
        picture.set_vexpand(True)
        if url:
            mediacache._TextureCache.load(
                url, picture,
                cover or span_cover(self.placed.colspan,
                                    self.placed.rowspan))
        return picture


class PostTile(_Tile):
    """A post: boost line, header, text, media or link card, actions."""

    __gtype_name__ = 'BabbleCatchUpPostTile'

    def _refresh_prefs(self):
        flag = timelinebase._flag
        self.cw_hidden = bool(self.spoiler) and not flag('show-spoilers')
        self.blur = self.cw_hidden or (
            bool(self.item.status.get('sensitive'))
            and not flag('show-sensitive-media'))

    def __init__(self, context, placed):
        self.post = placed.ref
        self.item = context.item_for(self.post)
        super().__init__(context, placed)
        status = self.item.status
        self.spoiler = (status.get('spoiler_text') or '').strip()
        # Read in _refresh_prefs, not frozen here: tiles survive a re-pack
        # and a Preferences change must take effect without a new digest,
        # the way show-preview-cards already does.
        self._refresh_prefs()
        self.media = [a for a in status.get('media_attachments') or []
                      if a.get('type') in ('image', 'video', 'gifv')]
        self.trending = catchupdigest.is_trending_post(
            self.post, context.trending_keys)
        self._segments = statustext.parse_content(status.get('content'))
        shortcodes = set(status_emoji_bank(status))
        if self.cw_hidden:
            self.headline, self.body = '', segments_text(
                [{'text': self.spoiler, 'href': None}], shortcodes)
        else:
            head, rest = split_headline(self._segments)
            self.headline = segments_text(head, shortcodes, lines=False)
            self.body = segments_text(rest, shortcodes)
        self.set_label(self.describe())
        connect_weak(self.item, 'changed', self._on_item_changed)

    @property
    def form(self):
        """'overlay' (image fills the tile, text over a gradient), 'inset'
        (image under the header, text over its bottom), 'inline' (images
        under or above the text) or 'plain' (no media)."""
        placed = self.placed
        if not self.media:
            return 'plain'
        if placed.rowspan == 1 or (placed.tier == 'hero'
                                   and placed.colspan >= 2):
            return 'overlay'
        text_length = len(self.headline) + len(self.body)
        if len(self.media) > 1 or text_length > INSET_TEXT_CHARS:
            return 'inline'
        return 'inset'

    @property
    def hero(self):
        return self.placed.tier == 'hero'

    def describe(self):
        status = self.item.status
        account = status.get('account') or {}
        excerpt = self.body or self.headline
        if self.headline and self.body and not self.cw_hidden:
            excerpt = '%s. %s' % (self.headline, self.body)
        excerpt = ' '.join(excerpt.split())
        if len(excerpt) > 200:
            excerpt = excerpt[:199] + '…'
        counts = [
            ngettext('%d reply', '%d replies', self.item.reply_count)
            % self.item.reply_count,
            ngettext('%d boost', '%d boosts', self.item.boost_count)
            % self.item.boost_count,
            ngettext('%d favorite', '%d favorites',
                     self.item.favourite_count) % self.item.favourite_count]
        return '. '.join(part for part in (
            plain_name(account), excerpt, ', '.join(counts)) if part)

    def open(self):
        statusactions._open_thread(self, self.context.client, self.item)

    def _on_item_changed(self, _item):
        self.set_label(self.describe())

    # Parts

    def _boost_line(self):
        item = self.item
        if not item.is_boost:
            return None
        name = plain_name(item.boosted_by)
        others = len(self.context.boosters.get(self.post['id']) or [])
        if others:
            # Translators: shown above a post boosted by several people;
            # the first %s is the first booster's name, %d the number of
            # others, e.g. "Ana and 2 others boosted".
            text = ngettext('%s and %d other boosted',
                            '%s and %d others boosted', others) \
                % (name, others)
        else:
            text = _('%s boosted') % name
        box = Gtk.Box(spacing=6)
        icon = _icon(statusactions.StatusActions.ICON_BOOST)
        icon.add_css_class('boost-accent')
        box.append(icon)
        box.append(_label(text, ('caption-heading', 'dimmed')))
        return box

    def _header(self, size, on_image, actions):
        status = self.item.status
        account = status.get('account') or {}
        box = Gtk.Box(spacing=8)
        avatar = Adw.Avatar(size=size, show_initials=True,
                            text=plain_name(account))
        mediacache.AvatarCache.load(avatar, account)
        box.append(avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.CENTER, hexpand=True)
        names.append(_label(plain_name(account), ('heading',)))
        meta = Gtk.Box()
        meta.append(_label(self.item.handle, ('caption', 'dimmed'),
                           Pango.EllipsizeMode.MIDDLE))
        stamp = self.item.timestamp
        if stamp:
            meta.append(Gtk.Label(label=' · %s' % stamp))
            meta.get_last_child().add_css_class('caption')
            meta.get_last_child().add_css_class('dimmed')
        names.append(meta)
        box.append(names)
        if self.trending and not on_image:
            box.append(_trending_pill(self.placed.colspan == 1))
        menu = _build_menu_button(self.item, actions, on_image,
                                  self.context.client)
        if menu is not None:
            connect_weak(menu, 'refresh', self._on_menu_refresh)
            connect_weak(menu, 'deleted', self._on_menu_deleted)
            self._menu = menu
            box.append(menu)
        return box

    def _on_menu_refresh(self, _menu):
        self.set_label(self.describe())

    def _on_menu_deleted(self, _menu):
        self.set_visible(False)

    def _texts(self, headline_lines, body_lines, quote=False):
        """The headline and body widgets. *quote* is a text-only tile two
        rows tall: large type that fills the card, centred when short."""
        short = len(self.headline) + len(self.body) <= QUOTE_SHORT_CHARS
        parts = []
        if self.headline:
            css = ('title-2' if self.hero else 'title-3') if quote \
                else ('title-2' if self.hero else 'heading')
            parts.append(_FitText(
                _markup(self.headline), 4 if quote else headline_lines,
                headline_lines and 1, (css,), markup=True))
        if self.body:
            if quote:
                if self.hero:
                    css = ['title-1' if short and not self.headline
                           else 'title-3']
                else:
                    css = ['title-3' if short and not self.headline
                           else 'title-4']
            else:
                css = ['body']
            if self.cw_hidden:
                css.append('catchup-cw')
            body = _FitText(_markup(self.body), 16 if quote else body_lines,
                            0, css, markup=True)
            body.set_vexpand(not quote or not short)
            parts.append(body)
        if quote and short and parts:
            centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=6, vexpand=True,
                             valign=Gtk.Align.CENTER)
            for part in parts:
                centre.append(part)
            return [centre]
        return parts

    def _media_url(self, attachment):
        if self.hero and attachment.get('type') == 'image':
            return attachment.get('url') or attachment.get('preview_url')
        return attachment.get('preview_url') or attachment.get('url')

    def _media_picture(self, attachment, cover=None, play=False):
        """The picture of an attachment. *play* centres a play glyph on a
        video: only where no text is drawn over the picture (the badge in
        the corner says it everywhere else)."""
        picture = self.picture(
            self._media_url(attachment), self.blur,
            attachment.get('description') or '', cover)
        if play and attachment.get('type') in ('video', 'gifv'):
            overlay = Gtk.Overlay(child=picture)
            play = _icon('media-playback-start-symbolic', 20)
            play.set_halign(Gtk.Align.CENTER)
            play.set_valign(Gtk.Align.CENTER)
            play.add_css_class('osd')
            play.add_css_class('catchup-play')
            overlay.add_overlay(play)
            return overlay
        return picture

    def _media_badge(self):
        kind = self.media[0].get('type')
        box = Gtk.Box(spacing=4, valign=Gtk.Align.START)
        box.add_css_class('osd')
        box.add_css_class('catchup-pill')
        box.append(_icon('image-x-generic-symbolic' if kind == 'image'
                         else 'media-playback-start-symbolic'))
        if len(self.media) > 1:
            count = Gtk.Label(label=str(len(self.media)))
            count.add_css_class('caption-heading')
            box.append(count)
        return box

    def build_content(self):
        form = self.form
        if form == 'overlay':
            return self._build_overlay()
        return self._build_flow(form)

    def _build_overlay(self):
        placed = self.placed
        hero = self.hero
        root = Gtk.Overlay()
        picture = self._media_picture(self.media[0])
        picture.add_css_class('catchup-cover')
        root.set_child(picture)
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        valign=Gtk.Align.END, margin_start=10, margin_end=10,
                        margin_bottom=8)
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.END)
        scrim.add_css_class('catchup-scrim')
        scrim.add_css_class('catchup-on-image')
        scrim.append(block)
        boost = self._boost_line()
        if boost is not None:
            block.append(boost)
        actions = _build_actions(self.item, True, self.context.client, hero)
        block.append(self._header(40 if hero else 32, True, actions))
        tall = placed.rowspan > 1
        for part in self._texts(2 if tall else 1, 6 if tall else 2):
            block.append(part)
        block.append(actions)
        root.add_overlay(scrim)
        corner = Gtk.Box(spacing=6, halign=Gtk.Align.END,
                         valign=Gtk.Align.START, margin_top=8,
                         margin_end=8)
        if self.trending:
            corner.append(_trending_pill())
        corner.append(self._media_badge())
        root.add_overlay(corner)
        return root

    def _build_flow(self, form):
        placed = self.placed
        hero = self.hero
        side = 10 if placed.colspan == 1 else 12
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=10, margin_bottom=8, margin_start=side,
                      margin_end=side)
        boost = self._boost_line()
        if boost is not None:
            box.append(boost)
        actions = _build_actions(self.item, False, self.context.client)
        box.append(self._header(40 if hero else 32, False, actions))
        card = self.item.status.get('card')
        # A 1x1 cell is 160 px: header, card and action bar fill it, and
        # the text would be squeezed out entirely. Cards are for the
        # taller tiles, where there is room for both.
        show_card = (not self.media and not self.cw_hidden and card
                     and card.get('url') and card.get('title')
                     and placed.rowspan > 1
                     and timelinebase._flag('show-preview-cards'))
        tall = placed.rowspan > 1
        quote = form == 'plain' and tall and not show_card
        texts = self._texts(2 if tall else 1, 10 if tall else 6, quote)
        if form == 'inline' and len(self.media) == 1:
            # One image above the text.
            box.append(self._inline_media(110))
            for part in texts:
                box.append(part)
        elif form == 'inset':
            frame = Gtk.Overlay(vexpand=True)
            frame.add_css_class('status-media')
            frame.set_overflow(Gtk.Overflow.HIDDEN)
            frame.set_child(self._media_picture(
                self.media[0], span_cover(1, placed.rowspan)))
            scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            valign=Gtk.Align.END, spacing=2)
            scrim.add_css_class('catchup-scrim')
            scrim.add_css_class('catchup-on-image')
            for part in texts:
                scrim.append(part)
            if texts:
                frame.add_overlay(scrim)
            badge = self._media_badge()
            badge.set_halign(Gtk.Align.END)
            badge.set_margin_top(6)
            badge.set_margin_end(6)
            frame.add_overlay(badge)
            box.append(frame)
        else:
            # A text-only tile is header, text, action bar; without this
            # the text keeps to the top of whatever is left and the card
            # shows a gap above the bar. The group takes the slack and
            # sits centred in it, which is also what _texts' "centred
            # when short" is about. The image forms keep the text where
            # it belongs against the picture instead.
            for part in (self._text_group(texts) if quote
                         else self._spread(texts)):
                box.append(part)
            if form == 'inline':
                box.append(self._inline_media(96))
        if not texts and form == 'plain':
            box.append(Gtk.Box(vexpand=True))
        if show_card:
            box.append(mediabox.CardPreview(card))
        box.append(actions)
        return box

    def _text_group(self, texts):
        """The headline and body as one centred block that takes the room
        the text does not need, so a text-only card has no gap above the
        action bar. The parts are centred too: the group being centred
        would otherwise leave the slack inside it, under the last line."""
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        valign=Gtk.Align.CENTER, vexpand=True)
        for part in texts:
            part.set_valign(Gtk.Align.CENTER)
            group.append(part)
        return [group]

    def _spread(self, texts):
        """The text as it is, each block on its own, for the tiles where
        the text belongs against the top of the card."""
        return texts

    def _inline_media(self, height):
        row = Gtk.Box(spacing=4, homogeneous=True)
        for attachment in self.media[:2]:
            picture = self._media_picture(
                attachment, (MAX_COLUMN, height), play=True)
            picture.add_css_class('status-media')
            picture.set_size_request(-1, height)
            row.append(picture)
        return row


class NewsTile(_Tile):
    """A trending or shared link (5.4)."""

    __gtype_name__ = 'BabbleCatchUpNewsTile'

    def __init__(self, context, placed):
        self.entry = placed.ref
        self.card = self.entry.get('card') or {}
        super().__init__(context, placed)
        self.set_label(self.describe())

    @property
    def image_led(self):
        return bool(self.card.get('image')) and self.placed.rowspan >= 2

    def describe(self):
        title, provider = explore.link_card_labels(self.card)
        parts = [_('News'), provider, title]
        if self.card.get('description'):
            parts.append(self.card['description'])
        sharers = self.entry.get('sharers')
        if sharers:
            parts.append(explore.shared_by(sharers, plain_name))
        return '. '.join(part for part in parts if part)

    def open(self):
        explore.open_link_timeline(self, self.context.client, self.card)

    def _on_browser(self, _button):
        pages.launch_external(self, self.card.get('url') or '')

    def _kind_label(self, with_provider):
        _title, provider = explore.link_card_labels(self.card)
        row = Gtk.Box(spacing=6)
        row.append(_icon(_first_icon(('babble-news-symbolic',
                                      'text-x-generic-symbolic')), 14))
        row.append(Gtk.Label(label=_('News')))
        row.get_last_child().add_css_class('caption-heading')
        if with_provider:
            detail = statustext.relative_time(self.card.get('published_at'))
            text = ' · '.join(part for part in (provider, detail) if part)
            row.append(_label(text, ('caption', 'dimmed')))
        return row

    def _footer(self):
        footer = Gtk.Box(spacing=8)
        footer.set_size_request(-1, FOOTER_HEIGHT)
        sharers = self.entry.get('sharers') or []
        if sharers:
            footer.append(_AvatarStack(sharers[:SHARER_AVATARS]))
        text = explore.link_card_footer_text(
            self.card, sharers if sharers else None)
        label = _label(text, ('caption', 'dimmed'), hexpand=True)
        footer.append(label)
        button = pages.icon_button('web-browser-symbolic',
                                   _('Open in Browser'))
        connect_weak(button, 'clicked', self._on_browser)
        footer.append(button)
        return footer

    def build_content(self):
        title, provider = explore.link_card_labels(self.card)
        description = self.card.get('description') or ''
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if self.image_led:
            height = round(self.placed.rowspan * ROW_HEIGHT
                           * NEWS_IMAGE_SHARE)
            image = Gtk.Overlay()
            picture = self.picture(self.card['image'],
                                   alt=self.card.get('image_description')
                                   or '')
            picture.add_css_class('catchup-cover')
            picture.set_vexpand(False)
            picture.set_size_request(-1, height)
            image.set_child(picture)
            chip = self._kind_label(True)
            chip.add_css_class('osd')
            chip.add_css_class('catchup-pill')
            chip.set_halign(Gtk.Align.START)
            chip.set_valign(Gtk.Align.START)
            chip.set_margin_top(8)
            chip.set_margin_start(8)
            image.add_overlay(chip)
            if self.entry.get('trending'):
                pill = _trending_pill()
                pill.set_valign(Gtk.Align.START)
                pill.set_margin_top(8)
                pill.set_margin_end(8)
                image.add_overlay(pill)
            root.append(image)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           margin_top=8, margin_bottom=0, margin_start=12,
                           margin_end=12, vexpand=True)
            text.append(_FitText(title, 3, 1, ('title-3',)))
            if description:
                text.append(_FitText(description, 2, 0, ('body',)))
            root.append(text)
        else:
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           margin_top=10, margin_start=12, margin_end=12,
                           vexpand=True)
            top = self._kind_label(True)
            top.set_hexpand(True)
            if self.entry.get('trending'):
                top.append(_trending_pill())
            text.append(top)
            text.append(_FitText(title, 2, 1, ('heading',)))
            if description:
                text.append(_FitText(description, 1, 0, ('body',)))
            root.append(text)
        footer = self._footer()
        footer.set_margin_start(12)
        footer.set_margin_end(6)
        footer.set_margin_bottom(4)
        root.append(footer)
        return root


class HashtagTile(_Tile):
    """A trending, followed or timeline hashtag (5.4)."""

    __gtype_name__ = 'BabbleCatchUpHashtagTile'

    def __init__(self, context, placed):
        self.entry = placed.ref
        super().__init__(context, placed)
        self.set_label(self.describe())

    @property
    def wide(self):
        return self.placed.colspan >= 2

    @property
    def tag(self):
        return self.entry.get('tag') or {}

    @property
    def show_sparkline(self):
        return catchupdigest.days_with_uses(self.entry) \
            >= catchupdigest.HASHTAG_WIDE_DAYS

    def lines(self):
        """The counts the tile writes under the name: the usage line and
        the posts in the reader's timeline."""
        return hashtags.catchup_hashtag_lines(self.entry)

    def describe(self):
        usage, mine = self.lines()
        return '. '.join(part for part in (
            _('Hashtag'), '#' + self.entry['name'], usage, mine) if part)

    def open(self):
        hashtags.push_hashtag(self, self.context.client, self.entry['name'])

    def build_content(self):
        entry = self.entry
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       margin_top=10, margin_bottom=10, margin_start=12,
                       margin_end=12)
        top = Gtk.Box(spacing=6)
        badge = hashtags.hash_badge()
        top.append(badge)
        kind = Gtk.Label(label=_('Hashtag'), xalign=0.0, hexpand=True)
        kind.add_css_class('caption-heading')
        top.append(kind)
        if entry.get('trending'):
            top.append(_trending_pill())
        elif entry.get('following'):
            top.append(_pill(_('Following'), None, 'catchup-pill-following'))
        root.append(top)
        root.append(_label('#' + entry['name'],
                           ('title-2' if self.wide else 'title-3',)))
        usage, mine = self.lines()
        counts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                         hexpand=True)
        if usage:
            counts.append(_FitText(usage, 2, 0, ('caption', 'dimmed')))
        if mine:
            counts.append(_label(mine, ('caption', 'catchup-accent')))
        accounts = (entry.get('accounts') or [])[:TAG_AVATARS]
        if self.wide and accounts:
            row = Gtk.Box(spacing=8)
            row.append(counts)
            row.append(_AvatarStack(accounts))
            root.append(row)
        else:
            root.append(counts)
        if self.show_sparkline:
            tag = self.tag
            if tag.get('history'):
                spark = hashtags.Sparkline(tag['history'], 24, 12)
            else:
                spark = hashtags.Sparkline(None, 24, 12,
                                           values=entry.get('buckets'))
            spark.set_hexpand(True)
            spark.set_vexpand(True)
            spark.set_valign(Gtk.Align.FILL)
            spark.add_css_class('catchup-accent')
            root.append(spark)
        return root


# --------------------------------------------------------------------------
# The grid

class BentoGrid(Gtk.Widget):
    """Tiles in cells (5.1). ``make_tile(placed)`` creates a tile for a
    ``catchupdigest.Placed``; tiles are reused across re-packs, by kind
    and id. The packing is computed once per column count."""

    __gtype_name__ = 'BabbleBentoGrid'
    has_more = GObject.Property(type=bool, default=False)

    def __init__(self, make_tile, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.LIST, **kwargs)
        self._make_tile = make_tile
        self._data = ([], {}, [], [])
        self._pages = catchupdigest.INITIAL_PAGES
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
        self._pages = catchupdigest.INITIAL_PAGES
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
            placed = catchupdigest.layout(posts, scores, news, tags,
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
        self.eligible = len(catchupdigest.allocate(
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
        self.grid.set_pages(self.grid.pages_shown + catchupdigest.MORE_PAGES)
        if self.on_pages_changed is not None:
            self.on_pages_changed(self.pages_shown)

    @property
    def pages_shown(self):
        return self.grid.pages_shown

    def _make_tile(self, placed):
        kind = {'post': PostTile, 'news': NewsTile,
                'hashtag': HashtagTile}[placed.kind]
        return kind(self.context, placed)

    def _on_more(self, _button):
        self.show_more()

    def _on_has_more(self, grid, _pspec):
        self.more_button.set_visible(grid.has_more)
