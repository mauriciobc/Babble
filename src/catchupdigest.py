# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up: the pure data layer (no GTK, no ``gi``, stdlib only).

Covers CATCH-UP.md sections 3.1 (the stop decision), 3.2 (filters, followed
tags, relationships, threads), 3.3 (categories), 3.4 (boost deduplication,
timeline links and hashtags), 3.5 (trends merging and fallback), 2.5
(density and sort keys) and 6 (tiers, sizes, page budgets, dense packing).
The relevance score (section 4) lives in ``catchuprank.py``; scores come in
here as a plain ``{status id: score}`` dict.

Everything is a pure function of its arguments. The clock is never read:
``now`` / ``start_at`` are passed in, so a snapshot gives the same digest
every time it is opened.

Statuses are Mastodon JSON (snake_case dicts). Babble's markers on a fetched
status are ``_filtered`` (``None`` or ``{'action', 'titles'}``),
``_followed_tags`` (list of tag names) and ``_thread`` (bool).

Ported from Phanpy's ``src/pages/catchup.jsx`` (fetch loop, categories, link
grouping, boost deduplication, post density, ``binByTime``) and
``src/utils/{timeline-utils,followed-tags,filters,html-content-length}.js``:

    MIT License

    Copyright (c) 2023-present, Lim Chee Aun

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
    TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
    SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Ambiguities of the spec are resolved in comments marked ``Reading:``.
"""

import datetime
import math
import re
from dataclasses import dataclass
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# Constants

PAGE_LIMIT = 40            # home timeline page size (3.1)
MAX_POSTS = 1000           # safety cap (3.1)
MAX_PAGES = 25

CATEGORIES = ('filtered', 'groups', 'boosts', 'quotes', 'followed_tags',
              'replies', 'original')

MIN_TOP_LINKS = 10         # Phanpy: keep at least this many links
MIN_FALLBACK_TAG_ACCOUNTS = 2

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

_VOID = frozenset(('br', 'hr', 'img', 'wbr', 'input', 'meta', 'link', 'area',
                   'base', 'col', 'embed', 'source', 'track'))


# --------------------------------------------------------------------------
# Time

def parse_time(value):
    """Aware ``datetime`` from an ISO-8601 string (or a datetime), else None.
    A naive result is taken as UTC."""
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.strip()
        if text.endswith(('Z', 'z')):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _ts(value):
    """Seconds since the epoch, or -inf for a value that does not parse."""
    dt = parse_time(value)
    return dt.timestamp() if dt else float('-inf')


def _ms(dt):
    return round(dt.timestamp() * 1000)


# --------------------------------------------------------------------------
# Small status helpers

def content_of(post):
    """The post whose content is shown: the boosted one for a boost."""
    return post.get('reblog') or post


def _acct_id(account):
    return str((account or {}).get('id', ''))


def _is_link_card(card):
    return bool(card and card.get('type') == 'link' and card.get('url')
                and card.get('image'))


class _TextExtractor(HTMLParser):
    """Visible text of status HTML. ``.invisible`` spans (the scheme and
    path-tail of shortened links) are dropped, as Phanpy's
    ``htmlContentLength`` does. With ``breaks``, ``<br>`` and ``</p>`` become
    newlines; without, the result is the bare ``textContent``."""

    def __init__(self, breaks):
        super().__init__(convert_charrefs=True)
        self.breaks = breaks
        self.parts = []
        self.stack = []        # (tag, invisible)
        self.hidden = 0

    def _open(self, tag, attrs):
        if tag in _VOID:
            if tag == 'br' and self.breaks and not self.hidden:
                self.parts.append('\n')
            return False
        classes = (dict(attrs).get('class') or '').split()
        invisible = 'invisible' in classes
        self.stack.append((tag, invisible))
        if invisible:
            self.hidden += 1
        return True

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if self._open(tag, attrs):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _name, invisible in self.stack[i:]:
                    if invisible:
                        self.hidden -= 1
                del self.stack[i:]
                break
        else:
            return
        if tag == 'p' and self.breaks and not self.hidden:
            self.parts.append('\n\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _extract(html, breaks):
    if not html:
        return ''
    parser = _TextExtractor(breaks)
    parser.feed(html)
    parser.close()
    return ''.join(parser.parts)


def content_length(html):
    """Phanpy ``htmlContentLength``: characters of visible text."""
    return len(_extract(html, False))


def plain_text(html):
    """Readable plain text: paragraphs separated by a blank line."""
    text = _extract(html, True).strip()
    return re.sub(r'\n{3,}', '\n\n', text)


def extract_hashtags(html):
    """Hashtag names (without ``#``) of the ``a.hashtag`` links in HTML,
    as Phanpy's ``extractTagsFromStatus``."""
    if not html or '#' not in html:
        return []

    class Finder(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.found = []
            self.text = []

        def handle_starttag(self, tag, attrs):
            if self.depth:
                if tag == 'a':
                    self.depth += 1
            elif tag == 'a' and 'hashtag' in (
                    dict(attrs).get('class') or '').split():
                self.depth = 1
                self.text = []

        def handle_endtag(self, tag):
            if self.depth and tag == 'a':
                self.depth -= 1
                if not self.depth:
                    self.found.append(''.join(self.text))

        def handle_data(self, data):
            if self.depth:
                self.text.append(data)

    finder = Finder()
    finder.feed(html)
    finder.close()
    return [re.sub(r'^[^#]*#+', '', t.strip()) for t in finder.found]


# --------------------------------------------------------------------------
# 3.1 Fetching: the pure stop decision

def in_range(status, start_at):
    """A post is in range when its wrapper's ``created_at`` (the boost time
    for a boost) is at or after ``start_at``. ``start_at`` None is unbounded.
    A ``created_at`` that does not parse is out of range (``NaN >= x`` is
    false in Phanpy too)."""
    if start_at is None:
        return True
    start = parse_time(start_at)
    created = parse_time(status.get('created_at'))
    if start is None:
        return True
    return created is not None and created >= start


def page_verdict(page, start_at):
    """``(in_range_items, should_stop)`` for one fetched page.

    Fix over Phanpy, which stops inside the per-post loop and so quits at a
    page whose first post is out of range even when later ones are in range
    (the home timeline is not strictly ordered by ``created_at``: a boost
    carries the boost time, but a page can still mix them). Here the whole
    page is examined first and the loop stops only when the page is empty or
    has no in-range post at all."""
    items = [s for s in page or [] if in_range(s, start_at)]
    return items, not items


def fetch_progress(page, start_at, kept_total, pages_done):
    """``(in_range_items, stop_reason)`` after one page.

    ``kept_total`` is the number of in-range posts kept before this page and
    ``pages_done`` the number of pages fetched including this one.
    ``stop_reason`` is ``None`` to go on, ``'complete'`` (empty page, or
    nothing in range) or ``'cap'`` (1,000 posts / 25 pages reached).
    Reading: the cap is checked after a page is taken and the page is kept
    whole, so up to 1,039 posts can be kept; the safety cap is a stop, not
    a truncation. Range wins when both apply."""
    items, stop = page_verdict(page, start_at)
    if stop:
        return items, 'complete'
    if kept_total + len(items) >= MAX_POSTS or pages_done >= MAX_PAGES:
        return items, 'cap'
    return items, None


# --------------------------------------------------------------------------
# 3.2 Post-processing

def filter_info(status, context='home', now=None):
    """Strongest filter hit as ``{'action', 'titles'}``, or None.

    A pure copy of the logic of ``timeline.status_filter`` and Phanpy's
    ``isFiltered``: the wrapper's and the boosted post's ``filtered`` lists
    are both read; ``hide`` wins, then ``warn`` (any action that is not
    ``blur``, unknown ones count as warn), and ``blur`` only when every hit
    blurs. Filters that do not cover ``context`` (when they say) or that have
    expired by ``now`` (when given) are ignored."""
    now = parse_time(now)
    hits = []
    for source in (status, status.get('reblog') or {}):
        for result in source.get('filtered') or []:
            rule = result.get('filter') or {}
            ctx = rule.get('context')
            if ctx and context not in ctx:
                continue
            expires = parse_time(rule.get('expires_at'))
            if now and expires and expires <= now:
                continue
            hits.append(rule)
    if not hits:
        return None
    actions = [r.get('filter_action') or 'warn' for r in hits]
    if 'hide' in actions:
        return {'action': 'hide', 'titles': []}
    titles = [r.get('title') or '' for r in hits]
    if all(a == 'blur' for a in actions):
        return {'action': 'blur', 'titles': titles}
    return {'action': 'warn', 'titles': titles}


def apply_filters(statuses, self_ids=(), context='home', now=None):
    """Step 1: drop ``hide`` hits (never on your own posts) and set
    ``_filtered`` on the rest. Returns the kept statuses, in order."""
    self_ids = {str(i) for i in self_ids}
    kept = []
    for status in statuses:
        author = _acct_id(content_of(status).get('account')
                          or status.get('account'))
        info = None
        if author not in self_ids:
            info = filter_info(status, context, now)
        if info and info['action'] == 'hide':
            continue
        status['_filtered'] = info
        kept.append(status)
    return kept


def followed_tag_hits(statuses, followed_tags):
    """Step 2: ``{status id: [followed tag names]}`` for non-boost posts
    carrying one of your followed tags (matched case-insensitively against
    ``tags`` and the content's hashtag links). ``followed_tags`` is a list
    of names or of ``Tag`` dicts."""
    names = []
    for tag in followed_tags or []:
        name = tag.get('name') if isinstance(tag, dict) else tag
        if name:
            names.append(name)
    hits = {}
    if not names:
        return hits
    for status in statuses:
        if status.get('reblog'):
            continue
        used = {t.lower() for t in extract_hashtags(status.get('content'))}
        used.update((t.get('name') or '').lower()
                    for t in status.get('tags') or [])
        if not used:
            continue
        found = [n for n in names if n.lower() in used]
        if found:
            hits[status['id']] = found
    return hits


def relationship_ids(statuses, hits):
    """Account ids for the one batched ``/accounts/relationships`` call: the
    authors of the followed-tag posts (``hits`` from ``followed_tag_hits``)
    and the authors of boosted posts, in first-seen order."""
    ids = []
    seen = set()

    def add(account):
        key = _acct_id(account)
        if key and key not in seen:
            seen.add(key)
            ids.append(key)

    for status in statuses:
        if status['id'] in hits:
            add(status.get('account'))
        if status.get('reblog'):
            add(status['reblog'].get('account'))
    return ids


def mark_followed_tags(statuses, hits, relationships):
    """Step 3, the relationship-dependent part: a followed-tag post whose
    author you do not follow gets ``_followed_tags``; everything else gets
    ``[]``. ``relationships`` maps account id to ``{'following', ...}``; an
    author with no entry is not marked (Phanpy: ``r && !r.following``)."""
    for status in statuses:
        names = hits.get(status['id'])
        rel = (relationships or {}).get(_acct_id(status.get('account')))
        status['_followed_tags'] = (
            list(names) if names and rel and not rel.get('following')
            else [])
    return statuses


def in_network(post):
    """True unless the post reached the snapshot only through a followed
    tag (the in-network flag of section 4.2)."""
    return not post.get('_followed_tags')


def mark_threads(posts):
    """Step 4: a post replying to another post of the snapshot that is not
    itself a reply marks that post as a thread starter (``_thread``)."""
    by_id = {}
    for post in posts:
        by_id.setdefault(post['id'], post)
    for post in posts:
        parent_id = post.get('in_reply_to_id')
        if parent_id:
            parent = by_id.get(parent_id)
            if parent is not None and not parent.get('in_reply_to_id'):
                parent['_thread'] = True
    return posts


# --------------------------------------------------------------------------
# 3.3 Categories

def is_filtered_post(post):
    """The Filtered category: a ``warn`` hit (``blur`` does not count)."""
    info = post.get('_filtered')
    return bool(info) and info.get('action') != 'blur'


def has_quote(quote):
    """Phanpy ``hasQuote``: a quote that names its quoted post."""
    if not quote:
        return False
    return bool(quote.get('id') or quote.get('quoted_status_id')
                or (quote.get('quoted_status') or {}).get('id'))


def categorize(post, native_quotes=True):
    """Exactly one category, checked in the order of CATEGORIES."""
    if is_filtered_post(post):
        return 'filtered'
    if post.get('group'):
        return 'groups'
    if post.get('reblog'):
        return 'boosts'
    if native_quotes and has_quote(post.get('quote')):
        return 'quotes'
    if post.get('_followed_tags'):
        return 'followed_tags'
    if (post.get('in_reply_to_id')
            and str(post.get('in_reply_to_account_id'))
            != _acct_id(post.get('account'))):
        return 'replies'
    return 'original'


def category_counts(posts, native_quotes=True):
    counts = dict.fromkeys(CATEGORIES, 0)
    for post in posts:
        counts[categorize(post, native_quotes)] += 1
    return counts


# --------------------------------------------------------------------------
# 3.4 Extraction

def dedupe_boosts(posts):
    """Boosts of the same ``reblog.id`` collapse into the first one seen.

    Returns ``(visible, boosters, hidden_ids)``: ``visible`` is ``posts``
    without the duplicates, ``boosters`` maps the kept boost's id to the
    list of the *other* accounts that boosted it (Phanpy's ``__BOOSTERS``;
    the kept boost's own account is its ``account``) and ``hidden_ids`` is
    the set of dropped ids (they still count in the posts bar).
    Reading: Phanpy dedupes inside the category-filtered list; here it runs
    once over the whole snapshot, which gives the same result because every
    boost of one post lands in the same category."""
    first = {}
    boosters = {}
    hidden = set()
    visible = []
    for post in posts:
        reblog = post.get('reblog')
        if reblog:
            kept = first.get(reblog['id'])
            if kept is not None:
                boosters.setdefault(kept['id'], []).append(post['account'])
                hidden.add(post['id'])
                continue
            first[reblog['id']] = post
        visible.append(post)
    return visible, boosters, hidden


def _link_key(url):
    return url[:-1] if url.endswith('/') else url   # Phanpy: replace(/\/$/)


def timeline_links(posts, trim=True):
    """Links shared in the timeline, Phanpy's ``topLinks``.

    Only ``type == 'link'`` cards with a ``url`` and an ``image``, from
    posts outside the Filtered category; the card comes from the boosted
    post when there is one. Grouped by URL minus one trailing ``/``. Per
    link: ``shared`` (distinct sharer accounts; the same account twice
    counts once and adds nothing), ``sharers``, and ``likes``/``boosts``
    summed over *different* posts. Sorted by shared, boosts, likes, all
    descending (stable, so ties keep first-seen order). With ``trim``,
    at least 10 links are kept and the list is cut at the first link past
    the tenth shared only once. Pass ``trim=False`` to match trending links
    against every shared link."""
    links = {}
    counted = {}
    for post in posts:
        if is_filtered_post(post):
            continue
        content = content_of(post)
        card = content.get('card')
        if not _is_link_card(card):
            continue
        key = _link_key(card['url'])
        likes = content.get('favourites_count') or 0
        boosts = content.get('reblogs_count') or 0
        entry = links.get(key)
        if entry is None:
            links[key] = {'url': key, 'post_id': content['id'],
                          'card': card, 'shared': 1,
                          'sharers': [post['account']],
                          'likes': likes, 'boosts': boosts}
            counted[key] = {content['id']}
            continue
        if any(a['id'] == post['account']['id'] for a in entry['sharers']):
            continue
        entry['shared'] += 1
        entry['sharers'].append(post['account'])
        # The counts are summed over different posts, so one post that
        # several people boosted adds them once, not once per boost.
        if content['id'] not in counted[key]:
            counted[key].add(content['id'])
            entry['likes'] += likes
            entry['boosts'] += boosts
    top = sorted(links.values(),
                 key=lambda e: (-e['shared'], -e['boosts'], -e['likes']))
    if trim and len(top) > MIN_TOP_LINKS:
        for i in range(MIN_TOP_LINKS, len(top)):
            if top[i]['shared'] <= 1:
                top = top[:i]
                break
    return top


def timeline_hashtags(posts, now=None):
    """Hashtags used in the timeline, over posts outside the Filtered
    category: ``[entry]`` sorted by distinct accounts, then post count, then
    name. Each tag counts once per post. Reading: a post is the content post
    (the boosted one for a boost), counted once however many people boosted
    it, and the "account using the tag" is its author, not a booster.
    ``entry`` has ``key`` (lowercase), ``name`` (first spelling seen),
    ``timeline_posts``, ``timeline_accounts``, ``accounts`` and, when ``now``
    is given, ``buckets``: 12 counts over the tag's posts by ``bin_by_time``
    for the fallback sparkline."""
    tags = {}
    seen = set()
    for post in posts:
        if is_filtered_post(post):
            continue
        content = content_of(post)
        names = {}
        for tag in content.get('tags') or []:
            name = tag.get('name') or ''
            if name:
                names.setdefault(name.lower(), name)
        for key, name in names.items():
            if (key, content['id']) in seen:
                continue
            seen.add((key, content['id']))
            entry = tags.setdefault(key, {
                'key': key, 'name': name, 'tag': None, 'trending': False,
                'following': False, 'timeline_posts': 0,
                'timeline_accounts': 0, 'accounts': [], '_posts': []})
            entry['timeline_posts'] += 1
            entry['_posts'].append(content)
            author = content.get('account') or {}
            if not any(a['id'] == author.get('id')
                       for a in entry['accounts']):
                entry['accounts'].append(author)
                entry['timeline_accounts'] += 1
    out = sorted(tags.values(), key=lambda e: (
        -e['timeline_accounts'], -e['timeline_posts'], e['key']))
    for entry in out:
        posts_of = entry.pop('_posts')
        entry['buckets'] = (
            [len(b) for b in bin_by_time(posts_of, 12, now)]
            if now is not None else None)
    return out


# --------------------------------------------------------------------------
# 3.5 News and hashtags: trends merged with the timeline

def trending_status_keys(trend_statuses, hide_trending=False):
    """The ids and URIs of ``/trends/statuses``, for the Trending badge.
    Empty when trends are hidden or unavailable (None)."""
    keys = set()
    if hide_trending:
        return frozenset()
    for status in trend_statuses or []:
        for field in ('id', 'uri'):
            if status.get(field):
                keys.add(str(status[field]))
    return frozenset(keys)


def is_trending_post(post, keys):
    """True when the post's content (id or URI) is in the trending set."""
    if not keys:
        return False
    content = content_of(post)
    return any(str(content[f]) in keys for f in ('id', 'uri')
               if content.get(f))


def _news_entry(card, key, trending, link):
    return {'key': key, 'url': card.get('url') or key, 'card': card,
            'trending': trending,
            'shared': link['shared'] if link else 0,
            'sharers': list(link['sharers']) if link else [],
            'likes': link['likes'] if link else 0,
            'boosts': link['boosts'] if link else 0,
            'post_id': link['post_id'] if link else None}


def merge_news(trend_links, links, hide_trending=False):
    """News tiles' data: the server's trending order, with links shared in
    the timeline moved to the front ordered by ``shared`` (ties keep the
    trending order), each carrying its sharers. Falls back to the timeline
    links (``trending`` False) when trends are None, empty or hidden.
    ``links`` is ``timeline_links(posts, trim=False)`` for the best match."""
    by_key = {e['key'] if 'key' in e else _link_key(e['url']): e
              for e in links or []}
    if hide_trending or not trend_links:
        return [_news_entry(e['card'], _link_key(e['url']), False, e)
                for e in links or []]
    seen = set()
    shared, rest = [], []
    for card in trend_links:
        if not card.get('url'):
            continue
        key = _link_key(card['url'])
        if key in seen:
            continue
        seen.add(key)
        link = by_key.get(key)
        entry = _news_entry(card, key, True, link)
        (shared if link else rest).append(entry)
    shared.sort(key=lambda e: -e['shared'])        # stable
    return shared + rest


def merge_hashtags(trend_tags, followed_tags, tl_tags, hide_trending=False):
    """Hashtag tiles' data. Trending order, with followed tags moved to the
    front, then tags used in the timeline (each group keeps trending
    order); every tag carries its timeline counts. ``tl_tags`` is
    ``timeline_hashtags(...)`` (all tags). Fallback when trends are None,
    empty or hidden: the timeline tags used by at least 2 distinct accounts,
    in the timeline order."""
    followed = set()
    for tag in followed_tags or []:
        name = tag.get('name') if isinstance(tag, dict) else tag
        if name:
            followed.add(name.lower())
    by_key = {e['key']: e for e in tl_tags or []}

    def entry_for(name, tag, trending):
        key = name.lower()
        tl = by_key.get(key)
        return {'key': key, 'name': name, 'tag': tag, 'trending': trending,
                'following': (key in followed
                              or bool(tag and tag.get('following'))),
                'timeline_posts': tl['timeline_posts'] if tl else 0,
                'timeline_accounts': tl['timeline_accounts'] if tl else 0,
                'accounts': list(tl['accounts']) if tl else [],
                'buckets': tl.get('buckets') if tl else None}

    if hide_trending or not trend_tags:
        return [entry_for(e['name'], None, False)
                for e in tl_tags or []
                if e['timeline_accounts'] >= MIN_FALLBACK_TAG_ACCOUNTS]
    seen = set()
    groups = ([], [], [])
    for tag in trend_tags:
        name = tag.get('name') or ''
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        entry = entry_for(name, tag, True)
        rank = 0 if entry['following'] else (
            1 if entry['timeline_posts'] else 2)
        groups[rank].append(entry)
    return groups[0] + groups[1] + groups[2]


# --------------------------------------------------------------------------
# 2.5 Density and sorts

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


def author_counts(posts):
    counts = {}
    for post in posts:
        key = _acct_id(post.get('account'))
        counts[key] = counts.get(key, 0) + 1
    return counts


def filter_posts(posts, category='all', author=None, boosters=None,
                 native_quotes=True):
    """The All posts list's filter: category, then author. The author
    matches the post's account or any of its other boosters (``boosters``
    from ``dedupe_boosts``). As in Phanpy, an author with no posts or boosts
    in the category is ignored instead of emptying the list. Pass the visible
    (deduplicated) posts."""
    boosters = boosters or {}
    out = [p for p in posts
           if category == 'all' or categorize(p, native_quotes) == category]
    present = set(author_counts(out))
    for post in out:
        present.update(_acct_id(a) for a in boosters.get(post['id'], ()))
    if author is not None and str(author) in present:
        author = str(author)
        out = [p for p in out
               if _acct_id(p.get('account')) == author
               or any(_acct_id(a) == author
                      for a in boosters.get(p['id'], ()))]
    return out


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


# --------------------------------------------------------------------------
# Posts bar

def bin_by_time(data, num_bins, now, key='created_at'):
    """Phanpy ``binByTime``: ``num_bins`` lists of items, by position of
    their ``key`` time between the earliest and the latest (the latest capped
    at ``now``). Items dated after ``now`` go into the last bin. Where
    Phanpy would divide by zero (all items at one instant) or index with NaN,
    everything not in the future goes in bin 0. Items without a parseable
    time are put in bin 0, and an unparseable ``now`` caps at the latest
    item instead of raising."""
    if num_bins < 1:
        return []
    bins = [[] for _ in range(num_bins)]
    stamps = [parse_time(item.get(key)) for item in data]
    valid = [_ms(s) for s in stamps if s]
    if not valid:
        if data:
            bins[0].extend(data)
        return bins
    now_stamp = parse_time(now)
    now_ms = _ms(now_stamp) if now_stamp is not None else max(valid)
    low = min(valid)
    span = min(max(valid), now_ms) - low
    for item, stamp in zip(data, stamps, strict=True):
        if stamp is None:
            bins[0].append(item)
            continue
        ms = _ms(stamp)
        if ms > now_ms:
            bins[-1].append(item)
        elif span <= 0:
            bins[0].append(item)
        else:
            index = math.floor((ms - low) / span * (num_bins - 1))
            bins[min(max(index, 0), num_bins - 1)].append(item)
    return bins


def posts_bar_bins(posts, now):
    """None for up to 160 posts (one dot each, in time order); above that,
    320 time buckets."""
    if len(posts) <= POSTS_BAR_THRESHOLD:
        return None
    return bin_by_time(posts, POSTS_BAR_BINS, now)


# --------------------------------------------------------------------------
# 6.1 Tiers and sizes

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
