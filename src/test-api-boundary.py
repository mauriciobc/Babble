# SPDX-License-Identifier: GPL-3.0-or-later

"""The Mastodon API boundary, as a test so the list of raw calls only shrinks.

`Docs/api-layer-plan.md` moves every Mastodon request behind `src/api/`.
Until that is done, UI code still builds paths and passes them to the
generic client. This test enforces three rules against
`src/api-boundary-allowlist.json`, which is EMPTY since phase 4 (the
mechanism stays so a list entry could be added to carry a move, and the
list still may only shrink):

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
    `accountrows.AccountListPage.__init__`, which forwards a variable path to
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

Phase 4 emptied the list. The generic helpers (`PagedList.load_more` and
`refresh`, `ApiTimelineSource.fetch`, the completers' `_search`) send through
`core.send_path`, the one function outside `mastodon.py` that sends a path a
caller supplies. The rule is: `send_path` takes a bare variable path (a name
or attribute chain, never a literal, a template or a call) and only in
`SEND_PATH_FILES`; anywhere else it is a site like a raw `request_async`. A
generic base class (`AccountListPage`) handing its own `path` parameter to
its base is not a site either. What this guards: a path is named once, at
the call that builds the helper (checked as a `helper` site), and the sender
cannot be used to name a path in a new place.

Counts, and the three layers in two lines. `raw` is a call that names a
Mastodon path itself (`request_async`); `internal` is a
generic helper's own inner send or path forward (7 `request_async` calls
plus `accountrows.AccountListPage.__init__`, 8 in all); `helper` hands a path or a
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
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit import apiboundary as kit  # noqa: E402
from testkit.apiboundary import (check, check_client_calls,  # noqa: E402
                                 check_decode_calls, compare,
                                 derived_names, prune_entries, scan_source,
                                 source_files)

ALLOWLIST = os.path.join(srcdir, 'api-boundary-allowlist.json')


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

    sp_var = ("def go(self):\n"
              "    api_core.send_path(self.client, 'GET', self.fetch_path)\n")
    check(scan_source('src/pages.py', sp_var) == [],
          'self-check: core.send_path with a variable path is allowed in '
          'a helper file')
    check(len(scan_source('src/timeline.py', sp_var)) == 1,
          'self-check: core.send_path with a variable path fails outside '
          'the helper files')
    for body in ("'/statuses'", "'/a/%s' % x", "f'/a/{x}'",
                 "api_accounts.list_path('mutes')"):
        check(len(scan_source('src/pages.py', (
            "def go(self):\n"
            "    api_core.send_path(self.client, 'GET', %s)\n" % body))) == 1,
              'self-check: core.send_path with %s fails even in a helper '
              'file' % body)
    check(len(scan_source('src/other.py', (
        "def go(client):\n"
        "    api_core.send_path(client, 'GET', '/statuses')\n"))) == 1,
          'self-check: core.send_path with a literal in a UI file fails')
    passthrough = ("class AccountListPage(PagedList):\n"
                   "    def __init__(self, client, path, row):\n"
                   "        super().__init__(client, path, row)\n")
    check(scan_source('src/pages.py', passthrough) == [],
          'self-check: a generic base forwarding its own path parameter '
          'is allowed')
    check(len(scan_source('src/pages.py', passthrough.replace(
        'client, path, row):', 'client, row):').replace(
        'super().__init__(client, path', "super().__init__(client, '/m'"))
          ) == 1,
          'self-check: a base forwarding a literal path is a site')


# Receivers that are named like a client but are not a MastodonClient. Keep
# this short, each with its reason.


def _class_body_names(node):
    names = set()
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(item.name)
        elif isinstance(item, ast.Assign):
            names.update(t.id for t in item.targets
                         if isinstance(t, ast.Name))
    return names


# The decode rule. Sending goes through `src/api`, and so must decoding: a
# UI module takes the answer with the api module's own `<name>_finish`, or
# `api.core.finish` / `finish_paged` for a request a generic helper started
# from a caller-supplied path. A direct `client.request_finish` /
# `request_finish_paged` would bypass any validation or unpacking a
# `*_finish` gains later, and would let one request be decoded two ways.
# `src/mastodon.py` (the transport) and `src/api/` (core.finish itself) are
# the only places that name them. File -> reason for an explicit exception;
# empty, and each entry would need a reason.


def main():
    self_check()
    check_client_calls()
    check_decode_calls()

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

    if kit.failures:
        print('%d failure(s)' % kit.failures)
        sys.exit(1)


main()
