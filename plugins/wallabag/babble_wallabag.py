# SPDX-License-Identifier: GPL-3.0-or-later

"""Save a post's link to Wallabag, from the post's menu.

Wallabag's API is OAuth 2 with the password grant: a client id and
secret (created in Wallabag under API clients management) and the
user's name and password buy an access token, good for an hour, and a
refresh token that buys the next one. The password is sent once and
never kept; the keyring holds the client secret and the refresh token,
and the access token lives in memory only. When the refresh token has
expired too (Wallabag's default is two weeks unused), saving asks to
sign in again.

The sign-in is a :class:`Session` in ``context.data``, shared by the
menu entry and the plugin's settings, so signing out in one is signing
out in the other.
"""

import json
import time
from urllib.parse import urlsplit

from gi.repository import Adw, Gtk

from babble import plugin

_ = plugin.gettext

SECRET = 'credentials'  # {"client_secret": …, "refresh_token": …}
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
# The hosted service's website, whose API is at app.wallabag.it: the
# address people type first, and a 404 when the token is asked of it.
HOSTED_SITE = ('wallabag.it', 'www.wallabag.it')
HOSTED_APP = 'https://app.wallabag.it'
# Refresh this long before the server says the token expires.
EXPIRY_MARGIN = 60


def normalize_server(text):
    """The server's origin, without a trailing slash, or None when it
    is not one. Plain http is refused except on this machine: the
    password and the client secret would cross the network in the
    clear."""
    text = (text or '').strip().rstrip('/')
    if not text:
        return None
    if '://' not in text:
        text = 'https://' + text
    parts = urlsplit(text)
    if not parts.hostname or parts.scheme not in ('https', 'http'):
        return None
    if parts.scheme == 'http' and parts.hostname not in LOCAL_HOSTS:
        return None
    if parts.hostname in HOSTED_SITE:
        return HOSTED_APP
    return text


def session_of(context):
    session = context.data.get('session')
    if session is None:
        session = context.data['session'] = Session(context)
    return session


class Session:
    """The account and its tokens, while the plugin is enabled."""

    def __init__(self, context):
        self.context = context
        self.access_token = None
        self.expires_at = 0.0
        self.credentials = None  # read from the keyring when first needed
        self.waiting = None  # callbacks waiting while a token is fetched

    def signed_in(self):
        settings = self.context.settings
        return bool(settings.get('server') and settings.get('client_id'))

    def account(self):
        """``(username, server host)`` of the signed-in account."""
        settings = self.context.settings
        return (settings.get('username', ''),
                urlsplit(settings.get('server', '')).hostname or '')

    # Saving

    def save(self, url, done, retry=True):
        """Add *url* to Wallabag: ``done(response)``, or ``done(None)``
        when there is no token to be had and the user has to sign in."""
        def with_token(token):
            if token is None:
                done(None)
                return
            self.add_entry(url, token, done, retry)
        self.with_token(with_token)

    def add_entry(self, url, token, done, retry):
        def sent(response):
            if response.status == 401 and retry:
                # The access token was revoked or is older than we
                # thought: drop it and refresh once.
                self.access_token = None
                self.save(url, done, retry=False)
                return
            done(response)
        self.context.request(
            'POST', self.context.settings.get('server') + '/api/entries.json',
            sent, json={'url': url},
            headers={'Authorization': 'Bearer ' + token})

    # Tokens

    def with_token(self, then):
        """``then(access_token)``, refreshing it first when it expired;
        ``then(None)`` when it cannot be refreshed."""
        if self.access_token and time.monotonic() < self.expires_at:
            then(self.access_token)
            return
        if self.waiting is not None:
            self.waiting.append(then)
            return
        self.waiting = [then]

        def finished(error, _status=None):
            waiting, self.waiting = self.waiting, None
            if error is not None:
                self.context.log('cannot refresh the token: %s' % error)
            for callback in waiting:
                callback(self.access_token if error is None else None)
        if self.credentials is not None:
            self.refresh(finished)
            return

        def loaded(value, error):
            try:
                self.credentials = json.loads(value) if value else None
            except ValueError:
                self.credentials = None
            if not self.credentials:
                finished(error or 'no credentials stored')
                return
            self.refresh(finished)
        self.context.lookup_secret(SECRET, loaded)

    def refresh(self, finished):
        secret = self.credentials.get('client_secret', '')
        self.token_request({
            'grant_type': 'refresh_token',
            'client_id': self.context.settings.get('client_id'),
            'client_secret': secret,
            'refresh_token': self.credentials.get('refresh_token', ''),
        }, secret, finished)

    def sign_in(self, server, client_id, client_secret, username, password,
                finished):
        """Get a first token with the password grant; *finished(error)*.
        The server and client id are kept only once it worked.

        Wallabag can live in a folder (``https://host/wallabag``), so the
        address is tried as typed first; when nothing answers there and
        it has a path, the server's root is tried next. That is what a
        page address pasted from the browser
        (``https://host/unread/list``) needs."""
        form = {
            'grant_type': 'password',
            'client_id': client_id,
            'client_secret': client_secret,
            'username': username,
            'password': password,
        }
        candidates = [server]
        parts = urlsplit(server)
        root = '%s://%s' % (parts.scheme, parts.netloc)
        if root != server:
            candidates.append(root)

        def attempt(address):
            def done(error, status=None):
                if status == 404 and candidates:
                    attempt(candidates.pop(0))
                    return
                if error is None:
                    settings = self.context.settings
                    settings.set('server', address)
                    settings.set('client_id', client_id)
                    settings.set('username', username)
                finished(error)
            self.token_request(form, client_secret, done, server=address)
        attempt(candidates.pop(0))

    def sign_out(self):
        """Forget the tokens and the client; the server and username stay
        to fill in the next sign-in."""
        self.access_token = None
        self.credentials = None
        self.context.settings.remove('client_id')
        self.context.clear_secret(SECRET)

    def token_request(self, form, client_secret, finished, server=None):
        server = server or self.context.settings.get('server')

        def done(response):
            data = response.data if isinstance(response.data, dict) else {}
            token = data.get('access_token')
            if not response.ok or not token:
                if response.status in (400, 401):
                    # The server refused the grant: what we hold is no
                    # good any more.
                    self.access_token = None
                    self.credentials = None
                    message = data.get('error_description') or _(
                        'The server did not accept these details')
                elif response.status == 404:
                    message = _('No Wallabag server answered at this '
                                'address')
                else:
                    message = response.error or _(
                        'The server sent no token')
                # The address and the outcome only: the form carries
                # the password and the client secret.
                self.context.log('token request to %s failed: %s' % (
                    url, response.error))
                finished(message, response.status)
                return
            self.access_token = token
            self.expires_at = time.monotonic() + max(
                0, int(data.get('expires_in') or 3600) - EXPIRY_MARGIN)
            self.credentials = {
                'client_secret': client_secret,
                'refresh_token': data.get('refresh_token') or '',
            }
            self.context.store_secret(SECRET, json.dumps(self.credentials))
            finished(None, response.status)
        url = server + '/oauth/v2/token'
        self.context.request('POST', url, done, form=form)


class Wallabag(plugin.PostMenuExtension):
    """Save to Wallabag, in every post's menu."""

    def menu_items(self, post):
        if not (post.url or post.uri):
            return []
        if session_of(self.context).signed_in():
            label = _('Save to _Wallabag')
        else:
            label = _('Save to _Wallabag…')
        return [plugin.MenuItem(label, self.save)]

    def save(self, post, widget):
        session = session_of(self.context)
        if not session.signed_in():
            self.ask_sign_in(widget, lambda: self.save(post, widget))
            return

        def done(response):
            if response is None:
                self.ask_sign_in(widget, lambda: self.save(post, widget))
            elif response.ok:
                self.context.toast(widget, _('Saved to Wallabag'))
            else:
                # Translators: %s is the reason, as the server or the
                # network gave it.
                self.context.toast(widget, _('Could not save to Wallabag: '
                                             '%s') % response.error)
        session.save(post.url or post.uri, done)

    def ask_sign_in(self, widget, then):
        SignInDialog(self, session_of(self.context), then).present(widget)


class WallabagPreferences(plugin.PreferencesExtension):
    """The account: who is signed in, and Sign Out; or Sign In."""

    def create_preferences(self):
        self.row = Adw.ActionRow(title=_('Account'))
        self.button = Gtk.Button(valign=Gtk.Align.CENTER, use_underline=True)
        self.row.add_suffix(self.button)
        group = Adw.PreferencesGroup(
            description=_('Saved posts go to this account'))
        group.add(self.row)
        self.connect(self.button, 'clicked', self.on_clicked)
        self.update()
        return group

    def update(self):
        session = session_of(self.context)
        if session.signed_in():
            username, server = session.account()
            # Translators: a Wallabag account, by its username and the
            # server's host name.
            self.row.set_subtitle(_('%(username)s on %(server)s') % {
                'username': username, 'server': server})
            self.button.set_label(_('Sign _Out'))
            self.button.remove_css_class('suggested-action')
        else:
            self.row.set_subtitle(_('Not signed in'))
            self.button.set_label(_('_Sign In…'))
            self.button.add_css_class('suggested-action')

    def on_clicked(self, button):
        session = session_of(self.context)
        if session.signed_in():
            session.sign_out()
            self.update()
            self.context.toast(button, _('Signed out of Wallabag'))
            return
        SignInDialog(self, session, self.update).present(button)


class SignInDialog(Adw.Dialog):
    """Server, API client and account; *then* runs once signed in."""

    def __init__(self, extension, session, then):
        super().__init__(title=_('Sign In to Wallabag'), content_width=420)
        self.extension = extension
        self.session = session
        self.then = then
        settings = extension.context.settings

        header = Adw.HeaderBar()
        self.sign_in_button = Gtk.Button(label=_('_Sign In'),
                                         use_underline=True)
        self.sign_in_button.add_css_class('suggested-action')
        header.pack_end(self.sign_in_button)

        self.server = Adw.EntryRow(title=_('Server'),
                                   text=settings.get('server', ''))
        self.server.set_tooltip_text(
            _('app.wallabag.it, or your own server’s address'))
        self.client_id = Adw.EntryRow(title=_('Client ID'),
                                      text=settings.get('client_id', ''))
        self.client_secret = Adw.PasswordEntryRow(title=_('Client Secret'))
        self.username = Adw.EntryRow(title=_('Username'),
                                     text=settings.get('username', ''))
        self.password = Adw.PasswordEntryRow(title=_('Password'))
        server_group = Adw.PreferencesGroup(
            description=_('Use app.wallabag.it for the hosted service. '
                          'Create a client under API Clients Management in '
                          'Wallabag to get its ID and secret.'))
        for row in (self.server, self.client_id, self.client_secret):
            server_group.add(row)
        account_group = Adw.PreferencesGroup(
            description=_('Your password is used once to sign in and is '
                          'not stored'))
        for row in (self.username, self.password):
            account_group.add(row)
        self.error = Gtk.Label(wrap=True, xalign=0.0, visible=False)
        self.error.add_css_class('error')

        page = Adw.PreferencesPage()
        page.add(server_group)
        page.add(account_group)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page)
        box.append(Adw.Clamp(child=self.error, margin_start=12,
                             margin_end=12, margin_bottom=12))
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(self.sign_in_button)

        extension.connect(self.sign_in_button, 'clicked', self.on_sign_in)
        for row in (self.server, self.client_id, self.client_secret,
                    self.username, self.password):
            extension.connect(row, 'changed', self.on_changed)
            extension.connect(row, 'entry-activated', self.on_sign_in)
        self.on_changed()

    def fields(self):
        return (normalize_server(self.server.get_text()),
                self.client_id.get_text().strip(),
                self.client_secret.get_text().strip(),
                self.username.get_text().strip(),
                self.password.get_text())

    def on_changed(self, *_args):
        self.sign_in_button.set_sensitive(all(self.fields()))

    def on_sign_in(self, *_args):
        if not all(self.fields()):
            if self.server.get_text().strip() and not self.fields()[0]:
                self.show_error(_('Use the server’s https:// address'))
            return
        self.sign_in_button.set_sensitive(False)
        self.error.set_visible(False)
        self.session.sign_in(*self.fields(), self.finished)

    def finished(self, error):
        if error is not None:
            self.show_error(error)
            self.on_changed()
            return
        then = self.then
        self.close()
        then()

    def show_error(self, text):
        self.error.set_label(text)
        self.error.set_visible(True)
