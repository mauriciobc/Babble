# Babble media architecture and streaming gaps

## Architecture, conventions, and test harness

### Takeaway
Babble is a Python/PyGObject GNOME 51 GTK 4.24+ Mastodon client; its tests run under headless mutter at 1280x1024 with Cairo and C locale, and new media code must respect 800-line file limits, weak-ref signal discipline, and install_data registration.

### Cited Findings
- Babble is “a Mastodon client for GNOME written in Python with PyGObject, on GTK 4.24+ and libadwaita 1.10+ (GNOME 51)”, using libsoup 3, libsecret, libspelling — [CLAUDE.md](CLAUDE.md)
- `src/meson.build` pins `LANG=C`, `LC_ALL=C` and `GSK_RENDERER=cairo` in `test_env` and `fatal_test_env` because translated strings change geometry and a GL renderer without context aborts — [src/meson.build](src/meson.build)
- Suite command is `XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- meson test -C build -j1`; 1280x1024 is required or `compose widgets` and `timeline memory` geometry tests fail — [AGENTS.md](AGENTS.md)
- `thread profile render` and `oauth callback` spawn their own Xvfb at 1280x1024 with `GDK_BACKEND=x11`, so Xvfb binary must be installed — [AGENTS.md](AGENTS.md)
- Each test is `src/test-*.py` printing `ok - …` / `FAIL - …`; exits 77 on no display; new modules must be added to `install_data` in `src/meson.build` or `file size rule` test fails — [AGENTS.md](AGENTS.md)
- `window.py` is an exception to the 800-line rule (`file size rule`); modules like `portals.py`, `auth.py`, `sidebarhost.py` stay under 800 lines — [CLAUDE.md](CLAUDE.md)
- Weak signal handling via `pages.connect_weak` / `pages.weak_method` is required; plain `connect` forms C cycles Python GC cannot see — [CLAUDE.md](CLAUDE.md)
- `src/meson.build` installs `mediaviewer.py`, `mediaplayback.py`, `mediazoom.py`, `mediacache.py`, `mediabox.py`, `imagecache.py` — [src/meson.build](src/meson.build)

### Inferences
- Any new inline/streaming video widget must be lazy-built in `StatusRow` (optional parts built on demand), decode off main thread, and be registered in both `src/meson.build` install list and `src/test-*.py` suite with the cairo/C test env.
- `GSK_RENDERER=cairo` means video tests cannot rely on GL context; `mediaviewer teardown` already documents SIGABRT without cairo.

### Gaps
- No documented line count for `mediaviewer.py` exception status beyond the file-size test; current size ~1058 lines per arch plan.

## How media is rendered, cached, and played today

### Takeaway
Images/GIFs/audio/video are all still-frame thumbnails in timelines via `_TextureCache`; full video/audio/gifv playback exists only in the full-screen `MediaViewer` overlay, which downloads the entire file via libsoup then plays from a local temp file with `Gtk.Video`/`Gtk.MediaFile` (GstPlay). There is no inline timeline video playback.

### Cited Findings
- `entities.attachment_still_url` (via `timelinebase.decodable_attachment_url`): `image` → `url` or `preview_url`; everything else (`video`, `gifv`, `audio`) → `preview_url` or `url` — [src/entities.py](src/entities.py)
- `mediabox.MediaBox` shows at most 4 visual (`image`/`video`/`gifv`) attachments in a grid; `video` gets a play glyph, `gifv` a GIF badge, `audio` and unknown become text `_file_row`s — [src/mediabox.py](src/mediabox.py)
- All timeline tiles call `_TextureCache.load(url, picture, cover)` — a `Gtk.Picture` with a static `Gdk.Texture`, never a `Gtk.MediaFile` — [src/mediabox.py](src/mediabox.py)
- `_TextureCache` / `imagecache.Fetcher`: single `Soup.Session` (30s timeout), LRU of decoded textures (`MEDIA_TEXTURE_CACHE_SIZE=48` in `timelinebase`), in-flight coalescing per `(url, cover)` key, failures logged not cached, 16 MB encoded cap, 16 MP / 8192px decode budget — [src/mediacache.py](src/mediacache.py)
- `mediaviewer.present_media` opens an `Adw.Bin` overlay (not dialog) covering the window; `MediaViewer` classifies by `attachment['type']`: `image` → `ZoomPicture` texture, `video`/`gifv`/`audio` → `_show_stream` — [src/mediaviewer.py](src/mediaviewer.py)
- `_download_stream` in `mediaplayback.py` downloads fully with `download()` (libsoup, 64 KiB chunks, atomic `replace_async`) to `GLib.dir_make_tmp('babble-media-XXXXXX')` because `Gtk.MediaFile` via gvfs http is unavailable in Flatpak sandbox — [src/mediaplayback.py](src/mediaplayback.py)
- `_start_stream`: `Gtk.MediaFile.new_for_file(destination)`; `video` → `Gtk.Video`, `gifv` → muted looping `Gtk.Picture(paintable=stream)`, `audio` → `Gtk.MediaControls` + cover + icon — [src/mediaplayback.py](src/mediaplayback.py)
- Metered connection defers video/audio/gifv download behind a preview + "Load Video/Audio" button (`_show_deferred`); power-saver blocks gifv autoplay — [src/mediaplayback.py](src/mediaplayback.py)
- `release_media` must pause stream, drop widget, let widget disposal tear down GstPlay; never `MediaStream.clear()` or `set_media_stream(None)` while widget alive (SIGSEGV coredump 45798) — [src/mediaplayback.py](src/mediaplayback.py)
- `TEXTURE_CACHE_SIZE=6` viewer store is separate from timeline cache to avoid evicting thumbnails — [src/mediaviewer.py](src/mediaviewer.py)
- `notificationrow.NotificationPost._set_media` uses `preview_url or url` static thumbnails (max 4, 96px), no click-through to viewer — [src/notificationrow.py](src/notificationrow.py)
- Catch-Up `PostTile` shows `image`/`video`/`gifv` as static pictures via `mediacache._TextureCache.load`; video/gifv get a play icon only, click opens thread not playback — [src/catchup/tiles_render.py](src/catchup/tiles_render.py)
- `statusviews.EditHistoryView` reuses `MediaBox` for revision attachments — [src/statusviews.py](src/statusviews.py)
- `composeattach.AttachmentRow`: video/audio must be lone attachment (`must_be_alone`); thumbnail via `Gdk.Texture.new_from_filename` (video shows first-frame or icon), audio shows icon only — [src/composeattach.py](src/composeattach.py)

### Inferences
- Inline “streaming” playback in timeline is the greenfield: the codebase has no `Gtk.Video` outside the viewer, no progress/seek outside `Gtk.MediaControls` in viewer, and no per-row media lifecycle for video.
- GIFs: `image/gif` uploads are transcoded server-side to `gifv` (MP4); static GIF display is one still frame via `Gdk.Texture.new_from_bytes`/`GdkPixbuf` — no animated GIF widget.

### Gaps
- Whether `Gdk.Texture.new_from_bytes` animates GIFs was not found in code comments/tests.

## Existing video, HLS, MP4, WebM, GStreamer, GtkVideo references

### Takeaway
Video support is real but viewer-only and file-download-based: `Gtk.Video` + `Gtk.MediaFile` (GstPlay) play MP4/WebM/OGG after full download; zero HLS, zero direct-media-file streaming, zero inline playback.

### Cited Findings
- `grep -r "Gtk.Video\|Gtk.MediaFile\|GstPlay\|GStreamer\|HLS\|hls\|m3u8\|mp4\|webm\|GtkVideo"` across `src/` returns hits only in `mediaplayback.py` (GstPlay lifetime, `Gtk.MediaFile.new_for_file`, `Gtk.Video`), `mediaviewer.py` (teardown note), `mediabox.py` (type checks), `composeupload.py`/`test-media-limits.py` (MIME limits) — [src/mediaplayback.py](src/mediaplayback.py)
- No file contains `hls`, `m3u8`, `dash`, `adaptive`, `exo`, `vlc`, or a `.m3u8` suffix check — [src/mediaplayback.py](src/mediaplayback.py)
- `webcolumn` sets `enable_media_stream=False` (WebKit setting, unrelated to Mastodon streaming) — [src/webcolumn.py](src/webcolumn.py)
- `streaming.py` is Mastodon WebSocket timeline streaming (user/public/list/hashtag), not media streaming: multiplexed over `wss://<instance>/api/v1/streaming?stream=user`, 4 MB max payload, 30s keepalive/backoff — [src/streaming.py](src/streaming.py)
- `test-mediaviewer-teardown.py` embeds a Theora-in-Ogg clip (base plugins) precisely because H.264 needs a decoder GTK does not ship; stream error surfaces when no decoder exists — [src/test-mediaviewer-teardown.py](src/test-mediaviewer-teardown.py)
- `composeupload.uses_video_size_limit` treats `video/` and `audio/` as one bucket against `video_size_limit`; `image/gif` is an image (server makes gifv after upload) — [src/composeupload.py](src/composeupload.py)

### Inferences
- “Streaming video support” likely means (a) inline/progressive video without full download, and possibly (b) HLS for Mastodon >? (Mastodon serves MP4/WebM, not HLS, for attachments; HLS would matter for live/other feeds). Both are absent.
- Adding HLS would require `gst-plugins-good/bad` HLS demuxer at runtime, which the Flatpak manifest does not declare (see packaging).

### Gaps
- Mastodon docs on attachment formats (served `url` vs `remote_url`) not vendored in repo; relies on API dict fields.

## URL handling, proxying, expiring URLs, and media test coverage

### Takeaway
Babble trusts Mastodon’s `url`/`preview_url`/`remote_url` directly over `is_web_link`-validated https; no signing, no proxy rewriting, no expiry refresh; media tests pin helpers, teardown ordering, cache coalescing, and limits but never network playback.

### Cited Findings
- `mediaviewer.show_index` uses `attachment.get('url') or attachment.get('remote_url')` with no re-fetch, no signature, no expiry check — [src/mediaviewer.py](src/mediaviewer.py)
- `imagecache.get_message` refuses non-`is_web_link` URLs (file/javascript/ftp/data) before building `Soup.Message` — [src/imagecache.py](src/imagecache.py)
- `download()` checks HTTP status 2xx via `_check_status`; no retry, no signed-URL refresh — [src/mediaplayback.py](src/mediaplayback.py)
- `test-mediaviewer.py` (`mediaviewer helpers`) pins `_texture_error` translation and invalid-download error only — [src/test-mediaviewer.py](src/test-mediaviewer.py)
- `test-mediaviewer-teardown.py` (`mediaviewer teardown`, 300s timeout, fatal-criticals) loops 12 open/close cycles of a playing Theora clip, asserting widget disposal order — [src/test-mediaviewer-teardown.py](src/test-mediaviewer-teardown.py)
- `test-imagecache.py` (`image cache`), `test-avatarcache.py` (`avatar cache`), `test-imagedecode.py` (`image decode`), `test-mediabox.py` (`mediabox keyboard`), `test-media-limits.py` (`media limits`) cover cache coalescing, decode bounds, grid keyboard nav, size limits — [src/meson.build](src/meson.build)
- `test-timeline-memory.py` (`timeline memory`) asserts pages/rows/textures freed and `mediaviewer.media_overlay` geometry at 1280x1024 — [src/meson.build](src/meson.build)
- `api/media.py` declares `media.upload` (POST `/api/v2/media`, multipart) and `media.status` (GET `/media/%s`, 206 while processing); composer polls `status` until `url` appears, max 80 attempts × 1.5s — [src/api/media.py](src/api/media.py)

### Inferences
- Expiring/signed CDNs would break once; the fix space is either re-reading the status for fresh URLs or proxying through the instance, neither of which exists.
- Viewer test with no network (embedded Theora) is the pattern to follow for any streaming test.

### Gaps
- No source discusses S3 presigned URL expiry for Mastodon media; assumed out of scope by direct-URL design.

## Performance, memory, packaging, and dependency constraints

### Takeaway
Budget is tight: 48 timeline textures, 256 avatars, 6 viewer textures, worker-thread decode, 16 MP decode cap; Flatpak is GNOME 51 runtime with no GStreamer plugin add-ons declared, so new codecs/HLS must come from the runtime or a new `extension`/`finish-args` entry.

### Cited Findings
- `MEDIA_TEXTURE_CACHE_SIZE=48`, `AVATAR_CACHE_SIZE=256`, `AVATAR_DECODE_SIZE=96`, 16 MP / 8192px decode budget, header-peek (JPEG SOF scan first 64 KiB) before allocating — [src/timelinebase.py](src/timelinebase.py)
- Decode runs in `Gio.Task.run_in_thread` (`_decode_async`); display scale read on main thread — [src/mediacache.py](src/mediacache.py)
- `PERFORMANCE.md` documents RSS near 96 MB retained after fixes, `CARD_SHELLS_KEPT=100`, `TIMELINE_MAX_ITEMS=400` trim with pagination — [PERFORMANCE.md](PERFORMANCE.md)
- Flatpak manifest: `runtime org.gnome.Platform 51`, `finish-args` network/ipc/wayland/dri/secrets only; modules = `libspelling` + `libpeas` (python-only) + `babble`; no `gst-plugins-good/bad`, no `openh264`, no codec extension — [build-aux/flatpak/io.github.mauriciobc.Babble.json](build-aux/flatpak/io.github.mauriciobc.Babble.json)
- `CLAUDE.md` notes `mediaplayback.py` was extracted from `mediaviewer.py` (ARCH-14) and `mediazoom.py` (ARCH-19); remaining `mediaviewer.py` chrome stays — [CLAUDE.md](CLAUDE.md)
- `GSettings` keys for media: `media-viewer-mode` (fullscreen preference), `show-sensitive-media`, `show-spoilers`, `letterbox-media` (read via `_flag`) — [src/mediaviewer.py](src/mediaviewer.py)
- CI uses Flathub’s `ghcr.io/flathub-infra/flatpak-github-actions:gnome-51` image + `flatpak-builder-lint` — [.github/workflows/ci.yml](.github/workflows/ci.yml)

### Inferences
- Inline video must be capped (max concurrent streams, pause off-screen, reuse one pipeline) or RSS/CPU budgets blow; the `timeline memory` geometry test will need video-aware sizing.
- H.264/HEVC playback in Flatpak depends on user-installed `org.freedesktop.Platform.ffmpeg-full` extension; GNOME 51 runtime ships `gst-plugins-base` (Theora/Vorbis) + `good` but not `bad`/`ugly` by default — confirm with runtime manifest before promising MP4.

### Gaps
- Exact GNOME 51 runtime GStreamer plugin set was not enumerated from the manifest; flagged as inference.
- `gauntlet/` directory exists but was not read for media-specific goals; may hold video quality bars.
