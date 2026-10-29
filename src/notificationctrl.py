# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch, filter and unread state for the notifications list.

The page is the widget. These methods are the controller that used to
live on it: paging, the type filter, the policy summary, announcements,
the read marker and clear-all. They use the page as ``self``.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GLib  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .pages import PagedPosts, push_page  # noqa: E402
from .statustext import parse_content  # noqa: E402


# Relationships fetched for "Follow Back", shared by every notifications
# page and keyed by (instance, account id) so recycled rows and a second
# page do not ask again. The endpoint takes 40 ids; Catch Up's pipeline
# fetches relationships in batches of the same number.
RELATIONSHIP_BATCH = 40
_RELATIONSHIPS = {}
_RELATIONSHIPS_PENDING = set()


class NotificationController:
    # Data

    def _fetch_params(self, base):
        """Merge the quick filter's ``types[]`` and the persisted
        ``exclude_types[]`` (docs.joinmastodon.org/methods/
        notifications); a request for one account also asks for the
        filtered notifications held back from the main list."""
        params = dict(base)
        types = self.FILTERS[self.filter]
        if types:
            params['types[]'] = types
        excluded = [t for t in self._applied_excluded
                    if t not in (types or ())]
        if excluded:
            params['exclude_types[]'] = excluded
        if self.account_id is not None:
            params['account_id'] = self.account_id
            params['include_filtered'] = 'true'
        return params

    def _finish(self, result):
        return self.client.notifications_finish(result)

    # Follow Back

    def _request_relationships(self):
        """Ask for the relationship of every single-follower row, in
        batches.

        Rows bind faster than a request returns, so a page of follow
        notifications would otherwise open one request per row. This runs
        after the model has merged the page, so only rows that will show
        a Follow Back button are asked about: several followers merged
        into one row never do. The answer re-emits the rows it covers so
        each one rebinds with its state.
        """
        client = self.client
        if not getattr(client, 'token', None):
            return
        instance = getattr(client, 'instance', '')
        for position in range(self.model.get_n_items()):
            item = self.model.get_item(position)
            if item.kind != 'follow' or len(item.actors) != 1:
                continue
            actor = item.actors[0]
            key = (instance, actor.get('id'))
            if actor.get('id') is None or key in _RELATIONSHIPS \
                    or key in _RELATIONSHIPS_PENDING:
                continue
            self._relationship_queue[key] = actor
        self._drain_relationship_queue()

    def _drain_relationship_queue(self):
        client = self.client
        if client is None or self._relationship_busy \
                or not self._relationship_queue:
            return
        batch = list(self._relationship_queue.items())[:RELATIONSHIP_BATCH]
        for key, _actor in batch:
            del self._relationship_queue[key]
            _RELATIONSHIPS_PENDING.add(key)
        instance = getattr(client, 'instance', '')
        self._relationship_busy = True

        def done(client, result, _data=None):
            self._relationship_busy = False
            try:
                data = client.request_finish(result)
            except GLib.Error:
                data = None
            for key, _actor in batch:
                _RELATIONSHIPS_PENDING.discard(key)
            arrived = set()
            for entry in data or []:
                if isinstance(entry, dict) and entry.get('id') is not None:
                    key = (instance, str(entry['id']))
                    _RELATIONSHIPS[key] = entry
                    arrived.add(key)
            # Ids queued while this was in flight go out now.
            self._drain_relationship_queue()
            self._relationships_arrived(instance, arrived)

        client.request_async('GET', '/accounts/relationships',
                             params={'id[]': [str(actor['id'])
                                              for _key, actor in batch]},
                             callback=done)

    def _relationships_arrived(self, instance, keys):
        """Re-emit the follow rows whose answer just came, so the ones
        on screen rebind; the rest pick it up when they are built."""
        if not keys:
            return
        for position in range(self.model.get_n_items()):
            item = self.model.get_item(position)
            if item.kind == 'follow' and len(item.actors) == 1 \
                    and (instance, str(item.actors[0].get('id'))) in keys:
                self.model.items_changed(position, 1, 1)

    def _paging_fetch(self, params, callback, generation):
        """One page request, with the type filters merged in.

        The filter merge is what makes this endpoint different from a
        timeline's, so it belongs in the seam the base calls rather than
        in a copy of the request. Goes through ``notifications_async`` so
        the page keeps the one typed client method for this endpoint.
        """
        self.client.notifications_async(
            params=self._fetch_params(params),
            callback=lambda c, r, _d=None, g=generation: callback(c, r, g))

    def on_page_loaded(self, client, result, generation=None):
        # A response from a superseded request must not write rows
        # of the old filter into the new model.
        if generation is not None and self._superseded(generation):
            return
        self.model.loading = False
        try:
            notifications = self._finish(result)
        except GLib.Error as e:
            if generation is not None \
                    and generation == self._quiet_generation:
                # A catch-up of an empty list: it keeps saying empty.
                return
            self.error_page.set_description(
                summarize_error_markup(e))
            self.stack.set_visible_child_name('error')
            return
        if not notifications:
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
            else:
                self.model.exhausted = True
            return
        if self.model.get_n_items() == 0:
            self._pin_top = True
            GLib.timeout_add(500, self._unpin_top)
        self.model.append_page(notifications)
        self._request_relationships()
        self.stack.set_visible_child_name('list')
        if self._is_visible():
            self.mark_read()

    def on_older_loaded(self, client, result, generation=None):
        # A response from a superseded request must not write rows
        # of the old filter into the new model.
        if generation is not None and self._superseded(generation):
            self._release_bottom()
            return
        self._loading_older = False
        try:
            notifications = self._finish(result)
        except GLib.Error:
            self._release_bottom()
            return
        added = self.model.append_page(notifications)
        self._request_relationships()
        if not added:
            self.model.exhausted = True
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
        # The page has landed (or there is none), so the bottom lock does
        # not have to wait out its debounce before the next one can go out.
        self._release_bottom()

    def refresh(self, _button=None):
        """Reload the newest notifications; an empty list reloads from
        scratch, which also recovers from the error page."""
        if self.client.token and self.account_id is None:
            self.fetch_policy()
            self.load_announcements()
        if self.model.get_n_items() == 0:
            self.load()
        else:
            self.on_refresh()

    def on_refresh(self, _button=None):
        # Paging's refresh(), which uses min_id for this endpoint.
        PagedPosts.refresh(self)

    def catch_up(self):
        """Ask quietly for what is newer than the top of the list.

        This is the page being opened or the stream coming back, not the
        reader pressing refresh, so it is the one request the list
        needs: no policy or announcements, no spinner over an empty
        list, and no error toast when it fails -- the reader asked for
        nothing, and a stale list is what they already had.
        """
        if self.account_id is not None or not self.client.token \
                or self.model.loading:
            return
        if self.model.get_n_items() == 0:
            if self.stack.get_visible_child_name() != 'empty':
                # Never loaded, or failed: a full load is the recovery.
                self.load()
                return
            self.model.loading = True
            self._request(self._first_page_params(), self.on_page_loaded)
        else:
            PagedPosts.refresh(self)
        self._quiet_generation = self._generation

    def on_refresh_loaded(self, client, result, generation=None):
        # A response from a superseded request must not write rows
        # of the old filter into the new model.
        if generation is not None and self._superseded(generation):
            return
        self.model.loading = False
        try:
            notifications = self._finish(result)
        except GLib.Error as e:
            if generation is None or generation != self._quiet_generation:
                pages.report_error(self, _('Refresh failed'), e)
            return
        if notifications:
            self.model.prepend_page(notifications)
            self._request_relationships()
            self._marked_read = False
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('empty')
        if self._is_visible():
            # Marking read is what clears the badge here.
            self.mark_read()
        else:
            self.fetch_unread()

    def _unpin_top(self):
        self._pin_top = False
        return GLib.SOURCE_REMOVE

    def on_scroll_position(self, adjustment):
        # The list view scrolls its first row into view when it is first
        # populated, which hides the leading date heading.
        if self._pin_top and adjustment.get_value() > 0:
            adjustment.set_value(0)
            return
        if adjustment.get_upper() > 0 and self.on_scroll_near_end(adjustment):
            self.load_older()

    # Streaming

    def on_stream_notification(self, notification):
        """A notification arrived on the user stream."""
        self.merge_notifications([notification])

    def merge_notifications(self, notifications):
        """Merge newer notifications from the stream or the poller into
        the list (honouring the type filters) and bump the unread badge
        unless the list is on screen.

        A batch is one splice and one marker or badge update: a poll of
        twenty fed in one at a time reflowed the list twenty times and
        sent twenty markers that could land out of order.
        """
        if self.account_id is not None:
            return
        types = self.FILTERS[self.filter]
        wanted = [n for n in notifications
                  if isinstance(n, dict) and 'id' in n and 'account' in n
                  and n.get('type', '') not in self._applied_excluded
                  and (not types or n.get('type', '') in types)]
        if not wanted:
            return
        if self.model.get_n_items() == 0 \
                and self.stack.get_visible_child_name() != 'empty':
            # The first page has not landed. Seeded with these, the list
            # would only ever ask for what is newer than them, and the
            # page they stand in for would never load.
            if not self.model.loading:
                self.load()
            return
        from .notifications import id_key
        wanted.sort(key=lambda n: id_key(n['id']), reverse=True)
        added = self.model.prepend_page(wanted)
        self._request_relationships()
        if not added:
            return
        self.stack.set_visible_child_name('list')
        self._marked_read = False
        if self._is_visible():
            self.mark_read()
        else:
            self._unread += added
            self.emit('unread-changed', self._unread)

    # Filtering

    def _reload(self):
        # Supersede anything in flight for the previous filter or
        # exclusion set.
        self._generation += 1
        self.model.clear()
        self.model.loading = False
        # A filter switch re-enters pagination from the top; released
        # from a pending page fetch, pagination delegates to the new
        # cursor. The bottom lock belongs to the superseded request, so
        # it ends with it rather than holding the next scroll out.
        self._loading_older = False
        self._release_bottom()
        self.load()

    def set_filter(self, name):
        if name == self.filter:
            return
        self.filter = name
        self._reload()

    def on_filter_changed(self, group, _pspec):
        self.set_filter(group.get_active_name())

    # Filtered notification requests

    def fetch_policy(self):
        """GET /api/v2/notifications/policy: its summary says how many
        filtered-notification requests are waiting."""
        if not self.client.token:
            return
        self.client.request_async('GET', '/api/v2/notifications/policy',
                                  callback=self.on_policy_loaded)

    def on_policy_loaded(self, client, result, _user_data=None):
        try:
            policy = client.request_finish(result)
        except GLib.Error:
            # Servers before Mastodon 4.3 have no policy endpoint.
            self.requests_banner.set_revealed(False)
            return
        count = int(((policy or {}).get('summary') or {})
                    .get('pending_requests_count') or 0)
        if count:
            self.requests_banner.set_title(ngettext(
                '%d filtered notification request',
                '%d filtered notification requests', count) % count)
        self.requests_banner.set_revealed(count > 0)

    def on_requests_banner_clicked(self, _banner):
        from .notifications import NotificationRequestsPage
        push_page(self, _('Filtered Notifications'),
                  NotificationRequestsPage(self.client,
                                           on_changed=self.fetch_policy),
                  tag='notification-requests')

    # Instance announcements

    def load_announcements(self):
        """Fetch /api/v1/announcements for the banner; failures are
        silent (a banner is never worth an error dialog)."""
        if not self.client.token:
            return
        # Client stubs in the OAuth tests run login without the generic
        # request API.
        self.client.request_async('GET', '/announcements',
                                  callback=self.on_announcements_loaded)

    def on_announcements_loaded(self, client, result, _user_data=None):
        try:
            announcements = client.request_finish(result)
        except GLib.Error:
            return
        unread = next((a for a in announcements or []
                       if not a.get('read')), None)
        self.banner.set_revealed(unread is not None)
        if unread is None:
            return
        from .notifications import _instance_domain
        domain = _instance_domain(self.client.instance)
        text = ' '.join(
            segment['text'] for segment
            in parse_content(unread.get('content') or '')
            if segment['text'] != '\n') or unread.get('title') or ''
        self.banner.set_title(_('%(domain)s: %(text)s')
                              % {'domain': domain, 'text': text})

    def on_announcement_view(self, *_args):
        from .announcements import AnnouncementsPage
        page = AnnouncementsPage(self.client)
        page.connect('unread-changed', self._on_announcements_unread)
        push_page(self, _('Announcements'), page, tag='announcements')

    def _on_announcements_unread(self, _page, _count):
        self.load_announcements()

    # Unread badge

    def fetch_unread(self):
        if not self.client.token:
            return
        self.client.markers_async(['notifications'],
                                  callback=self._on_markers_before_unread)

    def _on_markers_before_unread(self, client, result, _user_data=None):
        try:
            markers = client.markers_finish(result)
        except GLib.Error:
            markers = {}
        marker = (markers or {}).get('notifications') or {}
        last_read = marker.get('last_read_id')
        newest = self.model.newest_id
        if last_read and newest and str(last_read) >= str(newest):
            self._marked_read = True
            self._unread = 0
            self.emit('unread-changed', 0)
            return
        # Match the list's exclusions: an excluded type must not keep the
        # badge above zero forever, since the list can never clear it.
        params = None
        if self._applied_excluded:
            params = {'exclude_types[]': sorted(self._applied_excluded)}
        self.client.notifications_unread_count_async(
            params=params, callback=self.on_unread_count)

    def on_unread_count(self, client, result):
        try:
            count = client.notifications_unread_count_finish(result)
        except GLib.Error:
            return
        self._unread = count
        self.emit('unread-changed', count)

    def on_shown(self):
        """The window calls this when the page becomes visible.

        The list is kept live by the stream, but a notification can be
        seen by the app and still be missing from it: one that arrived
        while the stream was down is announced by the poller and only
        reaches the rows on a reload, and one that arrived before the
        page had a model to merge it into was dropped. Asking for what
        is newer than the top on every visit is what shows them.

        What is on screen is marked read now, so a failed or superseded
        catch-up cannot leave the badge up; the answer marks again with
        what it added. Markers go out one at a time, so the second
        cannot be overtaken by the first.
        """
        self._shown = True
        self._marked_read = False
        self.mark_read()
        self.catch_up()

    def on_hidden(self):
        """The window calls this when another page replaces this one;
        notifications streaming in afterwards count as unread."""
        self._shown = False

    def _is_visible(self):
        """True while this page is the one on screen: a reload triggered
        from elsewhere (a changed filter, a stream event) must not mark
        unseen notifications as read."""
        return self._shown and self.get_mapped()

    def mark_read(self):
        if not self.client.token or self._marked_read \
                or self.account_id is not None \
                or self.model.newest_id is None:
            return
        if self._marking is not None:
            # One marker at a time: two in flight can land out of order,
            # and the older id would be the one the server keeps.
            self._mark_again = True
            return
        self._marking = self.model.newest_id
        self.client.notifications_mark_read_async(
            self._marking, self.on_marked_read)

    def on_marked_read(self, client, result):
        marked, self._marking = self._marking, None
        again, self._mark_again = self._mark_again, False
        try:
            client.notifications_mark_read_finish(result)
        except GLib.Error:
            marked = None
        if marked is not None and marked == self.model.newest_id:
            self._marked_read = True
            self._unread = 0
            self.emit('unread-changed', 0)
        if again:
            self.mark_read()

    # Dismiss one

    def dismiss(self, item):
        """Dismiss the notifications behind one row: the row goes at once,
        one request per notification a grouped row holds, and the row comes
        back with an error if any of them fails."""
        if not self.client.token:
            return
        ids = [n['id'] for n in item.notifications]
        position = self.model.remove(item)
        if position is None:
            return
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('empty')
        outcome = {'left': len(ids), 'error': None}

        def done(client, result, _data=None):
            outcome['left'] -= 1
            try:
                client.notifications_dismiss_finish(result)
            except GLib.Error as error:
                outcome['error'] = error
            if outcome['left']:
                return
            if outcome['error'] is None:
                # The server knows which notifications are still unread.
                self.fetch_unread()
                return
            # A grouped row that half-succeeded comes back whole: the
            # dismissed ones disappear on the next load.
            self.model.restore(item, position)
            self.stack.set_visible_child_name('list')
            pages.report_error(self, _('Could not dismiss the notification'),
                               outcome['error'])

        for notification_id in ids:
            self.client.notifications_dismiss_async(notification_id, done)

    # Clear

    def on_clear_requested(self, _button):
        if not self.client.token:
            return
        dialog = Adw.AlertDialog.new(
            _('Clear All Notifications?'),
            _('All notifications will be permanently deleted'))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('clear', _('_Clear'))
        dialog.set_response_appearance('clear',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.get_root() or self, None, self.on_clear_chosen)

    def on_clear_chosen(self, dialog, result):
        if dialog.choose_finish(result) != 'clear':
            return
        self.client.notifications_clear_async(self.on_cleared)

    def on_cleared(self, client, result):
        try:
            client.notifications_clear_finish(result)
        except GLib.Error as e:
            pages.report_error(self, _('Clear failed'), e)
            return
        self._marked_read = True
        self._unread = 0
        self.model.clear()
        self.stack.set_visible_child_name('empty')
        self.emit('unread-changed', 0)

