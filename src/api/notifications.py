# SPDX-License-Identifier: GPL-3.0-or-later
"""Notification endpoints (audit rows NT-1..NT-8; the reading marker, NT-9
and NT-10, is `markers`), declared once and sent through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the notification page, its controller, the desktop
poller and the policy dialog sent before: same verb, path, params and body.

Carried over from the audit, not fixed here (phase 5): ``PATCH
/api/v2/notifications/policy`` sends the changed fields as a JSON body, the
docs say form data (NT-6).

The 4.3.0 endpoints (``unread_count``, the policy and the requests) carry
`min_version` 2, the number v4.3.0 reports; the page asks
``capabilities.NOTIFICATION_POLICY_API_VERSION`` before the policy, the
count and the Settings entry (audit 5c). It is written out here because
this package may import nothing but the standard library and `core`;
`api registry` holds it equal to the constant. The others are older than
`api_versions` and stay None.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/notifications/'

# Mirrors capabilities.NOTIFICATION_POLICY_API_VERSION (see above).
NOTIFICATION_POLICY = 2


def _declare(name, method, path, scope, anchor, note=None, version=None,
             gate=None):
    return core.declare('notifications.' + name, method, path, scope, gate,
                        DOCS + anchor, note=note, mastodon_version=version)


INDEX = _declare('index', 'GET', '/notifications', 'read:notifications',
                 '#get', note='types[], exclude_types[], account_id, '
                 'include_filtered (4.3.0), ids and limit 40/80',
                 version='1.0.0')
UNREAD_COUNT = _declare('unread_count', 'GET', '/notifications/unread_count',
                        'read:notifications', '#unread-count',
                        note='types[] and exclude_types[], as the list',
                        version='4.3.0', gate=NOTIFICATION_POLICY)
CLEAR = _declare('clear', 'POST', '/notifications/clear',
                 'write:notifications', '#clear', version='1.0.0')
DISMISS = _declare('dismiss', 'POST', '/notifications/%s/dismiss',
                   'write:notifications', '#dismiss', version='1.3.0')
POLICY = _declare('policy', 'GET', '/api/v2/notifications/policy',
                  'read:notifications', '#get-policy',
                  version='4.3.0', gate=NOTIFICATION_POLICY)
UPDATE_POLICY = _declare(
    'update_policy', 'PATCH', '/api/v2/notifications/policy',
    'write:notifications', '#update-the-filtering-policy',
    note='DEVIATES (audit NT-6): the changed `for_*` fields go as a JSON '
         'body, the docs say form data; fixed in phase 5. The request '
         'names are `for_*` while the docs example response shows '
         '`filter_*` booleans; Babble sends and reads `for_*` (audit '
         'item 7)',
    version='4.3.0',
    gate=NOTIFICATION_POLICY)
REQUESTS = _declare('requests', 'GET', '/notifications/requests',
                    'read:notifications', '#get-requests',
                    note='Link paged', version='4.3.0',
                    gate=NOTIFICATION_POLICY)
ACCEPT_REQUEST = _declare(
    'accept_request', 'POST', '/notifications/requests/%s/accept',
    'write:notifications', '#accept-request', version='4.3.0',
    gate=NOTIFICATION_POLICY)
DISMISS_REQUEST = _declare(
    'dismiss_request', 'POST', '/notifications/requests/%s/dismiss',
    'write:notifications', '#dismiss-request', version='4.3.0',
    gate=NOTIFICATION_POLICY)

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


# ``limit`` of unread_count: the docs default is 100 and the maximum 1000.
# Babble sends the default explicitly because the badge's "100+" is derived
# from it: a count that reaches it means "at least this many".
UNREAD_COUNT_LIMIT = 100


def unread_badge_text(count):
    """The badge's text: the exact count, or ``100+`` once the count
    reaches the limit the server counts up to, where it is a floor and not
    a number."""
    if count <= 0:
        return ''
    if count >= UNREAD_COUNT_LIMIT:
        return '%d+' % UNREAD_COUNT_LIMIT
    return str(count)


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
