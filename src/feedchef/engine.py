# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed engine orchestrator — one instance per view (no account singleton)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from . import diet as dietmod
from . import kitchen as kitchenmod
from . import network_reach as reachmod
from . import pantry as pantrymod
from .decisionlog import FeedDecisionLogger
from .recipe import FeedRecipe

log = logging.getLogger('babble.feedchef')

StageCallback = Callable[[str, list[dict[str, Any]]], None]
DoneCallback = Callable[['FeedResult'], None]
ErrorCallback = Callable[[BaseException], None]


@dataclass
class FeedResult:
    items: list[dict[str, Any]]
    next_page_max_id: str | None
    has_more: bool
    score_map: dict[str, float] = field(default_factory=dict)
    scope_oldest_id: str | None = None
    kitchen_meta: dict[str, Any] = field(default_factory=dict)
    logger: FeedDecisionLogger | None = None


class FeedEngine:
    """Per-view engine. ``fetch_scope(max_id, count)`` etc. injected by UI."""

    # Java fetchPantry: 30 s plus 10 s per hashtag fetch. Attributes so a
    # test can lower them.
    pantry_timeout_base = 30.0
    pantry_timeout_per_tag = 10.0

    def __init__(
        self,
        *,
        account_key: str,
        self_account_id: str,
        fetch_scope: Callable[[str | None, int], list[dict[str, Any]]],
        fetch_hashtag: Callable[[str, int], list[dict[str, Any]]] | None = None,
        search_statuses: Callable[[str], list[dict[str, Any]]] | None = None,
        fetch_trends: Callable[[], kitchenmod.ServerTrendContext] | None = None,
        fetch_following_page: Callable[[str | None],
                                      tuple[list[dict], str | None]] | None = None,
        ui_idle: Callable[[Callable[[], None]], None] | None = None,
    ):
        self.account_key = account_key
        self.self_account_id = self_account_id
        self.fetch_scope = fetch_scope
        self.fetch_hashtag = fetch_hashtag
        self.search_statuses = search_statuses
        self.fetch_trends = fetch_trends
        self.fetch_following_page = fetch_following_page
        self.ui_idle = ui_idle or (lambda cb: cb())
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='FeedEngine')
        self._trends_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='FeedEngine-trends')
        self._reach_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='NetworkReach')
        self._current = None
        self._cancel = threading.Event()
        self._trend_cache: tuple[float, kitchenmod.ServerTrendContext] | None = None
        self.reach = reachmod.NetworkReachCalculator(
            account_key, self_account_id)

    def init_preload(self) -> None:
        if self.fetch_following_page is None:
            return

        def work():
            reachmod.preload_following(self.reach, self.fetch_following_page)

        self._reach_executor.submit(work)

    def cancel(self) -> None:
        self._cancel.set()
        current = self._current
        if current is not None:
            current.cancel()

    def shutdown(self) -> None:
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._trends_executor.shutdown(wait=False, cancel_futures=True)
        self._reach_executor.shutdown(wait=False, cancel_futures=True)

    def process_feed(
        self,
        recipe: FeedRecipe,
        *,
        max_id: str | None = None,
        count: int = 80,
        on_stage: StageCallback | None = None,
        on_done: DoneCallback | None = None,
        on_error: ErrorCallback | None = None,
    ):
        self.cancel()
        self._cancel = threading.Event()
        cancel = self._cancel
        generation = uuid.uuid4().hex

        def run():
            try:
                result = self._process_sync(
                    recipe, max_id=max_id, count=count,
                    on_stage=on_stage, cancel=cancel, generation=generation)
                if cancel.is_set():
                    return
                if on_done:
                    self.ui_idle(lambda: on_done(result))
            except Exception as error:
                if cancel.is_set():
                    return
                log.exception('feedchef: process_feed failed')
                if on_error:
                    err = error
                    self.ui_idle(lambda err=err: on_error(err))

        self._current = self._executor.submit(run)
        return generation

    def process_feed_sync(
        self,
        recipe: FeedRecipe,
        *,
        max_id: str | None = None,
        count: int = 80,
    ) -> FeedResult:
        return self._process_sync(
            recipe, max_id=max_id, count=count,
            on_stage=None, cancel=threading.Event(), generation='sync')

    def _server_trends(self, recipe: FeedRecipe
                       ) -> kitchenmod.ServerTrendContext:
        """Run on the trends worker only — call ``fetch_trends`` directly.

        Never nest ``submit``+``result`` on ``_trends_executor`` (that
        deadlocks the single worker and was Java's failure mode).
        """
        wants = any(mod.type == 'trending'
                    for mod in recipe.kitchen.modifiers)
        if not wants or self.fetch_trends is None:
            return kitchenmod.EMPTY_TRENDS
        now = time.time()
        if self._trend_cache and now - self._trend_cache[0] < 300:
            return self._trend_cache[1]
        try:
            context = self.fetch_trends()
        except Exception as error:
            log.warning('feedchef: trends fetch failed: %s', error)
            return kitchenmod.EMPTY_TRENDS
        self._trend_cache = (now, context)
        return context

    def _process_sync(
        self,
        recipe: FeedRecipe,
        *,
        max_id: str | None,
        count: int,
        on_stage: StageCallback | None,
        cancel: threading.Event,
        generation: str,
    ) -> FeedResult:
        logger = FeedDecisionLogger(recipe.id)
        started = time.time()
        # Trends on a separate executor (Babble deviation from Java deadlock).
        trends_future = None
        if any(mod.type == 'trending' for mod in recipe.kitchen.modifiers):
            trends_future = self._trends_executor.submit(
                self._server_trends, recipe)

        hashtag_posts: list[dict[str, Any]] = []
        mlt_posts: list[dict[str, Any]] = []
        scope_posts: list[dict[str, Any]] = []
        timed_out = False

        if cancel.is_set():
            raise InterruptedError('cancelled')

        # MLT before scope latch wait (Java order).
        if max_id is None and self.search_statuses is not None:
            mlt_posts = pantrymod.fetch_more_like_this(
                recipe, self.search_statuses)

        tags = []
        if max_id is None and self.fetch_hashtag is not None:
            tags = list(recipe.pantry.hashtags_include[:3])
        # One pool per run: a fetch that outlives its deadline cannot be
        # cancelled, and must not hold a worker a later run needs. Its
        # thread ends on its own (the bridge bounds every fetch).
        pool = ThreadPoolExecutor(
            max_workers=1 + len(tags), thread_name_prefix='FeedEngine-pantry')
        scope_future = pool.submit(self.fetch_scope, max_id, count)
        tag_futures = [(tag, pool.submit(self.fetch_hashtag, tag, count))
                       for tag in tags]
        pool.shutdown(wait=False)
        timeout = (self.pantry_timeout_base
                   + self.pantry_timeout_per_tag * len(tags))
        futures = [scope_future] + [future for _tag, future in tag_futures]
        # Sliced wait: a cancel must not leave the single run worker
        # blocked until the deadline.
        deadline = time.monotonic() + timeout
        pending = set(futures)
        while pending and not cancel.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                break
            _done, pending = wait(pending, timeout=min(0.1, left))
        if cancel.is_set():
            for future in futures:
                future.cancel()
            raise InterruptedError('cancelled')
        if pending:
            # A fetch that missed the deadline is dropped; its late result
            # is never read.
            timed_out = True
            for future in pending:
                future.cancel()
            logger.add_error('pantry: %d fetch(es) timed out' % len(pending))
        if scope_future not in pending:
            try:
                scope_posts = scope_future.result()
            except Exception as error:
                logger.add_error('scope:%s' % error)
        for tag, future in tag_futures:
            if future in pending:
                continue
            try:
                hashtag_posts.extend(future.result())
            except Exception as error:
                logger.add_error('hashtag:%s:%s' % (tag, error))

        pantry = pantrymod.build_pantry(
            recipe,
            scope_posts=scope_posts,
            hashtag_posts=hashtag_posts,
            more_like_this=mlt_posts,
            max_id=max_id,
            count=count,
        )
        pantry.timed_out = timed_out
        logger.set_pantry(
            fetched_count=pantry.fetched_count,
            deduplicated_count=pantry.deduplicated_count,
            hashtag_filtered_count=pantry.deduplicated_count,
            duration_ms=int((time.time() - started) * 1000),
            pantry_timed_out=timed_out,
            pantry_has_more=pantry.has_more,
        )
        if on_stage:
            items = list(pantry.items)
            self.ui_idle(lambda: on_stage('PANTRY', items))

        diet_started = time.time()
        after_diet, decisions = dietmod.apply_diet(pantry.items, recipe)
        after_reach, reach_stats = reachmod.apply_network_reach(
            after_diet, recipe, self.reach)
        logger.set_diet(
            input_count=len(pantry.items),
            output_count=len(after_reach),
            duration_ms=int((time.time() - diet_started) * 1000),
            network_reach_stats=reach_stats,
            decisions=[decision.__dict__ for decision in decisions],
        )
        if reach_stats.get('fallback') == 'network_reach_empty_cache':
            logger.add_fallback('network_reach_empty_cache')
        if on_stage:
            items = list(after_reach)
            self.ui_idle(lambda: on_stage('DIET', items))

        server = kitchenmod.EMPTY_TRENDS
        if trends_future is not None:
            try:
                server = trends_future.result(timeout=5)
            except Exception:
                server = kitchenmod.EMPTY_TRENDS

        kitchen_started = time.time()
        ordered, score_map, kitchen_meta = kitchenmod.score_statuses(
            after_reach, recipe, server=server)
        if count > 0:
            ordered = ordered[:count]
        logger.set_kitchen(
            input_count=len(after_reach),
            output_count=len(ordered),
            duration_ms=int((time.time() - kitchen_started) * 1000),
            heat_filter=kitchen_meta.get('heat'),
            diversity_stats=kitchen_meta.get('diversity'),
            inert=kitchen_meta.get('inert'),
            ranks=kitchen_meta.get('ranks'),
        )
        if on_stage:
            items = list(ordered)
            self.ui_idle(lambda: on_stage('KITCHEN', items))

        logger.persist()
        # next_page_max_id mirrors Java FeedResult (oldest merged); UI should
        # prefer scope_oldest_id (Babble deviation).
        return FeedResult(
            items=ordered,
            next_page_max_id=pantrymod.oldest_id(pantry.items),
            has_more=pantry.has_more,
            score_map=score_map,
            scope_oldest_id=pantry.scope_oldest_id,
            kitchen_meta=kitchen_meta,
            logger=logger,
        )
