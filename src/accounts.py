# SPDX-License-Identifier: GPL-3.0-or-later

"""Multi-account helpers: the stored account list, and the sidebar's
account switcher.

Accounts live in the ``accounts`` setting as one JSON object per
entry: the account entity returned by ``verify_credentials`` plus an
``instance`` key. ``active-account`` names the one in use. Tokens are
kept in the system keyring under (instance, account id).

A Mastodon account id is server-local -- two instances both hand out
``42`` -- so an account is identified by the pair, never by the id
alone. ``active-account`` therefore holds an :func:`account_key`, and a
value written before this pairing (a bare id) still resolves, by id,
when it is unambiguous.
"""

import json
import re
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from . import catchupstore  # noqa: E402

_SHORTCODE = re.compile(r':[A-Za-z0-9_]{2,}:')

# Separates the instance from the id inside an account key. Neither part
# can contain it: an instance is an https origin and an id is a number.
_KEY_SEPARATOR = '#'


def account_key(account):
    """``'<instance>#<id>'``: the name one account is stored and
    switched under."""
    return '%s%s%s' % (account.get('instance') or '', _KEY_SEPARATOR,
                       account.get('id') or '')


def _split_key(value):
    """``(instance, id)`` from an account key; *instance* is None when
    *value* is a bare id, the shape written before accounts were paired
    with their instance."""
    instance, separator, account_id = (value or '').partition(
        _KEY_SEPARATOR)
    return (instance, account_id) if separator else (None, value or '')


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


def find_account(settings, account_id, instance=None):
    """The stored account with this id, on *instance* when given.

    Without *instance* this matches on the id alone, which is only
    unambiguous when a single stored account carries it -- callers that
    can name the server pass it.
    """
    matches = [a for a in read_accounts(settings) if a['id'] == account_id]
    if instance is not None:
        matches = [a for a in matches if a.get('instance') == instance]
    return matches[0] if len(matches) == 1 else None


def find_account_by_key(settings, key):
    """The stored account named by an :func:`account_key`."""
    instance, account_id = _split_key(key)
    return find_account(settings, account_id, instance)


def active_account(settings):
    active = settings.get_string('active-account')
    return find_account_by_key(settings, active) if active else None


def same_account(account, other):
    """Whether two dicts name the same account: the id *and* the server.

    Ids alone collide -- every instance starts handing them out at 1.
    """
    return account['id'] == other['id'] \
        and account.get('instance') == other.get('instance')


def save_account(settings, account, activate=True):
    """Add or replace *account* (same id on the same server)."""
    entries = [json.dumps(other) for other in read_accounts(settings)
               if not same_account(other, account)]
    entries.append(json.dumps(account))
    settings.set_strv('accounts', entries)
    if activate:
        settings.set_string('active-account', account_key(account))


def forget_account(settings, account_id, instance=None):
    """Drop the entry *account_id* on *instance* (every stored account
    carrying that id when *instance* is None); when it was the active
    one the first remaining account (if any) takes over. Returns the
    account now active, or None."""
    def matches(account):
        return account['id'] == account_id and (
            instance is None or account.get('instance') == instance)

    stored = read_accounts(settings)
    keep = [a for a in stored if not matches(a)]
    settings.set_strv('accounts', [json.dumps(a) for a in keep])
    # The Catch Up snapshots hold the account's posts: they go with the
    # login.
    for account in stored:
        if matches(account):
            catchupstore.delete_account(account_key(account))
    # A bare id in active-account predates the pairing; compare on the
    # id alone when that is all it carries.
    active_instance, active_id = _split_key(
        settings.get_string('active-account'))
    if active_id == account_id and active_instance in (instance, None):
        settings.set_string('active-account',
                            account_key(keep[0]) if keep else '')
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
        from .mediacache import AvatarCache
        while (child := self._box.get_first_child()) is not None:
            self._box.remove(child)
        active = self.settings.get_string('active-account')
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('navigation-sidebar')
        listbox.update_property([Gtk.AccessibleProperty.LABEL],
                                [_('Accounts')])
        for account in read_accounts(self.settings):
            key = account_key(account)
            row = Gtk.ListBoxRow()
            row.account_key = key
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
            if key == active:
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
            remove.connect('clicked', self._on_remove_clicked, key)
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
        if row.account_key != self.settings.get_string('active-account'):
            self.emit('switch', row.account_key)

    def _on_remove_clicked(self, _button, key):
        self.popdown()
        self.emit('remove', key)
