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

import os
import sys

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

from gi.repository import Adw, Gio  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()
Adw.init()

from src import emojichooser  # noqa: E402
from src import textemoji  # noqa: E402
from src.oauthpkce import (OAUTH_REDIRECT,  # noqa: E402
                          OOB_REDIRECT)
from gettext import gettext as _  # noqa: E402

from testkit import oauthkit as kit  # noqa: E402
from testkit.oauthkit import (REDIRECT_URI,
                              SECOND_URI, STATE,
                              StubClient, _real_client, check, fresh_window, pump, start_attempt)

# --- The same redirect twice: one exchange, one toast -------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('delivered redirect exchanges the code once', len(client.exchanges) == 1)
check('delivered redirect redirects to the app scheme',
      client.exchanges and client.exchanges[0][1] == OAUTH_REDIRECT)
check('delivered redirect settles one toast', len(toasts) == 1)
check('the toast reports the success',
      toasts == [_('Logged in as @me@example.social')])
check('no error toast after a success',
      not any('Authorization failed' in text for text in toasts))
check('the account is signed in',
      win.main_stack.get_visible_child_name() == 'shell'
      and win.client is client)
check('the token reached the secret store',
      client.stored == ('42', 'test-token'))
check('signing in activates the instance emoji bank',
      textemoji.instance_emoji_bank() == {
          'blobwave': 'https://example.social/custom/blobwave.png',
          'hushpup': 'https://example.social/custom/hushpup.png'})
check('emoji hidden from the picker still resolve when rendered',
      textemoji.lookup_emoji({}, 'hushpup')
      == 'https://example.social/custom/hushpup.png')
check('the chooser only offers picker-visible emoji',
      [e['shortcode'] for e in emojichooser.cached_custom_emojis(client)]
      == ['blobwave'])
check('a shortcode no layer knows stays literal',
      textemoji.lookup_emoji({}, 'nosuchemoji') is None)

# A third delivery, once the first has settled, must stay silent too.
toasts.clear()
win.on_oauth_callback(REDIRECT_URI)
pump()
check('a late redelivery does nothing', client.exchanges
      and len(client.exchanges) == 1 and toasts == [])

# --- The same redirect twice, refused by the instance -------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(fail_token=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('refused redirect is exchanged once', len(client.exchanges) == 1)
check('refused redirect settles one toast', len(toasts) == 1)
check('the toast reports the failure',
      toasts == [(_('Authorization failed') + ': invalid_grant')])
check('no shell without an account',
      win.main_stack.get_visible_child_name() == 'login'
      and win.client is None)
toasts.clear()
win.on_oauth_callback(REDIRECT_URI)
pump()
check('the spent code is not offered again',
      len(client.exchanges) == 1 and toasts == [])

# --- A failed verification still settles exactly one toast --------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(fail_verify=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(REDIRECT_URI)
pump()
check('refused verification is exchanged once', len(client.exchanges) == 1)
check('refused verification settles one toast',
      toasts == [(_('Could not verify the account') + ': '
                 + _('The access token is invalid'))])
check('no shell without a verified account',
      win.main_stack.get_visible_child_name() == 'login')

for field, payload in (('TOKEN', '{}'), ('TOKEN', '[]'), ('TOKEN', 'null'),
                       ('TOKEN', '{"access_token": 7}'),
                       ('ACCOUNT', '{}'), ('ACCOUNT', '{"id": 7}')):
    win, toasts = fresh_window()
    client = start_attempt(win, StubClient())
    setattr(client, field, payload)
    win.auth.on_oauth_callback(REDIRECT_URI)
    pump()
    check('invalid OAuth %s %s settles with no keyring persistence' % (field, payload),
          not win.auth.waiting and client.stored is None and len(toasts) == 1
          and win.account is None)

# --- A reply for a superseded attempt is dropped ------------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_cancel_login_clicked(None)
client.flush()
pump()
check('an abandoned attempt never signs the account back in',
      len(client.exchanges) == 1 and toasts == [] and win.client is None
      and win.main_stack.get_visible_child_name() == 'login')

# A second exchange for a superseded attempt cannot toast either.
win, toasts = fresh_window()
first = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.auth.begin()
first.flush()
pump()
check('a reply from a superseded attempt is silent',
      toasts == [] and win.client is None)

# State-validated denial settles once without spending a token code.
for error_code, message in (('access_denied', 'Authorization was denied.'),
                            ('server_error',
                             'The server could not authorize this request.')):
    win, toasts = fresh_window()
    client = start_attempt(win, StubClient())
    uri = OAUTH_REDIRECT + '?error=' + error_code + '&state=' + STATE
    check('scheme denial %s is accepted' % error_code,
          win.auth.on_oauth_callback(uri))
    win.auth.on_oauth_callback(uri)
    check('scheme denial %s settles once with no exchange' % error_code,
          not win.auth.waiting and client.exchanges == []
          and toasts == ['Authorization failed: ' + message])

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
for query in ('error=access_denied&state=wrong',
              'error=access_denied&state=' + STATE + '&state=' + STATE,
              'error=access_denied&error=access_denied&state=' + STATE,
              'code=c&error=access_denied&state=' + STATE,
              'code=c&code=d&state=' + STATE):
    check('malformed or foreign OAuth answer is ignored: ' + query,
          not win.auth.on_oauth_callback(OAUTH_REDIRECT + '?' + query)
          and win.auth.waiting and client.exchanges == [])
win.auth.on_oauth_callback(REDIRECT_URI)
check('denial cannot override a pending code exchange',
      not win.auth.on_oauth_callback(
          OAUTH_REDIRECT + '?error=access_denied&state=' + STATE)
      and win.auth.waiting and len(client.exchanges) == 1 and toasts == [])
client.flush()
pump()

# --- A new attempt is not blocked by the spent code --------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(REDIRECT_URI)
pump()
second = start_attempt(win, StubClient())
win.on_oauth_callback(SECOND_URI)
pump()
check('a second login with a new code works',
      len(second.exchanges) == 1
      and [code for code, _ in second.exchanges] == ['code-two'])
check('the second login settles one toast', len(toasts) == 2)

# --- The pasted fallback code is spent once too ------------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.auth._fallback = True
win.code_entry.set_text('pasted-code')
win.on_code_entered(win.code_entry)
win.on_code_entered(win.code_entry)
pump()
check('a pasted code is exchanged once', len(client.exchanges) == 1)
check('a pasted code answers the out of band redirect',
      client.exchanges[0][1] == OOB_REDIRECT)
check('a pasted code settles one toast', len(toasts) == 1)

# --- One attempt, one exchange: a second code waits for the first ------

win, toasts = fresh_window()
client = start_attempt(win, StubClient(deferred=True))
win.on_oauth_callback(REDIRECT_URI)
win.on_oauth_callback(SECOND_URI)
check('a busy attempt takes no second code',
      [code for code, _ in client.exchanges] == ['code-once'])
client.flush()
pump()
check('the first code still settles the attempt', len(toasts) == 1
      and toasts == [_('Logged in as @me@example.social')])
win.on_oauth_callback(SECOND_URI)
client.flush()
pump()
check('the rejected code is not exchanged after the attempt',
      [code for code, _ in client.exchanges] == ['code-once']
      and len(toasts) == 1)

# --- A callback for another authorization is refused ------------------

win, toasts = fresh_window()
client = start_attempt(win, StubClient())
win.on_oauth_callback(OAUTH_REDIRECT + '?code=stolen&state=someone-elses')
pump()
check('a callback whose state belongs elsewhere is not exchanged',
      client.exchanges == [] and toasts == [])

# A callback with the state stripped must be refused too: without the
# check, anything able to hand Babble a babble:// URL could redeem a
# code of its own choosing into the signed-in session.
win.on_oauth_callback(OAUTH_REDIRECT + '?code=stolen-no-state')
pump()
check('a callback with no state is not exchanged',
      client.exchanges == [] and toasts == [])

# Only the registered redirect is read, even with the right state.
for wrong in ('babble://elsewhere/callback', 'babble://oauth/other',
              'https://oauth/callback'):
    win.on_oauth_callback(wrong + '?code=stolen&state=' + STATE)
pump()
check('a callback to another host or path is not exchanged',
      client.exchanges == [] and toasts == [])

# A malformed authority (urlsplit raises on an unmatched '[') is
# refused, not raised out of the open handler.
win.on_oauth_callback('babble://[oauth/callback?code=stolen&state=' + STATE)
pump()
check('a malformed callback is refused without raising',
      client.exchanges == [] and toasts == [])

# The host of a URI is case-insensitive; the registered redirect still
# matches when a handler hands it back in another case.
check('the redirect host matches in any case',
      _real_client.parse_callback_uri(
          'babble://OAuth/callback?code=c&state=' + STATE) == 'c')

# The real attempt still works afterwards.
win.on_oauth_callback(REDIRECT_URI)
pump()
check('the matching state is still accepted',
      [code for code, _ in client.exchanges] == ['code-once']
      and len(toasts) == 1)

# --- The login field refuses a plaintext server -------------------------

win, toasts = fresh_window()
check('a bare host is accepted',
      win._instance_domain('mastodon.social') == 'mastodon.social')
check('an https address is accepted',
      win._instance_domain('https://fosstodon.org/') == 'fosstodon.org')
check('a plaintext address is refused',
      win._instance_domain('http://plain.example') == ''
      and win._instance_domain('HTTP://Plain.Example') == '')

win.instance_entry.set_text('http://plain.example')
win.on_login_clicked(None)
pump()
check('a plaintext login never starts an attempt',
      win.auth.client is None
      and win.main_stack.get_visible_child_name() == 'login')


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all oauth callback checks passed')
