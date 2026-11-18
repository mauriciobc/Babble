# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Catch Up data layer (no display, no network).

``src.catchup.digest`` imports nothing from GTK or ``gi``, so this runs
anywhere Python does. It pins the Phanpy behaviour that was ported
(categories, link grouping and trimming, boost deduplication, density,
``binByTime``), the fixed fetch stop rule, the trends merge and its fallbacks,
and the tile allocation and dense packing of CATCH-UP.md section 6.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import random
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit.catchupdigest import (boost, cd,  # noqa: E402
                                   link_card, make_news,
                                   make_tags, occupancy, ranked_posts,
                                   status)

class TierTest(unittest.TestCase):
    def tiers_of(self, n, scores=None):
        posts, default = ranked_posts(n)
        tiers = cd.assign_tiers(posts, scores or default)
        return [tiers[p['id']] for p in posts]

    def test_empty(self):
        self.assertEqual(cd.assign_tiers([], {}), {})

    def test_fewer_than_ten_only_first_is_hero(self):
        for n in range(1, 10):
            got = self.tiers_of(n)
            self.assertEqual(got.count('hero'), 1, n)
            self.assertEqual(got[0], 'hero')
        got = self.tiers_of(9)
        self.assertEqual(got.count('medium'), 2)
        self.assertEqual(self.tiers_of(1), ['hero'])
        self.assertEqual(self.tiers_of(2), ['hero', 'compact'])

    def test_first_is_hero_even_with_a_low_score(self):
        posts, scores = ranked_posts(5)
        scores[posts[0]['id']] = -1
        tiers = cd.assign_tiers(posts, scores)
        self.assertEqual(tiers[posts[0]['id']], 'hero')
        self.assertEqual(sum(t == 'hero' for t in tiers.values()), 1)

    def test_ten_and_more_use_percentiles(self):
        expected = {10: (1, 2), 11: (2, 2), 20: (2, 4), 36: (4, 7),
                    100: (10, 20)}
        for n, (heroes, mediums) in expected.items():
            got = self.tiers_of(n)
            self.assertEqual(got.count('hero'), heroes, n)
            self.assertEqual(got.count('medium'), mediums, n)
            self.assertEqual(got.count('compact'), n - heroes - mediums)

    def test_matches_linear_percentile_definition(self):
        """S >= P90 / S >= P70 with linear interpolation, on distinct
        scores, in an order that is not the score order."""
        n = 37
        posts, _ = ranked_posts(n)
        values = [((i * 17) % n) / n for i in range(n)]
        scores = {p['id']: v for p, v in zip(posts, values, strict=True)}

        def pct(q):
            s = sorted(values)
            k = (n - 1) * q
            lo = int(k)
            hi = min(lo + 1, n - 1)
            return s[lo] + (s[hi] - s[lo]) * (k - lo)

        p90, p70 = pct(0.9), pct(0.7)
        tiers = cd.assign_tiers(posts, scores)
        for p, v in zip(posts, values, strict=True):
            want = 'hero' if v >= p90 else 'medium' if v >= p70 \
                else 'compact'
            self.assertEqual(tiers[p['id']], want, v)

    def test_equal_scores_do_not_make_everyone_a_hero(self):
        posts, _ = ranked_posts(30)
        tiers = cd.assign_tiers(posts, {})
        counts = list(tiers.values())
        self.assertEqual(counts.count('hero'), 3)
        # Ties are broken by position: the first ones win.
        self.assertEqual(tiers[posts[0]['id']], 'hero')
        self.assertEqual(tiers[posts[29]['id']], 'compact')


class SizeTest(unittest.TestCase):
    def photo(self, w, h, text='short', n=1, kind='image'):
        att = {'type': kind, 'meta': {'original': {'width': w, 'height': h}}}
        return status(1, content='<p>%s</p>' % text,
                      media_attachments=[att] * n)

    def test_hero(self):
        s = status(1)
        self.assertEqual(cd.post_span(s, 'hero', 4), (2, 2))
        self.assertEqual(cd.post_span(s, 'hero', 4, first_hero=True), (2, 2))
        self.assertEqual(cd.post_span(s, 'hero', 5, first_hero=True), (2, 2))
        self.assertEqual(cd.post_span(s, 'hero', 6, first_hero=True), (3, 2))
        self.assertEqual(cd.post_span(s, 'hero', 8, first_hero=True), (3, 2))
        self.assertEqual(cd.post_span(s, 'hero', 6), (2, 2))

    def test_spans_clamped_to_columns(self):
        s = status(1)
        self.assertEqual(cd.post_span(s, 'hero', 2), (2, 2))
        self.assertEqual(cd.post_span(s, 'hero', 1), (1, 2))
        self.assertEqual(cd.post_span(self.photo(1600, 900), 'medium', 1),
                         (1, 1))
        # A one-row page (12 columns) clamps the height too.
        self.assertEqual(cd.post_span(s, 'hero', 12), (2, 1))

    def test_medium_landscape_short_is_wide(self):
        self.assertEqual(
            cd.post_span(self.photo(1600, 900), 'medium', 4), (2, 1))
        # Exactly 1.3 counts.
        self.assertEqual(
            cd.post_span(self.photo(130, 100), 'medium', 4), (2, 1))

    def test_medium_otherwise_tall(self):
        self.assertEqual(
            cd.post_span(self.photo(100, 100), 'medium', 4), (1, 2))
        self.assertEqual(
            cd.post_span(self.photo(129, 100), 'medium', 4), (1, 2))
        # Long text.
        self.assertEqual(cd.post_span(
            self.photo(1600, 900, 'x' * 141), 'medium', 4), (1, 2))
        self.assertEqual(cd.post_span(
            self.photo(1600, 900, 'x' * 140), 'medium', 4), (2, 1))
        # Two images, or a video.
        self.assertEqual(
            cd.post_span(self.photo(1600, 900, n=2), 'medium', 4), (1, 2))
        self.assertEqual(cd.post_span(
            self.photo(1600, 900, kind='video'), 'medium', 4), (1, 2))
        # No media, a link card, no text.
        self.assertEqual(cd.post_span(status(1), 'medium', 4), (1, 2))
        self.assertEqual(cd.post_span(
            status(1, card=link_card('https://e.com')), 'medium', 4),
            (1, 2))

    def test_aspect_sources(self):
        att = {'type': 'image', 'meta': {'original': {'aspect': 1.78}}}
        s = status(1, media_attachments=[att])
        self.assertEqual(cd.post_span(s, 'medium', 4), (2, 1))
        att = {'type': 'image', 'meta': {}}
        s = status(1, media_attachments=[att])
        self.assertEqual(cd.post_span(s, 'medium', 4), (1, 2))
        att = {'type': 'image'}
        self.assertFalse(cd.is_landscape_led(
            status(1, media_attachments=[att])))

    def test_medium_reads_the_boosted_post(self):
        orig = self.photo(1600, 900)
        self.assertEqual(cd.post_span(boost(2, 3, orig), 'medium', 4),
                         (2, 1))

    def test_compact(self):
        short = status(1)
        longer = status(2, content='<p>%s</p>' % ('x' * 181))
        edge = status(3, content='<p>%s</p>' % ('x' * 180))
        self.assertEqual(cd.post_span(short, 'compact', 4, room=True),
                         (1, 1))
        self.assertEqual(cd.post_span(longer, 'compact', 4, room=True),
                         (1, 2))
        # Only when the page has room.
        self.assertEqual(cd.post_span(longer, 'compact', 4, room=False),
                         (1, 1))
        self.assertEqual(cd.post_span(edge, 'compact', 4, room=True),
                         (1, 1))
        # With media it stays 1 x 1 whatever the text.
        with_media = status(4, content='<p>%s</p>' % ('x' * 300),
                            media_attachments=[{}])
        self.assertEqual(cd.post_span(with_media, 'compact', 4, room=True),
                         (1, 1))

    def test_news_and_hashtag(self):
        news = {'card': {'image': 'x'}}
        self.assertEqual(cd.news_span(news, 4), (2, 2))
        self.assertEqual(cd.news_span({'card': {}}, 4), (2, 1))
        self.assertEqual(cd.news_span(news, 1), (1, 2))
        tag = make_tags(1)[0]
        self.assertEqual(cd.hashtag_span(tag, 4), (2, 1))
        self.assertEqual(cd.hashtag_span(tag, 1), (1, 1))
        quiet = make_tags(2)[1]
        self.assertEqual(cd.hashtag_span(quiet, 4), (1, 1))
        # Fallback tag: wide when 3 buckets have posts.
        fb = {'tag': None, 'buckets': [0, 1, 0, 2, 0, 0, 1]}
        self.assertEqual(cd.hashtag_span(fb, 4), (2, 1))
        fb = {'tag': None, 'buckets': [0, 1, 0, 2]}
        self.assertEqual(cd.hashtag_span(fb, 4), (1, 1))
        self.assertEqual(cd.hashtag_span({'tag': None}, 4), (1, 1))

    def test_page_geometry(self):
        rows = {1: 12, 2: 6, 3: 4, 4: 3, 5: 3, 6: 2, 7: 2, 8: 2, 11: 2,
                12: 1, 20: 1}
        for c, r in rows.items():
            self.assertEqual(cd.page_rows(c), r, c)
            self.assertGreaterEqual(cd.page_cells(c), 12)
        for c in (1, 2, 3, 4, 6, 12):
            self.assertEqual(cd.page_cells(c), 12)
        self.assertEqual(cd.page_cells(5), 15)
        self.assertEqual(cd.page_rows(0), 12)


class AllocateTest(unittest.TestCase):
    def build(self, columns, n=80, pages=3, news=8, tags=5):
        posts, scores = ranked_posts(n)
        return posts, scores, cd.allocate(
            posts, scores, make_news(news), make_tags(tags), columns, pages)

    def test_budgets_per_page(self):
        for c in (1, 2, 3, 4, 5, 6, 8):
            _, _, tiles = self.build(c)
            cells = cd.page_cells(c)
            for page in range(3):
                here = [t for t in tiles if t.page == page]
                self.assertTrue(here, (c, page))
                self.assertLessEqual(
                    sum(t.kind == 'news' for t in here), 2, c)
                self.assertLessEqual(
                    sum(t.kind == 'hashtag' for t in here), 1, c)
                units = sum(t.colspan * t.rowspan for t in here)
                self.assertLessEqual(units, cells, (c, page))
            self.assertEqual({t.page for t in tiles}, {0, 1, 2})

    def test_news_and_tags_taken_in_order(self):
        _, _, tiles = self.build(4)
        # The geometry decides how many fit a page (two 2 x 2 news tiles do
        # not fit beside a hero on a 4 x 3 page), never the order.
        keys = [t.ref['key'] for t in tiles if t.kind == 'news']
        self.assertGreaterEqual(len(keys), 2)
        self.assertEqual(keys, ['n%d' % i for i in range(len(keys))])
        keys = [t.ref['key'] for t in tiles if t.kind == 'hashtag']
        self.assertEqual(keys, ['t%d' % i for i in range(len(keys))])
        self.assertGreaterEqual(len(keys), 1)

    def test_posts_are_never_duplicated_and_follow_order_mostly(self):
        posts, _, tiles = self.build(4)
        ids = [t.ref['id'] for t in tiles if t.kind == 'post']
        self.assertEqual(len(ids), len(set(ids)))
        # The first posts are on the first page, hero first.
        first = [t for t in tiles if t.page == 0]
        self.assertEqual(first[0].tier, 'hero')
        self.assertEqual(first[0].kind, 'post')
        self.assertEqual(first[1].kind, 'news')

    def test_tiles_do_not_overflow_into_unseen_posts(self):
        posts, _, tiles = self.build(4, pages=3)
        shown = {t.ref['id'] for t in tiles if t.kind == 'post'}
        self.assertTrue(shown <= {p['id'] for p in posts[:36]})

    def test_accents_take_at_most_a_third(self):
        """News and hashtags are caps, not quotas: posts keep >= 2/3 of every
        full page, and both kinds show up within three pages."""
        for c in range(1, 8):
            posts, scores = ranked_posts(60)
            tiles = cd.allocate(posts, scores, make_news(10), make_tags(10),
                                c, 3)
            cells = cd.page_cells(c)
            for page in range(3):
                here = [t for t in tiles if t.page == page]
                area = sum(t.colspan * t.rowspan for t in here)
                accent = sum(t.colspan * t.rowspan for t in here
                             if t.kind != 'post')
                self.assertEqual(area, cells, (c, page))
                self.assertLessEqual(accent, cells // 3, (c, page))
                self.assertGreaterEqual(area - accent, cells * 2 / 3)
                self.assertLessEqual(
                    sum(t.kind == 'news' for t in here), 2)
                self.assertLessEqual(
                    sum(t.kind == 'hashtag' for t in here), 1)
                if c == 1:
                    self.assertLessEqual(
                        sum(t.kind != 'post' for t in here), 1)
            self.assertIn('news', {t.kind for t in tiles}, c)
            self.assertIn('hashtag', {t.kind for t in tiles}, c)

    def test_first_claim_alternates(self):
        posts, scores = ranked_posts(60)
        tiles = cd.allocate(posts, scores, make_news(10), make_tags(10),
                            4, 3)
        first = [next(t.kind for t in tiles
                      if t.page == p and t.kind != 'post') for p in range(3)]
        self.assertEqual(first, ['news', 'hashtag', 'news'])

    def test_news_over_allowance_drops_to_one_row(self):
        posts, scores = ranked_posts(60)
        tiles = cd.allocate(posts, scores, make_news(10), make_tags(10),
                            4, 3)
        page1 = [t for t in tiles if t.page == 1 and t.kind == 'news']
        self.assertTrue(page1)
        self.assertEqual((page1[0].colspan, page1[0].rowspan), (2, 1))

    def test_first_hero_of_page_is_wide_from_six_columns(self):
        _, _, tiles = self.build(6)
        for page in range(3):
            heroes = [t for t in tiles
                      if t.page == page and t.tier == 'hero']
            self.assertEqual((heroes[0].colspan, heroes[0].rowspan), (3, 2))
            for extra in heroes[1:]:
                self.assertEqual(extra.colspan, 2)
        _, _, tiles = self.build(4)
        self.assertTrue(all(t.colspan == 2 and t.rowspan == 2
                            for t in tiles if t.tier == 'hero'))

    def test_short_lists(self):
        posts, scores = ranked_posts(5)
        tiles = cd.allocate(posts, scores, [], [], 4, 3)
        self.assertEqual({t.page for t in tiles}, {0})
        self.assertEqual(len([t for t in tiles if t.kind == 'post']), 5)
        self.assertEqual(cd.allocate([], {}, [], [], 4, 3), [])
        # Only news: a first page is still made.
        tiles = cd.allocate([], {}, make_news(3), [], 4, 3)
        self.assertEqual([t.kind for t in tiles], ['news'])

    def test_show_more_adds_pages(self):
        posts, scores = ranked_posts(120)
        three = cd.allocate(posts, scores, make_news(20), make_tags(10), 4, 3)
        six = cd.allocate(posts, scores, make_news(20), make_tags(10), 4, 6)
        self.assertEqual({t.page for t in three}, {0, 1, 2})
        self.assertEqual({t.page for t in six}, set(range(6)))
        self.assertGreater(len(six), len(three))

    def test_percentiles_over_shown_posts_only(self):
        """800 posts must not make 80 heroes: 3 pages show 36 posts."""
        posts, scores = ranked_posts(800)
        tiles = cd.allocate(posts, scores, [], [], 4, 3)
        heroes = [t for t in tiles if t.tier == 'hero']
        self.assertLessEqual(len(heroes), 4)
        self.assertGreaterEqual(len(heroes), 3)

    def test_compact_long_text_promoted_only_with_room(self):
        posts = [status(i, content='<p>%s</p>' % ('x' * 300))
                 for i in range(12)]
        scores = {p['id']: 1.0 for p in posts}
        tiles = cd.allocate(posts[:10], scores, [], [], 2, 1)
        compact = [(t.colspan, t.rowspan) for t in tiles
                   if t.tier == 'compact']
        self.assertIn((1, 2), compact)
        # Never beyond the page; the promotion stops when the room does.
        self.assertLessEqual(sum(t.colspan * t.rowspan for t in tiles), 12)
        placed = cd.pack(tiles, 2)
        self.assertEqual(len(placed), len(tiles))
        # A short post is never promoted.
        short = [status(i) for i in range(10)]
        tiles = cd.allocate(short, {p['id']: 1.0 for p in short}, [], [], 2, 1)
        self.assertEqual({(t.colspan, t.rowspan) for t in tiles
                          if t.tier == 'compact'}, {(1, 1)})




class PackTest(unittest.TestCase):
    def layout(self, columns, **kw):
        posts, scores = ranked_posts(kw.pop('n', 120))
        return cd.layout(posts, scores, make_news(kw.pop('news', 10)),
                         make_tags(kw.pop('tags', 6)), columns,
                         kw.pop('pages', 3))

    def test_properties_for_one_to_six_columns(self):
        for c in range(1, 8):
            placed = self.layout(c)
            self.assertTrue(placed, c)
            cells = occupancy(placed, c)
            rows = cd.page_rows(c)
            for p in placed:
                # Inside the bounds and spans clamped.
                self.assertGreaterEqual(p.col, 0)
                self.assertLessEqual(p.col + p.colspan, c, (c, p))
                self.assertGreaterEqual(p.row, 0)
                self.assertLessEqual(p.colspan, c)
                self.assertLessEqual(p.rowspan, rows)
                # A tile stays inside its page band.
                self.assertEqual(p.row // rows, (p.row + p.rowspan - 1)
                                 // rows, (c, p))
                self.assertEqual(p.page, p.row // rows)
            # Budgets per page band.
            for band in {p.page for p in placed}:
                here = [p for p in placed if p.page == band]
                self.assertLessEqual(
                    sum(p.kind == 'news' for p in here), 2, (c, band))
                self.assertLessEqual(
                    sum(p.kind == 'hashtag' for p in here), 1, (c, band))
            # No holes before the last page band.
            last = max(p.page for p in placed)
            for band in range(last):
                for r in range(band * rows, (band + 1) * rows):
                    for col in range(c):
                        self.assertIn((col, r), cells,
                                      'hole at %r with %d columns'
                                      % ((col, r), c))
            self.assertEqual(cd.grid_rows(placed),
                             max(p.row + p.rowspan for p in placed))

    def test_every_tile_is_placed_once(self):
        for c in (1, 2, 3, 4, 6):
            posts, scores = ranked_posts(120)
            tiles = cd.allocate(posts, scores, make_news(10), make_tags(6),
                                c, 3)
            placed = cd.pack(tiles, c)
            self.assertEqual(len(placed), len(tiles))
            self.assertEqual({id(p.ref) for p in placed},
                             {id(t.ref) for t in tiles})

    def test_pages_are_about_three_pages_tall(self):
        for c in (1, 2, 3, 4, 6):
            placed = self.layout(c)
            rows = cd.page_rows(c)
            self.assertEqual(cd.grid_rows(placed), rows * 3, c)

    def test_deterministic(self):
        for c in (1, 3, 4, 6):
            a = self.layout(c)
            b = self.layout(c)
            self.assertEqual(a, b)
        posts, scores = ranked_posts(60)
        tiles = cd.allocate(posts, scores, make_news(4), make_tags(3), 4, 3)
        self.assertEqual(cd.pack(tiles, 4), cd.pack(list(tiles), 4))

    def test_dense_scan_order(self):
        """First free cell scanning rows then columns."""
        tiles = [cd.Tile('post', 'a', 2, 2, 'hero'),
                 cd.Tile('news', 'b', 2, 2),
                 cd.Tile('hashtag', 'c', 1, 1),
                 cd.Tile('post', 'd', 1, 1, 'compact'),
                 cd.Tile('post', 'e', 1, 1, 'compact')]
        placed = {p.ref: (p.col, p.row) for p in cd.pack(tiles, 4)}
        self.assertEqual(placed, {'a': (0, 0), 'b': (2, 0), 'c': (0, 2),
                                  'd': (1, 2), 'e': (2, 2)})

    def test_hero_first_then_news_then_posts(self):
        tiles = [cd.Tile('post', 'p1', 1, 1, 'compact'),
                 cd.Tile('news', 'n', 2, 1),
                 cd.Tile('post', 'h', 2, 2, 'hero')]
        placed = cd.pack(tiles, 4)
        self.assertEqual([p.ref for p in placed], ['h', 'n', 'p1'])
        self.assertEqual((placed[0].col, placed[0].row), (0, 0))
        self.assertEqual((placed[1].col, placed[1].row), (2, 0))
        # First free cell, scanning rows then columns: under the news.
        self.assertEqual((placed[2].col, placed[2].row), (2, 1))

    def test_misfit_rolls_over_and_compact_fills_the_hole(self):
        # 3 columns, 4 rows. Two 2x2 fill columns 0-1; the third 2x2 cannot
        # fit this page (only column 2 is left), so it rolls to the next
        # page; the 1x1s behind it fill the column that is left.
        tiles = [cd.Tile('post', 'h1', 2, 2, 'hero'),
                 cd.Tile('post', 'h2', 2, 2, 'hero'),
                 cd.Tile('post', 'h3', 2, 2, 'hero')] + [
            cd.Tile('post', 'c%d' % i, 1, 1, 'compact') for i in range(8)]
        placed = cd.pack(tiles, 3)
        by = {p.ref: p for p in placed}
        self.assertEqual((by['h1'].col, by['h1'].row), (0, 0))
        self.assertEqual((by['h2'].col, by['h2'].row), (0, 2))
        self.assertEqual(by['h3'].page, 1)
        # Column 2 of the first page is filled by compacts.
        for r in range(4):
            self.assertEqual(
                sum(1 for p in placed if p.col == 2 and p.row == r
                    and p.colspan == 1 and p.page == 0), 1, r)
        self.assertEqual(len(placed), 11)
        occupancy(placed, 3)

    def test_later_news_not_pulled_back_into_holes(self):
        tiles = [cd.Tile('post', 'h', 2, 2, 'hero', page=0),
                 cd.Tile('news', 'n1', 2, 1, page=1),
                 cd.Tile('post', 'c', 1, 1, 'compact', page=1)]
        placed = {p.ref: p for p in cd.pack(tiles, 4)}
        self.assertEqual(placed['c'].page, 0)
        self.assertEqual(placed['n1'].page, 1)

    def test_per_page_news_budget_enforced_by_pack(self):
        tiles = [cd.Tile('news', 'n%d' % i, 1, 1) for i in range(5)] + [
            cd.Tile('hashtag', 't%d' % i, 1, 1) for i in range(3)]
        placed = cd.pack(tiles, 4)
        for band in {p.page for p in placed}:
            here = [p for p in placed if p.page == band]
            self.assertLessEqual(sum(p.kind == 'news' for p in here), 2)
            self.assertLessEqual(sum(p.kind == 'hashtag' for p in here), 1)
        self.assertEqual(len(placed), 8)

    def test_spans_clamped(self):
        tiles = [cd.Tile('news', 'n', 2, 2), cd.Tile('post', 'h', 3, 2)]
        (n, h) = cd.pack(tiles, 1)
        self.assertEqual((n.colspan, h.colspan), (1, 1))
        # Rows are clamped to the page height too.
        placed = cd.pack([cd.Tile('post', 'x', 2, 2)], 12)
        self.assertEqual((placed[0].colspan, placed[0].rowspan), (2, 1))
        # Zero or negative spans become 1.
        placed = cd.pack([cd.Tile('post', 'x', 0, -3)], 4)
        self.assertEqual((placed[0].colspan, placed[0].rowspan), (1, 1))

    def test_empty_and_one_column(self):
        self.assertEqual(cd.pack([], 4), [])
        self.assertEqual(cd.grid_rows([]), 0)
        placed = cd.pack([cd.Tile('post', i, 1, 1, 'compact')
                          for i in range(5)], 1)
        self.assertEqual([(p.col, p.row) for p in placed],
                         [(0, i) for i in range(5)])
        # Columns below one are one.
        placed = cd.pack([cd.Tile('post', 0, 1, 1)], 0)
        self.assertEqual((placed[0].col, placed[0].row), (0, 0))

    def test_empty_allocation_pages_leave_no_gap(self):
        tiles = [cd.Tile('post', 'a', 1, 1, 'compact', page=0),
                 cd.Tile('post', 'b', 2, 1, 'medium', page=5)]
        placed = cd.pack(tiles, 4)
        self.assertEqual({p.page for p in placed}, {0, 1})

    def test_input_not_mutated(self):
        tiles = [cd.Tile('post', 'a', 9, 9, 'hero')]
        cd.pack(tiles, 3)
        self.assertEqual((tiles[0].colspan, tiles[0].rowspan), (9, 9))

    def test_allocation_fits_its_pages_exactly(self):
        """``allocate`` simulates the packer, so nothing rolls over: every
        tile lands in the band it was allocated to."""
        for c in range(1, 13):
            posts, scores = ranked_posts(150)
            tiles = cd.allocate(posts, scores, make_news(12), make_tags(8),
                                c, 3)
            placed = cd.pack(tiles, c)
            self.assertEqual(len(placed), len(tiles), c)
            by_ref = {id(p.ref): p for p in placed}
            for t in tiles:
                self.assertEqual(by_ref[id(t.ref)].page, t.page, c)
            occupancy(placed, c)

    def test_random_tiles_never_overlap(self):
        rng = random.Random(7)
        for _ in range(200):
            c = rng.randint(1, 9)
            tiles = []
            for i in range(rng.randint(0, 40)):
                kind = rng.choice(('post', 'post', 'post', 'news',
                                   'hashtag'))
                tiles.append(cd.Tile(
                    kind, i, rng.randint(0, 4), rng.randint(0, 4),
                    rng.choice(('hero', 'medium', 'compact', None)),
                    rng.randint(0, 4)))
            placed = cd.pack(tiles, c)
            self.assertEqual(len(placed), len(tiles))
            occupancy(placed, c)
            self.assertEqual(cd.pack(tiles, c), placed)
            for p in placed:
                self.assertLessEqual(p.col + p.colspan, c)
            for band in {p.page for p in placed}:
                here = [p for p in placed if p.page == band]
                self.assertLessEqual(sum(p.kind == 'news' for p in here), 2)
                self.assertLessEqual(
                    sum(p.kind == 'hashtag' for p in here), 1)

    def test_wide_and_high_pages(self):
        for c in (8, 12):
            placed = self.layout(c, pages=2)
            occupancy(placed, c)
            self.assertTrue(all(p.col + p.colspan <= c for p in placed))


if __name__ == '__main__':
    unittest.main(verbosity=2)
