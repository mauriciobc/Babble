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
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, NamedTuple

from .rank_text import (
    _JS_WHITESPACE, _WS_CLASS, _count, _js_length, _to_ms,
    parse_timestamp_ms,
)



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
    half_life = params.recency_half_life_ms
    # A zero (or negative) half-life is Infinity in the TypeScript, which
    # floors the multiplier; dividing by it here would raise and take the
    # whole ranking down instead.
    half_lives = age / half_life if half_life > 0 else math.inf
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





