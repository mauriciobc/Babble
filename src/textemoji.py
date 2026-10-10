# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared custom-emoji text machinery.

Home of the InlineEmojiText renderer used for rendered status/profile
text: every segment flows through ONE wrapped Gtk.TextView where
:shortcode: occurrences become Gtk.Image child anchors loaded through
EmojiTextures, links stay clickable and the raw string survives as the
accessible label.  Originally written for the profile sheet; timeline
post bodies now reuse the same pipeline.
"""

import re
from gettext import gettext as _

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gdk, GLib, Gtk, Pango, Soup  # noqa: E402

EMOJI_RE = re.compile(r':([A-Za-z0-9_+\-]+):')


def launch_uri(widget, uri):
    """Open *uri* in the default handler, with a toast when that fails."""
    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None,
                                    _on_uri_launched, widget)


def _on_uri_launched(launcher, result, widget):
    try:
        launcher.launch_finish(result)
    except GLib.Error as error:
        root = widget.get_root()
        if not error.matches(Gtk.DialogError.quark(),
                             Gtk.DialogError.DISMISSED) \
                and hasattr(root, 'show_toast'):
            root.show_toast(_('Could not open link'))


def content_segments(raw_html):
    """parse_content is the shared converter; raw non-HTML payload now
    survives via the trailing-flush fix there."""
    from .timeline import parse_content
    return parse_content(raw_html or '')


def emoji_bank(account):
    """shortcode -> static image URL from a Mastodon `emojis` payload."""
    bank = {}
    for emoji in account.get('emojis') or []:
        code = emoji.get('shortcode')
        url = emoji.get('static_url') or emoji.get('url')
        if code and url:
            bank[code] = url
    return bank


def status_emoji_bank(status):
    """shortcode -> image URL for one status payload.

    Merges the account bank with the status's own `emojis` payload:
    the shortcodes as declared (including combined/dash-prefixed ones
    copied verbatim from the payload) are the only keys honoured — the
    renderer never guesses or downloads emoji for undeclared codes, so
    shortcodes missing from the payload stay literal text."""
    bank = emoji_bank(status.get('account') or {})
    for emoji in status.get('emojis') or []:
        code = emoji.get('shortcode')
        url = emoji.get('static_url') or emoji.get('url')
        if code and url:
            bank[code] = url
    return bank


class EmojiTextures:
    """Async custom-emoji texture cache keyed by image URL."""

    _textures = {}
    _inflight = {}

    @classmethod
    def bind(cls, image, url):
        """Load *url* into *image* through the shared decoded cache."""
        texture = cls._textures.get(url)
        if texture is not None:
            image.set_from_paintable(texture)
            return
        waiting = cls._inflight.setdefault(url, [])
        if image not in waiting:
            waiting.append(image)
        if len(waiting) > 1:
            # Another bind for the same URL already started the request.
            return
        message = Soup.Message.new('GET', url)
        if message is None:
            cls._inflight.pop(url, None)
            return
        from .timeline import AvatarCache
        AvatarCache.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT_IDLE, None, cls._on_loaded, url)

    @classmethod
    def _on_loaded(cls, session, result, url):
        waiting = cls._inflight.pop(url, [])
        try:
            data = session.send_and_read_finish(result)
        except GLib.Error:
            return
        if not data or data.get_size() == 0:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(data)
        except GLib.Error:
            return
        cls._textures[url] = texture
        for image in waiting:
            image.set_from_paintable(texture)


class InlineEmojiText(Gtk.TextView):
    """Rendered text/body copy with custom emoji images inline.

    All segments flow through ONE wrapped Gtk.TextView(:shortcode:
    occurrences are replaced by Gtk.Image child anchors), so links,
    spacing and wrapping behave like a label while images sit inside the
    line.  The raw string (shortcodes intact) is kept as the accessible
    label for screen readers.

    Links are underlined and accent-coloured.  A body that holds links
    is focusable: Tab and Shift+Tab step through its links, Return opens
    the selected one."""

    __gtype_name__ = 'BabbleInlineEmojiText'

    def __init__(self, emoji_size=16, shorten_links=False,
                 paragraph_gap=False, single_line=False, **kwargs):
        super().__init__(**kwargs)
        self._raw = ''
        self._emoji_size = emoji_size
        self._shorten_links = shorten_links
        self._paragraph_gap = paragraph_gap
        self._single_line = single_line
        self._links = []
        self._link_tags = {}
        self._style_ids = []
        self.set_editable(False)
        self.set_focusable(False)
        self.set_vexpand(False)
        if single_line:
            # Header lines (display name, boost byline): WORD_CHAR at zero
            # width stacks one glyph per row and blows up the card.
            self.set_wrap_mode(Gtk.WrapMode.NONE)
        else:
            self.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.set_left_margin(0)
        self.set_right_margin(0)
        self.set_top_margin(0)
        self.set_bottom_margin(0)
        self.set_pixels_below_lines(0)
        self._buffer = Gtk.TextBuffer()
        self.set_buffer(self._buffer)
        self.add_css_class('inline')
        self.add_css_class('inline-emoji-text')

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect('released', self._on_link_clicked)
        self.add_controller(click)

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._on_key_pressed)
        self.add_controller(keys)

        focus = Gtk.EventControllerFocus.new()
        focus.connect('enter', self._on_focus_enter)
        self.add_controller(focus)

        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)

    def set_emoji_size(self, size):
        """Pixel size of inline custom emojis for the next
        :meth:`set_segments`."""
        self._emoji_size = size

    def _context(self):
        """Shared link/URL helpers that still live in the timeline
        module (imported lazily to avoid an import cycle)."""
        from . import timeline
        return timeline

    def _on_map(self, _widget):
        manager = Adw.StyleManager.get_default()
        self._style_ids = [
            manager.connect('notify::dark', self._update_link_color),
            manager.connect('notify::accent-color',
                            self._update_link_color)]
        self._update_link_color()

    def _on_unmap(self, _widget):
        manager = Adw.StyleManager.get_default()
        for handler in self._style_ids:
            manager.disconnect(handler)
        self._style_ids = []

    def _update_link_color(self, *_args):
        manager = Adw.StyleManager.get_default()
        color = manager.get_accent_color().to_standalone_rgba(
            manager.get_dark())
        for tag in self._link_tags.values():
            tag.set_property('foreground-rgba', color)

    def set_segments(self, segments, emojis=None):
        """Render *segments*; emojis maps shortcode -> image URL."""
        emojis = emojis or {}
        buf = self._buffer
        # Drop the previous run's tags: create_tag adds them to the
        # buffer's tag table and nothing else removes them, so a recycled
        # timeline row would grow the table on every rebind.
        table = buf.get_tag_table()
        for tag in self._link_tags.values():
            table.remove(tag)
        buf.set_text('', -1)
        self._link_tags = {}
        self._links = []
        plain = []
        pending_breaks = 0
        previous_space_after = True
        helpers = self._context()

        def link_tag(href):
            tag = self._link_tags.get(href)
            if tag is None:
                tag = buf.create_tag(None,
                                     underline=Pango.Underline.SINGLE)
                tag.babble_href = href
                self._link_tags[href] = tag
            return tag

        def put(text, tag=None):
            start = buf.get_char_count()
            buf.insert(buf.get_end_iter(), text, -1)
            plain.append(text)
            if tag is not None:
                end = buf.get_char_count()
                buf.apply_tag(tag, buf.get_iter_at_offset(start),
                              buf.get_iter_at_offset(end))
                self._links.append((start, end, tag.babble_href))

        def emoji_anchor(url):
            image = Gtk.Image(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            image.set_pixel_size(self._emoji_size)
            EmojiTextures.bind(image, url)
            anchor = self._buffer.create_child_anchor(
                self._buffer.get_end_iter())
            self.add_child_at_anchor(image, anchor)
            image.set_visible(True)

        for segment in segments:
            text = segment['text']
            if text == '\n':
                if segment.get('br'):
                    pending_breaks += 1
                else:
                    pending_breaks = max(
                        pending_breaks, 2 if self._paragraph_gap else 1)
                continue
            if plain:
                if pending_breaks:
                    put('\n' * pending_breaks)
                elif plain[-1][-1:] not in ('', '\n', ' ') and (
                        segment.get('sp_before', True)
                        or previous_space_after):
                    put(' ')
            pending_breaks = 0
            previous_space_after = segment.get('sp_after', True)
            tag = link_tag(segment['href']) if segment['href'] else None
            if self._shorten_links and segment['href']:
                text = helpers.shorten_url_text(text)
            position = 0
            for match in EMOJI_RE.finditer(text):
                if match.start() > position:
                    put(text[position:match.start()], tag)
                url = emojis.get(match.group(1))
                if url:
                    emoji_anchor(url)
                    plain.append(match.group(0))
                else:
                    put(match.group(0), tag)
                position = match.end()
            if position < len(text):
                put(text[position:], tag)

        self._raw = ''.join(plain).strip()
        self.update_property([Gtk.AccessibleProperty.LABEL], [self._raw])
        self.set_focusable(bool(self._links))
        self.set_cursor_visible(bool(self._links))
        if self.get_mapped():
            self._update_link_color()

    def _href_at_offset(self, offset):
        for start, end, href in self._links:
            if start <= offset < end:
                return href
        return None

    def _href_at(self, x, y):
        wx, wy = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET,
                                              int(x), int(y))
        over_text, location = self.get_iter_at_location(wx, wy)
        if not over_text:
            return None
        return self._href_at_offset(location.get_offset())

    def _on_link_clicked(self, controller, n_press, x, y):
        href = self._href_at(x, y)
        if not href:
            return
        controller.set_state(Gtk.EventSequenceState.CLAIMED)
        self._open(href)

    def _open(self, href):
        """In-app routing (mentions, hashtags, fediverse links); set
        ``link_mentions``/``link_tags`` to the status payload's
        ``mentions``/``tags`` so they resolve exactly."""
        from .pages import open_link
        open_link(self, href, mentions=getattr(self, 'link_mentions', None),
                  tags=getattr(self, 'link_tags', None))

    def _caret_offset(self):
        return self._buffer.get_iter_at_mark(
            self._buffer.get_insert()).get_offset()

    def _select_link(self, index):
        start, end, _href = self._links[index]
        self._buffer.select_range(self._buffer.get_iter_at_offset(start),
                                  self._buffer.get_iter_at_offset(end))

    def _on_focus_enter(self, _controller):
        if self._links and self.has_visible_focus():
            self._select_link(0)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            href = self._href_at_offset(self._caret_offset())
            if href:
                self._open(href)
            return bool(href)
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        caret = self._caret_offset()
        if keyval == Gdk.KEY_Tab:
            targets = [i for i, link in enumerate(self._links)
                       if link[0] > caret]
            index = targets[0] if targets else None
        else:
            targets = [i for i, link in enumerate(self._links)
                       if link[0] < caret]
            index = targets[-1] if targets else None
        if index is None:
            return False
        self._select_link(index)
        return True
