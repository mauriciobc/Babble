# SPDX-License-Identifier: GPL-3.0-or-later
"""Ranking, conversation filtering and diversity ordering for Catch Up."""

import dataclasses
import functools
import math
from typing import NamedTuple

from .rank_model import (
    BIDIRECTIONAL_FOLLOW_DWELL_WEIGHT_BOOST, Candidate, ScoredCandidate,
    MAX_FOLLOWERS_THRESHOLD, MIN_VIDEO_DURATION_MS,
    NEGATIVE_SCORES_OFFSET, ScoreReason, _clamp, _clamp01,
    candidate_from_status, content_status, extract_ranking_features, lift,
    log_norm, _video_duration_ms,
)
from .rank_text import (
    EMBEDDING_DIM, DEFAULT_DIVERSITY_LAMBDA, DEFAULT_DIVERSITY_LIMIT,
    DEFAULT_DIVERSITY_POOL_SIZE, DEFAULT_DIVERSITY_THETA,
    DEFAULT_MAX_PER_CONVERSATION, EPSILON, SparseVector,
    _MIN_RESIDUAL, compute_token_idf, extract_candidate_features,
    sparse_embed,
)
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

        result.append(candidate._replace(score=score, reasons=reasons)
                       if isinstance(candidate, ScoredCandidate)
                       else dataclasses.replace(
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
def _rank_candidates(candidates, ctx):
    scored = [score_candidate(c, ctx) for c in candidates]
    adjusted = apply_adjustments(scored, ctx)
    return sorted(adjusted, key=functools.cmp_to_key(_by_score_then_id))


def rank_candidates(candidates, ctx) -> list[ScoredCandidate]:
    """Return immutable scored records in final score order."""
    return [ScoredCandidate(
        status=c.status, in_network=c.in_network,
        probabilities=c.probabilities, raw_score=c.raw_score,
        score=c.score, reasons=c.reasons)
        for c in _rank_candidates(candidates, ctx)]


# endregion
# region the model


# predictActions in ranking.ts: P(action) for every head, as a dict in the
# order of ACTIONS.
# ACC-04 / CLEAN-32: test-only scaffolding for the golden TypeScript diff;
# production Catch-Up does not call this — do not delete or "simplify".
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
# with candidate_from_status). Returns ScoredCandidate records best first,
# with probabilities, raw_score, score and reasons filled in. The inputs
# remain unchanged. The result depends only on the inputs and ``ctx.now``:
# no clock, no randomness. ``diversity`` is a dict of diversity_rerank
# keyword arguments.
def rank(candidates, ctx, *,
         max_per_conversation=DEFAULT_MAX_PER_CONVERSATION, diversity=None):
    wrapped = [c if isinstance(c, Candidate) else candidate_from_status(c)
               for c in candidates]
    ranked = _rank_candidates(wrapped, ctx)
    ranked = dedupe_conversations(ranked, max_per_conversation)
    ranked = diversity_rerank(ranked, **(diversity or {}))
    return [ScoredCandidate(
        status=c.status, in_network=c.in_network,
        probabilities=c.probabilities, raw_score=c.raw_score,
        score=c.score, reasons=c.reasons)
        for c in ranked]


# endregion
