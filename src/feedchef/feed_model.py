# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible Feed Chef list model — not TimelineModel id-append semantics."""

from __future__ import annotations

from typing import Any


class RequestGate:
    """Which of the page's requests may still deliver (display-free).

    Every refresh or older-page request bumps one counter; a result is
    accepted only while its generation is the current one. A refresh stays
    in flight until it finishes, and meanwhile no older page may start.
    """

    def __init__(self):
        self.generation = 0
        self.refreshing = False
        self.paging = False

    def begin_refresh(self) -> int:
        self.generation += 1
        self.refreshing = True
        self.paging = False
        return self.generation

    def begin_page(self) -> int | None:
        if self.refreshing or self.paging:
            return None
        self.generation += 1
        self.paging = True
        return self.generation

    def accept(self, generation: int) -> bool:
        return generation == self.generation

    def finish(self, generation: int) -> bool:
        """Close the request; False (and no state change) when stale."""
        if not self.accept(generation):
            return False
        self.refreshing = False
        self.paging = False
        return True


class FeedChefModel:
    """Ordered statuses plus the scope pagination cursor."""

    def __init__(self):
        self.items: list[dict[str, Any]] = []
        self._ids: set[str] = set()
        self.scope_oldest_id: str | None = None
        self.has_more: bool = False
        self.score_map: dict[str, float] = {}

    def clear(self) -> None:
        self.items.clear()
        self._ids.clear()
        self.scope_oldest_id = None
        self.has_more = False
        self.score_map.clear()

    def replace(self, statuses: list[dict[str, Any]], *,
                scope_oldest_id: str | None,
                has_more: bool,
                score_map: dict[str, float] | None = None) -> None:
        self.clear()
        for status in statuses:
            sid = str(status.get('id') or '')
            if not sid or sid in self._ids:
                continue
            self._ids.add(sid)
            self.items.append(status)
        self.scope_oldest_id = scope_oldest_id
        self.has_more = has_more
        if score_map:
            self.score_map.update(score_map)

    def append_page(self, statuses: list[dict[str, Any]], *,
                    scope_oldest_id: str | None,
                    has_more: bool,
                    score_map: dict[str, float] | None = None) -> int:
        added = 0
        for status in statuses:
            sid = str(status.get('id') or '')
            if not sid or sid in self._ids:
                continue
            self._ids.add(sid)
            self.items.append(status)
            added += 1
        if scope_oldest_id is not None:
            self.scope_oldest_id = scope_oldest_id
        self.has_more = has_more
        if score_map:
            self.score_map.update(score_map)
        return added

    def __len__(self) -> int:
        return len(self.items)
