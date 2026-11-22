# SPDX-License-Identifier: GPL-3.0-or-later
"""Degree-1 following reach (mashdon NetworkReachCalculator preload only).

``max_degrees is None`` disables the filter. Degree ≥ 2 is not fetched in
v1 (Java preload also only writes the viewer's following list).
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

from .recipe import FeedRecipe, NetworkReachConfig
from .textutil import account_of

log = logging.getLogger('babble.feedchef')

MAX_PAGES = 40
PAGE_LIMIT = 80
_root = None
_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


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


@dataclass
class ReachCache:
    status: str = 'empty'  # loading | ready | empty
    following_ids: set[str] | None = None
    self_id: str = ''
    fallback_active: bool = False


class NetworkReachCalculator:
    def __init__(self, account_key: str, self_account_id: str):
        self.account_key = account_key
        self.self_account_id = str(self_account_id or '')
        self._cache = ReachCache(self_id=self.self_account_id)
        self._lock = _lock(account_key)
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
                    status='ready' if ids else 'empty',
                    following_ids=ids,
                    self_id=self.self_account_id,
                )
        except (OSError, ValueError, TypeError) as error:
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
            following = set(self._cache.following_ids or ())
            return ReachCache(
                status=self._cache.status,
                following_ids=following,
                self_id=self._cache.self_id,
                fallback_active=self._cache.fallback_active,
            )

    def mark_loading(self) -> None:
        with self._lock:
            self._cache.status = 'loading'
            self._cache.fallback_active = True

    def set_following(self, ids: set[str]) -> None:
        with self._lock:
            self._cache.following_ids = set(ids)
            self._cache.status = 'ready' if ids else 'empty'
            self._cache.fallback_active = not bool(ids)
        self._save_disk(ids)

    def degree(self, author_id: str, max_degrees: int) -> int:
        author_id = str(author_id or '')
        if not author_id:
            return -1
        if author_id == self.self_account_id:
            return 0
        snap = self.cache_snapshot()
        following = snap.following_ids or set()
        if author_id in following:
            return 1
        # Java: without second-degree edges, degree ≥ 2 is unknown (-1).
        if max_degrees > 1:
            return -1
        return -1

    def apply_filter(
        self,
        statuses: list[dict[str, Any]],
        config: NetworkReachConfig,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        stats = {
            'cache_status': self.cache_snapshot().status,
            'cache_size': len(self.cache_snapshot().following_ids or ()),
            'fallback_active': False,
            'fallback': None,
        }
        if not config.enabled or config.max_degrees is None:
            return statuses, stats
        max_degrees = int(config.max_degrees)
        snap = self.cache_snapshot()
        include_unknown = config.include_unknown
        fallback = False
        if snap.status == 'loading' and not include_unknown:
            include_unknown = True
            fallback = True
        if snap.status in ('empty', 'ready') and not (snap.following_ids or ()):
            if snap.status == 'ready' or snap.status == 'empty':
                include_unknown = True
                fallback = True
                stats['fallback'] = 'network_reach_empty_cache'
        stats['fallback_active'] = fallback
        kept = []
        for status in statuses:
            # Java uses the boosted post's author, not the booster.
            account = account_of(status, wrapper=False)
            author_id = str(account.get('id') or '')
            if not author_id:
                continue
            if config.include_self and author_id == self.self_account_id:
                kept.append(status)
                continue
            deg = self.degree(author_id, max_degrees)
            if deg >= 0 and deg <= max_degrees:
                kept.append(status)
            elif deg < 0 and include_unknown:
                kept.append(status)
        return kept, stats


FollowingPageFn = Callable[[str | None], tuple[list[dict], str | None]]


def preload_following(
    calculator: NetworkReachCalculator,
    fetch_page: FollowingPageFn,
) -> None:
    """Paginate the viewer's following (cap 40×80). ``fetch_page(max_id)``."""
    calculator.mark_loading()
    all_ids: set[str] = set()
    max_id = None
    for _ in range(MAX_PAGES):
        try:
            accounts, next_max = fetch_page(max_id)
        except Exception as error:
            log.error('feedchef: following page failed: %s', error)
            break
        if not accounts:
            break
        for account in accounts:
            aid = account.get('id')
            if aid is not None:
                all_ids.add(str(aid))
        if not next_max or next_max == max_id:
            break
        max_id = next_max
    calculator.set_following(all_ids)


def apply_network_reach(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    calculator: NetworkReachCalculator | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reach = recipe.pantry.network_reach
    if calculator is None or not reach.enabled or reach.max_degrees is None:
        return statuses, {'cache_status': 'skipped', 'fallback_active': False}
    return calculator.apply_filter(statuses, reach)
