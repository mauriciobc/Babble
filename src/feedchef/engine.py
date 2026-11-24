# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed engine orchestrator — one instance per view (no account singleton)."""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import kitchen as kitchenmod
from . import network_reach as reachmod
from . import pantry as pantrymod
from . import stages
from .decisionlog import FeedDecisionLogger
from .recipe import FeedRecipe
from .stages import ScopeFetchError  # noqa: F401  (the page imports it here)

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
    # The same for servers ("Limit Posts per Person" with the instance
    # dimension).
    instance_counts: dict[str, int] = field(default_factory=dict)


class FeedEngine:
    """Per-view engine. ``fetch_scope(max_id, count)`` etc. injected by UI."""

    # Java fetchPantry: 30 s plus 10 s per hashtag fetch. Attributes so a
    # test can lower them.
    pantry_timeout_base = 30.0
    pantry_timeout_per_tag = 10.0
    # "Only People I Follow" waits this long for the follow list to load
    # before the cook fails with ``FollowListError``.
    reach_wait_timeout = 20.0
    # Java staggers the searches of More Like This by this long (an
    # attribute so a test or a replay can turn it off), to stay under the
    # servers' rate limit for search.
    search_stagger_ms = 450

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
        # Whether each page also writes its decision log (False for a view
        # nobody may read the choices of later: see ``feedchefbridge``).
        self.persist_log = True
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
            max_workers=1, thread_name_prefix='feedchef-engine')
        self._trends_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='feedchef-trends')
        self._reach_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='feedchef-reach')
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
        instance_counts: dict[str, int] | None = None,
        peek: bool = False,
        exclude: frozenset[str] | set[str] | None = None,
        variant: dict[str, Any] | None = None,
    ):
        """Cook one page on the engine worker. *variant* is what the A/B
        optimizer says of this cook (see ``optimizer.cook_tag``): recorded,
        never read. *peek* makes it the page
        Smart New Posts looks at (audit outcome 'peek', never shown by
        itself); *exclude* are the ids already on screen, dropped from the
        sources before anything else so they use no one's slot of the
        cap."""
        self.cancel()
        # The cook works on a copy taken now: the caller may change its
        # recipe (a focus that fades on refresh) while the cook runs, and
        # the page and its audit record must say what was cooked.
        recipe = copy.deepcopy(recipe)
        self._cancel = threading.Event()
        cancel = self._cancel
        generation = uuid.uuid4().hex

        def run():
            try:
                result = self._process_sync(
                    recipe, max_id=max_id, count=count,
                    on_stage=on_stage, cancel=cancel, generation=generation,
                    author_counts=author_counts, hide=hide,
                    cook_id=cook_id, page_index=page_index, hop=hop,
                    instance_counts=instance_counts, peek=peek,
                    exclude=exclude, variant=variant)
                if cancel.is_set():
                    # Its page is never shown: the record, already
                    # written, says so. A peek nobody saw leaves no log.
                    if result.logger is not None:
                        if peek:
                            result.logger.discard()
                        else:
                            result.logger.mark_cancelled()
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
        instance_counts: dict[str, int] | None = None,
        variant: dict[str, Any] | None = None,
    ) -> FeedResult:
        recipe = copy.deepcopy(recipe)
        return self._process_sync(
            recipe, max_id=max_id, count=count,
            on_stage=None, cancel=threading.Event(), generation='sync',
            author_counts=author_counts, hide=hide,
            cook_id=cook_id, page_index=page_index, hop=hop,
            instance_counts=instance_counts, variant=variant)

    def run_background(self, work: Callable[[], None]) -> None:
        """Run *work* on the engine worker, after the cook it may be
        queued behind: file writes the page must not do on the main
        thread. Nothing it raises reaches the caller."""
        def guarded():
            try:
                work()
            except Exception:
                log.debug('feedchef: background work failed', exc_info=True)
        try:
            self._executor.submit(guarded)
        except RuntimeError:
            # Shut down: the work (a file removed or written) still must
            # happen, so a short-lived thread does it.
            threading.Thread(target=guarded, daemon=True).start()

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
        instance_counts: dict[str, int] | None = None,
        peek: bool = False,
        exclude: frozenset[str] | set[str] | None = None,
        variant: dict[str, Any] | None = None,
    ) -> FeedResult:
        """One page. *peek* and *exclude* are those of ``process_feed``.
        *instance_counts* is the same as *author_counts* for
        the servers of the posts. *author_counts*: posts per person already shown by
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
        ctx = stages.begin(
            self, recipe, max_id=max_id, count=count, on_stage=on_stage,
            cancel=cancel, author_counts=author_counts, hide=hide,
            cook_id=cook_id, page_index=page_index, hop=hop,
            instance_counts=instance_counts, peek=peek, exclude=exclude,
            variant=variant)
        pantry = stages.gather(ctx)
        stages.ready(ctx)
        after_diet, decisions = stages.diet(ctx, pantry)
        after_reach, reach_stats = stages.reach(ctx, after_diet)
        after_reach = stages.viewer_hide(ctx, after_reach)
        stages.diet_report(ctx, pantry, after_reach, reach_stats, decisions)
        ordered, score_map, kitchen_meta = stages.kitchen(ctx, after_reach)
        ordered, guard_stats = stages.guard(
            ctx, pantry, ordered, score_map, kitchen_meta)
        stages.kitchen_report(ctx, after_reach, ordered, kitchen_meta)
        counts, instances = stages.caps(ctx, ordered)
        stages.finish(ctx, pantry, ordered, score_map, counts, instances)
        # next_page_max_id mirrors Java FeedResult (oldest merged); UI should
        # prefer scope_oldest_id (Babble deviation).
        return FeedResult(
            items=ordered,
            next_page_max_id=pantrymod.oldest_id(pantry.items),
            has_more=pantry.has_more,
            score_map=score_map,
            scope_oldest_id=pantry.scope_oldest_id,
            kitchen_meta=kitchen_meta,
            logger=ctx.logger,
            guard=guard_stats,
            author_counts=counts,
            instance_counts=instances,
        )

    def _stagger(self, cancel: threading.Event) -> Callable[[], None]:
        """The pause between two searches of More Like This: it ends the
        moment the cook is cancelled, and raises ``pantry.Cancelled`` then,
        so a cancelled cook does not go on to the next search."""
        def pause():
            if self.search_stagger_ms > 0 and cancel.wait(
                    self.search_stagger_ms / 1000.0):
                raise pantrymod.Cancelled('cancelled')
        return pause
