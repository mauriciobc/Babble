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
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import subprocess
import sys
import types
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)


























# Computed by the TypeScript (see the module docstring).




# region weights

from testkit.catchuprank import (HOUR, NOW, Base,  # noqa: E402
                                 cr, ctx, ids, make_candidate, make_status, raw)
from testkit.catchuprank_fixtures import (FIXTURE_IN_NETWORK, fixture_candidates,
                                          fixture_ctx, make_fixture_statuses)
from testkit.catchuprank_golden import GOLDEN  # noqa: E402

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

    def test_rank_candidates_returns_typed_results_without_mutating_inputs(self):
        candidates = [make_candidate(id='s1', author_id='A'),
                      make_candidate(id='s2', author_id='B', favourites=500)]
        original_statuses = [candidate.status for candidate in candidates]
        ranked = cr.rank_candidates(candidates, ctx())
        self.assertTrue(all(isinstance(c, cr.ScoredCandidate)
                            for c in ranked))
        self.assertCountEqual([candidate.status for candidate in ranked],
                              original_statuses)
        self.assertEqual([candidate.status for candidate in candidates],
                         original_statuses)
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

    def test_rank_returns_immutable_typed_scored_candidate_records(self):
        source = make_candidate(id='scored')
        result = cr.rank([source], ctx())[0]
        self.assertIsInstance(result, cr.ScoredCandidate)
        self.assertIs(result.status, source.status)
        self.assertIsNotNone(result.probabilities)
        self.assertIsNotNone(result.raw_score)
        self.assertIsNotNone(result.score)
        self.assertIsNotNone(result.reasons)
        self.assertIsNone(source.score)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.score = 0


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
























# endregion

# region html to text


if __name__ == '__main__':
    unittest.main()
