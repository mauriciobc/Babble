# SPDX-License-Identifier: GPL-3.0-or-later
"""The recipe's recent audit records and what is made of them (no widgets,
standard library only; meant to run on a worker, never on the main thread).

The decision log keeps the audit record of every page it cooked
(``decisionlog``). The health badge of a recipe, the Kitchen Report's
quality section and the alert all read the last cooks from there, so a
recipe that is not open has a badge too. The cook on screen is also handed in
(its records are in memory and, between the cook and the write, may not be on
disk yet); a record that is both is counted once.
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

from . import advice, decisionlog, health, quality
from .recipe import FeedRecipe

log = logging.getLogger('babble.feedchef')
# The recipes whose logs could not be judged, so that each is said once.
_said: set[str] = set()


class Analysis(NamedTuple):
    health: health.Health
    # quality.assess_pages of the cook on screen, or None without one.
    metrics: dict[str, Any] | None
    recommendations: list[advice.Recommendation]
    fresh_fallback: bool


def _shaped(record: Any) -> bool:
    """Whether *record* has the shape the quality code reads: a log that
    was edited, truncated or written by something else is left out rather
    than allowed to break the numbers of a recipe."""
    if not isinstance(record, dict):
        return False
    for key in ('outcome', 'recipe', 'clock', 'summary'):
        if record.get(key) is not None and not isinstance(
                record[key], dict):
            return False
    posts = record.get('posts')
    if posts is not None and not (isinstance(posts, list) and all(
            isinstance(p, dict) for p in posts)):
        return False
    for entry in posts or ():
        for key in ('facts', 'rules', 'removal', 'score'):
            if entry.get(key) is not None and not isinstance(
                    entry[key], dict):
                return False
    return True


def _read(path: str) -> dict[str, Any] | None:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            record = (json.load(handle) or {}).get('audit')
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return record if _shaped(record) else None


def _records(files: list[list[str]]) -> list[dict[str, Any]]:
    found = []
    for cook in files:
        found.extend(r for r in map(_read, cook) if r is not None)
    return found


def load_records(recipe_id: str, cooks: int = health.WINDOW
                 ) -> list[dict[str, Any]]:
    """The audit records of the last *cooks* cooks of a recipe, oldest
    first; a log that cannot be read or has no record is left out."""
    return _records(decisionlog.recent_cooks(recipe_id, cooks))


def _key(record: dict[str, Any]) -> tuple:
    recipe = record.get('recipe')
    return (record.get('cook_id'), record.get('page_index'),
            record.get('hop'),
            recipe.get('hash') if isinstance(recipe, dict) else None)


def merged(recipe_id: str, live=()) -> list[dict[str, Any]]:
    """The records on disk and the live ones, a page once (the live one
    stands)."""
    live = list(live)
    seen = {_key(record) for record in live}
    return [record for record in load_records(recipe_id)
            if _key(record) not in seen] + live


def health_of(recipe_id: str, live=()) -> health.Health:
    return health.assess(quality.summaries(merged(recipe_id, live)))


def health_map(recipe_ids) -> dict[str, health.Health]:
    """The health of each recipe, from the logs alone. The folder is read
    once; a recipe whose logs cannot be judged is Unknown (and said once),
    and never takes the others with it."""
    recipe_ids = list(recipe_ids)
    files = decisionlog.recent_cooks_many(recipe_ids, health.WINDOW)
    found = {}
    for recipe_id in recipe_ids:
        try:
            found[recipe_id] = health.assess(
                quality.summaries(_records(files[recipe_id])))
        except Exception:
            if recipe_id not in _said:
                _said.add(recipe_id)
                log.warning('feedchef: the logs of recipe %s could not be '
                            'judged', recipe_id, exc_info=True)
            found[recipe_id] = health.assess([])
    return found


def analyze(recipe: FeedRecipe, live, others=None) -> Analysis:
    """Everything the quality section needs: the health of *recipe*, the
    metrics of the cook on screen (*live*, its records) and the advice
    for it (judged against *others*, the reader's other recipes)."""
    live = list(live)
    found = quality.assess_pages(live) if live else None
    if found is not None and not found['pages']:
        found = None
    return Analysis(
        health_of(recipe.id, live), found,
        advice.recommend(recipe, found, list(others or ()) or None),
        any(record.get('fresh_fallback') for record in live
            if (record.get('outcome') or {}).get('status')
            in quality.SHOWN))
