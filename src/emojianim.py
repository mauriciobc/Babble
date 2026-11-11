# SPDX-License-Identifier: GPL-3.0-or-later

"""Animated custom emoji.

A Mastodon custom emoji has a ``url`` (the original: an animated APNG or
GIF when the emoji moves) and a ``static_url`` (its first frame).  The
renderer draws the static one at once; this module then swaps in the
animation for as long as the image is on screen.

* **Decoding is glycin's** (``Gly``), which runs the image loader in a
  sandboxed process and hands back one frame per async call, so the main
  thread only wraps finished pixels in a ``Gdk.Texture``.  GdkPixbuf
  cannot read APNG.  Without ``Gly`` every emoji stays static.
* **Fetching is :class:`imagecache.Fetcher`**: the shared session, the
  response cap and the coalescing; only the store and the decoder differ.
* **One shared animation per URL**: an :class:`AnimatedEmoji` is both the
  decoded frames and the ``Gdk.Paintable`` every occurrence displays,
  kept in a byte-budgeted LRU that never evicts one that is on screen.
  An emoji that is not animated, or is too big to animate, is remembered
  as such so it is not fetched again.
* **One clock** for all of them: a single GLib timeout that exists only
  while an animation has a mapped image, wakes at the earliest next
  frame and invalidates only the paintables whose frame changed.
* **Gates** are the loop rule of :mod:`mediapolicy` (the user's switch,
  animations off, reduced motion, power saver), followed live; closed,
  the image keeps its static frame and nothing is fetched.

Images are :class:`EmojiImage`, which registers itself with the manager
from its map and unmap vfuncs, so it connects no signal, and rows the
list view recycles cost nothing once unmapped.
"""

import collections

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')

from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import imagecache  # noqa: E402

try:
    gi.require_version('Gly', '2')
    gi.require_version('GlyGtk4', '2')
    from gi.repository import Gly, GlyGtk4  # noqa: E402
except (ImportError, ValueError):
    Gly = GlyGtk4 = None

# Decoded frames are w * h * 4 each.
CACHE_BUDGET_BYTES = 32 * 1024 * 1024
# One emoji may not take more than this, nor have more frames or a longer
# side than this; such a file stays static.
MAX_ANIMATION_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 200
MAX_DIMENSION = 256
# Browsers treat a frame delay of 10 ms or less as 100 ms.
MIN_FRAME_MS = 20
DEFAULT_FRAME_MS = 100
# A mapped image waits this long before it fetches, so rows flung past
# and the first layout's map/unmap churn start nothing.
DEFER_MS = 150
# At most this many glycin decodes at once; the rest queue.
MAX_DECODES = 2
# A glycin load that has not finished by now is given up on.
WATCHDOG_MS = 10000
# A failed URL is not asked for again for this long.
FAILURE_TTL_MS = 60000

# static_url -> url, for the emoji that move.
_animated_urls = {}


def register(static_url, url):
    """Remember that *static_url* has an animated original at *url*."""
    if static_url and url and url != static_url:
        _animated_urls[static_url] = url


def animated_url(static_url):
    """The original of *static_url*, or None when it is not known to
    differ."""
    return _animated_urls.get(static_url)


def available():
    return Gly is not None


def gate_open(widget):
    """Whether animation may run at all.  Read live; tests replace it."""
    from . import mediapolicy
    return mediapolicy.may_loop(mediapolicy.read_state(widget))


# The outcome stored for an image that is not animated.
_STATIC = object()


class AnimatedEmoji(GObject.Object, Gdk.Paintable):
    """The frames of one emoji URL, and the paintable showing the
    current one.  Shared by every image displaying that URL."""

    __gtype_name__ = 'BabbleAnimatedEmoji'

    def __init__(self, frames, delays):
        super().__init__()
        self.frames = frames
        # Milliseconds, one per frame.
        self.delays = delays
        self.index = 0
        self.deadline = 0
        # Mapped images displaying this paintable.
        self.users = 0
        self.cost = sum(f.get_width() * f.get_height() * 4 for f in frames)

    def do_get_flags(self):
        return Gdk.PaintableFlags.STATIC_SIZE

    def do_get_intrinsic_width(self):
        return self.frames[0].get_width()

    def do_get_intrinsic_height(self):
        return self.frames[0].get_height()

    def do_get_intrinsic_aspect_ratio(self):
        return self.frames[0].get_width() / self.frames[0].get_height()

    def do_get_current_image(self):
        return self.frames[self.index]

    def do_snapshot(self, snapshot, width, height):
        self.frames[self.index].snapshot(snapshot, width, height)

    def advance(self, now):
        self.index = (self.index + 1) % len(self.frames)
        self.deadline = now + self.delays[self.index]
        self.invalidate_contents()


class BudgetStore(imagecache.Store):
    """:class:`imagecache.Store` bounded by decoded bytes, not by count,
    and never evicting an animation that an image is showing."""

    __slots__ = ('budget', 'bytes')

    def __init__(self, budget):
        super().__init__(1 << 30)
        self.budget = budget
        self.bytes = 0

    @staticmethod
    def _cost(value):
        return getattr(value, 'cost', 0)

    def put(self, key, value):
        old = self._textures.pop(key, None)
        if old is not None:
            self.bytes -= self._cost(old)
        self._textures[key] = value
        self.bytes += self._cost(value)
        if self.bytes <= self.budget:
            return
        for victim in list(self._textures):
            if self.bytes <= self.budget:
                break
            held = self._textures[victim]
            if victim == key or getattr(held, 'users', 0):
                continue
            del self._textures[victim]
            self.bytes -= self._cost(held)

    def clear(self):
        self.bytes = 0
        super().clear()


class _Decoder:
    """Loads one image through glycin, a frame per async call.  Lives on
    its own callbacks until it calls *done* once."""

    def __init__(self, data, cancellable, done):
        self.cancellable = cancellable
        self.done = done
        self.image = None
        self.frames = []
        self.delays = []
        self.cost = 0
        self.request = None
        self.watchdog = 0
        self.watchdog = GLib.timeout_add(WATCHDOG_MS, self._on_watchdog)
        try:
            loader = Gly.Loader.new_for_bytes(data)
            loader.load_async(cancellable, self._on_image)
        except Exception as error:
            self._finish(None, str(error))

    def _on_watchdog(self):
        self.watchdog = 0
        if self.done is None:
            return GLib.SOURCE_REMOVE
        # Report first, so the request is forgotten with the failure
        # noted, then cancel whatever glycin is still doing.
        self._finish(None, 'timed out')
        if self.cancellable is not None:
            self.cancellable.cancel()
        return GLib.SOURCE_REMOVE

    def _finish(self, value, reason=None):
        done, self.done = self.done, None
        if self.watchdog:
            GLib.source_remove(self.watchdog)
            self.watchdog = 0
        self.image = self.request = None
        self.frames = []
        if done is not None:
            done(value, reason)

    def _on_image(self, loader, result):
        try:
            self.image = loader.load_finish(result)
        except GLib.Error as error:
            self._finish(None, error.message)
            return
        if max(self.image.get_width(), self.image.get_height()) \
                > MAX_DIMENSION:
            self._finish(_STATIC)
            return
        self.request = Gly.FrameRequest.new()
        self.request.set_loop_animation(False)
        self._next()

    def _next(self):
        self.image.get_specific_frame_async(
            self.request, self.cancellable, self._on_frame)

    def _on_frame(self, image, result):
        try:
            frame = image.get_specific_frame_finish(result)
        except GLib.Error as error:
            if error.matches(Gly.loader_error_quark(),
                             Gly.LoaderError.NO_MORE_FRAMES) \
                    and len(self.frames) > 1:
                self._finish(AnimatedEmoji(self.frames, self.delays))
            elif error.matches(Gly.loader_error_quark(),
                               Gly.LoaderError.NO_MORE_FRAMES):
                self._finish(_STATIC)
            else:
                self._finish(None, error.message)
            return
        try:
            texture = GlyGtk4.frame_get_texture(frame)
        except Exception as error:
            self._finish(None, str(error))
            return
        self.cost += texture.get_width() * texture.get_height() * 4
        if len(self.frames) >= MAX_FRAMES or self.cost > MAX_ANIMATION_BYTES:
            self._finish(_STATIC)
            return
        delay = frame.get_delay() // 1000
        if delay < MIN_FRAME_MS:
            delay = DEFAULT_FRAME_MS
        self.frames.append(texture)
        self.delays.append(delay)
        self._next()


_decoding = 0
_queued = collections.deque()


def _decode(data, _key, cancellable, done):
    """Decode *data*, at most MAX_DECODES at a time."""
    _queued.append((data, cancellable, done))
    _pump_decodes()


def _pump_decodes():
    global _decoding
    while _queued and _decoding < MAX_DECODES:
        data, cancellable, done = _queued.popleft()
        if cancellable is not None and cancellable.is_cancelled():
            done(None, imagecache.CANCELLED)
            continue
        _decoding += 1
        _Decoder(data, cancellable, _slot_done(done))


def _slot_done(done):
    def finished(value, reason):
        global _decoding
        _decoding -= 1
        try:
            done(value, reason)
        finally:
            GLib.idle_add(_pump_decodes)
    return finished


class _Clock:
    """The one timeout.  Armed while an animation has a mapped image."""

    def __init__(self):
        self.active = set()
        self.source = 0
        self.due = 0

    @staticmethod
    def _now():
        return GLib.get_monotonic_time() // 1000

    def add(self, anim):
        now = self._now()
        anim.deadline = now + anim.delays[anim.index]
        self.active.add(anim)
        self._arm()

    def remove(self, anim):
        self.active.discard(anim)
        if not self.active:
            self._disarm()

    def _disarm(self):
        if self.source:
            GLib.source_remove(self.source)
            self.source = 0

    def _arm(self):
        if not self.active:
            return
        earliest = min(a.deadline for a in self.active)
        if self.source and self.due <= earliest:
            return
        self._disarm()
        self.due = earliest
        self.source = GLib.timeout_add(
            max(1, earliest - self._now()), self._tick)

    def _tick(self):
        self.source = 0
        now = self._now()
        try:
            for anim in list(self.active):
                # A few ms of slack so animations a hair apart share a
                # wake.
                if anim.deadline <= now + 2:
                    anim.advance(now)
        finally:
            self._arm()
        return GLib.SOURCE_REMOVE


class _Manager:
    def __init__(self):
        self.store = BudgetStore(CACHE_BUDGET_BYTES)
        self.fetcher = imagecache.Fetcher(self.store, _decode,
                                          on_failure=self._on_failure)
        self.clock = _Clock()
        # id(image) -> GObject weak reference, for the mapped images
        # that have an animated original.
        self.mapped = {}
        # url -> monotonic ms until which it is not asked for again.
        self.failed = {}
        self.watching = False

    @staticmethod
    def _now():
        return GLib.get_monotonic_time() // 1000

    def _on_failure(self, url, _reason):
        if len(self.failed) > 512:
            now = self._now()
            self.failed = {u: t for u, t in self.failed.items() if t > now}
        self.failed[url] = self._now() + FAILURE_TTL_MS

    def _has_failed(self, url):
        until = self.failed.get(url)
        if until is None:
            return False
        if until <= self._now():
            del self.failed[url]
            return False
        return True

    def image_mapped(self, image):
        key = id(image)
        if key not in self.mapped:
            self.mapped[key] = image.weak_ref(self._forget, key)
        image._registered = True
        self._watch(image)
        if gate_open(image):
            self.show(image)

    def image_unmapped(self, image):
        self.hide(image)
        image.cancel_pending()
        image._registered = False
        self.mapped.pop(id(image), None)

    def _forget(self, key):
        self.mapped.pop(key, None)

    def show(self, image):
        """Display the animation when it is cached; otherwise start a
        deferred fetch."""
        url = image.animated_url
        if image.anim is not None or url is None:
            return
        found = self.store.get(url)
        if found is _STATIC:
            return
        if found is not None:
            image.attach(found)
            return
        if available() and not self._has_failed(url):
            image.defer_fetch()

    def request(self, image):
        """Fetch *image*'s original now (the deferral has passed)."""
        url = image.animated_url
        if url is None or image.anim is not None or self._has_failed(url):
            return
        image.cancel_fetch()
        cancellable = image._cancellable = Gio.Cancellable.new()
        self.fetcher.fetch(url, _Waiter(image, url), cancellable)

    def attach(self, anim):
        anim.users += 1
        if anim.users == 1:
            self.clock.add(anim)

    def detach(self, anim):
        anim.users -= 1
        if anim.users <= 0:
            anim.users = 0
            self.clock.remove(anim)

    def hide(self, image):
        if image.anim is not None:
            image.detach()

    def _watch(self, image):
        """Follow the gate's inputs (the media policy's signal list), once
        for the process. The handler is a module function, which the policy
        connects directly: nothing here is held that could outlive an image
        (the manager holds images by weak reference)."""
        if self.watching:
            return
        self.watching = True
        from . import mediapolicy
        mediapolicy.connect_changes(_on_gate_changed, image)

    def gate_changed(self):
        for key, ref in list(self.mapped.items()):
            image = ref()
            if image is None:
                self.mapped.pop(key, None)
            elif gate_open(image):
                self.show(image)
            else:
                self.hide(image)


_manager = None


def manager():
    global _manager
    if _manager is None:
        _manager = _Manager()
    return _manager


def _on_gate_changed(*_args):
    manager().gate_changed()


class EmojiImage(Gtk.Image):
    """A ``Gtk.Image`` for one custom emoji.  Shows the static texture,
    and the shared animation while mapped and the gates are open."""

    __gtype_name__ = 'BabbleEmojiImage'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.animated_url = None
        # The AnimatedEmoji being displayed, if any.
        self.anim = None
        self._static = None
        self._url = None
        # Registered with the manager (mapped, with an original).
        self._registered = False
        # The deferred fetch's timeout, and the fetch's cancellable.
        self._defer = 0
        self._cancellable = None

    def bind(self, static_url, animated=None):
        """Show *static_url*, then *animated* (default: the registered
        original of *static_url*) once mapped."""
        from .textemoji import EmojiTextures
        if self.anim is not None:
            self.detach()
        self.cancel_pending()
        self._url = static_url
        self._static = None
        self.animated_url = animated or animated_url(static_url)
        if self.animated_url == static_url:
            self.animated_url = None
        if self.animated_url is not None and self.get_mapped():
            manager().image_mapped(self)
        elif self._registered:
            manager().image_unmapped(self)
        EmojiTextures.bind(_StaticTarget(self), static_url)

    def _set_static(self, texture):
        self._static = texture
        if self.anim is None:
            self.set_from_paintable(texture)

    def defer_fetch(self):
        if self._defer or self._cancellable is not None:
            return
        from .pages import weak_method
        self._defer = GLib.timeout_add(DEFER_MS,
                                       weak_method(self._on_deferred))

    def _on_deferred(self):
        self._defer = 0
        if self.get_mapped() and gate_open(self):
            manager().request(self)
        return GLib.SOURCE_REMOVE

    def cancel_fetch(self):
        cancellable, self._cancellable = self._cancellable, None
        if cancellable is not None:
            cancellable.cancel()

    def cancel_pending(self):
        if self._defer:
            GLib.source_remove(self._defer)
            self._defer = 0
        self.cancel_fetch()

    def _on_animation(self, url, anim):
        if url != self.animated_url:
            return
        self._cancellable = None
        if anim is None or anim is _STATIC or self.anim is not None \
                or not self.get_mapped():
            return
        if not gate_open(self):
            return
        self.attach(anim)

    def attach(self, anim):
        self.anim = anim
        manager().attach(anim)
        self.set_from_paintable(anim)

    def detach(self):
        anim, self.anim = self.anim, None
        manager().detach(anim)
        self.set_from_paintable(self._static)

    def do_map(self):
        Gtk.Image.do_map(self)
        if self.animated_url is not None:
            manager().image_mapped(self)

    def do_unmap(self):
        if self._registered:
            manager().image_unmapped(self)
        Gtk.Image.do_unmap(self)


class _Waiter:
    """Receives a fetch's outcome for one image and the URL it asked
    for, held weakly; the image ignores it if it was rebound since."""

    def __init__(self, image, url):
        self._ref = image.weak_ref()
        self._url = url

    def __call__(self, anim, _reason):
        image = self._ref()
        if image is not None:
            image._on_animation(self._url, anim)


class _StaticTarget:
    """What ``EmojiTextures.bind`` fills: the image's static slot, held
    weakly, and only if it still shows the same URL."""

    def __init__(self, image):
        self._ref = image.weak_ref()
        self._url = image._url

    def set_from_paintable(self, texture):
        image = self._ref()
        if image is not None and image._url == self._url:
            image._set_static(texture)
