# SPDX-License-Identifier: GPL-3.0-or-later
"""Accounts: the moderation list, the undo row and the account editor.

Tuba: Dialogs/Admin/Account.vala, Views/Admin/Accounts.vala,
Widgets/Admin/Account*.vala. Mastodon: docs.joinmastodon.org/methods/admin/accounts/.
"""

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from . import feedback, pages, uihelpers  # noqa: E402
from .adminbase import (CtxRef,
    account_handle,
    AccountSummary,
    account_name,
    avatar_for,
    count_badge_label,
    format_date,
    info_row,
    send,
    status_label)
from .adminreports import TakeActionDialog  # noqa: E402
from .api import admin as admin_api  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402



class AdminAccountRow(Adw.ActionRow):
    def __init__(self, client, item, on_open, **kwargs):
        super().__init__(activatable=True, use_markup=False,
                         subtitle_lines=0, **kwargs)
        self.item = item
        self.on_open = on_open
        account = item.get('account') or {}
        self.set_title(account_name(item))
        lines = [account_handle(item, client)]
        if item.get('ip'):
            lines.append(item['ip'])
        if item.get('email'):
            lines.append(item['email'])
        self.set_subtitle('\n'.join(lines))
        self.add_prefix(avatar_for(account, 48))
        self.add_suffix(count_badge_label(status_label(item)))

    def on_activate(self):
        self.on_open(self.item)

class AccountsPage(CtxRef, Adw.Bin):
    ORIGINS = (('', _('All Locations')), ('local', _('Local')),
               ('remote', _('Remote')))
    STATUSES = (('', _('All Statuses')), ('active', _('Active')),
                ('pending', _('Pending')), ('disabled', _('Disabled')),
                ('silenced', _('Limited')), ('suspended', _('Suspended')))

    def __init__(self, ctx, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self.search_button = Gtk.ToggleButton(
            icon_name='system-search-symbolic')
        self.search_button.add_css_class('flat')
        uihelpers.set_accessible_label(self.search_button, _('Search'))
        self.header_widgets = [self.search_button]

        self.origin = self._dropdown(self.ORIGINS, _('Location'))
        self.status = self._dropdown(self.STATUSES, _('Moderation Status'))
        self.entries = {}
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           column_spacing=6, row_spacing=6,
                           min_children_per_line=1, max_children_per_line=2,
                           homogeneous=True, margin_top=6, margin_bottom=6,
                           margin_start=12, margin_end=12)
        flow.append(self.origin)
        flow.append(self.status)
        for key, placeholder in (('username', _('Username')),
                                 ('display_name', _('Display Name')),
                                 ('email', _('E-mail')),
                                 ('ip', _('IP'))):
            entry = Gtk.Entry(placeholder_text=placeholder)
            entry.update_property([Gtk.AccessibleProperty.LABEL],
                                  [placeholder])
            connect_weak(entry, 'activate', self._on_search)
            self.entries[key] = entry
            flow.append(entry)
        revealer = Gtk.Revealer(child=flow)
        self.top_bar = revealer
        self.search_button.bind_property(
            'active', revealer, 'reveal-child',
            GObject.BindingFlags.SYNC_CREATE)
        connect_weak(self.origin, 'notify::selected', self._on_search)
        connect_weak(self.status, 'notify::selected', self._on_search)

        self.list = pages.PagedList(
            ctx.client, admin_api.list_path('accounts'), weak_method(self._make_row),
            empty_title=_('No Accounts'), empty_icon='system-users-symbolic')
        self.set_child(self.list)

    @staticmethod
    def _dropdown(options, tooltip):
        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _k, label in options]))
        uihelpers.set_accessible_label(dropdown, tooltip)
        dropdown.options = [key for key, _l in options]
        return dropdown

    def _params(self):
        params = {}
        origin = self.origin.options[self.origin.get_selected()]
        status = self.status.options[self.status.get_selected()]
        if origin:
            params['origin'] = origin
        if status:
            params['status'] = status
        for key, entry in self.entries.items():
            text = entry.get_text().strip()
            if text:
                params[key] = text
        return params

    def _on_search(self, *_args):
        self.list.params = self._params()
        self.list.refresh()

    def _make_row(self, item):
        return AdminAccountRow(self.ctx.client, item,
                               weak_method(self._open))

    def _open(self, item):
        dialog = AdminAccountDialog(self.ctx, item)
        dialog.connect('closed', self._on_dialog_closed)
        dialog.present(self.ctx)

    def _on_dialog_closed(self, dialog):
        if dialog.should_refresh:
            self.refresh()

    def refresh(self):
        self.list.refresh()

class UndoRow(Adw.ActionRow):
    """A moderation state with an Undo button (unsuspend, unsilence…)."""

    def __init__(self, dialog, title, endpoint, **kwargs):
        super().__init__(title=title, activatable=False, **kwargs)
        self._dialog = dialog.weak_ref()
        self.endpoint = endpoint
        self.button = Gtk.Button(label=_('_Undo'), use_underline=True,
                                 valign=Gtk.Align.CENTER)
        self.button.add_css_class('destructive-action')
        connect_weak(self.button, 'clicked', self._on_clicked)
        self.add_suffix(self.button)

    @property
    def dialog(self):
        return self._dialog()

    def _on_clicked(self, _button):
        feedback.confirm(self.dialog, _('Undo This Action?'), None,
                      _('_Undo'), self._undo)

    def _undo(self):
        self.button.set_sensitive(False)

        def ok(_data):
            self.set_visible(False)
            self.dialog.should_refresh = True

        def failed(error):
            self.button.set_sensitive(True)
            self.dialog.error(_('Could not undo the action'), error)

        send(self.dialog.client,
             lambda cb: admin_api.account_review_async(
                 self.dialog.client, self.dialog.account_id, self.endpoint,
                 callback=cb),
             admin_api.account_review_finish,
             ok, failed)

class AdminAccountDialog(feedback.ToastDialog):
    def __init__(self, ctx, item):
        super().__init__(account_handle(item, ctx.client), width=480,
                         height=600)
        self.ctx = ctx
        self.client = ctx.client
        self.account_id = item['id']
        self.handle = account_handle(item, ctx.client)
        self.should_refresh = False

        self.add_header_buttons(
            _('_Close'), _('Take _Action'), self._on_take_action,
            css='destructive-action')

        profile = Adw.PreferencesGroup()
        profile.add(AccountSummary(self.client, item))
        self.page.add(profile)

        info = Adw.PreferencesGroup()
        created = (item.get('account') or {}).get('created_at') \
            or item.get('created_at')
        if created:
            info.add(info_row(_('Joined'),
                              format_date(created, with_time=False)))
        role = item.get('role') or {}
        info.add(info_row(_('Role'), role.get('name')))
        email = info_row(_('E-mail'), item.get('email'))
        confirmed_text = _('Confirmed') if item.get('confirmed') \
            else _('Not Confirmed')
        confirmed = Gtk.Image(
            icon_name='object-select-symbolic' if item.get('confirmed')
            else 'dialog-warning-symbolic', valign=Gtk.Align.CENTER)
        confirmed.add_css_class('success' if item.get('confirmed')
                                else 'warning')
        uihelpers.set_accessible_label(confirmed, confirmed_text)
        email.add_suffix(confirmed)
        info.add(email)
        ips = [entry.get('ip') for entry in item.get('ips') or []
               if entry.get('ip')] or ([item['ip']] if item.get('ip')
                                       else [])
        info.add(info_row(_('IPs'), '\n'.join(ips)))
        self.page.add(info)

        actions = Adw.PreferencesGroup()
        if item.get('approved') is False:
            note = item.get('invite_request') or _('No reason given')
            self.approval_row = Adw.ActionRow(
                title=_('Awaiting Approval'), subtitle=note,
                subtitle_lines=0, use_markup=False, activatable=False)
            reject = uihelpers.icon_button('window-close-symbolic', _('Reject'))
            connect_weak(reject, 'clicked', self._on_review, 'reject',
                               _('Reject %s?'), _('_Reject'))
            reject.add_css_class('error')
            approve = uihelpers.icon_button('object-select-symbolic',
                                        _('Approve'))
            connect_weak(approve, 'clicked', self._on_review,
                               'approve', _('Approve %s?'), _('_Approve'))
            approve.add_css_class('success')
            self.approval_row.add_suffix(reject)
            self.approval_row.add_suffix(approve)
            actions.add(self.approval_row)
        for flag, title, endpoint in (
                ('suspended', _('Suspended'), 'unsuspend'),
                ('silenced', _('Limited'), 'unsilence'),
                ('disabled', _('Disabled'), 'enable'),
                ('sensitized', _('Marked as Sensitive'), 'unsensitive')):
            if item.get(flag):
                actions.add(UndoRow(self, title, endpoint))
        self.page.add(actions)

    def _on_review(self, _button, endpoint, heading, label):
        self._review(endpoint, heading, label)

    def _review(self, endpoint, heading, label):
        def go():
            self.approval_row.set_sensitive(False)

            def ok(_data):
                self.approval_row.set_visible(False)
                self.should_refresh = True

            def failed(error):
                self.approval_row.set_sensitive(True)
                self.error(_('Could not review the account'), error)

            send(self.client,
                 lambda cb: admin_api.account_review_async(
                     self.client, self.account_id, endpoint, callback=cb),
                 admin_api.account_review_finish,
                 ok, failed)

        feedback.confirm(self, heading % self.handle, None, label, go,
                      destructive=endpoint == 'reject')

    def _on_take_action(self, _button):
        dialog = TakeActionDialog(self.ctx, self.account_id, self.handle)
        dialog.connect('took-action', self._on_took_action)
        dialog.present(self)

    def _on_took_action(self, _dialog):
        self.should_refresh = True
        self.force_close()
