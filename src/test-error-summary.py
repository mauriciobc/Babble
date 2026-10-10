# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import unittest

sys.path.insert(0, '.')

from mastodon import summarize_error, summarize_error_markup  # noqa: E402

# The exact nginx 403 page ursal.zone served to the default libsoup
# session: markup parsing of this body left the Adw.Toast empty, which is
# how the login failure went invisible until it was caught live.
NGINX_403 = ('<html>\r\n<head><title>403 Forbidden</title></head>\r\n'
             '<body>\r\n<center><h1>403 Forbidden</h1></center>\r\n'
             '<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n')


class TestErrorSummary(unittest.TestCase):
    def test_html_error_body_becomes_readable_text(self):
        out = summarize_error(NGINX_403)
        self.assertTrue(out, 'must never be empty')
        self.assertIn('403 Forbidden', out)
        self.assertIn('nginx', out)
        self.assertNotIn('<', out)

    def test_markup_ambush_is_rendered_as_text(self):
        # The output reaches Adw.Toast with use_markup=False, so '<b>'
        # must be stripped and '&' must survive as itself rather than as
        # an escaped '&amp;' the user would read literally.
        out = summarize_error('<b>bold</b> & <a>x</a>')
        self.assertNotIn('<b>', out)
        self.assertNotIn('&amp;', out)
        self.assertIn('&', out)

    def test_markup_variant_survives_a_bare_ampersand(self):
        # Adw.StatusPage parses its description as markup: an unescaped
        # '&' fails the parse and the label comes out empty, so the error
        # is invisible -- the failure this variant exists to prevent.
        out = summarize_error_markup('Tom & Jerry')
        self.assertIn('&amp;', out)
        self.assertNotEqual(out, '')

    def test_markup_variant_is_parseable_by_pango(self):
        from gi.repository import GLib, Pango
        for body in ('Tom & Jerry', 'a & b & c', '<b>x</b> & y',
                     'broken &amp; entity'):
            parsed = summarize_error_markup(body)
            try:
                Pango.parse_markup(parsed, -1, '\0')
            except GLib.Error as error:
                self.fail('unparseable markup for %r: %s' % (body, error))

    def test_plain_variant_stays_literal_for_toasts(self):
        # The toast path must not receive escaped entities: the reader
        # would see a literal '&amp;'.
        self.assertEqual(summarize_error('Tom & Jerry'), 'Tom & Jerry')

    def test_long_bodies_truncated(self):
        out = summarize_error('x' * 500)
        self.assertLessEqual(len(out), 240)
        self.assertTrue(out.endswith('…'))


if __name__ == '__main__':
    unittest.main()
