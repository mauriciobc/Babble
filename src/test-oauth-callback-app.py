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

from src import appsettings, linkrouting, pagenav  # noqa: E402
from src.main import BabbleApplication  # noqa: E402
from src.window import BabbleWindow  # noqa: E402
from gettext import gettext as _  # noqa: E402

from testkit import oauthkit as kit  # noqa: E402
from testkit.oauthkit import (PrefillView, REDIRECT_URI,
                              SearchClient, check)

# --- The application delivers a duplicated dispatch once ---------------

app = BabbleApplication('0.1.0')
# Windows may only join the application after it has started up.
app.register(None)
app.emit('startup')
uri = Gio.File.new_for_uri(REDIRECT_URI)
app.on_open(None, [uri], 1, None)
win = app.props.active_window
check('the redirect opened the window', isinstance(win, BabbleWindow))

delivered = []
win.on_oauth_callback = delivered.append
app.on_open(None, [uri, uri], 2, None)
check('a duplicated dispatch is delivered once',
      delivered == [REDIRECT_URI])
check('a later dispatch reuses that window',
      app.props.active_window is win)

# --- Open With: local files go to the composer, web URIs nowhere --------

composed = []
win.compose_with_files = lambda paths=(): composed.append(list(paths))
delivered.clear()
png = Gio.File.new_for_uri('file:///tmp/babble-open-with/picture.png')
app.on_open(None, [png], 1, None)
check('an opened image is handed to the composer',
      composed == [['/tmp/babble-open-with/picture.png']] and delivered == [])
composed.clear()
app.on_open(None, [Gio.File.new_for_uri('http://example.com/x.png')], 1, None)
check('a web URI handed to open is ignored',
      composed == [] and delivered == [])
app.on_open(None, [uri, png], 2, None)
check('a redirect next to a file still reaches the OAuth handler',
      delivered == [REDIRECT_URI]
      and composed == [['/tmp/babble-open-with/picture.png']])

# --- The jump-list and routing actions ---------------------------------

check('the application exports the desktop actions',
      all(app.lookup_action(name) is not None
          for name in ('compose', 'show-notifications', 'open-uri',
                       'open-notifications')))
composed.clear()
# Activated on the action itself: through the application, a Babble
# already running on the session bus would be the one to receive it.
app.lookup_action('compose').activate(None)
check('app.compose opens the composer', composed == [[]])
routed = []
real_open_link = linkrouting.open_link
linkrouting.open_link = lambda widget, link: routed.append((widget, link))
try:
    app.lookup_action('open-uri').activate(
        GLib.Variant('s', 'https://example.social/@me/1'))
finally:
    linkrouting.open_link = real_open_link
check('app.open-uri routes the link through the window',
      routed == [(win, 'https://example.social/@me/1')])

# --- web+ap and web+mastodon links open inside the window --------------

import src.main as main_module  # noqa: E402


parse = main_module.parse_web_uri
check('web+ap names the object at its https address',
      parse('web+ap://mastodon.social/@Gargron/1')
      == ('resolve', 'https://mastodon.social/@Gargron/1'))
check('the web+ap scheme and host match in any case, the query is kept',
      parse('WEB+AP://Mastodon.Social/@Gargron/1?x=1#top')
      == ('resolve', 'https://mastodon.social/@Gargron/1?x=1'))
check('a follow intent names the account',
      parse('web+mastodon://follow?uri=acct:a@b.c') == ('account', 'a@b.c'))
check('a follow intent may name a profile by its https address',
      parse('web+mastodon://follow?uri=https://b.c/@a')
      == ('resolve', 'https://b.c/@a'))
check('a share intent carries its text',
      parse('web+mastodon://share?text=hi%20there')
      == ('share', 'hi there'))
malformed = ('web+ap:///@Gargron/1', 'web+ap:@Gargron/1',
             'web+ap://user@evil.example/@a/1', 'web+ap://[bad/@a',
             'web+ap://javascript:alert(1)', 'web+ap:javascript:alert(1)',
             # What GVfs makes of the line above.
             'web+ap://javascript:0/', 'web+ap://localhost/@a/1',
             'web+ap://evil.example:99999/@a',
             'web+mastodon://follow?uri=javascript:alert(1)',
             'web+mastodon://follow?uri=http://plain.example/@a',
             'web+mastodon://follow?uri=https://u@evil.example/@a',
             'web+mastodon://follow', 'web+mastodon://evil?uri=acct:a@b.c',
             'web+mastodon:share?text=hi', 'https://mastodon.social/@a/1')
check('malformed links name nothing',
      [u for u in malformed if parse(u) is not None] == [])

launched_links = []
real_launch_external = linkrouting.launch_external
linkrouting.launch_external = lambda widget, link: launched_links.append(link)
real_open_link = linkrouting.open_link
real_open_profile = pagenav.open_profile
linked, profiles = [], []
linkrouting.open_link = lambda w, link, resolve=False: linked.append(
    (w, link, resolve))
pagenav.open_profile = lambda w, c, account=None, acct=None: profiles.append(
    (w, c, account, acct))
app_toasts = []
win.show_toast = app_toasts.append
win.account = {'id': '42', 'acct': 'me', 'instance': 'https://example.social'}
win.client = SearchClient({})
try:
    app.on_open(None, [Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1')], 1, None)
    check('web+ap reaches the resolver with the https address',
          linked == [(win, 'https://mastodon.social/@Gargron/1', True)])
    linked.clear()
    app.on_open(None, [Gio.File.new_for_uri(
        'web+mastodon://follow?uri=acct:a@b.c')], 1, None)
    check('a follow intent resolves the account',
          profiles == [(win, win.client, None, 'a@b.c')] and linked == [])
    profiles.clear()
    app.on_open(None, [Gio.File.new_for_uri(
        'web+mastodon://follow?uri=https://b.c/@a')], 1, None)
    check('a follow intent by address resolves it',
          linked == [(win, 'https://b.c/@a', True)])
    linked.clear()
    delivered.clear()
    composed.clear()
    app.on_open(None, [Gio.File.new_for_uri(u) for u in malformed], 1, None)
    check('malformed links are ignored',
          linked == [] and profiles == [] and app_toasts == []
          and delivered == [] and composed == [] and launched_links == [])
    app.on_open(None, [uri, Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1'), png], 3, None)
    check('babble:// and file:// still work next to a web+ap link',
          delivered == [REDIRECT_URI]
          and composed == [['/tmp/babble-open-with/picture.png']]
          and len(linked) == 1)
finally:
    linkrouting.open_link = real_open_link
    pagenav.open_profile = real_open_profile

prefill = PrefillView()
win.present_composer = lambda: types.SimpleNamespace(view=prefill)
app.on_open(None, [Gio.File.new_for_uri('web+mastodon://share?text=hi')],
            1, None)
check('a share intent opens the composer with its text',
      prefill.texts == ['hi'])

# End to end: a web+ap post is looked up on the instance even with
# aggressive resolving off, and opened in the app.
opened = []
real_open_status = pagenav.open_status
pagenav.open_status = lambda w, c, status: opened.append(status)
resolving = appsettings.settings().get_boolean('aggressive-resolving')
appsettings.settings().set_boolean('aggressive-resolving', False)
try:
    win.client = SearchClient({'statuses': [{'id': '1'}]})
    app.on_open(None, [Gio.File.new_for_uri(
        'web+ap://mastodon.social/@Gargron/1')], 1, None)
    check('a web+ap link is searched for with resolve',
          [s[2] for s in win.client.searches]
          == [{'q': 'https://mastodon.social/@Gargron/1', 'resolve': 'true',
               'limit': 1}])
    check('the found post is opened in the app',
          opened == [{'id': '1'}] and launched_links == [])
finally:
    pagenav.open_status = real_open_status
    appsettings.settings().set_boolean('aggressive-resolving', resolving)
    linkrouting.launch_external = real_launch_external

# Before a session exists the link waits, and is opened once signed in.
app_toasts.clear()
prefill.texts.clear()
win.client = win.account = None
win.main_stack.set_visible_child_name('login')
app.on_open(None, [Gio.File.new_for_uri('web+mastodon://share?text=hi')],
            1, None)
check('a link opened while signed out waits for a session',
      prefill.texts == [] and app_toasts == [_('Sign in to open this link')])
win.client = SearchClient({})
win.account = {'id': '42', 'acct': 'me', 'instance': 'https://example.social'}
win.main_stack.set_visible_child_name('shell')
check('signing in opens the waiting link', prefill.texts == ['hi'])
win.main_stack.set_visible_child_name('login')
win.main_stack.set_visible_child_name('shell')
check('the waiting link is opened once', prefill.texts == ['hi'])
win.client = win.account = None
win.main_stack.set_visible_child_name('login')


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all oauth callback app checks passed')
