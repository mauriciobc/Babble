# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Catch Up data layer (no display, no network).

``catchupdigest`` imports nothing from GTK or ``gi``, so this runs anywhere
Python does. It pins the Phanpy behaviour that was ported (categories, link
grouping and trimming, boost deduplication, density, ``binByTime``), the
fixed fetch stop rule, the trends merge and its fallbacks, and the tile
allocation and dense packing of CATCH-UP.md section 6.
"""

import datetime
import os
import random
import subprocess
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import catchupdigest as cd  # noqa: E402

BASE = datetime.datetime(2026, 10, 13, 12, 0, tzinfo=datetime.timezone.utc)


def iso(minutes_ago=0):
    t = BASE - datetime.timedelta(minutes=minutes_ago)
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def account(n, acct=None):
    return {'id': str(n), 'acct': acct or 'user%d' % n,
            'display_name': 'User %d' % n}


def status(sid, author=1, minutes_ago=0, **kw):
    s = {'id': str(sid), 'uri': 'https://x.example/%s' % sid,
         'created_at': iso(minutes_ago), 'account': account(author),
         'content': '<p>post %s</p>' % sid, 'spoiler_text': '',
         'media_attachments': [], 'tags': [], 'reblog': None,
         'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0,
         'in_reply_to_id': None, 'in_reply_to_account_id': None,
         'card': None, 'poll': None, 'filtered': []}
    s.update(kw)
    return s


def boost(sid, booster, original, minutes_ago=0):
    return status(sid, booster, minutes_ago, reblog=original, content='')


def link_card(url, image='https://img.example/a.png'):
    return {'type': 'link', 'url': url, 'image': image, 'title': 't'}


def filter_hit(action, title='f'):
    return [{'filter': {'filter_action': action, 'title': title,
                        'context': ['home']}}]


class ImportTest(unittest.TestCase):
    def test_no_gi(self):
        code = ('import sys; sys.path.insert(0, %r); '
                'import catchupdigest; '
                'sys.exit(1 if "gi" in sys.modules else 0)'
                % os.path.dirname(os.path.abspath(cd.__file__)))
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


def ranked_posts(n, with_media_every=4, long_every=5):
    """``n`` posts in display order with descending scores."""
    posts, scores = [], {}
    for i in range(n):
        media = []
        if with_media_every and i % with_media_every == 1:
            media = [{'type': 'image',
                      'meta': {'original': {'width': 1600, 'height': 900}}}]
        text = 'x' * (200 if long_every and i % long_every == 2 else 30)
        posts.append(status(1000 + i, author=i % 17,
                            content='<p>%s</p>' % text,
                            media_attachments=media))
        scores[posts[-1]['id']] = 1.0 - i / (n + 1)
    return posts, scores


def make_news(n, with_image=True):
    return [{'key': 'n%d' % i, 'url': 'https://e.com/%d' % i,
             'card': link_card('https://e.com/%d' % i,
                               image='x' if with_image and i % 3 != 2
                               else None),
             'trending': True, 'sharers': [], 'shared': 0}
            for i in range(n)]


def make_tags(n):
    out = []
    for i in range(n):
        history = [{'day': str(d), 'uses': str(5 if i % 2 == 0 else 0)}
                   for d in range(3)]
        out.append({'key': 't%d' % i, 'name': 't%d' % i,
                    'tag': {'name': 't%d' % i, 'history': history},
                    'trending': True, 'following': False,
                    'timeline_posts': 0, 'timeline_accounts': 0,
                    'accounts': [], 'buckets': None})
    return out


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


def occupancy(placed, columns):
    """cell -> index of the tile covering it; raises on overlap."""
    cells = {}
    for i, p in enumerate(placed):
        for r in range(p.row, p.row + p.rowspan):
            for c in range(p.col, p.col + p.colspan):
                if (c, r) in cells:
                    raise AssertionError('overlap at %r' % ((c, r),))
                cells[(c, r)] = i
    return cells


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
