# Tuba video/media benchmark (Tuba v0.11.1, HEAD 02305ed0 2026-09-29, GNOME/Vala/GTK4+libadwaita)

## All video/media code — files, classes, playback stack, and what is absent

### Takeaway
Tuba has **no inline timeline playback**. All playback lives in a single full-window `Views.MediaViewer` (carousel + morph reveal), backed by **Clapper (preferred) → GtkVideo/GtkMediaFile (fallback)** for video/GIFV and a **hand-rolled GStreamer `uridecodebin` pipeline** for audio. No subtitles/track-selection UI, no HLS/multi-bitrate code of its own, no duration badges, no data-saver.

### Cited Findings
- Media model: `Tuba.Attachment.MediaType {IMAGE, VIDEO, GIFV, AUDIO, UNKNOWN}` with `is_video()` returning true for VIDEO/GIFV/**AUDIO**, `can_copy()` only for IMAGE — `src/Views/MediaViewer.vala` (enum at top of file).
- API entity: `src/API/Attachment.vala` — `id, kind, url, description, meta{focus{x,y}}, blurhash, preview_url` (getter falls back to `url` when empty), `remote_url`, `tuba_translated_alt_text`; `to_widget()` returns `Widgets.Attachment.Image` if `preview_url != null` else `Widgets.Attachment.Item`. **`meta` parses only `focus` — no duration/width/height/frame-rate fields are read at all.**
- Viewer: `src/Views/MediaViewer.vala` (~1100 lines), `GtkTemplate ui/views/media_viewer.ui`, class `Tuba.Views.MediaViewer : Gtk.Widget, Adw.Swipeable`, inner `Item : Adw.Bin`. Entry point `MainWindow.show_media_viewer(...)` / `reveal_media_viewer_manually()` in `src/Dialogs/MainWindow.vala:115-153`; `media_viewer` is a `[GtkChild]` of the split view, `visible` bound inverted to `split_view.can-focus`.
- Viewer `add_media(url, media_type, preview, as_is, alt_text, user_friendly_url, blurhash, stream, revealer_widget, load_and_scroll)` — builds one `Item` per attachment into an `Adw.Carousel`; images via `Gtk.Picture` + `Helper.Image.request_paintable`; video via Clapper/GtkVideo; audio via `Widgets.Audio.Player` (only `#if GSTREAMER`, otherwise audio falls through to the video branch because `is_video()` includes AUDIO).
- Video backends (compile-time `#if CLAPPER` / `#else` everywhere in MediaViewer):
  - **Clapper**: `ClapperGtk.Video { auto_inhibit=true }`, `Clapper.MediaItem.cached(url, cached)`, queue select, `download_dir=<cache>/clapper`, `download_enabled=true`, LRU cache cleanup keeping **10 files** (`clapper_cache_cleanup`), `ClapperGtk.SimpleControls` fading overlay (non-GIFV only), MPRIS feature (`org.mpris.MediaPlayer2.Tuba.instance<N>`, handles both `CLAPPER_0_10` enhancer-proxy and older `CLAPPER_MPRIS` APIs), `queue.progression_mode=REPEAT_ITEM` for GIFV. Clapper built from git tag 0.10.0 bundled in Flatpak (see deps section).
  - **Fallback GtkVideo**: `Gtk.Video { graphics_offload=ENABLED }`; `video.loop=true` for GIFV; **non-stream video is fully downloaded first** (`download_video()` → `Utils.Host.download(url)` → `video.set_filename(path)`) — no progressive/range playback of its own; only `stream=true` callers use `video.set_file(File.new_for_uri(url))` directly. `stream=true` is only passed for **audio** (remote-audio fallback, Funkwhale, Drive) and PeerTube preview-card video (`src/API/Status/PreviewCard.vala:245`).
- Audio stack: `src/Widgets/AudioPlayer/Player.vala` (`Widgets.Audio.Player : Adw.Bin`, click-to-toggle play, controls auto-hide after 5s), `Stream.vala` (raw GStreamer: pipeline string `"uridecodebin ! audioconvert ! audio/x-raw,channels=2 ! volume ! level interval=100000000 ! autoaudiosink"`, `Gst.parse_launch`, bus watch handling ERROR/EOS/STATE_CHANGED/ELEMENT(level)/ASYNC_DONE/DURATION_CHANGED, 1s poll for position, `seek_simple(FLUSH|KEY_UNIT)` on scrub), `Controls.vala` (play/pause, seek scale 0–10, time labels `nanoseconds_to_string`, `Gtk.VolumeButton`), `Visualizer.vala` (texture/blurhash cover + level bar driven by GStreamer `level` element, smoothing 0.4).
- Timeline tiles: `src/Widgets/Attachment/Box.vala` (`Gtk.FlowBox` homogeneous, 1-per-line single / 2-per-line multi, single height_request 334), `Image.vala` (extends `Item`, `FocusPicture` + center overlay icon: `media-playback-start-symbolic` for video/GIFV, `tuba-music-note-symbolic` for audio, `tuba-paper-symbolic` for UNKNOWN), `Item.vala` (button + ALT badge, right-click/long-press context menu: Open in Browser / Copy URL / Save Media… / Copy Media (image-only)). Wired in `src/Widgets/Status.vala:1233,1450-1483` via `attachments.list = status.formal.media_attachments`, `has_spoiler = status.formal.sensitive`.
- Image loading/caching: `src/Services/Helpers/Image.vala` — libsoup session (64 conns), `Soup.Cache` at `<cache>/soup/media` **100 MB cap**, `Gdk.Pixbuf.from_stream_async(...).apply_embedded_orientation()`; blurhash shown as idle placeholder first if `settings.use_blurhash` (pure-Vala decoder `src/Utils/Blurhash.vala` + `Services/Helpers/Blurhash.vala`); video-file download cache `src/Utils/Host.vala:69 download()` → `<cache>/manual/media/<hash(dir)><basename>`, wiped on shutdown (`MediaViewer.cache_cleanup()` non-Clapper path).
- Viewer UX details: `ScaleRevealer` morph from source widget, `Adw.SwipeTracker` vertical swipe-to-dismiss (400ms cancel animation), `Adw.Carousel` prev/next revealers, fullscreen via `app.main_window.fullscreen()` (F11), image zoom only (`MAX_ZOOM=20`, Ctrl+scroll, pinch `GestureZoom`, double-click 2.5×, `media_viewer_expand_pictures` setting), spinner shown only after 1s (`spinner_timeout`), shortcuts (Esc/Alt+Left back, Ctrl+C copy-media, Ctrl+S save-as, ←/→ navigate, Ctrl± zoom), context menu (copy-url/open-in-browser/save-as/fullscreen/zoom), `last_used_volume` persisted in `media-viewer-last-used-volume` gschema key, auto-play on page change (`play_video(pos)` / `pause_all_videos()` on carousel page change; `pre_playing` flag gates play until `done()`).
- **Absent (explicitly searched, zero hits in viewer/audio code):** subtitles, audio/video track selection, playback-rate/speed control, HLS/m3u8 parsing, adaptive bitrate, duration badge (no `meta.duration` use anywhere in Attachment/viewer), hover preview, gif autoplay in timeline, muted-by-default, data-saver / power policy, codec-missing toast (GStreamer errors are only `critical()` log; viewer download failure shows `app.inform("Error", ...)` dialog; no `openh264`/codec-extension handling in code).

### Inferences
- Tuba's "streaming" is entirely delegated: Clapper (via its enhancers) handles HLS/adaptive internally; the GtkVideo fallback is download-then-play for video. Babble beating Tuba only requires real inline playback + progressive seek, not HLS from scratch.
- The GStreamer audio pipeline is the weakest code in the stack (string-launch pipeline, 1s position poll, `seek_simple` with `value/10*duration` arithmetic, level-element visualizer) — but it's the only in-app audio player; without `gstreamer` Meson feature, audio degrades to video-widget playback, and without `clapper`, video degrades to download-then-play.

### Gaps
- No runtime codec-availability probing found in static code; whether Clapper enhancers surface a user-facing "missing codec" path could not be verified without running the app.
- PeerTube HLS playlists (`src/API/PeerTube/StreamingPlaylist.vala`, `File.vala`, `Resolution.vala`) are parsed but no player wiring for them was found in the viewer (only the preview-card `stream=true` VIDEO call).

## Mastodon attachment-type handling (preview_url, blurhash, sensitive, carousel, fallbacks)

### Takeaway
Tuba covers all four Mastodon types but treats them uniformly as **static thumbnails in the timeline** (preview_url image + play/music icon) and only resolves the real media inside the viewer. Sensitive handling is strong (blur + reveal gate); remote/unproxied media has a distinctive consent flow.

### Cited Findings
- `preview_url` fallback to `url` when null/empty (`src/API/Attachment.vala:17-20`); timeline always renders `entity.preview_url` as a still via `Helper.Image.request_paintable` (`Image.vala on_rebind`), even for video/GIFV/audio — **no animated GIFV preview, no waveform, no duration badge** in the timeline.
- Blurhash: decoded by `Utils.Blurhash.decode_to_data` (pure Vala, `Services/Helpers/Blurhash.vala` wrapper) and shown as placeholder while the real paintable loads; gated by `use-blurhash` gschema (default true). Thumbless audio (no blurhash + no paintable) sets `Attachment.Box.has_thumbnailess_audio` and binds the **status author's avatar** as `audio_fallback_paintable` (`Status.vala:1478-1479`).
- Sensitive/spoiler: `Status.sensitive` → `Attachment.Box.has_spoiler`; `FocusPicture.spoilered` applies `snapshot.push_blur(60)` (or hides if `blur-sensitive-media=false`); overlay label "Show Sensitive Content" + eye-off button; `spoiler_revealed` defaults from `show-sensitive-media` gschema (default false); clicking a spoilered tile reveals instead of opening. Content-warning `spoiler_text` is a separate path (`Status.has_spoiler`, `tuba_spoiler_revealed`, `show-spoilers` setting).
- Multiple attachments: `Box.update()` lays all out in one FlowBox; clicking any tile calls `open_all_attachments(url)` which opens **all** attachments in the viewer and scrolls to the clicked one (`is_main` matching by URL, `load_and_scroll`); viewer carousel prev/next revealers appear when `n_pages > 1`.
- Focus/crop: `meta.focus{x,y}` applied to `FocusPicture.focus_x/y`; `letterbox-media` setting switches `ContentFit.COVER`↔`CONTAIN` (also forced CONTAIN for reports).
- Alt text: `description` → `alternative_text` (or `tuba_translated_alt_text`), ALT badge button opens `Adw.Dialog` with read-only `TextView`; viewer image items set `picture.alternative_text`.
- UNKNOWN/remote fallback (`Image.vala on_remote_fetch_media`): if `kind==unknown` and `remote_url` exists, shows consent dialog "Your instance hasn't proxied or can't handle this attachment…" with "Don't remind me again" (`fetch-remote-media-reminder` default true, `fetch-remote-media-default` default false); on accept, guesses type from MIME of `remote_url` basename (`media_type_from_url`: audio/*→AUDIO, image/*→IMAGE, video/*→VIDEO, else null→`Item.open()` = download + `Host.open_url` in external app). This is the **only external-player fallback**; known types never leave the app.
- Upload side: `Attachment.upload(uri/bytes, mime)` → `POST /api/v1/media` multipart; composer `Dialogs/Composer/Attachment.vala` supports IMAGE/GIFV/VIDEO/AUDIO + custom video/audio thumbnails (commit 31ad1900); instance limits parsed in `src/API/Instance/Mastodon/Configuration/MediaAttachments.vala` (`supported_mime_types, image/video_size_limit, image/video_matrix_limit, video_frame_rate_limit`).

### Inferences
- Babble parity checklist from Tuba: preview_url fallback, blurhash placeholder, 60px blur for sensitive on every media type (including video thumbs), carousel-open-all-from-any-tile, focus-point cropping, alt-text badge, remote-media consent gate. Tuba gaps Babble can beat: timeline GIFV animation, duration badges (data is in `meta` but Tuba ignores it), audio thumbnails/waveforms.
- The UNKNOWN→remote flow is a good pattern to steal: never a dead tile — either external-open or consent-then-fetch.

### Gaps
- `meta` parsing for `duration`/`width`/`height` was not found, but whether the server sends them and Tuba silently drops them vs. never receiving them was not verified against a live instance.

## Flatpak manifest and Meson dependencies (codecs/GStreamer)

### Takeaway
Tuba's codec story is **Flatpak extension-based**: `org.freedesktop.Platform.ffmpeg-full` (24.08) + a bundled Clapper 0.10 build, with GStreamer and Clapper as **optional Meson features** (`feature` options, auto-detected). No openh264/codecs-extra extension, no system-codec requirement.

### Cited Findings
- `build-aux/dev.geopjr.Tuba.Devel.json`: runtime `org.gnome.Platform` **50**, `add-extensions: org.freedesktop.Platform.ffmpeg-full v24.08` (`directory: lib/ffmpeg`, `add-ld-path: .`, autodownload/no-autodelete default) and `com.github.rafostar.Clapper.Enhancers v stable` (`extensions/clapper/enhancers`); `finish-args` include `--socket=pulseaudio`, `--own-name=org.mpris.MediaPlayer2.Tuba.*`, `--env=CLAPPER_ENHANCERS_PATH=...`. Tuba module config: `-Dclapper=enabled -Dgstreamer=enabled -Dspelling=enabled -Dgexiv2=enabled -Dunifiedpush=enabled -Din-app-browser=enabled`; clapper module config: `-Dmpris=disabled -Ddiscoverer=disabled -Dserver=disabled` (MPRIS re-enabled at runtime per-instance via `Clapper.Mpris` feature in MediaViewer when available).
- `meson.build`: `clapper-0.0 >=0.8.0`, `clapper-gtk-0.0`, `gstreamer-1.0`, `webkitgtk-6.0`, `gexiv2-0.16`, `libspelling-1`, `unifiedpush` — all `required: get_option(...)`; `meson_options.txt`: `clapper`, `gstreamer`, `spelling`, `in-app-browser`, `gexiv2` are plain `feature` (auto) options, `unifiedpush` default disabled. Presence defines `--define=CLAPPER` / `--define=GSTREAMER` / `--define=CLAPPER_0_10` / `--define=CLAPPER_MPRIS`.
- No `openh264`, `codecs-extra`, `ffmpeg` Meson dependency, or bundled codec binaries in-repo; codec availability is entirely the Flatpak `ffmpeg-full` extension's job. `data/dev.geopjr.Tuba.metainfo.xml.in` was not audited for codec notes (gap).
- History: `7800bb2b feat(MediaViewer): optional clapper support (#931)`, `0a2d9e5b feat: use Clapper enhancers & make default (#1308)`, `16f3e1af feat(clapper): prepare for 0.10 (#1435)`, `7663de42 feat(meson): use features to control optional dependencies (#1423)` — i.e. Clapper went from optional to default-within-capability.

### Inferences
- For Babble: ship the same two extensions (ffmpeg-full + enhancers) and treat GtkVideo/GStreamer as fallback tier; don't bundle codecs. Tuba proves one manifest line (`ffmpeg-full`) is enough for the GNOME runtime to cover H.264/HEVC/AAC etc.
- Build-time feature flags are the right shape: Tuba compiles one binary that degrades gracefully (no-Clapper → download-then-play; no-GStreamer → audio via video player). Babble should mirror: single binary, capability-detected backends.

### Gaps
- Whether the non-Devel/release manifest (`data/` only has metainfo; no release `dev.geopjr.Tuba.json` in `build-aux/` besides Devel) differs in runtime version/extensions was not checked (Flathub repo not inspected).

## UX patterns Babble could adopt or must beat

### Takeaway
Tuba's UX is conservative: **click-to-open viewer, everything plays inside the viewer, nothing in the timeline** — with polished viewer chrome (morph reveal, swipe-dismiss, smart control auto-hide) but zero playback-policy or data-awareness in the timeline.

### Cited Findings
- **Click-to-play**: only play control is inside the viewer; timeline tiles are inert thumbnails (click opens viewer). No hover preview, no animated GIFV, no inline play button in `Widgets/Attachment/*`.
- **GIF autoplay policy**: none in timeline; in viewer GIFV loops (`video.loop=true` / `REPEAT_ITEM`) and auto-plays when its page becomes current (`on_carousel_page_changed` → `play_video(pos)`), pausing all others. No reduced-motion / data-saver check anywhere (searched `data.saver|power|bandwidth|autoplay` in `src/` — only unrelated hits).
- **Muted**: no muted-by-default; viewer restores `last_used_volume` (default 1.0) onto every video/audio item. Timeline tiles are silent by construction (no playback).
- **Controls**: Clapper `SimpleControls` in viewer; audio `Controls` (play, seek, volume, time) auto-hide after **5s** of no motion, reappear on motion/click; viewer page/zoom/fullscreen revealers follow the same 5s pattern (`on_reveal_media_buttons`/`on_hide_media_buttons`). Video pages raise viewer buttons' `margin_bottom` to 68px to clear the Clapper controls.
- **Fullscreen/keyboard**: F11 toggles `main_window.fullscreen()`; Esc/Alt+Left/Pointer back closes; ←/→ navigate carousel; Ctrl+C copy-media (image only), Ctrl+S save-as, Ctrl± zoom; double-click toggles zoom; pinch-to-zoom; right-click context menu in both tile and viewer.
- **A11y**: `alternative_text` plumbed from attachment `description`; ALT badge + dialog; `aria_attachments = "Contains %d attachment(s)."` appended to status accessible description (`Status.vala:1094-1103`); focus restoration after viewer close via `media_viewer_source_widget` + `ScaleRevealer`. No caption/subtitle support.
- **Toasts/errors**: `app.toast("Copied …")` for copy actions; `app.inform("Error", e.message)` dialog on viewer video-download failure; `"Couldn't Save Media"` toast on save failure; GStreamer errors only logged (`critical("Gst Error: …")`), no user-facing codec error anywhere.
- **Settings worth noting**: `letterbox-media` (false), `media-viewer-expand-pictures` (true), `use-blurhash` (true), `show-sensitive-media` (false), `blur-sensitive-media` (true), `fetch-remote-media-*`, `media-viewer-last-used-volume` (1.0) — all in `data/dev.geopjr.Tuba.gschema.xml`.

### Inferences
- Adopt: 1s-delayed spinner, 5s control auto-hide, volume memory per viewer session (persisted), swipe-to-dismiss with dimmed backdrop, morph-from-widget reveal, open-all-attachments-from-any-tile, remote-media consent dialog, ALT badge/dialog, "Contains N attachments" aria text.
- Must beat: Tuba has **no data-saver, no GIF-pause-until-hover, no reduced-motion respect, no duration badges, no inline playback** — inline muted-looping GIFV + click-to-play video in the timeline would already surpass it.
- Keyboard parity: Tuba's viewer shortcut set (F11, arrows, Ctrl+C/S/±, Esc) is the bar; note it has **no space-to-play/pause or seek shortcuts** in the viewer itself (relies on Clapper controls).

### Gaps
- No user testing data; "no data-saver" is a static-code finding (no setting, no branch), not a runtime confirmation.

## Tests for media/video

### Takeaway
**Tuba has zero tests for media, video, attachments, or the viewer.** The entire test suite is pure-logic unit tests.

### Cited Findings
- `tests/` contains exactly 11 files: `Blurhash.test.vala, Celebrate.test.vala, Counting.test.vala, DateTime.test.vala, Html.test.vala, ShareHandler.test.vala, TagExtractor.test.vala, Tracking.test.vala, Units.test.vala, WebApHandler.test.vala` + `meson.build`. `grep -ri "media|video|audio|attach" tests/` returns **no media tests** (only Blurhash is media-adjacent, and it tests the decoder math, not rendering).
- No `test-*.py`-style integration tests, no screenshot tests, no viewer/audio pipeline tests exist in-tree (contrast: Babble's geometry-sensitive widget tests).

### Inferences
- Babble already exceeds Tuba on test coverage for media UI (geometry/timeline tests); keeping viewer/carousel + playback-lifecycle tests is a genuine differentiator, not parity work.
- The only Tuba test worth stealing is `Blurhash.test.vala` (decode correctness for the placeholder path) if Babble adds blurhash placeholders.

### Gaps
- None for in-tree tests; CI-only tests outside `tests/` were not audited.

## Maturity verdict: inline streaming vs viewer-only

### Takeaway
**Verdict: viewer-only, like Babble.** Tuba does not do inline timeline streaming at any level — the timeline is 100% static thumbnails, and even "streaming" inside the viewer is fully delegated to Clapper (or download-then-play on the GtkVideo fallback). Audio is the exception that proves the rule: a real streaming GStreamer pipeline, but only inside the viewer.

### Cited Findings
- Timeline: `Widgets.Attachment.Image` loads `preview_url` stills via libsoup→Pixbuf; video/GIFV/audio tiles differ only by center icon (`media-playback-start-symbolic` / `tuba-music-note-symbolic`); no `Gtk.Video`, `ClapperGtk.Video`, `Gst`, `preview_url` motion, or play button exists anywhere under `src/Widgets/Attachment/` or `src/Widgets/Status.vala` attachment wiring.
- Viewer video: Clapper `MediaItem.cached` streams (with enhancers for codec/HLS coverage); non-Clapper path downloads the whole file via `Host.download` before `set_filename` — the only "stream" flag (`stream=true`) is used for audio-remote/Funkwhale/Drive/PeerTube callers, never for Mastodon video attachments from the timeline (`Box.open_all_attachments` passes `stream=false` for non-audio).
- Viewer audio: `uridecodebin` streams the URL directly with seek support (`Stream.vala`), so audio is genuinely streamed — but again viewer-only.
- Maturity signals: v0.11.1 (Sep 2026 HEAD), ~15 media-related commits since 2024 (Clapper 0.10 prep, Loupe-matched zoom/controls, audio visualizer, GIFV fixes, remote-media handling, custom thumbnails) — active and polished **as a viewer**, with no roadmap visible in code toward inline playback.

### Inferences
- Benchmark bottom line for Babble: **parity is trivially achieved by matching the viewer** (Clapper streaming + fallback + audio GStreamer + carousel/fullscreen/zoom). Tuba is beatable, not ahead: it has **no inline playback, no duration badges, no GIF animation, no data-saver, no subtitles, no media tests**. The single highest-leverage Babble feature is inline muted-looping GIFV/video in the timeline with duration badges — Tuba has none of the three.
- Second-order steal: Tuba's two-tier backend (Clapper preferred, GtkVideo/GStreamer fallback, feature-flag compiled) and its Flatpak codec story (ffmpeg-full + enhancers extensions) are the correct architecture to copy; its audio pipeline string-launch + 1s poll is the part to *not* copy.

### Gaps
- Whether upstream Tuba has open issues/PRs for inline playback (GitHub/Codeberg) was not checked — only in-tree code and git log as of 2026-09-29.
