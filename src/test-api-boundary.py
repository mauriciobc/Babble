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

  * `raw`: `client.request_async(method, path, ...)` and `pages.call(widget,
    client, method, path, ...)`. These are the audit's 83 raw sites (75
    `request_async` outside `mastodon.py`, minus the 7 inner calls of the
    generic helpers, plus 15 `pages.call`). The path argument is keyed.
  * `internal`: the 7 inner `request_async` calls of the generic helpers
    (`pages.call`, `pages.mutate_row`, two in `PagedList`, `admin.send`,
    `fedicollections._send`, `wrapped._send`) and the 8th internal site,
    `pages.AccountListPage.__init__`, which forwards a variable path to
    `PagedList` and calls no `request_async`. They carry a *variable*
    path, so they are keyed by that variable's name. They stay on the
    list until the helper itself moves into `src/api/` (phase 4).
  * `helper`: a call that hands a path or a verb to a helper that sends
    it later: `admin.send`, `fedicollections._send`, `wrapped._send`,
    `mutate_row`, the `PagedList` / `AccountListPage` /
    `ApiTimelineSource` constructors, `profileactions.send_relationship`
    and `_send_profile_action` (their first argument names the operation,
    from which the path is built), Catch Up's `_get`, `multipart_async`,
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
(`r = c.request_async`), `getattr(c, 'request_async')` and `pages.call`
as a value are failures (`alias:` sites), as are aliased imports of
`pages` or `pages.call`. Known limit: `c2 = c; c2.method` by string
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
Mastodon path itself (`request_async`, `pages.call`); `internal` is a
generic helper's own inner send or path forward (7 `request_async` calls
plus `pages.AccountListPage.__init__`, 8 in all); `helper` hands a path or a
verb to a helper that sends it later. Today: 175 sites, raw 83, internal 8,
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
    'pages.call': ('raw', 3, 'path', None),
    'multipart_async': ('helper', 1, 'path', None),
    'admin.send': ('helper', 2, 'path', {'src/admin.py'}),
    'fedicollections._send': ('helper', 2, 'path',
                              {'src/fedicollections.py'}),
    'wrapped._send': ('helper', 2, 'path', {'src/wrapped.py'}),
    'mutate_row': ('helper', 2, 'path', None),
    'PagedList': ('helper', 1, 'fetch_path', None),
    'PagedList.__init__': ('helper', 2, 'fetch_path', None),
    'super().__init__': ('helper', 1, 'fetch_path', None),
    'AccountListPage': ('helper', 1, 'path', None),
    'ApiTimelineSource': ('helper', 1, 'path', None),
    'send_relationship': ('helper', 0, 'operation',
                          {'src/profile.py', 'src/profileactions.py'}),
    '_send_profile_action': ('helper', 1, 'path',
                             {'src/profileactions.py'}),
    'catchup._get': ('helper', 0, 'path', {'src/catchup/fetch.py'}),
}
HOOK_NAME = 'search_request'
# Methods whose use as a value (not a call) or through getattr is a failure:
# the scan could not see where the path goes.
ALIASED = frozenset({'request_async'})
# Classes whose constructor takes a path: a subclass hands it on through
# `super().__init__(client, '/path', ...)`, which names the path too.
PATH_BASES = frozenset({'PagedList', 'AccountListPage'})


def mechanism_of(call, rel, call_names, pages_names, derived, in_derived):
    """The MECHANISMS key a Call node is, or None.

    *call_names* are the local names `pages.call` is imported as,
    *pages_names* the local names of the `pages` module, *derived* the
    names of PagedList subclasses whose constructor forwards ``*args``,
    and *in_derived* tells that the call sits in a PagedList subclass."""
    func = call.func
    if isinstance(func, ast.Attribute):
        name, base = func.attr, func.value
        if name == 'call':
            if isinstance(base, ast.Name) and base.id in pages_names:
                return 'pages.call'
            return None
        if name == 'send' and rel == 'src/admin.py':
            return 'admin.send'
        if name == '_send':
            return {'src/fedicollections.py': 'fedicollections._send',
                    'src/wrapped.py': 'wrapped._send'}.get(rel)
        if name == '_get':
            return 'catchup._get' if rel == 'src/catchup/fetch.py' else None
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
        if name in call_names:
            return 'pages.call'
        if name == 'send' and rel == 'src/admin.py':
            return 'admin.send'
        if name == '_send':
            return {'src/fedicollections.py': 'fedicollections._send',
                    'src/wrapped.py': 'wrapped._send'}.get(rel)
        if name in derived:
            return 'PagedList'
    else:
        return None
    if name in derived:
        return 'PagedList'
    if name in MECHANISMS and name not in (
            'pages.call', 'admin.send', 'catchup._get', 'super().__init__',
            'PagedList.__init__'):
        files = MECHANISMS[name][3]
        if files is None or rel in files:
            return name
    return None


def path_text(call, mechanism):
    """The path argument of *call* as stable text, or None if absent."""
    _layer, index, keyword, _files = MECHANISMS[mechanism]
    node = None
    if len(call.args) > index:
        node = call.args[index]
    else:
        for kw in call.keywords:
            if kw.arg in (keyword, 'path'):
                node = kw.value
    if node is None:
        return None
    return ' '.join(ast.unparse(node).split())


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
    def __init__(self, rel, call_names, pages_names, derived, forwarding):
        self.rel = rel
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
            if text is not None:
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
        if id(node) not in self.called and (
                node.attr in ALIASED
                or (node.attr == 'call' and isinstance(node.value, ast.Name)
                    and node.value.id in self.pages_names)):
            self.add('alias:' + node.attr, ast.unparse(node), 'raw',
                     node.lineno)
        self.generic_visit(node)

    def visit_Return(self, node):
        # A completer hook answers (path, params): the path is named here.
        if (self.scope and self.scope[-1] == HOOK_NAME
                and isinstance(node.value, ast.Tuple) and node.value.elts):
            text = ' '.join(ast.unparse(node.value.elts[0]).split())
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


def scan_source(rel, source, known=frozenset()):
    """[(file, function, mechanism, path text, layer, line)] of one source.

    *known* are PagedList-subclass names defined in other files."""
    if is_exempt(rel):
        return []
    tree = ast.parse(source, rel)
    call_names, pages_names = import_names(tree)
    derived, forwarding = path_classes(tree, known)
    visitor = Visitor(rel, call_names, pages_names, derived, forwarding)
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

    helper = ("from .pages import call\n"
              "def go(w, c):\n"
              "    call(w, c, 'GET', '/statuses/1')\n"
              "    pages.call(w, c, 'GET', '/statuses/2')\n")
    found = scan_source('src/h.py', helper)
    check(sorted(f[3] for f in found)
          == ["'/statuses/1'", "'/statuses/2'"],
          'self-check: call and pages.call both count')

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
               "from .pages import call as pc\n"
               "def go(w, c):\n"
               "    pg.call(w, c, 'GET', '/a')\n"
               "    pc(w, c, 'GET', '/b')\n"
               "    r = c.request_async\n"
               "    getattr(c, 'request_async')\n"
               "    f = pg.call\n")
    found = scan_source('src/a.py', aliases)
    check(sorted(f[2] for f in found) == [
        'alias:call', 'alias:request_async', 'alias:request_async',
        'pages.call', 'pages.call'],
          'self-check: aliased pages.call and request_async are caught')

    local = ("def f():\n"
             "    def send(attempt):\n"
             "        pass\n"
             "    send(1)\n")
    check(scan_source('src/profileactions.py', local) == [],
          'self-check: a local send() outside admin.py is not the helper')


def main():
    self_check()

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
