# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance-wide reads: today the custom emoji list (audit row OT-1),
declared once and sent through `api.core`.

`custom_emojis_async(client, callback=None, cancellable=None)` /
`custom_emojis_finish(client, result)`; the request on the wire is the one
the emoji chooser sent before.
"""

from . import core

CUSTOM_EMOJIS = core.declare(
    'instance.custom_emojis', 'GET', '/custom_emojis', None, None,
    'https://docs.joinmastodon.org/methods/custom_emojis/#get',
    note='public', mastodon_version='2.0.0')


def custom_emojis_async(client, callback=None, cancellable=None):
    return core.send(client, CUSTOM_EMOJIS, callback=callback,
                     cancellable=cancellable)


def custom_emojis_finish(client, result):
    return core.finish(client, result)
