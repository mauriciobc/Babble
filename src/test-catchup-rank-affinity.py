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

import dataclasses
import datetime
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
                                 cr, ctx, elk_ctx, ids, make_candidate, only_weights,
                                 pre_scored, raw)

class TestAffinityLifts(Base):
    """Every affinity channel enters each head with Elk's (max, min)."""

    # head, channel, sign, max, min. sign -1: the head reads -affinity.
    TABLE = (
        ('favorite', 'author', 1, 3, 0.4),
        ('favorite', 'tag', 1, 1.6, 0.7),
        ('favorite', 'booster', 1, 1.8, 0.7),
        ('reply', 'author', 1, 5, 0.3),
        ('reply', 'tag', 1, 1.4, 0.8),
        ('retweet', 'author', 1, 2.5, 0.5),
        ('retweet', 'booster', 1, 1.6, 0.8),
        ('click', 'author', 1, 2, 0.6),
        ('open_link', 'tag', 1, 1.5, 0.7),
        ('profile_click', 'author', 1, 1.5, 0.8),
        ('photo_expand', 'author', 1, 1.5, 0.8),
        ('video_open', 'author', 1, 1.5, 0.8),
        ('dwell', 'author', 1, 1.3, 0.85),
        ('follow_author', 'author', 1, 4, 0.3),
        ('follow_author', 'tag', 1, 2, 0.6),
        ('not_interested', 'author', -1, 4, 0.25),
        ('mute_author', 'author', -1, 5, 0.2),
        ('block_author', 'author', -1, 5, 0.2),
        ('report', 'author', -1, 3, 0.3),
        ('not_dwelled', 'author', -1, 1.6, 0.6),
    )

    def probe(self, channel, value):
        resolver = cr.AffinityResolver(**{channel: lambda _key: value})
        candidate = make_candidate(
            in_network=False, tags=['t'], card=True, images=1,
            video_duration_secs=30, boost_of=dict(
                tags=['t'], card=True, images=1, video_duration_secs=30))
        return cr.predict_actions(candidate, ctx(affinity=resolver))

    def test_each_head_lifts_by_its_max_and_min(self):
        for head, channel, sign, maximum, minimum in self.TABLE:
            base = self.probe(channel, 0)[head]
            self.assertGreater(base, 0, head)
            up = self.probe(channel, 1)[head] / base
            down = self.probe(channel, -1)[head] / base
            at_one, at_minus = (maximum, minimum) if sign > 0 else (
                minimum, maximum)
            self.assertAlmostEqual(up, at_one, places=9,
                                   msg=(head, channel, '+1'))
            self.assertAlmostEqual(down, at_minus, places=9,
                                   msg=(head, channel, '-1'))

    def test_booster_affinity_is_zero_for_a_post_that_is_no_boost(self):
        resolver = cr.AffinityResolver(booster=lambda _key: 1)
        plain = make_candidate(favourites=5)
        self.assertEqual(
            cr.extract_ranking_features(plain, ctx(affinity=resolver))
            .booster_affinity, 0)

    def test_the_other_heads_ignore_the_channel(self):
        base = self.probe('booster', 0)
        boosted = self.probe('booster', 1)
        for key in ACTION_KEYS:
            if key not in ('favorite', 'retweet', 'quote'):
                self.assertEqual(boosted[key], base[key], key)

    def test_the_language_prior_lifts_up_to_1_15_and_down_to_the_mismatch(
            self):
        def prior(value, language='en'):
            return cr.extract_ranking_features(
                make_candidate(language=language),
                ctx(affinity=cr.AffinityResolver(
                    language=lambda _key: value))).language_prior
        self.assertClose(prior(1), 1.15)
        self.assertClose(prior(0), 1)
        self.assertClose(prior(-1), 0.6)
        # a foreign language the viewer has engaged with is not penalised
        self.assertClose(prior(0.5, 'ja'), cr.lift(0.5, 1.15, 0.6))
        self.assertClose(prior(0, 'ja'), 0.6)


class TestFeatureBoundaries(Base):
    """Thresholds that a coarse test would let drift by one."""

    def f(self, **options):
        return cr.extract_ranking_features(make_candidate(**options), ctx())

    def test_whitespace_is_javascripts_not_pythons(self):
        expected = [0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680,
                    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F,
                    0x3000, 0xFEFF]
        self.assertEqual(cr._JS_WHITESPACE, ''.join(map(chr, expected)))
        for code in expected:
            char = chr(code)
            f = self.f(content='<p>a%sb</p>' % (char * 2))
            self.assertEqual(f.text_length, 3, hex(ord(char)))
        # ZWSP, NEL and the C0 separators are not JavaScript whitespace
        for char in ('\u200b', '\x85', '\x1c'):
            f = self.f(content='<p>a%sb</p>' % (char * 2))
            self.assertEqual(f.text_length, 4, hex(ord(char)))

    def test_density_of_a_single_engagement_is_one(self):
        f = self.f(replies=1)
        self.assertEqual(f.reply_density, 1)
        self.assertEqual(f.reblog_density, 0)
        f = self.f(reblogs=1)
        self.assertEqual(f.reblog_density, 1)

    def test_log_norm_of_fractions(self):
        self.assertEqual(cr.log_norm(0.5, 0.5), 1)
        self.assertAlmostEqual(cr.log_norm(1, 1), 1)

    def test_the_follow_ratio_thresholds(self):
        def spam(followers, following):
            return self.f(followers=followers,
                          following=following).spamminess
        self.assertEqual(spam(0, 20), 0)
        self.assertClose(spam(0, 21), 0.6)
        self.assertClose(spam(99, 1000), 0.6)
        self.assertClose(spam(100, 1000), 0.3)
        self.assertClose(spam(499, 1000), 0.3)
        self.assertEqual(spam(500, 1000), 0)

    def test_click_rises_with_text_length_up_to_1_5x(self):
        def click(text_length):
            return cr.predict_actions(make_candidate(
                content='<p>' + 'x' * text_length + '</p>'), ctx())['click']
        base = click(280)
        self.assertClose(click(281) / base, 1 + 0.5 / 700)
        self.assertClose(click(630) / base, 1.25)
        self.assertClose(click(980) / base, 1.5)
        self.assertClose(click(5000) / base, 1.5)

    def test_not_dwelled_rises_for_posts_under_40_characters(self):
        def not_dwelled(text_length):
            return cr.predict_actions(make_candidate(
                content='<p>' + 'x' * text_length + '</p>'),
                ctx())['not_dwelled']
        self.assertClose(not_dwelled(39) / not_dwelled(40), 1.2)
        self.assertEqual(not_dwelled(40), not_dwelled(400))

    def test_the_vqv_gate_is_strictly_longer_than_10_seconds(self):
        def vqv(seconds, followers=0):
            return cr.effective_weights(
                make_candidate(video_duration_secs=seconds),
                ctx(viewer_follower_count=followers))['vqv']
        self.assertEqual(vqv(10), 0)
        self.assertEqual(vqv(10.001), 0.05)
        self.assertEqual(vqv(30, 9_999), 0.05)
        self.assertEqual(vqv(30, 10_000), 0)

    def test_the_new_user_rule_needs_five_follows(self):
        def factor(following):
            return cr.effective_oon_factor(ctx(
                viewer_account_age_ms=HOUR, viewer_following_count=following,
                params={'new_user_age_threshold_ms': 24 * HOUR}))
        self.assertEqual(factor(4), 0.75)
        self.assertEqual(factor(5), 0.00001)
        old = cr.effective_oon_factor(ctx(
            viewer_account_age_ms=24 * HOUR, viewer_following_count=50,
            params={'new_user_age_threshold_ms': 24 * HOUR}))
        self.assertEqual(old, 0.75)

    def test_reasons_below_a_millionth_stay_out_of_the_trace(self):
        def labels(weight):
            scored = cr.score_candidate(
                make_candidate(), ctx(weights=only_weights(favorite=weight)))
            return [r.label for r in scored.reasons]
        # P(favorite) of a plain post is 0.03
        self.assertIn('favorite', labels(3.5e-5))     # term 1.05e-6
        self.assertNotIn('favorite', labels(3.1e-5))  # term 9.3e-7

    def test_timestamps_truncate_to_milliseconds(self):
        self.assertEqual(
            cr.parse_timestamp_ms('2026-08-16T12:00:00.500499Z'),
            1786881600500)
        moment = datetime.datetime(2026, 8, 16, 12, 0, 0, 500499,
                                   tzinfo=datetime.timezone.utc)
        self.assertEqual(cr._to_ms(moment), 1786881600500)
        naive = moment.replace(tzinfo=None)
        self.assertEqual(cr._to_ms(naive), 1786881600500)


class TestBaseRates(Base):
    def test_keeps_the_negative_side_well_under_the_positive_side(self):
        pos = neg = 0
        for key in ACTION_KEYS:
            term = cr.BASE_RATES[key] * cr.X_WEIGHTS[key]
            if term >= 0:
                pos += term
            else:
                neg -= term
        self.assertGreater(pos, neg * 4)

    def test_base_rate_overrides_are_merged(self):
        base = cr.predict_actions(make_candidate(), ctx())
        doubled = cr.predict_actions(
            make_candidate(), ctx(base_rates={'favorite': 0.06}))
        self.assertClose(doubled['favorite'], 2 * base['favorite'], 12)
        self.assertEqual(doubled['reply'], base['reply'])


# endregion


# region scoring


class TestScoreCandidate(Base):
    def test_is_exactly_the_sum_of_one_head_when_the_others_are_zeroed(self):
        context = ctx(weights=only_weights(favorite=1))
        candidate = make_candidate(favourites=40, age_ms=0)
        scored = cr.score_candidate(candidate, context)
        self.assertIsNotNone(scored.probabilities)
        self.assertClose(cr.context_multiplier(candidate, context), 1)
        self.assertClose(
            scored.raw_score,
            scored.probabilities['favorite'] + cr.NEGATIVE_SCORES_OFFSET,
            12)

    def test_adds_two_heads_linearly(self):
        context = ctx(weights=only_weights(favorite=2, reply=3))
        candidate = make_candidate(favourites=40, replies=10, age_ms=0)
        scored = cr.score_candidate(candidate, context)
        p = scored.probabilities
        self.assertClose(
            scored.raw_score,
            2 * p['favorite'] + 3 * p['reply'] + cr.NEGATIVE_SCORES_OFFSET,
            12)

    def test_takes_the_negative_branch_when_only_a_negative_weight_is_active(
            self):
        context = ctx(weights=only_weights(report=-234))
        candidate = make_candidate(in_network=False)
        scored = cr.score_candidate(candidate, context)
        combined = -234 * scored.probabilities['report']
        self.assertLess(combined, 0)
        sums = cr.weight_sums(only_weights(report=-234))
        self.assertClose(scored.raw_score, cr.offset_score(combined, sums),
                         12)
        self.assertLess(scored.raw_score, cr.NEGATIVE_SCORES_OFFSET)

    def test_collapses_to_zero_when_every_weight_is_zero(self):
        scored = cr.score_candidate(make_candidate(),
                                    ctx(weights=only_weights()))
        self.assertEqual(scored.raw_score, 0)

    def test_does_not_mutate_its_input(self):
        candidate = make_candidate()
        scored = cr.score_candidate(candidate, ctx())
        self.assertIsNone(candidate.raw_score)
        self.assertIsNone(candidate.probabilities)
        self.assertIsNot(scored, candidate)

    def test_records_a_reason_per_contributing_head_and_the_multipliers(self):
        scored = cr.score_candidate(make_candidate(favourites=50), ctx())
        labels = [r.label for r in scored.reasons]
        self.assertIn('favorite', labels)
        self.assertIn('reply', labels)
        self.assertIn('freshness', labels)
        contributions = [r for r in scored.reasons
                         if r.label not in ('freshness', 'language')]
        for a, b in zip(contributions, contributions[1:], strict=False):
            self.assertGreaterEqual(abs(a.value), abs(b.value))
        # reasons are (label, value) pairs
        label, value = scored.reasons[0]
        self.assertIsInstance(label, str)
        self.assertIsInstance(value, float)

    def test_a_language_reason_appears_only_for_a_mismatch(self):
        plain = cr.score_candidate(make_candidate(), ctx())
        foreign = cr.score_candidate(make_candidate(language='ja'), ctx())
        self.assertNotIn('language', [r.label for r in plain.reasons])
        self.assertIn(('language', 0.6),
                      [tuple(r) for r in foreign.reasons])

    def test_reproduces_the_composition_of_the_public_pieces(self):
        cases = [
            make_candidate(),
            make_candidate(favourites=120, reblogs=40, replies=12),
            make_candidate(age_ms=40 * HOUR),
            make_candidate(video_duration_secs=30),
            make_candidate(images=3, tags=['baking', 'sourdough']),
            make_candidate(boost_of={'favourites': 9}),
            make_candidate(in_reply_to_id='parent-1'),
        ]
        c = ctx()
        for candidate in cases:
            scored = cr.score_candidate(candidate, c)
            probabilities = cr.predict_actions(candidate, c)
            weights = cr.effective_weights(candidate, c)
            pos = neg = 0
            for key in ACTION_KEYS:
                term = probabilities[key] * weights[key]
                if term >= 0:
                    pos += term
                else:
                    neg -= term
            net = pos - neg
            scaled = (cr.context_multiplier(candidate, c) * net
                      if net >= 0 else net)
            self.assertEqual(
                scored.raw_score,
                cr.offset_score(scaled,
                                cr.weight_sums(cr.MASTODON_WEIGHTS)))


class TestNegativeBand(Base):
    def penalty_ctx(self, table):
        return ctx(affinity=cr.AffinityResolver(
            author_penalty=lambda i: table.get(i, 0)))

    def test_keeps_a_realistic_slate_entirely_off_the_floor(self):
        slate = [
            make_candidate(id='p1', author_id='a', age_ms=0.5 * HOUR),
            make_candidate(id='p2', author_id='b', favourites=2),
            make_candidate(id='p3', author_id='c', replies=1,
                           age_ms=3 * HOUR),
            make_candidate(id='p4', author_id='d', favourites=12,
                           reblogs=3, age_ms=5 * HOUR),
            make_candidate(id='p5', author_id='e', in_network=False,
                           favourites=240, reblogs=80, replies=30,
                           age_ms=2 * HOUR),
            make_candidate(id='p6', author_id='f', in_network=False,
                           favourites=1, age_ms=9 * HOUR),
            make_candidate(id='p7', author_id='g', bot=True,
                           age_ms=20 * HOUR),
            make_candidate(id='p8', author_id='h', in_network=False,
                           favourites=5, age_ms=30 * HOUR, language='de'),
            make_candidate(id='p9', author_id='i', favourites=40,
                           replies=8, age_ms=7 * HOUR),
            make_candidate(id='p10', author_id='j', in_network=False,
                           followers=5, following=4000, note=''),
            make_candidate(id='p11', author_id='k', favourites=900,
                           reblogs=200, age_ms=26 * HOUR),
            make_candidate(id='p12', author_id='l', favourites=3,
                           age_ms=12 * HOUR),
        ]
        ranked = cr.rank_candidates(slate, ctx())
        on_floor = [c for c in ranked
                    if c.raw_score <= cr.NEGATIVE_SCORES_OFFSET]
        self.assertEqual(ids(on_floor), [])
        self.assertEqual(len({c.score for c in ranked}), len(ranked))
        self.assertEqual(ranked[0].status['id'], 'p5')

    def test_orders_dismissed_muted_and_irredeemable_strictly(self):
        context = self.penalty_ctx({'dismissed': 0.5, 'muted': 1,
                                    'worst': 1})
        spammy = dict(in_network=False, followers=3, following=9000,
                      note='', bot=True, sensitive=True)
        mediocre = raw(make_candidate(author_id='ok', favourites=3), context)
        spammy_stranger = raw(
            make_candidate(author_id='spam', **spammy), context)
        dismissed = raw(
            make_candidate(author_id='dismissed', favourites=3), context)
        muted = raw(make_candidate(author_id='muted', favourites=3),
                    context)
        worst = raw(make_candidate(author_id='worst', **spammy), context)

        self.assertGreater(mediocre, spammy_stranger)
        self.assertGreater(spammy_stranger, dismissed)
        self.assertGreater(dismissed, muted)
        self.assertGreater(muted, worst)
        self.assertGreater(worst, 0)
        self.assertLess(dismissed, cr.NEGATIVE_SCORES_OFFSET)
        self.assertGreater(spammy_stranger, cr.NEGATIVE_SCORES_OFFSET)

    def test_reads_the_penalty_into_not_interested_and_mute_author(self):
        p = cr.predict_actions(make_candidate(author_id='d'),
                               self.penalty_ctx({'d': 0.75}))
        self.assertClose(p['not_interested'], 0.75)
        self.assertClose(p['mute_author'], 0.75)

    def test_picks_up_muted_authors_and_dismissed_posts(self):
        muted = cr.predict_actions(
            make_candidate(author_id='m1'),
            ctx(cr.ForYouSignals(muted_for_you=['m1'])))
        self.assertEqual(muted['not_interested'], 1)
        self.assertEqual(muted['mute_author'], 1)

        dismissed = cr.predict_actions(
            make_candidate(id='x1'),
            ctx(cr.ForYouSignals(not_interested=['x1'])))
        self.assertEqual(dismissed['not_interested'], 1)
        # A dismissed post is no verdict on the whole author.
        self.assertLess(dismissed['mute_author'], 1)

    def test_a_dismissed_post_does_not_score_like_a_muted_author(self):
        dismissed_post = make_candidate(id='post-x', author_id='fine-author',
                                        favourites=3)
        muted_post = make_candidate(id='post-y', author_id='muted-author',
                                    favourites=3)
        s_dismissed = ctx(cr.ForYouSignals(not_interested=['post-x']))
        s_muted = ctx(cr.ForYouSignals(muted_for_you=['muted-author']))
        self.assertEqual(cr.predict_actions(
            dismissed_post, s_dismissed)['not_interested'], 1)
        self.assertEqual(cr.predict_actions(
            muted_post, s_muted)['not_interested'], 1)
        self.assertLess(cr.predict_actions(
            dismissed_post, s_dismissed)['mute_author'], 1)
        self.assertEqual(cr.predict_actions(
            muted_post, s_muted)['mute_author'], 1)
        a = raw(dismissed_post, s_dismissed)
        b = raw(muted_post, s_muted)
        self.assertNotAlmostEqual(a, b, places=6)
        self.assertGreater(a, b)


# endregion

# region adjustments


class TestDiversityMultiplier(Base):
    def test_matches_one_minus_floor_times_decay_to_the_k_plus_floor(self):
        p = cr.DEFAULT_RANKING_PARAMS
        d, f = p.author_diversity_decay, p.author_diversity_floor
        self.assertClose(cr.diversity_multiplier(0, d, f), 1)
        self.assertClose(cr.diversity_multiplier(1, d, f), 0.625)
        self.assertClose(cr.diversity_multiplier(2, d, f), 0.4375)
        self.assertAlmostEqual(cr.diversity_multiplier(20, d, f), f,
                               places=5)

    def test_never_falls_below_the_floor(self):
        for k in range(50):
            self.assertGreaterEqual(
                cr.diversity_multiplier(k, 0.5, 0.25), 0.25)


class TestAuthorDiversity(Base):
    no_oon = ctx(params={'oon_rescore_in_network_replies_reblogs': False})

    def test_demotes_the_2nd_and_3rd_post_but_leaves_the_first(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A'),
            pre_scored(9, id='s2', author_id='B'),
            pre_scored(8, id='s3', author_id='A'),
            pre_scored(7, id='s4', author_id='A'),
        ], self.no_oon)
        self.assertClose(adjusted[0].score, 10)
        self.assertClose(adjusted[1].score, 9)
        self.assertClose(adjusted[2].score, 8 * 0.625)
        self.assertClose(adjusted[3].score, 7 * 0.4375)

    def test_counts_k_in_score_order_not_array_order(self):
        adjusted = cr.apply_adjustments([
            pre_scored(5, id='s1', author_id='A'),
            pre_scored(9, id='s2', author_id='B'),
            pre_scored(10, id='s3', author_id='A'),
        ], self.no_oon)
        self.assertClose(adjusted[2].score, 10)
        self.assertClose(adjusted[0].score, 5 * 0.625)

    def test_keys_on_the_content_author_so_five_boosts_still_decay(self):
        boosts = [
            pre_scored(10, id='b%d' % i, author_id=booster,
                       boost_of={'author_id': 'ORIGINAL'})
            for i, booster in enumerate(['x1', 'x2', 'x3', 'x4', 'x5'])]
        other = pre_scored(9, id='other', author_id='Z')
        adjusted = cr.apply_adjustments([*boosts, other], self.no_oon)
        self.assertClose(adjusted[0].score, 10)
        self.assertClose(adjusted[1].score, 10 * 0.625)
        self.assertClose(adjusted[2].score, 10 * 0.4375)
        by_score = sorted(adjusted, key=lambda c: -c.score)
        self.assertEqual(by_score[1].status['id'], 'other')

    def test_can_reorder_the_slate(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A'),
            pre_scored(9.5, id='s2', author_id='A'),
            pre_scored(9, id='s3', author_id='A'),
            pre_scored(7, id='s4', author_id='B'),
        ], self.no_oon)
        ranked = sorted(adjusted, key=lambda c: -c.score)
        self.assertEqual(ids(ranked), ['s1', 's4', 's2', 's3'])

    def test_is_a_no_op_when_disabled(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A'),
            pre_scored(8, id='s2', author_id='A'),
        ], ctx(params={'enable_author_diversity': False,
                       'oon_rescore_in_network_replies_reblogs': False}))
        self.assertClose(adjusted[1].score, 8)

    def test_records_the_multiplier_as_a_reason(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A'),
            pre_scored(8, id='s2', author_id='A'),
        ], self.no_oon)
        self.assertIn(('author_diversity', 0.625),
                      [tuple(r) for r in adjusted[1].reasons])
        self.assertNotIn('author_diversity',
                         [r.label for r in adjusted[0].reasons])

    def test_ties_walk_in_input_order(self):
        adjusted = cr.apply_adjustments([
            pre_scored(5, id='s1', author_id='A'),
            pre_scored(5, id='s2', author_id='A'),
        ], self.no_oon)
        self.assertClose(adjusted[0].score, 5)
        self.assertClose(adjusted[1].score, 5 * 0.625)

    def test_an_unscored_candidate_counts_as_zero(self):
        adjusted = cr.apply_adjustments([make_candidate()], self.no_oon)
        self.assertEqual(adjusted[0].score, 0)

    def test_does_not_mutate_its_input(self):
        candidate = pre_scored(10, id='s1', author_id='A')
        cr.apply_adjustments([candidate], ctx())
        self.assertIsNone(candidate.score)


class TestOonApplies(Base):
    params = cr.DEFAULT_RANKING_PARAMS

    def test_is_true_for_anything_out_of_network(self):
        self.assertTrue(cr.oon_applies(
            make_candidate(in_network=False), self.params))

    def test_is_true_for_in_network_replies_and_boosts(self):
        self.assertTrue(cr.oon_applies(make_candidate(
            in_network=True, in_reply_to_id='x'), self.params))
        self.assertTrue(cr.oon_applies(make_candidate(
            in_network=True, boost_of={}), self.params))

    def test_is_false_for_an_in_network_original_post(self):
        self.assertFalse(cr.oon_applies(
            make_candidate(in_network=True), self.params))

    def test_spares_in_network_replies_and_boosts_when_the_flag_is_off(self):
        off = dataclasses.replace(
            self.params, oon_rescore_in_network_replies_reblogs=False)
        self.assertFalse(cr.oon_applies(make_candidate(
            in_network=True, in_reply_to_id='x'), off))
        self.assertTrue(cr.oon_applies(
            make_candidate(in_network=False), off))


class TestOutOfNetworkDiscount(Base):
    def test_multiplies_out_of_network_posts_by_the_oon_factor(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A', in_network=True),
            pre_scored(10, id='s2', author_id='B', in_network=False),
        ], ctx())
        self.assertClose(adjusted[0].score, 10)
        self.assertClose(adjusted[1].score, 10 * 0.75)

    def test_also_discounts_replies_and_boosts_from_followed_accounts(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A', in_network=True),
            pre_scored(10, id='s2', author_id='B', in_network=True,
                       in_reply_to_id='x'),
            pre_scored(10, id='s3', author_id='C', in_network=True,
                       boost_of={'author_id': 'Z'}),
        ], ctx())
        self.assertClose(adjusted[0].score, 10)
        self.assertClose(adjusted[1].score, 7.5)
        self.assertClose(adjusted[2].score, 7.5)

    def test_honours_a_custom_factor_and_records_it_as_a_reason(self):
        adjusted = cr.apply_adjustments(
            [pre_scored(10, id='s1', author_id='A', in_network=False)],
            ctx(params={'oon_weight_factor': 0.5}))
        self.assertClose(adjusted[0].score, 5)
        self.assertIn(('out_of_network', 0.5),
                      [tuple(r) for r in adjusted[0].reasons])

    def test_stacks_with_the_author_diversity_multiplier(self):
        adjusted = cr.apply_adjustments([
            pre_scored(10, id='s1', author_id='A', in_network=False),
            pre_scored(8, id='s2', author_id='A', in_network=False),
        ], ctx())
        self.assertClose(adjusted[1].score, 8 * 0.625 * 0.75)


class TestEffectiveOonFactor(Base):
    def test_is_the_oon_factor_by_default(self):
        self.assertClose(cr.effective_oon_factor(ctx(
            viewer_account_age_ms=0, viewer_following_count=100)), 0.75)

    def test_collapses_for_a_new_viewer_with_enough_follows(self):
        self.assertClose(cr.effective_oon_factor(ctx(
            viewer_account_age_ms=HOUR, viewer_following_count=10,
            params={'new_user_age_threshold_ms': 24 * HOUR})), 0.00001, 12)

    def test_does_not_apply_to_a_new_viewer_who_follows_almost_nobody(self):
        self.assertClose(cr.effective_oon_factor(ctx(
            viewer_account_age_ms=HOUR, viewer_following_count=2,
            params={'new_user_age_threshold_ms': 24 * HOUR})), 0.75)


# endregion

# region boosts


class TestBoostsJudgedByContent(Base):
    def boost_of_age(self, age_ms, favourites):
        return make_candidate(
            id='b', author_id='booster', age_ms=0.1 * HOUR,
            boost_of={'id': 'inner', 'author_id': 'ORIGINAL',
                      'age_ms': age_ms, 'favourites': favourites})

    def test_uses_the_contents_age_for_freshness_and_velocity(self):
        zombie = self.boost_of_age(700 * 24 * HOUR, 9_000)
        genuine = make_candidate(id='real', author_id='R', in_network=False,
                                 favourites=300, age_ms=2 * HOUR)
        # With Elk's cut a two-year-old post sits on the floor (Catch Up's
        # range bounds the candidates instead, see TestCatchUpDeviations).
        self.assertLessEqual(raw(zombie, elk_ctx()),
                             cr.NEGATIVE_SCORES_OFFSET)
        self.assertGreater(raw(genuine, elk_ctx()),
                           raw(zombie, elk_ctx()) * 50)
        f = cr.extract_ranking_features(zombie, ctx())
        self.assertGreater(f.age_hours, 1000)

    def test_scores_a_recent_post_boosted_now_on_its_own_age(self):
        recent = self.boost_of_age(2 * HOUR, 300)
        f = cr.extract_ranking_features(recent, ctx())
        self.assertAlmostEqual(f.age_hours, 2, places=6)
        self.assertGreater(raw(recent),
                           raw(self.boost_of_age(30 * HOUR, 300)))

    def test_reads_engagement_from_the_boosted_post(self):
        self.assertGreater(raw(self.boost_of_age(2 * HOUR, 300)),
                           raw(self.boost_of_age(2 * HOUR, 0)))

    def test_lifts_a_boost_from_a_curator_the_viewer_engages_with(self):
        candidate = self.boost_of_age(2 * HOUR, 100)
        cold = raw(candidate)
        warm = raw(candidate,
                   ctx(affinity=cr.AffinityResolver(booster=lambda _: 1)))
        self.assertGreater(warm, cold)
        original = make_candidate(favourites=100, age_ms=2 * HOUR)
        self.assertClose(
            raw(original,
                ctx(affinity=cr.AffinityResolver(booster=lambda _: 1))),
            raw(original), 12)

    def test_reads_media_and_duration_from_the_boosted_post(self):
        candidate = make_candidate(
            boost_of={'video_duration_secs': 30, 'images': 1})
        f = cr.extract_ranking_features(candidate, ctx())
        self.assertTrue(f.is_boost)
        self.assertTrue(f.has_video)
        self.assertTrue(f.has_image)
        self.assertEqual(f.video_duration_ms, 30_000)


# endregion


# region pipeline


if __name__ == '__main__':
    unittest.main()
