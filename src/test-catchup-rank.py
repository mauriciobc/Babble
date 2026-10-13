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

import ast
import dataclasses
import datetime
import math
import os
import subprocess
import sys
import types
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import catchuprank as cr  # noqa: E402

NOW = datetime.datetime(2026, 8, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)
HOUR = 3_600_000

ACTION_KEYS = list(cr.X_WEIGHTS)


def iso_ago(age_ms, now=NOW):
    moment = now - datetime.timedelta(milliseconds=age_ms)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (
        moment.microsecond // 1000)


def make_status(id='s1', author_id='a1', acct='alice', age_ms=HOUR,
                favourites=0, reblogs=0, replies=0,
                content='<p>hello world</p>', language='en',
                followers=200, following=200, statuses=500, bot=False,
                verified=False, note='<p>bio</p>', in_reply_to_id=None,
                in_reply_to_account_id=None, spoiler_text='',
                sensitive=False, tags=(), card=False, card_image=False,
                images=0, video_duration_secs=None, poll=False,
                video_type='video', now=NOW):
    """A Mastodon Status dict, the shape of ``makeStatus`` in Elk's tests."""
    media = [{'id': 'm%d' % i, 'type': 'image', 'preview_url': '',
              'meta': {'original': {'width': 100, 'height': 100,
                                    'size': '100x100', 'aspect': 1}}}
             for i in range(images)]
    if video_duration_secs is not None:
        media.append({'id': 'v0', 'type': video_type, 'preview_url': '',
                      'meta': {'original': {
                          'width': 100, 'height': 100, 'frame_rate': '30',
                          'duration': video_duration_secs,
                          'bitrate': 1000, 'aspect': 1}}})
    return {
        'id': id,
        'created_at': iso_ago(age_ms, now),
        'content': content,
        'language': language,
        'favourites_count': favourites,
        'reblogs_count': reblogs,
        'replies_count': replies,
        'in_reply_to_id': in_reply_to_id,
        'in_reply_to_account_id': in_reply_to_account_id,
        'spoiler_text': spoiler_text,
        'sensitive': sensitive,
        'media_attachments': media,
        'tags': [{'name': name, 'url': ''} for name in tags],
        'card': ({'url': 'https://example.com/card',
                  'image': 'https://img' if card_image else None}
                 if card else None),
        'poll': {'id': 'p1'} if poll else None,
        'reblog': None,
        'account': {
            'id': author_id, 'acct': acct, 'username': acct.split('@')[0],
            'note': note, 'bot': bot, 'followers_count': followers,
            'following_count': following, 'statuses_count': statuses,
            'fields': ([{'name': 'web', 'value': '',
                         'verified_at': '2024-01-01T00:00:00Z'}]
                       if verified else []),
        },
    }


def make_candidate(in_network=True, boost_of=None, **options):
    status = make_status(**options)
    if boost_of is not None:
        status['content'] = ''
        status['reblog'] = make_status(**boost_of)
    return cr.Candidate(status=status, in_network=in_network)


def pre_scored(raw_score, **options):
    candidate = make_candidate(**options)
    candidate.raw_score = raw_score
    return candidate


def ctx(signals=None, **overrides):
    """``ctx()`` of the TS tests; ``signals`` feeds Elk's default resolver."""
    overrides.setdefault('viewer_languages', ['en'])
    if signals is not None:
        overrides.setdefault('affinity',
                             cr.signals_affinity_resolver(signals))
    return cr.RankingContext(now=NOW, **overrides)


def elk_ctx(**overrides):
    """A context with Elk's 48 h AgeFilter cut on."""
    params = dict(overrides.pop('params', None) or {})
    params['max_post_age_ms'] = cr.ELK_MAX_POST_AGE_MS
    return ctx(params=params, **overrides)


def raw(candidate, c=None):
    return cr.score_candidate(candidate, c or ctx()).raw_score


def final(candidate, c=None):
    return cr.rank_candidates([candidate], c or ctx())[0].score


def only_weights(**overrides):
    """Zero every weight, so a test can isolate one head."""
    return {**dict.fromkeys(ACTION_KEYS, 0), **overrides}


def ids(candidates):
    return [c.status['id'] for c in candidates]


# Computed by the TypeScript (see the module docstring).
GOLDEN = {'raw_cut': {'1001': 0.15388888093151626,
             '1002': 0.22217372923993345,
             '1003': 0.04671803448125851,
             '1004': 0.005225522595815383,
             '1005': 0.09981995260313835,
             '1006': 0.001,
             '1007': 0.1802533477326949},
 'raw_nocut': {'1001': 0.15388888093151626,
               '1002': 0.22217372923993345,
               '1003': 0.04671803448125851,
               '1004': 0.005225522595815383,
               '1005': 0.09981995260313835,
               '1006': 0.019269150639179184,
               '1007': 0.1802533477326949},
 'adjusted_cut': {'1001': 0.09618055058219765,
                  '1002': 0.16663029692995007,
                  '1003': 0.03503852586094389,
                  '1004': 0.003919141946861537,
                  '1005': 0.09981995260313835,
                  '1006': 0.00075,
                  '1007': 0.1802533477326949},
 'ranked_cut': [['1007', 0.1802533477326949],
                ['1002', 0.16663029692995007],
                ['1005', 0.09981995260313835],
                ['1001', 0.09618055058219765],
                ['1003', 0.03503852586094389],
                ['1004', 0.003919141946861537],
                ['1006', 0.00075]],
 'ranked_nocut': [['1007', 0.1802533477326949],
                  ['1002', 0.16663029692995007],
                  ['1005', 0.09981995260313835],
                  ['1001', 0.09618055058219765],
                  ['1003', 0.03503852586094389],
                  ['1006', 0.014451862979384389],
                  ['1004', 0.003919141946861537]],
 'probs_cut': {'1002': {'favorite': 0.19561532398325754,
                        'reply': 0.008779262384199963,
                        'retweet': 0.0963500834702418,
                        'quote': 0.012457319701298208,
                        'share': 0.003459284333720466,
                        'click': 0.13024382176419405,
                        'open_link': 0,
                        'profile_click': 0.008137894907945092,
                        'photo_expand': 0,
                        'video_open': 0.03255157963178037,
                        'vqv': 0.014648210834301166,
                        'dwell': 0.5800599999999999,
                        'follow_author': 0.005172081705149454,
                        'not_interested': 0.000144,
                        'mute_author': 3.149116820441464e-05,
                        'block_author': 1.5e-05,
                        'report': 3e-06,
                        'not_dwelled': 0.27280000000000004},
               '1004': {'favorite': 0.012,
                        'reply': 0.0014000000000000002,
                        'retweet': 0.00432,
                        'quote': 0.0005184,
                        'share': 0.001,
                        'click': 0.03,
                        'open_link': 0,
                        'profile_click': 0.005,
                        'photo_expand': 0,
                        'video_open': 0,
                        'vqv': 0,
                        'dwell': 0.5044,
                        'follow_author': 0,
                        'not_interested': 0.000273,
                        'mute_author': 3.7000000000000005e-05,
                        'block_author': 4.05e-05,
                        'report': 1.065e-05,
                        'not_dwelled': 0.26949999999999996}},
 'reasons_1002': [['favorite', 0.09780766199162877],
                  ['retweet', 0.0963500834702418],
                  ['click', 0.052097528705677625],
                  ['reply', 0.04389631192099981],
                  ['follow_author', 0.020688326820597815],
                  ['quote', 0.012457319701298208],
                  ['not_interested', -0.0062208],
                  ['not_dwelled', -0.005456000000000001],
                  ['mute_author', -0.0018516806904195805],
                  ['share', 0.001729642166860233],
                  ['video_open', 0.0016275789815890186],
                  ['vqv', 0.0007324105417150584],
                  ['report', -0.000702],
                  ['block_author', -0.000468],
                  ['freshness', 0.7073295358348152]],
 'features_cut': {'1001': {'engagement_estimate': 17,
                           'popularity': 0.3138147192461524,
                           'velocity': 0.30516972670261566,
                           'engagement_signal': 0.30949222297438406,
                           'reply_density': 0.11764705882352941,
                           'reblog_density': 0.17647058823529413,
                           'spamminess': 0,
                           'author_reach': 0.490148223539285,
                           'language_prior': 1,
                           'freshness': 0.8599888454232688,
                           'age_hours': 3,
                           'text_length': 18},
                  '1002': {'engagement_estimate': 322.8494623655914,
                           'popularity': 0.6275789815890185,
                           'velocity': 0.6197475220448403,
                           'engagement_signal': 0.6236632518169294,
                           'reply_density': 0.0774354704412989,
                           'reblog_density': 0.19983347210657784,
                           'spamminess': 0,
                           'author_reach': 0.7908286244356357,
                           'language_prior': 1,
                           'freshness': 0.7073295358348152,
                           'age_hours': 7,
                           'text_length': 11,
                           'video_duration_ms': 30000},
                  '1003': {'engagement_estimate': 606.7741935483871,
                           'popularity': 0.6959280083691569,
                           'velocity': 0.49146072264527146,
                           'engagement_signal': 0.5936943655072142,
                           'reply_density': 0.01648059542796385,
                           'reblog_density': 0.15948963317384368,
                           'spamminess': 0,
                           'author_reach': 0.490148223539285,
                           'language_prior': 1,
                           'freshness': 0.27556349186104045,
                           'age_hours': 30,
                           'text_length': 9},
                  '1004': {'engagement_estimate': 0,
                           'popularity': 0,
                           'velocity': 0,
                           'engagement_signal': 0,
                           'reply_density': 0,
                           'reblog_density': 0,
                           'spamminess': 0.85,
                           'author_reach': 0.22162106998775605,
                           'language_prior': 0.6,
                           'freshness': 0.9749481082151732,
                           'age_hours': 0.5,
                           'text_length': 11}},
 'reply_weight': {'1001': 5,
                  '1002': 5,
                  '1003': 5,
                  '1004': 5,
                  '1005': 20,
                  '1006': 5,
                  '1007': 5},
 'vqv_weight': {'1001': 0,
                '1002': 0.05,
                '1003': 0,
                '1004': 0,
                '1005': 0,
                '1006': 0,
                '1007': 0},
 'pipeline_cut': [['1007', 0.1802533477326949],
                  ['1002', 0.16663029692995007],
                  ['1005', 0.09981995260313835],
                  ['1001', 0.09618055058219765],
                  ['1003', 0.03503852586094389],
                  ['1004', 0.003919141946861537],
                  ['1006', 0.00075]],
 'dedupe3': ['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd10', 'd11', 'd12'],
 'dedupe1': ['d1', 'd2', 'd3', 'd5', 'd10', 'd11', 'd12'],
 'rerank': ['d1',
            'd2',
            'd4',
            'd5',
            'd6',
            'd7',
            'd10',
            'd11',
            'd8',
            'd3',
            'd12',
            'd9'],
 'rerank_limit5': ['d1',
                   'd2',
                   'd4',
                   'd5',
                   'd6',
                   'd3',
                   'd7',
                   'd8',
                   'd9',
                   'd10',
                   'd11',
                   'd12'],
 'rerank_residual': ['d1',
                     'd2',
                     'd4',
                     'd5',
                     'd6',
                     'd10',
                     'd7',
                     'd11',
                     'd3',
                     'd8',
                     'd12',
                     'd9'],
 'rerank_max': ['d1',
                'd2',
                'd4',
                'd5',
                'd6',
                'd3',
                'd10',
                'd7',
                'd8',
                'd11',
                'd9',
                'd12'],
 'rerank_theta': ['d1',
                  'd2',
                  'd4',
                  'd5',
                  'd6',
                  'd10',
                  'd11',
                  'd7',
                  'd8',
                  'd3',
                  'd12',
                  'd9'],
 'embed': {0: [[325, -0.2403005636744443],
               [478, 0.2403005636744443],
               [599, -0.2403005636744443],
               [645, -0.6357755313912211],
               [740, 0.2403005636744443],
               [1344, 0.3178877656956106],
               [1778, 0.21192517713040707],
               [1779, -0.2403005636744443],
               [2364, 0.10596258856520353],
               [2772, 0.18543452998910617],
               [2936, 0.2403005636744443],
               [3220, 0.2403005636744443]],
           1: [[1778, 0.22179478920057105],
               [2019, -0.29756893542462787],
               [2092, 0.29756893542462787],
               [2118, 0.29756893542462787],
               [2123, -0.16634609190042826],
               [2157, -0.29756893542462787],
               [2772, 0.19407044055049963],
               [2915, -0.29756893542462787],
               [3109, -0.665384367601713]],
           3: [[460, 0.32916886922904176],
               [568, 0.2194459128193612],
               [1227, -0.2194459128193612],
               [1615, -0.6583377384580835],
               [1822, 0.32916886922904176],
               [2772, 0.19201517371694102],
               [3872, 0.32916886922904176],
               [3926, 0.32916886922904176]],
           4: [[181, -0.2573070083610708],
               [302, 0.29711254108328294],
               [489, -0.2573070083610708],
               [751, -0.2573070083610708],
               [1106, 0.2599734734478725],
               [1210, 0.2573070083610708],
               [1282, 0.2573070083610708],
               [1308, 0.2573070083610708],
               [1591, -0.2573070083610708],
               [1686, 0.2573070083610708],
               [1811, -0.2573070083610708],
               [2648, 0.15756771943166703],
               [2652, 0.15756771943166703],
               [2944, 0.2573070083610708],
               [3026, 0.2573070083610708],
               [3705, -0.2573070083610708]],
           9: [[2393, -1]],
           10: [[391, -0.10596258856520349],
                [564, 0.2843274613243643],
                [2289, -0.6357755313912209],
                [2577, -0.21192517713040698],
                [2772, 0.1854345299891061],
                [3181, -0.2843274613243643],
                [3254, 0.2843274613243643],
                [3650, 0.3178877656956105],
                [3658, 0.2843274613243643],
                [3998, 0.2843274613243643]]},
 'cos': [[0, 2, 0.7323698819034181],
         [0, 11, 0.8945205300671422],
         [3, 4, 0],
         [0, 8, 1]],
 'tokens': [['the', 'quick', 'brown', 'fox', 'jumped'],
            ['grüße', 'aus', 'münchen'],
            ['tiếng', 'việt', 'rất', 'đẹp'],
            ['今日',
             '日の',
             'の選',
             '選挙',
             '挙結',
             '結果',
             '果に',
             'につ',
             'つい',
             'いて',
             'て議',
             '議論',
             '論し',
             'して',
             'てい',
             'いま',
             'ます'],
            ['𝐚', 'ünï', 'नमस्ते', 'दुनिया'],
            ['het', 'is', 'een', 'van', 'de', 'bir', 've', 'bu', 'için']],
 'idf': [['shipping', 1.2237754316221157],
         ['the', 1.2237754316221157],
         ['caching', 1.2237754316221157],
         ['layer', 1.2237754316221157],
         ['today', 1.2237754316221157],
         ['latency', 1.2237754316221157]],
 'misc': {'logNorm': [[1, 300, 0.12145326590959868],
                      [10, 10000, 0.2603453447672145],
                      [0, 5, 0],
                      [500000, 10000, 1]],
          'lift': [[0.5, 3, 2],
                   [-0.5, 4, 0.5, 0.75],
                   [1, 0.25, 0.25],
                   [-1, 4, 0.25, 0.25]],
          'affinity': [['top', 0.9166666666666666],
                       ['a', 0.75],
                       ['b', 0.5833333333333334],
                       ['c', 0.4166666666666667],
                       ['d', 0.25],
                       ['e', 0.08333333333333333],
                       ['bad', -0.25],
                       ['worse', -0.75]],
          'offset': [[-10, 0.0009401268521199043],
                     [-0.5, 0.0009651288259599442],
                     [0, 0.001],
                     [2, 2.001]],
          'sums': [{'positive_sum': 12.750000000000002,
                    'negative_sum': 367.21999999999997,
                    'total_sum': 379.96999999999997},
                   {'positive_sum': 18.25,
                    'negative_sum': 367.21999999999997,
                    'total_sum': 385.46999999999997}]},
 'recency': [[0, 1, 1],
             [3600000, 0.9506093951598903, 0.9506093951598903],
             [43200000, 0.56, 0.56],
             [86400000, 0.33999999999999997, 0.33999999999999997],
             [172440000, 0.17531861175873192, 0.17531861175873192],
             [172800001, 0, 0.17499999911752095],
             [252000000, 0, 0.13543385316425388]]}


class Base(unittest.TestCase):
    def assertClose(self, a, b, places=10):
        self.assertAlmostEqual(a, b, places=places)


# region weights


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
                        r.author_penalty, r.post_penalty):
            self.assertEqual(channel('anything'), 0)
        self.assertIs(ctx().affinity, cr.NULL_AFFINITY)

    def test_signals_resolver_reads_the_channels(self):
        resolver = cr.signals_affinity_resolver(cr.ForYouSignals(
            author_affinity={'a1': 10}, tag_affinity={'cats': 3},
            language_affinity={'ja': 2}, muted_for_you=['m1'],
            not_interested=['post9']))
        self.assertGreater(resolver.author('a1'), 0)
        self.assertGreater(resolver.tag('cats'), 0)
        self.assertGreater(resolver.language('ja'), 0)
        # author_penalty is account-scoped, post_penalty post-scoped.
        self.assertEqual(resolver.author_penalty('m1'), 1)
        self.assertEqual(resolver.author_penalty('someone'), 0)
        self.assertEqual(resolver.post_penalty('post9'), 1)
        self.assertEqual(resolver.post_penalty('post1'), 0)

    def test_reads_booster_affinity_when_the_store_provides_it(self):
        with_boosters = cr.ForYouSignals(booster_affinity={'curator': 5})
        self.assertGreater(
            cr.signals_affinity_resolver(with_boosters).booster('curator'),
            0)
        self.assertEqual(cr.signals_affinity_resolver(
            cr.ForYouSignals()).booster('curator'), 0)

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


def fixture_candidates():
    """Seven statuses (see GOLDEN) with their in-network flags."""
    return [cr.Candidate(s, FIXTURE_IN_NETWORK[s['id']])
            for s in make_fixture_statuses()]


FIXTURE_IN_NETWORK = {'1001': True, '1002': False, '1003': True,
                      '1004': True, '1005': True, '1006': False,
                      '1007': True}


def make_fixture_statuses():
    boosted = make_status(
        id='9003', author_id='a4', acct='dave@far.example',
        age_ms=30 * HOUR, favourites=300, reblogs=90, replies=10,
        tags=('news',), card=True, poll=True, content='<p>Big news?</p>')
    boost = make_status(id='1003', author_id='a3', acct='carol',
                        age_ms=HOUR // 2)
    boost['content'] = ''
    boost['reblog'] = boosted
    return [
        make_status(
            id='1001', author_id='a1', acct='alice', age_ms=3 * HOUR,
            favourites=12, reblogs=3, replies=2, images=2, tags=('art',),
            card=True, card_image=True,
            content='<p>Look at this? <a href="https://example.com/x">'
                    'link</a></p>'),
        make_status(
            id='1002', author_id='a2', acct='bob@remote.example',
            age_ms=7 * HOUR, favourites=140, reblogs=60, replies=25,
            language='de', followers=5200, following=150,
            video_duration_secs=30, verified=True, tags=('linux', 'foss'),
            spoiler_text='cw', sensitive=True,
            content='<p>Kernel news</p>'),
        boost,
        make_status(
            id='1004', author_id='a5', acct='botty', age_ms=HOUR // 2,
            followers=10, following=500, statuses=0, bot=True, note='',
            language='ja', in_reply_to_id='1001',
            in_reply_to_account_id='a5'),
        make_status(id='1005', author_id='mut1', acct='mutual',
                    age_ms=HOUR, content='<p>from a mutual</p>'),
        make_status(id='1006', author_id='a6', acct='eve@old.example',
                    age_ms=70 * HOUR, favourites=50, reblogs=5, replies=1),
        make_status(id='1007', author_id='a1', acct='alice',
                    age_ms=2 * HOUR, favourites=40, replies=8,
                    video_duration_secs=5, video_type='gifv'),
    ]


def fixture_ctx(**params):
    return cr.RankingContext(
        now=NOW, viewer_languages=['en', 'de'], mutual_author_ids={'mut1'},
        viewer_follower_count=50, params=params or None)


class TestRankCandidates(Base):
    def test_returns_candidates_sorted_by_final_score_descending(self):
        ranked = cr.rank_candidates([
            make_candidate(id='s1', author_id='A', favourites=2),
            make_candidate(id='s2', author_id='B', favourites=200,
                           replies=30),
            make_candidate(id='s3', author_id='C', favourites=40),
        ], ctx())
        self.assertEqual(len(ranked), 3)
        for a, b in zip(ranked, ranked[1:], strict=False):
            self.assertGreaterEqual(a.score, b.score)
        self.assertEqual(ranked[0].status['id'], 's2')

    def test_populates_every_field_on_every_candidate(self):
        candidate = cr.rank_candidates([make_candidate()], ctx())[0]
        self.assertIsNotNone(candidate.probabilities)
        self.assertGreater(candidate.raw_score, 0)
        self.assertGreater(candidate.score, 0)
        self.assertGreater(len(candidate.reasons), 0)

    def test_does_not_mutate_the_input_array_or_its_candidates(self):
        candidates = [make_candidate(id='s1', author_id='A'),
                      make_candidate(id='s2', author_id='B', favourites=500)]
        snapshot = list(candidates)
        cr.rank_candidates(candidates, ctx())
        self.assertEqual(candidates, snapshot)
        self.assertTrue(all(c.score is None and c.raw_score is None
                            for c in candidates))

    def test_handles_an_empty_slate(self):
        self.assertEqual(cr.rank_candidates([], ctx()), [])
        self.assertEqual(cr.rank([], ctx()), [])

    def test_breaks_exact_ties_deterministically_newer_id_first(self):
        # rank_candidates re-scores, so tie on identical content
        a = make_candidate(id='100', author_id='A')
        b = make_candidate(id='200', author_id='B')
        first = ids(cr.rank_candidates([a, b], ctx()))
        second = ids(cr.rank_candidates([b, a], ctx()))
        self.assertEqual(first, second)
        self.assertEqual(first, ['200', '100'])


class TestRankPipeline(Base):
    def test_runs_score_adjust_sort_dedupe_rerank(self):
        ranked = cr.rank(fixture_candidates(), fixture_ctx())
        self.assertEqual(sorted(ids(ranked)),
                         sorted(FIXTURE_IN_NETWORK))
        for c in ranked:
            self.assertIsNotNone(c.score)
            self.assertIsNotNone(c.probabilities)

    def test_accepts_plain_status_dicts(self):
        statuses = make_fixture_statuses()
        statuses[1]['_followed_tags'] = True
        ranked = cr.rank(statuses, fixture_ctx())
        self.assertEqual(len(ranked), 7)
        self.assertFalse(next(c for c in ranked
                              if c.status['id'] == '1002').in_network)
        self.assertTrue(next(c for c in ranked
                             if c.status['id'] == '1001').in_network)

    def test_the_candidate_flag_sets_in_network(self):
        status = make_status(favourites=20)
        inside = cr.rank([cr.Candidate(status, True)], ctx())[0]
        outside = cr.rank([cr.Candidate(status, False)], ctx())[0]
        self.assertGreater(inside.score, outside.score)

    def test_dedupes_conversations_after_sorting(self):
        thread = [make_candidate(id='r%d' % i, author_id='u%d' % i,
                                 in_reply_to_id='root', favourites=50 - i)
                  for i in range(6)]
        ranked = cr.rank(thread, ctx())
        self.assertEqual(len(ranked), 3)
        ranked1 = cr.rank(thread, ctx(), max_per_conversation=1)
        self.assertEqual(len(ranked1), 1)

    def test_matches_the_typescript_pipeline_order_and_scores(self):
        got = cr.rank(fixture_candidates(),
                      fixture_ctx(max_post_age_ms=cr.ELK_MAX_POST_AGE_MS))
        expected = GOLDEN['pipeline_cut']
        self.assertEqual(ids(got), [i for i, _ in expected])
        for c, (_, score) in zip(got, expected, strict=True):
            self.assertAlmostEqual(c.score, score, places=12)


class TestDeterminism(Base):
    def snapshot(self, ranked):
        return [(c.status['id'], c.raw_score, c.score,
                 [tuple(r) for r in c.reasons]) for c in ranked]

    def test_the_same_input_twice_gives_identical_order_and_scores(self):
        a = self.snapshot(cr.rank(fixture_candidates(), fixture_ctx()))
        b = self.snapshot(cr.rank(fixture_candidates(), fixture_ctx()))
        self.assertEqual(a, b)

    def test_input_order_does_not_change_the_scores(self):
        forward = {c.status['id']: c.score for c in
                   cr.rank_candidates(fixture_candidates(), fixture_ctx())}
        backward = {c.status['id']: c.score for c in
                    cr.rank_candidates(reversed(fixture_candidates()),
                                       fixture_ctx())}
        # author diversity walks in score order, so scores match exactly
        self.assertEqual(forward, backward)

    def test_does_not_depend_on_the_hash_seed(self):
        # Sets and dicts of strings iterate differently under another
        # PYTHONHASHSEED; the ranking must not notice.
        script = '\n'.join([
            'import importlib.util, sys',
            'spec = importlib.util.spec_from_file_location("t", sys.argv[1])',
            't = importlib.util.module_from_spec(spec)',
            'spec.loader.exec_module(t)',
            'r = t.cr.rank(t.fixture_candidates(), t.fixture_ctx())',
            'print([(c.status["id"], repr(c.score)) for c in r])',
        ])
        outputs = set()
        for seed in ('0', '1', '12345'):
            env = dict(os.environ, PYTHONHASHSEED=seed,
                       BABBLE_SRCDIR=os.path.abspath(srcdir))
            outputs.add(subprocess.run(
                [sys.executable, '-c', script, os.path.abspath(__file__)],
                env=env, check=True, capture_output=True,
                text=True).stdout)
        self.assertEqual(len(outputs), 1)
        self.assertIn('1007', outputs.pop())


class TestNoClock(Base):
    def test_the_module_never_reads_the_clock_or_a_random_source(self):
        with open(cr.__file__, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
            elif isinstance(node, ast.Call):
                func = node.func
                called.add(func.attr if isinstance(func, ast.Attribute)
                           else getattr(func, 'id', ''))
        self.assertFalse(imported & {'time', 'random', 'secrets', 'gi',
                                     'os', 'uuid', 'locale'}, imported)
        self.assertFalse(called & {'now', 'utcnow', 'today', 'time',
                                   'monotonic', 'perf_counter',
                                   'random', 'urandom'}, called)

    def test_scoring_runs_with_every_clock_entry_point_disabled(self):
        class NoClock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                raise AssertionError('the clock was read')

            utcnow = today = now

        patched = types.SimpleNamespace(
            datetime=NoClock, timezone=datetime.timezone,
            timedelta=datetime.timedelta)
        original = cr.datetime
        cr.datetime = patched
        try:
            ranked = cr.rank(fixture_candidates(), fixture_ctx())
        finally:
            cr.datetime = original
        self.assertEqual(len(ranked), 7)

    def test_scores_depend_only_on_the_now_that_is_passed(self):
        status = make_status(favourites=20, age_ms=5 * HOUR)
        then = NOW + datetime.timedelta(hours=6)
        now_score = raw(cr.Candidate(status),
                        cr.RankingContext(now=NOW))
        later_score = raw(cr.Candidate(status),
                          cr.RankingContext(now=then))
        self.assertNotEqual(now_score, later_score)
        # The same status read at the same instant, however often.
        self.assertEqual(
            later_score,
            raw(cr.Candidate(status), cr.RankingContext(now=then)))

    def test_shifting_the_post_and_now_together_changes_nothing(self):
        years_on = datetime.datetime(2031, 3, 2, 9, 30, 15,
                                     tzinfo=datetime.timezone.utc)
        base = make_candidate(favourites=77, replies=4, age_ms=9 * HOUR,
                              tags=['x'])
        moved = cr.Candidate(make_status(
            favourites=77, replies=4, age_ms=9 * HOUR, tags=['x'],
            now=years_on))
        a = cr.score_candidate(base, cr.RankingContext(now=NOW))
        b = cr.score_candidate(moved, cr.RankingContext(now=years_on))
        self.assertEqual(a.raw_score, b.raw_score)

    def test_now_may_be_an_iso_string_and_zones_are_honoured(self):
        status = make_status(favourites=20, age_ms=5 * HOUR)
        same = raw(cr.Candidate(status), cr.RankingContext(now=NOW))
        iso = raw(cr.Candidate(status), cr.RankingContext(
            now='2026-08-16T12:00:00.000Z'))
        offset = raw(cr.Candidate(status), cr.RankingContext(
            now='2026-08-16T14:00:00+02:00'))
        self.assertEqual(same, iso)
        self.assertEqual(same, offset)
        with self.assertRaises(ValueError):
            cr.RankingContext(now='soon')

    def test_parse_timestamp_ms_matches_date_parse(self):
        self.assertEqual(
            cr.parse_timestamp_ms('2026-08-16T12:00:00.000Z'),
            1786881600000)
        self.assertEqual(
            cr.parse_timestamp_ms('2026-08-16T12:00:00.123456Z'),
            1786881600123)
        self.assertEqual(
            cr.parse_timestamp_ms('2026-08-16T12:00:00+01:00'),
            1786881600000 - 3_600_000)
        self.assertIsNone(cr.parse_timestamp_ms(''))
        self.assertIsNone(cr.parse_timestamp_ms(None))


class TestMatchesTheTypeScript(Base):
    """Scores computed by Elk's own code on the fixtures above."""

    def run_variant(self, variant, params):
        context = fixture_ctx(**params)
        scored = {c.status['id']: c for c in
                  (cr.score_candidate(c, context)
                   for c in fixture_candidates())}
        for i, expected in GOLDEN['raw_' + variant].items():
            self.assertAlmostEqual(scored[i].raw_score, expected,
                                   delta=1e-9, msg=i)
        adjusted = cr.apply_adjustments(list(scored.values()), context)
        ranked = cr.rank_candidates(fixture_candidates(), context)
        want = GOLDEN['ranked_' + variant]
        self.assertEqual(ids(ranked), [i for i, _ in want])
        for c, (_, score) in zip(ranked, want, strict=True):
            self.assertAlmostEqual(c.score, score, delta=1e-9)
        return {c.status['id']: c.score for c in adjusted}

    def test_with_elks_48h_cut(self):
        adjusted = self.run_variant(
            'cut', {'max_post_age_ms': cr.ELK_MAX_POST_AGE_MS})
        for i, expected in GOLDEN['adjusted_cut'].items():
            self.assertAlmostEqual(adjusted[i], expected, delta=1e-9,
                                   msg=i)

    def test_without_the_cut_catch_up_default(self):
        self.run_variant('nocut', {})
        # Only the 70 h old post differs between the two variants.
        for i, expected in GOLDEN['raw_cut'].items():
            if i != '1006':
                self.assertEqual(expected, GOLDEN['raw_nocut'][i])
        self.assertLess(GOLDEN['raw_cut']['1006'],
                        GOLDEN['raw_nocut']['1006'])

    def test_probabilities_and_reasons(self):
        context = fixture_ctx(max_post_age_ms=cr.ELK_MAX_POST_AGE_MS)
        by_id = {c.status['id']: c for c in fixture_candidates()}
        for i, expected in GOLDEN['probs_cut'].items():
            got = cr.predict_actions(by_id[i], context)
            self.assertEqual(list(got), list(expected))
            for key, value in expected.items():
                self.assertAlmostEqual(got[key], value, delta=1e-12,
                                       msg='%s %s' % (i, key))
        scored = cr.score_candidate(by_id['1002'], context)
        self.assertEqual([r.label for r in scored.reasons],
                         [label for label, _ in GOLDEN['reasons_1002']])
        for r, (_, value) in zip(scored.reasons, GOLDEN['reasons_1002'],
                                 strict=True):
            self.assertAlmostEqual(r.value, value, delta=1e-12)

    def test_features(self):
        context = fixture_ctx()
        by_id = {c.status['id']: c for c in fixture_candidates()}
        for i, expected in GOLDEN['features_cut'].items():
            f = cr.extract_ranking_features(by_id[i], context)
            for key, value in expected.items():
                self.assertAlmostEqual(getattr(f, key), value, delta=1e-9,
                                       msg='%s %s' % (i, key))

    def test_effective_weights(self):
        context = fixture_ctx()
        for c in fixture_candidates():
            w = cr.effective_weights(c, context)
            i = c.status['id']
            self.assertEqual(w['reply'], GOLDEN['reply_weight'][i], i)
            self.assertEqual(w['vqv'], GOLDEN['vqv_weight'][i], i)
        self.assertEqual(GOLDEN['reply_weight']['1005'], 20.0)
        self.assertEqual(GOLDEN['vqv_weight']['1002'], 0.05)
        self.assertEqual(GOLDEN['vqv_weight']['1007'], 0)

    def test_helpers(self):
        misc = GOLDEN['misc']
        for count, saturation, expected in misc['logNorm']:
            self.assertAlmostEqual(cr.log_norm(count, saturation), expected,
                                   delta=1e-12)
        for *args, expected in misc['lift']:
            self.assertAlmostEqual(cr.lift(*args), expected, delta=1e-12)
        values = {'top': 200, 'a': 20, 'b': 15, 'c': 10, 'd': 5, 'e': 2,
                  'bad': -1, 'worse': -5}
        for key, expected in misc['affinity']:
            self.assertAlmostEqual(cr.affinity_signal(values, key), expected,
                                   delta=1e-12)
        sums = cr.weight_sums(cr.MASTODON_WEIGHTS)
        for combined, expected in misc['offset']:
            self.assertAlmostEqual(cr.offset_score(combined, sums), expected,
                                   delta=1e-12)
        for got, expected in zip(
                (cr.weight_sums(cr.MASTODON_WEIGHTS),
                 cr.weight_sums(cr.X_WEIGHTS)), misc['sums'], strict=True):
            for key, value in expected.items():
                self.assertAlmostEqual(getattr(got, key), value, delta=1e-9)


# endregion


# region diversity fixtures


def div_status(id, author_id='author-1', text='', tags=(), language='en',
               media=(), links=(), spoiler='', in_reply_to=None,
               reblog_of=None):
    """``makeStatus`` of Elk's diversity tests (a ``StatusSpec``)."""
    link_html = ' '.join('<a href="%s">%s</a>' % (h, h) for h in links)
    tag_html = ' '.join(
        '<a href="https://example.social/tags/%s" class="hashtag">#%s</a>'
        % (t, t) for t in tags)
    return {
        'id': id,
        'created_at': '2024-01-01T00:00:00.000Z',
        'content': '<p>%s %s %s</p>' % (text, tag_html, link_html),
        'spoiler_text': spoiler,
        'language': language,
        'in_reply_to_id': in_reply_to,
        'in_reply_to_account_id': None,
        'account': {'id': author_id, 'acct': 'user' + author_id},
        'tags': [{'name': t, 'url': ''} for t in tags],
        'media_attachments': [{'id': '%s-m%d' % (id, i), 'type': t}
                              for i, t in enumerate(media)],
        'reblog': reblog_of,
    }


def div_candidate(score=1, **spec):
    return cr.Candidate(div_status(**spec), True, score=score)


FILLER_SUBJECTS = ('harbour', 'lantern', 'quarry', 'meadow', 'compass',
                   'thimble', 'lattice', 'burrow')


def filler(count):
    """Posts so IDF has a corpus to measure against."""
    return [div_candidate(
        id='filler-%d' % i, author_id='filler-author-%d' % i,
        tags=['filler-tag-%d' % i],
        text='The %s and the %s are here with a few more words so that the '
             'common function words appear in every single post of this '
             'corpus, sample %d.' % (
                 FILLER_SUBJECTS[i % 8], FILLER_SUBJECTS[(i + 3) % 8], i))
        for i in range(count)]


def similarity_in_corpus(probes, filler_count=40):
    vectors = cr.embed_candidates_sparse([*probes, *filler(filler_count)])
    return [[cr.sparse_cosine_similarity(vectors[i], vectors[j])
             for j in range(len(probes))] for i in range(len(probes))]


def author_of(candidate):
    return cr.content_status(candidate)['account']['id']


def topic_of(candidate):
    tags = cr.content_status(candidate)['tags']
    return tags[0]['name'] if tags else ''


def adjacent_runs(items, key, n):
    window = items[:n]
    return sum(1 for i in range(1, len(window))
               if key(window[i]) == key(window[i - 1]))


def mean_score(items, n):
    window = items[:n]
    return sum(c.score or 0 for c in window) / len(window)


def by_score(items):
    return sorted(items, key=lambda c: -(c.score or 0))


def tail_rho(reranked, ranked_by_score, start, end):
    """Spearman correlation of a slice of the output with score rank."""
    score_rank = {c.status['id']: i for i, c in enumerate(ranked_by_score)}
    observed = [score_rank[c.status['id']] for c in reranked[start:end]]
    order = sorted(observed)
    n = len(observed)
    total = sum((i - order.index(observed[i])) ** 2 for i in range(n))
    return 1 - (6 * total) / (n * (n * n - 1))


# endregion

# region html to text


class TestHtmlToText(Base):
    def test_paragraphs_and_breaks_become_newlines(self):
        self.assertEqual(cr.html_to_text('<p>one</p><p>two</p>'),
                         'one\ntwo')
        self.assertEqual(cr.html_to_text('<p>a<br>b<br/>c</p>'),
                         'a\nb\nc')

    def test_entities_are_decoded(self):
        self.assertEqual(cr.html_to_text('<p>caf&eacute; &amp; &lt;b&gt;'
                                         '</p>'), 'café & <b>')

    def test_links_keep_their_text_and_nothing_else(self):
        self.assertEqual(cr.html_to_text(
            '<p>see <a href="https://x.test/a"><span class="invisible">'
            'https://</span><span>x.test/a</span></a> now</p>'),
            'see https://x.test/a now')

    def test_marks_for_bold_italic_strike_and_code(self):
        self.assertEqual(cr.html_to_text(
            '<p><b>b</b> <strong>s</strong> <i>i</i> <em>e</em> '
            '<del>d</del> <code>c</code></p>'),
            '**b** **s** *i* *e* ~~d~~ `c`')
        self.assertEqual(
            cr.html_to_text('<pre><code class="language-py">x = 1</code>'
                            '</pre>'), '```py\nx = 1\n```')

    def test_mentions_use_their_data_id(self):
        self.assertEqual(cr.html_to_text(
            '<p><span data-type="mention" data-id="@a@b.c">x</span></p>'),
            '@a@b.c')
        self.assertEqual(cr.html_to_text(
            '<p><span data-type="mention" data-id="a@b.c">x</span></p>'),
            '@a@b.c')

    def test_custom_emoji_images_become_shortcodes(self):
        self.assertEqual(cr.html_to_text(
            '<p>hi <img class="custom-emoji" alt="blobcat" src="x"></p>'),
            'hi :blobcat:')
        self.assertEqual(cr.html_to_text(
            '<p><img class="custom-emoji" data-emoji-id="a b"></p>'),
            'a b')
        self.assertEqual(cr.html_to_text('<p><img src="x"></p>'), '')

    def test_tolerates_unclosed_and_stray_tags(self):
        self.assertEqual(cr.html_to_text('<p>open <b>bold'), 'open **bold**')
        self.assertEqual(cr.html_to_text('text</i></p>'), 'text')
        self.assertEqual(cr.html_to_text(''), '')

    def test_a_closed_pre_does_not_swallow_what_follows(self):
        self.assertEqual(cr.html_to_text('<pre>a</pre><code>b</code>'),
                         'a`b`')
        self.assertEqual(cr.html_to_text('<pre><code>a</code></pre>'),
                         '```\na\n```')

    def test_only_the_first_language_prefix_is_removed(self):
        self.assertEqual(cr.html_to_text(
            '<pre><code class="language-language-x">a</code></pre>'),
            '```language-x\na\n```')

    def test_a_stray_end_tag_closes_the_nearest_matching_open_one(self):
        self.assertEqual(cr.html_to_text('<p><b>a<i>b</b>c</i></p>'),
                         '**a*b***c')

    def test_trims_with_javascript_whitespace(self):
        self.assertEqual(cr.html_to_text('﻿<p> x </p>　'), 'x')


# endregion

# region tokenizer


class TestTokenizer(Base):
    def test_strips_markup_urls_mentions_and_hashtags(self):
        tokens = cr.tokenize_text(
            'The quick brown fox https://x.example/a @alice@fed.example '
            '#tagged jumped')
        for word in ('quick', 'brown', 'jumped'):
            self.assertIn(word, tokens)
        for word in ('https', 'alice', 'tagged'):
            self.assertNotIn(word, tokens)

    def test_keeps_function_words_rather_than_using_a_stop_list(self):
        self.assertIn('the', cr.tokenize_text('the and for with'))
        self.assertIn('new', cr.tokenize_text('Visiting New York in the '
                                              'new year'))

    def test_returns_distinct_tokens_in_first_seen_order(self):
        self.assertEqual(cr.tokenize_text('bb aa bb cc aa'),
                         ['bb', 'aa', 'cc'])

    def test_drops_single_latin_characters_but_keeps_cjk_ones(self):
        self.assertEqual(cr.tokenize_text('a b cc'), ['cc'])
        self.assertIn('猫', cr.tokenize_text('猫 x'))

    def test_counts_length_in_utf16_units_like_javascript(self):
        # one astral letter is two UTF-16 units, so it passes the length-2
        # gate that a lone BMP letter does not
        self.assertEqual(cr.tokenize_text('\U0001D41A'), ['\U0001D41A'])

    def test_down_weights_ubiquitous_tokens_in_any_language(self):
        corpus = [div_candidate(
            id='d%d' % i, author_id='a%d' % i,
            text='het is een van de bir ve bu için onderwerp%d kavram%d'
                 % (i, i)) for i in range(30)]
        idf = cr.compute_token_idf(
            [cr.extract_candidate_features(c.status) for c in corpus])
        ubiquitous = idf['het']
        distinctive = idf['onderwerp0']
        self.assertGreater(ubiquitous, 0)
        self.assertGreater(distinctive, ubiquitous * 3)
        self.assertAlmostEqual(ubiquitous, math.log(1 + 30 / 31), places=12)

    def test_bounds_work_by_input_length(self):
        huge = 'lorem ipsum dolor sit amet consectetur ' * 20000
        tokens = cr.tokenize_text(huge)
        # the head and the tail are kept; a slice may cut a word in two
        self.assertLessEqual(
            {'lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur'},
            set(tokens))
        self.assertLessEqual(len(tokens), 8)

    def test_samples_the_head_and_the_tail_of_a_long_text(self):
        text = ('headword ' + 'pad ' * 600 + 'midword ' + 'pad ' * 600
                + 'tailword')
        self.assertGreater(len(text), 4000)
        tokens = cr.tokenize_text(text)
        self.assertIn('headword', tokens)
        self.assertIn('tailword', tokens)
        self.assertNotIn('midword', tokens)

    def test_truncates_only_texts_longer_than_4000_units(self):
        # 'zqq' starts at unit 2000, the one unit a 4001-unit text loses.
        head = 'pad ' * 500 + 'zqq '
        self.assertIn('zqq', cr.tokenize_text(head + 'y' * (4000 - 2004)))
        tokens = cr.tokenize_text(head + 'y' * (4001 - 2004))
        self.assertNotIn('zqq', tokens)
        self.assertIn('qq', tokens)

    def test_measures_the_limit_in_utf16_units_not_code_points(self):
        # 998 astral letters are 1996 units: 4000 in all, not truncated;
        # 999 make 4002 (though only 3003 code points), truncated.
        head = 'pad ' * 500 + 'zqq '
        self.assertIn('zqq', cr.tokenize_text(head + '\U0001D41A' * 998))
        self.assertNotIn('zqq', cr.tokenize_text(head + '\U0001D41A' * 999))

    def test_caps_the_distinct_tokens(self):
        letters = 'abcdefghijklmnopqrstuvwxyz'
        text = ' '.join(a + b for a in letters for b in letters)
        self.assertLess(len(text), 4000)
        self.assertEqual(len(cr.tokenize_text(text)), 512)


class TestMultilingual(Base):
    def test_segments_japanese_into_bigrams_without_a_segmenter(self):
        tokens = cr.tokenize_text('今日の選挙結果について議論しています')
        self.assertGreater(len(tokens), 3)
        self.assertEqual(tokens[:3], ['今日', '日の', 'の選'])

        probes = [
            div_candidate(id='ja1', author_id='a', language='ja',
                          text='今日の選挙結果について議論しています'),
            div_candidate(id='ja2', author_id='b', language='ja',
                          text='選挙結果が発表されました'),
            div_candidate(id='ja3', author_id='c', language='ja',
                          text='今日は寿司を食べに行きました'),
        ]
        sim = similarity_in_corpus(probes)
        self.assertGreater(sim[0][1], sim[0][2] + 0.05)

    def test_segments_chinese_into_bigrams(self):
        tokens = cr.tokenize_text('今天的选举结果引发了广泛讨论')
        self.assertGreater(len(tokens), 3)
        probes = [
            div_candidate(id='zh1', author_id='a', language='zh',
                          text='今天的选举结果引发了广泛讨论'),
            div_candidate(id='zh2', author_id='b', language='zh',
                          text='选举结果已经公布了'),
            div_candidate(id='zh3', author_id='c', language='zh',
                          text='我今天去公园散步看花'),
        ]
        sim = similarity_in_corpus(probes)
        self.assertGreater(sim[0][1], sim[0][2] + 0.05)

    def test_bigrams_of_a_lone_character_are_the_character(self):
        self.assertEqual(cr.tokenize_text('選挙結果').count('選挙'), 1)
        self.assertEqual(cr.tokenize_text('選挙結果'),
                         ['選挙', '挙結', '結果'])

    def test_keeps_combining_marks_attached(self):
        hindi = cr.tokenize_text('नमस्ते दुनिया आप कैसे हैं')
        self.assertGreater(len(hindi), 2)
        self.assertIn('नमस्ते', hindi)
        tamil = cr.tokenize_text('தமிழ் மொழி மிகவும் அழகானது')
        self.assertGreater(len(tamil), 2)
        self.assertIn('தமிழ்', tamil)
        hebrew = cr.tokenize_text('שָׁלוֹם עוֹלָם יָפֶה')
        self.assertGreater(len(hebrew), 2)

    def test_treats_nfc_and_nfd_as_the_same_text(self):
        import unicodedata
        for text in ('Grüße aus München', 'Tiếng Việt rất đẹp',
                     'čaj s příchutí'):
            nfc = unicodedata.normalize('NFC', text)
            nfd = unicodedata.normalize('NFD', text)
            self.assertNotEqual(nfd, nfc)
            self.assertEqual(cr.tokenize_text(nfd), cr.tokenize_text(nfc))
        probes = [
            div_candidate(id='nfc', author_id='a', language='de',
                          text=unicodedata.normalize(
                              'NFC', 'Grüße aus München im Frühling')),
            div_candidate(id='nfd', author_id='a', language='de',
                          text=unicodedata.normalize(
                              'NFD', 'Grüße aus München im Frühling')),
        ]
        self.assertGreater(similarity_in_corpus(probes)[0][1], 0.999)

    def test_hashtags_and_emoji_are_removed_in_any_script(self):
        # The mention pattern is ASCII-only, as in JavaScript, so the
        # handle's own letters survive here.
        self.assertEqual(
            cr.tokenize_text('привет #тег мир \U0001F600 @иван'),
            ['привет', 'мир', 'иван'])

    def test_matches_the_typescript_tokens(self):
        from_ts = GOLDEN['tokens']
        texts = TOKEN_TEXTS
        self.assertEqual(len(from_ts), len(texts))
        for text, expected in zip(texts, from_ts, strict=True):
            self.assertEqual(cr.tokenize_text(text), expected, text)


DIV_FIXTURES = [
    (div_status('d1', 'alice', 'Shipping the caching layer today, latency '
                'down', tags=('backend',),
                links=('https://news.example/a/b/',)), 0.9),
    (div_status('d2', 'alice', 'Made sourdough focaccia with rosemary',
                tags=('baking',), media=('image',)), 0.8),
    (div_status('d3', 'carol', 'Shipping the caching layer today, latency '
                'down', tags=('backend', 'Perf'), language='de'), 0.85),
    (div_status('d4', 'dave', 'Grüße aus München \U0001F600 and '
                'http://x.y/z @bob@fed.example #tagged',
                in_reply_to='d1'), 0.7),
    (div_status('d5', 'erin', 'Tiếng Việt rất đẹp 今日の選挙結果',
                spoiler='nsfw? maybe', language='vi'), 0.6),
    (div_status('d6', 'frank', 'reply one', in_reply_to='d1'), 0.55),
    (div_status('d7', 'gina', 'reply two', in_reply_to='d6'), 0.5),
    (div_status('d8', 'hank', 'reply three', in_reply_to='d1'), 0.45),
    (div_status('d9', 'ivy', 'boosted wrapper', reblog_of=div_status(
        'd1', 'alice', 'Shipping the caching layer today, latency down',
        tags=('backend',), links=('https://news.example/a/b/',))), 0.4),
    (div_status('d10', 'jack', '', language=None), 0.35),
    (div_status('d11', 'kim', 'Rust borrow checker finally clicked',
                tags=('rustlang',),
                links=('https://www.crates.example/q?x=1',
                       'https://example.social/@zed')), 0.3),
    (div_status('d12', 'lee', 'Shipping the caching layer today, latency '
                'down', tags=('backend',)), 0.25),
]


def div_fixture_candidates():
    return [cr.Candidate(s, True, score=score) for s, score in DIV_FIXTURES]


TOKEN_TEXTS = [
    'The quick brown fox https://x.example/a @alice@fed.example #tagged '
    'jumped',
    'Grüße aus München',
    'Tiếng Việt rất đẹp',
    '今日の選挙結果について議論しています',
    'a \U0001D41A \U0001F600 x ünï नमस्ते दुनिया',
    'het is een van de bir ve bu için',
]


# endregion

# region embedding


class TestEmbedding(Base):
    def test_produces_unit_norm_vectors_of_the_declared_dimension(self):
        vector = cr.embed_candidate(div_candidate(
            id='1', text='Reverse engineering the home timeline ranker',
            tags=['algorithms']))
        self.assertEqual(len(vector), cr.EMBEDDING_DIM)
        self.assertGreaterEqual(cr.EMBEDDING_DIM, 4096)
        self.assertClose(cr.cosine_similarity(vector, vector), 1)
        self.assertClose(math.sqrt(sum(x * x for x in vector)), 1)

    def test_scores_identical_posts_near_one_and_unrelated_near_zero(self):
        base = dict(
            id='a', author_id='alice',
            text='A long walk through the mountains, photographing alpine '
                 'flowers at dawn',
            tags=['hiking', 'photography'], media=['image'],
            links=['https://alpinejournal.example/flowers'])
        probes = [
            div_candidate(**base),
            div_candidate(**{**base, 'id': 'b'}),
            div_candidate(
                id='c', author_id='bob', language='de',
                text='Rust borrow checker finally clicked while writing a '
                     'lock-free queue',
                tags=['rustlang', 'concurrency'],
                links=['https://crates.example/queue']),
        ]
        sim = similarity_in_corpus(probes)
        self.assertGreater(sim[0][1], 0.999)
        self.assertLess(abs(sim[0][2]), 0.05)

    def test_ranks_same_topic_above_unrelated_and_same_author_only_just(
            self):
        probes = [
            div_candidate(id='a', author_id='alice', tags=['backend'],
                          text='Shipping the new caching layer today, '
                               'latency down by half'),
            div_candidate(id='b', author_id='alice', tags=['baking'],
                          text='Made sourdough focaccia with rosemary and '
                               'olives'),
            div_candidate(id='c', author_id='carol', tags=['backend'],
                          text='Shipping the new caching layer today, '
                               'latency down by half'),
            div_candidate(id='d', author_id='dave', tags=['birds'],
                          text='Watched a heron fish the canal for twenty '
                               'minutes'),
        ]
        sim = similarity_in_corpus(probes)
        _, same_author, same_topic, unrelated = sim[0]
        self.assertGreater(same_topic, 0.8)
        self.assertLess(unrelated, 0.05)
        self.assertGreater(same_author, unrelated)
        self.assertLess(same_author, same_topic / 3)

    def test_keeps_hash_collision_noise_below_the_weakest_real_signal(self):
        posts = [div_candidate(
            id='n%d' % i, author_id='au%d' % i, tags=['tg%d' % i],
            language=None,
            text=' '.join('zzq%d%s' % (i, w) for w in (
                'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta')))
            for i in range(120)]
        vectors = cr.embed_candidates_sparse(posts)
        noise = sorted(
            abs(cr.sparse_cosine_similarity(vectors[i], vectors[j]))
            for i in range(len(posts)) for j in range(i + 1, len(posts)))
        shared = similarity_in_corpus([
            div_candidate(id='s1', author_id='x', tags=['climate'],
                          language=None, text='aaa bbb ccc'),
            div_candidate(id='s2', author_id='y', tags=['climate'],
                          language=None, text='ddd eee fff'),
        ])[0][1]
        p99 = noise[min(len(noise) - 1, math.floor(0.99 * len(noise)))]
        self.assertLess(p99, shared / 4)
        confusable = sum(1 for x in noise if x > shared / 2)
        self.assertLess(confusable / len(noise), 0.005)

    def test_keys_links_on_the_story_not_just_the_outlet(self):
        story = 'https://news.example/2026/03/election-results-explained'
        other = 'https://news.example/2026/03/badger-census-published'
        mirror = ('https://otherpaper.example/2026/03/'
                  'election-results-explained')
        sim = similarity_in_corpus([
            div_candidate(id='a', author_id='bot1', text='headline',
                          links=[story]),
            div_candidate(id='b', author_id='bot1', text='headline',
                          links=[other]),
            div_candidate(id='c', author_id='bot2', text='headline',
                          links=[mirror]),
        ])
        self.assertLess(sim[0][1], 0.95)
        self.assertGreater(sim[0][2], 0)

    def test_samples_tokens_across_the_post_not_just_its_opening(self):
        shared = ' '.join('sharedword%d' % i for i in range(400))
        sim = similarity_in_corpus([
            div_candidate(id='a', author_id='x',
                          text='alpha beta gamma delta ' + shared),
            div_candidate(id='b', author_id='x',
                          text='epsilon zeta eta theta ' + shared),
        ])
        self.assertGreater(sim[0][1], 0.85)

    def test_handles_degenerate_vectors_and_empty_posts(self):
        self.assertEqual(cr.cosine_similarity([0, 0, 0], [1, 2, 3]), 0)
        self.assertEqual(cr.cosine_similarity([], []), 0)
        self.assertClose(cr.cosine_similarity([1, 0], [0, 1]), 0)
        self.assertClose(cr.cosine_similarity([1, 0], [-1, 0]), -1)

        empty = div_candidate(id='empty', author_id='', text='',
                              language=None)
        v1 = cr.embed_candidate(empty)
        v2 = cr.embed_candidate(empty)
        self.assertClose(cr.cosine_similarity(v1, v2), 1)
        other = cr.embed_candidate(div_candidate(
            id='other', author_id='', text='', language=None))
        self.assertLess(abs(cr.cosine_similarity(v1, other)), 0.99)

    def test_a_boost_is_embedded_as_the_post_it_boosts(self):
        inner = div_status('500', 'alice', 'original post', tags=['t'])
        boost = cr.Candidate(div_status('900', 'bob', 'ignored',
                                        reblog_of=inner), True)
        plain = cr.Candidate(inner, True)
        self.assertEqual(cr.embed_candidate(boost),
                         cr.embed_candidate(plain))

    def test_hashes_utf16_code_units_like_charcodeat(self):
        # FNV-1a over UTF-16 code units; 'a' is 0x61.
        h = (0x811C9DC5 ^ 0x61) * 0x01000193 & 0xFFFFFFFF
        self.assertEqual(cr._hash_string('a', 0x811C9DC5), h)
        # an astral character hashes as its two surrogates
        h = 0x811C9DC5
        for unit in (0xD83D, 0xDE00):
            h = (h ^ unit) * 0x01000193 & 0xFFFFFFFF
        self.assertEqual(cr._hash_string('\U0001F600', 0x811C9DC5), h)

    def test_extracts_the_signals_of_the_original_post(self):
        features = cr.extract_candidate_features(div_status(
            '1', 'alice', 'Hello wörld \U0001F600', tags=['Foo', 'bar'],
            media=['image', 'video', 'image'],
            links=['https://www.example.com/a/b/', 'https://example.com/c',
                   'https://mastodon.social/@someone',
                   'https://mastodon.social/tags/x'],
            spoiler='cw text', language='EN'))
        self.assertEqual(features.hashtags, ['foo', 'bar'])
        self.assertEqual(features.media_types, ['image', 'video'])
        self.assertEqual(features.link_paths,
                         ['example.com/a/b', 'example.com/c'])
        self.assertEqual(features.link_hosts, ['example.com'])
        self.assertEqual(features.emoji, ['\U0001F600'])
        self.assertEqual(features.author_id, 'alice')
        self.assertEqual(features.spoiler_tokens, ['cw', 'text'])
        self.assertEqual(features.tokens[:2], ['cw', 'text'])
        self.assertEqual(features.fallback_key, '1')

    def test_falls_back_to_hashtags_found_in_the_text(self):
        status = div_status('1', text='x')
        status['tags'] = []
        status['content'] = '<p>fresh #Idea and #идея</p>'
        features = cr.extract_candidate_features(status)
        self.assertEqual(features.hashtags, ['idea', 'идея'])

    def test_picks_up_the_cards_url_as_a_link(self):
        status = div_status('1', text='x')
        status['card'] = {'url': 'https://news.example/story/'}
        features = cr.extract_candidate_features(status)
        self.assertEqual(features.link_paths, ['news.example/story'])

    def test_selects_48_tokens_by_stride_without_a_corpus(self):
        tokens = ['t%02d' % i for i in range(60)]
        picked = cr._select_tokens(tokens)
        self.assertEqual(len(picked), 48)
        self.assertEqual(picked, [tokens[math.floor(i * 1.25)]
                                  for i in range(48)])
        short = tokens[:48]
        self.assertIs(cr._select_tokens(short), short)

    def test_selects_the_48_most_distinctive_tokens_with_a_corpus(self):
        tokens = ['t%02d' % i for i in range(60)]
        # t59..t50 are the commonest (lowest idf); ties break alphabetically
        idf = {t: (1.0 if int(t[1:]) < 50 else 0.1) for t in tokens}
        picked = cr._select_tokens(tokens, idf)
        self.assertEqual(len(picked), 48)
        self.assertEqual(picked, tokens[:48])
        # an unknown token ranks last
        self.assertNotIn('zz', cr._select_tokens(tokens + ['zz'], idf))

    def test_a_token_missing_from_the_idf_weighs_one(self):
        features = cr.CandidateFeatures(tokens=['known', 'unknown'])
        vector = cr.sparse_embed(features, {'known': 2.0})
        weights = sorted(abs(v) for v in vector.values)
        self.assertClose(weights[1] / weights[0], 2.0)

    def test_a_featureless_post_gets_four_seeded_unit_directions(self):
        features = cr.CandidateFeatures(fallback_key='post-1')
        vector = cr.sparse_embed(features)
        self.assertEqual(len(vector.indices), 4)
        for value in vector.values:
            self.assertClose(abs(value), 0.5, 12)
        again = cr.sparse_embed(features)
        self.assertEqual(vector, again)
        other = cr.sparse_embed(cr.CandidateFeatures(fallback_key='post-2'))
        self.assertNotEqual(vector.indices, other.indices)
        self.assertEqual(
            cr.sparse_embed(cr.CandidateFeatures()).indices,
            cr.sparse_embed(cr.CandidateFeatures(fallback_key='')).indices)

    def test_family_weights_set_the_relative_pull_of_each_family(self):
        # One member per family, so each bucket value is its family weight
        # over the norm of the whole vector.
        features = cr.CandidateFeatures(
            hashtags=['h'], media_types=['image'], emoji=['x'],
            spoiler_tokens=['cw'], link_paths=['a.b/c'],
            link_hosts=['a.b'], tokens=['tok'], author_id='au',
            language='EN')
        vector = cr.sparse_embed(features)
        expected = sorted([0.4, 1.2, 0.3, 0.4, 0.3, 0.8 * 0.75,
                           0.8 * 0.25, 1.2, 0.35])
        norm = math.sqrt(sum(w * w for w in expected))
        got = sorted(abs(v) * norm for v in vector.values)
        for a, b in zip(got, expected, strict=True):
            self.assertAlmostEqual(a, b, places=9)

    def test_at_most_16_distinct_emoji_are_kept(self):
        emoji = [chr(0x1F600 + i) for i in range(20)]
        features = cr.extract_candidate_features(div_status(
            '1', text=''.join(emoji + emoji[:3])))
        self.assertEqual(features.emoji, emoji[:16])

    def test_matches_the_typescript_embeddings(self):
        candidates = div_fixture_candidates()
        vectors = cr.embed_candidates_sparse(candidates)
        for index, expected in GOLDEN['embed'].items():
            got = sorted(zip(vectors[index].indices,
                             vectors[index].values, strict=True))
            self.assertEqual([i for i, _ in got], [i for i, _ in expected])
            for (_, a), (_, b) in zip(got, expected, strict=True):
                self.assertAlmostEqual(a, b, delta=1e-12)
        for i, j, expected in GOLDEN['cos']:
            self.assertAlmostEqual(
                cr.sparse_cosine_similarity(vectors[i], vectors[j]),
                expected, delta=1e-12)
        dense = cr.embed_candidates(candidates[:2])
        self.assertAlmostEqual(
            cr.cosine_similarity(dense[0], dense[1]),
            cr.sparse_cosine_similarity(vectors[0], vectors[1]), delta=1e-12)
        idf = cr.compute_token_idf([cr.extract_candidate_features(c.status)
                                    for c in candidates])
        for token, expected in GOLDEN['idf']:
            self.assertAlmostEqual(idf[token], expected, delta=1e-12)


# endregion


# region conversation dedupe


class TestDedupeConversations(Base):
    # max_per_conversation=1 is the faithful port of home-mixer's filter.

    def kept(self, candidates, n=1):
        return cr.dedupe_conversations(candidates, n)

    def test_keeps_the_highest_scoring_branch_of_a_conversation(self):
        kept = self.kept([
            div_candidate(0.5, id='10', in_reply_to='1'),
            div_candidate(0.9, id='11', in_reply_to='10'),
            div_candidate(0.7, id='12', in_reply_to='1'),
            div_candidate(0.3, id='20', in_reply_to='2'),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(ids(kept)), ['11', '20'])
        self.assertEqual(kept[0].status['id'], '11')

    def test_links_sibling_replies_to_an_absent_parent(self):
        kept = self.kept([
            div_candidate(0.2, id='r1', in_reply_to='root'),
            div_candidate(0.8, id='r2', in_reply_to='root'),
            div_candidate(0.4, id='r3', in_reply_to='root'),
        ])
        self.assertEqual(ids(kept), ['r2'])

    def test_keeps_the_first_occurrence_when_scores_tie(self):
        kept = self.kept([
            div_candidate(1, id='1', in_reply_to='42'),
            div_candidate(1, id='2', in_reply_to='42'),
        ])
        self.assertEqual(ids(kept), ['1'])

    def test_does_not_dedup_standalone_posts(self):
        kept = cr.dedupe_conversations([
            div_candidate(1, id='1'), div_candidate(2, id='2'),
            div_candidate(3, id='3')])
        self.assertEqual(len(kept), 3)

    def test_collapses_reblogs_onto_the_post_they_boost(self):
        original = div_status('500', 'alice', 'original post')
        kept = self.kept([
            div_candidate(0.4, id='900', author_id='bob',
                          reblog_of=original),
            div_candidate(0.8, id='901', author_id='carol',
                          reblog_of=original),
            div_candidate(0.1, id='600', author_id='dave',
                          text='unrelated'),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0].status['id'], '901')
        self.assertEqual(kept[1].status['id'], '600')

    def test_collapses_a_reply_onto_the_conversation_of_a_boosted_post(self):
        original = div_status('500', 'alice', 'original post')
        kept = self.kept([
            div_candidate(0.5, id='900', author_id='bob',
                          reblog_of=original),
            div_candidate(0.9, id='11', author_id='carol',
                          in_reply_to='500'),
        ])
        self.assertEqual(ids(kept), ['11'])

    def test_uses_the_reply_target_of_the_boosted_post(self):
        original = div_status('500', 'alice', 'x', in_reply_to='root')
        kept = self.kept([
            div_candidate(0.5, id='900', author_id='bob',
                          reblog_of=original),
            div_candidate(0.9, id='11', author_id='carol',
                          in_reply_to='root'),
        ])
        self.assertEqual(ids(kept), ['11'])

    def test_preserves_score_ordering_of_the_survivors(self):
        kept = self.kept([
            div_candidate(3, id='a'),
            div_candidate(2, id='b', in_reply_to='a'),
            div_candidate(1, id='c'),
        ])
        self.assertEqual(ids(kept), ['a', 'c'])

    def test_caps_rather_than_collapses_keeping_the_best_n_branches(self):
        thread = [div_candidate(1 - i * 0.1, id='t%d' % i,
                                author_id='a%d' % i, in_reply_to='root')
                  for i in range(8)]
        capped = cr.dedupe_conversations(thread)
        self.assertEqual(ids(capped), ['t0', 't1', 't2'])
        wider = cr.dedupe_conversations(thread, 5)
        self.assertEqual(ids(wider), ['t0', 't1', 't2', 't3', 't4'])
        # A cap below 1 is meaningless and is clamped, never zero-length.
        self.assertEqual(len(cr.dedupe_conversations(thread, 0)), 1)
        self.assertEqual(cr.DEFAULT_MAX_PER_CONVERSATION, 3)

    def test_survivors_take_the_slots_in_input_order_best_branch_first(self):
        # Input order is not score order: slots 0..2 are filled with the
        # three best branches, best first.
        thread = [div_candidate(s, id='t%d' % i, author_id='a%d' % i,
                                in_reply_to='root')
                  for i, s in enumerate([0.1, 0.9, 0.2, 0.8, 0.7])]
        capped = cr.dedupe_conversations(thread)
        self.assertEqual(ids(capped), ['t1', 't3', 't4'])

    def test_always_collapses_identical_posts_even_under_a_cap(self):
        original = div_status('500', 'alice', 'original')
        kept = cr.dedupe_conversations([
            div_candidate(0.4, id='900', author_id='bob',
                          reblog_of=original),
            div_candidate(0.9, id='901', author_id='carol',
                          reblog_of=original),
            div_candidate(0.6, id='902', author_id='dave',
                          reblog_of=original),
        ])
        self.assertEqual(ids(kept), ['901'])

    def test_scores_fall_back_to_the_raw_score_then_zero(self):
        a = cr.Candidate(div_status('a', in_reply_to='r'), True,
                         raw_score=0.2)
        b = cr.Candidate(div_status('b', in_reply_to='r'), True)
        c = cr.Candidate(div_status('c', in_reply_to='r'), True, score=0.5,
                         raw_score=0.1)
        self.assertEqual(cr.candidate_score(a), 0.2)
        self.assertEqual(cr.candidate_score(b), 0)
        self.assertEqual(cr.candidate_score(c), 0.5)
        self.assertEqual(ids(cr.dedupe_conversations([a, b, c], 1)), ['c'])

    def test_does_not_gut_a_realistic_reply_heavy_feed(self):
        feed = []
        for i in range(300):
            score = 1 - i / 300
            if i % 4 == 0:
                feed.append(div_candidate(
                    score, id='p%d' % i, author_id='author-%d' % (i % 60),
                    in_reply_to='conversation-%d' % ((i // 4) % 12)))
            elif i % 3 == 0:
                feed.append(div_candidate(
                    score, id='p%d' % i, author_id='booster-%d' % i,
                    reblog_of=div_status('popular-%d' % (i % 25),
                                         'star-%d' % (i % 25))))
            else:
                feed.append(div_candidate(
                    score, id='p%d' % i,
                    author_id='author-%d' % (i % 60)))
        collapsed = cr.dedupe_conversations(feed, 1)
        capped = cr.dedupe_conversations(feed)
        self.assertLess(len(collapsed) / len(feed), 0.65)
        self.assertGreater(len(capped), len(collapsed))
        self.assertGreater(len(capped) / len(feed), 0.7)
        identities = [c.status['reblog']['id'] if c.status['reblog']
                      else c.status['id'] for c in capped]
        self.assertEqual(len(set(identities)), len(identities))

    def test_is_a_no_op_on_zero_or_one_candidates(self):
        self.assertEqual(cr.dedupe_conversations([]), [])
        self.assertEqual(
            len(cr.dedupe_conversations([div_candidate(id='1')])), 1)

    def test_does_not_mutate_its_input(self):
        feed = [div_candidate(0.5, id='a', in_reply_to='r'),
                div_candidate(0.9, id='b', in_reply_to='r')]
        snapshot = list(feed)
        cr.dedupe_conversations(feed, 1)
        self.assertEqual(feed, snapshot)

    def test_matches_the_typescript_results(self):
        candidates = div_fixture_candidates()
        self.assertEqual(ids(cr.dedupe_conversations(candidates)),
                         GOLDEN['dedupe3'])
        self.assertEqual(ids(cr.dedupe_conversations(candidates, 1)),
                         GOLDEN['dedupe1'])


# endregion

# region rerank


def clustered_feed():
    """Five authors, five topics, scored so a score sort clusters them."""
    authors = ['alice', 'bob', 'carol', 'dave', 'erin']
    topics = ['rustlang', 'baking', 'birdwatching', 'typography', 'trains']
    bodies = [
        'borrow checker lifetimes async runtime executor scheduling',
        'sourdough starter hydration crumb oven spring rye levain',
        'heron kingfisher canal reeds binoculars migration ringing',
        'kerning ligature grotesque serif metrics hinting variable font',
        'catenary bogie signalling timetable gauge sleeper depot',
    ]
    return [div_candidate(
        1 - (a * 8 + i) * 0.01, id='%d-%d' % (a, i), author_id=authors[a],
        tags=[topics[a]], text='%s note %d' % (bodies[a], i))
        for a in range(5) for i in range(8)]


def varied_feed():
    nouns = ['harbour', 'lantern', 'quarry', 'meadow', 'compass', 'thimble',
             'lattice', 'burrow', 'cinder', 'marsh', 'anvil', 'orchard',
             'ferry', 'kettle', 'pylon', 'satchel', 'willow', 'grotto',
             'beacon', 'trellis']
    verbs = ['collapsed', 'flourished', 'drifted', 'echoed', 'hardened',
             'scattered', 'gleamed', 'settled', 'vanished', 'unfolded']
    return [div_candidate(
        1 / (1 + i * 0.05), id='v%d' % i, author_id='author-%d' % (i % 40),
        tags=['topic-%d' % (i % 25)],
        text='%s %s %s %s %s beside the %s' % (
            nouns[i % 20], verbs[i % 10], nouns[(i * 7) % 20],
            nouns[(i * 13) % 20], verbs[(i * 3) % 10],
            nouns[(i * 11) % 20])) for i in range(200)]


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
