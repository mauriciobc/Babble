# SPDX-License-Identifier: GPL-3.0-or-later
"""List endpoints (audit rows LS-1..LS-6, AC-8 and TL-5), declared once and
sent through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the list editor and the lists pages sent before: same
verb, path, form and params.

``DELETE /lists/:id/accounts`` sends ``account_ids[]`` as form data, as the
docs say (LS-6, audit 5e). ``exclusive`` (4.2.0) is not version-gated by the
caller.

`min_version` is None throughout: the audit records no gate in the app for
these, so the release is in ``mastodon_version`` and nothing here changes
what the app asks of an instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/'


def _declare(name, method, path, scope, anchor, note=None, version=None,
             docs='lists/'):
    return core.declare('lists.' + name, method, path, scope, None,
                        DOCS + docs + anchor, note=note,
                        mastodon_version=version)


INDEX = _declare('index', 'GET', '/lists', 'read:lists', '#get',
                 version='2.1.0')
CREATE = _declare('create', 'POST', '/lists', 'write:lists', '#create',
                  note='form title, replies_policy, exclusive (4.2.0, not '
                       'version-gated by the caller)',
                  version='2.1.0')
UPDATE = _declare('update', 'PUT', '/lists/%s', 'write:lists', '#update',
                  note='same form as create', version='2.1.0')
DELETE = _declare('delete', 'DELETE', '/lists/%s', 'write:lists',
                  '#delete', version='2.1.0')
MEMBERS = _declare('members', 'GET', '/lists/%s/accounts', 'read:lists',
                   '#accounts', note='limit=0 returns every member',
                   version='2.1.0')
ADD_MEMBERS = _declare('add_members', 'POST', '/lists/%s/accounts',
                       'write:lists', '#accounts-add',
                       note='form account_ids[]', version='2.1.0')
REMOVE_MEMBERS = _declare(
    'remove_members', 'DELETE', '/lists/%s/accounts', 'write:lists',
    '#accounts-remove',
    note='form account_ids[] on a DELETE, as the docs show (audit LS-6, '
         'moved from the query in 5e). Server: `params.permit(account_ids: '
         '[])` reads query and body alike', version='2.1.0')
OF_ACCOUNT = _declare('of_account', 'GET', '/accounts/%s/lists',
                      'read:lists', '#lists', docs='accounts/',
                      note='the lists that have this account as a member',
                      version='2.1.0')
TIMELINE = _declare('timeline', 'GET', '/timelines/list/%s', 'read:lists',
                    '#list', docs='timelines/',
                    note='posts of the list members', version='2.1.0')

# Reads a paged view takes by path: `list_path('index')` for the lists page,
# `list_path('timeline', list_id)` for a list timeline source.
LISTS = {'index': INDEX, 'timeline': TIMELINE}


def list_path(kind, *ids):
    """The path of a paged read (``index``, ``timeline``) for the views
    that take a path rather than send a call; *ids* fill the placeholders
    (the list id)."""
    return LISTS[kind].path % ids


def _form(title, replies_policy, exclusive):
    return {'title': title, 'replies_policy': replies_policy,
            'exclusive': 'true' if exclusive else 'false'}


def index_async(client, callback=None, cancellable=None):
    return core.send(client, INDEX, callback=callback,
                     cancellable=cancellable)


def index_finish(client, result):
    return core.finish(client, result)


def create_async(client, title, replies_policy, exclusive, callback=None,
                 cancellable=None):
    """*exclusive* is a boolean; the form carries ``true`` or ``false``."""
    return core.send(client, CREATE,
                     form=_form(title, replies_policy, exclusive),
                     callback=callback, cancellable=cancellable)


def create_finish(client, result):
    return core.finish(client, result)


def update_async(client, list_id, title, replies_policy, exclusive,
                 callback=None, cancellable=None):
    return core.send(client, UPDATE, list_id,
                     form=_form(title, replies_policy, exclusive),
                     callback=callback, cancellable=cancellable)


def update_finish(client, result):
    return core.finish(client, result)


def delete_async(client, list_id, callback=None, cancellable=None):
    return core.send(client, DELETE, list_id, callback=callback,
                     cancellable=cancellable)


def delete_finish(client, result):
    return core.finish(client, result)


def members_async(client, list_id, callback=None, cancellable=None):
    """Every member (``limit=0``)."""
    return core.send(client, MEMBERS, list_id, params={'limit': 0},
                     callback=callback, cancellable=cancellable)


def members_finish(client, result):
    return core.finish(client, result)


def add_members_async(client, list_id, account_ids, callback=None,
                      cancellable=None):
    return core.send(client, ADD_MEMBERS, list_id,
                     form={'account_ids[]': account_ids},
                     callback=callback, cancellable=cancellable)


def add_members_finish(client, result):
    return core.finish(client, result)


def remove_members_async(client, list_id, account_ids, callback=None,
                         cancellable=None):
    return core.send(client, REMOVE_MEMBERS, list_id,
                     form={'account_ids[]': account_ids},
                     callback=callback, cancellable=cancellable)


def remove_members_finish(client, result):
    return core.finish(client, result)


def of_account_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, OF_ACCOUNT, account_id, callback=callback,
                     cancellable=cancellable)


def of_account_finish(client, result):
    return core.finish(client, result)
