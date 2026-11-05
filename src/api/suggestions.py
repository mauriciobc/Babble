# SPDX-License-Identifier: GPL-3.0-or-later
"""Follow suggestions (audit rows OT-2 and OT-3), declared once and sent
through `api.core`. Following a suggested account is
`relationships.action_async`.

`list_path('index')` is for the For You page, which pages through it itself
(the v2 endpoint, the v1 one is deprecated and not used); `dismiss_async`
is a `<name>_async` / `<name>_finish` pair. The request on the wire is the
one the explore page sent before.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/suggestions/'

INDEX = core.declare('suggestions.index', 'GET', '/api/v2/suggestions',
                     'read', None, DOCS + '#v2',
                     note='limit 40; not paged', mastodon_version='3.4.0')
DISMISS = core.declare('suggestions.dismiss', 'DELETE', '/suggestions/%s',
                       'read', None, DOCS + '#remove',
                       note='the docs list `read` as the scope, not '
                            '`write`', mastodon_version='2.4.3')

LISTS = {'index': INDEX}


def list_path(kind, *ids):
    return LISTS[kind].path % ids


def dismiss_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, DISMISS, account_id, callback=callback,
                     cancellable=cancellable)


def dismiss_finish(client, result):
    return core.finish(client, result)
