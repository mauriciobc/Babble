# SPDX-License-Identifier: GPL-3.0-or-later
"""Reports: the list, the report detail and the take-action dialog.

Tuba: Dialogs/Admin/Report.vala, Views/Admin/Reports.vala,
Widgets/Admin/Report*.vala. Mastodon: docs.joinmastodon.org/methods/admin/reports/.
"""

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from . import feedback, pagenav, pages, uihelpers  # noqa: E402
from .adminbase import (AccountSummary,
    CtxRef,
    account_handle,
    avatar_for,
    bool_str,
    count_badge_label,
    format_date,
    info_row,
    send)
from .api import admin as admin_api  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402



CATEGORIES = (
    ('spam', _('Spam')),
    ('violation', _('Rule Violation')),
    ('legal', _('Legal')),
    ('other', _('Other')),
)

def category_title(category):
    return dict(CATEGORIES).get(category, _('Other'))

class ReportRow(Adw.ActionRow):
    def __init__(self, client, report, on_open, **kwargs):
        super().__init__(activatable=True, use_markup=True,
                         subtitle_lines=0, **kwargs)
        self.report = report
        self.on_open = on_open
        target = report.get('target_account') or {}
        reporter = report.get('account') or {}
        self.set_title(GLib.markup_escape_text(
            account_handle(target, client)))
        if report.get('action_taken'):
            last_title = _('Action taken by')
            last = report.get('action_taken_by_account')
        else:
            last_title = _('Assigned to')
            last = report.get('assigned_account')
        statuses = report.get('statuses') or []
        esc = GLib.markup_escape_text
        self.set_subtitle(
            '<b>%s:</b> %s\n<b>%s:</b> %s\n<b>%s:</b> %s' % (
                _('Reported by'), esc(account_handle(reporter, client)),
                _('Reported posts'), len(statuses),
                last_title,
                esc(account_handle(last, client)) if last
                else _('Nobody')))
        self.add_prefix(avatar_for(target.get('account') or {}, 48))
        label = _('Resolved') if report.get('action_taken') \
            else category_title(report.get('category'))
        self.add_suffix(count_badge_label(label))

    def on_activate(self):
        self.on_open(self.report)

class ReportsPage(CtxRef, Adw.Bin):
    def __init__(self, ctx, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self.resolved_button = Gtk.ToggleButton(
            icon_name='object-select-symbolic')
        self.resolved_button.add_css_class('flat')
        uihelpers.set_accessible_label(self.resolved_button,
                                   _('Show Resolved Reports'))
        connect_weak(self.resolved_button, 'toggled', self._on_toggled)
        self.header_widgets = [self.resolved_button]
        self.list = pages.PagedList(
            ctx.client, admin_api.list_path('reports'), weak_method(self._make_row),
            empty_title=_('No Reports'), empty_icon='object-select-symbolic',
            empty_description=_('Nothing needs your attention.'))
        self.set_child(self.list)

    def _make_row(self, report):
        return ReportRow(self.ctx.client, report, weak_method(self._open))

    def _on_toggled(self, button):
        self.list.params = {'resolved': 'true'} if button.get_active() \
            else {}
        self.list.refresh()

    def _open(self, report):
        dialog = ReportDialog(self.ctx, report)
        dialog.connect('closed', self._on_dialog_closed)
        dialog.present(self.ctx)

    def _on_dialog_closed(self, dialog):
        if dialog.should_refresh:
            self.refresh()

    def refresh(self):
        self.list.refresh()

class ReportDialog(feedback.ToastDialog):
    """Report detail: reported account, who reported, category, rules,
    posts, and the assign / resolve / take-action controls."""

    def __init__(self, ctx, report):
        super().__init__(_('Report'), width=520, height=680)
        self.ctx = ctx
        self.client = ctx.client
        self.report = report
        self.report_id = report['id']
        self.should_refresh = False
        self._rule_checks = {}
        self._had_rules = bool(report.get('rules'))
        target = report.get('target_account') or {}
        resolved = bool(report.get('action_taken'))

        self.resolve_button = Gtk.Button(
            label=_('_Reopen') if resolved else _('_Resolve'),
            use_underline=True)
        self.resolve_button.add_css_class('suggested-action')
        connect_weak(
            self.resolve_button, 'clicked',
            self._on_reopen if resolved else self._on_resolve)
        self.header.pack_start(self.resolve_button)
        # Take Action opens a moderation dialog; it is not the other half
        # of this page's one decision, so it does not sit beside Resolve
        # in the header bar competing with it. It goes with the account
        # summary it acts on, as a plain button: the moderation choices
        # it leads to are destructive in their own right and each is
        # confirmed there, which is where that emphasis belongs.
        self.action_button = Gtk.Button(label=_('Take _Action'),
                                        use_underline=True,
                                        halign=Gtk.Align.CENTER,
                                        sensitive=not resolved)
        connect_weak(self.action_button, 'clicked',
                           self._on_take_action)
        self.action_button.add_css_class('pill')

        profile = Adw.PreferencesGroup()
        profile.add(AccountSummary(self.client, target))
        profile.set_header_suffix(self.action_button)
        self.page.add(profile)

        info = Adw.PreferencesGroup()
        created = (target.get('account') or {}).get('created_at')
        if created:
            info.add(info_row(_('Joined'),
                              format_date(created, with_time=False)))
        info.add(info_row(_('Reported on'),
                          format_date(report.get('created_at'))))
        info.add(info_row(_('Reported by'),
                          account_handle(report.get('account') or {},
                                         self.client)))
        info.add(info_row(_('Status'),
                          _('Resolved') if resolved else _('Unresolved')))
        if report.get('forwarded'):
            info.add(info_row(_('Forwarded'), _('Yes')))
        if report.get('comment'):
            info.add(info_row(_('Comment'), report['comment']))
        taker = report.get('action_taken_by_account')
        if taker:
            info.add(info_row(_('Action taken by'),
                              account_handle(taker, self.client)))
        else:
            self.assign_row = AssignedRow(self, report)
            info.add(self.assign_row)
        self.page.add(info)

        category = report.get('category') or 'other'
        self.category_group = Adw.PreferencesGroup(
            title=_('Category'),
            description=_('The reason this account and/or content was '
                          'reported will be cited in communication with '
                          'the reported account'))
        first = None
        self.category_checks = {}
        for key, title in CATEGORIES:
            check = Gtk.CheckButton(group=first, valign=Gtk.Align.CENTER)
            first = first or check
            check.set_active(key == category)
            row = Adw.ActionRow(title=title, activatable_widget=check,
                                sensitive=not resolved)
            row.add_prefix(check)
            self.category_group.add(row)
            self.category_checks[key] = check
        self.page.add(self.category_group)
        self.rules_group = Adw.PreferencesGroup(
            title=_('Violated Rules'), visible=category == 'violation')
        self.page.add(self.rules_group)
        self._build_rules(resolved)
        for check in self.category_checks.values():
            connect_weak(check, 'toggled', self._on_category_toggled)

        statuses = report.get('statuses') or []
        if statuses:
            group = Adw.PreferencesGroup(title=_('Reported Posts'))
            for status in statuses:
                group.add(self._status_row(status))
            self.page.add(group)

    def _status_row(self, status):
        from .timelinedata import StatusItem
        from .statusrow import StatusRow
        row = StatusRow()
        row.bind(StatusItem(status), self.client)
        row.set_focusable(False)
        # Moderators read the post here; acting on it is not their job.
        for name in ('actions', '_overflow'):
            widget = getattr(row, name, None)
            if widget is not None:
                widget.set_visible(False)
        profile_button = getattr(row, 'profile_button', None)
        if profile_button is not None:
            profile_button.set_sensitive(False)
        return row

    def _build_rules(self, resolved):
        selected = {rule.get('id') for rule in self.report.get('rules') or []}

        def populate(rules):
            for rule in rules:
                check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                check.set_active(rule.get('id') in selected)
                row = Adw.ActionRow(title=rule.get('text', ''),
                                    use_markup=False, title_lines=0,
                                    activatable_widget=check,
                                    sensitive=not resolved)
                row.add_prefix(check)
                self.rules_group.add(row)
                self._rule_checks[rule.get('id')] = check
                connect_weak(check, 'toggled',
                                   self._on_category_toggled)

        info = getattr(self.client, 'instance_info', None)
        if info and info.get('rules') is not None:
            populate(info['rules'])
            return

        def loaded(_client, result, _data=None):
            try:
                fresh = self.client.instance_info_finish(result)
            except GLib.Error:
                fresh = None
            rules = (fresh or {}).get('rules') or self.report.get('rules') \
                or []
            populate(rules)

        self.client.instance_info_async(callback=loaded)

    # Category and rules

    def _selected_category(self):
        for key, check in self.category_checks.items():
            if check.get_active():
                return key
        return None

    def _on_category_toggled(self, check):
        if not check.get_active() and any(
                check is radio for radio in self.category_checks.values()):
            return
        category = self._selected_category()
        if category is None:
            return
        self.rules_group.set_visible(category == 'violation')
        rule_ids = [rule_id for rule_id, box in self._rule_checks.items()
                    if box.get_active()] if category == 'violation' else []
        self.should_refresh = True

        def put(body):
            send(self.client,
                 lambda cb: admin_api.report_update_async(
                     self.client, self.report_id, body, callback=cb),
                 admin_api.report_update_finish,
                 on_error=lambda e: self.error(_('Could not update the '
                                                 'report'), e))

        # Mastodon refuses a category change while rules are attached:
        # clear them first.
        if category != 'violation' and self._had_rules:
            put({'category': 'violation', 'rule_ids': []})
            self._had_rules = False
        put({'category': category, 'rule_ids': rule_ids})
        self._had_rules = bool(rule_ids)

    # Assign, resolve, act

    def _transition(self, path, heading, body, label):
        def go():
            self.resolve_button.set_sensitive(False)

            def ok(_data):
                self.should_refresh = True
                self.force_close()

            def failed(error):
                self.resolve_button.set_sensitive(True)
                self.error(_('Could not update the report'), error)

            send(self.client,
                 lambda cb: admin_api.report_transition_async(
                     self.client, self.report_id, path, callback=cb),
                 admin_api.report_transition_finish,
                 ok, failed)

        feedback.confirm(self, heading, body, label, go, destructive=False)

    def _on_resolve(self, _button):
        self._transition('resolve', _('Mark This Report as Resolved?'),
                         None, _('_Resolve'))

    def _on_reopen(self, _button):
        self._transition('reopen', _('Reopen This Report?'), None,
                         _('_Reopen'))

    def _on_take_action(self, _button):
        target = self.report.get('target_account') or {}
        dialog = TakeActionDialog(
            self.ctx, target['id'], account_handle(target, self.client),
            self.report_id)
        dialog.connect('took-action', self._on_took_action)
        dialog.present(self)

    def _on_took_action(self, _dialog):
        self.should_refresh = True
        self.force_close()

class AssignedRow(Adw.ActionRow):
    """'Assigned to' with an Assign / Unassign button for yourself."""

    def __init__(self, dialog, report, **kwargs):
        super().__init__(title=_('Assigned to'), use_markup=False,
                         subtitle_selectable=True, **kwargs)
        self.add_css_class('property')
        self._dialog = dialog.weak_ref()
        self.report_id = report['id']
        self.button = Gtk.Button(valign=Gtk.Align.CENTER)
        connect_weak(self.button, 'clicked', self._on_clicked)
        self.add_suffix(self.button)
        self._update(report.get('assigned_account'))

    @property
    def dialog(self):
        return self._dialog()

    def _me(self):
        window = pagenav.window_of(self.dialog.ctx.parent)
        return (getattr(window, 'account', None) or {}).get('id')

    def _update(self, assigned):
        self.assigned = assigned
        if assigned is None:
            self.set_subtitle(_('Nobody'))
            self.button.set_visible(True)
            self.button.set_label(_('Assign'))
            self.button.remove_css_class('destructive-action')
            self.button.add_css_class('suggested-action')
            return
        self.set_subtitle(account_handle(assigned, self.dialog.client))
        mine = self._me() in (None, (assigned.get('account') or {}).get('id'),
                              assigned.get('id'))
        self.button.set_visible(mine)
        self.button.set_label(_('Unassign'))
        self.button.remove_css_class('suggested-action')
        self.button.add_css_class('destructive-action')

    def _on_clicked(self, _button):
        endpoint = 'unassign' if self.assigned else 'assign_to_self'
        self.button.set_sensitive(False)

        def ok(data):
            self.button.set_sensitive(True)
            self.dialog.should_refresh = True
            self._update(data.get('assigned_account'))

        def failed(error):
            self.button.set_sensitive(True)
            self.dialog.error(_('Could not change the assignment'), error)

        send(self.dialog.client,
             lambda cb: admin_api.report_transition_async(
                 self.dialog.client, self.report_id, endpoint, callback=cb),
             admin_api.report_transition_finish,
             ok, failed)

class TakeActionDialog(feedback.ToastDialog):
    """POST /admin/accounts/:id/action."""

    __gsignals__ = {'took-action': (GObject.SignalFlags.RUN_FIRST, None, ())}

    ACTIONS = (
        ('none', _('Warning'),
         _('Send a warning to the user without triggering any other '
           'action')),
        ('disable', _('Freeze'),
         _('Prevent the user from using their account, but do not delete '
           'or hide their content')),
        ('sensitive', _('Sensitive'),
         _("Force all this user's media attachments to be flagged as "
           'sensitive')),
        ('silence', _('Limit'),
         _('Prevent the user from posting with public visibility and hide '
           'their posts and notifications from people not following them. '
           'Closes all reports against this account')),
        ('suspend', _('Suspend'),
         _('Prevent any interaction from or to this account and delete '
           'its content. Revertible within 30 days. Closes all reports '
           'against this account')),
    )

    def __init__(self, ctx, account_id, handle, report_id=None):
        super().__init__(_('Take Action'), width=480, height=620)
        self.client = ctx.client
        self.account_id = account_id
        self.handle = handle
        self.report_id = report_id
        self.submit = self.add_header_buttons(
            _('_Cancel'), _('_Submit'), self._on_submit,
            css='destructive-action')

        group = Adw.PreferencesGroup()
        first = None
        self.checks = {}
        for key, title, description in self.ACTIONS:
            check = Gtk.CheckButton(group=first, valign=Gtk.Align.CENTER)
            first = first or check
            check.set_active(key == 'none')
            row = Adw.ActionRow(title=title, subtitle=description,
                                subtitle_lines=0, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
            self.checks[key] = check
        self.page.add(group)

        details = Adw.PreferencesGroup()
        self.comment = Adw.EntryRow(title=_('Comment'))
        details.add(self.comment)
        self.email = Adw.SwitchRow(
            title=_('Notify the User by E-mail'),
            subtitle=_('The user will receive an explanation of what '
                       'happened with their account'),
            active=True)
        details.add(self.email)
        self.page.add(details)

    def _kind(self):
        for key, check in self.checks.items():
            if check.get_active():
                return key
        return 'none'

    def _on_submit(self, _button):
        feedback.confirm(self, _('Take Action Against %s?') % self.handle,
                      None, _('_Take Action'), self._submit)

    def _submit(self):
        self.page.set_sensitive(False)
        form = {'type': self._kind(), 'text': self.comment.get_text(),
                'send_email_notification': bool_str(self.email.get_active())}
        if self.report_id is not None:
            form['report_id'] = self.report_id

        def ok(_data):
            self.emit('took-action')
            self.force_close()

        def failed(error):
            self.page.set_sensitive(True)
            self.error(_('Could not take action'), error)

        send(self.client,
             lambda cb: admin_api.account_action_async(
                 self.client, self.account_id, form, callback=cb),
             admin_api.account_action_finish,
             ok, failed)
