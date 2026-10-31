#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""``BabbleWindow._on_stream_event`` routes each Streams event to the page
that owns it, and to nothing else.

``test-stream-events`` only reads the handler's source for the branch
names, so a branch that called the wrong page, or the wrong method on the
right page, would pass it. This runs the unbound handler on a recording
stand-in and asserts the exact calls per event.

A plain recorder accepts any method name, so every method it records is
also checked to exist on the real class. Exits 77 (skip) without a
display."""

import os
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

from src.conversations import ConversationsPage  # noqa: E402
from src.desktopnotify import DesktopNotifier  # noqa: E402
from src.notifications import NotificationsPage  # noqa: E402
from src.streaming import FEDERATED, LOCAL, USER  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


class Recorder:
    """Records (target, method, args) for any attribute called on it."""

    def __init__(self, name, log):
        self._name = name
        self._log = log

    def __getattr__(self, method):
        def call(*args):
            self._log.append((self._name, method, args))
        return call


def build(with_pages=True):
    log = []
    rec = {name: Recorder(name, log) for name in (
        'home', 'local', 'federated', 'notifications', 'conversations',
        'notifier', 'window', 'other')}
    fake = types.SimpleNamespace(
        timeline_page=rec['home'] if with_pages else None,
        notifications_page=rec['notifications'] if with_pages else None,
        navigation=types.SimpleNamespace(
            public_pages={'local': rec['local'],
                          'federated': rec['federated']}
            if with_pages else {},
            children={'conversations': rec['conversations']}
            if with_pages else {}),
        notifier=rec['notifier'],
        _timeline_pages=lambda: (
            [rec['home'], rec['local'], rec['federated']]
            if with_pages else []),
        _refresh_admin_tool=lambda tool: log.append(
            ('window', '_refresh_admin_tool', (tool,))),
        note_background_notification=lambda payload: log.append(
            ('window', 'note_background_notification', (payload,))))
    return fake, log


def dispatch(name, stream=USER, with_pages=True):
    fake, log = build(with_pages)
    payload = {'id': 'P-%s' % name}
    BabbleWindow._on_stream_event(fake, None, stream, name, payload)
    return log, payload


def all_pages(method, payload):
    return [('home', method, (payload,)), ('local', method, (payload,)),
            ('federated', method, (payload,))]


def expected(name, payload, stream=USER):
    p = (payload,)
    if name == 'update':
        target = {LOCAL: 'local', FEDERATED: 'federated'}.get(stream, 'home')
        return [(target, 'on_stream_update', p)]
    return {
        'delete': all_pages('on_stream_delete', payload)
        + [('conversations', 'on_stream_delete', p)],
        'status.update': all_pages('on_stream_status_update', payload),
        'filters_changed': [('home', 'refresh', ()),
                            ('notifications', 'refresh', ())],
        'notifications_merged': [('notifications', 'refresh', ())],
        'follow_request': [('notifications', 'refresh', ())],
        'announcement': [('notifications', 'load_announcements', ())],
        'announcement.delete': [('notifications', 'load_announcements', ())],
        'announcement.reaction': [],
        'add': [],
        'remove': [],
        'report': [('window', '_refresh_admin_tool', ('reports',))],
        'notification': [('notifications', 'on_stream_notification', p),
                         ('notifier', 'handle', p),
                         ('window', 'note_background_notification', p)],
        'conversation': [('conversations', 'on_stream_conversation', p)],
        'encrypted_message': [('conversations', 'refresh', ())],
    }[name]


EVENTS = ('update', 'delete', 'status.update', 'filters_changed',
          'notifications_merged', 'follow_request', 'announcement',
          'announcement.delete', 'announcement.reaction', 'add', 'remove',
          'report', 'notification', 'conversation', 'encrypted_message')

seen = set()
for event in EVENTS:
    log, payload = dispatch(event)
    check('%s routes to exactly its targets' % event,
          log == expected(event, payload))
    seen.update((target, method) for target, method, _args in log)

for label, stream in (('the user stream', USER), ('local', LOCAL),
                      ('federated', FEDERATED)):
    log, payload = dispatch('update', stream)
    check('update on %s goes to its own page only' % label,
          log == expected('update', payload, stream))

for event in EVENTS:
    try:
        log, _payload = dispatch(event, with_pages=False)
        ok = True
    except Exception as err:  # noqa: BLE001
        print('  %s raised %r' % (event, err))
        ok = False
    check('%s survives every page being None' % event, ok)

log, _payload = dispatch('no-such-event')
check('an unknown event does nothing', log == [])

real = {'home': TimelinePage, 'local': TimelinePage,
        'federated': TimelinePage, 'notifications': NotificationsPage,
        'conversations': ConversationsPage, 'notifier': DesktopNotifier,
        'window': BabbleWindow}
for target, method in sorted(seen):
    check('%s.%s exists on %s' % (target, method, real[target].__name__),
          callable(getattr(real[target], method, None)))

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
