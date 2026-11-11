# SPDX-License-Identifier: GPL-3.0-or-later
"""The stream router: which Streams the open places want, and who
receives each event.

Two pieces of one piece of knowledge, kept apart from the window that
owns the socket. :meth:`StreamRouter.wanted_streams` answers which
streams the built places need; :meth:`StreamRouter.route` hands an event
to the pages that own it. The window keeps the lifecycle (starting and
stopping the socket, the polling fallback, the reconnect catch-up) and
passes each event in.

The router reaches pages only through a :class:`StreamHost` of callables,
the way ``navigation.Navigation`` reaches the window through its
``PlaceHost``. It looks a page up at every event and holds no page, no
window and no client, so a page that is closed or replaced is never kept
alive by it. It imports nothing but the stream constants and helpers of
``streaming``; the key format of a place (``list:<id>``, ``tag:<name>``)
stays in ``navigation``, which hands over :class:`~.navigation.StreamPlace`
records.

``TimelinePage`` is not imported: the host's ``is_timeline`` says which
objects are timeline pages, so the router stays free of widget code and
can be driven with plain recorders.

The events it routes are declared as a table, ``ROUTES``, at the end of
the module: event name to the handler itself (``StreamRouter._update``),
or None for an event that is decoded but deliberately needs no work, with
the reason beside it. The table holds references, not names, so a
misspelt handler fails when the module is imported. ``routed_events()``
lists the table, and ``test-stream-events`` holds it against the
documented set.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .streaming import (DIRECT, FEDERATED, LOCAL, hashtag_stream,
                        list_stream)


@dataclass
class StreamHost:
    """What the router needs from the window, as callables.

    Every callable is asked at the moment it is needed, never cached.
    ``home_page`` and ``notifications_page`` may return None (not built
    yet); ``place(tag)`` returns the built child of a place, or None.
    """
    home_page: Callable[[], Any]
    notifications_page: Callable[[], Any]
    public_pages: Callable[[], dict]
    place: Callable[[str], Any]
    stream_places: Callable[[], list]
    place_timelines: Callable[[], list]
    is_timeline: Callable[[Any], bool]
    live_public: Callable[[], bool]
    refresh_admin_tool: Callable[[str], None]
    notifier_handle: Callable[[Any], None]
    note_background_notification: Callable[[Any], None]


class StreamRouter:
    """Maps the built places to streams, and an event to its pages.

    ``ROUTES`` (declared after the class, which it refers to) is the table
    of events it routes."""

    ROUTES: dict = {}

    def __init__(self, host: StreamHost):
        self.host = host

    # Which streams the open places want

    def wanted_streams(self):
        """The streams to subscribe to now, as a set.

        The conversations place needs ``direct`` (direct messages only
        arrive there); local and federated need their public stream, and
        only while ``public-live-updates`` is on; each list and hashtag
        place adds its own (see :meth:`place_streams`)."""
        host = self.host
        wanted = set()
        if host.place('conversations') is not None:
            wanted.add(DIRECT)
        if host.live_public():
            public = host.public_pages()
            if 'local' in public:
                wanted.add(LOCAL)
            if 'federated' in public:
                wanted.add(FEDERATED)
        wanted.update(self.place_streams())
        return wanted

    def place_streams(self):
        """``{stream: [timeline page]}`` for the built list and hashtag
        places, so they update live like the home timeline.

        A list stream is the reader's own, so only ``live-updates`` gates
        it (the window checks that); a hashtag stream carries public
        posts, so it follows ``public-live-updates``. A page over several
        tags is left to refresh: its ``all``/``none`` filters are the
        server's, and a stream of one tag cannot apply them."""
        host = self.host
        public = host.live_public()
        found = {}
        for place in host.stream_places():
            page = place.page
            if not host.is_timeline(page):
                continue
            if place.kind == 'list':
                stream = list_stream(place.key)
            elif place.kind == 'tag' and public and not place.multi:
                stream = hashtag_stream(place.key)
            else:
                continue
            found.setdefault(stream, []).append(page)
        return found

    def timeline_pages(self):
        """Every timeline page there is: home, the public ones, and each
        built place's own timeline."""
        host = self.host
        found = [host.home_page(), *host.public_pages().values(),
                 *host.place_timelines()]
        return [page for page in found if host.is_timeline(page)]

    # Who receives each event

    def routed_events(self):
        """The names of the events this router declares (handled or
        deliberately ignored)."""
        return frozenset(self.ROUTES)

    def route(self, stream, name, payload):
        """Hand one event to the pages that own it. An event not in the
        table is ignored."""
        handler = self.ROUTES.get(name)
        if handler is not None:
            handler(self, stream, payload)

    def _update(self, stream, payload):
        if stream[:1] in (('list',), ('hashtag',)):
            for page in self.place_streams().get(stream, ()):
                page.on_stream_update(payload)
            return
        if stream == LOCAL:
            target = self.host.public_pages().get('local')
        elif stream == FEDERATED:
            target = self.host.public_pages().get('federated')
        else:
            target = self.host.home_page()
        if target is not None:
            target.on_stream_update(payload)

    def _delete(self, _stream, payload):
        for page in self.timeline_pages():
            page.on_stream_delete(payload)
        conversations = self.host.place('conversations')
        if conversations is not None:
            conversations.on_stream_delete(payload)

    def _status_update(self, _stream, payload):
        for page in self.timeline_pages():
            page.on_stream_status_update(payload)

    def _filters_changed(self, _stream, _payload):
        home = self.host.home_page()
        if home is not None:
            home.refresh()
        notifications = self.host.notifications_page()
        if notifications is not None:
            notifications.refresh()

    def _notifications_refresh(self, _stream, _payload):
        notifications = self.host.notifications_page()
        if notifications is not None:
            notifications.refresh()

    def _announcements(self, _stream, _payload):
        notifications = self.host.notifications_page()
        if notifications is not None:
            notifications.load_announcements()

    def _report(self, _stream, _payload):
        self.host.refresh_admin_tool('reports')

    def _notification(self, _stream, payload):
        notifications = self.host.notifications_page()
        if notifications is not None:
            notifications.on_stream_notification(payload)
        self.host.notifier_handle(payload)
        self.host.note_background_notification(payload)

    def _conversation(self, _stream, payload):
        conversations = self.host.place('conversations')
        if conversations is not None:
            conversations.on_stream_conversation(payload)

    def _encrypted_message(self, _stream, _payload):
        conversations = self.host.place('conversations')
        if conversations is not None:
            conversations.refresh()


# Every event the Streams API can send, mapped to the handler that routes
# it. None marks an event that is decoded but deliberately needs no work;
# the reason is kept beside it.
StreamRouter.ROUTES = ROUTES = {
    'update': StreamRouter._update,
    'delete': StreamRouter._delete,
    'status.update': StreamRouter._status_update,
    # Filters were changed (here or in another client). Their effect is
    # baked into each status's `filtered` list by the server, so only a
    # refetch shows it.
    'filters_changed': StreamRouter._filters_changed,
    # notifications_merged: accepted notification requests finished
    # merging into the stream, so the loaded list is stale.
    #
    # follow_request: a new request. It arrives on the user stream and
    # becomes a notification only once the server has written it, so the
    # list is refetched rather than patched -- the same reason
    # notifications_merged reloads.
    'notifications_merged': StreamRouter._notifications_refresh,
    'follow_request': StreamRouter._notifications_refresh,
    # A new announcement, or one withdrawn. The banner counts unread
    # ones, so it has to be asked again.
    'announcement': StreamRouter._announcements,
    'announcement.delete': StreamRouter._announcements,
    # Someone else's reaction on an announcement. Babble shows only your
    # own reactions, which are applied optimistically, so there is
    # nothing here to redraw.
    'announcement.reaction': None,
    # A new report, for a moderator with the dashboard open.
    'report': StreamRouter._report,
    # Collection membership (Mastodon 4.6+). The payload is the bare
    # string "collection": membership changes a Collection page's member
    # list, which it refetches when reopened.
    'add': None,
    'remove': None,
    'notification': StreamRouter._notification,
    'conversation': StreamRouter._conversation,
    # An end-to-end encrypted direct message. Babble reads conversations
    # over /conversations and holds no keys for them, so there is nothing
    # to open -- but the event says a message arrived, and the list
    # refetches to show it.
    'encrypted_message': StreamRouter._encrypted_message,
}
