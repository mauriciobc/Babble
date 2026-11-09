#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Bluesky data layer (no display, no network)."""

import os
os.environ['GSETTINGS_BACKEND'] = 'memory'
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    'bskyfixtures', os.path.join(srcdir, 'test-bskyfixtures.py'))
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

from src import bskytext  # noqa: E402
from src.statustext import parse_content  # noqa: E402

DID = fx.DID
RKEY = fx.RKEY
WEB = 'https://bsky.app/profile/%s/post/%s' % (DID, RKEY)
AT = 'at://%s/app.bsky.feed.post/%s' % (DID, RKEY)


class TestPostRef(unittest.TestCase):
    def test_forms(self):
        forms = [
            WEB,
            WEB + '/',
            'https://staging.bsky.app/profile/%s/post/%s' % (DID, RKEY),
            AT,
            'https://bsky.brid.gy/r/' + WEB,
            'https://fed.brid.gy/r/' + WEB,
            fx.STATUS['uri'],
            fx.STATUS['url'],
        ]
        for url in forms:
            self.assertEqual(bskytext.post_ref(url), (DID, RKEY), url)

    def test_handle_actor(self):
        self.assertEqual(
            bskytext.post_ref('https://bsky.app/profile/alice.bsky.social'
                              '/post/3abc'), ('alice.bsky.social', '3abc'))
        self.assertEqual(
            bskytext.post_ref('at://alice.bsky.social/app.bsky.feed.post/3x'),
            ('alice.bsky.social', '3x'))

    def test_user_example(self):
        self.assertEqual(bskytext.post_ref(
            'https://fed.brid.gy/r/https://bsky.app/profile/'
            'did:plc:ubji6txf3gdzwztlt2zegzdi/post/3mzv5qizm622q'),
            (DID, RKEY))

    def test_rejected(self):
        for url in [
                'at://%s/app.bsky.feed.repost/%s' % (DID, RKEY),
                'https://bsky.brid.gy/convert/ap/at://%s/'
                'app.bsky.feed.repost/%s' % (DID, RKEY),
                'https://bsky.app/profile/%s' % DID,
                'https://bsky.app/hashtag/foo',
                'https://example.com/profile/%s/post/%s' % (DID, RKEY),
                'https://bsky.brid.gy/r/https://example.com/x',
                'javascript:alert(1)', 'ftp://bsky.app/profile/a.b/post/c',
                '', None, 42, 'not a url', 'https://']:
            self.assertIsNone(bskytext.post_ref(url), url)

    def test_builders(self):
        self.assertEqual(bskytext.web_url(DID, RKEY), WEB)
        self.assertEqual(bskytext.at_uri(DID, RKEY), AT)
        self.assertTrue(bskytext.is_did(DID))
        self.assertFalse(bskytext.is_did('alice.bsky.social'))


class TestBridged(unittest.TestCase):
    def test_live_status(self):
        self.assertEqual(bskytext.bridged_ref(fx.STATUS), (DID, RKEY))

    def test_repost_and_native(self):
        repost = dict(fx.STATUS, url=None, uri=fx.STATUS['uri'].replace(
            'feed.post', 'feed.repost'))
        self.assertIsNone(bskytext.bridged_ref(repost))
        native = dict(fx.STATUS, account=dict(
            fx.STATUS['account'], acct='someone@mastodon.social'))
        self.assertIsNone(bskytext.bridged_ref(native))
        self.assertIsNone(bskytext.bridged_ref({}))
        self.assertIsNone(bskytext.bridged_ref(None))


class TestCardPost(unittest.TestCase):
    def test_live_card(self):
        data = bskytext.card_post(fx.CARD)
        self.assertEqual(data['name'], 'o que ta acontecendo aqui')
        self.assertEqual(data['handle'], 'acordeitarde.bsky.social')
        self.assertEqual(data['text'], fx.CARD['description'])
        self.assertEqual(data['avatar'], fx.CARD['image'])
        self.assertIsNone(data['picture'])
        self.assertEqual(data['actor'], 'did:plc:6zulsu3flxsklsaoxdsx52h4')
        self.assertEqual(data['rkey'], '3mzuxm3z6nk24')

    def test_wide_image_is_a_picture(self):
        card = dict(fx.CARD, width=1200, height=630)
        data = bskytext.card_post(card)
        self.assertEqual(data['picture'], fx.CARD['image'])
        self.assertIsNone(data['avatar'])

    def test_title_without_handle_uses_url_actor(self):
        url = 'https://bsky.app/profile/alice.bsky.social/post/3abc'
        data = bskytext.card_post(dict(fx.CARD, url=url, title='Just text'))
        self.assertEqual(data['name'], 'Just text')
        self.assertEqual(data['handle'], 'alice.bsky.social')

    def test_title_with_parentheses_in_name(self):
        data = bskytext.card_post(dict(
            fx.CARD, title='Ana (she/her) (@ana.bsky.social)'))
        self.assertEqual(data['name'], 'Ana (she/her)')
        self.assertEqual(data['handle'], 'ana.bsky.social')

    def test_not_bluesky(self):
        self.assertIsNone(bskytext.card_post(
            dict(fx.CARD, url='https://example.com/a')))
        self.assertIsNone(bskytext.card_post(None))
        self.assertIsNone(bskytext.card_post(
            dict(fx.CARD, url='https://bsky.app/profile/' + DID)))


class TestSegments(unittest.TestCase):
    def test_utf8_byte_offsets(self):
        text = 'não 😀 link here'
        start = len('não 😀 '.encode('utf-8'))
        end = start + len('link'.encode('utf-8'))
        out = bskytext.segments(text, [{
            'index': {'byteStart': start, 'byteEnd': end},
            'features': [{'$type': bskytext.FACET_LINK,
                          'uri': 'https://example.com/'}]}])
        self.assertEqual([s['text'] for s in out],
                         ['não 😀 ', 'link', ' here'])
        self.assertEqual([s['href'] for s in out],
                         [None, 'https://example.com/', None])

    def test_mention_tag_and_newline(self):
        text = '@a.b\n#tag'
        out = bskytext.segments(text, [
            {'index': {'byteStart': 0, 'byteEnd': 4},
             'features': [{'$type': bskytext.FACET_MENTION,
                           'did': 'did:plc:abc'}]},
            {'index': {'byteStart': 5, 'byteEnd': 9},
             'features': [{'$type': bskytext.FACET_TAG, 'tag': 'tag'}]}])
        self.assertEqual(out[0]['href'], 'https://bsky.app/profile/did:plc:abc')
        self.assertTrue(out[1].get('br'))
        self.assertEqual(out[2]['href'], 'https://bsky.app/hashtag/tag')

    def test_bad_facets_ignored(self):
        out = bskytext.segments('hello', [
            {'index': {'byteStart': 3, 'byteEnd': 99}, 'features': []},
            {'index': {'byteStart': 'x'}}, {}])
        self.assertEqual([s['text'] for s in out], ['hello'])


class TestLabelsAndEmbeds(unittest.TestCase):
    def test_moderation(self):
        self.assertEqual(bskytext.moderation(fx.post('x')),
                         {'sensitive': False, 'withheld': False})
        for val in ('porn', 'sexual', 'nudity', 'graphic-media'):
            post = fx.post('x', labels=[{'val': val}])
            self.assertTrue(bskytext.moderation(post)['sensitive'], val)
        for val in ('!hide', '!no-unauthenticated'):
            post = fx.post('x', labels=[{'val': val}])
            self.assertTrue(bskytext.moderation(post)['withheld'], val)

    def test_images_adapter(self):
        embed = {'$type': 'app.bsky.embed.images#view', 'images': [
            {'thumb': 'https://x/t1', 'fullsize': 'https://x/f1',
             'alt': 'one',
             'aspectRatio': {'width': 4, 'height': 3}},
            {'thumb': 'https://x/t2', 'fullsize': 'https://x/f2',
             'alt': ''},
            {}, 'junk', {'fullsize': 'javascript:x'}]}
        out = bskytext.attachments(embed)
        self.assertEqual([a['type'] for a in out], ['image', 'image'])
        self.assertEqual(out[0]['url'], 'https://x/f1')
        self.assertEqual(out[0]['preview_url'], 'https://x/t1')
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['description'], 'one')

    def test_video_is_a_poster_only(self):
        embed = {'$type': 'app.bsky.embed.video#view',
                 'playlist': 'https://x/p.m3u8', 'thumbnail': 'poster',
                 'alt': 'clip'}
        self.assertEqual(bskytext.attachments(embed), [])
        self.assertEqual(bskytext.video_poster(embed),
                         {'poster': 'poster', 'alt': 'clip'})

    def test_external_and_quote_with_media(self):
        ext = {'$type': 'app.bsky.embed.external#view', 'external': {
            'uri': 'https://e.com', 'title': 'T', 'description': 'D',
            'thumb': 'https://x/th'}}
        card = bskytext.external(ext)
        self.assertEqual((card['url'], card['title'], card['image']),
                         ('https://e.com', 'T', 'https://x/th'))
        record = {'$type': 'app.bsky.embed.record#viewRecord',
                  'uri': AT, 'author': fx.author(), 'value': {'text': 'q'},
                  'embeds': []}
        both = {'$type': 'app.bsky.embed.recordWithMedia#view',
                'record': {'record': record}, 'media': ext}
        self.assertIs(bskytext.quoted(both), record)
        self.assertEqual(bskytext.external(both)['url'], 'https://e.com')
        blocked = {'$type': 'app.bsky.embed.record#view', 'record': {
            '$type': 'app.bsky.embed.record#viewBlocked'}}
        self.assertIsNone(bskytext.quoted(blocked))
        data = bskytext.quote_card_data(record)
        self.assertEqual(data['text'], 'q')
        self.assertEqual(data['rkey'], RKEY)


class TestSafety(unittest.TestCase):
    def facet(self, **feature):
        return [{'index': {'byteStart': 0, 'byteEnd': 3},
                 'features': [feature]}]

    def test_unsafe_link_keeps_text_drops_href(self):
        for uri in ('javascript:alert(1)', 'file:///etc/passwd', 'x', None):
            out = bskytext.segments('abc def', self.facet(
                **{'$type': bskytext.FACET_LINK, 'uri': uri}))
            self.assertEqual(out[0]['text'], 'abc')
            self.assertIsNone(out[0]['href'], uri)
        out = bskytext.segments('abc', self.facet(
            **{'$type': bskytext.FACET_LINK, 'uri': 'HTTPS://x.com/'}))
        self.assertEqual(out[0]['href'], 'HTTPS://x.com/')

    def test_mention_needs_a_did(self):
        for did in ('x/../y?z', None, 5, 'did:plc:'):
            out = bskytext.segments('abc', self.facet(
                **{'$type': bskytext.FACET_MENTION, 'did': did}))
            self.assertIsNone(out[0]['href'], did)

    def test_external_unsafe_url(self):
        embed = {'$type': 'app.bsky.embed.external#view', 'external': {
            'uri': 'javascript:x', 'title': 'T', 'thumb': 'file:///x'}}
        card = bskytext.external(embed)
        self.assertEqual((card['url'], card['title'], card['image']),
                         ('', 'T', None))

    def test_dot_rkeys(self):
        for rkey in ('.', '..', '%2e', '%2e%2e', '.%2e'):
            self.assertIsNone(bskytext.post_ref(
                'https://bsky.app/profile/a.bsky.social/post/' + rkey), rkey)
            self.assertIsNone(bskytext.post_ref(
                'at://a.bsky.social/app.bsky.feed.post/' + rkey), rkey)

    def test_card_odd_values(self):
        url = 'https://bsky.app/profile/a.bsky.social/post/3k'
        for width, height in (('x', 'x'), (None, None), ('100', '100'),
                              (1.5, 1.5), (True, True), ([], {})):
            data = bskytext.card_post({'url': url, 'image': 'https://i/x',
                                       'width': width, 'height': height})
            self.assertEqual(data['picture'], 'https://i/x')
        data = bskytext.card_post({'url': url, 'title': 5,
                                   'description': ['x']})
        self.assertEqual((data['name'], data['text']),
                         ('a.bsky.social', ''))

    def test_junk_everywhere(self):
        junk = [None, 5, 'x', [], {}, [None, 'x', 5], {'labels': 'porn'},
                {'labels': [None, {'val': ['u']}, {'val': 'porn'}]},
                {'$type': 'app.bsky.embed.images', 'images': 'x'},
                {'$type': 'app.bsky.embed.recordWithMedia', 'media': None,
                 'record': 'x'},
                {'$type': 'app.bsky.embed.record', 'record': {
                    '$type': 'app.bsky.embed.record#viewRecord',
                    'author': None, 'value': 5, 'embeds': 5, 'uri': 3}}]
        for item in junk:
            bskytext.segments(item, item)
            bskytext.segments('héllo', [item, {'index': item}])
            bskytext.moderation(item)
            bskytext.attachments(item)
            bskytext.video_poster(item)
            bskytext.external(item)
            bskytext.quote_card_data(item)
            bskytext.card_post(item)
            quoted = bskytext.quoted(item)
            self.assertTrue(quoted is None or isinstance(quoted, dict))
        self.assertEqual(
            bskytext.moderation({'labels': [{'val': ['u']}]}),
            {'sensitive': False, 'withheld': False})

    def test_sensitive_quote_has_no_picture(self):
        image = {'$type': 'app.bsky.embed.images#view', 'images': [
            {'thumb': 'https://x/t', 'fullsize': 'https://x/f'}]}
        record = {'uri': AT, 'author': fx.author(), 'value': {'text': 'q'},
                  'embeds': [image], 'labels': [{'val': 'porn'}]}
        self.assertIsNone(bskytext.quote_card_data(record)['picture'])
        record['labels'] = []
        self.assertEqual(bskytext.quote_card_data(record)['picture'],
                         'https://x/t')


class TestQuoteInline(unittest.TestCase):
    def test_paragraph_form_and_self_closing(self):
        html = ('<p>Body</p><p class="quote-inline">RE: <a href="'
                'https://x/y">https://x/y</a></p>')
        text = ''.join(s['text'] for s in parse_content(html, True))
        self.assertEqual(text, 'Body')
        self.assertIn('RE:', ''.join(s['text'] for s in parse_content(html)))
        html = '<p>a<span class="quote-inline"/> b</p>'
        self.assertEqual(''.join(
            s['text'] for s in parse_content(html, True)).replace(' ', ''),
            'ab')

    def test_default_keeps_line(self):
        text = ''.join(s['text'] for s in parse_content(fx.STATUS['content']))
        self.assertIn('RE:', text)

    def test_drop(self):
        segs = parse_content(fx.STATUS['content'], True)
        text = ''.join(s['text'] for s in segs)
        self.assertNotIn('RE:', text)
        self.assertIn('Orkut', text)
        self.assertFalse(any(s['href'] for s in segs))

    def test_nested_spans_and_following_text(self):
        html = ('<p>a<span class="quote-inline"><span>x</span>y</span>'
                ' b</p>')
        text = ''.join(s['text'] for s in parse_content(html, True))
        self.assertEqual(text.replace(' ', ''), 'ab')


if __name__ == '__main__':
    unittest.main(verbosity=2)
