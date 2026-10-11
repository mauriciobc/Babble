# SPDX-License-Identifier: GPL-3.0-or-later

"""Admin dashboard: a large dialog with a sidebar of moderation tools.

Pages: Dashboard (measures and dimensions), Reports (with the report
detail and the take-action dialog), Accounts, Federation blocklist,
Federation allowlist, Blocked e-mail domains and IP rules.

Tuba: Dialogs/Admin.vala, Dialogs/Admin/*, Views/Admin/**,
Widgets/Admin/*.vala. Mastodon: docs.joinmastodon.org/methods/admin/.
The calls need the ``admin:read``/``admin:write`` OAuth scopes and a
role with the matching permission.

Those scopes are deliberately absent from the login the app asks for by
default: Mastodon shows every requested scope on its consent screen, and
"all administrative functions" is not something to ask of every user.
So the dashboard probes for them on open and, when the token lacks them,
offers one extra authorization that asks for exactly those. Every page
turns a 403 into an error state instead of crashing.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .pages import weak_method  # noqa: E402
from .mastodon import summarize_error  # noqa: E402

# Role permission bits (Mastodon UserRole::FLAGS)
PERM_ADMINISTRATOR = 1
PERM_REPORTS = 16
PERM_FEDERATION = 32
PERM_BLOCKS = 128
PERM_USERS = 1024


# Helpers

def _admin_line(error):
    """One readable line for a GLib.Error from an admin call."""
    code = getattr(error, 'code', 0)
    message = summarize_error(getattr(error, 'message', None) or str(error))
    if code in (401, 403):
        return _('%s. This login is not authorized for moderation on this '
                 'server.') % message.rstrip('.')
    return message


def error_text(error):
    """*error* as one plain-text line, for a ``use_markup=False`` toast."""
    return _admin_line(error)


def error_text_markup(error):
    """*error* as one line escaped for a markup-rendering widget.

    A status page parses its description as markup, so a bare ``&`` in
    a server body fails the parse and blanks the label.
    """
    return GLib.markup_escape_text(_admin_line(error))


def format_date(iso, fmt='%x %X'):
    value = GLib.DateTime.new_from_iso8601(iso, None) if iso else None
    return value.to_local().format(fmt) if value else ''


def account_handle(admin_account, client):
    """'@name@domain' for an Admin::Account, local accounts get the
    instance's host."""
    username = admin_account.get('username') \
        or admin_account.get('account', {}).get('username', '')
    domain = admin_account.get('domain')
    if not domain:
        domain = urlsplit(getattr(client, 'instance', '') or '').netloc
    return '@%s@%s' % (username, domain) if domain else '@' + username


def account_name(admin_account):
    account = admin_account.get('account') or {}
    return account.get('display_name') or admin_account.get('username') \
        or account.get('acct', '')


def send(client, method, path, on_ok=None, on_error=None, **kwargs):
    """One request; *on_ok(data)* or *on_error(GLib.Error)*."""

    def finished(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error as error:
            if on_error is not None:
                on_error(error)
            return
        if on_ok is not None:
            on_ok(data)

    return client.request_async(method, path, callback=finished, **kwargs)


def bool_str(value):
    return 'true' if value else 'false'


class CtxRef:
    """Mixin holding ``ctx`` (the AdminDialog) weakly: the pages live
    inside it, so a strong reference would cycle through C."""

    @property
    def ctx(self):
        return self._ctx()

    @ctx.setter
    def ctx(self, ctx):
        self._ctx = ctx.weak_ref()


def permission_allows(permissions, mask):
    """*permissions* is the role bitmask, None when unknown (the server
    decides then)."""
    if permissions is None:
        return True
    return bool(permissions & PERM_ADMINISTRATOR or permissions & mask)


def role_permissions(account):
    role = (account or {}).get('role') or {}
    value = role.get('permissions')
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def needs_admin_scopes(error):
    """Whether *error* is the token missing the admin scopes.

    The account's role is checked before the dashboard opens, so a 403
    raised inside it is not "this user is not a moderator" but "this
    token was never authorized for ``admin:read``/``admin:write``" --
    the one cause this app can act on, by asking the user for them.
    """
    return getattr(error, 'code', 0) in (401, 403)


# Small widgets

class Sparkline(Gtk.DrawingArea):
    """Tiny line chart of a number series, drawn in the text colour."""

    __gtype_name__ = 'BabbleAdminSparkline'

    def __init__(self, values, **kwargs):
        super().__init__(content_width=96, content_height=28,
                         valign=Gtk.Align.CENTER, **kwargs)
        self.values = list(values)
        # A plain function: a bound method would cycle through C.
        self.set_draw_func(_draw_sparkline)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [_('Trend over the selected period')])

    def _draw(self, area, cr, width, height):
        if len(self.values) < 2:
            return
        low, high = min(self.values), max(self.values)
        span = (high - low) or 1
        color = area.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.8)
        cr.set_line_width(1.5)
        step = width / (len(self.values) - 1)
        for index, value in enumerate(self.values):
            x = index * step
            y = height - 3 - (value - low) / span * (height - 6)
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()


def _draw_sparkline(area, cr, width, height):
    area._draw(area, cr, width, height)


class StatePage(CtxRef, Adw.Bin):
    """Loading / error / content states for pages that fetch by hand."""

    def __init__(self, ctx, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self._content = None
        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self.stack.add_named(pages.make_loading_page(_('Loading')), 'loading')
        self.error_page = pages.make_error_page(
            _('Could Not Load'), self._on_retry)
        self.stack.add_named(self.error_page, 'error')

    def _on_retry(self, _button):
        self.refresh()

    def show_loading(self):
        self.stack.set_visible_child_name('loading')

    def show_error(self, error):
        self.error_page.set_description(error_text_markup(error))
        self.stack.set_visible_child_name('error')

    def show_content(self, widget):
        if self._content is not None:
            self.stack.remove(self._content)
        self._content = widget
        self.stack.add_named(widget, 'content')
        self.stack.set_visible_child_name('content')

    def refresh(self):
        raise NotImplementedError


class AdminDialogBase(Adw.Dialog):
    """Dialog with a header bar, a preferences page and its own toasts
    (the window's overlay would sit behind a modal dialog)."""

    def __init__(self, title, width=460, height=560, **kwargs):
        super().__init__(title=title, content_width=width,
                         content_height=height, **kwargs)
        self.page = Adw.PreferencesPage()
        self.toasts = Adw.ToastOverlay(child=self.page)
        self.header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(self.header)
        self.set_child(toolbar)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False,
                                        timeout=5))

    def error(self, heading, error):
        self.toast('%s: %s' % (heading, error_text(error)))

    def add_header_buttons(self, cancel_label, action_label, callback,
                           css=None, sensitive=True):
        """Cancel button that closes the dialog plus an end-side action
        button calling *callback*; returns the action button."""
        cancel = Gtk.Button(label=cancel_label, use_underline=True)
        pages.connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        button = Gtk.Button(label=action_label, use_underline=True,
                            sensitive=sensitive)
        if css:
            button.add_css_class(css)
        pages.connect_weak(button, 'clicked', callback)
        self.header.pack_end(button)
        return button

    def _on_cancel(self, _button):
        self.force_close()


def count_badge_label(text, css=None):
    label = Gtk.Label(label=text, xalign=1.0, wrap=True, hexpand=True,
                      valign=Gtk.Align.CENTER,
                      wrap_mode=Pango.WrapMode.WORD_CHAR)
    label.add_css_class('dimmed')
    label.add_css_class('caption')
    if css:
        label.add_css_class(css)
    return label


def delete_button(tooltip, method, *extra):
    """Trash button calling the bound *method* ``(button, *extra)``,
    held weakly."""
    button = pages.icon_button('user-trash-symbolic', tooltip)
    pages.connect_weak(button, 'clicked', method, *extra)
    button.add_css_class('error')
    return button


def avatar_for(account, size=48):
    from .timeline import AvatarCache
    name = account.get('display_name') or account.get('username') or '?'
    avatar = Adw.Avatar(size=size, text=name, show_initials=True)
    AvatarCache.load(avatar, account)
    return avatar


def status_label(item):
    """Moderation state of an Admin::Account, as a short word."""
    if item.get('suspended'):
        return _('Suspended')
    if item.get('silenced'):
        return _('Limited')
    if item.get('disabled'):
        return _('Disabled')
    if item.get('approved') is False:
        return _('Waiting Approval')
    return _('No Limits')


class AccountSummary(Adw.ActionRow):
    """Avatar, name and handle of an Admin::Account (not clickable)."""

    def __init__(self, client, admin_account, **kwargs):
        super().__init__(activatable=False, use_markup=False,
                         subtitle_selectable=True, **kwargs)
        account = admin_account.get('account') or {}
        self.set_title(account_name(admin_account))
        self.set_subtitle(account_handle(admin_account, client))
        self.add_prefix(avatar_for(account, 48))


def info_row(title, value, selectable=True):
    row = Adw.ActionRow(title=title, subtitle=value or _('None'),
                        use_markup=False, subtitle_selectable=selectable)
    row.add_css_class('property')
    return row


class AddBar(Gtk.Box):
    """Entry plus button on top of a list (allow/block a domain)."""

    def __init__(self, placeholder, button_label, on_submit):
        super().__init__(spacing=6, margin_top=6, margin_bottom=6,
                         margin_start=12, margin_end=12)
        self.on_submit = on_submit
        self.entry = Gtk.Entry(placeholder_text=placeholder, hexpand=True,
                               input_purpose=Gtk.InputPurpose.URL)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   [placeholder])
        self.button = Gtk.Button(label=button_label, sensitive=False)
        self.button.add_css_class('suggested-action')
        self.append(self.entry)
        self.append(self.button)
        pages.connect_weak(self.entry, 'notify::text', self._on_changed)
        pages.connect_weak(self.entry, 'activate', self._on_submit)
        pages.connect_weak(self.button, 'clicked', self._on_submit)
        self.set_halign(Gtk.Align.CENTER)
        self.set_size_request(420, -1)

    def _on_changed(self, *_args):
        self.button.set_sensitive(bool(self.entry.get_text().strip()))

    def _on_submit(self, *_args):
        value = self.entry.get_text().strip()
        if value:
            self.on_submit(value)

    def clear(self):
        self.entry.set_text('')


# Dashboard

class DashboardPage(StatePage):
    """Measures (new users, interactions…) and dimensions (languages,
    servers…) over the last 7 or 30 days."""

    MEASURES = (
        ('new_users', _('New Users')),
        ('active_users', _('Active Users')),
        ('interactions', _('Interactions')),
        ('opened_reports', _('Reports Opened')),
        ('resolved_reports', _('Reports Resolved')),
    )
    DIMENSIONS = (
        ('sources', _('Sign-up Sources'), 8),
        ('languages', _('Top Active Languages'), 8),
        ('servers', _('Top Active Servers'), 8),
        ('software_versions', _('Software'), 4),
        ('space_usage', _('Space Usage'), 4),
    )

    def __init__(self, ctx, **kwargs):
        super().__init__(ctx, **kwargs)
        self.days = 30
        self._generation = 0
        self.period = Adw.ToggleGroup()
        self.period.add(Adw.Toggle(name='7', label=_('7 Days')))
        self.period.add(Adw.Toggle(name='30', label=_('30 Days')))
        self.period.set_active_name('30')
        pages.connect_weak(self.period, 'notify::active-name', self._on_period)
        self.header_widgets = [self.period]
        self.refresh()

    def _on_period(self, group, _pspec):
        name = group.get_active_name()
        if name and int(name) != self.days:
            self.days = int(name)
            self.refresh()

    def _window(self):
        end = GLib.DateTime.new_now_local()
        start = end.add_days(-(self.days - 1))
        return start.format('%F'), end.format('%F')

    def refresh(self):
        self._generation += 1
        generation = self._generation
        self.show_loading()
        start, end = self._window()
        state = {'pending': 2, 'measures': None, 'dimensions': None,
                 'errors': []}

        def settle():
            state['pending'] -= 1
            if state['pending'] or generation != self._generation:
                return
            if len(state['errors']) == 2:
                self.show_error(state['errors'][0])
                return
            for error in state['errors']:
                self.ctx.error(_('Could not load some statistics'), error)
            self.show_content(self._build(state['measures'] or [],
                                          state['dimensions'] or []))

        def got(key):
            def ok(data):
                state[key] = data
                settle()
            return ok

        def failed(error):
            state['errors'].append(error)
            settle()

        send(self.ctx.client, 'POST', '/admin/measures',
             got('measures'), failed,
             json_body={'start_at': start, 'end_at': end,
                        'keys': [key for key, _t in self.MEASURES]})
        send(self.ctx.client, 'POST', '/admin/dimensions',
             got('dimensions'), failed,
             json_body={'start_at': start, 'end_at': end, 'limit': 8,
                        'keys': [key for key, _t, _l in self.DIMENSIONS]})

    def _build(self, measures, dimensions):
        page = Adw.PreferencesPage()
        by_key = {item.get('key'): item for item in measures}
        stats = Adw.PreferencesGroup(
            title=_('Stats'),
            description=ngettext('Last %d day', 'Last %d days', self.days)
            % self.days)
        for key, title in self.MEASURES:
            item = by_key.get(key)
            if item is None:
                continue
            stats.add(self._measure_row(title, item))
        added = 0
        if by_key:
            page.add(stats)
            added += 1
        dim_by_key = {item.get('key'): item for item in dimensions}
        for key, title, limit in self.DIMENSIONS:
            entries = (dim_by_key.get(key) or {}).get('data') or []
            if not entries:
                continue
            group = Adw.PreferencesGroup(title=title)
            for entry in entries[:limit]:
                group.add(Adw.ActionRow(
                    title=str(entry.get('human_key') or entry.get('key')
                              or ''),
                    subtitle=str(entry.get('human_value')
                                 or entry.get('value') or ''),
                    use_markup=False, subtitle_selectable=True))
            page.add(group)
            added += 1
        if not added:
            return Adw.StatusPage(
                icon_name='view-grid-symbolic', title=_('No Statistics'),
                description=_('The server returned no numbers for this '
                              'period.'))
        return page

    @staticmethod
    def _number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _measure_row(self, title, item):
        total = item.get('human_value') or item.get('total')
        row = Adw.ActionRow(title=title, use_markup=False,
                            subtitle_selectable=True)
        subtitle = str(total if total is not None else '')
        now = self._number(item.get('total'))
        before = self._number(item.get('previous_total'))
        if now is not None and before:
            change = (now - before) / before * 100
            subtitle += '  ·  ' + _('%+.0f%% vs. previous period') % change
        row.set_subtitle(subtitle)
        row.add_css_class('property')
        series = [self._number(point.get('value')) or 0
                  for point in item.get('data') or []]
        if len(series) > 1:
            row.add_suffix(Sparkline(series))
        return row


# Reports

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
            icon_name='object-select-symbolic',
            tooltip_text=_('Show Resolved Reports'))
        self.resolved_button.add_css_class('flat')
        self.resolved_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Show Resolved Reports')])
        pages.connect_weak(self.resolved_button, 'toggled', self._on_toggled)
        self.header_widgets = [self.resolved_button]
        self.list = pages.PagedList(
            ctx.client, '/admin/reports', weak_method(self._make_row),
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


class ReportDialog(AdminDialogBase):
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
        pages.connect_weak(
            self.resolve_button, 'clicked',
            self._on_reopen if resolved else self._on_resolve)
        self.header.pack_start(self.resolve_button)
        self.action_button = Gtk.Button(label=_('Take _Action'),
                                        use_underline=True,
                                        sensitive=not resolved)
        self.action_button.add_css_class('destructive-action')
        pages.connect_weak(self.action_button, 'clicked',
                           self._on_take_action)
        self.header.pack_end(self.action_button)

        profile = Adw.PreferencesGroup()
        profile.add(AccountSummary(self.client, target))
        self.page.add(profile)

        info = Adw.PreferencesGroup()
        created = (target.get('account') or {}).get('created_at')
        if created:
            info.add(info_row(_('Joined'), format_date(created, '%x')))
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
            pages.connect_weak(check, 'toggled', self._on_category_toggled)

        statuses = report.get('statuses') or []
        if statuses:
            group = Adw.PreferencesGroup(title=_('Reported Posts'))
            for status in statuses:
                group.add(self._status_row(status))
            self.page.add(group)

    def _status_row(self, status):
        from .timeline import StatusItem, StatusRow
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
                pages.connect_weak(check, 'toggled',
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
            send(self.client, 'PUT', '/admin/reports/%s' % self.report_id,
                 on_error=lambda e: self.error(_('Could not update the '
                                                 'report'), e),
                 json_body=body)

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

            send(self.client, 'POST',
                 '/admin/reports/%s/%s' % (self.report_id, path), ok,
                 failed)

        pages.confirm(self, heading, body, label, go, destructive=False)

    def _on_resolve(self, _button):
        self._transition('resolve', _('Mark this Report as Resolved?'),
                         None, _('_Resolve'))

    def _on_reopen(self, _button):
        self._transition('reopen', _('Reopen this Report?'), None,
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
        pages.connect_weak(self.button, 'clicked', self._on_clicked)
        self.add_suffix(self.button)
        self._update(report.get('assigned_account'))

    @property
    def dialog(self):
        return self._dialog()

    def _me(self):
        window = pages.window_of(self.dialog.ctx.parent)
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

        send(self.dialog.client, 'POST',
             '/admin/reports/%s/%s' % (self.report_id, endpoint), ok, failed)


class TakeActionDialog(AdminDialogBase):
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
        pages.confirm(self, _('Take Action Against %s?') % self.handle,
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

        send(self.client, 'POST',
             '/admin/accounts/%s/action' % self.account_id, ok, failed,
             form=form)


# Accounts

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
            icon_name='system-search-symbolic', tooltip_text=_('Search'))
        self.search_button.add_css_class('flat')
        self.search_button.update_property([Gtk.AccessibleProperty.LABEL],
                                           [_('Search')])
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
                                 ('email', _('E-mail')), ('ip', 'IP')):
            entry = Gtk.Entry(placeholder_text=placeholder)
            entry.update_property([Gtk.AccessibleProperty.LABEL],
                                  [placeholder])
            pages.connect_weak(entry, 'activate', self._on_search)
            self.entries[key] = entry
            flow.append(entry)
        revealer = Gtk.Revealer(child=flow)
        self.top_bar = revealer
        self.search_button.bind_property(
            'active', revealer, 'reveal-child',
            GObject.BindingFlags.SYNC_CREATE)
        pages.connect_weak(self.origin, 'notify::selected', self._on_search)
        pages.connect_weak(self.status, 'notify::selected', self._on_search)

        self.list = pages.PagedList(
            ctx.client, '/api/v2/admin/accounts', weak_method(self._make_row),
            empty_title=_('No Accounts'), empty_icon='system-users-symbolic')
        self.set_child(self.list)

    @staticmethod
    def _dropdown(options, tooltip):
        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _k, label in options]),
            tooltip_text=tooltip)
        dropdown.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
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
        pages.connect_weak(self.button, 'clicked', self._on_clicked)
        self.add_suffix(self.button)

    @property
    def dialog(self):
        return self._dialog()

    def _on_clicked(self, _button):
        pages.confirm(self.dialog, _('Undo this Action?'), None,
                      _('_Undo'), self._undo)

    def _undo(self):
        self.button.set_sensitive(False)

        def ok(_data):
            self.set_visible(False)
            self.dialog.should_refresh = True

        def failed(error):
            self.button.set_sensitive(True)
            self.dialog.error(_('Could not undo the action'), error)

        send(self.dialog.client, 'POST',
             '/admin/accounts/%s/%s' % (self.dialog.account_id,
                                        self.endpoint), ok, failed)


class AdminAccountDialog(AdminDialogBase):
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
            info.add(info_row(_('Joined'), format_date(created, '%x')))
        role = item.get('role') or {}
        info.add(info_row(_('Role'), role.get('name')))
        email = info_row(_('E-mail'), item.get('email'))
        confirmed = Gtk.Image(
            icon_name='object-select-symbolic' if item.get('confirmed')
            else 'dialog-warning-symbolic',
            tooltip_text=_('Confirmed') if item.get('confirmed')
            else _('Not Confirmed'), valign=Gtk.Align.CENTER)
        confirmed.add_css_class('success' if item.get('confirmed')
                                else 'warning')
        confirmed.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Confirmed') if item.get('confirmed')
             else _('Not Confirmed')])
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
            reject = pages.icon_button('window-close-symbolic', _('Reject'))
            pages.connect_weak(reject, 'clicked', self._on_review, 'reject',
                               _('Reject %s?'), _('_Reject'))
            reject.add_css_class('error')
            approve = pages.icon_button('object-select-symbolic',
                                        _('Approve'))
            pages.connect_weak(approve, 'clicked', self._on_review,
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

            send(self.client, 'POST',
                 '/admin/accounts/%s/%s' % (self.account_id, endpoint),
                 ok, failed)

        pages.confirm(self, heading % self.handle, None, label, go,
                      destructive=endpoint == 'reject')

    def _on_take_action(self, _button):
        dialog = TakeActionDialog(self.ctx, self.account_id, self.handle)
        dialog.connect('took-action', self._on_took_action)
        dialog.present(self)

    def _on_took_action(self, _dialog):
        self.should_refresh = True
        self.force_close()


# Federation, e-mail domains and IP rules

class RuleList(CtxRef, Adw.Bin):
    """List of block/allow rules with delete buttons and an add
    control: shared by the four rule pages."""

    def __init__(self, ctx, path, make_row, empty_title, empty_icon,
                 empty_description=None, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self.path = path
        self.list = pages.PagedList(
            ctx.client, path, weak_method(make_row), empty_title=empty_title,
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

            send(self.ctx.client, 'DELETE', '%s/%s' % (self.path, item_id),
                 ok, failed)

        pages.confirm(self.ctx, heading, None, label, go)

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
            ctx, '/admin/domain_blocks', self._make_row,
            _('No Federation Blocks'), 'action-unavailable-symbolic',
            _('Servers you block or limit will show up here.'), **kwargs)
        self.add_button = pages.icon_button(
            'list-add-symbolic', _('Add Federation Block'))
        pages.connect_weak(self.add_button, 'clicked', self._on_add)
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


class AddFederationBlockDialog(AdminDialogBase):
    __gsignals__ = {'added': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, ctx):
        super().__init__(_('Add Federation Block'), width=480, height=620)
        self.client = ctx.client
        self.save = self.add_header_buttons(
            _('_Cancel'), _('_Add'), self._on_save,
            css='suggested-action', sensitive=False)

        group = Adw.PreferencesGroup()
        self.domain = Adw.EntryRow(title=_('Domain'))
        pages.connect_weak(self.domain, 'changed', self._on_domain_changed)
        group.add(self.domain)
        self.severity = Adw.ComboRow(
            title=_('Severity'),
            model=Gtk.StringList.new([label for _k, label in SEVERITIES]))
        pages.connect_weak(self.severity, 'notify::selected', self._on_severity)
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

        send(self.client, 'POST', '/admin/domain_blocks', ok, failed,
             form=form)


class DomainAllowsPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, '/admin/domain_allows', self._make_row,
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

            send(self.ctx.client, 'POST', '/admin/domain_allows', ok,
                 lambda e: self.ctx.error(_('Could not allow the server'),
                                          e),
                 form={'domain': domain})

        pages.confirm(self.ctx, _('Allow Federation with %s?') % domain,
                      None, _('_Allow'), go, destructive=False)


class EmailBlocksPage(RuleList):
    def __init__(self, ctx, **kwargs):
        super().__init__(
            ctx, '/admin/email_domain_blocks', self._make_row,
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

            send(self.ctx.client, 'POST', '/admin/email_domain_blocks', ok,
                 lambda e: self.ctx.error(_('Could not block the domain'),
                                          e),
                 form={'domain': domain})

        pages.confirm(
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
            ctx, '/admin/ip_blocks', self._make_row, _('No IP Rules'),
            'network-server-symbolic',
            _('Address ranges you restrict show up here.'), **kwargs)
        self.add_button = pages.icon_button(
            'list-add-symbolic', _('Add IP Rule'))
        pages.connect_weak(self.add_button, 'clicked', self._on_add)
        self.header_widgets = [self.add_button]

    def _make_row(self, block):
        severity = {key: title for key, title, _t, _d in IP_SEVERITIES}.get(
            block.get('severity'), block.get('severity') or '')
        subtitle = severity
        if block.get('comment'):
            subtitle += ' · ' + block['comment']
        if block.get('expires_at'):
            subtitle += '\n' + _('Expires %s') % format_date(
                block['expires_at'], '%x')
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


class AddIPBlockDialog(AdminDialogBase):
    __gsignals__ = {'added': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, ctx):
        super().__init__(_('Add IP Rule'), width=480, height=620)
        self.client = ctx.client
        self.save = self.add_header_buttons(
            _('_Cancel'), _('_Add'), self._on_save,
            css='suggested-action', sensitive=False)

        group = Adw.PreferencesGroup()
        self.ip = Adw.EntryRow(title='192.0.2.0/24')
        pages.connect_weak(self.ip, 'changed', self._on_ip_changed)
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
        first = None
        self.checks = {}
        for key, _label, title, description in IP_SEVERITIES:
            check = Gtk.CheckButton(group=first, valign=Gtk.Align.CENTER)
            first = first or check
            check.set_active(key == 'no_access')
            row = Adw.ActionRow(title=title, subtitle=description,
                                activatable_widget=check)
            row.add_prefix(check)
            rules.add(row)
            self.checks[key] = check
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

        send(self.client, 'POST', '/admin/ip_blocks', ok, failed, form=form)


# The dialog

class AdminDialog(Adw.Dialog):
    """Sidebar of tools on the left, the chosen tool on the right."""

    # (tag, title, icon, page class, permission bit)
    ENTRIES = (
        ('dashboard', _('Dashboard'), 'view-grid-symbolic', DashboardPage,
         0),
        ('reports', _('Reports'), 'dialog-warning-symbolic', ReportsPage,
         PERM_REPORTS),
        ('accounts', _('Accounts'), 'system-users-symbolic', AccountsPage,
         PERM_USERS),
        ('domain-blocks', _('Federation Blocklist'),
         'action-unavailable-symbolic', FederationBlocksPage,
         PERM_FEDERATION),
        ('domain-allows', _('Federation Allowlist'),
         'object-select-symbolic', DomainAllowsPage, PERM_FEDERATION),
        ('email-blocks', _('Blocked E-mail Domains'),
         'mail-unread-symbolic', EmailBlocksPage, PERM_BLOCKS),
        ('ip-blocks', _('IP Rules'), 'network-server-symbolic',
         IPBlocksPage, PERM_BLOCKS),
    )

    def __init__(self, parent, client, account=None):
        super().__init__(title=_('Admin Dashboard'), content_width=980,
                         content_height=700)
        self.parent = parent
        self.client = client
        self.account = account
        self.toasts = Adw.ToastOverlay()
        self._pages = {}
        self._authorized = False
        permissions = role_permissions(account)

        self.split = Adw.NavigationSplitView(
            min_sidebar_width=230, max_sidebar_width=300,
            sidebar_width_fraction=0.3)
        self.toasts.set_child(self.split)
        # 340, not 360: a window at its declared 360 px minimum has 350
        # available for content, and a larger request overflows it.
        bin_ = Adw.BreakpointBin(width_request=340, height_request=300,
                                 child=self.toasts)
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 560sp'))
        breakpoint_.add_setter(self.split, 'collapsed', True)
        bin_.add_breakpoint(breakpoint_)
        self.set_child(bin_)

        self.sidebar_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class('navigation-sidebar')
        pages.connect_weak(self.sidebar_list, 'row-activated',
                           self._on_row_activated)
        self._rows = {}
        for entry in self.ENTRIES:
            tag, title, icon, _cls, mask = entry
            if mask and not permission_allows(permissions, mask):
                continue
            row = Adw.ActionRow(title=title, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.entry = entry
            self.sidebar_list.append(row)
            self._rows[tag] = row
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True,
                                      child=self.sidebar_list)
        toolbar.set_content(scroller)
        self.split.set_sidebar(Adw.NavigationPage(
            title=_('Admin Dashboard'), child=toolbar))
        self.content_page = Adw.NavigationPage(title=_('Admin Dashboard'))
        self.split.set_content(self.content_page)
        self.split.set_show_content(True)
        self.show_tool('dashboard')

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False,
                                        timeout=5))

    def error(self, heading, error):
        self.toast('%s: %s' % (heading, error_text(error)))

    def _on_row_activated(self, _list, row):
        self.show_tool(row.entry[0])

    def show_tool(self, tag):
        row = self._rows.get(tag)
        if row is None:
            return
        self.sidebar_list.select_row(row)
        page = self._pages.get(tag)
        if page is None:
            _tag, title, _icon, cls, _mask = row.entry
            content = cls(self)
            page = pages.make_nav_page(title, content, tag)
            page.content = content
            self._pages[tag] = page
        self.split.set_content(page)
        self.split.set_show_content(True)
        return page

    # Moderation scopes

    def authorize_scopes(self, on_done):
        """Confirm the token may use the admin endpoints.

        A role permission and an OAuth scope are two different gates, and
        only the second is this app's to grant. The cheapest admin call
        is the probe: it answers 200 when both are in place and 403 when
        the token never asked for the scopes, which is what the extra
        authorization below fixes.
        """
        send(self.client, 'GET', '/admin/accounts',
             params={'limit': 1},
             on_ok=lambda _data: self._on_scopes_ok(on_done),
             on_error=lambda error: self._on_scopes_error(error, on_done))

    def _on_scopes_ok(self, on_done):
        self._authorized = True
        self.show_tool('dashboard')
        if on_done is not None:
            on_done(True)

    def _on_scopes_error(self, error, on_done):
        if not needs_admin_scopes(error):
            self.toast(error_text(error))
            if on_done is not None:
                on_done(False)
            return
        self._authorized = False
        self.show_authorization_needed()
        if on_done is not None:
            on_done(False)

    def show_authorization_needed(self):
        """The page shown when the token lacks the admin scopes."""
        page = Adw.StatusPage(
            icon_name='dialog-password-symbolic',
            title=_('Moderation Access Needed'),
            description=_('This login was authorized for reading and '
                          'posting, but not for moderation. Babble needs '
                          'permission to use this server’s moderation '
                          'tools.'))
        button = Gtk.Button(label=_('_Authorize Moderation'),
                            use_underline=True, halign=Gtk.Align.CENTER)
        button.add_css_class('pill')
        button.add_css_class('suggested-action')
        pages.connect_weak(button, 'clicked', self._on_authorize_clicked)
        page.set_child(button)
        content = pages.make_nav_page(_('Admin Dashboard'), page,
                                      'authorize')
        self.split.set_content(content)
        self.split.set_show_content(True)

    def _on_authorize_clicked(self, _button):
        window = pages.window_of(self) or self.parent
        authorize = getattr(window, 'authorize_admin', None)
        if authorize is None:
            return
        authorize(lambda granted: self._on_reauthorized(granted))

    def _on_reauthorized(self, granted):
        if granted:
            self._pages.clear()
            self.authorize_scopes(None)


def present_admin(parent_widget, client):
    """Open the admin dashboard over *parent_widget*'s window.

    The tool pages only build once the token is known to carry the admin
    scopes; until then the dashboard shows what it needs and one button
    that asks the server for them.
    """
    window = pages.window_of(parent_widget)
    account = getattr(window, 'account', None)
    dialog = AdminDialog(parent_widget, client, account)
    dialog.present(parent_widget)
    dialog.authorize_scopes(None)
    return dialog
