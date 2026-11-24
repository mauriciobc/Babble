# SPDX-License-Identifier: GPL-3.0-or-later
"""How a More Like This focus fades (the recipe's `focus_decay`).

Java stores `focus_decay` in the recipe and nothing applies it. This is
Babble's definition of it: on each refresh the reader asks for while a
focus is active (hashtags, keywords, or both), the focus's strength
(`active_focus_strength`, 1.0 when it is set) and every keyword weight are
multiplied by `focus_decay`; a keyword whose weight is then below
`MIN_WEIGHT` is dropped; and the whole focus, hashtags too, clears when the
strength falls below `MIN_WEIGHT`, or when it had keywords and none is left.
The caller saves the recipe and updates the focus banner when this returns
True.

A keyword's weight is read as the relevance sort reads it
(`relevance.usable_weights`: 1.0 for all when they are not given for every
keyword, 0.1 for one that is not a number above zero). Because every weight
is multiplied by the same number, the order of the posts does not change
while keywords remain (the relevance reference vector is scaled by its
largest weight); the decay is the focus's lifetime, about
`log(0.1) / log(focus_decay)` refreshes (22 at 0.9), and a keyword imported
with a smaller weight goes first. The strength is what fades a focus of
hashtags only, which has no weights. A `focus_decay` of 1.0 or more never
decays. No GTK.
"""

from __future__ import annotations

import math

from .recipe import KitchenConfig
from .relevance import usable_weights

# A focus weight, or the strength of the focus, below this is gone.
MIN_WEIGHT = 0.1


def decay_focus(kitchen: KitchenConfig) -> bool:
    """Apply one refresh's decay to the focus of *kitchen*; True when
    something changed (strength, weights, dropped keywords or a cleared
    focus)."""
    keywords = kitchen.active_focus_keywords or []
    hashtags = kitchen.active_focus_hashtags or []
    decay = kitchen.focus_decay
    if not (keywords or hashtags) or isinstance(decay, bool) \
            or not isinstance(decay, (int, float)) \
            or not math.isfinite(decay) or decay >= 1.0:
        return False
    factor = max(0.0, float(decay))
    strength = kitchen.active_focus_strength
    if not isinstance(strength, (int, float)) or not math.isfinite(strength):
        strength = 1.0
    strength *= factor
    kept = []
    for word, weight in zip(keywords, usable_weights(
            list(keywords), kitchen.active_focus_weights), strict=True):
        if weight * factor >= MIN_WEIGHT:
            kept.append((word, weight * factor))
    if strength < MIN_WEIGHT or (keywords and not kept):
        kitchen.active_focus_keywords = None
        kitchen.active_focus_weights = None
        kitchen.active_focus_hashtags = None
        kitchen.active_focus_strength = 1.0
        return True
    kitchen.active_focus_strength = strength
    if keywords:
        kitchen.active_focus_keywords = [word for word, _w in kept]
        kitchen.active_focus_weights = [weight for _w, weight in kept]
    return True
