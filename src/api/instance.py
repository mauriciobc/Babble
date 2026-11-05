# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance-wide reads: the instance document and the custom emoji list,
declared once and sent through `api.core`.

`info_async(client, callback=None, cancellable=None)` /
`info_finish(client, result)` is `GET /api/v2/instance`.
`MastodonClient.instance_info_async` sends through it but keeps the caching
and coalescing, which are client state that `is_supported` reads.

`custom_emojis_async(client, callback=None, cancellable=None)` /
`custom_emojis_finish(client, result)`; the request on the wire is the one
the emoji chooser sent before.
"""

from . import core

INFO = core.declare(
    'instance.info', 'GET', '/api/v2/instance', None, None,
    'https://docs.joinmastodon.org/methods/instance/#v2',
    note='public; also carries api_versions, which gates later calls',
    mastodon_version='4.0.0')

CUSTOM_EMOJIS = core.declare(
    'instance.custom_emojis', 'GET', '/custom_emojis', None, None,
    'https://docs.joinmastodon.org/methods/custom_emojis/#get',
    note='public', mastodon_version='2.0.0')


def info_async(client, callback=None, cancellable=None):
    return core.send(client, INFO, callback=callback,
                     cancellable=cancellable)


def info_finish(client, result):
    return core.finish(client, result)


def custom_emojis_async(client, callback=None, cancellable=None):
    return core.send(client, CUSTOM_EMOJIS, callback=callback,
                     cancellable=cancellable)


def custom_emojis_finish(client, result):
    return core.finish(client, result)
