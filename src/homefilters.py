# SPDX-License-Identifier: GPL-3.0-or-later
"""The Home timeline's filters, as a mixin for :class:`TimelinePage`.

Hiding boosts and replies from people the viewer does not follow both
need the page's own state (its model, its client, the follow table), so
they live beside it rather than in a function. Split out of
``timelinepage`` to keep that module under the size limit."""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa: E402

from .timelinebase import FOLLOW_CACHE_LIMIT, _own_account_id  # noqa: E402
from .timelinedata import (  # noqa: E402
    filter_boosts, filter_replies_from_non_follows)


class HomeFilters:
    """Needs ``client``, ``model``, ``_hide_boosts``,
    ``_hide_replies_non_follows``, ``_following_ids`` and
    ``_reply_filter_generation`` from the page it is mixed into."""

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

    def _with_home_reply_filter(self, statuses, on_done):
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
        generation = self._reply_filter_generation
        ids = list(unknown)[:40]

        def loaded(client, result, _data=None):
            if generation != self._reply_filter_generation:
                return
            try:
                # The whole list. relationships_finish returns the first
                # entry only, for the profile page that asks about one
                # account; iterating that dict here raised on every reply
                # from an author not yet asked about, and the page stayed
                # on "loading" forever.
                rels = client.request_finish(result)
            except GLib.Error:
                on_done(filter_replies_from_non_follows(
                    statuses, viewer_id, self._following_ids))
                return
            # Both answers are recorded, not just the following ones:
            # "asked and the answer is no" has to be distinguishable from
            # "never asked", or every page asks again about every author
            # the viewer does not follow.
            self._remember_follows(rels)
            on_done(filter_replies_from_non_follows(
                statuses, viewer_id, self._following_ids))

        self.client.relationships_async(ids, loaded)

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
        if self.model.exhausted or self.model.get_n_items() == 0:
            return
        if self.model.get_n_items() < self.PAGE_SIZE:
            self.load_older()
