# SPDX-License-Identifier: GPL-3.0-or-later
"""The health of a recipe from its last cooks (no widgets, standard library
only).

The input is the list of cook summaries (``quality.summaries``, oldest first;
numbers and flags only), so the badge and the alert are cheap to recompute
after every cook, on a worker. The state is moshidon's: "Working Well" or
"Needs Attention" with the reasons, and an alert (a banner on the recipe's
page, an offer to run the A/B optimizer) when the score has stayed under 0.5
for the last 10 cooks.

The score is mostly engagement (see ``quality``): a recipe that is Needs
Attention is one whose posts people did not react to, not one that breaks
its rules.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from .words import ENGLISH, Words

# The alert: the mean score of the last WINDOW cooks is under ALERT_BELOW
# (moshidon: "after 10 uses, if the quality is under 0.5").
WINDOW = 10
ALERT_BELOW = 0.5
# A badge needs a few cooks before it judges the score (one bad cook is not
# a trend); the other reasons read the latest cook alone.
BADGE_MIN_COOKS = 3
# The latest cook shows fewer posts than this (moshidon's fallback rule 1).
FEW_POSTS = 5
# The latest cook's share of Low posts above which it is "mostly low"
# (moshidon's fallback rule 3).
MOSTLY_LOW_PCT = 85.0

WORKING = 'working_well'
ATTENTION = 'needs_attention'
UNKNOWN = 'unknown'

REASONS = ('low_score', 'few_posts', 'zero_scores', 'mostly_low')


class Health(NamedTuple):
    state: str
    reasons: tuple[str, ...]
    # The mean score of the last cooks looked at (None: no cooks), and how
    # many of them.
    mean: float | None
    cooks: int
    # True when the last WINDOW cooks are all there and their mean is under
    # ALERT_BELOW.
    alert: bool


def mean_score(summaries: list[dict[str, Any]], window: int = WINDOW
               ) -> float | None:
    """The mean recipe score of the last *window* cooks, or None."""
    last = summaries[-window:]
    if not last:
        return None
    return sum(item['score'] for item in last) / len(last)


def alert(summaries: list[dict[str, Any]], window: int = WINDOW,
          below: float = ALERT_BELOW) -> bool:
    """The score stayed under *below* over the last *window* cooks: all
    *window* of them have to be there, and their mean is under it."""
    if len(summaries) < window:
        return False
    found = mean_score(summaries, window)
    return found is not None and found < below


def assess(summaries: list[dict[str, Any]]) -> Health:
    """The state of a recipe from its cook summaries, oldest first."""
    if not summaries:
        return Health(UNKNOWN, (), None, 0, False)
    latest = summaries[-1]
    found = mean_score(summaries)
    reasons = []
    if len(summaries) >= BADGE_MIN_COOKS and found is not None \
            and found < ALERT_BELOW:
        reasons.append('low_score')
    if latest['posts'] < FEW_POSTS:
        reasons.append('few_posts')
    if latest['zero_scores']:
        reasons.append('zero_scores')
    if latest['low_pct'] > MOSTLY_LOW_PCT:
        reasons.append('mostly_low')
    return Health(ATTENTION if reasons else WORKING, tuple(reasons), found,
                  min(len(summaries), WINDOW), alert(summaries))


def state_text(state: str, words: Words = ENGLISH) -> str:
    _ = words.gettext
    return {WORKING: _('Working Well'), ATTENTION: _('Needs Attention'),
            }.get(state, _('Not Enough Cooks Yet'))


def reason_text(reason: str, words: Words = ENGLISH) -> str:
    _ = words.gettext
    return {
        'low_score': _('The score has been low over the last cooks'),
        'few_posts': _('The last cook showed very few posts'),
        'zero_scores': _('Every post scored zero in the last cook'),
        'mostly_low': _('Most posts of the last cook got little '
                        'engagement'),
    }.get(reason, reason)
