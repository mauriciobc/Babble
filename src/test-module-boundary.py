# SPDX-License-Identifier: GPL-3.0-or-later

"""The Catch Up boundary, as a test so it cannot drift silently.

`Docs/timeline-algorithm.md` §5.0 draws a line through the module graph:
the ranked place may use Catch Up's ranker and nothing else of Catch Up,
Catch Up may not use the ranked place, and the ranker may use only the
standard library. That is an architectural promise rather than a runtime
behaviour, so nothing else in the tree would notice it being broken; an
import added for convenience would fail only in review, or not at all.

The checker reads sources with `ast`, as `ui strings` does, so an import
inside a function counts (`pipeline.compute()` imports its ranker lazily).
It is a function rather than the script body so the self-checks can feed it
synthetic sources; each self-check requires an exact violation.
"""

import ast
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(srcdir)

# Catch Up's own modules are package submodules; the source parser records
# their first module under `src` for each supported import form.
CATCHUP = frozenset({
    'catchup', 'page', 'page_controls', 'pipeline', 'fetch', 'rows', 'store',
    'digest', 'digest_layout', 'rank', 'rank_text', 'rank_model', 'tiles',
    'tiles_render', 'geometry'})
RANK = frozenset({'rank', 'rank_text', 'rank_model'})
NEW = frozenset({'affinity', 'rankedstore', 'rankedfeed', 'rankedpage',
                 'navigation', 'tileactions'})

# The harness lives outside src/ and does not exist yet; it is read only
# once it appears.
BENCH = ('rank_eval.py', 'ranked_stall.py')

# The ways to reach the network that the harness must not take for
# itself. The server is reached through `src/mastodon.py`, which owns the
# token and the request shape; a second HTTP path inside the harness
# would be a second way for a bearer token to leave the machine.
HTTP_PATHS = frozenset({'http', 'http.client', 'urllib.request', 'requests',
                        'httpx', 'aiohttp', 'socket', 'ssl'})


def stem(path):
    """The module name of a source path, without its directory or suffix."""
    return os.path.basename(path)[:-len('.py')]


def is_catchup_importer(path):
    return path.startswith('src/catchup/') or stem(path) in CATCHUP \
        or stem(path).startswith('test-catchup')


def is_new_importer(name):
    return (name in NEW and name not in ('affinity', 'navigation')
            or name == 'test-affinity'
            or name.startswith('test-ranked-')
            or name in ('rank_eval', 'ranked_stall'))


def source_imports(tree):
    """(module in src, line) for every src module a syntax tree imports.

    The known import forms all yield the first owned module under `src`.
    For the `src.catchup.rank_*` package, that means the rank module rather
    than the package root.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split('.')
                if len(parts) > 1 and parts[0] == 'src':
                    module = (parts[2] if len(parts) > 2
                              and parts[1] == 'catchup' else parts[1])
                    found.append((module, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    found.append((node.module.split('.')[0], node.lineno))
                else:
                    for alias in node.names:
                        found.append((alias.name.split('.')[0], node.lineno))
            elif node.module == 'src.catchup':
                for alias in node.names:
                    imported = alias.name.split('.')[0]
                    found.append((imported if imported in CATCHUP
                                  else 'catchup', node.lineno))
            elif node.module and node.module.startswith('src.'):
                parts = node.module.split('.')
                module = (parts[2] if len(parts) > 2
                          and parts[1] == 'catchup' else parts[1])
                found.append((module, node.lineno))
    return found


def all_imports(tree):
    """(module, line, relative) for every import a syntax tree makes.

    This is the ranker's view: unlike `source_imports` it keeps the names
    outside `src`, because R3 judges them by `sys.stdlib_module_names`.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno, False))
        elif isinstance(node, ast.ImportFrom):
            relative = node.level > 0
            found.append((node.module or '', node.lineno, relative))
    return found


def violations(sources: dict[str, str]) -> list[str]:
    """Every boundary violation in *sources*, as `RULE file:line detail`.

    *sources* maps a display path (`src/catchup/page.py`) to its source text,
    so the self-checks can hand in a scratch copy without touching the
    tree.
    """
    found = []
    for path in sorted(sources):
        importer = stem(path)
        try:
            tree = ast.parse(sources[path], filename=path)
        except SyntaxError as error:
            found.append('unparsable: %s:%d %s'
                         % (path, error.lineno or 0, error.msg))
            continue
        for module, line in source_imports(tree):
            if (is_new_importer(importer) and module in CATCHUP
                    and module not in RANK
                    and not (path == 'src/navigation.py'
                             and module == 'catchup')):
                found.append('R1: %s:%d imports %s'
                             % (path, line, module))
            if (is_catchup_importer(path) and module in NEW
                    and not (path == 'src/catchup/tiles_render.py'
                             and module == 'tileactions')):
                found.append('R2: %s:%d imports %s'
                             % (path, line, module))
            if path == 'src/affinity.py' and module in RANK:
                found.append('R5: %s:%d imports rank module %s'
                             % (path, line, module))
            if (is_catchup_importer(path) and module in ('navigation',
                                                        'tileactions')
                    and not (path == 'src/catchup/tiles_render.py'
                             and module == 'tileactions')):
                found.append('R7: %s:%d imports window-owned %s'
                             % (path, line, module))
        if path.startswith('src/catchup/tiles'):
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('StatusActions',
                                               'StatusMenuButton')):
                    found.append('R6: %s:%d constructs %s instead of '
                                 'using tileactions'
                                 % (path, node.lineno, node.func.attr))
        if path.startswith(('src/catchup/rank.py',
                            'src/catchup/rank_text.py',
                            'src/catchup/rank_model.py')):
            for name, line, relative in all_imports(tree):
                if relative:
                    if (path.startswith('src/catchup/rank')
                            and name in ('rank_text', 'rank_model')):
                        continue
                    found.append('R3: %s:%d has a relative import'
                                 % (path, line))
                elif name.split('.')[0] not in sys.stdlib_module_names:
                    found.append(
                        'R3: %s:%d imports %s outside the standard library'
                        % (path, line, name))
        if path.startswith('bench/'):
            # R4: the harness talks to the server through the shared
            # client, and only through it. A second HTTP path inside the
            # harness would be a second way for a bearer token to reach
            # the network, with none of the client's care.
            for name, line, _relative in all_imports(tree):
                if name.split('.')[0] in HTTP_PATHS:
                    found.append('R4: %s:%d imports %s: the server is'
                                 ' reached through src/mastodon.py'
                                 % (path, line, name))
    return found


failures = 0


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


def self_check(label, sources, expected):
    """Assert that the checker reports the *expected* violation line."""
    found = violations(sources)
    if any(line.startswith(expected) for line in found):
        print('ok - %s' % label)
        return
    global failures
    failures += 1
    print('FAIL - %s' % label)
    for line in found:
        print('    %s' % line)


# --- The checker itself, before it judges the tree ----------------------
#
# Each rule gets synthetic source that violates it, so a broken matcher
# cannot pass merely because the current tree happens to respect the rule.

self_check('R1 catches a new module importing Catch Up',
           {'src/rankedfeed.py': 'from src.catchup import store\n'},
           'R1: src/rankedfeed.py:1 imports store')
self_check('R2 catches a Catch Up module importing a new module',
           {'src/catchup/tiles.py': 'def load():\n    from . import rankedfeed\n'},
           'R2: src/catchup/tiles.py:2 imports rankedfeed')
self_check('R3 catches an import outside the standard library',
           {'src/catchup/rank_model.py': 'import requests\n'},
           'R3: src/catchup/rank_model.py:1 imports requests')
self_check('R3 catches a relative import',
           {'src/catchup/rank_model.py': 'from . import i18n\n'},
           'R3: src/catchup/rank_model.py:1 has a relative import')
self_check('R4 catches the harness opening its own HTTP path',
           {'bench/rank_eval.py': 'import http.client\n'},
           'R4: bench/rank_eval.py:1 imports http.client')
self_check('R4 catches the harness importing a request library',
           {'bench/rank_eval.py': 'import requests\n'},
           'R4: bench/rank_eval.py:1 imports requests')
self_check('R5 rejects affinity importing the ranker',
           {'src/affinity.py': 'import src.catchup.rank_model\n'},
           'R5: src/affinity.py:1 imports rank module rank_model')
self_check('R6 rejects tile constructors outside the shared adapter',
           {'src/catchup/tiles_render.py':
            'def build():\n    return statusactions.StatusActions()\n'},
           'R6: src/catchup/tiles_render.py:2 constructs StatusActions')
self_check('R7 rejects Catch Up importing window-owned navigation',
           {'src/catchup/pipeline.py': 'from .. import navigation\n'},
           'R7: src/catchup/pipeline.py:1 imports window-owned navigation')
check('the checker accepts the harness reaching the server through the'
      ' client',
      not violations({'bench/rank_eval.py': 'from src import mastodon\n'
                        'import urllib.parse\n'}))


def tree_sources():
    """The sources under the rules, keyed by the path shown in reports."""
    sources = {}
    for directory, _dirs, names in os.walk(srcdir):
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, root)
            with open(absolute, encoding='utf-8') as module:
                sources[relative] = module.read()
    for name in BENCH:
        path = os.path.join(root, 'bench', name)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as module:
                sources['bench/' + name] = module.read()
    return sources


SOURCES = tree_sources()
FOUND = violations(SOURCES)

unparsable = [line for line in FOUND if line.startswith('unparsable:')]
for line in unparsable:
    print('    %s' % line)
check('every source parses (%d unparsable)' % len(unparsable), not unparsable)

RULES = (
    ('R1', 'new ranked-place modules import only rank modules'),
    ('R2', 'Catch Up imports no ranked-place modules'),
    ('R3', 'rank modules import only standard library and siblings'),
    ('R4', 'benchmark reaches server through the shared client'),
    ('R5', 'affinity imports no ranker modules'),
    ('R6', 'Catch Up tiles construct controls through tileactions'),
    ('R7', 'Catch Up imports no window-owned module'),
)
for rule, label in RULES:
    hits = [line for line in FOUND if line.startswith(rule + ':')]
    for line in hits:
        print('    %s' % line)
    check('%s %s (%d violations)' % (rule, label, len(hits)), not hits)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all module boundary checks passed')
