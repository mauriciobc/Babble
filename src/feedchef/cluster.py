# SPDX-License-Identifier: GPL-3.0-or-later
"""Similarity grouping: Java's `similarity_cluster` kitchen modifier.

The posts of a page are grouped by how alike they are, and each group may
show only some of its posts (`max_per_cluster`), chosen by a strategy.
It is what Java does in `FeedEngineImpl.applySimilarityClustering`,
`PostSimilarityCalculator`, `KMeansClustering` and `ClusterSelector`, with
the changes listed below.

* **Similarity** of two posts is the sum of three terms (the weights of the
  current Java, `WEIGHTS`): the Jaccard index of their hashtags, the
  cosine of their TF-IDF vectors (`relevance.cosine`) and 1 for the same
  person (the account that put the post in the feed, a booster for a
  boost) or 0. The distance is `1 - similarity`.
* **Number of groups**: `k = min(5, max(1, n // 10))`, and fewer than ten
  posts are not grouped at all (the modifier then does nothing, and the
  record says so). `similarity_threshold`, which Java stores and never
  reads, is not read here either.
* **Grouping** is k-medoids by FasterPAM (the `kmedoids` package) from a
  deterministic start (`INIT`, `SEED`, `MAX_ITER`, all recorded with the
  version of the library), where Java runs a randomly started PAM-like loop.
  The same posts **in the same order** always give the same groups (the
  distances are built in page order, and a tie between two equally good
  medoids is settled by position, so another order may group differently); a
  replay reproduces them. It needs the `kmedoids` and `numpy`
  packages; without them (or if the call fails) the result says
  `unavailable` and why, and nothing is removed: the caller records that,
  it is never a quiet no-op.
* **Selection** is Java's round robin: each group offers its posts in the
  order of its strategy, and a group may keep `max_per_cluster` of them
  (the kept posts keep their order in the page). `best_score` orders by
  engagement (replies, boosts and favourites, weighted, Java's raw count, not
  the score after the modifiers), `most_recent` by date, and `centroid`
  starts with the post at the middle of the group (the medoid) and goes on
  by engagement. Equal keys are broken the way the kitchen breaks ties
  (newest first, then the id), where Java kept the order of arrival.

Everything here runs on the engine worker: the first call imports numpy
and kmedoids (`deps.get`), which must not happen on the main thread. No
GTK.
"""

from __future__ import annotations

import functools
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

from . import deps
from .recipe import max_per
from .relevance import Vector, WordCache, cosine, vectors_of
from .textutil import account_of, content_of

log = logging.getLogger('babble.feedchef')

# Hashtags, text, same person: Java's `PostSimilarityCalculator` at
# bc0b874 (the first version weighed them 0.3, 0.5 and 0.2).
WEIGHTS = (0.1, 0.7, 0.2)
# Fewer posts than this are not grouped (Java).
MIN_POSTS = 10
MAX_GROUPS = 5
# The start and the seed given to FasterPAM: with 'build' the start is
# fixed by the distances, and the seed pins whatever else is random.
INIT = 'build'
SEED = 0
MAX_ITER = 100
STRATEGIES = ('best_score', 'most_recent', 'centroid')
# Java reads only "most_recent" and "centroid"; anything else is
# best_score. "centroid_then_score" is the name of its enum.
_ALIASES = {'centroid_then_score': 'centroid'}


def strategy_of(name: Any) -> str:
    """The strategy a recipe's `cluster_strategy` means."""
    name = _ALIASES.get(str(name or ''), str(name or ''))
    return name if name in STRATEGIES else 'best_score'


def group_count(posts: int) -> int:
    """Java's k."""
    return min(MAX_GROUPS, max(1, posts // 10))


def max_per_cluster(value: Any) -> int:
    """How many posts a group may keep: the recipe's number, 1 when it is
    missing or malformed (Java returned no post at all for 0), never below
    1, and never an exception (``recipe.max_per``)."""
    return max_per(value)


@dataclass
class ClusterRun:
    """What one grouping did. *status* is 'ok', 'skipped' (too few posts)
    or 'unavailable' (*reason* says why); only 'ok' removes posts."""

    status: str
    reason: str = ''
    kept: list[dict[str, Any]] = field(default_factory=list)
    n: int = 0
    k: int = 0
    strategy: str = 'best_score'
    per_cluster: int = 1
    loss: float | None = None
    # status of each post, by the id of its entry: (group, medoid's id).
    groups: dict[str, tuple[int, str]] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        """The page-level part of the audit record."""
        out: dict[str, Any] = {
            'status': self.status, 'n': self.n, 'k': self.k,
            'strategy': self.strategy, 'max_per': self.per_cluster,
            'init': INIT, 'seed': SEED, 'max_iter': MAX_ITER,
            'weights': list(WEIGHTS), 'versions': versions()}
        if self.reason:
            out['reason'] = self.reason
        if self.status == 'ok':
            out['loss'] = self.loss
        return out


@functools.lru_cache(maxsize=1)
def _versions() -> tuple:
    from importlib import metadata
    found = []
    for name in ('kmedoids', 'numpy'):
        try:
            found.append((name, metadata.version(name)))
        except metadata.PackageNotFoundError:
            found.append((name, ''))
    return tuple(found)


def versions() -> dict[str, str]:
    """The versions of the libraries the groups depend on (a different one
    may group differently: a replay then says so instead of calling it a
    mismatch). Reads the installed metadata; imports nothing."""
    return dict(_versions())


def _tags(status: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str((tag or {}).get('name') or '').lower()
        for tag in content_of(status).get('tags') or []
        if (tag or {}).get('name'))


def _person(status: dict[str, Any]) -> str:
    return str(account_of(status, wrapper=True).get('id') or '')


def hashtag_similarity(first: frozenset[str], second: frozenset[str]
                       ) -> float:
    """The Jaccard index of two sets of hashtags; 0.0 if either is empty."""
    if not first or not second:
        return 0.0
    shared = len(first & second)
    if not shared:
        return 0.0
    return shared / (len(first) + len(second) - shared)


def similarity(weights: tuple[float, float, float], tags: tuple,
               text: float, people: tuple[str, str]) -> float:
    """Java's `calculateSimilarity` for two different posts."""
    author = 1.0 if people[0] and people[0] == people[1] else 0.0
    return (hashtag_similarity(*tags) * weights[0]) \
        + (text * weights[1]) + (author * weights[2])


def similarity_rows(statuses: list[dict[str, Any]],
                    words: WordCache | None = None) -> list[list[float]]:
    """The similarity of every pair of *statuses* (1.0 on the diagonal),
    computed once for each pair, so the matrix is exactly symmetric."""
    count = len(statuses)
    vectors: list[Vector] = vectors_of(statuses, words)
    tags = [_tags(status) for status in statuses]
    people = [_person(status) for status in statuses]
    rows = [[1.0] * count for _ in range(count)]
    for i in range(count):
        for j in range(i + 1, count):
            value = similarity(
                WEIGHTS, (tags[i], tags[j]), cosine(vectors[i], vectors[j]),
                (people[i], people[j]))
            rows[i][j] = rows[j][i] = value
    return rows


def select(statuses: list[dict[str, Any]], labels: list[int],
           medoids: list[int], strategy: str, per_cluster: int,
           score_of: Callable[[dict], float],
           tie_key: Callable[[dict], Any]) -> list[int]:
    """The indexes of the posts each group keeps (Java's
    `ClusterSelector.selectDiversePosts`), in the order of the page. Group
    `c` has its medoid at `medoids[c]`."""
    def newest(index):
        return tie_key(statuses[index])

    def best(index):
        return score_of(statuses[index])

    chosen: list[int] = []
    for group, medoid in enumerate(medoids):
        members = [i for i, label in enumerate(labels) if label == group]
        if strategy == 'centroid':
            first = [medoid]
            rest = [i for i in members if i != medoid]
        else:
            first, rest = [], members
        if strategy == 'most_recent':
            rest = sorted(rest, key=newest, reverse=True)
        else:
            rest = sorted(rest, key=lambda i: (best(i), newest(i)),
                          reverse=True)
        chosen.extend((first + rest)[:per_cluster])
    return sorted(chosen)


class Unavailable(Exception):
    """The grouping cannot run here; the message says why."""


def fit(statuses: list[dict[str, Any]], groups: int,
        words: WordCache | None = None
        ) -> tuple[list[int], list[int], float]:
    """Group *statuses* into *groups* by k-medoids (FasterPAM) over the
    distances `1 - similarity`: the group of each post, the index of each
    group's medoid (groups are numbered by the position of their medoid in
    the page, whatever order FasterPAM returns them in) and the loss (the
    sum of the distances of the posts to their medoid). Raises
    ``Unavailable`` when numpy or kmedoids is missing or fails."""
    reason = deps.unavailable_reason('numpy', 'kmedoids')
    numpy = deps.get('numpy') if not reason else None
    medoid_lib = deps.get('kmedoids') if numpy is not None else None
    if medoid_lib is None:
        raise Unavailable(reason or deps.unavailable_reason(
            'numpy', 'kmedoids') or 'kmedoids could not be imported')
    try:
        rows = similarity_rows(statuses, words)
        distance = numpy.array(rows, dtype=numpy.float64)
        distance = numpy.maximum(0.0, 1.0 - distance)
        result = medoid_lib.fasterpam(
            distance, groups, init=INIT, random_state=SEED, max_iter=MAX_ITER,
            n_cpu=1)
        labels = [int(label) for label in result.labels]
        medoids = [int(medoid) for medoid in result.medoids]
        loss = float(result.loss)
        count = len(statuses)
        if len(labels) != count or len(medoids) != groups \
                or len(set(medoids)) != groups \
                or not all(0 <= m < count for m in medoids) \
                or not all(0 <= label < groups for label in labels) \
                or not all(labels[m] == g for g, m in enumerate(medoids)) \
                or not math.isfinite(loss):
            raise ValueError('malformed result')
    except Exception as error:  # a broken build: say so, keep every post
        log.warning('feedchef: similarity grouping failed: %s', error)
        raise Unavailable(
            'kmedoids failed: %s' % type(error).__name__) from error
    order = sorted(range(len(medoids)), key=lambda c: medoids[c])
    renumber = {old: new for new, old in enumerate(order)}
    return ([renumber[label] for label in labels],
            [medoids[old] for old in order], loss)


def run(statuses: list[dict[str, Any]], mod, *,
        score_of: Callable[[dict], float],
        tie_key: Callable[[dict], Any],
        words: WordCache | None = None) -> ClusterRun:
    """Group *statuses* and keep each group's share, as *mod* (a
    `similarity_cluster` modifier) says. *score_of* is a post's
    engagement and *tie_key* its (date, id) key, newest greatest."""
    strategy = strategy_of(mod.cluster_strategy)
    per = max_per_cluster(mod.max_per_cluster)
    count = len(statuses)
    outcome = ClusterRun('skipped', n=count, strategy=strategy,
                         per_cluster=per, kept=list(statuses))
    if count < MIN_POSTS:
        outcome.reason = 'fewer than %d posts' % MIN_POSTS
        return outcome
    outcome.k = group_count(count)
    try:
        labels, medoids, loss = fit(statuses, outcome.k, words)
    except Unavailable as error:
        outcome.status = 'unavailable'
        outcome.reason = str(error)
        return outcome
    keep = select(statuses, labels, medoids, strategy, per, score_of,
                  tie_key)
    wanted = set(keep)
    outcome.status = 'ok'
    outcome.loss = loss
    outcome.kept = [status for index, status in enumerate(statuses)
                    if index in wanted]
    for index, status in enumerate(statuses):
        outcome.groups[str(status.get('id') or '')] = (
            labels[index], str(statuses[medoids[labels[index]]].get('id')
                               or ''))
    return outcome
