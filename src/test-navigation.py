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


class FakeWindow:
    def __init__(self):
        self.client = FakeClient()
        self.account = {'id': 'user'}
        self.calls = []
        self.restricted = set()

    def make_sidebar_control(self):
        return None

    def _restricted_public_places(self):
        return self.restricted

    def _timeline_place(self, *args):
        self.calls.append(('timeline', args))
        return FakePage('timeline', *args)

    def _public_place_built(self, tag, page):
        self.calls.append(('public', tag, page))

    def _sync_streaming(self):
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
window = FakeWindow()
routing = navigation.Navigation(window)

with patch.dict(sys.modules, {
    name: types.ModuleType(name) for name in modules
}):
    for module, symbols in modules.items():
        for symbol in symbols:
            setattr(sys.modules[module], symbol, fake_class(module))

    routing.set_pinned_lists([{'id': '42', 'title': 'List'}])
    expectations = {
        'conversations': ('src.conversations', (window.client,), {}),
        'catch-up': ('src.catchup', (window.client,),
                     {'account': window.account}),
        'search': ('src.search', (window.client,), {}),
        'hashtags': ('src.hashtags', (window.client,), {}),
        'explore': ('src.explore', (window.client,), {}),
        'lists': ('src.lists', (window.client,), {}),
        'list:42': ('src.lists',
                    (window.client, {'id': '42', 'title': 'List'}), {}),
        'tag:cats': ('src.hashtags', (window.client, ['cats']), {}),
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
        before = len(window.calls)
        child = routing.build_place(tag)
        timeline_call = window.calls[before]
        check('%s uses the expected timeline endpoint' % tag,
              child.name == 'timeline'
              and timeline_call[1][0] == path
              and timeline_call[1][1] == params)
    check('public routes notify the window for stream tracking',
          [call[1] for call in window.calls if call[0] == 'public']
          == ['local', 'federated'])
    window.restricted = {'local', 'federated'}
    before = len(window.calls)
    check('restricted public feeds are not constructed',
          routing.build_place('local') is None
          and routing.build_place('federated') is None
          and len(window.calls) == before)
    check('unknown routes return no page',
          routing.build_place('unknown') is None)

    settings = Settings()
    routing.set_pinned_lists([
        {'id': 'one', 'title': 'First list'},
        {'id': 'three', 'title': 'Third'}])
    check('pinned entries are scoped to the active account',
          routing.pinned_entries(settings, 'favorite-lists-ids', 'a')
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

if failures:
    raise SystemExit('%d navigation checks failed' % len(failures))
print('all navigation checks passed')
