# SPDX-License-Identifier: GPL-3.0-or-later
"""Pantry fetch/merge — mashdon FeedEngineImpl.fetchPantry semantics."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .recipe import FeedRecipe, normalize_hashtags
from .textutil import content_of, status_created_at

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


_mlt_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MLT_TTL = 30.0


def _status_has_tag(status: dict[str, Any], tag: str) -> bool:
    content = content_of(status)
    needle = tag.lower()
    for item in content.get('tags') or []:
        if str((item or {}).get('name') or '').lower() == needle:
            return True
    return False


def _filter_excludes(statuses: list[dict], excludes: list[str]) -> list[dict]:
    if not excludes:
        return statuses
    return [item for item in statuses
            if not any(_status_has_tag(item, tag) for tag in excludes)]


def _filter_includes(statuses: list[dict], includes: list[str]) -> list[dict]:
    if not includes:
        return statuses
    return [item for item in statuses
            if any(_status_has_tag(item, tag) for tag in includes)]


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
) -> list[dict[str, Any]]:
    scope = _filter_includes(_filter_excludes(scope_posts, excludes), includes)
    tags = _filter_excludes(hashtag_posts, excludes)
    mlt = _filter_excludes(more_like_this, excludes)
    seen = set()
    merged = []
    for bucket in (mlt, tags, scope):
        for status in bucket:
            sid = str(status.get('id') or '')
            if not sid or sid in seen:
                continue
            seen.add(sid)
            merged.append(status)
    return merged


def fetch_more_like_this(
    recipe: FeedRecipe,
    search: SearchFn,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
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
        return list(cached[1])
    collected: list[dict[str, Any]] = []
    try:
        if hashtags:
            for tag in hashtags[:3]:
                collected.extend(search('#' + tag)[:20])
        else:
            collected.extend(search(keywords[0])[:20])
    except Exception as error:
        log.warning('feedchef: MLT search failed: %s', error)
        collected = []
    # Drop cache entries older than 60 s.
    stale = [name for name, (stamp, _items) in _mlt_cache.items()
             if now - stamp > 60]
    for name in stale:
        _mlt_cache.pop(name, None)
    _mlt_cache[key] = (now, collected)
    return collected


def build_pantry(
    recipe: FeedRecipe,
    *,
    scope_posts: list[dict[str, Any]],
    hashtag_posts: list[dict[str, Any]] | None = None,
    more_like_this: list[dict[str, Any]] | None = None,
    max_id: str | None = None,
    count: int = 80,
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
