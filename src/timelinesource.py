# SPDX-License-Identifier: GPL-3.0-or-later

"""TimelinePage source over any Status-array Mastodon endpoint."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Soup', '3.0')

from gi.repository import Gio  # noqa: E402

from .api import core as api_core  # noqa: E402
from .timelinebase import PAGE_SIZE, _EMPTY_PAGED_ENVELOPE  # noqa: E402


class ApiTimelineSource:
    """TimelinePage source over any endpoint returning a Status array.

    ``paging='id'`` passes TimelinePage's max_id/since_id through (home,
    public, tag, list, account timelines). ``paging='link'`` follows the
    response's ``Link: rel=next`` instead, for endpoints whose cursor is
    not a status id (bookmarks, favourites, trends with ``offset``).
    ``paging='none'`` loads one batch. ``extract`` maps a non-array
    response (e.g. search results) to the status list."""

    def __init__(self, client, path, params=None, paging='id',
                 extract=None):
        self.client = client
        self.path = path
        self.base_params = dict(params or {})
        self.paging = paging
        self.paginated = paging != 'none'
        self.extract = extract
        self._next = None
        self._older_request = False

    def fetch(self, params, callback, cancellable=None):
        merged = dict(self.base_params)
        if self.paging == 'id':
            merged.update(params)
        elif self.paging == 'link':
            merged['limit'] = params.get('limit', PAGE_SIZE)
            if 'max_id' in params:
                # Older page: follow the Link cursor. None means the end
                # was reached, answered as an empty batch rather than a
                # request the server would refuse.
                if not self._next:
                    task = Gio.Task.new(self.client, cancellable, callback)
                    task.return_value(_EMPTY_PAGED_ENVELOPE)
                    return
                merged.update(self._next)
            elif 'since_id' in params:
                # Refresh. The cursor from the first page is not a
                # starting point -- it points into what is already shown
                # -- so the newest page is fetched and the model drops
                # what it has. Forwarding since_id here, which this
                # silently did not, would page past everything newer.
                self._next = None
            self._older_request = 'max_id' in params
        api_core.send_path(self.client, 'GET', self.path, params=merged,
                           callback=callback, cancellable=cancellable)

    def finish(self, result):
        data, next_params, _prev = api_core.finish_paged(
            self.client, result)
        statuses = self.extract(data) if self.extract else data
        if self.paging == 'link' and (
                self._older_request or self._next is None):
            self._next = next_params if statuses else None
        return statuses or []

