#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The endpoint registry is complete, and the checker can fail.

Every `Endpoint` in `src.api.core.registry` is held to the same rules:
a name, a valid verb, a path that starts with '/' and whose placeholders
are only %s or %d, an OAuth scope of a known shape (None only with a note
saying why), a docs URL on docs.joinmastodon.org, an int or None
`min_version`, a `mastodon_version` that is None or dotted digits
('4.5.0'), and a replacement named whenever `deprecated` is set.

Further rules over the whole registry: the docs URL carries an anchor
(`#...`); no two endpoints share a (method, path) unless the note of one
says `same request as`; and every endpoint a path accessor hands to a
paged view (`LISTS`, `PATH_ONLY` of a module) is a GET, because those views
hard-code GET and a declared POST would never be sent.

The rules are proved on a synthetic registry too: one good endpoint passes,
and each rule has a bad endpoint that must fail it. The real registry is
imported from the resource modules and its size is asserted, so the test
cannot pass by checking nothing. This test needs no display.

The same test keeps the "Endpoints" table of `Docs/mastodon-compat.md` in step
with the registry: the block between `<!-- api-table:start -->` and
`<!-- api-table:end -->` is generated, never edited by hand, and the test
fails when it differs from `render_table(core.registry)`. Regenerate it with

    python3 src/test-api-registry.py --write-table
"""

import os
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.api import core  # noqa: E402
from src.api import accounts, announcements, filters, lists  # noqa: E402, F401
from src.api import relationships, scheduled, search, statuses  # noqa: E402, F401
from src.api import tags  # noqa: E402, F401
from src.api import markers, notifications, timelines, trends  # noqa: E402, F401
from src.api import annual_reports, conversations, instance  # noqa: E402, F401
from src.api import suggestions  # noqa: E402, F401
from src.api import admin  # noqa: E402, F401
from src.api import collections_api, polls, reports  # noqa: E402, F401
from src.api import media  # noqa: E402, F401

RESOURCE_MODULES = (statuses, accounts, relationships, lists, filters,
                    announcements, scheduled, tags, search, timelines,
                    markers, trends, notifications, conversations,
                    suggestions, instance, annual_reports, admin,
                    collections_api, polls, reports, media)
# Modules whose endpoints a paged view reads by path: each declares LISTS.
# `filters` has no paged view (its list is read once), `search` serves
# only a path to completers and `markers`, `instance` and `annual_reports`
# are read once, so they declare none; nor do `polls` and `reports`, which
# only get or send one object, nor `media`, which uploads and polls one.
PAGED_MODULES = tuple(m for m in RESOURCE_MODULES
                      if m not in (filters, search, markers, instance,
                                   annual_reports, polls, reports, media))
MINIMUM_ENDPOINTS = (53 + 9 + 4 + 4 + 3 + 9 + 1 + 6 + 2 + 1 + 9
                     + 3 + 2 + 1 + 4 + 1 + 29 + 9 + 2 + 1
                     + 2 + 2 + 2 + 1)  # 160: statuses, accounts, media (3c)

failed = False


def check(label, condition):
    global failed
    print('%s - %s' % ('ok' if condition else 'FAIL', label))
    if not condition:
        failed = True


METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
SCOPE_TOKEN = re.compile(
    r'^(read|write|follow|push|profile|admin:read|admin:write)'
    r'(:[a-z_]+)*$')
DOCS = 'https://docs.joinmastodon.org/'
PLACEHOLDER = re.compile(r'%.')
RELEASE = re.compile(r'^\d+(\.\d+)+$')


def problems(endpoint):
    """The rules an endpoint breaks, as short names."""
    found = []
    if isinstance(endpoint.docs, str) and '#' not in endpoint.docs:
        found.append('docs-anchor')
    if not isinstance(endpoint.name, str) or not endpoint.name.strip():
        found.append('name')
    if endpoint.method not in METHODS:
        found.append('method')
    if not isinstance(endpoint.path, str) or not endpoint.path.startswith('/'):
        found.append('path')
    elif any(m not in ('%s', '%d') for m in PLACEHOLDER.findall(endpoint.path)):
        found.append('placeholder')
    scope = endpoint.scope
    if scope is None:
        if not endpoint.note:
            found.append('scope-undocumented')
    elif not (isinstance(scope, str) and scope.split()
              and all(SCOPE_TOKEN.match(t) for t in scope.split())):
        found.append('scope')
    if not (isinstance(endpoint.docs, str)
            and endpoint.docs.startswith(DOCS)
            and len(endpoint.docs) > len(DOCS)):
        found.append('docs')
    version = endpoint.min_version
    if version is not None and (not isinstance(version, int)
                                or isinstance(version, bool)):
        found.append('min_version')
    release = endpoint.mastodon_version
    if release is not None and not (isinstance(release, str)
                                    and RELEASE.match(release)):
        found.append('mastodon_version')
    if endpoint.deprecated is not None and (
            not isinstance(endpoint.deprecated, str)
            or not endpoint.deprecated.strip()):
        found.append('deprecated')
    return found


good = core.Endpoint('statuses.favourite', 'POST', '/statuses/%s/favourite',
                     'write:favourites', None,
                     DOCS + 'methods/statuses/#favourite')
check('a complete endpoint breaks no rule', problems(good) == [])
check('a public endpoint with a note is allowed',
      problems(core.Endpoint('x', 'GET', '/instance', None, None,
                             DOCS + 'methods/instance/#get',
                             note='public, no token needed')) == [])

BAD = {
    'name': good.__class__(' ', 'GET', '/a', 'read', None, DOCS + 'x/'),
    'method': good.__class__('a', 'FETCH', '/a', 'read', None, DOCS + 'x/'),
    'path': good.__class__('a', 'GET', 'a', 'read', None, DOCS + 'x/'),
    'placeholder': good.__class__('a', 'GET', '/a/%x', 'read', None,
                                  DOCS + 'x/'),
    'scope-undocumented': good.__class__('a', 'GET', '/a', None, None,
                                         DOCS + 'x/'),
    'scope': good.__class__('a', 'GET', '/a', 'writes:statuses', None,
                            DOCS + 'x/'),
    'docs': good.__class__('a', 'GET', '/a', 'read', None,
                           'https://example.com/x/'),
    'min_version': good.__class__('a', 'GET', '/a', 'read', '10',
                                  DOCS + 'x/#a'),
    'docs-anchor': good.__class__('a', 'GET', '/a', 'read', None, DOCS + 'x/'),
    'mastodon_version': good.__class__('a', 'GET', '/a', 'read', None,
                                       DOCS + 'x/#a', mastodon_version='v4'),
    'deprecated': good.__class__('a', 'GET', '/a', 'read', None, DOCS + 'x/',
                                 deprecated=' '),
}
for rule, endpoint in BAD.items():
    check('the checker fails the %r rule' % rule, rule in problems(endpoint))

# declare(): registers, rejects duplicates, and send() builds the request.
mine = {}
declared = core.declare('a.b', 'GET', '/a/%s', 'read', 1, DOCS + 'x/',
                        into=mine, mastodon_version='4.5.0')
check('declare registers the endpoint', mine.get('a.b') is declared)
check('declare keeps mastodon_version, defaulted to None',
      declared.mastodon_version == '4.5.0'
      and good.mastodon_version is None)
try:
    core.declare('a.b', 'GET', '/a', 'read', 1, DOCS + 'x/', into=mine)
    check('declare rejects a duplicate name', False)
except ValueError:
    check('declare rejects a duplicate name', True)


class Client:
    def request_async(self, *args, **kwargs):
        self.sent = (args, kwargs)
        return 'task'

    def request_finish(self, result):
        return {'result': result}


client = Client()
task = core.send(client, declared, '42', params={'limit': 1})
check('send formats the path and calls the public request_async',
      task == 'task' and client.sent[0] == ('GET', '/a/42')
      and client.sent[1]['params'] == {'limit': 1}
      and client.sent[1]['json_body'] is None)
core.send(client, core.Endpoint('b', 'POST', '/b', 'write', None, DOCS + 'x/'),
          form={'k': 'v'})
check('send without path args leaves the path untouched',
      client.sent[0] == ('POST', '/b') and client.sent[1]['form'] == {'k': 'v'})
check('finish returns the client\'s decoded body',
      core.finish(client, 'r') == {'result': 'r'})
client.request_finish_paged = lambda result: ([result], {'max_id': '1'}, None)
check('finish_paged returns the body and the parsed Link parameters',
      core.finish_paged(client, 'r') == (['r'], {'max_id': '1'}, None))



class MultipartClient:
    def multipart_async(self, method, path, **kwargs):
        self.sent = (method, path, kwargs)
        return 'multipart-task'


multi = MultipartClient()
task = core.send_multipart(
    multi, core.Endpoint('m', 'PATCH', '/m/%s', 'write', None, DOCS + 'x/#m'),
    '7', fields={'a': 'b'}, files={'f': (b'x', 'x.bin', 'text/plain')})
check('send_multipart formats the path and calls the public multipart_async',
      task == 'multipart-task' and multi.sent[:2] == ('PATCH', '/m/7')
      and multi.sent[2]['fields'] == {'a': 'b'}
      and multi.sent[2]['files'] == {'f': (b'x', 'x.bin', 'text/plain')})


def duplicates(registry):
    """Names sharing a (method, path), unless one says `same request as`."""
    seen = {}
    for endpoint in registry.values():
        seen.setdefault((endpoint.method, endpoint.path),
                        []).append(endpoint)
    return sorted(e.name for group in seen.values() if len(group) > 1
                  and not any('same request as' in (e.note or '')
                              for e in group) for e in group)


def non_get_lists(modules):
    """Endpoints a path accessor serves that are not GET."""
    out = []
    for module in modules:
        served = list(getattr(module, 'LISTS', {}).values())
        served += list(getattr(module, 'PATH_ONLY', ()))
        out += [e.name for e in served if e.method != 'GET']
    return out


twin = {'a': core.Endpoint('a', 'GET', '/x', 'read', None, DOCS + 'x/#a'),
        'b': core.Endpoint('b', 'GET', '/x', 'read', None, DOCS + 'x/#b')}
check('the checker fails a duplicated (method, path)',
      duplicates(twin) == ['a', 'b'])
twin['b'] = core.Endpoint('b', 'GET', '/x', 'read', None, DOCS + 'x/#b',
                          note='same request as a')
check('a duplicate marked in its note is allowed', duplicates(twin) == [])


class FakeModule:
    LISTS = {'k': core.Endpoint('k', 'POST', '/k', 'read', None,
                                DOCS + 'x/#k')}
    PATH_ONLY = ()


check('the checker fails a list-served endpoint that is not GET',
      non_get_lists([FakeModule]) == ['k'])

# The real registry: every declared endpoint passes every rule.
for name, endpoint in sorted(core.registry.items()):
    check('registry %s: %s' % (name, problems(endpoint) or 'complete'),
          problems(endpoint) == [] and endpoint.name == name)
print('registry holds %d endpoint(s)' % len(core.registry))

# `min_version` is the gate a caller asks. src/api may not import
# capabilities (R8), so the number is written out there and held equal to
# the constant here.
from src import capabilities  # noqa: E402

GATED = {
    'annual_reports.state': capabilities.ANNUAL_REPORT_STATE_API_VERSION,
    'annual_reports.generate': capabilities.ANNUAL_REPORT_STATE_API_VERSION,
    'accounts.endorsements': capabilities.ENDORSEMENTS_API_VERSION,
    'notifications.unread_count': capabilities.NOTIFICATION_POLICY_API_VERSION,
    'notifications.policy': capabilities.NOTIFICATION_POLICY_API_VERSION,
    'notifications.update_policy':
        capabilities.NOTIFICATION_POLICY_API_VERSION,
    'notifications.requests': capabilities.NOTIFICATION_POLICY_API_VERSION,
    'notifications.accept_request':
        capabilities.NOTIFICATION_POLICY_API_VERSION,
    'notifications.dismiss_request':
        capabilities.NOTIFICATION_POLICY_API_VERSION,
}
for name, constant in sorted(GATED.items()):
    check('%s min_version equals its capabilities constant (%d)'
          % (name, constant), core.registry[name].min_version == constant)
check('no other endpoint carries a min_version',
      sorted(n for n, e in core.registry.items()
             if e.min_version is not None) == sorted(GATED))
check('the registry holds at least %d endpoints (it checked %d)'
      % (MINIMUM_ENDPOINTS, len(core.registry)),
      len(core.registry) >= MINIMUM_ENDPOINTS)
check('no two endpoints share a (method, path): %s'
      % (duplicates(core.registry) or 'none'),
      duplicates(core.registry) == [])
check('every list-served endpoint is a GET: %s'
      % (non_get_lists(RESOURCE_MODULES) or 'all'),
      non_get_lists(RESOURCE_MODULES) == [])
check('every resource module serves at least one list',
      all(getattr(m, 'LISTS', None) for m in PAGED_MODULES))

TABLE_START = '<!-- api-table:start -->'
TABLE_END = '<!-- api-table:end -->'
COMPAT = os.path.join(os.path.dirname(os.path.abspath(srcdir)), 'Docs',
                      'mastodon-compat.md')


def render_table(registry):
    """The markdown table of *registry*, one row per endpoint."""
    lines = ['| Method | Path | Scope | Mastodon | Deprecated | Docs |',
             '|---|---|---|---|---|---|']
    for endpoint in sorted(registry.values(),
                           key=lambda e: (e.path, e.method, e.name)):
        lines.append('| %s | `%s` | %s | %s | %s | [docs](%s) |' % (
            endpoint.method, endpoint.path,
            '`%s`' % endpoint.scope if endpoint.scope else 'none',
            endpoint.mastodon_version or '',
            endpoint.deprecated or '', endpoint.docs))
    return '\n'.join(lines)


def splice_table(text, table):
    """*text* with the generated block replaced by *table*, or None when
    the markers are missing."""
    start, end = text.find(TABLE_START), text.find(TABLE_END)
    if start < 0 or end < start:
        return None
    return (text[:start + len(TABLE_START)] + '\n' + table + '\n'
            + text[end:])


with open(COMPAT, encoding='utf-8') as handle:
    compat_text = handle.read()
fresh = splice_table(compat_text, render_table(core.registry))
if '--write-table' in sys.argv and fresh is not None:
    with open(COMPAT, 'w', encoding='utf-8') as handle:
        handle.write(fresh)
    print('wrote %s' % COMPAT)
    compat_text = fresh
check('splice_table needs both markers',
      splice_table('no markers', 'x') is None
      and splice_table('%s\nold\n%s' % (TABLE_START, TABLE_END), 'new')
      == '%s\nnew\n%s' % (TABLE_START, TABLE_END))
changed = dict(core.registry)
first = sorted(changed)[0]
changed[first] = core.dataclasses.replace(changed[first], scope='read:x')
check('the table differs when an endpoint changes',
      render_table(changed) != render_table(core.registry))
check('Docs/mastodon-compat.md Endpoints table is current '
      '(regenerate: python3 src/test-api-registry.py --write-table)',
      fresh is not None and fresh == compat_text)

sys.exit(1 if failed else 0)
