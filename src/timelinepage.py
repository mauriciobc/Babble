# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, GLib, Gio,  # noqa: E402
                           Gtk)

from . import filterrules  # noqa: E402
from . import pages  # noqa: E402
from .pages import PagedPosts, connect_weak  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402

"""The scrolling timeline page and its API source."""
from .statusrun import StatusRunRow, RunModel
from .timelinebase import (
    AUTO_REFRESH_SCROLLED_TICKS, COLUMN_WIDTH, FOLLOW_CACHE_LIMIT,
    NEW_POSTS_ANNOUNCE_INTERVAL_US, PAGE_SIZE, TIMELINE_MAX_ITEMS,
    _EMPTY_PAGED_ENVELOPE, _own_account_id, id_key)
from .timelinedata import (
    BoostCarouselItem, TimelineModel, filter_replies_from_non_follows,
    matching_tag)


class TimelineFactory(Gtk.SignalListItemFactory):

    def __init__(self, client, filter_context=filterrules.HOME):
        super().__init__()
        self.client = client
        self.filter_context = filter_context
        self.connect('setup', self.on_setup)
        self.connect('bind', self.on_bind)

    def on_setup(self, _factory, list_item):
        card = StatusRunRow()
        card.filter_context = self.filter_context
        card.list_item_ref = list_item.weak_ref()
        list_item.set_child(card)
        list_item.set_activatable(True)

    def on_bind(self, _factory, list_item):
        run = list_item.get_item()
        card = list_item.get_child()
        card.bind(run, self.client)


def filter_context_for(source):
    """Which ``Filter#context`` a timeline's rows are shown under.

    A rule only applies where its own context says it should, so the page
    has to say which view it is before a row can ask. The path is enough:
    the home timeline and the lists a reader keeps for themselves are
    'home'; anything a stranger could see -- the local and federated
    timelines, a hashtag, a trending link -- is 'public'; and an account's
    own posts are 'account'.

    A source that is neither (bookmarks, favourites, search) falls back to
    'home', which is what the Mastodon web client does for the same views.
    """
    path = getattr(source, 'path', None) or ''
    if path.startswith('/accounts/'):
        return filterrules.ACCOUNT
    if path.startswith('/timelines/public') \
            or path.startswith('/timelines/tag/') \
            or path.startswith('/timelines/link'):
        return filterrules.PUBLIC
    return filterrules.HOME


def link_paged(path):
    """Whether *path* is one whose cursor is not a status id.

    Bookmarks and favourites are ``max_id``/``since_id`` in name only:
    Mastodon pages them by their own ids, so TimelinePage's id cursors
    mean nothing there and the ``Link`` header is the only way back.
    Named here rather than in the window, so the rule sits with the
    source that acts on it.
    """
    return path in LINK_PAGED_PATHS


# Endpoints whose Link header is their cursor. See link_paged().
LINK_PAGED_PATHS = ('/bookmarks', '/favourites')


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

    def fetch(self, params, callback):
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
                    task = Gio.Task.new(self.client, None, callback)
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
        self.client.request_async('GET', self.path, params=merged,
                                  callback=callback)

    def finish(self, result):
        data, next_params, _prev = self.client.request_finish_paged(result)
        statuses = self.extract(data) if self.extract else data
        if self.paging == 'link' and (
                self._older_request or self._next is None):
            self._next = next_params if statuses else None
        return statuses or []


def _auto_refresh_tick(page_ref):
    page = page_ref()
    if page is None:
        return GLib.SOURCE_REMOVE
    page._on_auto_refresh()
    return GLib.SOURCE_CONTINUE


def _new_posts_announce_tick(page_ref):
    page = page_ref()
    if page is not None:
        page._flush_new_posts_announcement()
    return GLib.SOURCE_REMOVE


class TimelinePage(PagedPosts, Adw.Bin):
    """Scrollable timeline with infinite scroll, refresh and status pages.

    mode: 'home' (authenticated) or 'public'. A custom ``source`` (with
    fetch(params, callback) and finish(result)) overrides both, e.g.
    account statuses on profiles.
    New posts arriving through a since_id refresh are announced with a
    'N new posts' pill instead of jumping the scroll position. Automatic
    polling is off until set_auto_refresh_interval() turns it on.
    """

    __gtype_name__ = 'BabbleTimelinePage'

    def __init__(self, client, mode='home', model=None, source=None,
                 boosts_carousel=None, autoload=True, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.mode = mode
        self._load_started = False
        self.model = model if model is not None else TimelineModel()
        settings = pages.settings()
        if boosts_carousel is None:
            boosts_carousel = settings.get_boolean('boosts-carousel')
        self.model.group_boosts_enabled = boosts_carousel
        self.model.dedupe_boosts_enabled = settings.get_boolean(
            'dedupe-boosts')
        self._hide_replies_non_follows = (
            mode == 'home' and source is None
            and settings.get_boolean('hide-replies-non-follows'))
        self._following_ids = {}
        self._reply_filter_generation = 0
        self.source = source
        # Paging flags and cursors. See PagedPosts.
        self._paging_init()
        self._followed_tags = None
        self._followed_loading = False
        self._followed_waiting = []
        self._auto_refresh_id = 0
        self._auto_refresh_ticks = 0
        self._pill_count = 0
        self._announce_count = 0
        self._announce_last_us = None
        self._announce_id = 0

        self.stack = Gtk.Stack()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.run_model = RunModel(self.model)
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection.new(self.run_model),
            factory=TimelineFactory(client,
                                    filter_context_for(source)),
            single_click_activate=True)
        self.list_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Posts')])
        connect_weak(self.list_view, 'activate', self.on_row_activated)
        # Phanpy-style: a single centered column with grey gutters. The
        # cap is COLUMN_WIDTH in sp, the clamp's default unit, so it is
        # 660px at the default text size and follows the text-scaling
        # factor. The clamp is the scrollable, so the column follows the
        # viewport and never raises the page's minimum width.
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        scrolled.set_child(clamp)
        scrolled.add_css_class('timeline-list')

        self.new_posts_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.new_posts_revealer.set_halign(Gtk.Align.CENTER)
        self.new_posts_revealer.set_valign(Gtk.Align.START)
        self.new_posts_revealer.set_margin_top(6)
        self.new_pill = Gtk.Button()
        self.new_pill.add_css_class('pill')
        self.new_pill.add_css_class('suggested-action')
        connect_weak(self.new_pill, 'clicked', self._on_new_pill_clicked)
        self.new_posts_revealer.set_child(self.new_pill)
        self.new_posts_revealer.set_reveal_child(False)

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(scrolled)
        self.overlay.add_overlay(self.new_posts_revealer)

        self.stack.add_named(self.overlay, 'list')
        self.set_child(self.stack)

        self.loading_page = pages.make_loading_page(
            _('Loading Timeline'), _('Fetching the latest posts'))
        self.error_page = pages.make_error_page(
            _('Could Not Load Timeline'), self._on_retry)
        self.empty_page = Adw.StatusPage(
            icon_name='user-home-symbolic',
            title=_('No Posts Yet'),
            description=_('New posts will appear here'))
        self.stack.add_named(self.loading_page, 'loading')
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(self.empty_page, 'empty')

        _RefreshGesture(self, scrolled)
        self._scrolled = scrolled
        adjustment = scrolled.get_vadjustment()
        connect_weak(adjustment, 'value-changed', self.on_scroll_position)
        if autoload:
            self.ensure_loaded()

    def ensure_loaded(self):
        """Fetch the first page once (profile tabs defer until selected)."""
        if self._load_started:
            return
        self._load_started = True
        self.load()

    def _fill_first(self, statuses):
        """Put *statuses* in the empty list as its first page."""
        self.model.append_old(statuses)
        self.model.oldest_id = min((s['id'] for s in statuses),
                                   key=id_key)
        self.model.newest_id = max((s['id'] for s in statuses),
                                   key=id_key)

    def _fill_loop(self, before):
        """Keep paging while a page left the list exactly as it was.

        One rule for the three ways a page can come back with nothing to
        show: every post hidden by a filter, or every post already seen.
        The cursor has still moved (or is moved to the raw page's oldest
        id), so the only way forward is the next older page; when the
        server has nothing more, the page says so and the loop ends.
        """
        if self.model.get_n_items() != before:
            return
        if self.model.exhausted:
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
            return
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('loading')
        self.load_older()

    # New posts pill / auto refresh

    def set_auto_refresh_interval(self, seconds):
        """Poll for new posts every *seconds* while the page is shown;
        0 turns polling off."""
        if self._auto_refresh_id:
            GLib.source_remove(self._auto_refresh_id)
            self._auto_refresh_id = 0
        self._auto_refresh_ticks = 0
        if seconds > 0:
            self._auto_refresh_id = GLib.timeout_add_seconds(
                seconds, _auto_refresh_tick, self.weak_ref())

    def _on_auto_refresh(self):
        if not self.get_mapped() or self.model.loading:
            return
        client = getattr(self, 'client', None)
        if client is not None and client.rate_limited():
            # A 429 is still in force; polling into it would only deepen
            # the limit the server just asked us to respect.
            return
        self._auto_refresh_ticks += 1
        at_top = self._scrolled.get_vadjustment().get_value() <= 0.0
        if at_top or self._auto_refresh_ticks \
                % AUTO_REFRESH_SCROLLED_TICKS == 0:
            self.refresh()

    def scroll_to_top(self):
        self._scrolled.get_vadjustment().set_value(0.0)

    def _show_new_pill(self, count):
        self._pill_count += count
        self.new_pill.set_label(
            ngettext('%d New Post', '%d New Posts', self._pill_count)
            % self._pill_count)
        self.new_posts_revealer.set_reveal_child(True)
        self._announce_new_posts(count)

    def _announce_new_posts(self, count):
        """Tell screen readers that *count* posts arrived at the top,
        throttled to one announcement per NEW_POSTS_ANNOUNCE_INTERVAL_US:
        a burst of streamed posts is spoken once, with its total."""
        self._announce_count += count
        if self._announce_id:
            return
        wait_us = 0 if self._announce_last_us is None else \
            self._announce_last_us + NEW_POSTS_ANNOUNCE_INTERVAL_US \
            - GLib.get_monotonic_time()
        if wait_us <= 0:
            self._flush_new_posts_announcement()
            return
        # A GObject weak reference, so the wait never keeps a closed
        # page alive.
        self._announce_id = GLib.timeout_add(
            wait_us // 1000 + 1, _new_posts_announce_tick, self.weak_ref())

    def _flush_new_posts_announcement(self):
        self._announce_id = 0
        count, self._announce_count = self._announce_count, 0
        # A page out of sight (another sidebar place) stays quiet.
        if not count or not self.get_mapped():
            return
        self._announce_last_us = GLib.get_monotonic_time()
        # Translators: spoken by screen readers when posts arrive at the
        # top of a timeline; %d is how many.
        pages.announce(self, ngettext('%d new post', '%d new posts', count)
                       % count)

    def _on_new_pill_clicked(self, _button):
        self._pill_count = 0
        self.new_posts_revealer.set_reveal_child(False)
        self.scroll_to_top()

    # Data

    def _paging_fetch(self, params, callback, generation):
        """Send one page request through whichever source this page has."""
        if self.source is not None:
            self.source.fetch(
                params, lambda c, r, _d=None, g=generation:
                callback(c, r, g))
            return
        if self.mode == 'public':
            self.client.timelines_public_async(
                params=params,
                callback=lambda c, r, _d=None, g=generation:
                callback(c, r, g))
        else:
            self.client.timelines_home_async(
                params=params,
                callback=lambda c, r, _d=None, g=generation:
                callback(c, r, g))

    def _finish(self, result):
        if self.source is not None:
            return self.source.finish(result)
        if self.mode == 'public':
            return self.client.timelines_public_finish(result)
        return self.client.timelines_home_finish(result)

    def _on_retry(self, _button):
        self.load()

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
            self.client.request_async('GET', '/followed_tags', {'limit': 200},
                                      callback=self._on_followed_tags)
            return
        self._apply_followed_tags(statuses)

    def _on_followed_tags(self, client, result, _data=None):
        self._followed_loading = False
        try:
            data = client.request_finish(result)
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
        self.client.relationships_async(
            unknown[:40],
            lambda client, result, _data=None:
                self._on_tag_relationships(client, result, hits))

    def _on_tag_relationships(self, client, result, hits):
        try:
            rels = client.request_finish(result)
        except GLib.Error:
            self.model.apply_source_tags(hits, self._following_ids)
            return
        self._remember_follows(rels)
        self.model.apply_source_tags(hits, self._following_ids)

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
        """Apply the hide-replies-from-non-follows filter when enabled."""
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
                rels = client.relationships_finish(result)
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

    def on_page_loaded(self, client, result, generation=None):
        if generation is not None and self._superseded(generation):
            return
        try:
            statuses = self._finish(result)
        except GLib.Error as e:
            self.model.loading = False
            # A failed home request is shown as a failure. Falling back
            # to the public timeline here silently replaced the reader's
            # home with posts from accounts they do not follow: a 5xx,
            # a rate limit or a network blip looked exactly like a
            # private home timeline, and the wrong feed was worse than
            # the error and its retry button.
            self.error_page.set_description(
                summarize_error_markup(e))
            self.stack.set_visible_child_name('error')
            return
        # Paging needs a cursor even if the reply filter hides it all.
        raw_oldest = min((s['id'] for s in statuses), key=id_key) \
            if statuses else None
        self._with_home_reply_filter(
            statuses, lambda filtered: self._finish_page_loaded(
                filtered, generation, raw_oldest))

    def _finish_page_loaded(self, statuses, generation=None, raw_oldest=None):
        if generation is not None and self._superseded(generation):
            return
        # A refresh during the reply-filter lookup must not win.
        self.model.loading = False
        if not statuses:
            if raw_oldest is not None:
                # Every post was filtered: page past the id the server
                # returned rather than stop with no way forward.
                self.model.oldest_id = raw_oldest
                self._fill_loop(0)
                return
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
            else:
                self.model.exhausted = True
            return
        if self.model.get_n_items() == 0:
            self._fill_first(statuses)
        else:
            added = self.model.insert_new(statuses)
            if added and self._scrolled.get_vadjustment().get_value() > 24:
                self._show_new_pill(added)
        self._note_followed_tags(statuses)
        if self.source is not None and not self.source.paginated:
            self.model.exhausted = True
        if self.model.get_n_items() == 0:
            # Every post of this page was hidden by a filter: keep paging.
            self._fill_loop(0)
            return
        self.stack.set_visible_child_name('list')

    def _trim_old(self):
        """Keep the timeline to TIMELINE_MAX_ITEMS while the reader is on
        its first screen, so the dropped tail is far out of sight. Only
        id-paged timelines: a Link cursor cannot be moved back, and an
        unpaginated source could never fetch the dropped posts again.
        An older page in flight would land after the gap, so wait."""
        if self._loading_older or (
                self.source is not None
                and getattr(self.source, 'paging', None) != 'id'):
            return
        adjustment = self._scrolled.get_vadjustment()
        if adjustment.get_value() > adjustment.get_page_size():
            return
        self.model.trim(TIMELINE_MAX_ITEMS)

    def on_older_loaded(self, client, result, generation=None):
        if generation is not None and self._superseded(generation):
            self._release_bottom()
            return
        try:
            statuses = self._finish(result)
        except GLib.Error:
            # A failed page releases the lock, so the reader can try again
            # instead of waiting out the debounce for nothing.
            self._loading_older = False
            self._release_bottom()
            return
        # Nothing is released yet. Clearing the in-flight flag before the
        # reply filter is what let a scroll at the bottom, during that
        # round trip, start a second request: the newer one won the
        # generation, and this page -- already paid for -- was discarded as
        # superseded. The reader saw nothing, on every page, forever. The
        # flag is therefore cleared where the page is actually applied.
        # Captured now, not inside the callback: by the time it runs, this
        # page may already be in the model and "what was there before" would
        # be unanswerable.
        before = self.model.get_n_items()
        self._with_home_reply_filter(
            statuses, lambda filtered: self._finish_older_loaded(
                filtered, generation, before=before))

    def _finish_older_loaded(self, statuses, generation=None, before=0):
        if generation is not None and self._superseded(generation):
            self._release_bottom()
            return
        self._loading_older = False
        appended = self.model.append_old(statuses)
        self._note_followed_tags(statuses)
        if not appended:
            self.model.exhausted = True
        elif self.model.get_n_items():
            self.stack.set_visible_child_name('list')
        # One rule for "this page showed the reader nothing new": all
        # filtered, or all already seen. The cursor moved anyway, so the
        # next older page is the way to fill the screen.
        self._fill_loop(before)
        self._release_bottom()

    def on_refresh_loaded(self, client, result, generation=None):
        if generation is not None and self._superseded(generation):
            return
        self.model.loading = False
        try:
            statuses = self._finish(result)
        except GLib.Error as e:
            root = self.get_root()
            if root is not None and hasattr(root, 'show_error'):
                root.show_error(_('Refresh failed'), e.message)
            return
        if not statuses:
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
            return
        self._with_home_reply_filter(
            statuses, lambda filtered: self._finish_refresh_loaded(filtered))

    def _finish_refresh_loaded(self, statuses):
        if statuses:
            added = self.model.insert_new(statuses)
            self._note_followed_tags(statuses)
            if added and self._scrolled.get_vadjustment().get_value() > 24:
                self._show_new_pill(added)
            self._trim_old()
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('empty')

    def on_row_activated(self, _view, position):
        run = self.run_model.get_item(position)
        item = None if run is None else run.items[0]
        if item is None or isinstance(item, BoostCarouselItem):
            return
        from .thread import push_thread
        view = getattr(self.get_root(), 'navigation_view', None)
        if view is not None:
            push_thread(view, self.client, item)

    def on_scroll_position(self, adjustment):
        if self.on_scroll_near_end(adjustment):
            self.load_older()
        if adjustment.get_value() <= 0.0:
            self._pill_count = 0
            self.new_posts_revealer.set_reveal_child(False)
            self._trim_old()

    # Streaming hooks (driven by the shell's streaming connection)

    def on_stream_update(self, status):
        """Insert one streamed post at the top, the way a refresh would:
        filters apply and a scrolled-away reader gets the new-posts
        pill instead of a jump."""
        added = self.model.insert_new([status])
        self._note_followed_tags([status])
        if self.model.oldest_id is None:
            self.model.oldest_id = status['id']
        if not added:
            return
        if self._scrolled.get_vadjustment().get_value() > 24:
            self._show_new_pill(added)
        else:
            self._announce_new_posts(added)
        self._trim_old()
        self.stack.set_visible_child_name('list')

    def on_stream_delete(self, status_id):
        """Drop a deleted post (and boosts of it) from the list."""
        if self.model.remove_status(status_id):
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')

    def on_stream_status_update(self, status):
        """Replace an edited post in place."""
        self.model.replace_status(status)


class _RefreshGesture:
    """Pull down at the top of the list to refresh (Phanpy-style)."""

    def __init__(self, page, scrolled):
        # Held weakly: the controller lives on the scrolled window inside
        # the page, so strong references here would keep the page alive
        # through the handler. A GObject weak reference, because PyGObject
        # re-creates the page's Python wrapper while GTK alone holds it.
        self._page = page.weak_ref()
        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL)
        controller.connect('scroll', self.on_scroll)
        scrolled.add_controller(controller)

    def on_scroll(self, controller, _dx, dy):
        page = self._page()
        adjustment = controller.get_widget().get_vadjustment()
        if page is None or adjustment is None:
            return False
        at_top = adjustment.get_value() <= 0.0
        if at_top and dy < 0:
            page.refresh()
        return False
