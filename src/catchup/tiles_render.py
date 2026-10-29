# SPDX-License-Identifier: GPL-3.0-or-later

"""Lazy post, news and hashtag tile widgets for Catch Up Highlights.

Each tile builds its content on the idle queue after it is mapped. Shared
GTK primitives live in ``tiles.py``; dimensions and text preparation live
in ``geometry.py``.
"""

import collections
from ..i18n import gettext as _, ngettext

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from .. import explore, hashtags, pages, statustext  # noqa: E402
from . import digest as catchupdigest, digest_layout  # noqa: E402
from .. import mediacache, mediabox, statusactions
from .. import timelinebase
from ..accounts import plain_name
from ..pages import connect_weak
from ..textemoji import status_emoji_bank  # noqa: E402
from .geometry import (
    FOOTER_HEIGHT, INSET_TEXT_CHARS, MAX_COLUMN, NEWS_IMAGE_SHARE,
    QUOTE_SHORT_CHARS, ROW_HEIGHT, SHARER_AVATARS, TAG_AVATARS,
    segments_text, span_cover, split_headline,
)
from .tiles import (
    _AvatarStack, _FitText, _first_icon, _icon, _label, _pill,
    _trending_pill)
from .. import tileactions


def _markup(text):
    return GLib.markup_escape_text(text)


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
        classes = ('osd', 'circular') if on_image else ('flat', 'circular')
        menu = tileactions.tile_menu_button(
            self.item, self.context.client or tileactions.WINDOW_CLIENT,
            actions=actions,
            button_classes=classes)
        connect_weak(menu, 'refresh', self._on_menu_refresh)
        connect_weak(menu, 'deleted', self._on_menu_deleted)
        connect_weak(menu, 'restored', self._on_menu_restored)
        self._menu = menu
        box.append(menu)
        return box

    def _on_menu_refresh(self, _menu):
        self.set_label(self.describe())

    def _on_menu_deleted(self, _menu):
        self.set_visible(False)

    def _on_menu_restored(self, _menu):
        self.set_visible(True)

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
        action_classes = ('osd', 'pill') if hero else ('osd',)
        actions = tileactions.tile_action_bar(
            self.item, self.context.client or tileactions.WINDOW_CLIENT,
            quote=False,
            button_classes=action_classes, tinted=True, catchup_style=True)
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
        actions = tileactions.tile_action_bar(
            self.item, self.context.client or tileactions.WINDOW_CLIENT,
            quote=False,
            button_classes=('flat',), tinted=True, catchup_style=True)
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
        return digest_layout.days_with_uses(self.entry) \
            >= digest_layout.HASHTAG_WIDE_DAYS

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



