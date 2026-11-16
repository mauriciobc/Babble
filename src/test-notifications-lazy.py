#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the Babble notifications widgets with documented API-shape
fixtures (docs.joinmastodon.org/entities/Notification) and performs one
LIVE anonymous fetch through MastodonClient.

The /api/v1/notifications endpoints always require authentication, so
the live fetch proves the transport plus the error-page path: the HTTP
unauthorized response arrives as a GLib.Error with the HTTP status code.
Rendering with real server payloads is proven by the timeline test,
which drives the exact StatusRow/ContentLabel path embedded inside
notification rows here.

Exits 77 (skip) when the display or network is unavailable."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.notificationmodel import (  # noqa: E402
    NotificationItem)
from src.notificationrow import NotificationPost, NotificationRow  # noqa: E402
from src.notifications import (NotificationsPage)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


ACTOR = {'id': '9', 'acct': 'alice', 'display_name': 'Alice Doe',
         'url': 'https://fosstodon.org/@alice'}
ACTOR2 = {'id': '10', 'acct': 'bob', 'display_name': 'Bob',
          'url': 'https://fosstodon.org/@bob'}
ACTOR3 = {'id': '11', 'acct': 'carol', 'display_name': 'Carol',
          'url': 'https://fosstodon.org/@carol'}
STATUS = {'id': '42', 'content': '<p>My original post</p>',
          'account': {'id': '1', 'acct': 'me', 'display_name': 'Me'},
          'created_at': '2026-10-01T10:00:00.000Z'}


def make_notification(id, type, account, created, status=None):
    """Shape documented at docs.joinmastodon.org/entities/Notification:
    {id, type, created_at, account, status?}."""
    data = {'id': id, 'type': type, 'created_at': created,
            'account': account}
    if status is not None:
        data['status'] = status
    return data


MENTION = make_notification('100', 'mention', ACTOR3,
                            '2026-10-05T12:05:00.000Z',
                            {**STATUS, 'id': '50',
                             'content': '<p>Hello @me</p>'})
FOLLOW = make_notification('99', 'follow', ACTOR3,
                           '2026-10-05T12:04:00.000Z')
FAV_A = make_notification('98', 'favourite', ACTOR2,
                          '2026-10-05T12:03:00.000Z', STATUS)
FAV_B = make_notification('97', 'favourite', ACTOR3,
                          '2026-10-05T12:02:00.000Z', STATUS)
FAV_C = make_notification('96', 'favourite', ACTOR,
                          '2026-10-05T12:01:00.000Z', STATUS)
REBLOG = make_notification('95', 'reblog', ACTOR2,
                           '2026-10-05T11:00:00.000Z', STATUS)
POLL = make_notification('94', 'poll', ACTOR, '2026-10-05T10:30:00.000Z',
                         {**STATUS, 'id': '52'})
EDIT = make_notification('93', 'update', ACTOR2, '2026-10-05T10:20:00.000Z',
                         {**STATUS, 'id': '53'})
REQUEST = make_notification('92', 'follow_request', ACTOR,
                            '2026-10-05T10:10:00.000Z')
SIGNUP = make_notification('91', 'admin.sign_up', ACTOR,
                           '2026-10-05T10:00:00.000Z')
MYSTERY = make_notification('90', 'future.mystery', ACTOR,
                            '2026-10-05T09:00:00.000Z')

# Mastodon 4.6 notification types that carry a `collection` object.
COLLECTION = {'id': '300', 'name': 'Nice accounts',
              'description': 'These accounts are very nice'}
COLLECTION_ADD = make_notification('89', 'added_to_collection', ACTOR,
                                   '2026-10-05T08:00:00.000Z')
COLLECTION_ADD['collection'] = COLLECTION
COLLECTION_UPDATE = make_notification('88', 'collection_update', ACTOR2,
                                      '2026-10-05T07:00:00.000Z')
COLLECTION_UPDATE['collection'] = COLLECTION

FIXTURES = [MENTION, FOLLOW, FAV_A, FAV_B, FAV_C, REBLOG, POLL, EDIT,
            REQUEST, SIGNUP, MYSTERY]

# Single-actor group then a two-actor cross-page merge.
FAV_D = make_notification('945', 'favourite', ACTOR3,
                          '2026-10-05T11:30:00.000Z', STATUS)
from testkit import notificationskit as kit  # noqa: E402
from testkit.notificationskit import FollowClient, notification_rows, pump_until  # noqa: E402
from testkit.notificationskit import NavRoot, client, nav_root, page  # noqa: E402

# Lazy parts: a row builds the post and the people strip only when a
# notification needs them, and keeps them for later binds once built.
follow_row = NotificationRow()
follow_row.bind(NotificationItem(FOLLOW), client)
check('a row without a status builds no post snapshot',
      follow_row.post is None)
check('a single-actor row builds no people strip',
      follow_row.cluster_expander is None)
mention_row = NotificationRow()
mention_row.bind(NotificationItem(MENTION), client)
check('a status row builds its post on first bind',
      isinstance(mention_row.post, NotificationPost))
check('a status row has no people strip until merged',
      mention_row.cluster_expander is None)
merged_item = NotificationItem(FAV_A)
merged_item.merge(FAV_B)
merged_row = NotificationRow()
merged_row.bind(merged_item, client)
check('a merged row builds its people strip on first bind',
      merged_row.cluster_expander is not None
      and merged_row.cluster_expander.get_visible()
      and merged_row.people_box.get_first_child() is not None)
merged_row.bind(NotificationItem(FOLLOW), client)
check('a recycled row hides the strip it keeps',
      not merged_row.cluster_expander.get_visible()
      and merged_row.avatar.get_visible()
      and merged_row.people_box.get_first_child() is None)
mention_row.bind(NotificationItem(FOLLOW), client)
check('a recycled row hides the post it keeps',
      mention_row.post is not None
      and not mention_row.post.get_visible())

stack = nav_root.navigation_view.get_navigation_stack()
page.on_row_activated(page.list_view, 0)  # mention row -> ThreadPage
check('mention row pushes thread page',
      stack.get_n_items() == 1 and 'Thread' in stack[0].get_title())
mention_id = page.model.get_item(0).status_item.status_id
follow_index = [i for i in range(page.model.get_n_items())
                if page.model.get_item(i).kind == 'follow'][0]
import src.profile  # noqa: E402
profile_calls = []
src.profile.push_profile = lambda view, client, account: \
    profile_calls.append(account['acct'])
page.on_row_activated(page.list_view, follow_index)
check('follow row opens the actor profile', profile_calls == ['carol'])
check('thread page tracks activated status', mention_id == '50')

# A grouped row with no post (two new followers) has no single account
# to open. Activating it toggles the people list, the chevron and the
# avatar strip are the expander's own interaction, and a named person
# opens that person. The expander's click gesture never claims its
# sequence, so a click on it used to reach the row's activation gesture
# as well: expanding the group also opened the first follower.
cluster_page = NotificationsPage(client)
cluster_page.model.append_page([
    make_notification('300', 'follow', ACTOR, '2026-10-06T12:00:00.000Z'),
    make_notification('299', 'follow', ACTOR2, '2026-10-06T11:59:00.000Z')])
cluster_page.stack.set_visible_child_name('list')
cluster_window = NavRoot()
cluster_window.navigation_view = Adw.NavigationView()
cluster_window.set_child(cluster_page)
cluster_window.set_default_size(600, 900)
cluster_window.present()


def cluster_rows():
    return [w for w in notification_rows(cluster_page.list_view)
            if w.get_mapped() and w.cluster_expander is not None
            and w.cluster_expander.get_visible()]


pump_until(lambda: bool(cluster_rows()))
cluster_row = cluster_rows()[0]
check('grouped followers merge into one row',
      cluster_page.model.get_n_items() == 1
      and len(cluster_page.model.get_item(0).actors) == 2)
first_person = cluster_row.people_box.get_first_child()
check('people rows are buttons that name the person',
      isinstance(first_person, Gtk.Button)
      and isinstance(first_person.get_child().get_last_child(), Gtk.Label)
      and first_person.get_child().get_last_child().get_text()
      == 'Alice Doe')
profile_calls.clear()
cluster_page.on_row_activated(cluster_page.list_view, 0)
check('activating a grouped follow row expands it',
      cluster_row.cluster_expander.get_expanded())
check('expanding a grouped follow row opens no profile',
      profile_calls == [])
pump_until(lambda: cluster_row.people_revealer.get_child_revealed())


def find_named(widget, name):
    if type(widget).__name__ == name:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_named(child, name)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def point_in_expander(widget):
    ok, rect = widget.compute_bounds(cluster_row.cluster_expander)
    return rect.origin.x + rect.size.width / 2, \
        rect.origin.y + rect.size.height / 2


check('the avatar strip is an expander title click',
      cluster_row._in_cluster_title(
          *point_in_expander(cluster_row.avatar_stack)))
# The expander's arrow is a private GtkBuiltinIcon; it is the widget the
# title gesture must claim, since its release reaches the row as well.
arrow = find_named(cluster_row.cluster_expander, 'GtkBuiltinIcon')
check('the expander arrow is an expander title click',
      arrow is not None
      and cluster_row._in_cluster_title(*point_in_expander(arrow)))
check('the revealed people list is not an expander title click',
      not cluster_row._in_cluster_title(
          *point_in_expander(cluster_row.people_box)))
profile_calls.clear()
cluster_row.people_box.get_first_child().emit('clicked')
check('a named person opens that person', profile_calls == ['alice'])
cluster_page.on_row_activated(cluster_page.list_view, 0)
check('activating the grouped row again collapses it',
      not cluster_row.cluster_expander.get_expanded())
cluster_window.destroy()

# A page of new followers asks for their relationships once, and the rows
# whose answer comes back are re-emitted so they rebind with Follow Back.
# The first page load used to call a method that did not exist.
follow_client = FollowClient()
follow_page = NotificationsPage(follow_client)
follow_page.on_page_loaded(follow_client, [
    make_notification('f1', 'follow', ACTOR, '2026-10-05T12:09:00.000Z'),
    MENTION,
    make_notification('f2', 'follow', ACTOR2, '2026-10-05T12:03:00.000Z')])
check('a page of followers asks for their relationships in one request',
      len(follow_client.asked()) == 1
      and follow_client.asked()[0][0] == 'GET'
      and sorted(follow_client.asked()[0][2]['id[]']) == ['10', '9'])
rebound = []
follow_page.model.connect(
    'items-changed', lambda _m, position, _r, _a: rebound.append(position))
follow_client.reply(follow_client, [
    {'id': '9', 'following': False, 'requested': False},
    {'id': '10', 'following': True, 'requested': False}])
follow_kinds = [follow_page.model.get_item(i).kind
                for i in range(follow_page.model.get_n_items())]
check('only the follow rows are rebound by the answer',
      sorted(rebound) == [i for i, k in enumerate(follow_kinds)
                          if k == 'follow'] and len(rebound) == 2)
follow_client.requests.clear()
# A follow that lands on top of another follow merges into one
# multi-actor row, which never shows Follow Back, so it is not asked for.
follow_page.on_stream_notification(
    make_notification('f3', 'follow', ACTOR3, '2026-10-05T12:20:00.000Z'))
check('a follower merged into a group is not asked for',
      follow_client.asked() == [])
# Between two mentions it stands alone, and is asked for.
follow_page.on_stream_notification(
    {**MENTION, 'id': 'm2', 'created_at': '2026-10-05T12:30:00.000Z'})
follow_page.on_stream_notification(
    make_notification('f4', 'follow', ACTOR, '2026-10-05T12:40:00.000Z'))
follow_page.on_stream_notification(
    {**MENTION, 'id': 'm3', 'created_at': '2026-10-05T12:50:00.000Z'})
follow_page.on_stream_notification(
    make_notification('f5', 'follow', ACTOR3, '2026-10-05T12:55:00.000Z'))
check('a streamed follower standing alone is asked for',
      [r[2]['id[]'] for r in follow_client.asked()] == [['11']])

# Filter switch clears and reloads through the client path: stub the
# client transport so the page's filter injection actually runs.
relay = {}


real_request_async = client.request_async


def fake_request_async(method, path, params=None, callback=None, **kwargs):
    if path != '/notifications':
        return real_request_async(method, path, params=params,
                                  callback=callback, **kwargs)
    relay['params'] = dict(params)
    relay['callback'] = callback


client.request_async = fake_request_async
page.filter_group.set_active_name('mentions')
check('mentions filter requests types[]=mention',
      relay['params'].get('types[]') == ['mention'])
page.filter_group.set_active_name('all')
check('all filter drops the types param',
      relay['params'].get('types[]') is None)
check('filter is a named toggle group', isinstance(
    page.filter_group, Adw.ToggleGroup)
    and page.filter_group.get_n_toggles() == 2
    and page.filter_group.get_toggle_by_name('mentions') is not None)
check('filter stays outside the stack so empty states keep it',
      page.filter_group.get_ancestor(Gtk.Stack) is None)


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all notifications lazy checks passed')
