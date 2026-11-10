# Babble updated streaming plan

> **Superseded (2026-11-09)** by `reports/Babble video playback plan (GstPlay).md`. The Phase 0 spike failed: `Gtk.MediaFile` cannot stream in the GNOME 51 sandbox, and its teardown crashes. Kept for history.

Revised after adversarial review (`reports/Babble video adversarial Tuba review.md`), 2026 GNOME docs check, and Tech Lead review (GO with conditions; Phase 2 NO GO as written). Supersedes `reports/Babble streaming video implementation.md` and the pre-review version of this file.

Revision 3 (2026-11-09): second Tech Lead pass against the GNOME packaging, UI, HIG and architecture guidance, the installed `org.gnome.Platform//51` metadata, Flathub's published refs, and `.github/workflows/ci.yml`. Adds the codec-extension correction, live system state, reduced motion, coordinator design, idle inhibition, and the CI changes.

Revision 4 (2026-11-09): Tuba section rewritten from Tuba's source (`v0.11.1-3-g02305ed0`) instead of the adversarial review's summary; Phase 0/1 and risks updated from it.

## Tech Lead verdict

- Phase 0 (inventory): **GO**, once probe list fixed (§Phase 0).
- Phase 1 (viewer progressive): **GO, only if Phase 0 spike passes.** Gaps added to scope (§Phase 1). The `ffmpeg-full` manifest step is **removed**: that extension has no GNOME 51 branch.
- Phase 2 (timeline inline): **NO GO as written.** Blocked on performance gate, `statusrow.py` extraction PR, settings/i18n/a11y scope (§Phase 2).
- Phases 3–4: deferred, no decision needed.
- CI: Fedora job needs GStreamer packages and a media-stack check before any media test lands (§CI).
- Tuba (rev. 4, read from source): Tuba never streams video through `GtkMediaFile`; it downloads, or uses Clapper. The plan loses its "Tuba proves it" support, so the Phase 0 spike is the only evidence. Also found: Babble's Flatpak has no audio socket, and Babble fetches `remote_url` media without consent.

Direction retained: prove before building widgets, viewer before timeline, defer `playbin3`, keep teardown rule.

## Verified facts (Tech Lead measurement, GNOME 51 runtime)

- File sizes exact: `statusrow.py` 793, `notificationrow.py` 797, `catchup/tiles_render.py` 785 (800-line rule). Phase 0 file-size step is **done**.
- Existing code exists as stated: `release_media`, 12-cycle teardown test, `TIMELINE_MAX_ITEMS=400`, `_TextureCache`, `MediaBox`, metered/power-saver gates.
- `org.gnome.Platform//51` (GStreamer **1.28.6**; docs latest is 1.28.9, runtime is 1.28.6) contains `avdec_h264`, `souphttpsrc`, `playbin3`, `hlsdemux2`, `gtk4paintablesink`, `theoradec`. H.264 decode without extension is true.
- `org.gnome.Platform//51` metadata declares `[Extension org.freedesktop.Platform.codecs-extra]` at `26.08-extra`, mounted at `lib/x86_64-linux-gnu/codecs-extra`, and that path's `lib/gstreamer-1.0` is already on the runtime's `GST_PLUGIN_SYSTEM_PATH`. The runtime brings the extension; the app manifest does not.
- `org.freedesktop.Platform.ffmpeg-full` on Flathub: branches 19.08–**24.08 only**. No 25.08/26.08 branch, so nothing for GNOME 51 (`flatpak remote-ls flathub --all`).
- `src/sysstate.py` already exposes `metered`, `power-saver`, `online` with `notify::` on change (`Gio.NetworkMonitor`, `Gio.PowerProfileMonitor`, portals inside the sandbox).
- GTK 4.24 `Gtk.Settings` has `gtk-enable-animations` **and** `gtk-interface-reduced-motion` (4.22+, fed by the portal's `org.freedesktop.appearance reduced-motion`). Babble's Python reads only the first (`mediaplayback.py:342`); `style.css` covers `prefers-reduced-motion` for CSS only.

Earlier errors corrected below replace the wrong claims in all prior sections.

## Errors fixed (Tech Lead)

1. **codecs-extra content:** Also ships x265, libde265 (HEVC), libheif plugins and ffmpeg libraries (`libavformat`, `libavfilter`, `libswscale`, others), not only `libgstx264.so` encoder. Rule "never name codecs-extra" is **withdrawn**: for HEVC from other servers (Pixelfed, PeerTube `remote_url`), codecs-extra is the right thing to name. Probe `avdec_h265` with and without extension.
2. **VA-API name:** `vaapih264dec` is dead (old `gstreamer-vaapi`). Current is `vah264dec`, plugin `libgstva.so` already in base runtime. VA elements only register when a GPU device is reachable, so "VA-API absent" was a probe artifact. Probe `va*` names inside the app sandbox, which has `--device=dri` from Babble's manifest.
3. **Version stamp:** Runtime GStreamer 1.28.6, not 1.28.9.
4. **`ffmpeg-full` (rev. 3):** The Tuba shape (`ffmpeg-full` 24.08 via `add-extensions`) is unavailable on GNOME 51. Phase 1's manifest step is removed and codec errors never name `ffmpeg-full`. Flathub AI policy also applies: an agent may edit only the local dev manifest; a Flathub manifest is human-written.
5. **Live system state (rev. 3):** "metered/power checked once at construction" is not a platform limit; `sysstate` already notifies. Moved from open risk to requirement.
6. **Reduced motion (rev. 3):** gating on `gtk-enable-animations` alone misses `gtk-interface-reduced-motion`. Moved from open risk to requirement, starting in Phase 1 (existing GIFV gate).

## What Babble must do differently

- Inventory before widgets (§Phase 0 probe list fixed).
- Viewer progressive before timeline inline; Tuba (viewer-only, Clapper->GtkVideo fallback, zero media tests) validates viewer-first.
- `playbin3` deferred; server `meta`/`preview_url` over `GstDiscoverer`.
- Codec error names `codecs-extra` when the missing decoder is one the extension carries (per Phase 0), in user terms (what to install), translatable, no GStreamer element names. Never `ffmpeg-full`.
- No codec change in the manifest.

## Technology (2026 GNOME docs)

- `GtkVideo` scope: previews/embedded animations, `autoplay`/`loop`, simple controls; full player needs `GdkPaintable` + GStreamer directly (docs.gtk.org/gtk4/class.Video.html, 4.24.1 generated 2026.1).
- `GtkMediaFile` GStreamer-backed, GIO extension point; no `new_for_uri()` — use `Gio.File.new_for_uri(url)` + `new_for_file()` (api.pygobject.gnome.org/Gtk-4.0/class-MediaFile.html). PyGObject site has no video/streaming tutorial; compose from signatures + GStreamer C docs.
- `GtkMediaStream`: `play/pause/seek`, `prepared/playing/ended/error`, `muted/volume`, `seekable/seeking`, `timestamp/duration` µs.
- `playbin3`+`gtk4paintablesink` only if track selection/HLS needed: `hlsdemux2/dashdemux2` (1.22+) require `playbin3`/`uridecodebin3`; DMABuf needs GTK >=4.14 + dmabuf feature. Deferred.
- GTK list rule: build widgets in `setup`, set data in `bind`, undo in `unbind`; never create widgets in `bind`. Shapes the Phase 2 coordinator.

## Tuba benchmark (rev. 4: checked against source)

Source: `~/Documentos/Code/Tuba` at `v0.11.1-3-g02305ed0` (2026-09-29). Earlier versions of this section were taken from the adversarial review; this one is read from the code.

| Claim | Source says | File |
|---|---|---|
| Viewer-only, static timeline thumbnails | True | `src/Widgets/Attachment/` |
| "`stream=true` progressive delivery (`set_file(Uri)`)" to steal | **False for video.** `stream` is set only for `AUDIO` (`Attachment/Box.vala:172-178`, `Attachment/Image.vala:184-187`), and audio goes to `Widgets.Audio.Player`, a hand-built `uridecodebin ! … ! autoaudiosink` pipeline (`AudioPlayer/Stream.vala:120`), not `GtkMediaFile`. The `GtkVideo` + `File.new_for_uri` branch (`MediaViewer.vala:1131-1134`) is unreachable for video. Without Clapper, video is downloaded then played (`download_video`, since the first viewer commit `c31521b6`, 2023). Only Clapper streams video. | `src/Views/MediaViewer.vala` |
| `ffmpeg-full` + Clapper.Enhancers extensions | True, but stale: Tuba targets `org.gnome.Platform//50` with `ffmpeg-full` **24.08**, while GNOME 49+ runtimes already declare their own `codecs-extra` (GNOME 49 → 25.08, GNOME 51 → 26.08, both on `GST_PLUGIN_SYSTEM_PATH`) | `build-aux/dev.geopjr.Tuba.Devel.json:6-20` |
| Audio output | Tuba has `--socket=pulseaudio`. **Babble's manifest has no audio socket**, so Babble's Flatpak build almost certainly plays video and audio attachments silently today | Tuba manifest `finish-args`; `build-aux/flatpak/io.github.mauriciobc.Babble.json` |
| MPRIS | Clapper's MPRIS enhancer under `--own-name=org.mpris.MediaPlayer2.Tuba.*`, one name per carousel item. It is a static own-name, not a portal | `MediaViewer.vala:1094-1113` |
| Clear-while-alive destructor | True: `stream_unprepared()`, `set_file(null)`, `set_media_stream(null)` on a live `Gtk.Video` (`MediaViewer.vala:323-327`), the order Babble pinned as its SIGSEGV. Tuba also had a teardown fix for the audio stream (`c2886e07`) | `MediaViewer.vala` |
| `GtkVideo` graphics offload | `graphics_offload = ENABLED` on the fallback `Gtk.Video` (`MediaViewer.vala:1081-1083`). Babble does not set it; the property exists in GTK 4.24 | — |
| Clapper download cache | Clapper streams **and** writes the file to a cache dir; Tuba replays by `MediaItem.cached(url, path)` and keeps the 10 newest (`MediaViewer.vala:1077-1079`, `1179-1225`). That is how Tuba has Save As without a second fetch | `MediaViewer.vala` |
| Volume memory | True: `media_viewer_last_used_volume` in GSettings, restored per item | `MediaViewer.vala:67, 351-372` |
| 1 s spinner, 5 s control auto-hide | True: `Timeout.add_once (1000, …)`, `Timeout.add (5 * 1000, …)` | `MediaViewer.vala:278, 845` |
| Remote-media consent gate | True: `remote_url` media opens only after a dialog (`fetch_remote_media_default`, with a "don't ask again" `fetch_remote_media_reminder`) | `Attachment/Image.vala:170-200` |
| No metered/power gating | True: `NetworkMonitor` used only for online/offline (`Application.vala:291, 454, 550`); no `PowerProfileMonitor`, no metered check. The only motion gate is the audio visualizer's `gtk_enable_animations` | `Application.vala`, `AudioPlayer/Visualizer.vala:36` |
| Zero media tests | True: `tests/` has Blurhash, Html, DateTime and similar; nothing on media | `tests/` |

What this changes for Babble:

1. **No field evidence for `GtkMediaFile` over `https://`.** Tuba avoided it for video in every version. The Phase 0 spike is the only evidence the plan will have, so it is a hard gate and its fallback is named in advance (Phase 0).
2. **Audio socket missing.** A current bug in Babble's viewer, independent of streaming. Fixed in Phase 0/1 (local dev manifest only; a Flathub manifest is human-written).
3. **Steal:** graphics offload on the viewer's `Gtk.Video`; volume memory (needs a gschema key); 1 s spinner delay; 5 s auto-hide; open-all-from-any-tile carousel; remote-media consent gate (Babble falls back to `remote_url` silently today: `mediaplayback.py:272`, `mediabox.py:621`, `mediaviewer.py:606,765`, which contacts a third-party server without asking).
4. **Consider, don't assume:** Clapper's stream-and-cache model answers Gap A (Save As) and gives MPRIS and enhancers (yt-dlp for PeerTube). Clapper has GObject introspection, so Python can use it. The runtime doesn't ship it, so Babble would have to bundle it. It is the named fallback if the Phase 0 spike fails, not the default.
5. **Beat:** inline muted-loop + badges, metered/power/reduced-motion gating, teardown order, retry UX, tests.
6. **Avoid:** unified 100 MiB `Soup.Cache`, download-everything default, the `#if CLAPPER`/`#if GSTREAMER` matrix (Tuba's carries a dead branch because of it), clear-while-alive teardown, `ffmpeg-full` 24.08, one MPRIS name per carousel item.

## Revised roadmap

### Phase 0 — Inventory, no widget code (GO with fixed probe)

- Fixed probe list, run inside the app sandbox so `--device=dri` applies:
  `avdec_h264`, `avdec_h265`, `vah264dec`, `vah265dec` (+ other `va*`), `souphttpsrc`, `playbin3`, `hlsdemux2`, `gtk4paintablesink`, `theoradec`, `vulkanav1dec`. Record element, plugin path, and whether it comes from base or `codecs-extra`.
- Run the probe twice: with `codecs-extra` installed, and after `flatpak uninstall org.freedesktop.Platform.codecs-extra//26.08-extra`. Also record whether a clean `flatpak install` of the runtime pulls `codecs-extra` automatically.
- Range probe: `curl -I` / `curl -r 0-1023` vs live Mastodon hosts (public media, read-only); `Accept-Ranges`, `206 vs 200`, redirects. `souphttpsrc` aborts on non-range/redirect (Bug 722185).
- `new_for_uri` spike in sandbox: `Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(url))`, `Gtk.Video.new_for_uri`, `Gio.File.new_for_uri` vs `new_for_file`. **Phase 1 gate: must pass.** Tuba never shipped this path for video (see §Tuba), so there is no field evidence. If it fails: (a) keep download-then-play, or (b) evaluate a bundled Clapper (stream + on-disk cache) as its own decision; no hybrid.
- Audio spike (rev. 4): play a video with sound in the Flatpak build. Expected silent: Babble's `finish-args` have no `--socket=pulseaudio`. If silent, add the socket to the local dev manifest (Tuba carries it) and re-run.
- File-size step: **already done** (793/797/785 verified). Output go/no-go per experiment with named fallback.

### Phase 1 — Viewer progressive (GO iff Phase 0 passes)

- Replace full-download in `mediaplayback._download_stream` with progressive `Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(url))`; download only on `notify::error`.
- Keep metered deferral, power-saver GIFV block, sensitive blur gate, teardown rule (pause, drop widget, disposal retires GstPlay; never clear stream while widget alive).
- **Gap A — downloaded-copy dependents:** Save As / Open With / drag-out read `_temp_media` (`mediaviewer.py:773`). Streaming removes the copy, so these actions must fetch the file again on demand (existing `download()` helper, with progress and cancel). Add scope + test.
- **Gap B — HTTP session bypass + cancellable replacement:** GStreamer backend uses its own `souphttpsrc`, not Babble's HTTP session (confirm in spike; `mediaplayback.py:284` gvfs comment expected wrong). Different User-Agent/proxy handling; paused stream may keep downloading. Add test: closing viewer mid-buffer stops network traffic, replacing old cancellable behaviour.
- **Gap C — live system state (rev. 3):** subscribe to `sysstate.monitor()` `notify::power-saver` and `notify::metered` with `pages.connect_weak`. Power-saver turning on pauses a playing GIFV; metered turning on mid-play does not restart or refetch anything.
- **Gap D — reduced motion (rev. 3):** one helper for "may this loop autoplay": `gtk-enable-animations` true, `gtk-interface-reduced-motion` not `reduce`, not power-saver. Replace the inline check at `mediaplayback.py:342`; Phase 2 reuses it.
- Codec error: translatable, names `codecs-extra` when a decoder it carries is missing; never `ffmpeg-full`; no element names in the text.
- Manifest: **no codec change.** (Previous "add `ffmpeg-full` via `add-extensions`" removed.) The only manifest change is `--socket=pulseaudio` if the Phase 0 audio spike confirms silence (local dev manifest only).
- From Tuba (rev. 4): `graphics-offload` enabled on the viewer's `Gtk.Video` (no effect under the Cairo harness; checked for no criticals); 1 s delay before the loading spinner; volume memory through a new gschema key; remote-media consent before fetching a `remote_url` (today Babble falls back to it silently), with a "don't ask again" switch in Preferences.

### Phase 2 — Timeline inline (NO GO as written; unblocked only when all met)

1. **Performance gate first:** stop-if-exceeded limits on `stall_max_ms`, `stalls_over_16ms`, `main_ms`, RSS per `PERFORMANCE.md`/`autoresearch.sh`, before any visibility computation work. No gate = no start.
2. **Coordinator owns the video widget and stream, not the row (rev. 3 design):** `mediainline.py` owns the single `Gtk.Video`/`Gtk.Picture` and its `GtkMediaFile`, and lends the widget to whichever tile is visible. Rows never build a video in `bind`. On `unbind`/recycle of the borrowing row the coordinator reclaims it in `release_media` order: pause, detach, let disposal take the pipeline down. Row rebind during playback is the SIGSEGV risk area; this is the transfer.
3. **`statusrow.py` extraction PR first:** 7 lines headroom (793/800). Named extraction as its own PR, landed before inline work.
4. **Settings scope complete:** gschema keys + Preferences row + UI strings + four translations + help pages for autoplay/data-saver keys. Keys do not exist yet; count i18n/help in estimate.
5. **Reduced motion + a11y as requirement:** reuse the Phase 1 helper (Gap D) plus metered and the autoplay pref, all live via `notify::`. The playing tile has a keyboard-reachable pause control with tooltip and accessible label; the media carries the attachment description as its label.
6. **No idle inhibition from inline playback (rev. 3):** only the viewer calls `_set_inhibited`. A scrolling feed must not keep the screen awake.
7. Then: `GtkVideo` muted-loop in `MediaBox`, >=50% visible, max one pipeline app-wide, poster until `prepared`, pause+restore on scroll-away/unmap. Visibility from layout bounds/scroll signals, throttled to the frame clock, connected with `pages.connect_weak`.

### Phase 3 — Surfaces + PeerTube bridge (deferred)

Notifications/Catch-Up open viewer; PeerTube/HLS browser bridge; remote-url recovery dialog.

### Phase 4 — Detail hardening, gated (deferred)

Trigger: multi-track/sidecar/HLS in data model. Then `playbin3`+`gtk4paintablesink`.

## Tests (all `src/test-*.py`, headless mutter 1280x1024, `GSK_RENDERER=cairo`, `LANG=C`)

Corrections per Tech Lead:

- **CI packages:** see §CI.
- **No wall-clock flake:** replace "plays in under 5 s" with wait on `prepared` + `timestamp` advance with generous timeout.
- `test-media-codec-probe.py` → **`test-media-codec-error.py`**: unit test of the missing-decoder → message mapping with a **fake element list**, not the host `Gst.Registry`. Asserts HEVC-absent names `codecs-extra`, the text never contains `ffmpeg-full` or an element name, and it is translatable.
- **Settings isolation:** each new test sets `GSETTINGS_BACKEND=memory` **before importing gi**, or settings isolation test fails.
- **No host state:** gate tests fake the `sysstate` monitor and the motion settings; they never depend on the machine's network, power profile or desktop settings.

Tests:
- `test-media-progressive.py`: loopback Range server + **committed** Ogg/Theora fixture (video only, a few seconds, tens of KB; Fedora ships no H.264 decoder by default, so the hermetic test stays Theora). Asserts the GTK media backend is the GStreamer one (not the no-media fallback), `prepared`, timestamp advances, seek, `error` null, fallback on 200/error; closing mid-buffer stops network (Gap B); Save As/Open With/drag-out refetch (Gap A); `notify::power-saver` pauses a GIFV (Gap C); reduced motion blocks autoplay (Gap D).
- `test-media-inline-gate.py`, `test-media-inline-teardown.py`, `test-media-timeline-geometry.py`: as before (gates, 12-cycle teardown, ±2px geometry, `TIMELINE_MAX_ITEMS=400`), plus coordinator reclaims the video on recycle-while-playing, and inline play never inhibits idle.
- Register modules in `src/meson.build` install + test lists or file-size rule fails.

## CI (`.github/workflows/ci.yml`)

### `test` job (Fedora rawhide)

The `dnf install` list has no GStreamer packages. Without them GTK's media backend falls back to no-op and the media tests can play nothing. Add to the install line:

```sh
gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good \
gstreamer1-plugins-bad-free-libs
```

- `gstreamer1`: core and `gst-inspect-1.0` (used by the check below).
- `gstreamer1-plugins-base`: `playbin`, `oggdemux`, `theoradec`, `videoconvert`.
- `gstreamer1-plugins-good`: `souphttpsrc` (uses the libsoup3 already installed), `qtdemux`.
- `gstreamer1-plugins-bad-free-libs`: `libgstplay`, which GTK's GStreamer media module links.
- Not needed: libav/openh264 (fixture is Theora), `gstreamer1-plugin-gtk4` (Phase 4 only), VA-API (no GPU in the container).
- Confirm names with `dnf repoquery` on rawhide when making the change.

New step, right after "Install build dependencies", so a missing plugin fails at its cause instead of as a media-test timeout:

```yaml
      - name: Check the media stack
        # GtkMediaFile falls back to a no-op backend when GTK's GStreamer
        # module or a plugin is missing; the media tests would then fail
        # on a timeout far from the cause.
        run: |
          for e in playbin souphttpsrc oggdemux theoradec qtdemux; do
            gst-inspect-1.0 --exists "$e" \
              || { echo "::error::missing GStreamer element $e"; exit 1; }
          done
```

The GTK backend itself is asserted inside `test-media-progressive.py`, since it needs a display.

"Check the suite ran": **no new allowed skip.** A media test that skips for a missing element is lost coverage, and the existing check already fails the run on it.

### `flatpak` job (gnome-51 image)

After "Build against the GNOME 51 SDK", check the runtime the app ships against still has what Phase 1 relies on:

```yaml
      - name: Check the runtime's media stack
        run: |
          flatpak-builder --run builddir \
            build-aux/flatpak/io.github.mauriciobc.Babble.json \
            sh -c 'for e in avdec_h264 souphttpsrc playbin3 theoradec; do
                     gst-inspect-1.0 --exists "$e" || { echo "missing $e"; exit 1; }
                   done'
```

Only base-runtime elements are asserted. HEVC depends on `codecs-extra`, which the CI image may not carry: report, do not fail. No VA-API check (no GPU).

## Risks / open questions

- RSS/stall budget unpriced until Phase 2 gate measured; fast-scroll churn.
- Wayland DMABuf/VA-API untestable under Cairo; driver masking removes HW decode regardless of extension.
- Whether a clean runtime install pulls `codecs-extra` automatically (Phase 0 answers). If not, the codec error is the user's only path to it.
- Sensitive re-gating; suspend/minimize; signed-URL expiry.
- MPRIS (rev. 4 correction): not a portal. It needs a static `--own-name=org.mpris.MediaPlayer2.<name>` finish-arg (Tuba's shape). If ever wanted, use one name for the viewer, not one per item.
- `gtk4paintablesink` under Cairo unproven; `mediainline.py` 800-line fit unproven until extraction PR lands.
- Resolved into requirements (rev. 3): live metered/power transitions (Gap C), reduced motion (Gap D), idle inhibition (Phase 2.6).

## First commands

```sh
# Inside the app sandbox, so --device=dri applies (gst-inspect has no such flag)
flatpak run --command=sh io.github.mauriciobc.Babble -c \
  'for e in avdec_h264 avdec_h265 vah264dec vah265dec souphttpsrc playbin3 hlsdemux2 gtk4paintablesink theoradec; do
     gst-inspect-1.0 --exists $e && echo "$e yes" || echo "$e NO"; done'
curl -I https://files.mastodon.social/<example>.mp4
curl -r 0-1023 -I https://files.mastodon.social/<example>.mp4
flatpak remote-ls flathub --all --columns=ref | grep -E 'codecs-extra|ffmpeg-full'
```
