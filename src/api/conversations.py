# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversation endpoints (audit rows TL-7 and TL-8, the direct-message
inbox), declared once and sent through `api.core`.

`list_path('index')` is for the inbox page, which pages through it itself;
`read_async` and `delete_async` are `<name>_async(client, conversation_id,
callback=None, cancellable=None)` / `<name>_finish(client, result)` pairs.
The request on the wire is the one the inbox sent before.

`min_version` is None: the app does not gate on these, so the release is in
``mastodon_version``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/conversations/'

INDEX = core.declare('conversations.index', 'GET', '/conversations',
                     'read:statuses', None, DOCS + '#get',
                     note='limit via the paged list; Link paged',
                     mastodon_version='2.6.0')
READ = core.declare('conversations.read', 'POST', '/conversations/%s/read',
                    'write:conversations', None, DOCS + '#read',
                    mastodon_version='2.6.0')
DELETE = core.declare('conversations.delete', 'DELETE',
                      '/conversations/%s', 'write:conversations', None,
                      DOCS + '#delete', mastodon_version='2.6.0')

LISTS = {'index': INDEX}


def list_path(kind, *ids):
    return LISTS[kind].path % ids


def read_async(client, conversation_id, callback=None, cancellable=None):
    return core.send(client, READ, conversation_id, callback=callback,
                     cancellable=cancellable)


def read_finish(client, result):
    return core.finish(client, result)


def delete_async(client, conversation_id, callback=None, cancellable=None):
    return core.send(client, DELETE, conversation_id, callback=callback,
                     cancellable=cancellable)


def delete_finish(client, result):
    return core.finish(client, result)
