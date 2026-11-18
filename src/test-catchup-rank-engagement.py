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

import math
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)


























# Computed by the TypeScript (see the module docstring).




# region weights

from testkit.catchuprank import (ACTION_KEYS, HOUR, Base,  # noqa: E402
                                 cr, ctx, final, make_candidate, raw)

class TestEngagementIsCoverageCorrected(Base):
    def at(self, favourites, c=None, **options):
        return raw(make_candidate(favourites=favourites, age_ms=2 * HOUR,
                                  **options), c)

    def test_engagement_keeps_discriminating_across_the_corpus(self):
        self.assertGreater(self.at(300), self.at(30) * 1.1)
        self.assertGreater(self.at(3_000), self.at(300) * 1.1)

    def test_is_strictly_increasing_across_the_plausible_corpus(self):
        ladder = [self.at(n) for n in
                  (0, 1, 3, 10, 37, 68, 300, 769, 1_343, 5_000)]
        for i in range(1, len(ladder)):
            self.assertGreater(ladder[i], ladder[i - 1], 'step %d' % i)

    def test_still_has_strongly_diminishing_returns(self):
        self.assertGreater(self.at(10) - self.at(0),
                           self.at(5_000) - self.at(1_343))

    def test_puts_a_remote_post_on_the_same_scale_as_a_local_one(self):
        local = raw(make_candidate(acct='alice', favourites=30))
        remote = raw(make_candidate(acct='alice@remote.example',
                                    favourites=18))
        self.assertClose(remote, local, 12)

    def test_corrects_each_count_by_how_much_of_it_federates(self):
        def estimate(**options):
            return cr.extract_ranking_features(
                make_candidate(**options), ctx()).engagement_estimate

        self.assertClose(
            estimate(acct='a@remote.example', favourites=20), 20 / 0.6)
        self.assertClose(
            estimate(acct='a@remote.example', reblogs=20), 20 / 0.93)
        self.assertClose(estimate(acct='a@remote.example', replies=20), 20)
        # A local post is authoritative and is left exactly alone.
        self.assertClose(estimate(acct='a', favourites=20), 20)
        # The raw observed sum stays honest either way.
        f = cr.extract_ranking_features(
            make_candidate(acct='a@remote.example', favourites=20), ctx())
        self.assertEqual(f.total_engagement, 20)

    def test_does_not_pretend_to_recover_engagement_that_never_federated(
            self):
        remote = raw(make_candidate(acct='bob@remote.example',
                                    favourites=0))
        local = raw(make_candidate(acct='bob', favourites=0))
        self.assertClose(remote, local, 12)

    def test_corrects_the_density_ratios_of_a_remote_post(self):
        f = cr.extract_ranking_features(make_candidate(
            acct='a@remote.example', favourites=30, reblogs=20,
            replies=10), ctx())
        estimate = 30 / 0.6 + 20 / 0.93 + 10
        self.assertClose(f.reply_density, 10 / estimate)
        self.assertClose(f.reblog_density, (20 / 0.93) / estimate)
        # The raw shares would read 10/60 and 20/60: both overstate.
        self.assertLess(f.reply_density, 10 / 60)
        self.assertLess(f.reblog_density, 20 / 60)

    def test_a_remote_post_with_no_favourites_reads_as_boost_heavy(self):
        f = cr.extract_ranking_features(make_candidate(
            acct='a@remote.example', reblogs=20, replies=20), ctx())
        self.assertGreater(f.reblog_density, f.reply_density)
        self.assertClose(f.reply_density, 20 / (20 / 0.93 + 20))

    def test_a_non_positive_coverage_means_no_correction(self):
        f = cr.extract_ranking_features(
            make_candidate(acct='a@remote.example', favourites=20,
                           reblogs=10, replies=5),
            ctx(params={'favourite_coverage_remote': 0}))
        self.assertTrue(math.isfinite(f.engagement_estimate))
        self.assertEqual(f.engagement_estimate, 20 + 10 / 0.93 + 5)

    def test_velocity_uses_engagement_per_hour_with_a_floor_on_age(self):
        f = cr.extract_ranking_features(
            make_candidate(favourites=100, age_ms=0), ctx())
        self.assertClose(f.velocity, cr.log_norm(100 / 0.25, 500))
        f = cr.extract_ranking_features(
            make_candidate(favourites=100, age_ms=100 * HOUR), ctx())
        self.assertClose(f.velocity, cr.log_norm(100 / 48, 500))
        self.assertClose(f.engagement_signal,
                         0.5 * f.popularity + 0.5 * f.velocity)

    def test_saturates_past_the_plausible_corpus(self):
        def at(n):
            return final(make_candidate(in_network=False, favourites=n,
                                        age_ms=2 * HOUR))
        self.assertClose(at(100_000), at(1_000_000), 12)


# endregion

# region the follow graph, priors and heads


class TestFollowGraphAppliedOnce(Base):
    def test_predicts_identical_probabilities_except_follow_author(self):
        options = dict(favourites=120, reblogs=30, replies=12, images=1,
                       card=True)
        in_net = cr.predict_actions(
            make_candidate(in_network=True, **options), ctx())
        out_net = cr.predict_actions(
            make_candidate(in_network=False, **options), ctx())
        for key in ACTION_KEYS:
            if key != 'follow_author':
                self.assertAlmostEqual(in_net[key], out_net[key],
                                       places=12, msg=key)
        self.assertEqual(in_net['follow_author'], 0)
        self.assertGreater(out_net['follow_author'], 0)

    def test_gives_in_network_at_most_the_discount_advantage(self):
        options = dict(favourites=80, replies=6)
        in_net = final(make_candidate(in_network=True, **options))
        out_net = final(make_candidate(in_network=False, **options))
        limit = 1 / cr.DEFAULT_RANKING_PARAMS.oon_weight_factor
        self.assertLessEqual(in_net / out_net, limit + 1e-9)
        self.assertGreater(in_net / out_net, 1)

    def test_lets_an_excellent_oon_post_outrank_a_mediocre_in_network_one(
            self):
        banger = make_candidate(
            id='oon', author_id='B', in_network=False, favourites=240,
            reblogs=80, replies=30, age_ms=2 * HOUR, followers=5000)
        mediocre = make_candidate(id='in', author_id='A', in_network=True,
                                  favourites=3, age_ms=HOUR)
        one_reply = make_candidate(id='in2', author_id='C', in_network=True,
                                   replies=1, age_ms=HOUR)
        self.assertGreater(final(banger), final(mediocre) * 1.5)
        self.assertGreater(final(banger), final(one_reply) * 1.5)
        self.assertEqual(
            cr.rank_candidates([mediocre, one_reply, banger],
                               ctx())[0].status['id'], 'oon')

    def test_has_no_oon_ceiling_more_engagement_keeps_helping(self):
        def at(n):
            return final(make_candidate(in_network=False, favourites=n,
                                        age_ms=2 * HOUR))
        ladder = [at(n) for n in (10, 100, 1_000, 5_000)]
        for i in range(1, len(ladder)):
            self.assertGreater(ladder[i], ladder[i - 1])
        dull = final(make_candidate(in_network=True, favourites=0,
                                    age_ms=HOUR))
        self.assertGreater(ladder[0], dull)
        self.assertGreater(ladder[3] / dull, 3)


class TestBidirectionalBoost(Base):
    def test_does_nothing_unless_the_caller_supplies_mutual_author_ids(self):
        candidate = make_candidate(author_id='mutual')
        self.assertEqual(
            cr.effective_weights(candidate, ctx())['reply'],
            cr.MASTODON_WEIGHTS['reply'])

    def test_adds_the_full_x_boost_to_reply_and_is_sweepable(self):
        mutuals = {'mutual'}
        candidate = make_candidate(author_id='mutual')
        self.assertClose(
            cr.effective_weights(
                candidate, ctx(mutual_author_ids=mutuals))['reply'],
            cr.MASTODON_WEIGHTS['reply'] + 15)
        self.assertClose(
            cr.effective_weights(candidate, ctx(
                mutual_author_ids=mutuals,
                params={'bidirectional_follow_reply_weight_boost': 0})
            )['reply'], cr.MASTODON_WEIGHTS['reply'])

    def test_adds_the_boost_only_for_mutual_original_posts(self):
        mutuals = {'a1'}
        c = ctx(mutual_author_ids=mutuals)
        original = make_candidate(author_id='a1')
        self.assertClose(cr.effective_weights(original, c)['reply'], 20)
        reply = make_candidate(author_id='a1', in_reply_to_id='x')
        self.assertClose(cr.effective_weights(reply, c)['reply'], 5)
        boost = make_candidate(author_id='a1', boost_of={'author_id': 'a1'})
        self.assertClose(cr.effective_weights(boost, c)['reply'], 5)
        stranger = make_candidate(author_id='a9')
        self.assertClose(cr.effective_weights(stranger, c)['reply'], 5)

    def test_a_mutual_original_post_scores_higher(self):
        candidate = make_candidate(author_id='a1', in_network=False)
        plain = cr.score_candidate(candidate, ctx())
        mutual = cr.score_candidate(
            candidate, ctx(mutual_author_ids={'a1'}))
        self.assertGreater(mutual.raw_score, plain.raw_score)

    def test_normalises_every_candidate_against_the_same_denominator(self):
        mutuals = {'a1'}
        candidate = make_candidate(author_id='a1', in_network=False)
        with_boost = cr.score_candidate(
            candidate, ctx(mutual_author_ids=mutuals))
        without = cr.score_candidate(candidate, ctx())

        sums = cr.weight_sums(cr.X_WEIGHTS)
        context = cr.context_multiplier(candidate, ctx())
        for scored, c in ((with_boost, ctx(mutual_author_ids=mutuals)),
                          (without, ctx())):
            weights = cr.effective_weights(candidate, c)
            pos = neg = 0
            for key in ACTION_KEYS:
                term = scored.probabilities[key] * weights[key]
                if term >= 0:
                    pos += term
                else:
                    neg -= term
            net = pos - neg
            self.assertClose(
                scored.raw_score,
                cr.offset_score(context * net if net >= 0 else net, sums),
                12)
        self.assertGreater(with_boost.raw_score, without.raw_score)


class TestVqvWeight(Base):
    def test_is_zeroed_below_the_minimum_video_duration(self):
        w = cr.effective_weights
        self.assertEqual(w(make_candidate(), ctx())['vqv'], 0)
        self.assertEqual(
            w(make_candidate(video_duration_secs=5), ctx())['vqv'], 0)
        self.assertClose(
            w(make_candidate(video_duration_secs=30), ctx())['vqv'], 0.05)

    def test_is_zeroed_for_viewers_at_or_above_the_follower_threshold(self):
        video = make_candidate(video_duration_secs=30)
        limit = cr.MAX_FOLLOWERS_THRESHOLD
        self.assertClose(cr.effective_weights(
            video, ctx(viewer_follower_count=limit - 1))['vqv'], 0.05)
        self.assertEqual(cr.effective_weights(
            video, ctx(viewer_follower_count=limit))['vqv'], 0)

    def test_gives_the_same_answer_with_and_without_extracted_features(self):
        candidate = make_candidate(video_duration_secs=30)
        c = ctx(viewer_follower_count=10)
        features = cr.extract_ranking_features(candidate, c)
        self.assertEqual(cr.effective_weights(candidate, c, features),
                         cr.effective_weights(candidate, c))


class TestMeasuredPriors(Base):
    def p(self, **options):
        return cr.predict_actions(make_candidate(**options), ctx())

    def test_discounts_bot_authors_on_the_positive_heads(self):
        human = self.p(bot=False)
        bot = self.p(bot=True)
        for key in ('favorite', 'reply', 'retweet'):
            self.assertLess(bot[key], human[key], key)

    def test_does_not_let_bot_ness_inflate_the_negative_heads(self):
        human = self.p(bot=False)
        bot = self.p(bot=True)
        self.assertAlmostEqual(bot['not_interested'],
                               human['not_interested'], places=12)
        self.assertAlmostEqual(bot['mute_author'], human['mute_author'],
                               places=12)

    def test_keeps_a_zero_engagement_bot_post_out_of_the_rejection_band(
            self):
        bot_post = raw(make_candidate(bot=True, favourites=0,
                                      in_network=False))
        self.assertGreater(bot_post, cr.NEGATIVE_SCORES_OFFSET)

    def test_discounts_link_posts_instead_of_boosting_them(self):
        self.assertLess(self.p(card=True)['favorite'],
                        self.p(card=False)['favorite'])
        self.assertLess(self.p(card=True)['share'],
                        self.p(card=False)['share'])

    def test_rewards_hashtags_and_media(self):
        self.assertGreater(self.p(tags=['art'])['favorite'],
                           self.p(tags=[])['favorite'])
        self.assertGreater(self.p(images=1)['favorite'],
                           self.p(images=0)['favorite'])

    def test_fades_the_priors_out_as_observed_engagement_grows(self):
        def gap(favourites):
            return (self.p(bot=False, favourites=favourites)['favorite']
                    / self.p(bot=True, favourites=favourites)['favorite'])
        silent = gap(0)
        popular = gap(5_000)
        self.assertGreater(silent, 1)
        self.assertLess(popular, silent)
        self.assertAlmostEqual(popular, 1, places=1)

    def test_the_shrink_formula_matches_the_stated_definition(self):
        # shrink(prior) = 1 + (prior - 1) * (1 - popularity)
        c = make_candidate(bot=True, favourites=0)
        silent = cr.predict_actions(c, ctx())['favorite']
        expected = (cr.BASE_RATES['favorite']
                    * cr.lift(0, 8) * 0.4)
        self.assertClose(silent, expected, 12)


class TestBotFollowPrior(Base):
    def follow(self, **options):
        return cr.predict_actions(
            make_candidate(in_network=False, **options),
            ctx())['follow_author']

    def test_discounts_a_bot_author_independent_of_popularity(self):
        prior = cr.DEFAULT_RANKING_PARAMS.bot_follow_prior
        for favourites in (0, 5_000):
            gap = (self.follow(bot=False, favourites=favourites)
                   / self.follow(bot=True, favourites=favourites))
            self.assertClose(gap, 1 / prior)

    def test_is_sweepable(self):
        self.assertLess(self.follow(bot=True), self.follow(bot=False))
        neutral = ctx(params={'bot_follow_prior': 1})
        human = cr.predict_actions(make_candidate(
            in_network=False, bot=False), neutral)['follow_author']
        bot = cr.predict_actions(make_candidate(
            in_network=False, bot=True), neutral)['follow_author']
        self.assertAlmostEqual(bot, human, places=12)


class TestPredictActions(Base):
    def test_returns_probabilities_in_the_unit_interval(self):
        cases = [
            make_candidate(),
            make_candidate(favourites=10**6, reblogs=10**6, replies=10**6),
            make_candidate(in_network=False, bot=True, followers=0,
                           following=9999, note=''),
            make_candidate(age_ms=47 * HOUR, sensitive=True, images=4,
                           video_duration_secs=60, card=True),
            make_candidate(boost_of={'favourites': 500}),
        ]
        for candidate in cases:
            probabilities = cr.predict_actions(candidate, ctx())
            for key in ACTION_KEYS:
                self.assertGreaterEqual(probabilities[key], 0, key)
                self.assertLessEqual(probabilities[key], 1, key)

    def test_gates_the_link_and_media_heads_on_the_media_existing(self):
        plain = cr.predict_actions(make_candidate(), ctx())
        self.assertEqual(plain['open_link'], 0)
        self.assertEqual(plain['photo_expand'], 0)
        self.assertEqual(plain['video_open'], 0)
        self.assertEqual(plain['vqv'], 0)

        rich = cr.predict_actions(make_candidate(
            card=True, images=2, video_duration_secs=30), ctx())
        self.assertGreater(rich['open_link'], 0)
        self.assertGreater(rich['photo_expand'], 0)
        self.assertGreater(rich['video_open'], 0)
        self.assertGreater(rich['vqv'], 0)
        self.assertLess(rich['vqv'], rich['video_open'])

    def test_lifts_favourites_and_replies_with_author_affinity(self):
        cold = cr.predict_actions(make_candidate(author_id='a1'), ctx())
        warm = cr.predict_actions(
            make_candidate(author_id='a1'),
            ctx(cr.ForYouSignals(author_affinity={'a1': 10, 'other': 1})))
        self.assertGreater(warm['favorite'], cold['favorite'])
        self.assertGreater(warm['reply'], cold['reply'])
        self.assertLess(warm['not_interested'], cold['not_interested'])

    def test_does_not_give_a_zero_engagement_post_a_large_reply_term(self):
        empty = cr.predict_actions(
            make_candidate(favourites=0, replies=0), ctx())
        engaged = cr.predict_actions(
            make_candidate(favourites=200, replies=40), ctx())
        self.assertGreater(engaged['reply'], empty['reply'] * 2.5)

    def test_raises_the_negative_heads_for_a_spam_shaped_stranger(self):
        normal = cr.predict_actions(make_candidate(in_network=False), ctx())
        spam = cr.predict_actions(make_candidate(
            in_network=False, followers=3, following=9000, note='',
            sensitive=True), ctx())
        self.assertGreater(spam['not_interested'], normal['not_interested'])
        self.assertGreater(spam['block_author'], normal['block_author'])
        self.assertGreater(spam['report'], normal['report'])

    def test_keeps_report_orders_of_magnitude_below_favorite(self):
        p = cr.predict_actions(make_candidate(), ctx())
        self.assertLess(p['report'], p['favorite'] / 1000)

    def test_each_head_formula_for_a_plain_post(self):
        # A plain post: engagement 0, no tags, no media, age 1 h.
        p = cr.predict_actions(make_candidate(), ctx())
        b = cr.BASE_RATES
        self.assertClose(p['favorite'], b['favorite'], 12)
        self.assertClose(p['reply'], b['reply'], 12)
        self.assertClose(p['retweet'], b['retweet'], 12)
        self.assertClose(p['quote'], b['retweet'] * 0.12, 12)
        self.assertClose(p['share'], b['share'], 12)
        self.assertClose(p['click'], b['click'], 12)
        self.assertClose(p['not_interested'],
                         b['not_interested'], 12)
        self.assertClose(p['report'], b['report'], 12)
        self.assertEqual(p['follow_author'], 0)
        self.assertClose(p['not_dwelled'],
                         b['not_dwelled'] * (1 + 1 / 24) * 1.2, 12)

    def test_question_poll_and_boost_multipliers_on_reply_and_favorite(self):
        base = cr.predict_actions(make_candidate(), ctx())
        question = cr.predict_actions(
            make_candidate(content='<p>why?</p>'), ctx())
        self.assertClose(question['reply'] / base['reply'], 1.4, 12)
        poll = cr.predict_actions(make_candidate(poll=True), ctx())
        self.assertClose(poll['reply'] / base['reply'], 1.3, 12)
        boosted = cr.predict_actions(make_candidate(
            boost_of={}), ctx())
        self.assertClose(boosted['favorite'] / base['favorite'], 0.9, 12)
        self.assertClose(boosted['reply'] / base['reply'], 0.5, 12)
        self.assertClose(boosted['retweet'] / base['retweet'], 0.5, 12)
        reply = cr.predict_actions(
            make_candidate(in_reply_to_id='p'), ctx())
        self.assertClose(reply['retweet'] / base['retweet'], 0.6, 12)

    def test_click_rises_with_a_spoiler_and_long_text(self):
        base = cr.predict_actions(make_candidate(), ctx())['click']
        cw = cr.predict_actions(
            make_candidate(spoiler_text='cw'), ctx())['click']
        self.assertClose(cw / base, 1.8, 12)
        long_ = cr.predict_actions(make_candidate(
            content='<p>' + 'word ' * 300 + '</p>'), ctx())['click']
        self.assertGreater(long_, base)

    def test_photo_expand_rises_for_galleries_and_hidden_images(self):
        one = cr.predict_actions(make_candidate(images=1), ctx())
        two = cr.predict_actions(make_candidate(images=2), ctx())
        cw = cr.predict_actions(
            make_candidate(images=1, sensitive=True), ctx())
        self.assertClose(two['photo_expand'] / one['photo_expand'], 1.3, 12)
        self.assertClose(cw['photo_expand'] / one['photo_expand'], 1.4, 12)

    def test_block_and_report_double_and_triple_for_unflagged_sensitive(
            self):
        base = cr.predict_actions(make_candidate(), ctx())
        sens = cr.predict_actions(make_candidate(sensitive=True), ctx())
        cw = cr.predict_actions(
            make_candidate(sensitive=True, spoiler_text='cw'), ctx())
        self.assertClose(sens['block_author'] / base['block_author'], 2, 12)
        self.assertClose(sens['report'] / base['report'], 3, 12)
        self.assertClose(cw['block_author'] / base['block_author'], 1, 12)
        self.assertClose(sens['not_interested'] / base['not_interested'],
                         1.2, 12)

    def test_follow_author_rewards_verified_reach_and_discounts_replies(
            self):
        def follow(**options):
            return cr.predict_actions(
                make_candidate(in_network=False, **options),
                ctx())['follow_author']
        self.assertClose(follow(verified=True) / follow(), 1.5, 12)
        self.assertClose(follow(in_reply_to_id='x') / follow(), 0.7, 12)
        self.assertGreater(follow(followers=40_000), follow(followers=10))

    def test_not_dwelled_is_capped(self):
        p = cr.predict_actions(make_candidate(
            age_ms=100_000 * HOUR, content='<p>x</p>'), ctx())
        self.assertLessEqual(p['not_dwelled'], 0.95)

    def test_a_tag_affinity_uses_the_strongest_signal_by_magnitude(self):
        resolver = cr.AffinityResolver(
            tag=lambda t: {'a': 0.2, 'b': -0.9, 'c': 0.5}[t])
        f = cr.extract_ranking_features(
            make_candidate(tags=['A', 'b', 'c']), ctx(affinity=resolver))
        self.assertEqual(f.tag_affinity, -0.9)


if __name__ == '__main__':
    unittest.main()
