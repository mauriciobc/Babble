# SPDX-License-Identifier: GPL-3.0-or-later

"""Account rows: a person as an avatar, a name and a handle, and the
paged list of them any endpoint returning an Account array can fill."""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gtk  # noqa: E402

from . import pagenav  # noqa: E402
from .pages import PagedList  # noqa: E402


def display_name(account):
    return account.get('display_name') or account.get('username') \
        or account.get('acct', '')


class AccountRow(Adw.ActionRow):
    """Avatar, display name (custom emoji stripped to text) and @handle;
    activating opens the profile. Extra widgets go in with
    ``add_suffix``."""

    __gtype_name__ = 'BabbleAccountRow'

    def __init__(self, client, account, subtitle=None, **kwargs):
        super().__init__(activatable=True, use_markup=False, **kwargs)
        from .mediacache import AvatarCache
        self.client = client
        self.account = account
        name = re.sub(r':[A-Za-z0-9_+\-]+:', '', display_name(account)) \
            .strip() or account.get('acct', '')
        self.set_title(name)
        self.set_subtitle(subtitle if subtitle is not None
                          else '@' + account.get('acct', ''))
        self.set_title_lines(1)
        self.set_subtitle_lines(1)
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)
        self.avatar = avatar
        if account.get('invalid_handle'):
            # Mastodon 4.7: the handle cannot be verified (a remote actor
            # that changed handles); the acct shown is server-unique.
            warning = Gtk.Image(icon_name='dialog-warning-symbolic')
            warning.add_css_class('dimmed')
            warning.set_tooltip_text(
                _('This account’s handle could not be verified'))
            self.add_suffix(warning)

    def on_activate(self):
        pagenav.open_profile(self, self.client, self.account)


class AccountListPage(PagedList):
    """Accounts from any endpoint returning an Account array
    (followers, favourited_by, mutes, list members, …)."""

    __gtype_name__ = 'BabbleAccountListPage'

    def __init__(self, client, path, params=None, empty_title=None,
                 make_suffix=None, extract=None, **kwargs):
        self._make_suffix = make_suffix
        super().__init__(client, path, self._make_account_row,
                         params=params,
                         empty_title=empty_title or _('No Accounts'),
                         empty_icon='system-users-symbolic',
                         extract=extract, **kwargs)

    def _make_account_row(self, account):
        row = AccountRow(self.client, account)
        if self._make_suffix is not None:
            suffix = self._make_suffix(account, row)
            if suffix is not None:
                row.add_suffix(suffix)
        return row
