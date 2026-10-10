# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import unittest

sys.path.insert(0, '.')

from mastodon import summarize_error  # noqa: E402

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
        # '<b>' from an attacker-supplied body must not survive as markup.
        out = summarize_error('<b>bold</b> & <a>x</a>')
        self.assertNotIn('<b>', out)

    def test_long_bodies_truncated(self):
        out = summarize_error('x' * 500)
        self.assertLessEqual(len(out), 240)
        self.assertTrue(out.endswith('…'))


if __name__ == '__main__':
    unittest.main()
