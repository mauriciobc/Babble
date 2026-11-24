# Picture-in-Picture — product plan

Status: historical — implemented (`src/mediapip.py`, `src/pipgeometry.py`,
`src/mediaviewer.py` `minimize_to_pip` → `mediapip.open_pip`,
`src/test-media-pip.py`). Design record; body is not current guidance.
Working note (English only, per the documentation policy). The interaction
and wording contract the DEV and QA agents implement against is
`Docs/pip-ux-spec.md`; the engineering contract is the working note held
by the session (mirrored in this file's Engineering plan section).

## Problem

Babble plays a post's video only inside the media viewer, which covers the
whole window. To keep watching while reading anything else, the reader
must leave the video paused or stare at the viewer. A video that plays in
a small card over the window — picture-in-picture — removes that choice.

## Discovery findings (code facts, measured)

- One `mediaengine.Engine` (GstPlay + `gtk4paintablesink`) per media
  viewer; `mediastream.EngineStream` (a `Gtk.MediaStream` that is also a
  `Gdk.Paintable`) wraps it and is shown by `Gtk.Video` or `Gtk.Picture`
  in the viewer's `media_bin`.
- The engine decodes independently of which widget shows its paintable,
  so playback can move between surfaces without touching GStreamer: the
  same `EngineStream` can be set on a new widget while it plays.
- Closing the viewer pauses the stream, detaches it, and hands the engine
  to a deferred release; freeing a GstPlay outside that order crashes
  (see `mediaengine` and `test-mediaviewer-teardown.py`).
- The window's inline GIFV player (`mediainline.InlinePlayer`) already
  gives way to another playing surface (`viewer_opened`/`viewer_closed`)
  to avoid two decoders.
- Media policy (`mediapolicy`) gates what starts by itself; playback the
  user began continues when metering or power saver turns on.
- `pip-in-symbolic` is not in the installed Adwaita set, so the two icons
  must be bundled (the `babble-*-symbolic` pattern in
  `data/icons/hicolor/symbolic/actions/` plus the gresource aliases).
- There is exactly one viewer per window (`mediaviewer._open_viewer`); a
  second `present_media` closes the first. PiP follows the same rule.

## Decisions

- **An overlay card of the window, not a second window.** Wayland gives
  ordinary windows no keep-above, layer-shell is a new dependency, and a
  separate window breaks the single-surface test setup. The card is an
  overlay child of the same `Gtk.Overlay` that hosts the viewer
  (`media_overlay(window)`).
- **One playing surface per window.** Opening the media viewer while a
  card plays closes the card (engine released); expanding the card moves
  playback back into the viewer with the engine and position intact.
- **Eligible media: `video` and `gifv`.** Audio has nothing to watch.
- **Playback the user put in PiP keeps playing** regardless of metered
  connections or power saver (the viewer's own rule), and a GIFV in PiP
  keeps looping; the inline GIFV player stands down while a card exists.
- **No new GSettings.** The card's position is remembered per window in
  memory only; nothing else is tunable in v1.
- **Idle inhibition transfers with playback**: a playing video in PiP
  keeps the session awake, exactly as in the viewer.

## User stories

1. As a reader, I can send the video I am watching in the viewer to a
   small card and close the viewer, so my timelines stay scrollable while
   the video keeps playing.
2. As a reader, I can pause, resume and stop the card's video from the
   card itself.
3. As a reader, I can expand the card back into the viewer and find the
   video at the same position, still playing.
4. As a reader, I can drag the card anywhere over my window so it does
   not cover what I am reading, and it snaps to the nearest corner.
5. As a reader, opening another post's media replaces the card's
   playback instead of decoding two videos at once.
6. As a screen-reader user, the card's buttons have accessible names and
   state, and the `p` key does what the button does.

## Engineering plan

New module `src/mediapip.py` (target < 500 lines):

- `PiPCard(Gtk.Overlay)`: the video `Gtk.Picture` bound to the transferred
  `EngineStream`, an OSD control bar (play/pause, expand, close), drag to
  move with snap to the nearest corner (margins only, RTL-safe), and the
  teardown discipline: pause → `stream.detach()` → `engine.release()`.
  It owns `engine` + `stream` from the moment `open_pip` is called.
- `open_pip(viewer)`: validate a playing `video`/`gifv` stream, take the
  engine and stream from the viewer, mark the viewer for handover, close
  the viewer (which removes its media widget without pausing or
  detaching), add the card to `media_overlay(window)`, suppress the
  inline player, inhibit idle for video.
- `restore(card)`: detach the card from the overlay without stopping
  playback and open the viewer again with `present_media(..., adopt=...)`;
  the viewer adopts the same engine and stream at the current attachment,
  sets `_autoplayed` so nothing restarts, and playback continues.
- Registry keyed by window, mirroring `mediaviewer._open_viewer`; the
  card closes (engine released) when the window closes, when another
  viewer opens, and on its own close button.

Viewer changes (`mediaviewer.py`, `mediaviewerchrome.py`,
`mediaplayback.py`):

- `MediaViewer.minimize_to_pip()`, a `pip_button` (visible for
  video/GIFV), and the `p` key.
- A `_pip_handover` close path: skip pause/detach/stop/release, skip
  volume save (the stream keeps its state), clear buffering and idle
  inhibition.
- `present_media(..., adopt=)` and `_show_adopted()`: factor the per-kind
  media widget construction out of `_start_stream` so both paths build
  the same view.

Inline player changes (`mediainline.py`): a `_pip_open` gate next to
`_viewer_open`, with `pip_opened`/`pip_closed` module functions.

Assets: `babble-pip-in-symbolic` and `babble-pip-out-symbolic` icons; CSS
classes `pip-card`, `pip-controls` in `src/style.css` using libadwaita
variables. Build wiring: `mediapip.py` in `src/meson.build`'s install
list, icons in `babble.gresource.xml`.

## Task list

- T1 UX spec (`Docs/pip-ux-spec.md`): interaction, states, wording.
- T2 `src/mediapip.py` (card, handover, restore, registry).
- T3 Viewer integration: button, key, handover close path, adopt path.
- T4 Inline player suppression; window-close lifecycle.
- T5 Icons + CSS + build wiring.
- T6 `src/test-media-pip.py` (behaviors + minimize/restore cycles +
  engine-release assertions) and harness runs.
- T7 Docs (`Docs/features.md` + manual media page, EN and pt_BR), pot/po.

## Test plan

Headless mutter at 1280x1024 (see AGENTS.md). The new test is a
standalone `src/test-*.py` under the fatal environment: a loopback media
fixture server, minimize → assert the card plays and the engine is not
released; close → assert deferred release empties `mediaengine._releasing`
and `_all`; restore → assert the viewer adopted the same engine object;
second viewer → assert the card closed; then 20 minimize/restore/close
cycles with no engine left and no release timeout. `style css` must still
pass with the new rules; `ui strings` with the new labels.
