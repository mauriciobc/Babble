# Playback Technology Options for Babble (GNOME GTK4/libadwaita Python, Flatpak, Wayland)

## GtkVideo / GtkMediaFile / GStreamer via PyGObject — maturity, HLS/DASH/MP4/WebM, hardware acceleration, Flatpak codecs?

### Takeaway
`GtkVideo` + `GtkMediaFile` are the simplest GTK4 path (GStreamer-backed under the hood) but are designed for previews, not full players; a custom GStreamer pipeline via `gtk4paintablesink`/`GdkPaintable` (or ClapperGtk) gives adaptive streaming, track selection, and hardware offload control needed for a full Mastodon client.

### Cited Findings
- `GtkVideo` "aims to cover use cases such as previews, embedded animations, etc. It supports autoplay, looping, and simple media controls. It does not have support for video overlays, multichannel audio, device selection, or input. If you are writing a full-fledged video player, you may want to use the `GdkPaintable` API and a media framework such as Gstreamer directly." — [GtkVideo doc](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkVideo` constructors include `new_for_file`, `new_for_filename`, `new_for_media_stream`, `new_for_resource`; properties include `autoplay`, `file`, `loop`, `media-stream`, and `graphics-offload` (since 4.14) — [GtkVideo doc (methods/properties)](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkMediaFile` "provides a simple way to play back video files with GTK. GTK provides a GIO extension point for `GtkMediaFile` implementations to allow for external implementations using various media frameworks. GTK itself includes an implementation using GStreamer." — [GtkMediaFile doc](https://docs.gtk.org/gtk4/class.MediaFile.html)
- `GtkMediaStream` properties surfaced via `GtkMediaFile` include `duration`, `ended`, `error`, `has-audio`, `has-video`, `loop`, `muted`, `playing`, `prepared`, `seekable`, `seeking`, `timestamp`, `volume` — evidence of API surface limited to single-stream playback state (no track selection, captions, or adaptive-variant control in that interface) — [GtkMediaFile doc (inherited GtkMediaStream properties)](https://docs.gtk.org/gtk4/class.MediaFile.html)
- GTK 4 provides `gtk::Video` & `gtk::Picture` for rendering; "the default `gtk::Video` widget doesn't offer the possibility to use a custom `gst::Pipeline`. The plugin provides a `gst_video::VideoSink` along with a `gdk::Paintable` that's capable of rendering the sink's frames." — [gtk4paintablesink (GStreamer docs)](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
- The sink "can generate GL Textures if the system is capable of it, but it needs to be compiled with either `wayland`, `x11glx` or `x11egl` cargo features ... Additionally, the sink can render DMABufs directly on Linux if GTK 4.14 or newer is used. For this the `dmabuf` feature needs to be enabled." — [gtk4paintablesink](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
- Pad caps of `gtk4paintablesink` include `video/x-raw(memory:DMABuf)` with `DMA_DRM`, `video/x-raw(memory:GLMemory)`, and system-memory raw formats — [gtk4paintablesink pad templates](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
- Community note: "The built-in video for Gtk4 is too limited, so I'm using gstreamer. create a glsinkbin, set its sink property to the GTK4 sink" — [GStreamer discourse](https://discourse.gstreamer.org/t/best-practice-for-pipeline-to-display-video-in-gtk4-app/2372); report as practitioner evidence, not primary docs.
- GStreamer adaptive streaming: there are "two sets of elements implementing client-side adaptive streaming (HLS, DASH, Microsoft Smooth Streaming)": legacy `dashdemux`/`hlsdemux`/`mssdemux` in gst-plugins-bad, and new `dashdemux2`/`hlsdemux2`/`mssdemux2` in gst-plugins-good added in GStreamer 1.22 — [Adaptive Demuxers design doc](https://gstreamer.freedesktop.org/documentation/additional/design/adaptive-demuxer.html)
- New adaptive demuxers "only work in combination with the 'streams-aware' `playbin3` and `uridecodebin3` elements that support advanced stream selection functionality, they won't work with the legacy `playbin` element" — [Adaptive Demuxers design doc](https://gstreamer.freedesktop.org/documentation/additional/design/adaptive-demuxer.html)
- GStreamer 1.26 release notes mention `playbin3: HLS/DASH stream selection handling improvements to fix disabling and re-enabling of audio/video streams with adaptivedemux2` — [GStreamer 1.26 release notes](https://gstreamer.freedesktop.org/releases/1.26/)
- GStreamer broad coverage lists containers "MP4/MOV (including fragmented MP4), Matroska/WebM, MPEG-PS/TS, MXF and Ogg" and streaming "WebRTC ..., HLS, DASH, SRT, RIST, RTMP and HTTP" and codecs "H.264, H.265, H.266, VP8, VP9, AV1, ... Opus, AAC, FLAC and many more, through hardware accelerated decoders and encoders, dedicated codec libraries and FFmpeg" — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- `GstDiscoverer` is a GStreamer pbutils class for probing media (duration, streams, bitrates) with Python bindings available (e.g., `GstPbutils.DiscovererAudioInfo.get_bitrate`, `get_max_bitrate`, `DiscovererVideoInfo.get_bitrate`, etc.) — [GstDiscoverer docs (Python examples)](https://gstreamer.freedesktop.org/documentation/pbutils/gstdiscoverer.html)
- PyGObject usage is `gi.require_version("Gst", "1.0"); from gi.repository import Gst; Gst.init()` — [PyGObject Gst API page](https://api.pygobject.gnome.org/Gst-1.0/index.html); GStreamer Python element-writing was noted as historically broken for custom elements ("writing meaningful elements using GStreamer through pygobject was badly broken since 2014") — [Python elements tutorial](https://mathieuduponchelle.github.io/2018-02-01-Python-Elements.html) — scope is custom element authoring, not pipeline construction with existing elements.
- Hardware acceleration on Linux per GStreamer: "VA-API, V4L2 (including stateless codecs), NVIDIA NVCODEC, Intel QSV, Vulkan, DMA-BUF, KMS/DRM and Wayland" — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- On Wayland/VA-API: community report "GStreamer-vaapi already works for Intel GPU on Wayland. GNOME Web/Epiphany supports GStreamer and video playback can be accelerated on Wayland" — [Arch forum report](https://bbs.archlinux.org/viewtopic.php?id=244031&p=12); treat as user report, not official doc.
- Vulkan video in GStreamer: plugin list includes `vulkanav1dec` ("An AV1 video decoder based on Vulkan"), `vulkancolorconvert`, plus supporting elements — [Vulkan index (GStreamer docs)](https://gstreamer.freedesktop.org/documentation/vulkan/index.html); Vulkan Video extensions finalized by Khronos for H.264/H.265 decode ("Khronos is releasing finalized extensions that incorporate industry feedback and expose core and decode Vulkan Video functionality") — [Khronos blog](https://www.khronos.org/blog/khronos-finalizes-vulkan-video-extensions-for-accelerated-h.264-and-h.265-decode)
- Flatpak extensions: `org.freedesktop.Platform.ffmpeg-full` provides ffmpeg with patented-codec support and **must be explicitly added via `add-extensions` in the app manifest** (version must match base runtime branch, e.g. `24.08`); `org.freedesktop.Platform.codecs-extra` is the successor since Freedesktop SDK 25.08 for `25.08-extra, 26.08-extra` branches, **automatically installed by the runtime**, maskable via `flatpak mask` — [Flatpak Extensions docs](https://docs.flatpak.org/en/latest/extension.html)
- `org.freedesktop.Platform.openh264` extension "automatically installed by the runtime. (This is discontinued since Freedesktop SDK 25.08)" — [Flatpak Extensions docs](https://docs.flatpak.org/en/latest/extension.html)
- Runtime extensions also include `org.freedesktop.Platform.GL.default` (Mesa + `-extra` branch "containing support for patented codecs"), `org.freedesktop.Platform.VAAPI.Intel`, `org.freedesktop.Platform.VAAPI.nvidia`, and `org.freedesktop.Platform.GStreamer` plugin extension point — [Flatpak Extensions docs](https://docs.flatpak.org/en/latest/extension.html)
- Clapper Flatpak note: "The `Flatpak` package includes all required dependencies and codecs. Additionally it also has a few patches" — [Clapper GitHub](https://github.com/Rafostar/clapper) — indicates bundling codecs in Flatpak vs relying on runtime alone is the practice for full codec coverage.
- GNOME build-meta issue notes the 25.08 rename: "Freedesktop SDK 25.08 will rename ffmpeg-full to codecs-extra ... Anyone not wanting to deal with patented codecs can ship or use `flatpak masks` to block installation of the extension" — [gnome-build-meta issue 931](https://gitlab.gnome.org/GNOME/gnome-build-meta/-/issues/931)

### Inferences
- For Babble (progressive MP4/WebM most likely, see Q3), `GtkVideo`/`GtkMediaFile` suffices for timeline previews and a minimal detail player, but adaptive streaming (if ever needed for non-Mastodon sources), multi-track audio/subtitle selection, overlays, or custom pipeline tuning require direct GStreamer + `gtk4paintablesink` → `GtkPicture` (or `GtkVideo` fed a custom `GtkMediaStream`).
- Practical pipeline choice: `playbin3`/`uridecodebin3` (not legacy `playbin`) if adaptive HLS/DASH or stream selection is required, because `adaptivedemux2` requires streams-aware elements.
- Flatpak on GNOME 51 (Freedesktop 25.08 base): H.264 via `codecs-extra` (-extra branch) is effectively automatic but maskable; H.264 fallback via openh264 is gone post-25.08. WebM/VP9/Opus (`libvpx`, opus plugins in GNOME runtime) is patent-free and should not depend on proprietary codec extensions. Declare `org.freedesktop.Platform.ffmpeg-full`/`codecs-extra` explicitly if vendoring older base runtimes, and verify `add-extensions` branch matches `org.gnome.Platform//51` base.
- Hardware path: prefer `gtk4paintablesink` with DMABuf (GTK 4.14+; Babble targets 4.24) or GLMemory → `GdkPaintable`; VA-API decoders on Wayland are supported by GStreamer but not guaranteed by GTK's own `GtkMediaFile` backend choice — the custom sink path lets Babble pick VA-API/Vulkan elements.

### Gaps
- No verified benchmark or first-party doc stating `GtkMediaFile`'s exact codec set / HLS support on GNOME 51 Flatpak; `GtkMediaFile` backend delegates to whatever GStreamer elements are in the runtime, but I found no authoritative list. Would require runtime testing with `gst-inspect-1.0` / playing sample files.
- No reliable statement on `GtkVideo` performance when many instances are realized simultaneously (timeline grids); inferred risk only.

## Alternatives: gtk-vlc, mpv embedding, Rust/C helper, external player launch, WebKit? Tradeoffs for a GNOME app.

### Takeaway
For a GTK4/libadwaita Python GNOME app, GStreamer-native (per above) or Clapper's LGPL GObject libraries are the only headless, Wayland-native, PyGObject-friendly options; VLC/mpv embedding in GTK4 is poorly supported and external-player launch or WebKit are fallback degradations, each sacrificing in-app controls, a11y, and Flatpak coherence.

### Cited Findings
- Clapper: "Powered by GStreamer and built for the GNOME desktop environment using GTK4 toolkit" — [Clapper GitHub](https://github.com/Rafostar/clapper)
- Clapper libraries: "Both libraries support *GObject Introspection* bindings. Check `examples` directory in this repo for usage" and are "licensed under `LGPL-2.1-or-later`. You are free to use them in your own projects... until version 1.0 they should be considered as an unstable API" — [Clapper GitHub](https://github.com/Rafostar/clapper)
- Clapper secondary description: "A GNOME media player built using GJS with GTK4 toolkit and powered by GStreamer with OpenGL rendering" — [FreshPorts](https://www.freshports.org/multimedia/clapper) — corroborates OpenGL rendering via GTK4.
- Celluloid (formerly GNOME MPV): "a simple GTK+ frontend for mpv ... Celluloid interacts with mpv via the client API exported by libmpv, allowing access to mpv's powerful playback capabilities" — [Celluloid Codeberg](https://codeberg.org/celluloid-player/celluloid)
- Celluloid is a **GTK3** frontend ("simple GTK+ frontend for mpv"), not GTK4 — [Celluloid site](https://celluloid-player.github.io/) — a port or embed would not be drop-in for a GTK4.24 app.
- GNOME Discourse on embedding foreign windows in GTK4: "How to embed foreign surface/window in gtk4 window?" discussion thread — [GNOME Discourse 10018](https://discourse.gnome.org/t/how-to-embed-foreign-surface-window-in-gtk4-window/10018) — evidences that XEmbed-style embedding is contested in GTK4 (cannot cite outcome without fetching full discussion; fetched snippet only shows the question).
- python-vlc embedding complaint: "The PlayerWidget will not be shown embedded anymore, but instead a new (second) window will be opened where the video is played" — [StackOverflow 56045346](https://stackoverflow.com/questions/56045346/python-vlc-will-not-embed-gtk-widget-into-window-but-open-a-new-window-instead) — symptom under GTK3-era; no verified GTK4 fix found.
- Python-mpv + GTK4 gist titled "python-mpv + GTK 4" — [GitHub gist](https://gist.github.com/trin94/3381395adc8b2c3fea81a38b9a385369) — existence of community recipe only, not production evidence.
- GStreamer `playbin3` purpose: "simplifies ... stream selection between different video/audio/subtitles streams" and publishes streams via `GstStreamCollection` — [playbin3 docs](https://gstreamer.freedesktop.org/documentation/playback/playbin3.html)
- `decodebin3`/`uridecodebin3`/`playbin3` gapless architecture: "decodebin3 internally uses parsebin ... uridecodebin3 wraps urisourcebins and decodebin3 ... playbin3 combines uridecodebin3 and playsink" — [Gapless design doc](https://gstreamer.freedesktop.org/documentation/additional/design/playback-gapless.html)
- GStreamer is LGPL ("GStreamer is released under the LGPL") and "API and ABI stable since 2012" for 1.x — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)

### Inferences
- Trade-off matrix (inferred from above, not a cited benchmark):
  - **Best fit (Babble):** (a) direct GStreamer via PyGObject (full control, Python-native, no extra deps), or (b) ClapperGtk widget library via GI if available in runtime (save effort, LGPL, but pre-1.0 unstable API and requires adding dependency not in `org.gnome.Platform` — vendoring/build cost).
  - **Avoid embedding:** libVLC (Qt-ish, XEmbed issues in GTK4, creates unmanaged window), libmpv (better embedding via libmpv but heavy, bundles FFmpeg, own render/AO loops, Flatpak size, duplicate codec story, not GTK4-native).
  - **Rust/C GStreamer helper:** useful only if PyGObject becomes a bottleneck (e.g., custom element, complex pipeline hot path); GStreamer's own Rust plugin story exists (`gst-plugins-rs`, "Plugins can be written in C, in Rust and in Python") — [GStreamer Features page](https://gstreamer.freedesktop.org/features/) — but for Babble's scope plain `playbin3` + sink from Python is sufficient and less FFI risk.
  - **External player launch (`gio open` / xdg-open / flatpak-spawn to Totem/Clapper/mpv):** zero codec burden, worst UX (context switch), no timeline inline playback, no autoplay pause policy.
  - **WebKit (WebKitGTK):** viable for HTML5 MP4/WebM (browser codecs, no GStreamer pipeline control), but adds web runtime weight, weaker MPRIS/power control, and non-GTK controls.

### Gaps
- No source confirming current `gtk-vlc` or `python-vlc` GTK4 embedding status as of 2026; StackOverflow evidence is GTK3-era. Needs hands-on test.
- No verified statement that ClapperGtk is packageable/reused by third-party Python apps in production; LGPL + GI bindings are confirmed, stability is not.
- Did not fetch GNOME Discourse thread content; no primary statement on GTK4-in-GTK4 embedding feasibility.

## Adaptive streaming, progressive download, caching, offline, subtitles, audio tracks, looping/muted autoplay — how GNOME apps handle this?

### Takeaway
Mastodon attachments are progressive MP4/WebM (H.264), not HLS/DASH; GStreamer `uridecodebin3`/`playbin3` + `httpsrc` handles progressive + HTTP Range automatically, while adaptive support comes free via `adaptivedemux2` only with `playbin3`; subtitles/audio-track switching must go through the streams-aware path (`select-streams` events), which `GtkVideo`/`GtkMediaStream` do not expose.

### Cited Findings
- Mastodon (official docs): "**Videos** (MP4, M4V, MOV, WebM) **up to 99MB**. Video will be transcoded to H.264 MP4 with a maximum bitrate of 1300kbps and framerate of 120fps. Only one video can be attached, per post." — [Mastodon docs (posting)](https://docs.joinmastodon.org/user/posting/)
- Animated GIFs: "converted to soundless MP4s. ... You can also upload soundless MP4 and WebM files, which will be looped in clients the same way as an animated GIF. Only one animated GIF can be attached, per post." — [Mastodon docs (posting)](https://docs.joinmastodon.org/user/posting/)
- Adaptive demuxers are the current GStreamer client-side adaptive implementation for HLS/DASH/Smooth Streaming; new `*demux2` in gst-plugins-good from 1.22, legacy `*demux` in gst-plugins-bad — [Adaptive design doc](https://gstreamer.freedesktop.org/documentation/additional/design/adaptive-demuxer.html)
- New demuxer internals: buffering handled inside adaptive element based on elementary streams; `libsoup` used directly for downloads (not via pipeline sources); scheduling/output/download are 3 dedicated threads — [Adaptive design doc (components/download helper)](https://gstreamer.freedesktop.org/documentation/additional/design/adaptive-demuxer.html)
- `playbin3`/`decodebin3` support stream publication/selection via `GstStreamCollection` and `GST_EVENT_SELECT_STREAMS`; legacy `playbin` lacks this — [Adaptive design doc (new design section)](https://gstreamer.freedesktop.org/documentation/additional/design/adaptive-demuxer.html); [playbin3 doc](https://gstreamer.freedesktop.org/documentation/playback/playbin3.html)
- `uridecodebin3` "Decodes data from a URI into raw media. It selects a source element that can handle the given uri scheme and connects it to a decodebin3." — [uridecodebin3 doc](https://gstreamer.freedesktop.org/documentation/playback/uridecodebin3.html)
- `GtkVideo` exposes only `autoplay` and `loop` booleans for playback automation; "It does not have support for video overlays, multichannel audio, device selection, or input." — [GtkVideo doc](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkMediaStream` states `muted`, `volume`, `loop`, `playing`, `prepared`, `seeking` — muted autoplay is representable, but there is no API for audio-track/subtitle-track selection on that interface — [GtkMediaFile doc (GtkMediaStream properties)](https://docs.gtk.org/gtk4/class.MediaFile.html)
- GStreamer container/codec support includes "MP4/MOV (including fragmented MP4), Matroska/WebM" and streaming "HLS, DASH, ... RTMP and HTTP" — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- GStreamer debugging/probing tools include `gst-discoverer-1.0` and `gst-device-monitor-1.0` — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- Totem: "Through use of the GStreamer backend many popular file and streaming formats are supported" — [totem man page (die.net)](https://linux.die.net/man/1/totem) / [GNOME Videos wiki](https://en.wikipedia.org/wiki/GNOME_Videos); Totem supports playlist, fullscreen, seek/volume — no primary source found detailing adaptive variant handling in Totem.
- Shortwave referenced by name in assignment only; no authoritative source retrieved in this pass (see Gap).

### Inferences
- For Mastodon specifically, **progressive download of H.264 MP4** (~≤99 MB) is the workload; `souphttpsrc`/`playbin3` with a small buffering window + `typefind` suffices. No HLS/DASH manifests are served by Mastodon for attachments (docs describe MP4 transcoding only), so adaptive support is "free insurance" via `playbin3`, not a required feature.
- Caching/offline: GStreamer `cache` elements and application-level HTTP range caching are orthogonal to `GtkMediaFile`; because attachments are immutable CDN URLs, Babble should cache by URL at the GIO/Soup layer rather than relying on GStreamer's transient download cache.
- Subtitles/audio tracks: when detail view needs track selection (rare for Mastodon JPEG→MP4 single-track clips), hop to `playbin3` + `select-streams`; `GtkMediaFile` alone cannot select tracks.
- Looping/muted autoplay in timelines: directly supported (`autoplay`/`loop`/`muted`), but resource policy (pause offscreen) is application responsibility (see Q4).

### Gaps
- No primary source documenting Totem/Celluloid/Shortwave adaptive-streaming UI or offline cache strategy; only general format-support claims for Totem. Celluloid/mpvs's adaptive behavior via FFmpeg is implied (mpv+FFmpeg) but not verified in this pass.
- No Mastodon API spec stating `MediaAttachment` URL expiry/caching headers for video; assumed stable CDN URL + Range-capable. Needs a quick `curl -I` check against a real remote URL.
- Subtitle delivery in Mastodon: docs do not mention sidecar subtitles; assumed not applicable.

## Accessibility, keyboard, a11y, power saving, memory, CPU, background audio, lockscreen / MPRIS?

### Takeaway
GTK4/GTK `GtkVideo`/`GtkMediaControls` inherit GTK4 a11y and keyboard infra for free, while MPRIS/lockscreen for a timeline client is overkill; the main engineering work is autoplay policy (muted, loop, pause offscreen), avoiding audio focus, and idle power management rather than shipping a full MPRIS server.

### Cited Findings
- `GtkVideo` implements `GtkAccessible`, `GtkBuildable`, `GtkConstraintTarget` — [GtkVideo doc (hierarchy)](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkVideo` controls: "The controls are available separately as `GtkMediaControls`. If you just want to display a video without controls, you can treat it like any other paintable and for example put it into a `GtkPicture`." — [GtkVideo doc](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkMediaControls` "Shows controls for video playback. Creates a new GtkMediaControls managing the stream passed to it." — [GtkMediaControls (PGI docs mirror)](https://lazka.github.io/pgi-docs/Gtk-4.0/classes/MediaControls.html)
- `GtkVideo` exposes `autoplay`, `loop`, `graphics-offload` (4.14+), plus inherited `GtkAccessible` methods (`gtk_accessible_announce` since 4.14, accessible id/parent/role) — [GtkVideo doc (methods/accessible)](https://docs.gtk.org/gtk4/class.Video.html)
- GStreamer's core provides "Clocking to ensure global inter-stream synchronization (a/v sync) and support for live pipelines with latency management" and "Quality of service (QoS) to ensure the best possible quality under high CPU load" — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- Buffers can wrap DMA-BUF/GPU memory "so video frames can stay on the hardware between elements" — [GStreamer Features page](https://gstreamer.freedesktop.org/features/)
- Celluloid MPRIS: site claims "MPRIS2 support — Celluloid implements the MPRIS D-Bus Interface." — [Celluloid site](https://celluloid-player.github.io/); but an open GitHub issue reports "we can not control Gnome MPV through the Media Player applet in systray. Media Player applet does not detect Gnome MPV." — [Celluloid issue #452](https://github.com/celluloid-player/celluloid/issues/452) — evidence MPRIS is bug-prone even in a dedicated player.
- Totem MPRIS as reported third-party: "Totem does provide an MPRIS interface, but it is very rudimentary: it allows you to play/pause, but doesn't provide any metadata like title" — [KDE bugzilla report on Totem](https://bugs.kde.org/show_bug.cgi?id=456516) — not a primary claim; cite cautiously.
- WCAG guidance for autoplay: autoplaying content "can interfere with assistive technologies, distract users, and reduce overall usability" and should provide accessible controls — [Section508 blog](https://www.section508.gov/blog/avoid-auto-playing-content/); analogous expectation for in-timeline autoplay (muted + pause control) — application guidance, not GTK API.
- GNOME/GTK autoplaying-video a11y patterns (HTML example, analogous): `autoplay playsinline loop muted` plus a visible play/pause toggle with `aria-pressed` / keyboard (Space/Enter) — [Thoughtbot a11y guide (HTML/JS example)](https://thoughtbot.com/blog/can-auto-playing-videos-be-accessible) — web pattern, cite as design inspiration not GNOME verbatim.
- Clapper as reusable lib: "Both libraries support *GObject Introspection* bindings" (LGPL-2.1-or-later, unstable until 1.0) — [Clapper GitHub](https://github.com/Rafostar/clapper)

### Inferences
- Timeline videos: ship **muted + loop + autoplay-when-visible + pause-when-offscreen** (matches `GtkMediaStream:{muted,loop,playing}` semantics). Provide a visible, keyboard-focusable play/pause button per playing item; honor `prefers-reduced-motion`-equivalent settings if GNOME exposes them (not verified — see Gap).
- Detail view: use `GtkMediaControls` (or ClapperGtk widget) for full control; confirm focus chain passes through controls (GTK gives this via `GtkAccessible` + `GtkMediaControls`, but verify with keyboard test).
- Memory/CPU/power: one **active** pipeline per viewport (others show poster thumbnail, not a realized `GtkVideo`); call `clear()`/`set-state(null)` + `set-file(None)` on recycle, and on window minimize/sleep. Prefer DMABuf hardware path; keep `gtk4paintablesink` default `reconfigure-on-window-resize = overlay-only`.
- Background audio / lockscreen MPRIS: **out of scope for MVP**. MPRIS even in Celluloid has open detection bugs, and a timeline client should not keep audio in background; if detail-view audio must survive lock, that requires an explicit MPRIS server decision with Flatpak `org.mpris.MediaPlayer2.*` (portal) consequences — flag for product decision.
- Audio focus: mute timeline playback by default so it never grabs audio focus; unmute only on explicit user action in detail view.

### Gaps
- No source confirming a GNOME-wide `prefers-reduced-motion` gsetting mapping to GTK video autoplay in 2026; not verified — treat reduced-motion handling as unknown for Babble (could check `org.gnome.desktop.interface enable-animations` indirectly, but not sourced here).
- No measured power/CPU data for `GtkVideo` vs `gtk4paintablesink` vs ClapperGtk in Python on Wayland; all recommendations are architectural, not benchmarked.
- MPRIS D-Bus ownership under Flatpak (portal requirement) not researched beyond Celluloid/Totem anecdotes.

## Minimal viable player widget design for list/grid timelines: thumbnails, play overlay, inline playback, pause offscreen, carousel for multiple attachments.

### Takeaway
A two-tier widget — static thumbnail + play affordance for the timeline, lazy-instantiated `GtkVideo`/`GtkPicture` pipeline only for the on-screen item, and a pager in detail view for multi-attachment posts — matches both GTK4 constraints and Mastodon's one-video-per-post rule.

### Cited Findings
- Mastodon: "Only one video can be attached, per post." — [Mastodon docs (posting)](https://docs.joinmastodon.org/user/posting/)
- Up to 4 images per post; 1 GIF/video/audio per post — same source defines carousel need for images primarily, not for video — [Mastodon docs (posting)](https://docs.joinmastodon.org/user/posting/)
- `GtkVideo` supports `autoplay` (start when visible or when a new file is loaded) and `loop` — [GtkVideo methods `set_autoplay`/`set_loop`](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkVideo` can be used without controls as a paintable in a `GtkPicture`: "If you just want to display a video without controls, you can treat it like any other paintable and for example put it into a `GtkPicture`." — [GtkVideo doc](https://docs.gtk.org/gtk4/class.Video.html)
- `GtkMediaFile` is a `GdkPaintable` (implements `GdkPaintable`) with `get_intrinsic_width/height`/`get_intrinsic_aspect_ratio` and `invalidate-contents`/`invalidate-size` — basis for aspect-ratio-correct thumbnails and `GtkPicture` reuse — [GtkMediaFile doc](https://docs.gtk.org/gtk4/class.MediaFile.html)
- `GtkVideo` `graphics-offload` property (since 4.14) — direct GPU compositing hint for timeline thumbnail players — [GtkVideo doc](https://docs.gtk.org/gtk4/class.Video.html)
- `gtk4paintablesink` `reconfigure-on-window-resize` default is `overlay-only` (2) — sizing upstream reconfigure policy for embedded sinks — [gtk4paintablesink properties](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
- Clapper pattern: "Clapper uses a playback queue where you can add multiple media files. Think of it like a playlist that you can build. You can easily reorder items or remove them from the queue with a simple drag and drop operation." — [Clapper GitHub](https://github.com/Rafostar/clapper) — multi-item handling pattern for detail view (queue vs carousel), not directly a timeline pattern.

### Inferences
Proposed widget spec (design, not sourced API):
- **Timeline cell (unfocused):** `GtkPicture` from `MediaAttachment.preview_url` (poster thumbnail) + centered play-button overlay (`GtkButton` with `dialog-play` icon, accessible label = post author + "Video"), plus duration badge (`GstDiscoverer`-derived or attachment meta) and sensitive/CW blur gate (Mastodon blurs sensitive media by default). Zero realized pipelines.
- **Timeline cell (visible/focused):** on `map`/`idle` when ≥50% visible and no other pipeline active in that viewport, instantiate one `GtkVideo(reuse=False)` or `GtkMediaFile` → `GtkPicture` with `loop=True`, `muted=True`; start playback; on `unmap`, visibility < threshold, or another item takes focus: `pause` + `set_file(None)`/`clear()` + return to poster. `autoplay` property provides the when-visible trigger, but Babble should gate it with its own IntersectionObserver-equivalent (GTK4 has no built-in IntersectionObserver; use `GtkWidget:map`/layout bounds + scroll container `visible` signals).
- **Detail view:** full `GtkVideo` + `GtkMediaControls` (or ClapperGtk `ClapperGtkVideo` if vendored), loop off by default, unmuted on user gesture, keyboard shortcuts (space/K, arrows, F/M), fullscreen toggle, and image carousel for multi-attachment posts via `GtkStack`/`AdwCarousel` (Mastodon allows up to 4 images; 1 video). Video does not need a carousel since only one video per post.
- **Thumbnail strategy:** reuse `preview_url` from the API (server-generated poster) instead of extracting frames client-side; avoids `gst-discoverer` rasterization cost per timeline item. Reserve `GstVideoFrame`-extracted custom thumbs for posts where `preview_url` is missing.
- **Resource cap:** max 1–2 concurrent decode pipelines per window; evict LRU; never start audio in timeline (muted always).

### Gaps
- No sourced widget-research on carousel precedents in GNOME social clients (Toot/Fractal/others not fetched); Babble should survey `AdwCarousel` a11y behavior separately.
- No data on `GtkVideo` instantiation cost in Python/PyGObject; the "1 active pipeline" cap is engineering judgment, not a measured threshold.
- Mastodon `MediaAttachment` entity field list (`url`, `preview_url`, `description`, `blurhash`) not fetched — confirm thumbnail/detail URL fields from [MediaAttachment entity](https://docs.joinmastodon.org/entities/MediaAttachment/) before implementing.
