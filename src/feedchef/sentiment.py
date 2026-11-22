# SPDX-License-Identifier: GPL-3.0-or-later
"""Status sentiment via vendored VADER (EN) and LeIA (pt*).

Babble deviation from mashdon ``SimpleSentimentAnalyzer`` + JSON lexicon:
social-media compound scores from MIT VADER/LeIA, not the Android JSON.

Only English and Portuguese posts are judged. A post in another language,
or one whose server gave no language, has no score (``None``): a lexicon
of the wrong language reads nearly every word as neutral, so a score of 0
would claim a judgment nobody made. The stages treat None as "not judged":
the post is neither hidden by "Only Positive Posts" nor boosted by "Favor
Positive Posts".
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


def pick_engine(language: str | None) -> str | None:
    """'leia' for Portuguese, 'vader' for English, None for any other
    language or none at all."""
    lang = (language or '').strip().lower().replace('_', '-')
    if lang == 'pt' or lang.startswith('pt-'):
        return 'leia'
    if lang == 'en' or lang.startswith('en-'):
        return 'vader'
    return None


def compound_score(text: str, language: str | None = None) -> float | None:
    """VADER/LeIA ``compound`` in [-1, 1]; 0.0 for empty text; None when
    the language is not one that is judged."""
    engine = pick_engine(language)
    if engine is None:
        return None
    cleaned = (text or '').strip()
    if not cleaned:
        return 0.0
    scores = _analyzer(engine).polarity_scores(cleaned)
    try:
        return float(scores.get('compound') or 0.0)
    except (TypeError, ValueError):
        return 0.0


def language_of(status: dict[str, Any]) -> str | None:
    """The language of the post the reader sees (the boosted post's), or
    the wrapper's when the post carries none."""
    language = content_of(status).get('language') or status.get('language')
    return language if isinstance(language, str) else None


def is_judged(status: dict[str, Any]) -> bool:
    return pick_engine(language_of(status)) is not None


def score_status(status: dict[str, Any]) -> float | None:
    content = content_of(status)
    spoiler = str(content.get('spoiler_text') or content.get('spoilerText')
                  or '')
    text = '%s %s' % (strip_html(content.get('content')), spoiler)
    return compound_score(text, language_of(status))


def recipe_needs_sentiment(recipe) -> bool:
    if recipe.diet.hard_filters.positive_only:
        return True
    return any(mod.type == 'sentiment_boost'
               for mod in recipe.kitchen.modifiers)
