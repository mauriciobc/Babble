# SPDX-License-Identifier: GPL-3.0-or-later
"""Filter endpoints (audit rows FL-1..FL-3), declared once and sent
through `api.core`.

The v2 filters carry their body as JSON, which the docs show as form data
(audit FL-2, kept in 5e for the reasons in ``_JSON``): that is how the
filter editor has always sent it and what the instances accept, so it
stays. The payload
itself is built by `src/filters.py` (`build_payload`), which knows the form;
this module only sends it. ``blur`` (4.4.0) is not offered.

`min_version` is None throughout: the audit records no gate in the app for
these, so the release is in ``mastodon_version``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/filters/'


def _declare(name, method, path, scope, anchor, note=None):
    return core.declare('filters.' + name, method, path, scope, None,
                        DOCS + anchor, note=note, mastodon_version='4.0.0')


_JSON = (
    'ACCEPTED-UNDOCUMENTED (audit FL-2, kept in 5e): JSON body, the docs '
    'show form data. '
    'JSON is needed because `expires_in: null` clears an expiry and the form '
    'encoder drops None (an empty `expires_in=` would also clear it, through '
    '`Expireable#expires_in=` `interval.present?`, but that is not a '
    'documented value), and `keywords_attributes` is an array of objects '
    '(`id`, `keyword`, `whole_word`, `_destroy`) that a flat form cannot '
    'carry without indexed keys the docs do not show. The controller reads '
    '`params.permit(:title, :expires_in, :filter_action, context: [], '
    'keywords_attributes: [...])`')

INDEX = _declare('index', 'GET', '/api/v2/filters', 'read:filters', '#get')
CREATE = _declare('create', 'POST', '/api/v2/filters', 'write:filters',
                  '#create', note=_JSON)
UPDATE = _declare('update', 'PUT', '/api/v2/filters/%s', 'write:filters',
                  '#update', note=_JSON)
DELETE = _declare('delete', 'DELETE', '/api/v2/filters/%s', 'write:filters',
                  '#delete')


def index_async(client, callback=None, cancellable=None):
    return core.send(client, INDEX, callback=callback,
                     cancellable=cancellable)


def index_finish(client, result):
    return core.finish(client, result)


def create_async(client, body, callback=None, cancellable=None):
    """*body* is the dict `filters.build_payload` returns, sent as JSON."""
    return core.send(client, CREATE, json_body=body, callback=callback,
                     cancellable=cancellable)


def create_finish(client, result):
    return core.finish(client, result)


def update_async(client, filter_id, body, callback=None, cancellable=None):
    return core.send(client, UPDATE, filter_id, json_body=body,
                     callback=callback, cancellable=cancellable)


def update_finish(client, result):
    return core.finish(client, result)


def delete_async(client, filter_id, callback=None, cancellable=None):
    return core.send(client, DELETE, filter_id, callback=callback,
                     cancellable=cancellable)


def delete_finish(client, result):
    return core.finish(client, result)
