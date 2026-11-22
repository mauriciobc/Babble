# SPDX-License-Identifier: GPL-3.0-or-later

"""Bluesky in the timeline: a quote card drawn from the Mastodon preview
card, and the "Bluesky" indicator on a bridged post.

A quote card is drawn from the Mastodon preview card alone, so the
timeline asks Bluesky for nothing here.  Bridgy Fed sends a quoted
Bluesky post as a preview card (author name and handle in the title,
the text in the description, the avatar or first picture as the image),
which is all a native quote card needs; ``bluesky.py`` is reached only
from the thread page, after a click.  Everything the row needs is known
at bind time, so the card has its final height at once and the timeline
does not jump.

A preview card cannot carry a post's own quote, so the timeline card is
one level deep; the thread page draws the whole chain, because the
AppView gives it the quoted records.  And a server may keep no preview
card of the quoted post at all: then the "RE: <link>" line the bridge
left in the text is the only address of it, and the post is fetched
from Bluesky -- behind the same setting, and only then."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import bskytext  # noqa: E402
from . import bluesky  # noqa: E402
from . import linkrouting, uihelpers  # noqa: E402
from . import quoteline, statustext  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .mediabox import CardPreview, status_picture  # noqa: E402
from .mediacache import AvatarCache, _TextureCache  # noqa: E402
from .timelinebase import COLUMN_WIDTH, _flag  # noqa: E402

PICTURE_HEIGHT = 110

# How many lines of text a fetched quote shows, and reserves: the card
# that waits for the post is as tall as the card that holds it, so the
# row does not move when the post arrives.  The same number the Mastodon
# quote card shows.
FETCHED_LINES = 4


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
        quote_data(status.get('card'))) or bool(quoted_ref(status))


def quoted_ref(status):
    """The ``(actor, rkey)`` of the Bluesky post that *status* quotes, or
    None.  A post bridged from Bluesky leaves the post it quotes in a
    "RE: <link>" line, and that link is the only address of the quoted
    post a server may keep: the preview card is the server's crawler at
    work, and it often is not there.

    Only when the native setting is on: the quote is then fetched from
    Bluesky, and the setting is what says Babble may talk to Bluesky."""
    if not enabled():
        return None
    url = statustext.quote_inline_url(status.get('content') or '')
    return bskytext.post_ref(url) if url else None


def wants_inline_dropped(status):
    """True when the "RE: …" line of *status* is shown as a card instead:
    a Mastodon quote, or a card for a Bluesky post that the row will show
    (a poll or attachments suppress the card, and then the line stays)."""
    # No preview card: the quote is fetched from Bluesky instead, and the
    # card that comes says the same thing as the line.
    return quoteline.hidden(status, enabled())


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
        linkrouting.launch_external(widget, bskytext.web_url(actor, rkey))


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


def _reserved_height(lines):
    """The height of *lines* lines of the interface font.  A card that is
    filled in later keeps that height, so a quote that arrives after the
    row was drawn does not move the timeline under the reader."""
    probe = Gtk.Label(label='X\n' * (lines - 1) + 'X')
    return probe.measure(Gtk.Orientation.VERTICAL, -1)[1]


def _quote_head(box, name, data, source=True):
    """The author line of a quoted post: avatar, name, handle and, for the
    outermost card, the word "Bluesky" that says where it came from.  An
    author with no display name is named by the handle alone, so the
    name is not said twice."""
    handle = data.get('handle') or ''
    head = Gtk.Box(spacing=6)
    if data.get('pending'):
        # Nothing is known yet: the card waits for Bluesky to answer.
        head.append(Adw.Spinner(width_request=22, height_request=22))
    else:
        avatar = Adw.Avatar(size=22, show_initials=True, text=name)
        if data.get('avatar'):
            AvatarCache.load(avatar, {'avatar': data['avatar']})
        head.append(avatar)
    if name and name != handle:
        title = Gtk.Label(label=name, xalign=0.0,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('caption-heading')
        head.append(title)
    if handle:
        label = Gtk.Label(label='@%s' % handle, xalign=0.0, hexpand=True,
                          ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        head.append(label)
    if source:
        label = Gtk.Label(label=_('Bluesky'))
        label.add_css_class('caption')
        label.add_css_class('dimmed')
        head.append(label)
    box.append(head)


def _quote_body(box, data, lines=6):
    """The text of a quoted post, or the notice that explains why there
    is none: Bluesky keeps a post from readers who are not signed in,
    and the preview card carries that notice instead of the text."""
    if data.get('hidden'):
        label = Gtk.Label(label=_('Bluesky hides this post from people who '
                                  'are not signed in.'), xalign=0.0, wrap=True,
                          lines=lines, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.add_css_class('dimmed')
        body = label
    elif data.get('pending'):
        label = Gtk.Label(label=_('Loading…'), xalign=0.0, wrap=True,
                          lines=lines, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.add_css_class('dimmed')
        body = label
    else:
        text = data.get('text') or ''
        if not text:
            return
        body = Gtk.Label(label=text, xalign=0.0, wrap=True, lines=lines,
                         ellipsize=Pango.EllipsizeMode.END,
                         wrap_mode=Pango.WrapMode.WORD_CHAR)
    if data.get('reserved'):
        # The height is the one the text will have when it arrives.
        body.set_size_request(-1, _reserved_height(lines))
    box.append(body)


def _quote_media(box, data):
    """At most one picture of a quoted post: its image, or the poster of
    its video (the video itself plays in the thread page)."""
    picture = data.get('picture')
    video = data.get('video') or {}
    poster = video.get('poster')
    if picture:
        widget = status_picture(-1, PICTURE_HEIGHT, presentation=True,
                                kind=_FixedPicture)
        _TextureCache.load(picture, widget, (COLUMN_WIDTH, PICTURE_HEIGHT))
    elif poster:
        widget = status_picture(-1, PICTURE_HEIGHT,
                                alternative_text=video.get('alt') or None,
                                kind=_FixedPicture)
        _TextureCache.load(poster, widget, (COLUMN_WIDTH, PICTURE_HEIGHT))
    else:
        return
    box.append(widget)


class QuotePreview(Gtk.Box):
    """The post a post quotes, drawn inside the card of the post that
    quotes it.  It is not a button: a button inside the card's button
    would be out of reach by keyboard, and the quoted post opens from
    the thread page of the card anyway."""

    __gtype_name__ = 'BabbleBlueskyQuotePreview'

    def __init__(self, data):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('quote-preview')
        name = data.get('name') or data.get('handle') or '?'
        _quote_head(self, name, data, source=False)
        _quote_body(self, data, lines=4)
        _quote_media(self, data)
        nested = data.get('quote')
        if nested:
            self.append(QuotePreview(nested))


class BlueskyQuoteCard(Gtk.Button):
    """Compact card for a Bluesky post: avatar, name, handle, text and at
    most one picture, laid out like ``QuoteCard``.  A click opens the
    native thread page, or the link when the setting is off.

    A post that quotes another post carries the quoted one inside the
    card, as a ``QuotePreview``: the chain of quotes a post holds is part
    of what the post says.

    A card whose post is still on its way from Bluesky (``pending``)
    waits with the height the text will have, so the row it is in does
    not move when the post arrives."""

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
        _quote_head(box, name, data)
        _quote_body(box, data, lines=data.get('lines') or 6)
        _quote_media(box, data)
        nested = data.get('quote')
        if nested:
            box.append(QuotePreview(nested))
        text = data.get('text') or ''
        if data.get('hidden'):
            text = _('Bluesky hides this post from people who are not '
                     'signed in.')
        if data.get('pending'):
            text = _('Loading…')
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Bluesky post by %(author)s: %(text)s') % {
                'author': name, 'text': text}])
        connect_weak(self, 'clicked', self._on_clicked)

    def _on_clicked(self, _button):
        if enabled() and self.data.get('actor') and self.data.get('rkey'):
            open_post(self, self.data['actor'], self.data['rkey'])
        elif self.url:
            linkrouting.open_link(self, self.url)


def fill_quote_box(box, data, key):
    """Fill the row's card box with the Bluesky quote card for *data*.
    The existing child stays when *key* is unchanged, since every
    favourite or boost rebinds the row."""
    box.set_visible(True)
    if getattr(box, '_bsky_key', None) == key \
            and box.get_first_child() is not None:
        return
    uihelpers.clear_children(box)
    box._bsky_key = key
    box.append(BlueskyQuoteCard(data))


def pending_data(actor, rkey):
    """The card of a quoted post that is still on its way from Bluesky."""
    return {'pending': True, 'reserved': True, 'actor': actor, 'rkey': rkey,
            'url': bskytext.web_url(actor, rkey), 'name': '', 'handle': '',
            'text': '', 'avatar': None, 'picture': None, 'video': None,
            'quote': None, 'hidden': False, 'lines': FETCHED_LINES}


def fetched_data(post):
    """The card of a quoted post fetched from Bluesky: its author and its
    text, and nothing more.  A picture, a video or a quote of its own
    would change the height of the row when it arrives, and the thread
    page has all of them."""
    record = bskytext.post_view_record(post)
    data = bskytext.quote_card_data(record, 0)
    data['picture'] = None
    data['video'] = None
    data['reserved'] = True
    data['lines'] = FETCHED_LINES
    data['hidden'] = bskytext.moderation(record)['withheld']
    if data['hidden']:
        data['text'] = ''
    return data


def fetch_quote(box, status):
    """Ask Bluesky for the post that *status* quotes, when the server's
    preview card does not carry it, and draw it in *box* when it
    arrives.  The card waits for the answer, with the height the text
    will have, so the row does not move when the post arrives.

    The answer is dropped when the row is rebound to another post: the
    card box is the state, and only the card that waits for this post
    is filled."""
    card = status.get('card') or {}
    if quote_data(card) and (card.get('title') or card.get('description')):
        return  # the server's card says it already
    ref = quoted_ref(status)
    if ref is None:
        return
    actor, rkey = ref
    key = ('bluesky-pending', actor, rkey)
    fill_quote_box(box, pending_data(actor, rkey), key)
    weak = box.weak_ref()

    def done(post, error):
        target = weak()
        if target is None or post is None:
            return
        if getattr(target, '_bsky_key', None) != key:
            return  # the row moved on to another post
        data = fetched_data(post)
        fill_quote_box(target, data,
                       ('bluesky', data.get('actor'), data.get('rkey')))
    bluesky.get_post_async(actor, rkey, done)


def fill_card_box(box, card):
    """Fill the row's card box for *card*: a ``BlueskyQuoteCard`` for a
    Bluesky post (setting on), else a ``CardPreview``.  The existing child
    stays when the card is unchanged, since every favourite or boost
    rebinds the row."""
    if not card or not (card.get('title') or card.get('description')):
        box.set_visible(False)
        uihelpers.clear_children(box)
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
    uihelpers.clear_children(box)
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
