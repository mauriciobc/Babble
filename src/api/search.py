# SPDX-License-Identifier: GPL-3.0-or-later
"""Search (audit row SR-1), declared once and sent through `api.core`.

One call, :func:`search_async`, serves the search page, its per-type pages,
the link and account lookups, the quote paste and the search provider:
they differ only in the parameters (``type``, ``resolve``, ``limit``,
``offset``), which the caller states. The request on the wire is the one
each of them sent before.

`min_version` is None: the app gates nothing on it, so the release is in
``mastodon_version``.
"""

from . import core

SEARCH = core.declare(
    'search.search', 'GET', '/api/v2/search', 'read:search', None,
    'https://docs.joinmastodon.org/methods/search/#v2',
    note='public, except `resolve` and `offset`, which need a user token; '
         'limit is at most 40',
    mastodon_version='2.4.1')

# Served to a completer by `search_path`; `api registry` requires GET.
PATH_ONLY = (SEARCH,)


def search_path():
    """`GET /api/v2/search`, for a completer that returns the path and
    parameters of the request the shared completer then sends."""
    return SEARCH.path


def search_async(client, q, callback=None, cancellable=None, **params):
    """Search for *q*; *params* are the other query parameters (``type``,
    ``resolve``, ``limit``, ``offset``, ...), sent as given, except an
    ``offset`` of 0: the docs say any ``offset`` needs a user token (401
    without one), so the first page leaves it out."""
    params = dict(params, q=q)
    if not params.get('offset'):
        params.pop('offset', None)
    return core.send(client, SEARCH, params=params,
                     callback=callback, cancellable=cancellable)


def search_finish(client, result):
    return core.finish(client, result)
