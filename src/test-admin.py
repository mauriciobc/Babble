#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from gi.repository import GLib  # noqa: E402

from src.admin import (PERM_ADMINISTRATOR, PERM_REPORTS, PERM_USERS,  # noqa: E402
                       needs_admin_scopes, permission_allows,
                       role_permissions)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('unknown permissions allow', permission_allows(None, PERM_REPORTS))
check('admin bit allows', permission_allows(PERM_ADMINISTRATOR, PERM_REPORTS))
check('missing bit denies', not permission_allows(PERM_REPORTS, PERM_USERS))
check('role permissions parse int',
      role_permissions({'role': {'permissions': '6'}}) == 6)
check('role permissions missing', role_permissions({}) is None)
check('needs scopes on 403 with scope text',
      needs_admin_scopes(GLib.Error('outside the authorized scopes', 'q', 403)))
check('role 403 is not scopes',
      not needs_admin_scopes(GLib.Error('Forbidden', 'q', 403)))

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
