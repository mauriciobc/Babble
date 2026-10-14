# SPDX-License-Identifier: GPL-3.0-or-later

"""The capability gate rule, as a test so it cannot drift back.

``client.mastodon_api_version()`` answers ``None`` when the instance says
nothing about ``api_versions.mastodon`` -- GoToSocial, a Mastodon older
than 4.3, or a fetch that failed. ``None`` does not compare to an int, so
``None >= 9`` is a ``TypeError`` rather than a ``False``: a call site
that spells the comparison out itself turns "this server did not say"
into a crash in whatever it was about to build. profileedit.py did
exactly that in three places, inside ``__init__``, and the dialog is
built even when the version is unknown, so Edit Profile raised instead of
opening.

The answer is ``client.is_supported(min_version, when_unknown)``, which
collapses the tri-state with a policy the caller states -- see CLAUDE.md,
"Capability gating". This scans for the shape that broke: an ordering
comparison against the accessor call itself.

The scan is deliberately narrow. The spelled-out form

    version = client.mastodon_api_version()
    if version is not None and version < 6:

passes, because the comparison is against a name rather than the call.
That form is only as good as the guard beside it, which is why the seam
is preferred; what this catches is the direct spelling, because that is
the one that shipped.

Needs no display and no server: it only reads source.
"""

import ast
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))

ACCESSOR = 'mastodon_api_version'
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def is_accessor(node):
    return isinstance(node, ast.Call) \
        and isinstance(node.func, ast.Attribute) \
        and node.func.attr == ACCESSOR


def offenders(path):
    """Line numbers of ordering comparisons against the accessor call."""
    with open(path, encoding='utf-8') as source:
        tree = ast.parse(source.read(), path)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ORDERING) for op in node.ops):
            continue
        operands = [node.left] + list(node.comparators)
        if any(is_accessor(operand) for operand in operands):
            found.append(node.lineno)
    return found


modules = sorted(name for name in os.listdir(srcdir)
                 if name.endswith('.py') and not name.startswith('test-'))

for name in modules:
    lines = offenders(os.path.join(srcdir, name))
    check('%s asks for its gates rather than comparing the accessor'
          % name, not lines)
    for line in lines:
        print('    %s:%d compares %s() directly; ask is_supported() '
              'instead' % (name, line, ACCESSOR))

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('every version gate is asked, not compared')
