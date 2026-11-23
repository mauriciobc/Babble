# SPDX-License-Identifier: GPL-3.0-or-later
"""Kitchen scoring — mashdon ``applyKitchen`` order (not Elk rank)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .audit import NULL
from .recipe import FeedRecipe, Modifier
from .textutil import account_of, content_of, status_created_at

_HALF_LIFE_HOURS = 12.0
_DECAY = math.log(2) / _HALF_LIFE_HOURS
_SERVER_TAG_BOOST = 1.5
_STATUS_TREND_BONUS = 5.0


@dataclass
class ServerTrendContext:
    trending_hashtags: dict[str, float] = field(default_factory=dict)
    trending_status_ids: set[str] = field(default_factory=set)


EMPTY_TRENDS = ServerTrendContext()


def base_score(status: dict[str, Any]) -> float:
    content = content_of(status)
    fav = float(content.get('favourites_count')
                or content.get('favouritesCount') or 0)
    reblog = float(content.get('reblogs_count')
                   or content.get('reblogsCount') or 0)
    reply = float(content.get('replies_count')
                  or content.get('repliesCount') or 0)
    score = fav * 1.0 + reblog * 2.5 + reply * 1.5
    if not math.isfinite(score) or score < 0:
        return 0.0
    return score


def _age_minutes(status: dict[str, Any],
                 now: datetime | None = None) -> float:
    created = status_created_at(status, content=True)
    if created is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    return (now - created).total_seconds() / 60.0


def freshness_factor(status: dict[str, Any], mod: Modifier,
                     now: datetime | None = None) -> float | None:
    """What freshness multiplies the score by; None: it leaves it be."""
    decay = float(mod.decay_minutes or 360)
    if decay <= 0:
        return None
    age = _age_minutes(status, now)
    if age < 0:
        return None
    return max(0.0, min(1.0, 1.0 - age / decay))


def apply_freshness(score: float, status: dict[str, Any],
                    mod: Modifier, now: datetime | None = None) -> float:
    factor = freshness_factor(status, mod, now)
    return score if factor is None else score * factor


def sentiment_factor(sentiment: float, mod: Modifier) -> float | None:
    boost = float(mod.sentiment_boost or 0.0)
    if sentiment > 0 and boost > 0:
        return 1.0 + sentiment * boost
    return None


def apply_sentiment_boost(score: float, sentiment: float,
                          mod: Modifier) -> float:
    factor = sentiment_factor(sentiment, mod)
    return score if factor is None else score * factor


def local_tag_heat(statuses: list[dict[str, Any]],
                   server: ServerTrendContext,
                   now: datetime | None = None) -> dict[str, float]:
    heat: dict[str, float] = {}
    authors: dict[str, set[str]] = {}
    now = now or datetime.now(timezone.utc)
    for status in statuses:
        created = status_created_at(status, content=False)
        if created is None:
            continue
        age_h = max(0.0, (now - created).total_seconds() / 3600.0)
        decay = math.exp(-_DECAY * age_h)
        if decay < 0.01:
            continue
        author = str(account_of(status, wrapper=True).get('id') or '')
        content = content_of(status)
        for tag in content.get('tags') or []:
            name = str((tag or {}).get('name') or '').lower()
            if not name:
                continue
            seen = authors.setdefault(name, set())
            amount = 1.0 * decay if author not in seen else 0.1 * decay
            if author:
                seen.add(author)
            heat[name] = heat.get(name, 0.0) + amount
    cleaned = {}
    for name, value in heat.items():
        if len(authors.get(name, ())) < 2 and value < 1.5:
            continue
        if name in server.trending_hashtags:
            value *= _SERVER_TAG_BOOST
        cleaned[name] = value
    return cleaned


def trending_factor(status: dict[str, Any], tag_heat: dict[str, float],
                    server: ServerTrendContext,
                    mod: Modifier) -> float | None:
    """What trending multiplies the score by; None: the post is not hot."""
    content = content_of(status)
    raw_threshold = mod.growth_threshold
    if raw_threshold is None or float(raw_threshold) <= 0:
        threshold = 0.3
    else:
        threshold = float(raw_threshold)
    trend_score = 0.0
    for tag in content.get('tags') or []:
        name = str((tag or {}).get('name') or '').lower()
        trend_score = max(trend_score, tag_heat.get(name, 0.0))
    content_id = str(content.get('id') or '')
    if content_id and content_id in server.trending_status_ids:
        trend_score += _STATUS_TREND_BONUS
    if trend_score >= threshold:
        return 1.0 + trend_score
    return None


def apply_trending(score: float, status: dict[str, Any],
                   tag_heat: dict[str, float],
                   server: ServerTrendContext,
                   mod: Modifier) -> float:
    factor = trending_factor(status, tag_heat, server, mod)
    return score if factor is None else score * factor


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return -1.0
    if percentile <= 0:
        return -1.0
    ordered = sorted(values)
    if percentile >= 100:
        return ordered[-1]
    rank = (percentile / 100.0) * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def apply_heat(statuses: list[dict[str, Any]], scores: dict[str, float],
               mod: Modifier) -> list[dict[str, Any]]:
    """Hide exactly floor(P x n / 100) of the n posts: the ones with the
    lowest score, the older first among equals, and never all of them
    (a batch of one is never emptied). The rest keep their order."""
    percentile = float(mod.percentile_threshold
                       if mod.percentile_threshold is not None else 0)
    if percentile <= 0 or not statuses:
        return statuses
    total = len(statuses)
    hide = min(int(math.floor(percentile * total / 100.0)), total - 1)
    if hide <= 0:
        return statuses

    def weakest(item):
        created = status_created_at(item, content=True) or _NO_DATE
        return (scores.get(str(item.get('id')), 0.0), created, _id_key(item))

    hidden = {id(item) for item in sorted(statuses, key=weakest)[:hide]}
    return [item for item in statuses if id(item) not in hidden]


def author_key(status: dict[str, Any]) -> str:
    """The person a post counts for in "Limit Posts per Person": the
    account that put it in the feed, so a boost counts for the booster
    (Java does the same), not for the author of the boosted post."""
    return str(account_of(status, wrapper=True).get('id') or '')


def apply_diversity(statuses: list[dict[str, Any]], mod: Modifier,
                    counts: dict[str, int] | None = None
                    ) -> list[dict[str, Any]]:
    """Keep the first ``max_per_cluster`` posts of each person *in the
    order given*, so called on a ranked list it keeps each person's best.
    *counts* is what each person already has in the cook session (older
    pages); it is read, never changed."""
    max_per = int(mod.max_per_cluster if mod.max_per_cluster is not None else 1)
    dimensions = mod.diversity_dimensions or ['author']
    if 'author' not in dimensions:
        return list(statuses)
    seen_counts = dict(counts or {})
    kept = []
    for status in statuses:
        author = author_key(status)
        seen = seen_counts.get(author, 0)
        if seen >= max_per:
            continue
        seen_counts[author] = seen + 1
        kept.append(status)
    return kept


def tally_authors(statuses: list[dict[str, Any]],
                  counts: dict[str, int] | None = None) -> dict[str, int]:
    """*counts* plus one for each post of each person in *statuses*."""
    total = dict(counts or {})
    for status in statuses:
        author = author_key(status)
        total[author] = total.get(author, 0) + 1
    return total


def _id_key(status: dict[str, Any]) -> tuple[int, Any]:
    """Ids compare as integers when they are digits (Mastodon), else as
    text; a tie in time or score then breaks the same way every run."""
    sid = str(status.get('id') or '')
    return (1, int(sid)) if sid.isdigit() else (0, sid)


_NO_DATE = datetime.min.replace(tzinfo=timezone.utc)


def order_statuses(statuses: list[dict[str, Any]], scores: dict[str, float],
                   base_sort: str) -> list[dict[str, Any]]:
    """'chronological': newest first by the created_at of the post the
    reader sees (posts without a date last, in arrival order), ties by id,
    newest id first. Otherwise: highest final score first, ties newest
    first, then by id."""
    if base_sort == 'chronological':
        with_date = []
        without = []
        for item in statuses:
            created = status_created_at(item, content=True)
            if created is None:
                without.append(item)
            else:
                with_date.append((created, _id_key(item), item))
        with_date.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in with_date] + without

    def key(item):
        created = status_created_at(item, content=True) or _NO_DATE
        return (scores.get(str(item.get('id')), 0.0), created, _id_key(item))

    return sorted(statuses, key=key, reverse=True)


def score_statuses(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    *,
    server: ServerTrendContext | None = None,
    sentiment_by_id: dict[str, float] | None = None,
    author_counts: dict[str, int] | None = None,
    now: datetime | None = None,
    audit=None,
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    """Score, filter by heat, order, then cap each person.

    The cap runs after the order so that it keeps each person's best-ranked
    posts, and *author_counts* (posts per person already shown by earlier
    pages of the cook session) makes it a cap on the whole session.

    *now* is the one clock reading of the page (None: read it here); *audit*
    only observes: the factors and the posts each cut removed."""
    audit = NULL if audit is None else audit
    now = now or datetime.now(timezone.utc)
    server = server or EMPTY_TRENDS
    sentiment_by_id = sentiment_by_id or {}
    scores: dict[str, float] = {}
    meta: dict[str, Any] = {'inert': [], 'heat': None, 'diversity': None}
    tag_heat = local_tag_heat(statuses, server, now)
    audit.heat_map(tag_heat)

    for status in statuses:
        sid = str(status.get('id') or '')
        content_id = str(content_of(status).get('id') or sid)
        score = base_score(status)
        audit.factor(sid, 'base', score)
        for mod in recipe.kitchen.modifiers:
            if mod.type == 'freshness':
                factor = freshness_factor(status, mod, now)
                kind = 'fresh'
            elif mod.type == 'sentiment_boost':
                # Posts that are not judged (other languages) keep theirs.
                factor = (sentiment_factor(sentiment_by_id[sid], mod)
                          if sid in sentiment_by_id else None)
                kind = 'sent'
            elif mod.type == 'trending':
                factor = trending_factor(status, tag_heat, server, mod)
                kind = 'trend'
            else:
                factor = None
            if factor is not None:
                score = score * factor
                audit.factor(sid, kind, factor)
            elif mod.type in ('similarity_cluster', 'connection'):
                meta['inert'].append(mod.type)
        scores[sid] = score
        scores[content_id] = score

    working = list(statuses)
    for mod in recipe.kitchen.modifiers:
        if mod.type == 'heat':
            before = len(working)
            kept = apply_heat(working, scores, mod)
            if audit.enabled:
                audit.kitchen_cut('heat_cut', working, kept)
            working = kept
            meta['heat'] = {
                'before': before,
                'after': len(working),
                'percentile': mod.percentile_threshold,
            }

    keywords = recipe.kitchen.active_focus_keywords or []
    if keywords:
        # TF-IDF relevance is inert without NLP; fall back to base_sort
        # (Java uses relevance when TF-IDF is available — never freeze merge).
        meta['inert'].append('active_focus_keywords')
    ordered = order_statuses(working, scores, recipe.kitchen.base_sort)

    for mod in recipe.kitchen.modifiers:
        if mod.type == 'diversity':
            before = len(ordered)
            kept = apply_diversity(ordered, mod, author_counts)
            if audit.enabled:
                audit.kitchen_cut('author_cap', ordered, kept)
            ordered = kept
            meta['diversity'] = {'before': before, 'after': len(ordered)}
        elif mod.type == 'similarity_cluster':
            meta['inert'].append('similarity_cluster')

    # Java DecisionLog: kitchen-filtered posts keep rank -1.
    ranks: dict[str, int] = {}
    for status in statuses:
        ranks[str(status.get('id') or '')] = -1
    for index, item in enumerate(ordered):
        ranks[str(item.get('id') or '')] = index
    meta['ranks'] = ranks
    meta['inert'] = sorted(set(meta['inert']))
    return ordered, scores, meta
