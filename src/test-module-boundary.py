# SPDX-License-Identifier: GPL-3.0-or-later

"""The Catch Up boundary, as a test so it cannot drift silently.

`Docs/timeline-algorithm.md` §5.0 draws a line through the module graph:
the ranked place may use Catch Up's ranker and nothing else of Catch Up,
Catch Up may not use the ranked place, and the ranker may use only the
standard library. That is an architectural promise rather than a runtime
behaviour, so nothing else in the tree would notice it being broken; an
import added for convenience would fail only in review, or not at all.

The checker reads sources with `ast`, as `ui strings` does, so an import
inside a function counts (`catchup.compute()` imports its ranker lazily).
It is a function rather than the script body so the self-checks below can
feed it synthetic sources; without them the test would pass vacuously
until the first new module exists.
"""

import ast
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(srcdir)

# The modules the boundary is drawn around, by file stem.
CATCHUP = frozenset({
    'catchup', 'catchupdigest', 'catchupgrid', 'catchupstore',
    'catchuprank'})
NEW = frozenset({'affinity', 'rankedstore', 'rankedfeed', 'rankedpage'})

# The harness lives outside src/ and does not exist yet; it is read only
# once it appears.
BENCH = ('rank_eval.py', 'ranked_stall.py')


def stem(path):
    """The module name of a source path, without its directory or suffix."""
    return os.path.basename(path)[:-len('.py')]


def is_catchup_importer(name):
    return name in CATCHUP or name.startswith('test-catchup')


def is_new_importer(name):
    return (name in NEW or name == 'test-affinity'
            or name.startswith('test-ranked-')
            or name in ('rank_eval', 'ranked_stall'))


def source_imports(tree):
    """(module in src, line) for every src module a syntax tree imports.

    The spec's five forms all name a module the same way: the first
    component under `src`. `import src.x`, `from src import x`,
    `from src.x import y`, `from . import x` and `from .x import y` all
    yield `x`.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split('.')
                if len(parts) > 1 and parts[0] == 'src':
                    found.append((parts[1], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    found.append((node.module.split('.')[0], node.lineno))
                else:
                    for alias in node.names:
                        found.append((alias.name.split('.')[0], node.lineno))
            elif node.module == 'src':
                for alias in node.names:
                    found.append((alias.name.split('.')[0], node.lineno))
            elif node.module and node.module.startswith('src.'):
                found.append((node.module.split('.')[1], node.lineno))
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

    *sources* maps a display path (`src/catchup.py`) to its source text,
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
                    and module != 'catchuprank'):
                found.append('R1: %s:%d imports %s'
                             % (path, line, module))
            if is_catchup_importer(importer) and module in NEW:
                found.append('R2: %s:%d imports %s'
                             % (path, line, module))
        if importer == 'catchuprank':
            for name, line, relative in all_imports(tree):
                if relative:
                    found.append('R3: %s:%d has a relative import'
                                 % (path, line))
                elif name.split('.')[0] not in sys.stdlib_module_names:
                    found.append(
                        'R3: %s:%d imports %s outside the standard library'
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
# The new modules do not exist yet, so a checker that never matched
# anything would pass. Each rule is shown a source that violates it, and
# the expected report names the file and the line.

self_check('R1 catches a new module importing Catch Up',
           {'src/affinity.py': 'from . import catchupdigest\n'},
           'R1: src/affinity.py:1 imports catchupdigest')
self_check('R2 catches a Catch Up module importing a new module',
           {'src/catchup.py': 'def load():\n    from . import rankedfeed\n'},
           'R2: src/catchup.py:2 imports rankedfeed')
self_check('R3 catches an import outside the standard library',
           {'src/catchuprank.py': 'import requests\n'},
           'R3: src/catchuprank.py:1 imports requests')
self_check('R3 catches a relative import',
           {'src/catchuprank.py': 'from . import i18n\n'},
           'R3: src/catchuprank.py:1 has a relative import')
check('the checker accepts the ranker import R1 allows',
      not violations({'src/affinity.py': 'from . import catchuprank\n'}))


def tree_sources():
    """The sources under the rules, keyed by the path shown in reports."""
    sources = {}
    for name in sorted(os.listdir(srcdir)):
        if not name.endswith('.py'):
            continue
        with open(os.path.join(srcdir, name), encoding='utf-8') as module:
            sources['src/' + name] = module.read()
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
    ('R1', 'a new module imports nothing from Catch Up but catchuprank'),
    ('R2', 'Catch Up imports no new module'),
    ('R3', 'catchuprank imports only the standard library'),
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
