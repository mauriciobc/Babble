# SPDX-License-Identifier: GPL-3.0-or-later
"""Status endpoints (audit rows ST-2..ST-18), declared once and sent
through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the old `MastodonClient` wrappers and the UI's
hand-built paths sent: same verb, path, form and (absent) params.

`POST /statuses` and `PUT /statuses/:id` (ST-1, ST-3) carry a JSON body,
which the docs show as a form: the audit records that as accepted, and the
note on each endpoint says so. `post`, `reply` and `edit` are theirs.

`min_version` is None throughout: these endpoints predate
`api_versions`, and the audit records no gate for the 4.5 ones (quote
policy, revoke, quotes list), so the release is named in ``note`` and
nothing here changes what the app asks of an instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/statuses/'


def _declare(name, method, path, scope, anchor, note=None, deprecated=None,
             version=None):
    return core.declare('statuses.' + name, method, path, scope, None,
                        DOCS + anchor, deprecated=deprecated, note=note,
                        mastodon_version=version)


_PUBLIC = 'public statuses need no token; others need read:statuses'

GET = _declare('get', 'GET', '/statuses/%s', None, '#get', note=_PUBLIC)
DELETE = _declare('delete', 'DELETE', '/statuses/%s', 'write:statuses',
                  '#delete',
                  note='delete_media (4.4.0) is never sent, on purpose: a '
                       'redraft needs the attachments to survive, and a '
                       'plain delete leaves them on the server for about '
                       '24 hours (audit item 9)')
CONTEXT = _declare('context', 'GET', '/statuses/%s/context', None,
                   '#context',
                   note=_PUBLIC + '. From 4.5.0 the response may carry the '
                   'experimental `Mastodon-Async-Refresh` header (a '
                   'background fetch of missing replies, polled through '
                   '/api/v1_alpha/async_refreshes). Babble does not read '
                   'it: the header is marked experimental in the docs and '
                   'the thread simply shows the replies the server holds '
                   'when asked, so a reply that arrives later is missed '
                   'until the thread is reopened (audit 5f)')
HISTORY = _declare('history', 'GET', '/statuses/%s/history', None,
                   '#history', note=_PUBLIC, version='3.5.0')
SOURCE = _declare('source', 'GET', '/statuses/%s/source', 'read:statuses',
                  '#source', version='3.5.0')
TRANSLATE = _declare('translate', 'POST', '/statuses/%s/translate',
                     'read:statuses', '#translate',
                     note='the instance must enable translation',
                     version='4.0.0')
FAVOURITE = _declare('favourite', 'POST', '/statuses/%s/favourite',
                     'write:favourites', '#favourite')
UNFAVOURITE = _declare('unfavourite', 'POST', '/statuses/%s/unfavourite',
                       'write:favourites', '#unfavourite')
REBLOG = _declare('reblog', 'POST', '/statuses/%s/reblog', 'write:statuses',
                  '#boost', note='optional form visibility (2.8.0)')
UNREBLOG = _declare('unreblog', 'POST', '/statuses/%s/unreblog',
                    'write:statuses', '#unreblog')
BOOKMARK = _declare('bookmark', 'POST', '/statuses/%s/bookmark',
                    'write:bookmarks', '#bookmark', version='3.1.0')
UNBOOKMARK = _declare('unbookmark', 'POST', '/statuses/%s/unbookmark',
                      'write:bookmarks', '#unbookmark',
                      version='3.1.0')
MUTE = _declare('mute', 'POST', '/statuses/%s/mute', 'write:mutes', '#mute',
                note='conversation mute', version='1.4.2')
UNMUTE = _declare('unmute', 'POST', '/statuses/%s/unmute', 'write:mutes',
                  '#unmute', note='conversation mute', version='1.4.2')
PIN = _declare('pin', 'POST', '/statuses/%s/pin', 'write:accounts', '#pin',
               version='1.6.0')
UNPIN = _declare('unpin', 'POST', '/statuses/%s/unpin', 'write:accounts',
                 '#unpin', version='1.6.0')
INTERACTION_POLICY = _declare(
    'interaction_policy', 'PUT', '/statuses/%s/interaction_policy',
    'write:statuses', '#update_interaction_policy',
    note='not version-gated by the caller: the menu entry needs the post '
         'to carry `quote_approval` (audit 5c)', version='4.5.0')
REVOKE_QUOTE = _declare(
    'revoke_quote', 'POST', '/statuses/%s/quotes/%s/revoke',
    'write:statuses', '#revoke_quote',
    note='not version-gated by the caller: only offered for a post whose '
         '`quote` is accepted (audit 5c); arguments are the quoted post '
         'id, then the quoting post id', version='4.5.0')
FAVOURITED_BY = _declare('favourited_by', 'GET', '/statuses/%s/favourited_by',
                         None, '#favourited_by', note=_PUBLIC)
REBLOGGED_BY = _declare('reblogged_by', 'GET', '/statuses/%s/reblogged_by',
                        None, '#reblogged_by', note=_PUBLIC)
QUOTES = _declare('quotes', 'GET', '/statuses/%s/quotes', 'read:statuses',
                  '#quotes',
                  note='not version-gated by the caller: the tab only shows '
                       'for a post with `quotes_count` (audit 5c)',
                  version='4.5.0')

CREATE = _declare(
    'create', 'POST', '/statuses', 'write:statuses', '#create',
    note='the app sends a JSON body, so nested values (poll options, '
         'media_ids arrays) keep their structure; the docs show a form. '
         'No Idempotency-Key, on purpose (see post_async); a repeated key '
         'returns the original status, so it would be safe to send')
EDIT = _declare(
    'edit', 'PUT', '/statuses/%s', 'write:statuses', '#edit',
    note='JSON body, as for create. The server permits only `status`, '
         '`spoiler_text`, `sensitive`, `language`, `media_ids`, '
         '`media_attributes`, `poll` and `quote_approval_policy` '
         '(statuses_controller#update at main and v4.3.0 to v4.5.0; the '
         'last from 4.5.0); '
         '`visibility`, `in_reply_to_id`, `quoted_status_id` and '
         '`scheduled_at` belong to create and are dropped by '
         '`edit_async` (audit 5f)', version='3.5.0')

# What PUT /statuses/:id reads (statuses_controller#update). The composer
# builds one draft for create and edit alike; an edit keeps only these.
EDIT_FIELDS = frozenset({
    'status', 'spoiler_text', 'sensitive', 'language', 'media_ids',
    'media_attributes', 'poll', 'quote_approval_policy'})

# The do/undo pairs a toggle button picks from: (kind, active) -> endpoint.
TOGGLES = {
    ('favourite', True): FAVOURITE, ('favourite', False): UNFAVOURITE,
    ('reblog', True): REBLOG, ('reblog', False): UNREBLOG,
    ('bookmark', True): BOOKMARK, ('bookmark', False): UNBOOKMARK,
}
# Verbs that act on a post and answer with the updated status.
ACTIONS = {
    'mute': MUTE, 'unmute': UNMUTE, 'pin': PIN, 'unpin': UNPIN,
}
# Stats tabs (who favourited, boosted, quoted): the list endpoint of each.
LISTS = {
    'favourites': FAVOURITED_BY, 'boosts': REBLOGGED_BY, 'quotes': QUOTES,
}


def _pair(name, endpoint):
    """`<name>_async` / `<name>_finish` for an endpoint that takes only the
    status id."""
    def start(client, status_id, callback=None, cancellable=None):
        return core.send(client, endpoint, status_id, callback=callback,
                         cancellable=cancellable)

    def finish(client, result):
        return core.finish(client, result)

    start.__name__, finish.__name__ = name + '_async', name + '_finish'
    return start, finish


get_async, get_finish = _pair('get', GET)
delete_async, delete_finish = _pair('delete', DELETE)
history_async, history_finish = _pair('history', HISTORY)
source_async, source_finish = _pair('source', SOURCE)
favourite_async, favourite_finish = _pair('favourite', FAVOURITE)
unfavourite_async, unfavourite_finish = _pair('unfavourite', UNFAVOURITE)
unreblog_async, unreblog_finish = _pair('unreblog', UNREBLOG)
bookmark_async, bookmark_finish = _pair('bookmark', BOOKMARK)
unbookmark_async, unbookmark_finish = _pair('unbookmark', UNBOOKMARK)
mute_async, mute_finish = _pair('mute', MUTE)
unmute_async, unmute_finish = _pair('unmute', UNMUTE)
pin_async, pin_finish = _pair('pin', PIN)
unpin_async, unpin_finish = _pair('unpin', UNPIN)


def context_async(client, status_id, callback=None, cancellable=None):
    return core.send(client, CONTEXT, status_id, callback=callback,
                     cancellable=cancellable)


def context_finish(client, result):
    """``(ancestors, descendants)``."""
    data = core.finish(client, result)
    return data.get('ancestors', []), data.get('descendants', [])


def reblog_async(client, status_id, visibility=None, callback=None,
                 cancellable=None):
    """*visibility* is ``public`` or ``private``; the server's default
    applies when it is absent."""
    form = {'visibility': visibility} if visibility else None
    return core.send(client, REBLOG, status_id, form=form,
                     callback=callback, cancellable=cancellable)


def reblog_finish(client, result):
    return core.finish(client, result)


def translate_async(client, status_id, lang, callback=None,
                    cancellable=None):
    return core.send(client, TRANSLATE, status_id, form={'lang': lang},
                     callback=callback, cancellable=cancellable)


def translate_finish(client, result):
    return core.finish(client, result)


def interaction_policy_async(client, status_id, quote_approval_policy,
                             callback=None, cancellable=None):
    return core.send(
        client, INTERACTION_POLICY, status_id,
        form={'quote_approval_policy': quote_approval_policy},
        callback=callback, cancellable=cancellable)


def interaction_policy_finish(client, result):
    return core.finish(client, result)


def revoke_quote_async(client, quoted_id, status_id, callback=None,
                       cancellable=None):
    return core.send(client, REVOKE_QUOTE, quoted_id, status_id,
                     callback=callback, cancellable=cancellable)


def revoke_quote_finish(client, result):
    return core.finish(client, result)


def toggle_async(client, status_id, kind, active, form=None, callback=None,
                 cancellable=None):
    """Set *kind* (``favourite``, ``reblog`` or ``bookmark``) to *active*
    with its do or undo endpoint. An unknown kind raises KeyError."""
    return core.send(client, TOGGLES[(kind, active)], status_id, form=form,
                     callback=callback, cancellable=cancellable)


def toggle_finish(client, result):
    return core.finish(client, result)


def post_action_async(client, status_id, operation, callback=None,
                      cancellable=None):
    """*operation* is a key of ``ACTIONS`` (``mute``, ``unmute``, ``pin``
    or ``unpin``); anything else raises KeyError."""
    return core.send(client, ACTIONS[operation], status_id,
                     callback=callback, cancellable=cancellable)


def post_action_finish(client, result):
    return core.finish(client, result)


def list_path(kind, *ids):
    """The path of a stats list (``favourites``, ``boosts``, ``quotes``)
    for the paged views, which take a path rather than send a call; *ids*
    fill the path's placeholders (the status id)."""
    return LISTS[kind].path % ids


def post_async(client, text, params=None, callback=None, cancellable=None):
    """POST /api/v1/statuses as JSON, so nested values (``poll``
    options, ``media_ids`` arrays) keep their structure.

    No ``Idempotency-Key`` is sent, and that is deliberate, but not
    because a repeat answers with something that is not a status (an
    earlier version of this note said so, wrongly). Verified in
    PostStatusService at main and at v4.3.0, v4.4.0 and v4.5.0: the key is
    stored per account in Redis for an hour (``idempotency:status:<account
    id>:<key>``, set only after the status was created), and a repeat
    returns the *original* Status (or ScheduledStatus), found by id with
    ``@account.statuses.find`` / ``scheduled_statuses.find``. At 4.3 to
    4.5 that is ``return idempotency_duplicate``; at main it is
    ``raise IdempotencyError, idempotency_duplicate`` rescued to
    ``e.status``, the same record, and the check runs under a Redis lock.
    So sending a key would be safe on every current version, with one
    difference: before the lock (4.3 to 4.5) two simultaneous requests with
    one key can both create. It is still not sent because a retry here is
    decided by :data:`IDEMPOTENT_METHODS`, which excludes POST: nothing
    would repeat the request, so the key would have no effect until the
    transport learned to retry a POST that carries one (audit 5f).
    """
    body = {'status': text}
    if params:
        body.update(params)
    return core.send(client, CREATE, json_body=body, callback=callback,
                     cancellable=cancellable)


def post_finish(client, result):
    return core.finish(client, result)


def reply_async(client, status_id, text, params=None, callback=None,
                cancellable=None):
    """POST /api/v1/statuses with ``in_reply_to_id``.

    A reply is a status posted to the same endpoint; this names the
    intent so a caller does not have to spell out the parent id.
    """
    body = dict(params or {})
    body['in_reply_to_id'] = str(status_id)
    return post_async(client, text, body, callback=callback,
                      cancellable=cancellable)


def reply_finish(client, result):
    return post_finish(client, result)


def edit_async(client, status_id, text, params=None, callback=None,
               cancellable=None):
    """PUT /api/v1/statuses/:id as JSON, mirroring :func:`post_async`
    (docs.joinmastodon.org/methods/statuses). Keys outside
    :data:`EDIT_FIELDS` are dropped: the server ignores them, and sending
    them suggests an edit can change a post's visibility or thread."""
    body = {'status': text}
    if params:
        body.update({k: v for k, v in params.items() if k in EDIT_FIELDS})
    return core.send(client, EDIT, status_id, json_body=body,
                     callback=callback, cancellable=cancellable)


def edit_finish(client, result):
    return core.finish(client, result)
