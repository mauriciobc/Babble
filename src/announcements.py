# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance announcements (``GET /api/v1/announcements``).

Every announcement is a card with its rendered content (custom emoji
included), publication/update and event dates, a read marker with
Mark as Read, and a reactions bar: existing reactions toggle your own
(``PUT``/``DELETE /announcements/:id/reactions/:name``) and a Gtk emoji
chooser adds a new one."""

import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from .pages import PagedList, connect_weak, report_error  # noqa: E402
from .textemoji import InlineEmojiText, emoji_bank_from  # noqa: E402
from .timeline import AvatarCache, parse_content  # noqa: E402


def _local(iso):
    try:
        return GLib.DateTime.new_from_iso8601(iso, None).to_local()
    except (TypeError, GLib.Error):
        return None


def format_date(iso, all_day=False):
    """Locale date (and time) of an ISO 8601 timestamp; '' when
    unparseable."""
    moment = _local(iso) if iso else None
    if moment is None:
        return ''
    return moment.format('%x' if all_day else '%x %R')


def date_lines(announcement):
    """Human lines under the content: when it was published/updated and,
    for event announcements, when it starts and ends."""
    all_day = bool(announcement.get('all_day'))
    lines = []
    published = announcement.get('published_at')
    updated = announcement.get('updated_at')
    if published:
        line = _('Published %s') % format_date(published)
        if updated and updated != published:
            line += ' · ' + _('Updated %s') % format_date(updated)
        lines.append(line)
    starts = announcement.get('starts_at')
    ends = announcement.get('ends_at')
    if starts and ends:
        lines.append(_('Event: %(start)s – %(end)s') % {
            'start': format_date(starts, all_day),
            'end': format_date(ends, all_day)})
    elif starts:
        lines.append(_('Starts %s') % format_date(starts, all_day))
    elif ends:
        lines.append(_('Until %s') % format_date(ends, all_day))
    return lines


class ReactionButton(Gtk.ToggleButton):
    """One reaction: emoji (or custom emoji image) and its count; active
    when it is yours."""

    __gtype_name__ = 'BabbleReactionButton'

    def __init__(self, reaction):
        super().__init__()
        self.reaction = reaction
        self.add_css_class('pill')
        self.add_css_class('reaction')
        box = Gtk.Box(spacing=6)
        url = reaction.get('url') or reaction.get('static_url')
        if url:
            from .textemoji import EmojiTextures
            image = Gtk.Image(pixel_size=16,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
            EmojiTextures.bind(image, url)
            box.append(image)
        else:
            box.append(Gtk.Label(label=reaction['name']))
        self.count_label = Gtk.Label()
        self.count_label.add_css_class('caption')
        box.append(self.count_label)
        self.set_child(box)
        self.set_active(bool(reaction.get('me')))
        self.refresh()

    def refresh(self):
        count = self.reaction.get('count') or 0
        self.count_label.set_text(str(count))
        name = self.reaction['name']
        self.set_tooltip_text(name if self.reaction.get('url') else None)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('%(name)s, %(count)d') % {'name': name, 'count': count}])


class ReactionsBar(Gtk.Box):
    """Reactions of one announcement plus the add-reaction button."""

    __gtype_name__ = 'BabbleReactionsBar'

    def __init__(self, client, announcement):
        super().__init__(spacing=6)
        self.client = client
        self.announcement_id = announcement['id']
        self.buttons = {}
        self.flow = Adw.WrapBox(child_spacing=6, line_spacing=6, hexpand=True)
        self.append(self.flow)

        chooser = Gtk.EmojiChooser()
        connect_weak(chooser, 'emoji-picked', self._on_emoji_picked)
        self.add_button = Gtk.MenuButton(icon_name='list-add-symbolic',
                                         popover=chooser,
                                         valign=Gtk.Align.START)
        self.add_button.add_css_class('circular')
        self.add_button.set_tooltip_text(_('Add Reaction'))
        self.add_button.update_property([Gtk.AccessibleProperty.LABEL],
                                        [_('Add Reaction')])
        self.append(self.add_button)

        for reaction in announcement.get('reactions') or []:
            self._add(dict(reaction))

    def _add(self, reaction):
        button = ReactionButton(reaction)
        button.toggled_id = connect_weak(button, 'toggled', self._on_toggled)
        self.buttons[reaction['name']] = button
        self.flow.append(button)
        return button

    def _on_emoji_picked(self, _chooser, emoji):
        button = self.buttons.get(emoji)
        if button is None:
            button = self._add({'name': emoji, 'count': 0, 'me': False})
        button.set_active(True)

    def _on_toggled(self, button):
        """Reflect the toggle on the server: PUT adds your reaction,
        DELETE removes it."""
        reaction = button.reaction
        mine = button.get_active()
        if bool(reaction.get('me')) == mine:
            return
        name = reaction['name']
        path = '/announcements/%s/reactions/%s' % (
            self.announcement_id, urllib.parse.quote(name, safe=''))
        button.set_sensitive(False)

        def done(_client, result, _data=None):
            button.set_sensitive(True)
            try:
                self.client.request_finish(result)
            except GLib.Error as error:
                button.handler_block(button.toggled_id)
                button.set_active(not mine)
                button.handler_unblock(button.toggled_id)
                report_error(self, _('Could not update the reaction'), error)
                return
            reaction['me'] = mine
            reaction['count'] = max(
                0, (reaction.get('count') or 0) + (1 if mine else -1))
            if reaction['count'] == 0:
                self.flow.remove(button)
                self.buttons.pop(name, None)
            else:
                button.refresh()

        self.client.request_async('PUT' if mine else 'DELETE', path,
                                  callback=done)


class AnnouncementRow(Gtk.ListBoxRow):
    """One announcement card."""

    __gtype_name__ = 'BabbleAnnouncementRow'

    def __init__(self, page, announcement):
        super().__init__(activatable=False, selectable=False)
        # Weak: the row lives inside the page; a strong back-reference
        # through C would keep both alive forever.
        self._page = page.weak_ref()
        self.announcement = announcement
        client = page.client
        self.add_css_class('announcement-row')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12)
        self.set_child(box)

        info = client.instance_info or {}
        domain = client.instance.removeprefix('https://') \
            .removeprefix('http://')
        title = info.get('title') or domain
        header = Gtk.Box(spacing=10)
        avatar = Adw.Avatar(size=32, text=title, show_initials=True,
                            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        thumbnail = (info.get('thumbnail') or {}).get('url')
        if thumbnail:
            AvatarCache.load(avatar, {'avatar': thumbnail})
        header.append(avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.CENTER, hexpand=True)
        name = Gtk.Label(label=title, xalign=0.0)
        name.add_css_class('heading')
        names.append(name)
        handle = Gtk.Label(label='@' + domain, xalign=0.0)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        names.append(handle)
        header.append(names)
        self.unread_badge = Gtk.Label(label=_('Unread'),
                                      valign=Gtk.Align.CENTER)
        self.unread_badge.add_css_class('caption')
        self.unread_badge.add_css_class('announcement-unread')
        header.append(self.unread_badge)
        box.append(header)

        self.content = InlineEmojiText(emoji_size=18, paragraph_gap=True,
                                       hexpand=True)
        self.content.add_css_class('status-content')
        self.content.link_mentions = announcement.get('mentions') or []
        self.content.link_tags = announcement.get('tags') or []
        bank = emoji_bank_from(announcement.get('emojis'))
        self.content.set_segments(
            parse_content(announcement.get('content') or ''), bank)
        box.append(self.content)

        for line in date_lines(announcement):
            label = Gtk.Label(label=line, xalign=0.0, wrap=True)
            label.add_css_class('caption')
            label.add_css_class('dimmed')
            box.append(label)

        self.reactions = ReactionsBar(client, announcement)
        box.append(self.reactions)

        self.read_button = Gtk.Button(label=_('Mark as _Read'),
                                      use_underline=True,
                                      halign=Gtk.Align.END)
        self.read_button.add_css_class('pill')
        connect_weak(self.read_button, 'clicked', self._on_read_clicked)
        box.append(self.read_button)
        self._sync_read()

    @property
    def page(self):
        return self._page()

    def _on_read_clicked(self, _button):
        self.mark_read()

    def _sync_read(self):
        unread = not self.announcement.get('read')
        self.unread_badge.set_visible(unread)
        self.read_button.set_visible(unread)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Unread announcement') if unread else _('Announcement')])

    def mark_read(self):
        """POST /api/v1/announcements/:id/dismiss."""
        if self.announcement.get('read'):
            return
        client = self.page.client
        self.read_button.set_sensitive(False)

        def done(_client, result, _data=None):
            self.read_button.set_sensitive(True)
            try:
                client.request_finish(result)
            except GLib.Error as error:
                report_error(self, _('Could not mark as read'), error)
                return
            self.announcement['read'] = True
            self._sync_read()
            self.page.emit_unread_changed()

        client.request_async(
            'POST', '/announcements/%s/dismiss' % self.announcement['id'],
            callback=done)


class AnnouncementsPage(PagedList):
    """Every announcement of the instance, dismissed ones included."""

    __gtype_name__ = 'BabbleAnnouncementsPage'
    __gsignals__ = {
        'unread-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, client, **kwargs):
        super().__init__(
            client, '/announcements', self._make_row,
            params={'with_dismissed': 'true'}, paginated=False,
            empty_title=_('No Announcements'),
            empty_icon='dialog-information-symbolic',
            empty_description=_('Messages from the server administrators '
                                'will appear here'),
            **kwargs)
        self.mark_all_button = Gtk.Button(icon_name='object-select-symbolic')
        self.mark_all_button.add_css_class('flat')
        self.mark_all_button.set_tooltip_text(_('Mark All as Read'))
        self.mark_all_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Mark All as Read')])
        self.mark_all_button.set_sensitive(False)
        connect_weak(self.mark_all_button, 'clicked',
                     self._on_mark_all_clicked)
        self.header_widgets = [self.mark_all_button]
        if not client.instance_info:
            client.instance_info_async(callback=self._on_instance_info)

    def _on_mark_all_clicked(self, _button):
        self.mark_all_read()

    def _on_instance_info(self, client, result, _data=None):
        """The cards show the instance's title and thumbnail; rebuild
        them once the instance details have arrived."""
        try:
            client.instance_info_finish(result)
        except GLib.Error:
            return
        self.refresh()

    def _make_row(self, announcement):
        return AnnouncementRow(self, announcement)

    def unread_count(self):
        return sum(1 for row in self.rows
                   if not row.announcement.get('read'))

    def emit_unread_changed(self):
        count = self.unread_count()
        self.mark_all_button.set_sensitive(count > 0)
        self.emit('unread-changed', count)

    def append_items(self, items):
        super().append_items(items)
        self.emit_unread_changed()

    def mark_all_read(self):
        """The API has no bulk endpoint: dismiss each unread one."""
        for row in self.rows:
            row.mark_read()
