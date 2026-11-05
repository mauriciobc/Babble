# SPDX-License-Identifier: GPL-3.0-or-later
"""Relationship endpoints (audit rows AC-9..AC-16, RL-1..RL-7), declared
once and sent through `api.core`.

The verbs a profile builds from a variable (`follow`, `block`, `mute`,
`endorse`, ...) go through :func:`action_async`, which checks the verb
against the table below, so a UI never formats ``/accounts/%s/%s``.

Carried over from the audit, not fixed here (phase 5): ``DELETE
/domain_blocks`` sends ``domain`` in the query, the docs say form data.
``endorse`` is gated by the caller on ``ENDORSE_API_VERSION``, which the
audit marks as unverified; the 404 fallback from ``endorse`` to the
deprecated ``pin`` stays in the caller, which has to see the 404 itself.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/'
_ACCOUNTS = DOCS + 'accounts/'


def _declare(name, method, path, scope, docs, note=None, deprecated=None,
             version=None):
    return core.declare('relationships.' + name, method, path, scope, None,
                        docs, deprecated=deprecated, note=note,
                        mastodon_version=version)


def _account(name, scope, note=None, deprecated=None, version=None):
    return _declare(name, 'POST', '/accounts/%s/' + name, scope,
                    _ACCOUNTS + '#' + name, note=note, deprecated=deprecated,
                    version=version)


FOLLOW = _account('follow', 'write:follows',
                  note='form reblogs, notify (3.3.0); `languages[]` is '
                       'not sent; a locked account answers requested')
UNFOLLOW = _account('unfollow', 'write:follows')
REMOVE_FROM_FOLLOWERS = _account('remove_from_followers', 'write:follows',
                                 version='3.5.0')
BLOCK = _account('block', 'write:blocks')
UNBLOCK = _account('unblock', 'write:blocks')
MUTE = _account('mute', 'write:mutes',
                note='form notifications, duration (3.3.0)')
UNMUTE = _account('unmute', 'write:mutes')
ENDORSE = _account('endorse', 'write:accounts',
                   note='4.4.0 per the docs; the app gates it on '
                        'api_versions 6, which is unverified (audit AC-14)',
                   version='4.4.0')
UNENDORSE = _account('unendorse', 'write:accounts',
                     note='see endorse', version='4.4.0')
PIN = _account('pin', 'write:accounts', deprecated='endorse',
               note='deprecated 4.4.0; kept as the 404 fallback for older '
                    'servers')
UNPIN = _account('unpin', 'write:accounts', deprecated='unendorse',
                 note='deprecated 4.4.0; kept as the 404 fallback for '
                      'older servers')
NOTE = _account('note', 'write:accounts', note='form comment',
                version='3.2.0')

FOLLOW_REQUESTS = _declare(
    'follow_requests', 'GET', '/follow_requests', 'read:follows',
    DOCS + 'follow_requests/#get')
AUTHORIZE = _declare(
    'authorize', 'POST', '/follow_requests/%s/authorize', 'write:follows',
    DOCS + 'follow_requests/#accept')
REJECT = _declare(
    'reject', 'POST', '/follow_requests/%s/reject', 'write:follows',
    DOCS + 'follow_requests/#reject')
BLOCKS = _declare('blocks', 'GET', '/blocks', 'read:blocks',
                  DOCS + 'blocks/#get')
MUTES = _declare('mutes', 'GET', '/mutes', 'read:mutes',
                 DOCS + 'mutes/#get')
DOMAIN_BLOCKS = _declare('domain_blocks', 'GET', '/domain_blocks',
                         'read:blocks', DOCS + 'domain_blocks/#get',
                         version='1.4.0')
DOMAIN_BLOCK = _declare('domain_block', 'POST', '/domain_blocks',
                        'write:blocks', DOCS + 'domain_blocks/#block',
                        note='form domain', version='1.4.0')
DOMAIN_UNBLOCK = _declare(
    'domain_unblock', 'DELETE', '/domain_blocks', 'write:blocks',
    DOCS + 'domain_blocks/#unblock',
    note='DEVIATES (audit RL-7): `domain` is sent in the query, the docs '
         'say form data; fixed in phase 5', version='1.4.0')

# Operation -> endpoint, for the callers that pick the verb at run time (the
# `api boundary` test reads this table to accept a literal verb).
# `authorize` and `reject` are in it for completeness; the follow-request
# page reaches them through `answer_request_async`, which restricts to
# `ANSWERS`.
ACTIONS = {
    'follow': FOLLOW, 'unfollow': UNFOLLOW,
    'remove_from_followers': REMOVE_FROM_FOLLOWERS,
    'block': BLOCK, 'unblock': UNBLOCK, 'mute': MUTE, 'unmute': UNMUTE,
    'endorse': ENDORSE, 'unendorse': UNENDORSE, 'pin': PIN, 'unpin': UNPIN,
    'authorize': AUTHORIZE, 'reject': REJECT,
}
# The two answers to a follow request, picked by `answer_request_async`.
ANSWERS = ('authorize', 'reject')
LISTS = {
    'follow_requests': FOLLOW_REQUESTS, 'blocks': BLOCKS, 'mutes': MUTES,
    'domain_blocks': DOMAIN_BLOCKS,
}


def list_path(kind, *ids):
    """The path of a paged list (``follow_requests``, ``blocks``,
    ``mutes``, ``domain_blocks``) for the views that take a path; none of
    them has a placeholder, so *ids* is empty."""
    return LISTS[kind].path % ids


def action_endpoint(operation):
    """The endpoint of *operation*; an unknown one raises KeyError."""
    return ACTIONS[operation]


def action_async(client, account_id, operation, form=None, callback=None,
                 cancellable=None):
    """Send *operation* (a key of the table above) on an account; the
    answer is the relationship. An unknown operation raises KeyError."""
    return core.send(client, ACTIONS[operation], account_id, form=form,
                     callback=callback, cancellable=cancellable)


def action_finish(client, result):
    return core.finish(client, result)


def follow_async(client, account_id, reblogs=None, notify=None,
                 callback=None, cancellable=None):
    """Follow. The form carries only what the caller states: ``reblogs``
    and ``notify`` are booleans, None leaves the server's default.

    Unlike the old ``MastodonClient.follow_async``, which always sent
    ``reblogs`` (default true) and ``notify`` (default false), this sends
    only the keys given, and with none given sends no form at all."""
    form = {}
    if reblogs is not None:
        form['reblogs'] = 'true' if reblogs else 'false'
    if notify is not None:
        form['notify'] = 'true' if notify else 'false'
    return core.send(client, FOLLOW, account_id, form=form or None,
                     callback=callback, cancellable=cancellable)


def follow_finish(client, result):
    return core.finish(client, result)


def unfollow_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, UNFOLLOW, account_id, callback=callback,
                     cancellable=cancellable)


def unfollow_finish(client, result):
    return core.finish(client, result)


def block_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, BLOCK, account_id, callback=callback,
                     cancellable=cancellable)


def block_finish(client, result):
    return core.finish(client, result)


def mute_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, MUTE, account_id, callback=callback,
                     cancellable=cancellable)


def mute_finish(client, result):
    return core.finish(client, result)


def unmute_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, UNMUTE, account_id, callback=callback,
                     cancellable=cancellable)


def unmute_finish(client, result):
    return core.finish(client, result)


def unblock_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, UNBLOCK, account_id, callback=callback,
                     cancellable=cancellable)


def unblock_finish(client, result):
    return core.finish(client, result)


def note_async(client, account_id, comment, callback=None,
               cancellable=None):
    return core.send(client, NOTE, account_id, form={'comment': comment},
                     callback=callback, cancellable=cancellable)


def note_finish(client, result):
    return core.finish(client, result)


def answer_request_async(client, account_id, operation, callback=None,
                         cancellable=None):
    """Answer a follow request: *operation* is ``authorize`` or ``reject``
    (keys of ``ACTIONS``); anything else raises KeyError."""
    if operation not in ANSWERS:
        raise KeyError(operation)
    return core.send(client, ACTIONS[operation], account_id,
                     callback=callback, cancellable=cancellable)


def answer_request_finish(client, result):
    return core.finish(client, result)


def domain_block_async(client, domain, callback=None, cancellable=None):
    return core.send(client, DOMAIN_BLOCK, form={'domain': domain},
                     callback=callback, cancellable=cancellable)


def domain_block_finish(client, result):
    return core.finish(client, result)


def domain_unblock_async(client, domain, callback=None, cancellable=None):
    return core.send(client, DOMAIN_UNBLOCK, params={'domain': domain},
                     callback=callback, cancellable=cancellable)


def domain_unblock_finish(client, result):
    return core.finish(client, result)
