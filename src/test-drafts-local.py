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

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
