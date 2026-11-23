# SPDX-License-Identifier: GPL-3.0-or-later
"""Decision log shaped like mashdon DecisionLog (not PRD §9 extras)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .audit import NULL, sidecar_path, write_private

_SAFE = re.compile(r'[^a-zA-Z0-9._-]+')
_root = None


def set_root(path: str | None) -> None:
    global _root
    _root = path


def _dir() -> str:
    if _root is not None:
        return _root
    try:
        from gi.repository import GLib
        base = GLib.get_user_state_dir()
    except Exception:
        base = os.path.expanduser('~/.local/state')
    return os.path.join(base, 'babble', 'feed_chef_logs')


def _sanitize_recipe_id(recipe_id: str) -> str:
    cleaned = _SAFE.sub('_', recipe_id or 'unknown')
    return cleaned[:80] or 'unknown'


@dataclass
class DecisionLog:
    session_id: str
    recipe_id: str
    timestamp: float = field(default_factory=time.time)
    pantry: dict[str, Any] = field(default_factory=dict)
    diet: dict[str, Any] = field(default_factory=dict)
    kitchen: dict[str, Any] = field(default_factory=dict)
    guard: dict[str, Any] = field(default_factory=dict)
    reliability: dict[str, Any] = field(
        default_factory=lambda: {'errors': [], 'fallbacks': []})

    def to_dict(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'recipe_id': self.recipe_id,
            'timestamp': self.timestamp,
            'stages': {
                'pantry': self.pantry,
                'diet': self.diet,
                'kitchen': self.kitchen,
                'guard': self.guard,
            },
            'reliability': self.reliability,
        }


class FeedDecisionLogger:
    def __init__(self, recipe_id: str, audit=None,
                 cook_id: str | None = None):
        self.recipe_id = recipe_id
        self.audit = NULL if audit is None else audit
        self.cook_id = cook_id or uuid.uuid4().hex[:12]
        self.log = DecisionLog(
            session_id='%d_%s' % (int(time.time() * 1000),
                                  _sanitize_recipe_id(recipe_id)),
            recipe_id=recipe_id,
        )
        if self.audit.enabled:
            self.audit.identity['session_id'] = self.log.session_id
        # The schema-2 record of the last persist (None without an audit).
        self.record: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def set_pantry(self, **fields: Any) -> None:
        with self._lock:
            self.log.pantry.update(fields)

    def set_diet(self, **fields: Any) -> None:
        with self._lock:
            self.log.diet.update(fields)

    def set_kitchen(self, **fields: Any) -> None:
        with self._lock:
            self.log.kitchen.update(fields)

    def set_guard(self, **fields: Any) -> None:
        with self._lock:
            self.log.guard.update(fields)

    def add_fallback(self, name: str) -> None:
        with self._lock:
            self.log.reliability['fallbacks'].append(name)

    def add_error(self, message: str) -> None:
        with self._lock:
            self.log.reliability['errors'].append(message)

    def persist(self) -> str | None:
        directory = _dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        name = 'session_%s__c%s_%s.json' % (
            self.log.session_id, self.cook_id, uuid.uuid4().hex[:6])
        path = os.path.join(directory, name)
        # Block path traversal via recipe id already sanitized in session_id.
        canonical_dir = os.path.realpath(directory)
        canonical_path = os.path.realpath(path)
        if not canonical_path.startswith(canonical_dir + os.sep):
            name = 'session_%d_safe.json' % int(time.time() * 1000)
            path = os.path.join(directory, name)
        payload = self.log.to_dict()
        if self.audit.enabled:
            if self.audit.ordered is None:
                self.audit.failed('; '.join(self.log.reliability['errors']))
            self.record = self.audit.build()
            payload['audit'] = self.record
            # Floats go out as repr: json writes them so, and they come
            # back bit for bit. The visible text goes to a private sidecar.
            write_private(sidecar_path(path), self.audit.sidecar)
            write_private(path, payload)
        else:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
            os.replace(tmp, path)
        self._rotate(directory)
        return path

    def _rotate(self, directory: str) -> None:
        prune(directory)


# Retention: the last cooks of each recipe, then a cap on the whole folder.
MAX_COOKS = 20
MAX_BYTES = 50 * 1024 * 1024
_NAME = re.compile(r'^session_(\d+)_(.+?)(?:__c([0-9a-f]+)_[0-9a-f]{6})?'
                   r'(\.text)?\.json$')


def _cooks(directory: str) -> list[dict[str, Any]]:
    """Every cook in *directory*: its log files (a page each) and their
    sidecars, newest page's time, total size. A log from before the cook id
    existed is a cook of its own."""
    cooks: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in os.listdir(directory):
        match = _NAME.match(entry)
        full = os.path.join(directory, entry)
        if match is None or not os.path.isfile(full):
            continue
        recipe, cook = match.group(2), match.group(3) or entry
        row = cooks.setdefault((recipe, cook), {
            'recipe': recipe, 'files': [], 'mtime': 0.0, 'size': 0})
        stat = os.stat(full)
        row['files'].append(full)
        row['mtime'] = max(row['mtime'], stat.st_mtime)
        row['size'] += stat.st_size
    return list(cooks.values())


def _remove(cook: dict[str, Any]) -> None:
    for path in cook['files']:
        try:
            os.remove(path)
        except OSError:
            pass


def prune(directory: str) -> None:
    """Keep the last ``MAX_COOKS`` cooks of each recipe (a busy recipe never
    evicts another one's history), then evict the oldest cooks overall
    while the folder is over ``MAX_BYTES``. The newest cook always stays."""
    by_recipe: dict[str, list[dict[str, Any]]] = {}
    for cook in _cooks(directory):
        by_recipe.setdefault(cook['recipe'], []).append(cook)
    kept = []
    for cooks in by_recipe.values():
        cooks.sort(key=lambda row: row['mtime'], reverse=True)
        for stale in cooks[MAX_COOKS:]:
            _remove(stale)
        kept.extend(cooks[:MAX_COOKS])
    kept.sort(key=lambda row: row['mtime'])
    total = sum(row['size'] for row in kept)
    while total > MAX_BYTES and len(kept) > 1:
        oldest = kept.pop(0)
        total -= oldest['size']
        _remove(oldest)


def clear_all() -> int:
    """Delete every audit log and sidecar; the number of cooks removed."""
    return _clear(None)


def clear_recipe(recipe_id: str) -> int:
    """Delete the logs of one recipe; the number of cooks removed."""
    return _clear(_sanitize_recipe_id(recipe_id))


def _clear(recipe: str | None) -> int:
    directory = _dir()
    if not os.path.isdir(directory):
        return 0
    removed = 0
    for cook in _cooks(directory):
        if recipe is None or cook['recipe'] == recipe:
            _remove(cook)
            removed += 1
    return removed
