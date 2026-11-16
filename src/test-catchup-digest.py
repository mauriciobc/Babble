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
import subprocess
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit.catchupdigest import (BASE, boost, cd,  # noqa: E402
                                   filter_hit, iso, link_card, status)

class ImportTest(unittest.TestCase):
    def test_no_gi(self):
        source_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(cd.__file__))))
        code = ('import sys; sys.path.insert(0, %r); '
                'from src.catchup import digest, digest_layout; '
                'sys.exit(1 if "gi" in sys.modules else 0)' % source_root)
        self.assertEqual(subprocess.run([sys.executable, '-c', code]
                                        ).returncode, 0)


class TimeTest(unittest.TestCase):
    def test_parse(self):
        dt = cd.parse_time('2026-10-13T12:00:00.000Z')
        self.assertEqual(dt, BASE)
        self.assertEqual(cd.parse_time('2026-10-13T14:00:00+02:00'), BASE)
        # Naive is UTC.
        self.assertEqual(cd.parse_time('2026-10-13T12:00:00'), BASE)
        self.assertIsNone(cd.parse_time('nope'))
        self.assertIsNone(cd.parse_time(None))
        self.assertIs(cd.parse_time(BASE), BASE)


class TextTest(unittest.TestCase):
    def test_content_length_drops_invisible(self):
        html = ('<p>Hi <a href="https://e.com/x"><span class="invisible">'
                'https://</span><span class="ellipsis">e.com/x</span>'
                '<span class="invisible">yz</span></a></p>')
        self.assertEqual(cd.content_length(html), len('Hi e.com/x'))
        self.assertEqual(cd.content_length(''), 0)
        self.assertEqual(cd.content_length(None), 0)

    def test_nested_invisible(self):
        html = '<span class="invisible">a<span>b</span>c</span>keep'
        self.assertEqual(cd.content_length(html), 4)

    def test_plain_text_paragraphs(self):
        html = '<p>One</p><p>Two<br>three &amp; four</p>'
        self.assertEqual(cd.plain_text(html), 'One\n\nTwo\nthree & four')

    def test_extract_hashtags(self):
        html = ('<p><a href="https://m/tags/Foo" class="mention hashtag" '
                'rel="tag">#<span>Foo</span></a> <a class="u-url" '
                'href="x">#nope</a></p>')
        self.assertEqual(cd.extract_hashtags(html), ['Foo'])
        self.assertEqual(cd.extract_hashtags('no tags'), [])


class FetchTest(unittest.TestCase):
    start = iso(60)

    def test_in_range_is_inclusive(self):
        self.assertTrue(cd.in_range(status(1, minutes_ago=60), self.start))
        self.assertFalse(cd.in_range(status(1, minutes_ago=61), self.start))
        self.assertTrue(cd.in_range(status(1, minutes_ago=999), None))
        bad = status(1)
        bad['created_at'] = 'garbage'
        self.assertFalse(cd.in_range(bad, self.start))

    def test_all_in_range_goes_on(self):
        page = [status(i, minutes_ago=i) for i in range(5)]
        items, stop = cd.page_verdict(page, self.start)
        self.assertEqual(items, page)
        self.assertFalse(stop)

    def test_phanpy_bug_first_post_out_of_range(self):
        """Phanpy stops at a page whose first post is out of range even
        when later ones are in range. The fix keeps them."""
        page = [status(1, minutes_ago=120), status(2, minutes_ago=10),
                status(3, minutes_ago=90), status(4, minutes_ago=5)]
        items, stop = cd.page_verdict(page, self.start)
        self.assertEqual([s['id'] for s in items], ['2', '4'])
        self.assertFalse(stop)

    def test_nothing_in_range_stops(self):
        page = [status(i, minutes_ago=100 + i) for i in range(3)]
        items, stop = cd.page_verdict(page, self.start)
        self.assertEqual(items, [])
        self.assertTrue(stop)

    def test_empty_page_stops_even_unbounded(self):
        self.assertEqual(cd.page_verdict([], None), ([], True))
        self.assertEqual(cd.page_verdict(None, None), ([], True))

    def test_unbounded_keeps_everything(self):
        page = [status(1, minutes_ago=99999)]
        self.assertEqual(cd.page_verdict(page, None), (page, False))

    def test_fetch_progress_reasons(self):
        page = [status(i, minutes_ago=i) for i in range(40)]
        items, why = cd.fetch_progress(page, self.start, 0, 1)
        self.assertEqual((len(items), why), (40, None))
        # Page cap.
        self.assertEqual(
            cd.fetch_progress(page, None, 0, cd.MAX_PAGES)[1], 'cap')
        self.assertIsNone(
            cd.fetch_progress(page, None, 0, cd.MAX_PAGES - 1)[1])
        # Post cap, counted with this page.
        self.assertEqual(
            cd.fetch_progress(page, None, cd.MAX_POSTS - 40, 3)[1], 'cap')
        self.assertIsNone(
            cd.fetch_progress(page, None, cd.MAX_POSTS - 41, 3)[1])
        # Out of range wins over the cap.
        old = [status(1, minutes_ago=500)]
        self.assertEqual(
            cd.fetch_progress(old, self.start, 2000, 99)[1], 'complete')
        self.assertEqual(cd.fetch_progress([], None, 0, 1)[1], 'complete')

    def test_constants(self):
        self.assertEqual((cd.MAX_POSTS, cd.MAX_PAGES), (1000, 25))


class FilterTest(unittest.TestCase):
    def test_filter_info(self):
        self.assertIsNone(cd.filter_info(status(1)))
        warn = cd.filter_info(status(1, filtered=filter_hit('warn', 'W')))
        self.assertEqual(warn, {'action': 'warn', 'titles': ['W']})
        blur = cd.filter_info(status(1, filtered=filter_hit('blur')))
        self.assertEqual(blur['action'], 'blur')
        # hide wins over warn, on the boosted post too.
        both = status(1, filtered=filter_hit('warn'),
                      reblog=status(2, filtered=filter_hit('hide')))
        self.assertEqual(cd.filter_info(both)['action'], 'hide')
        # Mixed blur + warn is warn; unknown actions are warn.
        mixed = status(1, filtered=filter_hit('blur') + filter_hit('warn'))
        self.assertEqual(cd.filter_info(mixed)['action'], 'warn')
        odd = status(1, filtered=filter_hit('mystery'))
        self.assertEqual(cd.filter_info(odd)['action'], 'warn')

    def test_context_and_expiry(self):
        hit = filter_hit('warn')
        hit[0]['filter']['context'] = ['public']
        self.assertIsNone(cd.filter_info(status(1, filtered=hit)))
        hit = filter_hit('warn')
        hit[0]['filter']['expires_at'] = iso(10)
        s = status(1, filtered=hit)
        self.assertIsNotNone(cd.filter_info(s))
        self.assertIsNone(cd.filter_info(s, now=BASE))

    def test_apply_filters(self):
        mine = status(1, author=7, filtered=filter_hit('hide'))
        hidden = status(2, filtered=filter_hit('hide'))
        warned = status(3, filtered=filter_hit('warn'))
        plain = status(4)
        kept = cd.apply_filters([mine, hidden, warned, plain], ['7'])
        self.assertEqual([s['id'] for s in kept], ['1', '3', '4'])
        # Own posts are never filtered at all.
        self.assertIsNone(mine['_filtered'])
        self.assertEqual(warned['_filtered']['action'], 'warn')
        self.assertIsNone(plain['_filtered'])
        # Own boosted post counts as own too.
        own_boost = boost(5, 3, status(6, author=7,
                                       filtered=filter_hit('hide')))
        self.assertEqual(cd.apply_filters([own_boost], ['7']), [own_boost])


class FollowedTagsTest(unittest.TestCase):
    def test_hits_from_tags_and_content(self):
        by_tag = status(1, tags=[{'name': 'Linux'}])
        by_html = status(
            2, content='<p><a class="mention hashtag" href="x">#'
            '<span>GNOME</span></a></p>')
        miss = status(3, tags=[{'name': 'other'}])
        boosted = boost(4, 2, status(5, tags=[{'name': 'linux'}]))
        hits = cd.followed_tag_hits(
            [by_tag, by_html, miss, boosted],
            [{'name': 'linux'}, 'gnome', {'name': 'unused'}])
        self.assertEqual(hits, {'1': ['linux'], '2': ['gnome']})
        self.assertEqual(cd.followed_tag_hits([by_tag], []), {})

    def test_relationship_ids_include_boosted_authors(self):
        tagged = status(1, author=10)
        other = status(2, author=11)
        b = boost(3, 12, status(4, author=13))
        hits = {'1': ['t']}
        self.assertEqual(cd.relationship_ids([tagged, other, b], hits),
                         ['10', '13'])
        again = boost(5, 12, status(6, author=13))
        self.assertEqual(cd.relationship_ids([tagged, b, again], hits),
                         ['10', '13'])

    def test_mark(self):
        followed = status(1, author=10)
        stranger = status(2, author=11)
        unknown = status(3, author=12)
        none = status(4, author=11)
        hits = {'1': ['a'], '2': ['b'], '3': ['c']}
        rels = {'10': {'following': True}, '11': {'following': False}}
        cd.mark_followed_tags([followed, stranger, unknown, none], hits, rels)
        self.assertEqual(followed['_followed_tags'], [])
        self.assertEqual(stranger['_followed_tags'], ['b'])
        # No relationship known: not marked.
        self.assertEqual(unknown['_followed_tags'], [])
        self.assertEqual(none['_followed_tags'], [])
        self.assertTrue(cd.in_network(followed))
        self.assertFalse(cd.in_network(stranger))

    def test_threads(self):
        root = status(1)
        reply = status(2, in_reply_to_id='1')
        deep = status(3, in_reply_to_id='2')
        elsewhere = status(4, in_reply_to_id='99')
        cd.mark_threads([root, reply, deep, elsewhere])
        self.assertTrue(root['_thread'])
        # A reply is not a starter, even with a reply below it.
        self.assertNotIn('_thread', reply)
        self.assertNotIn('_thread', deep)
        self.assertNotIn('_thread', elsewhere)


class CategoryTest(unittest.TestCase):
    def cat(self, **kw):
        return cd.categorize(status(1, **kw))

    def test_each(self):
        self.assertEqual(self.cat(), 'original')
        self.assertEqual(self.cat(_filtered={'action': 'warn'}), 'filtered')
        self.assertEqual(self.cat(group={'id': 'g'}), 'groups')
        self.assertEqual(self.cat(reblog=status(2)), 'boosts')
        self.assertEqual(self.cat(quote={'quoted_status': {'id': '5'}}),
                         'quotes')
        self.assertEqual(self.cat(quote={'quoted_status_id': '5'}), 'quotes')
        self.assertEqual(self.cat(_followed_tags=['x']), 'followed_tags')
        self.assertEqual(
            self.cat(in_reply_to_id='9', in_reply_to_account_id='2'),
            'replies')

    def test_self_reply_is_original(self):
        self.assertEqual(
            self.cat(in_reply_to_id='9', in_reply_to_account_id='1'),
            'original')

    def test_reply_to_deleted_parent_is_a_reply(self):
        # Phanpy: null !== account id.
        self.assertEqual(self.cat(in_reply_to_id='9'), 'replies')

    def test_blur_is_not_filtered(self):
        self.assertEqual(self.cat(_filtered={'action': 'blur'}), 'original')

    def test_empty_quote_is_not_a_quote(self):
        self.assertEqual(self.cat(quote={'state': 'pending'}), 'original')

    def test_native_quotes_switch(self):
        s = status(1, quote={'quoted_status_id': '5'})
        self.assertEqual(cd.categorize(s, native_quotes=False), 'original')

    def test_precedence_order(self):
        """Every row beats all the rows below it."""
        everything = dict(
            _filtered={'action': 'warn'}, group={'id': 'g'},
            reblog=status(2), quote={'quoted_status_id': '5'},
            _followed_tags=['x'], in_reply_to_id='9',
            in_reply_to_account_id='2')
        order = ['filtered', 'groups', 'boosts', 'quotes', 'followed_tags',
                 'replies', 'original']
        self.assertEqual(list(cd.CATEGORIES), order)
        drop = ['_filtered', 'group', 'reblog', 'quote', '_followed_tags',
                'in_reply_to_id']
        current = dict(everything)
        for expected, key in zip(order, drop + [None], strict=True):
            self.assertEqual(self.cat(**current), expected)
            if key:
                del current[key]
                if key == 'in_reply_to_id':
                    current.pop('in_reply_to_account_id', None)

    def test_counts(self):
        posts = [status(1), status(2, reblog=status(9)),
                 status(3, _filtered={'action': 'warn'}),
                 status(4, _filtered={'action': 'blur'})]
        counts = cd.category_counts(posts)
        self.assertEqual(counts['original'], 2)
        self.assertEqual(counts['boosts'], 1)
        self.assertEqual(counts['filtered'], 1)
        self.assertEqual(sum(counts.values()), 4)
        self.assertEqual(set(counts), set(cd.CATEGORIES))


class BoostDedupeTest(unittest.TestCase):
    def test_collapse_into_first(self):
        orig = status(100, author=1)
        b1 = boost(1, 2, orig, minutes_ago=30)
        b2 = boost(2, 3, orig, minutes_ago=20)
        b3 = boost(3, 4, orig, minutes_ago=10)
        other = boost(4, 2, status(101))
        plain = status(5)
        visible, boosters, hidden = cd.dedupe_boosts(
            [b1, plain, b2, other, b3])
        self.assertEqual([p['id'] for p in visible], ['1', '5', '4'])
        self.assertEqual(hidden, {'2', '3'})
        self.assertEqual([a['id'] for a in boosters['1']], ['3', '4'])
        self.assertNotIn('4', boosters)

    def test_author_filter_matches_boosters(self):
        orig = status(100, author=1)
        b1 = boost(1, 2, orig)
        b2 = boost(2, 3, orig)
        visible, boosters, _ = cd.dedupe_boosts([b1, b2, status(5, author=9)])
        for who, expected in (('2', ['1']), ('3', ['1']), ('9', ['5'])):
            got = cd.filter_posts(visible, author=who, boosters=boosters)
            self.assertEqual([p['id'] for p in got], expected)
        # An author with no posts is ignored (Phanpy).
        self.assertEqual(len(cd.filter_posts(visible, author='77')), 2)

    def test_category_then_author(self):
        posts = [status(1, author=1), status(2, author=2,
                                             reblog=status(9)),
                 status(3, author=1, reblog=status(8))]
        got = cd.filter_posts(posts, category='boosts')
        self.assertEqual([p['id'] for p in got], ['2', '3'])
        got = cd.filter_posts(posts, category='boosts', author='1')
        self.assertEqual([p['id'] for p in got], ['3'])


class LinksTest(unittest.TestCase):
    def shared(self, sid, author, url, likes=0, boosts=0, **kw):
        return status(sid, author, card=link_card(url),
                      favourites_count=likes, reblogs_count=boosts, **kw)

    def test_grouping_and_trailing_slash(self):
        posts = [self.shared(1, 1, 'https://e.com/a/'),
                 self.shared(2, 2, 'https://e.com/a'),
                 self.shared(3, 3, 'https://e.com/a//')]
        links = cd.timeline_links(posts)
        keys = sorted(link['url'] for link in links)
        # Only one slash is stripped, as in Phanpy.
        self.assertEqual(keys, ['https://e.com/a', 'https://e.com/a/'])
        by = {link['url']: link for link in links}
        self.assertEqual(by['https://e.com/a']['shared'], 2)

    def test_sharer_dedupe_and_counts(self):
        a = self.shared(1, 1, 'https://e.com/a', likes=5, boosts=2)
        again = self.shared(2, 1, 'https://e.com/a', likes=7, boosts=3)
        second = self.shared(3, 2, 'https://e.com/a', likes=10, boosts=4)
        links = cd.timeline_links([a, again, second])
        self.assertEqual(len(links), 1)
        link = links[0]
        # The same account twice counts once and adds nothing.
        self.assertEqual(link['shared'], 2)
        self.assertEqual([x['id'] for x in link['sharers']], ['1', '2'])
        self.assertEqual((link['likes'], link['boosts']), (15, 6))

    def test_same_post_boosted_is_not_summed_twice(self):
        orig = self.shared(100, 9, 'https://e.com/a', likes=5, boosts=2)
        b1 = boost(1, 1, orig)
        b2 = boost(2, 2, orig)
        links = cd.timeline_links([b1, b2])
        self.assertEqual(links[0]['shared'], 2)
        self.assertEqual((links[0]['likes'], links[0]['boosts']), (5, 2))
        self.assertEqual(links[0]['post_id'], '100')
        # The sharers are the boosters, not the author.
        self.assertEqual([x['id'] for x in links[0]['sharers']], ['1', '2'])

    def test_second_post_boosted_is_summed_once_and_order_free(self):
        # The guard is per post, not per boost wrapper: a second post on
        # the same URL adds its counts once however many times it was
        # boosted, and the result must not depend on fetch order.
        first = self.shared(1, 9, 'https://e.com/a', likes=10, boosts=5)
        second = self.shared(2, 8, 'https://e.com/a', likes=100, boosts=50)
        b1 = boost(1, 2, second)
        b2 = boost(2, 3, second)
        expected = (110, 55)
        for order in ([first, b1, b2, second], [b1, second, b2, first]):
            link = cd.timeline_links(order)[0]
            # Four accounts share it: the two posts' authors and the two.
            self.assertEqual(link['shared'], 4)
            self.assertEqual((link['likes'], link['boosts']), expected)
        # post_id still names the first post seen on the URL.
        self.assertEqual(cd.timeline_links([first, b1, b2, second])[0]
                         ['post_id'], '1')

    def test_filters(self):
        no_image = status(1, card=link_card('https://e.com/1', image=None))
        video = status(2, card={'type': 'video', 'url': 'https://e.com/2',
                                'image': 'x'})
        no_url = status(3, card={'type': 'link', 'url': '', 'image': 'x'})
        filtered = self.shared(4, 4, 'https://e.com/4',
                               _filtered={'action': 'warn'})
        blurred = self.shared(5, 5, 'https://e.com/5',
                              _filtered={'action': 'blur'})
        links = cd.timeline_links([no_image, video, no_url, filtered,
                                   blurred])
        self.assertEqual([x['url'] for x in links], ['https://e.com/5'])

    def test_sorting(self):
        posts = [
            self.shared(1, 1, 'https://e.com/low', likes=1, boosts=1),
            self.shared(2, 2, 'https://e.com/boosty', likes=1, boosts=9),
            self.shared(3, 3, 'https://e.com/likey', likes=9, boosts=1),
            self.shared(4, 4, 'https://e.com/two', likes=0, boosts=0),
            self.shared(5, 5, 'https://e.com/two', likes=0, boosts=0),
        ]
        order = [x['url'] for x in cd.timeline_links(posts)]
        self.assertEqual(order, [
            'https://e.com/two',        # shared twice
            'https://e.com/boosty',     # then boosts
            'https://e.com/likey',      # then likes
            'https://e.com/low'])

    def many(self, shared_counts):
        posts = []
        n = 0
        for i, shared in enumerate(shared_counts):
            for s in range(shared):
                n += 1
                posts.append(self.shared(n, 1000 + s,
                                         'https://e.com/%02d' % i))
        return posts

    def test_trim_cuts_at_first_single_after_ten(self):
        # 12 links: ten shared twice, one twice, then two singles.
        links = cd.timeline_links(self.many([3] * 4 + [2] * 7 + [1, 1]))
        self.assertEqual(len(links), 11)
        # Keeps at least ten even when they are singles.
        links = cd.timeline_links(self.many([1] * 15))
        self.assertEqual(len(links), 10)
        # Everything shared more than once is kept.
        links = cd.timeline_links(self.many([2] * 15))
        self.assertEqual(len(links), 15)
        # Not trimmed on request.
        self.assertEqual(
            len(cd.timeline_links(self.many([1] * 15), trim=False)), 15)
        # Nine links: nothing to trim.
        self.assertEqual(len(cd.timeline_links(self.many([1] * 9))), 9)


class HashtagsTest(unittest.TestCase):
    def tagged(self, sid, author, *names, **kw):
        return status(sid, author, tags=[{'name': n} for n in names], **kw)

    def test_counts_once_per_post_and_distinct_accounts(self):
        posts = [self.tagged(1, 1, 'Linux', 'linux'),     # one post, twice
                 self.tagged(2, 1, 'linux'),              # same account
                 self.tagged(3, 2, 'LINUX', 'gnome'),
                 self.tagged(4, 3, 'gnome')]
        tags = {t['key']: t for t in cd.timeline_hashtags(posts)}
        self.assertEqual(tags['linux']['timeline_posts'], 3)
        self.assertEqual(tags['linux']['timeline_accounts'], 2)
        self.assertEqual(tags['linux']['name'], 'Linux')
        self.assertEqual(tags['gnome']['timeline_posts'], 2)
        self.assertEqual(tags['gnome']['timeline_accounts'], 2)

    def test_sorted_by_accounts_then_posts(self):
        posts = [self.tagged(1, 1, 'a'), self.tagged(2, 1, 'a'),
                 self.tagged(3, 1, 'a'),
                 self.tagged(4, 1, 'b'), self.tagged(5, 2, 'b'),
                 self.tagged(6, 3, 'c'), self.tagged(7, 4, 'c'),
                 self.tagged(8, 5, 'c')]
        keys = [t['key'] for t in cd.timeline_hashtags(posts)]
        self.assertEqual(keys, ['c', 'b', 'a'])

    def test_filtered_posts_are_ignored_and_boosts_count_once(self):
        orig = self.tagged(100, 9, 'x')
        posts = [boost(1, 1, orig), boost(2, 2, orig),
                 self.tagged(3, 3, 'x', _filtered={'action': 'warn'})]
        (tag,) = cd.timeline_hashtags(posts)
        self.assertEqual((tag['timeline_posts'], tag['timeline_accounts']),
                         (1, 1))

    def test_buckets_only_with_now(self):
        posts = [self.tagged(i, i, 'x', minutes_ago=i * 10)
                 for i in range(1, 6)]
        (tag,) = cd.timeline_hashtags(posts)
        self.assertIsNone(tag['buckets'])
        (tag,) = cd.timeline_hashtags(posts, now=iso())
        self.assertEqual(len(tag['buckets']), 12)
        self.assertEqual(sum(tag['buckets']), 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
