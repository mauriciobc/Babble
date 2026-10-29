# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure placement and ordering helpers for Catch Up's digest views."""

from dataclasses import dataclass

from .digest import (  # noqa: E402
    _acct_id, _ts, bin_by_time, content_length, content_of, plain_text)


MEDIA_DENSITY = 8          # Phanpy
CARD_DENSITY = 8
DENSITY_CHARS = 140

POSTS_BAR_THRESHOLD = 160  # above this the posts bar bins by time
POSTS_BAR_BINS = 320

PAGE_UNITS = 12            # one unit is a 1 x 1 cell
INITIAL_PAGES = 3
MORE_PAGES = 3
MAX_NEWS_PER_PAGE = 2
MAX_TAGS_PER_PAGE = 1
NON_POST_SHARE = 3          # news + hashtags: at most 1/3 of a page
HERO_WIDE_COLUMNS = 6      # 3 x 2 first hero of a page from this many columns
MIN_TIER_POSTS = 10        # fewer posts: only the first is a hero
LANDSCAPE_ASPECT = 1.3
MEDIUM_WIDE_MAX_CHARS = 140
COMPACT_PROMOTE_CHARS = 180
HASHTAG_WIDE_DAYS = 3
def post_density(post):
    """Phanpy ``postDensity``: text and poll characters per 140, plus 8 per
    media attachment, or 8 when there is no media but the card has an
    image. Pass the content post (the boosted one for a boost)."""
    poll = post.get('poll') or {}
    poll_len = sum(len(o.get('title') or '') for o in poll.get('options') or [])
    text = (len(post.get('spoiler_text') or '')
            + content_length(post.get('content')) + poll_len)
    media = post.get('media_attachments') or []
    if media:
        extra = MEDIA_DENSITY * len(media)
    elif (post.get('card') or {}).get('image'):
        extra = CARD_DENSITY
    else:
        extra = 0
    return text / DENSITY_CHARS + extra


SORTS = ('time', 'replies', 'likes', 'boosts', 'density', 'relevance')


def post_info_values(post, ts, score):
    """The ``PostInfo.values`` tuple: one entry per :data:`SORTS` slot."""
    content = content_of(post)
    return (ts,
            content.get('replies_count') or 0,
            content.get('favourites_count') or 0,
            content.get('reblogs_count') or 0,
            post_density(content),
            score)


def sort_value(post, sort_by, scores=None):
    """The number a post is sorted by. Everything but time compares the
    boosted post when there is one. ``relevance`` reads ``scores`` by the
    post's own id, then by its content's id."""
    if sort_by == 'time':
        return _ts(post.get('created_at'))
    content = content_of(post)
    if sort_by == 'replies':
        return content.get('replies_count') or 0
    if sort_by == 'likes':
        return content.get('favourites_count') or 0
    if sort_by == 'boosts':
        return content.get('reblogs_count') or 0
    if sort_by == 'density':
        return post_density(content)
    if sort_by == 'relevance':
        scores = scores or {}
        if post.get('id') in scores:
            return scores[post['id']]
        return scores.get(content.get('id'), 0)
    raise ValueError('unknown sort: %r' % (sort_by,))
def author_order(posts):
    """Account ids by their post count in ``posts``, most first (ties keep
    first-seen order): the order of the author chips and of the groups."""
    counts = {}
    for post in posts:
        key = _acct_id(post.get('account'))
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts, key=lambda k: -counts[k])     # stable


def sort_posts(posts, sort_by='time', order='asc', group_by_author=False,
               scores=None, authors=None):
    """Sorted copy. Ties fall back to time (oldest first) and then id,
    whatever the order. ``group_by_author`` puts each author's posts
    together, authors ordered by ``authors`` (default ``author_order`` of
    ``posts``). Reading: Phanpy's density comparator is inconsistent on
    ties; here ties are resolved like every other sort."""
    out = sorted(posts, key=lambda p: (_ts(p.get('created_at')),
                                       str(p.get('id'))))
    if sort_by != 'time':
        out.sort(key=lambda p: sort_value(p, sort_by, scores),
                 reverse=(order == 'desc'))
    elif order == 'desc':
        out.reverse()
    if group_by_author:
        index = {a: i for i, a in enumerate(
            authors if authors is not None else author_order(posts))}
        out.sort(key=lambda p: index.get(_acct_id(p.get('account')),
                                         len(index)))
    return out


def sort_info_ids(infos, sort_by='time', order='asc', group=False,
                  author_index=None):
    """Like :func:`sort_posts`, on :class:`catchup.PostInfo` rows with
    precomputed sort keys (no HTML re-parsing on the main loop)."""
    out = sorted(infos, key=lambda i: (i.ts, str(i.id)))
    if sort_by != 'time':
        slot = SORTS.index(sort_by)
        out.sort(key=lambda i: i.values[slot], reverse=(order == 'desc'))
    elif order == 'desc':
        out.reverse()
    if group:
        index = author_index or {}
        out.sort(key=lambda i: index.get(i.author, len(index)))
    return [i.id for i in out]

def posts_bar_bins(posts, now):
    """None for up to 160 posts (one dot each, in time order); above that,
    320 time buckets."""
    if len(posts) <= POSTS_BAR_THRESHOLD:
        return None
    return bin_by_time(posts, POSTS_BAR_BINS, now)


def posts_bar_ids(sorted_posts, now):
    """``(bar_dots, bar_bins)`` for :class:`catchup.PostsBar`.

    Exactly one of the two is non-``None``: dots for up to
    :data:`POSTS_BAR_THRESHOLD` posts, otherwise 320 time buckets of ids."""
    bins = posts_bar_bins(sorted_posts, now)
    if bins is None:
        return [post['id'] for post in sorted_posts], None
    return None, [[post['id'] for post in bucket] for bucket in bins]

def page_rows(columns):
    """Rows of one page: 12 units over ``columns`` columns, rounded up to
    whole rows (3 x 4, 4 x 3, 2 x 6, 1 x 12; 5 columns give 3 rows, 12 or
    more give 1)."""
    return max(1, -(-PAGE_UNITS // max(1, int(columns))))


def page_cells(columns):
    """Cells of one page. Reading: the unit budget of a page is its whole
    cell count, so for 5, 7, 8... columns it is a little over 12 and no
    row is left ragged."""
    columns = max(1, int(columns))
    return columns * page_rows(columns)


def assign_tiers(ordered, scores):
    """``{post id: 'hero' | 'medium' | 'compact'}`` for the posts the grid
    will show, in their display order (the score order after diversity).

    Percentiles are over these posts only. Tiers are taken by score rank
    (ties broken by position) so they are exact even with equal scores:
    with linear interpolation, ``S >= P90`` holds for the top
    ``n - ceil(0.9 (n - 1))`` and ``S >= P70`` for the top
    ``n - ceil(0.7 (n - 1))``. Fewer than 10 posts: only the first is a hero
    and the medium tier is the P70 count minus that hero, taken from the
    others by score."""
    scores = scores or {}
    n = len(ordered)
    tiers = {}
    if not n:
        return tiers

    def value(i):
        return scores.get(ordered[i]['id'], 0)

    ranked = sorted(range(n), key=lambda i: (-value(i), i))
    medium_total = n - (7 * (n - 1) + 9) // 10
    if n >= MIN_TIER_POSTS:
        heroes = n - (9 * (n - 1) + 9) // 10
        for rank, i in enumerate(ranked):
            tiers[ordered[i]['id']] = (
                'hero' if rank < heroes
                else 'medium' if rank < medium_total else 'compact')
        return tiers
    tiers[ordered[0]['id']] = 'hero'
    mediums = max(0, medium_total - 1)
    for i in ranked:
        if i == 0:
            continue
        tiers[ordered[i]['id']] = 'medium' if mediums > 0 else 'compact'
        mediums -= 1
    return tiers


def media_aspect(attachment):
    """Width over height of an attachment, from ``meta.original`` (width and
    height, else ``aspect``), or None."""
    original = ((attachment.get('meta') or {}).get('original')) or {}
    width, height = original.get('width'), original.get('height')
    if width and height:
        return width / height
    return original.get('aspect') or None


def is_landscape_led(post):
    """Reading: "led by one landscape image" is a post with exactly one
    attachment, an image whose aspect ratio is at least 1.3."""
    media = content_of(post).get('media_attachments') or []
    if len(media) != 1 or media[0].get('type') != 'image':
        return False
    aspect = media_aspect(media[0])
    return bool(aspect) and aspect >= LANDSCAPE_ASPECT


def _text_len(post):
    return len(plain_text(content_of(post).get('content')))


def clamp_span(colspan, rowspan, columns):
    """Spans clamped to the grid: at most ``columns`` wide and one page
    (``page_rows``) tall, at least 1 x 1."""
    columns = max(1, int(columns))
    return (max(1, min(colspan, columns)),
            max(1, min(rowspan, page_rows(columns))))


def post_span(post, tier, columns, first_hero=False, room=False):
    """Spans ``(colspan, rowspan)`` of a post tile (6.1 step 4).

    Hero 2 x 2 (3 x 2 for the first hero of a page from 6 columns); medium
    2 x 1 when led by one landscape image with text of at most 140
    characters, else 1 x 2; compact 1 x 1, or 1 x 2 for text-only posts of
    more than 180 characters when ``room`` says the page budget has space."""
    if tier == 'hero':
        wide = first_hero and columns >= HERO_WIDE_COLUMNS
        span = (3 if wide else 2, 2)
    elif tier == 'medium':
        landscape = (is_landscape_led(post)
                     and _text_len(post) <= MEDIUM_WIDE_MAX_CHARS)
        span = (2, 1) if landscape else (1, 2)
    else:
        long_text = (not content_of(post).get('media_attachments')
                     and _text_len(post) > COMPACT_PROMOTE_CHARS)
        span = (1, 2) if room and long_text else (1, 1)
    return clamp_span(span[0], span[1], columns)


def news_span(entry, columns):
    """News: 2 x 2 with a cover image, else 2 x 1."""
    image = (entry.get('card') or {}).get('image')
    return clamp_span(2, 2 if image else 1, columns)


def days_with_uses(entry):
    """Days of history with uses of a hashtag entry: the server's
    ``history``, or for the fallback the non-empty ``buckets``."""
    tag = entry.get('tag')
    if tag and tag.get('history') is not None:
        total = 0
        for day in tag['history']:
            try:
                total += int(day.get('uses') or 0) > 0
            except (TypeError, ValueError):
                pass
        return total
    return sum(1 for b in entry.get('buckets') or [] if b)


def hashtag_span(entry, columns):
    """Hashtag: 2 x 1 with at least 3 days of usage, else 1 x 1."""
    wide = days_with_uses(entry) >= HASHTAG_WIDE_DAYS
    return clamp_span(2 if wide else 1, 1, columns)


# --------------------------------------------------------------------------
# 6.2 Page budgets

@dataclass(frozen=True, eq=False)
class Tile:
    """A tile to place. ``kind`` is 'post', 'news' or 'hashtag'; ``ref`` is
    the payload (the status, news entry or hashtag entry); ``page`` the
    page it was allocated to (a hint for ``pack``)."""
    kind: str
    ref: object
    colspan: int = 1
    rowspan: int = 1
    tier: str = None
    page: int = 0


@dataclass(frozen=True)
class Placed:
    """A tile at its cell: ``row`` counts from the top of the whole grid;
    ``page`` is the 12-unit page band it landed in."""
    kind: str
    ref: object
    col: int
    row: int
    colspan: int
    rowspan: int
    tier: str = None
    page: int = 0


class _Band:
    """Occupancy of one page band: ``columns`` wide, ``page_rows`` tall."""

    def __init__(self, columns):
        self.columns = columns
        self.rows = page_rows(columns)
        self.occ = [[False] * columns for _ in range(self.rows)]
        self.free = columns * self.rows

    def find(self, colspan, rowspan):
        """First free position for a tile, scanning rows then columns."""
        occ = self.occ
        for row in range(self.rows - rowspan + 1):
            for col in range(self.columns - colspan + 1):
                if all(not occ[r][c]
                       for r in range(row, row + rowspan)
                       for c in range(col, col + colspan)):
                    return col, row
        return None

    def place(self, colspan, rowspan):
        """Put a tile at its first free position, or return None."""
        pos = self.find(colspan, rowspan)
        if pos is not None:
            col, row = pos
            for r in range(row, row + rowspan):
                for c in range(col, col + colspan):
                    self.occ[r][c] = True
            self.free -= colspan * rowspan
        return pos


def allocate(posts, scores, news, tags, columns, pages=INITIAL_PAGES):
    """Tiles for up to ``pages`` pages (6.1, 6.2), in page order.

    ``posts`` are in display order (score order after diversity), ``news``
    and ``tags`` the merged entries of 3.5. Percentiles come from the first
    ``pages x cells`` posts. Each page is filled in the order ``pack`` uses,
    on a real occupancy grid, so every tile of a page fits that page: first
    the first hero of the queue, then news and hashtags (caps of 2 and 1,
    together at most a third of the page's cells; a 2 x 2 news tile over the
    allowance drops to 2 x 1 or waits; the kind with first claim alternates,
    news on even pages; one such tile at most on a 1-column page), then the
    posts in order. A tile that does not fit waits for the next page, and
    from then on only 1 x 1 posts fill in behind it. Compact text-only posts
    of more than 180 characters take 1 x 2 while the page has room for it.
    Reading: re-allocating with more pages (Show more) recomputes the
    percentiles, so earlier pages can change."""
    columns = max(1, int(columns))
    cells = page_cells(columns)
    considered = list(posts[:pages * cells])
    tiers = assign_tiers(considered, scores)
    queue = list(considered)
    news_q, tag_q = list(news or []), list(tags or [])
    tiles = []
    page = 0
    while page < pages and (queue or page == 0):
        band = _Band(columns)
        here = []

        hero = next((p for p in queue if tiers[p['id']] == 'hero'), None)
        if hero is not None:
            span = post_span(hero, 'hero', columns, first_hero=True)
            if band.place(*span):
                del queue[next(i for i, p in enumerate(queue)
                               if p is hero)]
                here.append(Tile('post', hero, *span, tier='hero',
                                 page=page))
        # News and hashtags are accents: caps per page, not quotas, and
        # together at most a third of the page. The kind with first claim
        # alternates page by page so both show up early.
        allowance = cells // NON_POST_SHARE
        if columns == 1:
            allowance = min(allowance, 2)
        accents = [(news_q, 'news', MAX_NEWS_PER_PAGE, news_span),
                   (tag_q, 'hashtag', MAX_TAGS_PER_PAGE, hashtag_span)]
        if page % 2:
            accents.reverse()
        taken = 0
        for source, kind, budget, spanner in accents:
            for _ in range(budget):
                if not source or (columns == 1 and taken):
                    break
                span = spanner(source[0], columns)
                if span[0] * span[1] > allowance and kind == 'news':
                    span = clamp_span(2, 1, columns)    # 2 x 2 -> 2 x 1
                if span[0] * span[1] > allowance or not band.place(*span):
                    break
                allowance -= span[0] * span[1]
                taken += 1
                here.append(Tile(kind, source.pop(0), *span, page=page))

        waiting = []
        stuck = False
        for post in queue:
            tier = tiers[post['id']]
            span = post_span(post, tier, columns)
            if tier == 'compact' and band.free >= 2 and not stuck:
                promoted = post_span(post, tier, columns, room=True)
                if promoted != span and band.find(*promoted):
                    span = promoted
            if (band.free <= 0 or (stuck and span != (1, 1))
                    or not band.place(*span)):
                waiting.append(post)
                stuck = True
                continue
            here.append(Tile('post', post, *span, tier=tier, page=page))
        queue = waiting
        if not here:
            break
        tiles.extend(here)
        page += 1
    return tiles


# --------------------------------------------------------------------------
# 6.3 Packing

def pack(tiles, columns):
    """Dense packing, like ``grid-auto-flow: row dense``, as a pure function
    of ``(tiles, columns)``: a list of ``Placed`` in placement order.

    The grid is ``columns`` wide and made of page bands of ``page_rows``
    rows. Spans are clamped to the grid. Tiles are taken page by page; inside
    a page the first hero comes first, then news and hashtags, then the
    posts (each group in input order). Each tile goes to the first free cell,
    scanning rows then columns, where it fits inside the current band. A
    tile that does not fit, or that would give the band a third news or a
    second hashtag tile, rolls to the next band; later tiles keep filling
    the holes it leaves. Reading: a hole may be filled by any later tile of
    the same or an earlier allocation page, and by 1 x 1 posts of a later
    page, so holes close without pulling a later page's news or hashtag
    tile forward past the per-page budget. For the output of ``allocate``
    nothing rolls and the placement is the one ``allocate`` simulated."""
    columns = max(1, int(columns))
    clamped = []
    for tile in tiles:
        span = clamp_span(tile.colspan, tile.rowspan, columns)
        clamped.append(Tile(tile.kind, tile.ref, span[0], span[1],
                            tile.tier, tile.page))
    first_hero = {}
    for i, tile in enumerate(clamped):
        if tile.kind == 'post' and tile.tier == 'hero':
            first_hero.setdefault(tile.page, i)

    def order(i):
        tile = clamped[i]
        klass = (0 if first_hero.get(tile.page) == i
                 else 1 if tile.kind != 'post' else 2)
        return tile.page, klass, i

    pending = [clamped[i] for i in sorted(range(len(clamped)), key=order)]
    placed = []
    index = 0
    logical = pending[0].page if pending else 0
    rows = page_rows(columns)
    while pending:
        band = _Band(columns)
        news = tags = 0
        leftover = []
        for tile in pending:
            eligible = tile.page <= logical or (
                tile.kind == 'post' and tile.colspan == 1
                and tile.rowspan == 1)
            if tile.kind == 'news' and news >= MAX_NEWS_PER_PAGE:
                eligible = False
            if tile.kind == 'hashtag' and tags >= MAX_TAGS_PER_PAGE:
                eligible = False
            pos = band.place(tile.colspan, tile.rowspan) if eligible \
                else None
            if pos is None:
                leftover.append(tile)
                continue
            news += tile.kind == 'news'
            tags += tile.kind == 'hashtag'
            placed.append(Placed(tile.kind, tile.ref, pos[0],
                                 index * rows + pos[1], tile.colspan,
                                 tile.rowspan, tile.tier, index))
        if len(leftover) == len(pending):
            # Nothing is eligible yet: jump to the next allocation page
            # instead of emitting an empty band.
            nearest = min(t.page for t in pending)
            if nearest <= logical:
                raise RuntimeError('tile does not fit an empty band')
            logical = nearest
            continue
        pending = leftover
        index += 1
        logical += 1
    return placed


def grid_rows(placed):
    """Rows the packed grid occupies."""
    return max((p.row + p.rowspan for p in placed), default=0)


def layout(posts, scores, news, tags, columns, pages=INITIAL_PAGES):
    """``pack(allocate(...), columns)``: the grid for one column count."""
    return pack(allocate(posts, scores, news, tags, columns, pages), columns)
