# SPDX-License-Identifier: GPL-3.0-or-later
"""How well a recipe's posts did, computed from audit records (no widgets,
no GTK, standard library only, no new fetch).

A faithful port of moshidon's ``toot_quality_metrics.py``: the same formulas,
thresholds and bands, applied to the posts an audit record (schema 2,
engine revision 5) already holds, stage by stage:

* **pantry**: the posts the pantry's hashtags let through;
* **diet**: those still there after the diet, "Only People I Follow" and the
  viewer's own filters (what enters the kitchen);
* **kitchen**: the posts the page shows.

Each stage's posts are judged against the recipe's own criteria (the
pantry's hashtags and the diet's rules, read from the record's per-post
``rules``), and scored:

* validation score: 1 for a post that meets every criterion, halved when
  it fails the pantry's, 0 when it fails the diet's;
* quality score: the engagement the kitchen uses (favorites x 1 + boosts x
  2.5 + replies x 1.5), times the recipe's "Favor Fresh Posts" fade
  (1 - age / fade-out, at least 0), from the post's counts and dates in the
  record, with no floor (moshidon's formula, so the numbers compare);
* relevance score: validation x 0.4 + min(quality / 100, 1) x 0.6.

The aggregate of a stage has the validity rate, the mean and median
quality, the mean relevance, the percentiles P25 to P95 (the value at
index int(p x n / 100) of the sorted scores) and the bands Low, Medium,
High and Very High (under 25, 50, 75 and 100 percent of the best score).
The recipe score is validity x 0.4 + min(mean quality / 100, 1) x 0.35 +
mean relevance x 0.25, of the kitchen stage.

**What the score measures.** Babble enforces every filter of a recipe and
checks each post again before it is shown, so at the diet and kitchen stages
validity is 100% by construction. The pantry stage shows what the diet then
removed. The recipe score therefore mostly measures *engagement* (and its
fade with age), not whether the recipe does what it says. The aggregates
carry ``caveat: 'validity_enforced'`` for a screen to say so
(``caveat_text``).
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any

from . import contract
from .audit import SHOWN
from .auditreport import ENGLISH, Words
from .textutil import parse_created_at

STAGES = ('pantry', 'diet', 'kitchen')
BANDS = ('low', 'medium', 'high', 'very_high')
PERCENTS = (25, 50, 75, 90, 95)
# The recipe score: validity, engagement, relevance.
WEIGHTS = (0.4, 0.35, 0.25)
# Quality / 100 is capped at 1 ("scores above 100 are very good").
QUALITY_CAP = 100.0
CAVEAT = 'validity_enforced'

_PANTRY_IDS = ('hashtags_include', 'hashtags_exclude')
# What the diet side of validity looks at: the diet's six rules, and "Only
# People I Follow" (moshidon has no such rule; it belongs to the diet side).
_DIET_IDS = tuple(contract.DIET_IDS) + ('network_reach',)
# The removal stages that keep a post out of a stage's posts; the kitchen's
# are the posts nothing removed.
_GONE = {'pantry': ('pantry',),
         'diet': ('pantry', 'diet', 'reach', 'viewer'),
         'kitchen': None}


def caveat_text(words: Words = ENGLISH) -> str:
    """What the reader is told about the score (translatable)."""
    _ = words.gettext
    return _('Your filters are always applied, so the score reflects how '
             'much people engaged with the posts shown.')


def band_names(words: Words = ENGLISH) -> dict[str, str]:
    _ = words.gettext
    return {'low': _('Low'), 'medium': _('Medium'), 'high': _('High'),
            'very_high': _('Very High')}


def state_of(score: float) -> str:
    """'excellent' (0.7 and up), 'good' (0.5), 'moderate' (0.3) or 'poor'."""
    if score >= 0.7:
        return 'excellent'
    if score >= 0.5:
        return 'good'
    if score >= 0.3:
        return 'moderate'
    return 'poor'


# -- one post -----------------------------------------------------------

def fresh_mods(snapshot: dict[str, Any]) -> list[float]:
    """The fade-out, in minutes, of each "Favor Fresh Posts" modifier of a
    recipe snapshot (the engine's reading: 360 when none is set; one that
    is not above zero leaves the score be)."""
    found = []
    for mod in (snapshot.get('kitchen') or {}).get('modifiers') or ():
        if isinstance(mod, dict) and mod.get('type') == 'freshness':
            try:
                decay = float(mod.get('decay_minutes') or 360)
            except (TypeError, ValueError):
                continue
            if decay > 0:
                found.append(decay)
    return found


def count_of(value: Any) -> tuple[float, bool]:
    """A reaction count as a number, and whether it was not one: a count
    that is missing is 0 (fine), one that is not a finite, non-negative
    number (text, NaN, infinity, a negative) is 0 and flagged, so no NaN
    reaches a score, a sum or a JSON file."""
    if value is None:
        return 0.0, False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, True
    if not math.isfinite(value) or value < 0:
        return 0.0, True
    return float(value), False


def quality_score(facts: dict[str, Any], decays: list[float],
                  now: datetime | None) -> float:
    """Engagement times freshness, as moshidon's ``_calculate_quality_score``.
    A post dated after the record's clock (a server's clock ahead of ours)
    keeps its engagement unfaded, as the kitchen leaves its freshness be."""
    return _scored(facts, decays, now)[0]


def _scored(facts, decays, now) -> tuple[float, bool]:
    fav, bad_a = count_of(facts.get('fav'))
    reblog, bad_b = count_of(facts.get('reblog'))
    reply, bad_c = count_of(facts.get('reply'))
    score = fav * 1.0 + reblog * 2.5 + reply * 1.5
    flagged = bad_a or bad_b or bad_c
    created = parse_created_at(facts.get('created_c'))
    if created is None or now is None:
        return score, flagged
    age = (now - created).total_seconds() / 60.0
    if age < 0:
        return score, flagged
    for decay in decays:
        score *= max(0.0, 1.0 - age / decay)
    return score, flagged


def assess_entry(entry: dict[str, Any], decays: list[float],
                 now: datetime | None) -> dict[str, Any]:
    """The assessment of one post of a record: ``is_valid``,
    ``validation_score``, ``quality_score``, ``relevance_score`` and the
    ``reasons`` it is not valid, as ids (``pantry:hashtags_include``,
    ``diet:must_have_alt_text``)."""
    rules = entry.get('rules') or {}
    reasons = []
    score = 1.0
    broken = [name for name in _PANTRY_IDS if rules.get(name) == 'fail']
    if broken:
        score *= 0.5
        reasons.extend('pantry:' + name for name in broken)
    broken = [name for name in _DIET_IDS if rules.get(name) == 'fail']
    if broken:
        score = 0.0
        reasons.extend('diet:' + name for name in broken)
    quality, flagged = _scored(entry.get('facts') or {}, decays, now)
    return {'is_valid': not reasons, 'validation_score': score,
            'quality_score': quality, 'reasons': reasons,
            'bad_counts': flagged,
            'relevance_score': score * 0.4 + min(quality / QUALITY_CAP,
                                                 1.0) * 0.6}


# -- aggregates -----------------------------------------------------------

def percentiles(scores: list[float]) -> dict[str, float]:
    """moshidon's percentiles: the sorted score at int(p x n / 100)."""
    if not scores:
        return {}
    ordered = sorted(scores)
    count = len(ordered)
    return {'p%d' % p: ordered[min(int(p / 100.0 * count), count - 1)]
            for p in PERCENTS}


def distribution(scores: list[float]) -> dict[str, int]:
    """How many scores are Low (under 25% of the best), Medium (under 50),
    High (under 75) and Very High."""
    if not scores:
        return {}
    best = max(scores)
    found = dict.fromkeys(BANDS, 0)
    for score in scores:
        share = (score / best) * 100 if best > 0 else 0
        found[BANDS[0 if share < 25 else 1 if share < 50
                    else 2 if share < 75 else 3]] += 1
    return found


def aggregate(assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """The metrics of a list of assessments (moshidon's
    ``calculate_aggregate_metrics``, with the rejection reasons as ids)."""
    if not assessments:
        return {'total_toots': 0, 'valid_toots': 0, 'invalid_toots': 0,
                'validity_rate': 0.0, 'avg_quality_score': 0.0,
                'avg_relevance_score': 0.0, 'rejection_reasons': {},
                'quality_distribution': {}, 'bad_counts': 0,
                'caveat': CAVEAT}
    valid = sum(1 for item in assessments if item['is_valid'])
    reasons: dict[str, int] = {}
    for item in assessments:
        for reason in item['reasons']:
            reasons[reason] = reasons.get(reason, 0) + 1
    quality = [item['quality_score'] for item in assessments]
    relevance = [item['relevance_score'] for item in assessments]
    return {
        'total_toots': len(assessments), 'valid_toots': valid,
        'invalid_toots': len(assessments) - valid,
        'validity_rate': valid / len(assessments) * 100,
        'avg_quality_score': statistics.mean(quality),
        'median_quality_score': statistics.median(quality),
        'avg_relevance_score': statistics.mean(relevance),
        'rejection_reasons': reasons,
        # Posts whose counts were not numbers (read as 0).
        'bad_counts': sum(1 for item in assessments if item['bad_counts']),
        'quality_distribution': distribution(quality),
        'quality_percentiles': percentiles(quality),
        'relevance_percentiles': percentiles(relevance),
        'caveat': CAVEAT}


def recipe_score(metrics: dict[str, Any]) -> float:
    """The score of a recipe, 0 to 1, from a stage's aggregate: validity x
    0.4 + engagement x 0.35 + relevance x 0.25, rounded to 3 places."""
    if not metrics.get('total_toots'):
        return 0.0
    validity = metrics['validity_rate'] / 100.0
    quality = min(metrics['avg_quality_score'] / QUALITY_CAP, 1.0)
    relevance = metrics['avg_relevance_score']
    return round(validity * WEIGHTS[0] + quality * WEIGHTS[1]
                 + relevance * WEIGHTS[2], 3)


# -- from audit records -----------------------------------------------------

def stage_entries(record: dict[str, Any]) -> dict[str, list[dict]]:
    """The posts of each stage of a record: a post is in a stage when no
    earlier stage removed it (a post the kitchen or the guard removed was in
    the diet's, one nothing removed is in the kitchen's, the page)."""
    found: dict[str, list[dict]] = {name: [] for name in STAGES}
    for entry in record.get('posts') or ():
        removal = entry.get('removal')
        stage = removal['stage'] if removal else None
        for name in STAGES:
            gone = _GONE[name]
            if stage is None or (gone is not None and stage not in gone):
                found[name].append(entry)
    return found


def clock_of(record: dict[str, Any]) -> datetime | None:
    """The clock reading of a record as an aware datetime: a reading with
    no zone is read as UTC (the engine writes UTC), one that is not a date
    is None (the posts then keep their engagement unfaded)."""
    try:
        now = datetime.fromisoformat(record['clock']['now_utc'])
    except (KeyError, TypeError, ValueError):
        return None
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now


def _assess_stages(record: dict[str, Any]) -> dict[str, list[dict]]:
    snapshot = (record.get('recipe') or {}).get('snapshot') or {}
    decays = fresh_mods(snapshot)
    now = clock_of(record)
    return {name: [assess_entry(entry, decays, now) for entry in entries]
            for name, entries in stage_entries(record).items()}


def _report(pooled: dict[str, list[dict]], pages: int,
            sizes: dict[str, list[int]]) -> dict[str, Any]:
    report: dict[str, Any] = {name: aggregate(pooled[name])
                              for name in STAGES}
    report['recipe_quality_score'] = recipe_score(report['kitchen'])
    report['state'] = state_of(report['recipe_quality_score'])
    report['pages'] = pages
    # How many posts each stage had on each page: the advice judges
    # moshidon's absolute thresholds and the heat fallback per page.
    report['page_sizes'] = sizes
    report['caveat'] = CAVEAT
    return report


def assess_pages(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The metrics of each stage over the pages of one cook (the records
    that were shown, in any order), pooled, and the recipe score."""
    pooled: dict[str, list[dict]] = {name: [] for name in STAGES}
    sizes: dict[str, list[int]] = {name: [] for name in STAGES}
    pages = 0
    for record in records:
        if (record.get('outcome') or {}).get('status') not in SHOWN:
            continue
        pages += 1
        for name, found in _assess_stages(record).items():
            pooled[name].extend(found)
            sizes[name].append(len(found))
    return _report(pooled, pages, sizes)


def cook_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Numbers and flags only, for one cook (its shown pages): the recipe
    score and its state, how many posts it showed, their mean quality, the
    share of them that is Low, whether every score was zero, and whether the
    page used the freshness fallback or a heat step lower than asked."""
    report = assess_pages(records)
    kitchen = report['kitchen']
    shown = [r for r in records
             if (r.get('outcome') or {}).get('status') in SHOWN]
    total = kitchen['total_toots']
    low = (kitchen['quality_distribution'].get('low', 0) / total * 100
           if total else 0.0)
    return {
        'score': report['recipe_quality_score'], 'state': report['state'],
        'posts': total, 'pages': report['pages'],
        'avg_quality': kitchen['avg_quality_score'], 'low_pct': low,
        'zero_scores': total > 0 and kitchen['avg_quality_score'] == 0,
        'fresh_fallback': any(r.get('fresh_fallback') for r in shown),
        'heat_stepped': any(row.get('used') != row.get('requested')
                            for r in shown for row in r.get('heat') or ()),
    }


def group_cooks(records: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The records split into cooks (by recipe hash and cook id), in the
    order each cook first appears; records of failed or cancelled pages are
    kept out."""
    cooks: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        if (record.get('outcome') or {}).get('status') not in SHOWN:
            continue
        key = ((record.get('recipe') or {}).get('hash', ''),
               str(record.get('cook_id') or ''))
        cooks.setdefault(key, []).append(record)
    return list(cooks.values())


def summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The summary of each cook of *records*, oldest first by the clock the
    records carry (not the order they were given in; a cook whose clock
    cannot be read sorts last, in the order given), a cook with no shown
    page left out."""
    def when(group):
        clocks = [c for c in map(clock_of, group) if c is not None]
        return min(clocks).timestamp() if clocks else float('inf')
    return [found for found in (cook_summary(group) for group in sorted(
        group_cooks(records), key=when)) if found['pages']]
