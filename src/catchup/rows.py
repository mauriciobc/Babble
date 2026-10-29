# SPDX-License-Identifier: GPL-3.0-or-later
"""List items and status rows shared by the Catch Up page."""

import math

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from ..i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

from .. import accounts as account_store, pages  # noqa: E402
from .digest import plain_text  # noqa: E402
from ..mediacache import AvatarCache, _TextureCache  # noqa: E402
from ..statustext import compact_count, relative_time  # noqa: E402
from ..timelinedata import StatusItem  # noqa: E402
from .pipeline import MAX_THUMBS, PEEK_CHARS, THUMB_SIZE, posts_phrase  # noqa: E402
class CatchUpItem(StatusItem):
    """A :class:`StatusItem` the list's models can filter and sort without
    calling back into Python: ``matches`` feeds a ``Gtk.BoolFilter``,
    ``sort_rank`` and ``author_rank`` two ``Gtk.NumericSorter``s. The page
    writes them when the filters change. One item per visible post; the
    Highlights slot receives the same objects."""

    __gtype_name__ = 'BabbleCatchUpItem'

    matches = GObject.Property(type=bool, default=True)
    sort_rank = GObject.Property(type=int, default=0)
    author_rank = GObject.Property(type=int, default=0)

    def __init__(self, data, top=False):
        super().__init__(data)
        self.is_top = top
        if top:
            # The block above the posts is the first item of the list, so
            # it scrolls with them and the list stays virtualized.
            self.sort_rank = -1
            self.author_rank = -1


def _plain_name(account):
    return account_store.plain_name(account)


def _peek(item):
    """One line of text for the row: the filter's reason, the content
    warning, or the start of the post."""
    raw = item.raw
    info = raw.get('_filtered')
    if info and info.get('action') != 'blur':
        titles = [t for t in info.get('titles') or () if t]
        # Translators: the row of a post hidden behind a filter; %s names
        # the filter(s)
        return _('Filtered: %s') % ', '.join(titles) if titles \
            else _('Filtered')
    status = item.status
    spoiler = (status.get('spoiler_text') or '').strip()
    if spoiler:
        # Translators: the row of a post with a content warning; %s is
        # the warning
        return _('CW: %s') % spoiler
    text = plain_text(status.get('content') or '')
    text = ' '.join(text.split())
    return text[:PEEK_CHARS]


def boosters_text(boosted_by, others):
    """'Alice boosted' / 'Alice and 2 others boosted'."""
    name = _plain_name(boosted_by)
    if not others:
        # Translators: %s is the name of the person who boosted the post
        return _('%s boosted') % name
    return ngettext('%(name)s and %(count)d other boosted',
                    '%(name)s and %(count)d others boosted',
                    len(others)) % {'name': name, 'count': len(others)}


class _Stat(Gtk.Box):
    """An icon and a count, in the row's last line."""

    def __init__(self, icon_name):
        super().__init__(spacing=4)
        self.icon = Gtk.Image(icon_name=icon_name, pixel_size=14,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.label = Gtk.Label()
        self.label.add_css_class('caption')
        self.append(self.icon)
        self.append(self.label)
        for child in (self.icon, self.label):
            child.add_css_class('dimmed')

    def set_count(self, count):
        self.label.set_label(compact_count(count))


class CatchUpRow(Gtk.Box):
    """One post of the All posts list, light on purpose: avatar, name and
    time, boosters, one line of text, up to three thumbnails, the counts.
    Rows are recycled by the list view; :meth:`bind` resets everything."""

    __gtype_name__ = 'BabbleCatchUpRow'

    def __init__(self):
        super().__init__(spacing=12, margin_top=12, margin_bottom=12,
                         margin_start=12, margin_end=12)
        self.item = None
        self.avatar = Adw.Avatar(size=40, valign=Gtk.Align.START,
                                 show_initials=True)
        self.append(self.avatar)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                         hexpand=True)
        self.append(column)

        line = Gtk.Box(spacing=6)
        self.name = Gtk.Label(xalign=0.0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.END)
        self.name.add_css_class('heading')
        self.thread_mark = Gtk.Image(
            icon_name='format-indent-more-symbolic', visible=False,
            tooltip_text=_('Thread'))
        self.thread_mark.add_css_class('dimmed')
        self.time = Gtk.Label(xalign=1.0)
        self.time.add_css_class('caption')
        self.time.add_css_class('dimmed')
        line.append(self.name)
        line.append(self.thread_mark)
        line.append(self.time)
        column.append(line)

        self.boost_line = Gtk.Label(xalign=0.0, visible=False,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.boost_line.add_css_class('caption')
        self.boost_line.add_css_class('dimmed')
        column.append(self.boost_line)

        self.peek = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END,
                              single_line_mode=True)
        column.append(self.peek)

        self.thumbs = Gtk.Box(spacing=4, visible=False)
        self.pictures = []
        for _n in range(MAX_THUMBS):
            holder = Gtk.Overlay(width_request=THUMB_SIZE,
                                 height_request=THUMB_SIZE, visible=False,
                                 overflow=Gtk.Overflow.HIDDEN)
            holder.add_css_class('card')
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER,
                                  can_shrink=True,
                                  width_request=THUMB_SIZE,
                                  height_request=THUMB_SIZE,
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION)
            picture.add_css_class('darkenable')
            holder.set_child(picture)
            hidden = Gtk.Image(icon_name='view-conceal-symbolic',
                               visible=False, pixel_size=16,
                               halign=Gtk.Align.CENTER,
                               valign=Gtk.Align.CENTER)
            holder.add_overlay(hidden)
            holder.picture = picture
            holder.hidden = hidden
            self.pictures.append(holder)
            self.thumbs.append(holder)
        column.append(self.thumbs)

        self.stats = Gtk.Box(spacing=16)
        self.replies = _Stat('mail-reply-sender-symbolic')
        self.boosts = _Stat('media-playlist-repeat-symbolic')
        self.likes = _Stat('starred-symbolic')
        for stat in (self.replies, self.boosts, self.likes):
            self.stats.append(stat)
        column.append(self.stats)

    def bind(self, item, others=()):
        self.item = item
        status = item.status
        account = status.get('account') or {}
        name = _plain_name(account)
        self.avatar.set_text(name)
        AvatarCache.load(self.avatar, account)
        self.name.set_label(name)
        self.time.set_label(relative_time(item.raw.get('created_at')))
        self.thread_mark.set_visible(bool(item.raw.get('_thread')))
        if item.boosted_by is not None:
            self.boost_line.set_label(boosters_text(item.boosted_by, others))
            self.boost_line.set_visible(True)
        else:
            self.boost_line.set_visible(False)
        peek = _peek(item)
        self.peek.set_label(peek)
        self.peek.set_visible(bool(peek))

        settings = pages.settings()
        info = item.raw.get('_filtered')
        filtered = bool(info) and info.get('action') != 'blur'
        media = [] if filtered else item.media[:MAX_THUMBS]
        cover = bool(status.get('spoiler_text')) and not \
            settings.get_boolean('show-spoilers')
        sensitive = bool(status.get('sensitive')) and not \
            settings.get_boolean('show-sensitive-media')
        hide = cover or sensitive
        for slot, holder in enumerate(self.pictures):
            if slot < len(media):
                holder.set_visible(True)
                holder.hidden.set_visible(hide)
                url = media[slot].get('preview_url') or media[slot].get('url')
                if url and not hide:
                    holder.picture.set_visible(True)
                    _TextureCache.load(url, holder.picture,
                                       cover=(THUMB_SIZE, THUMB_SIZE))
                else:
                    holder.picture.set_visible(False)
            else:
                holder.set_visible(False)
        self.thumbs.set_visible(bool(media))

        show_counts = settings.get_boolean('show-interaction-counters')
        self.stats.set_visible(show_counts)
        if show_counts:
            self.replies.set_count(status.get('replies_count') or 0)
            self.boosts.set_count(status.get('reblogs_count') or 0)
            self.likes.set_count(status.get('favourites_count') or 0)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ['%s. %s' % (name, peek) if peek else name])

    def unbind(self):
        self.item = None
        for holder in self.pictures:
            holder.picture.set_paintable(None)
        self.avatar.set_custom_image(None)


class PostsBar(Gtk.DrawingArea):
    """One dot per post in time order, or above 160 posts one column per
    time bucket. What matches the list's filters is drawn in the accent
    colour, the rest dimmed (CATCH-UP.md 2.4)."""

    __gtype_name__ = 'BabblePostsBar'

    def __init__(self):
        super().__init__(content_height=36, hexpand=True)
        self.dots = []
        self.bins = None
        self.matching = None
        self.set_draw_func(_draw_posts_bar)
        self._describe()

    def set_data(self, dots, bins):
        self.dots = dots or []
        self.bins = bins
        self.matching = None
        self._describe()
        self.queue_draw()

    def set_matching(self, matching):
        """Highlight the posts whose id is in *matching* (None: all)."""
        self.matching = matching
        self._describe()
        self.queue_draw()

    def total(self):
        if self.bins is not None:
            return sum(len(b) for b in self.bins)
        return len(self.dots)

    def _describe(self):
        total = self.total()
        shown = total if self.matching is None else sum(
            1 for ids in (self.bins if self.bins is not None
                          else [self.dots]) for i in ids
            if i in self.matching)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_('Posts over time'),
             _('%(shown)s of %(total)s') % {
                 'shown': posts_phrase(shown), 'total': total}])


def _draw_posts_bar(area, cr, width, height):
    text = area.get_color()
    accent = Adw.StyleManager.get_default().get_accent_color_rgba()
    matching = area.matching

    def paint(color, alpha):
        cr.set_source_rgba(color.red, color.green, color.blue, alpha)

    if area.bins is not None:
        bins = area.bins
        peak = max((len(b) for b in bins), default=0)
        if not peak:
            return
        step = width / len(bins)
        for index, ids in enumerate(bins):
            if not ids:
                continue
            tall = max(2.0, (height - 2) * len(ids) / peak)
            left = index * step
            paint(text, 0.25)
            cr.rectangle(left, height - tall, max(step - 0.5, 1), tall)
            cr.fill()
            hit = len(ids) if matching is None else sum(
                1 for i in ids if i in matching)
            if hit:
                part = max(2.0, tall * hit / len(ids))
                paint(accent, 1.0)
                cr.rectangle(left, height - part, max(step - 0.5, 1), part)
                cr.fill()
        return
    dots = area.dots
    if not dots:
        return
    step = width / len(dots)
    radius = min(4.0, max(1.0, step * 0.4))
    for index, post_id in enumerate(dots):
        if matching is None or post_id in matching:
            paint(accent, 1.0)
        else:
            paint(text, 0.25)
        cr.arc((index + 0.5) * step, height / 2, radius, 0, 2 * math.pi)
        cr.fill()


class _AuthorChip(Gtk.ToggleButton):
    """Avatar and post count of one author, in the chips row."""

    __gtype_name__ = 'BabbleAuthorChip'

    def __init__(self):
        super().__init__()
        box = Gtk.Box(spacing=6)
        self.avatar = Adw.Avatar(size=24, show_initials=True)
        self.count = Gtk.Label()
        self.count.add_css_class('caption-heading')
        box.append(self.avatar)
        box.append(self.count)
        self.set_child(box)
        self.account_id = None

    def show_author(self, account, count):
        name = _plain_name(account)
        self.account_id = str(account.get('id', ''))
        self.avatar.set_text(name)
        AvatarCache.load(self.avatar, account)
        self.count.set_label(str(count))
        self.set_tooltip_text(name)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ['%s, %s' % (name, posts_phrase(count))])


# --------------------------------------------------------------------------
# The page

class _Slot(Adw.Bin):
    """What a list item holds: the block above the posts, or a row. A row
    is kept for the next post this slot is bound to."""

    def __init__(self):
        super().__init__()
        self.row = None

