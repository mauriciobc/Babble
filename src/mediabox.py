# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, Gdk, GLib, Gtk, Pango)

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .statustext import (parse_content,  # noqa: E402
                         shorten_url_text, split_url, url_host)
from .textemoji import (InlineEmojiText, launch_uri,  # noqa: E402
                        poll_emoji_bank)

"""Polls, link previews, the boost carousel and the media grid."""
from .timelinebase import time_left, decodable_attachment_url, _flag, COLUMN_WIDTH
from .mediacache import _TextureCache, AvatarCache
from .timelinedata import StatusItem


class PollBox(Gtk.Box):
    """Renders status.poll. An open poll the reader has not answered is
    a vote form (radio buttons, or check buttons for a multiple-choice
    poll, plus Vote); an expired or answered one shows the result bars
    with the reader's own choices marked. ``set_poll`` takes the client
    and the post's view state ``ui`` so a vote or a refresh can update
    the post: ``on_update(new_poll)`` stores the server's answer."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('status-poll')
        self._poll = None
        self._bank = {}
        self._client = None
        self._ui = {}
        self._on_update = None
        self._choices = []
        self._animate_bars = False
        self._bar_animations = []

    def set_poll(self, poll, bank=None, client=None, ui=None,
                 on_update=None):
        pages.clear_children(self)
        self._bar_animations = []
        self._poll = poll
        if not poll:
            self._animate_bars = False
            self.set_visible(False)
            return
        self.set_visible(True)
        # A poll declares its own emoji for the option titles; they win
        # over the post's bank, which only covers the body.
        self._bank = dict(bank or {})
        self._bank.update(poll_emoji_bank(poll))
        self._client = client
        self._ui = ui if ui is not None else {}
        self._on_update = on_update
        self._choices = []
        closed = bool(poll.get('expired') or poll.get('closed'))
        can_vote = (client is not None and bool(getattr(client, 'token', None))
                    and not closed and not poll.get('voted')
                    and not self._ui.get('poll_results'))
        if can_vote:
            self._build_form(poll)
        else:
            self._build_results(poll, closed)
        self._build_footer(poll, closed, can_vote)
        for animation in self._bar_animations:
            animation.play()
        self._animate_bars = False

    def _label(self, option):
        text = InlineEmojiText(emoji_size=15)
        text.set_segments([{'text': option.get('title') or '', 'href': None}],
                          self._bank)
        text.set_hexpand(True)
        return text

    def _build_form(self, poll):
        multiple = bool(poll.get('multiple'))
        first = None
        for index, option in enumerate(poll.get('options') or []):
            button = Gtk.CheckButton()
            if not multiple:
                if first is None:
                    first = button
                else:
                    button.set_group(first)
            button.set_child(self._label(option))
            connect_weak(button, 'toggled', self._on_choice_toggled)
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [option.get('title') or ''])
            button.babble_index = index
            self._choices.append(button)
            self.append(button)

    def _selected(self):
        return [b.babble_index for b in self._choices if b.get_active()]

    def _on_choice_toggled(self, _button):
        self.vote_button.set_sensitive(bool(self._selected()))

    def _build_results(self, poll, closed):
        options = poll.get('options') or []
        own = set(poll.get('own_votes') or [])
        total = poll.get('votes_count') or 0
        counts = [(o.get('votes_count') or 0) for o in options]
        winner = counts.index(max(counts)) \
            if closed and counts and max(counts) > 0 else -1
        for index, option in enumerate(options):
            count = option.get('votes_count') or 0
            share = (count / total) if total else 0.0
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line = Gtk.Box(spacing=8)
            if index in own:
                mark = Gtk.Image(icon_name='object-select-symbolic',
                                 accessible_role=Gtk.AccessibleRole.IMG)
                mark.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_('Your vote')])
                mark.set_tooltip_text(_('Your vote'))
                line.append(mark)
            label = self._label(option)
            if index == winner:
                label.add_css_class('heading')
            line.append(label)
            percent = Gtk.Label(label='%d%%' % round(share * 100))
            percent.add_css_class('numeric')
            percent.add_css_class('caption')
            line.append(percent)
            row.append(line)
            bar = Gtk.ProgressBar(
                fraction=0.0 if self._animate_bars else share)
            if self._animate_bars:
                self._bar_animations.append(Adw.TimedAnimation(
                    widget=bar, value_from=0.0, value_to=share,
                    duration=400, easing=Adw.Easing.EASE_OUT_CUBIC,
                    target=Adw.PropertyAnimationTarget.new(bar, 'fraction')))
            bar.add_css_class('poll-bar')
            if index in own:
                bar.add_css_class('accent')
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ['%s, %d%%' % (option.get('title') or '', round(share * 100))])
            row.append(bar)
            self.append(row)

    def _build_footer(self, poll, closed, can_vote):
        footer = Gtk.Box(spacing=8)
        if can_vote:
            self.vote_button = Gtk.Button(label=_('_Vote'),
                                          use_underline=True)
            self.vote_button.add_css_class('suggested-action')
            self.vote_button.add_css_class('pill')
            self.vote_button.set_sensitive(False)
            connect_weak(self.vote_button, 'clicked', self._on_vote)
            footer.append(self.vote_button)
            show = Gtk.Button(label=_('Show _Results'), use_underline=True)
            show.add_css_class('flat')
            connect_weak(show, 'clicked', self._on_show_results)
            footer.append(show)
        elif not closed and not poll.get('voted') and self._ui.get(
                'poll_results'):
            back = Gtk.Button(label=_('_Back to Voting'), use_underline=True)
            back.add_css_class('flat')
            connect_weak(back, 'clicked', self._on_show_results)
            footer.append(back)
        votes = poll.get('votes_count') or 0
        voters = poll.get('voters_count')
        count = voters if voters is not None else votes
        detail = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        detail.add_css_class('caption')
        detail.add_css_class('dimmed')
        if voters is not None:
            parts = [ngettext('%d voter', '%d voters', count) % count]
        else:
            parts = [ngettext('%d vote', '%d votes', votes) % votes]
        if closed:
            parts.append(_('Closed'))
        else:
            left = time_left(poll.get('expires_at'))
            parts.append(_('%s left') % left if left else _('Open'))
        detail.set_text(' · '.join(parts))
        footer.append(detail)
        if not closed:
            refresh = pages.icon_button('view-refresh-symbolic',
                                        _('Refresh Results'))
            connect_weak(refresh, 'clicked', self._on_refresh)
            footer.append(refresh)
        self.append(footer)

    def _on_show_results(self, _button):
        self._ui['poll_results'] = not self._ui.get('poll_results')
        self._animate_bars = True
        self.set_poll(self._poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _apply(self, poll):
        if self._on_update is not None:
            self._on_update(poll)
        self._animate_bars = True
        self.set_poll(poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _on_vote(self, _button):
        choices = self._selected()
        if not choices or self._poll is None:
            return
        self.vote_button.set_sensitive(False)
        pages.call(self, self._client, 'POST',
                   '/polls/%s/votes' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Vote failed'),
                   json_body={'choices': choices})
        # A failed vote leaves the form usable again.
        GLib.timeout_add_seconds(
            3, lambda: self.vote_button.set_sensitive(
                bool(self._selected())) or GLib.SOURCE_REMOVE)

    def _on_refresh(self, _button):
        if self._poll is None:
            return
        pages.call(self, self._client, 'GET', '/polls/%s' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Refresh failed'))


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
            overlay = Gtk.Overlay()
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
                label.set_cursor_from_name('pointer')
                click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
                connect_weak(click, 'released', self._on_footnote_clicked,
                             label)
                label.add_controller(click)
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
        if label.get_lines() == FOOTNOTE_LINES:
            label.set_lines(-1)
            label.set_ellipsize(Pango.EllipsizeMode.NONE)
        else:
            label.set_lines(FOOTNOTE_LINES)
            label.set_ellipsize(Pango.EllipsizeMode.END)


# Settings, locale and status helpers
