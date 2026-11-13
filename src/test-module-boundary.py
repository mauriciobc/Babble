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
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
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
    return (name in NEW
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
                if node.module == 'catchup':
                    for alias in node.names:
                        imported = alias.name.split('.')[0]
                        found.append((imported if imported in CATCHUP
                                      else 'catchup', node.lineno))
                elif node.module and node.module.startswith('catchup.'):
                    found.append((node.module.split('.')[1], node.lineno))
                elif node.module:
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


def module_name(path):
    """The dotted name of a source under `src/` (`catchup.page`, `pages`,
    `api` for `src/api/__init__.py`), or None for one outside it."""
    if not path.startswith('src/'):
        return None
    parts = path[len('src/'):-len('.py')].split('/')
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def _is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == 'TYPE_CHECKING'
            or isinstance(test, ast.Attribute)
            and test.attr == 'TYPE_CHECKING')


def load_time_imports(tree):
    """The import statements that run when the module loads: everything
    outside a function body (a class body runs at load, a def does not),
    and outside an `if TYPE_CHECKING:` block."""
    found = []

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append(node)
            return
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for item in node.orelse:
                visit(item)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(tree)
    return found


def import_targets(node, package, modules):
    """The modules of *modules* an import statement executes, for an
    importer whose package is *package* (`''` at the top of `src`).

    `from X import name` runs X, and X.name too when that is a module.
    Every enclosing package of a target runs first, so each is a target
    as well, except the importer's own packages: those are already
    loading before the importer can run, whatever it imports."""
    if isinstance(node, ast.Import):
        bases = [(alias.name[len('src.'):], ()) for alias in node.names
                 if alias.name.startswith('src.')]
    elif node.level:
        parts = package.split('.') if package else []
        if node.level - 1 > len(parts):
            return set()
        base = '.'.join(parts[:len(parts) - (node.level - 1)])
        if node.module:
            base = base + '.' + node.module if base else node.module
        bases = [(base, [alias.name for alias in node.names])]
    elif node.module == 'src' or (node.module or '').startswith('src.'):
        bases = [(node.module[len('src.'):] if node.module != 'src' else '',
                  [alias.name for alias in node.names])]
    else:
        return set()
    own = set()
    if package:
        parts = package.split('.')
        own = {'.'.join(parts[:i]) for i in range(1, len(parts) + 1)}
    targets = set()
    for base, names in bases:
        named = [base] + [base + '.' + name if base else name
                          for name in names]
        for module in named:
            if module not in modules:
                continue
            parts = module.split('.')
            for i in range(1, len(parts) + 1):
                prefix = '.'.join(parts[:i])
                if prefix in modules and prefix not in own:
                    targets.add(prefix)
    return targets


def load_time_cycles(sources):
    """R9: every cycle among the `src/` modules through imports that run
    at load time, one line per strongly connected group.

    A cycle there either fails at import (a name read from a module that
    is still half loaded) or depends on the order the first import comes
    from. The plugin and link modules (`pages`, `pluginlinks`,
    `pluginhost`, `pluginmenu`, `pluginpanel`, `webcolumn`) call each
    other in a loop on purpose and keep it safe only by importing lazily,
    inside functions; this rule is what keeps one of those imports from
    moving to the top of its file."""
    names = {}
    for path in sources:
        name = module_name(path)
        if name:
            names[path] = name
    modules = set(names.values())
    edges = {}
    for path in sorted(names):
        importer = names[path]
        package = (importer if path.endswith('/__init__.py')
                   else importer.rpartition('.')[0])
        try:
            tree = ast.parse(sources[path], filename=path)
        except SyntaxError:
            continue  # reported as unparsable by violations()
        for node in load_time_imports(tree):
            for target in import_targets(node, package, modules):
                if target != importer:
                    edges.setdefault(importer, {}).setdefault(
                        target, '%s:%d' % (path, node.lineno))

    # Tarjan's strongly connected components; the graph is a few hundred
    # modules, so recursion depth is not a concern.
    index, low, stack, on_stack, groups = {}, {}, [], set(), []

    def connect(module):
        index[module] = low[module] = len(index)
        stack.append(module)
        on_stack.add(module)
        for target in sorted(edges.get(module, ())):
            if target not in index:
                connect(target)
                low[module] = min(low[module], low[target])
            elif target in on_stack:
                low[module] = min(low[module], index[target])
        if low[module] == index[module]:
            group = set()
            while True:
                member = stack.pop()
                on_stack.discard(member)
                group.add(member)
                if member == module:
                    break
            if len(group) > 1:
                groups.append(group)

    for module in sorted(edges):
        if module not in index:
            connect(module)

    found = []
    for group in sorted(groups, key=min):
        # The shortest cycle through the group's first module, by BFS
        # inside the group, so the report names a path one can follow.
        start = min(group)
        previous = {start: None}
        queue = [start]
        end = None
        while queue and end is None:
            module = queue.pop(0)
            for target in sorted(edges.get(module, ())):
                if target == start:
                    end = module
                    break
                if target in group and target not in previous:
                    previous[target] = module
                    queue.append(target)
        path = [end]
        while previous[path[-1]] is not None:
            path.append(previous[path[-1]])
        path.reverse()
        steps = ['%s (%s)' % (module, edges[module][following])
                 for module, following
                 in zip(path, path[1:] + [start], strict=True)]
        found.append('R9: load-time import cycle: %s -> %s'
                     % (' -> '.join(steps), start))
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
        if path.startswith('src/api/'):
            # R8: the api package is pure Python on the standard library
            # and its own core. No GObject, no gettext, no widgets, no
            # client: it builds a request and hands it to the client the
            # caller passes in, so it can be tested without a display.
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split('.')[0]
                        if top not in sys.stdlib_module_names:
                            found.append('R8: %s:%d imports %s: src/api'
                                         ' may use only the standard'
                                         ' library and core'
                                         % (path, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    names = {alias.name for alias in node.names}
                    if node.level == 0:
                        top = (node.module or '').split('.')[0]
                        if top not in sys.stdlib_module_names:
                            found.append('R8: %s:%d imports %s: src/api'
                                         ' may use only the standard'
                                         ' library and core'
                                         % (path, node.lineno, node.module))
                    elif not (node.level == 1
                              and (node.module == 'core'
                                   or (node.module is None
                                       and names == {'core'})
                                   or (path == 'src/api/timelines.py'
                                       and (node.module == 'lists'
                                            or (node.module is None
                                                and names <= {'core',
                                                              'lists'}))))):
                        # The one sibling import: the list timeline is
                        # declared once, in lists.TIMELINE, and
                        # timelines.list_path() serves it from there.
                        found.append('R8: %s:%d imports %s%s: src/api may'
                                     ' use only the standard library and'
                                     ' core'
                                     % (path, node.lineno,
                                        '.' * node.level, node.module or ''))
        if path == 'src/composetransition.py':
            # R10: composer transitions are pure data on the standard
            # library. No gi, no gettext, no sibling app modules: builders
            # and scheduled_content stay display-free.
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split('.')[0]
                        if top not in sys.stdlib_module_names:
                            found.append('R10: %s:%d imports %s: '
                                         'composetransition may use only '
                                         'the standard library'
                                         % (path, node.lineno, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level != 0:
                        found.append('R10: %s:%d has a relative import: '
                                     'composetransition may use only the '
                                     'standard library'
                                     % (path, node.lineno))
                        continue
                    top = (node.module or '').split('.')[0]
                    if top not in sys.stdlib_module_names:
                        found.append('R10: %s:%d imports %s: '
                                     'composetransition may use only the '
                                     'standard library'
                                     % (path, node.lineno, node.module))
    found.extend(load_time_cycles(sources))
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
self_check('R1 rejects affinity importing a Catch Up module',
           {'src/affinity.py': 'from .catchup import digest\n'},
           'R1: src/affinity.py:1 imports digest')
self_check('R1 rejects navigation importing a Catch Up submodule',
           {'src/navigation.py': 'from .catchup.store import X\n'},
           'R1: src/navigation.py:1 imports store')
self_check('R8 rejects src/api importing GObject',
           {'src/api/statuses.py': 'import gi\n'},
           'R8: src/api/statuses.py:1 imports gi')
self_check('R8 rejects src/api importing the translation helper',
           {'src/api/statuses.py': 'from ..i18n import gettext\n'},
           'R8: src/api/statuses.py:1 imports ..i18n')
self_check('R8 rejects src/api importing the client',
           {'src/api/accounts.py': 'from .mastodon import MastodonClient\n'},
           'R8: src/api/accounts.py:1 imports .mastodon')
self_check('R8 rejects one resource module importing another',
           {'src/api/accounts.py': 'from . import statuses\n'},
           'R8: src/api/accounts.py:1 imports .')
self_check('R9 catches two modules importing each other at load time',
           {'src/alpha.py': 'from . import beta\n',
            'src/beta.py': 'import os\nfrom .alpha import thing\n'},
           'R9: load-time import cycle: alpha (src/alpha.py:1) -> '
           'beta (src/beta.py:2) -> alpha')
self_check('R9 follows a cycle through a subpackage and a class body',
           {'src/catchup/__init__.py': '',
            'src/catchup/tiles.py': 'from .. import gamma\n',
            'src/gamma.py': 'class C:\n    from .catchup import tiles\n'},
           'R9: load-time import cycle: catchup.tiles (src/catchup/'
           'tiles.py:1) -> gamma (src/gamma.py:2) -> catchup.tiles')
self_check('R9 catches a cycle through a package __init__',
           {'src/catchup/__init__.py': 'from .. import gamma\n',
            'src/catchup/tiles.py': '',
            'src/gamma.py': 'from .catchup.tiles import X\n'},
           'R9: load-time import cycle: catchup (src/catchup/__init__.py:1)'
           ' -> gamma (src/gamma.py:1) -> catchup')
check('R9 accepts a cycle closed by an import inside a function',
      not violations({'src/alpha.py': 'from . import beta\n',
                      'src/beta.py': 'def f():\n    from . import alpha\n'}))
check('R9 accepts a cycle closed under TYPE_CHECKING',
      not violations({'src/alpha.py': 'from . import beta\n',
                      'src/beta.py': 'from typing import TYPE_CHECKING\n'
                                     'if TYPE_CHECKING:\n'
                                     '    from . import alpha\n'}))
check('R9 accepts a submodule importing its own package',
      not violations({'src/catchup/__init__.py': 'from . import tiles\n',
                      'src/catchup/tiles.py': 'from . import rank\n',
                      'src/catchup/rank.py': ''}))
self_check('R8 keeps the timelines exception to lists alone',
           {'src/api/timelines.py': 'from . import core, statuses\n'},
           'R8: src/api/timelines.py:1 imports .')
self_check('R8 keeps the lists exception to timelines alone',
           {'src/api/accounts.py': 'from . import lists\n'},
           'R8: src/api/accounts.py:1 imports .')
check('R8 accepts timelines importing lists',
      not [line for line in violations({
          'src/api/timelines.py': 'from . import core, lists\n'
                                  'from .lists import TIMELINE\n'})
          if line.startswith('R8')])
check('R8 accepts the standard library and core',
      not [line for line in violations({
          'src/api/accounts.py':
          'import dataclasses\nfrom typing import Optional\n'
          'from . import core\nfrom .core import send\n'})
          if line.startswith('R8')])
self_check('R10 rejects composetransition importing gi',
           {'src/composetransition.py': 'import gi\n'},
           'R10: src/composetransition.py:1 imports gi')
self_check('R10 rejects composetransition relative imports',
           {'src/composetransition.py': 'from . import entities\n'},
           'R10: src/composetransition.py:1 has a relative import')
check('R10 accepts composetransition on the standard library',
      not [line for line in violations({
          'src/composetransition.py':
          'import dataclasses\nfrom typing import Optional\n'})
          if line.startswith('R10')])
check('the checker accepts navigation importing the catchup package',
      not violations({'src/navigation.py':
                      'from .catchup import CatchUpPage\n'}))
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
    ('R8', 'src/api imports only the standard library and core'),
    ('R9', 'no import cycle among src modules at load time'),
    ('R10', 'composetransition imports only the standard library'),
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
