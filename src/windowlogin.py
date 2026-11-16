# SPDX-License-Identifier: GPL-3.0-or-later
"""Sign-in: the instance entry, the authorization and the code entry.

The login half of the window: the suggested servers, the instance entry's
normalization and suggestions, the authorization flow's callbacks, the
pasted-code path, and the two ways a URI is opened. A mixin of
``BabbleWindow``, so the window keeps one object while the file keeps one
subject.

``SUGGESTED_SERVERS`` lives here because the suggestions are the only
reader.
"""
import urllib.parse

import gi

from .i18n import gettext as _

gi.require_version('Gtk', '4.0')

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import auth  # noqa: E402
from .oauthpkce import OAUTH_REDIRECT, normalize_instance  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402



# One-click alternatives for the login suggestions row, ranked from the
# public JoinMastodon server directory (api.joinmastodon.org/servers)
# by weekly active users, largest communities first.
SUGGESTED_SERVERS = (
    'mastodon.social', 'mstdn.social', 'infosec.exchange', 'mstdn.jp',
    'hachyderm.io', 'piaille.fr', 'mastodon.world', 'social.vivaldi.net',
    'epsilon.social', 'troet.cafe', 'mastodon.uno', 'mathstodon.xyz')


class WindowLogin:
    def _setup_auth(self):
        # The OAuth state machine. It owns the attempt bookkeeping, the
        # loopback listener and the keyring writes, and answers in
        # signals; the window keeps only the widgets.
        self.auth = auth.LoginFlow(
            current_account=weak_method(self._signed_in_account))
        connect_weak(self.auth, 'succeeded', self._on_auth_succeeded)
        connect_weak(self.auth, 'failed', self._on_auth_failed)
        connect_weak(self.auth, 'message', self._on_auth_message)
        connect_weak(self.auth, 'browser', self._on_auth_browser)
        connect_weak(self.auth, 'changed', self._on_auth_changed)
        connect_weak(self.auth, 'attended', self._on_auth_attended)

    def _on_auth_succeeded(self, _flow, account):
        """Adopt the account a completed authorization belongs to."""
        client = self.auth.client
        if self.auth.admin:
            # A moderation authorization replaces the token of the
            # account already signed in, asking for the admin scopes on
            # top, instead of installing a second session.
            if self.client is not None and client is not None:
                self.client.token = client.token
                self.client.adopt_client_credentials(client)
        else:
            self.client = client
            self.instance_entry.set_text('')
        self.account = account
        self.store_account(account)
        if not self.auth.admin:
            self.show_shell()
        else:
            self._update_session_actions()

    def _on_auth_failed(self, _flow, heading, body):
        # The pending "finish in the browser" toast gives way to the
        # outcome instead of queueing behind it.
        self.toast_overlay.dismiss_all()
        self.show_error(heading, body)

    def _on_auth_message(self, _flow, text):
        self.toast_overlay.dismiss_all()
        self.show_toast(text)

    def _on_auth_browser(self, _flow, uri):
        self.open_url(uri)

    def _on_auth_attended(self, _flow):
        self.present()

    def _on_auth_changed(self, _flow):
        """The login widgets follow the flow.

        The code entry belongs to an authorization nobody is running any
        more once the flow is idle and has not fallen back to a pasted
        code, so it goes away with it. The manual-code button has its
        own rule, in :meth:`refresh_login_widgets`, which runs next.
        """
        if not self.auth.waiting and not self.auth.fallback:
            self._hide_code_entry()
        self.refresh_login_widgets()

    def _show_code_entry(self):
        self.code_entry.set_visible(True)
        self._grab_focus(self.code_entry)

    def _hide_code_entry(self):
        self.code_entry.set_text('')
        self.code_entry.set_visible(False)

    def _oauth_callback(self, uri, redirect_uri=OAUTH_REDIRECT):
        """Exchange the code a redirect to *redirect_uri* carries."""
        return self.auth.on_oauth_callback(uri, redirect_uri)

    def authorize_admin(self, callback=None):
        """Ask the instance for the admin scopes on the signed-in account.

        *callback(granted)* runs with the outcome. A refusal is the
        dashboard's cue to go back to plain moderation, so it always
        gets one, even when there was nothing to authorize.
        """
        if self.account is None or self.client is None:
            if callback is not None:
                callback(False)
            return
        # Bound to the account that asked for it: a switch or a logout
        # while the browser step is open must not let the reply install a
        # token for the account that replaced it.
        self.auth.start_admin(self.account['instance'],
                              account_store.account_key(self.account),
                              callback)

    def refresh_login_widgets(self):
        """Sync the login column with the typed server and the flow.

        With no server typed it shows the example hint and a plain
        Continue button; once a server name is in, the hint gives way to
        the suggestions and the button names the chosen server. While an
        authorization is in flight the button shows a spinner and a
        Cancel button offers a way out.
        """
        waiting = self.auth.waiting
        domain = self._instance_domain(self.instance_entry.get_text())
        self.instance_entry.set_sensitive(not waiting)
        self.login_button.set_sensitive(not waiting and bool(domain))
        self.login_spinner.set_visible(waiting)
        self.cancel_login_button.set_visible(waiting)
        if waiting:
            label = _('Waiting for Authorization')
        elif domain:
            label = _('_Continue with %s') % domain.replace('_', '__')
        else:
            label = _('_Continue')
        self.login_button_label.set_label(label)
        self.instance_hint.set_visible(not domain and not waiting)
        self.suggestions_section.set_visible(bool(domain) and not waiting)
        # The manual-code escape hatch belongs to an authorization in
        # flight, because that is the only state a pasted code can be
        # redeemed against -- and the only state where a browser that
        # never came back leaves the user stuck. It disappears once the
        # flow has already been switched to a pasted code.
        self.paste_code_button.set_visible(waiting
                                           and not self.auth.fallback)
        self._update_suggestions(domain)
        if domain:
            self.sign_up_link.set_uri('https://%s/auth/sign_up' % domain)
        else:
            self.sign_up_link.set_uri('https://joinmastodon.org/servers')

    @staticmethod
    def _instance_domain(text):
        """The bare server name typed, without scheme, path or case.

        An explicit ``http://`` is refused rather than quietly upgraded,
        so a user who typed it is told the connection would be
        plaintext instead of being sent somewhere they did not name.
        """
        text = (text or '').strip()
        if '://' in text:
            scheme, _, rest = text.partition('://')
            if scheme.lower() != 'https':
                return ''
            text = rest
        try:
            url = urllib.parse.urlsplit(normalize_instance(text))
        except ValueError:
            return ''
        return (url.netloc or url.path).lower().strip('.')

    def _update_suggestions(self, typed):
        """Refill the one-click alternatives.

        Servers matching the typed text rank first (prefix before
        substring), the typed server itself never repeats under the
        entry, and the directory order keeps everything else.
        """
        box = self.suggestions_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        if not typed:
            return
        ranked = sorted(
            (domain for domain in SUGGESTED_SERVERS if domain != typed),
            key=lambda domain: (not domain.startswith(typed),
                                typed not in domain,
                                SUGGESTED_SERVERS.index(domain)))
        for domain in ranked[:5]:
            button = Gtk.Button(label=domain)
            button.connect('clicked', self.on_suggestion_clicked, domain)
            box.append(button)

    def on_suggestion_clicked(self, _button, domain):
        """A suggestion is one click: adopt it as the chosen server."""
        self.instance_entry.set_text(domain)
        self.instance_entry.set_position(-1)
        self.refresh_login_widgets()
        self._grab_focus(self.login_button)

    def open_url(self, uri):
        """Open *uri* in the web browser through the portal, falling back
        to the default handler. The login depends on this: without it the
        authorization page never opens. It was dropped when the flow moved
        into auth.py, which still emits the URI for the window to open."""

        def launched(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                self._open_url_fallback(uri)

        Gtk.UriLauncher.new(uri).launch(self, None, launched)

    def _open_url_fallback(self, uri):
        def opened(source, result):
            try:
                source.launch_default_for_uri_finish(result)
            except GLib.Error as error:
                self.show_error(_('Could not open the web browser'),
                                error.message)

        app_info = Gio.AppInfo.get_default_for_uri(uri)
        if app_info is None:
            self.show_error(_('Could not open the web browser'),
                            _('No app is set to open web links'))
            return
        app_info.launch_default_for_uri_async(uri, None, None, opened)

    def _signed_in_account(self):
        """The account this window has signed in, or None.

        Handed to the login flow as a weak method, so a moderation
        authorization can tell whether the session it was started for is
        still the current one without the flow holding the window.
        """
        return self.account

    def _login_clicked(self, _widget):
        typed = self.instance_entry.get_text()
        domain = self._instance_domain(typed)
        if self.auth.waiting:
            return
        if not domain:
            if '://' in (typed or '') \
                    and not typed.strip().lower().startswith('https://'):
                self.show_error(
                    _('Could not connect to the server'),
                    # Translators: keep "https://" untranslated
                    _('Babble only connects over HTTPS. Enter the server '
                      'address without a scheme, or with https://.'))
            return
        self.auth.start_login(normalize_instance(domain))

    def _cancel_login_clicked(self, _button):
        # A reply still in flight for the abandoned attempt is ignored.
        self.auth.restart()
        self.toast_overlay.dismiss_all()
        self._grab_focus(self.instance_entry)

    def _paste_code_clicked(self, _button):
        # The authorization is named again for the out-of-band redirect,
        # so nothing can answer on the loopback listener any more.
        self.paste_code_button.set_visible(False)
        self.auth.use_pasted_code()
        # The field is shown either way: when the authorization could be
        # renamed the browser is on its way to showing a code, and when
        # it could not the user is still here holding one. Hiding it on
        # the first branch left them with a code and nowhere to put it.
        self._show_code_entry()

    def _instance_changed(self, entry):
        self.refresh_login_widgets()

    def _on_code_entered(self, entry):
        self.auth.enter_code(entry.get_text())
