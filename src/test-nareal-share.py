#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Na Real share button, taken over for Babble's composer.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

from testkit import narealplugin as kit  # noqa: E402
from testkit.narealplugin import LINK  # noqa: E402
from testkit.narealplugin import (ME,
    OWN_PATH,
    SERVER,
    Server,
    TITLE,
    accounts,
    check,
    column,
    composed,
    context,
    dom,
    host,
    invitation,
    module,
    open_questions,
    panel_now,
    restore_open_link,
    run,
    settings,
    settled,
    shut,
    spin,
    stub_open_link,
    toasts)  # noqa: E402
import json

# --- The share button ---------------------------------------------------------

CLICK = '''(function (id) {
  var b = document.getElementById(id);
  var event = new MouseEvent('click', {bubbles: true, cancelable: true});
  b.dispatchEvent(event);
  return event.defaultPrevented ? 'prevented' : 'passed';
})(%s)'''


def click(ident):
    return dom(CLICK % json.dumps(ident))


def goto(path):
    """Show *path* of the fake in the open panel, once its scripts ran
    (the page sets `copied` last; `settled` alone can return before the
    new page replaced the old one)."""
    panel_now().load_uri(SERVER + path)
    settled(SERVER + path)
    return spin(until=lambda: dom('location.pathname') == path.split('?')[0]
                and dom('document.body.dataset.copied') == '0', seconds=8)


def open_dashboard():
    open_questions()
    spin(until=lambda: panel_now() is not None, seconds=5)
    return spin(
        until=lambda: dom('location.pathname') == '/dashboard'
        and dom('document.body.dataset.copied') == '0', seconds=8)


def own_link():
    return SERVER + '/' + OWN_PATH


def shared():
    return TITLE + '\n' + own_link()


def settings_requests():
    return Server.paths.count('/settings')


stub_open_link()
Server.session = True
for path, label in (('/dashboard', 'dashboard'), ('/archived', 'archive')):
    del composed[:]
    open_dashboard()
    if path != '/dashboard':
        goto(path)
    check('the share listener is installed on the %s' % label,
          spin(until=lambda: dom('!!window.__narealShare') is True,
               seconds=6))
    Server.paths.clear()
    check('the click on Compartilhar is stopped (%s)' % label,
          click('share') == 'prevented')
    check('the page’s own handler never ran, so nothing is copied (%s)'
          % label, dom('document.body.dataset.copied') == '0')
    check('Babble’s composer gets the title and the link read from the '
          'user’s settings, in the panel’s column (%s)' % label,
          spin(until=lambda: composed, seconds=6)
          and composed == [(column(), shared())])
    check('the settings were fetched, once (%s)' % label,
          settings_requests() == 1)
    del composed[:]
    check('the click on the label inside the button counts too (%s)'
          % label, click('share-label') == 'prevented'
          and spin(until=lambda: composed, seconds=6)
          and composed == [(column(), shared())])
    del composed[:]
    run(module.scripts_for(context, path)[-1])
    click('share')
    spin(0.8)
    check('the listener is installed once per page (%s)' % label,
          len(composed) == 1 and dom('document.body.dataset.copied') == '0')
    del composed[:], toasts[:]
    check('another button, and text in the navbar, are left to the page '
          '(%s)' % label,
          click('other-button') == 'passed' and click('whoami') == 'passed'
          and dom('document.body.dataset.decoy') == '1')
    spin(0.6)
    check('and open nothing (%s)' % label, composed == [] and toasts == [])
    shut()

# On the settings page itself the form is already in the DOM.
open_dashboard()
goto('/settings')
spin(until=lambda: dom('!!window.__narealShare') is True, seconds=6)
Server.paths.clear()
check('on /settings the click is stopped', click('share') == 'prevented'
      and dom('document.body.dataset.copied') == '0')
check('and the title and link come from the page, with no second request',
      spin(until=lambda: composed, seconds=6)
      and composed == [(column(), shared())] and spin(0.5)
      and settings_requests() == 0)
del composed[:]

# The settings cannot be read: the host builds the link from the account.
open_dashboard()
goto('/archived')
spin(until=lambda: dom('!!window.__narealShare') is True, seconds=6)
for what, setup in (
        ('a /settings that answers an error with the form in it',
         lambda: setattr(Server, 'settings_status', 500)),
        ('a /settings whose connection drops',
         lambda: setattr(Server, 'settings_drop', True)),
        ('a /settings with an empty identification',
         lambda: setattr(Server, 'settings_url', '')),
        ('a /settings that redirects to the login (the cookie expired)',
         lambda: setattr(Server, 'session', False))):
    Server.settings_status, Server.settings_drop = 200, False
    Server.settings_url, Server.session = OWN_PATH, True
    setup()
    check('%s falls back to the account’s own link' % what,
          click('share') == 'prevented'
          and spin(until=lambda: composed, seconds=6)
          and composed == [(column(), invitation(LINK))])
    del composed[:]
    spin(0.3)
    check('and only once (%s)' % what, composed == [])
Server.settings_status, Server.settings_drop = 200, False
Server.settings_url, Server.session = OWN_PATH, False
settings.set_string('active-account', '')
del toasts[:]
check('and with nobody signed in either the user is told',
      click('share') == 'prevented'
      and spin(until=lambda: toasts, seconds=6)
      and toasts == [(column(), 'Couldn’t read your Na Real link.')]
      and composed == [])
accounts.save_account(settings, ME)
del toasts[:]
shut()

# Messages the host must drop, and the good ones, sent through the real
# channel.
open_questions()
settled(SERVER + '/login')
msg_panel = panel_now()


def post(data):
    run('window.webkit.messageHandlers.babble.postMessage(%s)'
        % json.dumps(data), msg_panel)


del composed[:], toasts[:]
LONG_TEXT = 'x' * 501
for bad in ({'share': {'text': 'x', 'url': 'https://thread.example/@a'}},
            {'share': {'text': 'x', 'url': 'http://127.0.0.1:1/a'}},
            {'share': {'text': 'x', 'url': 'ftp://127.0.0.1/a'}},
            {'share': {'text': 'x', 'url': 'javascript:alert(1)'}},
            {'share': {'text': 'x', 'url': SERVER + '/a b'}},
            {'share': {'text': 'x', 'url': SERVER + '/a\tb'}},
            {'share': {'text': 'x', 'url': SERVER + '/' + 'a' * 2100}},
            {'share': {'text': LONG_TEXT, 'url': SERVER + '/a'}},
            {'share': {'text': 'a\x07b', 'url': SERVER + '/a'}},
            {'share': {'text': 'a\nb', 'url': SERVER + '/a'}},
            {'share': {'text': 5, 'url': SERVER + '/a'}},
            {'share': {'text': 'x', 'url': 5}},
            {'share': {'text': 'x'}},
            {'share': 'x'}, {'share': None}, {'share': [SERVER]},
            {'text': 'x', 'url': SERVER + '/a'}, [SERVER], 'share', None):
    post(bad)
spin(0.8)
check('a foreign origin, a bad scheme, whitespace, an over-long url or '
      'text, control characters and anything else are dropped silently',
      composed == [] and toasts == [] and msg_panel.is_open
      and 'babble_nareal' in host.contexts)
post({'share': {'text': 'x' * 500, 'url': SERVER + '/' + 'a' * 100}})
check('a text of exactly 500 characters is accepted',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), 'x' * 500 + '\n' + SERVER + '/' + 'a' * 100)])
del composed[:]
post({'share': {'text': '', 'url': SERVER + '/@a@b.example'}})
check('an empty text is left out, the link alone goes',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), SERVER + '/@a@b.example')])
del composed[:]
post({'share': {'url': SERVER + '/@a@b.example'}})
check('so is a missing one',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), SERVER + '/@a@b.example')])
del composed[:]
post({'share': {'text': 'Hello', 'url': SERVER + '/@a@b.example'}})
check('title and link are joined by a line break',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), 'Hello\n' + SERVER + '/@a@b.example')])
del composed[:]
post({'share': {}})
check('an empty share (the page could not read it) falls back to the '
      'account’s link',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), invitation(LINK))])
shut()
restore_open_link()
Server.session = False


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all nareal share checks passed')
