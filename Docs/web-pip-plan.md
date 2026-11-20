# Picture-in-Picture for the web column — product plan

Status: implemented. Working note (English only, per the
documentation policy). The interaction and wording contract the DEV and
QA agents implement against is `Docs/web-pip-ux-spec.md` (T1, done);
this file holds the problem, the code facts, the spike measurements and
the engineering plan.

## Problem

A video playing in the web column (`webcolumn.py`, the WebKitGTK view
beside the timeline) has no picture-in-picture path. The reader chooses
between three bad states:

- leave the column pinned over or beside the timeline and stop reading;
- collapse the sidebar, which unmaps the view — the video is invisible
  (rendering stops) and only its audio may continue;
- close the column, which runs `dispose_column()` → `stop_loading()`:
  the page and its video die.

The engine-based PiP card (`mediapip.py`) cannot take this video:
WebKit decodes and renders its own media and hands out no paintable,
and the HTML5 `requestPictureInPicture` API is not exposed by
WebKitGTK today (see "Upstream state" under Reference
implementations — nothing on the GLib ports is coming in the near
term either). The card must be hosted by Babble and must hold the
live `WebKit.WebView` itself.

## Discovery findings (code facts)

- The column lives in `window.web_slot`, the sidebar of
  `web_split` (`AdwOverlaySplitView`, end position, collapsed below
  1100 px window width). It is built on the first web link
  (`webcolumn._show`), one `WebColumn` per window.
- `webcolumn.watch_split` keeps the column in step with the split: a
  hide that follows `notify::collapsed` within the same main-loop turn
  is the layout's doing and keeps the page; any other hide is the user
  dismissing the column and calls `column.dispose_column()`. A PiP
  handover hides the sidebar with the page alive, so this rule must
  learn about the card.
- `dispose_column()` tears the page down once loading settles
  (deferred, `TEARDOWN_GRACE_SECONDS = 3`, because cancelling and
  unmapping a view before its first load has started crashes
  WebKitGTK); `_teardown()` is `stop_loading()` plus unparenting.
  `load_uri()` on a disposed-with-timer column revives it. The card
  path must reuse these states, not route around them.
- A plugin panel page (`present_panel`) ties the column to the panel:
  `dispose_column()` ends it with `'hidden'`, a new load ends it with
  `'replaced'`. `load_uri` and the `_disposed`/`_teardown_timer`
  machinery live on the `WebColumn` widget, so reparenting the whole
  column preserves panel, error page, actions and teardown discipline.
- WebKit's GStreamer backend ties media to visibility. WPE WebKit
  2.54's release notes list "stopping the pipelines of muted,
  invisible video elements" as a resource improvement, and the 2.54
  cycle's bugs (webkit #327551, #330831, and Playwright's
  investigation of the same path) show muted videos that leave the
  viewport being suspended — sometimes deadlocking the WebProcess.
  Media in an offscreen window does not play at all (webkit #232860,
  still NEW). Two consequences: a hidden column is not a PiP (the
  pipeline itself may stop, so the keep-alive-when-hidden idea is
  weaker than it looks), and the card must keep the video element
  *visible in the page's layout* — which the 320 px reflow does for
  players that compact (YouTube), and may not for every site.
- The viewer's PiP card (`mediapip.PiPCard`) is an overlay child of the
  window's content `Gtk.Overlay` (`mediaviewer.media_overlay`), END/END
  aligned and positioned by `margin_end`/`margin_bottom` alone; drag is
  a flick to a corner (GTK 4.24 has no widget transform and
  reparenting a mapped widget mid-gesture is fatal), arrows move it
  while the card root has focus, and the card closes on window close.
  The registry is `card_for(window)`, kept on the window object.
- The engine card's video area is not focusable and not a control; a
  web card's video area *is* the page: clicks, scroll and keys (a
  YouTube player's arrows seek) belong to it. The mediapip drag
  gestures and key controller cannot sit on it.

## Decisions

A throwaway spike tested the load-bearing questions (measured
2026-11-19; WebKitGTK 2.54.2; muted autoplay MP4 over a `data:` URI;
`currentTime` read back through `evaluate_javascript`; reparent =
unparent → `add_overlay` in the same main-loop turn):

- **Reparenting a *mapped, playing* view survived ten cycles on the
  GL renderer path** (`GSK_RENDERER=gl`, llvmpipe — see the caveat):
  ~2.0 s of video per ~2.0 s cycle throughout, view mapped at every
  cycle, nothing logged. (The mediapip warning is about reparenting
  mid-gesture, not at rest.)
- **Unmapping freezes the video, and it resumes on remap — now
  measured.** Hidden reads answered (`evaluate_javascript` works on an
  unmapped view; the earlier claim it stops was wrong and retracted):
  pre-hide 20.83 s → +2 s 20.85 s → +4 s 20.85 s, i.e. the video froze
  essentially at the unmap and stayed frozen; after remapping, 23.43 s
  and 25.93 s at ~2.5 s intervals — realtime playback resumed.
- **Environment note.** Post-reboot, mutter headless works on this
  workstation again: the full spike ran green under it (10 realtime
  cycles, hidden reads frozen at 20.96 s, realtime resume at 23.56 s
  and 26.06 s) — on WebKit's **Cairo** path, because headless mutter
  offers client GL no EGL configuration ("Disabled hardware
  acceleration", the same warning Xvfb gave). The GL-renderer numbers
  above remain the Xvfb + `GSK_RENDERER=gl` + `LIBGL_ALWAYS_SOFTWARE=1`
  run (llvmpipe; an NVIDIA EGL probe crashed the first attempt until
  llvmpipe was forced). Driver-owned decode and DMA-BUF on a real
  desktop remain unmeasured — a live session is the only place for
  that. Running a standalone script (not `meson test`) under the
  bare `dbus-run-session` needs `NO_AT_BRIDGE=1` and `GTK_A11Y=test`,
  or the at-spi registration critical aborts it under
  `G_DEBUG=fatal-criticals`.

- **The card holds the whole `WebColumn`, reparented.** Its state
  (`panel`, `_disposed`, deferred teardown, navigation) is on the
  widget; moving the widget moves nothing else. In card mode the
  column's toolbar view's top bars are hidden and an OSD bar
  (close + expand + drag area) overlays the page.
- **One floating card per window, with four coexistence rules.**
  Opening the web card while the engine card is out closes the engine
  card (its tested release path; both cards float and would overlap).
  Opening a web link while the web card is out restores the column
  into `web_slot` first (`_show` never builds a second column).
  Presenting the media viewer restores the web card into its slot
  *without* forcing the sidebar open: the viewer covers the window
  anyway, and what happens to the page's playback there is WebKit's
  visibility rule, not ours to promise (a muted autoplay video may be
  suspended; see the bug lessons). Not a rule: `mediapip.open_pip`
  does **not** close the web card — the viewer covers the column, so
  its PiP button is unreachable with the card out, and closing would
  dispose the page the user is watching. Imports stay acyclic:
  `mediaviewer` already imports `mediapip` at load time, so
  `present_media` calls `webpip` through a function-local import, as
  `open_pip` already imports `media_overlay` that way.
- **The handover is an ordering, not a new hide rule.** `open_card`
  moves the column out of the slot *before* hiding the sidebar, so
  `watch_split`'s `on_shown` finds an empty slot and never disposes
  the page — no card-aware branch is added to `watch_split` at all.
  A test asserts the order (column out, then sidebar hidden) and that
  `_disposed` stays False.
- **Closing the card is closing the column, including the wait.**
  `close_card` runs `dispose_column()` and drops the card widget from
  the overlay, but the column stays parented to the card until
  teardown: while a load is still running, `dispose_column` defers up
  to `TEARDOWN_GRACE_SECONDS` and the card keeps the column as its
  child (unmapping it early is the pre-first-load unmapping crash the
  grace period exists for); `_teardown()` then unparents it with
  `set_child(None)`, which must be the card doing so. The card's own
  removal from the overlay happens only after `_teardown`.
- **Card mode shows the video, not the page layout** (this is the use
  case: any video in the tab, watched in PiP). On entering card mode,
  a script in an isolated world picks the largest top-frame `<video>`
  with `readyState != 0`, not `disablePictureInPicture`, and at least
  160 px in one dimension — the Google Picture-in-Picture extension
  heuristic — and an injected stylesheet fills the card's viewport
  with it; both are removed on expand. With no eligible video, or
  with `web-javascript` off, the card shows the page as it is. The
  pick re-runs on each committed load in card mode. A site's own
  fullscreen button is
  **denied in card mode** (the video-focus fill already gives the
  near-fullscreen look, and WebKit's default would take over the
  whole Babble window); fullscreen keeps working normally in sidebar
  mode. The OSD sits at the **top** of the card so it never covers a
  player's own bottom controls.
- **The card is interactive and its page area belongs to the page.**
  Drag moves the card only from the OSD bar; there is no arrow-key
  movement in v1 (the keys must reach the page). The card root is
  focusable so keyboard navigation can reach the OSD.
- **Closing the card is closing the column**: `dispose_column()`
  (deferred teardown intact) plus dropping the card. Expanding puts the
  column back into `web_slot` and shows the sidebar — no reload; the
  page is where it was.
- **Code reuse is extraction, not duplication.** The engine card's
  position model and window plumbing — `_apply_margins`, the
  begin/update/end flick drag, the resize clamp, the arrow-key corner
  move, the window `close-request` handler — are ~100 lines of
  `PiPCard` that a second card would otherwise copy. The shared parts
  move to `src/pipgeometry.py` (< 100 lines: margin state, flick to
  corner, clamp, key handling) and both cards import from there. The
  engine card's behaviour must not change: `test-media-pip.py` guards
  every one of those paths today. What deliberately stays per-card:
  content (`PipPaintable` vs a live WebView), drag surface (video
  area vs OSD bar — a web card's page area belongs to the page), play
  state, size rule (stream aspect ratio vs fixed 320×180), and idle
  inhibition (engine card only).
- **v1 limits**: the PiP button appears only for plain pages
  (`panel is None`, not disposed); card size is a fixed 320×180 —
  wider than the engine card's 240 because a page must still fit its
  own player chrome at that width, while the engine card shows only
  the video — no zoom, no aspect tracking; position is
  remembered in memory per window only; no idle inhibition (the web
  column has none today either); no `P` key for the web column (the
  webview consumes keys); no new GSettings, no new icons
  (`babble-pip-in-symbolic` / `babble-pip-out-symbolic` are reused).

## Reference implementations

- **Firefox (the model to follow).** A separate always-on-top chrome
  window whose `<browser>` runs in the same content process as the
  originating tab; Gecko *clones the frames* of the original `<video>`
  into a new `<video>` in the player window (`cloneElementVisually`,
  privileged), so the page never changes. The hover toggle lives in
  the video's UA-widget shadow DOM, so pages cannot detect, disable or
  hijack it; heuristics hide it where it would be noise (video under
  45 s, under 160 px, or silent); site-specific wrapper scripts
  (`picture_in_picture_overrides.js` + `video-wrappers/`) fix
  player-specific controls and captions (YouTube among them); the
  click on the toggle is captured and suppressed so the page — and an
  ad — never sees it. Design goals worth keeping: works on any video
  without site opt-in, and it is obvious *which* video went to the
  window. Source docs:
  `firefox-source-docs.mozilla.org/toolkit/components/pictureinpicture/`,
  and the Hacks post "How we built Picture-in-Picture in Firefox
  Desktop" (2020) for the design process and the Safari/Chrome
  comparison.
- **Chrome.** The W3C `requestPictureInPicture` Web API (sites opt in;
  sites can also opt out per video) plus a Google WebExtension whose
  toolbar button picks the *largest video* on the page. Chrome 116
  added Document PiP (any element into an always-on-top window);
  Firefox 151 has it now too. WebKitGTK exposes neither API.
- **Safari.** `video.webkitSetPresentationMode("picture-in-picture")`
  into the native macOS window; one PiP window system-wide and a
  user-facing size cap. The always-on-top limitation is what Firefox
  explicitly built its own window to avoid.
- **WebKitGTK, today (2.54.2 on this machine).** No PiP API at any
  layer, and no frame-cloning or paintable export an embedder could
  use to do what Firefox's `cloneElementVisually` does. So the
  Firefox idea — pop *the video* out, page untouched — is not
  reachable for us; the reachable approximation is moving the page
  surface itself.
- **Upstream state, verified 2026-11-19.** GNOME Web's only PiP
  proposal is `GNOME/epiphany#2069` ("Picture-in-picture (PiP) for
  videos", 2023): closed within hours by the maintainer, no merge
  request, no code (issues and MRs searched for pip/floating/video).
  The work lives in WebKit: bug 202756 "[GTK][GStreamer]
  [Picture-in-Picture Web API] Add platform support for PiP" has been
  NEW and unassigned since 2019. Its discussion sketches the
  engine-side approach — an output-selector in the video-sink bin
  with two downstream branches (the existing sink and one rendering
  to a separate GTK window), the window created in the UI process,
  zero-copy to be worked out — but the one promise of work (2020-11,
  Philippe Normand) never landed. WebKit's PiP effort since then is
  Apple-ports only: bug 182688 (PiP Web API, macOS, RESOLVED FIXED)
  and, this month, "[Mac] Add experimental support for Document
  Picture-in-Picture" plus its WPT imports. caniwebview's "WPE
  MiniBrowser, Linux, 2026-11" PiP row is a WPT expected-results
  artifact, not an implementation: no release note, commit or Igalia
  periodical mentions PiP on the GLib ports, and WebKitGTK 2.54.3
  (2026-11-18) ships none. The engine-native path is therefore not
  something to wait for; a kind word on bug 202756 is cheap if the
  feature matters to this ecosystem, but Babble's plan must not
  depend on it.

## Future ideas (not v1)

- **JS-assisted video transfer.** For a video whose `src`/`currentSrc`
  is a plain fetchable URL (news sites, direct MP4/GIFV links), run a
  script in the page to find the largest playing `<video>` (the
  Chrome-extension heuristic, filtered by the Firefox toggle
  heuristics) and hand that URL to the existing engine-based PiP path
  — one decoder, native controls, the real video. MSE/blob players
  (YouTube, Twitch) cannot transfer; they keep the whole-page card.
- **A hover toggle injected into pages** via a user script, Firefox
  style, once the column grows its own user-script story. Until then
  the column-level button is the only entry point.

## Lessons from the WebKit bug discussions

From 202756 (the GTK/WPE platform bug, 2019–2020) and its orbit
(211569, 182688, 252765) plus the visibility-suspend bugs (327551,
330831 and the Playwright investigation behind them):

- **The engine-side design everyone converged on is not ours to
  build.** An output-selector in the video-sink bin with two
  downstream branches, a second rendering target as a GTK window
  created in the UIProcess, one-window-per-page per the W3C spec, and
  zero-copy across the WebProcess→UIProcess boundary as the crux.
  That is months of upstream work in WebKit's rendering
  architecture — the whole reason Babble's plan moves the page
  surface instead of the video.
- **The Web API needs a platform presentation-mode layer first**
  (182688 built on `webkitSetPresentationMode`; the GLib ports never
  had one — even the layout-test mock is missing, which is how
  211569, a GTK test timeout, ended up duped into 202756). No test
  infrastructure means the feature could not land even with a patch.
- **Dormancy is ownership, not technical rebuttal.** Nobody in the
  thread ever said "wrong approach"; the proposer never started, the
  reviewer was over capacity, and a 2020 "I've started some work on
  this" was the last word for six years. Waiting is not a strategy.
- **Suspend is teardown, not pause.** WebKit's
  `managePlayerSuspend` ("Muted video player not visible in
  viewport") drives playbin3 all the way to READY — pipeline state
  discarded, rebuilt on resume. An invisible video is not playing
  quietly behind the scenes; it is being dismantled.
- **The suspend path can deadlock the WebProcess** (330831, with the
  Playwright gdb analysis): the main thread sets playbin3 to READY
  and blocks in `gst_pad_set_active` on a stream lock a streaming
  thread holds while that thread waits for the main thread in a
  synchronous bus post — a classic ABBA deadlock, permanent, from a
  muted video merely moving out of the viewport during preroll. Not
  Babble's bug to fix, but it makes any design that relies on
  invisible playback fragile by construction.
- **Babble consequences, mapped:**
  - the card must keep the video element *visible in the page's
    layout* (the 320 px reflow does it for compacting players);
  - the viewer-opens restore ("audio only") is optimistic for muted
    content — most autoplay in the column is muted
    (`ALLOW_WITHOUT_SOUND`), and a muted video the column hides may
    be suspended outright by the visibility rule, not just unheard;
  - the one-card-per-window rule matches what every engine does
    (the spec caps it at one, the 202756 thread accepts it);
  - a second GStreamer surface for one video is exactly what our own
    engine card already avoids (`mediapip` moves the paintable, one
    pipeline) — the web column has no paintable to move, so the page
    surface is the only transferable unit.

## User stories

1. As a reader, I can send the web column (with its playing video) into
   a small card and keep scrolling my timelines while it plays and
   renders.
2. As a reader, I can close the card and stop the page, or expand it
   back into the sidebar and find the page exactly as it was.
3. As a reader, I can drag the card (by its OSD bar) to a corner so it
   does not cover what I am reading.
4. As a reader, opening another link, a post's video or a plugin panel
   never leaves two floating cards or two columns fighting over the
   slot.
5. As a screen-reader user, the card's buttons have accessible names,
   and nothing the card does steals keys from the page.

## Engineering plan

New module `src/webpip.py` (target < 300 lines):

- `src/pipgeometry.py` (T2a, first): the shared card geometry moved
  out of `mediapip.PiPCard` — margin state with the END/END
  corners-only rule, the flick-to-corner drag result, the
  clamp-into-window on resize, the arrow-key corner move — plus the
  window `close-request` plumbing. The extraction also fixes the
  undefined `self._drag_base` read in `_on_window_resized`
  (`mediapip.py:388`, attribute never assigned: the clamp has never
  run, every window resize with a card out raises AttributeError), and
  `test-media-pip.py` grows a resize test for it — its current suite
  never resizes the window, which is why the bug survived.
- `open_card(window)`: take the live column out of `web_slot`
  (`window.web_slot.get_child()`) **first**, then reparent it into a
  `WebPiPCard(Gtk.Overlay)`, add the card to `media_overlay(window)`
  (function-local import — `mediaviewer` imports `mediapip` at load
  time) with the same END/END margins-only position model, hide the
  column's top bars, and only then hide the sidebar
  (`web_split.set_show_sidebar(False)`), so `watch_split`'s
  `on_shown` sees an empty slot and never disposes the page. Register
  the card on the window. When `web-javascript` is on, run the
  video-focus script (isolated world: largest eligible top-frame
  `<video>` by the Chrome-extension heuristic, injected fill
  stylesheet), and re-run it on each committed load while in card
  mode. Returns the card, or None when there is no live column.
- `WebPiPCard`: OSD bar at the card's top edge (close, expand, drag)
  so a player's own bottom controls stay clickable, drag = flick to a
  corner from the bar only, resize clamp, window `close-request` →
  close card, `enter-fullscreen` from the view denied while in card
  mode.
- `restore(card)`: remove the injected video-focus stylesheet and
  script state, reparent the column back into `web_slot`, show the
  top bars, drop the card. The sidebar is shown unless the caller asks
  otherwise (the viewer-opens case).
- `close_card(card)`: `column.dispose_column()` (which may defer up
  to `TEARDOWN_GRACE_SECONDS` while a load settles — the card keeps
  the column parented through that wait, and `_teardown`'s
  `set_child(None)` is the card's), then drop the card from the
  overlay, and hide the sidebar if the slot is now empty.
- `card_for(window)`, mirroring `mediapip.card_for`.

Module changes:

- `webcolumn.py`: a `minimize` action on the column (the PiP button in
  the header, shown for plain live pages); top bars hidden in card mode
  (a `card_mode` property the toolbar visibility follows); Escape in
  card mode closes the card; `enter-fullscreen` denied in card mode,
  untouched in sidebar mode. `load_uri` and `_revive` unchanged — but
  `load_uri` on a column that is in a card restores it first.
- `webcolumn.forward_action`: find the column through the slot **or**
  `webpip.card_for(window)`; without it, Ctrl+W with focus in the card
  falls through to `windowplaces.on_close_pane`'s fallback and closes
  the whole Babble window, and Alt+Left / Ctrl+R / F5 misroute to the
  main navigation. Routing tests cover all four.
- `webcolumn.watch_split`: unchanged. The handover's safety is the
  ordering inside `open_card` (column out of the slot, then sidebar
  hidden), not a new branch here; `_show` restores an out-card column
  instead of building a second one.
- `mediaviewer.present_media`: restore a web card without showing the
  sidebar (viewer-opens case), via a function-local `webpip` import.
  `mediapip.open_pip` is untouched — see the coexistence rules.
- Window: nothing new — the actions and template are untouched; the
  button lives in the column's header.
- `style.css`: `.web-pip-card`, `.web-pip-osd` with libadwaita palette
  variables; the column in card mode needs no new rules beyond hidden
  bars.

Build wiring: `webpip.py` and `pipgeometry.py` in `src/meson.build`'s
install list (the `file size rule` test reads that list).

## Task list

- T1 `Docs/web-pip-ux-spec.md`: interaction, states, wording (the
  contract for T2–T5).
- T2a `src/pipgeometry.py`: extract the shared card geometry from
  `mediapip.PiPCard`, fixing the undefined `_drag_base` read in the
  process; `test-media-pip.py` gains a window-resize test, and its
  existing cases pass untouched.
- T2 `src/webpip.py`: card, handover, restore, registry, reusing
  `pipgeometry` for position/drag/clamp/close plumbing; the
  video-focus script (pick + injected stylesheet, removed on expand)
  and the `enter-fullscreen` denial live here.
- T3 `webcolumn.py`: PiP button, card-mode chrome, `_show`/`load_uri`
  restore, and `forward_action` finding the card's column.
- T4 Coexistence: `mediaviewer.present_media` restores the web card
  (function-local import); `open_card`'s ordering keeps
  `watch_split` untouched.
- T5 CSS + build wiring.
- T6 `src/test-webpip.py` and harness runs.
- T7 Docs (`Docs/features.md` + the manual's web column section,
  EN and pt_BR), pot/po.

## Test plan

Headless mutter at 1280x1024 (see AGENTS.md). `test-webpip.py` is a
standalone `src/test-*.py` that skips when WebKitGTK is unavailable
(77) or there is no display. Lifecycle is tested without codecs;
playback gets one dedicated case that generates its own media and
skips (77) when no VP8 encoder is installed (gst-plugins-good ships
`videotestsrc`, so the fixture is two `gst-launch-1.0` lines, like the
spike's):

- minimize → the card exists, the column is its child, the sidebar is
  hidden, `_disposed` is False, no teardown timer runs, and the
  ordering held (the column left the slot before the sidebar hid);
- `close_card` → the deferred-teardown path runs while the column
  stays parented to the card, the column is dropped, the card is
  gone, the sidebar hides if the slot is empty;
- `_show` (a new web link) while the card is out restores the column
  and loads into it — no second `WebColumn`;
- `present_media` while the card is out restores the column without
  showing the sidebar (function-local import, no cycle);
- expand → the column is back in `web_slot`, the sidebar shown, no
  reload (`load_uri` not called), and the video-focus stylesheet is
  gone;
- window `close-request` with the card out disposes the column;
- routing in card mode: Ctrl+W closes the card (never the window),
  Alt+Left and Ctrl+R/F5 reach the column's page;
- `enter-fullscreen` in card mode is denied (the view stays inline),
  and in sidebar mode it still works;
- 20 minimize/expand/close cycles with no column leaked
  (`gc.collect()` + weak refs, as `test-webcolumn.py` does);
- `plugin panel`'s panel lifecycle is untouched (plain pages only);
- playback: with the generated fixture loaded and the video-focus
  script applied, `currentTime` keeps advancing across a
  minimize/expand cycle, read through `evaluate_javascript`.

`style css` must still pass with the new rules; `ui strings` with the
new labels; `file size rule` with both new modules in the install
list; `module boundary` with the new imports; and `media pip` /
`mediaviewer teardown` must pass after the T2a extraction (its
existing cases unchanged; the new resize case is the one that pins
the clamp the `_drag_base` bug had disabled).
