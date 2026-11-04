# SPDX-License-Identifier: GPL-3.0-or-later
"""Account endpoints (audit rows AC-1..AC-8, AC-17..AC-20), declared once
and sent through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair. The request on
the wire is the one the old `MastodonClient` wrappers and the UI's
hand-built paths sent.

Left for later phases, on purpose: `PATCH /accounts/update_credentials` and
`PATCH /profile` (phase 3: they carry multipart bodies and the profile
editor), `GET /accounts/:id/lists` (phase 2: lists) and the collections
(phase 3).

`min_version` is None throughout: the audit records no gate in the app
for these, so the release is named in ``note`` and nothing here changes
what the app asks of an instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/accounts/'


def _declare(name, method, path, scope, anchor, note=None):
    return core.declare('accounts.' + name, method, path, scope, None,
                        DOCS + anchor, note=note)


_PUBLIC = 'public; no token needed'

GET = _declare('get', 'GET', '/accounts/%s', None, '#get', note=_PUBLIC)
VERIFY_CREDENTIALS = _declare(
    'verify_credentials', 'GET', '/accounts/verify_credentials',
    'read:accounts', '#verify_credentials',
    note='the `profile` scope also works (4.3.0)')
LOOKUP = _declare('lookup', 'GET', '/accounts/lookup', 'read:accounts',
                  '#lookup', note='Mastodon 3.4.0')
BY_IDS = _declare('by_ids', 'GET', '/accounts', None, '#index',
                  note=_PUBLIC + '; Mastodon 4.3.0; not version-gated by '
                       'the caller (an error is an empty answer)')
STATUSES = _declare(
    'statuses', 'GET', '/accounts/%s/statuses', None, '#statuses',
    note='public statuses need no token; others need read:statuses')
FOLLOWERS = _declare('followers', 'GET', '/accounts/%s/followers', None,
                     '#followers', note=_PUBLIC)
FOLLOWING = _declare('following', 'GET', '/accounts/%s/following', None,
                     '#following', note=_PUBLIC)
FEATURED_TAGS = _declare('featured_tags', 'GET',
                         '/accounts/%s/featured_tags', None,
                         '#featured_tags', note=_PUBLIC + '; Mastodon 3.3.0')
ENDORSEMENTS = _declare(
    'endorsements', 'GET', '/accounts/%s/endorsements', None,
    '#endorsements',
    note=_PUBLIC + '; Mastodon 4.4.0; not version-gated by the caller')
FAMILIAR_FOLLOWERS = _declare(
    'familiar_followers', 'GET', '/accounts/familiar_followers',
    'read:follows', '#familiar_followers', note='Mastodon 3.5.0')
RELATIONSHIPS = _declare('relationships', 'GET', '/accounts/relationships',
                         'read:follows', '#relationships',
                         note='`with_suspended` (4.3.0) is not sent')
SEARCH = _declare('search', 'GET', '/accounts/search', 'read:accounts',
                  '#search', note='limit is at most 80')

# Lists of accounts or posts a paged view reads, by the name the UI uses.
# `LISTS` and `PATH_ONLY` are what reaches the wire through a path accessor
# below and a view that hard-codes GET; `api registry` requires GET of both.
LISTS = {
    'followers': FOLLOWERS, 'following': FOLLOWING,
    'endorsements': ENDORSEMENTS, 'statuses': STATUSES,
}
PATH_ONLY = (FAMILIAR_FOLLOWERS, SEARCH)


def list_path(kind, account_id):
    """The path of a paged list of one account (``followers``,
    ``following``, ``endorsements``, ``statuses``) for the views that take
    a path rather than send a call."""
    return LISTS[kind].path % account_id


def familiar_followers_path():
    """`GET /accounts/familiar_followers`, for the paged view (it passes
    ``id[]`` itself)."""
    return FAMILIAR_FOLLOWERS.path


def search_path():
    """`GET /accounts/search`, for a completer that returns the path and
    parameters of the request the shared completer then sends."""
    return SEARCH.path


def get_async(client, account_id, callback=None, cancellable=None):
    return core.send(client, GET, account_id, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def verify_credentials_async(client, callback=None, cancellable=None):
    return core.send(client, VERIFY_CREDENTIALS, callback=callback,
                     cancellable=cancellable)


def verify_credentials_finish(client, result):
    """The account; an answer without an id is an error, not an account."""
    data = core.finish(client, result)
    account_id = data.get('id') if isinstance(data, dict) else None
    if not isinstance(account_id, str) or not account_id:
        raise client.invalid_response('account')
    return data


def lookup_async(client, acct, callback=None, cancellable=None):
    return core.send(client, LOOKUP, params={'acct': acct},
                     callback=callback, cancellable=cancellable)


def lookup_finish(client, result):
    return core.finish(client, result)


def by_ids_async(client, ids, callback=None, cancellable=None):
    return core.send(client, BY_IDS, params={'id[]': ids},
                     callback=callback, cancellable=cancellable)


def by_ids_finish(client, result):
    return core.finish(client, result)


def statuses_async(client, account_id, params=None, callback=None,
                   cancellable=None):
    return core.send(client, STATUSES, account_id, params=params,
                     callback=callback, cancellable=cancellable)


def statuses_finish(client, result):
    return core.finish(client, result)


def following_async(client, account_id, params=None, callback=None,
                    cancellable=None):
    return core.send(client, FOLLOWING, account_id, params=params,
                     callback=callback, cancellable=cancellable)


def following_finish(client, result):
    return core.finish(client, result)


def featured_tags_async(client, account_id, callback=None,
                        cancellable=None):
    return core.send(client, FEATURED_TAGS, account_id, callback=callback,
                     cancellable=cancellable)


def featured_tags_finish(client, result):
    return core.finish(client, result)


def endorsements_async(client, account_id, params=None, callback=None,
                       cancellable=None):
    return core.send(client, ENDORSEMENTS, account_id, params=params,
                     callback=callback, cancellable=cancellable)


def endorsements_finish(client, result):
    return core.finish(client, result)


def familiar_followers_async(client, account_ids, callback=None,
                             cancellable=None):
    return core.send(client, FAMILIAR_FOLLOWERS,
                     params={'id[]': account_ids}, callback=callback,
                     cancellable=cancellable)


def familiar_followers_finish(client, result):
    return core.finish(client, result)


def relationships_async(client, account_ids, callback=None,
                        cancellable=None):
    return core.send(client, RELATIONSHIPS, params={'id[]': account_ids},
                     callback=callback, cancellable=cancellable)


def relationships_finish(client, result):
    """The first relationship, for a page that asked about one account.
    A caller that asked about several reads the whole list with
    :func:`relationships_all_finish`."""
    data = core.finish(client, result)
    return data[0] if data else {}


def relationships_all_finish(client, result):
    """Every relationship the server returned."""
    return core.finish(client, result)


def search_async(client, params, callback=None, cancellable=None):
    return core.send(client, SEARCH, params=params, callback=callback,
                     cancellable=cancellable)


def search_finish(client, result):
    return core.finish(client, result)
