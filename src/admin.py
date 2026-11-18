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

from .i18n import gettext as _, ngettext  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402
from .api import admin as admin_api  # noqa: E402
from . import pagenav  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .adminaccounts import AccountsPage  # noqa: E402
from .adminbase import (
    PERM_BLOCKS, PERM_FEDERATION, PERM_REPORTS, PERM_USERS, Sparkline,
    StatePage, error_text, needs_admin_scopes, permission_allows,
    role_permissions, send)  # noqa: E402
from .adminfederation import (
    DomainAllowsPage, EmailBlocksPage, FederationBlocksPage,
    IPBlocksPage)  # noqa: E402
from .adminreports import ReportsPage  # noqa: E402


# Helpers

# Small widgets

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
        connect_weak(self.period, 'notify::active-name', self._on_period)
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

        client = self.ctx.client
        measures = {'start_at': start, 'end_at': end,
                    'keys': [key for key, _t in self.MEASURES]}
        dimensions = {'start_at': start, 'end_at': end, 'limit': 8,
                      'keys': [key for key, _t, _l in self.DIMENSIONS]}
        send(client,
             lambda cb: admin_api.measures_async(client, measures,
                                                 callback=cb),
             admin_api.measures_finish,
             got('measures'), failed)
        send(client,
             lambda cb: admin_api.dimensions_async(client, dimensions,
                                                   callback=cb),
             admin_api.dimensions_finish,
             got('dimensions'), failed)

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


# Accounts

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
        connect_weak(self.sidebar_list, 'row-activated',
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

    def refresh_tool(self, tag):
        """Reload a tool page that has already been built.

        For a streaming event that changed what a tool would show -- a
        new report, say. A no-op before the dashboard is authorized, and
        for a tool that was never opened: a page builds when it is first
        shown and fetches for itself then.
        """
        if not self._authorized:
            return
        page = self._pages.get(tag)
        if page is None:
            return
        refresh = getattr(getattr(page, 'content', None), 'refresh', None)
        if refresh is not None:
            refresh()

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
            page = pagenav.make_nav_page(title, content, tag)
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
        send(self.client,
             lambda cb: admin_api.accounts_probe_async(self.client,
                                                       callback=cb),
             admin_api.accounts_probe_finish,
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
        connect_weak(button, 'clicked', self._on_authorize_clicked)
        page.set_child(button)
        content = pagenav.make_nav_page(_('Admin Dashboard'), page,
                                      'authorize')
        self.split.set_content(content)
        self.split.set_show_content(True)

    def _on_authorize_clicked(self, _button):
        window = pagenav.window_of(self) or self.parent
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
    window = pagenav.window_of(parent_widget)
    account = getattr(window, 'account', None)
    dialog = AdminDialog(parent_widget, client, account)
    dialog.present(parent_widget)
    dialog.authorize_scopes(None)
    return dialog
