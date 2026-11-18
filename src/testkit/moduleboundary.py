# SPDX-License-Identifier: GPL-3.0-or-later

"""The import rules: source scanning, the cycle detector and the pages rule.

Every rule the `module boundary` test enforces lives here: which modules
may import which, the load-time cycle detector, the page-toolkit rule and
the rule that nothing uses a name `pages.py` does not define. The test
keeps the tree it judges, its self-check cases and its report. Test-only;
never installed.
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
    from. Link routing no longer closes a loop through the plugin and
    web-column modules: they register with `linkhandlers` at import, and
    R10 keeps the page toolkit from importing them at all. What remains
    are the feature modules (`pagenav`, `linkrouting`, the plugin panel)
    that reach each other through imports inside functions; this rule is
    what keeps one of those imports from moving to the top of its file."""
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

# R10: the page toolkit and the link registry sit below the plugin and
# web-column modules and may not import them anywhere, function bodies
# included; those modules register through `linkhandlers` instead.
TOOLKIT = frozenset({'pages', 'pagenav', 'feedback', 'linkrouting',
                     'appsettings', 'weakconnect', 'linkhandlers',
                     'uihelpers', 'accountrows'})

SIDECAR = frozenset({'pluginlinks', 'pluginhost', 'pluginmenu',
                     'pluginpanel', 'webcolumn'})

def defined_names(tree):
    """The names a module defines at its top level: functions, classes
    and assignments, also inside a top-level `if`, `try`, `for`, `while`,
    `with` or `match` (a function body is not top level). An imported
    name is not a definition."""
    names = set()

    def walrus(node):
        """Targets of `:=` in *node*, not inside a function, lambda or
        class (a comprehension binds in the enclosing scope, so it
        counts)."""
        stack = [node]
        while stack:
            sub = stack.pop()
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(sub, ast.NamedExpr) \
                    and isinstance(sub.target, ast.Name):
                names.add(sub.target.id)
            stack.extend(ast.iter_child_nodes(sub))

    def visit(body):
        for node in body:
            walrus(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for sub in ast.walk(target):
                        if isinstance(sub, ast.Name):
                            names.add(sub.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) \
                    and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.If, ast.While)):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                bind(node.target)
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        bind(item.optional_vars)
                visit(node.body)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    for sub in ast.walk(case.pattern):
                        if isinstance(sub, (ast.MatchAs, ast.MatchStar)) \
                                and sub.name:
                            names.add(sub.name)
                        elif isinstance(sub, ast.MatchMapping) and sub.rest:
                            names.add(sub.rest)
                    visit(case.body)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                visit(node.body)
                for handler in node.handlers:
                    visit(handler.body)
                visit(node.orelse)
                visit(node.finalbody)

    def bind(target):
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
    visit(tree.body)
    return names

def is_dunder(name):
    return name.startswith('__') and name.endswith('__')

def pages_name_violations(sources):
    """R11: every `pages.<name>` (read, assignment or deletion), every
    `getattr`/`setattr` of a constant name on it and every
    `from .pages import <name>` must name something `src/pages.py`
    defines. A name that moved to another module and is still used
    through `pages` would fail at run time, or, worse, a test's
    `pages.x = fake` would patch nothing and pass."""
    if 'src/pages.py' not in sources:
        return []
    try:
        defined = defined_names(ast.parse(sources['src/pages.py']))
    except SyntaxError:
        return []
    found = []
    for path in sorted(sources):
        if path == 'src/pages.py':
            continue
        try:
            tree = ast.parse(sources[path], filename=path)
        except SyntaxError:
            continue
        aliases, src_names = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) \
                    and (node.module in (None, 'src')):
                for alias in node.names:
                    if alias.name == 'pages':
                        aliases.add(alias.asname or 'pages')
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'src.pages' and alias.asname:
                        aliases.add(alias.asname)
                    elif alias.name in ('src', 'src.pages') \
                            or alias.name.startswith('src.'):
                        # `import src`, `import src.pages`, `import src as s`
                        src_names.add(alias.asname or 'src')

        def is_pages(value, aliases=aliases, src_names=src_names):
            return ((isinstance(value, ast.Name) and value.id in aliases)
                    or (isinstance(value, ast.Attribute)
                        and value.attr == 'pages'
                        and isinstance(value.value, ast.Name)
                        and value.value.id in src_names))

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and is_pages(node.value) \
                    and node.attr not in defined \
                    and not is_dunder(node.attr):
                found.append('R11: %s:%d pages.%s is not defined by '
                             'src/pages.py' % (path, node.lineno, node.attr))
            elif (isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)
                  and node.func.id in ('getattr', 'setattr', 'delattr',
                                       'hasattr')
                  and len(node.args) >= 2 and is_pages(node.args[0])
                  and isinstance(node.args[1], ast.Constant)
                  and isinstance(node.args[1].value, str)
                  and node.args[1].value not in defined
                  and not is_dunder(node.args[1].value)):
                found.append('R11: %s:%d pages.%s is not defined by '
                             'src/pages.py'
                             % (path, node.lineno, node.args[1].value))
            elif isinstance(node, ast.ImportFrom) \
                    and ((node.level and node.module == 'pages')
                         or node.module == 'src.pages'):
                for alias in node.names:
                    if alias.name == '*' or alias.name not in defined:
                        found.append('R11: %s:%d imports %s from pages, '
                                     'which does not define it'
                                     % (path, node.lineno, alias.name))
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
        if importer in TOOLKIT and path == 'src/%s.py' % importer:
            for module, line in source_imports(tree):
                if module in SIDECAR:
                    found.append('R10: %s:%d imports %s: the registry in '
                                 'linkhandlers replaces it'
                                 % (path, line, module))
        if importer in TOOLKIT and path == 'src/%s.py' % importer:
            # An import by name (`importlib.import_module(...)`,
            # `__import__(...)`) is an import too, whatever it names.
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                        (isinstance(node.func, ast.Name)
                         and node.func.id == '__import__')
                        or (isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'import_module')
                        or (isinstance(node.func, ast.Name)
                            and node.func.id == 'import_module')):
                    found.append('R10: %s:%d imports a module by name: '
                                 'the toolkit may not hide an import'
                                 % (path, node.lineno))
        if path == 'src/linkhandlers.py':
            for name, line, relative in all_imports(tree):
                if relative or name.split('.')[0] not in \
                        sys.stdlib_module_names:
                    found.append('R10: %s:%d imports %s: linkhandlers may '
                                 'use only the standard library'
                                 % (path, line, name or '.'))
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
        if (path.startswith('src/') and not stem(path).startswith('test-')
                and not path.startswith('src/testkit/')):
            # R12: an installed module reaches its siblings relatively.
            # The package is installed as `babble`, so `from src.x import`
            # works only where the tests put the source tree on the path:
            # the suite passes and the installed app does not start.
            for name, line, relative in all_imports(tree):
                if not relative and name.split('.')[0] == 'src':
                    found.append('R12: %s:%d imports %s: an installed'
                                 ' module imports its siblings relatively'
                                 % (path, line, name))
    found.extend(load_time_cycles(sources))
    found.extend(pages_name_violations(sources))
    return found

failures = 0

def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)
