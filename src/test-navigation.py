# SPDX-License-Identifier: GPL-3.0-or-later

"""Lazy place routing, account-scoped pins, and cached navigation pages."""

import os
import sys
import types
from unittest.mock import patch

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import navigation  # noqa: E402


failures = []


def check(label, condition):
    if condition:
        print('ok - %s' % label)
    else:
        failures.append(label)
        print('FAIL - %s' % label)


class Settings:
    def get_strv(self, key):
        return {
            'favorite-lists-ids': ['a/one', 'b/two', 'a/three'],
            'favorite-tags-ids': ['b/other', 'a/hello world'],
        }[key]


class FakePage:
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.args = args
        self.kwargs = kwargs


class FakeClient:
    instance = 'https://example.test'

    def request_async(self, *args, **kwargs):
        raise AssertionError('route construction must not fetch data')


class Host:
    def __init__(self):
        self._client = FakeClient()
        self._account = {'id': 'user'}
        self.calls = []
        self.restricted = set()

    def client(self):
        return self._client

    def account(self):
        return self._account

    def make_sidebar_control(self, include_bell=True):
        return None

    def restricted_public_places(self):
        return self.restricted

    def timeline_place(self, path, params, icon, title, description):
        self.calls.append(('timeline', (path, params, icon, title, description)))
        return FakePage('timeline', path, params, icon, title, description)

    def notify_place_built(self, tag, page):
        self.calls.append(('public', tag, page))

    def sync_streaming(self):
        self.calls.append(('streaming',))


def fake_class(module):
    class Page(FakePage):
        def __init__(self, *args, **kwargs):
            super().__init__(module, *args, **kwargs)
    return Page


modules = {
    'src.conversations': ('ConversationsPage',),
    'src.catchup': ('CatchUpPage',),
    'src.search': ('SearchPage',),
    'src.hashtags': ('FollowedHashtagsPage', 'HashtagPage'),
    'src.explore': ('ExplorePage',),
    'src.lists': ('ListsPage', 'ListTimelinePage'),
}
host = Host()
routing = navigation.Navigation(host)

with patch.dict(sys.modules, {
    name: types.ModuleType(name) for name in modules
}):
    for module, symbols in modules.items():
        for symbol in symbols:
            setattr(sys.modules[module], symbol, fake_class(module))

    routing.set_pinned_lists([{'id': '42', 'title': 'List'}])
    expectations = {
        'conversations': ('src.conversations', (host.client(),), {}),
        'catch-up': ('src.catchup', (host.client(),),
                     {'account': host.account()}),
        'search': ('src.search', (host.client(),), {}),
        'hashtags': ('src.hashtags', (host.client(),), {}),
        'explore': ('src.explore', (host.client(),), {}),
        'lists': ('src.lists', (host.client(),), {}),
        'list:42': ('src.lists',
                    (host.client(), {'id': '42', 'title': 'List'}), {}),
        'tag:cats': ('src.hashtags', (host.client(), ['cats']), {}),
    }
    for tag, (module, args, kwargs) in expectations.items():
        child = routing.build_place(tag)
        check('%s dispatches to its lazy page factory' % tag,
              child.name == module and child.args == args
              and child.kwargs == kwargs)

    for tag, path, params in (
            ('favourites', '/favourites', None),
            ('bookmarks', '/bookmarks', None),
            ('local', '/timelines/public', {'local': 'true'}),
            ('federated', '/timelines/public', None)):
        before = len(host.calls)
        child = routing.build_place(tag)
        timeline_call = host.calls[before]
        check('%s uses the expected timeline endpoint' % tag,
              child.name == 'timeline'
              and timeline_call[1][0] == path
              and timeline_call[1][1] == params)
    check('building a public route alone does not notify the host',
          not [call for call in host.calls if call[0] == 'public'])
    host.restricted = {'local', 'federated'}
    before = len(host.calls)
    check('restricted public feeds are not constructed',
          routing.build_place('local') is None
          and routing.build_place('federated') is None
          and len(host.calls) == before)
    check('unknown routes return no page',
          routing.build_place('unknown') is None)

    settings = Settings()
    routing.set_pinned_lists([
        {'id': 'one', 'title': 'First list'},
        {'id': 'three', 'title': 'Third'}])
    check('pinned entries are scoped to the active account',
          navigation.pinned_entries(settings, 'favorite-lists-ids', 'a')
          == ['one', 'three'])
    check('pinned routes preserve order, titles, and account scope',
          routing.pinned_routes(settings, 'a') == [
              ('list:one', 'First list', 'view-list-symbolic'),
              ('list:three', 'Third', 'view-list-symbolic'),
              ('tag:hello world', '#hello world',
               'babble-hashtag-symbolic')])
    with patch.object(navigation.pages, 'make_nav_page',
                      side_effect=lambda title, child, tag=None,
                      sidebar_control=None: FakePage(title, child, tag)) \
            as make:
        first = routing.place_page('tag:hello world')
        second = routing.place_page('tag:hello world')
        check('a route reuses its page and child',
              first is second and make.call_count == 1
              and routing.children['tag:hello world'] is first.args[0])
        check('hashtag routes expose readable title and stable page tag',
              first.name == '#hello world'
              and first.args[1] == 'place-tag:hello world')
        routing.forget('tag:hello world')
        routing.forget('nope')
        check('forget drops one route from pages and children and ignores '
              'an unknown tag',
              'tag:hello world' not in routing.pages
              and 'tag:hello world' not in routing.children)
        routing.clear()
        check('clearing navigation drops pages, children, and pinned titles',
              not routing.pages and not routing.children
              and not routing.pinned_lists)

    # Public places: a view of children, announced to the host once, after
    # the page is cached (the host reads public_pages when it hears).
    host.restricted = set()
    host.calls.clear()
    seen = []
    host.notify_place_built = lambda tag, page: seen.append(
        (tag, tag in routing.public_pages, routing.children.get(tag)))
    with patch.object(navigation.pages, 'make_nav_page',
                      side_effect=lambda title, child, tag=None,
                      sidebar_control=None: FakePage(title, child, tag)):
        local_nav = routing.place_page('local')
        routing.place_page('local')
        federated_nav = routing.place_page('federated')
        routing.place_page('favourites')
    check('local and federated are public pages, and only those',
          set(routing.public_pages) == {'local', 'federated'}
          and routing.public_pages['local'] is routing.children['local']
          and routing.public_pages['federated'] is routing.children['federated']
          and 'favourites' in routing.children)
    check('the host hears of each public place once, with it already cached',
          [(tag, present) for tag, present, _child in seen]
          == [('local', True), ('federated', True)]
          and all(child is not None for _tag, _present, child in seen)
          and local_nav is not federated_nav)
    routing.forget('local')
    check('forget drops a public page with the rest of the route',
          'local' not in routing.public_pages
          and 'federated' in routing.public_pages)
    routing.clear()
    check('clear drops every public page',
          not routing.public_pages)

if failures:
    raise SystemExit('%d navigation checks failed' % len(failures))
print('all navigation checks passed')
