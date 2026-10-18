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

A cancelled request was, for a while, a *permanent* one. The fetcher
handed the first caller's cancellable to libsoup and to the decoder, so
recycling one row aborted the fetch for every other row sharing it; and
because the decoder drops its callback when its task is cancelled, a
cancellation landing mid-decode ran none of the teardown paths and left
the key in ``_inflight`` forever. No later request for that image could
then ever succeed, and every attempt queued another waiter on the dead
key. A request now owns its cancellable and has exactly one teardown path
(:meth:`Fetcher._finish`) that every ending goes through; cancelling a
waiter cancels only that waiter's interest. ``src/test-imagecache.py``
pins this down, and ``bench/v2`` holds ``inflight_requests`` at zero so
the same failure cannot come back unnoticed.
"""

import collections

import gi

gi.require_version('Soup', '3.0')

from gi.repository import GLib, Gio, Soup  # noqa: E402

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

# Not an error: a request nobody is waiting for any more is cancelled on
# purpose, and must not be logged as a failed image.
CANCELLED = 'cancelled'


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


def _watch_cancelled(cancellable, callback, *user_data):
    """Observe *cancellable* cancelling, returning a handler id.

    ``connect_after`` rather than ``connect``, deliberately. On
    ``Gio.Cancellable::cancelled`` -- a signal with no arguments --
    PyGObject 3.58's ``connect`` rejects a Python callable outright
    ("Callback needs to be a function or method not str") while
    ``connect_after`` accepts it and delivers *user_data* as one tuple.
    So the callback takes ``*data``. This wrapper keeps the quirk in one
    place, and ``src/test-imagecache.py`` exercises both this and the leak
    it guards, so a PyGObject that fixes or moves it fails a test instead
    of silently stranding requests again.
    """
    return cancellable.connect_after('cancelled', callback, *user_data)


class _Request:
    """One request in flight, and the waiters sharing it.

    The request owns a cancellable of its own instead of borrowing the
    first caller's. Borrowing it meant two separate faults: recycling one
    row aborted the fetch for every *other* row waiting on the same URL,
    and -- because the decoder was handed that same cancellable -- a
    cancellation could end the request with nothing left to forget it
    (see :meth:`Fetcher._finish`).
    """

    __slots__ = ('key', 'url', 'cancellable', 'waiters', 'watched',
                 'message', 'finished')

    def __init__(self, key, url):
        self.key = key
        self.url = url
        self.cancellable = Gio.Cancellable.new()
        self.waiters = []
        # (caller's cancellable, handler id, waiter), so a handler can be
        # disconnected again and never keeps this fetcher alive.
        self.watched = []
        self.message = None
        self.finished = False


class Fetcher:
    """Fetches one URL at most once at a time, whoever asks.

    *decode* turns the response body into a texture. It is called as
    ``decode(data, key, cancellable, callback)`` -- *key* is the cache key
    the request was made under, which is how a decoder that needs
    something the URL does not carry (the box an attachment has to fill)
    gets at it -- and must invoke *callback* with ``(texture, reason)``,
    *reason* being None on success. *cancellable* is the request's own,
    not a caller's, so a decoder that drops its callback when cancelled
    (as ``mediacache._decode_async`` does) cannot strand the request: the
    teardown is driven from here instead. *on_failure* is called with
    ``(key, reason)`` once per failed request rather than once per waiter,
    which is what a log line wants -- and never for a cancellation, which
    is a caller moving on rather than an image failing.
    """

    def __init__(self, store, decode, on_failure=None):
        self.store = store
        self.decode = decode
        self.on_failure = on_failure
        self._inflight = {}

    def inflight(self):
        """Keys with a request out, as a count-friendly view.

        The values are :class:`_Request`, so ``len(fetcher.inflight())``
        is how a caller -- or a benchmark -- checks that nothing was left
        stranded. It should be zero once the main loop has gone quiet.
        """
        return self._inflight.keys()

    def fetch(self, url, waiter, cancellable=None, key=None):
        """Deliver *url*'s texture to *waiter*, fetching only if needed.

        *waiter* is called with ``(texture, reason)``, *reason* being
        None on success. A *cancellable* cancels **this waiter's interest**,
        not the shared request: if others still want the image it carries
        on, and if nobody does the request is abandoned. So a row being
        recycled mid-decode no longer takes the picture out from under the
        rows that shared it, and no longer leaves a half-finished request
        behind that nothing will ever complete.
        """
        cache_key = url if key is None else key
        texture = self.store.get(cache_key)
        if texture is not None:
            waiter(texture, None)
            return
        request = self._inflight.get(cache_key)
        if request is None:
            request = _Request(cache_key, url)
            self._inflight[cache_key] = request
            # Attach before starting: _start can finish immediately (an
            # unusable URL), and the waiter must already be listening.
            self._attach(request, waiter, cancellable)
            self._start(request)
            return
        self._attach(request, waiter, cancellable)

    def _attach(self, request, waiter, cancellable):
        if request.finished:
            return
        if cancellable is not None and cancellable.is_cancelled():
            # Already abandoned by its caller; queuing it would resurrect
            # an interest nobody holds.
            return
        request.waiters.append(waiter)
        if cancellable is not None:
            handler = _watch_cancelled(
                cancellable, self._on_waiter_cancelled, request, waiter)
            request.watched.append((cancellable, handler, waiter))

    def _on_waiter_cancelled(self, _cancellable, *data):
        self._detach(data[0], data[1])

    def _detach(self, request, waiter):
        """A caller has given up its interest in this request."""
        for index, held in enumerate(request.waiters):
            if held is waiter:
                del request.waiters[index]
                break
        else:
            return
        self._unwatch(request, waiter)
        if not request.waiters and not request.finished:
            # Nobody will display the result. Cancelling reaches _finish
            # through the request's own cancellable, so the key is still
            # forgotten -- which is the whole point.
            request.cancellable.cancel()

    @staticmethod
    def _disconnect_later(cancellable, handler):
        """Disconnect once the current signal emission has finished.

        GLib does not allow a handler to be removed while it is being
        emitted, and _unwatch is reachable from the 'cancelled' handler
        itself, so disconnecting inline deadlocks the cancel. Every
        removal goes through an idle instead. A disconnect of a handler
        that has already gone raises, which is not worth reporting."""
        try:
            cancellable.disconnect(handler)
        except (TypeError, ValueError):
            pass
        return GLib.SOURCE_REMOVE

    def _unwatch(self, request, waiter):
        for entry in list(request.watched):
            if entry[2] is waiter:
                request.watched.remove(entry)
                GLib.idle_add(self._disconnect_later, entry[0], entry[1])

    def _start(self, request):
        # Tearing down is driven by the request's own cancellable, not by
        # the decoder calling back. _decode_async drops its callback when
        # the task is cancelled, so a decoder-driven teardown could skip
        # it entirely -- which is exactly how a key used to get stranded.
        request.cancellable.connect_after(
            'cancelled', self._on_request_cancelled, request)
        message = Soup.Message.new('GET', request.url)
        if message is None:
            self._finish(request, None, 'invalid URL')
            return
        request.message = message
        session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, request.cancellable,
            self._on_loaded, request)

    def _on_request_cancelled(self, _cancellable, *data):
        self._finish(data[0], None, CANCELLED)

    def _on_loaded(self, sess, result, request):
        reason = None
        data = None
        try:
            data = sess.send_and_read_finish(result)
        except GLib.Error as error:
            reason = error.message
        else:
            status = request.message.get_status()
            if not 200 <= status < 300:
                # A 404 page can be an image too; never show or cache it.
                reason = 'HTTP %d' % status
            elif data is None or data.get_size() == 0:
                reason = 'empty response'
        if reason is not None:
            self._finish(request, None, reason)
            return
        request.message = None
        # The key stays in flight while a worker decodes: a widget that
        # asks again meanwhile waits instead of starting a second fetch.
        self.decode(data, request.key, request.cancellable,
                    lambda texture, error: self._finish(request, texture,
                                                       error))

    def _finish(self, request, texture, reason):
        """Deliver the outcome once, and always forget the request.

        Every terminal path passes through here: success, HTTP failure,
        decode failure, and cancellation. That is what makes forgetting
        the key reliable. It was not, and the failure was permanent:

          * ``_TextureCache.load`` cancels the request a picture had in
            flight every time that picture is rebound, which a scrolling
            timeline does constantly;
          * ``_decode_async`` drops its callback when its cancellable
            fires, so if that happened while the worker was decoding,
            neither the success nor the failure path ran;
          * the key therefore stayed in ``_inflight`` for the life of the
            process, and every later ``fetch`` for that URL queued a
            waiter behind a request that would never answer.

        The image simply never appeared again, and the waiter list grew
        with each rebind. Idempotent, because cancellation and the
        decoder's late callback can both arrive.
        """
        if request.finished:
            return
        request.finished = True
        self._inflight.pop(request.key, None)
        for cancellable, handler, _waiter in request.watched:
            # Deferred, because _finish is reachable from the request's own
            # 'cancelled' handler and one of these may be firing right now.
            GLib.idle_add(self._disconnect_later, cancellable, handler)
        request.watched = []
        waiters, request.waiters = request.waiters, []
        if texture is None:
            # A cancellation is not a broken image: it is nobody asking
            # any more, and logging it as a media failure is noise.
            if self.on_failure is not None and reason != CANCELLED:
                self.on_failure(request.key, reason)
            for waiter in waiters:
                waiter(None, reason)
            return
        self.store.put(request.key, texture)
        for waiter in waiters:
            waiter(texture, None)
