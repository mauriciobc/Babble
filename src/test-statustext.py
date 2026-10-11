# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the status text layer (no display, no network).

Split out of ``timeline.py`` precisely so this can run headless: the
parsing and the timestamps are worth pinning on their own, without
standing up a window to reach them.
"""

import datetime
import os
import re
import sys
import unittest

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.statustext import (is_hashtag, parse_content,  # noqa: E402
                            relative_time, shorten_url_text)


def text_of(segments):
    return ''.join(segment['text'] for segment in segments)


class TestParseContent(unittest.TestCase):
    def test_a_paragraph_becomes_a_line_break(self):
        segments = parse_content('<p>one</p><p>two</p>')
        self.assertIn('\n', text_of(segments))
        self.assertEqual(text_of(segments).replace('\n', ''), 'onetwo')

    def test_a_br_is_marked_so_renderers_keep_the_break(self):
        segments = parse_content('a<br>b')
        self.assertTrue(any(s.get('br') for s in segments))

    def test_links_keep_their_href_and_escapes_are_resolved(self):
        segments = parse_content('<a href="https://x.test/a">caf&eacute;</a>')
        link = next(s for s in segments if s['href'])
        self.assertEqual(link['href'], 'https://x.test/a')
        self.assertEqual(link['text'], 'café')

    def test_a_script_body_never_reaches_the_text(self):
        # The status content is untrusted: a <script> whose text were
        # kept would be rendered as the post's own words.
        self.assertNotIn('alert', text_of(parse_content(
            '<p>a</p><script>alert(1)</script>')))

    def test_tagless_text_is_not_dropped(self):
        # HTMLParser does not flush a trailing run of plain text that no
        # tag follows; that silently emptied field values.
        self.assertEqual(text_of(parse_content('just words')), 'just words')

    def test_an_empty_body_yields_nothing(self):
        self.assertEqual(parse_content(''), [])
        self.assertEqual(parse_content(None), [])


class TestHashtag(unittest.TestCase):
    def test_a_tag_link_is_a_hashtag(self):
        self.assertTrue(is_hashtag(
            {'href': 'https://x.test/tags/rust', 'text': '#rust'}))

    def test_an_ordinary_link_is_not(self):
        self.assertFalse(is_hashtag(
            {'href': 'https://x.test/@me', 'text': 'me'}))


class TestShortenUrl(unittest.TestCase):
    def test_the_host_and_path_are_kept(self):
        self.assertEqual(
            shorten_url_text('https://example.test/a/b'),
            'example.test/a/b')

    def test_a_long_url_is_truncated_to_one_line(self):
        out = shorten_url_text('https://example.test/' + 'x' * 200)
        self.assertLessEqual(len(out), 43)
        self.assertTrue(out.endswith('…'))

    def test_something_that_is_not_a_url_is_left_alone(self):
        self.assertEqual(shorten_url_text('not a url'), 'not a url')


class TestRelativeTime(unittest.TestCase):
    """The unit suffixes are part of the string, but the digits are not:
    the result is translated, so assert the shape, never an exact text."""

    @staticmethod
    def _ago(**delta):
        moment = datetime.datetime.now(datetime.timezone.utc) \
            - datetime.timedelta(**delta)
        return moment.replace(microsecond=0).isoformat()

    def test_a_recent_post_is_counted_in_seconds(self):
        self.assertTrue(
            re.fullmatch(r'\d+s', relative_time(self._ago(seconds=42))))

    def test_a_post_of_minutes_is_counted_in_minutes(self):
        self.assertTrue(
            re.fullmatch(r'\d+m', relative_time(self._ago(minutes=5))))

    def test_a_post_of_hours_is_counted_in_hours(self):
        self.assertTrue(
            re.fullmatch(r'\d+h', relative_time(self._ago(hours=3))))

    def test_a_post_of_days_is_counted_in_days(self):
        self.assertTrue(
            re.fullmatch(r'\d+d', relative_time(self._ago(days=12))))

    def test_an_old_post_carries_a_year(self):
        # Older than a year it becomes a month and year, whose month name
        # is translated -- so only the year is safe to assert on.
        self.assertIn('2020', relative_time('2020-01-01T12:00:00Z'))

    def test_a_timestamp_in_the_future_does_not_go_negative(self):
        moment = datetime.datetime.now(datetime.timezone.utc) \
            + datetime.timedelta(hours=1)
        self.assertEqual(
            relative_time(moment.replace(microsecond=0).isoformat()), '0s')

    def test_a_broken_timestamp_does_not_raise(self):
        for value in (None, '', 'not a date'):
            self.assertEqual(relative_time(value), '')


if __name__ == '__main__':
    unittest.main()
