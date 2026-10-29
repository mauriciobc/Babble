# SPDX-License-Identifier: GPL-3.0-or-later

"""For You affinity: a Python port of Elk's signal derivation.

This module ports the engagement model behind the ranked feed from the Elk
fork at ``mauriciobc/elk@9597db5``:

* ``app/composables/for-you/signals.ts`` -- ``getEngagementTarget``,
  ``toSignal``, ``sortDedupTruncate``, ``decayFactor``,
  ``deriveAffinities`` and ``authorPenaltyIn``.

Elk keeps the engagement *sequence* and derives the affinity maps from it;
the maps are a cache, rebuildable from the sequence and ``now``. That is the
whole contract this module reproduces: a pure function of
``(engaged, dismissed, muted, now_ms)``.

Deliberate differences from the TypeScript (each is also noted where it
applies):

* Only the five kinds the server bootstrap produces are ported
  (``favourite``, ``reblog``, ``reply``, ``quote``, ``bookmark``). ``follow``
  and the passive kinds (``open``, ``dwell`` and the rest) are not: nothing
  here records them, and a constant nothing reads is dead weight.
* The sequence is not persisted or extended locally. ``bootstrap_engaged``
  builds it once from what the server already knows (CATCH-UP.md section
  4.8); there is no in-app recording path.
* ``now`` is always passed in (the snapshot's time); the clock is never read.
* Statuses are Mastodon JSON dicts (snake_case keys), not ``masto`` objects.
* ``MAX_SIGNALS_PER_KIND`` bounds every kind, because every ported kind is an
  engagement sequence; Elk's much larger ``follow`` bucket has no use here.

No GTK or GLib is imported: the module runs without a display.

Portions derived from Elk (https://github.com/elk-zone/elk), MIT License:

    Copyright (c) 2022-present, Elk contributors

    Permission is hereby granted, free of charge, to any person obtaining
    a copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to
    the following conditions:

    The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
    BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
    ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
    CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""

import datetime
import math
from dataclasses import dataclass
from typing import Callable

# Elk's ENGAGEMENT_KINDS, restricted to the kinds the bootstrap produces.
KINDS = ('favourite', 'reblog', 'reply', 'quote', 'bookmark')

# ENGAGEMENT_STRENGTH, for the ported kinds only.
ENGAGEMENT_STRENGTH = {
    'favourite': 1,
    'reblog': 2,
    'reply': 4,
    'quote': 4,
    'bookmark': 3,
}

# MUTUAL_MULTIPLIER: an exchange with a mutual says more than one with a
# stranger.
MUTUAL_MULTIPLIER = 3

# NOT_INTERESTED_STRENGTH: one dismissal undoes four favourites.
NOT_INTERESTED_STRENGTH = -4

# A post's tags and language are weaker evidence than its author, and a
# boost is a weaker endorsement than the original post.
TAG_SHARE = 0.5
LANGUAGE_SHARE = 0.25
BOOSTER_SHARE = 0.5

# Hashtag-stuffed posts must not spray affinity over a dozen tags.
MAX_TAGS_PER_SIGNAL = 5

# sortDedupTruncate's cap per kind.
MAX_SIGNALS_PER_KIND = 50

# EVIDENCE_CAP, for the ported kinds only. Each kind is its own ``within``
# bucket, so no two kinds share a cap.
EVIDENCE_CAP = {
    'favourite': 10,
    'reblog': 10,
    'reply': 10,
    'quote': 10,
    'bookmark': 10,
}

# Dismissals are deliberate, so they count like the deliberate kinds.
DISMISSAL_EVIDENCE_CAP = 10

# Evidence floor for a positive derived entry. Negative entries are exempt.
MIN_EVIDENCE = 0.75

# Affinities halve every two weeks, so yesterday's binge does not ossify.
DECAY_HALF_LIFE_MS = 14 * 24 * 60 * 60 * 1000

# Past this many half-lives a signal contributes nothing measurable.
MAX_DECAY_HALF_LIVES = 13

# Below this a derived entry is noise.
PRUNE_BELOW = 0.05


# EngagementSignal in signals.ts.
@dataclass(frozen=True)
class Signal:
    status_id: str
    author_id: str | None
    booster_id: str | None
    tags: tuple[str, ...]
    language: str | None
    at_ms: int
    mutual: bool = False
    weight: float | None = None


# The four derived maps deriveAffinities returns.
@dataclass(frozen=True)
class Affinities:
    author: dict[str, float]
    booster: dict[str, float]
    tag: dict[str, float]
    language: dict[str, float]


def _signal_weight(value):
    """``toSignal``'s weight filter: a number, at least 0, not 1.

    JavaScript keeps the value when ``typeof value === 'number'`` and
    ``value >= 0 && value !== 1``; NaN fails the comparison, and a boolean is
    not a number. ``None`` is what the TypeScript leaves (``undefined``).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value >= 0 and value != 1:
        return float(value)
    return None


# getEngagementTarget + toSignal in signals.ts. Boosts credit the original
# author and keep the booster; tags are lower-cased, deduplicated in order
# and capped; an empty post id yields no signal at all.
def signal_from_status(status, at_ms, mutual=False) -> Signal | None:
    if not isinstance(status, dict):
        return None
    reblog = status.get('reblog')
    post = reblog if isinstance(reblog, dict) else status
    status_id = post.get('id')
    if not status_id:
        return None

    account = post.get('account')
    author_id = account.get('id') if isinstance(account, dict) else None
    booster_id = None
    if isinstance(reblog, dict):
        booster = status.get('account')
        booster_id = booster.get('id') if isinstance(booster, dict) else None

    # getEngagementTarget lower-cases and slices, then toSignal deduplicates
    # and slices again; both slices are kept so a duplicate inside the first
    # five does not let a sixth tag in.
    names = []
    for tag in post.get('tags') or ():
        name = tag.get('name') if isinstance(tag, dict) else None
        if name:
            names.append(name.lower())
    tags = tuple(dict.fromkeys(names[:MAX_TAGS_PER_SIGNAL]))
    tags = tags[:MAX_TAGS_PER_SIGNAL]

    # Elk's caller can hand toSignal a weight; a status has none today, so
    # this is normally ``None``, which counts as 1.
    return Signal(
        status_id=status_id,
        author_id=author_id,
        booster_id=booster_id,
        tags=tags,
        language=post.get('language') or None,
        at_ms=at_ms,
        mutual=bool(mutual),
        weight=_signal_weight(status.get('weight')),
    )


# decayFactor in signals.ts. Clamped at both ends: a future stamp counts as
# brand new, and one past the horizon decays to zero instead of underflowing.
def decay_factor(age_ms, half_life_ms=DECAY_HALF_LIFE_MS) -> float:
    if not (age_ms > 0):
        return 1.0
    if age_ms >= half_life_ms * MAX_DECAY_HALF_LIVES:
        return 0.0
    return 2 ** (-age_ms / half_life_ms)


# sortDedupTruncate in signals.ts: newest first, one entry per post, capped.
def sort_dedup_truncate(signals, max_count) -> list[Signal]:
    ordered = sorted(signals, key=lambda signal: signal.at_ms, reverse=True)
    seen = set()
    out = []
    for signal in ordered:
        if signal.status_id in seen:
            continue
        seen.add(signal.status_id)
        out.append(signal)
        if len(out) >= max_count:
            break
    return out


def _add(record, key, weight):
    """``add`` in signals.ts: skip empty keys and zero weights."""
    if not key or not weight:
        return
    record[key] = record.get(key, 0) + weight


def _prune(record, share):
    """``prune`` in signals.ts.

    Drops anything below PRUNE_BELOW in magnitude, and any *positive* entry
    that has not cleared the evidence floor for its map. Negatives are
    exempt, because a dismissal is meant to be tiny and accumulate.
    """
    floor = MIN_EVIDENCE * share
    for key in list(record):
        value = record[key]
        enough = (value >= floor if value > 0
                  else abs(value) >= PRUNE_BELOW)
        if not enough:
            del record[key]


# deriveAffinities in signals.ts, over the ported kinds.
def derive_affinities(engaged, dismissed, muted, now_ms) -> Affinities:
    author = {}
    booster = {}
    tag = {}
    language = {}
    muted_ids = set(muted or ())

    # How many signals of each kind have already spoken for each key, so
    # repetition of a weak signal saturates instead of accumulating.
    counted = {}

    def within(map_name, bucket, key, cap):
        ident = '%s|%s|%s' % (map_name, bucket, key)
        count = counted.get(ident, 0) + 1
        counted[ident] = count
        return count <= cap

    def contribute(signal, bucket, strength, cap, half_life):
        magnitude = 1 if signal.weight is None else signal.weight
        weight = (strength * magnitude
                  * decay_factor(now_ms - signal.at_ms, half_life))
        # ``if (!weight) return`` in the TypeScript. A NaN weight is falsy
        # there, so the signal is skipped *before* ``within`` spends one of
        # its evidence slots -- Python's truthiness would let it through,
        # and the slots it took would not come back.
        if not weight or weight != weight:
            return
        if (signal.author_id and signal.author_id not in muted_ids
                and within('author', bucket, signal.author_id, cap)):
            _add(author, signal.author_id, weight)
        if (signal.booster_id and signal.booster_id not in muted_ids
                and within('booster', bucket, signal.booster_id, cap)):
            _add(booster, signal.booster_id, weight * BOOSTER_SHARE)
        for name in signal.tags:
            if within('tag', bucket, name, cap):
                _add(tag, name, weight * TAG_SHARE)
        if (signal.language
                and within('language', bucket, signal.language, cap)):
            _add(language, signal.language, weight * LANGUAGE_SHARE)

    for kind in KINDS:
        signals = engaged.get(kind)
        if not signals:
            continue
        base = ENGAGEMENT_STRENGTH[kind]
        cap = EVIDENCE_CAP[kind]
        for signal in signals:
            strength = base * MUTUAL_MULTIPLIER if signal.mutual else base
            contribute(signal, kind, strength, cap, DECAY_HALF_LIFE_MS)

    # Dismissals are their own bucket: a viewer who dismisses ten posts by
    # one author has said it ten times.
    for signal in dismissed or ():
        contribute(signal, 'dismissed', NOT_INTERESTED_STRENGTH,
                   DISMISSAL_EVIDENCE_CAP, DECAY_HALF_LIFE_MS)

    _prune(author, 1)
    _prune(booster, BOOSTER_SHARE)
    _prune(tag, TAG_SHARE)
    _prune(language, LANGUAGE_SHARE)
    return Affinities(author=author, booster=booster, tag=tag,
                      language=language)


def _target_author_id(status):
    """The original author of the post an engagement target refers to."""
    if not isinstance(status, dict):
        return None
    reblog = status.get('reblog')
    post = reblog if isinstance(reblog, dict) else status
    account = post.get('account')
    return account.get('id') if isinstance(account, dict) else None


def _at_ms(status):
    """A status's created_at in epoch milliseconds; malformed stamps are 0."""
    if not isinstance(status, dict):
        return 0
    value = status.get('created_at')
    if not isinstance(value, str):
        return 0
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    delta = moment - epoch
    return ((delta.days * 86400 + delta.seconds) * 1000
            + delta.microseconds // 1000)


def _is_mutual(status, mutual_ids):
    author_id = _target_author_id(status)
    return bool(author_id and mutual_ids and author_id in mutual_ids)


# The server bootstrap (CATCH-UP.md section 4.8): favourites, bookmarks and
# the viewer's own statuses, mapped onto Elk's kinds.
def bootstrap_engaged(favourites, bookmarks, own_statuses,
                      viewer_id, mutual_ids) -> dict[str, list[Signal]]:
    engaged = {kind: [] for kind in KINDS}

    for status in favourites or ():
        signal = signal_from_status(
            status, _at_ms(status), mutual=_is_mutual(status, mutual_ids))
        if signal is not None:
            engaged['favourite'].append(signal)

    for status in bookmarks or ():
        signal = signal_from_status(
            status, _at_ms(status), mutual=_is_mutual(status, mutual_ids))
        if signal is not None:
            engaged['bookmark'].append(signal)

    for status in own_statuses or ():
        if not isinstance(status, dict):
            continue
        at_ms = _at_ms(status)

        # A boost is your endorsement of the inner post; the event happened
        # when the wrapper was written.
        if isinstance(status.get('reblog'), dict):
            signal = signal_from_status(
                status, at_ms, mutual=_is_mutual(status, mutual_ids))
            if signal is not None:
                engaged['reblog'].append(signal)

        # A reply's target is the account replied to, not the viewer; its own
        # post's tags and language say nothing about that account.
        reply_to = status.get('in_reply_to_account_id')
        status_id = status.get('id')
        if reply_to and reply_to != viewer_id and status_id:
            engaged['reply'].append(Signal(
                status_id=status_id,
                author_id=reply_to,
                booster_id=None,
                tags=(),
                language=None,
                at_ms=at_ms,
                mutual=bool(mutual_ids) and reply_to in mutual_ids,
            ))

        # A quote is your endorsement of the quoted post, at the quote's time.
        quote = status.get('quote')
        quoted = quote.get('quoted_status') if isinstance(quote, dict) else None
        if isinstance(quoted, dict):
            signal = signal_from_status(
                quoted, at_ms, mutual=_is_mutual(quoted, mutual_ids))
            if signal is not None:
                engaged['quote'].append(signal)

    return {kind: sort_dedup_truncate(engaged[kind], MAX_SIGNALS_PER_KIND)
            for kind in KINDS}


def affinity_signal(values, key):
    if not values or not key:
        return 0.0
    value = values.get(key)
    if not value or not math.isfinite(value):
        return 0.0
    positive = value > 0
    below = equal = total = 0
    magnitude = abs(value)
    for other in values.values():
        if (not other or not math.isfinite(other)
                or (other > 0) != positive):
            continue
        total += 1
        if abs(other) < magnitude:
            below += 1
        elif abs(other) == magnitude:
            equal += 1
    if not total:
        return 0.0
    percentile = (below + equal / 2) / total
    return percentile if positive else -percentile


@dataclass(frozen=True)
class _AffinityProvider:
    author_affinity: Callable[[str], float]
    tag_affinity: Callable[[str], float]
    language_affinity: Callable[[str], float]
    booster_affinity: Callable[[str], float]
    author_penalty: Callable[[str], float]


# authorPenaltyIn in signals.ts (decision D10): 1 for an explicit mute,
# otherwise the saturating sum of the author's decayed dismissals.
def author_penalty_in(dismissed, muted_ids, author_id, now_ms) -> float:
    if not author_id:
        return 0.0
    if muted_ids and author_id in muted_ids:
        return 1.0

    dismissals = 0.0
    for signal in dismissed or ():
        if signal.author_id == author_id:
            dismissals += decay_factor(now_ms - signal.at_ms)
    if not dismissals:
        return 0.0
    return min(1.0, 1 - 2 ** -dismissals)


def resolver(affinities, dismissed, muted_ids, now_ms) -> _AffinityProvider:
    return _AffinityProvider(
        author_affinity=lambda key: affinity_signal(affinities.author, key),
        tag_affinity=lambda key: affinity_signal(affinities.tag, key),
        language_affinity=lambda key: affinity_signal(
            affinities.language, key),
        booster_affinity=lambda key: affinity_signal(
            affinities.booster, key),
        author_penalty=lambda key: author_penalty_in(
            dismissed, muted_ids, key, now_ms),
    )
