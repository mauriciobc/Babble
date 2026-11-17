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


# region weights

from testkit.catchuprank import (Base,  # noqa: E402
                                 cr)
from testkit.catchuprank_fixtures import (TOKEN_TEXTS,
                                          div_candidate,
                                          similarity_in_corpus)
from testkit.catchuprank_golden import GOLDEN  # noqa: E402

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

    def test_deeply_nested_html_returns_empty_instead_of_raising(self):
        # One pathological post must not abort the ranking of the whole
        # snapshot; Elk's try/catch returns '' here.
        self.assertEqual(cr.html_to_text('<div>' * 5000 + 'x'), '')
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


# endregion

# region embedding


if __name__ == '__main__':
    unittest.main()
