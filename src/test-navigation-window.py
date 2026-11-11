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
from src.desktopnotify import DesktopNotifier  # noqa: E402
from src.navigation import Navigation  # noqa: E402
from src.streaming import (FEDERATED, LOCAL, hashtag_stream,  # noqa: E402
                           list_stream)
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

# The stream router's host, on the real window. First that every member
# its callables name exists where they look for it, asserted on the class
# (or before anything is replaced on the instance), so a renamed or
# missing target fails here instead of being masked by a replacement.
for owner, names in (
        (BabbleWindow, ('_refresh_admin_tool',
                        'note_background_notification',
                        '_on_stream_event')),
        (DesktopNotifier, ('handle',)),
        (Navigation, ('stream_places', 'place_timelines'))):
    for name in names:
        check('%s.%s exists, as the stream host calls it'
              % (owner.__name__, name), callable(getattr(owner, name, None)))
check('the window has the attributes the host reads',
      all(hasattr(window, name) for name in (
          'timeline_page', 'notifications_page', 'notifier', 'navigation',
          'settings', 'stream_router')))
check('its notifier is a DesktopNotifier',
      isinstance(window.notifier, DesktopNotifier))

# The public-live-updates key, live on the real window: with both pages
# built, switching it off drops their streams and on brings them back.
window.settings.set_boolean('public-live-updates', False)
check('public-live-updates off drops the local and federated streams',
      not {LOCAL, FEDERATED} & window.stream_router.wanted_streams())
window.settings.set_boolean('public-live-updates', True)
check('and on brings them back',
      {LOCAL, FEDERATED} <= window.stream_router.wanted_streams())

# A list and a hashtag place, built through navigation's own entry point
# (no network: the pages are made, their first request just goes
# unanswered, as for the public places above).
del wanted[:]
list_nav = routing.place_page('list:12')
tag_nav = routing.place_page('tag:rust')
check('the window builds a list place and a hashtag place',
      list_nav is not None and tag_nav is not None
      and 'list:12' in routing.children and 'tag:rust' in routing.children)
check('building them synced the streams, and each one is wanted',
      bool(wanted) and {list_stream('12'), hashtag_stream('rust')}
      <= wanted[-1])
check('the router lists both places\' pages by stream',
      window.stream_router.place_streams()
      == {list_stream('12'): [routing.children['list:12'].timeline],
          hashtag_stream('rust'): [routing.children['tag:rust'].timeline]})
window.settings.set_boolean('public-live-updates', False)
check('public-live-updates off keeps the list stream and drops the hashtag',
      list_stream('12') in window.stream_router.wanted_streams()
      and hashtag_stream('rust') not in window.stream_router.wanted_streams())
window.settings.set_boolean('public-live-updates', True)

calls = []


def record(label):
    return lambda *args: calls.append((label,) + args)


def event(stream, name, payload):
    del calls[:]
    window._on_stream_event(None, stream, name, payload)
    return list(calls)


pushed = {'id': 'S1'}
# The pages themselves (place_page hands back the navigation page around
# them), as the router finds them.
local = routing.public_pages['local']
federated = routing.public_pages['federated']
local.on_stream_update = record('local.update')
federated.on_stream_update = record('federated.update')
check('a local update reaches the local page and nothing else',
      event(LOCAL, 'update', pushed) == [('local.update', pushed)])
check('a federated update reaches the federated page',
      event(FEDERATED, 'update', pushed) == [('federated.update', pushed)])
list_page = routing.children['list:12'].timeline
tag_page = routing.children['tag:rust'].timeline
list_page.on_stream_update = record('list.update')
tag_page.on_stream_update = record('tag.update')
check('an update on the list stream reaches that list\'s timeline only',
      event(list_stream('12'), 'update', pushed)
      == [('list.update', pushed)])
check('an update on the hashtag stream reaches that tag\'s timeline only',
      event(hashtag_stream('rust'), 'update', pushed)
      == [('tag.update', pushed)])
window.timeline_page = TimelinePage(window.client, mode='home',
                                    autoload=False)
window.timeline_page.on_stream_update = record('home.update')
check('a user-stream update reaches the window\'s home timeline',
      event(('user',), 'update', pushed) == [('home.update', pushed)])
local.on_stream_delete = record('local.delete')
federated.on_stream_delete = record('federated.delete')
list_page.on_stream_delete = record('list.delete')
tag_page.on_stream_delete = record('tag.delete')
window.timeline_page.on_stream_delete = record('home.delete')
deleted = event(('user',), 'delete', '7')
check('a delete reaches the home, public, list and hashtag timelines, '
      'each at least once, and nothing else',
      {entry[0] for entry in deleted} == {
          'home.delete', 'local.delete', 'federated.delete', 'list.delete',
          'tag.delete'}
      and all(entry[1:] == ('7',) for entry in deleted))
# KNOWN: the public pages are also built places, so the fan-out reaches
# local and federated twice. This documents existing behaviour, not a
# requirement: remove this check with the fix that stops the duplicate.
check('known: local and federated are reached twice by delete',
      [entry[0] for entry in deleted].count('local.delete') == 2
      and [entry[0] for entry in deleted].count('federated.delete') == 2
      and [entry[0] for entry in deleted].count('home.delete') == 1)
window.timeline_page.refresh = record('home.refresh')
check('filters_changed refreshes the home timeline',
      event(('user',), 'filters_changed', None) == [('home.refresh',)])
window.notifications_page = types.SimpleNamespace(
    on_stream_notification=record('notifications.notification'))
# The window's own members run for real, and the effect is observed: the
# notifier remembers the id it was handed, the window counts the mention
# (it is not visible), and the open admin dashboard is asked to refresh.
# No desktop notification is sent: no type is wanted.
window.settings.set_strv('desktop-notification-types', [])
mention = {'id': 'N1', 'type': 'mention'}
check('the window is hidden, so a mention is counted',
      not window.get_visible() and not window._background_mentions)
check('a notification reaches the page first',
      event(('user',), 'notification', mention)
      == [('notifications.notification', mention)])
check('then the real notifier, which now knows the id',
      'N1' in window.notifier._seen)
check('then the real window, which counted the mention',
      window._background_mentions == {'N1'})
admin = types.SimpleNamespace(refresh_tool=record('admin.refresh'))
window._admin_dialog = admin
check('a report refreshes the open admin dashboard\'s reports tool',
      event(('user',), 'report', None) == [('admin.refresh', 'reports')])
window._admin_dialog = None
check('and does nothing while there is none',
      event(('user',), 'report', None) == [])
window.timeline_page = None
window.notifications_page = None
del local.on_stream_update, federated.on_stream_update
del local.on_stream_delete, federated.on_stream_delete
del list_page.on_stream_update, tag_page.on_stream_update
del list_page.on_stream_delete, tag_page.on_stream_delete

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
