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

from gi.repository import GLib, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import feedback  # noqa: E402
from src.notificationmodel import (  # noqa: E402
    NotificationModel)
from src.notifications import (NotificationsPage)

# The fixtures and check() are the kit's, so a failed check here lands in
# kit.failures, the list the exit gate reads.
from testkit import notificationskit as kit  # noqa: E402
from testkit.notificationskit import (  # noqa: E402
    ACTOR, ACTOR2, FIXTURES, MENTION, STATUS, check, make_notification)
from testkit.notificationskit import FollowClient  # noqa: E402
from testkit.notificationskit import client, page, relay  # noqa: E402

# Refresh: the public hook the window calls; an empty list reloads from
# scratch.
page.model.clear()
relay.clear()
page.model.loading = False
page.refresh()
check('refresh on an empty list loads the newest page',
      relay['params'].get('limit') == 20
      and page.stack.get_visible_child_name() == 'loading')

# Infinite scroll: load_older pages with max_id.
page.model.clear()
page.model.oldest_id = '90'
page.load_older()
check('older page requests max_id',
      relay['params'].get('max_id') == '90'
      and 'min_id' not in relay['params'])

# Opening the page asks for what is newer than its top. A notification
# that arrived while the stream was down is announced by the poller but
# never merged into the rows, so without this the page stayed stale
# until a manual refresh. What is on screen is marked read at once, so a
# failed catch-up cannot leave the badge up, and the answer's marker
# waits for the first rather than racing it.
class VisitClient(FollowClient):
    """A signed-in client whose notification requests the test answers."""

    def __init__(self):
        super().__init__()
        self.token = ''            # keep the constructor from loading
        self.listed = []
        self.markers = []
        self.marker_callbacks = []
        self.pending = None
        self.other_requests = []

    def request_async(self, method, path, callback=None, params=None,
                      form=None, **kw):
        if path == '/notifications':
            self.listed.append(dict(params or {}))
            self.pending = callback
        elif path == '/markers' and method == 'POST':
            self.markers.append(form['notifications[last_read_id]'])
            self.marker_callbacks.append(callback)
        elif path not in ('/markers', '/notifications/unread_count'):
            self.other_requests.append(path)

    def request_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def finish_marker(self):
        self.marker_callbacks.pop(0)(self, None)


def visit_setup(client_class=VisitClient):
    client = client_class()
    page = NotificationsPage(client)
    client.token = 'token'
    page.model.append_page([MENTION])
    page.stack.set_visible_child_name('list')
    window = Gtk.Window(child=page, default_width=600, default_height=900)
    window.present()
    deadline = GLib.get_monotonic_time() + 5000000
    while not page.get_mapped() and GLib.get_monotonic_time() < deadline:
        if not GLib.MainContext.default().iteration(False):
            GLib.usleep(5000)
    client.listed.clear()
    client.markers.clear()
    return client, page, window


visit_client, visit_page, visit_window = visit_setup()
visit_page.on_shown()
check('opening the page asks for what is newer than its top',
      bool(visit_client.listed)
      and visit_client.listed[-1].get('min_id') == '100')
check('opening the page asks for nothing else',
      visit_client.other_requests == [])
check('opening the page marks what is on screen read at once',
      visit_client.markers == ['100'])
visit_client.pending(visit_client, [
    make_notification('101', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS)])
check('the notification that missed the stream is now first',
      visit_page.model.get_item(0).notification_id == '101')
check('the second marker waits for the first',
      visit_client.markers == ['100'])
visit_client.finish_marker()
check('the reloaded top is marked read once the first marker lands',
      visit_client.markers == ['100', '101'])
visit_window.destroy()
del visit_page, visit_client, visit_window

# A catch-up that fails is quiet: the reader asked for nothing, so no
# error toast, and the list was already marked read.
visit_client, visit_page, visit_window = visit_setup()
reported = []
real_report_error = feedback.report_error
feedback.report_error = lambda *args, **kwargs: reported.append(args)
visit_page.on_shown()
visit_client.pending(visit_client, GLib.Error('offline'))
feedback.report_error = real_report_error
check('a failed catch-up raises no error toast', reported == [])
check('a failed catch-up still leaves the list marked read',
      visit_client.markers == ['100'])
visit_window.destroy()
del visit_page, visit_client, visit_window

# An empty list is caught up without the loading spinner.
visit_client, visit_page, visit_window = visit_setup()
visit_page.model.clear()
visit_page.stack.set_visible_child_name('empty')
visit_page.on_shown()
check('catching up an empty list keeps the empty page, not a spinner',
      visit_page.stack.get_visible_child_name() == 'empty'
      and bool(visit_client.listed))
visit_client.pending(visit_client, GLib.Error('offline'))
check('a failed catch-up of an empty list keeps it empty',
      visit_page.stack.get_visible_child_name() == 'empty')
visit_window.destroy()
del visit_page, visit_client, visit_window

# A filter switch while a read is out. The old read's answer arrives after
# the new load went out (a cancelled Gio.Task still calls back): it must
# not give up the loading flag the new load owns, nor the quiet flag.
class HeldVisitClient(VisitClient):
    """Keeps every /notifications callback, to answer them in any order."""

    def __init__(self):
        super().__init__()
        self.held = []

    def request_async(self, method, path, callback=None, **kw):
        if path == '/notifications':
            self.held.append(callback)
        super().request_async(method, path, callback=callback, **kw)


def switch_while_out(name, start, quiet):
    client, page, window = visit_setup(HeldVisitClient)
    start(page)
    check('%s: the read is out' % name,
          len(client.held) == 1 and page.model.loading
          and page._quiet == quiet)
    page.set_filter('mentions')
    check('%s: the filter switch sent a new load' % name,
          len(client.held) == 2 and page.model.loading
          and client.listed[-1].get('types[]') == ['mention'])
    page._quiet = True               # the new load is a quiet one
    client.held[0](client, GLib.Error('stale'))   # the old read answers
    check('%s: the stale answer leaves loading to the new load' % name,
          page.model.loading)
    check('%s: the stale answer leaves the new load\'s quiet flag' % name,
          page._quiet)
    check('%s: the stale answer shows no error page' % name,
          page.stack.get_visible_child_name() != 'error')
    client.held[1](client, [make_notification(
        '201', 'mention', ACTOR2, '2026-10-05T13:00:00.000Z', STATUS)])
    check('%s: the new load fills the list and clears loading' % name,
          not page.model.loading and not page._quiet
          and page.model.get_n_items() == 1
          and page.model.get_item(0).notification_id == '201')
    window.destroy()


def stale_older_while_newer_live():
    """O1 is cancelled by a reload; O2 is live when O1's drop arrives."""
    name = 'stale older request'
    client, page, window = visit_setup(HeldVisitClient)
    page.load_older()                                    # O1
    check('%s: the first older page is out' % name,
          len(client.held) == 1 and page._loading_older)
    page.set_filter('mentions')                          # cancels O1, loads
    check('%s: the reload sent the first page' % name,
          len(client.held) == 2 and not page._loading_older)
    client.held[1](client, [make_notification(
        '201', 'mention', ACTOR2, '2026-10-05T13:00:00.000Z', STATUS)])
    page.load_older()                                    # O2
    check('%s: the newer older page is out and the bottom locked' % name,
          len(client.held) == 3 and page._loading_older
          and page._bottom_locked)
    client.held[0](client, GLib.Error('stale'))          # O1's late drop
    check('%s: the stale drop leaves the live request flagged' % name,
          page._loading_older)
    check('%s: the stale drop leaves the bottom locked' % name,
          page._bottom_locked)
    client.held[2](client, [])                           # O2 ends
    check('%s: the live request still ends normally' % name,
          not page._loading_older and not page._bottom_locked)
    window.destroy()


def start_quiet_refresh(page):
    page.on_shown()                  # catch_up of a list with rows


def start_first_load(page):
    page.model.clear()
    page.load()


def start_quiet_empty_catch_up(page):
    page.model.clear()
    page.stack.set_visible_child_name('empty')
    page.on_shown()


switch_while_out('quiet refresh', start_quiet_refresh, True)
switch_while_out('first-page load', start_first_load, False)
switch_while_out('quiet empty catch-up', start_quiet_empty_catch_up, True)
stale_older_while_newer_live()

# A batch from the poller is one merge: one marker, not one per item.
visit_client, visit_page, visit_window = visit_setup()
visit_page._shown = True
visit_page.merge_notifications([
    make_notification('102', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS),
    make_notification('103', 'follow', ACTOR,
                      '2026-10-05T14:00:00.000Z')])
check('a merged batch sends one marker, for its newest id',
      visit_client.markers == ['103'])
check('a merged batch is newest first',
      visit_page.model.get_item(0).notification_id == '103')
visit_window.destroy()
del visit_page, visit_client, visit_window

# Merged into a page whose first page never landed, a poll would seed a
# list that only ever asks for newer: the page loads instead.
visit_client, visit_page, visit_window = visit_setup()
visit_page.model.clear()
visit_page.stack.set_visible_child_name('error')
visit_page.merge_notifications([
    make_notification('104', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS)])
check('a poll into a never-loaded page loads it instead of seeding it',
      visit_page.model.get_n_items() == 0
      and visit_client.listed and 'min_id' not in visit_client.listed[-1])
visit_window.destroy()
del visit_page, visit_client, visit_window

# A merge that moves the top row to a newer day changes where the
# sections fall without adding a row there, so the model says so: the
# heading of the row after it is otherwise never drawn.
section_model = NotificationModel()
section_model.append_page([
    make_notification('200', 'favourite', ACTOR, '2026-10-04T12:00:00.000Z',
                      STATUS),
    make_notification('199', 'mention', ACTOR2, '2026-10-04T11:00:00.000Z',
                      {**STATUS, 'id': '51'})])
section_changes = []
section_model.connect('sections-changed',
                      lambda _m, pos, n: section_changes.append((pos, n)))
section_model.prepend_page([
    make_notification('201', 'reblog', ACTOR2, '2026-10-05T12:00:00.000Z',
                      STATUS)])
check('a merge that moves the top row to a newer day reports sections',
      section_model.get_n_items() == 2 and section_changes == [(0, 2)])
del section_model

# Unread badge signal.
received = []
page.connect('unread-changed', lambda _p, count: received.append(count))
# The marker in flight is for the newest id the list holds.
if page.model.newest_id is None:
    page.model.append_page(FIXTURES)
page._marking = page.model.newest_id
client.request_finish = lambda result: {}
page.on_marked_read(client, None)
check('badge signal emits 0 after mark-read', received == [0])
# One that was overtaken by a newer notification does not clear the badge:
# what it marked is not what the list now holds.
received.clear()
page._marking = '1'
page.on_marked_read(client, None)
del client.request_finish
check('an overtaken marker leaves the badge alone',
      received == [] and page.model.newest_id != '1')


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all notifications refresh checks passed')
