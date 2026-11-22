# SPDX-License-Identifier: GPL-3.0-or-later

"""The public Bluesky AppView, for the read-only thread page and for the
post a post quotes when the server kept no preview card of it.

A quote card is built from the Mastodon preview card alone, and Bluesky
is contacted only when the reader opens a post, or when a post quotes
another one and the server kept nothing about it.  Functions, not a
client: this is not the Mastodon API and goes nowhere near
``MastodonClient``.  Replies come as ``(data, error)``; a caller that is
a widget wraps its callback with ``weakconnect.weak_method``."""

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

# (DID, rkey) -> the post view, for the session.  The timeline asks for
# the same quoted post once per post it shows, and the answer does not
# change while the session lasts.
_posts = {}

# (DID, rkey) the AppView answered "unavailable" for (deleted, blocked or
# hidden from signed-out readers).  That does not change while the session
# lasts either, and a quote card asks on every bind, so a scroll or a
# favourite would send the same failing request again.  Only that answer is
# kept: a busy or failed one is worth asking again.
_unavailable = set()


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


def get_post_async(actor, rkey, callback):
    """``callback(post, error)`` for the post *rkey* by *actor* (a DID or
    a handle).  A handle is resolved first, as for the thread.

    The post comes in the shape the thread page reads: the record, the
    author and the resolved embed, so a quoted post can be drawn from it
    the way a thread one is.  The answer is kept for the session: the
    same quoted post is asked for once, however many posts quote it.

    Returns a state for :func:`cancel`; once cancelled no step of the
    chain goes on and *callback* is never called."""
    state = {'cancellable': None, 'cancelled': False}

    def answer(data, error, key=None):
        if state['cancelled']:
            return
        post = None
        for found in (data or {}).get('posts') or ():
            if isinstance(found, dict):
                post = found
                ref = bskytext.post_ref(found.get('uri') or '')
                if ref is not None:
                    _posts[ref] = found
                break
        if post is None:
            if key is not None and (error or UNAVAILABLE) == UNAVAILABLE:
                _unavailable.add(key)
            callback(None, error or UNAVAILABLE)
        else:
            callback(post, None)

    def fetch(did):
        if state['cancelled']:
            return
        key = (did, rkey)
        if key in _posts:
            post = _posts[key]

            def cached():
                if not state['cancelled']:
                    callback(post, None)
                return False
            GLib.idle_add(cached)
            return
        if key in _unavailable:
            def unavailable():
                if not state['cancelled']:
                    callback(None, UNAVAILABLE)
                return False
            GLib.idle_add(unavailable)
            return
        state['cancellable'] = _get_json(
            '/xrpc/app.bsky.feed.getPosts',
            {'uris': bskytext.at_uri(did, rkey)},
            lambda data, error: answer(data, error, key))

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
