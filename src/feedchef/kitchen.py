# SPDX-License-Identifier: GPL-3.0-or-later
"""Kitchen scoring — mashdon ``applyKitchen`` order (not Elk rank)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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


def _age_minutes(status: dict[str, Any]) -> float:
    created = status_created_at(status, content=True)
    if created is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return (now - created).total_seconds() / 60.0


def apply_freshness(score: float, status: dict[str, Any],
                    mod: Modifier) -> float:
    decay = float(mod.decay_minutes or 360)
    if decay <= 0:
        return score
    age = _age_minutes(status)
    if age < 0:
        return score
    factor = max(0.0, min(1.0, 1.0 - age / decay))
    return score * factor


def apply_sentiment_boost(score: float, sentiment: float,
                          mod: Modifier) -> float:
    boost = float(mod.sentiment_boost or 0.0)
    if sentiment > 0 and boost > 0:
        return score * (1.0 + sentiment * boost)
    return score


def local_tag_heat(statuses: list[dict[str, Any]],
                   server: ServerTrendContext) -> dict[str, float]:
    heat: dict[str, float] = {}
    authors: dict[str, set[str]] = {}
    now = datetime.now(timezone.utc)
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


def apply_trending(score: float, status: dict[str, Any],
                   tag_heat: dict[str, float],
                   server: ServerTrendContext,
                   mod: Modifier) -> float:
    content = content_of(status)
    threshold = float(mod.growth_threshold
                      if mod.growth_threshold is not None else 0.3)
    trend_score = 0.0
    for tag in content.get('tags') or []:
        name = str((tag or {}).get('name') or '').lower()
        trend_score = max(trend_score, tag_heat.get(name, 0.0))
    content_id = str(content.get('id') or '')
    if content_id and content_id in server.trending_status_ids:
        trend_score += _STATUS_TREND_BONUS
    if trend_score >= threshold:
        return score * (1.0 + trend_score)
    return score


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
    percentile = float(mod.percentile_threshold
                       if mod.percentile_threshold is not None else 0)
    if percentile <= 0:
        return statuses
    values = [scores.get(str(item.get('id')), 0.0) for item in statuses]
    threshold = _percentile(values, percentile)
    kept = [item for item in statuses
            if scores.get(str(item.get('id')), 0.0) > threshold]
    if kept:
        return kept
    # Would remove 100% with <= ; relax to keep scores == threshold.
    return [item for item in statuses
            if scores.get(str(item.get('id')), 0.0) >= threshold]


def apply_diversity(statuses: list[dict[str, Any]],
                    mod: Modifier) -> list[dict[str, Any]]:
    max_per = int(mod.max_per_cluster if mod.max_per_cluster is not None else 1)
    dimensions = mod.diversity_dimensions or ['author']
    if 'author' not in dimensions:
        return list(statuses)
    counts: dict[str, int] = {}
    kept = []
    for status in statuses:
        author = str(account_of(status, wrapper=True).get('id') or '')
        seen = counts.get(author, 0)
        if seen >= max_per:
            continue
        counts[author] = seen + 1
        kept.append(status)
    return kept


def score_statuses(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    *,
    server: ServerTrendContext | None = None,
    sentiment_by_id: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    server = server or EMPTY_TRENDS
    sentiment_by_id = sentiment_by_id or {}
    scores: dict[str, float] = {}
    meta: dict[str, Any] = {'inert': [], 'heat': None, 'diversity': None}
    tag_heat = local_tag_heat(statuses, server)

    for status in statuses:
        sid = str(status.get('id') or '')
        content_id = str(content_of(status).get('id') or sid)
        score = base_score(status)
        for mod in recipe.kitchen.modifiers:
            if mod.type == 'freshness':
                score = apply_freshness(score, status, mod)
            elif mod.type == 'sentiment_boost':
                meta['inert'].append('sentiment_boost')
                sent = sentiment_by_id.get(sid, 0.0)
                score = apply_sentiment_boost(score, sent, mod)
            elif mod.type == 'trending':
                score = apply_trending(score, status, tag_heat, server, mod)
            elif mod.type in ('similarity_cluster', 'connection'):
                meta['inert'].append(mod.type)
        scores[sid] = score
        scores[content_id] = score

    working = list(statuses)
    for mod in recipe.kitchen.modifiers:
        if mod.type == 'heat':
            before = len(working)
            working = apply_heat(working, scores, mod)
            meta['heat'] = {
                'before': before,
                'after': len(working),
                'percentile': mod.percentile_threshold,
            }

    for mod in recipe.kitchen.modifiers:
        if mod.type == 'diversity':
            before = len(working)
            working = apply_diversity(working, mod)
            meta['diversity'] = {'before': before, 'after': len(working)}
        elif mod.type == 'similarity_cluster':
            meta['inert'].append('similarity_cluster')

    keywords = recipe.kitchen.active_focus_keywords or []
    if keywords:
        meta['inert'].append('active_focus_keywords')
        ordered = working
    elif recipe.kitchen.base_sort == 'chronological':
        with_date = []
        without = []
        for item in working:
            created = status_created_at(item, content=True)
            if created is None:
                without.append(item)
            else:
                with_date.append((created, item))
        with_date.sort(key=lambda pair: pair[0], reverse=True)
        ordered = [item for _created, item in with_date] + without
    else:
        ordered = sorted(
            working,
            key=lambda item: scores.get(str(item.get('id')), 0.0),
            reverse=True,
        )
    # Java DecisionLog: kitchen-filtered posts keep rank -1.
    kept_ids = {str(item.get('id') or '') for item in ordered}
    ranks: dict[str, int] = {}
    for status in statuses:
        sid = str(status.get('id') or '')
        ranks[sid] = -1
    for index, item in enumerate(ordered):
        ranks[str(item.get('id') or '')] = index
    for sid in list(ranks):
        if sid not in kept_ids:
            ranks[sid] = -1
    meta['ranks'] = ranks
    meta['inert'] = sorted(set(meta['inert']))
    return ordered, scores, meta
