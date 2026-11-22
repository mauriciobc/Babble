# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact read-only snapshot of the post a notification is about."""

import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import filterrules  # noqa: E402
from . import linkrouting  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .textemoji import EMOJI_RE, status_emoji_bank  # noqa: E402
from .statustext import parse_content, shorten_url_text  # noqa: E402
from .timelinebase import status_filter  # noqa: E402
from .mediabox import BlurredPicture  # noqa: E402
from .mediaopen import MediaOpener  # noqa: E402


class NotificationPost(MediaOpener, Gtk.Box):
    """Compact read-only snapshot of the post a notification is about;
    the surrounding row opens the thread."""

    __gtype_name__ = 'BabbleNotificationPost'

    # Truncation for long plain content behind a 'Read More' button.
    READ_MORE_CHARS = 240
    MAX_THUMBNAILS = 4
    THUMBNAIL_HEIGHT = 96

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('notification-post')
        self._segments = []
        self._bank = {}
        # href -> hashtag name, for the anchors parse_content flagged.
        self._tag_by_href = {}
        # Built the first time a server filter applies to a post.
        self.filter_bar = None
        self.filter_label = None
        self._item = None

        byline = Gtk.Box(spacing=6)
        self.byline = byline
        self.name_label = Gtk.Label(xalign=0.0,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.name_label.add_css_class('caption-heading')
        byline.append(self.name_label)
        self.handle_label = Gtk.Label(xalign=0.0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.handle_label.add_css_class('caption')
        self.handle_label.add_css_class('dimmed')
        byline.append(self.handle_label)
        self.append(byline)

        self.content = Gtk.Label(xalign=0.0, hexpand=True, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 use_markup=True)
        # A Gtk.Label's own activate-link opens the URI externally;
        # route it through the app instead, hashtags included.
        connect_weak(self.content, 'activate-link',
                     self._on_activate_link)
        self.append(self.content)

        self.more_button = Gtk.Button(label=_('Read More'),
                                      halign=Gtk.Align.START,
                                      visible=False)
        self.more_button.add_css_class('flat')
        self.more_button.add_css_class('caption')
        connect_weak(self.more_button, 'clicked', self._on_read_more)
        self.append(self.more_button)

        self.poll_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=2)
        self.append(self.poll_box)

        self.media_box = Gtk.Box(spacing=6)
        self.append(self.media_box)

    def _preview(self, segments):
        """Leading segments within the character budget, or None when
        the content fits. Links are kept whole so a clipped match never
        breaks a URL or handle."""
        plain_chars = sum(len(s['text']) for s in segments
                          if s['text'] != '\n')
        if plain_chars <= self.READ_MORE_CHARS:
            return None
        preview, used = [], 0
        for segment in segments:
            text = segment['text']
            if text == '\n':
                if preview:
                    preview.append({'text': text, 'href': None})
                continue
            remaining = self.READ_MORE_CHARS - used
            if remaining <= 0:
                break
            if segment['href'] is None and len(text) > remaining:
                preview.append({'text': text[:remaining], 'href': None})
                break
            preview.append(segment)
            used += len(text)
        return preview

    def _on_read_more(self, _button):
        self._show(self._segments)
        self.more_button.set_visible(False)

    def _on_activate_link(self, _label, uri):
        linkrouting.open_link(self, uri, tag=self._tag_by_href.get(uri))
        return True

    def _show(self, segments):
        """Render segments as Pango markup: links stay focusable and
        activatable, custom emoji shortcodes are dropped."""
        self._tag_by_href = {}
        parts = []
        for segment in segments:
            text = segment['text']
            if text == '\n':
                if parts and not parts[-1].endswith('\n'):
                    parts.append('\n\n')
                continue
            if segment.get('hashtag') and segment.get('href'):
                name = text.strip().lstrip('#＃').split(None, 1)[0].strip()
                if name:
                    self._tag_by_href[segment['href']] = name
            text = EMOJI_RE.sub(
                lambda m: '' if m.group(1) in self._bank else m.group(0),
                shorten_url_text(text) if segment['href'] else text)
            text = GLib.markup_escape_text(text)
            if segment['href']:
                text = '<a href="%s">%s</a>' % (
                    GLib.markup_escape_text(segment['href']), text)
            if parts and not parts[-1].endswith(('\n', ' ')):
                parts.append(' ')
            parts.append(text)
        self.content.set_markup(''.join(parts).strip())

    def _set_poll(self, poll):
        while (child := self.poll_box.get_first_child()) is not None:
            self.poll_box.remove(child)
        self.poll_box.set_visible(bool(poll))
        if not poll:
            return
        votes = poll.get('votes_count') or 0
        for option in poll.get('options') or []:
            share = (option.get('votes_count') or 0) * 100 // votes \
                if votes else 0
            self.poll_box.append(Gtk.Label(
                label='%s (%d%%)' % (option.get('title'), share),
                xalign=0.0, wrap=True))
        total = Gtk.Label(
            label=ngettext('%d vote', '%d votes', votes) % votes,
            xalign=0.0)
        total.add_css_class('caption')
        total.add_css_class('dimmed')
        self.poll_box.append(total)

    def _ensure_filter_bar(self):
        """The bar a filtered post collapses behind, as StatusRow's."""
        if self.filter_bar is None:
            bar = Gtk.Box(spacing=8)
            bar.add_css_class('filter-bar')
            self.filter_label = Gtk.Label(xalign=0.0, hexpand=True,
                                          wrap=True)
            self.filter_label.add_css_class('dimmed')
            bar.append(self.filter_label)
            button = Gtk.Button(label=_('Show _Anyway'), use_underline=True,
                                valign=Gtk.Align.CENTER)
            button.add_css_class('flat')
            connect_weak(button, 'clicked', self._on_show_filtered)
            bar.append(button)
            self.prepend(bar)
            self.filter_bar = bar
        return self.filter_bar

    def _on_show_filtered(self, _button):
        if self._item is None:
            return
        self._item.ui['filter_shown'] = True
        self.bind(self._item)

    def _set_media(self, item, blur=False):
        while (child := self.media_box.get_first_child()) is not None:
            self.media_box.remove(child)
        attachments = item.media[:self.MAX_THUMBNAILS]
        self.set_media_source(item, bool(blur or item.sensitive))
        self.media_box.set_visible(bool(attachments))
        if attachments and item.sensitive:
            label = Gtk.Label(
                label=ngettext('%d sensitive attachment',
                               '%d sensitive attachments',
                               len(attachments)) % len(attachments),
                xalign=0.0)
            label.add_css_class('caption')
            label.add_css_class('dimmed')
            self.media_box.append(label)
            return
        self.fill_thumbnails(
            self.media_box, attachments,
            BlurredPicture if blur else Gtk.Picture, self.THUMBNAIL_HEIGHT)

    def bind(self, item):
        self._item = item
        rule = status_filter(item.raw, filterrules.NOTIFICATIONS,
                             datetime.datetime.now(datetime.timezone.utc))
        shown = item.ui.get('filter_shown')
        blur = rule is not None and rule[0] == 'blur' and not shown
        collapsed = rule is not None and rule[0] != 'blur' and not shown
        if collapsed or blur:
            self._ensure_filter_bar().set_visible(True)
            self.filter_label.set_text(
                _('Filtered: %s') % rule[1] if rule[1] else _('Filtered'))
        elif self.filter_bar is not None:
            self.filter_bar.set_visible(False)
        parts = (self.byline, self.content, self.more_button,
                 self.poll_box, self.media_box)
        for part in parts:
            part.set_visible(not collapsed)
        if collapsed:
            self.clear()
            self._item = item
            return
        self.name_label.set_text(item.display_name or '?')
        self.handle_label.set_text(item.handle)
        self._bank = status_emoji_bank(item.status)
        self._segments = parse_content(item.content)
        preview = self._preview(self._segments)
        self._show(preview or self._segments)
        self.more_button.set_visible(preview is not None)
        self._set_poll(item.status.get('poll'))
        self._set_media(item, blur)

    def clear(self):
        """Drop what the last bind showed, for a recycled row that now
        holds no post: hidden, its thumbnails would keep their textures
        alive for as long as the row lives."""
        self._item = None
        self._bank = {}
        self._segments = []
        self._tag_by_href = {}
        self.content.set_text('')
        self._set_poll(None)
        while (child := self.media_box.get_first_child()) is not None:
            self.media_box.remove(child)
