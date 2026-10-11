# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared custom-emoji text machinery.

Home of the InlineEmojiText renderer used for rendered status/profile
text: every segment flows through ONE wrapped Gtk.TextView where
::shortcode: occurrences become Gtk.Image child anchors loaded through
EmojiTextures, links stay clickable and the raw string survives as the
accessible label.  Originally written for the profile sheet; timeline
post bodies now reuse the same pipeline.

Shortcodes resolve in two layers: whatever the entity's own payload
declares, and the signed-in instance's whole custom-emoji set published
here by :mod:`emojichooser`.
"""

import collections
import re
from .i18n import gettext as _

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gdk, GLib, Gtk, Pango, Soup  # noqa: E402

from .pages import connect_weak  # noqa: E402

EMOJI_RE = re.compile(r':([A-Za-z0-9_+\-]+):')

# An instance can publish thousands of emoji; only a screenful is ever
# visible at once, so the decoded cache stays deliberately small.
EMOJI_TEXTURE_CACHE_SIZE = 128

# An instance publishes its whole custom-emoji set through
# ``GET /api/v1/custom_emojis``; emojichooser publishes it here once per
# session and every payload bank is layered on top of it (the Tuba
# ``InstanceAccount.instance_emojis`` layer).  Looked up through
# :func:`lookup_emoji` rather than merged into a fresh dict, because an
# instance can define thousands of emoji and a timeline rebinds a bank
# per row.
_instance_bank = {}


def set_instance_emoji_bank(bank):
    """Publish shortcode -> image URL for the signed-in instance."""
    global _instance_bank
    _instance_bank = bank if bank is not None else {}


def instance_emoji_bank():
    """The instance-wide bank (empty until it has been fetched)."""
    return _instance_bank


def lookup_emoji(emojis, shortcode):
    """Image URL for *shortcode*: the payload bank first, so a remote
    post keeps the emoji its own instance drew, then the instance-wide
    bank.  ``None`` means no layer knows the code and it stays literal
    text."""
    return emojis.get(shortcode) or _instance_bank.get(shortcode)


def launch_uri(widget, uri):
    """Open *uri* in the default handler, with a toast when that fails.

    Every link that comes from a server ends here, so only http(s) is let
    through (see :func:`statustext.is_web_link`)."""
    from .statustext import is_web_link
    if not is_web_link(uri):
        root = widget.get_root()
        if hasattr(root, 'show_toast'):
            root.show_toast(_('Could not open link'))
        return
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
    from .statustext import parse_content
    return parse_content(raw_html or '')


def emoji_bank_from(emojis):
    """shortcode -> image URL from one Mastodon ``emojis`` array."""
    bank = {}
    for emoji in emojis or []:
        code = emoji.get('shortcode')
        url = emoji.get('static_url') or emoji.get('url')
        if code and url:
            bank[code] = url
    return bank


def emoji_bank(account):
    """shortcode -> static image URL from a Mastodon `emojis` payload."""
    return emoji_bank_from((account or {}).get('emojis'))


def poll_emoji_bank(poll):
    """shortcode -> image URL declared by a poll's option titles."""
    return emoji_bank_from((poll or {}).get('emojis'))


def status_emoji_bank(status):
    """shortcode -> image URL for one status payload.

    Merges the account bank with the status's own `emojis` payload:
    the shortcodes as declared (including combined/dash-prefixed ones
    copied verbatim from the payload) are the only keys honoured — the
    renderer never guesses, so a shortcode neither the payload nor the
    instance declares stays literal text.  See :func:`lookup_emoji` for
    how the instance-wide bank joins in."""
    bank = emoji_bank(status.get('account') or {})
    bank.update(emoji_bank_from(status.get('emojis')))
    return bank


class EmojiTextures:
    """Async custom-emoji texture cache keyed by image URL."""

    # Bounded LRU: a server instance can define thousands of emoji and a
    # long reading session would keep every one of them decoded.
    _textures = collections.OrderedDict()
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
        cls._textures.move_to_end(url)
        while len(cls._textures) > EMOJI_TEXTURE_CACHE_SIZE:
            cls._textures.popitem(last=False)
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
        self._links = []
        self._link_tags = {}
        self._style_ids = []
        # Pango layout of the current text for do_measure's estimate.
        self._estimate_layout = None
        self.set_editable(False)
        # Nothing is ever typed here. A platform input-method module (IBus)
        # keeps a strong reference to its client text view, which is only
        # dropped in the view's dispose: the view could then never be
        # freed. The built-in simple context holds no such reference.
        self.set_property('im-module', 'gtk-im-context-simple')
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
        connect_weak(click, 'released', self._on_link_clicked)
        self.add_controller(click)

        # Links get the hand pointer; everything else keeps the text
        # view's own cursor.
        self._text_cursor = self.get_cursor()
        self._over_link = False
        motion = Gtk.EventControllerMotion.new()
        connect_weak(motion, 'motion', self._on_motion)
        connect_weak(motion, 'leave', self._on_leave)
        self.add_controller(motion)

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        connect_weak(keys, 'key-pressed', self._on_key_pressed)
        self.add_controller(keys)

        focus = Gtk.EventControllerFocus.new()
        connect_weak(focus, 'enter', self._on_focus_enter)
        self.add_controller(focus)

        # Weak even on self: a Gtk.TextView holds internal references to
        # itself, so PyGObject cannot collect a wrapper whose own
        # handlers point back at it.
        connect_weak(self, 'map', self._on_map)
        connect_weak(self, 'unmap', self._on_unmap)

    def set_emoji_size(self, size):
        """Pixel size of inline custom emojis for the next
        :meth:`set_segments`."""
        self._emoji_size = size

    def do_get_request_mode(self):
        # Gtk.TextView asks for a constant size, so containers never tell
        # it the width it is measured for; wrapped text needs it.
        if self.get_wrap_mode() == Gtk.WrapMode.NONE:
            return Gtk.SizeRequestMode.CONSTANT_SIZE
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL \
                and self.get_wrap_mode() == Gtk.WrapMode.NONE:
            # Gtk.TextView reports 0 as the natural width of unwrapped
            # text, so a header line (boost byline, display name) only
            # showed when it was given spare width to expand into. Ask
            # for the text's own width, inline emoji images included;
            # the minimum stays 0 so a long name can still be squeezed.
            layout = self._estimate()
            layout.set_width(-1)
            buf = self._buffer
            images = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(),
                                   True).count('\ufffc')
            natural = layout.get_pixel_size()[0] \
                + images * self._emoji_size + self.get_left_margin() \
                + self.get_right_margin()
            return 0, natural, -1, -1
        if orientation != Gtk.Orientation.VERTICAL \
                or self.get_width() > 0 \
                or self.get_wrap_mode() == Gtk.WrapMode.NONE:
            minimum, natural, _b, _b = Gtk.TextView.do_measure(
                self, orientation, for_size)
            # Chained through PyGObject the baselines come back as 0;
            # Gtk.TextView itself reports none.
            return minimum, natural, -1, -1
        # Gtk.TextView ignores for_size and answers with its text wrapped
        # to its last allocated width: 1px before the first allocation,
        # i.e. one character per line. A fresh list row would then
        # measure tens of thousands of pixels tall, which blows up the
        # scrollable height and makes the list realize far more rows
        # than it shows. Until the view has a width, answer with the
        # height of its text wrapped to the width being asked about
        # (unwrapped when no width is given).
        layout = self._estimate()
        if for_size > 0:
            layout.set_width(max(1, for_size - self.get_left_margin()
                                 - self.get_right_margin()) * Pango.SCALE)
        else:
            layout.set_width(-1)
        height = layout.get_pixel_size()[1] + self.get_top_margin() \
            + self.get_bottom_margin()
        return height, height, -1, -1

    def _estimate(self):
        """Pango layout of the plain text, for measuring before (or
        instead of) Gtk.TextView's own layout."""
        layout = self._estimate_layout
        if layout is None:
            buf = self._buffer
            layout = self.create_pango_layout(
                buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            self._estimate_layout = layout
        return layout

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
        self._estimate_layout = None
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
            image.add_css_class('custom-emoji')
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
                url = lookup_emoji(emojis, match.group(1))
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

    def _set_over_link(self, over):
        if over != self._over_link:
            self._over_link = over
            if over:
                self.set_cursor_from_name('pointer')
            else:
                self.set_cursor(self._text_cursor)

    def _on_motion(self, _controller, x, y):
        self._set_over_link(bool(self._links)
                            and self._href_at(x, y) is not None)

    def _on_leave(self, _controller):
        self._set_over_link(False)

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
