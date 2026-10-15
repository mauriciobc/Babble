# SPDX-License-Identifier: GPL-3.0-or-later

"""One image cache: one Soup session, one HTTP hit, bounded decoded
textures.

There were six of these in the app -- avatars, media, profile banners,
custom emoji, and the media viewer's two -- and they had drifted:

* :class:`HeaderCache` was unbounded, so a long session over many
  profiles grew without limit while its two siblings were capped;
* ``desktopnotify`` reached into ``AvatarCache._textures`` to read it;
* one of them fetched a second time for a URL already in flight.

What they share is the fetch, and this is it:

* one :class:`Soup.Session` with the app's timeout, rather than one per
  cache -- a connection pool each;
* an LRU of decoded textures, because a decoded image is expensive and a
  session meets hundreds of URLs;
* coalescing, so ten rows showing one avatar make one request;
* a failure that is logged and **not** cached, so a later row retries --
  a failure here is usually a server having a moment, not a verdict about
  the image.

What stays with the caller is what legitimately differs: the cache key (an
avatar is a URL, a media tile is a URL *and* the box it fills), whether a
cancelled request matters, and what the widget does with the outcome -- an
avatar falls back to its initials, a media tile paints the broken-image
glyph.

Decoding is injected rather than owned here. It is the part that differs
most and the part already covered by ``timeline``'s own tests, so moving
it would risk a working, tested path for no gain in duplication.
"""

import collections

import gi

gi.require_version('Soup', '3.0')

from gi.repository import GLib, Soup  # noqa: E402

# The same bound mastodon.py gives every session: libsoup defaults to
# zero, meaning "wait forever", and a stalled peer would then leave the
# request pending for the life of the process.
HTTP_TIMEOUT_SECONDS = 30

# Decoded-texture budgets, per use. Avatars repeat across a timeline so
# they are cheap to keep; full-size media are large, so far fewer stay
# resident. Banners are between the two: one per profile, and a session
# visits only so many.
AVATAR_CACHE_SIZE = 256
MEDIA_CACHE_SIZE = 48
BANNER_CACHE_SIZE = 16

_session = None


def session():
    """The process-wide Soup session every image fetch shares."""
    global _session
    if _session is None:
        _session = Soup.Session()
        _session.set_timeout(HTTP_TIMEOUT_SECONDS)
    return _session


class Store:
    """A bounded LRU of decoded textures, keyed however its owner likes."""

    __slots__ = ('_textures', 'limit')

    def __init__(self, limit):
        self._textures = collections.OrderedDict()
        self.limit = limit

    def get(self, key):
        """The texture for *key*, marking it most recently used."""
        if key in self._textures:
            self._textures.move_to_end(key)
            return self._textures[key]
        return None

    def has(self, key):
        return key in self._textures

    def put(self, key, texture):
        """Cache *texture*, evicting the least recently used past the
        limit."""
        self._textures[key] = texture
        self._textures.move_to_end(key)
        while len(self._textures) > self.limit:
            self._textures.popitem(last=False)

    def __len__(self):
        return len(self._textures)

    def keys(self):
        """Every cached key.

        For the one caller that needs to see what is already decoded
        without going through a widget -- the media viewer, which shares
        its cache with the timeline rather than fetching twice.
        """
        return self._textures.keys()

    def clear(self):
        """Drop everything. For a test, or a sign-out that wants the
        memory back rather than keeping it for an account it has left."""
        self._textures.clear()


class Fetcher:
    """Fetches one URL at most once at a time, whoever asks.

    *decode* turns the response body into a texture. It is called as
    ``decode(data, key, cancellable, callback)`` -- *key* is the cache key
    the request was made under, which is how a decoder that needs
    something the URL does not carry (the box an attachment has to fill)
    gets at it -- and must invoke *callback* with ``(texture, reason)``,
    *reason* being None on success. *on_failure* is called with
    ``(key, reason)`` once per failed request rather than once per waiter,
    which is what a log line wants.
    """

    def __init__(self, store, decode, on_failure=None):
        self.store = store
        self.decode = decode
        self.on_failure = on_failure
        self._inflight = {}

    def inflight(self):
        """Keys with a request out. For a caller that cancels its own."""
        return self._inflight.keys()

    def fetch(self, url, waiter, cancellable=None, key=None):
        """Deliver *url*'s texture to *waiter*, fetching only if needed.

        *waiter* is called with ``(texture, reason)``. *cancellable*
        cancels this request; a caller that recycles its widget should pass
        one, so the bytes for a picture nobody will display are not still
        being read off the network.
        """
        cache_key = url if key is None else key
        texture = self.store.get(cache_key)
        if texture is not None:
            waiter(texture, None)
            return
        waiting = self._inflight.get(cache_key)
        if waiting is not None:
            waiting.append(waiter)
            return
        self._inflight[cache_key] = [waiter]
        message = Soup.Message.new('GET', url)
        if message is None:
            self._fail(cache_key, 'invalid URL')
            return
        session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, cancellable, self._on_loaded,
            (cache_key, url, message, cancellable))

    def _fail(self, cache_key, reason):
        """Report *reason* to every waiter and forget the request.

        Nothing is cached, so the next widget to want this URL tries
        again.
        """
        waiting = self._inflight.pop(cache_key, [])
        if self.on_failure is not None:
            self.on_failure(cache_key, reason)
        for waiter in waiting:
            waiter(None, reason)

    def _on_loaded(self, sess, result, extra):
        cache_key, url, message, cancellable = extra
        reason = None
        data = None
        try:
            data = sess.send_and_read_finish(result)
        except GLib.Error as error:
            reason = error.message
        else:
            status = message.get_status()
            if not 200 <= status < 300:
                # A 404 page can be an image too; never show or cache it.
                reason = 'HTTP %d' % status
            elif data is None or data.get_size() == 0:
                reason = 'empty response'
        if reason is not None:
            self._fail(cache_key, reason)
            return
        # The key stays in flight while a worker decodes: a widget that
        # asks again meanwhile waits instead of starting a second fetch.
        self.decode(data, cache_key, cancellable,
                    lambda texture, error:
                    self._decoded(cache_key, texture, error))

    def _decoded(self, cache_key, texture, reason):
        waiting = self._inflight.pop(cache_key, [])
        if texture is None:
            if self.on_failure is not None:
                self.on_failure(cache_key, reason)
            for waiter in waiting:
                waiter(None, reason)
            return
        self.store.put(cache_key, texture)
        for waiter in waiting:
            waiter(texture, None)
