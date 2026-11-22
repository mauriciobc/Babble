# SPDX-License-Identifier: GPL-3.0-or-later

"""The API-boundary scan: mechanisms, the AST visitor and the comparators.

Every rule the `api boundary` test enforces lives here: which call shapes
count as naming a path, which files are the boundary itself, the visitor
that finds the sites, the derived-name resolution, and the two scans for
client and decode calls. The test keeps the allow-list itself, its
self-check and its report. Test-only; never installed.
"""

import ast
import collections
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.api import accounts as _accounts  # noqa: E402
from src.api import announcements as _announcements  # noqa: E402
from src.api import filters as _filters  # noqa: E402
from src.api import lists as _lists  # noqa: E402
from src.api import relationships as _relationships  # noqa: E402
from src.api import scheduled as _scheduled  # noqa: E402
from src.api import search as _search  # noqa: E402
from src.api import statuses as _statuses  # noqa: E402
from src.api import tags as _tags  # noqa: E402
from src.api import markers as _markers  # noqa: E402
from src.api import notifications as _notifications  # noqa: E402
from src.api import timelines as _timelines  # noqa: E402
from src.api import trends as _trends  # noqa: E402
from src.api import annual_reports as _annual_reports  # noqa: E402
from src.api import conversations as _conversations  # noqa: E402
from src.api import instance as _instance  # noqa: E402
from src.api import suggestions as _suggestions  # noqa: E402
from src.api import admin as _admin  # noqa: E402
from src.api import collections_api as _collections_api  # noqa: E402
from src.api import polls as _polls  # noqa: E402
from src.api import reports as _reports  # noqa: E402
from src.api import media as _media  # noqa: E402

API_MODULES = {'accounts': _accounts, 'announcements': _announcements,
               'filters': _filters, 'lists': _lists,
               'relationships': _relationships, 'scheduled': _scheduled,
               'markers': _markers, 'notifications': _notifications,
               'search': _search, 'statuses': _statuses, 'tags': _tags,
               'timelines': _timelines, 'trends': _trends,
               'annual_reports': _annual_reports,
               'conversations': _conversations, 'instance': _instance,
               'suggestions': _suggestions, 'admin': _admin,
               'collections_api': _collections_api, 'polls': _polls,
               'reports': _reports, 'media': _media}

ACCESSORS = ('list_path', 'search_path', 'familiar_followers_path')

EXEMPT_FILES = frozenset({'src/mastodon.py'})

EXEMPT_PREFIXES = ('src/api/',)

MECHANISMS = {
    'request_async': ('raw', 1, 'path', None),
    'send_path': ('raw', 2, 'path', None),
    'multipart_async': ('helper', 1, 'path', None),
    'mutate_row': ('helper', 2, 'path', None),
    'PagedList': ('helper', 1, 'fetch_path', None),
    'PagedList.__init__': ('helper', 2, 'fetch_path', None),
    'super().__init__': ('helper', 1, 'fetch_path', None),
    'AccountListPage': ('helper', 1, 'path', None),
    'ApiTimelineSource': ('helper', 1, 'path', None),
    'send_relationship': ('helper', 0, 'operation',
                          {'src/profile.py', 'src/profileactions.py'}),
}

SEND_PATH_FILES = frozenset({'src/completer.py', 'src/pages.py',
                             'src/timelinepage.py', 'src/timelinesource.py'})

HOOK_NAME = 'search_request'

ALIASED = frozenset({'request_async'})

PATH_BASES = frozenset({'PagedList', 'AccountListPage'})

VERB_MECHANISMS = frozenset({'send_relationship'})

def mechanism_of(call, rel, call_names, pages_names, derived, in_derived):
    """The MECHANISMS key a Call node is, or None.

    *call_names* are the local names `pages.call` is imported as,
    *pages_names* the local names of the `pages` module, *derived* the
    names of PagedList subclasses whose constructor forwards ``*args``,
    and *in_derived* tells that the call sits in a PagedList subclass."""
    func = call.func
    if isinstance(func, ast.Attribute):
        name, base = func.attr, func.value
        if name == '__init__':
            if isinstance(base, ast.Call) \
                    and isinstance(base.func, ast.Name) \
                    and base.func.id == 'super':
                return 'super().__init__' if in_derived else None
            if (isinstance(base, ast.Name) and base.id in PATH_BASES) or (
                    isinstance(base, ast.Attribute)
                    and base.attr in PATH_BASES):
                return 'PagedList.__init__'
            return None
    elif isinstance(func, ast.Name):
        name = func.id
        if name in derived:
            return 'PagedList'
    else:
        return None
    if name in derived:
        return 'PagedList'
    if name in MECHANISMS and name not in (
            'super().__init__', 'PagedList.__init__'):
        files = MECHANISMS[name][3]
        if files is None or rel in files:
            return name
    return None

def path_node(call, mechanism):
    """The path argument node of *call*, or None if absent."""
    _layer, index, keyword, _files = MECHANISMS[mechanism]
    if len(call.args) > index:
        return call.args[index]
    node = None
    for kw in call.keywords:
        if kw.arg in (keyword, 'path'):
            node = kw.value
    return node

def is_variable_path(node):
    """True for a bare name or attribute chain (`path`, `self.fetch_path`)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)

def path_text(call, mechanism):
    """The path argument of *call* as stable text, or None if absent."""
    node = path_node(call, mechanism)
    if node is None:
        return None
    return ' '.join(ast.unparse(node).split())

def is_registered(node, mechanism, api_names):
    """True when the path (or verb) argument *node* is a registered accessor.

    * a verb mechanism (`send_relationship`) given a string literal that is
      a key of `api.relationships.ACTIONS`; an unknown literal or a
      variable is still a site;
    * a helper-layer path that is a call of `list_path(...)`,
      `search_path()` or `familiar_followers_path()` on a name bound to a
      `src.api` module import. A string literal first argument must be a
      kind of that module's `LISTS`; a variable kind is accepted (the
      accessor raises KeyError at run time on a typo)."""
    if node is None:
        return False
    if mechanism in VERB_MECHANISMS:
        return isinstance(node, ast.Constant) \
            and node.value in _relationships.ACTIONS
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in api_names
            and node.func.attr in ACCESSORS):
        return False
    module = API_MODULES[api_names[node.func.value.id]]
    if not hasattr(module, node.func.attr):
        return False
    if node.func.attr == 'list_path' and node.args \
            and isinstance(node.args[0], ast.Constant):
        return node.args[0].value in getattr(module, 'LISTS', {})
    return True

def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None

def path_classes(tree, known=frozenset()):
    """(subclass names, forwarding ones) of PagedList / AccountListPage.

    Resolved through the class bases in this tree and the *known* names
    from other files, to a fixed point, so a subclass of a subclass counts.
    A *forwarding* class has an ``__init__(self, *args, ...)`` or none at
    all: building it names the path at the call site."""
    classes = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            init = next((n for n in node.body
                         if isinstance(n, ast.FunctionDef)
                         and n.name == '__init__'), None)
            classes[node.name] = (
                [_base_name(b) for b in node.bases],
                init is None or init.args.vararg is not None)
    derived = set()
    changed = True
    while changed:
        changed = False
        for name, (bases, _fwd) in classes.items():
            if name not in derived and any(
                    b in PATH_BASES or b in derived or b in known
                    for b in bases):
                derived.add(name)
                changed = True
    forwarding = {n for n in derived if classes[n][1]}
    return derived, forwarding

class Visitor(ast.NodeVisitor):
    def __init__(self, rel, call_names, pages_names, derived, forwarding,
                 api_names=None):
        self.rel = rel
        self.api_names = api_names or {}
        self.call_names = call_names
        self.pages_names = pages_names
        self.derived = derived
        self.forwarding = forwarding
        self.scope = []
        self.classes = []
        self.params = []
        self.called = set()
        self.found = []

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.params.append({a.arg for a in node.args.args
                            + node.args.kwonlyargs})
        self.generic_visit(node)
        self.params.pop()
        self.scope.pop()

    def is_pass_through(self, mechanism, node):
        """A generic base class handing its own `path` parameter to its base
        (`AccountListPage.__init__`). Its callers name the path and are
        checked there."""
        return (mechanism in ('super().__init__', 'PagedList.__init__')
                and bool(self.classes) and self.classes[-1] in PATH_BASES
                and bool(self.params) and isinstance(node, ast.Name)
                and node.id in self.params[-1])

    visit_AsyncFunctionDef = visit_FunctionDef

    def qualname(self):
        return '.'.join(self.scope) or '<module>'

    def add(self, mechanism, text, layer, line):
        self.found.append((self.rel, self.qualname(), mechanism, text,
                           layer, line))

    def visit_Call(self, node):
        self.called.add(id(node.func))
        in_derived = bool(self.classes) and self.classes[-1] in self.derived
        mechanism = mechanism_of(node, self.rel, self.call_names,
                                 self.pages_names, self.forwarding,
                                 in_derived)
        if mechanism is not None:
            text = path_text(node, mechanism)
            registered = MECHANISMS[mechanism][0] == 'helper' \
                and is_registered(path_node(node, mechanism), mechanism,
                                  self.api_names)
            if mechanism == 'send_path':
                registered = self.rel in SEND_PATH_FILES \
                    and is_variable_path(path_node(node, mechanism))
            elif self.is_pass_through(mechanism, path_node(node, mechanism)):
                registered = True
            if text is not None and not registered:
                self.add(mechanism, text, MECHANISMS[mechanism][0],
                         node.lineno)
        elif isinstance(node.func, ast.Name) and node.func.id == 'getattr' \
                and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value in ALIASED:
            # getattr(client, 'request_async') hides the call from the scan.
            self.add('alias:' + node.args[1].value, 'getattr', 'raw',
                     node.lineno)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # `send = client.request_async`: the method leaves the call site.
        if id(node) not in self.called and node.attr in ALIASED:
            self.add('alias:' + node.attr, ast.unparse(node), 'raw',
                     node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node):
        # A completer hook answers (path, params): the path is named here.
        if (self.scope and self.scope[-1] == HOOK_NAME
                and isinstance(node.value, ast.Tuple) and node.value.elts):
            text = ' '.join(ast.unparse(node.value.elts[0]).split())
            if not is_registered(node.value.elts[0], HOOK_NAME,
                                 self.api_names):
                self.add('hook:' + HOOK_NAME, text, 'helper', node.lineno)
        self.generic_visit(node)

def is_exempt(rel):
    return rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES) \
        or os.path.basename(rel).startswith('test-')

def import_names(tree):
    """(local names of pages.call, local names of the pages module)."""
    call_names, pages_names = set(), {'pages'}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            module = (n.module or '').split('.')[-1]
            for a in n.names:
                if module == 'pages' and a.name == 'call':
                    call_names.add(a.asname or a.name)
                elif a.name == 'pages':
                    pages_names.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split('.')[-1] == 'pages':
                    pages_names.add(a.asname or 'pages')
    return call_names, pages_names

def api_import_names(tree):
    """{local name: module} of the `src.api` modules a tree imports.

    Only `from .api import accounts as api_accounts` (any dots) counts: a
    name bound any other way is not a registered accessor."""
    names = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or '') in (
                'api', 'src.api'):
            for a in n.names:
                if a.name in API_MODULES:
                    names[a.asname or a.name] = a.name
    return names

def scan_source(rel, source, known=frozenset()):
    """[(file, function, mechanism, path text, layer, line)] of one source.

    *known* are PagedList-subclass names defined in other files."""
    if is_exempt(rel):
        return []
    tree = ast.parse(source, rel)
    call_names, pages_names = import_names(tree)
    derived, forwarding = path_classes(tree, known)
    visitor = Visitor(rel, call_names, pages_names, derived, forwarding,
                      api_import_names(tree))
    visitor.visit(tree)
    return visitor.found

def derived_names(sources):
    """Names of PagedList subclasses across all (rel, source) pairs."""
    trees = [ast.parse(src, rel) for rel, src in sources]
    known = set()
    while True:
        before = len(known)
        for tree in trees:
            known |= path_classes(tree, known)[0]
        if len(known) == before:
            return frozenset(known)

def source_files():
    """Relative paths of the files the rule covers, with their text."""
    out = []
    for sub in ('', 'catchup'):
        folder = os.path.join(srcdir, sub)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith('.py'):
                rel = 'src/' + (sub + '/' if sub else '') + name
                if not is_exempt(rel):
                    with open(os.path.join(folder, name),
                              encoding='utf-8') as handle:
                        out.append((rel, handle.read()))
    return out

def compare(found, entries):
    """(new sites, stale entries) of found sites against allow-list entries.

    Both are lists of human-readable lines. Keys are multisets, so the same
    path used twice in one function needs ``count: 2``."""
    have = collections.Counter()
    first_line = {}
    for rel, func, mech, text, _layer, line in found:
        key = (rel, func, mech, text)
        have[key] += 1
        first_line.setdefault(key, line)
    allowed = collections.Counter()
    for entry in entries:
        key = (entry['file'], entry['function'], entry['via'], entry['path'])
        allowed[key] += entry.get('count', 1)
    new, stale = [], []
    for key in sorted(have):
        extra = have[key] - allowed.get(key, 0)
        if extra > 0:
            new.append('%s:%d %s %s(%s) x%d' % (
                key[0], first_line[key], key[1], key[2], key[3], extra))
    for key in sorted(allowed):
        missing = allowed[key] - have.get(key, 0)
        if missing > 0:
            stale.append('%s %s %s(%s) x%d' % (
                key[0], key[1], key[2], key[3], missing))
    return new, stale

def prune_entries(found, entries):
    """(kept entries, removed lines): drop stale allow-list entries only.

    An entry with no matching call goes; an entry whose ``count`` exceeds the
    calls found is lowered to what was found. Nothing is ever added, so a new
    call stays a failure. The order of the kept entries is unchanged."""
    have = collections.Counter()
    for rel, func, mech, text, _layer, _line in found:
        have[(rel, func, mech, text)] += 1
    kept, removed = [], []
    for entry in entries:
        key = (entry['file'], entry['function'], entry['via'],
               entry['path'])
        want = entry.get('count', 1)
        take = min(want, have[key])
        have[key] -= take
        if take == want:
            kept.append(entry)
            continue
        if take:
            lowered = dict(entry)
            if take == 1:
                del lowered['count']
            else:
                lowered['count'] = take
            kept.append(lowered)
        removed.append('%s %s %s(%s) x%d' % (
            key[0], key[1], key[2], key[3], want - take))
    return kept, removed

failures = 0

def check(condition, label):
    global failures
    if condition:
        print('ok - ' + label)
    else:
        failures += 1
        print('FAIL - ' + label)

def _class_body_names(node):
    names = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(item.name)
        elif isinstance(item, ast.Assign):
            names.update(t.id for t in item.targets
                         if isinstance(t, ast.Name))
    return names


CLIENT_NAMES = ('client', '_client')

NOT_A_CLIENT = {}

def client_members(source, sources=()):
    """Names a caller may reach on MastodonClient.

    The transport is one object across several modules, so a member is
    either in ``MastodonClient``'s own body or in the body of one of the
    mixins its class statement names. ``sources`` is the scanned tree, in
    which those mixins live."""
    tree = ast.parse(source)
    names = set()
    bases = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MastodonClient':
            bases = {base.id for base in node.bases
                     if isinstance(base, ast.Name)}
            names |= _class_body_names(node)
    for _rel, text in sources:
        try:
            other = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in other.body:
            if isinstance(node, ast.ClassDef) and node.name in bases:
                names |= _class_body_names(node)
    return names

def client_method_calls(rel, source, members):
    """Lines calling `<client>.NAME_async/_finish` for a NAME the client no
    longer has: a leftover call to a wrapper that moved into `src/api/`."""
    out = []
    for node in ast.walk(ast.parse(source, rel)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.endswith(('_async', '_finish'))):
            continue
        receiver = node.func.value
        last = receiver.attr if isinstance(receiver, ast.Attribute) else (
            receiver.id if isinstance(receiver, ast.Name) else None)
        if last in CLIENT_NAMES and node.func.attr not in members \
                and NOT_A_CLIENT.get(rel) != node.func.attr:
            out.append('%s:%d %s.%s' % (rel, node.lineno,
                                        ast.unparse(receiver),
                                        node.func.attr))
    return out

def check_client_calls():
    folders = [srcdir, os.path.join(srcdir, 'catchup'),
               os.path.join(os.path.dirname(srcdir), 'bench')]
    sources = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(folder, name), encoding='utf-8') as h:
                sources.append((name, h.read()))
    with open(os.path.join(srcdir, 'mastodon.py'), encoding='utf-8') as h:
        members = client_members(h.read(), sources)
    check(len(members) > 20 and 'request_async' in members
          and 'favourite_async' not in members,
          'client calls: MastodonClient has %d members and no '
          'favourite_async' % len(members))
    planted = "def f(self):\n    self.client.favourite_async('1')\n"
    check(client_method_calls('src/x.py', planted, members)
          == ['src/x.py:2 self.client.favourite_async'],
          'self-check: a call to a removed client wrapper is found')
    check(client_method_calls(
        'src/x.py', "def f(client):\n    client.request_async('GET', '/')\n"
        "    api.favourite_async(client, '1')\n", members) == [],
          'self-check: a real client method and an api function are fine')
    scanned = 0
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            rel = 'src/' + name if folder == srcdir else (
                os.path.relpath(os.path.join(folder, name),
                                os.path.dirname(srcdir)))
            if not name.endswith('.py') or name == 'mastodon.py':
                continue
            scanned += 1
            with open(os.path.join(folder, name), encoding='utf-8') as h:
                found = client_method_calls(rel, h.read(), members)
            for line in found:
                print('FAIL - call to a name MastodonClient does not have '
                      '(moved to src/api/?): ' + line)
            check(not found, 'client calls: %s' % rel)
    check(scanned > 50, 'client calls: scanned %d files' % scanned)

DECODE_NAMES = ('request_finish', 'request_finish_paged')

DECODE_EXCEPTIONS = {}

def decode_calls(rel, source):
    """Lines naming `<x>.request_finish[_paged]` (called, or taken as a
    value such as `finish=client.request_finish`) or reaching it through
    `getattr(x, 'request_finish')`, in a file outside the transport and
    `src/api/`."""
    if rel in DECODE_EXCEPTIONS:
        return []
    out = []
    for node in ast.walk(ast.parse(source, rel)):
        if isinstance(node, ast.Attribute) and node.attr in DECODE_NAMES \
                and isinstance(node.ctx, ast.Load):
            out.append('%s:%d %s' % (rel, node.lineno, ast.unparse(node)))
        elif isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Name) \
                and node.func.id == 'getattr' and len(node.args) > 1 \
                and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value in DECODE_NAMES:
            out.append('%s:%d getattr(..., %r)' % (
                rel, node.lineno, node.args[1].value))
    return out

def check_decode_calls():
    planted = ("def f(client, result):\n"
               "    return client.request_finish(result)\n")
    check(decode_calls('src/x.py', planted)
          == ['src/x.py:2 client.request_finish'],
          'self-check: a request_finish in a UI file fails')
    check(len(decode_calls(
        'src/x.py', "def f(c, r):\n    return c.request_finish_paged(r)\n"
        "g = c.request_finish\nh = getattr(c, 'request_finish')\n")) == 3,
          'self-check: request_finish_paged, a value use and getattr fail')
    check(decode_calls(
        'src/x.py', "def f(client, result):\n"
        "    return core.finish(client, result)\n"
        "def g(client, result):\n"
        "    return core.finish_paged(client, result)\n"
        "def h(client, result):\n"
        "    return statuses.get_finish(client, result)\n") == [],
          'self-check: core.finish and an api finish pass')
    check(decode_calls('src/x.py', "class F:\n"
                       "    def request_finish(self, result):\n"
                       "        return result\n") == [],
          'self-check: defining a request_finish (a test fake) is not a call')
    root = os.path.dirname(srcdir)
    folders = [srcdir, os.path.join(srcdir, 'catchup'),
               os.path.join(root, 'bench')]
    scanned = 0
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            rel = 'src/' + name if folder == srcdir else (
                os.path.relpath(os.path.join(folder, name), root))
            if not name.endswith('.py') or name == 'mastodon.py' \
                    or name.startswith('test-'):
                continue
            scanned += 1
            with open(os.path.join(folder, name), encoding='utf-8') as h:
                found = decode_calls(rel, h.read())
            for line in found:
                print('FAIL - decode with the client directly (use the '
                      'api module\'s <name>_finish, or api.core.finish / '
                      'finish_paged): ' + line)
            check(not found, 'decode calls: %s' % rel)
    check(scanned > 50, 'decode calls: scanned %d files' % scanned)
