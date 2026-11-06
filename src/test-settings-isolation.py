# SPDX-License-Identifier: GPL-3.0-or-later
"""No test script may reach the user's real settings.

Meson gives every test GSETTINGS_BACKEND=memory, but each script is also
run by hand as a standalone file, and then it gets whatever backend the
session has: dconf. A test that builds a BabbleWindow restores the real
stored account, and when the keyring it sees has no entry for it (a
throwaway D-Bus session's keyring is empty) the window forgets that
account. That cleared a real login's ``accounts`` and ``active-account``
once; this test keeps every script setting the memory backend itself,
before it imports anything from gi.
"""

import glob
import os
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
failures = []

for path in sorted(glob.glob(os.path.join(srcdir, 'test-*.py'))):
    name = os.path.basename(path)
    if name == os.path.basename(__file__):
        continue
    with open(path, encoding='utf-8') as f:
        text = f.read()
    backend = re.search(
        r"os\.environ(\.setdefault\(|\[)\s*'GSETTINGS_BACKEND'", text)
    gi_import = re.search(r'^(import gi|from gi)', text, re.M)
    if backend is None:
        failures.append('%s never sets GSETTINGS_BACKEND' % name)
    elif gi_import and gi_import.start() < backend.start():
        failures.append('%s imports gi before setting GSETTINGS_BACKEND'
                        % name)

for failure in failures:
    print('FAIL - ' + failure)
if failures:
    sys.exit(1)
print('ok - every test script selects the memory settings backend first')
