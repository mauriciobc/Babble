# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, GLib, GObject, Gtk, Pango)

from .entities import (STATUS_MERGE_KEYS as MERGE_KEYS)
from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .textemoji import (InlineEmojiText, emoji_bank)

"""Quote and collection cards, and the reply/boost/favourite bar."""
from .mediacache import AvatarCache, _TextureCache
from .timelinebase import (
    COLUMN_WIDTH, OLD_POST_DAYS, VISIBILITY_INFO, _flag, _has_schema,
    _own_account_id, can_quote, days_since, decodable_attachment_url,
    plain_text)
from .mediabox import status_picture, _rewatch
from .timelinedata import BoostCarouselItem


class QuoteCard(Gtk.Button):
    """Compact card for a post's ``quote``: the quoted post (author, text,
    first picture) opening its thread, or a notice for quotes that are
    pending, declined, removed or hidden."""

    NOTICES = {
        'pending': _('Quote Pending'),
        'rejected': _('Quote Not Approved'),
        'revoked': _('Quote Removed by Author'),
        'deleted': _('Quoted Post Was Deleted'),
        'unauthorized': _('Quote Unavailable'),
        'blocked_account': _('Quote Hidden: You Blocked the Author'),
        'blocked_domain': _('Quote Hidden: You Blocked the Server'),
        'muted_account': _('Quote Hidden: You Muted the Author'),
    }

    def __init__(self, quote, client):
        super().__init__()
        self.add_css_class('card')
        self.add_css_class('quote-card')
        self.client = client
        self.quote = quote
        quoted = quote.get('quoted_status')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=10,
                      margin_end=10)
        self.set_child(box)
        if quoted:
            self._fill(box, quoted)
            self.connect('clicked', self._open_quoted, quoted)
            return
        state = quote.get('state') or ''
        if state == 'accepted' and quote.get('quoted_status_id'):
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Image(icon_name='format-indent-more-symbolic'))
            row.append(Gtk.Label(label=_('Quoted a Post'), xalign=0.0))
            box.append(row)
            self.connect('clicked', self._resolve_quoted)
            return
        text = self.NOTICES.get(state, _('Quote Unavailable'))
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image(icon_name='dialog-warning-symbolic'))
        label = Gtk.Label(label=text, xalign=0.0, wrap=True, hexpand=True)
        label.add_css_class('dimmed')
        row.append(label)
        box.append(row)
        self.set_sensitive(False)
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _fill(self, box, quoted):
        account = quoted.get('account') or {}
        name = account.get('display_name') or account.get('acct', '')
        head = Gtk.Box(spacing=6)
        avatar = Adw.Avatar(size=22, show_initials=True, text=name or '?')
        AvatarCache.load(avatar, account)
        head.append(avatar)
        title = InlineEmojiText(emoji_size=14, single_line=True)
        title.add_css_class('caption-heading')
        title.set_can_target(False)
        title.set_segments([{'text': name or '?', 'href': None}],
                           emoji_bank(account))
        head.append(title)
        handle = Gtk.Label(label='@%s' % account.get('acct', ''),
                           xalign=0.0, hexpand=True,
                           ellipsize=Pango.EllipsizeMode.END)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        head.append(handle)
        box.append(head)
        spoiler = quoted.get('spoiler_text')
        text = (_('Content warning: %s') % spoiler) if spoiler \
            else plain_text(quoted.get('content'), 280)
        body = Gtk.Label(label=text or '…', xalign=0.0, wrap=True,
                         lines=4, ellipsize=Pango.EllipsizeMode.END,
                         wrap_mode=Pango.WrapMode.WORD_CHAR)
        if spoiler:
            body.add_css_class('dimmed')
        box.append(body)
        media = [a for a in quoted.get('media_attachments') or []
                 if a.get('type') in ('image', 'video', 'gifv')]
        if media and not spoiler and not quoted.get('sensitive'):
            picture = status_picture(-1, 110, presentation=True)
            url = decodable_attachment_url(media[0])
            if url:
                _TextureCache.load(url, picture, (COLUMN_WIDTH, 110))
            box.append(picture)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Quoted post by %(author)s: %(text)s') % {
                'author': name, 'text': text}])

    def _open_quoted(self, _button, quoted):
        pages.open_status(self, self.client, quoted)

    def _resolve_quoted(self, _button):
        pages.call(
            self, self.client, 'GET',
            '/statuses/%s' % self.quote['quoted_status_id'],
            lambda data: pages.open_status(self, self.client, data),
            _('Could not open quoted post'))


class CollectionCard(Gtk.Button):
    """Compact card for a Collection shared in a post (Mastodon 4.6+,
    ``Status#tagged_collections``): name, member count, and a click that
    opens the Collection in-app."""

    __gtype_name__ = 'BabbleCollectionCard'

    def __init__(self, collection, client):
        super().__init__()
        self.collection = collection
        self.client = client
        self.add_css_class('card')
        self.add_css_class('collection-card')
        box = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8,
                      margin_start=10, margin_end=10)
        self.set_child(box)
        icon = Gtk.Image(icon_name='view-list-symbolic', pixel_size=22)
        icon.add_css_class('dimmed')
        box.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                       hexpand=True)
        box.append(text)
        name = Gtk.Label(label=collection.get('name') or _('Collection'),
                         xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('heading')
        text.append(name)
        count = collection.get('item_count')
        if count is None:
            count = len(collection.get('items') or [])
        detail = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        detail.add_css_class('caption')
        detail.add_css_class('dimmed')
        detail.set_text(ngettext('%d account', '%d accounts', count) % count)
        text.append(detail)
        box.append(Gtk.Image(icon_name='go-next-symbolic'))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [_('Collection %s')
                              % (collection.get('name') or '')])
        pages.connect_weak(self, 'clicked', self._open)

    def _open(self, _button):
        pages.open_collection(self, self.client, self.collection)


def _display_name_uses_custom_emoji(account, display_name):
    """True when the visible name contains a custom emoji shortcode."""
    bank = emoji_bank(account)
    if not bank or not display_name:
        return False
    for shortcode in bank:
        if ':%s:' % shortcode in display_name:
            return True
    return False


def _open_status_menu(_button, menu_ref):
    """Gtk.MenuButton create-popup callback of a StatusMenuButton; holds
    the button weakly so it does not keep itself alive."""
    menu = menu_ref()
    if menu is not None:
        menu.prepare()


# Passed to ``bind()`` for "ask the window", as opposed to None, which
# means signed out.
WINDOW_CLIENT = object()


def _merge_status(item, data):
    """Copy the fields a status action changes into *item*.

    *data* was typed by lift at the API boundary. ``reblog`` is the
    Status the reader actually acted on, when the action went through a
    boost; it inherits the reader's own state from the server."""
    if not isinstance(data, dict):
        return
    fresh = data.reblog or data
    if fresh.id != item.status_id:
        return
    for key in MERGE_KEYS:
        if key in fresh:
            item.status[key] = fresh[key]


def _open_composer(widget, method, status):
    window = pages.window_of(widget)
    if window is None:
        return
    dialog = window.ensure_composer()
    if dialog is None:
        return
    getattr(dialog, method)(status)
    window.present_composer()


def _open_thread(widget, client, item):
    window = pages.window_of(widget)
    if window is None:
        return
    from .thread import push_thread
    push_thread(window.navigation_view, client, item)


class _PostBound:
    """What the action bar and the overflow menu share: the bound
    StatusItem, the client acting on it, and what the signed-in account
    may do with the post."""

    item = None
    _client = None
    # A host that wants the notifications below as plain method calls
    # (StatusRow) sets this to its ``weak_ref()``: no signal handler, so
    # no closure per row. Without one they are emitted as signals.
    host = None

    def _notify(self, name):
        """Tell the host (or the signal's listeners) that the post
        changed in place, was translated or was deleted."""
        if self.host is None:
            self.emit(name)
            return
        owner = self.host()
        if owner is not None:
            owner.on_post_event(name)

    @property
    def client(self):
        if self._client is WINDOW_CLIENT:
            return pages.client_of(self)
        return self._client

    def _can_act(self):
        client = self.client
        return client is not None and bool(getattr(client, 'token', None))

    def _is_own(self):
        own = _own_account_id(self)
        account = self.item.status['account'] if self.item else {}
        return bool(own) and account.get('id') == own

    def can_be_boosted(self, item):
        visibility = item.status.get('visibility')
        return visibility != 'direct' and (
            visibility != 'private' or self._is_own())


class StatusActions(_PostBound, Adw.WrapBox):
    """The reply, boost, quote, favourite and bookmark buttons of a post,
    each with its count, bound to a StatusItem: StatusRow's action bar,
    usable on its own.

    *quote* adds the quote button. *button_classes* are the CSS classes
    of every button ('osd', 'pill' for buttons over an image); *tinted*
    colours the boost and favourite icons whatever the state. The counts
    follow ``show-interaction-counters`` and are written by
    *count_format*, which must not hold the widget that owns the bar.

    A host that redraws the whole post (StatusRow) passes *standalone*
    False and either connects ``refresh`` or sets ``host``; it is told
    after a toggle changed the post in place. Standalone, the bar
    follows the item's ``changed`` signal and redraws itself on
    ``refresh``."""

    __gtype_name__ = 'BabbleStatusActions'
    __gsignals__ = {'refresh': (GObject.SignalFlags.RUN_FIRST, None, ())}

    ICON_REPLY = 'mail-reply-sender-symbolic'
    ICON_BOOST = 'media-playlist-repeat-symbolic'
    ICON_QUOTE = 'format-indent-more-symbolic'
    # Not Adwaita's non-starred-symbolic: that glyph is a fill-opacity
    # 0.35 star, so an unfavorited post reads as a disabled button. The
    # outline shape alone carries the state, so it is drawn at full
    # strength here.
    ICON_FAVOURITE = ('babble-favorite-symbolic', 'starred-symbolic')
    ICON_BOOKMARK = ('bookmark-new-symbolic', 'user-bookmarks-symbolic')

    def __init__(self, quote=True, button_classes=('flat',), tinted=False,
                 count_format=str, standalone=True, **kwargs):
        # WrapBox, not Box: with a count next to each icon the five
        # actions need ~314 px, which overflows the 360 px window
        # minimum (350 px of content) instead of flowing to a second
        # line.
        super().__init__(child_spacing=4, line_spacing=4,
                         halign=Gtk.Align.START, **kwargs)
        self.add_css_class('status-actions')
        self.busy = False
        self._button_classes = button_classes
        self._tinted = tinted
        self._count_format = count_format
        self._standalone = standalone
        self._item_watch = None
        if standalone:
            connect_weak(self, 'refresh', self._on_refresh)
        self.reply_button = self._action_button(
            self.ICON_REPLY, _('Reply'), self._on_reply,
            nouns=('%d reply', '%d replies'))
        self.boost_button = self._action_button(
            (self.ICON_BOOST,) * 2, _('Boost'), self._on_boost,
            nouns=('%d boost', '%d boosts'), toggle=True,
            tint='tint-success')
        self.quote_button = self._action_button(
            self.ICON_QUOTE, _('Quote'), self._on_quote,
            nouns=('%d quote', '%d quotes')) if quote else None
        self.favourite_button = self._action_button(
            self.ICON_FAVOURITE, _('Favorite'), self._on_favourite,
            nouns=('%d favorite', '%d favorites'), toggle=True,
            tint='tint-error')
        self.bookmark_button = self._action_button(
            self.ICON_BOOKMARK, _('Bookmark'), self._on_bookmark,
            toggle=True)
        self.append(self.reply_button)
        self.append(self.boost_button)
        if self.quote_button is not None:
            self.append(self.quote_button)
        self.append(self.favourite_button)
        self.append(self.bookmark_button)

    # What the overflow menu's icon row runs, so reminders, the boost
    # dialog and the optimistic rollback stay in one place.

    def reply(self):
        self._on_reply(self.reply_button)

    def quote(self):
        self._on_quote(self.quote_button)

    def boost(self):
        self._on_boost(self.boost_button)

    def favourite(self):
        self._on_favourite(self.favourite_button)

    def bookmark(self):
        self._on_bookmark(self.bookmark_button)

    def clear_flashes(self):
        """End a pop still playing for the post this bar showed before."""
        for button in (self.favourite_button, self.boost_button,
                       self.bookmark_button):
            button.remove_css_class('just-toggled')

    def _refresh(self):
        self._notify('refresh')

    def _on_refresh(self, _actions):
        if self.item is not None:
            self.bind(self.item, self._client)

    def _watch(self, item):
        self._item_watch = _rewatch(
            self._item_watch, item, self._on_item_changed)

    def _on_item_changed(self, item):
        if item is self.item:
            self.bind(item, self._client)

    # Buttons

    def _action_button(self, icons, tooltip, callback, nouns=None,
                       toggle=False, tint=None):
        if isinstance(icons, str):
            icons = (icons, icons)
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        for css in self._button_classes:
            button.add_css_class(css)
        content = Adw.ButtonContent(icon_name=icons[0])
        if tint and self._tinted:
            content.add_css_class(tint)
        button.set_child(content)
        pages.set_accessible_label(button, tooltip)
        connect_weak(button, 'clicked', callback)
        button.icons = icons
        button.action_name_text = tooltip
        button.nouns = nouns
        return button

    def bind(self, item, client=WINDOW_CLIENT):
        """Show *item*'s counts and state. *client* defaults to the
        window's, looked up when an action runs."""
        if self._standalone:
            self._watch(item)
        self.item = item
        self._client = client
        status = item.status
        can_act = self._can_act()
        counters = _flag('show-interaction-counters')
        self._set_count(self.reply_button,
                        item.reply_count if counters else 0)
        self._set_count(self.boost_button,
                        item.boost_count if counters else 0)
        self._set_count(self.favourite_button,
                        item.favourite_count if counters else 0)
        self._set_count(self.bookmark_button, 0)
        self._set_active(self.boost_button, item.reblogged)
        self._set_active(self.favourite_button, item.favourited)
        self._set_active(self.bookmark_button, item.bookmarked)
        if self.quote_button is not None:
            quotable = 'quote_approval' in status
            self.quote_button.set_visible(quotable)
            if quotable:
                self._set_count(self.quote_button,
                                (status.get('quotes_count') or 0)
                                if counters else 0)
                allowed = can_quote(status)
                self.quote_button.set_sensitive(can_act and allowed)
                self.quote_button.set_tooltip_text(
                    _('Quote') if allowed
                    else _('This post can’t be quoted'))
        boostable = item.reblogged or self.can_be_boosted(item)
        self.boost_button.set_tooltip_text(
            _('Boost') if boostable else _('This post can’t be boosted'))
        self.favourite_button.set_sensitive(can_act and not self.busy)
        self.bookmark_button.set_sensitive(can_act and not self.busy)
        self.boost_button.set_sensitive(
            can_act and boostable and not self.busy)

    def _set_active(self, button, active):
        button.set_active(active)
        child = button.get_child()
        child.set_icon_name(button.icons[bool(active)])
        if button is self.boost_button:
            # add/remove rather than set_css_classes: the icon may
            # carry other classes on it.
            if active:
                child.add_css_class('boost-accent')
            else:
                child.remove_css_class('boost-accent')

    def _set_count(self, button, count):
        """Show *count* next to the icon and name it in the accessible
        label, so the button reads as 'Boost, 12 boosts'."""
        button.get_child().set_label(
            self._count_format(count) if count else '')
        label = button.action_name_text
        if count and button.nouns:
            label = '%s, %s' % (label, ngettext(
                button.nouns[0], button.nouns[1], count) % count)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _on_reply(self, _button):
        item = self.item
        if item is None:
            return
        window = pages.window_of(self)
        if window is None or not hasattr(window, 'open_compose_reply'):
            _open_thread(self, self.client, item)
            return
        status = item.status

        def reply():
            window.open_compose_reply(
                item.status_id, status['account'].get('acct', ''),
                status.get('visibility') or 'public')

        age = days_since(status.get('created_at'))
        if age > OLD_POST_DAYS and _flag('reply-to-old-post-reminder'):
            self._confirm_old_reply(age, reply)
        else:
            reply()

    def _confirm_old_reply(self, days, reply):
        pages.confirm_remind(
            self, _('Reply to an Old Post?'),
            _('This post is %(age)s old. You can still reply, but it '
              'may no longer be relevant.') % {
                'age': ngettext('%d day', '%d days', days) % days},
            'reply', _('_Reply'), 'reply-to-old-post-reminder', reply)

    def _on_quote(self, _button):
        if self.item is not None:
            _open_composer(self, 'start_quote', self.item.status)

    def _set_busy(self, busy):
        self.busy = busy
        if self.item is not None and not isinstance(
                self.item, BoostCarouselItem):
            can_act = self._can_act()
            self.favourite_button.set_sensitive(can_act and not busy)
            self.bookmark_button.set_sensitive(can_act and not busy)
            self.boost_button.set_sensitive(
                can_act and not busy
                and (self.item.reblogged or self.can_be_boosted(self.item)))

    def _send_toggle(self, kind, active, form=None):
        """Set favourite/reblog/bookmark to *active* with the matching
        do/undo endpoint, optimistic with rollback on failure."""
        item, client = self.item, self.client
        flag = {'favourite': 'favourited', 'reblog': 'reblogged',
                'bookmark': 'bookmarked'}[kind]
        count_key = {'favourite': 'favourites_count',
                     'reblog': 'reblogs_count'}.get(kind)
        verb = kind if active else {'favourite': 'unfavourite',
                                    'reblog': 'unreblog',
                                    'bookmark': 'unbookmark'}[kind]
        previous = {flag: item.status.get(flag)}
        item.status[flag] = active
        if count_key:
            previous[count_key] = item.status.get(count_key)
            item.status[count_key] = max(
                0, (item.status.get(count_key) or 0) + (1 if active else -1))
        self._set_busy(True)
        self._refresh()

        def finished(_client, result, _data=None):
            self._set_busy(False)
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                item.status.update(previous)
                pages.report_error(self, _('Action failed'), error)
            else:
                _merge_status(item, data)
            if self.item is item:
                self._refresh()

        client.request_async(
            'POST', '/statuses/%s/%s' % (item.status_id, verb), form=form,
            callback=finished)

    def _on_favourite(self, button):
        item = self.item
        self._set_active(button, item.favourited)
        if not item.favourited:
            pages.flash_class(button, 'just-toggled')
        self._send_toggle('favourite', not item.favourited)

    def _on_bookmark(self, button):
        item = self.item
        self._set_active(button, item.bookmarked)
        if not item.bookmarked:
            pages.flash_class(button, 'just-toggled')
        self._send_toggle('bookmark', not item.bookmarked)

    def _missing_alt(self):
        return any(
            a.get('type') in ('image', 'video', 'gifv', 'audio')
            and not a.get('description')
            for a in self.item.status.get('media_attachments') or [])

    def _on_boost(self, button):
        item = self.item
        # The toggle flipped itself; the request decides the real state.
        self._set_active(button, item.reblogged)
        if item.reblogged:
            self._send_toggle('reblog', False)
            return

        def proceed():
            if _flag('advanced-boost-dialog'):
                self._present_boost_dialog()
            else:
                pages.flash_class(button, 'just-toggled')
                self._send_toggle('reblog', True)

        if _flag('boost-alt-text-reminder') and self._missing_alt():
            self._present_alt_reminder(proceed)
        else:
            proceed()

    def _present_alt_reminder(self, proceed):
        dialog = Adw.AlertDialog(
            heading=_('This Post Has Media Without Alt Text'),
            body=_('Some of your followers might not be able to '
                   'understand it.'))
        never = Gtk.CheckButton(label=_('Don’t _remind me again'),
                                use_underline=True, halign=Gtk.Align.CENTER)
        dialog.set_extra_child(never)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('boost', _('_Boost'))
        dialog.set_response_appearance('boost',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if never.get_active():
                pages.settings().set_boolean('boost-alt-text-reminder',
                                             False)
            if response == 'boost':
                proceed()

        dialog.connect('response', on_response)
        dialog.present(self)

    def _present_boost_dialog(self):
        """Boost with a visibility choice and the option to quote."""
        item = self.item
        status = item.status
        original = status.get('visibility')
        allowed = {'public': ['public', 'unlisted', 'private'],
                   'unlisted': ['unlisted', 'private']}.get(
                       original, ['private'])
        descriptions = {
            'public': _('Visible for all'),
            'unlisted': _('Visible for all, but not in public timelines'),
            'private': _('Visible for your followers only')}
        preferred = pages.settings().get_string('default-visibility') \
            if _has_schema() else 'public'
        default = preferred if preferred in allowed else allowed[0]
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        checks = {}
        group = None
        for value in allowed:
            title, icon = VISIBILITY_INFO[value]
            check = Gtk.CheckButton(active=value == default,
                                    valign=Gtk.Align.CENTER)
            if group is None:
                group = check
            else:
                check.set_group(group)
            checks[value] = check
            row = Adw.ActionRow(title=title, subtitle=descriptions[value],
                                activatable_widget=check)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_prefix(check)
            listbox.append(row)
        dialog = Adw.AlertDialog(heading=_('Boost With Visibility'))
        dialog.set_extra_child(listbox)
        dialog.add_response('cancel', _('_Cancel'))
        if can_quote(status):
            dialog.add_response('quote', _('_Quote'))
        dialog.add_response('boost', _('_Boost'))
        dialog.set_response_appearance('boost',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('boost')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response == 'boost':
                chosen = next((v for v, c in checks.items()
                               if c.get_active()), default)
                pages.flash_class(self.boost_button, 'just-toggled')
                self._send_toggle('reblog', True,
                                  form={'visibility': chosen})
            elif response == 'quote':
                self._on_quote(None)

        dialog.connect('response', on_response)
        dialog.present(self)

