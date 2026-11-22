# SPDX-License-Identifier: GPL-3.0-or-later
"""Degree-1 following reach (mashdon NetworkReachCalculator preload only).

``max_degrees is None`` disables the filter. Degree >= 2 is not fetched in
v1 (Java preload also only writes the viewer's following list).

Babble deviation from Java: the filter **fails closed**. Java let everyone
in while the follow list was loading or empty. Here the content author of
a post (the boosted account for a boost) must be followed or be the
viewer, and while the list is not known the engine waits for it
(``wait_usable``) and, if it cannot be loaded, the cook fails with
``FollowListError`` instead of showing strangers. A follow list that loaded
and is empty is a valid answer: only the viewer's own posts show.

The list is cached on disk per account. A cache read from disk is *stale*
until this session's preload replaces it: it is used at once (nothing
waits) and may miss a follow made since it was written, or keep an
unfollowed account, until the refresh lands. The preload runs when a
timeline opens, so the cache is never older than the last time one did.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from . import contract
from .recipe import FeedRecipe, NetworkReachConfig

log = logging.getLogger('babble.feedchef')

# 250 x 80 accounts: above every follow limit Mastodon servers set by
# default (7,500), so the cap is not a practical truncation; if it is hit
# the list is flagged ``truncated`` and logged.
MAX_PAGES = 250
PAGE_LIMIT = 80
_root = None
_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


class FollowListError(Exception):
    """The list of people the viewer follows is not known and could not
    be loaded, so "Only People I Follow" cannot be applied."""


def set_root(path: str | None) -> None:
    global _root
    _root = path


def _lock(key: str) -> threading.RLock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _dir() -> str:
    if _root is not None:
        return _root
    try:
        from gi.repository import GLib
        base = GLib.get_user_cache_dir()
    except Exception:
        base = os.path.expanduser('~/.cache')
    return os.path.join(base, 'babble', 'feed-chef-reach')


def _path(account_key: str) -> str:
    name = urllib.parse.quote(account_key or '', safe='') or '_'
    return os.path.join(_dir(), name + '.json')


def needs_follow_list(config: NetworkReachConfig) -> bool:
    """Whether applying the filter needs the follow list."""
    return contract.reach_kind(config) == 'follows'


@dataclass
class ReachCache:
    # unknown: nothing yet | loading | ready: a list is known (it may be
    # empty or stale) | failed: no list and the last load failed.
    status: str = 'unknown'
    following_ids: set[str] | None = None
    self_id: str = ''
    stale: bool = False
    error: str = ''
    truncated: bool = False


class NetworkReachCalculator:
    def __init__(self, account_key: str, self_account_id: str):
        self.account_key = account_key
        self.self_account_id = str(self_account_id or '')
        self._cache = ReachCache(self_id=self.self_account_id)
        self._lock = _lock(account_key)
        self._changed = threading.Condition(self._lock)
        self._load_disk()

    def _load_disk(self) -> None:
        path = _path(self.account_key)
        if not os.path.isfile(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
            ids = {str(item) for item in raw.get('following_ids') or []}
            with self._lock:
                self._cache = ReachCache(
                    status='ready', following_ids=ids, stale=True,
                    self_id=self.self_account_id)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            log.error('feedchef: reach cache read failed: %s', error)

    def _save_disk(self, ids: set[str]) -> None:
        directory = _dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = _path(self.account_key)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'following_ids': sorted(ids),
                       'updated_at': time.time()}, handle)
        os.replace(tmp, path)

    def cache_snapshot(self) -> ReachCache:
        with self._lock:
            cache = self._cache
            return ReachCache(
                status=cache.status,
                following_ids=(None if cache.following_ids is None
                               else set(cache.following_ids)),
                self_id=cache.self_id, stale=cache.stale,
                error=cache.error, truncated=cache.truncated)

    def mark_loading(self) -> None:
        """A refresh starts. A list already known (from disk) stays
        usable meanwhile; with none, cooks wait."""
        with self._changed:
            self._cache.error = ''
            if self._cache.following_ids is None:
                self._cache.status = 'loading'
            self._changed.notify_all()

    def set_following(self, ids: set[str], truncated: bool = False) -> None:
        with self._changed:
            self._cache.following_ids = set(ids)
            self._cache.status = 'ready'
            self._cache.stale = False
            self._cache.error = ''
            self._cache.truncated = truncated
            self._changed.notify_all()
        self._save_disk(ids)

    def set_failed(self, error: str) -> None:
        """The load failed. A stale list from disk stays in use; with no
        list, cooks fail instead of guessing."""
        with self._changed:
            self._cache.error = error
            if self._cache.following_ids is None:
                self._cache.status = 'failed'
            self._changed.notify_all()

    def wait_usable(self, cancel: threading.Event,
                    timeout: float) -> ReachCache:
        """Block until a follow list is known. Raises FollowListError when
        the load failed or *timeout* seconds passed, InterruptedError when
        *cancel* is set."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                cache = self._cache
                if cache.following_ids is not None:
                    return self.cache_snapshot()
                if cache.status == 'failed':
                    raise FollowListError(
                        cache.error or 'the follow list failed to load')
                if cancel.is_set():
                    raise InterruptedError('cancelled')
                left = deadline - time.monotonic()
                if left <= 0:
                    raise FollowListError(
                        'the follow list did not load in time')
                self._changed.wait(min(0.1, left))

    def apply_filter(
        self,
        statuses: list[dict[str, Any]],
        config: NetworkReachConfig,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        snap = self.cache_snapshot()
        stats = {
            'cache_status': snap.status,
            'cache_size': len(snap.following_ids or ()),
            'stale': snap.stale,
            'truncated': snap.truncated,
            'fallback': None,
        }
        kind = contract.reach_kind(config)
        if kind is None:
            return statuses, stats
        if kind == 'follows' and snap.following_ids is None:
            raise FollowListError('the follow list is not known')
        kept = [status for status in statuses
                if contract.reach_allows(
                    config, contract.author_id(status), snap.following_ids,
                    self.self_account_id)]
        return kept, stats


FollowingPageFn = Callable[[str | None], tuple[list[dict], str | None]]


def preload_following(
    calculator: NetworkReachCalculator,
    fetch_page: FollowingPageFn,
) -> None:
    """Paginate the viewer's following (cap MAX_PAGES x 80).
    ``fetch_page(max_id)``. A failed page fails the whole load: a partial
    list would hide followed people and be saved as if it were complete."""
    calculator.mark_loading()
    all_ids: set[str] = set()
    max_id = None
    truncated = False
    for index in range(MAX_PAGES):
        try:
            accounts, next_max = fetch_page(max_id)
        except Exception as error:
            log.error('feedchef: following page failed: %s', error)
            calculator.set_failed(str(error) or error.__class__.__name__)
            return
        if not accounts:
            break
        for account in accounts:
            aid = account.get('id')
            if aid is not None:
                all_ids.add(str(aid))
        if not next_max or next_max == max_id:
            break
        max_id = next_max
        truncated = index == MAX_PAGES - 1
    if truncated:
        log.warning('feedchef: following list truncated at %d accounts',
                    len(all_ids))
    calculator.set_following(all_ids, truncated=truncated)


def apply_network_reach(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    calculator: NetworkReachCalculator | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reach = recipe.pantry.network_reach
    if calculator is None or contract.reach_kind(reach) is None:
        return statuses, {'cache_status': 'skipped', 'stale': False,
                          'fallback': None}
    return calculator.apply_filter(statuses, reach)
