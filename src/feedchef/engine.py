# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed engine orchestrator — one instance per view (no account singleton)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import contract
from . import diet as dietmod
from . import kitchen as kitchenmod
from . import network_reach as reachmod
from . import pantry as pantrymod
from . import sentiment as sentimentmod
from .audit import NULL, Recorder
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
    # The final contract guard: posts checked, and dropped by promise id
    # (always empty unless a stage has a bug).
    guard: dict[str, Any] = field(
        default_factory=lambda: {'checked': 0, 'dropped': {}})
    # Posts per person in the cook session after this page, for the next
    # one (see ``author_counts`` of ``process_feed``).
    author_counts: dict[str, int] = field(default_factory=dict)


class ScopeFetchError(Exception):
    """The posts of the recipe's scope (Home, a list, ...) could not be
    fetched in time. The page shows its error page with Try Again: an
    empty page that says "no more" would end the paging for good."""


class _Judge:
    """Memoizes the sentiment of each post for one cook: the diet, the
    kitchen and the guard all ask, and the engines are not cheap."""

    MISSING = object()

    def __init__(self):
        self._memo: dict[int, float | None] = {}

    def peek(self, status):
        """The score already asked for, or MISSING; never judges."""
        return self._memo.get(id(status), self.MISSING)

    def __call__(self, status):
        key = id(status)
        if key not in self._memo:
            self._memo[key] = sentimentmod.score_status(status)
        return self._memo[key]

class FeedEngine:
    """Per-view engine. ``fetch_scope(max_id, count)`` etc. injected by UI."""

    # Java fetchPantry: 30 s plus 10 s per hashtag fetch. Attributes so a
    # test can lower them.
    pantry_timeout_base = 30.0
    pantry_timeout_per_tag = 10.0
    # "Only People I Follow" waits this long for the follow list to load
    # before the cook fails with ``FollowListError``.
    reach_wait_timeout = 20.0

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
        native_bluesky: Callable[[], bool] | None = None,
        app_version: str = 'dev',
    ):
        self.account_key = account_key
        # The audit: every page writes a schema-2 record (audit.py) beside
        # its decision log. ``audit = False`` turns it off; ``clock`` is the
        # one reading of the time per page (a test or a replay injects it).
        self.audit = True
        self.app_version = app_version
        self.clock = lambda: datetime.now(timezone.utc)
        # Whether the native Bluesky setting is on, asked once per cook
        # (it decides what the minimum length counts, see ``quoteline``).
        self.native_bluesky = native_bluesky or (lambda: False)
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
        self._preload_future = None
        self._cancel = threading.Event()
        self._trend_cache: tuple[float, kitchenmod.ServerTrendContext] | None = None
        self.reach = reachmod.NetworkReachCalculator(
            account_key, self_account_id)

    def init_preload(self) -> None:
        self._ensure_preload()

    def _ensure_preload(self) -> bool:
        """Start loading the follow list unless a load is running; False
        when there is nothing to load it with."""
        if self.fetch_following_page is None:
            return False
        future = self._preload_future
        if future is not None and not future.done():
            return True

        # Before the worker starts, so a cook that asks right away sees a
        # load in progress and not the failure of the last one.
        self.reach.mark_loading()

        def work():
            reachmod.preload_following(self.reach, self.fetch_following_page)

        self._preload_future = self._reach_executor.submit(work)
        return True

    def _await_follow_list(self, cancel: threading.Event
                           ) -> reachmod.ReachCache:
        """The follow list, waiting for the preload (bounded, cancellable).
        Raises ``FollowListError`` when it cannot be had: "Only People I
        Follow" never lets strangers through instead."""
        snap = self.reach.cache_snapshot()
        if snap.following_ids is None:
            if snap.status in ('unknown', 'failed') \
                    and not self._ensure_preload():
                raise reachmod.FollowListError(
                    'no way to load the follow list')
        return self.reach.wait_usable(cancel, self.reach_wait_timeout)

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
        author_counts: dict[str, int] | None = None,
        hide: Callable[[dict[str, Any]], bool] | None = None,
        cook_id: str | None = None,
        page_index: int = 0,
        hop: int = 0,
    ):
        self.cancel()
        self._cancel = threading.Event()
        cancel = self._cancel
        generation = uuid.uuid4().hex

        def run():
            try:
                result = self._process_sync(
                    recipe, max_id=max_id, count=count,
                    on_stage=on_stage, cancel=cancel, generation=generation,
                    author_counts=author_counts, hide=hide,
                    cook_id=cook_id, page_index=page_index, hop=hop)
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
        author_counts: dict[str, int] | None = None,
        hide: Callable[[dict[str, Any]], bool] | None = None,
        cook_id: str | None = None,
        page_index: int = 0,
        hop: int = 0,
    ) -> FeedResult:
        return self._process_sync(
            recipe, max_id=max_id, count=count,
            on_stage=None, cancel=threading.Event(), generation='sync',
            author_counts=author_counts, hide=hide,
            cook_id=cook_id, page_index=page_index, hop=hop)

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
        author_counts: dict[str, int] | None = None,
        hide: Callable[[dict[str, Any]], bool] | None = None,
        cook_id: str | None = None,
        page_index: int = 0,
        hop: int = 0,
    ) -> FeedResult:
        """One page. *author_counts*: posts per person already shown by
        the earlier pages of the cook session ("Limit Posts per Person"
        is a cap on the whole session). The engine never changes it; the
        counts for the next page come back in ``FeedResult.author_counts``
        and the caller keeps them only for a page it showed. *hide* tells
        the posts the viewer's own filters hide: they are dropped before
        the kitchen, so they use no one's slot of the cap and no place in
        the heat percentile.

        A page is never truncated: the scope's cursor has already moved
        past every post it returned, so a post cut here would be skipped
        for good. Page one may hold more than *count* posts (hashtag and
        More Like This posts come on top of the scope's page).

        A scope that failed or timed out raises (the page offers Try
        Again): an empty page with no more to come would end the paging
        while older posts exist."""
        audit = (Recorder(
            recipe, account_key=self.account_key,
            self_id=self.self_account_id, cook_id=cook_id or '',
            page_index=page_index, hop=hop, cursor_in=max_id, count=count,
            app_version=self.app_version)
            if self.audit else NULL)
        logger = FeedDecisionLogger(recipe.id, audit, cook_id)
        if audit.enabled:
            audit.identity['cook_id'] = logger.cook_id
        now = self.clock()
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
                recipe, self.search_statuses,
                terms=audit.trace.focus_of if audit.enabled else None)

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
        if scope_future in pending:
            logger.add_error('scope: timed out')
            logger.persist()
            raise ScopeFetchError('the posts did not load in time')
        try:
            scope_posts = scope_future.result()
        except Exception as error:
            logger.add_error('scope:%s' % error)
            logger.persist()
            raise
        for tag, future in tag_futures:
            if future in pending:
                continue
            try:
                found = future.result()
                hashtag_posts.extend(found)
                audit.hashtag_fetched(tag, found)
            except Exception as error:
                logger.add_error('hashtag:%s:%s' % (tag, error))

        pantry = pantrymod.build_pantry(
            recipe,
            scope_posts=scope_posts,
            hashtag_posts=hashtag_posts,
            more_like_this=mlt_posts,
            max_id=max_id,
            count=count,
            origins=audit.trace,
        )
        audit.fetched(mlt_posts, hashtag_posts, scope_posts)
        audit.pantry(pantry.items)
        audit.counts(author_counts)
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

        judge = _Judge()
        native = bool(self.native_bluesky())
        audit.clock(now, native)
        if reachmod.needs_follow_list(recipe.pantry.network_reach):
            # Fail closed: wait for the list, or fail the cook.
            try:
                self._await_follow_list(cancel)
                audit.follow_state(self.reach.cache_snapshot())
            except reachmod.FollowListError as error:
                audit.follow_state(None, error=True)
                logger.add_error('reach:%s' % error)
                logger.persist()
                raise
        diet_started = time.time()
        after_diet, decisions = dietmod.apply_diet(
            pantry.items, recipe, sentiment=judge, native_bluesky=native)
        audit.diet_decisions(decisions)
        try:
            after_reach, reach_stats = reachmod.apply_network_reach(
                after_diet, recipe, self.reach)
        except reachmod.FollowListError as error:
            logger.add_error('reach:%s' % error)
            logger.persist()
            raise
        audit.reach_removed(after_diet, after_reach)
        hidden: list[str] = []
        if hide is not None:
            shown = []
            for status in after_reach:
                if (hide(status, now) if getattr(hide, 'takes_now', False)
                        else hide(status)):
                    hidden.append(str(status.get('id') or ''))
                else:
                    shown.append(status)
            after_reach = shown
        audit.viewer_hidden(hidden)
        logger.set_diet(
            input_count=len(pantry.items),
            output_count=len(after_reach),
            duration_ms=int((time.time() - diet_started) * 1000),
            network_reach_stats=reach_stats,
            decisions=[decision.__dict__ for decision in decisions],
        )
        if reach_stats.get('stale'):
            logger.add_fallback('network_reach_stale_cache')
        if on_stage:
            items = list(after_reach)
            self.ui_idle(lambda: on_stage('DIET', items))

        server = kitchenmod.EMPTY_TRENDS
        if trends_future is not None:
            try:
                server = trends_future.result(timeout=5)
            except Exception:
                server = kitchenmod.EMPTY_TRENDS
        audit.trends_seen(server)

        sentiment_by_id = {}
        if sentimentmod.recipe_needs_sentiment(recipe):
            for status in after_reach:
                sid = str(status.get('id') or '')
                score = judge(status)
                if sid and score is not None:
                    sentiment_by_id[sid] = score

        kitchen_started = time.time()
        ordered, score_map, kitchen_meta = kitchenmod.score_statuses(
            after_reach, recipe, server=server,
            sentiment_by_id=sentiment_by_id, author_counts=author_counts,
            now=now, audit=audit)
        ordered, guard_stats = self._guard(
            recipe, ordered, judge, logger, native, audit)
        audit.judged_scores(judge, pantry.items)
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

        capped = any(mod.type == 'diversity'
                     for mod in recipe.kitchen.modifiers)
        counts = (kitchenmod.tally_authors(ordered, author_counts)
                  if capped else dict(author_counts or {}))
        audit.finish(ordered, score_map, counts, pantry.scope_oldest_id)
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
            guard=guard_stats,
            author_counts=counts,
        )

    def _guard(self, recipe: FeedRecipe, ordered: list[dict[str, Any]],
               judge: _Judge, logger: FeedDecisionLogger,
               native_bluesky: bool = False, audit=NULL
               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The final contract guard: every promise of the recipe, checked
        again on every post about to be returned, independently of the
        stages. A violator is dropped and recorded; it means a stage is
        wrong."""
        reach = recipe.pantry.network_reach
        follow_ids = None
        if contract.reach_kind(reach) == 'follows':
            follow_ids = self.reach.cache_snapshot().following_ids
        kept = []
        dropped: dict[str, int] = {}
        for status in ordered:
            broken = contract.violations(
                recipe, status, follow_ids=follow_ids,
                self_id=self.self_account_id, sentiment=judge,
                native_bluesky=native_bluesky)
            audit.guard_verdict(str(status.get('id') or ''), broken)
            if not broken:
                kept.append(status)
                continue
            post_id = str(status.get('id') or '')
            for promise_id in broken:
                dropped[promise_id] = dropped.get(promise_id, 0) + 1
                logger.add_error('contract:%s:%s' % (promise_id, post_id))
                log.warning(
                    'feedchef: contract %s broken by post %s: a stage let '
                    'it through; dropped', promise_id, post_id)
        stats = {'checked': len(ordered), 'dropped': dropped}
        logger.set_guard(**stats)
        return kept, stats
