#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Na Real plugin, against a fake Na Real on loopback.

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
from testkit.narealplugin import LINK, real_timers, timer_calls  # noqa: E402
from testkit.narealplugin import (DEAD,
    GLib,
    INVITE,
    ME,
    SERVER,
    Server,
    accounts,
    anchor,
    check,
    column,
    composed,
    context,
    host,
    invitation,
    menu,
    module,
    no_panel_for,
    open_questions,
    panel_now,
    pluginhost,
    settings,
    settled,
    share_link,
    shut,
    spin,
    toasts,
    window)  # noqa: E402
import ast

check('the host starts', kit.started)
check('the Na Real plugin is shipped and found', kit.shipped)
check('it loads', kit.loaded)

# --- The server setting --------------------------------------------------

check('the default server is the main Na Real instance',
      module.DEFAULT_SERVER == 'https://na-real.bolha.dev')
normalize = module.normalize_server
check('a bare host name is taken as https, and a slash dropped',
      normalize('na-real.example/') == 'https://na-real.example')
check('https is accepted',
      normalize('https://x.example') == 'https://x.example')
check('plain http is accepted on this machine only',
      normalize('http://localhost:8000') == 'http://localhost:8000'
      and normalize('http://127.0.0.1:1') == 'http://127.0.0.1:1'
      and normalize('http://[::1]:1') == 'http://[::1]:1'
      and normalize('http://x.example') is None)
check('other schemes and nothing are refused',
      normalize('ftp://x.example') is None and normalize('') is None
      and normalize(None) is None)

# --- No timers, no polling, no stored widgets (the source) ----------------

with open(module.__file__, encoding='utf-8') as source:
    tree = ast.parse(source.read())
named = {node.attr for node in ast.walk(tree)
         if isinstance(node, ast.Attribute)}
named |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
check('the plugin names no timer or idle source',
      not named & {'timeout_add', 'timeout_add_seconds', 'idle_add',
                   'GLib', 'Timer', 'sleep'})


def watched(real):
    def call(*args, **kwargs):
        caller = sys._getframe(1).f_code.co_filename
        if caller == module.__file__:
            timer_calls.append(caller)
        return real(*args, **kwargs)
    return call


GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add = (
    watched(real_timers[0]), watched(real_timers[1]),
    watched(real_timers[2]))

# --- The main menu ----------------------------------------------------------

check('the main menu has the two entries, in this order',
      [label for label, _activate in menu()]
      == ['My _Questions', '_Share My Questions Link'])
check('and the plugin has no post menu: no class, nothing registered',
      host.menu_extensions['babble_nareal'] == []
      and not any(isinstance(v, type)
                  and issubclass(v, module.plugin.PostMenuExtension)
                  for v in vars(module).values())
      and pluginhost.menu_items(anchor, {
          'id': '1', 'account': {'acct': 'alice'}, 'content': '<p>hi</p>',
          'url': 'https://mastodon.social/@x/1'}, 'https://mastodon.social')
      == [])

# My Questions.
Server.paths.clear()
open_questions()
check('My Questions goes to the dashboard and on to the login page',
      settled(SERVER + '/login')
      and Server.paths[:2] == ['/dashboard', '/login'])
check('the panel is on the Na Real server, and kept as the current one',
      column().panel.origin == SERVER
      and context.data.get('panel') is panel_now())
shut()

# --- Share My Questions Link ------------------------------------------------

del toasts[:]
Server.paths.clear()
Server.accepts.clear()
share_link()
check('an own page that answers 200 opens the composer, once, with the '
      'invitation, the link and the hashtag',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation(LINK))])
spin(0.5)
check('the composer is in the menu’s window, and nothing else happened',
      composed[0][0] is window and len(composed) == 1 and toasts == []
      and no_panel_for(0.2))
check('the probe asked the user’s own page, for HTML',
      Server.paths == [LINK] and Server.accepts == ['text/html'])
del composed[:]

context.settings.set('hashtag', 'ask')
share_link()
check('the configured hashtag is the one the invitation ends with',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation(LINK, 'ask'))])
context.settings.remove('hashtag')
del composed[:]

# The link is exact: the host in lower case, a plain @.
accounts.save_account(settings, {
    'id': '77', 'acct': 'Bob', 'username': 'Bob',
    'instance': 'https://Ursal.ZONE', 'token': 'T'})
Server.paths.clear()
share_link()
check('a mixed-case host is lower-cased, the @ is not percent-encoded',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation('/@Bob@ursal.zone'))]
      and Server.paths == ['/@Bob@ursal.zone'])
check('no path was asked for with %40', not any('%40' in x
                                                  for x in Server.paths))
accounts.save_account(settings, ME)
del composed[:]

# The user has never signed in to Na Real: its own page is a 404.
Server.own_status = 404
Server.session = False
Server.paths.clear()
share_link()
check('a 404 toasts that the user is not on Na Real yet, in the window',
      spin(until=lambda: toasts, seconds=5)
      and toasts == [(window, 'You are not on Na Real yet. Sign in from '
                      'My Questions first.')])
check('and opens the dashboard, not the composer',
      settled(SERVER + '/login') and composed == []
      and Server.paths[:3] == [LINK, '/dashboard', '/login']
      and context.data.get('panel') is panel_now())
shut()

for status in (500, 503):
    Server.own_status = status
    share_link()
    check('a %d still opens the composer with the invitation' % status,
          spin(until=lambda: composed, seconds=5)
          and composed == [(window, invitation(LINK))])
    del composed[:]
Server.own_status = 200

del toasts[:]
context.settings.set('server', DEAD)
share_link()
check('no answer at all (status 0) still opens the composer',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, '%s\n%s%s\n\n#nareal' % (INVITE, DEAD, LINK))]
      and toasts == [] and column() is None)
del composed[:]
context.settings.set('server', SERVER)

settings.set_string('active-account', '')
Server.paths.clear()
del toasts[:]
share_link()
check('with nobody signed in the user is told, and nothing else happens',
      toasts == [(window, 'Sign in to Babble first.')]
      and spin(0.5) and composed == [] and Server.paths == []
      and column() is None)
accounts.save_account(settings, ME)
del toasts[:]


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all nareal plugin checks passed')
