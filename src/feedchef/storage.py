# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-account FeedRecipe persistence (mashdon FeedRecipeStorage semantics).

Corrupt JSON → empty list on read. A save or delete never overwrites an
unreadable file: it is first renamed to ``<name>.corrupt-<unix-ts>`` (Java
wipes it). Upsert keeps insertion
order (dict insertion order). Lock per account key.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
from typing import Any

from .recipe import (
    FeedRecipe, recipe_from_dict, recipe_to_dict, validate_for_save,
)

log = logging.getLogger('babble.feedchef')

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_root = None


def set_root(path: str | None) -> None:
    """Tests: redirect the storage root."""
    global _root
    _root = path


def _account_lock(account_key: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(account_key)
        if lock is None:
            lock = threading.RLock()
            _locks[account_key] = lock
        return lock


def _safe_name(account_key: str) -> str:
    return urllib.parse.quote(account_key or '', safe='') or '_'


def _dir() -> str:
    if _root is not None:
        return _root
    try:
        from gi.repository import GLib
        base = GLib.get_user_data_dir()
    except Exception:
        base = os.path.expanduser('~/.local/share')
    return os.path.join(base, 'babble', 'feed-chef')


def _path(account_key: str) -> str:
    return os.path.join(_dir(), _safe_name(account_key) + '.json')


def _optimizer_path(account_key: str) -> str:
    """The A/B optimizer's sidecar: apart from the recipes, so a recipe's
    file, its hash and its share link never carry it."""
    return os.path.join(_dir(), 'optimizer', _safe_name(account_key)
                        + '.json')


class FeedRecipeStorage:
    def __init__(self, account_key: str):
        if not account_key:
            raise ValueError('account_key is required')
        self.account_key = account_key
        self._lock = _account_lock(account_key)

    def load_recipes(self) -> list[FeedRecipe]:
        with self._lock:
            path = _path(self.account_key)
            if not os.path.isfile(path):
                return []
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    raw = json.load(handle)
            except (OSError, ValueError, TypeError) as error:
                log.error('feedchef: corrupt recipes for %s: %s',
                          self.account_key, error)
                # Do not rewrite an empty list over the bad file.
                return []
            if not isinstance(raw, list):
                log.error('feedchef: recipes root is not a list for %s',
                          self.account_key)
                return []
            recipes = []
            for item in raw:
                if not isinstance(item, dict) or not item.get('id'):
                    continue
                try:
                    recipes.append(recipe_from_dict(item))
                except Exception as error:
                    log.error('feedchef: skip bad recipe: %s', error)
            return recipes

    def get_recipe_by_id(self, recipe_id: str) -> FeedRecipe | None:
        for recipe in self.load_recipes():
            if recipe.id == recipe_id:
                return recipe
        return None

    def save_recipe(self, recipe: FeedRecipe) -> None:
        recipe = validate_for_save(recipe)
        with self._lock:
            self._quarantine_corrupt()
            by_id: dict[str, dict[str, Any]] = {}
            for existing in self.load_recipes():
                by_id[existing.id] = recipe_to_dict(existing)
            by_id[recipe.id] = recipe_to_dict(recipe)
            self._write(list(by_id.values()))

    def delete_recipe(self, recipe_id: str) -> bool:
        with self._lock:
            self._quarantine_corrupt()
            recipes = self.load_recipes()
            kept = [recipe for recipe in recipes if recipe.id != recipe_id]
            if len(kept) == len(recipes):
                return False
            self._write([recipe_to_dict(recipe) for recipe in kept])
            self.save_optimizer(recipe_id, None)
            return True

    # -- the A/B optimizer's state (see feedchef.optimizer) ---------------

    def _optimizer_states(self) -> dict[str, Any]:
        try:
            with open(_optimizer_path(self.account_key), 'r',
                      encoding='utf-8') as handle:
                raw = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def load_optimizer(self, recipe_id: str) -> dict[str, Any] | None:
        """The experiment of a recipe, or None: no file, an unreadable one,
        or a state that is not valid (it ends the experiment, nothing
        else)."""
        from . import optimizer
        with self._lock:
            state = self._optimizer_states().get(recipe_id)
        return optimizer.validate(state)

    def save_optimizer(self, recipe_id: str,
                       state: dict[str, Any] | None,
                       require_recipe: bool = False) -> bool:
        """Keep the experiment of a recipe (None: forget it), private to
        the user. With *require_recipe* a state is kept only while the
        recipe exists (a deleted recipe leaves no sidecar behind); False
        when it was not."""
        with self._lock:
            if state is not None and require_recipe and not any(
                    r.id == recipe_id for r in self.load_recipes()):
                return False
            states = self._optimizer_states()
            if state is None:
                if states.pop(recipe_id, None) is None:
                    return True
            else:
                states[recipe_id] = state
            from .audit import write_private
            path = _optimizer_path(self.account_key)
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            os.chmod(os.path.dirname(path), 0o700)
            # Private from its creation (never a moment at the umask's
            # mode), and replaced atomically.
            write_private(path, states)
            return True

    def _quarantine_corrupt(self) -> None:
        """Keep an unreadable recipes file before a write replaces it."""
        path = _path(self.account_key)
        if not os.path.isfile(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
            if isinstance(raw, list):
                return
        except (OSError, ValueError, TypeError):
            pass
        backup = '%s.corrupt-%d' % (path, int(time.time()))
        n = 1
        while os.path.exists(backup):
            backup = '%s.corrupt-%d-%d' % (path, int(time.time()), n)
            n += 1
        try:
            os.replace(path, backup)
            os.chmod(backup, 0o600)
        except OSError as error:
            log.error('feedchef: could not keep corrupt file %s: %s',
                      path, error)
            return
        log.error('feedchef: unreadable recipes for %s kept as %s',
                  self.account_key, backup)

    def _write(self, payload: list[dict[str, Any]]) -> None:
        directory = _dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        path = _path(self.account_key)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
