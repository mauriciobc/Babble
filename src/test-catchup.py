#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Catch Up page against a fake client: the first-run page, a full run
that writes a snapshot, the All posts list and its filters and sorts, the
Highlights slot staying put while they change, cancel and 429 keeping what
was gathered, snapshot retention and reopening, and the cleanup when an
account is removed. Also the pieces under the page that need no widget
(ordering, the snapshot files, the run's stop logic).

Exits 77 (skip) without a display."""

import datetime
import gc
import json
import os
import shutil
import stat
import sys
import tempfile

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import accounts, catchup, catchupdigest, catchupstore  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


Adw.init()
CACHE = tempfile.mkdtemp(prefix='babble-catchup-test-')
catchupstore.set_root(os.path.join(CACHE, 'catch-up'))
catchup.FETCH_PAUSE_SECONDS = 0
catchup.BUILD_CHUNK = 25                 # several idle chunks per snapshot

KEY = 'https://test.example#99'
ACCOUNT = {'id': '99', 'instance': 'https://test.example', 'acct': 'me',
           'display_name': 'Me', 'source': {'language': 'en'}}
NOW = datetime.datetime.now(datetime.timezone.utc)


def settle(condition=None, timeout=15.0):
    """Run the main loop until *condition()* holds (or a short while)."""
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(timeout * 1000000)
    while GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        if condition is not None and condition():
            return True
        if condition is None:
            return True
        GLib.usleep(2000)
    return condition() if condition is not None else True


def stamp(minutes_ago):
    moment = NOW - datetime.timedelta(minutes=minutes_ago)
    return catchup.iso(moment)


def account(n):
    return {'id': str(n), 'acct': 'user%d' % n, 'username': 'user%d' % n,
            'display_name': 'User %d' % n, 'avatar': '', 'url': 'u%d' % n}


def status(n, minutes_ago, **extra):
    data = {'id': '%06d' % n, 'created_at': stamp(minutes_ago),
            'account': account(n % 6 + 1),
            'content': '<p>post number %d</p>' % n, 'spoiler_text': '',
            'sensitive': False, 'media_attachments': [], 'tags': [],
            'replies_count': n % 5, 'reblogs_count': n % 7,
            'favourites_count': n % 11, 'in_reply_to_id': None,
            'in_reply_to_account_id': None, 'reblog': None, 'card': None,
            'mentions': [], 'emojis': [], 'uri': 'https://x/%d' % n,
            'url': 'https://x/%d' % n, 'visibility': 'public'}
    data.update(extra)
    return data


def timeline(count=90, spacing=20):
    """*count* posts, newest first, *spacing* minutes apart, with every
    category in them."""
    rebloggable = status(900, 600, account=account(7))
    posts = []
    for n in range(count):
        top = count - n                    # ids rise towards the newest
        minutes = n * spacing
        extra = {}
        if n in (3, 13):                   # two boosts of one post
            extra = {'reblog': rebloggable}
        elif n % 10 == 6:                  # a boost of another one
            extra = {'reblog': status(800 + n, minutes + 90,
                                      account=account(8))}
        elif n % 7 == 2:                   # a reply to someone else
            extra = {'in_reply_to_id': '%06d' % (top - 1),
                     'in_reply_to_account_id': '77'}
        elif n == 5:                       # a warn filter hit
            extra = {'filtered': [{'filter': {
                'title': 'noise', 'filter_action': 'warn',
                'context': ['home']}}]}
        elif n % 9 == 1:                   # a followed tag
            extra = {'tags': [{'name': 'linux'}],
                     'content': '<p>#<a class="hashtag" '
                                'href="https://x/tags/linux">linux</a></p>'}
        elif n % 11 == 4:                  # media
            extra = {'media_attachments': [{
                'type': 'image', 'url': 'https://x/i.png',
                'preview_url': 'https://x/p.png'}]}
        elif n % 13 == 8:
            extra = {'spoiler_text': 'warning', 'sensitive': True}
        posts.append(status(top, minutes, **extra))
    return posts


class FakeClient:
    """Answers the endpoints a catch-up reads, from fixtures, through the
    same async calls as MastodonClient (the result handed back to
    ``request_finish*`` is whatever this class made)."""

    instance = 'https://test.example'
    token = 'token'

    def __init__(self, posts=None, page_size=40, followed=('linux',)):
        self.posts = posts if posts is not None else timeline()
        self.page_size = page_size
        self.followed = list(followed)
        self.calls = []
        self.limited = False
        self.fail_page = None             # (page number, code or None)
        self.fail_trends = False
        self.on_page = None

    def rate_limited(self):
        return self.limited

    def retry_after_seconds(self):
        return 120 if self.limited else 0

    def paths(self):
        return [path for _method, path, _params in self.calls]

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        result = self._answer(path, params)

        def deliver():
            if cancellable is not None and cancellable.is_cancelled():
                result_ = ('error', GLib.Error.new_literal(
                    Gio.io_error_quark(), 'Cancelled',
                    Gio.IOErrorEnum.CANCELLED))
            else:
                result_ = result
            callback(self, result_, None)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)

    def _answer(self, path, params):
        if path == '/timelines/home':
            number = sum(1 for p in self.paths() if p == '/timelines/home')
            if self.fail_page and self.fail_page[0] == number:
                code = self.fail_page[1] or 500
                if code == 429:
                    self.limited = True
                return ('error', GLib.Error(
                    'failed', 'mastodon-http-error-quark', code))
            older = [p for p in self.posts
                     if 'max_id' not in params or p['id'] < params['max_id']]
            page = older[:self.page_size]
            nxt = {'limit': '40', 'max_id': page[-1]['id']} if page else None
            if self.on_page is not None:
                self.on_page(number)
            return ('ok', page, nxt)
        if path == '/followed_tags':
            return ('ok', [{'name': n} for n in self.followed], None)
        if path == '/accounts/relationships':
            ids = params.get('id[]') or []
            return ('ok', [{'id': i, 'following': int(i) % 2 == 0,
                            'followed_by': int(i) % 3 == 0} for i in ids],
                    None)
        if path.startswith('/trends/'):
            if self.fail_trends:
                return ('error', GLib.Error('nope',
                                            'mastodon-http-error-quark', 500))
            if path == '/trends/links':
                return ('ok', [{'url': 'https://news.example/a/', 'title': 'A',
                                'type': 'link', 'image': 'https://x/a.png'}],
                        None)
            if path == '/trends/tags':
                return ('ok', [{'name': 'linux', 'history': []},
                               {'name': 'gnome', 'history': []}], None)
            return ('ok', [{'id': self.posts[0]['id'], 'uri': 'u'}], None)
        return ('ok', [], None)

    def request_finish(self, result):
        if result[0] == 'error':
            raise result[1]
        return result[1]

    def request_finish_paged(self, result):
        if result[0] == 'error':
            raise result[1]
        return result[1], result[2], None


# --------------------------------------------------------------------------
# Ordering and filtering: the same as catchupdigest, over cached keys

snapshot = {'version': 1, 'id': 'ordering', 'account': KEY,
            'start_at': None, 'end_at': catchup.iso(NOW), 'period': '24h',
            'stopped': 'complete', 'posts': timeline(),
            'relationships': {'8': {'following': True, 'followed_by': True}},
            'followed_tags': ['linux'],
            'trends': {'links': None, 'tags': None, 'statuses': None}}
snapshot['posts'] = catchupdigest.apply_filters(snapshot['posts'], ['99'])
catchupdigest.mark_threads(snapshot['posts'])
computed = catchup.compute(snapshot, ['en'])
by_id = {p['id']: p for p in computed.visible}
same = True
for sort_by in catchupdigest.SORTS:
    for order in ('asc', 'desc'):
        for group in (False, True):
            posts = computed.visible
            ours = catchup.order_ids(
                [computed.info[p['id']] for p in posts], sort_by, order,
                group, {a: i for i, a in enumerate(
                    catchupdigest.author_order(posts))} if group else None)
            theirs = [p['id'] for p in catchupdigest.sort_posts(
                posts, sort_by, order, group, computed.scores)]
            same = same and ours == theirs
check('the cached-key ordering is catchupdigest.sort_posts', same)
check('two boosts of one post collapse into the first',
      len(computed.visible) == len(snapshot['posts']) - 1
      and len(computed.dup_of) == 1)
check('the categories add up to the visible posts',
      sum(computed.counts.values()) == len(computed.visible)
      and computed.counts['boosts'] > 0 and computed.counts['replies'] > 0
      and computed.counts['filtered'] == 1)
check('the Filtered category never reaches the ranking',
      all(not catchupdigest.is_filtered_post(p) for p in computed.ranked)
      and computed.counts['filtered']
      == len(computed.visible) - len(computed.ranked))
view = catchup.derive_view(computed, catchup.ViewState(category='boosts'))
check('a category narrows the view',
      len(view.matching) == computed.counts['boosts']
      and all(by_id[i].get('reblog') for i in view.matching))
author = view.category_authors[0]
view = catchup.derive_view(computed, catchup.ViewState(
    category='boosts', author=author))
check('an author narrows it further',
      0 < len(view.matching) <= computed.counts['boosts'])
view = catchup.derive_view(computed, catchup.ViewState(author='nobody'))
check('an author with no posts is ignored',
      view.author is None and len(view.matching) == len(computed.visible))
check('ranking is deterministic',
      [p['id'] for p in catchup.compute(snapshot, ['en']).ranked]
      == [p['id'] for p in computed.ranked])
text = catchup.view_summary(catchup.ViewState(
    category='boosts', sort='likes', order='desc', group=True))
check('the toast is put together from its parts',
      text == 'Showing boosts, most likes first, grouped by authors')
check('the period titles follow the period',
      catchup.period_title('1h') == 'Highlights of the last hour'
      and catchup.period_title('6h') == 'Highlights of the last 6 hours'
      and catchup.period_title('since')
      == 'Highlights since your last catch-up'
      and catchup.period_title('max') == 'Highlights')

# --------------------------------------------------------------------------
# Snapshot files

store_key = 'https://store.example#1'
saved = []
for n in range(5):
    snap = dict(snapshot, id='snap%d' % n, account=store_key,
                end_at=catchup.iso(NOW + datetime.timedelta(hours=n)),
                count=len(snapshot['posts']))
    path = catchupstore.write_snapshot(snap)
    saved.append(path)
directory = catchupstore.account_dir(store_key)
check('the snapshot directory is private',
      stat.S_IMODE(os.stat(directory).st_mode) == 0o700)
check('a snapshot file is private',
      all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in saved))
check('no temporary file is left behind',
      not [n for n in os.listdir(directory) if n.endswith('.tmp')])
meta = catchupstore.read_meta(saved[0])
check('the head of a snapshot is read without its posts',
      meta['id'] == 'snap0' and 'posts' not in meta)
loaded = catchupstore.read_snapshot(saved[0])
check('a snapshot reads back as it was written',
      loaded['posts'] == snapshot['posts']
      and loaded['trends'] == snapshot['trends']
      and loaded['followed_tags'] == ['linux'])
check('the snapshot is plain JSON',
      json.load(open(saved[0]))['posts'] == snapshot['posts'])
empty = dict(snapshot, id='empty', account=store_key, posts=[])
check('a snapshot without posts round-trips',
      catchupstore.read_snapshot(catchupstore.write_snapshot(empty))
      ['posts'] == [])
kept = catchupstore.prune(store_key)
check('only the three newest snapshots are kept',
      [m['id'] for m in kept] == ['snap4', 'snap3', 'snap2']
      and len(catchupstore.list_snapshots(store_key)) == 3)
with open(os.path.join(directory, 'broken.json'), 'w') as handle:
    handle.write('{ not json')
check('a broken file is skipped',
      [m['id'] for _p, m in catchupstore.list_snapshots(store_key)]
      == ['snap4', 'snap3', 'snap2'])
catchupstore.delete_account(store_key)
check('an account\'s snapshots can be deleted at once',
      not os.path.exists(directory))
other = dict(snapshot, id='keep', account='https://other.example#1')
other_path = catchupstore.write_snapshot(other)
check('two instances with one id do not share a directory',
      catchupstore.account_dir('https://other.example#1')
      != catchupstore.account_dir('https://store.example#1')
      and os.path.exists(other_path))


# --------------------------------------------------------------------------
# The run, without a page

def run_to_end(client, hours=24, stop_on=None, hide_trending=False):
    end_at = catchup.now_utc()
    start_at = end_at - datetime.timedelta(hours=hours) if hours else None
    got = []
    run = catchup.CatchUpRun(client, start_at, end_at, ['99'],
                             hide_trending, done=got.append, pause_seconds=0)
    if stop_on is not None:
        run.progress = lambda evaluated, oldest: (
            run.stop() if run.pages_done == stop_on else None)
    run.start()
    settle(lambda: bool(got))
    return got[0] if got else None


client = FakeClient()
result = run_to_end(client)
in_range = [p for p in client.posts
            if catchupdigest.in_range(p, NOW - datetime.timedelta(hours=24))]
check('the run pages back until a page has nothing in range',
      result is not None and result.stopped == 'complete'
      and client.paths().count('/timelines/home') == 3
      and len(result.posts) == len(in_range))
check('the next page continues from the Link cursor',
      [c[2].get('max_id') for c in client.calls
       if c[1] == '/timelines/home'][1:] == [client.posts[39]['id'],
                                             client.posts[79]['id']])
check('every request asks for 40 posts',
      all(c[2].get('limit') in (40, '40') for c in client.calls
          if c[1] == '/timelines/home'))
batches = [c[2]['id[]'] for c in client.calls
           if c[1] == '/accounts/relationships']
check('relationships are fetched in batches by id[]',
      batches and all(0 < len(b) <= catchup.RELATIONSHIP_BATCH
                      for b in batches))
check('the three trends are fetched',
      {'/trends/links', '/trends/tags', '/trends/statuses'}
      <= set(client.paths()))
check('the relationships and followed tags are kept',
      result.followed_tags == ['linux'] and result.relationships
      and set(result.trends) == {'links', 'tags', 'statuses'})
check('followed-tag posts of people you do not follow are marked',
      any(p.get('_followed_tags') for p in result.posts))
check('threads are marked', any(p.get('_thread') for p in result.posts))

client = FakeClient()
result = run_to_end(client, hide_trending=True)
check('hide-trending skips the trends',
      not [p for p in client.paths() if p.startswith('/trends/')]
      and result.trends == {})

client = FakeClient()
client.fail_trends = True
result = run_to_end(client)
check('a failing trends call degrades to None, not to a failure',
      result.stopped == 'complete' and result.trends['links'] is None
      and result.trends['tags'] is None and len(result.posts) > 0)

client = FakeClient(page_size=10)
result = run_to_end(client, stop_on=2)
check('Cancel keeps the pages gathered so far',
      result.stopped == 'cancelled'
      and client.paths().count('/timelines/home') == 2
      and len(result.posts) == 20)

client = FakeClient(page_size=10)
client.fail_page = (3, 429)
result = run_to_end(client)
check('a 429 stops the paging and keeps what was gathered',
      result.stopped == 'rate-limited' and len(result.posts) == 20
      and result.retry_after == 120)

client = FakeClient(page_size=10)
client.fail_page = (2, 500)
result = run_to_end(client)
check('another error stops it the same way',
      result.stopped == 'error' and len(result.posts) == 10
      and result.error == 'failed')

client = FakeClient()
client.limited = True
result = run_to_end(client)
check('a rate-limited client is not asked at all',
      result.stopped == 'rate-limited' and not result.posts
      and client.paths() == [])

client = FakeClient(posts=timeline(count=1400, spacing=1), page_size=40)
result = run_to_end(client, hours=None)
check('the safety cap stops an endless timeline',
      result.stopped == 'cap'
      and client.paths().count('/timelines/home') == 25
      and len(result.posts) == 1000)

client = FakeClient(page_size=10)
got = []
run = catchup.CatchUpRun(client, None, catchup.now_utc(), [],
                         done=got.append, pause_seconds=0)
run.start()
run.abort()
settle(lambda: False, timeout=0.3)
check('an aborted run says nothing and asks for nothing more',
      not got and client.paths().count('/timelines/home') == 1)


# --------------------------------------------------------------------------
# The page

class StubHighlights(Gtk.Box):
    """Stands in for catchupgrid.HighlightsView."""

    instances = []

    def __init__(self, client):
        super().__init__()
        self.digests = []
        StubHighlights.instances.append(self)

    def set_digest(self, digest):
        self.digests.append(digest)


class StubModule:
    HighlightsView = StubHighlights


sys.modules['src.catchupgrid'] = StubModule


def make_page(client, key_account=ACCOUNT):
    page = catchup.CatchUpPage(client, account=key_account)
    host = Gtk.Box()
    host.append(page)
    return page, host


def posts_listed(page):
    return page.sorted.get_n_items() - 1       # the block above the posts


def listed_ids(page):
    return [page.sorted.get_item(i).raw['id']
            for i in range(1, page.sorted.get_n_items())]


shutil.rmtree(CACHE, ignore_errors=True)
os.makedirs(CACHE)
catchupstore.set_root(os.path.join(CACHE, 'catch-up'))

client = FakeClient()
page, host = make_page(client)
settle(lambda: page.state != 'loading')
check('a first run shows the Catch Up page, not a fetch',
      page.state == 'first-run' and client.calls == [])
check('the first-run page has the period menu and a Catch Up button',
      page.catch_up_button.has_css_class('suggested-action')
      and page.first_run_page.get_title() == 'Catch Up'
      and page._period_buttons[0].get_label() == 'Last 24 hours')
page._period_action.change_state(GLib.Variant('s', '6h'))
check('choosing a period on the first run only selects it',
      page.state == 'first-run' and client.calls == []
      and page._period_buttons[0].get_label() == 'Last 6 hours')
page._period_action.change_state(GLib.Variant('s', '24h'))

page.catch_up_button.emit('clicked')
check('pressing Catch Up scans the timeline', page.state == 'scanning')
settle(lambda: page.state == 'results')
check('a finished run shows the results', page.state == 'results')
files = catchupstore.list_snapshots(KEY)
check('it saved one snapshot, readable only by the user',
      len(files) == 1
      and stat.S_IMODE(os.stat(files[0][0]).st_mode) == 0o600
      and stat.S_IMODE(os.stat(os.path.dirname(files[0][0])).st_mode)
      == 0o700)
check('the snapshot carries the period and the stop reason',
      files[0][1]['period'] == '24h' and files[0][1]['stopped'] == 'complete'
      and files[0][1]['count'] == len(in_range))
check('the list holds one row per visible post',
      posts_listed(page) == len(page._computed.visible)
      and page.store.get_n_items() == len(page._computed.visible) + 1)
check('the title follows the period',
      page.title_label.get_label() == 'Highlights of the last 24 hours')
check('the posts bar has a dot per post',
      page.bar.bins is None
      and len(page.bar.dots) == len(page._computed.snapshot['posts']))
check('author chips list the authors, most active first',
      sum(1 for c in page.chips if c.get_visible()) == 6
      and page.chips[0].account_id == page._view.category_authors[0])
check('the category counts are on the toggles',
      page.category_group.get_toggle(0).get_label()
      == 'All (%d)' % len(page._computed.visible))
check('the Highlights slot got the digest once',
      len(StubHighlights.instances) == 1
      and len(StubHighlights.instances[0].digests) == 1)
digest = StubHighlights.instances[0].digests[0]
check('the digest is ranked, filtered and shares the list\'s items',
      isinstance(digest, catchup.Digest)
      and all(not catchupdigest.is_filtered_post(p) for p in digest.posts)
      and digest.items[digest.posts[0]['id']]
      is page._items[digest.posts[0]['id']]
      and digest.end_at == page._computed.end_at
      and digest.scores[digest.posts[0]['id']] is not None)
check('the digest carries the trends and the boosters',
      digest.trending_keys and digest.news and digest.tags
      and digest.boosters)

total = posts_listed(page)
page.category_group.set_active_name('boosts')
check('a category filters the list',
      0 < posts_listed(page) == page._computed.counts['boosts'])
check('the filter only touched the list models',
      len(StubHighlights.instances[0].digests) == 1
      and StubHighlights.instances[0].digests[0] is digest)
check('the posts bar highlights the matching posts',
      page.bar.matching is not None
      and len(page.bar.matching) >= posts_listed(page))
page.category_group.set_active_name('all')
check('All brings every post back', posts_listed(page) == total)

author = page._author_ids[1]
page.author_dropdown.set_selected(1)
check('an author filters the list',
      0 < posts_listed(page) < total and page._view_state.author == author)
check('the chip of the author is down',
      [c for c in page.chips if c.get_active()][0].account_id == author)
page._on_reset(None)
check('Reset filters restores everything',
      posts_listed(page) == total and page._view_state.is_default()
      and page.bar.matching is None)

ids = listed_ids(page)
check('by default the list is oldest first',
      ids == sorted(ids))
page.sort_dropdown.set_selected(catchupdigest.SORTS.index('likes'))
likes = [page._items[i].favourite_count for i in listed_ids(page)]
check('sorting by likes puts the most liked first',
      likes == sorted(likes, reverse=True)
      and page._view_state.order == 'desc')
page.order_group.set_active_name('asc')
likes = [page._items[i].favourite_count for i in listed_ids(page)]
check('the order can be flipped', likes == sorted(likes))
page.sort_dropdown.set_selected(catchupdigest.SORTS.index('relevance'))
scores = [page._computed.info[i].values[5] for i in listed_ids(page)]
check('relevance sorts by the score',
      scores == sorted(scores, reverse=True))
page._on_reset(None)

page.group_switch.set_active(True)
authors_listed = [page._computed.info[i].author for i in listed_ids(page)]
runs = 1 + sum(1 for a, b in zip(authors_listed, authors_listed[1:],
                                  strict=False) if a != b)
check('grouping puts each author\'s posts together',
      runs == len(set(authors_listed)))
check('the groups are ordered by post count',
      authors_listed[0] == page._view.category_authors[0])
check('the list has section headers while grouped',
      page.sorted.get_section_sorter() is page.section_sorter)
page.group_switch.set_active(False)
check('ungrouping drops them', page.sorted.get_section_sorter() is None)
check('the Highlights digest never changed',
      len(StubHighlights.instances[0].digests) == 1)

page._toast_id = 0
page.category_group.set_active_name('replies')
page.sort_dropdown.set_selected(catchupdigest.SORTS.index('boosts'))
settle(lambda: page._toast_text != '')
check('the filter change is announced from its parts',
      page._toast_text.startswith('Showing replies, most boosts first'))
page._on_reset(None)
settle(lambda: False, timeout=0.6)

# Keys
page.category_group.set_active_name('all')
page.list_view.scroll_to(1, Gtk.ListScrollFlags.NONE, None)
check('j and k need a post in focus to move from, and stop at the ends',
      page._key_previous_post() in (True, False))

# Reopening: a new page opens the newest snapshot straight away
page2, host2 = make_page(FakeClient())
settle(lambda: page2.state != 'loading')
check('later visits open the newest snapshot',
      page2.state == 'results'
      and page2._snapshot_id == files[0][1]['id']
      and posts_listed(page2) == total)
check('and give the same ranking',
      [p['id'] for p in page2._computed.ranked]
      == [p['id'] for p in page._computed.ranked])
check('the menu now offers the last catch-up',
      'since' in page2._period_choices())
page2.cancel()

# Choosing the period already shown does nothing; another one runs
client = page.client
before = len(client.calls)
page._period_action.change_state(GLib.Variant('s', '24h'))
check('the period already shown does nothing',
      page.state == 'results' and len(client.calls) == before)
page._period_action.change_state(GLib.Variant('s', 'since'))
check('choosing another period runs a new catch-up',
      page.state == 'scanning')
settle(lambda: page.state == 'results')
check('"since the last catch-up" ends at the last snapshot',
      page._period == 'since'
      and page.title_label.get_label()
      == 'Highlights since your last catch-up')
check('it is a second snapshot',
      len(catchupstore.list_snapshots(KEY)) == 2)
check('and the digest was rebuilt for it',
      len(StubHighlights.instances[0].digests) == 2)
check('few posts raise the low-activity banner',
      page.low_banner.get_revealed()
      and 'Only' in page.low_banner.get_title()
      and page.low_banner.get_button_label() == 'Catch up on 24 hours')

# Retention
for hours in ('1h', '3h', '6h'):
    page._period_action.change_state(GLib.Variant('s', hours))
    settle(lambda hours=hours: page.state == 'results'
           and page._period == hours)
    settle(lambda: len(page._metas) <= 3 and page.state == 'results')
check('only three snapshots are kept per account',
      len(catchupstore.list_snapshots(KEY)) == 3 and len(page._metas) == 3)
check('the previous catch-ups are in the menu',
      page._metas[0]['id'] == page._snapshot_id)

# Showing an older one, removing one
older = page._metas[1]['id']
page._on_show_snapshot(None, GLib.Variant('s', older))
settle(lambda: page.state == 'results' and page._snapshot_id == older)
check('a previous catch-up can be shown', page._snapshot_id == older)
page._remove_snapshot(older)
settle(lambda: page.state == 'results' and page._snapshot_id != older)
check('removing the shown one opens the newest left',
      len(catchupstore.list_snapshots(KEY)) == 2
      and page._snapshot_id == page._metas[0]['id'])

# Cancel in the middle of a run
client = FakeClient(page_size=10)
page3, host3 = make_page(client, dict(ACCOUNT, id='55'))
settle(lambda: page3.state == 'first-run')
client.on_page = lambda number: (
    page3._on_cancel_clicked(None) if number == 2 else None)
page3.catch_up_button.emit('clicked')
settle(lambda: page3.state == 'results')
snap = catchupstore.list_snapshots('https://test.example#55')
check('Cancel shows and saves what was gathered',
      page3.state == 'results' and snap
      and snap[0][1]['stopped'] == 'cancelled' and snap[0][1]['count'] == 20)
check('and says how far back it got',
      page3.note_label.get_visible()
      and page3.note_label.get_label().startswith('Covers the last')
      and '(20 posts)' in page3.note_label.get_label())
page3.cancel()

# 429 in the middle
client = FakeClient(page_size=10)
client.fail_page = (3, 429)
page4, host4 = make_page(client, dict(ACCOUNT, id='56'))
settle(lambda: page4.state == 'first-run')
page4.catch_up_button.emit('clicked')
settle(lambda: page4.state == 'results')
snap = catchupstore.list_snapshots('https://test.example#56')
check('a 429 keeps the posts and saves them as rate-limited',
      snap and snap[0][1]['stopped'] == 'rate-limited'
      and snap[0][1]['count'] == 20)
check('and shows a banner with the wait',
      page4.limit_banner.get_revealed()
      and '2 minutes' in page4.limit_banner.get_title())
page4.cancel()

# Nothing gathered: an error page, nothing saved
client = FakeClient()
client.fail_page = (1, 500)
page5, host5 = make_page(client, dict(ACCOUNT, id='57'))
settle(lambda: page5.state == 'first-run')
page5.catch_up_button.emit('clicked')
settle(lambda: page5.state == 'error')
check('a failure before any post shows the error page and saves nothing',
      page5.state == 'error'
      and not catchupstore.list_snapshots('https://test.example#57'))
page5.cancel()

# A page closed mid-run stops asking and saves nothing
client = FakeClient(page_size=10)
page6, host6 = make_page(client, dict(ACCOUNT, id='58'))
settle(lambda: page6.state == 'first-run')
client.on_page = lambda number: page6.cancel() if number == 2 else None
page6.catch_up_button.emit('clicked')
settle(lambda: False, timeout=0.5)
check('a cancelled page asks for nothing more and saves nothing',
      client.paths().count('/timelines/home') == 2
      and '/followed_tags' not in client.paths()
      and not catchupstore.list_snapshots('https://test.example#58'))

# Without catchupgrid the slot stays empty
sys.modules['src.catchupgrid'] = None
page7, host7 = make_page(FakeClient(), dict(ACCOUNT, id='59'))
settle(lambda: page7.state == 'first-run')
page7.catch_up_button.emit('clicked')
settle(lambda: page7.state == 'results')
check('a missing Highlights module leaves the slot empty',
      page7.state == 'results' and page7._highlights is None
      and page7.highlights_slot.get_first_child() is None)
page7.cancel()

# --------------------------------------------------------------------------
# Account removal

class MemorySettings:
    def __init__(self, entries):
        self.values = {'accounts': list(entries), 'active-account': ''}

    def get_strv(self, key):
        return list(self.values[key])

    def set_strv(self, key, value):
        self.values[key] = list(value)

    def get_string(self, key):
        return self.values[key]

    def set_string(self, key, value):
        self.values[key] = value


gone = {'id': '56', 'instance': 'https://test.example'}
kept_account = {'id': '55', 'instance': 'https://test.example'}
settings = MemorySettings([json.dumps(gone), json.dumps(kept_account)])
check('before removal the account has snapshots',
      catchupstore.list_snapshots('https://test.example#56'))
accounts.forget_account(settings, '56', 'https://test.example')
check('removing an account deletes its snapshots',
      not os.path.exists(catchupstore.account_dir('https://test.example#56')))
check('and only its own',
      catchupstore.list_snapshots('https://test.example#55'))

# --------------------------------------------------------------------------
# The real Highlights grid under the page

sys.modules.pop('src.catchupgrid', None)      # the page imports it lazily
from src import catchupgrid  # noqa: E402

page8, host8 = make_page(FakeClient(), dict(ACCOUNT, id='60'))
window8 = Gtk.Window(default_width=700, default_height=900, child=host8)
window8.present()
settle(lambda: page8.state == 'first-run')
page8.catch_up_button.emit('clicked')
settle(lambda: page8.state == 'results')
view8 = page8._highlights
check('the Highlights slot holds a real HighlightsView',
      isinstance(view8, catchupgrid.HighlightsView))
grid8 = view8.grid
settle(lambda: any(p.kind == 'post' for p in grid8.placed)
       and len(grid8.tiles) > 0)
tiles = list(grid8.tiles.values())
children = []
child = grid8.get_first_child()
while child is not None:
    children.append(child)
    child = child.get_next_sibling()
check('the grid has a placed post tile',
      any(p.kind == 'post' for p in grid8.placed) and any(
          isinstance(t, catchupgrid.PostTile) for t in tiles))
check('the tiles are children of the grid',
      tiles and all(t.get_parent() is grid8 for t in tiles))
check('no tile is the same widget twice',
      len({id(w) for w in children}) == len(children) == len(tiles))

page8.cancel()
refs = [page8.weak_ref(), host8.weak_ref(), view8.weak_ref(),
        grid8.weak_ref()] + [t.weak_ref() for t in tiles]
window8.set_child(None)
window8.destroy()
settle(lambda: False, timeout=0.5)
tiles = children = child = view8 = grid8 = page8 = host8 = None
window8 = None
gc.collect()
check('a dropped page frees its Highlights view, grid and tiles',
      all(ref() is None for ref in refs))

shutil.rmtree(CACHE, ignore_errors=True)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all catch-up checks passed')
