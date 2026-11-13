# SPDX-License-Identifier: GPL-3.0-or-later
"""An avatar's request ends when the widget is rebound or unbound.

A scrolling timeline rebinds the same avatar widget to a new account
constantly. The previous account's request used to stay attached to it, so
a slow answer could land on a widget that now belonged to someone else.
``AvatarCache.load`` now cancels the widget's own waiter first (the shared
request carries on for any other widget still waiting for that URL), a
Catch Up row cancels on unbind, and an answer that was already queued when
the cancellation came is ignored.

Exits 77 (skip) without a display.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
    print('SKIP - needs a display')
    sys.exit(77)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Adw, Gdk, GLib  # noqa: E402

from src import mediacache  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))
        FAILURES.append(name)


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, waiter, cancellable=None, key=None):
        self.calls.append((url, waiter, cancellable))


fetcher = FakeFetcher()
mediacache.AvatarCache._fetch = classmethod(lambda cls: fetcher)
avatar = Adw.Avatar()


def pixel():
    return Gdk.MemoryTexture.new(
        1, 1, Gdk.MemoryFormat.R8G8B8,
        GLib.Bytes.new(b'\x00\x00\x00'), 3)


mediacache.AvatarCache.load(avatar, {'avatar': 'https://x.example/1.png'})
mediacache.AvatarCache.load(avatar, {'avatar': 'https://x.example/2.png'})
(_u1, waiter1, cancel1), (_u2, waiter2, cancel2) = fetcher.calls
check('rebinding cancels the previous request',
      cancel1.is_cancelled() and not cancel2.is_cancelled())
waiter1(pixel(), None)
check('a stale answer never lands on the rebound avatar',
      avatar.get_custom_image() is None)
waiter2(pixel(), None)
check('the current answer does', avatar.get_custom_image() is not None)

mediacache.AvatarCache.load(avatar, {'avatar': 'https://x.example/3.png'})
cancel3 = fetcher.calls[-1][2]
mediacache.AvatarCache.cancel(avatar)
check('unbinding cancels the request in flight', cancel3.is_cancelled())
mediacache.AvatarCache.cancel(avatar)
check('and cancelling twice is harmless', True)

# A decode failure must end the request, not strand its URL in flight.
from src import imagecache  # noqa: E402

real = imagecache.Fetcher(
    imagecache.Store(4),
    lambda data, key, cancellable, done: mediacache.decode_async(
        data, (96, 96), cancellable,
        lambda texture, error: done(texture,
                                    mediacache._reason_text(error))))
outcome = []
request = imagecache._Request('u', 'https://x.example/bad.png')
real._inflight['u'] = request
request.waiters.append(lambda texture, reason: outcome.append(reason))
real.decode(GLib.Bytes.new(b'not an image'), 'u', request.cancellable,
            lambda texture, error: real._finish(request, texture, error))
ctx = GLib.MainContext.default()
for _ in range(500):
    ctx.iteration(False)
    if outcome:
        break
    GLib.usleep(2000)
check('an undecodable image fails with a reason', bool(outcome and outcome[0]),
      repr(outcome))
check('and its URL is not left in flight', 'u' not in real._inflight)

if FAILURES:
    print('\n%d failure(s): %s' % (len(FAILURES), ', '.join(FAILURES)))
    sys.exit(1)
print('\nall avatar cache tests passed')
