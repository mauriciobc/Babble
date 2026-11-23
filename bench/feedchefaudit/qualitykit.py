# SPDX-License-Identifier: GPL-3.0-or-later
"""Builders the Feed Chef quality tests share: the metrics of a kitchen
stage as the quality module makes them, a recipe with only what a test
names, and a pseudo-translation to prove text goes through the caller's
gettext."""

import json

from src.feedchef.auditreport import Words
from src.feedchef.recipe import default_recipe, recipe_to_dict

# What a translation does to a text: nothing but wrap it, so every text
# that is not wrapped did not go through the caller's words.
MARKED = Words(lambda s: '<%s>' % s, lambda a, b, n: '<%s>' % (
    a if n == 1 else b))


def metrics(total=50, low=0.0, high=20.0, avg=20.0, median=10.0,
            validity=100.0, entering=None, pantry=None, score=0.6,
            reasons=None):
    low_n = round(total * low / 100)
    high_n = min(round(total * high / 100), total - low_n)
    return {
        'kitchen': {'total_toots': total, 'validity_rate': validity,
                    'avg_quality_score': avg, 'median_quality_score': median,
                    'quality_distribution': {
                        'low': low_n, 'medium': total - low_n - high_n,
                        'high': high_n, 'very_high': 0},
                    'rejection_reasons': reasons or {}},
        'diet': {'total_toots': entering if entering is not None else total},
        'pantry': {'total_toots': pantry if pantry is not None
                   else (entering if entering is not None else total)},
        'recipe_quality_score': score}


def plain(**kwargs):
    recipe = default_recipe('plain')
    recipe.pantry.scope = kwargs.get('scope', 'federated')
    recipe.pantry.hashtags_include = kwargs.get('tags', [])
    recipe.kitchen.base_sort = kwargs.get('sort', 'score')
    recipe.kitchen.modifiers = list(kwargs.get('mods', []))
    return recipe


def by_id(found):
    """The first recommendation of each id (the most severe)."""
    first = {}
    for rec in found:
        first.setdefault(rec.id, rec)
    return first


def heats(found):
    return [rec.suggested for rec in found if rec.field == 'heat']


def stored(recipe):
    return json.dumps(recipe_to_dict(recipe), sort_keys=True)
