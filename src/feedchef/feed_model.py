# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible Feed Chef list model — not TimelineModel id-append semantics."""

from __future__ import annotations

from typing import Any


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
