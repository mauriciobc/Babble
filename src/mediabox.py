# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, Gdk, Gtk, Pango)

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .statustext import (parse_content,  # noqa: E402
                         shorten_url_text, split_url, url_host)
from .textemoji import launch_uri  # noqa: E402

"""Link previews, the boost carousel and the media grid.

The poll widget lives in :mod:`pollbox`; this module owns the attachment
surface a status row draws.
"""
from .timelinebase import decodable_attachment_url, _flag, COLUMN_WIDTH
from .mediacache import _TextureCache, AvatarCache
from .timelinedata import StatusItem


CARD_THUMB_W = 200
CARD_THUMB_H = 96
BOOST_CARD_WIDTH = 272
BOOST_CARD_SPACING = 8
BOOST_SCROLL_MS = 250


def snap_carousel_offset(value, step, maximum):
    """Scroll offset snapped to the nearest card column."""
    if step <= 0:
        return max(0.0, min(value, maximum))
    snapped = round(value / step) * step
    return max(0.0, min(snapped, maximum))


def status_picture(width, height, *, alternative_text=None,
                   presentation=False, fit=None, kind=Gtk.Picture,
                   hexpand=False):
    """A preview tile: cover unless *fit* says otherwise, shrinkable,
    ``status-media darkenable``. *kind* is ``BlurredPicture`` when a
    server filter should hide the pixels."""
    kwargs = {}
    if presentation:
        kwargs['accessible_role'] = Gtk.AccessibleRole.PRESENTATION
    if alternative_text is not None:
        kwargs['alternative_text'] = alternative_text
    picture = kind(**kwargs)
    picture.set_content_fit(Gtk.ContentFit.COVER if fit is None else fit)
    picture.set_can_shrink(True)
    picture.set_size_request(width, height)
    if hexpand:
        picture.set_hexpand(True)
    picture.add_css_class('status-media')
    picture.add_css_class('darkenable')
    return picture


def _rewatch(watch, item, callback):
    """One ``changed`` handler on *item*.

    Drops the previous item's handler when a recycled row binds a
    different item. Returns the ``(item, handler)`` pair to store.
    """
    if watch is not None and watch[0] is not item:
        watched, handler = watch
        watched.disconnect(handler)
        watch = None
    if watch is None:
        watch = (item, connect_weak(item, 'changed', callback))
    return watch


class CardPreview(Gtk.Button):
    """Link/quote preview card built from status.card, Phanpy-style:
    portrait-cropped thumbnail on the LEFT (~40% of the card), text block
    (title / description / muted domain) on the right."""

    def __init__(self, card):
        super().__init__(halign=Gtk.Align.FILL)
        self.add_css_class('card')
        self.add_css_class('status-card')
        self._href = card.get('url') or card.get('uri') or ''
        title = Gtk.Label(label=card.get('title') or '', xalign=0.0,
                          wrap=True, lines=2,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        description = Gtk.Label(label=card.get('description') or '',
                                xalign=0.0, wrap=True, lines=2,
                                ellipsize=Pango.EllipsizeMode.END)
        description.add_css_class('caption')
        description.add_css_class('dimmed')
        parts = split_url(self._href)
        info = Gtk.Label(label=url_host(parts) if parts else '', xalign=0.0)
        info.add_css_class('caption')
        info.add_css_class('dimmed')
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       hexpand=True, valign=Gtk.Align.CENTER)
        text.append(title)
        text.append(description)
        text.append(info)
        content = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER,
                          margin_top=8, margin_bottom=8, margin_start=8,
                          margin_end=8)
        if card.get('image'):
            # Square at minimum, CARD_THUMB_W wide when there is room.
            image = status_picture(CARD_THUMB_H, CARD_THUMB_H,
                                   presentation=True)
            _TextureCache.load(card['image'], image,
                               (CARD_THUMB_W, CARD_THUMB_W))
            thumb = Adw.Clamp(maximum_size=CARD_THUMB_W,
                              tightening_threshold=CARD_THUMB_W,
                              child=image)
            thumb.set_valign(Gtk.Align.CENTER)
            content.append(thumb)
        content.append(text)
        self.set_child(content)
        if self._href:
            self.connect('clicked', lambda _b: launch_uri(self, self._href))
        else:
            self.set_sensitive(False)


class _BoostCard(Gtk.Button):
    """One numbered boost in the carousel; opens the boosted post."""

    def __init__(self, number, status, client):
        super().__init__(width_request=BOOST_CARD_WIDTH)
        self.add_css_class('card')
        self.add_css_class('boost-card')
        self.status = status
        self.client = client
        account = status['account']
        author = account.get('display_name') or account.get('acct', '')
        media = [a for a in status.get('media_attachments', [])
                 if a.get('type') in ('image', 'gifv')]
        if media:
            self.add_css_class('has-media')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=8,
                      margin_end=8)
        self.set_child(box)

        header = Gtk.Box(spacing=6)
        self._badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self._badge.add_css_class('boost-num')
        self._badge.add_css_class('caption-heading')
        self._badge.add_css_class('numeric')
        header.append(self._badge)
        avatar = Adw.Avatar(size=24, show_initials=True, text=author or '?')
        AvatarCache.load(avatar, account)
        header.append(avatar)
        name = Gtk.Label(label=author, xalign=0.0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('caption-heading')
        header.append(name)
        box.append(header)

        texts = []
        for segment in parse_content(status.get('content', '')):
            if segment['text'] == '\n':
                continue
            texts.append(shorten_url_text(segment['text'])
                         if segment['href'] else segment['text'])
            if len(texts) >= 3:
                break
        summary = ' '.join(texts) or '…'
        label = Gtk.Label(label=summary, xalign=0.0, wrap=True, lines=3,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          ellipsize=Pango.EllipsizeMode.END)
        box.append(label)
        self._author = author
        self._summary = summary
        self.set_number(number)

        if media:
            picture = status_picture(-1, 96, presentation=True)
            url = decodable_attachment_url(media[0])
            if url:
                _TextureCache.load(url, picture, (BOOST_CARD_WIDTH, 96))
            box.append(picture)
        self.connect('clicked', self._on_clicked)

    def set_number(self, number):
        self._badge.set_label(str(number))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ['%d. %s: %s' % (number, self._author,
                                              self._summary)])

    def _on_clicked(self, _button):
        view = getattr(self.get_root(), 'navigation_view', None)
        if view is not None:
            from .thread import push_thread
            push_thread(view, self.client, StatusItem(self.status))


class BoostCarousel(Gtk.Box):
    """Phanpy-style boosts section: a horizontally scrolling strip of
    numbered boost cards with previous and next buttons."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('boosts-carousel')

        header = Gtk.Box(spacing=6)
        self.title = Gtk.Label(xalign=0.0, hexpand=True)
        self.title.add_css_class('heading')
        header.append(self.title)
        self.prev = self._arrow('go-previous-symbolic',
                                _('Show Previous Boosts'), -1)
        self.next = self._arrow('go-next-symbolic',
                                _('Show Next Boosts'), 1)
        header.append(self.prev)
        header.append(self.next)
        self.append(header)

        self.cards_box = Gtk.Box(spacing=BOOST_CARD_SPACING)
        self.scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            kinetic_scrolling=False)
        self.scroll.set_child(self.cards_box)
        adjustment = self.scroll.get_hadjustment()
        connect_weak(adjustment, 'changed', self._update_arrows)
        connect_weak(adjustment, 'value-changed', self._update_arrows)
        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.HORIZONTAL)
        connect_weak(controller, 'scroll-end', self._on_scroll_end)
        self.scroll.add_controller(controller)
        self._scroll_animation = None
        self._scroll_animating = False
        self._scroll_target = None
        self.append(self.scroll)

    def _arrow(self, icon_name, tooltip, direction):
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        button.add_css_class('flat')
        button.add_css_class('circular')
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        connect_weak(button, 'clicked', self._page_by, direction)
        return button

    def set_boosts(self, statuses, client=None):
        # Cards are kept for posts still in the strip and only renumbered:
        # a new boost shifts every number but changes no other card.
        current = {}
        child = self.cards_box.get_first_child()
        while child is not None:
            current[id(child.status)] = child
            child = child.get_next_sibling()
        cards = []
        seen = set()
        for status in statuses:
            key = status.get('id')
            if key in seen:
                continue
            seen.add(key)
            card = current.pop(id(status), None)
            if card is None or card.client is not client:
                card = _BoostCard(len(seen), status, client)
            else:
                card.set_number(len(seen))
            cards.append(card)
        for stale in current.values():
            self.cards_box.remove(stale)
        previous = None
        for card in cards:
            if card.get_parent() is None:
                self.cards_box.insert_child_after(card, previous)
            else:
                self.cards_box.reorder_child_after(card, previous)
            previous = card
        self.set_visible(bool(seen))
        self.title.set_text(
            ngettext('%d Boost', '%d Boosts', len(seen)) % len(seen))
        self._update_arrows()

    def _card_step(self):
        card = self.cards_box.get_first_child()
        if card is not None and card.get_width() > 0:
            return card.get_width() + self.cards_box.get_spacing()
        return BOOST_CARD_WIDTH + self.cards_box.get_spacing()

    def _scroll_maximum(self, adjustment):
        return max(0.0, adjustment.get_upper() - adjustment.get_page_size())

    def _scroll_to_value(self, target, animate=True):
        adjustment = self.scroll.get_hadjustment()
        maximum = self._scroll_maximum(adjustment)
        target = snap_carousel_offset(target, self._card_step(), maximum)
        # A running animation is dropped where it stands, so a click made
        # during one carries on from the current position instead of
        # jumping to the end of the old move.
        if self._scroll_animation is not None:
            self._scroll_animation.pause()
            self._scroll_animation = None
        self._scroll_animating = False
        self._scroll_target = None
        current = adjustment.get_value()
        if not animate or abs(target - current) < 0.5:
            adjustment.set_value(target)
            self._update_arrows()
            return
        self._scroll_animating = True
        self._scroll_target = target
        animation = Adw.TimedAnimation(
            widget=self.scroll, value_from=current, value_to=target,
            duration=BOOST_SCROLL_MS, easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.CallbackAnimationTarget.new(
                pages.weak_method(self._apply_scroll_value)))
        connect_weak(animation, 'done', self._on_scroll_animation_done)
        self._scroll_animation = animation
        animation.play()

    def _apply_scroll_value(self, value):
        self.scroll.get_hadjustment().set_value(value)

    def _on_scroll_animation_done(self, _animation):
        self._scroll_animation = None
        self._scroll_animating = False
        self._scroll_target = None
        self._update_arrows()

    def _snap_to_nearest(self, animate=True):
        adjustment = self.scroll.get_hadjustment()
        self._scroll_to_value(adjustment.get_value(), animate=animate)

    def _on_scroll_end(self, _controller):
        if self._scroll_animating:
            return
        self._snap_to_nearest(animate=True)

    def _page_by(self, _button, direction):
        adjustment = self.scroll.get_hadjustment()
        step = self._card_step()
        if step <= 0:
            return
        # From where a running animation is heading, so quick clicks add up.
        base = self._scroll_target if self._scroll_target is not None \
            else adjustment.get_value()
        self._scroll_to_value((round(base / step) + direction) * step,
                              animate=True)

    def _update_arrows(self, *_args):
        adjustment = self.scroll.get_hadjustment()
        self.prev.set_sensitive(adjustment.get_value() > 0.5)
        self.next.set_sensitive(
            adjustment.get_value()
            < adjustment.get_upper() - adjustment.get_page_size() - 0.5)




def _media_badge(text, halign):
    badge = Gtk.Label(label=text, halign=halign, valign=Gtk.Align.END,
                      margin_bottom=6, margin_start=6, margin_end=6,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    badge.add_css_class('osd')
    badge.add_css_class('caption-heading')
    badge.add_css_class('media-badge')
    return badge


MEDIA_KINDS = {'image': _('Image'), 'video': _('Video'),
               'gifv': _('Animated Image'), 'audio': _('Audio')}


class BlurredPicture(Gtk.Picture):
    """A Gtk.Picture blurred at snapshot time: server filters set to
    'blur' reveal the shape of the media without its content, while
    the filter badge above it names what matched."""

    def __init__(self, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG,
                         **kwargs)

    def do_snapshot(self, snapshot):
        snapshot.push_blur(10.0)
        Gtk.Picture.do_snapshot(self, snapshot)
        snapshot.pop()

FOOTNOTE_LINES = 2

class MediaBox(Gtk.Box):
    """Adaptive attachment grid: 1 full / 2 side-by-side / 3-4 in a 2x2
    grid, with ALT and GIF badges. Clicking a tile opens the media
    viewer at that attachment. Sensitive media stays covered until the
    reader reveals it (or ``show-sensitive-media`` is on); ``ui`` keeps
    that choice across row recycling. Audio and unknown attachments are
    listed as rows."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('status-media-box')
        self._attachments = []

    def set_media(self, attachments, sensitive, ui=None, blur=False):
        pages.clear_children(self)
        ui = ui if ui is not None else {}
        self._attachments = list(attachments or [])
        if not self._attachments:
            self.set_visible(False)
            return
        self.set_visible(True)
        visual = [(i, a) for i, a in enumerate(self._attachments)
                  if a.get('type') in ('image', 'video', 'gifv')][:4]
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if sensitive:
            covered = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                child=content)
            self.append(covered)
        else:
            self.append(content)
        if visual:
            content.append(self._build_grid(visual, blur))
        for index, attachment in enumerate(self._attachments):
            if attachment.get('type') not in ('image', 'video', 'gifv'):
                content.append(self._file_row(index, attachment))
        if sensitive and not _flag('show-sensitive-media') \
                and not ui.get('media_revealed'):
            pages.reveal(covered, False)
            reveal = Gtk.Button(label=_('Show Sensitive Content'),
                                halign=Gtk.Align.START)
            connect_weak(reveal, 'clicked', self._toggle_sensitive,
                         covered, ui)
            self.append(reveal)
        elif sensitive:
            pages.reveal(covered, True)
            hide = Gtk.Button(label=_('Hide Sensitive Content'),
                              halign=Gtk.Align.START)
            hide.add_css_class('flat')
            connect_weak(hide, 'clicked', self._toggle_sensitive,
                         covered, ui)
            self.append(hide)

    def _build_grid(self, visual, blur=False):
        count = len(visual)
        if count == 1:
            columns, tile_height = 1, 260
        elif count == 2:
            columns, tile_height = 2, 160
        else:
            columns, tile_height = 2, 130
        fit = Gtk.ContentFit.CONTAIN if _flag('letterbox-media') \
            else Gtk.ContentFit.COVER
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        box.append(grid)
        footnotes = []
        for slot, (index, attachment) in enumerate(visual):
            kind = attachment.get('type')
            description = attachment.get('description')
            caption = description or MEDIA_KINDS.get(kind, _('Image'))
            picture = status_picture(
                -1, tile_height, alternative_text=caption, fit=fit,
                kind=BlurredPicture if blur else Gtk.Picture, hexpand=True)
            # Focusable like catchupgrid._Tile: a bare Overlay with only
            # GestureClick is unreachable from the keyboard.
            overlay = Gtk.Overlay(
                focusable=True,
                accessible_role=Gtk.AccessibleRole.BUTTON)
            overlay.add_css_class('media-tile')
            overlay.update_property(
                [Gtk.AccessibleProperty.LABEL], [caption])
            overlay.set_child(picture)
            if description:
                mark = '¹²³⁴'[slot]
                overlay.add_overlay(
                    _media_badge('ALT%s' % mark, Gtk.Align.START))
                footnotes.append('%s %s' % (mark, description))
            if kind == 'gifv':
                overlay.add_overlay(_media_badge('GIF', Gtk.Align.END))
            elif kind == 'video':
                play = Gtk.Image(
                    icon_name='media-playback-start-symbolic',
                    pixel_size=24, halign=Gtk.Align.CENTER,
                    valign=Gtk.Align.CENTER,
                    accessible_role=Gtk.AccessibleRole.PRESENTATION)
                play.add_css_class('osd')
                overlay.add_overlay(play)
            click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
            connect_weak(click, 'released', self._on_tile_clicked, index)
            overlay.add_controller(click)
            keys = Gtk.EventControllerKey()
            connect_weak(keys, 'key-pressed', self._on_tile_key, index)
            overlay.add_controller(keys)
            if not blur:
                # Dragging a tile out drops the attachment; a blurred one
                # stays put until the reader chooses to see it.
                drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
                connect_weak(drag, 'prepare', self._on_tile_drag_prepare,
                             index)
                overlay.add_controller(drag)
            overlay.set_cursor_from_name('pointer')
            grid.attach(overlay, slot % columns, slot // columns, 1, 1)
            url = decodable_attachment_url(attachment)
            if url:
                _TextureCache.load(url, picture,
                                   (COLUMN_WIDTH // columns, tile_height))
        if footnotes:
            notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for note in footnotes:
                label = Gtk.Label(label=note, xalign=0.0, wrap=True,
                                  wrap_mode=Pango.WrapMode.WORD_CHAR,
                                  hexpand=True, lines=FOOTNOTE_LINES,
                                  ellipsize=Pango.EllipsizeMode.END)
                label.add_css_class('caption')
                label.add_css_class('dimmed')
                label.set_focusable(True)
                label.set_cursor_from_name('pointer')
                click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
                connect_weak(click, 'released', self._on_footnote_clicked,
                             label)
                label.add_controller(click)
                keys = Gtk.EventControllerKey()
                connect_weak(keys, 'key-pressed',
                             self._on_footnote_key, label)
                label.add_controller(keys)
                notes.append(label)
            box.append(notes)
        return box

    def _file_row(self, index, attachment):
        kind = attachment.get('type')
        icon = 'audio-x-generic-symbolic' if kind == 'audio' \
            else 'text-x-generic-symbolic'
        title = attachment.get('description') \
            or MEDIA_KINDS.get(kind, _('Attachment'))
        button = Gtk.Button()
        button.add_css_class('flat')
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Image(icon_name=icon,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        label = Gtk.Label(label=title, xalign=0.0, hexpand=True,
                          ellipsize=Pango.EllipsizeMode.END)
        line.append(label)
        button.set_child(line)
        connect_weak(button, 'clicked', self._on_file_clicked, index)
        return button

    def _on_file_clicked(self, _button, index):
        self._open(index)

    def _on_tile_clicked(self, gesture, _n_press, _x, _y, index):
        # Claim the click: otherwise the ListView's single-click
        # activation opens the thread of the containing row too.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._open(index)

    def _on_tile_key(self, _controller, keyval, _keycode, _state, index):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._open(index)
            return True
        return False

    def _on_footnote_key(self, _controller, keyval, _keycode, _state, label):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._toggle_footnote(label)
            return True
        return False

    def _on_tile_drag_prepare(self, source, _x, _y, index):
        if index >= len(self._attachments):
            return None
        attachment = self._attachments[index]
        url = attachment.get('url') or attachment.get('remote_url')
        # The tile shows its texture from _TextureCache once decoded.
        texture = source.get_widget().get_child().get_paintable()
        if not isinstance(texture, Gdk.Texture):
            texture = None
        from .mediaviewer import media_provider, set_drag_texture
        if texture is not None:
            set_drag_texture(source, texture)
        # A video's tile is its preview frame, not the attachment.
        return media_provider(
            url, texture if attachment.get('type') == 'image' else None)

    def _open(self, index):
        from .mediaviewer import present_media
        present_media(self, self._attachments, index)

    def _toggle_sensitive(self, button, covered, ui):
        revealed = not covered.get_reveal_child()
        ui['media_revealed'] = revealed
        pages.reveal(covered, revealed, animate=True)
        if revealed:
            button.set_label(_('Hide Sensitive Content'))
            button.add_css_class('flat')
        else:
            button.set_label(_('Show Sensitive Content'))
            button.remove_css_class('flat')

    def _on_footnote_clicked(self, gesture, _n_press, _x, _y, label):
        """A description cut at two lines opens on click and closes on
        the next one."""
        # Claim the click: otherwise the ListView's single-click
        # activation opens the thread of the containing row too.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._toggle_footnote(label)

    def _toggle_footnote(self, label):
        if label.get_lines() == FOOTNOTE_LINES:
            label.set_lines(-1)
            label.set_ellipsize(Pango.EllipsizeMode.NONE)
        else:
            label.set_lines(FOOTNOTE_LINES)
            label.set_ellipsize(Pango.EllipsizeMode.END)


# Settings, locale and status helpers
