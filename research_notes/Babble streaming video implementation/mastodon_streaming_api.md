# Mastodon/Fediverse media and streaming APIs relevant to streaming video in Babble

## Mastodon REST API: statuses, media attachments, formats, limits, and async transcoding

### Takeaway
Mastodon serves video as progressive MP4 (H.264) files via plain HTTP URLs (`url`), with a thumbnail (`preview_url`) and rich `meta` (dimensions, duration, fps, bitrate); there is no HLS/multi-bitrate endpoint — "streaming" here means progressive download with HTTP range seeking.

### Cited Findings
- `MediaAttachment` types are `unknown | image | gifv | video | audio`; `gifv` = looping, soundless animation, `video` = video clip — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- `url` = "location of the original full-size attachment. Url may be null if the file is still being processed"; `preview_url` = scaled-down preview; `remote_url` = full-size original on remote site, null if local — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- `meta` is a Paperclip metadata hash: may contain `small` and `original` subtrees plus top-level props; for video example includes `length` ("0:01:28.65"), `duration` (88.65), `fps`, `size` ("1280x720"), `width`, `height`, `aspect`, `audio_encode` ("aac (LC)..."), `audio_bitrate` ("44100 Hz"), `audio_channels`, `original{width,height,frame_rate,duration,bitrate}`, `small{width,height,size,aspect}` — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- `meta[focus]` / `meta[focus][x][y]` = focal point for smart thumbnail cropping (x,y in -1.0..1.0) — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- `description` = alt text for accessibility, null if not provided; `blurhash` = BlurHash for colorful preview before download, nullable — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- Statuses carry `media_attachments` array (cross-ref to MediaAttachment entity) — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- Upload: `POST /api/v2/media` (`write:media`); images processed synchronously (200), larger formats (video/gifv/audio) asynchronously (202 with `url: null`, `preview_url` available); use `GET /api/v1/media/:id` to poll completion (200 done / 206 still processing) — [Mastodon media methods](https://docs.joinmastodon.org/methods/media/)
- `GET /api/v1/media/:id` returns 404 if not owned; `PUT` updates `thumbnail/description/focus`; `DELETE` (v4.4.0) deletes unattached media — [Mastodon media methods](https://docs.joinmastodon.org/methods/media/)
- Posting limits (defaults): images PNG/JPG/HEIF/WEBP/AVIF up to 16MB (downscaled to 8.3MP), up to 4 per post; GIFV up to 16MB converted to soundless MP4, <1MP (1280x720), one per post; videos MP4/M4V/MOV/WebM up to 99MB transcoded to H.264 MP4 max 1300kbps, 120fps, one per post; audio up to 99MB transcoded to MP3 V2 VBR ~192kbps, one per post — [Mastodon Posting](https://docs.joinmastodon.org/user/posting/)
- Soundless MP4/WebM uploads "will be looped in clients the same way as an animated GIF" — [Mastodon Posting](https://docs.joinmastodon.org/user/posting/)
- Per-instance limits exposed at `configuration[media_attachments]`: `description_limit=1500`, `image_matrix_limit=33177600`, `image_size_limit=16777216`, `video_size_limit=103809024`, `video_frame_rate_limit=120`, `video_matrix_limit=8294400`, plus `supported_mime_types` — [Mastodon Instance entity](https://docs.joinmastodon.org/entities/Instance)
- mastodon.social reported lower effective video limit (~68MB observed despite 99MB API value) — user report/discussion, strength uncertain — [Discussion #26709](https://github.com/mastodon/mastodon/discussions/26709)
- Older/community sources cite 40MB as mastodon.social video limit and 10MB image limit — likely outdated vs. current 99MB/16MB docs; instance config is authoritative — [Opalstack forum](https://community.opalstack.com/d/1078-media-upload-errors-with-mastodon-instance)
- ffmpeg preparation: supported MP4/M4V/MOV/WebM, max bitrate 1300kbps, max framerate 60fps in a 2023 how-to; use `-b:v 1300k -maxrate 1300k` and `scale/fps` filters — [Danny van Kooten](https://www.dannyvankooten.com/blog/2023/resize-downsample-video-ffmpeg)

### Inferences
- Babble should read limits from the instance entity, not hardcode 40/99MB; older instances still advertise 40MB.
- Client needs only progressive MP4 playback + thumbnail + blurhash placeholder; no adaptive ladder needed for Mastodon-native media.
- `url: null` + `preview_url` present is the normal "still transcoding" state for own uploads via v2 API; statuses from federation should normally have resolved `url`.

### Gaps
- No documented Mastodon HLS endpoint, signed/expiring media URLs, or CDN-specific headers were found in official docs searched — see next section; I found no reliable source confirming signed media URLs, so treat media URLs as public/permanent unless the instance uses S3 with signed URLs privately.
- Exact Paperclip transcoding pipeline (ffmpeg flags, when WebM is re-encoded vs. preserved) not verified from official docs — inferred only from "transcoded to H.264 MP4" statement.

## Mastodon streaming API (WebSocket timeline) vs. media streaming / HLS

### Takeaway
Mastodon's "streaming API" is a timeline/notification push channel over WebSocket/SSE, entirely distinct from video playback; Mastodon media is fetched via ordinary HTTPS requests to the `url` returned in the REST API.

### Cited Findings
- Streaming API purpose: "Subscribe to server-sent events for real-time updates via a long-lived HTTP connection or via WebSocket" — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- WebSocket endpoint: `wss://mastodon.example/api/v1/streaming`; text frames only, multiplexed subscriptions via `subscribe` control messages or single-stream via `stream` query param; requires user token with `read` scope — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- Hostname discovery: streaming may be on a different hostname than REST API; obtain via `GET /api/v2/instance` `configuration.urls.streaming`, or request `api/v1/streaming` on normal host (redirect or 404) — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- Event types: `update`, `delete`, `notification`, `filters_changed`, `conversation`, `announcement`, `status.update`, etc.; payload for `update` is a full Status JSON string — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- Streams: `public`, `public:media`, `public:local`, `public:local:media`, `public:remote`, `hashtag`, `user`, `list`, `direct`, etc.; `only_media=true` filter exists on public streams — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- Since 4.2.0 streaming requires a User token, removing Public/App token access; lossy/volatile (only while connected) and closed to scrapers — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/); [SocialHub discussion](https://socialhub.activitypub.rocks/t/mastodon-cancelling-streaming-api-access/4562)
- mastodon.social streaming actually lives at `streaming.mastodon.social`, not the REST host — [GitHub Discussion #24325](https://github.com/mastodon/mastodon/discussions/24325)
- SSE endpoints under `/api/v1/streaming/...` return `Content-Type: text/event-stream`; heartbeats are `:thump` comments — [Mastodon streaming methods](https://docs.joinmastodon.org/methods/streaming/)
- Media files are served from S3-like object storage (`https://files.mastodon.social/media_attachments/files/.../original/...mp4` and `.../small/...png` in all MediaAttachment examples) — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- PeerTube (Fediverse precedent) does use HLS: VOD HLS playlists (`.m3u8`) served as static files; live via RTMP→ffmpeg→HLS; hls.js loads playlist and fetches `.ts` segments, with a custom loader also fetching segments from P2P peers via WebRTC — [PeerTube Architecture](https://docs.joinpeertube.org/contribute/architecture); older versions combined HLS-over-WebRTC-P2P, v6.0.0 removed WebTorrent — [PeerTube Wikipedia](https://en.wikipedia.org/wiki/PeerTube)
- HLS mechanics (general): master/multivariant playlist lists media playlists with `EXT-X-STREAM-INF` (bandwidth/resolution), each media playlist lists 2–10s segments fetched over plain HTTP — [Mux HLS tags](https://www.mux.com/articles/hls-ext-tags); [BlazingCDN m3u8](https://blog.blazingcdn.com/en-us/what-is-an-m3u8-file-hls-playlists-explained)

### Inferences
- Do not conflate timeline streaming with video streaming in Babble: timeline uses `wss://.../api/v1/streaming` with OAuth; video uses HTTPS GET on `media_attachments[].url` — progressive MP4, seeking via HTTP Range handled by the player/GStreamer.
- If Babble ever wants adaptive streaming, it would need to implement it itself (transcode + HLS) or federate with PeerTube embeds/live — Mastodon gives no HLS source to consume.
- A video-support design for Babble is: thumbnail/blurhash → click → GStreamer playbin on the MP4 URL; "streaming" = progressive playback, not HLS.

### Gaps
- I found no reliable primary source stating whether Mastodon's media host sends `Accept-Ranges`, `Cache-Control`, or signed-URL expiry — object storage (S3) defaults make Range/Cache likely but unverified; could not verify from docs in scope.
- No evidence of auth headers needed for media `url` GETs (media appears public); private/sensitive media authorization rules ad hoc — not verified.

## Other Fediverse / microblog protocols and clients: transferable patterns

### Takeaway
Other Fediverse software reuses the same ActivityPub attachment + progressive-file model; PeerTube is the only Fediverse HLS precedent, and mainstream Mastodon clients treat video/gifv as thumbnail-first, muted-loop-if-animation, controls-if-video.

### Cited Findings
- ActivityPub attachments in Pixelfed: `attachment: [{type:"Image", mediaType:"image/jpeg", url, name}]` with BlurHash in `@context` (`toot:blurhash`) — same shape as Mastodon — [Pixelfed ActivityPub](https://pixelfed.github.io/docs-next/spec/ActivityPub.html)
- Pixelfed is an image-sharing service on ActivityPub interoperable with Mastodon/PeerTube/Friendica — [Pixelfed Wikipedia](https://en.wikipedia.org/wiki/Pixelfed)
- PeerTube is an ActivityPub video platform; videos available via HTTP download, playback favors HLS P2P via WebRTC (hls.js + custom loader), WebTorrent removed in v6.0.0 — [PeerTube Wikipedia](https://en.wikipedia.org/wiki/PeerTube); [PeerTube GitHub](https://github.com/Chocobozzz/PeerTube)
- Mastodon preferences: autoplay animated GIFs disabled by default for accessibility; sensitive media hidden behind click-through overlay by default; hidden media uses BlurHash gradient (can be disabled) — [Mastodon Preferences](https://docs.joinmastodon.org/user/preferences)
- Animated WebP not respecting "auto-play GIFs off" is a known bug topic — [GitHub Discussion #36584](https://github.com/mastodon/mastodon/discussions/36584)
- CW/sensitive media: "mark media as sensitive" hides full media behind blurred thumbnail; CW automatically marks media sensitive — [Mastodon Posting](https://docs.joinmastodon.org/user/posting/)
- HTML `<video>` autoplay convention: muted+autoplay+loop+playsinline is the GIF-replacement pattern; browsers allow muted autoplay, block unmuted autoplay; `loading="lazy"` defers download until in viewport — [Video.js autoplay guide](https://videojs.org/docs/framework/html/guides/autoplay); [MDN video element](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/video)
- GNOME precedent for players: GNOME Videos (Totem) uses GStreamer; GNOME 49 replaced it with Showtime (GTK4/libadwaita) — [GNOME Videos Wikipedia](https://en.wikipedia.org/wiki/GNOME_Videos); [Showtime on Flathub](https://apps.gnome.org/Showtime)
- GTK4/GStreamer embedding precedent: `playbin`/`playbin3` with `gtk4paintablesink` as `video-sink` (or via `glsinkbin`), custom `Gtk.Picture` subclass pattern — [GStreamer Discourse](https://discourse.gstreamer.org/t/best-practice-for-pipeline-to-display-video-in-gtk4-app/2372); `Gtk.Video` supports autoplay/looping/simple controls but is limited (no overlays, multichannel audio, device selection); full players should use GdkPaintable + GStreamer directly — [GTK GtkVideo docs](https://docs.gtk.org/gtk4/class.Video.html)

### Inferences
- Babble (GTK4/libadwaita, Python/PyGObject) should use GStreamer `playbin3` → `gtk4paintablesink` in a `Gtk.Picture` (or `Gtk.Video` for simple looped gifv), not build HLS.
- Mirror Mastodon client conventions: thumbnail+blurhash first, honor global autoplay-GIF and sensitive-media preferences, autoplay only muted/looped gifv, require explicit play for `video`/`audio`.
- Remote media is cached/proxied by the local instance (files host), not fetched direct from origin by the client — client just GETs `url`.

### Gaps
- Lemmy/Pixelfed native video playback specifics (HLS vs progressive) not confirmed — Pixelfed docs found cover images; [Lemmy video handling: no reliable source found in scope].
- Specific Tusky/Mammoth/Elk/Kaiteki/Moshidon/Subway Tooter implementation details (ExoPlayer, AVPlayer, `<video>` components) could not be confirmed from primary sources in this pass — searches returned generic ExoPlayer/video-element docs; [client-by-client matrix: partial gap].
- Whalebird/Lophis GNOME video specifics: only confirmed they are Mastodon desktop clients / general GNOME player landscape; [no primary source on their video widgets in scope].

## Security/privacy, bandwidth, preload, and autoplay policy

### Takeaway
Mastodon already proxies remote media through the local instance, so Babble inherits that privacy boundary by using API URLs; the remaining risks are token leakage to media hosts, tracking via remote URLs, and bandwidth from autoplay/preload.

### Cited Findings
- Remote media caching: instances download remote media into local cache with cleanup (e.g., 7 days for post media, 30 days avatars/headers, 90 days preview cards) — [Masto.host](https://masto.host/re-mastodon-media-storage)
- Media proxy abuse: unauthenticated proxy access enables bot networks to trigger 250k proxy requests/day; Mastodon issue proposes disabling unauthenticated media-proxy access — [GitHub Issue #37987](https://github.com/mastodon/mastodon/issues/37987)
- Sensitive/CW media hidden behind click-through; "always mark media as sensitive" option exists — [Mastodon Preferences](https://docs.joinmastodon.org/user/preferences); [Privacy Guides Mastodon](https://www.privacyguides.org/articles/2025/07/22/mastodon-tutorial-privacy-and-security)
- Mastodon's privacy model: per-post visibility (public/quiet public/followers/private mention); admins of local/remote instances can read stored private posts/DMs; no E2E encryption — [Mastodon Wikipedia](https://en.wikipedia.org/wiki/Mastodon_(social_network))
- Autoplay: disabled by default for GIFs (accessibility), sensitive media behind overlay; BlurHash gradients optional — [Mastodon Preferences](https://docs.joinmastodon.org/user/preferences)
- On Mastodon, images/video are downloaded from the (possibly remote) instance's files host as shown in examples (`files.mastodon.social`) — client never sees the origin author's server directly via API URLs — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)
- `remote_url` exposes origin location only as metadata; recommended client behavior is to load `url` (local cached copy), not `remote_url` — inferred from field docs — [Mastodon MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/)

### Inferences
- Never send the OAuth `Authorization: Bearer` header to media hosts/CDN; media GETs are anonymous in the API design. Babble must not add tokens to GStreamer URIs, and must not log tokens.
- Load `url`, never `remote_url`, to avoid tracking by origin servers and leaking viewership.
- Respect sensitive-media gating and autoplay preferences before creating any player; default to thumbnail-only for `video`, muted-loop for `gifv` only when autoplay enabled.
- Bandwidth controls worth copying: lazy-load thumbnails, preload only `preview_url`/`blurhash`, download full MP4 only on explicit play; pause off-screen videos; data-saver toggle that forces thumbnail-only.

### Gaps
- Mastodon API spec does not document `Content-Security` / `Accept-Ranges` / signed-URL headers for media — [no reliable primary source found in scope].
- Whether Mastodon shows a "download remote media before viewing" confirmation prompt (some clients do for privacy/Tor) — not confirmed from sources here; [gap].
- Tor/onion instance media behavior — documented as admin feature (onion services) but client-side media handling not verified — [gap].
