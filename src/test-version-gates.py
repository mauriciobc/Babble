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
comparison against the accessor call itself, and a module reading the
human ``version`` string to decide instead of asking at all.

The scan is deliberately narrow. The spelled-out form

    version = client.mastodon_api_version()
    if version is not None and version < 6:

passes, because the comparison is against a name rather than the call.
That form is only as good as the guard beside it, which is why the seam
is preferred; what this catches is the direct spelling, because that is
the one that shipped.

The second check is narrower still. ``explore.supports_link_timeline``
once decided by ``re.match(r'^(\\d+)\\.(\\d+)', instance_info['version'])``
-- a marketing version string that a fork may set to anything, parsed
where every other gate asks. Reading ``instance_info['version']`` to
answer "is this feature here" is the mistake; showing a version to the
reader is not, so the scan is for the combination of the key with a
regex or an int() call.

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


# The human version string, read to decide a capability rather than to
# show a version. ``instance_info['version']`` on its own is fine --
# profile.py and admin.py both surface it -- so it takes a sibling that
# parses it: a re.match on it, or int() of what came out.
VERSION_KEY = 'version'
PARSE_NAMES = {'match', 'search', 'fullmatch', 'findall', 'int'}


def _is_version_key(node):
    """A ``.get('version')`` or ``['version']``, on anything at all."""
    if isinstance(node, ast.Subscript):
        key = node.slice
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == 'get' and node.args):
        key = node.args[0]
    else:
        return False
    return isinstance(key, ast.Constant) and key.value == VERSION_KEY


def _mentions_version(node, bound):
    """Whether *node* reads the version key, directly or through a name
    bound to it earlier in the module."""
    if any(_is_version_key(sub) for sub in ast.walk(node)):
        return True
    return any(isinstance(sub, ast.Name) and sub.id in bound
               and any(_is_version_key(deeper)
                       for deeper in ast.walk(bound[sub.id]))
               for sub in ast.walk(node))


def parses_version_string(path):
    """Line numbers where the human version string is parsed."""
    with open(path, encoding='utf-8') as source:
        tree = ast.parse(source.read(), path)
    # One level of name resolution is enough: the shape this catches is
    # ``version = info.get('version')`` then ``re.match(pat, version)``.
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            bound[node.targets[0].id] = node.value
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if name not in PARSE_NAMES:
            continue
        # int(x) with no argument is not parsing anything.
        if name == 'int' and not node.args:
            continue
        # For re.match(pat, value) the pattern is arg 0 and is never the
        # thing being parsed; skipping it avoids matching a regex literal.
        for arg in (node.args[1:] if name != 'int' else node.args):
            if _mentions_version(arg, bound):
                found.append(node.lineno)
                break
    return sorted(set(found))


modules = sorted(name for name in os.listdir(srcdir)
                 if name.endswith('.py') and not name.startswith('test-'))

for name in modules:
    lines = offenders(os.path.join(srcdir, name))
    check('%s asks for its gates rather than comparing the accessor'
          % name, not lines)
    for line in lines:
        print('    %s:%d compares %s() directly; ask is_supported() '
              'instead' % (name, line, ACCESSOR))

for name in modules:
    parsed = parses_version_string(os.path.join(srcdir, name))
    check('%s does not decide a gate from the version string' % name,
          not parsed)
    for line in parsed:
        print('    %s:%d parses instance_info["version"]; ask '
              'is_supported() instead' % (name, line))

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('every version gate is asked, not compared or parsed')
