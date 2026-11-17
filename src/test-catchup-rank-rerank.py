# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Catch Up relevance model (no display, no network).

``catchuprank`` is a port of Elk's For You ranker, so most of these are the
numeric expectations of Elk's ``for-you-ranking.test.ts`` and
``for-you-diversity.test.ts`` carried over, plus checks that are specific
to the port: determinism, no clock, and ``GOLDEN``, scores computed by the
TypeScript itself (``ranking.ts`` and ``diversity.ts`` at
``mauriciobc/elk@9597db5`` run under Node 22, with the segmenter disabled
and, for the ``nocut`` figures, the 48 h age cut removed) on the fixtures
below, which the port has to reproduce to 1e-9.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit.catchuprank import cr, ctx, elk_ctx, ids, make_candidate, make_status, raw  # noqa: E402
from testkit.catchuprank import (HOUR,
    Base)  # noqa: E402
from testkit.catchuprank_fixtures import (adjacent_runs,
    author_of,
    by_score,
    clustered_feed,
    div_candidate,
    div_fixture_candidates,
    fixture_candidates,
    fixture_ctx,
    mean_score,
    tail_rho,
    topic_of,
    varied_feed)  # noqa: E402
from testkit.catchuprank_golden import GOLDEN  # noqa: E402


# region weights


class TestDiversityRerank(Base):
    def test_breaks_up_same_author_and_same_topic_runs(self):
        feed = clustered_feed()
        ranked = by_score(feed)
        reranked = cr.diversity_rerank(feed)

        self.assertEqual(len(reranked), len(feed))
        self.assertEqual(len(set(ids(reranked))), len(feed))

        self.assertGreaterEqual(adjacent_runs(ranked, author_of, 20), 15)
        self.assertEqual(adjacent_runs(reranked, author_of, 20), 0)
        self.assertGreaterEqual(adjacent_runs(ranked, topic_of, 20), 15)
        self.assertEqual(adjacent_runs(reranked, topic_of, 20), 0)

        self.assertEqual(len({author_of(c) for c in ranked[:12]}), 2)
        self.assertEqual(len({author_of(c) for c in reranked[:12]}), 5)

    def test_the_textbook_max_mmr_penalty_cannot_do_this_at_any_lambda(self):
        feed = clustered_feed()
        best = min(
            adjacent_runs(
                cr.diversity_rerank(feed, lambda_=lam, penalty='max'),
                author_of, 20)
            for lam in (0.05, 0.1, 0.2, 0.35, 0.5, 0.8, 1.5, 3))
        self.assertGreater(best, 5)

    def test_gives_up_only_a_little_score_to_buy_that_diversity(self):
        feed = clustered_feed()
        ranked = by_score(feed)
        reranked = cr.diversity_rerank(feed)
        before = mean_score(ranked, 20)
        after = mean_score(reranked, 20)
        score_range = ranked[0].score - ranked[-1].score
        self.assertLessEqual(after, before)
        self.assertLess((before - after) / score_range, 0.25)
        # The single best post is never displaced.
        self.assertEqual(reranked[0].status['id'], ranked[0].status['id'])

    def test_barely_perturbs_a_feed_that_is_already_diverse(self):
        feed = varied_feed()
        ranked = by_score(feed)
        reranked = cr.diversity_rerank(feed)
        original_top = set(ids(ranked[:20]))
        overlap = sum(1 for c in reranked[:20]
                      if c.status['id'] in original_top)
        self.assertGreaterEqual(overlap, 18)
        loss = (mean_score(ranked, 20) - mean_score(reranked, 20)) \
            / mean_score(ranked, 20)
        self.assertLess(loss, 0.02)

    def test_restructures_a_feed_whose_posts_share_their_vocabulary(self):
        feed = [div_candidate(
            1 / (1 + i * 0.05), id='p%d' % i, author_id='author-%d' % (i % 40),
            tags=['topic-%d' % (i % 25)],
            text='Post number %d about widget %d and gadget %d with filler '
                 'prose %d' % (i, i % 25, i % 11, i)) for i in range(200)]
        vectors = cr.embed_candidates_sparse(feed)
        self.assertGreater(
            cr.sparse_cosine_similarity(vectors[0], vectors[1]), 0.4)
        top20 = set(ids(by_score(feed)[:20]))
        overlap = sum(1 for c in cr.diversity_rerank(feed)[:20]
                      if c.status['id'] in top20)
        self.assertLess(overlap, 10)

    def test_breaks_up_one_story_many_authors(self):
        feed = [div_candidate(
            1 - i * 0.001, id='story-%d' % i, author_id='reporter-%d' % i,
            tags=['breakingnews'],
            text='The council voted last night to approve the harbour '
                 'redevelopment plan',
            links=['https://news.example/harbour-redevelopment-approved'])
            for i in range(18)]
        feed += [div_candidate(
            0.9 - i * 0.005, id='other-%d' % i, author_id='person-%d' % i,
            tags=['hobby-%d' % i],
            text='An entirely separate thought about subject%d and matter%d '
                 'today' % (i, i)) for i in range(40)]

        def is_story(c):
            return c.status['id'].startswith('story-')

        self.assertEqual(sum(map(is_story, by_score(feed)[:10])), 10)
        self.assertLessEqual(
            sum(map(is_story, cr.diversity_rerank(feed)[:10])), 3)

    def test_leaves_the_tail_in_score_order_instead_of_novelty_order(self):
        feed = [div_candidate(
            1 / (1 + i * 0.02), id='p%d' % i, author_id='author-%d' % (i % 30),
            tags=['topic-%d' % (i % 15)],
            text='Filler post %d discussing subject %d and object %d' % (
                i, i % 15, i % 9)) for i in range(300)]
        ranked = by_score(feed)
        self.assertLessEqual(cr.DEFAULT_DIVERSITY_LIMIT, 50)

        reranked = cr.diversity_rerank(feed)
        self.assertAlmostEqual(tail_rho(reranked, ranked, 100, 200), 1,
                               places=6)
        self.assertAlmostEqual(tail_rho(reranked, ranked, 200, 300), 1,
                               places=6)

        unbounded = cr.diversity_rerank(feed, limit=300, pool_size=300)
        self.assertLess(tail_rho(unbounded, ranked, 150, 300), 0.6)
        self.assertLess(tail_rho(unbounded, ranked, 150, 300),
                        tail_rho(reranked, ranked, 150, 300) - 0.3)

    def test_needs_no_lambda_the_dpp_objective_is_parameter_free(self):
        feed = clustered_feed()
        a = cr.diversity_rerank(feed, lambda_=0)
        b = cr.diversity_rerank(feed, lambda_=99)
        self.assertEqual(ids(a), ids(b))

        ranked = by_score(feed)
        # theta -> 1 sends the quality coefficient to infinity.
        score_ordered = cr.diversity_rerank(feed, theta=1 - 1e-9)
        self.assertEqual(ids(score_ordered), ids(ranked))

        # theta = 0 drops the quality term and orders purely by novelty.
        novelty = cr.diversity_rerank(feed, theta=0)
        self.assertEqual(novelty[0].status['id'], ranked[0].status['id'])
        self.assertNotEqual(ids(novelty), ids(ranked))
        self.assertLess(mean_score(novelty, 20), mean_score(ranked, 20))

    def test_lambda_zero_degenerates_the_linear_objectives_to_a_sort(self):
        feed = clustered_feed()
        ranked = by_score(feed)
        for penalty in ('residual', 'max'):
            self.assertEqual(
                ids(cr.diversity_rerank(feed, penalty=penalty, lambda_=0)),
                ids(ranked))

    def test_a_larger_lambda_trades_more_score_for_more_diversity(self):
        feed = clustered_feed()
        gentle = cr.diversity_rerank(feed, penalty='residual', lambda_=0.05,
                                     limit=40)
        aggressive = cr.diversity_rerank(feed, penalty='residual',
                                         lambda_=1.5, limit=40)
        self.assertGreater(adjacent_runs(gentle, author_of, 20),
                           adjacent_runs(aggressive, author_of, 20))
        self.assertLess(mean_score(aggressive, 20), mean_score(gentle, 20))

    def test_is_deterministic_and_a_strict_permutation_of_its_input(self):
        feed = clustered_feed()
        a = cr.diversity_rerank(feed)
        b = cr.diversity_rerank(feed)
        self.assertEqual(ids(a), ids(b))
        self.assertTrue(all(any(c is x for x in a) for c in feed))
        self.assertEqual(len(a), len(feed))

    def test_honours_limit_and_pool_size_like_max_selected_rank(self):
        feed = clustered_feed()
        ranked = by_score(feed)

        pooled = cr.diversity_rerank(feed, pool_size=10)
        self.assertEqual(ids(pooled[10:]), ids(ranked[10:]))
        self.assertEqual(set(ids(pooled[:10])), set(ids(ranked[:10])))

        limited = cr.diversity_rerank(feed, limit=5)
        placed = set(ids(limited[:5]))
        self.assertEqual(
            ids(limited[5:]),
            [i for i in ids(ranked) if i not in placed])

    def test_ignores_precomputed_embeddings_of_the_wrong_width(self):
        feed = clustered_feed()
        good = cr.embed_candidates(feed)
        baseline = ids(cr.diversity_rerank(feed))
        self.assertEqual(
            ids(cr.diversity_rerank(feed, embeddings=good)), baseline)
        narrow = [[0.0] * 256 for _ in feed]
        self.assertEqual(
            ids(cr.diversity_rerank(feed, embeddings=narrow)), baseline)

    def test_precomputed_embeddings_are_actually_used(self):
        feed = clustered_feed()[:6]
        # all-identical vectors: every post is a duplicate of every other
        same = [1.0] + [0.0] * (cr.EMBEDDING_DIM - 1)
        from_same = cr.diversity_rerank(feed, embeddings=[same] * 6)
        self.assertEqual(ids(from_same), ids(by_score(feed)))
        # orthogonal vectors: nothing is penalised, so a pure score sort
        ortho = [[1.0 if d == i else 0.0 for d in range(cr.EMBEDDING_DIM)]
                 for i in range(6)]
        self.assertEqual(
            ids(cr.diversity_rerank(feed, embeddings=ortho)),
            ids(by_score(feed)))

    def test_a_duplicate_pays_the_full_log_floor(self):
        # B duplicates the top post A, C is unrelated. With the quality
        # coefficient at 20, B only loses to C because the penalty of a
        # duplicate is -log(1e-6) = 13.8 (a floor of 1e-3 would give 6.9).
        feed = [div_candidate(1.0, id='A'), div_candidate(0.99, id='B'),
                div_candidate(0.5, id='C')]
        a = [1.0, 0.0, 0.0] + [0.0] * (cr.EMBEDDING_DIM - 3)
        c = [0.0, 1.0, 0.0] + [0.0] * (cr.EMBEDDING_DIM - 3)
        order = cr.diversity_rerank(
            feed, theta=20 / 21, embeddings=[a, a, c])
        self.assertEqual(ids(order), ['A', 'C', 'B'])

    def test_two_candidates_are_still_ordered_by_score(self):
        feed = [div_candidate(0.1, id='low', author_id='a'),
                div_candidate(0.9, id='high', author_id='b')]
        self.assertEqual(ids(cr.diversity_rerank(feed)), ['high', 'low'])

    def test_theta_of_one_or_more_is_clamped_and_gives_a_score_sort(self):
        feed = clustered_feed()
        for theta in (1, 1.5):
            self.assertEqual(
                ids(cr.diversity_rerank(feed, theta=theta)),
                ids(by_score(feed)))

    def test_only_the_top_200_by_score_may_move_by_default(self):
        feed = [div_candidate(
            1 / (1 + i * 0.05), id='p%d' % i, author_id='a%d' % (i % 3),
            tags=['t%d' % (i % 3)], text='same words here %d' % (i % 3))
            for i in range(230)]
        ranked = by_score(feed)
        reranked = cr.diversity_rerank(feed)
        self.assertEqual(ids(reranked[200:]), ids(ranked[200:]))
        self.assertEqual(cr.DEFAULT_DIVERSITY_POOL_SIZE, 200)

    def test_the_linear_modes_default_to_lambda_0_25(self):
        feed = clustered_feed()
        for penalty in ('residual', 'max'):
            self.assertEqual(
                ids(cr.diversity_rerank(feed, penalty=penalty)),
                ids(cr.diversity_rerank(feed, penalty=penalty,
                                        lambda_=0.25)))
        self.assertEqual(cr.DEFAULT_DIVERSITY_LAMBDA, 0.25)

    def test_the_constants_are_vm_rankers(self):
        self.assertEqual(
            (cr.DEFAULT_DIVERSITY_THETA, cr.DEFAULT_DIVERSITY_LIMIT,
             cr.DEFAULT_DIVERSITY_POOL_SIZE, cr.EMBEDDING_DIM),
            (0.5, 50, 200, 4096))

    def test_handles_trivial_inputs(self):
        self.assertEqual(cr.diversity_rerank([]), [])
        self.assertEqual(
            len(cr.diversity_rerank([div_candidate(id='1')])), 1)
        flat = [div_candidate(0, id='1', author_id='a'),
                div_candidate(0, id='2', author_id='a'),
                div_candidate(0, id='3', author_id='b')]
        self.assertEqual(len(cr.diversity_rerank(flat)), 3)
        negative = [div_candidate(-3, id='1', author_id='a'),
                    div_candidate(-1, id='2', author_id='b'),
                    div_candidate(-2, id='3', author_id='c')]
        self.assertEqual(cr.diversity_rerank(negative)[0].status['id'], '2')

    def test_get_score_overrides_the_score_read(self):
        feed = [div_candidate(1, id='a', author_id='x'),
                div_candidate(2, id='b', author_id='y')]
        flipped = cr.diversity_rerank(feed, get_score=lambda c: -c.score)
        self.assertEqual(ids(flipped), ['a', 'b'])

    def test_never_drops_a_post_and_never_changes_a_score(self):
        feed = varied_feed()
        scores = {c.status['id']: c.score for c in feed}
        reranked = cr.diversity_rerank(feed)
        self.assertEqual(len(reranked), 200)
        self.assertEqual({c.status['id']: c.score for c in reranked},
                         scores)

    def test_matches_the_typescript_orders(self):
        candidates = div_fixture_candidates()
        rerank = cr.diversity_rerank
        self.assertEqual(ids(rerank(candidates)), GOLDEN['rerank'])
        self.assertEqual(ids(rerank(candidates, limit=5)),
                         GOLDEN['rerank_limit5'])
        self.assertEqual(
            ids(rerank(candidates, penalty='residual', lambda_=1.5)),
            GOLDEN['rerank_residual'])
        self.assertEqual(
            ids(rerank(candidates, penalty='max', lambda_=0.5)),
            GOLDEN['rerank_max'])
        self.assertEqual(ids(rerank(candidates, theta=0.2)),
                         GOLDEN['rerank_theta'])


# endregion

# region Catch Up deviations


class TestCatchUpDeviations(Base):
    def test_no_hard_age_cut_old_posts_keep_a_rankable_score(self):
        old = make_candidate(id='old', age_ms=200 * HOUR, favourites=400)
        self.assertGreater(raw(old), cr.NEGATIVE_SCORES_OFFSET * 10)
        self.assertGreater(cr.recency_multiplier(200 * HOUR), 0.12)

    def test_a_week_old_hit_still_beats_a_week_old_dud(self):
        hit = make_candidate(id='hit', author_id='h', age_ms=150 * HOUR,
                             favourites=900, reblogs=300)
        dud = make_candidate(id='dud', author_id='d', age_ms=150 * HOUR)
        ranked = cr.rank_candidates([dud, hit], ctx())
        self.assertEqual(ids(ranked), ['hit', 'dud'])

    def test_elks_cut_is_available_by_parameter(self):
        old = make_candidate(age_ms=60 * HOUR, favourites=400)
        self.assertLessEqual(raw(old, elk_ctx()), cr.NEGATIVE_SCORES_OFFSET)

    def test_there_is_no_randomness_two_runs_agree_to_the_bit(self):
        runs = [[(c.status['id'], c.score) for c in
                 cr.rank(fixture_candidates(), fixture_ctx())]
                for _ in range(3)]
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_in_network_comes_from_the_candidate(self):
        status = make_status(favourites=30)
        followed = cr.rank_candidates([cr.Candidate(status, True)], ctx())
        via_tag = cr.rank_candidates([cr.Candidate(status, False)], ctx())
        self.assertLess(via_tag[0].score, followed[0].score)

    def test_mutual_author_ids_come_from_the_context(self):
        status = make_status(author_id='friend')
        plain = cr.score_candidate(cr.Candidate(status), ctx())
        mutual = cr.score_candidate(
            cr.Candidate(status), ctx(mutual_author_ids=frozenset(
                {'friend'})))
        self.assertGreater(mutual.raw_score, plain.raw_score)

    def test_the_default_affinity_resolver_is_a_no_op(self):
        status = make_status(favourites=30, tags=['a'])
        with_null = cr.score_candidate(cr.Candidate(status), ctx())
        explicit = cr.score_candidate(
            cr.Candidate(status),
            ctx(affinity=cr.AffinityResolver()))
        self.assertEqual(with_null.raw_score, explicit.raw_score)
        empty = cr.score_candidate(
            cr.Candidate(status), ctx(cr.ForYouSignals()))
        self.assertEqual(with_null.raw_score, empty.raw_score)


# endregion


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
