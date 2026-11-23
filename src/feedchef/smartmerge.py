# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart New Posts, the part with no widget (Java section 12).

A recipe's page peeks at page one again every few minutes. The peek is a
cook like any other, so every promise already holds for its posts; what is
decided here is whether it is worth interrupting the reader, how the posts
are merged into the top of the list, and whether that merge kept the
promises of the list as a whole. Nothing in this module imports GTK, so the
page, the guard and ``bench/feedchefaudit`` ask the same functions.
"""

from __future__ import annotations

import math
import statistics
from collections import OrderedDict
from typing import Any, Callable

from . import kitchen as kitchenmod
from . import relevance as relevancemod
from .recipe import FeedRecipe

# Java AlgorithmicTimelineFragment.SCORE_CACHE_SIZE / RERANK_WINDOW_SIZE.
SCORE_CACHE_SIZE = 100
RERANK_WINDOW_SIZE = 50
# Seconds between two peeks of an open recipe page.
PEEK_INTERVAL_S = 300
# A peek's page_index, so its record never shares a (cook, page, hop) with
# a page of the cook: the evaluator reads two logs of one key as one page.
PEEK_PAGE_BASE = 1000

# The promises of the merged top of the list. A merge that breaks one is
# refused (nothing is inserted) and the peek is dropped.
# merge_unscored: a post of the window has no cached score, so its place
# cannot be judged; merge_shape (record only): the order, the window, the
# keys or the size of a recorded merge cannot be what the recipe made;
# merge_empty (record only): a shown peek that inserted nothing.
MERGE_IDS = ('merge_duplicate', 'merge_foreign', 'merge_members',
             'merge_window', 'merge_tail', 'merge_order', 'merge_unscored',
             'merge_shape', 'merge_empty')


class ScoreCache:
    """The final score of each shown post, by id: an LRU of
    ``SCORE_CACHE_SIZE`` entries. The relevance to a focus is kept beside
    it (the order of a recipe with a focus) and leaves with its score.

    *keep* says which ids must never be evicted (the posts still in the
    list): the LRU applies only to ids no longer shown, and the cache may
    grow past its size while the list holds more than that."""

    def __init__(self, size: int = SCORE_CACHE_SIZE,
                 keep: Callable[[str], bool] | None = None):
        self.size = size
        self.keep = keep
        self._scores: OrderedDict[str, float] = OrderedDict()
        self._relevance: dict[str, float] = {}

    def put(self, sid: str, score: float,
            relevance: float | None = None) -> None:
        self._scores[sid] = score
        self._scores.move_to_end(sid)
        if relevance is not None:
            self._relevance[sid] = relevance
        else:
            self._relevance.pop(sid, None)
        if len(self._scores) <= self.size:
            return
        for old in list(self._scores):
            if len(self._scores) <= self.size:
                break
            if self.keep is not None and self.keep(old):
                continue
            del self._scores[old]
            self._relevance.pop(old, None)

    def get(self, sid: str, default: float | None = None) -> float | None:
        """The score of *sid*, which makes it the most recently used."""
        if sid not in self._scores:
            return default
        self._scores.move_to_end(sid)
        return self._scores[sid]

    def relevance(self, sid: str) -> float | None:
        return self._relevance.get(sid)

    def clear(self) -> None:
        self._scores.clear()
        self._relevance.clear()

    def ids(self) -> list[str]:
        return list(self._scores)

    def __contains__(self, sid: object) -> bool:
        return sid in self._scores

    def __len__(self) -> int:
        return len(self._scores)


def threshold(visible_scores: list[float]) -> float:
    """Mean plus one population standard deviation of the scores of the
    posts on screen. With none known it is 0.0, as in Java: any post with
    a positive score is then worth showing."""
    if not visible_scores:
        return 0.0
    return statistics.fmean(visible_scores) + statistics.pstdev(
        visible_scores)


def worth_interrupting(visible_scores: list[float],
                       new_scores: list[float]) -> bool:
    """Whether at least one new post scores above the threshold. With no
    visible post that has a score there is nothing to compare with, and
    the answer is no."""
    if not visible_scores:
        return False
    limit = threshold(visible_scores)
    return any(score > limit for score in new_scores)


def _sid(status: dict[str, Any]) -> str:
    return str(status.get('id') or '')


def order_kind(recipe: FeedRecipe, statuses: list[dict[str, Any]],
               cache: ScoreCache) -> str:
    """What the window is ordered by: 'relevance' (a recipe with a focus
    whose every post has its relevance cached), 'chronological' or
    'score'."""
    if relevancemod.focus_keywords(recipe.kitchen) and statuses and all(
            cache.relevance(_sid(s)) is not None for s in statuses):
        return 'relevance'
    if recipe.kitchen.base_sort == 'chronological':
        return 'chronological'
    return 'score'


def order_window(recipe: FeedRecipe, statuses: list[dict[str, Any]],
                 cache: ScoreCache) -> list[dict[str, Any]]:
    """*statuses* in the recipe's order rule, from the cached scores: the
    kitchen's own tie rules (newer first, then the larger id). A post with
    no cached score counts as 0.0, as in Java."""
    kind = order_kind(recipe, statuses, cache)
    if kind == 'relevance':
        values = {_sid(s): cache.relevance(_sid(s)) for s in statuses}
        return kitchenmod.order_statuses(statuses, values, 'score')
    values = {_sid(s): cache.get(_sid(s), 0.0) for s in statuses}
    return kitchenmod.order_statuses(
        statuses, values,
        'chronological' if kind == 'chronological' else 'score')


def merge_head(recipe: FeedRecipe, current: list[dict[str, Any]],
               new: list[dict[str, Any]], cache: ScoreCache,
               window: int = RERANK_WINDOW_SIZE) -> list[dict[str, Any]]:
    """The list after the new posts are put on top and only its first
    *window* posts are ordered again; what lies beyond keeps its order."""
    merged = list(new) + list(current)
    return order_window(recipe, merged[:window], cache) + merged[window:]


def merge_violations(recipe: FeedRecipe, *, before: list[str],
                     inserted: list[str], after: list[str],
                     peek_order: list[str],
                     statuses: dict[str, dict[str, Any]],
                     cache: ScoreCache,
                     window: int = RERANK_WINDOW_SIZE) -> list[str]:
    """The promises of the merged list that are broken (see
    ``MERGE_IDS``); empty when the merge is sound. *before* and *after*
    are the ids of the list in order, *inserted* those put on top,
    *peek_order* the ids the recorded peek page selected."""
    found: list[str] = []

    def add(promise):
        if promise not in found:
            found.append(promise)

    if len(set(after)) != len(after) or len(set(inserted)) != len(inserted) \
            or set(inserted) & set(before):
        add('merge_duplicate')
    if any(sid not in cache for sid in after[:window]):
        add('merge_unscored')
    if not set(inserted) <= set(peek_order):
        add('merge_foreign')
    if set(after) != set(before) | set(inserted):
        add('merge_members')
    merged = list(inserted) + list(before)
    if set(after[:window]) != set(merged[:window]):
        add('merge_window')
    if after[window:] != merged[window:]:
        add('merge_tail')
    head = after[:window]
    if all(sid in statuses for sid in head):
        expected = [_sid(s) for s in order_window(
            recipe, [statuses[sid] for sid in head], cache)]
        if head != expected:
            add('merge_order')
    return found


def merge_record(recipe: FeedRecipe, *, inserted: list[str],
                 after: list[str], statuses: dict[str, dict[str, Any]],
                 cache: ScoreCache,
                 window: int = RERANK_WINDOW_SIZE) -> dict[str, Any]:
    """What the audit record keeps of a merge: the ids put on top, the
    window after it and the value each of its posts was ordered by (the
    relevance, the final score, or nothing for a chronological order)."""
    head = after[:window]
    kind = order_kind(recipe, [statuses[sid] for sid in head if sid in
                               statuses], cache)
    if kind == 'relevance':
        keys = [cache.relevance(sid) for sid in head]
    elif kind == 'score':
        keys = [cache.get(sid, 0.0) for sid in head]
    else:
        keys = []
    return {'window': window, 'size': len(after),
            'inserted': list(inserted), 'head': head, 'order': kind,
            'keys': keys}


def allowed_orders(recipe: FeedRecipe) -> set[str]:
    """The orders a merge of this recipe's window can have been made by:
    the recipe's base order, and the relevance too with a focus."""
    base = ('chronological' if recipe.kitchen.base_sort == 'chronological'
            else 'score')
    found = {base}
    if relevancemod.focus_keywords(recipe.kitchen):
        found.add('relevance')
    return found


def record_violations(merge: Any, order: list[str],
                      recipe: FeedRecipe | None = None) -> list[str]:
    """The promises a recorded merge (``merge_record``) breaks, judged from
    the record alone: *order* is the ``outputs.order`` of the peek page it
    names, *recipe* the snapshot's (when given, the recorded order must be
    one the recipe can make). The full order rule (ties included) is
    checked live, where the posts are at hand; here a head ordered by a
    number must not rise, and a window is exactly the first
    ``RERANK_WINDOW_SIZE`` posts of a list of the recorded size."""
    found: list[str] = []

    def add(promise):
        if promise not in found:
            found.append(promise)

    if not isinstance(merge, dict):
        return ['merge_shape']
    head = merge.get('head')
    inserted = merge.get('inserted')
    keys = merge.get('keys')
    window = merge.get('window')
    size = merge.get('size')
    if not (isinstance(head, list) and isinstance(inserted, list)
            and isinstance(keys, list)):
        return ['merge_shape']
    if not inserted:
        add('merge_empty')
    if len(set(head)) != len(head) or len(set(inserted)) != len(inserted):
        add('merge_duplicate')
    if not set(inserted) <= set(order):
        add('merge_foreign')
    if window != RERANK_WINDOW_SIZE or isinstance(window, bool):
        add('merge_shape')
    if not isinstance(size, int) or isinstance(size, bool) \
            or size < len(set(inserted)) \
            or len(head) != min(RERANK_WINDOW_SIZE, size):
        add('merge_shape')
    elif size <= RERANK_WINDOW_SIZE and not set(inserted) <= set(head):
        add('merge_members')
    kind = merge.get('order')
    if kind not in ('score', 'relevance', 'chronological') or (
            recipe is not None and kind not in allowed_orders(recipe)):
        add('merge_shape')
    numbers = [k for k in keys if isinstance(k, (int, float))
               and not isinstance(k, bool) and math.isfinite(k)]
    if kind in ('score', 'relevance'):
        if len(numbers) != len(keys) or len(keys) != len(head):
            add('merge_shape')
        elif any(later > earlier
                 for earlier, later in zip(keys, keys[1:], strict=False)):
            add('merge_order')
    elif keys:
        add('merge_shape')
    return found
