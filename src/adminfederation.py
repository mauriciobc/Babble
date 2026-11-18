# SPDX-License-Identifier: GPL-3.0-or-later
"""Federation: block/allow lists and IP rules.

Tuba: Dialogs/Admin/Federation.vala, Views/Admin/Federation.vala,
Views/Admin/EmailBlocks.vala, Views/Admin/IpBlocks.vala.
Mastodon: docs.joinmastodon.org/methods/admin/domain_blocks/ and
docs.joinmastodon.org/methods/admin/ip_blocks/.
"""

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from . import feedback, pages, uihelpers  # noqa: E402
from .adminbase import (AddBar,
    CtxRef,
    bool_str,
    delete_button,
    format_date,
    info_row,
    send)
from .api import admin as admin_api  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402



class RuleList(CtxRef, Adw.Bin):
    """List of block/allow rules with delete buttons and an add
    control: shared by the four rule pages."""

    def __init__(self, ctx, kind, make_row, empty_title, empty_icon,
                 empty_description=None, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self.kind = kind
        self.list = pages.PagedList(
            ctx.client, admin_api.list_path(kind), weak_method(make_row),
            empty_title=empty_title,
            empty_icon=empty_icon, empty_description=empty_description)
        self.set_child(self.list)

    def refresh(self):
        self.list.refresh()

    def delete(self, row, item_id, heading, label):
        def go():
            row.set_sensitive(False)

            def ok(_data):
                self.list.remove_row(row)

            def failed(error):
                row.set_sensitive(True)
                self.ctx.error(_('Could not remove the entry'), error)

            send(self.ctx.client,
                 lambda cb: admin_api.rule_delete_async(
                     self.ctx.client, self.kind, item_id, callback=cb),
                 admin_api.rule_delete_finish,
                 ok, failed)

        feedback.confirm(self.ctx, heading, None, label, go)

    def _on_delete(self, _button, row_ref, item_id, heading, label):
        # The row owns the button, so the handler holds it weakly.
        row = row_ref()
        if row is not None:
            self.delete(row, item_id, heading, label)

SEVERITIES = (
    ('silence', _('Limit')),
    ('suspend', _('Suspend')),
    ('noop', _('None')),
)

def severity_title(value):
    return dict(SEVERITIES).get(value, _('None'))

class FederationBlocksPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, 'domain_blocks', self._make_row,
            _('No Federation Blocks'), 'action-unavailable-symbolic',
            _('Servers you block or limit will show up here.'), **kwargs)
        self.add_button = uihelpers.icon_button(
            'list-add-symbolic', _('Add Federation Block'))
        connect_weak(self.add_button, 'clicked', self._on_add)
        self.header_widgets = [self.add_button]

    def _make_row(self, block):
        row = Adw.ExpanderRow(title=block['domain'],
                              subtitle=severity_title(block.get('severity')),
                              use_markup=False)
        details = [
            (_('Private comment'), block.get('private_comment')),
            (_('Public comment'), block.get('public_comment')),
        ]
        for title, value in details:
            row.add_row(info_row(title, value))
        flags = []
        if block.get('reject_media'):
            flags.append(_('Reject media files'))
        if block.get('reject_reports'):
            flags.append(_('Reject reports'))
        if block.get('obfuscate'):
            flags.append(_('Obfuscate domain name'))
        if flags:
            row.add_row(info_row(_('Restrictions'), ' · '.join(flags)))
        row.add_suffix(delete_button(
            _('Remove Block'), self._on_delete, row.weak_ref(),
            block['id'], _('Unblock %s?') % block['domain'], _('_Unblock')))
        return row

    def _on_add(self, _button):
        dialog = AddFederationBlockDialog(self.ctx)
        dialog.connect('added', lambda *_a: self.refresh())
        dialog.present(self.ctx)

class AddFederationBlockDialog(feedback.ToastDialog):
    __gsignals__ = {'added': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, ctx):
        super().__init__(_('Add Federation Block'), width=480, height=620)
        self.client = ctx.client
        self.save = self.add_header_buttons(
            _('_Cancel'), _('_Add'), self._on_save,
            css='suggested-action', sensitive=False)

        group = Adw.PreferencesGroup()
        self.domain = Adw.EntryRow(title=_('Domain'))
        connect_weak(self.domain, 'changed', self._on_domain_changed)
        group.add(self.domain)
        self.severity = Adw.ComboRow(
            title=_('Severity'),
            model=Gtk.StringList.new([label for _k, label in SEVERITIES]))
        connect_weak(self.severity, 'notify::selected', self._on_severity)
        group.add(self.severity)
        self.reject_media = Adw.SwitchRow(
            title=_('Reject Media Files'), active=True,
            subtitle=_('Removes locally stored media files and refuses to '
                       'download any in the future'))
        self.reject_reports = Adw.SwitchRow(
            title=_('Reject Reports'),
            subtitle=_('Ignore all reports coming from this domain'))
        self.obfuscate = Adw.SwitchRow(
            title=_('Obfuscate Domain Name'),
            subtitle=_('Partially obfuscate the domain name in the list if '
                       'advertising the list of domain limitations is '
                       'enabled'))
        for row in (self.reject_media, self.reject_reports, self.obfuscate):
            group.add(row)
        self.private_comment = Adw.EntryRow(title=_('Private Comment'))
        self.public_comment = Adw.EntryRow(title=_('Public Comment'))
        group.add(self.private_comment)
        group.add(self.public_comment)
        self.page.add(group)

    def _kind(self):
        return SEVERITIES[self.severity.get_selected()][0]

    def _on_domain_changed(self, row):
        self.save.set_sensitive(bool(row.get_text().strip()))

    def _on_severity(self, *_args):
        # A suspension already implies both.
        free = self._kind() != 'suspend'
        self.reject_media.set_sensitive(free)
        self.reject_reports.set_sensitive(free)

    def _on_save(self, _button):
        self.page.set_sensitive(False)
        free = self._kind() != 'suspend'
        form = {
            'domain': self.domain.get_text().strip(),
            'severity': self._kind(),
            'reject_media': bool_str(free and self.reject_media.get_active()),
            'reject_reports': bool_str(
                free and self.reject_reports.get_active()),
            'obfuscate': bool_str(self.obfuscate.get_active()),
            'private_comment': self.private_comment.get_text(),
            'public_comment': self.public_comment.get_text(),
        }

        def ok(_data):
            self.emit('added')
            self.force_close()

        def failed(error):
            self.page.set_sensitive(True)
            self.error(_('Could not add the block'), error)

        send(self.client,
             lambda cb: admin_api.rule_create_async(
                 self.client, 'domain_blocks', form, callback=cb),
             admin_api.rule_create_finish,
             ok, failed)

class DomainAllowsPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, 'domain_allows', self._make_row,
            _('No Allowed Servers'), 'object-select-symbolic',
            _('Servers allowed to federate show up here when the '
              'allowlist is in use.'), **kwargs)
        self.bar = AddBar(_('Allow federation with a server'), _('_Allow'),
                          weak_method(self._on_add))
        self.bar.button.set_use_underline(True)
        self.top_bar = self.bar

    def _make_row(self, allow):
        row = Adw.ActionRow(title=allow['domain'], use_markup=False)
        row.add_suffix(delete_button(
            _('Remove'), self._on_delete, row.weak_ref(), allow['id'],
            _('Remove Federation with %s?') % allow['domain'],
            _('_Remove')))
        return row

    def _on_add(self, domain):
        def go():
            def ok(data):
                self.bar.clear()
                self.refresh()

            send(self.ctx.client,
                 lambda cb: admin_api.rule_create_async(
                     self.ctx.client, 'domain_allows', {'domain': domain},
                     callback=cb),
                 admin_api.rule_create_finish,
                 ok,
                 lambda e: self.ctx.error(_('Could not allow the server'),
                                          e))

        feedback.confirm(self.ctx, _('Allow Federation with %s?') % domain,
                      None, _('_Allow'), go, destructive=False)

class EmailBlocksPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, 'email_domain_blocks', self._make_row,
            _('No Blocked E-mail Domains'), 'mail-unread-symbolic',
            _('Sign-ups from these e-mail domains are refused.'), **kwargs)
        self.bar = AddBar(_('Block an e-mail domain'), _('_Block'),
                          weak_method(self._on_add))
        self.bar.button.set_use_underline(True)
        self.top_bar = self.bar

    def _make_row(self, block):
        attempts = 0
        for day in block.get('history') or []:
            for key in ('accounts', 'uses'):
                try:
                    attempts += int(day.get(key) or 0)
                except (TypeError, ValueError):
                    pass
        row = Adw.ActionRow(
            title=block['domain'], use_markup=False,
            subtitle=ngettext('%d sign-up attempt', '%d sign-up attempts',
                              attempts) % attempts)
        row.add_suffix(delete_button(
            _('Remove'), self._on_delete, row.weak_ref(), block['id'],
            _('Unblock %s?') % block['domain'], _('_Unblock')))
        return row

    def _on_add(self, domain):
        def go():
            def ok(_data):
                self.bar.clear()
                self.refresh()

            send(self.ctx.client,
                 lambda cb: admin_api.rule_create_async(
                     self.ctx.client, 'email_domain_blocks',
                     {'domain': domain}, callback=cb),
                 admin_api.rule_create_finish,
                 ok,
                 lambda e: self.ctx.error(_('Could not block the domain'),
                                          e))

        feedback.confirm(
            self.ctx, _('Block %s?') % domain,
            _('This can be the domain name that shows up in the e-mail '
              'address or the MX record it uses. They will be checked '
              'upon sign-up.'), _('_Block'), go)

IP_SEVERITIES = (
    ('no_access', _('Blocked'), _('Block Access'),
     _('Block access to all resources')),
    ('sign_up_block', _('Blocked Sign-ups'), _('Block Sign-ups'),
     _('New sign-ups will not be possible')),
    ('sign_up_requires_approval', _('Limited Sign-ups'),
     _('Limit Sign-ups'), _('New sign-ups will require your approval')),
)

EXPIRATIONS = (
    (None, _('Never')),
    (86400, _('1 Day')),
    (1209600, _('2 Weeks')),
    (2630000, _('1 Month')),
    (15780000, _('6 Months')),
    (31536000, _('1 Year')),
    (94608000, _('3 Years')),
)

class IPBlocksPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, 'ip_blocks', self._make_row, _('No IP Rules'),
            'network-server-symbolic',
            _('Address ranges you restrict show up here.'), **kwargs)
        self.add_button = uihelpers.icon_button(
            'list-add-symbolic', _('Add IP Rule'))
        connect_weak(self.add_button, 'clicked', self._on_add)
        self.header_widgets = [self.add_button]

    def _make_row(self, block):
        severity = {key: title for key, title, _t, _d in IP_SEVERITIES}.get(
            block.get('severity'), block.get('severity') or '')
        subtitle = severity
        if block.get('comment'):
            subtitle += ' · ' + block['comment']
        if block.get('expires_at'):
            subtitle += '\n' + _('Expires %s') % format_date(
                block['expires_at'], with_time=False)
        row = Adw.ActionRow(title=block['ip'], subtitle=subtitle,
                            subtitle_lines=0, use_markup=False)
        row.add_suffix(delete_button(
            _('Remove'), self._on_delete, row.weak_ref(), block['id'],
            _('Remove the Rule for %s?') % block['ip'], _('_Remove')))
        return row

    def _on_add(self, _button):
        dialog = AddIPBlockDialog(self.ctx)
        dialog.connect('added', lambda *_a: self.refresh())
        dialog.present(self.ctx)

class AddIPBlockDialog(feedback.ToastDialog):
    __gsignals__ = {'added': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, ctx):
        super().__init__(_('Add IP Rule'), width=480, height=620)
        self.client = ctx.client
        self.save = self.add_header_buttons(
            _('_Cancel'), _('_Add'), self._on_save,
            css='suggested-action', sensitive=False)

        group = Adw.PreferencesGroup()
        self.ip = Adw.EntryRow(title='192.0.2.0/24')
        connect_weak(self.ip, 'changed', self._on_ip_changed)
        group.add(self.ip)
        self.comment = Adw.EntryRow(title=_('Comment'))
        group.add(self.comment)
        self.expiration = Adw.ComboRow(
            title=_('Expiration'),
            model=Gtk.StringList.new([label for _s, label in EXPIRATIONS]))
        self.expiration.set_selected(3)
        group.add(self.expiration)
        self.page.add(group)

        rules = Adw.PreferencesGroup(
            title=_('Rule'),
            description=_('Choose what will happen with requests from this '
                          'IP'))
        self.checks = {}
        for key, _label, title, description in IP_SEVERITIES:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.set_active(key == 'no_access')
            row = Adw.ActionRow(title=title, subtitle=description,
                                activatable_widget=check)
            row.add_prefix(check)
            rules.add(row)
            self.checks[key] = check
        uihelpers.join_radio(self.checks.values())
        self.page.add(rules)

    def _on_ip_changed(self, row):
        self.save.set_sensitive(bool(row.get_text().strip()))

    def _on_save(self, _button):
        self.page.set_sensitive(False)
        severity = next((key for key, check in self.checks.items()
                         if check.get_active()), 'no_access')
        form = {'ip': self.ip.get_text().strip(), 'severity': severity,
                'comment': self.comment.get_text()}
        seconds = EXPIRATIONS[self.expiration.get_selected()][0]
        if seconds is not None:
            form['expires_in'] = str(seconds)

        def ok(_data):
            self.emit('added')
            self.force_close()

        def failed(error):
            self.page.set_sensitive(True)
            self.error(_('Could not add the rule'), error)

        send(self.client,
             lambda cb: admin_api.rule_create_async(
                 self.client, 'ip_blocks', form, callback=cb),
             admin_api.rule_create_finish,
             ok, failed)
