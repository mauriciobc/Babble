# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint declarations, the registry, and the one function that sends.

An endpoint is declared once, at import time, with everything the docs say
about it. A resource module's wrapper then calls `send()` with that
declaration, so the method and path are never typed twice and
`Docs/mastodon-compat.md` can be generated from `registry`.

`send()` calls the PUBLIC `MastodonClient.request_async`, which forwards
unchanged to `_request_async`, and that reaches `_spawn`, the chokepoint
the `api golden` recorder watches: a request sent here is byte-identical to
the one the old `<name>_async` wrapper sent. The wrappers use the private
`_request_async` only for `auth=False` (the OAuth app and token calls),
which the public method does not offer; those calls are not API endpoints
of this layer and stay in `mastodon.py`.

A body is JSON (``json_body``), form-encoded (``form``) or multipart
(:func:`send_multipart`); the caller picks, and an endpoint's ``note`` says
which forms the app sends when the docs name another.

No version or scope gating happens here: a caller asks
`client.is_supported(endpoint.min_version)` before it sends.
"""

import dataclasses
from typing import Optional


@dataclasses.dataclass(frozen=True)
class Endpoint:
    """One Mastodon REST endpoint, as the documentation describes it.

    ``path`` is a template in the code base's ``%`` style (``%s`` for ids,
    ``%d`` for numbers), relative to /api/v1 or a full ``/api/v2/...``.
    ``scope`` is the OAuth scope the docs require, or None for a public
    endpoint (then ``note`` says so). ``min_version`` is the
    ``api_versions.mastodon`` that added it, or None when unversioned.
    ``deprecated`` names the replacement when the docs deprecate it.
    """
    name: str
    method: str
    path: str
    scope: Optional[str]
    min_version: Optional[int]
    docs: str
    deprecated: Optional[str] = None
    note: Optional[str] = None
    mastodon_version: Optional[str] = None


registry = {}


def declare(name, method, path, scope, min_version, docs, deprecated=None,
            note=None, into=None, mastodon_version=None):
    """Create an `Endpoint` and register it under ``name``.

    A duplicate name raises ValueError at import time, so two modules cannot
    quietly declare the same endpoint. ``into`` is for tests, which use a
    registry of their own."""
    target = registry if into is None else into
    if name in target:
        raise ValueError('endpoint %r is declared twice' % name)
    endpoint = Endpoint(name, method, path, scope, min_version, docs,
                        deprecated, note, mastodon_version)
    target[name] = endpoint
    return endpoint


def send(client, endpoint, *path_args, params=None, form=None,
         json_body=None, callback=None, cancellable=None):
    """Format the path with ``path_args`` and queue the call.

    Returns the Gio.Task, like ``request_async``; finish it with
    :func:`finish`. ``json_body`` is the keyword ``request_async`` takes."""
    path = endpoint.path % path_args if path_args else endpoint.path
    return client.request_async(endpoint.method, path, params=params,
                                form=form, json_body=json_body,
                                callback=callback, cancellable=cancellable)


def send_path(client, method, path, params=None, callback=None,
              cancellable=None):
    """Queue a call whose path the CALLER supplies, not a declaration.

    This is the one place outside ``mastodon.py`` where a path that is not
    a declared :class:`Endpoint` is sent. It exists for the generic helpers
    (``pages.PagedList``, ``timelinepage.ApiTimelineSource`` and the
    completers' ``search_request`` hook), which page or search whatever
    path their caller hands over. That path comes from a registered
    accessor (``list_path(kind, ...)``, ``search_path()``, ...) or from the
    helper's own subclass, and ``api boundary`` checks the caller where it
    names the path. Nothing else may call this with a literal or a
    template: ``api boundary`` fails on it, and allows a variable path only
    in the three files that hold those helpers. Finish with :func:`finish`
    or :func:`finish_paged`."""
    return client.request_async(method, path, params=params,
                                callback=callback, cancellable=cancellable)


def send_multipart(client, endpoint, *path_args, fields=None, files=None,
                   callback=None, cancellable=None):
    """Format the path with ``path_args`` and queue a multipart/form-data
    call through the client's public ``multipart_async``, which stays on
    the client as transport like ``request_async``.

    ``fields`` maps names to strings (a list repeats the key); ``files``
    maps names to ``(bytes_or_GLib.Bytes, filename, content_type)``. Finish
    with :func:`finish`."""
    path = endpoint.path % path_args if path_args else endpoint.path
    return client.multipart_async(endpoint.method, path, fields=fields,
                                  files=files, callback=callback,
                                  cancellable=cancellable)


def finish(client, result):
    """The decoded JSON body; raises GLib.Error on HTTP errors."""
    return client.request_finish(result)


def finish_paged(client, result):
    """``(data, next_params, prev_params)``: the decoded body and the query
    parameters of the ``Link`` header's next and prev URLs (None when
    absent), for a read a caller pages through."""
    return client.request_finish_paged(result)
