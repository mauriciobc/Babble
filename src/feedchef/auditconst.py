# SPDX-License-Identifier: GPL-3.0-or-later
"""What the audit record is made of and does not change: the schema and
engine revision, the text cap, and the three small functions that name a
stage, a recipe and a text. A leaf (no import of ``audit`` or
``auditbuild``), so both import it at load time; ``audit`` re-exports
these names for its callers."""

from __future__ import annotations

import hashlib
import json

from .recipe import FeedRecipe, recipe_to_dict

SCHEMA = 2
# Bumped by hand whenever the meaning of a stage changes; a replay refuses
# a record made by another revision unless it is forced.
ENGINE_REV = 5
# The stripped text kept per post in the sidecar.
TEXT_CAP = 2000

_STAGES = {'reach_not_followed': 'reach', 'viewer_hide': 'viewer',
           'heat_cut': 'kitchen', 'author_cap': 'kitchen',
           'cluster_cap': 'kitchen', 'instance_cap': 'kitchen'}


def stage_of(reason: str) -> str:
    if reason.startswith('pantry_'):
        return 'pantry'
    return _STAGES.get(reason) or reason.partition(':')[0]


def text_digest(text: str, spoiler: str) -> str:
    """sha256 of the text and the spoiler exactly as the sidecar keeps
    them: the proof that the sidecar is the one this record was made with."""
    return hashlib.sha256(
        (text + '\0' + spoiler).encode('utf-8')).hexdigest()


def recipe_hash(recipe: FeedRecipe) -> str:
    """sha256 of the recipe as the same canonical JSON everywhere."""
    text = json.dumps(recipe_to_dict(recipe), sort_keys=True,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
