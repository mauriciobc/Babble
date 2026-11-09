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

# What a failed request is reported as; the thread page words them.
UNAVAILABLE = 'unavailable'
RATE_LIMITED = 'rate-limited'
FAILED = 'failed'

# handle -> DID, for the session.
_dids = {}


def appview():
    return (os.environ.get('BABBLE_BSKY_APPVIEW')
            or DEFAULT_APPVIEW).rstrip('/')


def _kind(status):
    if status in (400, 404):
        return UNAVAILABLE
    if status == 429:
        return RATE_LIMITED
    return FAILED


def _get_json(path, params, callback):
    """GET *path* on the AppView and call ``callback(data, error)`` on the
    main loop, with *data* a dict or None and *error* one of the kinds
    above.  Always answers, unless cancelled.  Returns the
    Gio.Cancellable."""
    cancellable = Gio.Cancellable.new()
    url = '%s%s?%s' % (appview(), path, urllib.parse.urlencode(params))
    message = imagecache.get_message('GET', url)
    if message is None:
        GLib.idle_add(callback, None, FAILED)
        return cancellable

    def done(session, result):
        try:
            body = session.send_and_read_finish(result)
        except GLib.Error:
            if not cancellable.is_cancelled():
                callback(None, FAILED)
            return
        if cancellable.is_cancelled():
            return
        status = int(message.get_status())
        data = body.get_data() if body is not None else b''
        if not 200 <= status < 300:
            callback(None, _kind(status))
            return
        if len(data) > MAX_BODY:
            callback(None, FAILED)
            return
        try:
            parsed = json.loads(data)
        except ValueError:
            callback(None, FAILED)
            return
        if not isinstance(parsed, dict):
            callback(None, FAILED)
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
        if isinstance(did, str) and bskytext.is_did(did):
            _dids[handle] = did
            callback(did, None)
        else:
            callback(None, error or UNAVAILABLE)

    return _get_json('/xrpc/com.atproto.identity.resolveHandle',
                     {'handle': handle}, done)


def get_thread_async(actor, rkey, callback):
    """``callback(thread, error)`` for the post *rkey* by *actor* (a DID or
    a handle).  A handle is resolved first: the AppView answers HTTP 500 to
    an ``at://`` URI that carries one.  Returns a state for :func:`cancel`;
    once cancelled no step of the chain goes on and *callback* is never
    called."""
    state = {'cancellable': None, 'cancelled': False}

    def answer(data, error):
        if state['cancelled']:
            return
        thread = data.get('thread') if isinstance(data, dict) else None
        if isinstance(thread, dict):
            callback(thread, None)
        else:
            callback(None, error or UNAVAILABLE)

    def fetch(did):
        if state['cancelled']:
            return
        state['cancellable'] = _get_json(
            '/xrpc/app.bsky.feed.getPostThread',
            {'uri': bskytext.at_uri(did, rkey), 'depth': THREAD_DEPTH,
             'parentHeight': THREAD_PARENT_HEIGHT}, answer)

    if bskytext.is_did(actor):
        fetch(actor)
        return state

    def resolved(did, error):
        if state['cancelled']:
            return
        if did:
            fetch(did)
        else:
            callback(None, error)

    state['cancellable'] = resolve_handle_async(actor, resolved)
    return state


def cancel(state):
    """Stop a request started by :func:`get_thread_async`."""
    if not state:
        return
    state['cancelled'] = True
    cancellable = state.get('cancellable')
    if cancellable is not None:
        cancellable.cancel()
