# SPDX-License-Identifier: GPL-3.0-or-later
"""A profile's statuses list and the dialog that wraps a profile.

``AccountStatuses`` is the timeline component the profile's tabs render;
``ProfileDialog`` is the window a profile opens into when it is reached
from a status or a link.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib  # noqa: E402

from .api import accounts as api_accounts  # noqa: E402
from .timelinebase import COLUMN_WIDTH, id_key  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



class AccountStatuses:
    """TimelinePage source over /accounts/:id/statuses.

    ``include_pinned`` puts the account's pinned posts ahead of the first
    page (Tuba's Posts tab); they are flagged with ``pinned = True`` and
    their regular copies further down are dropped by the model."""

    def __init__(self, client, account_id, pinned=False, media=False,
                 exclude_replies=False, paginated=True,
                 exclude_reblogs=False, include_pinned=False):
        self.client = client
        self.account_id = account_id
        self.pinned = pinned
        self.media = media
        self.exclude_replies = exclude_replies
        self.exclude_reblogs = exclude_reblogs
        self.include_pinned = include_pinned
        self.paginated = paginated
        self.pinned_statuses = []
        self.oldest_regular_id = None

    def base_params(self):
        params = {}
        if self.pinned:
            params['pinned'] = 'true'
        if self.media:
            params['only_media'] = 'true'
        if self.exclude_replies:
            params['exclude_replies'] = 'true'
        if self.exclude_reblogs:
            params['exclude_reblogs'] = 'true'
        return params

    def fetch(self, params, callback, cancellable=None):
        merged = self.base_params()
        merged.update(params or {})
        paged = any(key in merged for key in ('max_id', 'since_id', 'min_id'))
        if not self.include_pinned or paged:
            api_accounts.statuses_async(
                self.client, self.account_id, params=merged,
                callback=callback, cancellable=cancellable)
            return

        def pinned_loaded(client, result, _data=None):
            if cancellable is not None and cancellable.is_cancelled():
                callback(client, result)
                return
            try:
                pinned = api_accounts.statuses_finish(client, result)
            except GLib.Error:
                pinned = []
            for status in pinned:
                status['pinned'] = True
            self.pinned_statuses = pinned
            api_accounts.statuses_async(
                client, self.account_id, params=merged,
                callback=callback, cancellable=cancellable)

        api_accounts.statuses_async(
            self.client, self.account_id, params={'pinned': 'true'},
            callback=pinned_loaded, cancellable=cancellable)

    def finish(self, result):
        statuses = api_accounts.statuses_finish(self.client, result)
        pinned, self.pinned_statuses = self.pinned_statuses, []
        if statuses:
            self.oldest_regular_id = min((s['id'] for s in statuses),
                                         key=id_key)
        return pinned + statuses

class ProfileTimeline(TimelinePage):
    """Timeline of one account tab. Pinned posts ahead of the first page
    must not drag the paging cursor back to the pin's age."""

    def on_page_loaded(self, client, result):
        TimelinePage.on_page_loaded(self, client, result)
        oldest = getattr(self.source, 'oldest_regular_id', None)
        if oldest is not None and self.model.get_n_items():
            self.model.oldest_id = oldest

class ProfileDialog(Adw.Dialog):
    """Profile in a bottom sheet, like Phanpy's account sheet. Lists the
    profile opens (followers, following, familiar followers) stack inside
    the sheet; anything the profile opens in the window's navigation (a
    thread, a post, a hashtag) closes the sheet so it lands in view. The
    media viewer is an overlay of the window too, so presenting it from
    inside the sheet closes the sheet."""

    __gtype_name__ = 'BabbleProfileDialog'

    def __init__(self, view, page, title, **kwargs):
        navigation = Adw.NavigationView()
        navigation.push(Adw.NavigationPage(title=title, tag='profile',
                                           child=page))
        # The sheet covers the window's own toast overlay, so it needs
        # one of its own for the feedback raised from inside it.
        super().__init__(title=title,
                         child=Adw.ToastOverlay(child=navigation),
                         content_width=COLUMN_WIDTH, content_height=900,
                         presentation_mode=Adw.DialogPresentationMode.BOTTOM_SHEET,
                         **kwargs)
        self._view = view
        # The window's navigation view outlives the sheet.
        self._pushed = connect_weak(view, 'pushed', self._on_view_pushed)
        connect_weak(self, 'closed', self._on_closed)

    def _on_view_pushed(self, _view):
        self.close()

    def _on_closed(self, _dialog):
        self._view.disconnect(self._pushed)
