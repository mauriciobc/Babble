# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up: a separate view over the people you follow (CATCH-UP.md).

You choose a time range, Babble pages back through the home timeline to
that point and saves the result as an unchanging snapshot. This module is
the page around that: the period menu, the fetch loop, the snapshot
round-trip, and the All posts list (Phanpy's compact list with category and
author filters, sorts and grouping). The Highlights grid is another module's
widget; it is handed a :class:`Digest` and nothing else.

The data work lives elsewhere and has no widgets: ``catchupdigest`` (filters,
categories, boost deduplication, trends, sort keys), ``catchuprank`` (the
relevance score) and ``catchupstore`` (the files). What is here is plumbing
and widgets, and the two rules that keep it light:

* Nothing heavy runs on the main loop. Ranking and the snapshot file I/O run
  on a worker thread over plain data (:func:`compute`); the widgets are
  built, a chunk at a time, from idle callbacks.
* Filters and sorts change only the list's models. One ``CatchUpItem`` is
  built per visible post, once per snapshot, and the list, the posts bar and
  the Highlights slot all share it.

Ported from Phanpy's ``src/pages/catchup.jsx`` (MIT, (c) Lim Chee Aun; the
notice is in ``catchupdigest.py``).
"""

import dataclasses
import datetime
import functools
import math

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import accounts as account_store  # noqa: E402
from . import catchupstore, pages  # noqa: E402
from .catchupdigest import (  # noqa: E402
    CATEGORIES, PAGE_LIMIT, SORTS, apply_filters, author_counts,
    author_order, categorize, content_of, dedupe_boosts, fetch_progress,
    filter_posts, followed_tag_hits, is_filtered_post, mark_followed_tags,
    mark_threads, merge_hashtags, merge_news, parse_time, plain_text,
    post_info_values, posts_bar_ids, relationship_ids,
    sort_info_ids,
    timeline_hashtags, timeline_links, trending_status_keys)
from .mastodon import summarize_error_markup  # noqa: E402
from .statustext import compact_count, format_time, relative_time  # noqa: E402
from .mediacache import AvatarCache, _TextureCache  # noqa: E402
from .timelinedata import StatusItem  # noqa: E402
from .timelinebase import ui_language  # noqa: E402


@dataclasses.dataclass
class Digest:
    """What the Highlights slot is given: the one contract between this
    page and ``catchupgrid.HighlightsView``.

    Built once per catch-up (and once per reopened snapshot), after
    ranking, and never rebuilt when the All posts filters change.

    ``posts``: the ranked status dicts in final rank order (best first):
        boosts already deduplicated, the Filtered category excluded.
    ``scores``: ``{status id: relevance score}`` for those posts.
    ``items``: ``{status id: timeline.StatusItem}``, the same objects the
        All posts list shows (keyed by the id of the status in the snapshot,
        the boost wrapper's own id for a boost), so an action in one is seen
        in the other. It holds every visible post, filtered ones too.
    ``news``: ``catchupdigest.merge_news`` output.
    ``tags``: ``catchupdigest.merge_hashtags`` output.
    ``trending_keys``: ids and URIs of ``/trends/statuses``
        (``catchupdigest.trending_status_keys``); a post is trending when
        ``is_trending_post`` finds its content in it.
    ``boosters``: ``{kept boost id: [accounts of the other boosters]}``
        (``catchupdigest.dedupe_boosts``).
    ``end_at``: the snapshot's end time, the ``now`` of the whole digest.
    """

    posts: list
    scores: dict
    items: dict
    news: list
    tags: list
    trending_keys: frozenset
    boosters: dict
    end_at: datetime.datetime


# --------------------------------------------------------------------------
# Periods (2.2)

PERIOD_HOURS = {'1h': 1, '3h': 3, '6h': 6, '12h': 12, '24h': 24}
DEFAULT_PERIOD = '24h'
FETCH_PAUSE_SECONDS = 1           # between two pages (3.1)
# Mastodon takes at most 40 ids per /accounts/relationships call, so a
# long run is several calls; CATCH-UP.md 3.2 says "one batched" and 2.2
# budgets "~30 requests" for the whole catch-up. Batching is what the API
# allows, so this is a deliberate deviation from both.
RELATIONSHIP_BATCH = 40
TREND_LIMITS = (('links', '/trends/links', 20), ('tags', '/trends/tags', 20),
                ('statuses', '/trends/statuses', 40))
LOW_ACTIVITY_POSTS = 20
LOW_ACTIVITY_PERIOD = '24h'
BUILD_CHUNK = 150
MAX_CHIPS = 40
PEEK_CHARS = 240
THUMB_SIZE = 48
MAX_THUMBS = 3
TOAST_DELAY_MS = 450
COLUMN_WIDTH = 1000

STOPPED_COMPLETE = 'complete'
STOPPED_RATE_LIMITED = 'rate-limited'
STOPPED_CANCELLED = 'cancelled'
STOPPED_ERROR = 'error'

HTTP_TOO_MANY_REQUESTS = 429


def period_label(period):
    """The period menu's label for *period*."""
    hours = PERIOD_HOURS.get(period)
    if period == 'since':
        return _('Since the last catch-up')
    if period == 'max':
        return _('As far back as possible')
    if hours == 1:
        return _('Last hour')
    return ngettext('Last %d hour', 'Last %d hours', hours or 24) \
        % (hours or 24)


def period_title(period):
    """The results title, one translatable string per period."""
    hours = PERIOD_HOURS.get(period)
    if period == 'since':
        return _('Highlights since your last catch-up')
    if period == 'max':
        return _('Highlights')
    if hours == 1:
        return _('Highlights of the last hour')
    return ngettext('Highlights of the last %d hour',
                    'Highlights of the last %d hours', hours or 24) \
        % (hours or 24)


def hours_phrase(hours):
    """'the last hour' / 'the last 7 hours', for sentences."""
    if hours <= 1:
        return _('the last hour')
    return ngettext('the last %d hour', 'the last %d hours', hours) % hours


def posts_phrase(count):
    return ngettext('%d post', '%d posts', count) % count


def snapshot_label(meta):
    """'<start> - <end> . N posts', an entry of the previous catch-ups."""
    start = parse_time(meta.get('start_at') or meta.get('oldest_at'))
    end = parse_time(meta.get('end_at'))
    # Translators: an entry of the list of previous catch-ups: the time it
    # covers and how many posts it holds, e.g. "9 Oct 2026, 08:00 – 9 Oct
    # 2026, 20:00 · 120 posts"
    return _('%(start)s – %(end)s · %(posts)s') % {
        'start': format_time(start), 'end': format_time(end),
        'posts': posts_phrase(meta.get('count') or 0)}


def hours_between(newer, older):
    """Whole hours from *older* to *newer* (aware datetimes), at least 0."""
    if newer is None or older is None:
        return 0
    return max(0, int(round((newer - older).total_seconds() / 3600)))


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(moment):
    """ISO-8601 with a Z, or None."""
    if moment is None:
        return None
    return moment.astimezone(datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (moment.microsecond // 1000)


# --------------------------------------------------------------------------
# Worker thread plumbing

def run_in_thread(work, done):
    """Call ``work()`` on a worker thread and ``done(value, error)`` on the
    main context with its return value, or ``None`` and the exception.
    *work* must touch plain data only: no widget, no GSettings."""

    def thread(task, _source, _data, _cancellable):
        try:
            outcome = (work(), None)
        except Exception as failure:  # handed to done()
            outcome = (None, failure)
        # Always return, or the task never calls back.
        task.return_value(GObject.Value(GObject.TYPE_PYOBJECT, outcome))

    def finished(_source, task, _data):
        _ok, outcome = task.propagate_value()
        done(*outcome)

    task = Gio.Task.new(None, None, finished, None)
    task.run_in_thread(thread)


# --------------------------------------------------------------------------
# The digest of one snapshot (worker thread, pure data)

@dataclasses.dataclass(slots=True)
class PostInfo:
    """Sort keys and identity of one visible post, computed once."""

    id: str
    category: str
    author: str
    ts: float
    # One number per entry of ``catchupdigest.SORTS``.
    values: tuple


@dataclasses.dataclass
class Computed:
    """A snapshot after boost deduplication, ranking and trends merging."""

    snapshot: dict
    end_at: datetime.datetime
    visible: list           # deduplicated posts, snapshot order
    boosters: dict          # kept boost id -> other boosters' accounts
    dup_of: dict            # hidden boost id -> the kept boost's id
    info: dict              # post id -> PostInfo
    counts: dict            # category -> posts
    ranked: list            # status dicts, best first
    scores: dict            # post id -> score
    news: list
    tags: list
    trending_keys: frozenset
    accounts: dict          # author id -> account
    bar_dots: list | None   # post ids in time order, up to 160 posts
    bar_bins: list | None   # 320 lists of post ids, above that
    total: int              # every post, duplicates included


def _when(post):
    moment = parse_time(post.get('created_at'))
    return moment.timestamp() if moment else float('-inf')


def compute(snapshot, viewer_languages=None, hide_trending=False):
    """Everything the page derives from *snapshot*: categories, ranks,
    trends and sort keys. A pure function of its arguments (``now`` is the
    snapshot's ``end_at``), so reopening a snapshot gives the same page;
    it runs on a worker thread."""
    from . import catchuprank

    posts = snapshot.get('posts') or []
    end_at = parse_time(snapshot.get('end_at')) or now_utc()
    visible, boosters, hidden = dedupe_boosts(posts)
    first = {}
    dup_of = {}
    for post in posts:
        reblog = post.get('reblog')
        if reblog:
            kept = first.setdefault(reblog['id'], post['id'])
            if post['id'] in hidden:
                dup_of[post['id']] = kept

    relationships = snapshot.get('relationships') or {}
    mutual = {key for key, rel in relationships.items()
              if rel.get('followed_by')}
    candidates = [p for p in visible if not is_filtered_post(p)]
    context = catchuprank.RankingContext(
        now=end_at, mutual_author_ids=mutual,
        viewer_languages=list(viewer_languages) if viewer_languages
        else None)
    ranked_candidates = catchuprank.rank(candidates, context)
    ranked = [c.status for c in ranked_candidates]
    scores = {c.status['id']: c.score for c in ranked_candidates}

    info = {}
    accounts = {}
    counts = dict.fromkeys(CATEGORIES, 0)
    for post in visible:
        content = content_of(post)
        author = post.get('account') or {}
        accounts.setdefault(str(author.get('id', '')), author)
        ts = _when(post)
        score = scores.get(post['id'], scores.get(content.get('id'), 0))
        category = categorize(post)
        counts[category] += 1
        info[post['id']] = PostInfo(
            post['id'], category, str(author.get('id', '')), ts,
            post_info_values(post, ts, score))

    trends = snapshot.get('trends') or {}
    # Untrimmed, so a trending link is matched against every shared link.
    # CATCH-UP.md 3.5 also calls this the fallback news source, and 3.4
    # trims to at least 10 links; using the trimmed list there would give
    # fewer tiles. Kept untrimmed on purpose — say so if that changes.
    links = timeline_links(posts, trim=False)
    tl_tags = timeline_hashtags(posts, now=end_at)
    news = merge_news(trends.get('links'), links, hide_trending)
    tags = merge_hashtags(trends.get('tags'), snapshot.get('followed_tags'),
                          tl_tags, hide_trending)
    keys = trending_status_keys(trends.get('statuses'), hide_trending)

    by_time = sorted(posts, key=_when)
    bar_dots, bar_bins = posts_bar_ids(by_time, end_at)
    return Computed(
        snapshot=snapshot, end_at=end_at, visible=visible,
        boosters=boosters, dup_of=dup_of, info=info, counts=counts,
        ranked=ranked, scores=scores, news=news, tags=tags,
        trending_keys=keys, accounts=accounts,
        bar_dots=bar_dots, bar_bins=bar_bins,
        total=len(posts))


# --------------------------------------------------------------------------
# The All posts view: filters, sort, grouping (2.5)

@dataclasses.dataclass
class ViewState:
    category: str = 'all'
    author: str | None = None
    sort: str = 'time'
    order: str = 'asc'
    group: bool = False
    # Highlight pages the user had expanded with "Show more", kept per
    # snapshot so reopening one does not collapse it again (7).
    pages: int = 1

    def is_default(self):
        return self == ViewState()


@dataclasses.dataclass
class View:
    """What the list shows for a :class:`ViewState`."""

    author: str | None          # the author filter that took effect
    matching: set               # ids of the posts listed
    order: list                 # their ids, in display order
    author_rank: dict           # author id -> section, when grouped
    category_authors: list      # author ids in the category, most posts first
    category_counts: dict       # author id -> posts in the category


def order_ids(infos, sort_by='time', order='asc', group=False,
              author_index=None):
    """Post ids in display order; delegates to
    :func:`catchupdigest.sort_info_ids`."""
    return sort_info_ids(infos, sort_by, order, group, author_index)


def derive_view(computed, state):
    """The list for *state*: which posts match and in what order."""
    visible = computed.visible
    in_category = filter_posts(visible, state.category, None,
                               computed.boosters)
    counts = author_counts(in_category)
    present = set(counts)
    for post in in_category:
        present.update(str(a.get('id', ''))
                       for a in computed.boosters.get(post['id'], ()))
    author = state.author if state.author in present else None
    matching = filter_posts(in_category, 'all', author, computed.boosters)
    infos = [computed.info[p['id']] for p in matching]
    author_index = None
    if state.group:
        author_index = {a: i for i, a in enumerate(author_order(matching))}
    order = order_ids(infos, state.sort, state.order, state.group,
                      author_index)
    return View(author, {p['id'] for p in matching}, order,
                author_index or {}, author_order(in_category), counts)


# --------------------------------------------------------------------------
# Words for the filter toast (2.5): parts, each translatable

def category_name(category):
    return {
        'all': _('All'), 'original': _('Original'), 'replies': _('Replies'),
        'boosts': _('Boosts'), 'quotes': _('Quotes'),
        'followed_tags': _('Followed tags'), 'groups': _('Groups'),
        'filtered': _('Filtered'),
    }.get(category, category)


def category_showing(category):
    return {
        'all': _('all posts'), 'original': _('original posts'),
        'replies': _('replies'), 'boosts': _('boosts'),
        'quotes': _('quotes'), 'followed_tags': _('followed tags'),
        'groups': _('groups'), 'filtered': _('filtered posts'),
    }.get(category, category)


def sort_name(sort_by):
    return {
        'time': _('Time'), 'replies': _('Replies'), 'likes': _('Likes'),
        'boosts': _('Boosts'), 'density': _('Density'),
        'relevance': _('Relevance'),
    }.get(sort_by, sort_by)


def sort_phrase(sort_by, order):
    ascending = order == 'asc'
    return {
        'time': (_('newest first'), _('oldest first')),
        'replies': (_('most replies first'), _('fewest replies first')),
        'likes': (_('most likes first'), _('fewest likes first')),
        'boosts': (_('most boosts first'), _('fewest boosts first')),
        'density': (_('densest first'), _('sparsest first')),
        'relevance': (_('most relevant first'), _('least relevant first')),
    }[sort_by][1 if ascending else 0]


def view_summary(state, author_name=None):
    """'Showing boosts, most likes first, grouped by authors', put
    together from parts so each can be translated."""
    parts = [category_showing(state.category)]
    if author_name:
        # Translators: part of the toast shown when the Catch Up list
        # changes, e.g. "from Alice"
        parts.append(_('from %s') % author_name)
    parts.append(sort_phrase(state.sort, state.order))
    if state.group:
        parts.append(_('grouped by authors'))
    text = parts[0]
    for part in parts[1:]:
        # Translators: joins two parts of the toast shown when the Catch
        # Up list changes, e.g. "boosts" and "most likes first"
        text = _('%(first)s, %(second)s') % {'first': text, 'second': part}
    # Translators: the toast shown when the Catch Up list changes; %s is
    # a list like "boosts, most likes first, grouped by authors"
    return _('Showing %s') % text


# --------------------------------------------------------------------------
# The fetch loop (3.1, 3.2)

@dataclasses.dataclass
class RunResult:
    posts: list
    stopped: str
    start_at: datetime.datetime | None
    end_at: datetime.datetime
    error: str = ''
    retry_after: int = 0
    evaluated: int = 0
    followed_tags: list = dataclasses.field(default_factory=list)
    relationships: dict = dataclasses.field(default_factory=dict)
    trends: dict = dataclasses.field(default_factory=dict)


class CatchUpRun:
    """One catch-up: the home timeline paged back to ``start_at``, then the
    followed tags, the relationships and the trends, handed to ``done``
    as a :class:`RunResult`.

    It holds no widget. ``progress(evaluated, oldest_created_at)`` and
    ``done(result)`` are the page's weak methods; ``abort()`` (page closed,
    account changed) cancels the one Gio.Cancellable and silences both,
    ``stop()`` (the Cancel button) ends the paging like a 429 does and
    keeps what was gathered.
    """

    def __init__(self, client, start_at, end_at, self_ids=(),
                 hide_trending=False, progress=None, done=None,
                 pause_seconds=None):
        self.client = client
        self.start_at = start_at
        self.end_at = end_at
        self.self_ids = list(self_ids)
        self.hide_trending = hide_trending
        self.progress = progress
        self.done = done
        self.pause_seconds = FETCH_PAUSE_SECONDS if pause_seconds is None \
            else pause_seconds
        self.cancellable = Gio.Cancellable()
        self.aborted = False
        self.finished = False
        self.kept = []
        self.evaluated = 0
        self.pages_done = 0
        self.oldest = None
        self.stopped = STOPPED_COMPLETE
        self.error = ''
        self.retry_after = 0
        self._seen = set()
        self._stop_requested = False
        self._busy = False
        self._timer = 0
        self._followed = []
        self._hits = {}
        self._pending_ids = []
        self._relationships = {}
        self._trends = {}
        self._trends_left = 0
        # Cursor for the next page of a Link-paginated endpoint, by path.
        # See _get.
        self._more = {}

    # Control

    def start(self):
        self._request_page({'limit': PAGE_LIMIT})

    def abort(self):
        self.aborted = True
        self.cancellable.cancel()
        self._clear_timer()

    def stop(self):
        """Stop paging and go on with what was gathered."""
        if self.aborted or self.finished or self._stop_requested:
            return
        self._stop_requested = True
        if self._timer:
            self._clear_timer()
            self._end_paging(STOPPED_CANCELLED)

    def _clear_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # Paging

    def _request_page(self, params):
        if self.aborted:
            return
        if self.client.rate_limited():
            self.retry_after = self.client.retry_after_seconds()
            self._end_paging(STOPPED_RATE_LIMITED)
            return
        self._busy = True
        self.client.request_async(
            'GET', '/timelines/home', params=params,
            callback=self._on_page, cancellable=self.cancellable)

    def _on_page(self, _client, result, _data=None):
        self._busy = False
        if self.aborted:
            return
        try:
            page, next_params, _prev = self.client.request_finish_paged(
                result)
            if not isinstance(page, list):
                raise GLib.Error('Unexpected response',
                                 'mastodon-http-error-quark', 0)
        except GLib.Error as error:
            self._on_page_error(error)
            return
        self.pages_done += 1
        self.evaluated += len(page)
        for status in page:
            created = parse_time(status.get('created_at'))
            if created is not None and (self.oldest is None
                                        or created < self.oldest):
                self.oldest = created
        items, reason = fetch_progress(page, self.start_at, len(self.kept),
                                       self.pages_done)
        for status in items:
            if status['id'] not in self._seen:
                self._seen.add(status['id'])
                self.kept.append(status)
        if self.progress is not None:
            self.progress(self.evaluated, self.oldest)
        if reason is None and self._stop_requested:
            reason = STOPPED_CANCELLED
        if reason is not None:
            self._end_paging(reason)
            return
        params = dict(next_params) if next_params else {
            'max_id': page[-1]['id']}
        params['limit'] = str(PAGE_LIMIT)
        self._wait_then(functools.partial(self._request_page, params))

    def _on_page_error(self, error):
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            if self.aborted:
                return
            self._end_paging(STOPPED_CANCELLED)
        elif error.code == HTTP_TOO_MANY_REQUESTS:
            self.retry_after = self.client.retry_after_seconds()
            self.error = error.message
            self._end_paging(STOPPED_RATE_LIMITED)
        else:
            self.error = error.message
            self._end_paging(STOPPED_ERROR)

    def _wait_then(self, func):
        """Run *func* after the pause between two pages (a source the
        run removes when it is aborted or stopped)."""

        def fire():
            self._timer = 0
            if not self.aborted:
                func()
            return GLib.SOURCE_REMOVE

        if self.pause_seconds:
            self._timer = GLib.timeout_add_seconds(self.pause_seconds, fire)
        else:
            self._timer = GLib.idle_add(fire)

    def _end_paging(self, reason):
        if self.aborted or self.finished:
            return
        self.stopped = reason
        if not self.kept:
            self._finish()
            return
        self.kept = apply_filters(self.kept, self.self_ids, 'home',
                                  self.end_at)
        self._get('/followed_tags', {'limit': 200}, self._on_followed_tags)

    # Followed tags, relationships, trends (each degrades, never fails)

    def _get(self, path, params, done):
        if self.aborted:
            return
        if self.client.rate_limited():
            done(None)
            return

        def finished(_client, result, _data=None):
            if self.aborted:
                return
            # request_finish_paged also returns the Link header's cursors.
            # A single page is all this ever asked for before, which
            # silently capped the endpoints Mastodon pages -- followed
            # tags most visibly, at 200 of them.
            try:
                data, next_params, _prev = \
                    self.client.request_finish_paged(result)
            except GLib.Error:
                data, next_params = None, None
            self._more[path] = next_params
            done(data)

        self.client.request_async('GET', path, params=params,
                                  callback=finished,
                                  cancellable=self.cancellable)

    def _on_followed_tags(self, data):
        # An account may follow more tags than one page holds, and the
        # cursor is the only record of where the next one starts.
        for tag in data or []:
            if isinstance(tag, dict) and tag.get('name'):
                self._followed.append(tag['name'])
        more = self._more.get('/followed_tags')
        if more:
            self._get('/followed_tags', more, self._on_followed_tags)
            return
        self._followed = list(dict.fromkeys(self._followed))
        self._hits = followed_tag_hits(self.kept, self._followed)
        self._pending_ids = relationship_ids(self.kept, self._hits)
        self._next_relationships()

    def _next_relationships(self):
        if not self._pending_ids:
            self._mark_and_trends()
            return
        batch = self._pending_ids[:RELATIONSHIP_BATCH]
        self._pending_ids = self._pending_ids[RELATIONSHIP_BATCH:]
        self._get('/accounts/relationships', {'id[]': batch},
                  self._on_relationships)

    def _on_relationships(self, data):
        for rel in data or []:
            if isinstance(rel, dict) and rel.get('id'):
                self._relationships[str(rel['id'])] = {
                    'following': bool(rel.get('following')),
                    'followed_by': bool(rel.get('followed_by'))}
        self._next_relationships()

    def _mark_and_trends(self):
        mark_followed_tags(self.kept, self._hits, self._relationships)
        mark_threads(self.kept)
        self._trends = {}
        if self.hide_trending:
            self._finish()
            return
        self._trends_left = len(TREND_LIMITS)
        for key, path, limit in TREND_LIMITS:
            self._get(path, {'limit': limit},
                      functools.partial(self._on_trend, key))

    def _on_trend(self, key, data):
        self._trends[key] = data if isinstance(data, list) else None
        self._trends_left -= 1
        if self._trends_left == 0:
            self._finish()

    def _finish(self):
        if self.aborted or self.finished:
            return
        self.finished = True
        self._clear_timer()
        if self.done is not None:
            self.done(RunResult(
                posts=self.kept, stopped=self.stopped,
                start_at=self.start_at, end_at=self.end_at,
                error=self.error, retry_after=self.retry_after,
                evaluated=self.evaluated, followed_tags=self._followed,
                relationships=self._relationships,
                trends=self._trends))


def make_snapshot(result, account_key, period):
    """The JSON-able snapshot of a finished run (CATCH-UP.md 7)."""
    oldest = None
    for post in result.posts:
        created = parse_time(post.get('created_at'))
        if created is not None and (oldest is None or created < oldest):
            oldest = created
    trends = result.trends or {}
    statuses = [{'id': s.get('id'), 'uri': s.get('uri')}
                for s in trends.get('statuses') or []
                if isinstance(s, dict)]
    return {
        'version': catchupstore.VERSION,
        'id': catchupstore.new_id(),
        'account': account_key,
        'start_at': iso(result.start_at),
        'end_at': iso(result.end_at),
        'period': period,
        'stopped': result.stopped,
        'count': len(result.posts),
        'oldest_at': iso(oldest),
        'posts': result.posts,
        'relationships': result.relationships,
        'followed_tags': result.followed_tags,
        'trends': {
            'links': trends.get('links'),
            'tags': trends.get('tags'),
            'statuses': statuses if trends.get('statuses') is not None
            else None,
        },
        'affinity': None,
    }


# --------------------------------------------------------------------------
# List items

_TOP_DATA = {'id': '', 'account': {}, 'content': ''}


class CatchUpItem(StatusItem):
    """A :class:`StatusItem` the list's models can filter and sort without
    calling back into Python: ``matches`` feeds a ``Gtk.BoolFilter``,
    ``sort_rank`` and ``author_rank`` two ``Gtk.NumericSorter``s. The page
    writes them when the filters change. One item per visible post; the
    Highlights slot receives the same objects."""

    __gtype_name__ = 'BabbleCatchUpItem'

    matches = GObject.Property(type=bool, default=True)
    sort_rank = GObject.Property(type=int, default=0)
    author_rank = GObject.Property(type=int, default=0)

    def __init__(self, data, top=False):
        super().__init__(data)
        self.is_top = top
        if top:
            # The block above the posts is the first item of the list, so
            # it scrolls with them and the list stays virtualized.
            self.sort_rank = -1
            self.author_rank = -1


def _plain_name(account):
    return account_store.plain_name(account)


def _peek(item):
    """One line of text for the row: the filter's reason, the content
    warning, or the start of the post."""
    raw = item.raw
    info = raw.get('_filtered')
    if info and info.get('action') != 'blur':
        titles = [t for t in info.get('titles') or () if t]
        # Translators: the row of a post hidden behind a filter; %s names
        # the filter(s)
        return _('Filtered: %s') % ', '.join(titles) if titles \
            else _('Filtered')
    status = item.status
    spoiler = (status.get('spoiler_text') or '').strip()
    if spoiler:
        # Translators: the row of a post with a content warning; %s is
        # the warning
        return _('CW: %s') % spoiler
    text = plain_text(status.get('content') or '')
    text = ' '.join(text.split())
    return text[:PEEK_CHARS]


def boosters_text(boosted_by, others):
    """'Alice boosted' / 'Alice and 2 others boosted'."""
    name = _plain_name(boosted_by)
    if not others:
        # Translators: %s is the name of the person who boosted the post
        return _('%s boosted') % name
    return ngettext('%(name)s and %(count)d other boosted',
                    '%(name)s and %(count)d others boosted',
                    len(others)) % {'name': name, 'count': len(others)}


class _Stat(Gtk.Box):
    """An icon and a count, in the row's last line."""

    def __init__(self, icon_name):
        super().__init__(spacing=4)
        self.icon = Gtk.Image(icon_name=icon_name, pixel_size=14,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.label = Gtk.Label()
        self.label.add_css_class('caption')
        self.append(self.icon)
        self.append(self.label)
        for child in (self.icon, self.label):
            child.add_css_class('dimmed')

    def set_count(self, count):
        self.label.set_label(compact_count(count))


class CatchUpRow(Gtk.Box):
    """One post of the All posts list, light on purpose: avatar, name and
    time, boosters, one line of text, up to three thumbnails, the counts.
    Rows are recycled by the list view; :meth:`bind` resets everything."""

    __gtype_name__ = 'BabbleCatchUpRow'

    def __init__(self):
        super().__init__(spacing=12, margin_top=10, margin_bottom=10,
                         margin_start=12, margin_end=12)
        self.item = None
        self.avatar = Adw.Avatar(size=40, valign=Gtk.Align.START,
                                 show_initials=True)
        self.append(self.avatar)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                         hexpand=True)
        self.append(column)

        line = Gtk.Box(spacing=6)
        self.name = Gtk.Label(xalign=0.0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.END)
        self.name.add_css_class('heading')
        self.thread_mark = Gtk.Image(
            icon_name='format-indent-more-symbolic', visible=False,
            tooltip_text=_('Thread'))
        self.thread_mark.add_css_class('dimmed')
        self.time = Gtk.Label(xalign=1.0)
        self.time.add_css_class('caption')
        self.time.add_css_class('dimmed')
        line.append(self.name)
        line.append(self.thread_mark)
        line.append(self.time)
        column.append(line)

        self.boost_line = Gtk.Label(xalign=0.0, visible=False,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.boost_line.add_css_class('caption')
        self.boost_line.add_css_class('dimmed')
        column.append(self.boost_line)

        self.peek = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END,
                              single_line_mode=True)
        column.append(self.peek)

        self.thumbs = Gtk.Box(spacing=4, visible=False)
        self.pictures = []
        for _n in range(MAX_THUMBS):
            holder = Gtk.Overlay(width_request=THUMB_SIZE,
                                 height_request=THUMB_SIZE, visible=False,
                                 overflow=Gtk.Overflow.HIDDEN)
            holder.add_css_class('card')
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER,
                                  can_shrink=True,
                                  width_request=THUMB_SIZE,
                                  height_request=THUMB_SIZE,
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION)
            picture.add_css_class('darkenable')
            holder.set_child(picture)
            hidden = Gtk.Image(icon_name='view-conceal-symbolic',
                               visible=False, pixel_size=16,
                               halign=Gtk.Align.CENTER,
                               valign=Gtk.Align.CENTER)
            holder.add_overlay(hidden)
            holder.picture = picture
            holder.hidden = hidden
            self.pictures.append(holder)
            self.thumbs.append(holder)
        column.append(self.thumbs)

        self.stats = Gtk.Box(spacing=16)
        self.replies = _Stat('mail-reply-sender-symbolic')
        self.boosts = _Stat('media-playlist-repeat-symbolic')
        self.likes = _Stat('starred-symbolic')
        for stat in (self.replies, self.boosts, self.likes):
            self.stats.append(stat)
        column.append(self.stats)

    def bind(self, item, others=()):
        self.item = item
        status = item.status
        account = status.get('account') or {}
        name = _plain_name(account)
        self.avatar.set_text(name)
        AvatarCache.load(self.avatar, account)
        self.name.set_label(name)
        self.time.set_label(relative_time(item.raw.get('created_at')))
        self.thread_mark.set_visible(bool(item.raw.get('_thread')))
        if item.boosted_by is not None:
            self.boost_line.set_label(boosters_text(item.boosted_by, others))
            self.boost_line.set_visible(True)
        else:
            self.boost_line.set_visible(False)
        peek = _peek(item)
        self.peek.set_label(peek)
        self.peek.set_visible(bool(peek))

        settings = pages.settings()
        info = item.raw.get('_filtered')
        filtered = bool(info) and info.get('action') != 'blur'
        media = [] if filtered else item.media[:MAX_THUMBS]
        cover = bool(status.get('spoiler_text')) and not \
            settings.get_boolean('show-spoilers')
        sensitive = bool(status.get('sensitive')) and not \
            settings.get_boolean('show-sensitive-media')
        hide = cover or sensitive
        for slot, holder in enumerate(self.pictures):
            if slot < len(media):
                holder.set_visible(True)
                holder.hidden.set_visible(hide)
                url = media[slot].get('preview_url') or media[slot].get('url')
                if url and not hide:
                    holder.picture.set_visible(True)
                    _TextureCache.load(url, holder.picture,
                                       cover=(THUMB_SIZE, THUMB_SIZE))
                else:
                    holder.picture.set_visible(False)
            else:
                holder.set_visible(False)
        self.thumbs.set_visible(bool(media))

        show_counts = settings.get_boolean('show-interaction-counters')
        self.stats.set_visible(show_counts)
        if show_counts:
            self.replies.set_count(status.get('replies_count') or 0)
            self.boosts.set_count(status.get('reblogs_count') or 0)
            self.likes.set_count(status.get('favourites_count') or 0)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ['%s. %s' % (name, peek) if peek else name])

    def unbind(self):
        self.item = None
        for holder in self.pictures:
            holder.picture.set_paintable(None)
        self.avatar.set_custom_image(None)


class PostsBar(Gtk.DrawingArea):
    """One dot per post in time order, or above 160 posts one column per
    time bucket. What matches the list's filters is drawn in the accent
    colour, the rest dimmed (CATCH-UP.md 2.4)."""

    __gtype_name__ = 'BabblePostsBar'

    def __init__(self):
        super().__init__(content_height=36, hexpand=True)
        self.dots = []
        self.bins = None
        self.matching = None
        self.set_draw_func(_draw_posts_bar)
        self._describe()

    def set_data(self, dots, bins):
        self.dots = dots or []
        self.bins = bins
        self.matching = None
        self._describe()
        self.queue_draw()

    def set_matching(self, matching):
        """Highlight the posts whose id is in *matching* (None: all)."""
        self.matching = matching
        self._describe()
        self.queue_draw()

    def total(self):
        if self.bins is not None:
            return sum(len(b) for b in self.bins)
        return len(self.dots)

    def _describe(self):
        total = self.total()
        shown = total if self.matching is None else sum(
            1 for ids in (self.bins if self.bins is not None
                          else [self.dots]) for i in ids
            if i in self.matching)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_('Posts over time'),
             _('%(shown)s of %(total)s') % {
                 'shown': posts_phrase(shown), 'total': total}])


def _draw_posts_bar(area, cr, width, height):
    text = area.get_color()
    accent = Adw.StyleManager.get_default().get_accent_color_rgba()
    matching = area.matching

    def paint(color, alpha):
        cr.set_source_rgba(color.red, color.green, color.blue, alpha)

    if area.bins is not None:
        bins = area.bins
        peak = max((len(b) for b in bins), default=0)
        if not peak:
            return
        step = width / len(bins)
        for index, ids in enumerate(bins):
            if not ids:
                continue
            tall = max(2.0, (height - 2) * len(ids) / peak)
            left = index * step
            paint(text, 0.25)
            cr.rectangle(left, height - tall, max(step - 0.5, 1), tall)
            cr.fill()
            hit = len(ids) if matching is None else sum(
                1 for i in ids if i in matching)
            if hit:
                part = max(2.0, tall * hit / len(ids))
                paint(accent, 1.0)
                cr.rectangle(left, height - part, max(step - 0.5, 1), part)
                cr.fill()
        return
    dots = area.dots
    if not dots:
        return
    step = width / len(dots)
    radius = min(4.0, max(1.0, step * 0.4))
    for index, post_id in enumerate(dots):
        if matching is None or post_id in matching:
            paint(accent, 1.0)
        else:
            paint(text, 0.25)
        cr.arc((index + 0.5) * step, height / 2, radius, 0, 2 * math.pi)
        cr.fill()


class _AuthorChip(Gtk.ToggleButton):
    """Avatar and post count of one author, in the chips row."""

    __gtype_name__ = 'BabbleAuthorChip'

    def __init__(self):
        super().__init__()
        box = Gtk.Box(spacing=6)
        self.avatar = Adw.Avatar(size=24, show_initials=True)
        self.count = Gtk.Label()
        self.count.add_css_class('caption-heading')
        box.append(self.avatar)
        box.append(self.count)
        self.set_child(box)
        self.account_id = None

    def show_author(self, account, count):
        name = _plain_name(account)
        self.account_id = str(account.get('id', ''))
        self.avatar.set_text(name)
        AvatarCache.load(self.avatar, account)
        self.count.set_label(str(count))
        self.set_tooltip_text(name)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ['%s, %s' % (name, posts_phrase(count))])


# --------------------------------------------------------------------------
# The page

class _Slot(Adw.Bin):
    """What a list item holds: the block above the posts, or a row. A row
    is kept for the next post this slot is bound to."""

    def __init__(self):
        super().__init__()
        self.row = None


class CatchUpPage(Adw.Bin):
    """The Catch Up place (CATCH-UP.md 2): first run, scanning, results."""

    __gtype_name__ = 'BabbleCatchUpPage'

    def __init__(self, client, account=None):
        super().__init__()
        self.client = client
        self.account = account or {}
        if account:
            self.account_key = account_store.account_key(account)
        else:
            self.account_key = '%s#' % getattr(client, 'instance', '')
        self.state = 'loading'
        self._disposed = False
        self._generation = 0
        self._metas = []
        self._snapshot_id = None
        self._save_failed = False
        self._period = DEFAULT_PERIOD
        self._run = None
        self._run_period = DEFAULT_PERIOD
        self._computed = None
        self._items = {}
        self._matching = set()
        self._grouped = False
        self._view = None
        self._view_states = {}
        self._view_state = ViewState()
        self._syncing = False
        self._menu_category = None
        self._menu_for = None
        self._author_ids = [None]
        self._building = None
        self._idles = set()
        self._toast_id = 0
        self._toast_text = ''
        self._menu_buttons = []
        self._period_buttons = []
        self._highlights = None
        self._highlights_tried = False
        self._results_built = False

        self._actions = Gio.SimpleActionGroup()
        self._period_action = Gio.SimpleAction.new_stateful(
            'period', GLib.VariantType.new('s'),
            GLib.Variant('s', DEFAULT_PERIOD))
        pages.connect_weak(self._period_action, 'change-state',
                           self._on_period_changed)
        self._actions.add_action(self._period_action)
        for name, handler in (('show', self._on_show_snapshot),
                              ('remove', self._on_remove_snapshot)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            pages.connect_weak(action, 'activate', handler)
            self._actions.add_action(action)
        self.insert_action_group('catchup', self._actions)

        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self.stack.add_named(pages.make_loading_page(_('Loading')),
                             'loading')
        self.stack.add_named(self._build_first_run(), 'first-run')
        self.stack.add_named(self._build_scanning(), 'scanning')
        self.error_page = pages.make_error_page(_('Could Not Catch Up'),
                                                self._on_retry)
        self.stack.add_named(self.error_page, 'error')
        self._add_keys()

        self._load_snapshots()

    # ----------------------------------------------------------------------
    # Lifetime

    def do_dispose(self):
        self.cancel()
        Adw.Bin.do_dispose(self)

    def cancel(self):
        """Stop everything in flight (the page is closed or the account
        changed); nothing more is saved or shown."""
        self._disposed = True
        self._generation += 1
        if self._run is not None:
            self._run.abort()
            self._run = None
        for source in list(self._idles):
            GLib.source_remove(source)
        self._idles.clear()
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        self._building = None

    def _idle(self, method):
        """Call *method* once from an idle source below GDK's redraw. The
        source is removed when the page is cancelled, and holds the page
        weakly, so a page dropped meanwhile is not kept alive."""
        idles = self._idles
        call = pages.weak_method(method)
        holder = []

        def run():
            idles.discard(holder[0])
            call()
            return GLib.SOURCE_REMOVE

        source = GLib.idle_add(run, priority=GLib.PRIORITY_DEFAULT_IDLE)
        holder.append(source)
        idles.add(source)

    def _set_state(self, name):
        self.state = name
        self.stack.set_visible_child_name(name)

    # ----------------------------------------------------------------------
    # Static pages

    def _build_first_run(self):
        page = Adw.StatusPage(
            icon_name='babble-catch-up-symbolic', title=_('Catch Up'),
            description=_('A separate timeline for your followings, '
                          'offering a high-level view at a glance'))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)
        box.append(self._make_period_button())
        button = Gtk.Button(label=_('_Catch Up'), use_underline=True,
                            halign=Gtk.Align.CENTER)
        button.add_css_class('suggested-action')
        button.add_css_class('pill')
        pages.connect_weak(button, 'clicked', self._on_first_run_clicked)
        box.append(button)
        page.set_child(box)
        self.first_run_page = page
        self.catch_up_button = button
        return page

    def _build_scanning(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(width_request=48, height_request=48,
                                   halign=Gtk.Align.CENTER)
        self.scan_label = Gtk.Label(label=_('Scanning timeline…'),
                                    wrap=True, justify=Gtk.Justification.CENTER)
        self.scan_label.add_css_class('title-3')
        self.cancel_button = Gtk.Button(label=_('_Cancel'),
                                        use_underline=True,
                                        halign=Gtk.Align.CENTER)
        self.cancel_button.add_css_class('pill')
        pages.connect_weak(self.cancel_button, 'clicked',
                           self._on_cancel_clicked)
        box.append(self.spinner)
        box.append(self.scan_label)
        box.append(self.cancel_button)
        return box

    # ----------------------------------------------------------------------
    # The period menu (2.2)

    def _period_choices(self):
        choices = ['1h', '3h', '6h', '12h', '24h']
        if self._metas:
            choices.append('since')
        choices.append('max')
        return choices

    def _make_period_button(self):
        button = Gtk.MenuButton(always_show_arrow=True,
                                tooltip_text=_('Time Range'),
                                valign=Gtk.Align.CENTER)
        button.set_label(period_label(self._period))
        self._period_buttons.append(button)
        self._fill_period_popover(button)
        return button

    def _fill_period_popover(self, button):
        menu = Gio.Menu()
        periods = Gio.Menu()
        for period in self._period_choices():
            item = Gio.MenuItem.new(period_label(period), None)
            item.set_action_and_target_value(
                'catchup.period', GLib.Variant('s', period))
            periods.append_item(item)
        menu.append_section(None, periods)
        if self._metas:
            previous = Gio.Menu()
            for meta in self._metas:
                item = Gio.MenuItem.new(None, None)
                item.set_attribute_value(
                    'custom', GLib.Variant('s', 'snapshot:' + meta['id']))
                previous.append_item(item)
            menu.append_section(_('Previous catch-ups'), previous)
        footer = Gio.Menu()
        item = Gio.MenuItem.new(None, None)
        item.set_attribute_value('custom', GLib.Variant('s', 'footnote'))
        footer.append_item(item)
        menu.append_section(None, footer)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        for meta in self._metas:
            popover.add_child(self._snapshot_entry(meta),
                              'snapshot:' + meta['id'])
        note = Gtk.Label(
            label=_('Your server might only show a maximum of 800 posts '
                    'in the Home timeline regardless of the time range.'),
            wrap=True, max_width_chars=34, xalign=0.0, margin_start=12,
            margin_end=12, margin_top=6, margin_bottom=6, sensitive=False)
        note.add_css_class('caption')
        popover.add_child(note, 'footnote')
        button.set_popover(popover)

    def _snapshot_entry(self, meta):
        box = Gtk.Box(spacing=0)
        show = Gtk.Button(hexpand=True)
        show.add_css_class('flat')
        label = Gtk.Label(label=snapshot_label(meta), xalign=0.0,
                          wrap=True, max_width_chars=30)
        show.set_child(label)
        show.set_action_name('catchup.show')
        show.set_action_target_value(GLib.Variant('s', meta['id']))
        remove = Gtk.Button(icon_name='user-trash-symbolic',
                            tooltip_text=_('Remove This Catch-Up'),
                            valign=Gtk.Align.CENTER)
        remove.add_css_class('flat')
        remove.update_property([Gtk.AccessibleProperty.LABEL],
                               [_('Remove This Catch-Up')])
        remove.set_action_name('catchup.remove')
        remove.set_action_target_value(GLib.Variant('s', meta['id']))
        show.set_sensitive(meta['id'] != self._snapshot_id)
        box.append(show)
        box.append(remove)
        return box

    def _refresh_period_menus(self):
        for button in self._period_buttons:
            self._fill_period_popover(button)
            button.set_label(period_label(self._period))
        self._period_action.set_state(GLib.Variant('s', self._period))

    def _popdown_menus(self):
        for button in self._period_buttons:
            button.popdown()

    def _on_period_changed(self, action, value):
        period = value.get_string()
        if self._run is not None or self.state in ('loading', 'scanning'):
            return
        if self.state == 'results' and period == self._period:
            return                        # the period already shown
        if period == 'since' and not self._metas:
            return
        action.set_state(value)
        self._period = period
        self._refresh_period_menus()
        if self.state in ('results', 'error'):
            self._start_run(period)

    def _on_first_run_clicked(self, _button):
        self._start_run(self._period)

    def _on_retry(self, _button):
        self._start_run(self._run_period)

    def _on_cancel_clicked(self, _button):
        if self._run is not None:
            self.cancel_button.set_sensitive(False)
            self._run.stop()

    def _on_show_snapshot(self, _action, parameter):
        self._popdown_menus()
        snapshot_id = parameter.get_string()
        if snapshot_id == self._snapshot_id or self._run is not None:
            return
        self._open_snapshot(snapshot_id)

    def _on_remove_snapshot(self, _action, parameter):
        self._popdown_menus()
        pages.confirm(
            self, _('Remove This Catch-Up?'),
            _('It will be deleted from this device.'), _('_Remove'),
            functools.partial(pages.weak_method(self._remove_snapshot),
                              parameter.get_string()))

    def _remove_snapshot(self, snapshot_id):
        if self._disposed:
            return
        catchupstore.delete_snapshot(self.account_key, snapshot_id)
        self._view_states.pop(snapshot_id, None)
        self._metas = [m for m in self._metas if m['id'] != snapshot_id]
        if snapshot_id != self._snapshot_id:
            self._refresh_period_menus()
            return
        self._snapshot_id = None
        if self._metas:
            self._open_snapshot(self._metas[0]['id'])
        else:
            self._computed = None
            self._period = DEFAULT_PERIOD
            self._refresh_period_menus()
            self._set_state('first-run')

    # ----------------------------------------------------------------------
    # Loading snapshots from disk

    def _languages(self):
        languages = [ui_language()]
        language = (self.account.get('source') or {}).get('language')
        if language and language.lower() not in languages:
            languages.append(language.lower())
        return languages

    def _load_snapshots(self):
        key = self.account_key
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        generation = self._generation

        def work():
            found = catchupstore.list_snapshots(key)
            for path, _meta in found:
                try:
                    snapshot = catchupstore.read_snapshot(path)
                except (OSError, ValueError):
                    continue
                return ([m for _p, m in found],
                        compute(snapshot, languages, hide))
            return ([m for _p, m in found], None)

        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_snapshots_loaded), generation))

    def _on_snapshots_loaded(self, generation, value, error):
        if self._disposed or generation != self._generation:
            return
        if error is not None or value is None:
            self._metas = []
            self._set_state('first-run')
            return
        self._metas, computed = value
        if computed is None:
            self._refresh_period_menus()
            self._set_state('first-run')
            return
        self._begin_present(computed)

    def _open_snapshot(self, snapshot_id):
        meta = next((m for m in self._metas if m['id'] == snapshot_id), None)
        if meta is None:
            return
        path = catchupstore.snapshot_path(self.account_key, snapshot_id)
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        self._generation += 1
        generation = self._generation

        def work():
            return compute(catchupstore.read_snapshot(path), languages, hide)

        self._scan_label(_('Opening the catch-up…'))
        self._set_state('scanning')
        self.cancel_button.set_visible(False)
        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_snapshot_opened), generation,
            snapshot_id))

    def _on_snapshot_opened(self, generation, snapshot_id, computed, error):
        if self._disposed or generation != self._generation:
            return
        self._save_failed = False
        if error is not None:
            # Drop the snapshot that would not open, not the one on
            # screen, which is still readable.
            self._metas = [m for m in self._metas
                           if m['id'] != snapshot_id]
            self._show_error(str(error))
            return
        self._begin_present(computed)

    # ----------------------------------------------------------------------
    # Running a catch-up (3.1)

    def _scan_label(self, text):
        self.scan_label.set_label(text)

    def _start_run(self, period):
        if self._run is not None or self._disposed or self.client is None:
            return
        end_at = now_utc()
        start_at = None
        hours = PERIOD_HOURS.get(period)
        if hours:
            start_at = end_at - datetime.timedelta(hours=hours)
        elif period == 'since':
            start_at = parse_time(self._metas[0].get('end_at')) \
                if self._metas else None
            if start_at is None:
                period = DEFAULT_PERIOD
                start_at = end_at - datetime.timedelta(hours=24)
        self._run_period = period
        self._period = period
        self._generation += 1
        self._popdown_menus()
        self._run = CatchUpRun(
            self.client, start_at, end_at,
            self_ids=[self.account.get('id')] if self.account.get('id')
            else [],
            hide_trending=pages.settings().get_boolean('hide-trending'),
            progress=pages.weak_method(self._on_progress),
            done=pages.weak_method(self._on_run_done))
        self._scan_label(_('Scanning timeline…'))
        self.cancel_button.set_visible(True)
        self.cancel_button.set_sensitive(True)
        self._set_state('scanning')
        self._run.start()

    def _on_progress(self, evaluated, oldest):
        if self._disposed or self._run is None:
            return
        hours = hours_between(self._run.end_at, oldest)
        # Translators: shown while Catch Up reads the home timeline;
        # "h" is hours, as in "8 h back"
        self._scan_label(ngettext(
            'Scanning timeline… %(count)d post evaluated (%(hours)d h back)',
            'Scanning timeline… %(count)d posts evaluated '
            '(%(hours)d h back)', evaluated)
            % {'count': evaluated, 'hours': hours})

    def _on_run_done(self, result):
        run, self._run = self._run, None
        if self._disposed or run is None:
            return
        if not result.posts and result.stopped in (STOPPED_ERROR,
                                                   STOPPED_RATE_LIMITED):
            self._show_error(result.error)
            return
        snapshot = make_snapshot(result, self.account_key, self._run_period)
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        self._scan_label(_('Preparing your catch-up…'))
        self.cancel_button.set_visible(False)
        generation = self._generation

        def work():
            saved = True
            try:
                catchupstore.write_snapshot(snapshot)
            except OSError:
                saved = False
            metas = catchupstore.prune(snapshot['account']) if saved \
                else None
            return (saved, metas, compute(snapshot, languages, hide))

        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_run_saved), generation))

    def _on_run_saved(self, generation, value, error):
        if self._disposed or generation != self._generation:
            return
        if error is not None:
            self._show_error(str(error))
            return
        saved, metas, computed = value
        # A catch-up that could not be written is still worth showing;
        # the banner says it will not come back.
        self._save_failed = not saved
        if metas is not None:
            self._metas = metas
        self._begin_present(computed)

    def _show_error(self, message):
        if message:
            self.error_page.set_description(summarize_error_markup(message))
        else:
            self.error_page.set_description('')
        self._set_state('error')

    # ----------------------------------------------------------------------
    # Building the items, a chunk per idle

    def _begin_present(self, computed):
        self._generation += 1
        self._building = {'computed': computed, 'at': 0, 'items': {},
                          'generation': self._generation}
        self._scan_label(_('Preparing your catch-up…'))
        self.cancel_button.set_visible(False)
        if self.state not in ('scanning',):
            self._set_state('scanning')
        self._idle(self._build_chunk)

    def _build_chunk(self):
        building = self._building
        if building is None or self._disposed \
                or building['generation'] != self._generation:
            return
        visible = building['computed'].visible
        end = min(building['at'] + BUILD_CHUNK, len(visible))
        items = building['items']
        for post in visible[building['at']:end]:
            items[post['id']] = CatchUpItem(post)
        building['at'] = end
        if end < len(visible):
            self._idle(self._build_chunk)
            return
        self._building = None
        self._present(building['computed'], items)

    # ----------------------------------------------------------------------
    # The results

    def _ensure_results(self):
        if self._results_built:
            return
        self._results_built = True
        self.store = Gio.ListStore.new(CatchUpItem)
        self.top_item = CatchUpItem(_TOP_DATA, top=True)
        item_type = CatchUpItem.__gtype__
        self.filter = Gtk.BoolFilter.new(
            Gtk.PropertyExpression.new(item_type, None, 'matches'))
        self.filtered = Gtk.FilterListModel.new(self.store, self.filter)
        self.sorter = Gtk.NumericSorter.new(
            Gtk.PropertyExpression.new(item_type, None, 'sort-rank'))
        self.section_sorter = Gtk.NumericSorter.new(
            Gtk.PropertyExpression.new(item_type, None, 'author-rank'))
        self.sorted = Gtk.SortListModel.new(self.filtered, self.sorter)

        factory = Gtk.SignalListItemFactory()
        pages.connect_weak(factory, 'setup', self._on_item_setup)
        pages.connect_weak(factory, 'bind', self._on_item_bind)
        pages.connect_weak(factory, 'unbind', self._on_item_unbind)
        headers = Gtk.SignalListItemFactory()
        pages.connect_weak(headers, 'setup', self._on_header_setup)
        pages.connect_weak(headers, 'bind', self._on_header_bind)
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection.new(self.sorted), factory=factory,
            header_factory=headers, single_click_activate=True,
            show_separators=True)
        self.list_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Posts')])
        pages.connect_weak(self.list_view, 'activate',
                           self._on_row_activated)

        self._build_top()
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scrolled.set_child(clamp)
        self.stack.add_named(self.scrolled, 'results')

    # The block above the posts

    def _build_top(self):
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=6, margin_start=12,
                      margin_end=12)
        self.top = top

        header = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        hexpand=True)
        self.title_label = Gtk.Label(xalign=0.0, wrap=True)
        self.title_label.add_css_class('title-1')
        self.subtitle_label = Gtk.Label(
            xalign=0.0, wrap=True,
            label=_('The most relevant posts from your network, chosen by '
                    'an algorithm that weighs engagement, reach and '
                    'relevance.'))
        self.subtitle_label.add_css_class('dimmed')
        texts.append(self.title_label)
        texts.append(self.subtitle_label)
        header.append(texts)
        controls = Gtk.Box(spacing=6, valign=Gtk.Align.START)
        controls.append(self._make_period_button())
        self.options_button = Gtk.MenuButton(
            icon_name='babble-filter-symbolic',
            tooltip_text=_('List Options'), valign=Gtk.Align.CENTER)
        self.options_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('List Options')])
        self.options_button.set_popover(self._build_options())
        controls.append(self.options_button)
        header.append(controls)
        top.append(header)

        self.note_label = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        self.note_label.add_css_class('dimmed')
        top.append(self.note_label)
        self.low_banner = Adw.Banner(revealed=False)
        self.low_banner.set_action_name('catchup.period')
        self.low_banner.set_action_target_value(
            GLib.Variant('s', LOW_ACTIVITY_PERIOD))
        top.append(self.low_banner)
        self.limit_banner = Adw.Banner(revealed=False)
        top.append(self.limit_banner)
        self.save_banner = Adw.Banner(revealed=False)
        self.save_banner.set_title(_(
            'This catch-up could not be saved, so it will not be here '
            'next time.'))
        top.append(self.save_banner)

        self.highlights_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(self.highlights_slot)

        self.all_heading = Gtk.Label(label=_('All posts'), xalign=0.0,
                                     margin_top=12)
        self.all_heading.add_css_class('title-2')
        top.append(self.all_heading)
        self.bar = PostsBar()
        top.append(self.bar)
        self.chips_box = Gtk.Box(spacing=6)
        self.chips = []
        chips_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True)
        chips_scroll.set_child(self.chips_box)
        self.chips_scroll = chips_scroll
        top.append(chips_scroll)
        self.empty_label = Gtk.Label(label=_('No posts match these filters'),
                                     visible=False, margin_top=12,
                                     margin_bottom=12)
        self.empty_label.add_css_class('dimmed')
        top.append(self.empty_label)

    def _build_options(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12, width_request=320)

        def heading(text):
            label = Gtk.Label(label=text, xalign=0.0)
            label.add_css_class('caption-heading')
            return label

        box.append(heading(_('Category')))
        self.category_group = Adw.ToggleGroup()
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True)
        scroll.set_child(self.category_group)
        box.append(scroll)
        pages.connect_weak(self.category_group, 'notify::active-name',
                           self._on_category_changed)

        box.append(heading(_('Author')))
        self.author_model = Gtk.StringList()
        self.author_dropdown = Gtk.DropDown(model=self.author_model,
                                            enable_search=True)
        self.author_dropdown.set_expression(Gtk.PropertyExpression.new(
            Gtk.StringObject.__gtype__, None, 'string'))
        pages.connect_weak(self.author_dropdown, 'notify::selected',
                           self._on_author_changed)
        box.append(self.author_dropdown)

        box.append(heading(_('Sort by')))
        sort_row = Gtk.Box(spacing=6)
        self.sort_dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([sort_name(s) for s in SORTS]),
            hexpand=True)
        pages.connect_weak(self.sort_dropdown, 'notify::selected',
                           self._on_sort_changed)
        sort_row.append(self.sort_dropdown)
        self.order_group = Adw.ToggleGroup()
        ascending = Adw.Toggle(name='asc',
                               icon_name='view-sort-ascending-symbolic',
                               tooltip=_('Ascending'))
        descending = Adw.Toggle(name='desc',
                                icon_name='view-sort-descending-symbolic',
                                tooltip=_('Descending'))
        self.order_group.add(ascending)
        self.order_group.add(descending)
        pages.connect_weak(self.order_group, 'notify::active-name',
                           self._on_order_changed)
        sort_row.append(self.order_group)
        box.append(sort_row)

        group_row = Gtk.Box(spacing=12)
        group_label = Gtk.Label(label=_('Group by author'), xalign=0.0,
                                hexpand=True)
        self.group_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.group_switch.update_property([Gtk.AccessibleProperty.LABEL],
                                          [_('Group by author')])
        pages.connect_weak(self.group_switch, 'notify::active',
                           self._on_group_changed)
        group_row.append(group_label)
        group_row.append(self.group_switch)
        box.append(group_row)

        self.reset_button = Gtk.Button(label=_('_Reset Filters'),
                                       use_underline=True)
        pages.connect_weak(self.reset_button, 'clicked', self._on_reset)
        box.append(self.reset_button)
        popover.set_child(box)
        return popover

    # List item factories

    def _on_item_setup(self, _factory, list_item):
        list_item.set_child(_Slot())

    def _on_item_bind(self, _factory, list_item):
        item = list_item.get_item()
        slot = list_item.get_child()
        if item.is_top:
            list_item.set_activatable(False)
            list_item.set_focusable(False)
            holder = self.top.get_parent()
            if holder is not None and holder is not slot:
                holder.set_child(None)
            slot.set_child(self.top)
            return
        list_item.set_activatable(True)
        list_item.set_focusable(True)
        if slot.row is None:
            slot.row = CatchUpRow()
        slot.set_child(slot.row)
        others = self._computed.boosters.get(item.raw['id'], ()) \
            if self._computed is not None else ()
        slot.row.bind(item, others)

    def _on_item_unbind(self, _factory, list_item):
        item = list_item.get_item()
        slot = list_item.get_child()
        if item is not None and item.is_top:
            if self.top.get_parent() is slot:
                slot.set_child(None)
        elif slot.row is not None:
            slot.row.unbind()

    def _on_header_setup(self, _factory, header):
        box = Gtk.Box(spacing=8, margin_top=10, margin_bottom=4,
                      margin_start=12, margin_end=12)
        avatar = Adw.Avatar(size=28, show_initials=True)
        name = Gtk.Label(xalign=0.0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('heading')
        count = Gtk.Label()
        count.add_css_class('caption')
        count.add_css_class('dimmed')
        for child in (avatar, name, count):
            box.append(child)
        box.avatar, box.name, box.count = avatar, name, count
        header.set_child(box)

    def _on_header_bind(self, _factory, header):
        item = header.get_item()
        box = header.get_child()
        if item is None or item.is_top:
            box.set_visible(False)
            return
        box.set_visible(True)
        account = item.status.get('account') or {}
        name = _plain_name(account)
        box.avatar.set_text(name)
        AvatarCache.load(box.avatar, account)
        box.name.set_label(name)
        box.count.set_label(posts_phrase(header.get_n_items()))

    def _on_row_activated(self, _view, position):
        item = self.sorted.get_item(position)
        if item is None or item.is_top:
            return
        pages.open_status(self, self.client, item.raw)

    # ----------------------------------------------------------------------
    # Presenting a snapshot

    def _begin_period_state(self, snapshot):
        self._snapshot_id = snapshot.get('id')
        period = snapshot.get('period')
        self._period = period if period in PERIOD_HOURS or period in (
            'since', 'max') else DEFAULT_PERIOD

    def _present(self, computed, items):
        self._ensure_results()
        snapshot = computed.snapshot
        self._computed = computed
        self._items = items
        self._begin_period_state(snapshot)
        self._view_state = dataclasses.replace(
            self._view_states.get(snapshot.get('id'), ViewState()))
        self._matching = set(items)         # new items match by default
        self._grouped = False
        self._menu_category = None
        self._rebuild_category_toggles()
        self._apply_view()
        entries = [self.top_item] + list(items.values())
        self.store.splice(0, self.store.get_n_items(), entries)
        self._refresh_period_menus()
        self._update_header()
        self.bar.set_data(computed.bar_dots, computed.bar_bins)
        self._sync_bar()
        self._present_highlights(computed)
        self._set_state('results')

    def _present_highlights(self, computed):
        if not self._highlights_tried:
            self._highlights_tried = True
            from .catchupgrid import HighlightsView
            self._highlights = HighlightsView(self.client)
            self.highlights_slot.append(self._highlights)
            self._highlights.on_pages_changed = pages.weak_method(
                self._on_pages_changed)
        if self._highlights is None:
            return
        self._highlights.set_digest(Digest(
            posts=computed.ranked, scores=computed.scores,
            items=self._items, news=computed.news, tags=computed.tags,
            trending_keys=computed.trending_keys,
            boosters=computed.boosters, end_at=computed.end_at),
            pages=self._view_state.pages)

    def _on_pages_changed(self, pages):
        """The user expanded the Highlights with "Show More"; remember how
        far, so reopening this snapshot starts where they left it (7)."""
        if self._snapshot_id is None:
            return
        # Both: _apply_view copies the live state back over the stored one,
        # so updating only the dict would be undone on the next change.
        self._view_state.pages = pages
        self._view_states[self._snapshot_id] = dataclasses.replace(
            self._view_state)

    def _update_header(self):
        computed = self._computed
        snapshot = computed.snapshot
        period = self._period
        self.title_label.set_label(period_title(period))
        end_at = computed.end_at
        stopped = snapshot.get('stopped') or STOPPED_COMPLETE
        count = snapshot.get('count') or len(snapshot.get('posts') or ())
        oldest = parse_time(snapshot.get('oldest_at'))
        covered = hours_between(end_at, oldest)

        note = ''
        if stopped != STOPPED_COMPLETE:
            # Translators: shown under the Catch Up title when it could not
            # go back as far as asked; %(range)s is like "the last 7 hours"
            # and %(posts)s like "400 posts"
            note = _('Covers %(range)s (%(posts)s)') % {
                'range': hours_phrase(covered), 'posts': posts_phrase(count)}
        self.note_label.set_label(note)
        self.note_label.set_visible(bool(note))

        low = stopped == STOPPED_COMPLETE and count < LOW_ACTIVITY_POSTS
        if low:
            hours = PERIOD_HOURS.get(period)
            if hours:
                range_text = hours_phrase(hours)
            elif period == 'since':
                range_text = _('since your last catch-up')
            else:
                range_text = _('your timeline')
            # Translators: %(range)s is like "the last 6 hours"
            self.low_banner.set_title(ngettext(
                'Only %(count)d post in %(range)s',
                'Only %(count)d posts in %(range)s', count)
                % {'count': count, 'range': range_text})
            can_widen = period != LOW_ACTIVITY_PERIOD and period != 'max'
            self.low_banner.set_button_label(
                _('Catch up on 24 hours') if can_widen else '')
        self.low_banner.set_revealed(low)

        limited = stopped == STOPPED_RATE_LIMITED
        if limited:
            client = self.client
            wait = client.retry_after_seconds()
            if wait > 0:
                minutes = max(1, int(math.ceil(wait / 60)))
                self.limit_banner.set_title(ngettext(
                    'The server is limiting requests, so this catch-up is '
                    'incomplete. Try again in %d minute.',
                    'The server is limiting requests, so this catch-up is '
                    'incomplete. Try again in %d minutes.', minutes)
                    % minutes)
            else:
                self.limit_banner.set_title(_(
                    'The server limited requests, so this catch-up is '
                    'incomplete.'))
        self.limit_banner.set_revealed(limited)
        self.save_banner.set_revealed(self._save_failed)
        self.options_button.set_sensitive(count > 0)

    # ----------------------------------------------------------------------
    # Controls <-> view state

    def _rebuild_category_toggles(self):
        counts = self._computed.counts
        self._syncing = True
        self.category_group.remove_all()
        for category in ('all',) + tuple(
                c for c in CATEGORIES if c != 'filtered') + ('filtered',):
            total = len(self._computed.visible) if category == 'all' \
                else counts.get(category, 0)
            if category in ('groups', 'quotes', 'followed_tags',
                            'filtered') and not total:
                continue
            # Translators: a category of the Catch Up list and how many
            # posts it holds, e.g. "Boosts (12)"
            label = _('%(category)s (%(count)d)') % {
                'category': category_name(category), 'count': total}
            self.category_group.add(Adw.Toggle(
                name=category, label=label, enabled=bool(total)))
        self._syncing = False

    def _sync_controls(self):
        state = self._view_state
        self._syncing = True
        known = {self.category_group.get_toggle(i).get_name()
                 for i in range(self.category_group.get_n_toggles())}
        self.category_group.set_active_name(
            state.category if state.category in known else 'all')
        self.sort_dropdown.set_selected(SORTS.index(state.sort))
        self.order_group.set_active_name(state.order)
        self.group_switch.set_active(state.group)
        self._sync_author_menu()
        index = self._author_ids.index(state.author) \
            if state.author in self._author_ids else 0
        self.author_dropdown.set_selected(index)
        for chip in self.chips:
            chip.set_active(chip.account_id == state.author)
        self.reset_button.set_sensitive(not state.is_default())
        self._syncing = False

    def _sync_author_menu(self):
        """The author dropdown and the chips list the authors of the
        category shown, most active first."""
        view = self._view
        if self._menu_category == self._view_state.category \
                and self._menu_for is self._computed:
            return
        self._menu_category = self._view_state.category
        self._menu_for = self._computed
        accounts = self._computed.accounts
        ids = [a for a in view.category_authors if a in accounts]
        self._author_ids = [None] + ids
        names = [_('All authors')] + [
            _('%(name)s (%(count)d)') % {
                'name': _plain_name(accounts[a]),
                'count': view.category_counts.get(a, 0)} for a in ids]
        self.author_model.splice(0, self.author_model.get_n_items(), names)
        shown = ids[:MAX_CHIPS]
        while len(self.chips) < len(shown):
            chip = _AuthorChip()
            pages.connect_weak(chip, 'toggled', self._on_chip_toggled)
            self.chips.append(chip)
            self.chips_box.append(chip)
        for chip in self.chips:
            chip.set_visible(False)
        for chip, author in zip(self.chips, shown, strict=False):
            chip.show_author(accounts[author], view.category_counts[author])
            chip.set_visible(True)
        self.chips_scroll.set_visible(bool(shown))

    def _apply_view(self, announce=False):
        state = self._view_state
        view = derive_view(self._computed, state)
        state.author = view.author
        self._view = view
        items = self._items
        matching = view.matching
        previous = self._matching
        for post_id in previous - matching:
            items[post_id].matches = False
        for post_id in matching - previous:
            items[post_id].matches = True
        changed = previous != matching
        self._matching = matching
        for rank, post_id in enumerate(view.order):
            item = items[post_id]
            if item.sort_rank != rank:
                item.sort_rank = rank
        if state.group:
            for post_id in view.order:
                rank = view.author_rank.get(
                    self._computed.info[post_id].author, 0)
                item = items[post_id]
                if item.author_rank != rank:
                    item.author_rank = rank
        if changed:
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        # Sorter.changed() did not make the sort model sort again once its
        # keys had been read; putting the sorters back does.
        self.sorted.set_sorter(None)
        self.sorted.set_sorter(self.sorter)
        self.sorted.set_section_sorter(None)
        if state.group:
            self.sorted.set_section_sorter(self.section_sorter)
        self._grouped = state.group
        self._sync_controls()
        self.empty_label.set_visible(not matching)
        self._sync_bar()
        self._view_states[self._snapshot_id] = dataclasses.replace(state)
        if announce:
            self._schedule_toast()

    def _sync_bar(self):
        if self._view is None:
            return
        matching = set(self._view.matching)
        # A hidden duplicate boost counts for the boost it collapsed into.
        for hidden, kept in self._computed.dup_of.items():
            if kept in matching:
                matching.add(hidden)
        self.bar.set_matching(None if self._view_state.is_default()
                              else matching)

    def _schedule_toast(self):
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(
            TOAST_DELAY_MS, _toast_tick, self.weak_ref())

    def _show_view_toast(self):
        self._toast_id = 0
        author = None
        if self._view_state.author is not None:
            account = self._computed.accounts.get(self._view_state.author)
            author = _plain_name(account) if account else None
        self._toast_text = view_summary(self._view_state, author)
        pages.show_toast(self, self._toast_text)

    # Handlers

    def _on_category_changed(self, group, _pspec):
        if self._syncing:
            return
        category = group.get_active_name() or 'all'
        if category == self._view_state.category:
            return
        self._view_state.category = category
        self._apply_view(announce=True)

    def _on_author_changed(self, dropdown, _pspec):
        if self._syncing:
            return
        index = dropdown.get_selected()
        author = self._author_ids[index] \
            if 0 <= index < len(self._author_ids) else None
        self._set_author(author)

    def _on_chip_toggled(self, chip):
        if self._syncing:
            return
        self._set_author(chip.account_id if chip.get_active() else None)

    def _set_author(self, author):
        if author == self._view_state.author:
            return
        self._view_state.author = author
        self._apply_view(announce=True)

    def _on_sort_changed(self, dropdown, _pspec):
        if self._syncing:
            return
        sort_by = SORTS[dropdown.get_selected()]
        if sort_by == self._view_state.sort:
            return
        self._view_state.sort = sort_by
        # The first thing to read is the most: likes, replies... largest
        # first; time starts from the oldest, as Phanpy does.
        self._view_state.order = 'asc' if sort_by == 'time' else 'desc'
        self._apply_view(announce=True)

    def _on_order_changed(self, group, _pspec):
        if self._syncing:
            return
        order = group.get_active_name() or 'asc'
        if order == self._view_state.order:
            return
        self._view_state.order = order
        self._apply_view(announce=True)

    def _on_group_changed(self, switch, _pspec):
        if self._syncing or switch.get_active() == self._view_state.group:
            return
        self._view_state.group = switch.get_active()
        self._apply_view(announce=True)

    def _on_reset(self, _button):
        if self._view_state.is_default():
            return
        self._view_state = ViewState()
        self._apply_view(announce=True)

    # ----------------------------------------------------------------------
    # Keyboard (2.6)

    def _add_keys(self):
        # Return is not here: the Gtk.ListView opens the focused post on
        # Enter itself, which is why the results header is activatable
        # False. Binding it here as well would fight that.
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for key, handler in (('j', self._key_next_post),
                             ('k', self._key_previous_post),
                             ('l', self._key_next_author),
                             ('h', self._key_previous_author),
                             ('period', self._key_top)):
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.KeyvalTrigger.new(Gdk.keyval_from_name(key),
                                      Gdk.ModifierType(0)),
                Gtk.CallbackAction.new(pages.weak_method(handler))))
        self.add_controller(controller)

    def _current_position(self):
        """Where the focused post is in the list, or -1."""
        root = self.get_root()
        widget = root.get_focus() if root is not None else None
        while widget is not None and not isinstance(widget, CatchUpRow):
            widget = widget.get_parent()
        if widget is None or widget.item is None:
            return -1
        for position in range(self.sorted.get_n_items()):
            if self.sorted.get_item(position) is widget.item:
                return position
        return -1

    def _focus_position(self, position):
        count = self.sorted.get_n_items()
        if not 0 < position < count:      # 0 is the block above the posts
            return False
        self.list_view.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)
        return True

    def _in_entry(self):
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return isinstance(focus, Gtk.Editable)

    def _key_next_post(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        return self._focus_position(max(self._current_position() + 1, 1))

    def _key_previous_post(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        return self._focus_position(position - 1 if position > 0 else 1)

    def _author_at(self, position):
        return self._computed.info[
            self.sorted.get_item(position).raw['id']].author

    def _key_next_author(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        count = self.sorted.get_n_items()
        if position < 1:
            return self._focus_position(1)
        current = self._author_at(position)
        for later in range(position + 1, count):
            if self._author_at(later) != current:
                return self._focus_position(later)
        return False

    def _key_previous_author(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        if position < 1:
            return self._focus_position(1)
        current = self._author_at(position)
        earlier = position - 1
        while earlier > 0 and self._author_at(earlier) == current:
            earlier -= 1
        if earlier < 1:
            return False
        author = self._author_at(earlier)
        while earlier > 1 and self._author_at(earlier - 1) == author:
            earlier -= 1
        return self._focus_position(earlier)

    def _key_top(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        self.scrolled.get_vadjustment().set_value(0)
        return True


def _toast_tick(page_ref):
    page = page_ref()
    if page is not None:
        page._show_view_toast()
    return GLib.SOURCE_REMOVE
