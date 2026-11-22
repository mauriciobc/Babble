# SPDX-License-Identifier: GPL-3.0-or-later
"""Decision log shaped like mashdon DecisionLog (not PRD §9 extras)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_SAFE = re.compile(r'[^a-zA-Z0-9._-]+')
_root = None
_MAX_FILES = 50


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
            },
            'reliability': self.reliability,
        }


class FeedDecisionLogger:
    def __init__(self, recipe_id: str):
        self.recipe_id = recipe_id
        self.log = DecisionLog(
            session_id='%d_%s' % (int(time.time() * 1000),
                                  _sanitize_recipe_id(recipe_id)),
            recipe_id=recipe_id,
        )
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

    def add_fallback(self, name: str) -> None:
        with self._lock:
            self.log.reliability['fallbacks'].append(name)

    def add_error(self, message: str) -> None:
        with self._lock:
            self.log.reliability['errors'].append(message)

    def persist(self) -> str | None:
        directory = _dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        name = 'session_%s.json' % self.log.session_id
        path = os.path.join(directory, name)
        # Block path traversal via recipe id already sanitized in session_id.
        canonical_dir = os.path.realpath(directory)
        canonical_path = os.path.realpath(path)
        if not canonical_path.startswith(canonical_dir + os.sep):
            name = 'session_%d_safe.json' % int(time.time() * 1000)
            path = os.path.join(directory, name)
        payload = self.log.to_dict()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(tmp, path)
        self._rotate(directory)
        return path

    def _rotate(self, directory: str) -> None:
        files = []
        for entry in os.listdir(directory):
            full = os.path.join(directory, entry)
            if os.path.isfile(full) and entry.endswith('.json'):
                files.append(full)
        files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        for stale in files[_MAX_FILES:]:
            try:
                os.remove(stale)
            except OSError:
                pass
