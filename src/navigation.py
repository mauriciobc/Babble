# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy construction and pinned-entry routing for window-owned pages."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .i18n import gettext as _
from . import pages


PLACES = (
    ('home', 'user-home-symbolic', 0),
    ('catch-up', 'babble-catch-up-symbolic', 0),
    ('notifications', 'babble-notifications-symbolic', 0),
    ('conversations', 'mail-unread-symbolic', 0),
    ('search', 'system-search-symbolic', 0),
    ('favourites', 'starred-symbolic', 0),
    ('bookmarks', 'babble-bookmarks-symbolic', 0),
    ('hashtags', 'babble-hashtag-symbolic', 0),
    ('explore', 'babble-explore-symbolic', 1),
    ('local', 'network-server-symbolic', 1),
    ('federated', 'babble-compose-globe-symbolic', 1),
    ('lists', 'view-list-symbolic', 1),
)

# Places whose pages get live updates and a refresh timer from the window.
PUBLIC_TAGS = ('local', 'federated')

HIDE_KEYS = {'catch-up': 'hide-catch-up', 'explore': 'hide-trending',
             'local': 'hide-local', 'federated': 'hide-federated'}


def place_title(tag):
    return {
        'home': _('Home'), 'catch-up': _('Catch Up'),
        'notifications': _('Notifications'),
        'conversations': _('Conversations'), 'search': _('Search'),
        'favourites': _('Favorites'), 'bookmarks': _('Bookmarks'),
        'hashtags': _('Hashtags'), 'explore': _('Explore'),
        'local': _('Local'), 'federated': _('Federated'),
        'lists': _('Lists'),
    }.get(tag, tag)


def pinned_entries(settings, key, account_id):
    """Return this account's values from account-scoped setting entries."""
    prefix = '%s/' % account_id
    return [entry[len(prefix):] for entry in settings.get_strv(key)
            if entry.startswith(prefix)]


def pinned_routes(settings, account_id):
    """Return ordered ``(tag, icon)`` routes for pinned lists/tags."""
    lists = pinned_entries(settings, 'favorite-lists-ids', account_id)
    tags = pinned_entries(settings, 'favorite-tags-ids', account_id)
    return ([(pinned_tag('list', value), 'view-list-symbolic')
             for value in lists]
            + [(pinned_tag('tag', value), 'babble-hashtag-symbolic')
               for value in tags])


def pinned_tag(kind, value):
    if kind not in ('list', 'tag'):
        raise ValueError('unsupported pinned place kind: %s' % kind)
    return '%s:%s' % (kind, value)


@dataclass
class PlaceHost:
    """What Navigation needs from the window, as callables and values.

    ``client`` and ``account`` are callables returning the current values
    (they change on account switch); the rest are callable actions.
    """
    client: Callable[[], Any]
    account: Callable[[], dict]
    make_sidebar_control: Callable[..., Any]
    timeline_place: Callable[[str, dict | None, str, str, str], Any]
    restricted_public_places: Callable[[], set]
    notify_place_built: Callable[[str, Any], None]
    sync_streaming: Callable[[], None]


class Navigation:
    """Per-window page cache and lazy factory for secondary places."""

    def __init__(self, host: PlaceHost):
        self.host = host
        self.pages = {}
        self.children = {}
        self.pinned_lists = {}

    @property
    def public_pages(self):
        """The built local and federated pages, by tag: a view of
        ``children``, so there is nothing to keep in step with it."""
        return {tag: self.children[tag] for tag in PUBLIC_TAGS
                if tag in self.children}

    def forget(self, tag):
        self.pages.pop(tag, None)
        self.children.pop(tag, None)

    def clear(self):
        self.pages.clear()
        self.children.clear()
        self.pinned_lists.clear()

    def pinned_routes(self, settings, account_id):
        routes = pinned_routes(settings, account_id)
        return [(tag, self.place_title(tag), icon)
                for tag, icon in routes]

    def place_title(self, tag):
        if tag.startswith('list:'):
            return self.pinned_lists.get(tag[5:]) or _('List')
        if tag.startswith('tag:'):
            return '#' + tag[4:]
        return place_title(tag)

    def set_pinned_lists(self, lists):
        self.pinned_lists = {str(item['id']): item.get('title', '')
                             for item in lists}

    def build_place(self, tag):
        """Construct a place's content widget, importing its page lazily."""
        client = self.host.client()
        if tag == 'conversations':
            from .conversations import ConversationsPage
            return ConversationsPage(client)
        if tag == 'catch-up':
            from .catchup import CatchUpPage
            return CatchUpPage(client, account=self.host.account())
        if tag == 'search':
            from .search import SearchPage
            return SearchPage(client)
        if tag == 'favourites':
            return self.host.timeline_place(
                '/favourites', None, 'starred-symbolic',
                _('No Favorites Yet'), _('Posts you favorite will appear here'))
        if tag == 'bookmarks':
            return self.host.timeline_place(
                '/bookmarks', None, 'babble-bookmarks-symbolic',
                _('No Bookmarks Yet'), _('Posts you bookmark will appear here'))
        if tag == 'hashtags':
            from .hashtags import FollowedHashtagsPage
            return FollowedHashtagsPage(client)
        if tag == 'explore':
            from .explore import ExplorePage
            return ExplorePage(client)
        if tag in ('local', 'federated'):
            if tag in self.host.restricted_public_places():
                return None
            params = {'local': 'true'} if tag == 'local' else None
            return self.host.timeline_place(
                '/timelines/public', params, 'network-server-symbolic',
                _('No Posts Yet'), _('New posts will appear here'))
        if tag == 'lists':
            from .lists import ListsPage
            return ListsPage(client)
        if tag.startswith('list:'):
            from .lists import ListTimelinePage
            list_id = tag[5:]
            return ListTimelinePage(client, {
                'id': list_id,
                'title': self.pinned_lists.get(list_id, _('List'))})
        if tag.startswith('tag:'):
            from .hashtags import HashtagPage
            return HashtagPage(client, [tag[4:]])
        return None

    def place_page(self, tag):
        page = self.pages.get(tag)
        if page is not None:
            return page
        child = self.build_place(tag)
        if child is None:
            return None
        page = pages.make_nav_page(
            self.place_title(tag), child, tag='place-%s' % tag,
            sidebar_control=self.host.make_sidebar_control)
        self.pages[tag] = page
        self.children[tag] = child
        if tag in PUBLIC_TAGS:
            # After ``children`` holds it: the host reads ``public_pages``
            # to start the stream and the refresh timer.
            self.host.notify_place_built(tag, child)
        if tag == 'conversations':
            self.host.sync_streaming()
        return page
