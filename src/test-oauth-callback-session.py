#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""OAuth callback delivery is idempotent.

A scheme handler can be entered more than once for the same redirect: the
browser retries the navigation, a spawned ``babble %u`` races a D-Bus
Open, or the shell hands the URI over twice in one dispatch. Every
delivery used to start a token exchange, and since /oauth/token spends the
authorization code, the second one came back invalid_grant and toasted an
error on top of the login that had just succeeded.

Drives the same redirect twice and asserts one exchange and one toast per
auth attempt, plus the routes that could leak a second attempt: a stale
reply, a fresh login and a duplicated dispatch.

Exits 77 (skip) when no display is available."""

import json
import os
import sys
import types

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

# Own Xvfb + X11 backend: the window under test is a real one, so the
# session's WAYLAND_DISPLAY must not leak in. The display has to be set
# before GDK reads it, which is before ``import gi``.
from testkit.xvfb import start_xvfb  # noqa: E402

start_xvfb()
os.environ.setdefault('GDK_BACKEND', 'x11')

# The window reads the account list on startup; keep the test off the
# user's real settings.
os.environ['GSETTINGS_SCHEMA_DIR'] = os.path.join(builddir, 'data')
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio, GLib  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()
Adw.init()

from src import linkrouting  # noqa: E402
from src import textemoji  # noqa: E402
from src.oauthpkce import (ADMIN_SCOPES, OAUTH_REDIRECT,  # noqa: E402
                          )
from src.window import BabbleWindow  # noqa: E402
from gettext import gettext as _  # noqa: E402

from testkit import oauthkit as kit  # noqa: E402
from testkit.oauthkit import (AdminScopeClient, BrowserLauncher,  # noqa: E402
                              DeferredTokenClient, FakeComposeView,
                              RecordingLauncher, REDIRECT_URI,
                              RevokeClient, StubClient, check, fresh_window, launched, pump, start_attempt,
                              successful_clear, uris)

# --- Files opened before signing in wait for the session ----------------




win, toasts = fresh_window()
fake_view = FakeComposeView()
presented = []
win.present_composer = lambda: (presented.append(True)
                                or types.SimpleNamespace(view=fake_view))
win.compose_with_files(['/tmp/a.png', '/tmp/b.png'])
win.compose_with_files(['/tmp/b.png', '/tmp/c.mp4'])
check('files opened while signed out wait for a session',
      presented == [] and fake_view.batches == []
      and toasts == [_('Sign in to post the files')] * 2)
toasts.clear()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
pump()
check('signing in hands the waiting files to the composer, once each',
      presented == [True]
      and fake_view.batches == [['/tmp/a.png', '/tmp/b.png',
                                  '/tmp/c.mp4']])
presented.clear()
win.show_shell()
check('the waiting files are handed over once', presented == [])

# --- A stale keyring reply must not touch the session -------------------

# Reading the token is an out-of-process round trip, so a switch can be
# started before an earlier restore answers. The late reply used to win,
# showing the account the user had already switched away from.




win, toasts = fresh_window()
import src.auth as auth_module  # noqa: E402
_real_client_class = auth_module.MastodonClient
auth_module.MastodonClient = AdminScopeClient
try:
    # Sign in, so there is an account whose token the moderation
    # authorization has to replace.
    session = AdminScopeClient('https://mod.example')
    session.token = 'login-token'
    win.account = {'id': '42', 'acct': 'mod@mod.example',
                   'instance': 'https://mod.example',
                   'role': {'permissions': 16}}
    win.client = session
    win.store_account(win.account)
    opened = []
    win.open_url = lambda uri: opened.append(uri)

    granted = []
    win.authorize_admin(granted.append)
    pump()
    admin = AdminScopeClient.created[-1]
    check('the moderation authorization asks for the admin scopes',
          admin.registered_with == ADMIN_SCOPES)
    check('the moderation authorization opens the browser',
          len(opened) == 1)

    # Answer it with a redirect carrying that attempt's state.
    win.on_oauth_callback(OAUTH_REDIRECT + '?code=admin-once&state='
                          + admin._state)
    pump()
    check('the moderation token replaces the login token',
          session.token == 'test-token')
    check('the new token is what the keyring keeps',
          admin.stored == ('42', 'test-token'))
    check('the caller is told it was granted', granted == [True])
    check('a granted attempt leaves no authorization in flight',
          win.auth.client is None and not win.auth.admin)
    check('same-identity moderation retains the current role',
          win.account['role'] == {'permissions': 16})

    for verified_id, verified_instance in (('99', 'https://mod.example'),
                                           ('42', 'https://other.example')):
        outcome = []
        win.authorize_admin(outcome.append)
        pump()
        mismatch = AdminScopeClient.created[-1]
        mismatch.ACCOUNT = json.dumps({'id': verified_id, 'acct': 'other'})
        mismatch.instance = verified_instance
        previous_account = win.account
        previous_pages = win.page_home
        win.on_oauth_callback(OAUTH_REDIRECT + '?code=mismatch-'
                              + verified_id + verified_instance.rsplit('/', 1)[-1]
                              + '&state=' + mismatch._state)
        pump()
        check('moderation identity %s %s cannot persist or adopt' % (
            verified_id, verified_instance), mismatch.stored is None
              and not hasattr(mismatch, 'client_credentials')
              and win.account is previous_account and win.client is session
              and win.page_home is previous_pages and outcome == [False])

    outcome = []
    win.authorize_admin(outcome.append)
    pump()
    held = AdminScopeClient.created[-1]
    held.defer_store = True
    win.on_oauth_callback(OAUTH_REDIRECT + '?code=held&state=' + held._state)
    pump()
    old_account = win.account
    new_session = AdminScopeClient('https://next.example')
    new_session.token = 'next-token'
    win.account = {'id': '42', 'instance': 'https://next.example'}
    win.client = new_session
    held.held_store(None)
    held.held_store(None)
    check('a switch during the moderation keyring write keeps the pair '
          'and adopts nothing',
          outcome == [False] and win.client is new_session
          and new_session.token == 'next-token'
          and held.stored == ('42', 'test-token')
          and held.client_credentials
          == ('42', 'admin-client', 'admin-secret'))
    check('the switch is reported as such',
          toasts[-1:] == ['Moderation access was saved for the account '
                          'you switched away from.'])
    win.account, win.client = old_account, session

    # A second attempt that fails must still answer its caller, or the
    # dashboard waits on a button that will never reply.
    refused = []
    win.authorize_admin(refused.append)
    pump()
    retry = AdminScopeClient.created[-1]
    retry.fail_token = True
    win.auth._client = retry
    attempt = win.auth.begin()
    retry.request_token_async(
        'bad', auth_module._replying(win.auth._on_token_received, attempt))
    pump()
    check('a refused moderation attempt answers its caller',
          refused == [False])
finally:
    auth_module.MastodonClient = _real_client_class




win, toasts = fresh_window()
import src.windowlogin as windowlogin_module  # noqa: E402
import src.windowsession as windowsession_module  # noqa: E402
_real_client_class = windowsession_module.MastodonClient
windowsession_module.MastodonClient = DeferredTokenClient
try:
    # Which accounts the window decided to forget, if any. The whole
    # account, so another instance's account with the same id survives.
    forgotten = []
    win.remove_account = lambda acc: forgotten.append(acc)
    win._activate_account(
        {'id': '1', 'instance': 'https://a.example', 'acct': 'first'},
        login_only=True)
    win._activate_account(
        {'id': '2', 'instance': 'https://b.example', 'acct': 'second'})
    check('two keyring lookups are in flight',
          len(DeferredTokenClient.created) == 2)
    # The newer switch answers first; the older restore lands late.
    DeferredTokenClient.created[1].answer('token-b')
    pump()
    check('the newest lookup wins',
          win.account is not None and win.account['id'] == '2')
    DeferredTokenClient.created[0].answer('token-a')
    pump()
    check('a stale keyring reply does not change the account',
          win.account is not None and win.account['id'] == '2')
    check('a stale keyring reply does not replace the client',
          getattr(win.client, 'instance', None) == 'https://b.example')

    # A stale reply carrying no token must not report a missing login,
    # forget the account, or drop a signed-in window back to the login
    # page. This one is a restore, which is the case that used to do all
    # three.
    win._activate_account(
        {'id': '3', 'instance': 'https://c.example', 'acct': 'third'},
        login_only=True)
    win._activate_account(
        {'id': '4', 'instance': 'https://d.example', 'acct': 'fourth'})
    DeferredTokenClient.created[3].answer('token-d')
    pump()
    toast_count = len(toasts)
    DeferredTokenClient.created[2].answer(None)
    pump()
    check('a stale empty reply does not report a missing login',
          toasts[toast_count:] == [])
    check('a stale empty reply keeps the signed-in account',
          win.account is not None and win.account['id'] == '4')
    check('a stale empty reply keeps the shell visible',
          win.main_stack.get_visible_child_name() == 'shell')
    check('a stale empty reply does not forget the account',
          forgotten == [])

    # A locked or unreachable keyring is not "no login". Deleting the
    # account (and its Catch-Up snapshots) on GError was the bug.
    forgotten.clear()
    toast_count = len(toasts)
    win._activate_account(
        {'id': '9', 'instance': 'https://k.example', 'acct': 'kept'},
        login_only=True)
    DeferredTokenClient.created[-1].answer(
        None,
        GLib.Error('keyring locked', 'g-io-error-quark',
                   int(Gio.IOErrorEnum.FAILED)))
    pump()
    check('a keyring error does not forget the account',
          forgotten == [])
    check('a keyring error reports that the keyring failed',
          any('keyring' in t.lower() for t in toasts[toast_count:]))
    check('a keyring error leaves the login page up',
          win.main_stack.get_visible_child_name() == 'login')
finally:
    windowsession_module.MastodonClient = _real_client_class

# --- Logging out revokes the token, not just forgets it ----------------

# Removal also clears the keyring. This section stubs that call: the
# checks are about revoke-then-forget, and a session with no secret
# service raises out of the real clear before the window can finish.
import src.clientoauth as clientoauth_module
_real_clear_secret = windowsession_module.clear_secret_async
_real_mastodon_clear = clientoauth_module.clear_secret_async




windowsession_module.clear_secret_async = successful_clear
clientoauth_module.clear_secret_async = successful_clear

# Deleting the keyring entry leaves the token valid on the instance: a
# copy of it, from a backup or a shared machine, would keep working. So
# the removal has to ask the server, and must not wait for the answer --
# the local state has to be settled either way.

win, toasts = fresh_window()




try:
    win.account = {'id': '42', 'acct': 'me@example.social',
                   'instance': 'https://example.social'}
    win.client = RevokeClient()
    win.store_account(win.account)
    win._remove_account_now(win.account)
    pump()
    check('logging out revokes the token it was using',
          RevokeClient.revokes == [('live-token', 'cid', 'csecret')])
    check('logging out forgets the account',
          win.account is None
          and win.main_stack.get_visible_child_name() == 'login')

    # A server that cannot be reached must not strand the window signed in.
    RevokeClient.revokes = []
    RevokeClient.unreachable = True
    win, toasts = fresh_window()
    win.account = {'id': '43', 'acct': 'other@example.social',
                   'instance': 'https://example.social'}
    win.client = RevokeClient()
    win.store_account(win.account)
    win._remove_account_now(win.account)
    pump()
    check('an unreachable server does not keep the window signed in',
          win.account is None
          and win.main_stack.get_visible_child_name() == 'login')
finally:
    windowsession_module.clear_secret_async = _real_clear_secret
    clientoauth_module.clear_secret_async = _real_mastodon_clear

# Both exact account-bound keys are removed; deferred results cannot sign out B.
from src.oauthpkce import CLIENT_CREDENTIALS_KIND
for failed_kind in (None, '42', CLIENT_CREDENTIALS_KIND + '42'):
    win, toasts = fresh_window()
    removed = {'id': '42', 'instance': 'https://a.example', 'acct': 'removed'}
    other = {'id': '43', 'instance': 'https://a.example', 'acct': 'other'}
    remote = {'id': '42', 'instance': 'https://b.example', 'acct': 'remote'}
    secrets = {(a['instance'], prefix + a['id']): 'synthetic'
               for a in (removed, other, remote)
               for prefix in ('', CLIENT_CREDENTIALS_KIND)}
    pending_deletes = []

    def deferred_clear(instance, kind, callback=None, cancellable=None,
                       pending_deletes=pending_deletes):
        pending_deletes.append((instance, kind, callback))

    def answer_clear(pending_deletes=pending_deletes, failed_kind=failed_kind,
                     secrets=secrets):
        instance, kind, callback = pending_deletes.pop(0)
        error = None
        if kind == failed_kind:
            error = GLib.Error('synthetic keyring deletion failed',
                               'g-io-error-quark', 0)
        else:
            secrets.pop((instance, kind), None)
        callback(error)

    windowsession_module.clear_secret_async = deferred_clear
    clientoauth_module.clear_secret_async = deferred_clear
    try:
        win.account = removed
        win.client = RevokeClient(removed['instance'])
        win.store_account(removed)
        win._finish_account_removal(removed)
        check('local account removal completes before either keyring callback',
              win.account is None and len(pending_deletes) == 2
              and win.settings.get_strv('accounts') == [])
        win.account = other
        new_client = win.client = RevokeClient(other['instance'])
        answer_clear()
        check('first deletion result emits no premature warning', toasts == [])
        answer_clear()
        check('credential deletion is scoped to exact instance and account',
              all((a['instance'], prefix + a['id']) in secrets
                  for a in (other, remote) for prefix in ('', CLIENT_CREDENTIALS_KIND))
              and all(((removed['instance'], prefix + '42') in secrets)
                      == (prefix + '42' == failed_kind)
                      for prefix in ('', CLIENT_CREDENTIALS_KIND)))
        check('late keyring result preserves new session and reports one warning',
              win.account is other and win.client is new_client
              and len(toasts) == (0 if failed_kind is None else 1)
              and (not toasts or toasts[0].startswith(
                  'Could not remove all saved credentials')))
        if failed_kind is None:
            win._finish_account_removal(removed)
            answer_clear()
            answer_clear()
            check('deleting absent credential entries remains successful', toasts == [])
    finally:
        windowsession_module.clear_secret_async = _real_clear_secret
        clientoauth_module.clear_secret_async = _real_mastodon_clear

# --- Only web links reach the system launcher -------------------------
#
# Every link a server supplies ends in textemoji.launch_uri; the GTK
# launcher is swapped for one that records what it was handed.





win, toasts = fresh_window()
real_gtk = textemoji.Gtk
textemoji.Gtk = types.SimpleNamespace(UriLauncher=RecordingLauncher)
try:
    for uri in ('javascript:alert(1)', 'file:///etc/passwd',
                'smb://host/share', 'babble://oauth/callback?code=x',
                'https://[oops/x'):
        textemoji.launch_uri(win, uri)
    check('non-web links never reach the launcher, with a toast each',
          launched == [] and toasts == [_('Could not open link')] * 5)
    toasts.clear()
    linkrouting.open_link(win, 'https://[oops/x')
    check('a malformed link clicked in a post is refused, not raised',
          launched == [] and toasts == [_('Could not open link')])
    toasts.clear()
    launched.clear()
    textemoji.launch_uri(win, 'mailto:a@b.example')
    check('a mailto link reaches the UriLauncher',
          launched == ['mailto:a@b.example'] and toasts == [])
    RecordingLauncher.can = False
    toasts.clear()
    launched.clear()
    textemoji.launch_uri(win, 'mailto:a@b.example')
    check('a mailto without a handler toasts and skips launch',
          launched == ['mailto:a@b.example']
          and toasts == [_('Could not open link')])
    RecordingLauncher.can = True
    toasts.clear()
    launched.clear()
    textemoji.launch_uri(win, 'https://example.social/@me')
    check('a web link is launched',
          launched == ['https://example.social/@me'])
finally:
    textemoji.Gtk = real_gtk

# --- The login opens the browser through the window's own open_url -----
#
# Every other test here replaces win.open_url, so the method itself went
# missing without a single failure and a login never left the app.





win, toasts = fresh_window()
fallbacks = []
win._open_url_fallback = fallbacks.append
real_window_gtk = windowlogin_module.Gtk
windowlogin_module.Gtk = types.SimpleNamespace(UriLauncher=BrowserLauncher)
try:
    check('the window has an open_url for the login to call',
          callable(getattr(BabbleWindow, 'open_url', None)))
    BabbleWindow.open_url(win, 'https://example.social/oauth/authorize?x=1')
    check('the authorization page is handed to the launcher',
          uris == ['https://example.social/oauth/authorize?x=1']
          and fallbacks == [])
    BrowserLauncher.refuse = True
    BabbleWindow.open_url(win, 'https://example.social/oauth/authorize?x=2')
    check('a refusing portal falls back to the default handler',
          fallbacks == ['https://example.social/oauth/authorize?x=2'])
finally:
    windowlogin_module.Gtk = real_window_gtk




if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all oauth callback session checks passed')
