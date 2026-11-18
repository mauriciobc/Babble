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
import math
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import types
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.catchup import rank as _rank  # noqa: E402


# region weights

from testkit.catchuprank import (ACTION_KEYS, HOUR, Base,  # noqa: E402
                                 cr, ctx, elk_ctx, ids, iso_ago,
                                 make_candidate, make_status, raw)
from testkit.catchuprank_golden import GOLDEN  # noqa: E402

class TestTieBreakIds(Base):
    """Equal scores break on the newer id first, and ids of different
    digit lengths compare as numbers."""

    def test_longer_id_is_newer(self):
        def scored(id_):
            return types.SimpleNamespace(score=1.0, status={'id': id_})
        ordered = sorted(
            [scored('99999999'), scored('100000001')],
            key=__import__('functools').cmp_to_key(_rank._by_score_then_id))
        self.assertEqual([c.status['id'] for c in ordered],
                         ['100000001', '99999999'])


class TestWeights(Base):
    def test_x_weights_match_the_production_defaults(self):
        self.assertEqual(dict(cr.X_WEIGHTS), {
            'favorite': 0.5, 'reply': 5.0, 'retweet': 1.0, 'quote': 5.0,
            'share': 2.0, 'click': 0.4, 'open_link': 0.2,
            'profile_click': 0.0, 'photo_expand': 0.05, 'video_open': 0.05,
            'vqv': 0.05, 'dwell': 0.0, 'follow_author': 4.0,
            'not_interested': -43.2, 'mute_author': -58.8,
            'block_author': -31.2, 'report': -234.0, 'not_dwelled': -0.02,
        })

    def test_covers_exactly_the_keys_of_the_action_probabilities(self):
        probabilities = cr.predict_actions(make_candidate(), ctx())
        self.assertEqual(sorted(probabilities), sorted(ACTION_KEYS))
        self.assertEqual(sorted(cr.BASE_RATES), sorted(ACTION_KEYS))
        self.assertEqual(list(cr.ACTIONS), ACTION_KEYS)

    def test_mastodon_weights_differ_from_x_in_exactly_two_heads(self):
        changed = [k for k in ACTION_KEYS
                   if cr.MASTODON_WEIGHTS[k] != cr.X_WEIGHTS[k]]
        self.assertEqual(sorted(changed), ['quote', 'share'])
        self.assertEqual(cr.MASTODON_WEIGHTS['quote'], 1.0)
        self.assertEqual(cr.MASTODON_WEIGHTS['share'], 0.5)

    def test_negative_side_is_exactly_as_x_calibrated_it(self):
        for key in ('not_interested', 'mute_author', 'block_author',
                    'report', 'not_dwelled'):
            self.assertEqual(cr.MASTODON_WEIGHTS[key], cr.X_WEIGHTS[key])
        self.assertClose(cr.weight_sums(cr.MASTODON_WEIGHTS).negative_sum,
                         367.22)

    def test_weight_sums_are_pinned(self):
        self.assertEqual(dict(cr.X_WEIGHT_SUMS), {
            'positive_sum': 43.32, 'negative_sum': 367.22,
            'total_sum': 410.54})

        ours = cr.weight_sums(cr.X_WEIGHTS)
        self.assertClose(ours.positive_sum, 18.25)
        self.assertClose(ours.negative_sum, 367.22)
        self.assertClose(ours.total_sum, 385.47)

        scored = cr.weight_sums(cr.MASTODON_WEIGHTS)
        self.assertClose(scored.positive_sum, 12.75)
        self.assertClose(scored.negative_sum, 367.22)
        self.assertClose(scored.total_sum, 379.97)

    def test_x_own_sums_cover_heads_we_do_not_model(self):
        self.assertNotAlmostEqual(
            cr.X_WEIGHT_SUMS['positive_sum'],
            cr.weight_sums(cr.X_WEIGHTS).positive_sum, places=6)
        self.assertAlmostEqual(
            cr.X_WEIGHT_SUMS['negative_sum'],
            cr.weight_sums(cr.X_WEIGHTS).negative_sum, places=10)

    def test_the_quote_surcharge_is_12_percent_not_60(self):
        given_retweet = 0.12
        self.assertClose(
            given_retweet * cr.X_WEIGHTS['quote'] / cr.X_WEIGHTS['retweet'],
            0.6)
        self.assertClose(
            given_retweet * cr.MASTODON_WEIGHTS['quote']
            / cr.MASTODON_WEIGHTS['retweet'], 0.12)

    def test_an_unconfigured_context_scores_with_mastodon_weights(self):
        candidate = make_candidate(favourites=40)
        self.assertClose(
            raw(candidate),
            raw(candidate, ctx(weights=dict(cr.MASTODON_WEIGHTS))), 12)
        self.assertNotAlmostEqual(
            raw(candidate), raw(candidate, ctx(weights=dict(cr.X_WEIGHTS))),
            places=6)

    def test_calibration_values_are_elks(self):
        p = cr.DEFAULT_RANKING_PARAMS
        self.assertEqual(
            (p.engagement_saturation, p.velocity_saturation,
             p.reply_saturation, p.follower_saturation,
             p.author_volume_saturation), (10_000, 500, 100, 50_000, 50_000))
        self.assertEqual(
            (p.favourite_coverage_remote, p.reblog_coverage_remote,
             p.reply_coverage_remote), (0.6, 0.93, 1))
        self.assertEqual(
            (p.bot_engagement_prior, p.bot_follow_prior,
             p.link_engagement_prior, p.media_engagement_prior,
             p.hashtag_engagement_prior), (0.4, 0.4, 0.7, 1.25, 1.3))
        self.assertEqual((p.recency_half_life_ms, p.recency_floor),
                         (12 * HOUR, 0.12))
        self.assertEqual(p.language_mismatch_prior, 0.6)
        self.assertEqual(
            (p.author_diversity_decay, p.author_diversity_floor,
             p.oon_weight_factor), (0.5, 0.25, 0.75))
        self.assertEqual(p.bidirectional_follow_reply_weight_boost, 15.0)
        self.assertEqual(cr.BASE_RATES['quote'], 0.0006)
        self.assertEqual(cr.BASE_RATES['retweet'], 0.018)
        self.assertEqual(cr.NEGATIVE_SCORES_OFFSET, 0.001)

    def test_the_new_author_boost_is_not_ported(self):
        names = {f.name for f in dataclasses.fields(cr.RankingParams)}
        self.assertFalse([n for n in names if 'cold_start' in n
                          or 'new_author' in n])
        self.assertFalse(hasattr(cr, 'apply_new_author_boost'))

    def test_the_age_cut_is_off_for_catch_up_and_elks_48h_is_available(self):
        self.assertIsNone(cr.CATCHUP_MAX_POST_AGE)
        self.assertIsNone(cr.DEFAULT_RANKING_PARAMS.max_post_age_ms)
        self.assertEqual(cr.ELK_MAX_POST_AGE_MS, 48 * HOUR)


class TestWeightSums(Base):
    def test_splits_by_sign_the_way_scoring_weights_does(self):
        sums = cr.weight_sums(cr.X_WEIGHTS)
        # 0.5 + 5 + 1 + 5 + 2 + 0.4 + 0.2 + 0 + 0.05 + 0.05 + 0.05 + 0 + 4
        self.assertClose(sums.positive_sum, 18.25)
        # -(-43.2 + -58.8 + -31.2 + -234 + -0.02)
        self.assertClose(sums.negative_sum, 367.22)
        self.assertClose(sums.total_sum, 385.47)


class TestOffsetScore(Base):
    sums = cr.weight_sums(cr.X_WEIGHTS)

    def test_adds_the_offset_to_a_non_negative_combined_score(self):
        self.assertClose(cr.offset_score(2, self.sums),
                         2 + cr.NEGATIVE_SCORES_OFFSET, 12)
        self.assertClose(cr.offset_score(0, self.sums),
                         cr.NEGATIVE_SCORES_OFFSET, 12)

    def test_maps_a_negative_combined_score_into_zero_offset(self):
        scored = cr.offset_score(-10, self.sums)
        self.assertClose(
            scored, (-10 + self.sums.negative_sum) / self.sums.total_sum
            * cr.NEGATIVE_SCORES_OFFSET, 12)
        self.assertGreater(scored, 0)
        self.assertLess(scored, cr.NEGATIVE_SCORES_OFFSET)

    def test_is_monotonic_across_the_sign_boundary(self):
        o = cr.offset_score
        self.assertLess(o(-1, self.sums), o(0, self.sums))
        self.assertLess(o(-2, self.sums), o(-1, self.sums))

    def test_clamps_at_zero_when_every_weight_is_zero(self):
        zero = cr.WeightSums(0, 0, 0)
        self.assertEqual(cr.offset_score(-5, zero), 0)
        self.assertEqual(cr.offset_score(5, zero), 5)


# endregion

# region helpers


class TestLogNorm(Base):
    def test_is_zero_at_zero_and_saturates_at_one(self):
        self.assertEqual(cr.log_norm(0, 300), 0)
        self.assertEqual(cr.log_norm(-5, 300), 0)
        self.assertClose(cr.log_norm(300, 300), 1)
        self.assertEqual(cr.log_norm(30_000, 300), 1)

    def test_is_zero_for_a_nonsense_saturation_or_nan(self):
        self.assertEqual(cr.log_norm(5, 0), 0)
        self.assertEqual(cr.log_norm(5, -1), 0)
        self.assertEqual(cr.log_norm(math.nan, 300), 0)

    def test_is_monotonically_increasing(self):
        previous = 0
        for n in (1, 2, 5, 10, 50, 100, 299):
            value = cr.log_norm(n, 300)
            self.assertGreater(value, previous)
            previous = value

    def test_has_strongly_diminishing_returns(self):
        low = cr.log_norm(10, 10_000) - cr.log_norm(0, 10_000)
        high = cr.log_norm(1010, 10_000) - cr.log_norm(1000, 10_000)
        self.assertGreater(low, high * 100)


class TestLift(Base):
    def test_is_a_no_op_at_zero_signal_and_reaches_max_at_one(self):
        self.assertEqual(cr.lift(0, 4), 1)
        self.assertEqual(cr.lift(1, 4), 4)
        self.assertClose(cr.lift(0.5, 3), 2)

    def test_works_as_a_penalty_when_max_is_below_one(self):
        self.assertEqual(cr.lift(0, 0.25), 1)
        self.assertClose(cr.lift(1, 0.25), 0.25)

    def test_ignores_a_negative_signal_unless_a_min_is_supplied(self):
        self.assertEqual(cr.lift(-1, 4), 1)
        self.assertClose(cr.lift(-1, 4, 0.25), 0.25)
        self.assertClose(cr.lift(-0.5, 4, 0.5), 0.75)

    def test_clamps_out_of_range_signals(self):
        self.assertEqual(cr.lift(5, 4), 4)
        self.assertClose(cr.lift(-5, 4, 0.25), 0.25)

    def test_negative_zero_is_a_no_op_too(self):
        self.assertEqual(cr.lift(-0.0, 7, 0.1), 1)


class TestAffinitySignal(Base):
    def test_is_rank_based_not_winner_take_all(self):
        values = {'top': 200, 'a': 20, 'b': 15, 'c': 10, 'd': 5, 'e': 2}
        got = [cr.affinity_signal(values, k) for k in values]
        self.assertAlmostEqual(got[0], 0.9166, places=3)
        self.assertAlmostEqual(got[1], 0.75, places=3)
        self.assertAlmostEqual(got[2], 0.5833, places=3)
        # the runner-up is nowhere near zero despite being 10x behind
        self.assertGreater(cr.affinity_signal(values, 'a'), 0.5)
        for i in range(1, len(got)):
            self.assertLess(got[i], got[i - 1])

    def test_is_invariant_to_how_the_counters_are_scaled(self):
        small = {'a': 2, 'b': 1.5, 'c': 1, 'd': 0.5}
        large = {'a': 20_000, 'b': 15_000, 'c': 10_000, 'd': 5_000}
        for key in small:
            self.assertClose(cr.affinity_signal(small, key),
                             cr.affinity_signal(large, key), 12)

    def test_returns_a_negative_value_for_a_negative_entry(self):
        values = {'good': 10, 'bad': -1, 'worse': -5}
        self.assertGreater(cr.affinity_signal(values, 'good'), 0)
        self.assertLess(cr.affinity_signal(values, 'bad'), 0)
        self.assertLess(cr.affinity_signal(values, 'worse'),
                        cr.affinity_signal(values, 'bad'))
        self.assertGreaterEqual(cr.affinity_signal(values, 'worse'), -1)

    def test_returns_zero_for_unknown_absent_or_non_finite_keys(self):
        self.assertEqual(cr.affinity_signal({'a': 10}, 'z'), 0)
        self.assertEqual(cr.affinity_signal({}, 'a'), 0)
        self.assertEqual(cr.affinity_signal(None, 'a'), 0)
        self.assertEqual(cr.affinity_signal({'a': 10}, None), 0)
        self.assertEqual(cr.affinity_signal({'a': math.nan}, 'a'), 0)

    def test_picks_up_in_place_mutations_of_the_map(self):
        values = {'a': 10, 'b': 5}
        before = cr.affinity_signal(values, 'b')
        values['c'] = 1
        self.assertNotAlmostEqual(cr.affinity_signal(values, 'b'), before,
                                  places=6)


class TestAffinityResolver(Base):
    def test_the_default_resolver_returns_zero_everywhere(self):
        r = cr.NULL_AFFINITY
        for channel in (r.author, r.tag, r.language, r.booster,
                        r.author_penalty):
            self.assertEqual(channel('anything'), 0)
        self.assertIs(ctx().affinity, cr.NULL_AFFINITY)

    def test_signals_resolver_reads_the_channels(self):
        resolver = cr.signals_affinity_resolver(cr.ForYouSignals(
            author_affinity={'a1': 10}, tag_affinity={'cats': 3},
            language_affinity={'ja': 2}, muted_for_you=['m1'],
            not_interested=['post9']))
        self.assertGreater(resolver.author_affinity('a1'), 0)
        self.assertGreater(resolver.tag_affinity('cats'), 0)
        self.assertGreater(resolver.language_affinity('ja'), 0)
        self.assertEqual(resolver.author_penalty('m1'), 1)
        self.assertEqual(resolver.author_penalty('someone'), 0)
        self.assertEqual(ctx(cr.ForYouSignals(
            not_interested=['post9'])).not_interested_ids, {'post9'})

    def test_reads_booster_affinity_when_the_store_provides_it(self):
        with_boosters = cr.ForYouSignals(booster_affinity={'curator': 5})
        self.assertGreater(
            cr.signals_affinity_resolver(with_boosters)
            .booster_affinity('curator'), 0)
        self.assertEqual(cr.signals_affinity_resolver(
            cr.ForYouSignals()).booster_affinity('curator'), 0)

    def test_is_overridable_one_channel_at_a_time(self):
        warm = cr.predict_actions(
            make_candidate(author_id='nobody'),
            ctx(affinity=cr.AffinityResolver(author=lambda _id: 1)))
        cold = cr.predict_actions(make_candidate(author_id='nobody'), ctx())
        self.assertGreater(warm['favorite'], cold['favorite'])


# endregion

# region recency


class TestRecencyMultiplier(Base):
    def test_is_one_for_a_brand_new_post(self):
        self.assertClose(cr.recency_multiplier(0), 1)

    def test_halves_the_above_floor_part_every_half_life(self):
        floor = cr.DEFAULT_RANKING_PARAMS.recency_floor
        half = cr.DEFAULT_RANKING_PARAMS.recency_half_life_ms
        self.assertClose(cr.recency_multiplier(half) - floor,
                         (1 - floor) * 0.5)
        self.assertClose(cr.recency_multiplier(2 * half) - floor,
                         (1 - floor) * 0.25)

    def test_decreases_strictly_across_the_whole_48h_window(self):
        previous = math.inf
        for h in range(0, 50, 2):
            value = cr.recency_multiplier(h * HOUR)
            self.assertLess(value, previous)
            previous = value

    def test_is_zero_past_the_cap_when_the_cap_is_set(self):
        elk = dataclasses.replace(cr.DEFAULT_RANKING_PARAMS,
                                  max_post_age_ms=cr.ELK_MAX_POST_AGE_MS)
        self.assertGreater(
            cr.recency_multiplier(cr.ELK_MAX_POST_AGE_MS, elk), 0)
        self.assertEqual(
            cr.recency_multiplier(cr.ELK_MAX_POST_AGE_MS + 1, elk), 0)

    def test_has_no_hard_cut_for_catch_up(self):
        # Catch Up's default: an "as far back as possible" run goes past
        # 48 h and the floor keeps old posts rankable.
        past = cr.recency_multiplier(cr.ELK_MAX_POST_AGE_MS + 1)
        self.assertGreater(past, cr.DEFAULT_RANKING_PARAMS.recency_floor)
        self.assertGreaterEqual(cr.recency_multiplier(30 * 24 * HOUR), 0.12)
        self.assertLess(cr.recency_multiplier(10_000 * HOUR), 0.1200001)

    def test_a_cut_of_another_length_is_honoured(self):
        p = dataclasses.replace(cr.DEFAULT_RANKING_PARAMS,
                                max_post_age_ms=6 * HOUR)
        self.assertGreater(cr.recency_multiplier(6 * HOUR, p), 0)
        self.assertEqual(cr.recency_multiplier(6 * HOUR + 1, p), 0)

    def test_treats_a_future_dated_post_as_brand_new(self):
        self.assertClose(cr.recency_multiplier(-HOUR), 1)

    def test_a_zero_half_life_floors_instead_of_raising(self):
        # Infinity in the TypeScript, where 0.5 ** Infinity is 0.
        p = dataclasses.replace(cr.DEFAULT_RANKING_PARAMS,
                                recency_half_life_ms=0)
        self.assertClose(cr.recency_multiplier(HOUR, p),
                         cr.DEFAULT_RANKING_PARAMS.recency_floor)
        negative = dataclasses.replace(cr.DEFAULT_RANKING_PARAMS,
                                       recency_half_life_ms=-HOUR)
        self.assertClose(cr.recency_multiplier(HOUR, negative),
                         cr.DEFAULT_RANKING_PARAMS.recency_floor)

    def test_matches_the_typescript_values(self):
        golden = GOLDEN['recency']
        cut = dataclasses.replace(cr.DEFAULT_RANKING_PARAMS,
                                  max_post_age_ms=cr.ELK_MAX_POST_AGE_MS)
        for age, with_cut, without_cut in golden:
            self.assertAlmostEqual(cr.recency_multiplier(age, cut),
                                   with_cut, places=12)
            self.assertAlmostEqual(
                cr.recency_multiplier(age), without_cut, places=12)


class TestRecencyIsOneMultiplier(Base):
    def test_scales_the_positive_sum_by_exactly_the_freshness_ratio(self):
        context = ctx(weights={'not_dwelled': 0})
        off = cr.NEGATIVE_SCORES_OFFSET
        fresh = raw(make_candidate(favourites=0, age_ms=0), context) - off
        older = raw(make_candidate(favourites=0, age_ms=12 * HOUR),
                    context) - off
        self.assertAlmostEqual(older / fresh,
                               cr.recency_multiplier(12 * HOUR), places=9)

    def test_puts_freshness_and_language_in_the_context_multiplier(self):
        candidate = make_candidate(age_ms=12 * HOUR, language='ja')
        self.assertClose(
            cr.context_multiplier(candidate, ctx()),
            cr.recency_multiplier(12 * HOUR)
            * cr.DEFAULT_RANKING_PARAMS.language_mismatch_prior, 12)

    def test_is_beatable_by_engagement_across_an_eight_hour_gap(self):
        self.assertGreater(
            raw(make_candidate(favourites=300, age_ms=8 * HOUR)),
            raw(make_candidate(favourites=3, age_ms=0)))
        self.assertGreater(
            raw(make_candidate(favourites=3_000, age_ms=6 * HOUR)),
            raw(make_candidate(favourites=2, age_ms=0)))

    def test_still_keeps_a_day_and_a_half_old_hit_from_beating_a_fresh_post(
            self):
        self.assertLess(
            raw(make_candidate(favourites=3_000, age_ms=40 * HOUR)),
            raw(make_candidate(favourites=3, age_ms=0)))

    def test_orders_identical_posts_newest_first(self):
        candidates = [
            make_candidate(id='h%d' % h, author_id='a%d' % h,
                           age_ms=h * HOUR, favourites=40)
            for h in (36, 24, 12, 1)]
        self.assertEqual(
            ids(cr.rank_candidates(candidates, ctx())),
            ['h1', 'h12', 'h24', 'h36'])

    def test_elks_cut_drives_a_post_past_48h_to_the_offset_floor(self):
        expired = make_candidate(id='expired', author_id='a2',
                                 age_ms=50 * HOUR, favourites=5000,
                                 reblogs=900)
        ordinary = make_candidate(id='ordinary', author_id='a1',
                                  age_ms=20 * HOUR, favourites=1)
        ranked = cr.rank_candidates([expired, ordinary], elk_ctx())
        self.assertEqual(ranked[0].status['id'], 'ordinary')
        self.assertLessEqual(ranked[1].score, cr.NEGATIVE_SCORES_OFFSET)

    def test_catch_up_ranks_that_same_old_hit_above_the_ordinary_post(self):
        expired = make_candidate(id='expired', author_id='a2',
                                 age_ms=50 * HOUR, favourites=5000,
                                 reblogs=900)
        ordinary = make_candidate(id='ordinary', author_id='a1',
                                  age_ms=20 * HOUR, favourites=1)
        ranked = cr.rank_candidates([expired, ordinary], ctx())
        self.assertEqual(ranked[0].status['id'], 'expired')
        self.assertGreater(ranked[1].score, cr.NEGATIVE_SCORES_OFFSET)

    def test_raises_not_dwelled_with_age_and_nothing_else(self):
        fresh = cr.predict_actions(
            make_candidate(age_ms=HOUR, favourites=0), ctx())
        old = cr.predict_actions(
            make_candidate(age_ms=36 * HOUR, favourites=0), ctx())
        self.assertGreater(old['not_dwelled'], fresh['not_dwelled'])
        for key in ACTION_KEYS:
            if key != 'not_dwelled':
                self.assertAlmostEqual(old[key], fresh[key], places=12,
                                       msg=key)


class TestLanguage(Base):
    def test_costs_well_under_2x_not_two_orders_of_magnitude(self):
        readable = raw(make_candidate(favourites=30, language='en'))
        foreign = raw(make_candidate(favourites=30, language='ja'))
        self.assertLess(foreign, readable)
        self.assertLess(readable / foreign, 2)

    def test_is_exactly_the_configured_prior_on_the_positive_sum(self):
        context = ctx(weights={'not_dwelled': 0})
        off = cr.NEGATIVE_SCORES_OFFSET
        readable = raw(make_candidate(favourites=30, language='en',
                                      age_ms=0), context) - off
        foreign = raw(make_candidate(favourites=30, language='ja',
                                     age_ms=0), context) - off
        self.assertAlmostEqual(
            foreign / readable,
            cr.DEFAULT_RANKING_PARAMS.language_mismatch_prior, places=9)

    def test_is_lifted_away_by_the_viewers_own_history(self):
        foreign = raw(make_candidate(favourites=30, language='ja'))
        learned = raw(
            make_candidate(favourites=30, language='ja'),
            ctx(cr.ForYouSignals(language_affinity={'ja': 5})))
        self.assertGreater(learned, foreign)

    def test_does_not_also_inflate_not_interested(self):
        readable = cr.predict_actions(make_candidate(language='en'), ctx())
        foreign = cr.predict_actions(make_candidate(language='ja'), ctx())
        self.assertAlmostEqual(foreign['not_interested'],
                               readable['not_interested'], places=12)

    def test_a_post_with_no_language_or_a_viewer_without_one_is_never_penalised(
            self):
        base = raw(make_candidate(favourites=30, language='en'))
        self.assertAlmostEqual(
            raw(make_candidate(favourites=30, language=None)), base, 12)
        self.assertAlmostEqual(
            raw(make_candidate(favourites=30, language='ja'),
                ctx(viewer_languages=[])), base, 12)
        self.assertAlmostEqual(
            raw(make_candidate(favourites=30, language='ja'),
                ctx(viewer_languages=None)), base, 12)


# endregion


# region features


class TestFeatures(Base):
    def features(self, candidate, c=None):
        return cr.extract_ranking_features(candidate, c or ctx())

    def test_reads_engagement_media_and_author_shape(self):
        f = self.features(make_candidate(
            favourites=10, reblogs=4, replies=6, images=2, card=True,
            card_image=True, spoiler_text='cw', verified=True, bot=True))
        self.assertEqual(f.total_engagement, 20)
        self.assertClose(f.reply_density, 0.3)
        self.assertClose(f.reblog_density, 0.2)
        self.assertEqual(f.image_count, 2)
        self.assertTrue(f.has_link)
        self.assertTrue(f.has_card_image)
        self.assertTrue(f.has_spoiler)
        self.assertTrue(f.verified)
        self.assertTrue(f.bot)

    def test_reports_a_video_duration_only_for_real_videos(self):
        self.assertIsNone(
            self.features(make_candidate(images=1)).video_duration_ms)
        self.assertEqual(self.features(make_candidate(
            video_duration_secs=12)).video_duration_ms, 12_000)
        self.assertEqual(self.features(make_candidate(
            video_duration_secs=12, video_type='gifv')).video_duration_ms,
            12_000)

    def test_survives_a_status_with_missing_optional_fields(self):
        bare = {'id': 'bare', 'created_at': iso_ago(HOUR),
                'account': {'id': 'a1', 'acct': 'bare'}}
        f = self.features(cr.Candidate(bare))
        self.assertEqual(f.total_engagement, 0)
        self.assertEqual(f.text_length, 0)
        self.assertTrue(math.isfinite(f.freshness))
        cr.score_candidate(cr.Candidate(bare), ctx())

    def test_survives_nulls_where_the_api_sends_them(self):
        status = make_status()
        status.update(language=None, card=None, poll=None, tags=None,
                      media_attachments=None, content=None,
                      favourites_count=None, created_at=None)
        status['account'].update(fields=None, note=None,
                                 followers_count=None)
        f = self.features(cr.Candidate(status))
        self.assertEqual(f.age_ms, 0)
        self.assertEqual(f.text_length, 0)
        self.assertEqual(f.tag_count, 0)
        self.assertTrue(math.isfinite(
            cr.score_candidate(cr.Candidate(status), ctx()).raw_score))

    def test_an_unparsable_timestamp_reads_as_age_zero(self):
        status = make_status()
        status['created_at'] = 'yesterday-ish'
        self.assertEqual(self.features(cr.Candidate(status)).age_ms, 0)

    def test_a_future_dated_post_has_age_zero(self):
        self.assertEqual(
            self.features(make_candidate(age_ms=-5 * HOUR)).age_ms, 0)

    def test_measures_age_in_hours_from_now(self):
        f = self.features(make_candidate(age_ms=3 * HOUR))
        self.assertEqual(f.age_ms, 3 * HOUR)
        self.assertEqual(f.age_hours, 3)

    def test_plain_text_length_strips_tags_and_collapses_whitespace(self):
        f = self.features(make_candidate(
            content='<p>one   two</p>\n<p>three</p>'))
        self.assertEqual(f.text_length, len('one two three'))
        # UTF-16 length, like JavaScript's `.length`
        f = self.features(make_candidate(content='<p>\U0001F600</p>'))
        self.assertEqual(f.text_length, 2)

    def test_a_question_mark_anywhere_in_the_content_counts(self):
        self.assertTrue(self.features(make_candidate(
            content='<p>why?</p>')).has_question)
        self.assertFalse(self.features(make_candidate()).has_question)

    def test_self_reply_and_reply_flags(self):
        f = self.features(make_candidate(
            in_reply_to_id='x', in_reply_to_account_id='a1'))
        self.assertTrue(f.is_reply)
        self.assertTrue(f.is_self_reply)
        f = self.features(make_candidate(
            in_reply_to_id='x', in_reply_to_account_id='other'))
        self.assertTrue(f.is_reply)
        self.assertFalse(f.is_self_reply)
        self.assertFalse(self.features(make_candidate()).is_reply)

    def test_an_empty_card_object_is_still_a_link(self):
        status = make_status()
        status['card'] = {}
        self.assertTrue(self.features(cr.Candidate(status)).has_link)

    def test_spamminess_follows_the_follow_ratio_bio_and_post_count(self):
        def spam(**options):
            return self.features(make_candidate(**options)).spamminess
        self.assertEqual(spam(), 0)
        self.assertClose(spam(followers=3, following=9000), 0.6)
        self.assertClose(spam(followers=100, following=400), 0.3)
        self.assertClose(spam(note=''), 0.15)
        self.assertClose(spam(statuses=0), 0.1)
        self.assertEqual(spam(followers=0, following=20), 0)
        self.assertClose(spam(followers=3, following=9000, note='',
                              statuses=0), 0.85)

    def test_author_reach_and_volume_are_log_normalised(self):
        f = self.features(make_candidate(followers=50_000, statuses=0))
        self.assertClose(f.author_reach, 1)
        self.assertEqual(f.author_volume, 0)


if __name__ == '__main__':
    unittest.main()
