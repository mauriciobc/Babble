#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""instance_info coalesce: one shared HTTP call, per-waiter cancel.

Needs no display. Cancelling the first waiter must not fail the others,
and a cancelled waiter must not receive the answer.
"""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

import gi  # noqa: E402

gi.require_version('Soup', '3.0')

from gi.repository import Gio, GLib  # noqa: E402

from src.api import instance as api_instance  # noqa: E402
from src.mastodon import MastodonClient  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


def drain():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


INFO = {'configuration': {'statuses': {'max_characters': 500}},
        'api_versions': {'mastodon': 4}}

pending = []
real_info_async = api_instance.info_async


def fake_info_async(client, callback=None, cancellable=None):
    pending.append({'callback': callback, 'cancellable': cancellable})
    return None


api_instance.info_async = fake_info_async
try:
    client = MastodonClient('https://example.test')
    answers = []

    def remember(label):
        def done(source, result, _data=None):
            try:
                answers.append((label, source.instance_info_finish(result)))
            except GLib.Error as error:
                answers.append((label, error))
        return done

    first_cancel = Gio.Cancellable()
    client.instance_info_async(callback=remember('a'),
                               cancellable=first_cancel)
    client.instance_info_async(callback=remember('b'),
                               cancellable=None)
    check('coalesce starts one shared request', len(pending) == 1)
    check('shared request takes no caller cancellable',
          pending[0]['cancellable'] is None)

    first_cancel.cancel()
    # What the shared loader does on success, without a real HTTP body.
    client.instance_info = INFO
    client._instance_info_in_flight = False
    client._complete_instance_info_waiters(None)
    drain()

    by_label = dict(answers)
    check('cancelled first waiter gets CANCELLED',
          isinstance(by_label.get('a'), GLib.Error)
          and by_label['a'].matches(
              Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED),
          repr(answers))
    check('second waiter receives the instance document',
          by_label.get('b') == INFO, repr(answers))

    # A later caller with a cancelled cancellable still finishes CANCELLED.
    later = []
    dead = Gio.Cancellable()
    dead.cancel()

    def late(_source, result, _data=None):
        try:
            later.append(client.instance_info_finish(result))
        except GLib.Error as error:
            later.append(error)

    before = len(pending)
    client.instance_info_async(callback=late, cancellable=dead)
    drain()
    check('cache hit starts no new shared request',
          len(pending) == before)
    check('cancelled waiter on a cache hit gets CANCELLED',
          len(later) == 1 and isinstance(later[0], GLib.Error)
          and later[0].matches(
              Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED),
          repr(later))
finally:
    api_instance.info_async = real_info_async

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
