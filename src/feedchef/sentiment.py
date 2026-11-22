# SPDX-License-Identifier: GPL-3.0-or-later
"""Status sentiment via vendored VADER (EN) and LeIA (pt*).

Babble deviation from mashdon ``SimpleSentimentAnalyzer`` + JSON lexicon:
social-media compound scores from MIT VADER/LeIA, not the Android JSON.
"""

from __future__ import annotations

from typing import Any

from .textutil import content_of, strip_html

_analyzers: dict[str, Any] = {}


def _analyzer(kind: str):
    cached = _analyzers.get(kind)
    if cached is not None:
        return cached
    if kind == 'leia':
        from .vendor.leia import SentimentIntensityAnalyzer
    else:
        from .vendor.vader import SentimentIntensityAnalyzer
    analyzer = SentimentIntensityAnalyzer()
    _analyzers[kind] = analyzer
    return analyzer


def pick_engine(language: str | None) -> str:
    lang = (language or '').strip().lower().replace('_', '-')
    if lang == 'pt' or lang.startswith('pt-'):
        return 'leia'
    return 'vader'


def compound_score(text: str, language: str | None = None) -> float:
    """Return VADER/LeIA ``compound`` in [-1, 1], or 0.0 for empty text."""
    cleaned = (text or '').strip()
    if not cleaned:
        return 0.0
    engine = pick_engine(language)
    scores = _analyzer(engine).polarity_scores(cleaned)
    try:
        return float(scores.get('compound') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def score_status(status: dict[str, Any]) -> float:
    content = content_of(status)
    spoiler = str(content.get('spoiler_text') or content.get('spoilerText')
                  or '')
    text = '%s %s' % (strip_html(content.get('content')), spoiler)
    language = content.get('language') or status.get('language')
    return compound_score(text, language if isinstance(language, str)
                          else None)


def recipe_needs_sentiment(recipe) -> bool:
    if recipe.diet.hard_filters.positive_only:
        return True
    return any(mod.type == 'sentiment_boost'
               for mod in recipe.kitchen.modifiers)
