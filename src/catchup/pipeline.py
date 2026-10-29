# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up's plain-data pipeline, run state machine and snapshot contract."""

import dataclasses
import datetime

from ..i18n import gettext as _, ngettext

from .digest import (
    CATEGORIES, apply_filters, author_counts, categorize, content_of,
    dedupe_boosts, filter_posts, is_filtered_post, mark_threads,
    merge_hashtags, merge_news, parse_time, timeline_hashtags, timeline_links,
    trending_status_keys)
from .digest_layout import (
    author_order, post_info_values, posts_bar_ids, sort_info_ids)


 


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
    from ..statustext import format_time

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


def run(snapshot, viewer_languages=None, hide_trending=False):
    """Derive categories, rank, trends and sort keys from a persisted
    snapshot. Pure function of plain-data inputs; reopening a snapshot gives
    the same result."""
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
    scored = rank.rank(candidates, context)
    ranked = [candidate.status for candidate in scored]
    scores = {candidate.status['id']: candidate.score
              for candidate in scored}

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



def make_snapshot(result, account_key, period):
    from . import store as catchupstore
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


