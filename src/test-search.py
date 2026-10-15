#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import pages, search  # noqa: E402
from src.statustext import compact_count  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('compact_number is the shared formatter',
      search.compact_number(1200) == compact_count(1200))

if os.environ.get('DISPLAY'):
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    dialog = search.AdvancedSearchDialog('')
    dialog._load('from:alice is:reply has:media language:pt extra words')
    built = dialog.build_query()
    check('advanced search builds from:',
          'from:alice' in built and 'is:reply' in built)
    check('advanced search keeps free text', 'extra' in built)
    again = search.AdvancedSearchDialog(built)
    check('advanced search round-trips', again.build_query() == built)

    opened = []
    pages.open_status = lambda *args: opened.append(('status', args[-1]))
    pages.open_profile = lambda *args: opened.append(('profile', args[-1]))

    class _Client:
        def __init__(self):
            self.calls = []
            self.body = {}

        def request_async(self, method, path, params=None, callback=None,
                          **_kwargs):
            self.calls.append((method, path, dict(params or {})))
            if callback is not None:
                callback(self, self.body)

        def request_finish(self, result):
            return result

    client = _Client()
    page = search.SearchPage(client)
    client.body = {'statuses': [{'id': 's1'}]}
    page._resolve_url('https://example.social/@ada/1')
    check('a resolved status opens the thread',
          opened and opened[-1][0] == 'status'
          and opened[-1][1]['id'] == 's1'
          and client.calls[-1][2].get('resolve') == 'true')
    calls = len(client.calls)
    page._resolve_url('https://example.social/@ada/1')
    check('the same url is not resolved twice', len(client.calls) == calls)
    client.body = {'accounts': [{'id': 'a1'}]}
    page._resolve_url('https://example.social/@ada', force=True)
    check('a resolved account opens the profile',
          opened[-1][0] == 'profile' and opened[-1][1]['id'] == 'a1')

    # Posts in the results used to raise while the row was built, so the
    # search stayed on its loading page forever.
    client.body = {'statuses': [{
        'id': 's2', 'content': '<p>a post about ferns</p>',
        'created_at': '2026-10-01T10:00:00.000Z', 'visibility': 'public',
        'account': {'id': '7', 'acct': 'fern', 'display_name': 'Fern'}}]}
    page._request({'q': 'ferns'}, page._show_results)
    check('a search with posts leaves the loading page',
          page.stack.get_visible_child_name() == 'results')
    check('the post is shown as a result row',
          page.results_box.get_first_child() is not None)
else:
    print('SKIP - advanced search dialog (no display)')

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
