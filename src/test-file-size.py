# SPDX-License-Identifier: GPL-3.0-or-later

"""The file size rule, as a test so it cannot drift silently.

No module the window was broken into shall grow past ``LIMIT`` lines.
They came out of window.py one seam each, and the size is what keeps a
candidate seam from quietly moving back in: growth in one of these
files is how a God class reappears without anybody calling it that.

window.py is a recorded exception rather than a silent pass, with the
reason we could not cut it further named right there rather than left
unrecorded for the next reader to re-litigate.

This is number of lines, which ruff covers through E501 (characters per
line), not module length -- ruff has no rule for the latter.
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))

LIMIT = 800

# One seam each, extracted from window.py. A growth in one of these is
# how a God class reappears without anybody calling it that.
SEAMS = ('portals.py', 'auth.py', 'drafts.py', 'clipboard.py')

# Recorded in CLAUDE.md, not silently tolerated. The reason is stated
# here, where a future reader will meet it, and mirrored in CLAUDE.md;
# the line count itself is what this test prints above, so it is not
# repeated here to go stale.
EXCEPTIONS = {
    'window.py': 'four of five seams extracted (portals, auth, drafts, '
                 'clipboard); the remaining lines are view routing over '
                 'page containers the window owns, and extracting them '
                 'would invert streaming.py\'s dependency on that state '
                 'rather than cut a seam.',
}

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


for name in SEAMS:
    path = os.path.join(srcdir, name)
    lines = open(path, encoding='utf-8').read().count('\n')
    check('%s at %d lines stays under %d' % (name, lines, LIMIT),
          lines <= LIMIT)

for name, reason in EXCEPTIONS.items():
    path = os.path.join(srcdir, name)
    lines = open(path, encoding='utf-8').read().count('\n')
    check('%s is the declared exception (%d lines over the limit)' % (
        name, max(0, lines - LIMIT)),
        name in EXCEPTIONS and lines > LIMIT and bool(reason))
    print('    %s: %s' % (name, reason))

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all file size checks passed')
