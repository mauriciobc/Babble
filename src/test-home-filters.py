#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The home timeline's boost filter: the pure helper, the pages it applies
to, a page that filtering empties (which must not read as the end of the
timeline), and streamed posts. Exits 77 (skip) without a display."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
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

    def request_async(self, _method, _path, params=None, callback=None,
                      **_kw):
        if _path != '/timelines/home':
            return None
        self.params.append(dict(params or {}))
        callback(self, self.pages.pop(0) if self.pages else [])

    def request_finish(self, result):
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
# The real api.accounts.relationships_finish returns the first entry only
# (the profile page asks about one account), so the page has to read the
# whole list with relationships_all_finish; reading the first entry raised
# and left the page on "loading" forever.
def reply(number, author, parent=1):
    data = status(number)
    data['in_reply_to_id'] = '%05d' % parent
    data['account'] = {'acct': 'user%d' % author, 'id': str(author)}
    return data


class _RelationshipClient(_Client):
    def request_async(self, method, path, params=None, callback=None, **_kw):
        if path != '/accounts/relationships':
            return super().request_async(method, path, params=params,
                                         callback=callback, **_kw)
        callback(self, [{'id': i, 'following': i == '21'}
                        for i in params['id[]']])

    def request_finish(self, result):
        return result


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

    def request_async(self, _method, _path, params=None, callback=None,
                      **_kw):
        if _path != '/timelines/home':
            return None
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

# A relationship lookup that answers late. The Home reply filter asks who
# the authors of a page's replies are before the page may land, and the
# answer can arrive after something newer has taken the page's place (a
# refresh, an older page, a reload). These scenarios hold the answer until
# the test releases it, and pin what the paging guard in the _finish_*
# methods does with it: a superseded page is dropped, the busy flags and
# the bottom lock end clear, nothing is duplicated, the refresh chain ends.
# (A per-lookup generation counter used to sit around the lookup and was
# never incremented; these checks are why it was not needed.)
class _HeldClient(_Server):
    """A server whose /accounts/relationships answers wait for the test."""

    def __init__(self, posts):
        super().__init__(posts)
        self.held = []                     # [{'ids': [...], 'callback': f}]

    def request_async(self, method, path, params=None, callback=None,
                      **_kw):
        if path != '/accounts/relationships':
            return super().request_async(method, path, params=params,
                                         callback=callback, **_kw)
        self.held.append({'ids': list(params['id[]']), 'callback': callback})

    def request_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def answer(self, index, following=(), error=False):
        entry = self.held[index]
        if error:
            entry['callback'](self, GLib.Error('lookup failed'))
        else:
            entry['callback'](self, [
                {'id': i, 'following': i in following}
                for i in entry['ids']])

    def timeline_requests(self):
        return list(self.params)


def held_home(posts):
    pages.settings().set_boolean('hide-replies-non-follows', True)
    client = _HeldClient(posts)
    return client, home_page(client, False)


def plain(first, last):
    return [status(n) for n in range(first, last + 1)]


def flags_clear(page):
    return not page.model.loading and not page._loading_older \
        and not page._bottom_locked


def shown(page):
    return [int(i) for i in ids(page)]


# First page: while its lookup is out the model is loading, and nothing
# can supersede it (load, refresh and load_older all decline), so there is
# no stale first page to drop. The answer lands it and the page pages on.
client, page = held_home(plain(1, 58) + [reply(59, 22), reply(60, 21)])
check('first page: the lookup is out, the model is loading',
      len(client.held) == 1 and page.model.loading)
requests = len(client.timeline_requests())
page.load()
page.refresh()
page.load_older()
check('first page: load, refresh and load_older decline while it is out',
      len(client.timeline_requests()) == requests)
client.answer(0, following={'21'})
check('first page: the answer lands the followed reply, not the stranger',
      shown(page)[:3] == [60, 58, 57] and 59 not in shown(page))
check('first page: flags end clear', flags_clear(page))
# The filtered page is short, so it already topped itself up from the
# server (the strangers' lookup has been answered); one more is still
# possible.
requests = len(client.timeline_requests())
page.load_older()
check('first page: the page can page on afterwards',
      len(client.timeline_requests()) == requests + 1)

# First page, reloaded over a non-empty list: the reload's lookup is out,
# an older page (the reader at the bottom) takes its place and lands.
client, page = held_home(plain(1, 60))
client.grow([reply(61, 21), reply(62, 22)])
page.load()
check('reload: a lookup is out for the reload',
      len(client.held) == 1 and page.model.loading)
page.load_older()
check('reload: the older page went out and landed',
      21 in shown(page) and page.model.loading and not page._loading_older)
before = shown(page)
client.answer(0, following={'21'})
check('reload: the stale page is kept out of the model',
      shown(page) == before and 61 not in shown(page))
check('reload: flags end clear after the stale answer', flags_clear(page))
page.refresh()
check('reload: a refresh then completes the page normally',
      shown(page)[:2] == [61, 60] and 62 not in shown(page))
check('reload: the refresh left the flags clear', flags_clear(page))

# Older page: its lookup is out (flag set, bottom locked) when a refresh
# supersedes it. The stale answer drops the page, clears the flags, frees
# the lock, and the page can be asked for again.
client, page = held_home(plain(1, 38) + [reply(39, 21), reply(40, 22)]
                         + plain(41, 60))
check('older: the first page needs no lookup', not client.held)
page.load_older()
check('older: its lookup is out, flag set, bottom locked',
      len(client.held) == 1 and page._loading_older and page._bottom_locked)
client.grow([status(61)])
page.refresh()
check('older: a refresh took its place and landed',
      shown(page)[0] == 61 and page._loading_older and page._bottom_locked)
before = shown(page)
older_requests = [p for p in client.timeline_requests() if 'max_id' in p]
client.answer(0, following={'21'})
check('older: the stale page is kept out of the model',
      shown(page) == before)
check('older: flags end clear and the bottom lock is released',
      flags_clear(page))
page.load_older()
again = [p for p in client.timeline_requests() if 'max_id' in p]
check('older: the reader can page again, from the same cursor',
      len(again) == len(older_requests) + 1
      and again[-1]['max_id'] == '00041')
# Both authors were recorded by the stale answer (21 followed, 22 not), so
# the second try needs no new lookup and lands at once.
check('older: the second try needed no lookup', len(client.held) == 1)
check('older: the second try lands the followed reply only',
      39 in shown(page) and 40 not in shown(page) and flags_clear(page))
check('older: no duplicates', len(shown(page)) == len(set(shown(page))))

# Refresh, then a second refresh and an older page, then the first
# lookup answers. The refresh insert is idempotent; the cursor never
# goes backwards; the chain ends.
client, page = held_home(plain(1, 60))
client.grow(plain(61, 62) + [reply(63, 21), reply(64, 22)])
page.refresh()
check('refresh: a lookup is out, the model is not loading',
      len(client.held) == 1 and not page.model.loading)
page.refresh()                             # a second refresh, same page
check('refresh: the second refresh asked again with its own lookup',
      len(client.held) == 2)
page.load_older()                          # and an older page lands
older_len = len(shown(page))
client.answer(1, following={'21'})
after_second = shown(page)
check('refresh: the second answer landed the followed reply',
      64 not in after_second and 63 in after_second
      and 62 in after_second)
client.answer(0, following={'21'})
check('refresh: the late first answer adds nothing and duplicates nothing',
      shown(page) == after_second
      and len(shown(page)) == len(set(shown(page))))
check('refresh: newest_id is the newest the server sent',
      page.model.newest_id == '00064')
check('refresh: the chain ended and the flags are clear',
      page._refresh_chain == 0 and flags_clear(page))
check('refresh: the list is in server order',
      shown(page) == sorted(shown(page), reverse=True)
      and older_len <= len(shown(page)))
sent = len(client.timeline_requests())
page.refresh()
check('refresh: the next refresh asks above the new cursor',
      client.timeline_requests()[-1].get('min_id') == '00064'
      and len(client.timeline_requests()) == sent + 1)

# A refresh that is a full page continues the chain; the late answer of a
# superseded refresh must not run the chain twice past the cap, and must
# end it.
client, page = held_home(plain(1, 20))
client.grow(plain(21, 38) + [reply(39, 21), reply(40, 22)])   # a full page
page.refresh()
page.refresh()
check('refresh chain: two lookups out for the same full page',
      len(client.held) == 2)
client.answer(0, following={'21'})
check('refresh chain: the first answer continued the chain with a request',
      39 in shown(page) and 40 not in shown(page))
client.answer(1, following={'21'})
check('refresh chain: the late answer duplicates nothing',
      len(shown(page)) == len(set(shown(page))))
requests = len(client.timeline_requests())
check('refresh chain: it ended (cursor past the page, no request left)',
      page._refresh_chain == 0 and flags_clear(page)
      and page.model.newest_id == '00040')
check('refresh chain: a quiet server ends it with a short page',
      client.timeline_requests()[-1].get('min_id') == '00040'
      and requests <= 6)

# Two lookups answering in the opposite order from which they were sent.
client, page = held_home(plain(1, 60))
client.grow([reply(61, 21)])
page.refresh()                             # R1, lookup 0
page.refresh()                             # R2, lookup 1
client.answer(1, following=())            # R2 first: 21 is a stranger
check('opposite order: the later answer hid the reply',
      shown(page)[0] == 60 and page.model.newest_id == '00061')
client.answer(0, following={'21'})         # then R1 says 21 is followed
check('opposite order: the earlier answer, late, adds the reply once',
      shown(page)[:2] == [61, 60]
      and len(shown(page)) == len(set(shown(page))))
check('opposite order: flags clear, chain ended',
      flags_clear(page) and page._refresh_chain == 0)

client, page = held_home(plain(1, 38) + [reply(39, 21), reply(40, 22)]
                         + plain(41, 60))
page.load_older()                          # older, lookup 0
client.grow([reply(61, 22)])
page.refresh()                             # refresh, lookup 1
client.answer(1, following={'22'})
check('opposite order: the refresh landed first',
      shown(page)[0] == 61)
client.answer(0, following={'21'})
check('opposite order: the older answer, late, is dropped cleanly',
      39 not in shown(page) and flags_clear(page))

# A failed late lookup, on each path: the page is filtered with what is
# known (strangers hidden) or dropped when superseded, and nothing hangs.
client, page = held_home(plain(1, 58) + [reply(59, 22), reply(60, 21)])
client.answer(0, error=True)
check('failed lookup, first page: lands with the strangers hidden',
      shown(page)[:2] == [58, 57] and flags_clear(page))

client, page = held_home(plain(1, 60))
client.grow([reply(61, 21)])
page.load()
page.load_older()
client.answer(0, error=True)
check('failed lookup, superseded reload: dropped, flags clear',
      61 not in shown(page) and flags_clear(page))

client, page = held_home(plain(1, 38) + [reply(39, 21), reply(40, 22)]
                         + plain(41, 60))
page.load_older()
client.answer(0, error=True)
check('failed lookup, older page: lands, strangers hidden, lock free',
      39 not in shown(page) and 38 in shown(page) and flags_clear(page))
page.load_older()
check('failed lookup, older page: the reader pages on',
      len(client.timeline_requests()) > 2)

client, page = held_home(plain(1, 38) + [reply(39, 21), reply(40, 22)]
                         + plain(41, 60))
page.load_older()
client.grow([status(61)])
page.refresh()
client.answer(0, error=True)
check('failed lookup, superseded older page: dropped, flags clear',
      38 not in shown(page) and flags_clear(page))

client, page = held_home(plain(1, 60))
client.grow([reply(61, 21), reply(62, 22)])
page.refresh()
client.answer(0, error=True)
check('failed lookup, refresh: lands, strangers hidden, chain ended',
      shown(page)[0] == 60 and 61 not in shown(page)
      and page._refresh_chain == 0 and flags_clear(page))

client, page = held_home(plain(1, 60))
client.grow([reply(61, 21)])
page.refresh()
page.refresh()
client.answer(1, following={'21'})
client.answer(0, error=True)
check('failed lookup, superseded refresh: nothing lost, no duplicate',
      shown(page)[0] == 61 and len(shown(page)) == len(set(shown(page)))
      and page._refresh_chain == 0 and flags_clear(page))
pages.settings().reset('hide-replies-non-follows')

# Only the home timeline is filtered.
pages.settings().set_boolean('hide-boosts-home', True)
public = timelinepage.TimelinePage(_Client([]), mode='public',
                                   autoload=False)
check('the public timeline is not filtered', not public._hide_boosts)

pages.settings().reset('hide-boosts-home')
if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
