#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin notifications error-handling policy (TEST-07)."""

import os
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
# The page and its controller: paging and the read marker moved with
# ARCH-03, the policy dialog stayed on the page.
source = ''
for name in ('notifications.py', 'notificationctrl.py'):
    path = os.path.join(srcdir, name)
    source += open(path, encoding='utf-8').read()

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('page-1 errors show the error page',
      'on_page_loaded' in source
      and re.search(r'def on_page_loaded.*?stack\.set_visible_child_name\(\'error\'\)',
                    source, re.S))
check('page-2 errors are swallowed in on_older_loaded',
      re.search(r'def on_older_loaded.*?except GLib\.Error:\s+return',
                source, re.S))
check('markers gate unread_count',
      '_on_markers_before_unread' in source
      and 'markers_async' in source)
check('policy dialog distinguishes a 404 from other errors',
      re.search(
          r'def on_loaded\(.*?getattr\(error, \'code\', 0\) == 404',
          source, re.S))
check('policy summary fetch swallows every error',
      re.search(
          r'def on_policy_loaded\(.*?except GLib\.Error:\s+'
          r'# Servers before Mastodon 4\.3 have no policy endpoint\.\s+'
          r'self\.requests_banner\.set_revealed\(False\)\s+return',
          source, re.S))
check('refresh and clear report through pages.report_error',
      "pages.report_error(self, _('Refresh failed'), e)" in source
      and "pages.report_error(self, _('Clear failed'), e)" in source
      and 'hasattr(root, \'show_error\')' not in source)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
