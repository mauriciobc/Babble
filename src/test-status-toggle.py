#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""StatusActions._send_toggle: optimistic state, rollback on failure,
merge on success, and a row rebound before the reply.

Drives the real action bar with a stub client whose reply the test
delivers by hand. Exits 77 (skip) without a display or a compiled bundle."""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib  # noqa: E402

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
Gio.Resource.load(bundle)._register()

Adw.init()

from src import pages  # noqa: E402
from src.entities import lift  # noqa: E402
from src.statusactions import StatusActions  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


reported = []
pages.report_error = lambda widget, heading, error: reported.append(heading)
pages.show_toast = lambda *args, **kwargs: None


class Client:
    """Holds the reply: the test resolves it with fail() or succeed()."""
    token = 'secret'

    def __init__(self):
        self.requests = []
        self.callback = None

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.requests.append((method, path))
        self.callback = callback

    def request_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def fail(self):
        self.callback(self, GLib.Error.new_literal(
            Gio.io_error_quark(), 'nope', Gio.IOErrorEnum.FAILED), None)

    def succeed(self, body):
        self.callback(self, lift(body), None)


def status(identifier, **fields):
    data = {'id': identifier, 'content': 'p%s' % identifier,
            'account': {'acct': 'a', 'id': '1'}, 'visibility': 'public',
            'created_at': '2026-01-01T00:00:00.000Z',
            'favourited': False, 'reblogged': False, 'bookmarked': False,
            'favourites_count': 3, 'reblogs_count': 2}
    data.update(fields)
    return data


def buttons(actions):
    return (actions.favourite_button, actions.boost_button,
            actions.bookmark_button)


FLAGS = {'favourite': ('favourited', 'favourites_count', '/favourite'),
         'reblog': ('reblogged', 'reblogs_count', '/reblog'),
         'bookmark': ('bookmarked', None, '/bookmark')}

for kind, (flag, count_key, tail) in FLAGS.items():
    label = kind

    def setup(**fields):
        client = Client()
        item = StatusItem(status('7', **fields))
        actions = StatusActions()
        actions.bind(item, client)
        return client, item, actions

    # Optimistic, then failure rolls back
    client, item, actions = setup()
    before = dict(item.status)
    actions._send_toggle(kind, True)
    check('%s: request is the do endpoint' % label,
          client.requests == [('POST', '/statuses/7' + tail)],
          repr(client.requests))
    check('%s: flag flips before the reply' % label, item.status[flag] is True)
    if count_key:
        check('%s: count goes up before the reply' % label,
              item.status[count_key] == before[count_key] + 1)
    check('%s: busy while waiting' % label, actions.busy)
    check('%s: buttons disabled while waiting' % label,
          not any(b.get_sensitive() for b in buttons(actions)))
    client.fail()
    check('%s: failure restores the flag' % label,
          item.status[flag] == before[flag])
    if count_key:
        check('%s: failure restores the count exactly' % label,
              item.status[count_key] == before[count_key])
    check('%s: failure clears busy' % label, not actions.busy)
    check('%s: failure re-enables the buttons' % label,
          all(b.get_sensitive() for b in buttons(actions)))
    check('%s: failure is reported' % label, bool(reported))
    reported.clear()

    # Undoing an active toggle floors the count at zero, and a failure
    # puts the original value back
    client, item, actions = setup(**{flag: True, 'favourites_count': 0,
                                     'reblogs_count': 0})
    actions._send_toggle(kind, False)
    check('%s: flag clears before the reply' % label,
          item.status[flag] is False)
    if count_key:
        check('%s: count never goes below zero' % label,
              item.status[count_key] == 0)
    check('%s: undo uses the undo endpoint' % label,
          client.requests == [('POST', '/statuses/7' + tail.replace(
              'favourite', 'unfavourite').replace('reblog', 'unreblog')
              .replace('bookmark', 'unbookmark'))], repr(client.requests))
    client.fail()
    check('%s: failed undo restores the flag' % label,
          item.status[flag] is True)
    if count_key:
        check('%s: failed undo restores a zero count' % label,
              item.status[count_key] == 0)
    reported.clear()

    # Success merges the server body
    client, item, actions = setup()
    actions._send_toggle(kind, True)
    body = status('7', **{flag: True, 'favourites_count': 40,
                          'reblogs_count': 50})
    client.succeed(body)
    check('%s: success keeps the flag' % label, item.status[flag] is True)
    if count_key:
        check('%s: success takes the server count' % label,
              item.status[count_key] == body[count_key])
    check('%s: success clears busy' % label, not actions.busy)
    check('%s: nothing reported on success' % label, not reported)

    # Rebound before the reply: the new item is not touched
    client, item, actions = setup()
    other = StatusItem(status('8', favourites_count=9, reblogs_count=9))
    actions._send_toggle(kind, True)
    actions.bind(other, client)
    snapshot = dict(other.status)
    client.fail()
    check('%s: rebound item untouched by a failure' % label,
          other.status == snapshot)
    check('%s: the old item is rolled back' % label,
          item.status[flag] is False)
    check('%s: busy cleared after rebinding' % label, not actions.busy)

    client, item, actions = setup()
    other = StatusItem(status('8', favourites_count=9, reblogs_count=9))
    actions._send_toggle(kind, True)
    actions.bind(other, client)
    snapshot = dict(other.status)
    client.succeed(status('7', **{flag: True, 'favourites_count': 40,
                                  'reblogs_count': 50}))
    check('%s: rebound item untouched by a success' % label,
          other.status == snapshot)
    check('%s: the old item still takes the merge' % label,
          item.status[flag] is True)

if failures:
    print('\n%d FAILED' % len(failures))
    sys.exit(1)
print('all status toggle checks passed')
