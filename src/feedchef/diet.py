# SPDX-License-Identifier: GPL-3.0-or-later
"""Diet stage — mashdon ``passDiet`` order on reblog content.

The rules themselves are the predicates of ``contract``; this stage only
applies them in order and records why a post was filtered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import contract
from .recipe import DietConfig, FeedRecipe


@dataclass
class DietDecision:
    post_id: str
    verdict: str  # PASSED | FILTERED
    reason: str
    details: str = ''


def _details(reason: str, status: dict[str, Any],
             native_bluesky: bool = False) -> str:
    if reason == 'min_content_length':
        return str(contract.text_length(status, native_bluesky))
    return ''


def pass_diet(
    status: dict[str, Any],
    diet: DietConfig,
    *,
    sentiment: contract.SentimentJudge | None = None,
    recipe: FeedRecipe | None = None,
    native_bluesky: bool = False,
) -> DietDecision:
    """*sentiment* judges a post (None: the post's language is not read)."""
    post_id = str(status.get('id') or '')
    recipe = recipe or FeedRecipe(diet=diet)
    found = contract.violations(
        recipe, status, sentiment=sentiment, only=contract.DIET_IDS,
        first=True, native_bluesky=native_bluesky)
    if found:
        return DietDecision(post_id, 'FILTERED', found[0],
                            _details(found[0], status, native_bluesky))
    return DietDecision(post_id, 'PASSED', 'ok')


def apply_diet(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    *,
    sentiment: contract.SentimentJudge | None = None,
    native_bluesky: bool = False,
) -> tuple[list[dict[str, Any]], list[DietDecision]]:
    kept = []
    decisions = []
    for status in statuses:
        decision = pass_diet(status, recipe.diet, sentiment=sentiment,
                             recipe=recipe, native_bluesky=native_bluesky)
        decisions.append(decision)
        if decision.verdict == 'PASSED':
            kept.append(status)
    return kept, decisions
