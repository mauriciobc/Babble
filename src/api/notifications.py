# SPDX-License-Identifier: GPL-3.0-or-later
"""Notification endpoints (audit rows NT-1..NT-8; the reading marker, NT-9
and NT-10, is `markers`), declared once and sent through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the notification page, its controller, the desktop
poller and the policy dialog sent before: same verb, path, params and body.

Carried over from the audit, not fixed here (phase 5): ``PATCH
/api/v2/notifications/policy`` sends the changed fields as a JSON body, the
docs say form data (NT-6). ``unread_count`` is ungated although it is 4.3.0
(NT-2), and so are the policy and the requests (NT-5, NT-7, NT-8): a server
without them answers an error the caller already handles.

`min_version` is None throughout, so the release is in ``mastodon_version``
and nothing here changes what the app asks of an instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/notifications/'


def _declare(name, method, path, scope, anchor, note=None, version=None):
    return core.declare('notifications.' + name, method, path, scope, None,
                        DOCS + anchor, note=note, mastodon_version=version)


INDEX = _declare('index', 'GET', '/notifications', 'read:notifications',
                 '#get', note='types[], exclude_types[], account_id, '
                 'include_filtered (4.3.0), ids and limit 40/80',
                 version='1.0.0')
UNREAD_COUNT = _declare('unread_count', 'GET', '/notifications/unread_count',
                        'read:notifications', '#unread-count',
                        note='types[] and exclude_types[], as the list',
                        version='4.3.0')
CLEAR = _declare('clear', 'POST', '/notifications/clear',
                 'write:notifications', '#clear', version='1.0.0')
DISMISS = _declare('dismiss', 'POST', '/notifications/%s/dismiss',
                   'write:notifications', '#dismiss', version='1.3.0')
POLICY = _declare('policy', 'GET', '/api/v2/notifications/policy',
                  'read:notifications', '#get-policy', version='4.3.0')
UPDATE_POLICY = _declare(
    'update_policy', 'PATCH', '/api/v2/notifications/policy',
    'write:notifications', '#update-the-filtering-policy',
    note='DEVIATES (audit NT-6): the changed `for_*` fields go as a JSON '
         'body, the docs say form data; fixed in phase 5. The request '
         'names are `for_*` while the docs example response shows '
         '`filter_*` booleans; Babble sends and reads `for_*` (audit '
         'item 7)',
    version='4.3.0')
REQUESTS = _declare('requests', 'GET', '/notifications/requests',
                    'read:notifications', '#get-requests',
                    note='Link paged', version='4.3.0')
ACCEPT_REQUEST = _declare(
    'accept_request', 'POST', '/notifications/requests/%s/accept',
    'write:notifications', '#accept-request', version='4.3.0')
DISMISS_REQUEST = _declare(
    'dismiss_request', 'POST', '/notifications/requests/%s/dismiss',
    'write:notifications', '#dismiss-request', version='4.3.0')

# The two answers to a filtered-notification request, picked by
# `answer_request_async`.
ACTIONS = {'accept': ACCEPT_REQUEST, 'dismiss': DISMISS_REQUEST}

# Reads a paged view takes by path: `list_path('requests')`.
LISTS = {'requests': REQUESTS}


def list_path(kind, *ids):
    """The path of a paged read (``requests``) for the views that take a
    path rather than send a call."""
    return LISTS[kind].path % ids


def index_async(client, params=None, callback=None, cancellable=None):
    """One page; *params* carries the cursor, ``limit`` and the type
    filters."""
    return core.send(client, INDEX, params=params, callback=callback,
                     cancellable=cancellable)


def index_finish(client, result):
    return core.finish(client, result)


def unread_count_async(client, params=None, callback=None,
                       cancellable=None):
    return core.send(client, UNREAD_COUNT, params=params, callback=callback,
                     cancellable=cancellable)


def unread_count_finish(client, result):
    """The count, 0 when the answer carries none."""
    return core.finish(client, result).get('count', 0)


def clear_async(client, callback=None, cancellable=None):
    return core.send(client, CLEAR, callback=callback,
                     cancellable=cancellable)


def clear_finish(client, result):
    return core.finish(client, result)


def dismiss_async(client, notification_id, callback=None, cancellable=None):
    return core.send(client, DISMISS, notification_id, callback=callback,
                     cancellable=cancellable)


def dismiss_finish(client, result):
    return core.finish(client, result)


def policy_async(client, callback=None, cancellable=None):
    return core.send(client, POLICY, callback=callback,
                     cancellable=cancellable)


def policy_finish(client, result):
    return core.finish(client, result)


def update_policy_async(client, changes, callback=None, cancellable=None):
    """Send the changed ``for_*`` fields in *changes* as a JSON body."""
    return core.send(client, UPDATE_POLICY, json_body=changes,
                     callback=callback, cancellable=cancellable)


def update_policy_finish(client, result):
    return core.finish(client, result)


def answer_request_async(client, request_id, operation, callback=None,
                         cancellable=None):
    """Send *operation* (``accept`` or ``dismiss``, a key of ``ACTIONS``)
    on a filtered-notification request; anything else raises KeyError."""
    return core.send(client, ACTIONS[operation], request_id,
                     callback=callback, cancellable=cancellable)


def answer_request_finish(client, result):
    return core.finish(client, result)
