# SPDX-License-Identifier: GPL-3.0-or-later
"""The steps of "Hide the Least Engaging", the fallback that keeps a page
from running dry, and the Popular badge (no widgets, standard library only).

The reader picks a step, not a percent: Cool (nothing hidden), Warm, Hot or
Fire. A recipe imported with another percent keeps it exactly (the builder
calls it Custom). When hiding the posts of a page would leave fewer than
``MIN_LEFT`` of them, the page steps down to the next lower step, and on
down to Cool, until at least that many remain: the first step, from the
requested one down, that leaves enough. The page says which step it used.
The Hot badge marks the posts whose score is above the 90th percentile of
the page's own scores, with the same definition of a percentile that the
kitchen has always used.
"""

from __future__ import annotations

import math

# (name, percent), the hottest first: the order the fallback walks.
STEPS = (('fire', 90), ('hot', 75), ('warm', 50), ('cool', 0))
# A page keeps at least this many posts through the heat cut, or the cut
# steps down.
MIN_LEFT = 5
# The Hot badge: a score above this percentile of the page's.
FIRE_PERCENTILE = 90


def clean(value) -> float:
    """A stored percent as everything reads it (the kitchen, the audit, the
    builder): a number kept between 0 and 100, 0 when it is missing or not
    one (a heat modifier with no percent hides nothing)."""
    try:
        number = float(value if value is not None else 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(100.0, number))


def whole(value):
    """*value* as an int when it is a whole number (what the record and the
    builder show), else as it is."""
    return int(value) if float(value).is_integer() else float(value)


def step_of(percent) -> str | None:
    """The name of the standard step with this percent ('cool', 'warm',
    'hot' or 'fire'), or None for a Custom one."""
    number = clean(percent)
    for name, value in STEPS:
        if number == value:
            return name
    return None


def hidden_count(percent, total: int) -> int:
    """How many of *total* posts the cut at *percent* hides: exactly
    floor(P x n / 100), and never all of them (a batch of one is never
    emptied)."""
    number = clean(percent)
    if number <= 0 or total <= 0:
        return 0
    return max(0, min(int(math.floor(number * total / 100.0)), total - 1))


def left_after(percent, total: int) -> int:
    return total - hidden_count(percent, total)


def candidates(requested) -> list:
    """The percents the fallback tries, in order: the requested one, then
    every standard step below it, ending with Cool (0)."""
    number = clean(requested)
    if number <= 0:
        return [0]
    found = [whole(number)]
    found += [value for _name, value in STEPS if value < number]
    return found if found[-1] == 0 else found + [0]


def used(requested, total: int):
    """The percent a page of *total* posts is cut at: the first of
    ``candidates(requested)`` that leaves ``MIN_LEFT`` posts or more, or 0
    (Cool, which leaves them all)."""
    for percent in candidates(requested):
        if percent <= 0 or left_after(percent, total) >= MIN_LEFT:
            return percent
    return 0


def percentile(values: list[float], percent: float) -> float:
    """The *percent*th percentile of *values* by linear interpolation
    between the closest ranks; -1 for no values or a percent of 0 or less."""
    if not values or percent <= 0:
        return -1.0
    ordered = sorted(values)
    if percent >= 100:
        return ordered[-1]
    rank = (percent / 100.0) * (len(ordered) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    weight = rank - low
    # Exact when the two neighbours are equal (no rounding error from the
    # weights), which a threshold compared with > needs.
    return ordered[low] + (ordered[high] - ordered[low]) * weight


def fire_threshold(scores: list[float]) -> float | None:
    """The score a post must beat to wear the Popular badge on a page with
    these scores; None when no post can (an empty page, or every score
    equal: nothing stands out)."""
    if not scores or min(scores) == max(scores):
        return None
    return percentile(scores, FIRE_PERCENTILE)
