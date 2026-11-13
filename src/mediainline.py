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
* **Gates**, read live through :mod:`mediapolicy` (the inline key, the
  loop rule, ``media-autoload`` with the metered state, and
  ``remote-media``), once per evaluation. A tile whose only copy is on
  another server is never played unless that setting is ``load``:
  nothing here asks.
* **Switching** tiles is ``stop()`` then ``load()`` on the same engine. The
  tile keeps its poster until the first frame arrives, and gets it back
  when it stops being the active one.
* **Release.** With no candidate for :data:`RELEASE_DELAY_MS`, with the
  window hidden or closed, or while the media viewer is open (it has its
  own engine), the engine is handed to its deferred release. It is never
  dropped directly (see :mod:`mediaengine`).
* **Off by default.** ``inline-animated-media`` must be on as well, and
  with it off no engine is ever created; tiles still register (a few
  dictionary operations), which the bench measured as free.
* **Pause.** A tile that plays shows a Pause Animation button, built when
  it first plays. The choice is kept in the row's ``ui`` by attachment id,
  so recycling keeps it, and a paused tile is not resumed on its own; a
  click on the tile itself opens the viewer.

GStreamer is imported only when the first tile plays.
"""

import itertools

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from . import mastodon  # noqa: E402
from . import mediapolicy  # noqa: E402
from . import pages  # noqa: E402
from .remotemedia import media_url  # noqa: E402

# A tile must have at least this share of itself inside the viewport.
MIN_VISIBLE = 0.5
# How long the engine is kept without a candidate before it is released.
RELEASE_DELAY_MS = 2000
# The key of the row's ``ui`` that holds the attachment ids the user paused.
PAUSED_KEY = 'inline_paused'

_tokens = itertools.count(1)


def inline_enabled():
    """Whether Play Animated GIFs in Timelines is on."""
    return pages.settings().get_boolean('inline-animated-media')


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

    def __init__(self, attachment, ui, picture, **kwargs):
        super().__init__(**kwargs)
        self.token = next(_tokens)
        self.attachment = attachment
        self.ui = ui if ui is not None else {}
        self.picture = picture
        self._poster = None
        self._shown = False
        self._button = None
        self._player = None
        pages.connect_weak(picture, 'notify::paintable', self._on_paintable)
        if self.user_paused:
            self._sync_button()

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

    @property
    def user_paused(self):
        return self.attachment.get('id') in self.ui.get(PAUSED_KEY, ())

    def set_user_paused(self, paused):
        ids = self.ui.setdefault(PAUSED_KEY, set())
        if paused:
            ids.add(self.attachment.get('id'))
        else:
            ids.discard(self.attachment.get('id'))
        self._sync_button()

    def show_stream(self, stream):
        self._poster = self.picture.get_paintable()
        self.picture.set_paintable(stream)
        self._shown = True
        self._sync_button()

    def show_poster(self):
        """Put the poster back; a no-op when the stream was never shown."""
        if self._shown and self._poster is not None:
            self.picture.set_paintable(self._poster)
        self._poster = None
        self._shown = False
        self._sync_button()

    def sync_button(self):
        self._sync_button()

    def _sync_button(self):
        """The Pause Animation button: built the first time a tile plays
        or is paused, so idle tiles carry no extra widget. Only while
        Play Animated GIFs in Timelines is on: with it off a paused tile
        would carry a button that starts nothing."""
        enabled = inline_enabled()
        paused = self.user_paused
        if self._button is None:
            if not enabled or not (self._shown or paused):
                return
            self._button = Gtk.Button(
                halign=Gtk.Align.END, valign=Gtk.Align.START,
                margin_top=6, margin_end=6)
            self._button.add_css_class('osd')
            self._button.add_css_class('circular')
            pages.connect_weak(self._button, 'clicked', self._on_clicked)
            self.add_overlay(self._button)
        button = self._button
        button.set_visible(enabled and (self._shown or paused))
        if paused:
            button.set_icon_name('media-playback-start-symbolic')
            # Translators: the button over an animated image that you
            # paused; it starts the animation again.
            pages.set_accessible_label(button, _('Play Animation'))
        else:
            button.set_icon_name('media-playback-pause-symbolic')
            # Translators: the button over an animated image that plays;
            # it stops the animation.
            pages.set_accessible_label(button, _('Pause Animation'))

    def _on_clicked(self, _button):
        paused = not self.user_paused
        self.set_user_paused(paused)
        player = self._player() if self._player is not None else None
        if player is not None:
            player.user_toggled(self, paused)


class InlinePlayer(GObject.Object):
    """The window's inline player: see the module docstring."""

    __gtype_name__ = 'BabbleInlinePlayer'

    def __init__(self, root):
        super().__init__()
        self._root = root.weak_ref(pages.weak_method(self._on_root_gone))
        self._tiles = {}
        self._watched = {}
        from .mediaengineslot import EngineSlot
        self._engine_slot = EngineSlot()
        self._stream = None
        self._active = None
        self._shown = False
        self._frame_seen = False
        self._frame_handler = 0
        self._pinned = 0
        self._failed = set()
        # The GStreamer warm-up: 0 not started, 1 module imported, 2 done.
        self._warm_stage = 0
        self._warm_source = 0
        self._unavailable = False
        self._viewer_open = False
        self._idle = 0
        self._painted = 0
        self._clock = None
        self._release_source = 0
        self._gates_connected = False
        # The key's last known state: off, nothing below does any work.
        self._on = False
        pages.connect_weak(root, 'unmap', self._on_root_unmap)
        pages.connect_weak(root, 'map', self._on_root_map)
        pages.connect_weak(root, 'close-request', self._on_root_close)

    # Registration

    def register(self, tile):
        self._tiles[tile.token] = tile.weak_ref()
        self._connect_gates()
        tile.sync_button()
        if self._on:
            self._watch_tile(tile)
            self._warm_up()
            self.schedule()

    def _warm_up(self):
        """Load GStreamer in two idle steps before the first tile plays.
        The first play otherwise imports it in one dispatch of about
        45 ms (the typelib 25 ms, ``Gst.init`` 12 ms, measured), longer
        than three frames, in the middle of a scroll; each step is
        shorter than two."""
        if self._warm_stage >= 2 or self._warm_source or self._unavailable:
            return
        self._warm_source = GLib.idle_add(
            pages.weak_method(self._on_warm_step),
            priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _on_warm_step(self):
        self._warm_source = 0
        try:
            # Imported here: GStreamer is not loaded until media may play.
            from . import mediaengine
            if self._warm_stage == 0:
                self._warm_stage = 1
                self._warm_up()
            else:
                mediaengine.warm_up()
                self._warm_stage = 2
        except (ImportError, ValueError, RuntimeError):
            self._unavailable = True
        return GLib.SOURCE_REMOVE

    def unregister(self, tile):
        if self._active == tile.token:
            self._stop_active()
        self._tiles.pop(tile.token, None)
        self.schedule()

    def _watch_tile(self, tile):
        scrolled = _scrolled_ancestor(tile)
        if scrolled is not None:
            self._watch(scrolled.get_vadjustment())

    def _watch(self, adjustment):
        """Follow a scrolled window's vertical adjustment, once. Only
        while the key is on: off, a scroll costs the player nothing."""
        key = hash(adjustment)
        known = self._watched.get(key)
        if known is not None and known[0]() is adjustment:
            return
        self._watched = {k: v for k, v in self._watched.items()
                         if v[0]() is not None}
        self._watched[key] = (adjustment.weak_ref(), [
            pages.connect_weak(adjustment, 'value-changed', self._on_moved),
            pages.connect_weak(adjustment, 'changed', self._on_moved)])

    def _unwatch_all(self):
        watched, self._watched = self._watched, {}
        for ref, ids in watched.values():
            adjustment = ref()
            if adjustment is not None:
                for handler in ids:
                    adjustment.disconnect(handler)

    def _on_key_changed(self, *_args):
        """Play Animated GIFs in Timelines changed: start or stop all
        per-scroll work, and bring the tiles' buttons along."""
        on = inline_enabled()
        if on == self._on:
            return
        self._on = on
        tiles = [t for t in (r() for r in self._tiles.values())
                 if t is not None]
        if on:
            for tile in tiles:
                self._watch_tile(tile)
            if tiles:
                self._warm_up()
            self.schedule()
        else:
            self._unwatch_all()
            self._cancel_pending()
            self._evaluate()
        for tile in tiles:
            tile.sync_button()

    def _connect_gates(self):
        """Follow every gate, once per player. The signals that can change
        an answer are the media policy's list; the player's own switch is
        also connected to ``_on_key_changed`` (first, so ``_on`` is
        current when the policy's handler runs), because turning it on or
        off is the player's lifecycle (it starts and stops the per-scroll
        work), not only a re-evaluation."""
        if self._gates_connected:
            return
        self._gates_connected = True
        self._on = inline_enabled()
        pages.connect_weak(pages.settings(), 'changed::inline-animated-media',
                           self._on_key_changed)
        mediapolicy.connect_changes(self._on_moved, self._root())

    # Re-evaluation: one pass for any number of causes

    def _on_moved(self, *_args):
        self.schedule()

    def schedule(self):
        """Evaluate once, after the next frame. A scroll moves the tiles
        only when the frame lays them out, so positions read earlier
        (from an idle, say) are the ones from before the scroll."""
        if not self._on or self._idle or self._painted:
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

    def _gates_state(self, root):
        """The media state when an inline tile may play under it, else
        None. The player's own switch is read first on its own, so with it
        off nothing else (GTK's settings, the system monitor) is touched.
        One read per evaluation: :meth:`_may_fetch` reuses it."""
        if not inline_enabled():
            return None
        state = mediapolicy.read_state(root)
        return state if mediapolicy.inline_may_play(state) else None

    def _may_fetch(self, tile, state):
        """The remote-media rule: a copy on another server plays only when
        the policy allows it without asking; nothing is asked from here."""
        url, is_remote = media_url(tile.attachment)
        if not url or url in self._failed:
            return False
        return mediapolicy.remote_access(state, is_remote) \
            == mediapolicy.ALLOW

    def _pick(self, root):
        """The tile that should play, or None."""
        tiles = self._live_tiles()
        state = self._gates_state(root) if tiles else None
        if state is None:
            return None
        best = best_key = frozen = None
        for tile in tiles:
            # The active tile shows the stream, not its poster.
            if not self._may_fetch(tile, state) or not (
                    tile.token == self._active or tile.poster_ready()):
                continue
            scrolled = _scrolled_ancestor(tile)
            fraction = visible_fraction(
                tile, scrolled if scrolled is not None else root)
            if fraction < MIN_VISIBLE - 1e-6:
                if self._pinned == tile.token:
                    self._pinned = 0
                continue
            if tile.user_paused:
                if tile.token == self._active:
                    frozen = tile
                continue
            # A tile the reader just started wins; then the largest share;
            # a tie keeps the tile that plays, then takes the topmost.
            top = tile.compute_bounds(root)[1].get_y()
            key = (tile.token == self._pinned, round(fraction, 3),
                   tile.token == self._active, -top)
            if best_key is None or key > best_key:
                best, best_key = tile, key
        # A tile the reader paused keeps its frozen frame, and the others
        # wait, while it is in view; only a tile the reader started takes
        # over.
        if frozen is not None and not (best_key and best_key[0]):
            return frozen
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

    @property
    def _engine(self):
        return self._engine_slot.engine

    def _start(self, tile):
        self._cancel_release()
        url = media_url(tile.attachment)[0]
        from .mediaengineslot import PlaybackUnavailable
        try:
            stream = self._engine_slot.start(
                url, user_agent=mastodon.user_agent(), gl=False)
        except PlaybackUnavailable:
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

    def user_toggled(self, tile, paused):
        """The Pause Animation button was pressed on *tile*, whose choice
        is already stored."""
        if paused:
            if self._pinned == tile.token:
                self._pinned = 0
        else:
            self._pinned = tile.token
        if self._active == tile.token and self._stream is not None \
                and self._stream.is_prepared():
            if paused:
                self._stream.pause()
            else:
                self._stream.play()
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
        self._engine_slot.stop(stream)
        if tile is not None:
            tile.show_poster()

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
        self._engine_slot.release()

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
    """The media viewer opened on *window*: it has its own engine. With
    no player there is nothing playing inline to give way."""
    player = existing_player(window)
    if player is not None:
        player.viewer_opened()


def viewer_closed(window):
    player = existing_player(window)
    if player is not None:
        player.viewer_closed()
