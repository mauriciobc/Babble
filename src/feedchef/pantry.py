# SPDX-License-Identifier: GPL-3.0-or-later
"""Pantry fetch/merge — mashdon FeedEngineImpl.fetchPantry semantics."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import contract
from .recipe import FeedRecipe, normalize_hashtags
from .textutil import status_created_at

log = logging.getLogger('babble.feedchef')

FetchFn = Callable[[], list[dict[str, Any]]]
SearchFn = Callable[[str], list[dict[str, Any]]]


@dataclass
class PantryResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    scope_items: list[dict[str, Any]] = field(default_factory=list)
    scope_oldest_id: str | None = None
    has_more: bool = False
    timed_out: bool = False
    cancelled: bool = False
    fetched_count: int = 0
    deduplicated_count: int = 0


_mlt_cache: dict[str, tuple[float, list[dict[str, Any]],
                            dict[str, list[str]],
                            dict[str, list[dict[str, Any]]]]] = {}
_MLT_TTL = 30.0


def _filter_excludes(statuses: list[dict], excludes: list[str],
                     trace=None, bucket: str = '') -> list[dict]:
    if not excludes:
        return statuses
    kept = []
    for item in statuses:
        if any(contract.has_tag(item, tag) for tag in excludes):
            if trace is not None and item.get('id'):
                trace.drop(bucket, str(item['id']), 'pantry_excluded_tag')
        else:
            kept.append(item)
    return kept


def _filter_includes(statuses: list[dict], includes: list[str],
                     trace=None, bucket: str = '') -> list[dict]:
    if not includes:
        return statuses
    kept = []
    for item in statuses:
        if any(contract.has_tag(item, tag) for tag in includes):
            kept.append(item)
        elif trace is not None and item.get('id'):
            trace.drop(bucket, str(item['id']),
                       'pantry_missing_included_tag')
    return kept


def oldest_id(statuses: list[dict[str, Any]]) -> str | None:
    if not statuses:
        return None
    best = None
    best_ts = None
    for status in statuses:
        created = status_created_at(status, content=True)
        sid = str(status.get('id') or '')
        if not sid:
            continue
        if best is None or (
                created is not None and (best_ts is None or created < best_ts)
                ) or (created is None and best_ts is None and sid < best):
            best = sid
            best_ts = created
    return best


def scope_cursor(statuses: list[dict[str, Any]]) -> str | None:
    """Paging cursor of a scope page: the smallest wrapper id.

    Mastodon pages by the id of the timeline entry (the wrapper of a
    boost), not by the created_at of the boosted post, so the cursor is
    taken from the wrapper ids. Digit ids compare as integers; for any
    other id the page is newest first, so the last one is the oldest.
    """
    ids = [str(status.get('id') or '') for status in statuses]
    ids = [sid for sid in ids if sid]
    if not ids:
        return None
    if all(sid.isdigit() for sid in ids):
        return min(ids, key=int)
    return ids[-1]


def merge_pantry(
    *,
    more_like_this: list[dict[str, Any]],
    hashtag_posts: list[dict[str, Any]],
    scope_posts: list[dict[str, Any]],
    includes: list[str],
    excludes: list[str],
    origins=None,
) -> list[dict[str, Any]]:
    # *origins*: an optional ``audit.PantryTrace``, an out-map for the audit
    # (where each post came from, why it was dropped); never read here.
    # Included hashtags bind every source whose posts are not tagged by
    # construction: the scope and the More Like This search (a focus
    # narrows within the recipe, it does not escape it). A hashtag
    # timeline is the posts of one included tag, so it matches already;
    # the final contract guard asserts it.
    scope = _filter_includes(
        _filter_excludes(scope_posts, excludes, origins, 'scope'),
        includes, origins, 'scope')
    tags = _filter_excludes(hashtag_posts, excludes, origins, 'hashtag')
    mlt = _filter_includes(
        _filter_excludes(more_like_this, excludes, origins, 'focus'),
        includes, origins, 'focus')
    seen = set()
    merged = []
    for name, bucket in (('focus', mlt), ('hashtag', tags),
                         ('scope', scope)):
        for status in bucket:
            sid = str(status.get('id') or '')
            if not sid or sid in seen:
                if origins is not None:
                    if sid:
                        origins.duplicate(name, sid)
                    else:
                        origins.no_id += 1
                continue
            seen.add(sid)
            merged.append(status)
            if origins is not None:
                origins.keep(name, sid)
    return merged


def fetch_more_like_this(
    recipe: FeedRecipe,
    search: SearchFn,
    *,
    now: float | None = None,
    terms: dict[str, list[str]] | None = None,
    sequences: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    # *terms*: an optional out-map, post id -> the focus terms it was
    # found by (for the audit); a cache hit fills it too. *sequences*
    # likewise: term -> the posts its search gave, in order.
    hashtags = normalize_hashtags(recipe.kitchen.active_focus_hashtags or [])
    keywords = [str(item).strip().lower()
                for item in (recipe.kitchen.active_focus_keywords or [])
                if str(item).strip()]
    if not hashtags and not keywords:
        return []
    key = '|'.join(hashtags) + '#' + '|'.join(keywords[:1])
    now = time.time() if now is None else now
    cached = _mlt_cache.get(key)
    if cached and now - cached[0] <= _MLT_TTL:
        if terms is not None:
            terms.update({sid: list(found) for sid, found in cached[2].items()})
        if sequences is not None:
            sequences.update({term: list(found)
                              for term, found in cached[3].items()})
        return list(cached[1])
    collected: list[dict[str, Any]] = []
    found_by: dict[str, list[str]] = {}
    by_term: dict[str, list[dict[str, Any]]] = {}
    try:
        queries = ([(tag, '#' + tag) for tag in hashtags[:3]] if hashtags
                   else [(keywords[0], keywords[0])])
        for term, query in queries:
            found = search(query)[:20]
            collected.extend(found)
            by_term[term] = list(found)
            for status in found:
                names = found_by.setdefault(str(status.get('id') or ''), [])
                if term not in names:
                    names.append(term)
    except Exception as error:
        log.warning('feedchef: MLT search failed: %s', error)
        collected = []
        found_by = {}
        by_term = {}
    # Drop cache entries older than 60 s.
    stale = [name for name, (stamp, *_rest) in _mlt_cache.items()
             if now - stamp > 60]
    for name in stale:
        _mlt_cache.pop(name, None)
    _mlt_cache[key] = (now, collected, found_by, by_term)
    if terms is not None:
        terms.update({sid: list(found) for sid, found in found_by.items()})
    if sequences is not None:
        sequences.update(by_term)
    return collected


def build_pantry(
    recipe: FeedRecipe,
    *,
    scope_posts: list[dict[str, Any]],
    hashtag_posts: list[dict[str, Any]] | None = None,
    more_like_this: list[dict[str, Any]] | None = None,
    max_id: str | None = None,
    count: int = 80,
    origins=None,
) -> PantryResult:
    includes = recipe.pantry.hashtags_include
    excludes = recipe.pantry.hashtags_exclude
    tags = hashtag_posts or []
    mlt = more_like_this or []
    if max_id is not None:
        tags = []
        mlt = []
    merged = merge_pantry(
        more_like_this=mlt,
        hashtag_posts=tags,
        scope_posts=scope_posts,
        includes=includes,
        excludes=excludes,
        origins=origins,
    )
    # Pagination follows the scope page (Babble cursor = scope's smallest
    # wrapper id).
    # Hashtag/MLT enrichment on page 1 must not hide further scope pages.
    return PantryResult(
        items=merged,
        scope_items=list(scope_posts),
        scope_oldest_id=scope_cursor(scope_posts),
        has_more=len(scope_posts) >= count,
        fetched_count=len(scope_posts) + len(tags) + len(mlt),
        deduplicated_count=len(merged),
    )
