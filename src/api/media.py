# SPDX-License-Identifier: GPL-3.0-or-later
"""Media attachments (audit rows MD-1, MD-2), declared once and sent
through `api.core`.

The upload is the one multipart call the app makes to a content endpoint,
so it goes through :func:`core.send_multipart`.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/media/'

UPLOAD = core.declare(
    'media.upload', 'POST', '/api/v2/media', 'write:media', None,
    DOCS + '#v2',
    note='multipart/form-data with a `file` part and optional `description` '
         'alt text (`thumbnail` and `focus` are not sent). Answers HTTP 202 '
         'with a `url` of null while the full-size file is processed in the '
         'background; the composer polls media.status until it appears. '
         'The v1 route it replaced has been deprecated since Mastodon 3.1.3',
    mastodon_version='3.1.3')
STATUS = core.declare(
    'media.status', 'GET', '/media/%s', 'write:media', None,
    DOCS + '#get',
    note='polls an upload being processed (HTTP 206 while it is)',
    mastodon_version='3.1.3')


def upload_async(client, file_bytes, filename, content_type,
                 description=None, callback=None, cancellable=None):
    """Upload one attachment to ``POST /api/v2/media``.

    *file_bytes* may be a :class:`GLib.Bytes` or a ``bytes``; pass
    the former when the caller already holds one, so the attachment
    is not copied twice on the way out."""
    fields = {'description': description} if description else None
    return core.send_multipart(
        client, UPLOAD, fields=fields,
        files={'file': (file_bytes, filename, content_type)},
        callback=callback, cancellable=cancellable)


def upload_finish(client, result):
    return core.finish(client, result)


def status_async(client, media_id, callback=None, cancellable=None):
    """GET /api/v1/media/:id, to poll an upload being processed."""
    return core.send(client, STATUS, media_id, callback=callback,
                     cancellable=cancellable)


def status_finish(client, result):
    return core.finish(client, result)
