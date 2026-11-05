# SPDX-License-Identifier: GPL-3.0-or-later

"""The Mastodon API boundary, as a test so the list of raw calls only shrinks.

`Docs/api-layer-plan.md` moves every Mastodon request behind `src/api/`.
Until that is done, UI code still builds paths and passes them to the
generic client. This test freezes today's set of such places in
`src/api-boundary-allowlist.json` and enforces three rules:

  (a) a place that is not on the list fails: a new raw call;
  (b) a list entry with no matching place fails: the list cannot rot, it
      can only shrink as phases 1-4 move calls into `src/api/`;
  (c) the remaining count is printed, by layer and by phase.

Which layer is keyed (the decision `Docs/mastodon-api-audit.md` asked for).
A site is the call where a Mastodon path is *named*, which is not always
the call that sends it:

  * `raw`: `client.request_async(method, path, ...)`. (The audit's 83 raw
    sites also counted 15 `pages.call`; that helper is gone.) The path
    argument is keyed.
  * `internal`: the inner `request_async` calls of the generic helpers
    (two in `PagedList`; the old `mutate_row`, `wrapped._send`,
    `admin.send`, `pages.call` and `fedicollections._send` are gone) and the site
    `pages.AccountListPage.__init__`, which forwards a variable path to
    `PagedList` and calls no `request_async`. They carry a *variable*
    path, so they are keyed by that variable's name. They stay on the
    list until the helper itself moves into `src/api/` (phase 4).
  * `helper`: a call that hands a path or a verb to a helper that sends
    it later: `mutate_row_api`, the `PagedList` / `AccountListPage` /
    `ApiTimelineSource` constructors, `profileactions.send_relationship`
    (its first argument names the operation, from which the path is
    built), `multipart_async`,
    and the completers' `search_request` hooks (their `return path,
    params`). Without these, a new endpoint could be reached through a
    helper and never show up as a `request_async` with a literal path,
    because the helper's own call carries only a variable.

A `PagedList` / `AccountListPage` subclass hands its path on through
`super().__init__(client, '/path', ...)` (or `PagedList.__init__`); those
calls are `helper` sites keyed by the subclass's `__init__`, found by
resolving the class bases (also across files, and subclasses of
subclasses). Building a subclass that only forwards `*args` names the
path at the call site and is keyed there. Using `request_async` as a value
(`r = c.request_async`) and `getattr(c, 'request_async')` are failures
(`alias:` sites). Known limit: `c2 = c; c2.method` by string
concatenation or `getattr(c, name)` with a variable are not seen.

A literal path, a `%` / f-string template, or a variable all render to a
stable text with `ast.unparse`, and the key is (file, enclosing function,
mechanism, that text). So a variable path cannot hide: the day someone
writes `request_async('GET', path)` in a new function, the key is new.
Two identical keys in one function are one entry with `count` 2.

Out of scope: `src/mastodon.py` and the future `src/api/` package (they
are the boundary), `src/test-*.py`, and `plugins/` (third-party servers
only, never Mastodon, and `plugin boundary` governs them). Known limit:
a path written in a table (statusviews' `_KINDS`, desktopnotify's
`_ACTIONS`) is seen where the table's value reaches a call, as a variable;
adding a row to the table is not seen. The registry test of phase 1
closes that.

Counts, and the three layers in two lines. `raw` is a call that names a
Mastodon path itself (`request_async`); `internal` is a
generic helper's own inner send or path forward (7 `request_async` calls
plus `pages.AccountListPage.__init__`, 8 in all); `helper` hands a path or a
verb to a helper that sends it later. At phase 0: 175 sites, raw 83, internal 8,
helper 84.

Pruning. The list may only shrink, and removing an entry by hand after a
move is the normal way. `BABBLE_BOUNDARY_PRUNE=1` does the mechanical part:
it removes only stale entries (ones with no matching call, or the surplus of
a `count`), writes the file in the same format (indent 1, entry order kept)
and prints every entry removed. It never adds: a new call still fails. Normal
mode never writes the file.

The checker is a function over (relative path, source) so the self-checks
can feed it synthetic sources; each self-check requires an exact result.
"""

import ast
import collections
import json
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

# The resource modules are pure Python (no GI): their tables say which
# verbs and list kinds a literal may name.
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
# The path accessors of `src.api`: a helper handed one of these names no
# path of its own, so the site needs no allow-list entry.
ACCESSORS = ('list_path', 'search_path', 'familiar_followers_path')
ALLOWLIST = os.path.join(srcdir, 'api-boundary-allowlist.json')

# Files that are the boundary itself.
EXEMPT_FILES = frozenset({'src/mastodon.py'})
EXEMPT_PREFIXES = ('src/api/',)

# mechanism -> (layer, positional index of the path, keyword name of it,
#               files it applies to or None for all)
# `send` and `_send` are common local names, so they only count in the
# modules that define the generic helper of that name.
MECHANISMS = {
    'request_async': ('raw', 1, 'path', None),
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
HOOK_NAME = 'search_request'
# Methods whose use as a value (not a call) or through getattr is a failure:
# the scan could not see where the path goes.
ALIASED = frozenset({'request_async'})
# Classes whose constructor takes a path: a subclass hands it on through
# `super().__init__(client, '/path', ...)`, which names the path too.
PATH_BASES = frozenset({'PagedList', 'AccountListPage'})
# Mechanisms whose first argument names a verb of the relationships table.
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
        self.generic_visit(node)
        self.scope.pop()

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


def self_check():
    """Prove each failure mode fails, so the real checks cannot pass idly."""
    a = {'file': 'src/x.py', 'function': 'f', 'via': 'request_async',
         'path': "'/a'", 'layer': 'raw', 'reason': 'phase 1: x'}
    b = dict(a, path="'/b'")
    c = dict(a, path="'/c'", count=3)
    sites = [('src/x.py', 'f', 'request_async', "'/a'", 'raw', 1),
             ('src/x.py', 'f', 'request_async', "'/c'", 'raw', 2),
             ('src/x.py', 'f', 'request_async', "'/c'", 'raw', 3),
             ('src/x.py', 'f', 'request_async', "'/new'", 'raw', 4)]
    kept, removed = prune_entries(sites, [a, b, c])
    check([e['path'] for e in kept] == ["'/a'", "'/c'"]
          and kept[1]['count'] == 2 and len(removed) == 2
          and "'/b'" in removed[0] and "'/c'" in removed[1],
          'self-check: prune drops a stale entry and lowers a count')
    check(all(e['path'] != "'/new'" for e in kept),
          'self-check: prune never adds a new site')
    check(prune_entries(sites[:1], [a]) == ([a], []),
          'self-check: prune leaves a clean list alone')
    listed = {'file': 'src/x.py', 'function': 'Page.load',
              'via': 'request_async', 'path': "'/accounts/1'"}
    source = ("class Page:\n"
              "    def load(self, client):\n"
              "        client.request_async('GET', '/accounts/1')\n")
    found = scan_source('src/x.py', source)
    check(len(found) == 1 and found[0][1] == 'Page.load',
          'self-check: a request_async call is found with its qualname')
    check(compare(found, [listed]) == ([], []),
          'self-check: a listed site passes')

    new, stale = compare(found, [])
    check(len(new) == 1 and not stale,
          'self-check: a new raw call fails')

    new, stale = compare([], [listed])
    check(not new and len(stale) == 1,
          'self-check: a stale list entry fails')

    moved = source.replace('Page.load', 'Page.other').replace(
        'def load', 'def other')
    new, stale = compare(scan_source('src/x.py', moved), [listed])
    check(len(new) == 1 and len(stale) == 1,
          'self-check: the same call in another function is new and stale')

    var = ("def go(client, path):\n"
           "    client.request_async('POST', path)\n")
    found = scan_source('src/y.py', var)
    check(len(found) == 1 and found[0][3] == 'path',
          'self-check: a variable path is keyed by its expression')
    new, _stale = compare(found, [])
    check(len(new) == 1, 'self-check: a variable path cannot hide')
    other = var.replace('(client, path)', '(client, verb, path)').replace(
        "'POST', path", "verb, path")
    other = other.replace("request_async(verb, path)",
                          "request_async('POST', verb)")
    new, _stale = compare(scan_source('src/y.py', other), [
        {'file': 'src/y.py', 'function': 'go', 'via': 'request_async',
         'path': 'path'}])
    check(len(new) == 1,
          'self-check: a different variable in the same function is new')

    two = ("def go(client):\n"
           "    client.request_async('GET', '/a')\n"
           "    client.request_async('GET', '/a')\n")
    entry = {'file': 'src/z.py', 'function': 'go', 'via': 'request_async',
             'path': "'/a'"}
    new, _stale = compare(scan_source('src/z.py', two), [entry])
    check(len(new) == 1,
          'self-check: a second identical call needs count 2')
    entry2 = dict(entry, count=2)
    check(compare(scan_source('src/z.py', two), [entry2]) == ([], []),
          'self-check: count 2 allows two identical calls')

    unrelated = ("def go(bus, call):\n"
                 "    bus.call('name', '/path')\n"
                 "    call()\n")
    check(scan_source('src/u.py', unrelated) == [],
          'self-check: D-Bus bus.call and a local call are not API calls')

    check(scan_source('src/mastodon.py', source) == []
          and scan_source('src/api/statuses.py', source) == []
          and scan_source('src/test-x.py', source) == [],
          'self-check: mastodon.py, src/api/ and tests are exempt')

    via = ("def build(self, client):\n"
           "    return pages.PagedList(client, '/mutes', self.row)\n")
    found = scan_source('src/p.py', via)
    check(len(found) == 1 and found[0][2] == 'PagedList'
          and found[0][4] == 'helper',
          'self-check: a PagedList path is a helper site')

    hook = ("class C:\n"
            "    def search_request(self, query):\n"
            "        return '/accounts/search', {'q': query}\n")
    found = scan_source('src/c.py', hook)
    check(len(found) == 1 and found[0][2] == 'hook:search_request',
          'self-check: a completer hook return is a helper site')

    sub = ("class Muted(pages.AccountListPage):\n"
           "    def __init__(self, client):\n"
           "        super().__init__(client, '/mutes', self.row)\n")
    found = scan_source('src/s.py', sub)
    check(len(found) == 1 and found[0][2] == 'super().__init__'
          and found[0][1] == 'Muted.__init__' and found[0][3] == "'/mutes'",
          'self-check: a PagedList subclass super().__init__ path is found')
    entry = {'file': 'src/s.py', 'function': 'Muted.__init__',
             'via': 'super().__init__', 'path': "'/mutes'"}
    check(compare(found, [entry]) == ([], []),
          'self-check: a listed subclass path passes')
    new, stale = compare(scan_source('src/s.py', sub.replace(
        "'/mutes'", "'/mutesX'")), [entry])
    check(len(new) == 1 and len(stale) == 1,
          'self-check: a changed subclass path is new and stale')
    new, _stale = compare(scan_source('src/s.py', sub), [])
    check(len(new) == 1, 'self-check: a new subclass path fails')
    deep = sub + ("class Deeper(Muted):\n"
                  "    def __init__(self, client):\n"
                  "        super().__init__(client)\n"
                  "class Other(Muted):\n"
                  "    def __init__(self, client):\n"
                  "        PagedList.__init__(self, client, '/blocks', None)\n")
    found = scan_source('src/s.py', deep)
    check(sorted((f[1], f[2]) for f in found) == [
        ('Muted.__init__', 'super().__init__'),
        ('Other.__init__', 'PagedList.__init__')],
          'self-check: subclass of a subclass and PagedList.__init__ count')
    other_file = ("class Third(Muted):\n"
                  "    def __init__(self):\n"
                  "        super().__init__(c, '/x')\n")
    check(len(scan_source('src/t.py', other_file,
                          derived_names([('src/s.py', sub)]))) == 1,
          'self-check: a subclass of an imported subclass counts')
    unrelated = ("class W(Gtk.Box):\n"
                 "    def __init__(self):\n"
                 "        super().__init__(c, '/not-api')\n")
    check(scan_source('src/w.py', unrelated) == [],
          'self-check: super().__init__ of a non-PagedList class is ignored')
    fwd = ("class Tags(pages.PagedList):\n"
           "    def __init__(self, *args, **kwargs):\n"
           "        super().__init__(*args, **kwargs)\n"
           "def build(c):\n"
           "    return Tags(c, '/followed_tags', None)\n")
    found = scan_source('src/f.py', fwd)
    check([f[1] for f in found] == ['build'] and found[0][3]
          == "'/followed_tags'",
          'self-check: building a forwarding subclass names the path')

    aliases = ("from . import pages as pg\n"
               "def go(w, c):\n"
               "    r = c.request_async\n"
               "    getattr(c, 'request_async')\n")
    found = scan_source('src/a.py', aliases)
    check(sorted(f[2] for f in found) == [
        'alias:request_async', 'alias:request_async'],
          'self-check: aliased request_async is caught')

    imports = ("from .api import accounts as api_accounts\n"
               "from .api import relationships as api_rel\n"
               "from .api import statuses as api_statuses\n")

    def scan_api(body, rel='src/profileactions.py'):
        return scan_source(rel, imports + body)

    ok_verb = ("def f(self):\n"
               "    self.send_relationship('follow')\n"
               "    self.send_relationship('remove_from_followers')\n")
    check(scan_api(ok_verb) == [],
          'self-check: a send_relationship literal verb of the table needs '
          'no entry')
    check(len(scan_api("def f(self):\n"
                       "    self.send_relationship('folow')\n")) == 1,
          'self-check: a typo in a send_relationship verb fails')
    check(len(scan_api("def f(self, verb):\n"
                       "    self.send_relationship(verb)\n")) == 1,
          'self-check: a variable send_relationship verb is still a site')
    ok_path = ("def f(self, c, kind):\n"
               "    pages.AccountListPage(c, api_rel.list_path('mutes'))\n"
               "    pages.PagedList(c, api_accounts.list_path(kind, 1), f)\n"
               "    pages.PagedList(c, api_accounts.familiar_followers_path()"
               ", f)\n"
               "    pages.AccountListPage(c, api_statuses.list_path("
               "'boosts', 3))\n")
    check(scan_api(ok_path) == [],
          'self-check: registered list_path accessors need no entry')
    check(len(scan_api("def f(self, c):\n"
                       "    pages.AccountListPage(c, api_rel.list_path("
                       "'mute'))\n")) == 1,
          'self-check: an unknown list_path kind fails')
    check(len(scan_api("def f(self, c):\n"
                       "    pages.AccountListPage(c, '/mutes')\n")) == 1,
          'self-check: a raw literal path is still a site')
    check(len(scan_api("def f(self, c):\n"
                       "    pages.AccountListPage(c, other.list_path("
                       "'mutes'))\n")) == 1,
          'self-check: list_path on a name that is not an api import is '
          'still a site')
    check(len(scan_api("def f(self, c):\n"
                       "    c.request_async('GET', api_rel.list_path("
                       "'mutes'))\n")) == 1,
          'self-check: a raw request_async path is never covered')
    hook_ok = ("class C:\n"
               "    def search_request(self, q):\n"
               "        return api_accounts.search_path(), {'q': q}\n")
    check(scan_api(hook_ok, 'src/mentioncompleter.py') == [],
          'self-check: a hook returning search_path() needs no entry')
    check(len(scan_api(hook_ok.replace('api_accounts.search_path()',
                                       "'/accounts/search'"),
                       'src/mentioncompleter.py')) == 1,
          'self-check: a hook returning a literal path is still a site')


CLIENT_NAMES = ('client', '_client')
# Receivers that are named like a client but are not a MastodonClient. Keep
# this short, each with its reason.
NOT_A_CLIENT = {}


def client_members(source):
    """Names defined in the body of class MastodonClient of *source*."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MastodonClient':
            names = set()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(item.name)
                elif isinstance(item, ast.Assign):
                    names.update(t.id for t in item.targets
                                 if isinstance(t, ast.Name))
            return names
    return set()


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
    with open(os.path.join(srcdir, 'mastodon.py'), encoding='utf-8') as h:
        members = client_members(h.read())
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
    folders = [srcdir, os.path.join(srcdir, 'catchup'),
               os.path.join(os.path.dirname(srcdir), 'bench')]
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


def main():
    self_check()
    check_client_calls()

    with open(ALLOWLIST, encoding='utf-8') as handle:
        data = json.load(handle)
    entries = data['entries']

    for entry in entries:
        check(all(entry.get(k) for k in ('file', 'function', 'via', 'path',
                                         'layer', 'reason'))
              and entry['reason'].startswith('phase ')
              and entry['layer'] in ('raw', 'internal', 'helper'),
              'allow-list entry is complete: %s %s %s' % (
                  entry.get('file'), entry.get('function'),
                  entry.get('path')))

    found = []
    sources = source_files()
    known = derived_names(sources)
    for rel, source in sources:
        found.extend(scan_source(rel, source, known))

    new, stale = compare(found, entries)
    for line in new:
        print('FAIL - raw API call not on the allow-list (route it through '
              'src/api/): ' + line)
    for line in stale:
        print('FAIL - allow-list entry has no matching call, remove it: '
              + line)
    if os.environ.get('BABBLE_BOUNDARY_PRUNE'):
        kept, removed = prune_entries(found, entries)
        for line in removed:
            print('pruned - ' + line)
        if removed:
            data['entries'] = kept
            with open(ALLOWLIST, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=1)
                handle.write('\n')
        print('note - pruned %d stale entr%s' % (
            len(removed), 'y' if len(removed) == 1 else 'ies'))
        entries = kept
        new, stale = compare(found, entries)
    check(not new, 'no API call outside the allow-list')
    check(not stale, 'no stale allow-list entry')

    # Counts, by layer and by phase, from the list the sites matched.
    by_layer = collections.Counter()
    by_phase = collections.Counter()
    for entry in entries:
        n = entry.get('count', 1)
        by_layer[entry['layer']] += n
        by_phase[entry['reason'].split(':')[0]] += n
    total = sum(by_layer.values())
    print('allow-list: %d sites remaining (raw %d, internal %d, helper %d)'
          % (total, by_layer['raw'], by_layer['internal'],
             by_layer['helper']))
    print('allow-list by phase: ' + ', '.join(
        '%s %d' % (k, by_phase[k]) for k in sorted(by_phase)))

    if failures:
        print('%d failure(s)' % failures)
        sys.exit(1)


main()
