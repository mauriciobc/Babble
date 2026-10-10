# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversations: the direct-message inbox (``GET /api/v1/conversations``).

Each row shows the participants, a preview of the last message
(honouring content warnings) and an unread dot; activating a row marks it
as read and opens the thread of its last message. The row menu offers
Mark as Read and Delete."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

import re  # noqa: E402

from gi.repository import Adw, GLib, Gio, GObject, Gtk, Pango  # noqa: E402

from .pages import (PagedList, confirm, open_status, report_error,  # noqa: E402
                    show_toast, window_of)
from .timeline import AvatarCache, parse_content, relative_time  # noqa: E402

MAX_AVATARS = 3


def _plain_name(account):
    name = account.get('display_name') or account.get('username') \
        or account.get('acct') or '?'
    # Custom emoji shortcodes carry no meaning without their image.
    return re.sub(r':[A-Za-z0-9_+\-]+:', '', name).strip() \
        or account.get('acct') or '?'


def participants_title(conversation):
    return ', '.join(_plain_name(a) for a in conversation.get('accounts', []))


def last_status_preview(conversation):
    """One short plain-text line for the last message: the content
    warning replaces the body when there is one."""
    status = conversation.get('last_status')
    if not status:
        return _('No messages')
    if status.get('spoiler_text'):
        return _('CW: %s') % status['spoiler_text']
    text = ' '.join(s['text'] for s in parse_content(status.get('content')
                                                      or '')
                    if s['text'] != '\n').strip()
    if text:
        return text
    count = len(status.get('media_attachments') or [])
    if count:
        return ngettext('%d attachment', '%d attachments', count) % count
    return _('No messages')


class ConversationRow(Gtk.ListBoxRow):
    """One conversation: participant avatars and names, last message
    preview, relative time, unread dot and a Mark as Read / Delete
    menu."""

    __gtype_name__ = 'BabbleConversationRow'

    def __init__(self, page, conversation):
        super().__init__(activatable=True)
        self.page = page
        self.conversation = conversation
        self.add_css_class('conversation-row')

        box = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        self.set_child(box)

        accounts = conversation.get('accounts') or []
        self.avatars = Gtk.Box(valign=Gtk.Align.START)
        self.avatars.add_css_class('conversation-avatars')
        size = 40 if len(accounts) <= 1 else 32
        for account in accounts[:MAX_AVATARS] or [{}]:
            avatar = Adw.Avatar(
                size=size, text=_plain_name(account), show_initials=True,
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            AvatarCache.load(avatar, account)
            self.avatars.append(avatar)
        box.append(self.avatars)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       hexpand=True, valign=Gtk.Align.CENTER)
        top = Gtk.Box(spacing=6)
        self.names_label = Gtk.Label(
            xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
            single_line_mode=True)
        self.names_label.add_css_class('heading')
        top.append(self.names_label)
        self.time_label = Gtk.Label()
        self.time_label.add_css_class('caption')
        self.time_label.add_css_class('dimmed')
        top.append(self.time_label)
        text.append(top)
        self.preview_label = Gtk.Label(
            xalign=0.0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
            wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.preview_label.add_css_class('dimmed')
        text.append(self.preview_label)
        box.append(text)

        self.unread_dot = Gtk.Image(
            icon_name='media-record-symbolic', pixel_size=12,
            valign=Gtk.Align.CENTER,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.unread_dot.add_css_class('accent')
        box.append(self.unread_dot)

        actions = Gio.SimpleActionGroup()
        for name, handler in (('read', lambda *_a: self.mark_read()),
                              ('delete', lambda *_a: self.delete())):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', handler)
            actions.add_action(action)
        self.read_action = actions.lookup_action('read')
        menu = Gio.Menu()
        menu.append(_('Mark as Read'), 'conv.read')
        menu.append(_('Delete Conversation'), 'conv.delete')
        self.menu_button = Gtk.MenuButton(
            icon_name='view-more-symbolic', menu_model=menu,
            valign=Gtk.Align.CENTER)
        self.menu_button.add_css_class('flat')
        self.menu_button.set_tooltip_text(_('Conversation Options'))
        self.menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Conversation Options')])
        self.menu_button.insert_action_group('conv', actions)
        box.append(self.menu_button)

        self.update_content()

    def update_content(self):
        conversation = self.conversation
        names = participants_title(conversation)
        status = conversation.get('last_status')
        preview = last_status_preview(conversation)
        when = relative_time(status['created_at']) if status else ''
        self.names_label.set_text(names)
        self.names_label.set_tooltip_text(', '.join(
            '@' + a.get('acct', '') for a in conversation.get('accounts', [])))
        self.preview_label.set_text(preview)
        self.time_label.set_text(when)
        self.set_unread(bool(conversation.get('unread')))

    def set_unread(self, unread):
        self.conversation['unread'] = unread
        self.unread_dot.set_visible(unread)
        self.read_action.set_enabled(unread)
        if unread:
            self.names_label.remove_css_class('dimmed')
        else:
            self.names_label.add_css_class('dimmed')
        label = '%s: %s, %s' % (self.names_label.get_text(),
                                self.preview_label.get_text(),
                                self.time_label.get_text())
        if unread:
            label = _('Unread. %s') % label
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    # Actions

    def on_activate(self):
        """Mark as read, then open the thread of the last message."""
        if self.conversation.get('unread'):
            self.mark_read()
        status = self.conversation.get('last_status')
        if status is None:
            show_toast(self, _('This conversation has no messages'))
            return
        open_status(self, self.page.client, status)

    def mark_read(self):
        """POST /api/v1/conversations/:id/read."""
        client = self.page.client

        def done(_client, result, _data=None):
            try:
                updated = client.request_finish(result)
            except GLib.Error as error:
                report_error(self, _('Could not mark as read'), error)
                return
            if isinstance(updated, dict):
                self.conversation.update(updated)
            self.set_unread(False)
            self.page.emit_unread_changed()

        client.request_async(
            'POST', '/conversations/%s/read' % self.conversation['id'],
            callback=done)

    def delete(self):
        confirm(self, _('Delete Conversation?'),
                _('The conversation is removed from your inbox. Other '
                  'participants keep their copy.'),
                _('_Delete'), self._delete_confirmed)

    def _delete_confirmed(self):
        """DELETE /api/v1/conversations/:id."""
        client = self.page.client

        def done(_client, result, _data=None):
            try:
                client.request_finish(result)
            except GLib.Error as error:
                report_error(self, _('Could not delete the conversation'),
                             error)
                return
            self.page.remove_row(self)
            self.page.emit_unread_changed()
            show_toast(self, _('Conversation deleted'))

        client.request_async(
            'DELETE', '/conversations/%s' % self.conversation['id'],
            callback=done)


class ConversationsPage(PagedList):
    """Direct-message inbox with a New Direct Message header button."""

    __gtype_name__ = 'BabbleConversationsPage'
    __gsignals__ = {
        'unread-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, client, **kwargs):
        super().__init__(
            client, '/conversations', self._make_row,
            empty_title=_('No Conversations'),
            empty_icon='mail-unread-symbolic',
            empty_description=_('Direct messages will appear here'),
            **kwargs)
        button = Gtk.Button(icon_name='mail-message-new-symbolic')
        button.add_css_class('flat')
        button.set_tooltip_text(_('New Direct Message'))
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [_('New Direct Message')])
        button.connect('clicked', lambda _b: self.new_message())
        self.header_widgets = [button]

    def _make_row(self, conversation):
        return ConversationRow(self, conversation)

    def new_message(self):
        """Open the composer with the visibility set to Direct."""
        window = window_of(self)
        dialog = window.ensure_composer() if window is not None else None
        if dialog is None:
            return
        dialog.start_reply(None, '', 'direct')
        window.present_composer()

    def unread_count(self):
        return sum(1 for row in self.rows if row.conversation.get('unread'))

    def emit_unread_changed(self):
        self.emit('unread-changed', self.unread_count())

    def append_items(self, items):
        super().append_items(items)
        self.emit_unread_changed()

    # Streaming

    def on_stream_conversation(self, conversation):
        """A conversation changed on the ``direct`` stream: replace its
        row (if listed) and move it to the top."""
        if not isinstance(conversation, dict) or 'id' not in conversation:
            return
        for row in list(self.rows):
            if row.conversation.get('id') == conversation['id']:
                self.remove_row(row)
        self.prepend_row(ConversationRow(self, conversation))
        self.emit_unread_changed()

    def on_stream_delete(self, status_id):
        """A deleted status removes the conversation whose last message
        it was."""
        for row in list(self.rows):
            last = row.conversation.get('last_status') or {}
            if last.get('id') == status_id:
                self.remove_row(row)
        self.emit_unread_changed()
