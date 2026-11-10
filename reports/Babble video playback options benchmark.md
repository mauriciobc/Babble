# Babble video playback options: benchmark

2026-11-09. Tech Lead. Input to `reports/Babble updated streaming plan.md` (Phase 0 / Phase 1).
Harness, probes and raw results: `reports/video-playback-bench/` (`full.jsonl`, `live.jsonl`, `smoke.jsonl`).

## Verdict

**Use GstPlay + `gtk4paintablesink` (the Showtime / Fractal pattern). Keep Clapper as the named alternative. Drop `GtkMediaFile` for remote media.**

> Update (2026-11-09, after adversarial review): the engine choice stands. How long a GstPlay must live to avoid the teardown crash is **not yet settled**: per-viewer with deferred release, or resident. It is decided by experiments E1/E2 in `reports/Babble video playback plan (GstPlay).md`.
> Correction (E3, E3b): a reduced harness of Babble's teardown order ran 504 cycles with 0 crashes (E3). The real viewer does crash: 4 SIGSEGVs in ~415 open/close cycles of `present_media`/`close()` at `fd6f7b5` (E3b), with the same GstPlay use-after-free signature.
> Correction (E6): "H.264 decode is in the base runtime" is wrong. The base `libavcodec` decodes neither H.264 nor HEVC; `avdec_h264` works only because the `codecs-extra` extension (installed with the runtime by default) ships a fuller `libavcodec`. Without it, only hardware decoders play Mastodon video.
> E1 result: per-viewer engines are safe with deferred release (wait for `STOPPED`, drop from idle): 0 crashes in 436 cycles, against 6 of 7 crashing runs for immediate release on the same reproducer.

1. `GtkMediaFile` cannot stream in the sandbox. In `org.gnome.Platform//51` an `https://` `Gio.File` is a `GDummyFile`; GTK's builtin backend (`GtkGstMediaFileBuiltin`) reads through GIO (`giostreamsrc`), so it fails with "Failed to pause". On the host it works only through gvfs, at 2× the bytes, ~620 HTTP requests per play, and 15–24 s to first frame on the live post. The plan's Phase 1 premise is false. Babble's comment at `src/mediaplayback.py:284` was right.
2. GstPlay + `gtk4paintablesink` played the live post inside the GNOME 51 sandbox with nothing bundled. Every piece is in the runtime: `libgstplay-1.0`, `GstPlay-1.0.typelib`, `GstPbutils-1.0.typelib`, `libgstgtk4.so`. It is what GNOME's core video player Showtime does, in Python (`showtime/play.py`), and what Fractal does (`video_player_renderer.rs`).
3. `GtkMediaFile` teardown still crashes. Two of the four 12-cycle runs (one streaming, one with a local downloaded file) died with SIGSEGV in `g_type_check_instance_is_fundamentally_a` on a `libgstplay` thread, Babble's pinned crash, while following `release_media` order. GstPlay driven directly with `stop()`: 24 cycles, 0 crashes. This is a risk in the shipping viewer today, independent of streaming.
4. Clapper performs the same as GstPlay (it is GstPlay-like on top of `playbin3`) and adds a stream-and-cache download, ready controls, MPRIS and the yt-dlp enhancer. Its cost is bundling a pre-1.0 library that is not in the runtime. It earns its place only if Babble wants those extras; it is not needed to stream.
5. WebKitGTK costs ~+350 MB RSS (web processes) that is not returned on teardown. libmpv is fast but needs a GL bridge through ctypes and a bundled mpv + ffmpeg, and grew RSS by ~157 MB over 12 cycles. libVLC cannot embed video in a GTK 4 widget on Wayland. None of the three fits.

## Candidates

| # | Candidate | What it is | Used by |
|---|---|---|---|
| A | `GtkMediaFile` streaming | `Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(url))` in `Gtk.Video` | Plan's Phase 1 premise |
| B | Download then play | libsoup download to a temp file, then `GtkMediaFile` | **Babble today** (`_download_stream`) |
| C | GstPlay + `gtk4paintablesink` | `GstPlay.Play` with a `gtk4paintablesink` (in `glsinkbin` when it has a GL context), shown in `Gtk.Picture` | Showtime (GNOME core, Python), Fractal |
| D | `playbin3` + `gtk4paintablesink` | Raw pipeline, same sink | — (baseline for C) |
| E | Clapper 0.10 | `ClapperGtk.Video` + `Clapper.Player` (own `GstClapperPaintable` sink, `clapperextractablesrc` over `souphttpsrc`) | Tuba |
| F | WebKitGTK 6 `<video>` | A `WebView` with one `<video>` element | — |
| G | libmpv 0.41 | `python-mpv` render API into a `Gtk.GLArea` (EGL `get_proc_address` via ctypes) | Celluloid |
| — | libVLC | Excluded without measurement: libVLC embeds only into X11/Win32/macOS windows; GTK 4 offers no way to embed a foreign Wayland surface ([VLC #16106](https://code.videolan.org/videolan/vlc/-/issues/16106), [KDE bug 397594](https://bugs.kde.org/show_bug.cgi?id=397594), [GNOME Discourse](https://discourse.gnome.org/t/windows-are-displayed-separately-when-embedding-video-stream-into-gtk4/22753)) | — |

## Setup

- Machine: i7-11800H, Intel UHD + RTX 3060 Mobile. Arch, GTK 4.24, GStreamer 1.28.8, libclapper 0.10.0, WebKitGTK 2.54.2, mpv 0.41.0, python-mpv 1.0.8 (scratch venv).
- Display: headless mutter, Wayland, 1280×1024 virtual monitor, default GSK renderer, separate `dbus-run-session`. Window 1280×720. Every player muted (audio still decoded).
- Decoders picked: GStreamer chose `nvh264dec` (NVIDIA hardware) for C/D/E; mpv `hwdec=auto-safe` chose `nvdec-copy`. A/B do not expose their pipeline.
- **Loopback**: a 30 s 1280×720 H.264 High + AAC MP4, 2.5 Mbit/s, faststart, 9.9 MB, from a Range-capable server capped at 1 MB/s per connection (shaped like Mastodon's output, at a realistic mobile-ish rate). 5 runs per candidate, one process each.
- **Live**: the attachment of `https://mastodon.me.uk/@minesNarf/117581333386761171`, fetched read-only: 480×848 H.264 High + AAC, 21.4 s, 3.6 MB, nginx, `Accept-Ranges: bytes`, `206` on range, `moov` before `mdat`. 3 runs per candidate (kept low to spare a small instance), from Brazil.
- **Cycles**: 12× create → play the 5 s GIFV-like clip → first frame → 1 s → tear down → `gc.collect()`, in one process.
- **Sandbox**: `flatpak run --command=python3 org.gnome.Platform//51` with `--share=network --socket=wayland --device=dri`, live URL (`sandbox_probe.py`).
- Each candidate is torn down the way its own API intends: A/B Babble's `release_media` order; C `play.stop()`; D `set_state(NULL)`; E `player.stop()`; F remove + `try_close()`; G `terminate()`.

## Results

Every run is listed (no medians), per the project's measuring rule.

### Sandbox (GNOME 51 runtime, live URL)

| Check | Result |
|---|---|
| `Gio.File.new_for_uri(https)` | `GDummyFile`; `read()` → "Operation not supported" |
| A `GtkMediaFile` | **ERROR: Failed to pause** |
| C GstPlay + `gtk4paintablesink` | **frame OK** |

Clapper and libmpv were not run in the sandbox: neither is in the runtime.

### Time to first frame (ms)

| Candidate | Loopback, 1 MB/s (5 runs) | Live, mastodon.me.uk (3 runs) |
|---|---|---|
| A GtkMediaFile stream (host, via gvfs) | 784, 741, 738, 748, 760 | 15093, 18250, 23709 |
| B Download then play (today) | 9732, 9734, 9757, 9768, 9754 | 5514, 16849, 8900 |
| C GstPlay + paintablesink | 374, 379, 399, 373, 389 | 2046, 1745, 1809 |
| D playbin3 + paintablesink | 364, 376, 397, 381, 395 | 1674, 1732, 1631 |
| E Clapper | 381, 355, 364, 363, 355 | 1784, 1759, 1716 |
| F WebKitGTK | 1066, 444, 409, 425, 424 | 2311, 2552, 1825 |
| G libmpv | 527, 486, 491, 497, 540 | 2100, 2035, 2035 |

### Network behaviour (loopback, bytes)

| Candidate | Bytes at first frame | Total bytes (file is 9.9 MB) | HTTP requests | Fetched while paused 3 s |
|---|---|---|---|---|
| A | 2.18–2.21 MB | **19.0–19.6 MB** | **622–638** | 2.72–2.75 MB |
| B | 9.90 MB | 9.90 MB | 1 | 0 |
| C | 344–377 KB | 7.37–8.83 MB | 2 | 0.70–1.03 MB |
| D | 360–377 KB | 8.24–9.95 MB | 2 | 1.47–3.15 MB |
| E | 328–360 KB | 8.57–8.88 MB | 2 | 0.77–1.08 MB |
| F | 229 KB | 9.73–9.75 MB | 1 | 3.15–3.16 MB |
| G | 426–459 KB | 9.90 MB | 1 | 3.15–3.16 MB |

Every streaming player keeps downloading while paused. That is the expected read-ahead, but it means "metered" must stop or never start the stream, not merely pause it. C and E read ahead the least.

### Playback cost (loopback, 5 s of playback after first frame)

| Candidate | CPU s (all threads + children) | Frames in 5 s | RSS at play, MB | RSS after teardown, MB |
|---|---|---|---|---|
| A | 0.62, 0.64, 0.61, 0.73, 0.90 | 150, 150, 149, 150, 149 | 609.6–611.7 | 462.2–464.4 |
| B | 0.56, 0.61, 0.66, 0.79, 0.77 | 150, 150, 149, 150, 150 | 615.5–615.9 | 468.9–469.3 |
| C | 0.62, 0.64, 0.65, 0.89, 0.72 | 122, 123, 123, 123, 123 | 620.1–620.7 | 473.0–473.9 |
| D | 0.50, 0.49, 0.77, 0.88, 0.74 | 150 ×5 | 619.3–619.9 | 472.3–472.9 |
| E | 0.64, 0.76, 0.53, 0.74, 0.47 | 123, 122, 122, 122, 122 | 618.9–621.8 | 471.9–474.5 |
| F | 0.53, 0.51, 0.68, 0.64, 0.52 | 85, 86, 85, 85, 85 | 971.0–974.9 | **965.5–969.2** |
| G | 0.52, 0.55, 0.70, 0.56, 0.62 | 152, 152, 152, 152, 151 | 641.8–646.0 | 452.4–459.0 |

- RSS is the whole process (Python + GTK + GL + the player, plus WebKit's web processes). The process idles at ~305 MB before any video; compare candidates against each other, not against Babble's budget.
- C and E's ~123 frames are not drops: a follow-up read of the sink's own counters gave `rendered=123–124, dropped=0` for C against `150, 0` for D (`sink_rendered`/`sink_dropped`). GstPlay and Clapper pause to buffer after the first frame on a network source, which is correct. My bare D harness ignores `BUFFERING` messages, which a real app must not.
- F's frame count comes from `requestVideoFrameCallback` and is not comparable. On the live runs it counted 0 frames at 0.25 s CPU, so the video likely stalled after its first frame. **Inconclusive**; I did not debug it because WebKit is out on memory alone.

### Seek (loopback to 20 s; live to 15 s), ms until the player reports done

| Candidate | Loopback | Live |
|---|---|---|
| A | 234, 210, 226, 234, 223 | not reported (`notify::seeking` never seen) |
| B | 97, 95, 97, 99, 94 | 9, 9, 7 |
| C | 1123, —, 1119, 1122, — | 498, 410, 557 |
| D | 85, 84, 84, 91, 87 | 500, 437, 410 |
| E | 1115, 1120, 1124, 1120, 1121 | 455, 467, 460 |
| F | 56, 55, 53, 52, 53 | 4188, 2785, 2360 |
| G | 9, 11, 12, 10, 9 | 7, 7, 6 |

"—" = GstPlay's `seek-done` not emitted in that run. The ~1.1 s for C/E is an accurate seek fetching new ranges at 1 MB/s; D used `KEY_UNIT` (faster, lands on a keyframe). This compares seek modes as much as libraries. B and G seek within data they already hold.

### Teardown: 12 cycles in one process

| Candidate | Result | RSS after cycle 1 → after 12, MB | First frame per cycle, ms |
|---|---|---|---|
| A | **SIGSEGV** (this run); passed in the smoke run | — | — |
| B | **SIGSEGV** (this run); passed in the smoke run | — | — |
| C | 12/12 | 431.5 → 482.8 | 276, 225, 210, 225, 211, 226, 220, 228, 216, 215, 230, 206 |
| D | 12/12 | 430.6 → 531.1 | 281, 230, 223, 224, 271, 217, 254, 233, 221, 245, 216, 232 |
| E | 12/12 | 566.9 → 483.8 | 288, 210, 206, 188, 165, 193, 199, 203, 196, 186, 178, 215 |
| F | 12/12 | 943.3 → 982.7 | 408, 146, 143, 159, 142, 142, 162, 157, 151, 175, 177, 146 |
| G | 12/12 | 474.0 → **631.4** | 487, 156, 136, 366, 364, 368, 376, 140, 377, 143, 355, 153 |

Crash traces (`coredumpctl`, 13:58:51 and 14:01:02): `g_type_check_instance_is_fundamentally_a` ← `g_object_ref`/`g_object_unref` ← `libgstplay-1.0` signal-adapter path ← `gst_bus_async_signal_func`, on a non-main thread. The same function as coredumpctl 45798, which `release_media` was written against. C also uses GstPlay; the difference is that C stops the player explicitly before the widget goes, while A/B leave GTK's builtin media file to tear GstPlay down from its own disposal.

Smoke run cycle numbers (one more run each, `smoke.jsonl`): A 12/12, B 12/12, C 12/12, D 12/12, E 12/12 (its first-frame figures there are invalid, see Caveats), F 12/12, G 12/12. So A/B crashed in 2 of 4 twelve-cycle runs; C/D/E/F/G in 0 of 2.

## Integration cost for Babble

| | A/B GtkMediaFile | C GstPlay + sink | D playbin3 + sink | E Clapper | F WebKitGTK | G libmpv |
|---|---|---|---|---|---|---|
| In `org.gnome.Platform//51` | yes | **yes** (verified) | yes | **no**: bundle libclapper (meson, C) + optional Enhancers extension | yes (Babble already uses it for the web column) | **no**: bundle mpv + ffmpeg |
| Streams in sandbox | **no** | **yes** (verified) | yes (same sink and source) | expected (uses `souphttpsrc`); not verified | expected; not verified | expected; not verified |
| Python API | GTK | GI (`GstPlay`, `GstPbutils`) | GI (`Gst`) | GI (`Clapper`, `ClapperGtk`, 0.0 = unstable) | GI (`WebKit`) + JS | ctypes (`python-mpv`, PyPI) + GL calls |
| Harness lines for the candidate | ~45 | ~30 | ~45 (no buffering handling) | ~55 | ~45 | ~80 |
| Controls | `Gtk.Video` built in | build them, or wrap as a `Gtk.MediaStream` subclass so `Gtk.MediaControls` works | build them | `ClapperGtk.SimpleControls` | HTML controls | build them |
| Buffering / missing plugins | hidden | `GstPlay` `BUFFERING` state; `GstPbutils.is_missing_plugin_message` (Showtime) | own bus handling | handled; `missing-plugin` signal | browser | mpv |
| Save As without refetch | no (B: yes) | no | no | **yes** (`download-enabled` cache) | no | no |
| MPRIS / PeerTube | no | no | no | via Enhancers (MPRIS, yt-dlp) | PeerTube embed page | yt-dlp hook |
| Licence | LGPL | LGPL + MPL-2.0 (sink) | same | LGPL-2.1-or-later | LGPL/BSD | GPL-2.0-or-later / LGPL (build-dependent) |
| Fedora CI | packages already planned | + `gstreamer1-plugin-gtk4` | same | + Clapper packages | already installed | + mpv-libs, python-mpv |
| Teardown risk seen | **SIGSEGV 2/4** | 0/2 | 0/2 | 0/2 | 0/2, RSS not returned | 0/2, RSS +157 MB |

## What this changes in the plan

- **Phase 0 `new_for_uri` spike: done, failed.** `GtkMediaFile` + `Gio.File.new_for_uri` does not stream in the GNOME 51 sandbox. No further spike needed.
- **Phase 1 changes engine.** A small `mediaplayer.py` on GstPlay + `gtk4paintablesink` (`glsinkbin` when the paintable has a GL context), modelled on Showtime's `play.py`:
  - explicit `stop()` before the widget goes;
  - `PlaySignalAdapter` signals connected with `pages.connect_weak`;
  - `GstPbutils` missing-plugin messages for the codec error (`codecs-extra` wording);
  - honour `BUFFERING`.
  - Controls: either a thin `Gtk.MediaStream` subclass forwarding to the sink's paintable so `Gtk.Video`/`Gtk.MediaControls` keep working, or own controls as Showtime does. Decide in Phase 1, measured.
- **Current viewer teardown:** treat the `GtkMediaFile` crash as a live bug. Moving the viewer to C removes `GtkMediaFile` from the path; until then, extend `test-mediaviewer-teardown.py` to rapid cycles, which this benchmark shows can crash.
- **Metered:** every streaming engine keeps reading while paused, so the metered gate must prevent starting, and leaving the viewer must `stop()`.
- **CI:** the Fedora job additionally needs `gstreamer1-plugin-gtk4`; the "Check the media stack" step adds `gtk4paintablesink`. The plan's line "`gstreamer1-plugin-gtk4` (Phase 4 only)" no longer holds.
- **Clapper:** a later, separate decision if Save As without refetch, MPRIS or PeerTube via yt-dlp are wanted. It needs a bundling and CI cost estimate and a teardown test of its own.

## Caveats

- One machine, one GPU path (NVIDIA hardware decode); Intel VA-API and software decode not measured. Headless mutter, not a real session.
- RSS includes everything in the process; absolute numbers are not Babble's.
- A's host result relies on gvfs; it says nothing for the Flatpak, where A fails.
- Clapper's smoke-run first-frame figures (123 ms; 22–62 ms in cycles) are invalid: the hook latched onto its buffering-spinner paintable. The full run hooks `GstClapperPaintable` only.
- WebKit live frame counting failed (see above). WebKit, Clapper and libmpv were not run inside the sandbox.
- The live runs depend on a Brazil→UK path; spread between runs is network, not player.
- `python-mpv` was installed from PyPI into a throwaway venv in the session scratchpad; nothing was installed system-wide.

## Sources

- Showtime source (installed): `/usr/lib/python3.14/site-packages/showtime/play.py`, `widgets/window.py` (GstPlay + `gtk4paintablesink` + `glsinkbin`, `GstPbutils` missing-plugin handling, idle inhibit)
- Fractal `video_player_renderer.rs`: https://fractal-tobias-kuendig-a2308b584e67215e00d29986814e3bc2ee19fca1.pages.gitlab.gnome.org/src/fractal/components/media/video_player_renderer.rs.html
- GNOME 49 replaces Totem with Showtime: https://www.omgubuntu.co.uk/2025/05/gnome-49-replaces-totem-video-player-with-showtime/amp
- GStreamer `gtk4paintablesink` docs: https://gstreamer.freedesktop.org/documentation/gtk4/
- Flathub discussion on bundling gst-plugins-rs (now unnecessary on GNOME 51, verified): https://discourse.flathub.org/t/bundling-gst-plugin-rs-libraries-in-flatpak/7474
- Clapper 0.10 release: https://linuxiac.com/clapper-0-10-media-player-released-with-enhancer-plugin-system-improvements/ ; Flathub: https://flathub.org/de/apps/com.github.rafostar.Clapper
- GTK graphics offload and the GStreamer sink: https://blog.gtk.org/2024/04/ ; https://gitlab.gnome.org/GNOME/gtk/-/merge_requests/6618
- libmpv render API / Celluloid: https://upd.dev/mpv-player/mpv/raw/branch/testing_queue/libmpv/render_gl.h ; https://celluloid-player.github.io
- WebKitGTK HLS disabled by default: https://www.webkitgtk.org/2023/02/02/webkitgtk2.38.4-released.html
- libVLC Wayland embedding: https://code.videolan.org/videolan/vlc/-/issues/16106 ; https://bugs.kde.org/show_bug.cgi?id=397594
- Fedora `gstreamer1-plugin-gtk4`: https://packages.fedoraproject.org/pkgs/rust-gst-plugin-gtk4/gstreamer1-plugin-gtk4/
