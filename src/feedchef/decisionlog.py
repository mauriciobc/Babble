# SPDX-License-Identifier: GPL-3.0-or-later
"""Decision log shaped like mashdon DecisionLog (not PRD §9 extras)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .audit import NULL, redact, sidecar_path, write_private

log = logging.getLogger('babble.feedchef')

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


def log_dir() -> str:
    """The folder the audit logs are kept in."""
    return _dir()


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
        self._payload: dict[str, Any] | None = None
        self._path: str | None = None
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
            self.log.reliability['errors'].append(redact(message))

    # False for a cook nobody may read later (the unsaved preview, the live
    # count): nothing of it is written.
    persistent = True

    def persist(self) -> str | None:
        """Write the log. The log observes the cook and never decides it:
        a folder that cannot be written is a warning, and the cook goes on."""
        if not self.persistent:
            return None
        try:
            return self._persist()
        except OSError as error:
            log.warning('feedchef: could not write the decision log: %s',
                        error)
            return None

    def _persist(self) -> str | None:
        directory = _dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.stat(directory).st_mode & 0o077:
            os.chmod(directory, 0o700)
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
        record = self._record()
        if record is not None:
            payload['audit'] = record
            # Floats go out as repr: json writes them so, and they come
            # back bit for bit. The visible text goes to a private sidecar.
            write_private(sidecar_path(path), self.audit.sidecar)
            write_private(path, payload)
            self._payload, self._path = payload, path
        else:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
            os.replace(tmp, path)
        self._rotate(directory)
        return path

    def _record(self) -> dict[str, Any] | None:
        """The schema-2 record of this page, None when the audit is off or
        could not be built (it never stops the log)."""
        if not self.audit.enabled:
            return None
        if self.audit.ordered is None:
            self.audit.failed('; '.join(self.log.reliability['errors']))
        try:
            self.record = self.audit.build()
        except Exception:
            log.debug('feedchef: audit record not built', exc_info=True)
            self.record = None
        return self.record

    def mark_cancelled(self) -> None:
        """The cook was cancelled after its record was written: the page
        was never shown, so the record must not read as a finished cook."""
        record = self.record
        if record is None or self._payload is None \
                or record['outcome']['status'] not in ('ok', 'peek'):
            return
        record['outcome'] = {'status': 'cancelled', 'error': ''}
        self._rewrite()

    def mark_shown(self, merge: dict[str, Any], write: bool = True) -> bool:
        """A peek whose posts were inserted: its outcome reads 'shown' and
        the record keeps the merge (see ``smartmerge.merge_record``). False
        when the record is not a peek or its file is gone."""
        record = self.record
        if record is None or self._payload is None \
                or record['outcome']['status'] != 'peek':
            return False
        record['outcome'] = {'status': 'shown', 'error': ''}
        record['merge'] = merge
        return self.flush() if write else True

    def writer(self):
        """A function that writes the record as it is now, to be run on
        the worker. It holds shallow copies made here, on the calling
        thread, so what the worker serializes is never changed by the
        page afterwards."""
        path, payload = self._path, self._payload
        if path is None or payload is None or self.record is None:
            return lambda: False
        payload = {**payload, 'audit': {**self.record}}

        def write():
            try:
                write_private(path, payload)
            except OSError as error:
                log.warning('feedchef: could not mark the log: %s', error)
                return False
            return True
        return write

    def flush(self) -> bool:
        """Write the record as it is now, on the calling thread."""
        return self._rewrite()

    def discard(self) -> None:
        """Delete this page's files: a peek nobody saw and a newer one
        replaced, so the folder does not fill with them."""
        if self._path is None or not self.persistent:
            return
        for path in (self._path, sidecar_path(self._path)):
            try:
                os.remove(path)
            except OSError:
                pass
        self._path = None

    def _rewrite(self) -> bool:
        if self._path is None:
            return False
        try:
            write_private(self._path, self._payload)
        except OSError as error:
            log.warning('feedchef: could not mark the log: %s', error)
            return False
        return True

    def _rotate(self, directory: str) -> None:
        prune(directory)


# Retention: the last cooks of each recipe, then a cap on the whole folder.
MAX_COOKS = 20
MAX_BYTES = 50 * 1024 * 1024
# A log, its text sidecar, or the temporary file of either mid-write.
_NAME = re.compile(r'^session_(\d+)[_](.+?)(?:__c([0-9a-f]+)_[0-9a-f]{6})?'
                   r'(\.text)?\.json(\.tmp)?$')
_PAGE = re.compile(r'(\.text)?\.json(\.tmp)?$')


def _cooks(directory: str) -> list[dict[str, Any]]:
    """Every cook in *directory*: its pages (a log, its sidecar and any
    temporary file each), the newest page's time, total size. A log from
    before the cook id existed is a cook of its own."""
    cooks: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in os.listdir(directory):
        match = _NAME.match(entry)
        full = os.path.join(directory, entry)
        if match is None or not os.path.isfile(full):
            continue
        recipe, cook = match.group(2), match.group(3) or entry
        row = cooks.setdefault((recipe, cook), {
            'recipe': recipe, 'pages': {}, 'mtime': (0.0, ''), 'size': 0})
        stat = os.stat(full)
        page = row['pages'].setdefault(_PAGE.sub('', entry), {
            'files': [], 'mtime': (0.0, ''), 'size': 0})
        page['files'].append(full)
        # The file name breaks a tie of the (coarse) modification time.
        page['mtime'] = max(page['mtime'], (stat.st_mtime, entry))
        page['size'] += stat.st_size
        row['mtime'] = max(row['mtime'], (stat.st_mtime, entry))
        row['size'] += stat.st_size
    return list(cooks.values())


def _remove_files(files: list[str]) -> None:
    for path in files:
        try:
            os.remove(path)
        except OSError:
            pass


def _remove(cook: dict[str, Any]) -> None:
    for page in cook['pages'].values():
        _remove_files(page['files'])


def _trim_pages(cook: dict[str, Any], limit: int) -> int:
    """Remove the oldest pages of one cook while it is over *limit*,
    keeping at least the newest page; the bytes left."""
    pages = sorted(cook['pages'].values(), key=lambda page: page['mtime'])
    total = cook['size']
    while total > limit and len(pages) > 1:
        oldest = pages.pop(0)
        total -= oldest['size']
        _remove_files(oldest['files'])
    return total


def prune(directory: str) -> None:
    """Keep the last ``MAX_COOKS`` cooks of each recipe (a busy recipe never
    evicts another one's history), then evict the oldest cooks overall
    while the folder is over ``MAX_BYTES``; the one cook left is trimmed
    page by page, oldest first. The newest page always stays."""
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
    if kept and total > MAX_BYTES:
        _trim_pages(kept[0], MAX_BYTES)


def recent_cooks_many(recipe_ids, limit: int = MAX_COOKS
                      ) -> dict[str, list[list[str]]]:
    """The log files of the last *limit* cooks of each recipe, oldest first:
    for each cook the files of its pages (the logs, not their text
    sidecars), oldest page first. The folder is read once, however many
    recipes are asked for; a folder that is not there has none."""
    found: dict[str, list[list[str]]] = {rid: [] for rid in recipe_ids}
    directory = _dir()
    if not os.path.isdir(directory):
        return found
    wanted = {_sanitize_recipe_id(rid): rid for rid in recipe_ids}
    by_recipe: dict[str, list[dict[str, Any]]] = {}
    for cook in _cooks(directory):
        if cook['recipe'] in wanted:
            by_recipe.setdefault(cook['recipe'], []).append(cook)
    for name, cooks in by_recipe.items():
        cooks.sort(key=lambda row: row['mtime'])
        for cook in cooks[-limit:]:
            pages = sorted(cook['pages'].values(),
                           key=lambda page: page['mtime'])
            found[wanted[name]].append(
                [path for page in pages for path in page['files']
                 if path.endswith('.json')
                 and not path.endswith('.text.json')])
    return found


def recent_cooks(recipe_id: str, limit: int = MAX_COOKS) -> list[list[str]]:
    """``recent_cooks_many`` for one recipe."""
    return recent_cooks_many([recipe_id], limit)[recipe_id]


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
