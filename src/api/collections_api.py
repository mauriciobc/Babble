# SPDX-License-Identifier: GPL-3.0-or-later
"""Collection endpoints (audit rows OT-7 and AC-23), declared once and sent
through `api.core`.

Named ``collections_api`` so that nothing in the package can shadow the
standard library's ``collections``; the screens are ``fedicollections``.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the Collections screens sent before: same verb, path
and JSON body.

Source of the declarations: https://docs.joinmastodon.org/methods/collections/
(read on 2026-11-05). Every endpoint is new in Mastodon 4.6.0 (API version
10; the callers gate the feature on ``capabilities.COLLECTIONS_API_VERSION``,
so ``min_version`` stays None here). The docs label the parameters of
create, update and add-item "Form data" but show ``Content-Type:
application/json`` examples; the app sends JSON (audit OT-7), so that is
what is recorded and what the golden log pins.

Docs anchors differ from the function names: ``#get_collection``,
``#get_collections``, ``#in_collections``, ``#update_collection``,
``#delete_collection``, ``#add_account``, ``#remove_account`` and
``#revoke_item``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/collections/'


def _declare(name, method, path, scope, anchor, note=None):
    return core.declare('collections.' + name, method, path, scope, None,
                        DOCS + anchor, note=note, mastodon_version='4.6.0')


_JSON = ('JSON body; the docs label the parameters form data but their '
         'example is JSON (audit OT-7)')

CREATE = _declare('create', 'POST', '/collections', 'write:collections',
                  '#create',
                  note='%s: name, description, language, sensitive, '
                       'discoverable, tag_name, account_ids' % _JSON)
GET = _declare('get', 'GET', '/collections/%s', 'read:collections',
               '#get_collection',
               note='public, or a user token with read:collections; '
                    'answers {collection, accounts}')
UPDATE = _declare('update', 'PATCH', '/collections/%s', 'write:collections',
                  '#update_collection',
                  note='%s; only the changed fields are sent' % _JSON)
DELETE = _declare('delete', 'DELETE', '/collections/%s',
                  'write:collections', '#delete_collection')
ADD_ITEM = _declare('add_item', 'POST', '/collections/%s/items',
                    'write:collections', '#add_account',
                    note='%s: account_id' % _JSON)
REMOVE_ITEM = _declare('remove_item', 'DELETE',
                       '/collections/%s/items/%s', 'write:collections',
                       '#remove_account')
REVOKE_ITEM = _declare('revoke_item', 'POST',
                       '/collections/%s/items/%s/revoke',
                       'write:collections', '#revoke_item',
                       note='the featured account removes itself')
BY_ACCOUNT = _declare('by_account', 'GET', '/accounts/%s/collections',
                      'read:collections', '#get_collections',
                      note='public, or a user token with read:collections; '
                           'paged with limit and offset')
IN_COLLECTIONS = _declare('in_collections', 'GET',
                          '/accounts/%s/in_collections', 'read:collections',
                          '#in_collections',
                          note='the collections that feature the account; '
                               'user token required')

# Reads a paged view takes by path.
LISTS = {'by_account': BY_ACCOUNT, 'featuring': IN_COLLECTIONS}


def list_path(kind, account_id):
    """The path of a paged read (``by_account``, ``featuring``) for the
    Collections page, which takes a path rather than sending a call."""
    return LISTS[kind].path % account_id


def create_async(client, body, callback=None, cancellable=None):
    return core.send(client, CREATE, json_body=body, callback=callback,
                     cancellable=cancellable)


def create_finish(client, result):
    return core.finish(client, result)


def get_async(client, collection_id, callback=None, cancellable=None):
    return core.send(client, GET, collection_id, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def update_async(client, collection_id, changes, callback=None,
                 cancellable=None):
    return core.send(client, UPDATE, collection_id, json_body=changes,
                     callback=callback, cancellable=cancellable)


def update_finish(client, result):
    return core.finish(client, result)


def delete_async(client, collection_id, callback=None, cancellable=None):
    return core.send(client, DELETE, collection_id, callback=callback,
                     cancellable=cancellable)


def delete_finish(client, result):
    return core.finish(client, result)


def add_item_async(client, collection_id, account_id, callback=None,
                   cancellable=None):
    return core.send(client, ADD_ITEM, collection_id,
                     json_body={'account_id': account_id},
                     callback=callback, cancellable=cancellable)


def add_item_finish(client, result):
    return core.finish(client, result)


def remove_item_async(client, collection_id, item_id, callback=None,
                      cancellable=None):
    return core.send(client, REMOVE_ITEM, collection_id, item_id,
                     callback=callback, cancellable=cancellable)


def remove_item_finish(client, result):
    return core.finish(client, result)


def revoke_item_async(client, collection_id, item_id, callback=None,
                      cancellable=None):
    return core.send(client, REVOKE_ITEM, collection_id, item_id,
                     callback=callback, cancellable=cancellable)


def revoke_item_finish(client, result):
    return core.finish(client, result)
