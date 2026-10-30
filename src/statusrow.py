# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import (Adw, Gdk, GLib, Gtk, Pango)

from . import filterrules  # noqa: E402
from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .statustext import parse_content
from .textemoji import InlineEmojiText, emoji_bank, status_emoji_bank
from .sourceline import SourceLine
from .statusactions import (CollectionCard, QuoteCard, StatusActions,
                            _display_name_uses_custom_emoji, _open_thread)
from .statusmenu import StatusMenuButton
from . import tileactions
from .mediacache import AvatarCache, StatusBody
from .mediabox import BoostCarousel, CardPreview, MediaBox, _rewatch
from .pollbox import PollBox
from .timelinedata import BoostCarouselItem
from .timelinebase import (VISIBILITY_INFO, _collapse_view, _flag,
                           absolute_time, status_filter, ui_language)


class StatusRow(Gtk.Overlay):
    """Post card: boost strip, author header with indicators and the
    overflow menu, content warning, body, poll, quote, card, media and
    an action row; like the reference client the bar is off on patrols
    and always on detail views."""

    __gtype_name__ = 'BabbleStatusRow'

    ICON_REPLY = StatusActions.ICON_REPLY
    ICON_BOOST = StatusActions.ICON_BOOST
    ICON_QUOTE = StatusActions.ICON_QUOTE
    ICON_FAVOURITE = StatusActions.ICON_FAVOURITE
    ICON_BOOKMARK = StatusActions.ICON_BOOKMARK

    MENU_ACTIONS = StatusMenuButton.MENU_ACTIONS
    MENU_TOGGLES = StatusMenuButton.MENU_TOGGLES

    def __init__(self, detail=False, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.ARTICLE,
                         **kwargs)
        # A card is sized by its content; without this it stretches to
        # whatever box it lands in (thread view, notification insets).
        self.set_valign(Gtk.Align.START)
        self.set_vexpand(False)
        self.set_hexpand(True)
        self.add_css_class('status-row')
        self.item = None
        self.client = None
        self.detail = detail
        self.media_blur = False
        # Which view this row is standing in, so a filter scoped to
        # another one does not apply here. Set by whoever builds the row:
        # the page knows, the row does not.
        self.filter_context = filterrules.HOME
        self._emoji_bank = {}
        self._body_animation = None
        self._body_tick = None
        self._body_overflow = Gtk.Overflow.VISIBLE

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_hexpand(True)
        self.body = body
        self.set_child(body)

        # The source line, display-name emoji text, content-warning bar,
        # translation note and boosts carousel are built on first use:
        # most posts need none of them, and each costs a text view or a
        # scrolled window per row.
        self.source_line = None
        self._has_source = False

        # Filtered posts collapse into this bar until shown anyway.
        self.filter_bar = Gtk.Box(spacing=8)
        self.filter_bar.add_css_class('filter-bar')
        self.filter_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        self.filter_label.add_css_class('dimmed')
        self.filter_bar.append(self.filter_label)
        self.filter_button = Gtk.Button(label=_('Show _Anyway'),
                                        use_underline=True,
                                        valign=Gtk.Align.CENTER)
        self.filter_button.add_css_class('flat')
        connect_weak(self.filter_button, 'clicked', self._on_show_filtered)
        self.filter_bar.append(self.filter_button)
        self.filter_bar.set_visible(False)
        body.append(self.filter_bar)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=4, margin_bottom=4, margin_start=4,
                       margin_end=4)
        self.main = main
        body.append(main)

        header = Gtk.Box(spacing=6)
        self.header = header
        # Fills the header up to the time and menu, so its hover pill
        # reads as the header's left side, not a box hugging the name.
        profile_button = Gtk.Button(tooltip_text=_('View Profile'))
        profile_button.add_css_class('flat')
        profile_button.add_css_class('profile-opener')
        profile_button.set_hexpand(True)
        opener = Gtk.Box(spacing=10)
        opener.set_hexpand(True)
        self.avatar = Adw.Avatar(size=46, show_initials=True)
        opener.append(self.avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.CENTER)
        names.set_hexpand(True)
        name_line = Gtk.Box(spacing=6)
        name_line.set_hexpand(True)
        self._name_line = name_line
        self.name_plain = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.name_plain.add_css_class('heading')
        name_line.append(self.name_plain)
        self.name_label = None
        self.bot_badge = Gtk.Label(label=_('Bot'), valign=Gtk.Align.CENTER)
        self.bot_badge.set_hexpand(False)
        self.bot_badge.add_css_class('status-chip')
        self.bot_badge.add_css_class('caption')
        self.bot_badge.set_visible(False)
        name_line.append(self.bot_badge)
        self.handle_label = Gtk.Label(xalign=0.0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.handle_label.add_css_class('caption')
        self.handle_label.add_css_class('dimmed')
        names.append(name_line)
        names.append(self.handle_label)
        opener.append(names)
        profile_button.set_child(opener)
        connect_weak(profile_button, 'clicked', self._on_profile_clicked)
        self.profile_button = profile_button
        header.append(profile_button)
        meta = Gtk.Box(spacing=6, halign=Gtk.Align.END,
                       valign=Gtk.Align.CENTER)
        # Thread position badge: '2/5' on posts of a same-thread run.
        self.badge = Gtk.Label()
        self.badge.add_css_class('run-badge')
        self.badge.add_css_class('caption-heading')
        self.badge.add_css_class('numeric')
        self.badge.set_visible(False)
        meta.append(self.badge)
        self.pinned_badge = Gtk.Label(label=_('Pinned'))
        self.pinned_badge.add_css_class('status-chip')
        self.pinned_badge.add_css_class('caption')
        self.pinned_badge.set_visible(False)
        meta.append(self.pinned_badge)
        self.language_label = Gtk.Label()
        self.language_label.add_css_class('caption')
        self.language_label.add_css_class('dimmed')
        self.language_label.set_visible(False)
        meta.append(self.language_label)
        self.visibility_icon = Gtk.Image(pixel_size=14)
        self.visibility_icon.add_css_class('dimmed')
        self.visibility_icon.set_visible(False)
        meta.append(self.visibility_icon)
        self.edited_button = Gtk.Button(label=_('Edited'))
        self.edited_button.add_css_class('flat')
        self.edited_button.add_css_class('caption')
        self.edited_button.add_css_class('edited-marker')
        connect_weak(self.edited_button, 'clicked', self._on_edited_clicked)
        self.edited_button.set_visible(False)
        meta.append(self.edited_button)
        self.time_label = Gtk.Label()
        self.time_label.add_css_class('caption')
        self.time_label.add_css_class('dimmed')
        self.time_label.add_css_class('numeric')
        meta.append(self.time_label)
        # Dragging the time or badges out drops the post's link. One
        # controller per row; the content is only built when a drag starts.
        link_drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        connect_weak(link_drag, 'prepare', self._on_link_drag_prepare)
        meta.add_controller(link_drag)
        header.append(meta)
        # Overflow menu sits with the timestamp, the way the bar keeps it
        # in the post header rather than in the action row.
        host = self.weak_ref()
        self._overflow = tileactions.tile_menu_button(None, None)
        self._overflow.host = host
        header.append(self._overflow)
        main.append(header)

        self.reply_box = Gtk.Box(spacing=6)
        reply_icon = Gtk.Image(
            icon_name=self.ICON_REPLY, pixel_size=12,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        reply_icon.add_css_class('dimmed')
        self.reply_box.append(reply_icon)
        self.reply_label = Gtk.Label(xalign=0.0,
                                     ellipsize=Pango.EllipsizeMode.END)
        self.reply_label.add_css_class('caption')
        self.reply_label.add_css_class('dimmed')
        self.reply_box.append(self.reply_label)
        main.append(self.reply_box)

        self.cw_box = None
        self.cw_label = None
        self.cw_toggle = None

        guarded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.guarded = guarded
        self.guarded_revealer = None
        main.append(guarded)

        self.content = StatusBody()
        guarded.append(self.content)

        self.expand_button = Gtk.Button(label=_('Show More'),
                                        halign=Gtk.Align.START)
        self.expand_button.add_css_class('flat')
        self.expand_button.add_css_class('caption-heading')
        self.expand_button.set_visible(False)
        connect_weak(self.expand_button, 'clicked', self._on_expand)
        guarded.append(self.expand_button)

        self.post_meta = Gtk.Label(xalign=0.0, wrap=True)
        self.post_meta.add_css_class('caption')
        self.post_meta.add_css_class('dimmed')
        self.post_meta.set_visible(False)
        guarded.append(self.post_meta)

        self.translation_box = None
        self.translation_label = None

        self.poll_box = PollBox()
        guarded.append(self.poll_box)

        self.quote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        guarded.append(self.quote_box)

        self.collections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                       spacing=6)
        guarded.append(self.collections_box)

        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=0)
        guarded.append(self.card_box)

        self.media_box = MediaBox()
        guarded.append(self.media_box)

        # The row redraws itself whole after a toggle (refresh), so the
        # bar does not follow the item on its own.
        actions = tileactions.tile_action_bar(
            None, None, standalone=False)
        actions.host = host
        self.actions = actions
        self._overflow.actions = actions
        main.append(actions)

        self.carousel = None
        self._item_watch = None

    # The action buttons live in the bar; kept as attributes of the row.

    @property
    def reply_button(self):
        return self.actions.reply_button

    @property
    def boost_button(self):
        return self.actions.boost_button

    @property
    def quote_button(self):
        return self.actions.quote_button

    @property
    def favourite_button(self):
        return self.actions.favourite_button

    @property
    def bookmark_button(self):
        return self.actions.bookmark_button

    # Parts built on first use

    def _ensure_source_line(self):
        if self.source_line is None:
            line = SourceLine(self.ICON_BOOST)
            self.source_line = line
            self.body.prepend(line)
        return self.source_line

    def _ensure_name_label(self):
        if self.name_label is None:
            label = InlineEmojiText(emoji_size=18, single_line=True)
            label.add_css_class('heading')
            # Clicks fall through to the profile button.
            label.set_can_target(False)
            self.name_label = label
            self._name_line.insert_child_after(label, self.name_plain)
        return self.name_label

    def _ensure_cw_box(self):
        """Content warning: the spoiler line with its Show/Hide toggle."""
        if self.cw_box is None:
            box = Gtk.Box(spacing=8)
            box.add_css_class('cw-bar')
            self.cw_label = InlineEmojiText(emoji_size=16, shorten_links=True)
            self.cw_label.set_hexpand(True)
            self.cw_label.add_css_class('heading')
            self.cw_label.set_valign(Gtk.Align.CENTER)
            box.append(self.cw_label)
            self.cw_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
            self.cw_toggle.add_css_class('pill')
            self.cw_toggle.add_css_class('cw-toggle')
            connect_weak(self.cw_toggle, 'clicked', self._on_cw_toggle)
            box.append(self.cw_toggle)
            self.cw_box = box
            self.main.insert_child_after(box, self.reply_box)
            # Only rows that have shown a content warning pay for the
            # revealer the body slides in and out of.
            revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
            self.main.insert_child_after(revealer, self.guarded)
            self.main.remove(self.guarded)
            revealer.set_child(self.guarded)
            self.guarded_revealer = revealer
        return self.cw_box

    def _ensure_translation_box(self):
        if self.translation_box is None:
            box = Gtk.Box(spacing=8)
            self.translation_label = Gtk.Label(xalign=0.0, hexpand=True,
                                               wrap=True)
            self.translation_label.add_css_class('caption')
            self.translation_label.add_css_class('dimmed')
            box.append(self.translation_label)
            original = Gtk.Button(label=_('Show _Original'),
                                  use_underline=True)
            original.add_css_class('flat')
            original.add_css_class('caption')
            connect_weak(original, 'clicked',
                         self._overflow.toggle_translation)
            box.append(original)
            self.translation_box = box
            self.guarded.insert_child_after(box, self.post_meta)
        return self.translation_box

    def _ensure_carousel(self):
        """Boosts-carousel mode: the first list row can render as the
        horizontal boosts carousel instead of a normal post."""
        if self.carousel is None:
            self.carousel = BoostCarousel()
            self.body.append(self.carousel)
        return self.carousel

    # Overflow menu and action bar

    def on_post_event(self, name):
        """What the action bar and the menu report: ``refresh`` (a
        toggle or a menu action changed the post in place),
        ``translated`` and ``deleted``."""
        if name == 'refresh':
            if self.item is not None:
                self.bind(self.item, self.client)
        elif name == 'translated':
            # A post collapsed by a filter binds no translation box.
            if self.translation_box is not None:
                pages.flash_class(self.translation_box, 'fade-in')
            pages.flash_class(self.content, 'fade-in')
        elif name == 'deleted':
            self.set_visible(False)
        elif name == 'restored':
            self.set_visible(True)

    def _on_edited_clicked(self, _button):
        self._overflow.show_history()

    def set_run(self, position, total):
        """Lay this post out as part of a run of same-thread posts by
        the same author: the ``n/total`` badge appears, and the run card
        decides which of the other header pieces to keep."""
        self.badge.set_visible(bool(total) and total >= 2)
        if self.badge.get_visible():
            text = _('Post %(position)d of %(total)d in Thread') % {
                'position': position, 'total': total}
            self.badge.set_text('%d/%d' % (position, total))
            pages.set_accessible_label(self.badge, text)
        if self.source_line is not None:
            self.source_line.set_visible(self._has_source and position == 1)

    # Binding

    def bind(self, item, client):
        self._stop_body_animation()
        # Edits and carousel updates change the item in place.
        self._item_watch = _rewatch(
            self._item_watch, item, self._on_item_changed)
        if item is not self.item:
            self._clear_flashes()
        self.item = item
        self.client = client
        tileactions.tile_menu_button(
            item, client, button=self._overflow, actions=self.actions)
        if isinstance(item, BoostCarouselItem):
            self._bind_carousel(item, client)
            return
        if self.carousel is not None:
            self.carousel.set_visible(False)
        self._bind_status(item)

    def _clear_flashes(self):
        """End a pop or fade still playing for the post this recycled row
        showed before, so the next post does not inherit it."""
        self.actions.clear_flashes()
        for widget in (self.content, self.translation_box):
            if widget is not None:
                widget.remove_css_class('fade-in')

    def _bind_status(self, item):
        status = item.status
        account = status['account']
        ui = item.ui
        if item.is_boost:
            self._has_source = True
            label = _('Post by %(author)s, boosted by %(booster)s') % {
                'author': item.display_name, 'booster': item.boost_line}
        else:
            self._has_source = bool(item.ui.get('source_tag'))
            label = _('Post by %s') % item.display_name
        if self._has_source:
            self._ensure_source_line().show_for(item)
        elif self.source_line is not None:
            self.source_line.set_visible(False)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

        rule = status_filter(item.raw, self.filter_context,
                             datetime.datetime.now(datetime.timezone.utc))
        shown = ui.get('filter_shown')
        # 'blur' never collapses: the post stays readable and only the
        # media is covered; 'warn' and 'hide' collapse until shown.
        blurred_rule = rule is not None and rule[0] == 'blur'
        collapsed = rule is not None and not blurred_rule and not shown
        blur = blurred_rule and not shown
        self.media_blur = blur
        self.filter_bar.set_visible(collapsed or blur)
        self.actions.set_visible(
            self.detail or _flag('show-action-bar'))
        if collapsed:
            self.filter_label.set_text(
                _('Filtered: %s') % rule[1] if rule[1] else _('Filtered'))
            self.main.set_visible(False)
            return
        self.main.set_visible(True)
        if blur:
            # The badge stays so the filter names itself; the Show
            # Anyway click clears it together with the blur.
            self.filter_label.set_text(
                _('Filtered: %s') % rule[1] if rule[1] else _('Filtered'))

        AvatarCache.load(self.avatar, account)
        display = item.display_name or '?'
        if _display_name_uses_custom_emoji(account, display):
            self.name_plain.set_visible(False)
            self._ensure_name_label().set_visible(True)
            self.name_label.set_segments(
                [{'text': display, 'href': None}], emoji_bank(account))
        else:
            if self.name_label is not None:
                self.name_label.set_visible(False)
            self.name_plain.set_visible(True)
            self.name_plain.set_label(display)
            self.name_plain.set_tooltip_text(display)
        caption = item.header_caption
        self.handle_label.set_text(caption)
        self.handle_label.set_visible(bool(caption))
        self.handle_label.set_tooltip_text(item.handle)
        self.bot_badge.set_visible(bool(account.get('bot')))
        self.profile_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ['%s %s' % (item.display_name, item.handle)])
        self._bind_indicators(item)
        self._set_time(item)
        self._set_post_meta(item)
        self._bind_reply_line(item)

        translation = item.translation
        spoiler = (translation or {}).get('spoiler_text') \
            or status.get('spoiler_text') or ''
        bank = status_emoji_bank(status)
        self._emoji_bank = bank
        has_cw = bool(spoiler)
        revealed = True
        if has_cw:
            self._ensure_cw_box().set_visible(True)
            self.cw_label.set_segments(
                [{'text': spoiler, 'href': None}], bank)
            revealed = ui.get('cw_open')
            if revealed is None:
                revealed = _flag('show-spoilers')
            self._update_cw_toggle(spoiler, revealed)
        elif self.cw_box is not None:
            self.cw_box.set_visible(False)
        if self.guarded_revealer is not None:
            pages.reveal(self.guarded_revealer, bool(revealed))

        self._render_body(item, bank)
        self._bind_translation(item)

        poll = status.get('poll')
        if poll and translation and translation.get('poll'):
            titles = [o.get('title') for o in
                      translation['poll'].get('options') or []]
            options = [dict(o, title=titles[i] if i < len(titles)
                            and titles[i] else o.get('title'))
                       for i, o in enumerate(poll.get('options') or [])]
            poll = dict(poll, options=options)
        self.poll_box.set_poll(poll, bank, self.client, ui,
                               lambda new: status.__setitem__('poll', new))
        quote = status.get('quote')
        self._show_quote(quote)
        self._show_collections(status.get('tagged_collections'))
        attachments = status.get('media_attachments') or []
        self.media_box.set_media(
            attachments, bool(status.get('sensitive')) and not has_cw, ui,
            blur=self.media_blur)
        show_card = _flag('show-preview-cards') and not poll and not quote \
            and not attachments
        self._show_card(status.get('card') if show_card else None)
        tileactions.tile_action_bar(item, self.client, bar=self.actions)

    def _bind_translation(self, item):
        """The 'Translated from …' note above the body, when shown."""
        translation = item.translation
        if not translation:
            if self.translation_box is not None:
                self.translation_box.set_visible(False)
            return
        self._ensure_translation_box().set_visible(True)
        detected = (translation.get('detected_source_language') or '').upper()
        provider = translation.get('provider') or ''
        if detected and provider:
            text = _('Translated from %(language)s using %(provider)s') \
                % {'language': detected, 'provider': provider}
        elif provider:
            text = _('Translated using %s') % provider
        else:
            text = _('Translated')
        self.translation_label.set_text(text)

    def _bind_indicators(self, item):
        status = item.status
        info = VISIBILITY_INFO.get(status.get('visibility'))
        show_visibility = info is not None \
            and status.get('visibility') != 'public'
        self.visibility_icon.set_visible(show_visibility)
        if show_visibility:
            self.visibility_icon.set_from_icon_name(info[1])
            pages.set_accessible_label(self.visibility_icon, info[0])
        language = status.get('language')
        show_language = bool(language) \
            and language.lower().split('-')[0] != ui_language()
        self.language_label.set_visible(show_language)
        if show_language:
            self.language_label.set_text(language.upper())
            self.language_label.set_tooltip_text(
                _('Language: %s') % language)
        edited = status.get('edited_at')
        self.edited_button.set_visible(bool(edited))
        if edited:
            tip = _('Edited %s') % absolute_time(edited)
            self.edited_button.set_tooltip_text(tip)
            self.edited_button.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [_('Edited. Show edit history')])
        self.pinned_badge.set_visible(bool(status.get('pinned')))

    def _bind_reply_line(self, item):
        status = item.status
        target = status.get('in_reply_to_account_id')
        show = bool(target and status.get('in_reply_to_id'))
        self.reply_box.set_visible(show)
        if not show:
            return
        if target == status['account'].get('id'):
            text = _('Replying to themselves')
        else:
            acct = next((m.get('acct') for m in status.get('mentions') or []
                         if m.get('id') == target), None)
            text = _('Replying to @%s') % acct if acct \
                else _('Replying to a post')
        self.reply_label.set_text(text)

    def _render_body(self, item, bank):
        status = item.status
        html = (item.translation or {}).get('content') \
            or status.get('content', '')
        segments = parse_content(html)
        self.content.link_mentions = status.get('mentions')
        self.content.set_emoji_size(
            32 if _flag('enlarge-custom-emojis') else 20)
        shown, collapsible = _collapse_view(segments)
        expanded = bool(item.ui.get('expanded'))
        if collapsible and not expanded:
            self.content.set_segments(shown, bank)
        else:
            self.content.set_segments(segments, bank)
        self.expand_button.set_visible(collapsible)
        self.expand_button.set_label(
            _('Show Less') if expanded else _('Show More'))
        self.content.set_visible(bool(segments))

    def _show_quote(self, quote):
        pages.clear_children(self.quote_box)
        self.quote_box.set_visible(bool(quote))
        if quote:
            self.quote_box.append(QuoteCard(quote, self.client))

    def _show_collections(self, collections):
        """Cards for the Collections a post links to (Mastodon 4.6+,
        ``Status#tagged_collections``). Hidden when the post shares
        none."""
        pages.clear_children(self.collections_box)
        known = [c for c in collections or [] if c.get('id')]
        self.collections_box.set_visible(bool(known))
        for collection in known:
            self.collections_box.append(CollectionCard(collection,
                                                       self.client))

    def _set_time(self, item):
        self.time_label.set_text(item.timestamp)
        # absolute_time, which answers '' rather than raising.
        full = absolute_time(item.status.get('created_at'))
        self.time_label.set_tooltip_text(full or None)
        if full:
            self.time_label.update_property([Gtk.AccessibleProperty.LABEL],
                                            [full])
        # A continuation post of a run card leaves the time to the lead;
        # it can rebind on its own when the post is edited.
        self.time_label.set_visible(not self.has_css_class('run-post'))

    def _set_post_meta(self, item):
        """Phanpy-style line under the body: only the visibility and the
        relative time; the absolute date stays in the tooltip."""
        self.post_meta.set_visible(True)
        status = item.status
        visibility = VISIBILITY_INFO.get(status.get('visibility'),
                                       VISIBILITY_INFO['public'])[0]
        absolute = absolute_time(status.get('created_at'))
        self.post_meta.set_label('%s · %s' % (visibility, item.timestamp)
                                 if item.timestamp else visibility)
        self.post_meta.set_tooltip_text(absolute or None)

    def _bind_carousel(self, item, client):
        for widget in (self.source_line, self.filter_bar, self.main):
            if widget is not None:
                widget.set_visible(False)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Boosts')])
        carousel = self._ensure_carousel()
        carousel.set_visible(True)
        carousel.set_boosts(item.boosts, client)

    def _on_item_changed(self, item):
        if item is self.item:
            self.bind(item, self.client)

    def _on_show_filtered(self, _button):
        if self.item is None:
            return
        self.item.ui['filter_shown'] = True
        self.bind(self.item, self.client)

    def _update_cw_toggle(self, spoiler, revealed):
        self.cw_toggle.set_label(_('Hide Content') if revealed
                                 else _('Show Content'))
        self.cw_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Hide content warning: %s') % spoiler if revealed
             else _('Show content warning: %s') % spoiler])

    def _on_cw_toggle(self, _button):
        item = self.item
        if item is None:
            return
        current = item.ui.get('cw_open')
        if current is None:
            current = _flag('show-spoilers')
        item.ui['cw_open'] = not current
        spoiler = (item.translation or {}).get('spoiler_text') \
            or item.status.get('spoiler_text') or ''
        self._update_cw_toggle(spoiler, not current)
        pages.reveal(self.guarded_revealer, not current, animate=True)

    def _on_expand(self, _button):
        item = self.item
        if item is None:
            return
        content = self.content
        h0 = content.get_height()
        animate = h0 > 0 and content.get_mapped()
        self._stop_body_animation()
        item.ui['expanded'] = not item.ui.get('expanded')
        self._render_body(item, self._emoji_bank)
        if not animate:
            return
        # The text view lays the new text out before the next frame;
        # measured now it still answers with the old height. Hold the
        # old height until then and size the animation from the layout.
        self._body_overflow = content.get_overflow()
        content.set_overflow(Gtk.Overflow.HIDDEN)
        content._height_override = h0
        self._body_tick = content.add_tick_callback(
            pages.weak_method(self._on_body_laid_out), h0)

    def _on_body_laid_out(self, content, _clock, h0):
        self._body_tick = None
        h1 = Gtk.TextView.do_measure(
            content, Gtk.Orientation.VERTICAL, content.get_width())[1]
        if abs(h1 - h0) < 4:
            self._clear_body_height()
            return GLib.SOURCE_REMOVE
        animation = Adw.TimedAnimation(
            widget=content, value_from=h0, value_to=h1, duration=250,
            easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.CallbackAnimationTarget.new(
                pages.weak_method(self._on_body_height)))
        connect_weak(animation, 'done', self._on_body_animation_done)
        self._body_animation = animation
        animation.play()
        return GLib.SOURCE_REMOVE

    def _on_body_height(self, value):
        self.content._height_override = round(value)
        self.content.queue_resize()

    def _on_body_animation_done(self, _animation):
        self._body_animation = None
        self._clear_body_height()

    def _clear_body_height(self):
        content = self.content
        if content._height_override is not None:
            content._height_override = None
            content.set_overflow(self._body_overflow)
            content.queue_resize()

    def _stop_body_animation(self):
        if self._body_tick is not None:
            self.content.remove_tick_callback(self._body_tick)
            self._body_tick = None
        animation = self._body_animation
        if animation is not None:
            self._body_animation = None
            animation.skip()
        self._clear_body_height()

    def _show_card(self, card):
        pages.clear_children(self.card_box)
        if not card or not (card.get('title') or card.get('description')):
            self.card_box.set_visible(False)
            return
        self.card_box.set_visible(True)
        self.card_box.append(CardPreview(card))

    def _on_profile_clicked(self, _button):
        if self.item is not None:
            self.activate_profile(self.item)

    def _on_link_drag_prepare(self, source, _x, _y):
        url = self.item.status_url if self.item is not None else ''
        if not url:
            return None
        from .mediaviewer import link_provider
        if self.avatar.get_mapped():
            source.set_icon(Gtk.WidgetPaintable.new(self.avatar),
                            self.avatar.get_width() // 2,
                            self.avatar.get_height() // 2)
        return link_provider(url)

    def activate_status(self, item):
        _open_thread(self, self.client, item)

    def activate_profile(self, item):
        window = pages.window_of(self)
        if window is None:
            return
        from .profile import push_profile
        push_profile(window.navigation_view, self.client,
                     item.status['account'])

