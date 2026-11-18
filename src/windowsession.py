# SPDX-License-Identifier: GPL-3.0-or-later
"""The signed-in session: restoring, switching and removing accounts.

The account lifecycle of the window: restore the stored account, present
the no-keyring warning, store and forget accounts (with their drafts and
their token), the session chrome around them, and signing out. A mixin of
``BabbleWindow``; it reaches the pages through the window's own
attributes.
"""
import gi

from .i18n import gettext as _

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import appsettings  # noqa: E402
from . import auth  # noqa: E402
from . import drafts  # noqa: E402
from . import emojichooser  # noqa: E402
from . import feedback  # noqa: E402
from .api import accounts as api_accounts  # noqa: E402
from .capabilities import COLLECTIONS_API_VERSION  # noqa: E402
from .keyringstore import clear_secret_async  # noqa: E402
from .mastodon import MastodonClient, keyring_problem  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402
from .statustext import relative_time  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .mastodon import summarize_error  # noqa: E402



class WindowSession:
    def restore_account(self):
        account = self.get_stored_account()
        account_store.log_account_event(
            'startup: %d stored, active-account=%r, restoring %s' % (
                len(account_store.read_accounts(self.settings)),
                self.settings.get_string('active-account'),
                account_store.account_key(account) if account
                else 'nothing'))
        if account is None:
            self.main_stack.set_visible_child_name('login')
            return
        self._activate_account(account, login_only=True)

    def _activate_account(self, account, login_only=False):
        """Read *account*'s token from the keyring and make it the
        session.

        The read is an out-of-process round trip, so a second restore or
        switch can start before the first answers. Only the newest
        lookup may touch the session: a stale reply that returned a
        token would show the wrong account, and one that returned None
        would forget an account that is in fact signed in.
        """
        account_id = account['id']
        client = MastodonClient(account['instance'])
        lookup = self._session_lookup = self._session_lookup + 1

        def loaded(token, error=None):
            if lookup != self._session_lookup:
                return
            account_store.log_account_event(
                'keyring read for %s (login_only=%s): %s' % (
                    account_store.account_key(account), login_only,
                    'error %s' % error.message if error is not None
                    else 'token found' if token else 'no entry'))
            if error is not None:
                # A locked or unreachable keyring is not a signed-out
                # account. Keep the stored entry (and its Catch-Up
                # snapshots) and ask the user to try again.
                if error.matches(Gio.io_error_quark(),
                                 Gio.IOErrorEnum.CANCELLED):
                    return
                self.main_stack.set_visible_child_name('login')
                if keyring_problem(error) == 'missing':
                    self.present_no_keyring()
                    return
                self.show_toast(
                    _('Could not read the login for @%s from the keyring')
                    % account.get('acct', ''))
                return
            if token is None:
                if login_only:
                    # The whole account, not just an id: another
                    # instance may hold the same id.
                    self.remove_account(account)
                    self.main_stack.set_visible_child_name('login')
                else:
                    self.show_toast(
                        _('The login for @%s is gone from the keyring')
                        % account.get('acct', ''))
                return
            if not login_only:
                self._teardown_session()
            client.token = token
            self.client = client
            self.account = account
            if not login_only:
                # The paired key, so a second account with the same id on
                # another instance cannot take the selection with it.
                self.settings.set_string(
                    'active-account', account_store.account_key(account))
            self.show_shell()

        # The keyring is an out-of-process round trip; the token arrives
        # later and the shell is built then.
        client.load_secret_token(account_id, loaded)

    def present_no_keyring(self):
        """Say once per session that no keyring is running, and what to
        do. Returns the dialog, or None when it was already shown."""
        if self._no_keyring_shown:
            return None
        self._no_keyring_shown = True
        dialog = Adw.AlertDialog.new(
            _('No Keyring Found'),
            _('Babble keeps your login in the system keyring, and no '
              'keyring service is running. Start one, such as GNOME '
              'Keyring or KeePassXC, then restart Babble.'))
        dialog.add_response('close', _('_Close'))
        dialog.add_response('help', _('_Learn More'))
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        connect_weak(dialog, 'response', self._on_no_keyring_response)
        dialog.present(self)
        return dialog

    def _on_no_keyring_response(self, _dialog, response):
        if response == 'help':
            self.open_help()

    def get_stored_account(self):
        return account_store.active_account(self.settings)

    def store_account(self, account):
        account_store.save_account(self.settings, account, activate=True)

    def remove_account(self, account):
        """Forget *account* -- the id together with its instance.

        Forgetting by id alone would delete every stored account carrying
        that id, including another instance's.
        """
        account_store.forget_account(self.settings, account['id'],
                                      account['instance'])

    def _update_session_actions(self):
        signed_in = self.client is not None and self.account is not None
        for name in ('logout', 'show-notifications', 'compose',
                     'view-profile', 'reload', 'back-home', 'search',
                     'announcements', 'follow-requests', 'mutes-blocks',
                     'drafts', 'scheduled', 'wrapped', 'goto-place'):
            self.lookup_action(name).set_enabled(signed_in)
        # A server that is not Mastodon, or an older one, is told no
        # rather than being assumed yes: Collections is Mastodon API
        # version 10, and an unproven server is not evidence that it has
        # it. The admin switch is about the signed-in role, not the
        # server's version.
        self.lookup_action('collections').set_enabled(
            signed_in and self.client.is_supported(
                COLLECTIONS_API_VERSION))
        self.lookup_action('admin').set_enabled(
            signed_in and account_store.role_is_admin(self.account))
        self._refresh_draft_banner()

    def _local_drafts(self):
        """The signed-in account's restorable drafts, newest first, as
        ``(raw, draft)`` pairs."""
        if self.account is None or self.client is None:
            return []
        return drafts.stored_drafts(
            appsettings.settings().get_strv('local-drafts'),
            account_store.account_key(self.account))

    def _refresh_draft_banner(self, *_args):
        drafts_ = self._local_drafts()
        self.draft_banner.set_revealed(bool(drafts_))
        when = relative_time(drafts_[0][1].saved_at) if drafts_ else ''
        if when:
            # Translators: a draft saved earlier is offered for
            # restoring; %s is a compact age like "5m"
            self.draft_banner.set_title(_('Draft from %s') % when)
        else:
            self.draft_banner.set_title(_('You have an unsent draft'))

    def _teardown_session(self):
        """Drop every page, stream and dialog tied to the current
        client, before another account takes over."""
        # An authorization in flight belongs to the session being
        # dropped, so its requests are cancelled rather than left to
        # land on the account replacing this one.
        self.auth.cancel()
        self.streaming.stop()
        self._stream_dropped = False
        emojichooser.deactivate_instance_emojis()
        self.notifier.reset()
        # A cached composer also owns requests when not presented.
        if self.compose_dialog is not None:
            self.compose_dialog.abandon_session()
        self.compose_dialog = None
        # navigation.clear() stops places (including home/notifications
        # via unregister_external) and must not sync_streaming.
        self.navigation.clear()
        self.navigation_view.replace([self.page_home])

    def switch_account(self, account_key):
        account = account_store.find_account_by_key(self.settings, account_key)
        if account is None:
            return
        self._activate_account(account)

    def begin_add_account(self):
        """Show the login page on top of the running session; Back
        returns to it."""
        self.auth.restart()
        self.login_back_button.set_visible(self.client is not None)
        self.main_stack.set_visible_child_name('login')
        self._grab_focus(self.instance_entry)

    def on_remove_account_requested(self, _switcher, account_key):
        account = account_store.find_account_by_key(self.settings, account_key)
        if account is None:
            return
        feedback.confirm(
            self, _('Remove Account?'),
            _('The login for %s will be removed from this device.')
            % account_store.full_handle(account),
            _('_Remove'), lambda: self._remove_account_now(account))

    def _remove_account_now(self, account):
        """Sign *account* out and stop using it on this device.

        The token is read back, the local removal happens at once, and
        the server is told afterwards. The read has to come first -- the
        revoke needs the token, and the keyring entry is deleted on the
        way out -- but it is local IPC, so the window never waits on the
        network to finish logging you out.
        """
        self._revoke_then_remove(account)

    def _revoke_then_remove(self, account):
        instance, account_id = account['instance'], account['id']
        # Whether this is the account being removed *now* is decided when
        # the removal completes, not when it starts: the keyring read is
        # asynchronous, and the user can switch accounts in between.
        was_active = self._is_active(account)
        # The session that signed in already holds this account's token
        # and client credentials; removing another account needs a client
        # that reads them back.
        client = self.client if was_active and self.client is not None \
            else MastodonClient(instance)
        live = client is self.client
        have_token = live and bool(client.token)
        have_credentials = live and bool(client.client_id
                                         and client.client_secret)

        def revoke_then_forget():
            """Start the revoke, then drop everything locally.

            The revoke runs on its own from here: a server that has
            already forgotten the token answers 200, and one that cannot
            be reached must not keep the window signed in.
            """
            if client.token and client.client_id and client.client_secret:
                client.revoke_token_async(
                    client.token,
                    lambda _c, result, _d=None: auth.discard_reply(
                        client.revoke_token_finish, result))
            self._finish_account_removal(account)

        def on_credentials(_known):
            revoke_then_forget()

        def on_token(token, error=None):
            if error is None:
                client.token = token
            client.load_client_credentials(account_id, on_credentials)

        if have_token and have_credentials:
            revoke_then_forget()
        elif have_token:
            client.load_client_credentials(account_id, on_credentials)
        else:
            client.load_secret_token(account_id, on_token)

    def _is_active(self, account):
        """Whether *account* is the one currently signed in."""
        return self.account is not None \
            and account_store.same_account(self.account, account)

    def _finish_account_removal(self, account):
        window_ref = self.weak_ref()
        errors = []
        pending = 2

        def cleared(error):
            nonlocal pending
            if error is not None:
                errors.append(error)
            pending -= 1
            window = window_ref()
            if pending or not errors or window is None:
                return
            detail = summarize_error(errors[0].message)
            body = _('The account was removed from Babble, but the system '
                     'keyring could not remove all of its credentials.')
            if detail:
                body += ' ' + detail
            window.show_error(_('Could not remove all saved credentials'), body)

        client = MastodonClient(account['instance'])
        clear_secret_async(account['instance'], account['id'], cleared)
        client.clear_client_credentials(account['id'], cleared)
        remaining = account_store.forget_account(self.settings,
                                                 account['id'],
                                                 account['instance'])
        # Local removal never waits for keyring deletion or server revocation.
        if not self._is_active(account):
            return
        self._teardown_session()
        self.account = None
        self.client = None
        if remaining is not None:
            self.switch_account(account_store.account_key(remaining))
        else:
            self._sign_out_to_login()

    def _sign_out_to_login(self):
        self.account_avatar.set_custom_image(None)
        self.account_button.set_tooltip_text(_('Switch Account'))
        self._update_session_actions()
        self.login_back_button.set_visible(False)
        self.auth.cancel()
        self.main_stack.set_visible_child_name('login')

    def show_shell(self):
        name = account_store.plain_name(self.account)
        handle = account_store.full_handle(self.account)
        self.account_name_label.set_label(name)
        self.account_handle_label.set_label(handle)
        # The label ellipsizes when narrow or large-text; keep the full
        # handle (with instance) on the button for hover and Orca.
        self.account_button.set_tooltip_text(handle)
        self.account_avatar.set_text(name)
        AvatarCache.load(self.account_avatar, self.account)
        self._update_session_actions()
        self._refresh_sidebar()
        self.ensure_home_timeline()
        self.ensure_notifications_page()
        self.show_page('home')
        # Settle the sidebar before the shell's first allocation: setting
        # show-sidebar from inside the collapsed notification makes the
        # split view allocate its content pane with a negative width.
        self._sync_sidebar_mode()
        self._sync_sidebar_visibility()
        self.login_back_button.set_visible(False)
        self.main_stack.set_visible_child_name('shell')
        self._load_session_info()
        self._load_instance_emojis()
        self._flush_pending_compose()

    def _load_instance_emojis(self):
        """The instance's whole custom-emoji set, fetched once per
        session: it is the fallback layer every rendered ``:shortcode:``
        resolves against, so it cannot wait for the composer to ask
        for it (Tuba gathers these as the account comes up)."""
        client = self.client
        emojichooser.activate_instance_emojis(client)

        def loaded(_emojis):
            if client is self.client:
                emojichooser.activate_instance_emojis(client)

        emojichooser.load_custom_emojis(client, loaded)

    def _load_session_info(self):
        """Instance details (limits, streaming URL, API level) and the
        fresh account entity (role, display name)."""
        client = self.client
        if not hasattr(client, 'instance_info_async'):
            return

        def info_done(_client, result, _data=None):
            try:
                client.instance_info_finish(result)
            except GLib.Error:
                pass
            if client is self.client:
                self._update_session_actions()
                self._refresh_sidebar()
                self._sync_streaming()

        client.instance_info_async(callback=info_done)

        def account_done(_client, result, _data=None):
            try:
                fresh = api_accounts.verify_credentials_finish(client, result)
            except GLib.Error:
                return
            if client is not self.client or self.account is None \
                    or fresh.get('id') != self.account.get('id'):
                return
            fresh['instance'] = self.account['instance']
            self.account = fresh
            account_store.save_account(self.settings, fresh, activate=False)
            self._update_session_actions()
            self._refresh_sidebar()

        api_accounts.verify_credentials_async(client, callback=account_done)

    def on_account_updated(self, account):
        """The profile editor saved: show the new name and avatar."""
        account = dict(account, instance=self.account['instance'])
        self.account = account
        account_store.save_account(self.settings, account, activate=False)
        self.account_name_label.set_label(account_store.plain_name(account))
        AvatarCache.load(self.account_avatar, account)

    def on_logout_requested(self, _action, _param):
        if self.account is None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Log Out?'),
            body=_('The login for @%s will be signed out on the server '
                   'and removed from this device.')
            % self.account.get('acct', ''))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('logout', _('_Log Out'))
        dialog.set_response_appearance('logout',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self, None, self._on_logout_chosen)

    def _on_logout_chosen(self, dialog, result):
        if dialog.choose_finish(result) == 'logout':
            self.on_logout(None, None)

    def on_logout(self, _action, _param):
        if self.account is not None:
            self._remove_account_now(self.account)

    def _draft_restore(self, _banner):
        """Reopen the newest unsent draft saved when the composer was
        closed; the entry leaves the list once it is back in the
        composer."""
        drafts = self._local_drafts()
        if not drafts:
            self.draft_banner.set_revealed(False)
            return
        raw, draft = drafts[0]
        dialog = self.present_composer()
        if dialog is None:
            return
        if not dialog.view.restore_stashing(draft):
            return
        from .drafts import drop_local_draft
        drop_local_draft(appsettings.settings(), raw)

    def _login_back_clicked(self, _button):
        self.auth.restart()
        if self.client is not None and self.account is not None:
            self.main_stack.set_visible_child_name('shell')
