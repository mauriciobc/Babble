# SPDX-License-Identifier: GPL-3.0-or-later

"""Account relationship lists: pending follow requests and the mutes,
blocks and blocked domains of the signed-in account."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, Gtk  # noqa: E402

from . import pages  # noqa: E402


def _account_label(account):
    return '@%s' % account.get('acct', '')


class FollowRequestsPage(pages.AccountListPage):
    """People asking to follow you (GET /follow_requests). Each row has
    Accept and Reject buttons (POST /follow_requests/:id/authorize|reject);
    the row leaves the list once the server agrees."""

    __gtype_name__ = 'BabbleFollowRequestsPage'

    def __init__(self, client, **kwargs):
        super().__init__(client, '/follow_requests',
                         empty_title=_('No Follow Requests'),
                         make_suffix=self._suffix_for, **kwargs)
        self.empty_page.set_icon_name('address-book-new-symbolic')
        self.empty_page.set_description(
            _('Requests appear here when your account is locked.'))

    def _suffix_for(self, account, row):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      valign=Gtk.Align.CENTER)
        reject = pages.icon_button(
            'window-close-symbolic',
            _('Reject Follow Request from %s') % _account_label(account),
            lambda: self.answer(row, account, 'reject'))
        reject.add_css_class('circular')
        accept = pages.icon_button(
            'object-select-symbolic',
            _('Accept Follow Request from %s') % _account_label(account),
            lambda: self.answer(row, account, 'authorize'), flat=False)
        accept.add_css_class('circular')
        accept.add_css_class('suggested-action')
        box.append(reject)
        box.append(accept)
        return box

    def answer(self, row, account, verb):
        accepted = verb == 'authorize'
        self.mutate_row(
            row, 'POST', '/follow_requests/%s/%s' % (account['id'], verb),
            lambda: pages.show_toast(
                self, (_('Accepted %s') if accepted else _('Rejected %s'))
                % _account_label(account)),
            _('Could not accept the request') if accepted
            else _('Could not reject the request'))


def _remove_button(label, callback):
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    button.connect('clicked', lambda *_a: callback())
    return button


class MutedAccountsPage(pages.AccountListPage):
    """GET /mutes with an Unmute button per row."""

    __gtype_name__ = 'BabbleMutedAccountsPage'

    def __init__(self, client, **kwargs):
        super().__init__(client, '/mutes',
                         empty_title=_('No Muted Accounts'),
                         make_suffix=self._suffix_for, **kwargs)
        self.empty_page.set_icon_name('audio-volume-muted-symbolic')

    def _suffix_for(self, account, row):
        return _remove_button(_('Unmute'),
                              lambda: self.unmute(row, account))

    def unmute(self, row, account):
        self.mutate_row(
            row, 'POST', '/accounts/%s/unmute' % account['id'],
            lambda: pages.show_toast(
                self, _('Unmuted %s') % _account_label(account)),
            _('Could not unmute %s') % _account_label(account))


class BlockedAccountsPage(pages.AccountListPage):
    """GET /blocks with an Unblock button per row."""

    __gtype_name__ = 'BabbleBlockedAccountsPage'

    def __init__(self, client, **kwargs):
        super().__init__(client, '/blocks',
                         empty_title=_('No Blocked Accounts'),
                         make_suffix=self._suffix_for, **kwargs)
        self.empty_page.set_icon_name('action-unavailable-symbolic')

    def _suffix_for(self, account, row):
        return _remove_button(_('Unblock'),
                              lambda: self.unblock(row, account))

    def unblock(self, row, account):
        self.mutate_row(
            row, 'POST', '/accounts/%s/unblock' % account['id'],
            lambda: pages.show_toast(
                self, _('Unblocked %s') % _account_label(account)),
            _('Could not unblock %s') % _account_label(account))


class BlockedDomainsPage(pages.PagedList):
    """GET /domain_blocks (a list of domain names) with an Unblock button
    per row (DELETE /domain_blocks?domain=)."""

    __gtype_name__ = 'BabbleBlockedDomainsPage'

    def __init__(self, client, **kwargs):
        super().__init__(client, '/domain_blocks', self._make_row,
                         empty_title=_('No Blocked Domains'),
                         empty_icon='network-server-symbolic', **kwargs)

    def _make_row(self, domain):
        row = Adw.ActionRow(title=domain, use_markup=False,
                            title_selectable=True)
        row.add_prefix(Gtk.Image(icon_name='network-server-symbolic'))
        row.add_suffix(_remove_button(
            _('Unblock'), lambda: self.unblock(row, domain)))
        return row

    def unblock(self, row, domain):
        self.mutate_row(
            row, 'DELETE', '/domain_blocks', params={'domain': domain},
            on_done=lambda: pages.show_toast(
                self, _('Unblocked %s') % domain),
            error_heading=_('Could not unblock %s') % domain)


class MutesBlocksPage(Adw.Bin):
    """Muted accounts, blocked accounts and blocked domains behind a view
    switcher. ``top_bar`` carries the switcher for :func:`pages.make_nav_page`."""

    __gtype_name__ = 'BabbleMutesBlocksPage'

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.view_stack = Adw.ViewStack()
        for name, title, icon, page in (
                ('mutes', _('Muted'), 'audio-volume-muted-symbolic',
                 MutedAccountsPage(client)),
                ('blocks', _('Blocked'), 'action-unavailable-symbolic',
                 BlockedAccountsPage(client)),
                ('domains', _('Domains'), 'network-server-symbolic',
                 BlockedDomainsPage(client))):
            self.view_stack.add_titled(page, name, title)
            self.view_stack.get_page(page).set_icon_name(icon)
        self.set_child(self.view_stack)

        self.switcher = Adw.ViewSwitcher(
            stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               margin_top=6, margin_bottom=6)
        self.switcher.set_hexpand(True)
        self.top_bar.append(self.switcher)

    def refresh(self):
        page = self.view_stack.get_visible_child()
        if page is not None:
            page.refresh()
