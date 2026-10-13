# SPDX-License-Identifier: GPL-3.0-or-later

"""Catch Up relevance model: a Python port of Elk's For You ranker.

This module is the score behind the Highlights grid (CATCH-UP.md section
4). It ports, function for function, the heavy ranker and the diversity
reordering of the Elk fork at ``mauriciobc/elk@9597db5``:

* ``app/composables/for-you/ranking.ts``  -- features, action heads,
  weighted sum, slate adjustments;
* ``app/composables/for-you/diversity.ts`` -- conversation dedupe and the
  hashed-feature DPP reordering;
* ``app/composables/for-you/types.ts``     -- the candidate shape.

Elk's code is in turn a transcription of xai-org/x-algorithm
``home-mixer``. Every public function names its TypeScript original in a
comment. Elk's calibration values (saturation points, federation
coverage, content priors, weights, base rates) are kept exactly.

Deliberate differences from the TypeScript (each is also noted where it
applies):

* The new-author boost (``applyNewAuthorBoost``, ``impressionProxy`` and
  the ``coldStart*`` parameters) is not ported. It draws a random slot, and
  the Catch Up grid is a fixed digest. Nothing in this module is random.
* The 48 h ``AgeFilter`` cut in ``recencyMultiplier`` is a parameter,
  ``RankingParams.max_post_age_ms``, and is off (``None``) for Catch Up.
  ``ELK_MAX_POST_AGE_MS`` restores Elk's behaviour.
* ``now`` is always passed in (the snapshot's ``end_at``); the clock is
  never read.
* Affinity comes from ``RankingContext.affinity``, an ``AffinityResolver``
  that returns 0 for everything by default (phase 1). The ``signals``
  argument of the TypeScript functions exists only to feed Elk's default
  resolver, so it is gone; ``signals_affinity_resolver`` ports that
  default for phase 2.
* Statuses are Mastodon JSON dicts (snake_case keys), not ``masto``
  objects. ``in_network`` is a field of the candidate. Reason labels and
  action names are snake_case (``open_link``, ``author_diversity``).
* ``Intl.Segmenter`` does not exist here, so Han, Kana, Thai, Khmer, Lao
  and Myanmar runs always take the character-bigram path that the
  TypeScript uses when the segmenter is missing. Unicode scripts and
  ``Extended_Pictographic`` come from tables generated from ICU (Unicode
  16.0); general categories come from ``unicodedata``.
* ``htmlToText`` (``content-parse.ts``) is re-implemented with
  ``html.parser``.

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

import dataclasses
import datetime
import functools
import math
import re
import struct
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Any, NamedTuple

EPSILON = 1e-9

# region JavaScript parity helpers

# What JavaScript's `\s` and `String.prototype.trim` treat as whitespace.
# Python's `str.strip()` and `\s` differ (they add U+001C-U+001F and U+0085
# and lack U+FEFF), and these sets decide text lengths and URL spans.
_JS_WHITESPACE = ''.join(chr(c) for c in (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF))
_WS_CLASS = re.escape(_JS_WHITESPACE)


def _js_length(text):
    """``text.length`` in JavaScript: UTF-16 code units, not code points."""
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def _utf16_key(text):
    """Sort key matching JavaScript's ``<`` on strings (UTF-16 code units)."""
    return text.encode('utf-16-be', 'surrogatepass')


def _js_slice(text, start, end=None):
    """``text.slice(start, end)`` over UTF-16 code units."""
    raw = text.encode('utf-16-le', 'surrogatepass')
    units = len(raw) // 2
    if end is None:
        end = units
    if start < 0:
        start = max(0, units + start)
    if end < 0:
        end = max(0, units + end)
    return raw[2 * start:2 * min(end, units)].decode(
        'utf-16-le', 'surrogatepass')


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_DATETIME = datetime.datetime


def parse_timestamp_ms(value):
    """``Date.parse`` for the ISO-8601 stamps Mastodon sends.

    Returns epoch milliseconds, or ``None`` where JavaScript yields ``NaN``.
    A stamp without an offset is read as UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    delta = moment - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000
            + delta.microseconds // 1000)


def _to_ms(moment):
    """Epoch milliseconds from the ``now`` a caller passes in."""
    if isinstance(moment, _DATETIME):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        delta = moment - _EPOCH
        return ((delta.days * 86400 + delta.seconds) * 1000
                + delta.microseconds // 1000)
    ms = parse_timestamp_ms(moment)
    if ms is None:
        raise ValueError('now must be a datetime or an ISO-8601 string, '
                         'got %r' % (moment,))
    return ms


def _count(mapping, key):
    """``mapping.key ?? 0`` for a numeric field."""
    value = mapping.get(key)
    return 0 if value is None else value


# endregion

# region weights

# Order matters: it is the order of the TypeScript object literals, and the
# weighted sum adds the terms in this order.
ACTIONS = (
    'favorite', 'reply', 'retweet', 'quote', 'share', 'click', 'open_link',
    'profile_click', 'photo_expand', 'video_open', 'vqv', 'dwell',
    'follow_author', 'not_interested', 'mute_author', 'block_author',
    'report', 'not_dwelled',
)

# X_WEIGHTS in ranking.ts: X's production weights from
# home-mixer/params/param.rs, over the 18 heads that are modelled.
X_WEIGHTS = MappingProxyType({
    'favorite': 0.5,
    'reply': 5.0,
    'retweet': 1.0,
    'quote': 5.0,
    'share': 2.0,
    'click': 0.4,
    'open_link': 0.2,
    'profile_click': 0.0,
    'photo_expand': 0.05,
    'video_open': 0.05,
    'vqv': 0.05,
    'dwell': 0.0,
    'follow_author': 4.0,
    'not_interested': -43.2,
    'mute_author': -58.8,
    'block_author': -31.2,
    'report': -234.0,
    'not_dwelled': -0.02,
})

# MASTODON_WEIGHTS in ranking.ts: what the ranker resolves. Two judgment
# calls over X: quote 5.0 -> 1.0 and share 2.0 -> 0.5.
MASTODON_WEIGHTS = MappingProxyType({
    **X_WEIGHTS,
    'quote': 1.0,
    'share': 0.5,
})

# X_WEIGHT_SUMS in ranking.ts: X's own sums over its full head table.
X_WEIGHT_SUMS = MappingProxyType({
    'positive_sum': 43.32, 'negative_sum': 367.22, 'total_sum': 410.54})

# BIDIRECTIONAL_FOLLOW_REPLY_WEIGHT_BOOST in ranking.ts.
BIDIRECTIONAL_FOLLOW_REPLY_WEIGHT_BOOST = 15.0
# BIDIRECTIONAL_FOLLOW_DWELL_WEIGHT_BOOST in ranking.ts (0.0 in production).
BIDIRECTIONAL_FOLLOW_DWELL_WEIGHT_BOOST = 0.0
# NEGATIVE_SCORES_OFFSET in ranking.ts (config.rs).
NEGATIVE_SCORES_OFFSET = 0.001
# MAX_POST_AGE_MS in ranking.ts: Elk's 48 h AgeFilter cap. Not used by
# default; pass it as ``RankingParams.max_post_age_ms`` to get Elk's cut.
ELK_MAX_POST_AGE_MS = 48 * 60 * 60 * 1000
# Catch Up's value for the same parameter (CATCH-UP.md 4.5): no hard cut.
CATCHUP_MAX_POST_AGE = None
# MIN_VIDEO_DURATION_MS in ranking.ts.
MIN_VIDEO_DURATION_MS = 10_000
# MAX_FOLLOWERS_THRESHOLD in ranking.ts.
MAX_FOLLOWERS_THRESHOLD = 10_000


# RankingParams / DEFAULT_RANKING_PARAMS in ranking.ts. The new-author
# boost parameters (enableNewAuthorBoost, coldStart*) are not ported.
@dataclass(frozen=True)
class RankingParams:
    # EnableAuthorDiversity
    enable_author_diversity: bool = True
    # AuthorDiversityDecay
    author_diversity_decay: float = 0.5
    # AuthorDiversityFloor
    author_diversity_floor: float = 0.25
    # OonWeightFactor: the only place the follow graph enters the score.
    oon_weight_factor: float = 0.75
    # EnableOonRescoreForInNetworkRepliesRetweets
    oon_rescore_in_network_replies_reblogs: bool = True
    # NEW_USER_OON_WEIGHT_FACTOR
    new_user_oon_weight_factor: float = 0.00001
    # NEW_USER_MIN_FOLLOWING
    new_user_min_following: int = 5
    # NewUserAgeThresholdSecs, in ms; 0 switches the new-user rule off.
    new_user_age_threshold_ms: float = 0
    # Freshness: half-life and floor (Elk-specific).
    recency_half_life_ms: float = 12 * 60 * 60 * 1000
    recency_floor: float = 0.12
    # Multiplier for a post in a language the viewer neither reads nor
    # engages with.
    language_mismatch_prior: float = 0.6
    # logNorm saturation points, measured on the fediverse (CALIBRATION.md).
    engagement_saturation: float = 10_000
    velocity_saturation: float = 500
    reply_saturation: float = 100
    follower_saturation: float = 50_000
    author_volume_saturation: float = 50_000
    # How much of a remote post's true counts this instance sees.
    favourite_coverage_remote: float = 0.6
    reblog_coverage_remote: float = 0.93
    reply_coverage_remote: float = 1
    # Measured content priors.
    bot_engagement_prior: float = 0.4
    bot_follow_prior: float = 0.4
    link_engagement_prior: float = 0.7
    media_engagement_prior: float = 1.25
    hashtag_engagement_prior: float = 1.3
    # Added to the reply weight for an original post by a mutual.
    bidirectional_follow_reply_weight_boost: float = (
        BIDIRECTIONAL_FOLLOW_REPLY_WEIGHT_BOOST)
    # The AgeFilter cap: past this age freshness is 0. None switches the
    # cut off (Catch Up). Elk uses ELK_MAX_POST_AGE_MS.
    max_post_age_ms: float | None = CATCHUP_MAX_POST_AGE


DEFAULT_RANKING_PARAMS = RankingParams()

# BASE_RATES in ranking.ts: roughly P(action) for a post that was shown.
BASE_RATES = MappingProxyType({
    'favorite': 0.03,
    'reply': 0.0035,
    'retweet': 0.018,
    'quote': 0.0006,
    'share': 0.001,
    'click': 0.03,
    'open_link': 0.012,
    'profile_click': 0.005,
    'photo_expand': 0.02,
    'video_open': 0.02,
    'vqv': 0.02,
    'dwell': 0.5,
    'follow_author': 0.0006,
    'not_interested': 0.00012,
    'mute_author': 0.00002,
    'block_author': 0.000015,
    'report': 0.000003,
    'not_dwelled': 0.22,
})

# endregion

# region affinity and context


def _zero(_key):
    return 0


# AffinityResolver in ranking.ts. Every channel is signed, in [-1, 1],
# and 0 means "no information" (``lift(0, ...) == 1``). authorPenalty and
# postPenalty are 0..1. The default returns 0 everywhere (CATCH-UP.md 4.8,
# phase 1); phase 2 plugs the server-derived affinity in here.
@dataclass(frozen=True)
class AffinityResolver:
    author: Callable[[str], float] = _zero
    tag: Callable[[str], float] = _zero
    language: Callable[[str], float] = _zero
    # Whose boosts the viewer engages with; 0 for a post that is no boost.
    booster: Callable[[str], float] = _zero
    # Pushback against an author as a whole (a mute), 0..1.
    author_penalty: Callable[[str], float] = _zero
    # Pushback against one post (a "not interested"), 0..1.
    post_penalty: Callable[[str], float] = _zero


NULL_AFFINITY = AffinityResolver()


# ForYouSignals in types.ts, trimmed to what the default resolver reads.
@dataclass
class ForYouSignals:
    author_affinity: dict = field(default_factory=dict)
    tag_affinity: dict = field(default_factory=dict)
    language_affinity: dict = field(default_factory=dict)
    # Not in the TypeScript contract type; Elk's store carries it.
    booster_affinity: dict = field(default_factory=dict)
    not_interested: list = field(default_factory=list)
    muted_for_you: list = field(default_factory=list)


def _clamp(value, low, high):
    """``clamp`` in ranking.ts; NaN becomes ``low``."""
    if value != value:
        return low
    return low if value < low else high if value > high else value


def _clamp01(value):
    return _clamp(value, 0, 1)


# logNorm in ranking.ts: heavy-tail normalizer onto 0..1.
def log_norm(count, saturation):
    if not count > 0 or not saturation > 0:
        return 0.0
    return _clamp01(math.log1p(count) / math.log1p(saturation))


# lift in ranking.ts: a signed signal in [-1, 1] as a multiplicative factor.
# ``lift(0, ...) == 1``, so a missing signal is a no-op.
def lift(signal, maximum, minimum=1):
    s = _clamp(signal, -1, 1)
    return 1 + (maximum - 1) * s if s >= 0 else 1 + (1 - minimum) * s


# affinitySignal in ranking.ts: signed mid-rank empirical CDF of ``key``'s
# value among the entries of the same sign. Recomputed on every call.
def affinity_signal(values, key):
    if not values or not key:
        return 0
    value = values.get(key)
    if not value or not math.isfinite(value):
        return 0
    positive = value > 0
    below = 0
    equal = 0
    total = 0
    for other in values.values():
        if (not other or not math.isfinite(other)
                or (other > 0) != positive):
            continue
        total += 1
        a = abs(other)
        b = abs(value)
        if a < b:
            below += 1
        elif a == b:
            equal += 1
    if total == 0:
        return 0
    percentile = (below + equal / 2) / total
    return percentile if positive else -percentile


# defaultAffinityResolver in ranking.ts: reads affinity off Elk's scalar
# maps. Not used by default here; phase 2 builds on it.
def signals_affinity_resolver(signals):
    muted = signals.muted_for_you
    dismissed = signals.not_interested
    return AffinityResolver(
        author=lambda i: affinity_signal(signals.author_affinity, i),
        tag=lambda t: affinity_signal(signals.tag_affinity, t),
        language=lambda c: affinity_signal(signals.language_affinity, c),
        booster=lambda i: affinity_signal(signals.booster_affinity, i),
        author_penalty=lambda i: 1 if i and i in muted else 0,
        post_penalty=lambda i: 1 if i and i in dismissed else 0,
    )


# RankingContext in ranking.ts. ``now`` is the snapshot's end time (a
# datetime, or an ISO-8601 string); the clock is never read. ``params``
# may be a RankingParams or a dict of overrides, ``weights`` and
# ``base_rates`` are dicts of overrides, merged as in resolveParams,
# resolveWeights and resolveBaseRates. Treat a context as immutable.
@dataclass(frozen=True)
class RankingContext:
    now: Any
    viewer_id: str | None = None
    viewer_languages: Sequence[str] | None = None
    viewer_following_count: int | None = None
    viewer_follower_count: int | None = None
    viewer_account_age_ms: float | None = None
    # Ids of authors that follow the viewer back (``followed_by``).
    mutual_author_ids: Any = None
    affinity: AffinityResolver = NULL_AFFINITY
    weights: Mapping | None = None
    params: Any = None
    base_rates: Mapping | None = None
    now_ms: int = field(init=False, repr=False)
    resolved_params: RankingParams = field(init=False, repr=False)
    resolved_weights: Mapping = field(init=False, repr=False)
    resolved_base_rates: Mapping = field(init=False, repr=False)

    def __post_init__(self):
        set_ = object.__setattr__
        set_(self, 'now_ms', _to_ms(self.now))
        params = self.params
        if params is None:
            params = DEFAULT_RANKING_PARAMS
        elif not isinstance(params, RankingParams):
            params = dataclasses.replace(DEFAULT_RANKING_PARAMS, **params)
        set_(self, 'resolved_params', params)
        set_(self, 'resolved_weights',
             {**MASTODON_WEIGHTS, **self.weights} if self.weights
             else MASTODON_WEIGHTS)
        set_(self, 'resolved_base_rates',
             {**BASE_RATES, **self.base_rates} if self.base_rates
             else BASE_RATES)


class ScoreReason(NamedTuple):
    label: str
    value: float


# PostCandidate in types.ts. ``status`` is a Mastodon Status dict.
# ``in_network`` is True when the author is followed by the viewer; Catch
# Up sets it False only for a post that reached the snapshot through a
# followed tag. The scorer fills the rest; the pipeline never mutates a
# candidate it was given.
@dataclass(eq=False)
class Candidate:
    status: dict
    in_network: bool = True
    probabilities: dict | None = None
    raw_score: float | None = None
    score: float | None = None
    reasons: list | None = None


def candidate_from_status(status, in_network=None):
    """Wrap a Status dict. ``in_network`` defaults to "not only found
    through a followed tag" (the ``_followed_tags`` marker, spec 4.2)."""
    if in_network is None:
        in_network = not status.get('_followed_tags')
    return Candidate(status=status, in_network=bool(in_network))


# endregion

# region features


# contentStatus in ranking.ts: for a boost, the boosted post.
def content_status(candidate):
    reblog = candidate.status.get('reblog')
    return candidate.status if reblog is None else reblog


# contentAgeMs in ranking.ts, clamped at 0 for clock skew.
def _content_age_ms(candidate, now_ms):
    created = content_status(candidate).get('created_at')
    if created is None:
        created = candidate.status.get('created_at')
    created_ms = parse_timestamp_ms('' if created is None else created)
    return 0 if created_ms is None else max(0, now_ms - created_ms)


# videoDurationMs in ranking.ts: first video/gifv attachment, in ms.
def _video_duration_ms(status):
    for media in status.get('media_attachments') or ():
        if media.get('type') in ('video', 'gifv'):
            original = (media.get('meta') or {}).get('original')
            if not original:
                return None
            duration = original.get('duration')
            if (isinstance(duration, bool)
                    or not isinstance(duration, (int, float))):
                return None
            return duration * 1000
    return None


_HTML_TAG_RE = re.compile(r'<[^>]*>')
_WHITESPACE_RE = re.compile('[%s]+' % _WS_CLASS)


# plainTextLength in ranking.ts. Entities are not decoded, as in Elk.
def _plain_text_length(status):
    html = status.get('content') or ''
    text = _HTML_TAG_RE.sub(' ', html)
    text = _WHITESPACE_RE.sub(' ', text).strip(_JS_WHITESPACE)
    return _js_length(text)


# coverageCorrected in ranking.ts: a non-positive coverage means "no
# measurement", so the raw count stands.
def _coverage_corrected(count, coverage):
    return count / coverage if coverage > 0 else count


# MIN_VELOCITY_AGE_HOURS in ranking.ts.
MIN_VELOCITY_AGE_HOURS = 0.25


# recencyMultiplier in ranking.ts: freshness in (floor, 1]. Elk returns 0
# past 48 h; here the cut is ``params.max_post_age_ms`` (off by default).
def recency_multiplier(age_ms, params=DEFAULT_RANKING_PARAMS):
    age = max(0, age_ms)
    if params.max_post_age_ms is not None and age > params.max_post_age_ms:
        return 0.0
    half_lives = age / params.recency_half_life_ms
    return ((1 - params.recency_floor) * 0.5 ** half_lives
            + params.recency_floor)


# RankingFeatures in ranking.ts: everything the estimators read.
@dataclass
class RankingFeatures:
    age_ms: float
    age_hours: float
    freshness: float
    favourites: float
    reblogs: float
    replies: float
    total_engagement: float
    engagement_estimate: float
    popularity: float
    velocity: float
    engagement_signal: float
    reply_density: float
    reblog_density: float
    author_affinity: float
    tag_affinity: float
    tag_count: int
    language_affinity: float
    booster_affinity: float
    author_penalty: float
    post_penalty: float
    language_prior: float
    language_mismatch: bool
    author_reach: float
    spamminess: float
    author_volume: float
    verified: bool
    bot: bool
    has_image: bool
    image_count: int
    has_video: bool
    video_duration_ms: float | None
    has_link: bool
    has_card_image: bool
    has_poll: bool
    has_spoiler: bool
    sensitive: bool
    is_reply: bool
    is_self_reply: bool
    is_boost: bool
    has_question: bool
    text_length: int
    in_network: bool


# extractRankingFeatures in ranking.ts.
def extract_ranking_features(candidate, ctx):
    params = ctx.resolved_params
    affinity = ctx.affinity
    outer = candidate.status
    status = content_status(candidate)
    account = status.get('account') or {}

    age_ms = _content_age_ms(candidate, ctx.now_ms)
    age_hours = age_ms / 3_600_000
    freshness = recency_multiplier(age_ms, params)

    favourites = _count(status, 'favourites_count')
    reblogs = _count(status, 'reblogs_count')
    replies = _count(status, 'replies_count')
    total_engagement = favourites + reblogs + replies

    # Remote counts are divided by how much of each count federates
    # (0.60 / 0.93 / 1.00, measured); local counts are authoritative.
    # total_engagement stays the raw observed sum.
    remote = '@' in (account.get('acct') or '')
    corrected_favourites = (
        _coverage_corrected(favourites, params.favourite_coverage_remote)
        if remote else favourites)
    corrected_reblogs = (
        _coverage_corrected(reblogs, params.reblog_coverage_remote)
        if remote else reblogs)
    corrected_replies = (
        _coverage_corrected(replies, params.reply_coverage_remote)
        if remote else replies)
    engagement_estimate = (
        corrected_favourites + corrected_reblogs + corrected_replies)

    popularity = log_norm(engagement_estimate, params.engagement_saturation)
    velocity = log_norm(
        engagement_estimate / max(MIN_VELOCITY_AGE_HOURS,
                                  min(age_hours, 48)),
        params.velocity_saturation)
    # Half absolute reach, half rate.
    engagement_signal = 0.5 * popularity + 0.5 * velocity
    # Densities are shares of the corrected composite.
    reply_density = (corrected_replies / engagement_estimate
                     if engagement_estimate > 0 else 0)
    reblog_density = (corrected_reblogs / engagement_estimate
                      if engagement_estimate > 0 else 0)

    account_id = account.get('id')
    author_affinity = affinity.author(account_id) if account_id else 0
    author_penalty = _clamp01(affinity.author_penalty(account_id or ''))
    post_penalty = _clamp01(affinity.post_penalty(outer.get('id') or ''))
    booster_affinity = 0
    if outer.get('reblog') is not None:
        booster_affinity = affinity.booster(
            (outer.get('account') or {}).get('id') or '')
    tag_affinity = 0
    for tag in status.get('tags') or ():
        name = (tag.get('name') or '').lower()
        if not name:
            continue
        value = affinity.tag(name)
        if abs(value) > abs(tag_affinity):
            tag_affinity = value

    language = status.get('language') or None
    language_affinity = affinity.language(language) if language else 0
    viewer_languages = ctx.viewer_languages
    known = (not language or not viewer_languages
             or language in viewer_languages)
    language_mismatch = not known and language_affinity <= 0
    # Applied once, as a mild multiplier: the language field is whatever
    # the posting client sent.
    language_prior = (
        params.language_mismatch_prior if language_mismatch
        else lift(language_affinity, 1.15, params.language_mismatch_prior))

    followers = _count(account, 'followers_count')
    following = _count(account, 'following_count')
    author_reach = log_norm(followers, params.follower_saturation)
    # Classic follow-spam shape: follows many, followed by few.
    follow_ratio = followers / following if following > 20 else 1
    spamminess = _clamp01(
        (0.6 if follow_ratio < 0.1 else 0.3 if follow_ratio < 0.5 else 0)
        + (0 if account.get('note') else 0.15)
        + (0.1 if account.get('statuses_count') == 0 else 0))
    author_volume = log_norm(_count(account, 'statuses_count'),
                             params.author_volume_saturation)
    verified = any(field_.get('verified_at')
                   for field_ in account.get('fields') or ())

    media = status.get('media_attachments') or []
    images = [m for m in media if m.get('type') == 'image']
    video = next((m for m in media if m.get('type') in ('video', 'gifv')),
                 None)
    card = status.get('card')
    tags = status.get('tags') or []

    return RankingFeatures(
        age_ms=age_ms,
        age_hours=age_hours,
        freshness=freshness,
        favourites=favourites,
        reblogs=reblogs,
        replies=replies,
        total_engagement=total_engagement,
        engagement_estimate=engagement_estimate,
        popularity=popularity,
        velocity=velocity,
        engagement_signal=engagement_signal,
        reply_density=reply_density,
        reblog_density=reblog_density,
        author_affinity=author_affinity,
        tag_affinity=tag_affinity,
        tag_count=len(tags),
        language_affinity=language_affinity,
        booster_affinity=booster_affinity,
        author_penalty=author_penalty,
        post_penalty=post_penalty,
        language_prior=language_prior,
        language_mismatch=language_mismatch,
        author_reach=author_reach,
        spamminess=spamminess,
        author_volume=author_volume,
        verified=verified,
        bot=bool(account.get('bot')),
        has_image=len(images) > 0,
        image_count=len(images),
        has_video=video is not None,
        video_duration_ms=_video_duration_ms(status),
        has_link=card is not None,
        has_card_image=bool((card or {}).get('image')),
        has_poll=status.get('poll') is not None,
        has_spoiler=bool(status.get('spoiler_text')),
        sensitive=bool(status.get('sensitive')),
        is_reply=bool(status.get('in_reply_to_id')),
        is_self_reply=(bool(status.get('in_reply_to_account_id'))
                       and status.get('in_reply_to_account_id')
                       == account.get('id')),
        is_boost=outer.get('reblog') is not None,
        has_question='?' in (status.get('content') or ''),
        text_length=_plain_text_length(status),
        in_network=candidate.in_network,
    )


# endregion

# region the model


# predictActions in ranking.ts: P(action) for every head, as a dict in the
# order of ACTIONS.
def predict_actions(candidate, ctx):
    return _predict_actions_from(
        candidate, extract_ranking_features(candidate, ctx), ctx)


# predictActionsFrom in ranking.ts, head by head. Every head is a base rate
# times independent multiplicative lifts; none of them reads the follow
# graph (except follow_author), freshness or language -- those are applied
# once, by the pipeline.
def _predict_actions_from(candidate, f, ctx):
    params = ctx.resolved_params
    B = ctx.resolved_base_rates

    # Measured content priors. They fade out as observed engagement grows:
    # the counts already embody them.
    prior_strength = 1 - f.popularity

    def shrink(prior):
        return 1 + (prior - 1) * prior_strength

    bot_prior = shrink(params.bot_engagement_prior if f.bot else 1)
    link_prior = shrink(params.link_engagement_prior if f.has_link else 1)
    media_prior = shrink(params.media_engagement_prior
                         if f.has_image or f.has_video else 1)
    hashtag_prior = shrink(params.hashtag_engagement_prior
                           if f.tag_count > 0 else 1)

    # favorite
    favorite = _clamp01(
        B['favorite']
        * lift(f.engagement_signal, 8)
        * lift(f.author_affinity, 3, 0.4)
        * lift(f.tag_affinity, 1.6, 0.7)
        # Whose boosts the viewer engages with: a curator is close to an
        # author.
        * lift(f.booster_affinity, 1.8, 0.7)
        * (0.9 if f.is_boost else 1)
        * bot_prior
        * link_prior
        * media_prior
        * hashtag_prior)

    # reply: weight 5.0, so tied to engagement as well as reply density,
    # lest a post nobody engaged with collect 5.0 x base for free.
    reply = _clamp01(
        B['reply']
        * lift(f.engagement_signal, 3)
        * lift(f.reply_density, 2.5)
        * lift(f.author_affinity, 5, 0.3)
        * lift(f.tag_affinity, 1.4, 0.8)
        * (1.4 if f.has_question else 1)
        * (1.3 if f.has_poll else 1)
        * (0.5 if f.is_boost else 1)
        * bot_prior)

    # retweet (boost)
    retweet = _clamp01(
        B['retweet']
        * lift(f.engagement_signal, 6)
        * lift(f.reblog_density, 2.5)
        * lift(f.author_affinity, 2.5, 0.5)
        * lift(f.booster_affinity, 1.6, 0.8)
        * (0.5 if f.is_boost else 1)
        * (0.6 if f.is_reply else 1)
        * bot_prior)

    # quote: a small fraction of the boost propensity, lifted by how
    # discussion-heavy the post is.
    quote_given_retweet = 0.12
    quote = _clamp01(
        retweet * quote_given_retweet * lift(f.reply_density, 2))

    # share: the weakest positive head; links are discounted, not boosted.
    share = _clamp01(
        B['share']
        * lift(f.popularity, 4)
        * link_prior
        * (1.2 if f.has_image or f.has_video else 1))

    # click (open the post): a thread, a content warning, long text.
    click = _clamp01(
        B['click']
        * lift(log_norm(f.replies, params.reply_saturation), 3)
        * (1.8 if f.has_spoiler else 1)
        * lift(_clamp01((f.text_length - 280) / 700), 1.5)
        * lift(f.author_affinity, 2, 0.6))

    # open_link: strictly conditional on a preview card.
    open_link = (
        _clamp01(
            B['open_link']
            * lift(f.popularity, 3)
            * (1.3 if f.has_card_image else 1)
            * lift(f.tag_affinity, 1.5, 0.7))
        if f.has_link else 0)

    # profile_click: inert today (weight 0.0).
    profile_click = _clamp01(
        B['profile_click']
        * lift(f.popularity, 2)
        * lift(f.author_affinity, 1.5, 0.8))

    # photo_expand: conditional on an image.
    photo_expand = (
        _clamp01(
            B['photo_expand']
            * (1.3 if f.image_count > 1 else 1)
            * (1.4 if f.has_spoiler or f.sensitive else 1)
            * lift(f.author_affinity, 1.5, 0.8))
        if f.has_image else 0)

    # video_open
    video_open = (
        _clamp01(B['video_open']
                 * lift(f.popularity, 2)
                 * lift(f.author_affinity, 1.5, 0.8))
        if f.has_video else 0)

    # vqv (video quality view): the weight is gated in effective_weights.
    vqv_given_open = 0.45
    vqv = _clamp01(video_open * vqv_given_open)

    # dwell: weight 0.0 in production; a content-shape prior.
    dwell = _clamp01(
        B['dwell']
        * lift(_clamp01(f.text_length / 500), 1.4)
        * (1.15 if f.has_image or f.has_video else 1)
        * lift(f.author_affinity, 1.3, 0.85))

    # follow_author: out of network only. You cannot follow an account you
    # already follow; the one place in_network enters an estimator.
    follow_author = (
        0 if f.in_network else _clamp01(
            B['follow_author']
            * lift(f.engagement_signal, 6)
            * lift(f.author_affinity, 4, 0.3)
            * lift(f.tag_affinity, 2, 0.6)
            * lift(f.author_reach, 1.5)
            * (1.5 if f.verified else 1)
            # Not bot_prior: that one fades with popularity, this must not.
            * (params.bot_follow_prior if f.bot else 1)
            * (0.7 if f.is_reply else 1)))

    # not_interested: spam shape and the viewer's own dismissals. max, not
    # a product: the penalty is already the posterior.
    not_interested = _clamp01(max(
        B['not_interested']
        * lift(-f.author_affinity, 4, 0.25)
        # No bot term: bot-ness lives on the positive heads only.
        * lift(f.spamminess, 2.5)
        * (1.2 if f.sensitive else 1),
        max(f.post_penalty, f.author_penalty)))

    # mute_author: volume. Floored by author_penalty only, never by
    # post_penalty: a dismissed post is no verdict on the whole author.
    mute_author = _clamp01(max(
        B['mute_author']
        * lift(-f.author_affinity, 5, 0.2)
        * lift(f.author_volume, 2)
        * lift(f.spamminess, 2),
        f.author_penalty))

    # block_author: spam shape and unflagged sensitive content.
    block_author = _clamp01(
        B['block_author']
        * lift(-f.author_affinity, 5, 0.2)
        * lift(f.spamminess, 3)
        * (2 if f.sensitive and not f.has_spoiler else 1))

    # report: the base rate is ~1000x lower than a like.
    report = _clamp01(
        B['report']
        * lift(-f.author_affinity, 3, 0.3)
        * lift(f.spamminess, 4)
        * (3 if f.sensitive and not f.has_spoiler else 1))

    # not_dwelled: the one head that rises with age. Capped at 0.95.
    not_dwelled = _clamp(
        B['not_dwelled']
        * (1 + f.age_hours / 24)
        * lift(-f.author_affinity, 1.6, 0.6)
        * (0.8 if f.has_image or f.has_video else 1)
        * (1.2 if f.text_length < 40 else 1),
        0, 0.95)

    return {
        'favorite': favorite,
        'reply': reply,
        'retweet': retweet,
        'quote': quote,
        'share': share,
        'click': click,
        'open_link': open_link,
        'profile_click': profile_click,
        'photo_expand': photo_expand,
        'video_open': video_open,
        'vqv': vqv,
        'dwell': dwell,
        'follow_author': follow_author,
        'not_interested': not_interested,
        'mute_author': mute_author,
        'block_author': block_author,
        'report': report,
        'not_dwelled': not_dwelled,
    }


# endregion

# region the weighted sum


# effectiveWeights in ranking.ts: per-candidate weights. reply gets the
# bidirectional-follow boost on original posts by mutuals; vqv is zeroed
# unless the video is longer than 10 s and the viewer has fewer than
# MAX_FOLLOWERS_THRESHOLD followers. Used for the numerator only.
def effective_weights(candidate, ctx, features=None):
    base = ctx.resolved_weights
    params = ctx.resolved_params
    status = content_status(candidate)
    outer = candidate.status
    mutuals = ctx.mutual_author_ids

    bidirectional_eligible = (
        not status.get('in_reply_to_id')
        and outer.get('reblog') is None
        and bool(mutuals)
        and (status.get('account') or {}).get('id') in mutuals)

    duration_ms = (features.video_duration_ms if features is not None
                   else _video_duration_ms(status))
    follower_count = ctx.viewer_follower_count
    vqv_eligible = (
        duration_ms is not None
        and duration_ms > MIN_VIDEO_DURATION_MS
        and (0 if follower_count is None else follower_count)
        < MAX_FOLLOWERS_THRESHOLD)

    if not bidirectional_eligible and vqv_eligible:
        return base

    return {
        **base,
        'reply': (base['reply'] + params.bidirectional_follow_reply_weight_boost
                  if bidirectional_eligible else base['reply']),
        'dwell': (base['dwell'] + BIDIRECTIONAL_FOLLOW_DWELL_WEIGHT_BOOST
                  if bidirectional_eligible else base['dwell']),
        'vqv': base['vqv'] if vqv_eligible else 0,
    }


# WeightSums in ranking.ts.
class WeightSums(NamedTuple):
    positive_sum: float
    negative_sum: float
    total_sum: float


# weightSums in ranking.ts: computed once per request from the base
# weights, as ScoringWeights::from_params does.
def weight_sums(weights):
    positive_sum = 0
    negative_sum = 0
    for w in weights.values():
        if w >= 0:
            positive_sum += w
        else:
            negative_sum -= w
    return WeightSums(positive_sum, negative_sum,
                      positive_sum + negative_sum)


# offsetScore in ranking.ts (RankingScorer::offset_score).
def offset_score(combined, sums):
    if sums.total_sum == 0:
        return max(combined, 0)
    if combined < 0:
        return ((combined + sums.negative_sum) / sums.total_sum
                * NEGATIVE_SCORES_OFFSET)
    return combined + NEGATIVE_SCORES_OFFSET


# contextMultiplierOf in ranking.ts: freshness x language, applied once.
def _context_multiplier_of(f):
    return f.freshness * f.language_prior


# contextMultiplier in ranking.ts.
def context_multiplier(candidate, ctx):
    return _context_multiplier_of(extract_ranking_features(candidate, ctx))


# REASON_EPSILON in ranking.ts: terms below this stay out of the trace.
REASON_EPSILON = 1e-6


# scoreCandidate in ranking.ts: sum(weight x P(action)) split into a
# positive and a negative bucket, scaled by freshness x language when
# non-negative, then offset. Returns a new candidate with probabilities,
# raw_score and reasons; the input is not mutated.
def score_candidate(candidate, ctx):
    features = extract_ranking_features(candidate, ctx)
    probabilities = _predict_actions_from(candidate, features, ctx)
    weights = effective_weights(candidate, ctx, features)
    # Denominator from the base weights, once per request, as X does.
    sums = weight_sums(ctx.resolved_weights)

    pos = 0
    neg = 0
    reasons = []
    for key, probability in probabilities.items():
        term = probability * weights[key]
        if term >= 0:
            pos += term
        else:
            neg -= term
        if abs(term) >= REASON_EPSILON:
            reasons.append(ScoreReason(key, term))

    reasons.sort(key=lambda reason: -abs(reason.value))

    context = _context_multiplier_of(features)
    net = pos - neg
    scaled = context * net if net >= 0 else net

    reasons.append(ScoreReason('freshness', features.freshness))
    if features.language_prior != 1:
        reasons.append(ScoreReason('language', features.language_prior))

    return dataclasses.replace(
        candidate,
        probabilities=probabilities,
        raw_score=offset_score(scaled, sums),
        reasons=reasons)


# endregion

# region post-scoring adjustments


# diversityMultiplier in ranking.ts (RankingScorer::diversity_multiplier):
# (1 - floor) * decay^k + floor, k = higher-scoring posts by the author.
def diversity_multiplier(k, decay, floor):
    return (1 - floor) * decay ** k + floor


# diversityKey in ranking.ts: the author of the content, not of the boost.
def _diversity_key(candidate):
    return (content_status(candidate).get('account') or {}).get('id') or ''


# effectiveOonFactor in ranking.ts (RankingScorer::effective_oon_weight).
def effective_oon_factor(ctx):
    params = ctx.resolved_params
    following = ctx.viewer_following_count
    is_new_user = (
        params.new_user_age_threshold_ms > 0
        and ctx.viewer_account_age_ms is not None
        and ctx.viewer_account_age_ms < params.new_user_age_threshold_ms
        and (0 if following is None else following)
        >= params.new_user_min_following)
    return (params.new_user_oon_weight_factor if is_new_user
            else params.oon_weight_factor)


# oonApplies in ranking.ts: out-of-network posts always and, while
# oon_rescore_in_network_replies_reblogs is on, in-network replies and
# boosts too. The only place the follow graph enters the score.
def oon_applies(candidate, params):
    if not candidate.in_network:
        return True
    if not params.oon_rescore_in_network_replies_reblogs:
        return False
    return (candidate.status.get('reblog') is not None
            or bool(candidate.status.get('in_reply_to_id')))


# applyAdjustments in ranking.ts: author diversity, then the out-of-network
# discount. Reads raw_score and writes score; returns new candidates in the
# input order (sorting is rank_candidates' job). The first step of Elk's
# version, the new-author boost, is not ported.
def apply_adjustments(candidates, ctx):
    params = ctx.resolved_params
    oon_factor = effective_oon_factor(ctx)
    adjusted = [0 if c.raw_score is None else c.raw_score
                for c in candidates]

    # Author diversity: walk the slate in descending score order; k is how
    # many posts by the same author were already passed.
    multipliers = [1] * len(candidates)
    if params.enable_author_diversity:
        order = sorted(range(len(adjusted)),
                       key=lambda i: (-adjusted[i], i))
        seen = {}
        for index in order:
            author_id = _diversity_key(candidates[index])
            k = seen.get(author_id, 0)
            multipliers[index] = diversity_multiplier(
                k, params.author_diversity_decay,
                params.author_diversity_floor)
            seen[author_id] = k + 1

    result = []
    for i, candidate in enumerate(candidates):
        reasons = list(candidate.reasons) if candidate.reasons else []

        score = adjusted[i] * multipliers[i]
        if multipliers[i] != 1:
            reasons.append(ScoreReason('author_diversity', multipliers[i]))

        # Out-of-network discount.
        if oon_applies(candidate, params):
            score *= oon_factor
            reasons.append(ScoreReason('out_of_network', oon_factor))

        result.append(dataclasses.replace(
            candidate, score=score, reasons=reasons))
    return result


# endregion

# region pipeline


def _by_score_then_id(a, b):
    delta = (0 if b.score is None else b.score) - (
        0 if a.score is None else a.score)
    if delta != 0:
        return -1 if delta < 0 else 1
    a_id = a.status['id']
    b_id = b.status['id']
    # b.status.id.localeCompare(a.status.id): newer (larger) id first.
    return -1 if b_id < a_id else 1 if b_id > a_id else 0


# rankCandidates in ranking.ts: predict, weighted sum, adjustments, sort.
# Highest score first, ties by status id descending. Returns a new list.
def rank_candidates(candidates, ctx):
    scored = [score_candidate(c, ctx) for c in candidates]
    adjusted = apply_adjustments(scored, ctx)
    return sorted(adjusted, key=functools.cmp_to_key(_by_score_then_id))


# endregion

# region html to text

_VOID_ELEMENTS = frozenset((
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr'))


class _Node:
    """A node of the little tree ``htmlToText`` walks (ultrahtml's)."""

    __slots__ = ('name', 'attrs', 'children', 'parent', 'value')

    def __init__(self, name=None, attrs=None, parent=None, value=None):
        self.name = name          # None for a text node
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.value = value


class _TreeBuilder(HTMLParser):
    def __init__(self):
        # convert_charrefs: text arrives decoded, which is what ultrahtml's
        # `decode(input.value)` does in treeToText.
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: ('' if v is None else v) for k, v in attrs},
                     self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_endtag(self, tag):
        if tag in _VOID_ELEMENTS:
            return
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].name == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(
            _Node(parent=self._stack[-1], value=data))


_EMOJI_ID_NEEDS_WRAPPING_RE = re.compile(r'^[A-Za-z0-9_-]+\Z')


# treeToText in content-parse.ts.
def _tree_to_text(node):
    if node.name is None:
        return node.value
    if node.name == 'br':
        return '\n'

    pre = ''
    post = ''
    if node.name in ('p', 'pre'):
        pre = '\n'

    if node.attrs.get('data-type') == 'mention':
        acct = node.attrs.get('data-id')
        if acct:
            return acct if acct.startswith('@') else '@' + acct

    if node.name == 'code':
        if node.parent is not None and node.parent.name == 'pre':
            lang = node.attrs.get('class')
            if lang is not None:
                lang = lang.replace('language-', '', 1)
            pre = '```%s\n' % (lang or '')
            post = '\n```'
        else:
            pre = '`'
            post = '`'
    elif node.name in ('b', 'strong'):
        pre = post = '**'
    elif node.name in ('i', 'em'):
        pre = post = '*'
    elif node.name == 'del':
        pre = post = '~~'

    body = ''.join(_tree_to_text(child) for child in node.children)

    if node.name in ('img', 'picture'):
        css = node.attrs.get('class')
        if css is not None and 'custom-emoji' in css:
            emoji_id = next(
                (node.attrs[k] for k in ('data-emoji-id', 'alt', 'title')
                 if k in node.attrs), 'unknown')
            if _EMOJI_ID_NEEDS_WRAPPING_RE.match(emoji_id):
                return ':%s:' % emoji_id
            return emoji_id
        if css is not None and 'iconify-emoji' in css:
            return node.attrs.get('alt', '')

    return pre + body + post


# htmlToText in content-parse.ts: status HTML to plain text, with the
# markdown-ish marks Elk's version adds for code, bold, italic and strike.
def html_to_text(html):
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    text = ''.join(_tree_to_text(child) for child in builder.root.children)
    return text.strip(_JS_WHITESPACE)


# endregion

# region Unicode tables
_EXTENDED_PICTOGRAPHIC = (
    (0xA9, 0xA9), (0xAE, 0xAE), (0x203C, 0x203C), (0x2049, 0x2049),
    (0x2122, 0x2122), (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA),
    (0x231A, 0x231B), (0x2328, 0x2328), (0x2388, 0x2388), (0x23CF, 0x23CF),
    (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25AB),
    (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2605),
    (0x2607, 0x2612), (0x2614, 0x2685), (0x2690, 0x2705), (0x2708, 0x2712),
    (0x2714, 0x2714), (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721),
    (0x2728, 0x2728), (0x2733, 0x2734), (0x2744, 0x2744), (0x2747, 0x2747),
    (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757),
    (0x2763, 0x2767), (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D),
    (0x3297, 0x3297), (0x3299, 0x3299), (0x1F000, 0x1F0FF),
    (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A),
    (0x1F1AD, 0x1F1E5), (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A), (0x1F23C, 0x1F23F),
    (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F), (0x1F7D5, 0x1F7FF),
    (0x1F80C, 0x1F80F), (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F),
    (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF), (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
)

_HAN = (
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005),
    (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B), (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
    (0x16FF0, 0x16FF1), (0x20000, 0x2A6DF), (0x2A700, 0x2B739),
    (0x2B740, 0x2B81D), (0x2B820, 0x2CEA1), (0x2CEB0, 0x2EBE0),
    (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A),
    (0x31350, 0x323AF),
)

_HIRAGANA = (
    (0x3041, 0x3096), (0x309D, 0x309F), (0x1B001, 0x1B11F),
    (0x1B132, 0x1B132), (0x1B150, 0x1B152), (0x1F200, 0x1F200),
)

_KATAKANA = (
    (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE),
    (0x3300, 0x3357), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D), (0x1AFF0, 0x1AFF3),
    (0x1AFF5, 0x1AFFB), (0x1AFFD, 0x1AFFE), (0x1B000, 0x1B000),
    (0x1B120, 0x1B122), (0x1B155, 0x1B155), (0x1B164, 0x1B167),
)

_THAI = (
    (0xE01, 0xE3A), (0xE40, 0xE5B),
)

_KHMER = (
    (0x1780, 0x17DD), (0x17E0, 0x17E9), (0x17F0, 0x17F9), (0x19E0, 0x19FF),
)

_LAO = (
    (0xE81, 0xE82), (0xE84, 0xE84), (0xE86, 0xE8A), (0xE8C, 0xEA3),
    (0xEA5, 0xEA5), (0xEA7, 0xEBD), (0xEC0, 0xEC4), (0xEC6, 0xEC6),
    (0xEC8, 0xECE), (0xED0, 0xED9), (0xEDC, 0xEDF),
)

_MYANMAR = (
    (0x1000, 0x109F), (0xA9E0, 0xA9FE), (0xAA60, 0xAA7F), (0x116D0, 0x116E3),
)

_HANGUL = (
    (0x1100, 0x11FF), (0x302E, 0x302F), (0x3131, 0x318E), (0x3200, 0x321E),
    (0x3260, 0x327E), (0xA960, 0xA97C), (0xAC00, 0xD7A3), (0xD7B0, 0xD7C6),
    (0xD7CB, 0xD7FB), (0xFFA0, 0xFFBE), (0xFFC2, 0xFFC7), (0xFFCA, 0xFFCF),
    (0xFFD2, 0xFFD7), (0xFFDA, 0xFFDC),
)
# endregion

# region diversity constants

# EMBEDDING_DIM in diversity.ts: the hashed feature space.
EMBEDDING_DIM = 4096
# DEFAULT_DIVERSITY_THETA: vm-ranker's dpp_theta.
DEFAULT_DIVERSITY_THETA = 0.5
# DEFAULT_DIVERSITY_LIMIT: vm-ranker's dpp_top_k.
DEFAULT_DIVERSITY_LIMIT = 50
# DEFAULT_DIVERSITY_POOL_SIZE: max_selected_rank, 200 against a top_k of 50.
DEFAULT_DIVERSITY_POOL_SIZE = 200
# DEFAULT_DIVERSITY_LAMBDA: for the linear penalty modes only.
DEFAULT_DIVERSITY_LAMBDA = 0.25
# DEFAULT_MAX_PER_CONVERSATION: how many branches of a thread survive.
DEFAULT_MAX_PER_CONVERSATION = 3

# MIN_RESIDUAL: vm-ranker's EPSILON, the conditional variance floor.
_MIN_RESIDUAL = 1e-6

# Relative pull of each feature family on the cosine.
_WEIGHT_AUTHOR = 0.4
_WEIGHT_TAGS = 1.2
_WEIGHT_TOKENS = 1.2
_WEIGHT_LINK = 0.8
_WEIGHT_EMOJI = 0.4
_WEIGHT_LANGUAGE = 0.35
_WEIGHT_SPOILER = 0.3
_WEIGHT_MEDIA = 0.3

# Within the link family: which story, mostly, and which outlet, a little.
_LINK_PATH_SHARE = 0.75
_LINK_HOST_SHARE = 0.25

# Hard bound on how much text is tokenised; sampled head and tail.
_MAX_TEXT_LENGTH = 4000
# Distinct tokens retained before the per-post selection step.
_MAX_DISTINCT_TOKENS = 512
# Distinct tokens that actually reach the embedding.
_MAX_TOKENS = 48

# endregion

# region tokenizer


def _ranges_class(ranges):
    """The body of a regex character class for (first, last) code points."""
    return ''.join('\\U%08x-\\U%08x' % pair for pair in ranges)


def _category_ranges(prefixes):
    """Code point ranges whose general category starts with a prefix."""
    ranges = []
    start = None
    category = unicodedata.category
    for code in range(0x110000):
        hit = category(chr(code))[0] in prefixes
        if hit and start is None:
            start = code
        elif not hit and start is not None:
            ranges.append((start, code - 1))
            start = None
    if start is not None:
        ranges.append((start, 0x10FFFF))
    return ranges


class _Patterns(NamedTuple):
    hashtag: Any
    emoji: Any
    token_split: Any
    needs_segmentation: Any
    single_char_ok: Any


@functools.lru_cache(maxsize=None)
def _patterns():
    """The Unicode-property regexes of diversity.ts, built on first use.

    Python's ``re`` has no ``\\p{...}``. General categories (``\\p{L}``,
    ``\\p{N}``, ``\\p{M}``) are scanned out of ``unicodedata`` once (about
    0.2 s), scripts and ``Extended_Pictographic`` come from the tables above.
    """
    word = _ranges_class(_category_ranges('LNM'))
    unspaced = ''.join(_ranges_class(table) for table in (
        _HAN, _HIRAGANA, _KATAKANA, _THAI, _KHMER, _LAO, _MYANMAR))
    single_ok = ''.join(_ranges_class(table) for table in (
        _HAN, _HIRAGANA, _KATAKANA, _HANGUL, _THAI))
    return _Patterns(
        # HASHTAG_RE = /#[\p{L}\p{N}\p{M}_]+/gu
        hashtag=re.compile('#[%s_]+' % word),
        # EMOJI_RE = /\p{Extended_Pictographic}/gu
        emoji=re.compile('[%s]' % _ranges_class(_EXTENDED_PICTOGRAPHIC)),
        # TOKEN_SPLIT_RE = /[^\p{L}\p{N}\p{M}_‌‍]+/u
        token_split=re.compile('[^%s_‌‍]+' % word),
        # NEEDS_SEGMENTATION_RE: scripts that do not delimit words.
        needs_segmentation=re.compile('[%s]' % unspaced),
        # SINGLE_CHAR_OK_RE: a character that is a morpheme by itself.
        single_char_ok=re.compile('[%s]' % single_ok),
    )


# URL_RE = /https?:\/\/\S+/g, and MENTION_RE (ASCII \w, as in JavaScript).
_URL_RE = re.compile('https?://[^%s]+' % _WS_CLASS)
_MENTION_RE = re.compile(r'@[\w.-]+(?:@[\w.-]+)?', re.ASCII)


# bigrams in diversity.ts: the CJK fallback when no segmenter exists.
def _bigrams(run, out):
    chars = list(run)
    if len(chars) == 1:
        out.append(chars[0])
        return
    for i in range(len(chars) - 1):
        out.append(chars[i] + chars[i + 1])


# keepToken in diversity.ts. Lengths are UTF-16 lengths, as in JavaScript.
def _keep_token(token):
    if len(token) == 0:
        return False
    if _js_length(token) >= 2:
        return True
    return _patterns().single_char_ok.match(token) is not None


# tokenizeText in diversity.ts: distinct, script-aware content tokens.
# NFC first, no stop-word list (IDF down-weights ubiquitous tokens).
def tokenize_text(text):
    if not text:
        return []
    patterns = _patterns()

    # Bound the work before doing anything quadratic-ish to it.
    bounded = text
    if _js_length(bounded) > _MAX_TEXT_LENGTH:
        half = _MAX_TEXT_LENGTH >> 1
        bounded = (_js_slice(bounded, 0, half) + ' '
                   + _js_slice(bounded, -half))

    cleaned = unicodedata.normalize('NFC', bounded).lower()
    cleaned = _URL_RE.sub(' ', cleaned)
    cleaned = _MENTION_RE.sub(' ', cleaned)
    cleaned = patterns.hashtag.sub(' ', cleaned)
    cleaned = patterns.emoji.sub(' ', cleaned)

    tokens = {}
    for raw in patterns.token_split.split(cleaned):
        if len(raw) == 0:
            continue

        if patterns.needs_segmentation.search(raw):
            # Intl.Segmenter is not available: take the bigram path.
            pieces = []
            _bigrams(raw, pieces)
            for piece in pieces:
                if _keep_token(piece):
                    tokens[piece] = None
        elif _keep_token(raw):
            tokens[raw] = None

        if len(tokens) >= _MAX_DISTINCT_TOKENS:
            break

    return list(tokens)


# endregion

# region hashing

_HASH_SEED_BUCKET = 0x811C9DC5
_HASH_SEED_SIGN = 0x9E3779B1


# hashString in diversity.ts: FNV-1a, 32-bit, over UTF-16 code units (what
# charCodeAt yields), so the buckets match Elk's.
def _hash_string(value, seed):
    h = seed & 0xFFFFFFFF
    raw = value.encode('utf-16-le', 'surrogatepass')
    for unit in struct.unpack('<%dH' % (len(raw) // 2), raw):
        h ^= unit
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


# endregion

# region feature extraction


# CandidateFeatures in diversity.ts: the content signals a client can
# extract. The *_ lists are ordered, de-duplicated (JavaScript Sets).
@dataclass
class CandidateFeatures:
    tokens: list = field(default_factory=list)
    spoiler_tokens: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    emoji: list = field(default_factory=list)
    author_id: str = ''
    language: str | None = None
    media_types: list = field(default_factory=list)
    link_paths: list = field(default_factory=list)
    link_hosts: list = field(default_factory=list)
    # Stable fallback so a featureless post still gets a unit vector.
    fallback_key: str = ''


_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_PATH_RE = re.compile(r'/tags?/', re.IGNORECASE)
_PROFILE_PATH_RE = re.compile(r'/@[^/]+/?\Z')
_LINK_PARTS_RE = re.compile(r'^https?://([^/?#]+)([^?#]*)', re.IGNORECASE)
_WWW_PREFIX_RE = re.compile(r'^www\.')
_TRAILING_SLASH_RE = re.compile(r'/+\Z')


# linkParts in diversity.ts: host and host/path of an outbound link, or
# None for in-fediverse chrome (profile and tag pages).
def _link_parts(href):
    if _TAG_PATH_RE.search(href) or _PROFILE_PATH_RE.search(href):
        return None
    match = _LINK_PARTS_RE.match(href)
    if not match:
        return None
    host = _WWW_PREFIX_RE.sub('', match.group(1).lower())
    path = _TRAILING_SLASH_RE.sub('', match.group(2) or '')
    return host, host + path


# distinctEmoji in diversity.ts: at most 16 distinct emoji.
def _distinct_emoji(text):
    found = {}
    for match in _patterns().emoji.finditer(text):
        found[match.group(0)] = None
        if len(found) >= 16:
            break
    return list(found)


# extractCandidateFeatures in diversity.ts: the signals of the *original*
# post (a boost is judged by what it boosts).
def extract_candidate_features(status):
    reblog = status.get('reblog')
    post = status if reblog is None else reblog
    html = post.get('content') or ''
    body = html_to_text(html) if html else ''
    spoiler = post.get('spoiler_text') or ''

    hashtags = {}
    for tag in post.get('tags') or ():
        if tag and tag.get('name'):
            name = unicodedata.normalize('NFC', tag['name']).lower()
            hashtags[name] = None
    # Servers do not always populate `tags`, so fall back to what looks
    # like a hashtag in the rendered text.
    for raw in _patterns().hashtag.findall(
            unicodedata.normalize('NFC', body)):
        hashtags[raw[1:].lower()] = None

    media_types = {}
    for media in post.get('media_attachments') or ():
        if media and media.get('type'):
            media_types[media['type']] = None

    link_paths = {}
    link_hosts = {}

    def collect_link(href):
        parts = _link_parts(href)
        if parts is None:
            return
        link_hosts[parts[0]] = None
        link_paths[parts[1]] = None

    for match in _HREF_RE.finditer(html):
        collect_link(match.group(1))
    card = post.get('card')
    if card and card.get('url'):
        collect_link(card['url'])

    account = post.get('account') or {}
    return CandidateFeatures(
        # The content warning is part of the post's topic, so its tokens
        # go in the body bag and get their own small family too.
        tokens=tokenize_text(spoiler + '\n' + body if spoiler else body),
        spoiler_tokens=tokenize_text(spoiler),
        hashtags=list(hashtags),
        emoji=_distinct_emoji(body),
        author_id=account.get('id') or '',
        language=post.get('language'),
        media_types=list(media_types),
        link_paths=list(link_paths),
        link_hosts=list(link_hosts),
        fallback_key=post.get('id') or '',
    )


# endregion

# region embedding


# SparseVector in diversity.ts: parallel index and value lists.
class SparseVector(NamedTuple):
    indices: list
    values: list


# computeTokenIdf in diversity.ts: log(1 + n / (1 + df)).
def compute_token_idf(features):
    df = {}
    for f in features:
        for token in f.tokens:
            df[token] = df.get(token, 0) + 1
    n = len(features)
    return {token: math.log(1 + n / (1 + count))
            for token, count in df.items()}


# selectTokens in diversity.ts: trim a token bag to MAX_TOKENS. With a
# corpus keep the most distinctive; without, sample at an even stride.
def _select_tokens(tokens, idf=None):
    if len(tokens) <= _MAX_TOKENS:
        return tokens
    if idf is not None:
        return sorted(tokens, key=lambda t: (-idf.get(t, 0), _utf16_key(t))
                      )[:_MAX_TOKENS]
    stride = len(tokens) / _MAX_TOKENS
    return [tokens[math.floor(i * stride)] for i in range(_MAX_TOKENS)]


# sparseEmbed in diversity.ts: hash a post's features into a sparse unit
# vector (hashing trick with sign hashing).
def sparse_embed(features, idf=None):
    acc = {}

    def add(key, weight):
        if weight == 0:
            return
        bucket = _hash_string(key, _HASH_SEED_BUCKET) % EMBEDDING_DIM
        sign = 1 if (_hash_string(key, _HASH_SEED_SIGN) & 1) == 0 else -1
        acc[bucket] = acc.get(bucket, 0) + sign * weight

    def add_family(prefix, keys, family_weight, weights=None):
        """L2-normalise a family internally, then scale to its weight."""
        if len(keys) == 0 or family_weight == 0:
            return
        norm = 0
        for i in range(len(keys)):
            w = weights[i] if weights else 1
            norm += w * w
        if norm <= EPSILON:
            return
        scale = family_weight / math.sqrt(norm)
        for i in range(len(keys)):
            add(prefix + keys[i], (weights[i] if weights else 1) * scale)

    if features.author_id:
        add('a:' + features.author_id, _WEIGHT_AUTHOR)
    if features.language:
        add('l:' + features.language.lower(), _WEIGHT_LANGUAGE)

    add_family('h:', features.hashtags, _WEIGHT_TAGS)
    add_family('m:', features.media_types, _WEIGHT_MEDIA)
    add_family('e:', features.emoji, _WEIGHT_EMOJI)
    add_family('c:', _select_tokens(features.spoiler_tokens, idf),
               _WEIGHT_SPOILER)
    add_family('u:', features.link_paths, _WEIGHT_LINK * _LINK_PATH_SHARE)
    add_family('d:', features.link_hosts, _WEIGHT_LINK * _LINK_HOST_SHARE)

    tokens = _select_tokens(features.tokens, idf)
    add_family('t:', tokens, _WEIGHT_TOKENS,
               [idf.get(t, 1) for t in tokens] if idf is not None else None)

    if len(acc) == 0:
        # vm-ranker hands a random unit vector to candidates whose
        # embedding is missing. Same idea, seeded off the post id so the
        # order is the same on every load.
        seed = features.fallback_key or 'empty'
        for i in range(4):
            add('z:%d:%s' % (i, seed), 1)

    norm = 0
    for value in acc.values():
        norm += value * value
    inv = 1 / math.sqrt(norm) if norm > EPSILON else 0

    return SparseVector(list(acc), [value * inv for value in acc.values()])


# densify in diversity.ts.
def _densify(sparse):
    dense = [0.0] * EMBEDDING_DIM
    for index, value in zip(sparse.indices, sparse.values, strict=True):
        dense[index] = value
    return dense


# embedFeatures in diversity.ts: features to a dense unit-norm vector.
def embed_features(features, idf=None):
    return _densify(sparse_embed(features, idf))


# embedCandidate in diversity.ts: the corpus-free embedding of one post.
def embed_candidate(candidate):
    return embed_features(extract_candidate_features(candidate.status))


# embedCandidates in diversity.ts: dense embeddings for a whole set, with
# IDF measured over that set.
def embed_candidates(candidates):
    return [_densify(v) for v in embed_candidates_sparse(candidates)]


def embed_candidates_sparse(candidates):
    """``embed_candidates`` without the 4096-wide densify (Python only)."""
    features = [extract_candidate_features(c.status) for c in candidates]
    idf = compute_token_idf(features)
    return [sparse_embed(f, idf) for f in features]


# cosineSimilarity in diversity.ts: in [-1, 1], 0 if either is degenerate.
def cosine_similarity(a, b):
    dot = 0
    na = 0
    nb = 0
    for x, y in zip(a, b, strict=False):  # min(len), as in Elk
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= EPSILON:
        return 0
    return dot / denom


def sparse_cosine_similarity(a, b):
    """``cosine_similarity`` over two SparseVectors (Python only)."""
    other = dict(zip(b.indices, b.values, strict=True))
    dot = 0
    for index, value in zip(a.indices, a.values, strict=True):
        y = other.get(index)
        if y is not None:
            dot += value * y
    na = sum(v * v for v in a.values)
    nb = sum(v * v for v in b.values)
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= EPSILON:
        return 0
    return dot / denom


# endregion

# region conversation dedup


# candidateScore in diversity.ts: score ?? rawScore ?? 0.
def candidate_score(candidate):
    if candidate.score is not None:
        return candidate.score
    if candidate.raw_score is not None:
        return candidate.raw_score
    return 0


# statusIdentity in diversity.ts: a reblog is its original.
def _status_identity(status):
    reblog = status.get('reblog')
    return status['id'] if reblog is None else reblog['id']


# ConversationSets in diversity.ts: union-find over in_reply_to_id.
class _ConversationSets:
    def __init__(self):
        self._parent = {}

    def find(self, id_):
        root = id_
        nxt = self._parent.get(root)
        while nxt is not None and nxt != root:
            root = nxt
            nxt = self._parent.get(root)
        # Path compression.
        cursor = id_
        while cursor != root:
            up = self._parent.get(cursor, root)
            self._parent[cursor] = root
            cursor = up
        return root

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        # Deterministic representative, independent of insertion order.
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb


# dedupeConversations in diversity.ts (home-mixer's DedupConversationFilter,
# widened from "keep one" to "keep the best N"). Identical posts (a boost
# and its original) always collapse to one; distinct posts of one
# conversation are capped at max_per_conversation. The best-scoring
# branches win, ties keep the earlier input, survivors occupy the input
# positions their conversation held. It does not sort.
def dedupe_conversations(candidates,
                         max_per_conversation=DEFAULT_MAX_PER_CONVERSATION):
    max_per_conversation = max(1, max_per_conversation)
    if len(candidates) < 2:
        return list(candidates)

    sets = _ConversationSets()
    ids = [None] * len(candidates)

    for i, candidate in enumerate(candidates):
        status = candidate.status
        id_ = _status_identity(status)
        ids[i] = id_
        reblog = status.get('reblog')
        parent_id = None
        if reblog is not None:
            parent_id = reblog.get('in_reply_to_id')
        if parent_id is None:
            parent_id = status.get('in_reply_to_id')
        if parent_id:
            sets.union(id_, parent_id)

    # Stage 1: collapse candidates that are the same underlying post. The
    # winner is the best-scoring one; it occupies the earliest slot.
    slot_by_identity = {}
    winner_by_slot = {}
    for i, candidate in enumerate(candidates):
        slot = slot_by_identity.get(ids[i])
        if slot is None:
            slot_by_identity[ids[i]] = i
            winner_by_slot[i] = candidate
        elif candidate_score(candidate) > candidate_score(
                winner_by_slot[slot]):
            winner_by_slot[slot] = candidate
    unique = sorted(slot_by_identity.values())

    # Stage 2: group the surviving distinct posts by conversation.
    groups = {}
    for i in unique:
        groups.setdefault(sets.find(ids[i]), []).append(i)

    # Each conversation keeps its first max_per_conversation slots, filled
    # with its best branches in score order.
    assigned = {}
    for members in groups.values():
        if len(members) <= max_per_conversation:
            for slot in members:
                assigned[slot] = winner_by_slot[slot]
            continue
        winners = sorted(
            members,
            key=lambda s: (-candidate_score(winner_by_slot[s]), s)
        )[:max_per_conversation]
        slots = members[:max_per_conversation]
        for slot, winner in zip(slots, winners, strict=True):
            assigned[slot] = winner_by_slot[winner]

    return [assigned[i] for i in range(len(candidates)) if i in assigned]


# endregion

# region rerank


# sparsifyDense in diversity.ts.
def _sparsify_dense(dense):
    indices = [d for d, value in enumerate(dense) if value != 0]
    return SparseVector(indices, [dense[d] for d in indices])


# diversityRerank in diversity.ts: greedy DPP diversity reorder, the
# closed form of vm-ranker's objective under the orthogonality
# approximation:
#
#     value_i = theta / (1 - theta) * q_i  -  sum_{j placed} log(1 - cos^2)
#
# with q_i = score_i / max_score. It reorders and never drops: the result
# is a permutation of the input. ``penalty`` is 'dpp' (default), 'residual'
# or 'max'; ``theta`` only matters for 'dpp', ``lambda_`` for the other two;
# ``limit`` is how many positions are actively placed (the rest keep score
# order), ``pool_size`` how many of the top-scored posts may move.
# ``embeddings`` are precomputed dense vectors, used only if every pooled
# one is EMBEDDING_DIM wide. ``get_score`` overrides candidate_score.
def diversity_rerank(candidates, *, penalty='dpp', theta=None, lambda_=None,
                     limit=None, pool_size=None, embeddings=None,
                     get_score=None):
    n = len(candidates)
    if n < 2:
        return list(candidates)

    mode = penalty
    lam = DEFAULT_DIVERSITY_LAMBDA if lambda_ is None else lambda_
    theta = min(max(DEFAULT_DIVERSITY_THETA if theta is None else theta, 0),
                1 - 1e-6)
    q_coefficient = theta / (1 - theta)
    get_score = candidate_score if get_score is None else get_score
    pool_size = max(0, min(n, DEFAULT_DIVERSITY_POOL_SIZE
                           if pool_size is None else pool_size))

    scores = [get_score(c) for c in candidates]

    # Stable score-descending order over the original indices.
    order = sorted(range(n), key=lambda i: (-scores[i], i))

    pool = order[:pool_size]
    overflow = order[pool_size:]
    m = len(pool)

    inert = mode != 'dpp' and lam <= 0
    if m < 2 or inert:
        return [candidates[i] for i in pool + overflow]

    # q_i = score_i / max_score, as vm-ranker normalises. If the scorer
    # emitted nothing positive, that ratio is meaningless: use min-max.
    low = math.inf
    high = -math.inf
    for i in pool:
        s = scores[i]
        if s < low:
            low = s
        if s > high:
            high = s
    if high > EPSILON:
        q = [scores[pool[p]] / high for p in range(m)]
    else:
        span = high - low
        q = [(scores[pool[p]] - low) / span if span > EPSILON else 0.5
             for p in range(m)]

    # Precomputed embeddings are only usable at exactly the right width.
    if (embeddings is not None
            and all(i < len(embeddings) and embeddings[i] is not None
                    and len(embeddings[i]) == EMBEDDING_DIM for i in pool)):
        vectors = [_sparsify_dense(embeddings[i]) for i in pool]
    else:
        features = [extract_candidate_features(candidates[i].status)
                    for i in pool]
        idf = compute_token_idf(features)
        vectors = [sparse_embed(f, idf) for f in features]

    limit = max(0, min(m, DEFAULT_DIVERSITY_LIMIT if limit is None
                       else limit))
    taken = [False] * m
    penalties = [0.0] * m
    placed = []

    for _step in range(limit):
        best = -1
        best_value = -math.inf
        for p in range(m):
            if taken[p]:
                continue
            # penalties already holds -sum log(1 - cos^2) for 'dpp', and
            # the raw linear aggregate for the other two.
            if mode == 'dpp':
                value = q_coefficient * q[p] - penalties[p]
            else:
                value = q[p] - lam * penalties[p]
            if value > best_value:
                best_value = value
                best = p
        if best < 0:
            break

        taken[best] = True
        placed.append(pool[best])

        if len(placed) == limit:
            break

        # Fold the freshly placed vector into every remaining candidate's
        # running penalty. The embeddings are unit-norm, so the dot
        # product is the cosine.
        chosen = vectors[best]
        if len(chosen.indices) > 0:
            scratch = dict(zip(chosen.indices, chosen.values,
                               strict=True))
            for p in range(m):
                if taken[p]:
                    continue
                dot = 0
                for index, value in zip(vectors[p].indices,
                                        vectors[p].values, strict=True):
                    s = scratch.get(index)
                    if s is not None:
                        dot += s * value

                # Squared, so anti-correlation penalises too.
                squared = dot * dot
                if mode == 'dpp':
                    penalties[p] -= math.log(max(1 - squared, _MIN_RESIDUAL))
                elif mode == 'residual':
                    penalties[p] += squared
                elif dot > penalties[p]:
                    penalties[p] = dot

    # Whatever the loop did not place keeps its score order.
    rest = [pool[p] for p in range(m) if not taken[p]]
    return [candidates[i] for i in placed + rest + overflow]


# endregion

# region entry point


# The whole Catch Up pipeline (CATCH-UP.md 4.1), the sequence feed.ts runs
# after candidate generation:
#
#     score -> author diversity + out-of-network discount -> sort
#       -> dedupe_conversations -> diversity_rerank
#
# ``candidates`` are Candidate objects or Mastodon Status dicts (wrapped
# with candidate_from_status). Returns the ranked Candidates, best first,
# with probabilities, raw_score, score and reasons filled in. The result
# depends only on the inputs and ``ctx.now``: no clock, no randomness.
# ``diversity`` is a dict of diversity_rerank keyword arguments.
def rank(candidates, ctx, *,
         max_per_conversation=DEFAULT_MAX_PER_CONVERSATION, diversity=None):
    wrapped = [c if isinstance(c, Candidate) else candidate_from_status(c)
               for c in candidates]
    ranked = rank_candidates(wrapped, ctx)
    ranked = dedupe_conversations(ranked, max_per_conversation)
    return diversity_rerank(ranked, **(diversity or {}))


# endregion
