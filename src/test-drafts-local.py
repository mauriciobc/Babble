#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
os.environ.setdefault('GSETTINGS_SCHEMA_DIR',
                      os.path.join(os.path.dirname(srcdir), 'data'))

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio

from src.drafts import MAX_LOCAL_DRAFTS, drop_local_draft, store_local_draft
from src.drafts import Draft, Poll

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


settings = Gio.Settings.new('io.github.mauriciobc.Babble')
settings.reset('local-drafts')
store_local_draft(settings, 'a', None)
store_local_draft(settings, 'b', 'a')
check('store prepends and replaces previous',
      settings.get_strv('local-drafts') == ['b'])
for i in range(MAX_LOCAL_DRAFTS + 2):
    store_local_draft(settings, 'x%d' % i, None)
check('store respects MAX_LOCAL_DRAFTS',
      len(settings.get_strv('local-drafts')) == MAX_LOCAL_DRAFTS)
drop_local_draft(settings, settings.get_strv('local-drafts')[0])
check('drop removes one entry',
      len(settings.get_strv('local-drafts')) == MAX_LOCAL_DRAFTS - 1)

legacy = {'text': 'legacy', 'visibility': 0, 'language': 0,
          'cw_visible': False, 'cw_text': '', 'attachments': [],
          'poll': {'active': False, 'options': ['', ''], 'expires': 0},
          'in_reply_to_id': None}
check('legacy draft JSON remains readable', Draft.parse(legacy) is not None)
for value in (True, 0, -1, '7200'):
    check('invalid exact duration is rejected: %r' % value,
          Poll.parse(dict(legacy['poll'], duration_seconds=value)) is None)
for value in ('', 1, False):
    check('invalid exact language is rejected: %r' % value,
          Draft.parse(dict(legacy, language_code=value)) is None)
check('poll options must be strings',
      Poll.parse(dict(legacy['poll'], options=['a', 2])) is None)
exact = dict(legacy, language_code='zz', poll={
    'active': True, 'options': ['a', 'b', 'c'], 'expires': 0,
    'duration_seconds': 7200, 'multiple': True, 'hide_totals': True})
draft = Draft.parse(exact)
check('exact language duration and poll flags round trip',
      Draft.parse_raw(draft.as_stored('instance#id', 'now')).as_dict()
      == draft.as_dict())

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
