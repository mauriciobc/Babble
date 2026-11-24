# SPDX-License-Identifier: GPL-3.0-or-later
"""The stages of one Feed Chef cook, in the order the engine runs them.

``FeedEngine._process_sync`` is the sequence; each function here is one
stage of it and works on the ``CookContext`` of the page: gather the
posts, check the follow list, the diet, network reach, the viewer's own
filters, the kitchen, the final guard, the caps. The audit calls stay in
the stage that owns the fact (the recorder only observes). The bodies were
moved out of the engine unchanged: each one starts by naming what it reads
from the context, so the statements below are the ones that ran inline.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
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
from .textutil import content_of

log = logging.getLogger('babble.feedchef')


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


@dataclass
class CookContext:
    """What every stage of one page reads: the recipe and the arguments of
    the cook, its clock, the recorder and the decision log, and the few
    things an early stage hands to a later one (``trends_future``,
    ``judge``, ``native``, ``diet_started``, ``kitchen_started``)."""

    engine: Any
    recipe: FeedRecipe
    now: datetime
    started: float
    max_id: str | None
    count: int
    cancel: Any
    on_stage: Callable[[str, list[dict[str, Any]]], None] | None
    author_counts: dict[str, int] | None
    instance_counts: dict[str, int] | None
    hide: Callable[..., bool] | None
    exclude: Any
    audit: Any
    logger: FeedDecisionLogger
    trends_future: Any = None
    judge: _Judge | None = None
    native: bool = False
    diet_started: float = 0.0
    kitchen_started: float = 0.0


def begin(engine, recipe, *, max_id, count, on_stage, cancel, author_counts,
          hide, cook_id, page_index, hop, instance_counts, peek, exclude,
          variant) -> CookContext:
    """The recorder and the decision log of the page, and its context."""
    audit = (Recorder(
        recipe, account_key=engine.account_key,
        self_id=engine.self_account_id, cook_id=cook_id or '',
        page_index=page_index, hop=hop, cursor_in=max_id, count=count,
        app_version=engine.app_version,
        # Only the first page of a refresh is a trial of the A/B
        # optimizer: a peek, and a page loaded further down, are not.
        variant=None if peek or page_index else variant)
        if engine.audit else NULL)
    logger = FeedDecisionLogger(recipe.id, audit, cook_id)
    logger.persistent = engine.persist_log
    if audit.enabled:
        audit.identity['cook_id'] = logger.cook_id
        if peek:
            audit.peeked()
    now = engine.clock()
    started = time.time()
    return CookContext(
        engine=engine, recipe=recipe, now=now, started=started,
        max_id=max_id, count=count, cancel=cancel, on_stage=on_stage,
        author_counts=author_counts, instance_counts=instance_counts,
        hide=hide, exclude=exclude, audit=audit, logger=logger)


def gather(ctx: CookContext) -> pantrymod.PantryResult:
    """Fetch the scope, the hashtags and More Like This, and merge them
    into the pantry."""
    engine = ctx.engine
    recipe = ctx.recipe
    started = ctx.started
    max_id = ctx.max_id
    count = ctx.count
    cancel = ctx.cancel
    on_stage = ctx.on_stage
    author_counts = ctx.author_counts
    instance_counts = ctx.instance_counts
    audit = ctx.audit
    logger = ctx.logger
    shown_ids = ctx.exclude or ()

    def fresh(posts):
        """The posts not already on screen (a boost counts by the
        post it shows as well as by itself)."""
        if not shown_ids:
            return posts
        return [post for post in posts
                if str(post.get('id') or '') not in shown_ids
                and str(content_of(post).get('id') or '')
                not in shown_ids]
    # Trends on a separate executor (Babble deviation from Java deadlock).
    trends_future = None
    if any(mod.type == 'trending' for mod in recipe.kitchen.modifiers):
        trends_future = engine._trends_executor.submit(
            engine._server_trends, recipe)
    ctx.trends_future = trends_future

    hashtag_posts: list[dict[str, Any]] = []
    mlt_posts: list[dict[str, Any]] = []
    scope_posts: list[dict[str, Any]] = []
    timed_out = False

    if cancel.is_set():
        raise InterruptedError('cancelled')

    tags = []
    if max_id is None and engine.fetch_hashtag is not None:
        tags = list(recipe.pantry.hashtags_include[:3])
    # One pool per run: a fetch that outlives its deadline cannot be
    # cancelled, and must not hold a worker a later run needs. Its
    # thread ends on its own (the bridge bounds every fetch).
    pool = ThreadPoolExecutor(
        max_workers=1 + len(tags), thread_name_prefix='feedchef-pantry')
    scope_future = pool.submit(engine.fetch_scope, max_id, count)
    tag_futures = [(tag, pool.submit(engine.fetch_hashtag, tag, count))
                   for tag in tags]
    pool.shutdown(wait=False)
    # More Like This runs while the scope is already being fetched, so
    # its stagger (up to 0.9 s) does not delay page one (Java does it
    # before the wait for the latch; the fetches started first here).
    # Its searches are still 450 ms apart.
    if max_id is None and engine.search_statuses is not None:
        try:
            mlt_posts = pantrymod.fetch_more_like_this(
                recipe, engine.search_statuses,
                terms=audit.trace.focus_of if audit.enabled else None,
                sequences=(audit.trace.focus_seq if audit.enabled
                           else None),
                pause=engine._stagger(cancel))
            mlt_posts = fresh(mlt_posts)
        except pantrymod.Cancelled:
            scope_future.cancel()
            for _tag, future in tag_futures:
                future.cancel()
            raise
    timeout = (engine.pantry_timeout_base
               + engine.pantry_timeout_per_tag * len(tags))
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
        scope_posts = fresh(scope_future.result())
    except Exception as error:
        logger.add_error('scope:%s' % error)
        logger.persist()
        raise
    for tag, future in tag_futures:
        if future in pending:
            continue
        try:
            found = fresh(future.result())
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
    audit.counts(author_counts, instance_counts)
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
        engine.ui_idle(lambda: on_stage('PANTRY', items))
    return pantry


def ready(ctx: CookContext) -> None:
    """The sentiment judge and the clock of the page, then the follow
    list when the recipe's network reach needs it (fail closed: wait for
    the list, or fail the cook)."""
    engine = ctx.engine
    recipe = ctx.recipe
    now = ctx.now
    cancel = ctx.cancel
    audit = ctx.audit
    logger = ctx.logger
    judge = _Judge()
    native = bool(engine.native_bluesky())
    audit.clock(now, native)
    ctx.judge = judge
    ctx.native = native
    if reachmod.needs_follow_list(recipe.pantry.network_reach):
        # Fail closed: wait for the list, or fail the cook.
        try:
            engine._await_follow_list(cancel)
            audit.follow_state(engine.reach.cache_snapshot())
        except reachmod.FollowListError as error:
            audit.follow_state(None, error=True)
            logger.add_error('reach:%s' % error)
            logger.persist()
            raise


def diet(ctx: CookContext, pantry):
    """The diet: the posts it lets through, and its decisions."""
    recipe = ctx.recipe
    audit = ctx.audit
    judge = ctx.judge
    native = ctx.native
    diet_started = time.time()
    ctx.diet_started = diet_started
    after_diet, decisions = dietmod.apply_diet(
        pantry.items, recipe, sentiment=judge, native_bluesky=native)
    audit.diet_decisions(decisions)
    return after_diet, decisions


def reach(ctx: CookContext, after_diet):
    """Network reach: the posts of the people the recipe lets in, and the
    stats of the follow list used."""
    engine = ctx.engine
    recipe = ctx.recipe
    audit = ctx.audit
    logger = ctx.logger
    try:
        after_reach, reach_stats = reachmod.apply_network_reach(
            after_diet, recipe, engine.reach)
    except reachmod.FollowListError as error:
        logger.add_error('reach:%s' % error)
        logger.persist()
        raise
    audit.reach_removed(after_diet, after_reach)
    return after_reach, reach_stats


def viewer_hide(ctx: CookContext, after_reach):
    """Drop the posts the viewer's own filters hide, before the kitchen."""
    now = ctx.now
    hide = ctx.hide
    audit = ctx.audit
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
        audit.viewer_checked(
            hidden, [str(status.get('id') or '') for status in shown])
    return after_reach


def diet_report(ctx: CookContext, pantry, after_reach, reach_stats,
                decisions) -> None:
    """The decision log of the diet stages, and the DIET stage callback."""
    engine = ctx.engine
    on_stage = ctx.on_stage
    logger = ctx.logger
    diet_started = ctx.diet_started
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
        engine.ui_idle(lambda: on_stage('DIET', items))


def kitchen(ctx: CookContext, after_reach):
    """The kitchen: score, group, cut by heat, order and cap each person."""
    recipe = ctx.recipe
    now = ctx.now
    author_counts = ctx.author_counts
    instance_counts = ctx.instance_counts
    audit = ctx.audit
    judge = ctx.judge
    trends_future = ctx.trends_future
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
    ctx.kitchen_started = kitchen_started
    ordered, score_map, kitchen_meta = kitchenmod.score_statuses(
        after_reach, recipe, server=server,
        sentiment_by_id=sentiment_by_id, author_counts=author_counts,
        instance_counts=instance_counts, now=now, audit=audit)
    return ordered, score_map, kitchen_meta


def guard(ctx: CookContext, pantry, ordered, score_map, kitchen_meta):
    """The final contract guard over the kitchen's page, and the
    sentiment scores the audit keeps."""
    engine = ctx.engine
    recipe = ctx.recipe
    now = ctx.now
    instance_counts = ctx.instance_counts
    audit = ctx.audit
    logger = ctx.logger
    judge = ctx.judge
    native = ctx.native
    ordered, guard_stats = check_promises(
        engine, recipe, ordered, judge, logger, native, audit,
        contract.ListFacts(
            relevance=kitchen_meta.get('relevance_scores'),
            groups=kitchen_meta.get('groups'),
            fresh=kitchen_meta.get('fresh'),
            instance_counts=instance_counts, now=now,
            heat=kitchen_meta.get('heat_steps'), scores=score_map,
            fire=kitchen_meta.get('fire'),
            fresh_fallback=bool(kitchen_meta.get('fresh_fallback'))))
    audit.judged_scores(judge, pantry.items)
    return ordered, guard_stats


def kitchen_report(ctx: CookContext, after_reach, ordered,
                   kitchen_meta) -> None:
    """The decision log of the kitchen, its fallbacks, and the KITCHEN
    stage callback."""
    engine = ctx.engine
    on_stage = ctx.on_stage
    logger = ctx.logger
    kitchen_started = ctx.kitchen_started
    logger.set_kitchen(
        input_count=len(after_reach),
        output_count=len(ordered),
        duration_ms=int((time.time() - kitchen_started) * 1000),
        heat_filter=kitchen_meta.get('heat'),
        diversity_stats=kitchen_meta.get('diversity'),
        inert=kitchen_meta.get('inert'),
        ranks=kitchen_meta.get('ranks'),
        cluster=kitchen_meta.get('cluster'),
        unavailable=kitchen_meta.get('unavailable'),
    )
    if kitchen_meta.get('fresh_fallback'):
        # Not a silent change: the page says it used the floor.
        logger.add_fallback('fresh_floor_fallback')
    for kind in kitchen_meta.get('unavailable') or ():
        # A modifier that could not run here (a package it needs is
        # missing) is a fallback of the cook, not a quiet no-op.
        logger.add_fallback('%s_unavailable' % kind)
    if on_stage:
        items = list(ordered)
        engine.ui_idle(lambda: on_stage('KITCHEN', items))


def caps(ctx: CookContext, ordered):
    """Posts per person and per server in the cook session after this
    page (the counts the next page starts from)."""
    recipe = ctx.recipe
    author_counts = ctx.author_counts
    instance_counts = ctx.instance_counts
    capped = [mod for mod in recipe.kitchen.modifiers
              if mod.type == 'diversity']
    counts = (kitchenmod.tally_authors(ordered, author_counts)
              if capped else dict(author_counts or {}))
    by_server = any(kitchenmod.diversity_dimensions(mod)[1]
                    for mod in capped)
    instances = (kitchenmod.tally_instances(ordered, instance_counts)
                 if by_server else dict(instance_counts or {}))
    return counts, instances


def finish(ctx: CookContext, pantry, ordered, score_map, counts,
           instances) -> None:
    """Hand the page to the recorder and write the decision log."""
    cancel = ctx.cancel
    audit = ctx.audit
    logger = ctx.logger
    audit.finish(ordered, score_map, counts, pantry.scope_oldest_id,
                 instances)
    if cancel.is_set():
        audit.cancelled()
    logger.persist()


def check_promises(engine, recipe: FeedRecipe, ordered: list[dict[str, Any]],
                   judge: _Judge, logger: FeedDecisionLogger,
                   native_bluesky: bool = False, audit=NULL,
                   facts: contract.ListFacts | None = None
                   ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The final contract guard: every promise of the recipe, checked
    again on every post about to be returned, independently of the
    stages. A violator is dropped and recorded; it means a stage is
    wrong."""
    reach = recipe.pantry.network_reach
    follow_ids = None
    if contract.reach_kind(reach) == 'follows':
        follow_ids = engine.reach.cache_snapshot().following_ids
    kept = []
    dropped: dict[str, int] = {}
    for status in ordered:
        broken = contract.violations(
            recipe, status, follow_ids=follow_ids,
            self_id=engine.self_account_id, sentiment=judge,
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
    if facts is not None:
        kept = _guard_list(recipe, kept, facts, logger, audit,
                           dropped)
    stats = {'checked': len(ordered), 'dropped': dropped}
    logger.set_guard(**stats)
    return kept, stats


def _guard_list(recipe, kept, facts, logger, audit, dropped):
    """The promises about the page as a whole (``contract.LIST_IDS``),
    asked of the posts that kept their own; a violator is dropped and
    recorded the same way."""
    for promise_id in contract.heat_violations(recipe, facts.heat):
        dropped[promise_id] = dropped.get(promise_id, 0) + 1
        logger.add_error('contract:%s' % promise_id)
        log.warning('feedchef: contract %s broken by the heat cut of '
                    'the page', promise_id)
    for promise_id in contract.fresh_fallback_violations(
            recipe, facts.fresh_fallback):
        dropped[promise_id] = dropped.get(promise_id, 0) + 1
        logger.add_error('contract:%s' % promise_id)
        log.warning('feedchef: contract %s broken by the page: the '
                    'freshness fallback was not needed', promise_id)
    broken = contract.list_violations(recipe, kept, facts)
    if not broken:
        return kept
    for status in kept:
        post_id = str(status.get('id') or '')
        for promise_id in broken.get(post_id, ()):
            dropped[promise_id] = dropped.get(promise_id, 0) + 1
            logger.add_error('contract:%s:%s' % (promise_id, post_id))
            log.warning(
                'feedchef: contract %s broken by post %s: a stage let '
                'it through; dropped', promise_id, post_id)
        if post_id in broken:
            audit.guard_verdict(post_id, broken[post_id])
    return [status for status in kept
            if str(status.get('id') or '') not in broken]
