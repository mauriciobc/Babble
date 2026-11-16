# SPDX-License-Identifier: GPL-3.0-or-later
"""One collection: its header, its members and their rows."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import accountrows, pagenav, pages  # noqa: E402
from .api import collections_api  # noqa: E402
from .collectionactions import CollectionActions  # noqa: E402
from .collectionbase import _is_member, format_date  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from src.collectionbase import remember_account  # noqa: E402



class CollectionPage(Adw.Bin):
    """GET /collections/:id: header, description and the members."""

    __gtype_name__ = 'BabbleCollectionPage'

    def __init__(self, client, collection_id, collection=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.collection_id = collection_id
        self.collection = collection
        self.stack = pages.StateStack(
            _('Loading'), _('Could Not Load'), self._on_retry)
        self.error_page = self.stack.error_page
        self.set_child(self.stack)
        self._body = None
        self.refresh()

    def _on_retry(self, _button):
        self.refresh()

    def refresh(self):
        token = self.stack.begin()
        collections_api.get_async(
            self.client, self.collection_id,
            callback=token.callback(self._on_loaded),
            cancellable=token.cancellable)

    def _on_loaded(self, client, result):
        try:
            data = collections_api.get_finish(client, result)
        except GLib.Error as error:
            self.stack.show_error(error)
            return
        self.collection = data.get('collection') or data
        accounts = data.get('accounts') or []
        for account in accounts:
            remember_account(self.client, account)
        self._show(accounts)

    def _show(self, accounts):
        collection = self.collection
        by_id = {account['id']: account for account in accounts}
        if self._body is not None:
            self.stack.remove(self._body)
        actions = self._actions = CollectionActions(
            self, self.client, collection, self.refresh, self._pop)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                         margin_top=18, margin_bottom=24, margin_start=12,
                         margin_end=12)

        title = Gtk.Label(label=collection.get('name', ''), wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          justify=Gtk.Justification.CENTER,
                          halign=Gtk.Align.CENTER, selectable=True)
        title.add_css_class('title-1')
        column.append(title)

        tag = collection.get('tag')
        if tag and tag.get('name'):
            name = tag['name'].lstrip('#')
            chip = Gtk.Button(label='#' + name, halign=Gtk.Align.CENTER)
            chip.add_css_class('pill')
            connect_weak(chip, 'clicked', self._on_tag_clicked, name)
            column.append(chip)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        description = collection.get('description')
        if description:
            label = Gtk.Label(label=description, wrap=True, xalign=0.0,
                              wrap_mode=Pango.WrapMode.WORD_CHAR,
                              selectable=True)
            details.append(label)
        facts = [_('Public') if collection.get('discoverable')
                 else _('Unlisted')]
        if collection.get('language'):
            facts.append(collection['language'].upper())
        if collection.get('updated_at'):
            facts.append(_('Updated %s') % format_date(
                collection['updated_at']))
        meta = Gtk.Label(label=' · '.join(facts), halign=Gtk.Align.CENTER)
        meta.add_css_class('dimmed')
        meta.add_css_class('caption')
        column.append(meta)

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        copy = Gtk.Button(label=_('Copy _URL'), use_underline=True)
        copy.add_css_class('pill')
        connect_weak(copy, 'clicked', self._on_action_clicked,
                           'copy_url')
        buttons.append(copy)
        if actions.is_owner:
            edit = Gtk.Button(label=_('_Edit'), use_underline=True)
            edit.add_css_class('pill')
            connect_weak(edit, 'clicked', self._on_action_clicked,
                               'edit')
            buttons.append(edit)
            delete = Gtk.Button(label=_('_Delete'), use_underline=True)
            delete.add_css_class('pill')
            delete.add_css_class('destructive-action')
            connect_weak(delete, 'clicked', self._on_action_clicked,
                               'delete')
            buttons.append(delete)
        elif actions.my_item is not None:
            leave = Gtk.Button(label=_('Remove _Myself'),
                               use_underline=True)
            leave.add_css_class('pill')
            leave.add_css_class('destructive-action')
            connect_weak(leave, 'clicked', self._on_action_clicked,
                               'remove_me')
            buttons.append(leave)
        if not actions.is_owner:
            report = Gtk.Button(label=_('_Report'), use_underline=True)
            report.add_css_class('pill')
            connect_weak(report, 'clicked', self._on_action_clicked,
                               'report')
            buttons.append(report)
        column.append(buttons)

        members = [item for item in collection.get('items') or []
                   if _is_member(item)]
        group = Adw.PreferencesGroup(title=ngettext(
            '%d Member', '%d Members', len(members)) % len(members))
        for item in members:
            account = by_id.get(item.get('account_id'))
            if account is None:
                continue
            group.add(self._member_row(account, item))
        details.append(group)

        owner = by_id.get(collection.get('account_id'))
        if owner is not None:
            created_by = Adw.PreferencesGroup(title=_('Created By'))
            created_by.add(accountrows.AccountRow(self.client, owner))
            details.append(created_by)

        if collection.get('sensitive'):
            warning = Adw.StatusPage(
                icon_name='dialog-warning-symbolic',
                title=_('Sensitive Collection'),
                description=_('The description and the accounts are '
                              'hidden behind a content warning.'))
            show = Gtk.Button(label=_('_Show Content'), use_underline=True,
                              halign=Gtk.Align.CENTER)
            show.add_css_class('pill')
            warning.set_child(show)
            details.set_visible(False)
            self._hidden = (details, warning)
            connect_weak(show, 'clicked', self._on_show_content)
            column.append(warning)
        column.append(details)

        clamp = Adw.Clamp(maximum_size=pages.COLUMN_WIDTH, child=column)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, child=clamp)
        self._body = scroller
        self.stack.add_named(scroller, 'content')
        self.stack.set_visible_child_name('content')

    def _member_row(self, account, item):
        row = accountrows.AccountRow(self.client, account)
        if item.get('state') == 'pending':
            badge = Gtk.Label(label=_('Pending'), valign=Gtk.Align.CENTER)
            badge.add_css_class('caption-heading')
            badge.add_css_class('warning')
            row.add_suffix(badge)
        return row

    def _on_tag_clicked(self, _button, name):
        self._open_tag(name)

    def _on_action_clicked(self, _button, method):
        getattr(self._actions, method)()

    def _on_show_content(self, _button):
        details, warning = self._hidden
        details.set_visible(True)
        warning.set_visible(False)

    def _open_tag(self, name):
        from .hashtags import push_hashtag
        window = pagenav.window_of(self)
        if window is not None:
            push_hashtag(self, self.client, name)

    def _pop(self):
        window = pagenav.window_of(self)
        if window is not None:
            window.navigation_view.pop()

class MemberRow(Adw.ActionRow):
    """An account in the editor: Add/Remove button, pending badge."""

    def __init__(self, account, **kwargs):
        super().__init__(activatable=False, use_markup=False, **kwargs)
        from .mediacache import AvatarCache
        name = accountrows.display_name(account)
        self.set_title(name)
        self.set_subtitle('@' + account.get('acct', ''))
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)

    def add_badge(self, text):
        badge = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
        badge.add_css_class('caption-heading')
        badge.add_css_class('warning')
        self.add_suffix(badge)
