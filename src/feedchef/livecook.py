# SPDX-License-Identifier: GPL-3.0-or-later
"""The live count of the recipe builder: how many posts a recipe keeps,
cooked again on every change without asking the server again (no widgets).

The posts a recipe starts from (its pantry: the scope's newest posts, the
first included hashtags' timelines and the More Like This searches) are
fetched once, on the worker, and kept as long as the settings that decide
them stay the same (``pantry_key``). Every other change (diet, reach, the
viewer's filters, kitchen, heat, caps) only cooks those posts again through
the real engine, with the audit off. All of it runs on the one worker of this
object: the main thread asks (``request``) and receives the answer through
``deliver``, and a request that a newer one supersedes is dropped without
being cooked (or its answer is not delivered).
"""

from __future__ import annotations

import copy
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .recipe import FeedRecipe

log = logging.getLogger('babble.feedchef')

# Posts asked of the scope and of each hashtag for the count: what a first
# page of the recipe would start from.
COOK_COUNT = 40


def pantry_key(recipe: FeedRecipe) -> tuple:
    """What the fetched posts depend on: the scope (and list), the hashtags
    (a hashtag's timeline is fetched, and an excluded one filters the
    pantry), the follow filter (it needs the follow list) and the focus
    (its searches). A change to any of these fetches again."""
    pantry, kitchen = recipe.pantry, recipe.kitchen
    return (pantry.scope, pantry.list_id if pantry.scope == 'list' else None,
            tuple(pantry.hashtags_include), tuple(pantry.hashtags_exclude),
            bool(pantry.network_reach.enabled),
            tuple(kitchen.active_focus_hashtags or ()),
            tuple(kitchen.active_focus_keywords or ()))


class LiveCount:
    """What a cook of the pantry kept: *matching* posts of *fetched*."""

    __slots__ = ('matching', 'fetched')

    def __init__(self, matching: int, fetched: int):
        self.matching = matching
        self.fetched = fetched


class LiveCook:
    """One worker, one pantry. *make_engine(recipe)* builds the engine of a
    recipe's pantry (the bridge's, with the audit off); *deliver(callback)*
    runs *callback* where the answer is wanted (the main loop); *hide* is the
    viewer's own filter, as the timeline passes it."""

    def __init__(self, make_engine: Callable[[FeedRecipe], Any],
                 deliver: Callable[[Callable[[], None]], None] | None = None,
                 hide: Callable | None = None):
        self._make_engine = make_engine
        self._deliver = deliver or (lambda callback: callback())
        self._hide = hide
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='feedchef-live')
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False
        # Worker-only state.
        self._engine = None
        self._key = None
        self._fetched = 0
        # Counts, for a test or a log: pantries fetched, cooks made.
        self.fetches = 0
        self.cooks = 0

    def request(self, recipe: FeedRecipe,
                on_done: Callable[[LiveCount], None],
                on_error: Callable[[BaseException], None]) -> int:
        """Count what *recipe* keeps; the answer goes to *on_done* (or
        *on_error*) unless a later request came first. Returns at once."""
        recipe = copy.deepcopy(recipe)
        with self._lock:
            if self._closed:
                return 0
            self._generation += 1
            generation = self._generation
        self._executor.submit(self._run, generation, recipe, on_done,
                              on_error)
        return generation

    def supersede(self) -> None:
        """Drop the answer of every request made so far (the form is in a
        state that cannot be counted)."""
        with self._lock:
            self._generation += 1

    def _current(self, generation: int) -> bool:
        with self._lock:
            return not self._closed and generation == self._generation

    def _run(self, generation, recipe, on_done, on_error):
        if not self._current(generation):
            return
        try:
            engine = self._pantry(recipe)
            if not self._current(generation):
                return
            result = engine.process_feed_sync(
                recipe, count=COOK_COUNT, hide=self._hide)
            self.cooks += 1
            answer = LiveCount(len(result.items), self._fetched)
        except Exception as error:
            if self._closed:
                # Closed while it was fetching: nobody wants this engine.
                self._drop_engine()
            if not self._current(generation):
                return
            log.info('feedchef: live count failed: %s', error)
            self._deliver(lambda error=error: on_error(error))
            return
        if self._current(generation):
            self._deliver(lambda: on_done(answer))
        elif self._closed:
            self._drop_engine()

    def _pantry(self, recipe):
        """The engine holding the pantry of *recipe*: the one it already
        has when the pantry settings did not change, else a new one that
        fetches (here, on the worker) and keeps what it got."""
        key = pantry_key(recipe)
        if self._engine is not None and key == self._key:
            return self._engine
        self._drop_engine()
        engine = self._make_engine(recipe)
        try:
            return self._fetch(engine, recipe, key)
        except BaseException:
            engine.shutdown()
            raise

    def _fetch(self, engine, recipe, key):
        scope = list(engine.fetch_scope(None, COOK_COUNT))
        tags = {}
        if engine.fetch_hashtag is not None:
            for tag in recipe.pantry.hashtags_include[:3]:
                tags[tag] = list(engine.fetch_hashtag(tag, COOK_COUNT))
        searches: dict[str, list] = {}
        search = engine.search_statuses
        if self._closed:
            raise InterruptedError('closed')
        if recipe.pantry.network_reach.enabled:
            engine.init_preload()
        # From here on the engine reads what it fetched: a cook costs the
        # server nothing. (A More Like This search is made the first time it
        # is asked for, then kept.)
        engine.fetch_scope = lambda _max_id, _count: list(scope)
        if engine.fetch_hashtag is not None:
            engine.fetch_hashtag = lambda tag, _count: list(
                tags.get(tag, []))
        if search is not None:
            def kept_search(query):
                if query not in searches:
                    searches[query] = list(search(query))
                return list(searches[query])
            engine.search_statuses = kept_search
        self._engine, self._key = engine, key
        self._fetched = len({str(item.get('id') or id(item))
                             for item in scope
                             + [p for found in tags.values() for p in found]})
        self.fetches += 1
        return engine

    def _drop_engine(self):
        engine, self._engine, self._key = self._engine, None, None
        if engine is not None:
            engine.shutdown()

    def close(self):
        """Stop: nothing more is delivered, queued requests are dropped and
        the engine's workers end. It never waits (the worker may be inside
        a fetch): the engine is shut down without joining, and a fetch that
        outlives it ends the engine it was making."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.shutdown()
