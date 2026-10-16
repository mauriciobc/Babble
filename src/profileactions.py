# SPDX-License-Identifier: GPL-3.0-or-later
"""Relationship commands for a profile: the action group, the overflow
menu, and the busy / toast / undo / 404-fallback requests.

:class:`profile.ProfilePage` is the widget. These methods used to live
on it; they only need the page as ``self`` (account, relationship,
client, the follow button), so they are a mixin rather than a second
copy of that state.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import pages  # noqa: E402
from .capabilities import ENDORSE_API_VERSION  # noqa: E402
from .pages import connect_weak  # noqa: E402


def endorse_action_names(endorse, endorse_api_supported):
    """POST path segments for feature/unfeature, modern or legacy."""
    names = ('endorse', 'pin') if endorse else ('unendorse', 'unpin')
    if not endorse_api_supported:
        return names[::-1]
    return names


class ProfileActions:

    # Action name -> (method, arguments); dispatched by name so the
    # actions, which the page owns, hold the page only weakly.
    _PLAIN_ACTIONS = {
        'mention': ('on_mention', False),
        'direct': ('on_mention', True),
        'copy-handle': ('on_copy_handle',),
        'copy-link': ('on_copy_link',),
        'open-browser': ('on_open_browser',),
        'private-note': ('edit_private_note',),
        'lists': ('on_lists',),
        'hide-boosts': ('set_boosts', False),
        'show-boosts': ('set_boosts', True),
        'remove-follower': ('confirm_remove_follower',),
        'mute': ('on_mute',),
        'unmute': ('on_unmute',),
        'block': ('confirm_block', True),
        'unblock': ('confirm_block', False),
        'block-domain': ('confirm_domain_block', True),
        'unblock-domain': ('confirm_domain_block', False),
        'report': ('on_report',),
    }
    _STATEFUL_ACTIONS = {
        'notify': 'set_notifying',
        'endorse': 'set_endorsed',
    }

    def _build_actions(self):
        self.actions = Gio.SimpleActionGroup()
        for name in self._PLAIN_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            connect_weak(action, 'activate', self._on_plain_action)
            self.actions.add_action(action)
        for name in self._STATEFUL_ACTIONS:
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(False))
            connect_weak(action, 'change-state', self._on_stateful_action)
            self.actions.add_action(action)
        self.insert_action_group('profile', self.actions)

    def _on_plain_action(self, action, _param):
        method, *args = self._PLAIN_ACTIONS[action.get_name()]
        getattr(self, method)(*args)

    def _on_stateful_action(self, action, value):
        getattr(self, self._STATEFUL_ACTIONS[action.get_name()])(
            value.get_boolean())

    def update_menu(self):
        """Rebuild the overflow menu for the current relationship."""
        if self.menu_button is None:
            return
        rs = self.relationship
        mine = self._is_self()
        following = bool(rs.get('following'))
        menu = Gio.Menu()

        # Access keys are unique across the whole menu (not per section).
        compose = Gio.Menu()
        compose.append(_('_Mention'), 'profile.mention')
        if not mine:
            compose.append(_('_Direct Message'), 'profile.direct')
        menu.append_section(None, compose)

        share = Gio.Menu()
        share.append(_('Copy _Handle'), 'profile.copy-handle')
        share.append(_('_Copy Link'), 'profile.copy-link')
        share.append(_('Open in _Browser'), 'profile.open-browser')
        menu.append_section(None, share)

        if not mine:
            relation = Gio.Menu()
            if following:
                relation.append(_('Receive Post _Notifications'),
                                'profile.notify')
                relation.append(_('_Feature on Profile'), 'profile.endorse')
                relation.append(
                    _('Show Boo_sts') if rs.get('showing_reblogs') is False
                    else _('Hide Boo_sts'),
                    'profile.show-boosts'
                    if rs.get('showing_reblogs') is False
                    else 'profile.hide-boosts')
                relation.append(_('Add or Remove from _Lists…'),
                                'profile.lists')
            relation.append(_('_Private Note…'), 'profile.private-note')
            if rs.get('followed_by'):
                relation.append(_('_Remove from Followers'),
                                'profile.remove-follower')
            menu.append_section(None, relation)

            moderation = Gio.Menu()
            # Not _Mute: _Mention already takes M. Not U_nmute: Receive
            # Post _Notifications already takes N when that row is shown.
            moderation.append(
                _('Unmu_te') if rs.get('muting') else _('M_ute…'),
                'profile.unmute' if rs.get('muting') else 'profile.mute')
            # Not _Block: Open in _Browser already takes B.
            moderation.append(
                _('Unbloc_k') if rs.get('blocking') else _('Bloc_k…'),
                'profile.unblock' if rs.get('blocking')
                else 'profile.block')
            if self._remote_domain():
                moderation.append(
                    _('Unblock Dom_ain') if rs.get('domain_blocking')
                    else _('Block Dom_ain…'),
                    'profile.unblock-domain' if rs.get('domain_blocking')
                    else 'profile.block-domain')
            moderation.append(_('R_eport…'), 'profile.report')
            menu.append_section(_('Moderati_on'), moderation)

        for name, key in (('notify', 'notifying'), ('endorse', 'endorsed')):
            self.actions.lookup_action(name).set_state(
                GLib.Variant.new_boolean(bool(rs.get(key))))
        self.menu_button.set_menu_model(menu)
        self.menu_button.set_sensitive(not self._busy)

    def _close_sheet(self):
        dialog = self.get_ancestor(Adw.Dialog)
        if dialog is not None:
            dialog.close()

    def on_mention(self, direct):
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        if dialog is None:
            return
        self._close_sheet()
        dialog.start_mention(self.account, direct=direct)
        window.present_composer()

    def on_copy_handle(self):
        self.get_clipboard().set(self._full_handle())
        pages.show_toast(self, _('Copied handle to clipboard'))

    def on_copy_link(self):
        url = self.account.get('url') or ''
        self.get_clipboard().set(url)
        pages.show_toast(self, _('Copied link to clipboard'))

    def on_open_browser(self):
        url = self.account.get('url')
        if url:
            pages.launch_external(self, url)

    def on_lists(self):
        from .lists import present_list_membership
        present_list_membership(self, self.client, self.account)

    def on_report(self):
        from .report import present_report
        present_report(self, self.client, self.account)

    def on_edit_profile(self):
        from .profileedit import present_profile_edit
        present_profile_edit(self, self.client, self.account,
                             on_saved=self.on_profile_saved)

    def on_profile_saved(self, account):
        self.account = dict(self.account, **account)
        self.refresh_header(self.account)

    # Confirmations

    def confirm_unfollow(self):
        """Reversible: act at once and offer Undo, unless the account is
        locked (a fresh request would need approval again)."""
        if self.account.get('locked'):
            pages.confirm(
                self, _('Unfollow %s?') % self._acct(),
                _('Their posts will no longer appear in your home '
                  'timeline.'),
                _('_Unfollow'),
                lambda: self.send_relationship(
                    'unfollow', done=_('Unfollowed %s'),
                    failed=_('Could not unfollow %s')))
            return
        self.send_relationship(
            'unfollow', done=_('Unfollowed %s'),
            failed=_('Could not unfollow %s'),
            undo=('follow', None, _('_Undo')))

    def confirm_cancel_request(self):
        self.send_relationship(
            'unfollow', done=_('Withdrew follow request to %s'),
            failed=_('Could not withdraw follow request to %s'),
            undo=('follow', None, _('_Undo')))

    def confirm_remove_follower(self):
        pages.confirm(
            self, _('Remove %s from Followers?') % self._acct(),
            _('They will stop following you and will not be told.'),
            _('_Remove'),
            lambda: self.send_relationship(
                'remove_from_followers',
                done=_('Removed %s from your followers'),
                failed=_('Could not remove %s from your followers')))

    def confirm_block(self, block):
        if block:
            pages.confirm(
                self, _('Block %s?') % self._acct(),
                _('They will not be able to follow you or see your posts, '
                  'and their posts will be hidden from you.'),
                _('_Block'),
                lambda: self.send_relationship(
                    'block', done=_('Blocked %s'),
                    failed=_('Could not block %s')))
            return
        self.send_relationship(
            'unblock', done=_('Unblocked %s'),
            failed=_('Could not unblock %s'),
            undo=('block', None, _('_Undo')))

    def confirm_domain_block(self, block):
        domain = self._remote_domain()
        if not domain:
            return
        if block:
            pages.confirm(
                self, _('Block Entire %s?') % domain,
                _('Blocking a domain will:\n\n'
                  '• Remove its public posts and notifications from '
                  'your timelines\n'
                  '• Remove its followers from your account\n'
                  '• Prevent you from following its users'),
                _('_Block'),
                lambda: self.send_domain_block(block, domain),
                destructive=True)
            return
        self.send_domain_block(block, domain, undo=_('_Undo'))

    # Requests

    def _set_busy(self, busy):
        self._busy = busy
        self.update_follow_button()
        if self.menu_button is not None:
            self.menu_button.set_sensitive(not busy)

    def _send_profile_action(self, method, path, form=None, params=None,
                             failed=None, on_failed=None, apply=None,
                             done=None, undo=None, undo_label=None):
        """One account action: busy, POST, apply, report, toast.

        The shape every mutation on this page has -- follow, unfollow,
        block, mute, feature, boost visibility, domain block -- and the
        reason it is here rather than spelled out in each: they were three
        near-identical closures, and the copies had already drifted (one
        refreshes the menu on failure, another does not).

        *apply* receives the decoded body and puts it on the page; the
        default is a Relationship, which is what
        ``POST /accounts/:id/<op>`` answers. *done* is the toast text.
        *undo* is ``(method, path, form, params, label)`` -- the reverse
        call, offered as a button on the success toast.
        """
        self._set_busy(True)

        def finished(client, result, _data=None):
            try:
                body = client.request_finish(result)
            except GLib.Error as error:
                self._set_busy(False)
                pages.report_error(self, failed or _('Action failed'),
                                   error)
                if on_failed is not None:
                    on_failed(error)
                self.update_menu()
                return
            self._busy = False
            if apply is not None:
                apply(body)
            else:
                self.set_relationship(body)
            if not done:
                return
            undo_action = None
            if undo is not None:
                undo_action = lambda: self._send_profile_action(  # noqa: E731
                    *undo[:4], failed=failed, done=_('Restored'))
            pages.show_toast(self, done, undo=undo_action,
                             undo_label=undo_label)

        self.client.request_async(method, path, form=form, params=params,
                                  callback=finished)

    def send_relationship(self, operation, form=None, done=None,
                          failed=None, on_failed=None, undo=None):
        """POST /accounts/:id/<operation>; the response is the new
        Relationship. *undo* is ``(operation, form, label)`` for a
        reversible action, offered as a button on the success toast."""
        handle = self._acct()
        undo_spec = None
        undo_label = None
        if undo is not None:
            undo_spec = ('POST',
                         '/accounts/%s/%s' % (self.account['id'], undo[0]),
                         undo[1], None)
            undo_label = undo[2]
        self._send_profile_action(
            'POST', '/accounts/%s/%s' % (self.account['id'], operation),
            form=form,
            failed=(failed or _('Action failed: %s')) % handle,
            on_failed=on_failed,
            done=done % handle if done else None,
            undo=undo_spec, undo_label=undo_label)

    def send_domain_block(self, block, domain, undo_label=None):
        """Block or unblock *domain*. The response carries no
        Relationship, so the flag is applied locally and the real one is
        fetched afterwards -- the one action here that does not answer
        with the relationship."""
        def applied(_body):
            self.relationship = dict(self.relationship,
                                     domain_blocking=block)
            self.update_relationship_ui()
            self.load_relationship()

        method = 'POST' if block else 'DELETE'
        kwargs = {'form': {'domain': domain}} if block \
            else {'params': {'domain': domain}}
        undo_spec = None
        if undo_label:
            undo_spec = ('DELETE' if block else 'POST', '/domain_blocks',
                         {'domain': domain}, None)
        self._send_profile_action(
            method, '/domain_blocks', applied=applied,
            failed=_('Could not block domain') if block
            else _('Could not unblock domain'),
            done=(_('Blocked %s') if block else _('Unblocked %s')) % domain,
            undo=undo_spec, undo_label=undo_label,
            **kwargs)

    def on_mute(self):
        from .profile import present_mute_dialog
        present_mute_dialog(
            self, self._acct(),
            lambda duration, notifications: self.send_relationship(
                'mute',
                form={'notifications': str(notifications).lower(),
                      'duration': str(duration)},
                done=_('Muted %s'), failed=_('Could not mute %s')))

    def on_unmute(self):
        self.send_relationship('unmute', done=_('Unmuted %s'),
                               failed=_('Could not unmute %s'))

    def set_boosts(self, show):
        self.send_relationship(
            'follow', form={'reblogs': str(show).lower()},
            done=_('Showing boosts from %s') if show
            else _('Hiding boosts from %s'),
            failed=_('Could not change boosts from %s'))

    def set_notifying(self, notify):
        self.send_relationship(
            'follow', form={'notify': str(notify).lower()},
            done=_('You will be notified when %s posts') if notify
            else _('You will no longer be notified when %s posts'),
            failed=_('Could not change notifications for %s'))

    def set_endorsed(self, endorse):
        """Feature or unfeature the account on your own profile. Servers
        that predate the ``endorse`` aliases only know ``pin``/``unpin``,
        so a 404 retries with the other spelling.

        The switch is a range, not a threshold: both ends matter, since
        version 6 is when ``endorse`` appeared and any server before it
        needs the old names. A server that says nothing about
        api_versions.mastodon keeps the modern spelling, which is what a
        404 and the fallback below already tried.

        That ladder is the reason this does not go through
        _send_profile_action: it has to see the 404 itself to know to
        retry, and that retry has to run while the busy state is still
        held. It is also the one caller that asks ``is_supported`` for
        ``when_unknown=True`` -- stated at the call site, which is where
        a policy like that belongs."""
        names = endorse_action_names(
            endorse, self.client.is_supported(ENDORSE_API_VERSION,
                                              when_unknown=True))
        done = _('Featured %s on your profile') if endorse \
            else _('Removed %s from your featured profiles')
        failed = _('Could not feature %s') if endorse \
            else _('Could not unfeature %s')

        self._set_busy(True)
        handle = self._acct()

        def finished(client, result, attempt):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                if getattr(error, 'code', 0) == 404 and attempt == 0:
                    send(1)
                    return
                self._set_busy(False)
                pages.report_error(self, failed % handle, error)
                self.update_menu()
                return
            self._busy = False
            self.set_relationship(relationship)
            pages.show_toast(self, done % handle)

        def send(attempt):
            self.client.request_async(
                'POST', '/accounts/%s/%s' % (self.account['id'],
                                             names[attempt]),
                callback=lambda c, r, _d=None: finished(c, r, attempt))

        send(0)

    # Private note

    def edit_private_note(self, text=None):
        from .profile import PrivateNoteDialog
        note = self.relationship.get('note') if text is None else text
        dialog = PrivateNoteDialog(
            self._acct(), note,
            lambda value: self.save_private_note(dialog, value))
        dialog.present(self)

    def save_private_note(self, dialog, text):
        def finished(client, result, _data=None):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                dialog.save_button.set_sensitive(True)
                pages.report_error(self, _('Could not save note'), error)
                return
            dialog.close()
            self.set_relationship(relationship)
            pages.show_toast(self, _('Private note saved'))

        self.client.request_async(
            'POST', '/accounts/%s/note' % self.account['id'],
            form={'comment': text}, callback=finished)

