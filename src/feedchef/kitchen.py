# SPDX-License-Identifier: GPL-3.0-or-later
"""Kitchen scoring — mashdon ``applyKitchen`` order (not Elk rank)."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import cluster as clustermod
from . import heat as heatmod
from . import relevance as relevancemod
from .audit import NULL
from .contract import FRESHNESS_FLOOR
from .recipe import FeedRecipe, Modifier
from .recipe import max_per as max_per_value
from .textutil import (account_of, content_of, host_of_account,
                       status_created_at)

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
                     now: datetime | None = None,
                     floor: bool = False) -> float | None:
    """What freshness multiplies the score by; None: it leaves it be.
    With *floor* (the recipe also has "trending") a factor under
    `FRESHNESS_FLOOR` is raised to it."""
    decay = float(mod.decay_minutes or 360)
    if decay <= 0:
        return None
    age = _age_minutes(status, now)
    if age < 0:
        return None
    factor = max(0.0, min(1.0, 1.0 - age / decay))
    if floor and factor < FRESHNESS_FLOOR:
        return FRESHNESS_FLOOR
    return factor


def floor_applied(status: dict[str, Any], mod: Modifier,
                  now: datetime | None = None) -> bool:
    """Whether the floor raised this post's freshness factor."""
    raw = freshness_factor(status, mod, now)
    return raw is not None and raw < FRESHNESS_FLOOR


def has_trending(recipe: FeedRecipe) -> bool:
    return any(mod.type == 'trending' for mod in recipe.kitchen.modifiers)


def apply_freshness(score: float, status: dict[str, Any],
                    mod: Modifier, now: datetime | None = None,
                    floor: bool = False) -> float:
    factor = freshness_factor(status, mod, now, floor)
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


_percentile = heatmod.percentile


def apply_heat(statuses: list[dict[str, Any]], scores: dict[str, float],
               mod: Modifier) -> list[dict[str, Any]]:
    """Hide exactly floor(P x n / 100) of the n posts: the ones with the
    lowest score, the older first among equals, and never all of them
    (a batch of one is never emptied). The rest keep their order."""
    total = len(statuses)
    hide = heatmod.hidden_count(mod.percentile_threshold, total)
    if hide <= 0 or not statuses:
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


def instance_key(status: dict[str, Any]) -> str:
    """The server a post counts for in the 'instance' dimension of the
    cap: the host of the account that put it in the feed (as for the
    person), '' when it has none (such a post is never capped, as in Java)."""
    return host_of_account(account_of(status, wrapper=True))


def diversity_dimensions(mod: Modifier) -> tuple[bool, bool]:
    """(by person, by server): what a diversity modifier caps. A modifier
    with no dimensions caps the person, as in Java."""
    dimensions = mod.diversity_dimensions or ['author']
    return 'author' in dimensions, 'instance' in dimensions


def diversity_pass(statuses: list[dict[str, Any]], mod: Modifier,
                   counts: dict[str, int] | None = None,
                   instance_counts: dict[str, int] | None = None
                   ) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """The posts the cap keeps, and the reason each of the others was
    removed for (``id(post)`` -> 'author_cap' or 'instance_cap'). A post
    is kept while its person, and its server when the modifier caps those,
    each have fewer than ``max_per_cluster`` kept posts, so only a kept
    post is counted."""
    max_per = max_per_value(mod.max_per_cluster)
    by_person, by_server = diversity_dimensions(mod)
    people = dict(counts or {})
    servers = dict(instance_counts or {})
    kept = []
    removed: dict[int, str] = {}
    for status in statuses:
        author = author_key(status)
        server = instance_key(status) if by_server else ''
        if by_person and people.get(author, 0) >= max_per:
            removed[id(status)] = 'author_cap'
            continue
        if server and servers.get(server, 0) >= max_per:
            removed[id(status)] = 'instance_cap'
            continue
        if by_person:
            people[author] = people.get(author, 0) + 1
        if server:
            servers[server] = servers.get(server, 0) + 1
        kept.append(status)
    return kept, removed


def apply_diversity(statuses: list[dict[str, Any]], mod: Modifier,
                    counts: dict[str, int] | None = None,
                    instance_counts: dict[str, int] | None = None
                    ) -> list[dict[str, Any]]:
    """Keep the first ``max_per_cluster`` posts of each person (and of
    each server, when the modifier says so) *in the order given*, so called
    on a ranked list it keeps the best of each. *counts* and
    *instance_counts* are what each person and server already have in the
    cook session (older pages); they are read, never changed."""
    return diversity_pass(statuses, mod, counts, instance_counts)[0]


def tally_authors(statuses: list[dict[str, Any]],
                  counts: dict[str, int] | None = None) -> dict[str, int]:
    """*counts* plus one for each post of each person in *statuses*."""
    total = dict(counts or {})
    for status in statuses:
        author = author_key(status)
        total[author] = total.get(author, 0) + 1
    return total


def tally_instances(statuses: list[dict[str, Any]],
                    counts: dict[str, int] | None = None) -> dict[str, int]:
    """*counts* plus one for each post of each server in *statuses* (a post
    with no server is not counted)."""
    total = dict(counts or {})
    for status in statuses:
        server = instance_key(status)
        if server:
            total[server] = total.get(server, 0) + 1
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


def _tie_key(status: dict[str, Any]) -> tuple:
    """(date, id) of the post the reader sees: the larger, the newer."""
    return (status_created_at(status, content=True) or _NO_DATE,
            _id_key(status))


def score_statuses(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    *,
    server: ServerTrendContext | None = None,
    sentiment_by_id: dict[str, float] | None = None,
    author_counts: dict[str, int] | None = None,
    instance_counts: dict[str, int] | None = None,
    now: datetime | None = None,
    audit=None,
) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
    """Score, group, filter by heat, order, then cap each person.

    The order of the stages is Java's, but for the cap: similarity
    grouping, then the heat cut, then the order (by relevance to the
    focus when there is one, else by *base_sort*), and the cap after the
    order so that it keeps each person's best-ranked posts. *author_counts*
    and *instance_counts* (posts per person and per server already shown by
    earlier pages of the cook session) make the cap one on the whole
    session.

    *now* is the one clock reading of the page (None: read it here); *audit*
    only observes: the factors and the posts each cut removed."""
    audit = NULL if audit is None else audit
    now = now or datetime.now(timezone.utc)
    server = server or EMPTY_TRENDS
    sentiment_by_id = sentiment_by_id or {}
    scores: dict[str, float] = {}
    meta: dict[str, Any] = {'inert': [], 'heat': None, 'diversity': None,
                            'heat_steps': [], 'fire': None,
                            'cluster': None, 'unavailable': {},
                            # What the final guard checks the page against
                            # (contract.ListFacts): each post's freshness
                            # factor and whether the floor raised it, the
                            # relevance the order used, the group of each
                            # post.
                            'fresh': {}, 'relevance_scores': None,
                            'groups': None}
    tag_heat = local_tag_heat(statuses, server, now)
    audit.heat_map(tag_heat)
    floor = has_trending(recipe)
    words = relevancemod.WordCache()
    audit.words(words)
    meta['inert'] = sorted({mod.type for mod in recipe.kitchen.modifiers
                            if mod.type == 'connection'})

    for status in statuses:
        sid = str(status.get('id') or '')
        content_id = str(content_of(status).get('id') or sid)
        score = base_score(status)
        audit.factor(sid, 'base', score)
        for mod in recipe.kitchen.modifiers:
            if mod.type == 'freshness':
                factor = freshness_factor(status, mod, now, floor)
                kind = 'fresh'
                lifted = floor and floor_applied(status, mod, now)
                if lifted:
                    audit.fresh_floor(sid)
                if factor is not None:
                    meta['fresh'][sid] = (factor, bool(lifted))
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
        scores[sid] = score
        scores[content_id] = score

    working = list(statuses)
    groupings = [mod for mod in recipe.kitchen.modifiers
                 if mod.type == 'similarity_cluster']
    if groupings:
        working = _group(working, groupings[0], meta, audit, words)
        if len(groupings) > 1:
            # Only the first one is applied; the rest are said, not
            # silently dropped.
            meta['inert'] = sorted({*meta['inert'], 'similarity_cluster#2'})
    for mod in recipe.kitchen.modifiers:
        if mod.type == 'heat':
            working = _heat(working, scores, mod, meta, audit)

    keywords = relevancemod.focus_keywords(recipe.kitchen)
    ordered = None
    if keywords:
        # Java: a focus replaces the base sort. The relevance is the
        # cosine of each post with the focus, over the posts that are left.
        weights = recipe.kitchen.active_focus_weights
        found = relevancemod.relevance_scores(working, keywords, weights,
                                              words)
        if relevancemod.matched(found) or not working:
            audit.relevance(found, keywords, weights)
            meta['relevance'] = len(keywords)
            meta['relevance_scores'] = found
            ordered = order_statuses(working, found, 'score')
        else:
            # Babble, not Java: when no post has any relevance to the focus
            # the page would silently become "newest first", whatever the
            # recipe asks. It keeps the recipe's order and says so.
            meta['focus_unmatched'] = True
            audit.focus_unmatched()
    if ordered is None:
        ordered = order_statuses(working, scores, recipe.kitchen.base_sort)

    for mod in recipe.kitchen.modifiers:
        if mod.type == 'diversity':
            before = len(ordered)
            kept, removed = diversity_pass(
                ordered, mod, author_counts, instance_counts)
            if audit.enabled:
                for reason in ('author_cap', 'instance_cap'):
                    audit.kitchen_cut(reason, ordered, [
                        item for item in ordered
                        if removed.get(id(item)) != reason])
            ordered = kept
            meta['diversity'] = {'before': before, 'after': len(ordered)}

    meta['fire'] = _fire(ordered, scores)
    audit.heat_steps(meta['heat_steps'])
    audit.fire(meta['fire'])
    # Java DecisionLog: kitchen-filtered posts keep rank -1.
    ranks: dict[str, int] = {}
    for status in statuses:
        ranks[str(status.get('id') or '')] = -1
    for index, item in enumerate(ordered):
        ranks[str(item.get('id') or '')] = index
    meta['ranks'] = ranks
    meta['inert'] = sorted(set(meta['inert']))
    return ordered, scores, meta


def _heat(working: list[dict[str, Any]], scores: dict[str, float],
          mod: Modifier, meta: dict[str, Any], audit
          ) -> list[dict[str, Any]]:
    """The heat cut of one modifier, at the first step that leaves the page
    enough posts (see ``heat.used``); *meta* and the audit say which
    step was asked for and which one cut."""
    before = len(working)
    requested = heatmod.whole(heatmod.clean(mod.percentile_threshold))
    percent = heatmod.used(requested, before)
    stepped = mod if percent == requested else dataclasses.replace(
        mod, percentile_threshold=percent)
    kept = apply_heat(working, scores, stepped)
    if audit.enabled:
        audit.kitchen_cut('heat_cut', working, kept)
    row = {'requested': requested, 'used': percent, 'before': before,
           'after': len(kept)}
    meta['heat_steps'].append(row)
    meta['heat'] = {'before': before, 'after': len(kept),
                    'percentile': mod.percentile_threshold, **{
                        key: row[key] for key in ('requested', 'used')}}
    return kept


def _fire(ordered: list[dict[str, Any]], scores: dict[str, float]
          ) -> dict[str, Any]:
    """The Hot badge of the page: the score a post must beat (the 90th
    percentile of the scores of the posts shown) and the ids of the posts
    that beat it."""
    shown = [(str(item.get('id') or ''),
              scores.get(str(item.get('id') or ''), 0.0))
             for item in ordered]
    threshold = heatmod.fire_threshold([score for _sid, score in shown])
    return {'threshold': threshold, 'scores': [s for _sid, s in shown],
            'ids': [sid for sid, score in shown
                    if threshold is not None and score > threshold]}


def _group(working: list[dict[str, Any]], mod: Modifier,
           meta: dict[str, Any], audit,
           words: relevancemod.WordCache) -> list[dict[str, Any]]:
    """Similarity grouping of the page (see ``cluster.py``): the posts
    each group keeps, or all of them when it did not run, which *meta* and
    the audit then say and why."""
    outcome = clustermod.run(working, mod, score_of=base_score,
                             tie_key=_tie_key, words=words)
    meta['cluster'] = outcome.record()
    audit.cluster(outcome)
    if outcome.status == 'ok':
        meta['groups'] = {sid: group[0]
                          for sid, group in outcome.groups.items()}
    if outcome.status == 'unavailable':
        meta['unavailable']['similarity_cluster'] = outcome.reason
    if outcome.status != 'ok':
        return working
    if audit.enabled:
        audit.kitchen_cut('cluster_cap', working, outcome.kept)
    return outcome.kept
