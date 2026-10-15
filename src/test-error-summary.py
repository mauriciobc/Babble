# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import unittest

import gi

gi.require_version('Pango', '1.0')

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from gi.repository import Gio, GLib  # noqa: E402

from src.mastodon import summarize_error, summarize_error_markup  # noqa: E402

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

    def test_takes_a_glib_error_too(self):
        # Every consumer past the transport has a GLib.Error in hand, not
        # a string; the four error_text helpers used to unwrap it
        # themselves, which is how they drifted.
        error = GLib.Error.new_literal(
            Gio.io_error_quark(), '<b>Tom & Jerry</b>', -1)
        self.assertEqual(summarize_error(error), 'Tom & Jerry')
        self.assertEqual(summarize_error_markup(error), 'Tom &amp; Jerry')

    def test_an_error_without_a_message_still_produces_a_line(self):
        # A plain object standing in for an error: no .message, so the
        # fallback is its own text. Nothing should raise here.
        self.assertEqual(summarize_error(RuntimeError('boom')), 'boom')
        self.assertEqual(summarize_error(None), '')

    def test_long_bodies_truncated(self):
        out = summarize_error('x' * 500)
        self.assertLessEqual(len(out), 240)
        self.assertTrue(out.endswith('…'))


class TestSummarizedAtTheSource(unittest.TestCase):
    """A server body must never reach a consumer whole.

    Every error leaves ``_on_response`` as a GLib.Error, and a consumer
    that renders it as markup blanks its label -- which is how a failure
    became invisible. Summarizing once, where the body enters, bounds
    every path; these pin that, including for the consumers that still
    summarize themselves.
    """

    # An nginx/CDN error page: not JSON, so it lands in `error` whole.
    HTML_502 = ('<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n'
                '<body><h1>502 Bad Gateway</h1></body>\r\n</html>\r\n')

    def _error_for(self, status, body):
        """The GLib.Error ``_on_response`` would raise for *body*."""
        from src.mastodon import MastodonClient

        client = MastodonClient('https://example.test')
        client._session = _FakeSession(body)

        class _Message:
            def get_status(self):
                return status

            def get_response_headers(self):
                return _FakeHeaders()

            def get_method(self):
                return 'GET'

            def get_uri(self):
                return _FakeURI()

        task = _FakeTask()
        client._pending[task] = _Message()
        client._on_response(None, None, task)
        return task.error

    def test_a_non_json_body_is_bounded_before_it_leaves(self):
        error = self._error_for(502, self.HTML_502)
        self.assertLessEqual(len(error.message), 240)
        self.assertIn('502 Bad Gateway', error.message)
        self.assertNotIn('<', error.message)

    def test_a_body_reaching_a_markup_widget_still_parses(self):
        from gi.repository import GLib, Pango
        for body in (self.HTML_502, 'Tom & Jerry', 'a & b',
                     '{"error":"Rejected: bad & ugly"}'):
            with self.subTest(body=body[:24]):
                message = self._error_for(500, body).message
                try:
                    Pango.parse_markup(summarize_error_markup(message),
                                       -1, '\0')
                except GLib.Error as error:
                    self.fail('unparseable for %r: %s' % (body, error))

    def test_the_status_code_is_kept_for_the_scope_check(self):
        # admin.py tells "needs the admin scopes" from "not a moderator"
        # by the code, so sanitizing the message must not lose it.
        self.assertEqual(self._error_for(403, 'Forbidden').code, 403)
        self.assertEqual(self._error_for(404, 'Record not found').code, 404)

    def test_an_empty_body_falls_back_to_the_status_phrase(self):
        self.assertTrue(self._error_for(500, '<html></html>').message)

    def test_a_second_summarize_changes_nothing(self):
        once = self._error_for(502, self.HTML_502).message
        self.assertEqual(summarize_error(once), once)


class _FakeTask:
    """The Gio.Task surface ``_on_response`` touches."""

    def __init__(self):
        self.error = None

    def return_error(self, error):
        self.error = error

    def return_value(self, _value):
        pass


class _FakeSession:
    def __init__(self, body):
        self._body = body

    def send_and_read_finish(self, _result):
        return _FakeBytes(self._body.encode())


class _FakeBytes:
    def __init__(self, data):
        self._data = data

    def get_data(self):
        return self._data


class _FakeHeaders:
    def get_one(self, _name):
        return None


class _FakeURI:
    """Just enough of GUri for the deprecation log line."""

    def get_path(self):
        return '/api/v1/statuses'


if __name__ == '__main__':
    unittest.main()
