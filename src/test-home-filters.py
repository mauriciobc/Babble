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

from gi.repository import Adw, GLib, Gtk  # noqa: E402

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

# On: an older page of nothing but boosts moves the cursor instead of
# reading as the end of the timeline: the next page still lands.
client = _Client([[status(9)], [boost(6, 1), boost(5, 2)], [status(4)]])
page = home_page(client, True)
page.load_older()
check('paging went on past an all-boost older page', '00004' in ids(page))

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

# Hiding replies from non-follows asks the server who the authors are.
# The real relationships_finish returns the first entry only (the profile
# page asks about one account), so the page has to read the whole list
# with request_finish; reading the first entry raised and left the page
# on "loading" forever.
def reply(number, author, parent=1):
    data = status(number)
    data['in_reply_to_id'] = '%05d' % parent
    data['account'] = {'acct': 'user%d' % author, 'id': str(author)}
    return data


class _RelationshipClient(_Client):
    def relationships_async(self, ids, callback=None, **_kw):
        callback(self, [{'id': i, 'following': i == '21'} for i in ids])

    def request_finish(self, result):
        return result

    def relationships_finish(self, result):
        return result[0] if result else {}


pages.settings().set_boolean('hide-replies-non-follows', True)
client = _RelationshipClient([[status(9), reply(8, 21), reply(7, 22)]])
page = home_page(client, False)
check('a followed author reply is kept', '00008' in ids(page))
check('a reply from a stranger is hidden', '00007' not in ids(page))
check('the page left the loading state', not page.model.loading)
pages.settings().reset('hide-replies-non-follows')

# A page the filter left too short to scroll keeps asking for older posts:
# scrolling is what pages, and five posts fit on the screen.
def spin(milliseconds):
    loop = GLib.MainLoop()
    GLib.timeout_add(milliseconds, loop.quit)
    loop.run()


Adw.init()
older = [[status(100 - n)] for n in range(2, 8)]
client = _Client([[status(99), boost(98, 1), boost(97, 1)], *older])
page = home_page(client, True)
window = Gtk.Window(default_width=600, default_height=900)
window.set_child(page)
window.present()
spin(1500)
check('a short filtered page pages on by itself', len(client.params) > 3)
window.destroy()

# Reaching the bottom edge asks for older posts even when nothing scrolls.
client = _Client([[status(99)], [status(98)]])
page = home_page(client, False)
requests = len(client.params)
page._scrolled.emit('edge-reached', Gtk.PositionType.TOP)
check('the top edge asks for nothing', len(client.params) == requests)
page._scrolled.emit('edge-reached', Gtk.PositionType.BOTTOM)
check('reaching the bottom edge pages on',
      len(client.params) == requests + 1)

# Unfiltered, the same short first page does not trigger extra requests.
client = _Client([[status(99), status(98)], *older])
page = home_page(client, False)
window = Gtk.Window(default_width=600, default_height=900)
window.set_child(page)
window.present()
spin(800)
check('an unfiltered page makes no extra request', len(client.params) == 1)
window.destroy()

# Refresh must not lose posts. A fake server with Mastodon's real cursor
# semantics: since_id answers with the NEWEST page above the cursor (so the
# oldest posts are skipped when more than a page arrived), min_id with the
# page immediately above it. Verified against a live instance.
class _Server(_Client):
    def __init__(self, posts):
        super().__init__([])
        self.posts = list(posts)          # ascending ids

    def grow(self, posts):
        self.posts.extend(posts)

    def timelines_home_async(self, params=None, callback=None, **_kw):
        params = dict(params or {})
        self.params.append(params)
        limit = int(params.get('limit', 20))
        if 'min_id' in params:
            above = [p for p in self.posts if int(p['id']) > int(params['min_id'])]
            out = above[:limit][::-1]
        elif 'since_id' in params:
            above = [p for p in self.posts if int(p['id']) > int(params['since_id'])]
            out = above[-limit:][::-1]
        elif 'max_id' in params:
            below = [p for p in self.posts if int(p['id']) < int(params['max_id'])]
            out = below[-limit:][::-1]
        else:
            out = self.posts[-limit:][::-1]
        callback(self, out)


pages.settings().reset('hide-replies-non-follows')
server = _Server([status(n) for n in range(1, 101)])
page = home_page(server, False)
server.grow([status(n) for n in range(101, 146)])      # 45 arrived meanwhile
page.refresh()
shown = [int(i) for i in ids(page)]
check('a refresh after 45 new posts leaves no gap',
      shown[:45] == list(range(145, 100, -1)))
check('the posts stay in server order', shown == sorted(shown, reverse=True))
check('a refresh asks min_id, never since_id',
      all('since_id' not in p for p in server.params))
check('it stops on a short page', len(server.params) == 4)

# Filters that empty a whole refresh page still move the cursor, and the
# chain ends instead of asking for the same page again.
server = _Server([status(n) for n in range(1, 21)])
page = home_page(server, True)
server.grow([boost(n, 500 + n) for n in range(21, 61)])  # 40 boosts
before = len(server.params)
page.refresh()
check('a fully filtered refresh terminates',
      len(server.params) - before == 3)
check('its cursor moved to the newest id the server sent',
      page.model.newest_id == '00060')
check('no boost got through', all(int(i) <= 20 for i in ids(page)))

# Only the home timeline is filtered.
pages.settings().set_boolean('hide-boosts-home', True)
public = timelinepage.TimelinePage(_Client([]), mode='public',
                                   autoload=False)
check('the public timeline is not filtered', not public._hide_boosts)

pages.settings().reset('hide-boosts-home')
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
