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

from testkit.catchuprank import (Base,  # noqa: E402
                                 cr, ids)
from testkit.catchuprank_fixtures import (div_candidate,
                                          div_fixture_candidates, div_status,
                                          similarity_in_corpus)
from testkit.catchuprank_golden import GOLDEN  # noqa: E402

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

    def test_the_hash_cache_changes_nothing_and_stays_bounded(self):
        # Keys repeat across a pool, so _hash_string is cached; the cache
        # must return exactly what the plain function does, for a lone
        # surrogate as well, and must not grow without limit.
        plain = cr._hash_string.__wrapped__
        seeds = (0x811C9DC5, 0x9E3779B1)
        keys = ('l:en', 'a:109', 't:wörld', '\U0001F600', '\ud83d', '')
        for _round in range(2):
            for key in keys:
                for seed in seeds:
                    self.assertEqual(cr._hash_string(key, seed),
                                     plain(key, seed))
        self.assertIsNotNone(cr._hash_string.cache_info().maxsize)

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


if __name__ == '__main__':
    unittest.main()
