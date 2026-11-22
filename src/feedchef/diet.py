# SPDX-License-Identifier: GPL-3.0-or-later
"""Diet stage — mashdon ``passDiet`` order on reblog content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .recipe import DietConfig, FeedRecipe
from .textutil import content_of, strip_html

SentimentFn = Callable[[str], float]


@dataclass
class DietDecision:
    post_id: str
    verdict: str  # PASSED | FILTERED
    reason: str
    details: str = ''


def _keyword_patterns(blocklist: list[str]) -> list[re.Pattern[str]]:
    patterns = []
    for word in blocklist:
        # Java Pattern.quote + \\b — ASCII word boundaries.
        patterns.append(re.compile(r'\b%s\b' % re.escape(word), re.I))
    return patterns


def analyze_sentiment_stub(text: str) -> float:
    """Default analyzer: always 0.0 (Java path when lexicon missing)."""
    return 0.0


def pass_diet(
    status: dict[str, Any],
    diet: DietConfig,
    *,
    sentiment: SentimentFn | None = None,
) -> DietDecision:
    content = content_of(status)
    post_id = str(status.get('id') or '')
    hard = diet.hard_filters
    html = str(content.get('content') or '').lower()
    spoiler = str(content.get('spoiler_text') or content.get('spoilerText')
                  or '').lower()
    for pattern in _keyword_patterns(diet.keyword_blocklist):
        if pattern.search(html) or pattern.search(spoiler):
            return DietDecision(post_id, 'FILTERED', 'keyword_blocklist',
                                pattern.pattern)
    attachments = content.get('media_attachments') or content.get(
        'mediaAttachments') or []
    if hard.must_have_media and not attachments:
        return DietDecision(post_id, 'FILTERED', 'must_have_media')
    if hard.must_have_alt_text and attachments:
        for item in attachments:
            desc = (item or {}).get('description')
            if not (desc and str(desc).strip()):
                return DietDecision(post_id, 'FILTERED', 'must_have_alt_text')
    if hard.no_sensitive_media and bool(content.get('sensitive')):
        return DietDecision(post_id, 'FILTERED', 'no_sensitive_media')
    if hard.min_content_length > 0:
        length = len(strip_html(content.get('content')))
        if length < hard.min_content_length:
            return DietDecision(post_id, 'FILTERED', 'min_content_length',
                                str(length))
    if hard.positive_only:
        fn = sentiment or analyze_sentiment_stub
        text = strip_html(content.get('content')) + ' ' + spoiler
        score = fn(text)
        if score < 0.0:
            return DietDecision(post_id, 'FILTERED', 'positive_only',
                                str(score))
    return DietDecision(post_id, 'PASSED', 'ok')


def apply_diet(
    statuses: list[dict[str, Any]],
    recipe: FeedRecipe,
    *,
    sentiment: SentimentFn | None = None,
) -> tuple[list[dict[str, Any]], list[DietDecision]]:
    kept = []
    decisions = []
    for status in statuses:
        decision = pass_diet(status, recipe.diet, sentiment=sentiment)
        decisions.append(decision)
        if decision.verdict == 'PASSED':
            kept.append(status)
    return kept, decisions
