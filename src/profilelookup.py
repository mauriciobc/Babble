# SPDX-License-Identifier: GPL-3.0-or-later
"""The profile page a remote handle resolves to.

A subclass of ``ProfilePage`` for a page that has no account record yet:
it fetches, shows the error state, and retries, then hands over to the
real page. ``profile`` imports this module only inside the two methods
that test for it, so the edge never closes into a cycle.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa: E402

from .api import accounts as api_accounts  # noqa: E402
from .api import search as api_search  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .profile import ProfilePage  # noqa: E402



class LookupProfilePage(ProfilePage):
    """Profile page that resolves an @user@instance handle first: the
    instance's account lookup, then a resolving search for remote
    accounts it has not met yet."""

    def __init__(self, client, acct, **kwargs):
        acct = (acct or '').lstrip('@')
        super().__init__(client, {'acct': acct, 'note': ''}, **kwargs)
        self.lookup()

    def lookup(self):
        self.set_stack_state('loading')
        api_accounts.lookup_async(self.client, self.account['acct'],
                                  self.on_lookup)

    def on_lookup(self, client, result, _user_data=None):
        try:
            account = api_accounts.lookup_finish(client, result)
        except GLib.Error as e:
            if e.code == 404 and self._signed_in():
                self.resolve()
                return
            self.show_lookup_error(e)
            return
        self.show_account(account)

    def resolve(self):
        """Search with resolve=true: makes the server fetch a remote
        account it does not know yet."""
        api_search.search_async(
            self.client, self.account['acct'], type='accounts',
            resolve='true', limit=5, callback=self.on_resolved)

    def on_resolved(self, client, result, _user_data=None):
        try:
            data = api_search.search_finish(client, result)
        except GLib.Error as e:
            self.show_lookup_error(e)
            return
        wanted = self.account['acct'].lower()
        found = data.get('accounts') or []
        account = next((a for a in found
                        if a.get('acct', '').lower() == wanted),
                       found[0] if found else None)
        if account is None:
            self.error_page.set_description(_('No account found for %s')
                                            % self._acct())
            self.set_stack_state('error')
            return
        self.show_account(account)

    def show_lookup_error(self, error):
        self.error_page.set_description(
            summarize_error_markup(error))
        self.set_stack_state('error')

    def show_account(self, account):
        self.account = account
        self.on_account_ready()
