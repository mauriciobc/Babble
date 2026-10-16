# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the status text layer (no display, no network).

Split out of ``timeline.py`` precisely so this can run headless: the
parsing and the timestamps are worth pinning on their own, without
standing up a window to reach them.
"""

import datetime
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# The GSettings fallback is read from a schema compiled here, never from
# the desktop's own settings.
os.environ['GSETTINGS_BACKEND'] = 'memory'

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import statustext  # noqa: E402
from src.statustext import (clock_format, compact_count,  # noqa: E402
                            format_time, is_hashtag, is_web_link,
                            parse_content, relative_time,
                            shorten_url_text)

from gi.repository import Gio, GLib  # noqa: E402

INTERFACE_SCHEMA = '''<?xml version="1.0" encoding="UTF-8"?>
<schemalist>
  <schema id="org.gnome.desktop.interface"
          path="/org/gnome/desktop/interface/">
    <key name="clock-format" type="s"><default>'12h'</default></key>
  </schema>
</schemalist>
'''


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


class TestWebLink(unittest.TestCase):
    def test_http_and_https_may_be_launched(self):
        for uri in ('https://x.test/a', 'http://x.test', 'HTTPS://X.test/'):
            self.assertTrue(is_web_link(uri), uri)

    def test_other_schemes_never_reach_the_launcher(self):
        # What a server that does not sanitise its HTML could serve.
        for uri in ('javascript:alert(1)', 'file:///etc/passwd',
                    'smb://host/share', 'babble://oauth/callback?code=x',
                    'data:text/html,x', 'https:no-host', '', None):
            self.assertFalse(is_web_link(uri), uri)

    def test_a_backslash_is_a_slash_whatever_surrounds_the_scheme(self):
        # A browser strips leading spaces and every tab before it reads
        # the scheme, then treats the backslash as a slash: all three
        # open evil.test, so the gate must not see bank.test.
        for uri in ('https://evil.test\\@bank.test',
                    ' https://evil.test\\@bank.test',
                    'https\t://evil.test\\@bank.test',
                    '\x01\nhttps://evil.test\\@bank.test'):
            self.assertTrue(is_web_link(uri), repr(uri))
            self.assertEqual(
                statustext.url_host(statustext.split_url(uri)),
                'evil.test', repr(uri))

    def test_a_malformed_address_is_refused_not_raised(self):
        # urlsplit raises ValueError on an unmatched '['.
        self.assertFalse(is_web_link('https://[oops/x'))


class TestShortenUrl(unittest.TestCase):
    def test_the_host_and_path_are_kept(self):
        self.assertEqual(
            shorten_url_text('https://example.test/a/b'),
            'example.test/a/b')

    def test_a_long_url_is_truncated_to_one_line(self):
        out = shorten_url_text('https://example.test/' + 'x' * 200)
        self.assertLessEqual(len(out), 43)
        self.assertTrue(out.endswith('…'))

    def test_credentials_never_disguise_the_host(self):
        self.assertEqual(
            shorten_url_text('https://bank.test:pw@evil.test/login'),
            'evil.test/login')
        # Long enough to be truncated: the real host must still lead.
        out = shorten_url_text(
            'https://bank.test.verify.account.example.com@evil.test/x')
        self.assertTrue(out.startswith('evil.test'), out)

    def test_a_backslash_ends_the_host_as_in_a_browser(self):
        # A browser opens evil.test here; the label must say so.
        self.assertEqual(shorten_url_text('https://evil.test\\@bank.test/x'),
                         'evil.test/@bank.test/x')

    def test_a_malformed_url_is_left_alone(self):
        self.assertEqual(shorten_url_text('https://[oops/x'),
                         'https://[oops/x')

    def test_the_port_is_kept(self):
        self.assertEqual(shorten_url_text('https://u@example.test:8443/a'),
                         'example.test:8443/a')

    def test_something_that_is_not_a_url_is_left_alone(self):
        self.assertEqual(shorten_url_text('not a url'), 'not a url')


class TestParseTime(unittest.TestCase):
    """The one ISO-8601 reader.

    Four modules had their own before this, and two disagreed about
    whether a stamp with no offset was an error or UTC. Every timestamp
    the API sends is UTC, so the shared answer is: read a naive one as
    UTC, and never raise on anything.
    """

    def test_a_zulu_stamp(self):
        self.assertEqual(
            statustext.parse_time('2026-10-13T12:00:00.000Z'),
            datetime.datetime(2026, 10, 13, 12, 0,
                               tzinfo=datetime.timezone.utc))

    def test_an_offset_is_kept(self):
        moment = statustext.parse_time('2026-10-13T08:00:00-04:00')
        self.assertIsNotNone(moment)
        self.assertEqual(moment.utcoffset(), datetime.timedelta(hours=-4))

    def test_a_stamp_with_no_offset_is_utc(self):
        # The disagreement this replaced: filters.py returned None here
        # while timeline.py assumed UTC.
        self.assertEqual(
            statustext.parse_time('2026-10-13T12:00:00'),
            datetime.datetime(2026, 10, 13, 12, 0,
                               tzinfo=datetime.timezone.utc))

    def test_nonsense_is_none_rather_than_an_exception(self):
        for value in ('not a time', '', None, 42, [], '2026-13-45'):
            self.assertIsNone(statustext.parse_time(value), value)

    def test_the_dependent_readers_agree(self):
        # relative_time and the timeline's own helpers all go through
        # this now; a stamp none of them can read is a bug in one place.
        self.assertEqual(
            statustext.parse_time('2026-10-13T12:00:00Z'),
            datetime.datetime(2026, 10, 13, 12, 0,
                               tzinfo=datetime.timezone.utc))


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


class ClockState(unittest.TestCase):
    """Saves and restores the module's clock cache, so a test can set
    it -- or the bus and schema lookups behind it -- freely."""

    NAMES = ('_clock_format', '_clock_subscription', '_clock_settings',
             '_session_bus', '_schema_source')

    def setUp(self):
        self._saved = {name: getattr(statustext, name)
                       for name in self.NAMES}

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(statustext, name, value)


class TestFormatTime(ClockState):
    """The format strings are translatable, so assert the time part --
    computed from the same local time -- and the year, not exact text."""

    MOMENT = GLib.DateTime.new_utc(2026, 10, 9, 14, 47, 0)

    def setUp(self):
        super().setUp()
        local = self.MOMENT.to_local()
        self.hour, self.minute = local.get_hour(), local.get_minute()

    def test_24_hour_time_with_date(self):
        statustext._clock_format = '24h'
        text = format_time(self.MOMENT)
        self.assertIn('2026', text)
        self.assertTrue(text.endswith('%02d:%02d' % (self.hour, self.minute)))

    def test_24_hour_time_alone(self):
        statustext._clock_format = '24h'
        self.assertEqual(format_time(self.MOMENT, with_date=False),
                         '%02d:%02d' % (self.hour, self.minute))

    def test_12_hour_time_with_date(self):
        statustext._clock_format = '12h'
        text = format_time(self.MOMENT)
        self.assertIn('2026', text)
        self.assertIn(', %d:%02d' % (self.hour % 12 or 12, self.minute), text)

    def test_12_hour_time_alone(self):
        statustext._clock_format = '12h'
        text = format_time(self.MOMENT, with_date=False)
        self.assertTrue(text.startswith('%d:%02d' % (self.hour % 12 or 12,
                                                     self.minute)))

    def test_an_aware_datetime_reads_like_a_glib_one(self):
        statustext._clock_format = '24h'
        moment = datetime.datetime(2026, 10, 9, 14, 47,
                                   tzinfo=datetime.timezone.utc)
        self.assertEqual(format_time(moment), format_time(self.MOMENT))

    def test_no_moment_is_no_text(self):
        self.assertEqual(format_time(None), '')


class TestClockFormat(ClockState):
    def setUp(self):
        super().setUp()
        statustext._clock_format = None
        statustext._session_bus = lambda: None

    def test_no_bus_and_no_schema_is_24_hour(self):
        lookups = []

        def no_source():
            lookups.append(True)
            return None
        statustext._schema_source = no_source
        self.assertEqual(clock_format(), '24h')
        self.assertEqual(clock_format(), '24h')
        self.assertEqual(len(lookups), 1, 'read once, then cached')

    @unittest.skipUnless(shutil.which('glib-compile-schemas'),
                         'needs glib-compile-schemas')
    def test_no_bus_falls_back_to_gsettings(self):
        with tempfile.TemporaryDirectory() as schemas:
            with open(os.path.join(schemas, 'interface.gschema.xml'),
                      'w') as handle:
                handle.write(INTERFACE_SCHEMA)
            subprocess.run(['glib-compile-schemas', schemas], check=True)
            source = Gio.SettingsSchemaSource.new_from_directory(
                schemas, None, False)
        statustext._schema_source = lambda: source
        self.assertEqual(clock_format(), '12h')
        # A change to the setting reaches the cache.
        statustext._clock_settings.set_string('clock-format', '24h')
        self.assertEqual(clock_format(), '24h')

    def test_a_portal_change_updates_the_cache(self):
        statustext._clock_format = '24h'

        def changed(namespace, key, value):
            statustext._on_portal_setting_changed(
                None, None, None, None, None,
                GLib.Variant('(ssv)', (namespace, key,
                                       GLib.Variant('s', value))))
        changed('org.gnome.desktop.interface', 'clock-format', '12h')
        self.assertEqual(clock_format(), '12h')
        changed('org.gnome.desktop.interface', 'color-scheme', '24h')
        self.assertEqual(clock_format(), '12h')
        changed('org.gnome.desktop.interface', 'clock-format', 'bogus')
        self.assertEqual(clock_format(), '24h')


class TestCompactCount(unittest.TestCase):
    def setUp(self):
        self._numeric = locale.setlocale(locale.LC_NUMERIC)
        locale.setlocale(locale.LC_NUMERIC, 'C')

    def tearDown(self):
        locale.setlocale(locale.LC_NUMERIC, self._numeric)

    def test_small_counts_are_the_plain_number(self):
        self.assertEqual(compact_count(0), '0')
        self.assertEqual(compact_count(7), '7')
        self.assertEqual(compact_count(999), '999')

    def test_thousands_drop_a_zero_decimal(self):
        self.assertEqual(compact_count(1000), '1K')
        self.assertEqual(compact_count(1049), '1K')
        self.assertEqual(compact_count(12000), '12K')

    def test_thousands_keep_one_decimal(self):
        self.assertEqual(compact_count(3400), '3.4K')
        self.assertEqual(compact_count(12345), '12.3K')

    def test_a_half_decimal_rounds_by_python_round(self):
        # 1050 / 1000 is 1.05, which is a little above the midpoint as a
        # float, so round(x, 1) gives 1.1; it must not crash either way.
        self.assertEqual(compact_count(1050), '1.1K')

    def test_rounding_up_to_1000_K_moves_to_the_next_unit(self):
        self.assertEqual(compact_count(999950), '1M')
        self.assertEqual(compact_count(999949), '999.9K')

    def test_millions_and_billions(self):
        self.assertEqual(compact_count(1000000), '1M')
        self.assertEqual(compact_count(2100000), '2.1M')
        self.assertEqual(compact_count(1000000000), '1B')
        self.assertEqual(compact_count(1200000000), '1.2B')

    def test_none_and_negatives_count_as_zero(self):
        self.assertEqual(compact_count(None), '0')
        self.assertEqual(compact_count(-5), '0')

    def test_pt_BR_uses_the_decimal_comma(self):
        try:
            locale.setlocale(locale.LC_NUMERIC, 'pt_BR.UTF-8')
        except locale.Error:
            self.skipTest('pt_BR.UTF-8 locale is not installed')
        self.assertEqual(compact_count(3400), '3,4K')
        self.assertEqual(compact_count(2100000), '2,1M')
        self.assertEqual(compact_count(999), '999')



if __name__ == '__main__':
    unittest.main()
