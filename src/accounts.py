# SPDX-License-Identifier: GPL-3.0-or-later

"""Multi-account helpers: the stored account list, and the sidebar's
account switcher.

Accounts live in the ``accounts`` setting as one JSON object per
entry: the account entity returned by ``verify_credentials`` plus an
``instance`` key. ``active-account`` names the one in use. Tokens are
kept in the system keyring under (instance, account id).
"""

import json
import re
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, GObject, Gtk  # noqa: E402

_SHORTCODE = re.compile(r':[A-Za-z0-9_]{2,}:')


# Names and handles

def strip_shortcodes(text):
    """*text* without ``:custom_emoji:`` shortcodes, which a plain label
    would print verbatim."""
    return re.sub(r'\s{2,}', ' ', _SHORTCODE.sub('', text or '')).strip()


def plain_name(account):
    """The account's display name as plain text: shortcodes removed, the
    username when nothing readable is left."""
    name = strip_shortcodes(account.get('display_name') or '')
    return name or account.get('username') or account.get('acct') or '?'


def instance_host(account):
    instance = account.get('instance') or ''
    return urllib.parse.urlsplit(instance).netloc or instance


def full_handle(account):
    """``@user@host``: local accounts carry no domain in ``acct``."""
    acct = account.get('acct') or account.get('username') or ''
    if '@' not in acct and instance_host(account):
        acct = '%s@%s' % (acct, instance_host(account))
    return '@' + acct


# The stored list

def read_accounts(settings):
    accounts = []
    for entry in settings.get_strv('accounts'):
        try:
            account = json.loads(entry)
        except ValueError:
            continue
        if isinstance(account, dict) and account.get('id'):
            accounts.append(account)
    return accounts


def find_account(settings, account_id):
    for account in read_accounts(settings):
        if account['id'] == account_id:
            return account
    return None


def active_account(settings):
    active = settings.get_string('active-account')
    return find_account(settings, active) if active else None


def _same(account, other):
    return account['id'] == other['id'] \
        and account.get('instance') == other.get('instance')


def save_account(settings, account, activate=True):
    """Add or replace *account* (same id on the same server)."""
    entries = [json.dumps(other) for other in read_accounts(settings)
               if not _same(other, account)]
    entries.append(json.dumps(account))
    settings.set_strv('accounts', entries)
    if activate:
        settings.set_string('active-account', account['id'])


def forget_account(settings, account_id):
    """Drop the entry *account_id*; when it was the active one the
    first remaining account (if any) takes over. Returns the account now
    active, or None."""
    remaining = [a for a in read_accounts(settings)
                 if a['id'] != account_id]
    settings.set_strv('accounts', [json.dumps(a) for a in remaining])
    if settings.get_string('active-account') == account_id:
        settings.set_string('active-account',
                            remaining[0]['id'] if remaining else '')
    return active_account(settings)


def role_is_admin(account):
    """Whether the account's role carries any administration permission
    (every Mastodon permission bit except 'Invite users'), the
    condition for the admin dashboard."""
    role = account.get('role') or {}
    try:
        permissions = int(role.get('permissions') or 0)
    except (TypeError, ValueError):
        return False
    return bool(permissions & ~0x10000)


# Switcher

class AccountSwitcher(Gtk.Popover):
    """Popover listing every stored account. Activating a row switches
    to it, the trash button removes it, the last row adds another."""

    __gtype_name__ = 'BabbleAccountSwitcher'
    __gsignals__ = {
        'switch': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'remove': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'add': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, settings, **kwargs):
        super().__init__(**kwargs)
        self.settings = settings
        self.add_css_class('menu')
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._box.set_margin_top(6)
        self._box.set_margin_bottom(6)
        self._box.set_margin_start(6)
        self._box.set_margin_end(6)
        self._box.set_size_request(300, -1)
        scrolled = Gtk.ScrolledWindow(propagate_natural_height=True,
                                      max_content_height=420,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(self._box)
        self.set_child(scrolled)
        self.connect('show', lambda *_a: self.refresh())

    def refresh(self):
        from .timeline import AvatarCache
        while (child := self._box.get_first_child()) is not None:
            self._box.remove(child)
        active = self.settings.get_string('active-account')
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('navigation-sidebar')
        listbox.update_property([Gtk.AccessibleProperty.LABEL],
                                [_('Accounts')])
        self._rows = {}
        for account in read_accounts(self.settings):
            row = Gtk.ListBoxRow()
            row.account_id = account['id']
            line = Gtk.Box(spacing=12)
            line.set_margin_top(4)
            line.set_margin_bottom(4)
            avatar = Adw.Avatar(size=32, show_initials=True,
                                text=plain_name(account))
            AvatarCache.load(avatar, account)
            line.append(avatar)
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            valign=Gtk.Align.CENTER, hexpand=True)
            name = Gtk.Label(label=plain_name(account), xalign=0.0,
                             ellipsize=3)
            name.add_css_class('heading')
            handle = Gtk.Label(label=full_handle(account), xalign=0.0,
                               ellipsize=3)
            handle.add_css_class('caption')
            handle.add_css_class('dimmed')
            names.append(name)
            names.append(handle)
            line.append(names)
            if account['id'] == active:
                check = Gtk.Image(icon_name='object-select-symbolic',
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION)
                line.append(check)
                row.update_property(
                    [Gtk.AccessibleProperty.DESCRIPTION], [_('Current account')])
            remove = Gtk.Button(icon_name='user-trash-symbolic',
                                valign=Gtk.Align.CENTER,
                                tooltip_text=_('Remove Account'))
            remove.add_css_class('flat')
            remove.add_css_class('circular')
            remove.update_property([Gtk.AccessibleProperty.LABEL],
                                   [_('Remove %s') % full_handle(account)])
            remove.connect('clicked', self._on_remove_clicked,
                           account['id'])
            line.append(remove)
            row.set_child(line)
            listbox.append(row)
        listbox.connect('row-activated', self._on_row_activated)
        self._box.append(listbox)

        add = Gtk.Button()
        add.add_css_class('flat')
        content = Gtk.Box(spacing=12)
        content.set_margin_start(6)
        content.append(Gtk.Image(icon_name='list-add-symbolic',
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        label = Gtk.Label(label=_('_Add Account…'), use_underline=True,
                          xalign=0.0)
        content.append(label)
        add.set_child(content)
        add.connect('clicked', lambda _b: (self.popdown(),
                                           self.emit('add')))
        self._box.append(add)

    def _on_row_activated(self, _listbox, row):
        self.popdown()
        if row.account_id != self.settings.get_string('active-account'):
            self.emit('switch', row.account_id)

    def _on_remove_clicked(self, _button, account_id):
        self.popdown()
        self.emit('remove', account_id)
