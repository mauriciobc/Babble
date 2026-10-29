# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up's plain-data pipeline, run state machine and snapshot contract."""

import dataclasses
import datetime
import functools


from ..i18n import gettext as _, ngettext

from gi.repository import Gio, GLib, GObject  # noqa: E402

from . import store as catchupstore  # noqa: E402
from .digest import (  # noqa: E402
    CATEGORIES, PAGE_LIMIT, apply_filters, author_counts, categorize,
    content_of, dedupe_boosts, fetch_progress, filter_posts, followed_tag_hits,
    is_filtered_post, mark_followed_tags, mark_threads, merge_hashtags,
    merge_news, parse_time, relationship_ids, timeline_hashtags,
    timeline_links, trending_status_keys)
from .digest_layout import (  # noqa: E402
    author_order, post_info_values, posts_bar_ids, sort_info_ids)
from ..statustext import format_time  # noqa: E402

@dataclasses.dataclass
class Digest:
    """What the Highlights slot is given: the one contract between this
    page and ``catchup.tiles.HighlightsView``.

    Built once per catch-up (and once per reopened snapshot), after
    ranking, and never rebuilt when the All posts filters change.

    ``posts``: the ranked status dicts in final rank order (best first):
        boosts already deduplicated, the Filtered category excluded.
    ``scores``: ``{status id: relevance score}`` for those posts.
    ``items``: ``{status id: timeline.StatusItem}``, the same objects the
        All posts list shows (keyed by the id of the status in the snapshot,
        the boost wrapper's own id for a boost), so an action in one is seen
        in the other. It holds every visible post, filtered ones too.
    ``news``: ``digest.merge_news`` output.
    ``tags``: ``digest.merge_hashtags`` output.
    ``trending_keys``: ids and URIs of ``/trends/statuses``
        (``digest.trending_status_keys``); a post is trending when
        ``is_trending_post`` finds its content in it.
    ``boosters``: ``{kept boost id: [accounts of the other boosters]}``
        (``digest.dedupe_boosts``).
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


def period_menu_label(period):
    """``period_label`` with a unique access key for the period menu."""
    hours = PERIOD_HOURS.get(period)
    if period == 'since':
        return _('Since the _last catch-up')
    if period == 'max':
        return _('As _far back as possible')
    if hours == 1:
        return _('Last _hour')
    if hours == 3:
        return _('Last _3 hours')
    if hours == 6:
        return _('Last _6 hours')
    if hours == 12:
        return _('Last 1_2 hours')
    if hours == 24:
        return _('Last 2_4 hours')
    return period_label(period)


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
    # One number per entry of ``digest_layout.SORTS``.
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
    from . import rank, rank_model

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
    context = rank_model.RankingContext(
        now=end_at, mutual_author_ids=mutual,
        viewer_languages=list(viewer_languages) if viewer_languages
        else None)
    ranked_candidates = rank.rank(candidates, context)
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
    :func:`digest.sort_info_ids`."""
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


