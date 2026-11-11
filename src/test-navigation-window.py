#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window's side of place routing: what `PlaceHost` is wired to.

`test-navigation` drives `Navigation` against a stand-in host, which cannot
notice a callback wired to the wrong window method. This builds the real
window and follows the local and federated places through it: building
them, the stream and auto-refresh the window starts for them, closing one
the way the sidebar does when the server will not serve it, and the
teardown on an account switch. Exits 77 (skip) without a display."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import types

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Adw.init()
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mastodon  # noqa: E402
from src.streaming import FEDERATED, LOCAL  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
window = BabbleWindow(application=Gio.Application.get_default())
window.main_stack.set_visible_child_name('shell')
window.client = mastodon.MastodonClient('https://example.org')
window.client.token = 'token'
window.account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
window.settings.set_boolean('live-updates', True)
window.settings.set_boolean('public-live-updates', True)
window.settings.set_string('auto-refresh', '60')

wanted = []
window.streaming.set_streams = lambda streams: wanted.append(set(streams))
window.streaming.start = lambda client: None
window.notifier.start_polling = lambda: None

routing = window.navigation
host = routing.host
check('the host is wired to the window, method for method',
      host.make_sidebar_control == window.make_sidebar_control
      and host.timeline_place == window._timeline_place
      and host.restricted_public_places == window._restricted_public_places
      and host.notify_place_built == window._public_place_built
      and host.sync_streaming == window._sync_streaming)
check('and hands out the window\'s client and account as they are now',
      host.client() is window.client and host.account() is window.account)
switched = {'id': '2', 'acct': 'you', 'instance': 'https://example.org'}
window.account = switched
check('including after an account switch', host.account() is switched)
window.account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}

local = routing.place_page('local')
federated = routing.place_page('federated')
check('the window builds both public places', local and federated)
check('they are timeline pages, found by tag',
      set(routing.public_pages) == {'local', 'federated'}
      and all(isinstance(page, TimelinePage)
              for page in routing.public_pages.values()))
check('the window counts them among its timeline pages',
      all(page in window.stream_router.timeline_pages()
          for page in routing.public_pages.values()))
check('it started a stream for each',
      bool(wanted) and {LOCAL, FEDERATED} <= wanted[-1])
check('and switched on the auto-refresh of each',
      all(page._auto_refresh_id for page in routing.public_pages.values()))

# The stream router's host, on the real window: one event through the
# window's own handler (the one the manager's signal is connected to)
# reaches the real page, so a callable wired to the wrong window member
# cannot pass. The pages' methods are recorded, not run.
calls = []


def record(label):
    return lambda *args: calls.append((label,) + args)


def event(stream, name, payload):
    del calls[:]
    window._on_stream_event(None, stream, name, payload)
    return list(calls)


pushed = {'id': 'S1'}
local.on_stream_update = record('local.update')
federated.on_stream_update = record('federated.update')
check('a local update reaches the local page and nothing else',
      event(LOCAL, 'update', pushed) == [('local.update', pushed)])
check('a federated update reaches the federated page',
      event(FEDERATED, 'update', pushed) == [('federated.update', pushed)])
window.timeline_page = TimelinePage(window.client, mode='home',
                                    autoload=False)
window.timeline_page.on_stream_update = record('home.update')
check('a user-stream update reaches the window\'s home timeline',
      event(('user',), 'update', pushed) == [('home.update', pushed)])
local.on_stream_delete = record('local.delete')
federated.on_stream_delete = record('federated.delete')
window.timeline_page.on_stream_delete = record('home.delete')
# The public pages are also built places, so the fan-out has always
# reached them a second time through the places; kept as it was.
check('a delete reaches the home, local and federated timelines',
      event(('user',), 'delete', '7')
      == [('home.delete', '7'), ('local.delete', '7'),
          ('federated.delete', '7'), ('local.delete', '7'),
          ('federated.delete', '7')])
window.timeline_page.refresh = record('home.refresh')
check('filters_changed refreshes the home timeline',
      event(('user',), 'filters_changed', None) == [('home.refresh',)])
window.notifications_page = types.SimpleNamespace(
    on_stream_notification=record('notifications.notification'))
window.notifier.handle = record('notifier.handle')
window.note_background_notification = record('window.note')
mention = {'id': 'N1', 'type': 'mention'}
check('a notification reaches the page, the notifier and the window, '
      'in that order',
      event(('user',), 'notification', mention)
      == [('notifications.notification', mention),
          ('notifier.handle', mention), ('window.note', mention)])
window._refresh_admin_tool = record('window.admin')
check('a report asks the window to refresh the admin tool',
      event(('user',), 'report', None) == [('window.admin', 'reports')])
window.timeline_page = None
window.notifications_page = None
del local.on_stream_update, federated.on_stream_update
del local.on_stream_delete, federated.on_stream_delete
del window.notifier.handle, window.note_background_notification
del window._refresh_admin_tool

# A feed the server will not serve: the sidebar refresh drops its page,
# its child and its stream, and leaves the other alone.
window._restricted_public_places = lambda: {'local'}
window._refresh_sidebar()
window._sync_streaming()
check('a closed feed keeps no page',
      'local' not in routing.pages and 'local' not in routing.children
      and 'local' not in routing.public_pages
      and 'federated' in routing.public_pages)
check('and no stream', LOCAL not in wanted[-1] and FEDERATED in wanted[-1])

window._teardown_session()
check('an account switch empties every cache',
      not routing.pages and not routing.children
      and not routing.public_pages)
window._restricted_public_places = lambda: set()
check('and a place can be built again afterwards',
      routing.place_page('local') is not None
      and 'local' in routing.public_pages)

if failures:
    raise SystemExit('%d navigation window checks failed' % len(failures))
print('all navigation window checks passed')
