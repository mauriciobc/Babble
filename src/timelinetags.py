# SPDX-License-Identifier: GPL-3.0-or-later
"""Followed-hashtag marking for the Home timeline, as a mixin for
:class:`TimelinePage`.

Posts that reached Home through a followed hashtag are marked with the
tag, and the authors the reader does not follow are looked up once per
page (not per post) so the row can say why the post is there.

Split out of ``timelinepage.py``, which holds the page itself."""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa: E402

from .api import accounts as api_accounts  # noqa: E402
from .api import tags as api_tags  # noqa: E402
from .timelinedata import matching_tag  # noqa: E402


class FollowedTags:
    """Needs ``mode``, ``source``, ``client``, ``model``,
    ``_followed_tags``, ``_followed_waiting``, ``_followed_loading``,
    ``_following_ids`` and ``_remember_follows()`` from the page it is
    mixed into."""

    def invalidate_followed_tags(self):
        """Forget the followed tags, so the next page re-reads them."""
        self._followed_tags = None

    def _note_followed_tags(self, statuses):
        """Mark posts that reached Home through a followed hashtag."""
        if not (self.mode == 'home' and self.source is None and statuses):
            return
        if self.client.rate_limited():
            return
        if self._followed_tags is None:
            self._followed_waiting.append(statuses)
            if self._followed_loading:
                return
            self._followed_loading = True
            api_tags.followed_async(self.client, {'limit': 200},
                                    callback=self._on_followed_tags)
            return
        self._apply_followed_tags(statuses)

    def _on_followed_tags(self, client, result, _data=None):
        self._followed_loading = False
        try:
            data = api_tags.followed_finish(client, result)
        except GLib.Error:
            self._followed_tags = []
        else:
            self._followed_tags = sorted(
                {tag.get('name') for tag in data or () if tag.get('name')},
                key=str.casefold)
        waiting = self._followed_waiting
        self._followed_waiting = []
        for statuses in waiting:
            self._apply_followed_tags(statuses)

    def _apply_followed_tags(self, statuses):
        names = self._followed_tags or ()
        if not names:
            return
        hits = {}
        authors = []
        for status in statuses:
            tag = matching_tag(status, names)
            if tag is None:
                continue
            status_id = str(status.get('id') or '')
            if not status_id:
                continue
            hits[status_id] = tag
            author_id = str(
                (status.get('account') or {}).get('id') or '')
            if author_id and author_id not in authors:
                authors.append(author_id)
        if not hits:
            return
        # Only authors this page has not already been told about go back to
        # the server. Paging down a boost-heavy timeline meets the same
        # boosters over and over, and asking about them again on every page
        # was a request per page, followed by a full synchronous rebuild of
        # every row it touched when the answer arrived.
        unknown = [a for a in authors if a not in self._following_ids]
        if not unknown:
            self.model.apply_source_tags(hits, self._following_ids)
            return
        api_accounts.relationships_async(
            self.client, unknown[:40],
            lambda client, result, _data=None:
                self._on_tag_relationships(client, result, hits))

    def _on_tag_relationships(self, client, result, hits):
        try:
            rels = api_accounts.relationships_all_finish(client, result)
        except GLib.Error:
            self.model.apply_source_tags(hits, self._following_ids)
            return
        self._remember_follows(rels)
        self.model.apply_source_tags(hits, self._following_ids)
