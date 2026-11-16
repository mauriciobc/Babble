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
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import tempfile

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.catchup import CatchUpPage, pipeline as catchup  # noqa: E402
from src.catchup import digest as catchupdigest  # noqa: E402
from src.catchup import store as catchupstore  # noqa: E402

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


class StubHighlights(Gtk.Box):
    """Stands in for catchup.tiles.HighlightsView."""

    instances = []

    def __init__(self, client):
        super().__init__()
        self.digests = []
        self.pages = []
        self.on_pages_changed = None
        StubHighlights.instances.append(self)

    def set_digest(self, digest, pages=None):
        self.digests.append(digest)
        self.pages.append(pages)


class StubModule:
    HighlightsView = StubHighlights


_catchup_tiles_saved = sys.modules.get('src.catchup.tiles')
# The page reaches the Highlights grid through this module, so the stub
# stands in for it until a part pops it for the real one.
sys.modules['src.catchup.tiles'] = StubModule


def make_page(client, key_account=ACCOUNT):
    page = CatchUpPage(client, account=key_account)
    host = Gtk.Box()
    host.append(page)
    return page, host


def posts_listed(page):
    return page.sorted.get_n_items() - 1       # the block above the posts


def listed_ids(page):
    return [page.sorted.get_item(i).raw['id']
            for i in range(1, page.sorted.get_n_items())]


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


def in_range(client, hours=24):
    """The fixture posts a run covering *hours* would keep."""
    return [p for p in client.posts
            if catchupdigest.in_range(p, NOW - datetime.timedelta(hours=hours))]

