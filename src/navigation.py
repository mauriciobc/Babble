# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy construction and pinned-entry routing for window-owned pages."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .i18n import gettext as _
from . import pagenav


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


@dataclass(frozen=True)
class PlacePolicy:
    """What happens after a place is built, and how outage catch-up
    treats it. Product choices are rows here, not ``startswith`` forks.

    ``streams``: subscribe via the stream router after build.
    ``auto_refresh``: also run the window refresh timer (implies stream
    sync through ``notify_place_built`` today).
    ``catch_up``: ``'refresh'`` if loaded, ``'catch_up'`` for the
    notifications page API, or ``None``.
    """
    streams: bool = False
    auto_refresh: bool = False
    catch_up: str | None = None


def place_policy(tag):
    """Policy for *tag*. Home is listed for documentation and outage
    membership; Navigation does not build it (template-mounted)."""
    if tag == 'home':
        # User stream is session-level; home still auto-refreshes.
        return PlacePolicy(streams=False, auto_refresh=True,
                           catch_up='refresh')
    if tag in PUBLIC_TAGS:
        return PlacePolicy(streams=True, auto_refresh=True,
                           catch_up='refresh')
    if tag == 'conversations' or tag.startswith(('list:', 'tag:')):
        return PlacePolicy(streams=True, auto_refresh=False,
                           catch_up='refresh')
    if tag in ('favourites', 'bookmarks'):
        # No place stream; still refresh after an outage if loaded.
        return PlacePolicy(streams=False, auto_refresh=False,
                           catch_up='refresh')
    if tag == 'notifications':
        return PlacePolicy(streams=False, auto_refresh=False,
                           catch_up='catch_up')
    return PlacePolicy()


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


@dataclass
class StreamPlace:
    """A built list or hashtag place, as the stream router sees it.

    ``kind`` is ``'list'`` or ``'tag'``, ``key`` the list id or the tag
    name (the part of the place's tag after the prefix), ``page`` the
    place's timeline (None when the place has none) and ``multi`` whether
    it is a hashtag page over several tags."""
    kind: str
    key: str
    page: Any
    multi: bool


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

    def stream_places(self):
        """The built list and hashtag places, in build order, as
        :class:`StreamPlace` records. The ``list:``/``tag:`` key format
        is read here and nowhere else."""
        found = []
        for tag, child in self.children.items():
            for prefix, kind in (('list:', 'list'), ('tag:', 'tag')):
                if tag.startswith(prefix):
                    found.append(StreamPlace(
                        kind, tag[len(prefix):],
                        getattr(child, 'timeline', None),
                        bool(getattr(child, 'multi', False))))
                    break
        return found

    def place_timelines(self):
        """Each built place's timeline: its ``timeline`` attribute when it
        has one (list and hashtag pages wrap a timeline), else the child
        itself. The caller decides which of them are timeline pages."""
        return [getattr(child, 'timeline', child)
                for child in self.children.values()]

    def forget(self, tag):
        """Drop one place: its page, its child and its stream."""
        self.forget_many([tag])

    def forget_many(self, tags, sync=True):
        """Drop several places at once, and optionally let the stream
        know once.

        A place that is gone must not stay subscribed: the socket would
        keep receiving events for a timeline nobody shows, and a public
        feed the server closed would keep its subscription until
        something else happened to sync the stream. Each child stops the
        work it has in flight first (a catch-up, its auto-refresh timer)
        and closes its request scope at once, so a page that stays alive
        (held by a reference cycle until the next collection) cannot be
        answered after it was dropped.

        *sync* is True for the sidebar path. Session teardown passes
        False: it has already ``streaming.stop()`` and must not restart
        the old client through ``sync_streaming``.

        Does nothing when none of *tags* is built.
        """
        dropped = False
        for tag in tags:
            child = self.children.pop(tag, None)
            if self.pages.pop(tag, None) is not None or child is not None:
                dropped = True
            self._stop_work(child)
        if sync and dropped:
            self.host.sync_streaming()

    @staticmethod
    def _stop_work(child):
        """Stop what a dropped place has in flight, the way an account
        switch does."""
        if child is None:
            return
        # Prefer close_requests() (Catch-Up maps it to cancel(); search
        # and paged lists close their scopes). Fall back to cancel() for
        # older pages that only have that name.
        close = getattr(child, 'close_requests', None)
        if close is not None:
            close()
        else:
            cancel = getattr(child, 'cancel', None)
            if cancel is not None:
                cancel()
        timeline = getattr(child, 'timeline', child)
        if timeline is not child:
            stop = getattr(timeline, 'set_auto_refresh_interval', None)
            if stop is not None:
                stop(0)
            close = getattr(timeline, 'close_requests', None)
            if close is not None:
                close()
        else:
            stop = getattr(child, 'set_auto_refresh_interval', None)
            if stop is not None:
                stop(0)

    def clear(self):
        """Stop every built place's work and drop the maps.

        Does not sync streaming: account switch has already stopped the
        socket and must not restart it for the outgoing client.
        """
        for child in list(self.children.values()):
            self._stop_work(child)
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
                'favourites', None, 'starred-symbolic',
                _('No Favorites Yet'), _('Posts you favorite will appear here'))
        if tag == 'bookmarks':
            return self.host.timeline_place(
                'bookmarks', None, 'babble-bookmarks-symbolic',
                _('No Bookmarks Yet'), _('Posts you bookmark will appear here'))
        if tag == 'hashtags':
            from .hashtaglists import FollowedHashtagsPage
            return FollowedHashtagsPage(client)
        if tag == 'explore':
            from .explore import ExplorePage
            return ExplorePage(client)
        if tag in ('local', 'federated'):
            if tag in self.host.restricted_public_places():
                return None
            params = {'local': 'true'} if tag == 'local' else None
            return self.host.timeline_place(
                'public', params, 'network-server-symbolic',
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
        page = pagenav.make_nav_page(
            self.place_title(tag), child, tag='place-%s' % tag,
            sidebar_control=self.host.make_sidebar_control)
        self.pages[tag] = page
        self.children[tag] = child
        policy = place_policy(tag)
        if policy.auto_refresh:
            # After ``children`` holds it: the host reads ``public_pages``
            # to start the stream and the refresh timer.
            self.host.notify_place_built(tag, child)
        elif policy.streams:
            self.host.sync_streaming()
        return page
