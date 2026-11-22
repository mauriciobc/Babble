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
import gc
import sys
import types

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

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
# Push/pop still animate with gtk-enable-animations; off so place-teardown
# checks do not race mutter's frame clock for transitions that are not
# replace() (which Adw documents as unanimated).
Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
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
check('a delete reaches the home, local, federated, list and hashtag '
      'timelines exactly once each, and nothing else',
      sorted(entry[0] for entry in deleted) == sorted((
          'home.delete', 'local.delete', 'federated.delete', 'list.delete',
          'tag.delete'))
      and all(entry[1:] == ('7',) for entry in deleted))
for label, page in (('home', window.timeline_page), ('local', local),
                    ('federated', federated), ('list', list_page),
                    ('tag', tag_page)):
    page.on_stream_status_update = record('%s.status' % label)
edited = event(('user',), 'status.update', '8')
check('a status.update reaches the same five timelines exactly once each',
      sorted(entry[0] for entry in edited) == sorted((
          'home.status', 'local.status', 'federated.status', 'list.status',
          'tag.status'))
      and all(entry[1:] == ('8',) for entry in edited))
for page in (local, federated, list_page, tag_page):
    del page.on_stream_status_update
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

# A place the reader hides keeps neither its page nor its stream: the
# sidebar is the only way to it, and a page that stayed built would keep
# its subscription and its auto-refresh timer for the rest of the
# session.
routing.place_page('federated')
local_page = routing.children['local']
federated_page = routing.children['federated']
local_freed = []
local_page.weak_ref(lambda: local_freed.append(True))
local_scope = local_page._scope
check('the built places have their auto-refresh timer',
      local_page._auto_refresh_id and federated_page._auto_refresh_id)
del wanted[:]
window.settings.set_boolean('hide-local', True)
check('hiding a place drops its page', 'local' not in routing.children)
check('and its auto-refresh timer', not local_page._auto_refresh_id)
check('and its stream', bool(wanted) and LOCAL not in wanted[-1])
check('and leaves the other places alone',
      'federated' in routing.children and federated_page._auto_refresh_id
      and FEDERATED in wanted[-1])
check('and its request scope closes at once, before any collection',
      local_scope.closed)
del local_page
for _ in range(5):
    gc.collect()
    GLib.MainContext.default().iteration(False)
check('and the page is freed, its request scope closed',
      local_freed and local_scope.closed)
window.settings.set_boolean('hide-local', False)
check('and showing it again builds a new page when it is opened',
      'local' not in routing.children)

# A page pushed on top of a place hides the place's tag, so the sidebar
# cannot tell from the visible tag that the place under it is going. Hide
# the place anyway: the stack goes back to home, and popping the pushed
# page cannot land on a page that is gone.
window.show_page('local')
under_page = routing.pages['local']
under_child = routing.children['local']
under_freed = []
under_child.weak_ref(lambda: under_freed.append(True))
under_scope = under_child._scope
window.navigation_view.push(Adw.NavigationPage.new(Gtk.Label(), 'thread'))
stack = window.navigation_view.get_navigation_stack()
check('a page is pushed on top of Local',
      stack.get_n_items() == 2 and window.visible_tag() is None)
# Observe replace() itself: Adw emits ::replaced when the stack is
# swapped (no animation). Reading get_navigation_stack() afterwards
# raced mutter's clock on CI even after show_page('home') had run —
# the forgotten child was already freed. Capture the stack in the
# signal so the assert does not depend on a later frame.
replaced = []


def _on_replaced(view, *_args):
    # ::replaced may pass the previous-pages model; ignore extras.
    model = view.get_navigation_stack()
    visible = view.get_visible_page()
    replaced.append((
        model.get_n_items(),
        visible == window.page_home
        or (visible is not None and visible.get_tag() == 'home')))


replaced_id = window.navigation_view.connect('replaced', _on_replaced)
try:
    window.settings.set_boolean('hide-local', True)
finally:
    window.navigation_view.disconnect(replaced_id)
check('hiding Local under a pushed page resets the stack to home',
      replaced == [(1, True)])
del under_page, under_child, stack
for _ in range(5):
    gc.collect()
    GLib.MainContext.default().iteration(False)
check('and the forgotten page is freed, its scope closed',
      under_freed and under_scope.closed)
window.settings.set_boolean('hide-local', False)

# A list or hashtag that is no longer pinned is gone from the sidebar,
# and goes with it.
window.settings.set_strv('favorite-tags-ids', ['1/rust'])
routing.place_page('tag:rust')
check('a pinned hashtag is built', 'tag:rust' in routing.children)
del wanted[:]
window.settings.set_strv('favorite-tags-ids', [])
# changed::favorite-tags-ids rebuilds the sidebar synchronously; drain a
# few iterations so a nested navigation transition cannot leave the
# forgotten hashtag still in children when we assert.
for _ in range(5):
    GLib.MainContext.default().iteration(False)
if 'tag:rust' in routing.children:
    window._refresh_sidebar()
check('unpinning a place drops its page', 'tag:rust' not in routing.children)
check('and lets the stream go',
      bool(wanted) and hashtag_stream('rust') not in wanted[-1])
del wanted[:]
window._refresh_sidebar()
check('a rebuild that drops no place syncs nothing', not wanted)

# A stream that comes back after an outage catches the other open places
# up as well: the stream does not replay, and a list, a hashtag or the
# conversations would keep showing what was there when the socket went
# down until the reader refreshed them by hand.
refreshed = []
home = TimelinePage(window.client, mode='home', autoload=False)
home.refresh = lambda: refreshed.append('home')
window.timeline_page = home
routing.place_page('local')
routing.place_page('federated')
window.settings.set_strv('favorite-tags-ids', ['1/rust'])
routing.place_page('tag:rust')
routing.children['conversations'] = types.SimpleNamespace(
    refresh=lambda: refreshed.append('conversations'))
# Only Local has loaded something; Federated and the hashtag never did.
routing.children['local'].model.insert_new([
    {'id': '00100', 'content': '<p>post</p>',
     'account': {'acct': 'user', 'id': '1'},
     'created_at': '2026-01-01T00:00:00.000Z'}])
routing.children['local'].model.loading = False   # its first load ended
# The others are idle and empty: never loaded, nothing in flight.
routing.children['federated'].model.loading = False
routing.children['tag:rust'].timeline.model.loading = False
requests = []
window.client.request_async = (
    lambda method, path, **kw: requests.append((method, path, kw['params'])))
window._stream_dropped = True
window._on_stream_connection_changed(None, True)
check('a reconnect refreshes home and the conversations',
      sorted(refreshed) == ['conversations', 'home'])
check('a loaded place sends exactly one newer-posts request',
      requests == [('GET', '/timelines/public',
                    {'local': 'true', 'limit': 20, 'min_id': '00100'})])
check('and the places that never loaded (Federated, the hashtag) send '
      'nothing', not any(r[2].get('local') != 'true' for r in requests))
del refreshed[:], requests[:]
window._on_stream_connection_changed(None, True)
check('and only after an outage', not refreshed and not requests)
window._stream_dropped = False
window.timeline_page = None
del routing.children['conversations']

# A place with work in flight stops it when the place is dropped, and
# receives nothing afterwards: no stream event, no catch-up.
stopped = []
dropped_page = types.SimpleNamespace(
    cancel=lambda: stopped.append('cancel'),
    refresh=lambda: stopped.append('refresh'))
routing.children['conversations'] = dropped_page
routing.pages['conversations'] = object()
routing.forget_many(['conversations'])
window.timeline_page = home
window._catch_up_after_outage()
window.timeline_page = None
check('a place with work in flight stops it when it is dropped',
      stopped == ['cancel'] and 'conversations' not in routing.children)
check('and is not asked to catch up afterwards', 'refresh' not in stopped)

# Visit-time mark-read / catch-up must run on every open of notifications,
# including when Navigation returns the cached template page (mount is
# skipped after the first ensure/register).
shown = []
window.notifications_page = types.SimpleNamespace(
    client=window.client,
    on_shown=lambda: shown.append('shown'),
    on_hidden=lambda: shown.append('hidden'),
    header_widget=Gtk.Label())


class _Withdrawals:
    def __init__(self):
        self.ids = []

    def withdraw_notification(self, platform_id):
        self.ids.append(platform_id)


withdrawals = _Withdrawals()
window.notifier._application = withdrawals
window.notifier._sent_ids = {'babble-42'}
window.show_page('notifications')
window.show_page('home')
window.show_page('notifications')
check('opening notifications again still runs on_shown',
      shown == ['shown', 'hidden', 'shown'])
check('opening notifications withdraws the desktop notifications it sent',
      withdrawals.ids == ['babble-42']
      and window.notifier._sent_ids == set())
window.notifier._sent_ids = {'babble-43'}
window.show_page('home')
check('leaving notifications leaves them',
      withdrawals.ids == ['babble-42'])
window.show_page('notifications')
check('opening it again withdraws the rest',
      withdrawals.ids == ['babble-42', 'babble-43'])
window.notifications_page = None

if failures:
    raise SystemExit('%d navigation window checks failed' % len(failures))
print('all navigation window checks passed')
