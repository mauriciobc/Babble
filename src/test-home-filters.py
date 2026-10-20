#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The home timeline's boost filter: the pure helper, the pages it applies
to, a page that filtering empties (which must not read as the end of the
timeline), and streamed posts. Exits 77 (skip) without a display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402,F401

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import pages, timelinedata, timelinepage  # noqa: E402


failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def status(number):
    return {'id': '%05d' % number, 'content': '<p>post %d</p>' % number,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}


def boost(number, of):
    wrapper = status(number)
    wrapper['reblog'] = status(of)
    return wrapper


# The helper keeps originals, drops boosts, and leaves its input alone.
mixed = [status(9), boost(8, 1), status(7)]
kept = timelinedata.filter_boosts(mixed)
check('filter_boosts drops boosts only',
      [s['id'] for s in kept] == ['00009', '00007'])
check('filter_boosts leaves its input alone', len(mixed) == 3)
check('filter_boosts of nothing is nothing',
      timelinedata.filter_boosts([]) == [])


class _Client:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def __init__(self, pages_):
        self.pages = list(pages_)
        self.params = []

    def rate_limited(self):
        return False

    def timelines_home_async(self, params=None, callback=None, **_kw):
        self.params.append(dict(params or {}))
        callback(self, self.pages.pop(0) if self.pages else [])

    def timelines_home_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def home_page(client, hide_boosts):
    pages.settings().set_boolean('hide-boosts-home', hide_boosts)
    page = timelinepage.TimelinePage(client, mode='home', autoload=False)
    page.ensure_loaded()
    return page


def ids(page):
    return [page.model.get_item(i).raw['id']
            for i in range(page.model.get_n_items())
            if hasattr(page.model.get_item(i), 'raw')]


# Off: boosts stay.
page = home_page(_Client([[status(9), boost(8, 1)]]), False)
check('boosts are shown when the setting is off', len(ids(page)) == 2)

# On: the first page drops them.
page = home_page(_Client([[status(9), boost(8, 1), status(7)]]), True)
check('boosts are hidden on the first page',
      ids(page) == ['00009', '00007'])

# On: an older page of nothing but boosts moves the cursor and does not
# mark the timeline exhausted.
client = _Client([[status(9)], [boost(6, 1), boost(5, 2)], [status(4)]])
page = home_page(client, True)
page.load_older()
check('an all-boost older page does not end the timeline',
      not page.model.exhausted)
# The all-boost page leaves the list as it was, so the page keeps paging
# on its own: the third page is requested and its post lands.
check('paging went on past it', '00004' in ids(page))

# On: a refresh drops boosts too.
page = home_page(_Client([[status(9)], [boost(10, 1), status(11)]]), True)
page.refresh()
check('boosts are hidden on refresh', '00010' not in ids(page)
      and '00011' in ids(page))

# On: a streamed boost is not inserted, a streamed post is.
page = home_page(_Client([[status(9)]]), True)
page.on_stream_update(boost(12, 1))
page.on_stream_update(status(13))
check('a streamed boost is dropped', '00012' not in ids(page))
check('a streamed post is kept', '00013' in ids(page))

# Only the home timeline is filtered.
pages.settings().set_boolean('hide-boosts-home', True)
public = timelinepage.TimelinePage(_Client([]), mode='public',
                                   autoload=False)
check('the public timeline is not filtered', not public._hide_boosts)

pages.settings().reset('hide-boosts-home')
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
