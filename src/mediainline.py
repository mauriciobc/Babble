# SPDX-License-Identifier: GPL-3.0-or-later

"""Inline playback of GIFV tiles in the timeline.

One :class:`InlinePlayer` per window owns at most one
:class:`mediaengine.Engine` and one :class:`mediastream.EngineStream`, and
lends them to the one GIFV tile that is at least half visible and has the
largest visible share of its scrolled window. It is created by the first
tile that is mapped in the window and lives as long as the window.

* **Tiles** (:class:`GifvTile`, built by :mod:`mediabox`) register on map
  and unregister on unmap, so a recycled row costs the player nothing. The
  player holds them by GObject weak reference.
* **Gates**, read live: ``mediaplayback.may_autoplay_loop`` (the key, the
  animation settings, power saving), ``media-autoload`` with the metered
  state, and ``remote-media``. A tile whose only copy is on another server
  is never played unless that setting is ``load``: nothing here asks.
* **Switching** tiles is ``stop()`` then ``load()`` on the same engine. The
  tile keeps its poster until the first frame arrives, and gets it back
  when it stops being the active one.
* **Release.** With no candidate for :data:`RELEASE_DELAY_MS`, with the
  window hidden or closed, or while the media viewer is open (it has its
  own engine), the engine is handed to its deferred release. It is never
  dropped directly (see :mod:`mediaengine`).
* **No controls.** A GIFV is a muted loop with no sound or progress to
  control; the Autoplay Animated Media preference, the animation settings
  and power saving turn it off for everyone, and a click opens the viewer.

GStreamer is imported only when the first tile plays.
"""

import itertools

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from . import mastodon  # noqa: E402
from . import mediaplayback  # noqa: E402
from . import pages  # noqa: E402
from .remotemedia import media_url  # noqa: E402

# A tile must have at least this share of itself inside the viewport.
MIN_VISIBLE = 0.5
# How long the engine is kept without a candidate before it is released.
RELEASE_DELAY_MS = 2000

_tokens = itertools.count(1)


def visible_fraction(tile, viewport):
    """The share of *tile* that lies inside *viewport*'s own bounds, 0..1."""
    found, rect = tile.compute_bounds(viewport)
    if not found:
        return 0.0
    width, height = rect.get_width(), rect.get_height()
    if width <= 0 or height <= 0:
        return 0.0
    left = max(rect.get_x(), 0.0)
    top = max(rect.get_y(), 0.0)
    right = min(rect.get_x() + width, float(viewport.get_width()))
    bottom = min(rect.get_y() + height, float(viewport.get_height()))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top) / (width * height)


def _scrolled_ancestor(widget):
    node = widget.get_parent()
    while node is not None and not isinstance(node, Gtk.ScrolledWindow):
        node = node.get_parent()
    return node


class GifvTile(Gtk.Overlay):
    """The overlay of a GIFV tile in a media grid: registers with its
    window's :class:`InlinePlayer` while it is mapped."""

    __gtype_name__ = 'BabbleGifvTile'

    def __init__(self, attachment, picture, **kwargs):
        super().__init__(**kwargs)
        self.token = next(_tokens)
        self.attachment = attachment
        self.picture = picture
        self._poster = None
        self._shown = False
        self._player = None
        pages.connect_weak(picture, 'notify::paintable', self._on_paintable)

    def do_map(self):
        Gtk.Overlay.do_map(self)
        player = player_for(self)
        if player is not None:
            self._player = player.weak_ref()
            player.register(self)

    def do_unmap(self):
        player = self._player() if self._player is not None else None
        self._player = None
        if player is not None:
            player.unregister(self)
        Gtk.Overlay.do_unmap(self)

    def _on_paintable(self, _picture, _pspec):
        # The poster arrived: the tile may be a candidate now.
        player = self._player() if self._player is not None else None
        if player is not None and not self._shown:
            player.schedule()

    def poster_ready(self):
        """Whether the picture shows a still frame already: playing waits
        for it, or a late poster would replace the video."""
        return isinstance(self.picture.get_paintable(), Gdk.Texture)

    def show_stream(self, stream):
        self._poster = self.picture.get_paintable()
        self.picture.set_paintable(stream)
        self._shown = True

    def show_poster(self):
        """Put the poster back; a no-op when the stream was never shown."""
        if self._shown and self._poster is not None:
            self.picture.set_paintable(self._poster)
        self._poster = None
        self._shown = False


class InlinePlayer(GObject.Object):
    """The window's inline player: see the module docstring."""

    __gtype_name__ = 'BabbleInlinePlayer'

    def __init__(self, root):
        super().__init__()
        self._root = root.weak_ref(pages.weak_method(self._on_root_gone))
        self._tiles = {}
        self._watched = {}
        self._engine = None
        self._stream = None
        self._active = None
        self._shown = False
        self._frame_seen = False
        self._frame_handler = 0
        self._failed = set()
        self._unavailable = False
        self._viewer_open = False
        self._idle = 0
        self._painted = 0
        self._clock = None
        self._release_source = 0
        self._gates_connected = False
        pages.connect_weak(root, 'unmap', self._on_root_unmap)
        pages.connect_weak(root, 'map', self._on_root_map)
        pages.connect_weak(root, 'close-request', self._on_root_close)

    # Registration

    def register(self, tile):
        self._tiles[tile.token] = tile.weak_ref()
        self._connect_gates()
        scrolled = _scrolled_ancestor(tile)
        if scrolled is not None:
            self._watch(scrolled.get_vadjustment())
        self.schedule()

    def unregister(self, tile):
        if self._active == tile.token:
            self._stop_active()
        self._tiles.pop(tile.token, None)
        self.schedule()

    def _watch(self, adjustment):
        """Follow a scrolled window's vertical adjustment, once."""
        key = hash(adjustment)
        known = self._watched.get(key)
        if known is not None and known() is adjustment:
            return
        self._watched = {k: r for k, r in self._watched.items()
                         if r() is not None}
        self._watched[key] = adjustment.weak_ref()
        pages.connect_weak(adjustment, 'value-changed', self._on_moved)
        pages.connect_weak(adjustment, 'changed', self._on_moved)

    def _connect_gates(self):
        """Follow every gate, once per player (the viewer's loop gate
        follows the same signals)."""
        if self._gates_connected:
            return
        self._gates_connected = True
        from . import sysstate
        monitor = sysstate.monitor()
        for name in ('power-saver', 'metered'):
            pages.connect_weak(monitor, 'notify::' + name, self._on_moved)
        root = self._root()
        gtk_settings = Gtk.Settings.get_for_display(root.get_display())
        for name in ('gtk-enable-animations', 'gtk-interface-reduced-motion'):
            pages.connect_weak(gtk_settings, 'notify::' + name,
                               self._on_moved)
        settings = pages.settings()
        for key in ('autoplay-animated-media', 'media-autoload',
                    'remote-media'):
            pages.connect_weak(settings, 'changed::' + key, self._on_moved)

    # Re-evaluation: one pass for any number of causes

    def _on_moved(self, *_args):
        self.schedule()

    def schedule(self):
        """Evaluate once, after the next frame. A scroll moves the tiles
        only when the frame lays them out, so positions read earlier
        (from an idle, say) are the ones from before the scroll."""
        if self._idle or self._painted:
            return
        root = self._root()
        clock = root.get_frame_clock() if root is not None else None
        if clock is None:
            self._idle = GLib.idle_add(
                pages.weak_method(self._on_idle),
                priority=GLib.PRIORITY_DEFAULT_IDLE)
            return
        self._clock = clock.weak_ref()
        self._painted = pages.connect_weak(
            clock, 'after-paint', self._on_painted)
        clock.request_phase(Gdk.FrameClockPhase.PAINT)

    def _cancel_pending(self):
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        clock = self._clock() if self._clock is not None else None
        if self._painted and clock is not None:
            clock.disconnect(self._painted)
        self._painted = 0

    def _on_idle(self):
        self._idle = 0
        self._evaluate()
        return GLib.SOURCE_REMOVE

    def _on_painted(self, clock):
        clock.disconnect(self._painted)
        self._painted = 0
        self._evaluate()

    def _evaluate(self):
        root = self._root()
        if root is None:
            return
        target = None
        if root.get_mapped() and not self._viewer_open \
                and not self._unavailable:
            target = self._pick(root)
        self._apply(target)

    def _live_tiles(self):
        live = []
        for token, ref in list(self._tiles.items()):
            tile = ref()
            if tile is None:
                del self._tiles[token]
            elif tile.get_mapped():
                live.append(tile)
        return live

    def _gates_open(self, root):
        if not mediaplayback.may_autoplay_loop(root):
            return False
        mode = pages.settings().get_string('media-autoload')
        return not mediaplayback.should_defer_stream(
            mode, mediaplayback._system_state('metered'))

    def _may_fetch(self, tile):
        """The remote-media rule: a copy on another server plays only when
        the user said to load such media; nothing is asked from here."""
        url, is_remote = media_url(tile.attachment)
        if not url or url in self._failed:
            return False
        return (not is_remote
                or pages.settings().get_string('remote-media') == 'load')

    def _pick(self, root):
        """The tile that should play, or None."""
        tiles = self._live_tiles()
        if not tiles or not self._gates_open(root):
            return None
        best = best_key = None
        for tile in tiles:
            # The active tile shows the stream, not its poster.
            if not self._may_fetch(tile) or not (
                    tile.token == self._active or tile.poster_ready()):
                continue
            scrolled = _scrolled_ancestor(tile)
            fraction = visible_fraction(
                tile, scrolled if scrolled is not None else root)
            if fraction < MIN_VISIBLE - 1e-6:
                continue
            # The largest share wins; a tie keeps the tile that plays,
            # then takes the topmost.
            top = tile.compute_bounds(root)[1].get_y()
            key = (round(fraction, 3), tile.token == self._active, -top)
            if best_key is None or key > best_key:
                best, best_key = tile, key
        return best

    def _active_tile(self):
        ref = self._tiles.get(self._active)
        return ref() if ref is not None else None

    def _apply(self, target):
        active = self._active_tile()
        if target is not None and active is target:
            return
        if self._active is not None:
            self._stop_active()
        if target is None:
            self._arm_release()
        else:
            self._start(target)

    # Playing

    def _start(self, tile):
        self._cancel_release()
        url = media_url(tile.attachment)[0]
        try:
            # Imported here: GStreamer is not loaded until media plays.
            from . import mediaengine
            from .mediastream import EngineStream
            if self._engine is None:
                self._engine = mediaengine.Engine(
                    user_agent=mastodon.user_agent())
            stream = EngineStream(self._engine)
            self._engine.load(url)
        except (ImportError, ValueError, RuntimeError):
            self._unavailable = True
            self._release_engine()
            return
        stream.set_loop(True)
        stream.set_muted(True)
        self._stream = stream
        self._active = tile.token
        self._shown = False
        self._frame_seen = False
        pages.connect_weak(stream, 'notify::error', self._on_error)
        pages.connect_weak(stream, 'notify::prepared', self._on_prepared)
        self._frame_handler = pages.connect_weak(
            stream, 'invalidate-contents', self._on_frame)
        if stream.get_error() is not None:
            self._on_error(stream, None)

    def _on_prepared(self, stream, _pspec):
        if stream is not self._stream or not stream.is_prepared() \
                or stream.get_error() is not None:
            return
        # Prepared once, from the preroll; the first prepare plays.
        stream.play()
        self._maybe_show()

    def _on_frame(self, stream):
        if stream is not self._stream:
            return
        self._frame_seen = True
        self._maybe_show()

    def _maybe_show(self):
        """Swap the poster for the video at the first frame."""
        stream, tile = self._stream, self._active_tile()
        if self._shown or stream is None or tile is None \
                or not self._frame_seen or not stream.is_prepared():
            return
        self._shown = True
        if self._frame_handler:
            stream.disconnect(self._frame_handler)
            self._frame_handler = 0
        tile.show_stream(stream)

    def _on_error(self, stream, _pspec):
        if stream is not self._stream or stream.get_error() is None:
            return
        tile = self._active_tile()
        if tile is not None:
            self._failed.add(media_url(tile.attachment)[0])
        self._stop_active()
        self._arm_release()
        self.schedule()

    def _stop_active(self):
        """Stop the tile that plays and give it its poster back. The
        engine stays, for the next tile or the release timer."""
        tile = self._active_tile()
        stream, self._stream = self._stream, None
        self._active = None
        self._shown = False
        self._frame_seen = False
        self._frame_handler = 0
        if stream is not None:
            stream.pause()
            stream.detach()
        if tile is not None:
            tile.show_poster()
        if self._engine is not None:
            self._engine.stop()

    # Release

    def _arm_release(self):
        if self._engine is None or self._release_source:
            return
        self._release_source = GLib.timeout_add(
            RELEASE_DELAY_MS, pages.weak_method(self._on_release_timeout))

    def _cancel_release(self):
        if self._release_source:
            GLib.source_remove(self._release_source)
            self._release_source = 0

    def _on_release_timeout(self):
        self._release_source = 0
        if self._active is None:
            self._release_engine()
        return GLib.SOURCE_REMOVE

    def _release_engine(self):
        self._cancel_release()
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.release()

    def shutdown(self):
        """Stop, restore the poster and hand the engine to its release."""
        if self._active is not None:
            self._stop_active()
        self._release_engine()

    # The window

    def _on_root_unmap(self, _root):
        self._cancel_pending()
        self.shutdown()

    def _on_root_close(self, _root):
        self.shutdown()
        return False

    def _on_root_map(self, _root):
        self.schedule()

    def _on_root_gone(self):
        self.shutdown()

    def viewer_opened(self):
        self._viewer_open = True
        self.shutdown()

    def viewer_closed(self):
        self._viewer_open = False
        self.schedule()


def existing_player(widget):
    """The player of the window *widget* is in, or None."""
    root = widget.get_root() if widget is not None else None
    return getattr(root, '_babble_inline_player', None)


def player_for(widget):
    """The player of the window *widget* is in, created on first use; None
    when it is in no window. Kept as an attribute of the window, which
    holds it by weak reference only, so there is no cycle."""
    root = widget.get_root() if widget is not None else None
    if not isinstance(root, Gtk.Window):
        return None
    player = getattr(root, '_babble_inline_player', None)
    if player is None:
        player = InlinePlayer(root)
        root._babble_inline_player = player
    return player


def viewer_opened(window):
    """The media viewer opened on *window*: it has its own engine."""
    player = player_for(window)
    if player is not None:
        player.viewer_opened()


def viewer_closed(window):
    player = existing_player(window)
    if player is not None:
        player.viewer_closed()
