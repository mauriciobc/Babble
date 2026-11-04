# SPDX-License-Identifier: GPL-3.0-or-later
"""Status endpoints (audit rows ST-2..ST-18), declared once and sent
through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the old `MastodonClient` wrappers and the UI's
hand-built paths sent: same verb, path, form and (absent) params.

`POST /statuses`, `PUT /statuses/:id`, the media upload and the reply
helper (ST-1, ST-3) stay on `MastodonClient` until phase 3: they carry a
JSON body, which the docs show as a form.

`min_version` is None throughout: these endpoints predate
`api_versions`, and the audit records no gate for the 4.5 ones (quote
policy, revoke, quotes list), so the release is named in ``note`` and
nothing here changes what the app asks of an instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/statuses/'


def _declare(name, method, path, scope, anchor, note=None, deprecated=None):
    return core.declare('statuses.' + name, method, path, scope, None,
                        DOCS + anchor, deprecated=deprecated, note=note)


_PUBLIC = 'public statuses need no token; others need read:statuses'

GET = _declare('get', 'GET', '/statuses/%s', None, '#get', note=_PUBLIC)
DELETE = _declare('delete', 'DELETE', '/statuses/%s', 'write:statuses',
                  '#delete',
                  note='delete_media (4.4.0) is not sent, on purpose: a '
                       'redraft keeps the attachments')
CONTEXT = _declare('context', 'GET', '/statuses/%s/context', None,
                   '#context', note=_PUBLIC)
HISTORY = _declare('history', 'GET', '/statuses/%s/history', None,
                   '#history', note=_PUBLIC + '; Mastodon 3.5.0')
SOURCE = _declare('source', 'GET', '/statuses/%s/source', 'read:statuses',
                  '#source', note='Mastodon 3.5.0')
TRANSLATE = _declare('translate', 'POST', '/statuses/%s/translate',
                     'read:statuses', '#translate',
                     note='Mastodon 4.0.0; the instance must enable '
                          'translation')
FAVOURITE = _declare('favourite', 'POST', '/statuses/%s/favourite',
                     'write:favourites', '#favourite')
UNFAVOURITE = _declare('unfavourite', 'POST', '/statuses/%s/unfavourite',
                       'write:favourites', '#unfavourite')
REBLOG = _declare('reblog', 'POST', '/statuses/%s/reblog', 'write:statuses',
                  '#boost', note='optional form visibility (2.8.0)')
UNREBLOG = _declare('unreblog', 'POST', '/statuses/%s/unreblog',
                    'write:statuses', '#unreblog')
BOOKMARK = _declare('bookmark', 'POST', '/statuses/%s/bookmark',
                    'write:bookmarks', '#bookmark', note='Mastodon 3.1.0')
UNBOOKMARK = _declare('unbookmark', 'POST', '/statuses/%s/unbookmark',
                      'write:bookmarks', '#unbookmark',
                      note='Mastodon 3.1.0')
MUTE = _declare('mute', 'POST', '/statuses/%s/mute', 'write:mutes', '#mute',
                note='conversation mute; Mastodon 1.4.2')
UNMUTE = _declare('unmute', 'POST', '/statuses/%s/unmute', 'write:mutes',
                  '#unmute', note='conversation mute; Mastodon 1.4.2')
PIN = _declare('pin', 'POST', '/statuses/%s/pin', 'write:accounts', '#pin',
               note='Mastodon 1.6.0')
UNPIN = _declare('unpin', 'POST', '/statuses/%s/unpin', 'write:accounts',
                 '#unpin', note='Mastodon 1.6.0')
INTERACTION_POLICY = _declare(
    'interaction_policy', 'PUT', '/statuses/%s/interaction_policy',
    'write:statuses', '#update_interaction_policy',
    note='Mastodon 4.5.0; not version-gated by the caller')
REVOKE_QUOTE = _declare(
    'revoke_quote', 'POST', '/statuses/%s/quotes/%s/revoke',
    'write:statuses', '#revoke_quote',
    note='Mastodon 4.5.0; not version-gated by the caller; arguments are '
         'the quoted post id, then the quoting post id')
FAVOURITED_BY = _declare('favourited_by', 'GET', '/statuses/%s/favourited_by',
                         None, '#favourited_by', note=_PUBLIC)
REBLOGGED_BY = _declare('reblogged_by', 'GET', '/statuses/%s/reblogged_by',
                        None, '#reblogged_by', note=_PUBLIC)
QUOTES = _declare('quotes', 'GET', '/statuses/%s/quotes', 'read:statuses',
                  '#quotes',
                  note='Mastodon 4.5.0; not version-gated by the caller')

# The do/undo pairs a toggle button picks from: (kind, active) -> endpoint.
_TOGGLES = {
    ('favourite', True): FAVOURITE, ('favourite', False): UNFAVOURITE,
    ('reblog', True): REBLOG, ('reblog', False): UNREBLOG,
    ('bookmark', True): BOOKMARK, ('bookmark', False): UNBOOKMARK,
}
# Verbs that act on a post and answer with the updated status.
_POST_ACTIONS = {
    'mute': MUTE, 'unmute': UNMUTE, 'pin': PIN, 'unpin': UNPIN,
}
# Stats tabs (who favourited, boosted, quoted): the list endpoint of each.
_LISTS = {
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
    return core.send(client, _TOGGLES[(kind, active)], status_id, form=form,
                     callback=callback, cancellable=cancellable)


def toggle_finish(client, result):
    return core.finish(client, result)


def post_action_async(client, status_id, action, callback=None,
                      cancellable=None):
    """``mute``, ``unmute``, ``pin`` or ``unpin``; anything else raises
    KeyError."""
    return core.send(client, _POST_ACTIONS[action], status_id,
                     callback=callback, cancellable=cancellable)


def post_action_finish(client, result):
    return core.finish(client, result)


def list_path(kind, status_id):
    """The path of a stats list (``favourites``, ``boosts``, ``quotes``)
    for the paged views, which take a path rather than send a call."""
    return _LISTS[kind].path % status_id
