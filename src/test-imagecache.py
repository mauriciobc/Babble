# SPDX-License-Identifier: GPL-3.0-or-later
"""The image fetcher's request lifecycle, including the cancel paths.

The behaviour pinned here is a bug that was permanent and invisible.
``_TextureCache.load`` cancels whatever request a picture had in flight
every time that picture is rebound, which a scrolling timeline does
constantly. If the cancellation landed while the worker was decoding,
``mediacache._decode_async`` dropped its callback -- so neither the
success nor the failure path ran, and the URL's key stayed in
``Fetcher._inflight`` for the life of the process.

Every later request for that image then queued a waiter behind a request
that would never answer:

  * the image never appeared again, with no error and nothing logged;
  * no second request was ever made, so it could not recover either;
  * and the waiter list grew with each rebind.

The tests below drive that window deliberately. Decoding is injected, so
a decode can be made slow enough to cancel *inside* rather than racing it,
and it can drop its callback on cancellation exactly as the real one
does -- which is the condition the fetcher has to survive without being
told.

Run directly::

    python3 src/test-imagecache.py

Prints ok - / FAIL - lines and exits non-zero if any failed.
"""

import http.server
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gi  # noqa: E402

gi.require_version('Soup', '3.0')
from gi.repository import Gio, GLib  # noqa: E402

from src import imagecache  # noqa: E402

FAILURES = []
PIXEL = b'\x89PNG\r\n\x1a\nnot-really-a-png-but-bytes-are-bytes'


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))
        FAILURES.append(name)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    served = 0
    missing = set()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        Handler.served += 1
        if self.path.lstrip('/') in Handler.missing:
            body = b'{"error": "no such thing"}'
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(PIXEL)))
        self.end_headers()
        self.wfile.write(PIXEL)


def pump(seconds):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not ctx.iteration(False):
            time.sleep(0.002)


def wait_for(predicate, limit=10.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + limit
    while time.monotonic() < end and not predicate():
        if not ctx.iteration(False):
            time.sleep(0.002)
    return predicate()


def threaded(worker):
    """A decoder shaped like ``mediacache._decode_async``: it runs on a
    worker thread and delivers its callback on the main context.

    Both halves matter. Without the worker the decode would run inside
    ``_on_loaded``, blocking the main loop, and a cancellation could never
    land *during* it -- the very window these tests exist to hit. Without
    the hop back to the main loop, ``_finish`` would touch the store and
    call waiters off the main thread.
    """
    def decode(data, key, cancellable, done):
        def run():
            def deliver(texture, reason):
                GLib.idle_add(lambda: (done(texture, reason),
                                        GLib.SOURCE_REMOVE)[1])
            worker(data, key, cancellable, deliver)
        threading.Thread(target=run, daemon=True).start()
    return decode


def main():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:%d/' % server.server_address[1]
    failures = []

    def make(decode, limit=8):
        return imagecache.Fetcher(imagecache.Store(limit), decode,
                                  on_failure=lambda key, reason:
                                  failures.append((key, reason)))

    # --- 1. a cancel during the decode must not strand the key ---------
    entered = threading.Event()
    release = threading.Event()

    def slow_worker(data, key, cancellable, done):
        """Slow, and it drops the callback when cancelled -- the condition
        the fetcher has to survive without being told."""
        entered.set()
        release.wait(10)
        if cancellable is not None and cancellable.is_cancelled():
            return
        done('texture-for-%s' % key, None)

    slow_decode = threaded(slow_worker)
    fetcher = make(slow_decode)
    url = base + 'img/1.png'
    got = []
    cancellable = Gio.Cancellable.new()
    fetcher.fetch(url, lambda t, r: got.append((t, r)),
                  cancellable=cancellable)

    check('the decode started', wait_for(entered.is_set))
    cancellable.cancel()                  # a rebind, mid-decode
    release.set()
    pump(0.6)

    check('a cancelled decode leaves nothing in flight',
          len(fetcher.inflight()) == 0,
          'still holding %d' % len(fetcher.inflight()))
    check('a cancelled waiter is not called back', got == [])

    # The real test: the image must still be fetchable afterwards.
    Handler.served = 0
    again = []
    fetcher.fetch(url, lambda t, r: again.append((t, r)))
    check('the same image is fetchable again after a cancel',
          wait_for(lambda: bool(again)), 'it stayed unfetchable')
    check('the retry made a real request', Handler.served >= 1,
          'served=%d' % Handler.served)

    # --- 2. one waiter's cancel must not disturb the others -----------
    entered.clear()
    release.clear()
    fetcher = make(slow_decode)
    served_before = Handler.served
    got_a, got_b = [], []
    cancel_a = Gio.Cancellable.new()
    cancel_b = Gio.Cancellable.new()
    fetcher.fetch(base + 'img/2.png', lambda t, r: got_a.append((t, r)),
                  cancellable=cancel_a)
    fetcher.fetch(base + 'img/2.png', lambda t, r: got_b.append((t, r)),
                  cancellable=cancel_b)
    check('both waiters coalesced onto one request', wait_for(entered.is_set))
    served_at_join = Handler.served
    check('the second waiter did not start a second request',
          served_at_join == served_before + 1,
          'served %d -> %d, expected exactly one more'
          % (served_before, served_at_join))
    cancel_a.cancel()
    release.set()
    pump(0.8)

    check('a cancelled waiter is dropped', got_a == [])
    check('the other waiter still gets its texture',
          bool(got_b) and got_b[0][0] is not None,
          'got_b=%r' % (got_b,))
    check('the surviving waiter was not made to re-fetch',
          Handler.served == served_at_join,
          'served=%d, was %d' % (Handler.served, served_at_join))
    check('nothing stranded after a partial cancel',
          len(fetcher.inflight()) == 0)

    # --- 3. a failure is reported once and is not cached -------------
    instant_decode = threaded(
        lambda data, key, cancellable, done: done(None, 'decode refused it'))

    failures.clear()
    fetcher = make(instant_decode)
    got = []
    Handler.missing = {'img/3.png'}
    fetcher.fetch(base + 'img/3.png', lambda t, r: got.append((t, r)))
    fetcher.fetch(base + 'img/3.png', lambda t, r: got.append((t, r)))
    pump(0.5)
    check('a failed fetch tells every waiter', len(got) == 2, 'got=%r' % got)
    check('a failure is reported once, not once per waiter',
          len(failures) == 1, 'failures=%r' % failures)
    check('a failure is not cached', len(fetcher.store) == 0)
    check('a failure leaves nothing in flight', len(fetcher.inflight()) == 0)

    # A cancellation must not be logged as a broken image.
    failures.clear()
    entered.clear()
    release.clear()
    fetcher = make(slow_decode)
    cancellable = Gio.Cancellable.new()
    fetcher.fetch(base + 'img/4.png', lambda t, r: None,
                  cancellable=cancellable)
    wait_for(entered.is_set)
    cancellable.cancel()
    release.set()
    pump(0.6)
    check('a cancellation is not logged as a media failure', failures == [],
          'failures=%r' % failures)

    # --- 4. an already-cancelled waiter is not queued ------------------
    fetcher = make(instant_decode)
    got = []
    dead = Gio.Cancellable.new()
    dead.cancel()
    fetcher.fetch(base + 'img/5.png', lambda t, r: got.append((t, r)),
                  cancellable=dead)
    pump(0.5)
    check('nothing in flight for an already-cancelled waiter',
          len(fetcher.inflight()) == 0, 'inflight=%d'
          % len(fetcher.inflight()))

    # --- 5. the store serves a later row without a request ------------
    good_decode = threaded(
        lambda data, key, cancellable, done: done('texture-for-%s' % key,
                                                  None))

    fetcher = make(good_decode)
    first = []
    Handler.served = 0
    fetcher.fetch(base + 'img/6.png', lambda t, r: first.append((t, r)))
    pump(0.4)
    second = []
    fetcher.fetch(base + 'img/6.png', lambda t, r: second.append((t, r)))
    pump(0.2)
    check('the first fetch delivered', bool(first) and first[0][0])
    check('a cached image is served without a request',
          bool(second) and second[0][0] and Handler.served == 1,
          'served=%d' % Handler.served)

    server.shutdown()
    if FAILURES:
        print('\n%d failure(s): %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('\nall imagecache tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
