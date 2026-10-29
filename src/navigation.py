# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy construction and pinned-entry routing for window-owned pages."""

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
    """Return ordered ``(tag, title, icon)`` routes for pinned lists/tags."""
    lists = pinned_entries(settings, 'favorite-lists-ids', account_id)
    tags = pinned_entries(settings, 'favorite-tags-ids', account_id)
    return ([(pinned_tag('list', value), _('List'), 'view-list-symbolic')
             for value in lists]
            + [(pinned_tag('tag', value), '#' + value,
                'babble-hashtag-symbolic') for value in tags])


def pinned_tag(kind, value):
    if kind not in ('list', 'tag'):
        raise ValueError('unsupported pinned place kind: %s' % kind)
    return '%s:%s' % (kind, value)


class Navigation:
    """Per-window page cache and lazy factory for secondary places."""

    def __init__(self, window):
        self.window = window
        self.pages = {}
        self.children = {}
        self.pinned_lists = {}

    def clear(self):
        self.pages.clear()
        self.children.clear()
        self.pinned_lists.clear()

    def pinned_entries(self, settings, key, account_id):
        return pinned_entries(settings, key, account_id)

    def pinned_routes(self, settings, account_id):
        routes = pinned_routes(settings, account_id)
        return [(tag, self.place_title(tag), icon)
                for tag, _title, icon in routes]

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
        window = self.window
        client = window.client
        if tag == 'conversations':
            from .conversations import ConversationsPage
            return ConversationsPage(client)
        if tag == 'catch-up':
            from .catchup import CatchUpPage
            return CatchUpPage(client, account=window.account)
        if tag == 'search':
            from .search import SearchPage
            return SearchPage(client)
        if tag == 'favourites':
            return window._timeline_place(
                '/favourites', None, 'starred-symbolic',
                _('No Favorites Yet'), _('Posts you favorite will appear here'))
        if tag == 'bookmarks':
            return window._timeline_place(
                '/bookmarks', None, 'babble-bookmarks-symbolic',
                _('No Bookmarks Yet'), _('Posts you bookmark will appear here'))
        if tag == 'hashtags':
            from .hashtags import FollowedHashtagsPage
            return FollowedHashtagsPage(client)
        if tag == 'explore':
            from .explore import ExplorePage
            return ExplorePage(client)
        if tag in ('local', 'federated'):
            if tag in window._restricted_public_places():
                return None
            params = {'local': 'true'} if tag == 'local' else None
            page = window._timeline_place(
                '/timelines/public', params, 'network-server-symbolic',
                _('No Posts Yet'), _('New posts will appear here'))
            window._public_place_built(tag, page)
            return page
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
            sidebar_control=self.window.make_sidebar_control)
        self.pages[tag] = page
        self.children[tag] = child
        if tag == 'conversations':
            self.window._sync_streaming()
        return page
