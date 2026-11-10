# Babble vs Tuba: Video Support — Comparative Benchmark and Revised Recommendation

> Corpus: Babble repo `/home/mauriciobc/Documentos/Code/Babble` (Python/PyGObject, main branch incl. `reports/Babble streaming video implementation.md`, `CLAUDE.md`, `src/mediaviewer.py`, `src/mediaplayback.py`, `mediabox.py`, `entities.py`, `sysstate.py`, `src/meson.build`, `src/test-mediaviewer-teardown.py`) vs Tuba repo `/home/mauriciobc/Documentos/Code/Tuba` (Vala, `src/Views/MediaViewer.vala`, `src/Widgets/Attachment/Image.vala`, `src/Widgets/AudioPlayer/Stream.vala`, `src/API/Status/PreviewCard.vala`, `src/Utils/Host.vala`, `src/Services/Helpers/Image.vala`, `meson.build`, `meson_options.txt`, `build-aux/dev.geopjr.Tuba.Devel.json`, `tests/`). Convention: **[Verified]** = read in code; **[Inferred]** = logical consequence, not directly observed; file paths are the source.

## Side-by-side benchmark matrix (verified vs inferred)

### Takeaway
Tuba is ahead of Babble only in the full-screen viewer player backend (Clapper with MPRIS + caching + enhancers) and in Flatpak codec packaging; it is even with Babble on the timeline (both static thumbnails, no inline playback) and strictly behind on metered/power gating, teardown discipline, and tests — so it validates Babble's viewer-only progressive MVP and invalidates any plan to copy its team-wide download-everything model.

### Cited Findings
- **Architecture.** Babble is Python/PyGObject on GTK 4.24+ / libadwaita 1.10+, Meson build, no pytest — [Source](file:///home/mauriciobc/Documentos/Code/Babble/CLAUDE.md).
  Tuba is Vala (Valac), GTK ≥4.19, libadwaita ≥1.8 — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/meson.build).
- **Timeline thumbnail.** Babble `MediaBox` draws a static `Gtk.Picture` from `_TextureCache` (48-entry LRU, coalesced fetches, 16 MP / 8192 px budget) via `entities.attachment_still_url` (`image`→`url|preview_url`, else `preview_url|url`), max 4 visual attachments, play glyph / GIF badge — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediabox.py) and prior report lines 13–15.
  Tuba timeline attachment is `Widgets.Attachment.Image` → `FocusPicture` fed by `Helper.Image.request_paintable(entity.preview_url, entity.blurhash, …)` with a play-icon overlay for video/gifv — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/Attachment/Image.vala). Neither realizes a video widget in timeline rows — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediabox.py).
- **Inline playback.** Babble: none — clicks open `present_media` full-screen viewer — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediabox.py).
  Tuba: also none — `on_click` → `on_any_attachment_click(entity.url)` → opens `MediaViewer` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/Attachment/Image.vala). No `Gtk.Video`/`ClapperGtk.Video` instantiated in any timeline/attachment widget; all `Video` construction is inside `MediaViewer.add_media` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- **Media viewer — Babble.** `MediaViewer` (Adw.Bin overlay, not Adw.Dialog) classifies by type: `video`/`gifv`/`audio` → `mediaplayback._show_stream` which **downloads the entire file via libsoup in 64 KiB chunks to an atomic temp file** (because `Gtk.MediaFile` over GVFS HTTP is unavailable in Flatpak) then `Gtk.MediaFile.new_for_file` + `Gtk.Video` (video), muted looping `Gtk.Picture` (gifv), `Gtk.MediaControls` (audio) — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
- **Media viewer — Tuba.** `Views.MediaViewer : Gtk.Widget, Gtk.Buildable, Adw.Swipeable` with an inner `Item : Adw.Bin` per attachment; video path has two compile-time backends: `ClapperGtk.Video` when Clapper is present, else `Gtk.Video` with `graphics_offload=ENABLED` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
  Clapper path: `video.player.download_dir = clapper_cache_dir; download_enabled=true; download_complete` handler, `QueueProgressionMode.REPEAT_ITEM` for gifv, `ClapperGtk.SimpleControls` fading overlay, optional MPRIS feature (`clapper-mpris` proxy or `Clapper.Mpris`) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
  Non-Clapper path: either streams `File.new_for_uri(url)` directly (`stream==true`, used for PeerTube/Funkwhale cards) or full `Host.download(url)` → `video.set_filename(path)` (same download-then-play model as Babble) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala); [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Utils/Host.vala).
- **Codec / Flatpak — Babble.** Manifest `org.gnome.Platform 51`, finish-args network/IPC/Wayland/DRI/secrets, only `libspelling` + `libpeas` modules, **no GStreamer plugin, codec extension, or Clapper module** — [Source](file:///home/mauriciobc/Documentos/Code/Babble/build-aux/flatpak/io.github.mauriciobc.Babble.json).
- **Codec / Flatpak — Tuba.** Devel manifest `org.gnome.Platform 50`, `add-extensions: org.freedesktop.Platform.ffmpeg-full@24.08` **and** `com.github.rafostar.Clapper.Enhancers@stable`, builds Clapper 0.10.0 from git with `-Dintrospection=enabled -Dvapi=enabled -Ddiscoverer=disabled -Dmpris=disabled`, `-Dclapper=enabled -Dgstreamer=enabled` in Tuba's own config opts — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/build-aux/dev.geopjr.Tuba.Devel.json).
  Meson: `clapper` is an optional `feature` option (default auto), `gstreamer` likewise; `CLAPPER`/`CLAPPER_0_10`/`CLAPPER_MPRIS` defines are conditional on the deps being found — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/meson.build).
- **Cache.** Babble viewer: 6-entry texture store separate from the 48-entry timeline cache, so full-screen cannot evict scrolling posters; temp downloads deleted on unload (`_discard_temp`) — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaviewer.py).
  Tuba image cache: `Soup.Cache` SINGLE_USER, 100 MiB max, at `cache_path/soup/media`, with `clear_cache`/`flush_cache` on network events — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Services/Helpers/Image.vala).
  Tuba video cache: Clapper download cache dir + `clapper_cached_urls` map + `clapper_cached_locations` list hard-capped at 10 entries (oldest-first eviction) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala); non-Clapper: `cache_path/manual/media` wiped wholesale on shutdown — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Utils/Host.vala).
- **API attachment handling.** Both use `url` with `remote_url` fallback, `preview_url` for stills — Babble: `attachment.get('url') or attachment.get('remote_url')` — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py). Tuba adds a **remote-media recovery path**: when `entity.url` is null/unproxied, dialog asks to fetch `entity.remote_url` from the remote instance, guesses `MediaType` from filename content-type, and opens it in the viewer (streaming for audio) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/Attachment/Image.vala).
  Tuba also has **special preview-card playback**: PeerTube / enhancer-supported URLs are rewritten to `peertube://` scheme and handed to Clapper HLS-capable queue; Funkwhale via API JSON → audio `stream=true` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/API/Status/PreviewCard.vala).
- **Autoplay policy.** Babble: metered connection defers any video/audio/gifv download behind preview + “Load Video/Audio” button; power-saver blocks GIFV autoplay (`gtk-enable-animations` + `power-saver` gate); GIFV click-to-toggle; video/audio autoplay only on explicit viewer open; idle-inhibit only for video/audio while playing — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
  Tuba: **no metered, no power-saver, no autoplay-policy code at all** (grep across `src/` returns nothing; only `auto_inhibit=true` on `ClapperGtk.Video` and a settings key `media_viewer_last_used_volume`) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala). GIFV always loops; `on_carousel_page_changed` calls `play_video(pos)` immediately — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- **Error handling.** Babble viewer: `stream.notify::error` → translated `Could Not Load Media` StatusPage with `_Try Again` + `_Open in Browser` — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py). Download failure → error page, atomic temp discarded — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
  Tuba non-Clapper download: `catch (Error e) { app.inform(_("Error"), e.message) }` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala). Audio `Stream` bus: `critical(@"Gst Error: …")` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/AudioPlayer/Stream.vala). No codec-missing guidance found.
- **A11y.** Babble: alternate text via `update_property(AccessibleProperty.LABEL)` on viewer + tiles, GIFV muted-loop glyph, focusable tiles with key handler — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediabox.py).
  Tuba: `alt_text` on picture, `alternative_text` on timeline images, `use_blurhash` pause — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala); Clapper `SimpleControls` provides playback chrome — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- **Performance / memory.** Babble: pinned SIGSEGV teardown rule (pause, drop widget, let disposal retire GstPlay thread; never clear stream early), 12-cycle teardown test — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py); [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-mediaviewer-teardown.py).
  Tuba viewer `~Item` destructor for `Gtk.Video`: `media_stream.stream_unprepared(); set_file(null); set_media_stream(null)` while the widget is being torn down — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
  Tuba `Audio.Stream:destroy()` removes its timeout + bus watch, sets pipeline NULL — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/AudioPlayer/Stream.vala).
- **Test harness.** Babble: ~50 standalone `src/test-*.py` scripts printing `ok -`/`FAIL -`, run under headless mutter (1280×1024, `GSK_RENDERER=cairo`, `LANG=C`), including `mediaviewer teardown` (embedded base64 Theora clip, no network, 12 cycles), `timeline memory`, geometry asserts — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/meson.build); [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-mediaviewer-teardown.py); [Source](file:///home/mauriciobc/Documentos/Code/Babble/CLAUDE.md).
  Tuba: 11 unit tests (`Blurhash`, `Celebrate`, `Counting`, `DateTime`, `Html`, `TagExtractor`, `Tracking`, `Units`, `WebApHandler`, `ShareHandler`), **zero media/video/viewer tests** (grep for media/video in `tests/` = no matches) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/tests/).

#### Matrix (synthesis)

| Tier | Tuba vs Babble | Evidence |
|---|---|---|
| Full-screen video player | **Tuba ahead** (Clapper queue, SimpleControls, MPRIS, enhancers, fading overlay, volume memory) | MediaViewer.vala:1108–1121, meson.build:160–166 |
| Flatpak codecs | **Tuba ahead** (ffmpeg-full + Clapper.Enhancers extensions) | dev.geopjr.Tuba.Devel.json lines 6–21 |
| PeerTube/HLS + Funkwhale special cards | **Tuba ahead** (enhancer rewrites, streams) | PreviewCard.vala:175–246 |
| Remote-url recovery dialog | **Tuba ahead** (unique UX) | Attachment/Image.vala:142–202 |
| Timeline thumbnail | **Equal** (both static `preview_url`, no inline) | Attachment/Image.vala vs mediabox.py |
| Inline/timeline playback | **Equal-zero** (neither has it) | Image.vala:129–140 vs mediabox.py:598–635 |
| Cache | **Equal** (Babble splits viewer/timeline; Tuba 100 MiB Soup.Cache + 10-file Clapper cap) | mediaviewer.py:48–91 vs Image.vala |
| Metered/power gating | **Babble ahead** (Tuba: none) | mediaplayback.py:242–264 vs grep-null in Tuba |
| Teardown safety | **Babble ahead** (pinned rule + 12-cycle test vs Tuba's clear-while-alive destructor) | mediaplayback.py:186–209 vs MediaViewer.vala:317–328 |
| Error UX | **Babble ahead** (translatable retry/browser page vs `inform(Error)`) | mediaplayback.py:405–412 vs MediaViewer.vala:1141–1144 |
| A11y | **Equal** (both set labels, both loop GIFV) | mediab* / MediaViewer.vala:1114 |
| Perf/mem | **Babble ahead** (bounded caches + GstPlay rule vs Tuba: 100 MiB cache, no pipeline cap) | mediaviewer.py:48–91 vs Image.vala:36 |
| Test harness | **Babble ahead** (media teardown/geometry tests vs 11 unit tests, none media) | meson.build vs tests/ |

### Inferences
- Tuba's viewer-only architecture proves that the market leader deliberately has **not** solved inline timeline playback either; Babble's phased plan (viewer-first, inline second) is not behind the field — it is the field's unstated consensus.
- Tuba's dual-backend `#if CLAPPER` branching (every video method written twice) is the code smell that warns Babble *against* adopting Clapper: 200+ lines of conditional code to support two players, with version (`CLAPPER_0_10`) and feature (`CLAPPER_MPRIS`) matrix on top.
- Tuba's download-then-play model (Host.download → set_filename) is a validation that **even with Clapper available, cold remote streaming without a cache is unreliable enough that Tuba pre-downloads**; Babble's plan to do progressive range playback is *riskier* than Tuba's, not safer — it must justify why Babble doesn't need Tuba's fallback.

### Gaps
- No source for which Clapper version ships on Flathub stable vs devel, or whether `Clapper.Enhancers` extends to HLS in the stable manifest — only the Devel manifest was available locally (Flathub manifest not in repo).
- No Tsuka/Tusky/Elk evidence collected (out of scope for this subagent).

## Python feasibility without Clapper (and what Tuba's gaps validate)

### Takeaway
Babble can implement every Tuba capability that matters (viewer playback, MPRIS-less GNOME integration, enhancer-less PeerTube fallback) with `Gtk.Video` + `Gtk.MediaFile` purely from PyGObject, and Tuba's own non-Clapper fallback *is* proof of that sufficiency — Clapper buys polish (MPRIS, enhancers, queue), not raw playback.

### Cited Findings
- Tuba's non-Clapper fallback is a complete `Gtk.Video` path: `graphics_offload=ENABLED`, `video.set_file(File.new_for_uri(url))` for streaming or `download_video → set_filename(path)` for buffered playback, `video.loop = true` for gifv — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- Tuba's audio path is **not** `Gtk.Video` at all but a hand-rolled GStreamer `uridecodebin name=uridecodebin ! audioconvert ! volume ! level ! autoaudiosink` pipeline wired in Vala via `Gst.parse_launch` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Widgets/AudioPlayer/Stream.vala).
- Clapper is built with `-Dintrospection=enabled -Dvapi=enabled` and consumed from Vala as `ClapperGtk.Video` + `Clapper.Player` + `Clapper.MediaItem` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/build-aux/dev.geopjr.Tuba.Devel.json); [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- Clapper version variance is real complexity: `CLAPPER_0_10` changes the enhancer-check API signature (`enhancer_check(scheme, host)` vs `enhancer_check(typeof(Extractable), scheme, host, null)`), and `CLAPPER_MPRIS` toggles between two MPRIS registration APIs — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/API/Status/PreviewCard.vala); [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- Babble already drives `Gtk.MediaFile` from Python for all three kinds (`video`/`gifv`/`audio`) with weak-signal disposal discipline — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
- Babble's_flatpak has no GStreamer module; GTK's GstPlay needs a decoder to exist at runtime (Theora test clip was specifically chosen from GStreamer base, since GTK's deps guarantee it) — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-mediaviewer-teardown.py).

### Inferences
- **Viewer playback**: already done in Babble (identical primitive to Tuba's fallback). No Clapper needed.
- **Timeline inline playback (muted loop)**: `Gtk.Video(autoplay, loop, muted)` + `Gtk.MediaFile.seekable` is sufficient; `gtk4paintablesink`/`playbin3` are *only* needed for track selection, subtitle overlays, or adaptive variant control — none of which Mastodon progressive MP4s need. **Steal**: Tuba's non-Clapper `File.new_for_uri` streaming pattern (stream=true) as the *default* for inline, with download fallback; **Avoid**: building a second `#if` backend.
- **Audio with visualizer**: only Tuba capability that *requires* a bespoke Gst pipeline (level element). Babble's `Gtk.MediaControls` covers transport; a visualizer would need a parallel `Gst.parse_launch` in Python — defer it, it has no Mastodon-API driver.
- **MPRIS**: Tuba's MPRIS is a Clapper-plugin feature (`clapper-mpris`), not a Tuba feature; replicating it in Python means adding a separate `Gst`/`Mpris` integration with same plugin dependency — **out of scope** unless Clapper is adopted.
- **HLS/PeerTube cards (Tuba's one genuine leap)**: Tuba gets this free from Clapper enhancers (`peertube://` rewrite + HLS); without enhancers, Babble can only link out to browser/external player. This is a *card-open* feature (link interception), **not** inline playback, and therefore can safely be a Phase 3 browser-fallback, not a Phase 1 blocker.
- **Conclusion for the plan**: Tuba's fallback path *validates* Babble's `Gtk.Video`-first design (Tuba ships it in production as a real backend); Tuba's HLS gap-free enhancers *invalidate* extrapolating its model to Mastodon-native media, because Mastodon has no HLS mast at all — the gap is a Fediverse-API artifact, not a Mastodon-media requirement.

### Gaps
- Python introspection coverage of `gtk4paintablesink`/`playbin3` not verified locally (no `gst-inspect-1.0` output captured); treat `playbin3`-in-Python as inferred-only.

## Revised phased roadmap (keep / cut / reorder)

### Takeaway
Reorder Babble's roadmap to **codec inventory → viewer progressive range playback → timeline inline → PeerTube/HLS browser bridge**, cut `playbin3`/custom-sink until viewer+HLS both demand it, and steal Tuba's `stream=true` progressive delivery + ffmpeg-full extension while avoiding its download-everything default and `#if CLAPPER` dual-backend.

### Cited Findings
- Babble's existing pipeline already downloads the *whole* file before `Gtk.MediaFile.new_for_file`, with a comment citing GVFS HTTP unavailability in Flatpak — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
- Tuba's non-Clapper *streaming* path (`video.set_file(File.new_for_uri(url))`) skips download entirely when `stream=true`; it is used for remote media, PeerTube, Funkwhale — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- Tuba's download path dedupes by file existence only (`if (!file.query_exists())`), no size/TTL/verification — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Utils/Host.vala).
- Tuba ships **no** inline playback (see matrix) and **no** metered/power enforcement (see matrix).
- Babble's test clip choice documents the codec-probe problem: H.264 needs a non-base decoder, so CI-only base (Theora/Ogg) tests cannot prove H.264 works — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-mediaviewer-teardown.py).

### Inferences — Revised recommendation

| Phase | What | Keep / Cut / Steal / Avoid |
|---|---|---|
| **0 — Codec & range inventory (new Phase 0)** | Runtime `gst-inspect-1.0` probe script; `curl -I` range check vs live mastodon.social media host; manifest `add-extensions` amendment adding `org.freedesktop.Platform.ffmpeg-full` (Babble's missing Tuba-equivalent). Manual, no widget code. | **Steal from Tuba**: ffmpeg-full extension line. **Keep**: prior report's “inventory first”. |
| **1 — Viewer progressive (reordered before inline)** | Replace full-download in `_download_stream` with `Gtk.MediaFile.new_for_uri(url)` progressive play + download-as-fallback only on `notify::error` (Tuba's `stream=true` pattern); keep metered deferral, keep teardown rule; translated codec-missing error (“install org.freedesktop.Platform.ffmpeg-full”). | **Steal**: `set_file(new_for_uri)` progressive path. **Avoid**: Tuba's unconditional download (`Host.download` always). **Keep**: metered/power gates (Tuba lacks them). |
| **2 — Timeline inline (narrowed)** | `Gtk.Video` muted-loop in `MediaBox` tile only when ≥50% visible, max 1 active pipeline app-wide, unknown → pause; reuse `_TextureCache` poster until `prepared`. GIFV respects power-saver; video never autoplays with sound. | **Keep**: prior report's one-pipeline cap. **Cut**: any `playbin3`/`gtk4paintablesink` work, any custom sink — defer to Phase 4 trigger (track selection / subtitle demand). |
| **3 — Surfaces + PeerTube bridge** | Notifications/Catch-Up parity (open viewer, not inline); `remote_url` recovery dialog (Tuba's unique steal); PeerTube card → open in browser external player (not enhancer). | **Steal**: remote-url dialog. **Cut**: Clapper enhancer rewrite (no enhancer runtime). |
| **4 — Detail hardening (deferred)** | Only if HLS demands appear: `playbin3` + `gtk4paintablesink` module behind capability gate; MPRIS out (Tuba proves it's plugin-owned). | **Avoid**: Clapper vendoring (pre-1.0 API churn, not in `org.gnome.Platform`, `#if` matrix cost). |

Critical reordering rationale: Tuba proves viewer playback with `Gtk.Video` is shippable; nothing in the codebase proves range-streaming works in the GNOME 51 Flatpak until the Phase 0 probe runs. Building inline over an unverified codec/range substrate repeats the exact failure Babble already pinned with its Theora-only test (H.264 silently absent).

### Gaps
- Whether `Gtk.MediaFile.new_for_uri(https://…)` works inside the `org.gnome.Platform 51` sandbox (vs the GVFS claim in mediaplayback.py) is not verified here — Phase 0 must test it inside `flatpak-builder` runtime, not the host.

## Acceptance criteria: tests that prove it (Babble-mutter harness)

### Takeaway
Tuba has no media tests to copy; Babble must extend its own teardown/matrix suite with five new `src/test-*.py` scripts (progressive play, inline gate, teardown cycles under mutter geometry, codec-missing error, timeline-memory geometry), each mapped to a Tuba behavior Babble either steals or deliberately exceeds.

### Cited Findings
- Babble harness pins: headless mutter, `1280x1024` virtual monitor, `GSK_RENDERER=cairo`, `LANG=C`, standalone `src/test-*.py` with `ok -`/`FAIL -`, registered in `src/meson.build` with matching env and install list — [Source](file:///home/mauriciobc/Documentos/Code/Babble/CLAUDE.md).
- `mediaviewer teardown` test is the template: embedded Theora clip, 12 cycles, asserts widget-disposed + stream-not-freed-under-live-widget + child-removed, watchdog at 90 s — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-mediaviewer-teardown.py).
- Tuba has zero media tests; its `tests/` are pure unit tests — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/tests/).
- Timeline-memory geometry asserts row heights and run-shell placeholders — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/test-timeline-memory.py).

### Inferences — New tests Babble should add

| Test (mirrors) | Assertions (must print `ok -`/`FAIL -`) | Maps to Tuba behavior |
|---|---|---|
| `test-media-progressive.py` (new; mirrors `test-mediaviewer-teardown.py`) | Embedded Theora clip served via loopback HTTP `Range` header; `Gtk.MediaFile.new_for_uri(loopback)` reaches `prepared`, plays within 5 s, seeks (timestamp advances), `notify::error` is null; fallback download triggered when server returns 200-non-range or `error != null` | Steals Tuba `stream=true` (MediaViewer.vala:1131–1134); exceeds Tuba by proving range telemetry, not just “plays” |
| `test-media-inline-gate.py` (new; mirrors `test-mediabox.py`) | At 1280×1024: tile starts paused w/ poster; on `visible ≥50%` → `stream.playing==true`, muted; on scroll-away → paused + poster restored; metered GSettings → no `MediaFile` created; power-saver → GIFV no autoplay; CW/blur tile → no pipeline ever constructed (spy `MediaFile.new`) | Exceeds Tuba (no gates exist — MediaViewer.vala:1339) by pinning the gate |
| `test-media-inline-teardown.py` (extends `test-mediaviewer-teardown.py`) | 12 open/close cycles of the *timeline* cell under mutter (not Xvfb); asserts same 5 invariants (widget disposed, stream not freed early, file intact, child removed, not playing) + weakref rows freed (mirrors `test-timeline-memory.py` pattern) | **Avoid** Tuba `~Item` pattern (`stream_unprepared`+`set_media_stream(null)` mid-teardown — MediaViewer.vala:317–328) |
| `test-media-codec-probe.py` (new, no display) | Probes `avdec_h264`, `vaapih264dec`, `souphttpsrc`, `playbin3`, `gtk4paintablesink`, `hlsdemux2` via `Gst.Registry`; when H.264 decoder absent, `_show_error` text names `org.freedesktop.Platform.ffmpeg-full` | Steals Tuba packaging (Devel.json:7–13) by turning silent black-screen (Tuba's `inform(Error)`) into a translatable recovery |
| `test-media-timeline-geometry.py` (extends `test-timeline-memory.py`) | At 1280×1024 with inline cell: row allocated height unchanged ±2 px vs poster-only cell; recycled row rebind does not leak (`weak_ref` freed); `TIMELINE_MAX_ITEMS=400` cap still enforced | No Tuba equivalent (no such test) — Babble-only risk because inline changes row height budget |

Additional acceptance gates (manual, pinned): Cairo fallback (suite already pins `GSK_RENDERER=cairo` — no new gate needed, but inline test must run under it, not GL); teardown cycles = 12 per existing convention; metered/offline/power-saver matrix each toggled via `GSETTINGS_BACKEND=memory` + `sysstate` stub (`metered`/`power-saver` properties — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/sysstate.py)); geometry invariant = no row-height delta at 1280×1024. All new modules must be listed in `src/meson.build` install list or `file size rule` fails — [Source](file:///home/mauriciobc/Documentos/Code/Babble/CLAUDE.md).

### Gaps
- Tuba-like media tests cannot be “copied” because Tuba has none; the matrix above invents the suite from Babble's own prior art + Tuba's failure modes.
- Loopback `Range`-serving fixture design not validated (suggested: Python `http.server` with `Range` support inside the test, 60 s meson timeout).

## Leapfrog vs match (competitive / UX)

### Takeaway
Do not match Tuba's Clapper feature list; leapfrog it on the two axes Tuba neglected (metered/power discipline + test rigor) and steal only its two cheap UX wins (remote-url dialog, PeerTube browser bridge), because Clapper polish is inaccessible to a Python app without vendoring an unstable dependency.

### Cited Findings
- Tuba's Clapper adoption costs: source-built Clapper 0.10.0 module + enhancers extension + `CLAPPER_0_10`/`CLAPPER_MPRIS` version matrix + dual-backend `#if` code — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/build-aux/dev.geopjr.Tuba.Devel.json); [Source](file:///home/mauriciobc/Documentos/Code/Tuba/meson.build).
- Clapper's MPRIS is opt-in plugin-gated (`clapper-mpris` enhancer proxy) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- Clapper `SimpleControls` overlay + fading overlay is the only player-chrome moat Tuba has over `Gtk.Video` — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).
- README lists `clapper | ❌` (dependency-table row in Tuba README) — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/README.md) — meaning stock distro builds without Clapper degrade to the `Gtk.Video` fallback path.
- Babble already wins on consent UX (metered deferral, separate caches, weak-signal recycle, pinned teardown) — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).

### Inferences
- **Match**: nothing wholesale. Tuba's player *is* its fallback (Gtk.Video) plus Clapper chrome; Babble matching means owning the `#if` matrix in Python for zero timeline benefit.
- **Leapfrog** with three cheap, Python-feasible differentiators:
  1. **MPRIS via GStreamer directly** (only if Clapper absent): a minimal `org.mpris.MediaPlayer2.<id>` D-Bus interface advertising title/artist/duration + PlayPause — 200 lines of Gio D-Bus, no vendoring. Inferred feasible; not verified against GNOME lockscreen, treat as Phase 4 stretch.
  2. **Keyboard-first inline cells**: Babble's timeline tiles are already focusable with `Gtk.AccessibleProperty.LABEL` + key handler — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediabox.py) — so inline play/pause/seek can be fully keyboard-operable where Clapper's `SimpleControls` is mouse-centric. This is a genuine a11y leap, grounded in existing tile design.
  3. **Consent transparency badge**: surface `metered`/`power-saver` state on the tile (“Data saver — tap to load”) instead of silent deferral — Babble already owns the state (`sysstate.py`), Tuba has none; one label makes the differentiator visible.
- **Avoid** spatial-navigation-style carousel swipe-matching (Tuba's `Adw.SwipeTracker` vertical dismiss — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala)) as a Babble goal; it serves Vala's full-screen list, not Python's row-recycling timeline.

### Gaps
- Whether a minimal Python MPRIS D-Bus interface is acceptable to GNOME's media applet without Clapper's plugin was not verified; cite as stretch goal with explicit “verify against GNOME Shell media applet” gate.
- No competitive evidence on User-asked “match Tuba exactly” demand; treat leapfrog recommendation as inferred from code, not from user research.

## Adversarial-review checklist (what survives and what doesn't)

### Takeaway
The prior Babble report's core claims survive Tuba evidence except for one silent assumption (GVFS-impossible streaming) that Tuba's own streaming path contradicts — that single line must be re-tested before Phase 1, not assumed.

### Cited Findings
- Prior report (§Babble today) asserts: “`Gtk.MediaFile` over GVFS HTTP is unavailable inside the Flatpak sandbox, and only then starts `Gtk.MediaFile.new_for_file`” — [Source](file:///home/mauriciobc/Documentos/Code/Babble/src/mediaplayback.py).
- Tuba's fallback *streams* `video.set_file(File.new_for_uri(url))` for remote/PeerTube/Funkwhale without download — [Source](file:///home/mauriciobc/Documentos/Code/Tuba/src/Views/MediaViewer.vala).

### Inferences
- **Survives**: progressive-not-HLS framing, two-tier widget split, codec-inventory-first, metered/power gates, teardown discipline, test methodology — all corroborated by Tuba's actual design.
- **Does not survive unexamined**: the GVFS-impossibility premise. Tuba's working streaming (inside Flatpak) does not disprove it — GNOME 50 vs 51 GVFS backends differ, and `set_file` ≠ `MediaFile.new_for_uri` — but the prior report asserts without a Flatpak-runtime probe. Phase 0 must run `flatpak-builder` + `Gtk.MediaFile.new_for_uri(https)` inside the sandbox and record the outcome; if it works, the entire download-first architecture collapses to progressive-first.
- **Watch**: Babble's separate viewer cache (6) vs timeline (48) is a deliberate, tested split; copying Tuba's unified 100 MiB `Soup.Cache` would regress scrolling eviction — do not steal it.
- **Challenge accepted**: prior report's “streaming = range playback” framing is stricter than Tuba's (download-first). Babble should acknowledge Tuba's download-first *is* shippable and keep its own progressive claim conditional on Phase 0 range results, with download-fallback retained.

### Gaps
- No Flathub-stable manifest for Tuba was in-repo to confirm stable `ffmpeg-full`/`Enhancers` parity (only Devel). Assumption that Devel represents shipping is marked [Inferred]; verifier should check Flathub repo before quoting numbers in a final report.

---

*Evidence quality note: every factual claim above that is used to justify the recommendation was read in the cited file; [Inferred] items are flagged and limited to roadmap ordering, Python-feasibility extrapolation beyond the verified `Gtk.Video` fallback, and the MPRIS-by-D-Bus stretch goal.*
