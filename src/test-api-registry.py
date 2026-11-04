#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The endpoint registry is complete, and the checker can fail.

Every `Endpoint` in `src.api.core.registry` is held to the same rules:
a name, a valid verb, a path that starts with '/' and whose placeholders
are only %s or %d, an OAuth scope of a known shape (None only with a note
saying why), a docs URL on docs.joinmastodon.org, an int or None
`min_version`, and a replacement named whenever `deprecated` is set.

The registry is empty in phase 1a (no wrapper has moved yet), so the
rules are proved on a synthetic registry: one good endpoint passes, and
each rule has a bad endpoint that must fail it. This test needs no display.
"""

import os
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.api import core  # noqa: E402

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


def problems(endpoint):
    """The rules an endpoint breaks, as short names."""
    found = []
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
                             DOCS + 'methods/instance/',
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
    'min_version': good.__class__('a', 'GET', '/a', 'read', '10', DOCS + 'x/'),
    'deprecated': good.__class__('a', 'GET', '/a', 'read', None, DOCS + 'x/',
                                 deprecated=' '),
}
for rule, endpoint in BAD.items():
    check('the checker fails the %r rule' % rule, rule in problems(endpoint))

# declare(): registers, rejects duplicates, and send() builds the request.
mine = {}
declared = core.declare('a.b', 'GET', '/a/%s', 'read', 1, DOCS + 'x/',
                        into=mine)
check('declare registers the endpoint', mine.get('a.b') is declared)
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

# The real registry: every declared endpoint passes every rule.
for name, endpoint in sorted(core.registry.items()):
    check('registry %s: %s' % (name, problems(endpoint) or 'complete'),
          problems(endpoint) == [] and endpoint.name == name)
print('registry holds %d endpoint(s)' % len(core.registry))

sys.exit(1 if failed else 0)
