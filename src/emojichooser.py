# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom emoji of the signed-in instance: a cached ``/custom_emojis``
fetch published to :mod:`textemoji` as shortcode -> image URL, so a
``:shortcode:`` resolves to an image wherever it is rendered, not just
where a payload happened to declare it."""

import gi

gi.require_version('Soup', '3.0')

from gi.repository import GLib  # noqa: E402

from . import textemoji  # noqa: E402

_cache = {}
_waiting = {}

# Per-instance shortcode -> image URL for EVERY emoji the instance
# publishes (including the ones hidden from the picker); the renderer
# reads whichever one the session has activated.
_instance_banks = {}


def cached_custom_emojis(client):
    """The picker-visible custom emoji of the client's instance, or None
    while they have not been fetched yet."""
    return _cache.get(client.instance)


def activate_instance_emojis(client):
    """Make the client's instance the renderer's fallback emoji layer.

    Separate from the fetch because the active instance is the window's
    business: a fetch started for a previous account can still land
    after a switch, and publishing straight from the response would
    hand the new session the old instance's emoji."""
    textemoji.set_instance_emoji_bank(
        _instance_banks.get(client.instance) or {})


def deactivate_instance_emojis():
    """Drop the fallback layer, so nothing renders against a stale
    instance's emoji while no account is signed in."""
    textemoji.set_instance_emoji_bank({})


def load_custom_emojis(client, callback):
    """Fetch ``GET /api/v1/custom_emojis`` once per instance and call
    ``callback(emojis)`` (a list; empty on failure) on the main loop.
    Emoji flagged ``visible_in_picker: false`` are hidden from the
    picker but kept in the instance bank, because other people's posts
    use them."""
    key = client.instance
    if key in _cache:
        GLib.idle_add(lambda: (callback(_cache[key]), False)[1])
        return
    if key in _waiting:
        _waiting[key].append(callback)
        return
    _waiting[key] = [callback]

    def finished(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error:
            data = None
        waiting = _waiting.pop(key, [])
        if isinstance(data, list):
            _instance_banks[key] = textemoji.emoji_bank_from(data)
            emojis = [e for e in data if e.get('visible_in_picker', True)
                      and e.get('shortcode')]
            emojis.sort(key=lambda e: e['shortcode'].lower())
            _cache[key] = emojis
        else:
            emojis = []
        for waiter in waiting:
            waiter(emojis)

    client.request_async('GET', '/custom_emojis', callback=finished)
