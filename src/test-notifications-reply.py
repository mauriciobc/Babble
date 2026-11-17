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

from gi.repository import GLib, Gio

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

# The fixtures and check() are the kit's, so a failed check here lands in
# kit.failures, the list the exit gate reads.
from testkit import notificationskit as kit  # noqa: E402
from testkit.notificationskit import (  # noqa: E402
    ACTOR, ACTOR2, STATUS, check, make_notification)

# Desktop notification buttons and their app actions.
from src import desktopnotify  # noqa: E402

recorded_buttons = []
_orig_add_button = Gio.Notification.add_button_with_target


def _record_button(self, label, action, target):
    recorded_buttons.append((label, action, target.unpack()))
    return _orig_add_button(self, label, action, target)


Gio.Notification.add_button_with_target = _record_button


class StubSettings:
    def __init__(self, group):
        self.group = group

    def get_strv(self, key):
        return ['mention', 'follow', 'favourite']

    def get_boolean(self, key):
        return self.group


class StubClient:
    def __init__(self):
        self.calls = []

    def request_async(self, method, path, callback=None, **kw):
        self.calls.append((method, path))


class StubWindow:
    def __init__(self, group=False):
        self.settings = StubSettings(group)
        self.account = {'id': '1', 'instance': 'https://fosstodon.org'}
        self.client = StubClient()
        self.replies = []
        self.switched = []
        self.presented = 0

    def get_visible(self):
        return False

    def present(self):
        self.presented += 1

    def open_compose_reply(self, status_id, handle, visibility='public'):
        self.replies.append((status_id, handle, visibility))

    def switch_account(self, key):
        self.switched.append(key)
        instance, _sep, aid = key.rpartition('#')
        self.account = {'id': aid, 'instance': instance}


class StubApp:
    def __init__(self, win):
        self.sent = []
        self.actions = {}
        self.win = win

    def send_notification(self, nid, note):
        self.sent.append((nid, note))

    def add_action(self, action):
        self.actions[action.get_name()] = action

    def get_windows(self):
        return [self.win]

    def ensure_window(self):
        return self.win


ME_KEY = 'https://fosstodon.org#1'
CREATED = '2026-10-05T12:00:00.000Z'
stub_win = StubWindow()
stub_app = StubApp(stub_win)
notifier = desktopnotify.DesktopNotifier(stub_win, stub_app)

recorded_buttons.clear()
notifier.handle(make_notification('d1', 'mention', ACTOR, CREATED,
                                  dict(STATUS, visibility='public')),
                force=True)
check('mention notification offers reply, favorite and boost',
      recorded_buttons == [('Reply', 'app.reply', (ME_KEY, '42')),
                           ('Favorite', 'app.favourite', (ME_KEY, '42')),
                           ('Boost', 'app.boost', (ME_KEY, '42'))])

recorded_buttons.clear()
notifier.handle(make_notification('d2', 'mention', ACTOR, CREATED,
                                  dict(STATUS, visibility='direct')),
                force=True)
check('direct mention offers reply but no boost',
      [b[1] for b in recorded_buttons] == ['app.reply', 'app.favourite'])

recorded_buttons.clear()
notifier.handle(make_notification('d3', 'follow', ACTOR, CREATED),
                force=True)
check('follow notification offers follow back',
      recorded_buttons == [('Follow Back', 'app.follow-back',
                            (ME_KEY, '9'))])

recorded_buttons.clear()
notifier.handle(make_notification('d4', 'favourite', ACTOR, CREATED,
                                  STATUS), force=True)
check('favourite of own post offers no buttons', recorded_buttons == [])

group_win = StubWindow(group=True)
group_notifier = desktopnotify.DesktopNotifier(group_win, StubApp(group_win))
recorded_buttons.clear()
group_notifier.handle(make_notification('g1', 'mention', ACTOR, CREATED,
                                        STATUS), force=True)
check('single-actor group still offers buttons', len(recorded_buttons) == 3)
recorded_buttons.clear()
group_notifier.handle(make_notification('g2', 'mention', ACTOR2, CREATED,
                                        STATUS), force=True)
check('multi-actor grouped notification offers no buttons',
      recorded_buttons == [])

# Polling stands in for the stream while it is down, so what it finds has
# to reach the list as well: before this it only raised a desktop
# notification and the page stayed stale until a manual refresh.
class PollPage:
    def __init__(self):
        self.seen = []

    def merge_notifications(self, notifications):
        self.seen.append([n['id'] for n in notifications])


class PollWindow(StubWindow):
    def __init__(self):
        super().__init__()
        self.notifications_page = PollPage()


class PollClient:
    def request_finish(self, result):
        return result


poll_win = PollWindow()
poll_notifier = desktopnotify.DesktopNotifier(poll_win, StubApp(poll_win))
poll_notifier._polling_client = PollClient()
poll_notifier._on_polled(poll_notifier._polling_client, [
    make_notification('p1', 'poll', ACTOR, CREATED, STATUS),
    make_notification('p2', 'mention', ACTOR, CREATED, STATUS)], False)
check('a poll reaches the list too, as one batch',
      poll_win.notifications_page.seen == [['p1', 'p2']])

desktopnotify.install_app_actions(stub_app)
check('app actions registered',
      sorted(stub_app.actions) == ['boost', 'favourite', 'follow-back',
                                   'reply'])
stub_app.actions['favourite'].activate(GLib.Variant('(ss)', (ME_KEY, '42')))
stub_app.actions['follow-back'].activate(GLib.Variant('(ss)', (ME_KEY, '9')))
check('action handlers call the active client',
      stub_win.client.calls == [('POST', '/statuses/42/favourite'),
                                ('POST', '/accounts/9/follow')])

# Reply: the status kept from the mention prefills the composer.
stub_win.client.calls.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (ME_KEY, '42')))
check('reply presents the window', stub_win.presented == 1)
check('reply opens the composer with id, handle and visibility',
      stub_win.replies == [('42', STATUS['account']['acct'],
                            STATUS.get('visibility') or 'public')])
check('reply of a kept status needs no fetch', stub_win.client.calls == [])
# A status not kept from a notification is fetched first.
stub_win.replies.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (ME_KEY, '777')))
check('reply of an unknown status fetches it',
      stub_win.client.calls == [('GET', '/statuses/777')]
      and stub_win.replies == [])

# Another stored account: switch to it, then reply.
OTHER_KEY = 'https://mastodon.social#5'
_orig_find = desktopnotify.find_account_by_key
desktopnotify.find_account_by_key = lambda s, k: (
    {'id': '5', 'instance': 'https://mastodon.social',
     'display_name': 'Other'} if k == OTHER_KEY else None)
desktopnotify._remember_status(OTHER_KEY, dict(STATUS, id='60'))
stub_win.replies.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (OTHER_KEY, '60')))
check('reply for another account switches to it',
      stub_win.switched == [OTHER_KEY])
check('reply after the switch opens the composer',
      stub_win.replies == [('60', STATUS['account']['acct'], 'public')])

# An account no longer stored: toast, no switch, no composer.
import src.feedback  # noqa: E402
toasts = []
_orig_toast = src.feedback.show_toast
src.feedback.show_toast = lambda w, text, *a, **k: toasts.append(text)
stub_win.replies.clear()
stub_app.actions['reply'].activate(
    GLib.Variant('(ss)', ('https://gone.example#3', '61')))
check('reply for a gone account toasts instead',
      len(toasts) == 1 and stub_win.replies == []
      and stub_win.switched == [OTHER_KEY])
src.feedback.show_toast = _orig_toast
desktopnotify.find_account_by_key = _orig_find
Gio.Notification.add_button_with_target = _orig_add_button


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all notifications reply checks passed')
