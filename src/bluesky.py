# SPDX-License-Identifier: GPL-3.0-or-later

"""The public Bluesky AppView, for the read-only thread page and nothing
else.  The timeline never reaches this module: a quote card is built from
the Mastodon preview card alone, and Bluesky is contacted only when the
reader opens a post.

Functions, not a client: this is not the Mastodon API and goes nowhere near
``MastodonClient``.  Replies come as ``(data, error)``; a caller that is a
widget wraps its callback with ``pages.weak_method``."""

import json
import os
import urllib.parse

import gi

gi.require_version('Soup', '3.0')

from gi.repository import Gio, GLib  # noqa: E402

from . import bskytext  # noqa: E402
from . import imagecache  # noqa: E402

DEFAULT_APPVIEW = 'https://public.api.bsky.app'
THREAD_DEPTH = 6
THREAD_PARENT_HEIGHT = 20
MAX_BODY = 8 * 1024 * 1024

# handle -> DID, for the session.
_dids = {}


def appview():
    return (os.environ.get('BABBLE_BSKY_APPVIEW')
            or DEFAULT_APPVIEW).rstrip('/')


def _get_json(path, params, callback):
    """GET *path* on the AppView and call ``callback(data, error)`` on the
    main loop.  Returns the Gio.Cancellable."""
    cancellable = Gio.Cancellable.new()
    url = '%s%s?%s' % (appview(), path, urllib.parse.urlencode(params))
    message = imagecache.get_message('GET', url)
    if message is None:
        GLib.idle_add(callback, None, 'invalid URL')
        return cancellable

    def done(session, result):
        try:
            body = session.send_and_read_finish(result)
        except GLib.Error as error:
            if not cancellable.is_cancelled():
                callback(None, error.message)
            return
        status = message.get_status()
        data = body.get_data() if body is not None else b''
        if not 200 <= int(status) < 300:
            callback(None, 'HTTP %d' % int(status))
            return
        if len(data) > MAX_BODY:
            callback(None, 'response too large')
            return
        try:
            parsed = json.loads(data)
        except ValueError:
            callback(None, 'invalid response')
            return
        callback(parsed, None)

    imagecache.session().send_and_read_async(
        message, GLib.PRIORITY_DEFAULT, cancellable, done)
    return cancellable


def resolve_handle_async(handle, callback):
    """``callback(did, error)``; cached for the session."""
    handle = handle.lower()
    if handle in _dids:
        GLib.idle_add(callback, _dids[handle], None)
        return None

    def done(data, error):
        did = (data or {}).get('did')
        if did:
            _dids[handle] = did
            callback(did, None)
        else:
            callback(None, error or 'handle not found')

    return _get_json('/xrpc/com.atproto.identity.resolveHandle',
                     {'handle': handle}, done)


def get_thread_async(actor, rkey, callback):
    """``callback(thread, error)`` for the post *rkey* by *actor* (a DID or
    a handle).  A handle is resolved first: the AppView answers HTTP 500 to
    an ``at://`` URI that carries one."""
    state = {'cancellable': None}

    def fetch(did):
        state['cancellable'] = _get_json(
            '/xrpc/app.bsky.feed.getPostThread',
            {'uri': bskytext.at_uri(did, rkey), 'depth': THREAD_DEPTH,
             'parentHeight': THREAD_PARENT_HEIGHT},
            lambda data, error: callback((data or {}).get('thread'), error
                                         or (None if data and data.get(
                                             'thread') else 'no thread')))

    if bskytext.is_did(actor):
        fetch(actor)
        return state

    def resolved(did, error):
        if did:
            fetch(did)
        else:
            callback(None, error)

    state['cancellable'] = resolve_handle_async(actor, resolved)
    return state


def cancel(state):
    """Stop a request started by :func:`get_thread_async`."""
    cancellable = (state or {}).get('cancellable')
    if cancellable is not None:
        cancellable.cancel()
