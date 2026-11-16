#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Na Real login form, as the plugin fills it in.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

from testkit import narealplugin as kit  # noqa: E402
from testkit.narealplugin import DETOUR, ME, SERVER, Server, accounts, check, column, composed, context, dom, host, module, open_questions, opened, panel_now, run, settings, settled, shut, spin, toasts, wait_dom  # noqa: E402

# --- The login form -------------------------------------------------------

Server.posts.clear()
open_questions()
settled(SERVER + '/login')
check('the instance field gets the account’s host',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
check("the page's own handler heard the input event",
      dom('document.body.dataset.heard') == 'mastodon.social')
spin(0.8)
check('the login form was never submitted', Server.posts == [])
shut()

Server.prefilled_login = True
open_questions()
settled(SERVER + '/login')
spin(0.8)
check('a field that already has a value is left alone',
      dom("document.getElementById('instance').value") == '@me@else.example'
      and dom('document.body.dataset.heard') is None)
check('and nothing was submitted either', Server.posts == [])
shut()
Server.prefilled_login = False

# The OAuth detour: a login page on another origin is left alone.
open_questions()
settled(SERVER + '/login')
wait_dom("document.getElementById('instance').value", 'mastodon.social')
del toasts[:]
column().view.load_uri(DETOUR + '/login')
settled(DETOUR + '/login')
spin(1.0)
marker = []
column().evaluate('test-reader',
                  "String(document.getElementById('instance').value)",
                  lambda value, error: marker.append(
                      value.to_string() if value is not None else error))
spin(until=lambda: marker, seconds=10)
check('another origin’s login page is not filled, and nothing is said',
      marker == [''] and toasts == [] and 'babble_nareal' in host.contexts)
shut()

# Missing selectors do nothing: the pages without what the plugin uses.
Server.bare = True
Server.session = True
open_questions()
settled(SERVER + '/dashboard')
bare_panel = panel_now()
outcomes = []
for path in ('/login', '/dashboard', '/archived', '/settings'):
    for script in module.scripts_for(context, path):
        outcomes.append(run(script, bare_panel))
check('scripts on pages without their elements neither throw nor do '
      'anything',
      all(error is None for _result, error in outcomes)
      and [result for result, _e in outcomes]
      == ['skipped', 'installed', 'installed', 'installed', 'installed',
          'installed'])
settings.set_string('active-account', '')
check('with nobody signed in the login page gets no script',
      module.scripts_for(context, '/login') == [])
accounts.save_account(settings, ME)
check('there is no script for a page that has none to run',
      module.scripts_for(context, '/@me@mastodon.social') == []
      and module.scripts_for(context, '/') == [])
opened_before = len(opened)
check('and the page is untouched: no message, no toast, no composer',
      run('document.body.click(); document.body.innerHTML.length')[1]
      is None and spin(0.4) and len(opened) == opened_before
      and toasts == [] and composed == [])
shut()
Server.bare = False
Server.session = False

# JavaScript off: one toast per panel, not per load.
settings.set_boolean('web-javascript', False)
del toasts[:]
open_questions()
settled(SERVER + '/login')
panel_a = panel_now()
check('with JavaScript off the user is told, in the panel’s own column',
      spin(until=lambda: toasts, seconds=5)
      and toasts[0][1] == 'Na Real needs JavaScript. Turn it on in '
      'Preferences.' and toasts[0][0] is column())
panel_a.load_uri(SERVER + '/login')
settled(SERVER + '/login')
spin(0.5)
panel_a.load_uri(SERVER + '/login?again')
settled(SERVER + '/login?again')
spin(0.5)
check('and only once for that panel, however many pages it loads',
      len(toasts) == 1)
open_questions()
settled(SERVER + '/login')
check('a new panel is told again',
      spin(until=lambda: len(toasts) == 2, seconds=5))
shut()
settings.set_boolean('web-javascript', True)


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all nareal login checks passed')
