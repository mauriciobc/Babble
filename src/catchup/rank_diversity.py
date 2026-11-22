# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversation dedupe and diversity re-ranking for Catch Up."""

import math

from .rank_text import (
    DEFAULT_DIVERSITY_LAMBDA, DEFAULT_DIVERSITY_LIMIT,
    DEFAULT_DIVERSITY_POOL_SIZE, DEFAULT_DIVERSITY_THETA,
    DEFAULT_MAX_PER_CONVERSATION, EMBEDDING_DIM, EPSILON, SparseVector,
    _MIN_RESIDUAL, compute_token_idf, extract_candidate_features,
    sparse_embed,
)

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
def diversity_rerank(candidates, *, penalty='dpp',
                     theta=DEFAULT_DIVERSITY_THETA,
                     lambda_=DEFAULT_DIVERSITY_LAMBDA,
                     limit=DEFAULT_DIVERSITY_LIMIT,
                     pool_size=DEFAULT_DIVERSITY_POOL_SIZE,
                     embeddings=None, get_score=candidate_score):
    n = len(candidates)
    if n < 2:
        return list(candidates)

    mode = penalty
    lam = lambda_
    theta = min(max(theta, 0), 1 - 1e-6)
    q_coefficient = theta / (1 - theta)
    pool_size = max(0, min(n, pool_size))

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
    low = min(scores[i] for i in pool)
    high = max(scores[i] for i in pool)
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

    limit = max(0, min(m, limit))
    # (index, value) pairs in each vector's own order: the dot product below
    # adds in that order, and so must any faster form of it.
    pairs = [list(zip(v.indices, v.values, strict=True)) for v in vectors]
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
        chosen = pairs[best]
        if chosen:
            scratch_get = dict(chosen).get
            for p in range(m):
                if taken[p]:
                    continue
                dot = 0
                for index, value in pairs[p]:
                    s = scratch_get(index)
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
