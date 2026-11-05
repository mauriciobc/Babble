# SPDX-License-Identifier: GPL-3.0-or-later
"""Scheduled-status endpoints (audit rows SC-1..SC-3), declared once and
sent through `api.core`.

A scheduled status without a ``scheduled_at`` that is far enough away is
what the app shows as a draft; that is the caller's reading of the answer,
not something this module decides. Creating one is ``POST /statuses`` with
``scheduled_at`` and stays with the compose code until phase 3. The server
refuses (422, with the sentence the app shows) more than 25 scheduled
statuses dated on one day and more than 300 per account; see UPDATE's note.

`min_version` is None throughout: the audit records no gate in the app for
these, so the release is in ``mastodon_version``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/scheduled_statuses/'


def _declare(name, method, path, scope, anchor, note=None):
    return core.declare('scheduled.' + name, method, path, scope, None,
                        DOCS + anchor, note=note, mastodon_version='2.7.0')


INDEX = _declare('index', 'GET', '/scheduled_statuses', 'read:statuses',
                 '#get', note='paged with the Link header')
UPDATE = _declare('update', 'PUT', '/scheduled_statuses/%s',
                  'write:statuses', '#update',
                  note='form scheduled_at; limits (app/models/'
                       'scheduled_status.rb, 422 with the sentence): 25 '
                       'per scheduled_at date, 300 per account, and the '
                       'total check counts the entry being changed. '
                       'Creating one, `POST /statuses` with scheduled_at, '
                       'has the same limits; every draft is dated the same '
                       'day (is_draft), so 25 drafts a day')
DELETE = _declare('delete', 'DELETE', '/scheduled_statuses/%s',
                  'write:statuses', '#cancel')

# Reads a paged view takes by path.
LISTS = {'index': INDEX}


def list_path(kind, *ids):
    """The path of the scheduled list for the paged page."""
    return LISTS[kind].path % ids


def index_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, INDEX, params=params, callback=callback,
                     cancellable=cancellable)


def index_finish(client, result):
    return core.finish(client, result)


def index_finish_paged(client, result):
    """``(data, next_params, prev_params)`` of one page of the list."""
    return core.finish_paged(client, result)


def update_async(client, scheduled_id, scheduled_at, callback=None,
                 cancellable=None):
    """Reschedule to *scheduled_at*, an ISO 8601 string."""
    return core.send(client, UPDATE, scheduled_id,
                     form={'scheduled_at': scheduled_at},
                     callback=callback, cancellable=cancellable)


def update_finish(client, result):
    return core.finish(client, result)


def delete_async(client, scheduled_id, callback=None, cancellable=None):
    return core.send(client, DELETE, scheduled_id, callback=callback,
                     cancellable=cancellable)


def delete_finish(client, result):
    return core.finish(client, result)
