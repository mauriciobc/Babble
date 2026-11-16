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

from gi.repository import Adw, GLib, Gio, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src.notificationrow import NotificationRow  # noqa: E402
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
class NavRoot(Gtk.Window):
    pass


client = MastodonClient('https://fosstodon.org')  # no token: idle client

page = NotificationsPage(client)
page.model.append_page(FIXTURES)
page.stack.set_visible_child_name('list')

window = Gtk.Window()
window.set_child(page)
window.set_default_size(600, 900)
window.present()

nav_root = NavRoot()
nav_root.navigation_view = Adw.NavigationView()
window.set_child(None)
nav_root.set_child(page)
nav_root.set_default_size(600, 900)
nav_root.present()

relay = {}
real_request_async = client.request_async


def fake_request_async(method, path, params=None, callback=None, **kwargs):
    if path != '/notifications':
        return real_request_async(method, path, params=params,
                                  callback=callback, **kwargs)
    relay['params'] = dict(params)
    relay['callback'] = callback


client.request_async = fake_request_async




def notification_rows(widget):
    found = [widget] if isinstance(widget, NotificationRow) else []
    child = widget.get_first_child()
    while child is not None:
        found += notification_rows(child)
        child = child.get_next_sibling()
    return found


class FollowClient(MastodonClient):
    """A signed-in client that records its requests and never leaves the
    machine."""

    def __init__(self):
        super().__init__('https://fosstodon.org')
        self.token = 'token'
        self.requests = []
        self.reply = None

    def request_async(self, method, path, callback=None, params=None, **kw):
        self.requests.append((method, path, params))
        if path == '/accounts/relationships':
            self.reply = callback

    def asked(self):
        """The relationship lookups made so far."""
        return [r for r in self.requests
                if r[1] == '/accounts/relationships']

    def request_finish(self, result):
        return result


def _live_error_result(error):
    """Wrap an already-arrived GLib.Error as a Gio.Task result."""
    task = Gio.Task.new()
    task.return_error(error)
    return task


def body_text(post):
    return post.content.get_text()


def collect(widget, out):
    out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect(child, out)
        child = child.get_next_sibling()


def pump_until(predicate, seconds=5):
    deadline = GLib.get_monotonic_time() + int(seconds * 1000000)
    while GLib.get_monotonic_time() < deadline and not predicate():
        if not GLib.MainContext.default().iteration(False):
            GLib.usleep(5000)


def visible_text(widget):
    out = []
    collect(widget, out)
    return ' '.join(w.get_text() or '' for w in out
                    if isinstance(w, Gtk.Label))
