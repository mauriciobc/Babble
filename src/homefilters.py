# SPDX-License-Identifier: GPL-3.0-or-later
"""The Home timeline's filters, as a mixin for :class:`TimelinePage`.

Hiding boosts and replies from people the viewer does not follow both
need the page's own state (its model, its client, the follow table), so
they live beside it rather than in a function. Split out of
``timelinepage`` to keep that module under the size limit."""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa: E402

from .api import accounts as api_accounts  # noqa: E402
from .timelinebase import FOLLOW_CACHE_LIMIT, _own_account_id  # noqa: E402
from .timelinedata import (  # noqa: E402
    filter_boosts, filter_replies_from_non_follows)
from .weakconnect import weak_method  # noqa: E402


# /accounts/relationships takes this many ids a call.
RELATIONSHIP_BATCH = 40


def ask_relationships(client, ids, on_done, cancellable=None, wanted=None,
                      on_drop=None):
    """Ask the server about every id, RELATIONSHIP_BATCH at a time, then
    call ``on_done(relationships, ok)`` once.

    *relationships* is every entry the server returned, across batches;
    *ok* is False when a batch failed, and the batches after it are not
    sent (what was answered before it is still handed over). *cancellable*
    goes to every request. *wanted*, when given, is asked before an answer
    is decoded, as a :class:`~.requestscope.Ticket` callback does; when it
    says no, the answer is dropped, the batches after it are not sent,
    ``on_done`` is never called and ``on_drop()`` is, if given.
    """
    ids = list(dict.fromkeys(ids))
    collected = []

    def send():
        if not ids:
            on_done(collected, True)
            return
        batch = ids[:RELATIONSHIP_BATCH]
        del ids[:RELATIONSHIP_BATCH]
        api_accounts.relationships_async(
            client, batch, loaded, cancellable=cancellable)

    def loaded(client, result, _data=None):
        if wanted is not None and not wanted():
            if on_drop is not None:
                on_drop()
            return
        try:
            # The whole list. accounts.relationships_finish returns the
            # first entry only, for the profile page that asks about one
            # account; iterating that dict here raised on every reply
            # from an author not yet asked about, and the page stayed
            # on "loading" forever.
            collected.extend(
                api_accounts.relationships_all_finish(client, result) or ())
        except GLib.Error:
            on_done(collected, False)
            return
        send()

    send()


class HomeFilters:
    """Needs ``client``, ``model``, ``_hide_boosts``,
    ``_hide_replies_non_follows``, ``_following_ids``, ``_stream_scope``
    (a RequestScope), ``_stream_pending`` and ``_stream_inflight`` (lists)
    from the page it is mixed into.

    The paged path's relationship lookup has no stale-answer guard of its
    own: its requests carry the page ticket's cancellable, and when it
    answers, ``on_done`` runs the page's ``_finish_*`` method, which asks
    the ticket (a refresh re-checks its refresh ticket there, because a
    newer refresh may have started while the lookup was pending). The
    follow table is keyed by author, so recording a late relationship
    answer remains safe even when its page is discarded. The stream path's
    lookup belongs to ``_stream_scope`` and drops its answer itself."""

    def _remember_follows(self, relationships):
        """Record what the server said about a batch of authors.

        The table is shared with the reply filter: both consumers ask the
        same question ("does the viewer follow this author"), both go to
        /relationships for the answer, and a reader who has already paged
        past an author does not make the app ask about them again.
        """
        for rel in relationships or ():
            author_id = str(rel.get('id') or '')
            if author_id:
                self._remember_follow(author_id, bool(rel.get('following')))

    def _remember_follow(self, author_id, following):
        follows = self._following_ids
        if author_id not in follows:
            # Bounded like the boost dedupe table: a long session scrolls
            # past thousands of authors, and the table only exists to save
            # a request, not to be an archive.
            while len(follows) >= FOLLOW_CACHE_LIMIT:
                del follows[next(iter(follows))]
        follows[author_id] = following

    def _with_home_reply_filter(self, statuses, on_done, ticket=None):
        """Apply the Home filters: boosts, then replies from non-follows."""
        if self._hide_boosts:
            statuses = filter_boosts(statuses)
        if not self._hide_replies_non_follows:
            on_done(statuses)
            return
        viewer_id = _own_account_id(self)
        unknown = set()
        for status in statuses:
            if not status.get('in_reply_to_id'):
                continue
            author_id = str((status.get('account') or {}).get('id') or '')
            if not author_id:
                continue
            if viewer_id and author_id == str(viewer_id):
                continue
            if author_id not in self._following_ids:
                unknown.add(author_id)
        if not unknown:
            on_done(filter_replies_from_non_follows(
                statuses, viewer_id, self._following_ids))
            return

        def loaded(rels, _ok):
            # Both answers are recorded, not just the following ones:
            # "asked and the answer is no" has to be distinguishable from
            # "never asked", or every page asks again about every author
            # the viewer does not follow. A failed batch leaves the
            # authors it did not answer unknown, and their replies out.
            self._remember_follows(rels)
            on_done(filter_replies_from_non_follows(
                statuses, viewer_id, self._following_ids))

        # A cancelled request answers with an error, and the answer still
        # reaches ``on_done``: the page's finish method asks its ticket and
        # drops the page itself (it has loading flags to release).
        ask_relationships(
            self.client, unknown, loaded,
            cancellable=None if ticket is None else ticket.cancellable)

    # The same filter, on the stream

    def _stream_unknown_author(self, status):
        """The author of a streamed post the server has to be asked about.

        None when the post needs no answer: it is not a reply, it is the
        viewer's own, or the page already knows whether the viewer
        follows its author.
        """
        if not self._hide_replies_non_follows:
            return None
        if not status.get('in_reply_to_id'):
            return None
        author_id = str((status.get('account') or {}).get('id') or '')
        if not author_id:
            return None
        viewer_id = _own_account_id(self)
        if viewer_id and author_id == str(viewer_id):
            return None
        if author_id in self._following_ids:
            return None
        return author_id

    def _queue_streamed(self, status):
        """Hold a streamed post back when the reply filter has to ask the
        server about it, or about a post ahead of it; True when held.

        The posts behind a held one wait with it: the timeline keeps the
        order the posts arrived in."""
        if not (self._stream_pending or self._stream_inflight
                or self._stream_unknown_author(status)):
            return False
        self._stream_pending.append(status)
        self._ask_about_stream_authors()
        return True

    def _ask_about_stream_authors(self):
        """Ask who the queued streamed posts' authors are, then land them.

        One lookup at a time; posts that arrive while it is out wait for
        the next round. The lookup is a read this page owns, so it goes
        through the page's stream scope: closed when the page is freed,
        and answers to a superseded one are dropped. Either way nothing
        is inserted."""
        if self._stream_inflight or not self._stream_pending:
            return
        queue = self._stream_pending
        self._stream_pending = []
        unknown = [a for a in (self._stream_unknown_author(s) for s in queue)
                   if a is not None]
        if not unknown:
            self._land_streamed(queue)
            self._ask_about_stream_authors()
            return
        scope = self._stream_scope
        if scope.closed:
            return
        self._stream_inflight = queue
        ticket = scope.begin()
        ask_relationships(
            self.client, unknown, weak_method(self._stream_authors_known),
            cancellable=ticket.cancellable, wanted=ticket.wanted,
            on_drop=weak_method(self._stream_lookup_dropped))

    def _stream_lookup_dropped(self):
        """The lookup was superseded or its scope closed: the posts it was
        asking about are not inserted. Posts that arrived since get their
        own round, unless the page is going away."""
        self._stream_inflight = []
        if not self._stream_scope.closed:
            self._ask_about_stream_authors()

    def _stream_authors_known(self, rels, _ok):
        queue = self._stream_inflight
        self._stream_inflight = []
        self._remember_follows(rels)
        self._land_streamed(queue)
        self._ask_about_stream_authors()

    def _land_streamed(self, queue):
        """Insert the queued posts the filter keeps, in arrival order.
        An author the lookup could not answer stays unknown and the reply
        is dropped, as on the paged path."""
        viewer_id = _own_account_id(self)
        for status in queue:
            kept = filter_replies_from_non_follows(
                [status], viewer_id, self._following_ids)
            if kept:
                self._insert_streamed(kept[0])

    def _fill_filtered(self):
        """Top a filtered page up to one page of posts.

        Scrolling is what asks for older posts, and a Home page whose
        filters hid most of a page may fit on the screen, with nothing to
        scroll and no way to ask. Counting posts instead of measuring the
        layout keeps this independent of row heights, which the list has
        not settled when a page lands. Only a filtering page does this: an
        unfiltered page is already full."""
        if not (self._hide_boosts or self._hide_replies_non_follows):
            return
        if self._catching:
            # The newer-posts request and this one would supersede each
            # other; the catch-up goes first.
            return
        if self.model.exhausted or self.model.get_n_items() == 0:
            return
        if self.model.get_n_items() < self.PAGE_SIZE:
            self.load_older()
