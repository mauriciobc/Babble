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
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit.catchupdigest import (BASE, boost, cd,  # noqa: E402
                                   link_card, status)

class TrendsTest(unittest.TestCase):
    def setUp(self):
        shared = lambda sid, author, url, **kw: status(  # noqa: E731
            sid, author, card=link_card(url), **kw)
        self.posts = [shared(1, 1, 'https://e.com/b'),
                      shared(2, 2, 'https://e.com/b/'),
                      shared(3, 3, 'https://e.com/c'),
                      shared(4, 4, 'https://e.com/not-trending')]
        self.links = cd.timeline_links(self.posts, trim=False)
        self.trend_links = [link_card('https://e.com/a'),
                            link_card('https://e.com/b/'),
                            link_card('https://e.com/c'),
                            link_card('https://e.com/d')]

    def test_news_order_front_by_shared(self):
        news = cd.merge_news(self.trend_links, self.links)
        self.assertEqual([n['key'] for n in news],
                         ['https://e.com/b', 'https://e.com/c',
                          'https://e.com/a', 'https://e.com/d'])
        b = news[0]
        self.assertTrue(b['trending'])
        self.assertEqual(b['shared'], 2)
        self.assertEqual([a['id'] for a in b['sharers']], ['1', '2'])
        # The card is the server's, not the timeline's.
        self.assertIs(b['card'], self.trend_links[1])
        self.assertEqual(news[2]['sharers'], [])
        self.assertEqual(news[2]['shared'], 0)

    def test_news_ties_keep_trending_order(self):
        links = cd.timeline_links([
            status(1, 1, card=link_card('https://e.com/x')),
            status(2, 2, card=link_card('https://e.com/y'))], trim=False)
        trend = [link_card('https://e.com/y'), link_card('https://e.com/x')]
        news = cd.merge_news(trend, links)
        self.assertEqual([n['key'] for n in news],
                         ['https://e.com/y', 'https://e.com/x'])

    def test_news_duplicates_in_trends_dropped(self):
        trend = [link_card('https://e.com/a'), link_card('https://e.com/a/')]
        self.assertEqual(len(cd.merge_news(trend, [])), 1)

    def test_news_fallbacks(self):
        for trends, hide in ((None, False), ([], False),
                             (self.trend_links, True)):
            news = cd.merge_news(trends, self.links, hide_trending=hide)
            self.assertEqual(
                [n['key'] for n in news],
                [link['url'] for link in self.links], (trends, hide))
            self.assertFalse(any(n['trending'] for n in news))
            self.assertEqual(news[0]['shared'], 2)
        self.assertEqual(cd.merge_news(None, []), [])

    def tl_tags(self):
        posts = [status(1, 1, tags=[{'name': 'rust'}, {'name': 'solo'}]),
                 status(2, 2, tags=[{'name': 'Rust'}]),
                 status(3, 3, tags=[{'name': 'gnome'}]),
                 status(4, 4, tags=[{'name': 'gnome'}]),
                 status(5, 5, tags=[{'name': 'gnome'}])]
        return cd.timeline_hashtags(posts)

    def test_hashtag_order(self):
        trend = [{'name': 'cats'}, {'name': 'Rust'}, {'name': 'dogs'},
                 {'name': 'GNOME'}, {'name': 'birds'}]
        tags = cd.merge_hashtags(trend, ['dogs'], self.tl_tags())
        self.assertEqual([t['name'] for t in tags],
                         ['dogs',               # followed
                          'Rust', 'GNOME',      # used in the timeline
                          'cats', 'birds'])     # the rest, trending order
        by = {t['key']: t for t in tags}
        self.assertTrue(by['dogs']['following'])
        self.assertEqual(by['rust']['timeline_posts'], 2)
        self.assertEqual(by['gnome']['timeline_accounts'], 3)
        self.assertEqual(by['cats']['timeline_posts'], 0)
        self.assertTrue(all(t['trending'] for t in tags))
        self.assertIs(by['cats']['tag'], trend[0])

    def test_followed_flag_from_server_and_dicts(self):
        trend = [{'name': 'a', 'following': True}, {'name': 'b'}]
        tags = cd.merge_hashtags(trend, [{'name': 'B'}], [])
        self.assertEqual([t['following'] for t in tags], [True, True])

    def test_followed_beats_timeline_but_keeps_trending_order(self):
        trend = [{'name': 'x'}, {'name': 'rust'}, {'name': 'f1'},
                 {'name': 'f2'}]
        tags = cd.merge_hashtags(trend, ['f2', 'f1'], self.tl_tags())
        self.assertEqual([t['name'] for t in tags],
                         ['f1', 'f2', 'rust', 'x'])

    def test_hashtag_fallbacks(self):
        for trends, hide in ((None, False), ([], False),
                             ([{'name': 'rust'}], True)):
            tags = cd.merge_hashtags(trends, ['gnome'], self.tl_tags(),
                                     hide_trending=hide)
            # Only tags with >= 2 distinct accounts qualify: not "solo".
            self.assertEqual([t['key'] for t in tags], ['gnome', 'rust'])
            self.assertFalse(any(t['trending'] for t in tags))
            self.assertTrue(tags[0]['following'])
            self.assertIsNone(tags[0]['tag'])
        self.assertEqual(cd.merge_hashtags(None, [], []), [])

    def test_trending_status_keys(self):
        keys = cd.trending_status_keys([
            {'id': '1', 'uri': 'https://a/1'}, {'id': '2'}])
        self.assertEqual(keys, {'1', 'https://a/1', '2'})
        self.assertEqual(cd.trending_status_keys(None), frozenset())
        self.assertEqual(cd.trending_status_keys([{'id': '1'}], True),
                         frozenset())

    def test_is_trending_post(self):
        keys = cd.trending_status_keys([{'id': '1', 'uri': 'https://a/1'}])
        self.assertTrue(cd.is_trending_post(status(1), keys))
        self.assertTrue(cd.is_trending_post(
            status(7, uri='https://a/1'), keys))
        # A boost is trending when the boosted post is.
        self.assertTrue(cd.is_trending_post(boost(9, 2, status(1)), keys))
        self.assertFalse(cd.is_trending_post(status(2), keys))
        self.assertFalse(cd.is_trending_post(status(1), frozenset()))


class DensityTest(unittest.TestCase):
    def test_text_only(self):
        s = status(1, content='<p>%s</p>' % ('x' * 140))
        self.assertEqual(cd.post_density(s), 1.0)

    def test_spoiler_and_poll_count(self):
        s = status(1, content='<p>%s</p>' % ('x' * 70),
                   spoiler_text='y' * 35,
                   poll={'options': [{'title': 'a' * 20},
                                     {'title': 'b' * 15}]})
        self.assertEqual(cd.post_density(s), 140 / 140)

    def test_media_is_eight_each(self):
        s = status(1, content='', media_attachments=[{}, {}, {}])
        self.assertEqual(cd.post_density(s), 24)

    def test_card_image_without_media(self):
        s = status(1, content='', card={'image': 'x'})
        self.assertEqual(cd.post_density(s), 8)
        s = status(1, content='', card={'image': None})
        self.assertEqual(cd.post_density(s), 0)
        # Media wins over the card.
        s = status(1, content='', card={'image': 'x'},
                   media_attachments=[{}])
        self.assertEqual(cd.post_density(s), 8)

    def test_invisible_url_parts_do_not_count(self):
        s = status(1, content=(
            '<p><span class="invisible">https://</span>a.com</p>'))
        self.assertEqual(cd.post_density(s), 5 / 140)


class SortTest(unittest.TestCase):
    def setUp(self):
        self.a = status(1, 1, minutes_ago=50, favourites_count=5,
                        reblogs_count=1, replies_count=3)
        self.b = status(2, 2, minutes_ago=40, favourites_count=9,
                        reblogs_count=1, replies_count=0)
        self.c = status(3, 1, minutes_ago=30, favourites_count=9,
                        reblogs_count=7, replies_count=3,
                        media_attachments=[{}])
        self.posts = [self.c, self.a, self.b]

    def ids(self, posts):
        return [p['id'] for p in posts]

    def test_time(self):
        self.assertEqual(self.ids(cd.sort_posts(self.posts)),
                         ['1', '2', '3'])
        self.assertEqual(self.ids(cd.sort_posts(self.posts, order='desc')),
                         ['3', '2', '1'])

    def test_likes_ties_by_time_both_ways(self):
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'likes', 'desc')),
            ['2', '3', '1'])
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'likes', 'asc')),
            ['1', '2', '3'])

    def test_replies_and_boosts(self):
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'replies', 'desc')),
            ['1', '3', '2'])
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'boosts', 'desc')),
            ['3', '1', '2'])

    def test_boost_sorts_by_boosted_post(self):
        orig = status(50, 9, favourites_count=100)
        wrapped = boost(4, 1, orig, minutes_ago=5)
        got = cd.sort_posts(self.posts + [wrapped], 'likes', 'desc')
        self.assertEqual(got[0]['id'], '4')
        self.assertEqual(cd.sort_value(wrapped, 'likes'), 100)

    def test_density(self):
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'density', 'desc')),
            ['3', '1', '2'])

    def test_relevance(self):
        scores = {'1': 0.1, '2': 0.9, '3': 0.5}
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'relevance', 'desc',
                                   scores=scores)), ['2', '3', '1'])
        # Missing scores sort last when descending.
        self.assertEqual(
            self.ids(cd.sort_posts(self.posts, 'relevance', 'desc',
                                   scores={'2': 1})), ['2', '1', '3'])

    def test_group_by_author(self):
        extra = status(4, 2, minutes_ago=10)
        posts = [self.a, self.b, self.c, extra]
        # Authors 1 and 2 have two posts each; first seen goes first.
        self.assertEqual(cd.author_order(posts), ['1', '2'])
        got = cd.sort_posts(posts, 'time', 'asc', group_by_author=True)
        self.assertEqual(self.ids(got), ['1', '3', '2', '4'])
        busy = [self.a, self.b, extra, self.c, status(9, 2, minutes_ago=1)]
        self.assertEqual(cd.author_order(busy), ['2', '1'])
        got = cd.sort_posts(busy, 'likes', 'desc', group_by_author=True)
        self.assertEqual(self.ids(got), ['2', '4', '9', '3', '1'])

    def test_unknown_sort(self):
        with self.assertRaises(ValueError):
            cd.sort_value(self.a, 'nope')

    def test_does_not_mutate(self):
        before = list(self.posts)
        cd.sort_posts(self.posts, 'likes')
        self.assertEqual(self.posts, before)


class BinByTimeTest(unittest.TestCase):
    def test_spread(self):
        posts = [status(i, minutes_ago=m)
                 for i, m in enumerate((100, 75, 50, 25, 0))]
        bins = cd.bin_by_time(posts, 5, BASE)
        self.assertEqual([len(b) for b in bins], [1, 1, 1, 1, 1])
        self.assertEqual(bins[0][0]['id'], '0')
        self.assertEqual(bins[4][0]['id'], '4')

    def test_phanpy_formula(self):
        """floor(normalized * (bins - 1)): the newest post sits in the last
        bin only when it is exactly at the end."""
        posts = [status(0, minutes_ago=100), status(1, minutes_ago=50),
                 status(2, minutes_ago=0)]
        bins = cd.bin_by_time(posts, 11, BASE)
        self.assertEqual([i for i, b in enumerate(bins) if b], [0, 5, 10])

    def test_future_dates_go_to_last_bin(self):
        future = status(9, minutes_ago=-30)
        posts = [status(0, minutes_ago=100), status(1, minutes_ago=0),
                 future]
        bins = cd.bin_by_time(posts, 4, BASE)
        self.assertIn(future, bins[-1])
        # The future post does not stretch the range: the newest real post
        # is still the end of it.
        self.assertEqual(bins[3], [posts[1], future])
        self.assertEqual(bins[0], [posts[0]])

    def test_all_future(self):
        posts = [status(1, minutes_ago=-5), status(2, minutes_ago=-9)]
        bins = cd.bin_by_time(posts, 3, BASE)
        self.assertEqual(bins[2], posts)

    def test_one_instant_does_not_divide_by_zero(self):
        posts = [status(1, minutes_ago=5), status(2, minutes_ago=5)]
        bins = cd.bin_by_time(posts, 4, BASE)
        self.assertEqual(bins[0], posts)

    def test_empty_and_bad_input(self):
        self.assertEqual(cd.bin_by_time([], 3, BASE), [[], [], []])
        self.assertEqual(cd.bin_by_time([status(1)], 0, BASE), [])
        bad = status(1)
        bad['created_at'] = 'x'
        self.assertEqual(cd.bin_by_time([bad], 3, BASE)[0], [bad])

    def test_an_unparseable_now_caps_at_the_latest_post(self):
        posts = [status(1, minutes_ago=100), status(2, minutes_ago=10)]
        for now in (None, 'not a date'):
            bins = cd.bin_by_time(posts, 4, now)
            self.assertEqual(sum(len(b) for b in bins), len(posts))
            self.assertEqual(bins[0], [posts[0]])

    def test_every_item_lands_once(self):
        posts = [status(i, minutes_ago=(i * 37) % 500) for i in range(200)]
        bins = cd.bin_by_time(posts, 320, BASE)
        self.assertEqual(len(bins), 320)
        self.assertEqual(sum(len(b) for b in bins), 200)

    def test_posts_bar_threshold(self):
        posts = [status(i, minutes_ago=i) for i in range(160)]
        self.assertIsNone(cd.posts_bar_bins(posts, BASE))
        posts.append(status(999, minutes_ago=1))
        self.assertEqual(len(cd.posts_bar_bins(posts, BASE)), 320)

    def test_posts_bar_ids(self):
        posts = [status(i, minutes_ago=i) for i in range(3)]
        dots, bins = cd.posts_bar_ids(posts, BASE)
        self.assertEqual(dots, [p['id'] for p in posts])
        self.assertIsNone(bins)
        many = [status(i, minutes_ago=i) for i in range(161)]
        dots, bins = cd.posts_bar_ids(many, BASE)
        self.assertIsNone(dots)
        self.assertEqual(len(bins), 320)


if __name__ == '__main__':
    unittest.main(verbosity=2)
