# SPDX-License-Identifier: GPL-3.0-or-later

"""Bluesky in the timeline: a quote card drawn from the Mastodon preview
card, and the "Bluesky" indicator on a bridged post.

Nothing here touches the network.  Bridgy Fed sends a quoted Bluesky post
as a preview card (author name and handle in the title, the text in the
description, the avatar or first picture as the image), which is all a
native quote card needs; ``bluesky.py`` is reached only from the thread
page, after a click.  Everything the row needs is known at bind time, so
the card has its final height at once and the timeline does not jump."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import bskytext  # noqa: E402
from . import pages  # noqa: E402
from .mediabox import CardPreview, status_picture  # noqa: E402
from .mediacache import AvatarCache, TextureCache  # noqa: E402
from .timelinebase import COLUMN_WIDTH, _flag  # noqa: E402

PICTURE_HEIGHT = 110


def enabled():
    return _flag('bluesky-native')


def quote_data(card):
    """What :func:`bskytext.card_post` makes of *card*, when the native
    card applies (the setting is on), else None."""
    return bskytext.card_post(card) if enabled() else None


def card_wanted(status):
    """Whether the row may show a card for *status*: the link-preview
    setting, or a Bluesky quote, which is not a link preview."""
    return _flag('show-preview-cards') or bool(
        quote_data(status.get('card')))


def wants_inline_dropped(status):
    """True when the "RE: …" line of *status* is shown as a card instead:
    a Mastodon quote, or a card for a Bluesky post that the row will show
    (a poll or attachments suppress the card, and then the line stays)."""
    if status.get('quote'):
        return True
    if status.get('poll') or status.get('media_attachments'):
        return False
    card = status.get('card')
    return bool(quote_data(card)) and bool(
        card.get('title') or card.get('description'))


def open_post(widget, actor, rkey):
    from . import blueskythread
    blueskythread.push_bluesky_thread(widget, actor, rkey)


def view_on_bluesky(widget, actor, rkey):
    """The menu's "View on Bluesky": the native page, or the web page when
    the setting is off, since that setting means never contacting Bluesky
    from Babble."""
    if enabled():
        open_post(widget, actor, rkey)
    else:
        pages.launch_external(widget, bskytext.web_url(actor, rkey))


class _FixedPicture(Gtk.Picture):
    """A picture whose height is PICTURE_HEIGHT whatever it shows: a
    Gtk.Picture is height-for-width by the aspect ratio of its texture, so
    a plain one would grow when the image arrives and move the timeline."""

    __gtype_name__ = 'BabbleFixedPicture'

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return PICTURE_HEIGHT, PICTURE_HEIGHT, -1, -1
        return 0, 0, -1, -1


class BlueskyQuoteCard(Gtk.Button):
    """Compact card for a Bluesky post: avatar, name, handle, text and at
    most one picture, laid out like ``QuoteCard``.  A click opens the
    native thread page, or the link when the setting is off."""

    __gtype_name__ = 'BabbleBlueskyQuoteCard'

    def __init__(self, data):
        super().__init__()
        self.add_css_class('card')
        self.add_css_class('quote-card')
        self.data = data
        self.url = data.get('url') or ''
        name = data.get('name') or data.get('handle') or '?'
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=10,
                      margin_end=10)
        self.set_child(box)
        head = Gtk.Box(spacing=6)
        avatar = Adw.Avatar(size=22, show_initials=True, text=name)
        if data.get('avatar'):
            AvatarCache.load(avatar, {'avatar': data['avatar']})
        head.append(avatar)
        title = Gtk.Label(label=name, xalign=0.0,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('caption-heading')
        head.append(title)
        handle = Gtk.Label(
            label='@%s' % data['handle'] if data.get('handle') else '',
            xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        head.append(handle)
        source = Gtk.Label(label=_('Bluesky'))
        source.add_css_class('caption')
        source.add_css_class('dimmed')
        head.append(source)
        box.append(head)
        text = data.get('text') or ''
        if text:
            body = Gtk.Label(label=text, xalign=0.0, wrap=True, lines=6,
                             ellipsize=Pango.EllipsizeMode.END,
                             wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(body)
        if data.get('picture'):
            picture = status_picture(-1, PICTURE_HEIGHT, presentation=True,
                                     kind=_FixedPicture)
            TextureCache.load(data['picture'], picture,
                               (COLUMN_WIDTH, PICTURE_HEIGHT))
            box.append(picture)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Bluesky post by %(author)s: %(text)s') % {
                'author': name, 'text': text}])
        pages.connect_weak(self, 'clicked', self._on_clicked)

    def _on_clicked(self, _button):
        if enabled() and self.data.get('actor') and self.data.get('rkey'):
            open_post(self, self.data['actor'], self.data['rkey'])
        elif self.url:
            pages.open_link(self, self.url)


def fill_card_box(box, card):
    """Fill the row's card box for *card*: a ``BlueskyQuoteCard`` for a
    Bluesky post (setting on), else a ``CardPreview``.  The existing child
    stays when the card is unchanged, since every favourite or boost
    rebinds the row."""
    if not card or not (card.get('title') or card.get('description')):
        box.set_visible(False)
        pages.clear_children(box)
        box._bsky_key = None
        return
    box.set_visible(True)
    data = quote_data(card)
    key = ('bluesky' if data else 'preview',
           card.get('url') or card.get('uri') or '', card.get('title'),
           card.get('description'), card.get('image'))
    if getattr(box, '_bsky_key', None) == key \
            and box.get_first_child() is not None:
        return
    pages.clear_children(box)
    box._bsky_key = key
    box.append(BlueskyQuoteCard(data) if data else CardPreview(card))


def bridged_badge(row, status):
    """Show the dimmed "Bluesky" indicator on a post bridged from Bluesky,
    building the label on first use."""
    label = getattr(row, 'bluesky_label', None)
    bridged = bskytext.bridged_ref(status) is not None
    if label is None:
        if not bridged:
            return
        label = Gtk.Label(label=_('Bluesky'))
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        label.set_tooltip_text(_('Bridged from Bluesky'))
        row.language_label.get_parent().insert_child_after(
            label, row.language_label)
        row.bluesky_label = label
    label.set_visible(bridged)
