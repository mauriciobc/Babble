# SPDX-License-Identifier: GPL-3.0-or-later

"""News and hashtag Catch Up tiles (extracted from tiles_render)."""

from ..i18n import gettext as _

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk  # noqa: E402

from ..charts import Sparkline  # noqa: E402
from .. import (explore, hashtags, linkrouting,  # noqa: E402
                uihelpers)
from . import digest_layout  # noqa: E402
from ..accounts import plain_name  # noqa: E402
from ..weakconnect import connect_weak  # noqa: E402
from .. import statustext  # noqa: E402
from .geometry import (
    FOOTER_HEIGHT, NEWS_IMAGE_SHARE, ROW_HEIGHT, SHARER_AVATARS,
    TAG_AVATARS,
)
from .tiles import (
    _AvatarStack, _FitText, _first_icon, _icon, _label, _pill,
    _trending_pill)
from .tiles_render import _Tile


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
        linkrouting.launch_external(self, self.card.get('url') or '')

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
        button = uihelpers.icon_button('web-browser-symbolic',
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
                spark = Sparkline(tag['history'], 24, 12)
            else:
                spark = Sparkline(None, 24, 12,
                                  values=entry.get('buckets'))
            spark.set_hexpand(True)
            spark.set_vexpand(True)
            spark.set_valign(Gtk.Align.FILL)
            spark.add_css_class('catchup-accent')
            root.append(spark)
        return root



