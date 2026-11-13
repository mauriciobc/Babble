# Timeline performance and memory

Measured on branch `autoresearch/improve-system-performance-and-memory-management-20261010` at `8db2a79`.

Same workload throughout: a real `TimelinePage` on Xvfb with the Cairo renderer, 80 fixture posts loaded by infinite scroll, 24 jumps back to the top, then 12 streaming inserts with deletes and edits, then teardown. Each figure is the median of 3 fresh processes. The harness is `autoresearch.sh` / `bench/timeline_bench.py`.

| | Baseline (run 0001) | Final (run 0027) | Change |
|---|---:|---:|---|
| CPU | 38.5 s | 2.28 s | **16.9×**, −94% |
| Wall | 44.2 s | 8.0 s | **5.5×**, −82% |
| First page | 741 ms | 432 ms | −42% |
| Scroll to end | 4.00 s | 0.95 s | −76% |
| Scroll back to top | 302 ms | 494 ms | **+64%** |
| Stream burst | 32.6 s | 347 ms | **94×**, −99% |
| Teardown | 528 ms | 70 ms | −87% |
| Peak RSS | 3679 MB | 277 MB | **13×**, −92% |
| RSS growth during the run | 3498 MB | 96 MB | −97% |
| RSS still held after teardown | 3504 MB | 91 MB | −97% |
| Live `StatusRow` / `StatusItem` after teardown | 3357 / 115 | 0 / 0 | freed |

Wall time fell less than CPU time because the harness waits 80 ms of idle after every scroll step and stream event. That waiting is about 5–6 s and did not change. The CPU drop is the real work disappearing.

## What changed

**Leaks.** `connect_weak` and `weak_method` in `src/pages.py` hold the owner with a GObject weak reference. A Python `weakref` was not enough: PyGObject 3.58 drops the wrapper of a widget that only GTK still holds and builds a new one later, so the handler died while the widget lived. This was applied across timeline, notifications, profile, and the other paged views. Read-only `InlineEmojiText` is pinned to `gtk-im-context-simple`, because an IBus context kept a strong reference that was only dropped in dispose. After teardown the benchmark now finds no live rows or items. About 91 MB of RSS remains (GTK, the decoded-texture cache, the interpreter).

**List updates.** An edit mutates the existing `StatusItem` and emits `changed`; the bound row rebinds itself and keeps view state such as whether the content warning is expanded. `RunModel` keeps a Python mirror of the source, caches each item’s run key, and emits one splice for the changed middle instead of replacing every run. A boosts carousel is one list item per fetched page that is mostly boosts (`group_boosts`); when its boosts change, the item updates in place and its cards are reused and only renumbered. The earlier figures (11 carousels and 106 cards became 1 and 16) measured the previous single carousel pinned to the top of the list and have not been re-measured for per-page carousels.

**Cheaper rows.** A plain post no longer builds the boost strip, emoji display name, content-warning bar, translation note, or carousel until those parts are needed. The 17-action overflow menu is built on popup. `StatusRow()` went from about 3.5 ms to 1.5 ms. Gettext is resolved once in `src/i18n.py` instead of probing the filesystem on every `_()` call, which had been about 37% of `StatusRow.bind`.

**Scroll height.** `InlineEmojiText` uses height-for-width and a cached Pango estimate until the first allocation. `Gtk.TextView` was wrapping at 1 px, so rows measured about 5400 px and the scrollable height was about 440,000 px. It is about 33,000 px now. That is why scroll-down and streaming stopped realizing thousands of rows.

**Images.** Attachments, cards, boosts, and quotes are decoded with GdkPixbuf only large enough to cover the tile at the largest monitor scale. The cache key is URL plus box, capped at 48 textures. Avatars stay on their own 256-entry cache. Explore, notifications, and scheduled posts that do not pass a cover size still decode the full image.

**Off-screen cards.** `Gtk.ListView` still creates a shell for every loaded run and lays all of them out when a post is inserted at the top. The `StatusRow` tree is built once, kept on the shell, and hidden while the run is unmapped, so measure and snapshot skip it. An earlier version unparented those cards. Streaming CPU fell the same way, but scrolling back reparented them and scroll-up CPU rose to about 690 ms. Hiding instead of unparenting brought scroll-up back to 494 ms. Off-screen shells keep the height they last measured; later shells start from a running typical height, with a 64 px floor so an unmapped run cannot collapse to zero and never be mapped.

**Bounded kept cards.** `Gtk.ListView` keeps about 205 item widgets alive per list whatever the scroll position (a bare `ListView` does the same), and sidebar pages live for the whole session. Every one of those shells held its hidden cards, so memory grew by a full page of cards for each page opened. On a soak with realistic images (400 px avatars, 1600×1200 media, 200 posts per page), three loaded and scrolled pages took +463 MB with 609 live `StatusRow`s. About half of a hidden card's cost is the decoded images it keeps referenced beyond `MEDIA_TEXTURE_CACHE_SIZE`; the rest is its widget tree. Dropping every shell's cards on unmap cut a page from 169 MB to 92 MB but doubled scroll CPU (16.7 s to 32 s). So `StatusRunRow` keeps an app-wide LRU of the last `CARD_SHELLS_KEPT` unmapped shells that still hold cards, as GObject weak refs so it never keeps a page alive. Older shells drop their cards and rebuild them if scrolled back to, keeping their measured height. Pages the reader has left are the least recently unmapped, so they are evicted first, and 100 covers about one page of rows. With it, the same three pages take +165 MB with 104 live `StatusRow`s. The standard benchmark is unchanged (median of 5: CPU 3372 vs 3450 ms, scroll-up 932 vs 928 ms), since its 92 shells fit the budget. The worst case is re-scrolling a 200-post page end to end over and over: four passes took 18.1 s of CPU against 11.5 s unbounded. A budget of 40 saved little more memory per page (65 vs 86 MB) and cost 63% on the standard scroll-up.

**Trimmed timelines.** Streaming and refreshes only ever prepend, so an open timeline kept every post it had shown for the whole session. `TimelineModel.trim()` now drops posts past `TIMELINE_MAX_ITEMS` (400) and pages again from the oldest one left, so infinite scroll fetches them back. `TimelinePage` only trims while the reader is on the first screen, so the dropped tail is far out of sight; never while an older page is in flight, which would land after the gap; and only on id-paged timelines, since a Link cursor cannot be moved back and an unpaginated source could never fetch the dropped posts again. A reader scrolled away keeps everything until they return to the top. Streaming 600 posts at the top now holds the list at 400 with memory flat; the standard benchmark, at 92 posts, is unaffected.

**Avatars decoded to size.** `AvatarCache` decoded avatars at full size: instances serve 400×400 and the largest one shown is 96 px, so a full 256-entry cache held about 156 MB. They are now decoded to cover `AVATAR_DECODE_SIZE` (96 px) at the display scale, like media thumbnails.

**Second pass (`autoresearch/improve-timeline-performance-20261012`).** CPU went from 3037 ms to about 2705 ms on the same workload (median of 3; single runs spread ±150 ms). Peak RSS stayed near 405 MB and retained RSS near 96 MB. Two changes:

- *Images that already fit the tile skip GdkPixbuf.* `_decode_covering` reads the width and height from the PNG, GIF, or JPEG header (the JPEG scan stops at 64 KiB). When the image is no larger than the cover box, it is loaded with `Gdk.Texture.new_from_bytes`, which was several times faster than GdkPixbuf's loader plus the copy into a `Gdk.MemoryTexture`. Larger originals still decode through GdkPixbuf with `set_size`. The same header read now also enforces a decoded-size budget (16 MP, 8192 px per side): an image over it, or one whose header is not scanned, goes through the loader and is scaled down as it loads, so a small file cannot declare a bitmap it then allocates. The fast path is unchanged for everything inside the budget. Decoding modestly oversized files at full size was tried and dropped: Cairo then scaled the full bitmap on every paint, and RSS rose about 40 MB. Glycin (`Gly.Loader` plus a Cairo downscale) was also dropped: its `FrameRequest.set_scale` is ignored by PNG and JPEG loaders, and the gain was within noise.
- *Off-screen runs keep a measured height.* `Gtk.ListView` maps each new shell, measures it, and unmaps the off-screen ones before they get an allocation, so `get_height()` was still 0 and hiding the cards collapsed the row. `_on_unmap` now measures the shell while its cards are still shown. It stores that height on the run, with the column width and a stamp of what the height depends on (edit, content warning, expansion, media reveal, translation, carousel size). A later shell bound to the same run uses it as its placeholder. The pin only applies while the shell is empty: `_populate` clears it, because as a minimum on a shown post it outlived any change that made the post shorter, such as a wider column, and the next unmap stored the inflated height for good. `timeline memory` checks that a widened post shown again is as tall as a fresh one.

**Third pass: main-thread stalls.** CPU totals hid what the reader feels, a frame that does not come. The harness now also reports main-thread time, the longest single main-loop dispatch, the 99th percentile, dispatches over 16 ms, and frames painted while a shown run was still empty. It calls the introspected `GLib.MainContext.iteration` directly: PyGObject's override pauses an asyncio loop on every call, about 0.25 s of CPU per run that `Adw.Application.run` never pays. It defaults to 5 runs, because single runs spread ±14%. Same workload, median of 5, before → after:

| | Before | After |
|---|---:|---:|
| CPU, all threads | 2782 ms | 2535 ms |
| Main thread | 1967 ms | 1565 ms |
| Longest dispatch | 82.4 ms | 22.2 ms |
| p99 dispatch | 23.6 ms | 10.2 ms |
| Dispatches over 16 ms | 36 | 4 |
| First page | 337 ms | 159 ms |
| Scroll to end | 1159 ms | 502 ms |
| Scroll back to top | 755 ms | 1360 ms |
| Stream burst | 439 ms | 346 ms |
| RSS growth while scrolling | 89 MB | 48 MB |
| Frames with an empty shown run | 0 of 175 | 52 of 265 |

- *Cards are built after layout, one run per dispatch.* The worst stalls were page loads: every new shell is mapped on items-changed, and `_on_map` built all of its `StatusRow`s there (about 5 ms each), although the next layout unmapped most of them. A shell mapped without its cards now queues itself (`_pending_shells`, GObject weak refs), and one idle source at default idle priority, below GDK's redraw, builds the next still-mapped, still-empty shell per dispatch. Shells layout has already unmapped are skipped, so posts that are never shown are never built. A shell whose cards are all kept still shows them at once, and `bind` on a shown shell still rebuilds synchronously, so an edited or regrouped run never blanks. A waiting shell wears `status-run-pending`, an empty card frame, and `_on_unmap` stores no height for it. Two alternatives were measured and dropped: a tick callback runs before layout, so it still built every shell and its longest stall rose to 139 ms; an 8 ms per-burst budget left 19 stalls over 16 ms.
- *Media and avatars decode on a worker.* `_decode_async` runs `_decode_covering` and the `Gdk.Texture.new_from_bytes` fallback through `Gio.Task.run_in_thread`; GDK documents both texture constructors as threadsafe. The display scale is read on the main thread and passed in. The cancellable a picture already holds drops a stale decode. An avatar stays in flight while it decodes, so a second row waits for it instead of fetching it again.
- *Measured and not worth it:* inserting a refresh with one `splice` instead of one `insert` per post (GTK already lays the list out once per frame; 259–279 ms either way), and prebuilding 20 blank `StatusRow`s while idle (longest stall 84 → 68 ms, but more main-thread time overall).

## Tradeoffs

- **A run can show empty for a frame.** Cards are built in the idle after the layout that shows their shell, so a run scrolling into view, or a post streamed in at the top, appears as an empty card first: 52 of 265 frames in the benchmark. Scrolling back to the top also rose to 1360 ms, mostly because the benchmark jumps straight to the end, so the posts it skipped are built on the way back up. Reading continuously, each post is built as it comes into view.
- **Scrolling back is slower than the original 302 ms.** The old figure was cheap only because the list was already holding thousands of realized rows. Hiding is the better of the two virtualization attempts, and it is still about 190 ms more CPU than the baseline on the way up. Only the `CARD_SHELLS_KEPT` most recently unmapped shells, across all pages, keep their `StatusRow`s; scrolling back further rebuilds them.
- **The scrollbar is an estimate until a row is shown.** The first off-screen shells use 64 px, then the height of posts already displayed. The thumb can grow as real rows are measured. The unallocated-body test only checks that a body is not one character per line.
- **Downscaling applies to timeline tiles.** The media viewer does not go through this cache. Two on-screen sizes of the same URL occupy two cache slots. If the scaled decode fails, the full image is decoded.
- **Weak handlers return `False` once the owner is gone.** That is what lets the page be freed. A signal that used a `True` return to stop propagation will not stop it after the owner has been finalized.
- **The action bar and overflow menu are their own widgets.** `StatusActions` and `StatusMenuButton` were split out of `StatusRow` so Catch Up's bento tiles can show the same buttons and menu. They are Python GObject subclasses, so each row carries two more wrapper instances: about 0.3–0.8 KB per row measured directly. They reach the row through one weak reference, with no per-row closures. On the benchmark CPU, main-thread time and stalls are unchanged; `retained_mb` sits at about 73 MB, at the top of the baseline's range (67.5–72.5 MB, bimodal), with `live_rows` 0. Making them plain controllers instead of widgets would save the last ~1 MB at the cost of a second design for the tiles.
- **The simple input-method context is only on read-only bodies.** Those views are not editable. The composer is unchanged. If a body later becomes editable, it will not see the platform input method until that pin is removed.
- **Replacing `Gtk.TextView` was not done.** Only 11 of the 92 fixture posts are plain text; 79 have links, custom emoji, or both. A `Gtk.Label` hybrid would have saved little and broken link focus, inline emoji images, and the tests that read the text buffer. A custom drawing widget would have been a new text, hit-testing, and accessibility implementation. The remaining per-row cost is still that text view.

`src/test-timeline-memory.py` is wired into Meson as `timeline memory`. It checks in-place edits, that a dropped page is actually freed, and that an unallocated body measures a sane height.

## After the improvement register (2026-10-15)

Same harness, median of 3, HEAD (`da2eeab`) against this tree, including the timeline shim deletion. Single runs of this workload spread about ±14%, so a difference inside that band is noise. The 2705 ms CPU figure above was measured on another machine and is not comparable to these.

| | HEAD | This tree |
|---|---:|---:|
| `cpu_ms` | 6135 | 5653 |
| `main_ms` | 3929 | 3724 |
| `stall_p99_ms` | 22.4 | 20.9 |
| `stalls_over_16ms` | 62 | 52 |
| `peak_rss_mb` | 404.4 | 403.4 |
| `rss_growth_mb` | 78.6 | 84.5 |
| `frames` | 298 | 306 |
| `frames_with_empty_shell` | 102 | 100 |

No measurable regression. The empty-shell frames are the same cost the section above already records (a run shows empty for a frame while its card is built), not a new bug. The +5 MB of RSS growth is inside the noise.

## First load (2026-10-17)

`load_ms` could not see this, so it was measured on the real window, one run at a time, from process start to the first painted card. On the author's instance (230 ms away) a cold start looked like this:

| stage | ms |
|---|---:|
| imports, `BabbleWindow` built | ~320 |
| `present()` returns | ~550 (once 1,990 on a cold first launch) |
| token read (was `present()` + 133-581) | 46-101 |
| home request sent | ~700 |
| home response | ~2,200 (1.2-1.5 s after it is sent) |
| first card | 2,100-3,400 |

The home request itself is the floor: TCP and TLS are about 3 round trips (~750 ms), the server another ~250 ms, and the body is only 21 KB (the client already negotiates zstd/gzip/br; 133 KB uncompressed). Preconnecting at launch does not help, because the main loop does not run during imports, window construction and `present()`, so the handshake cannot progress; run in a worker thread it finishes, but the main-thread request then never answers on that connection.

What was done:

- **The timeline canvas is a flat colour.** The 2.5 MB wallpaper PNG was decoded on the main thread, again on every stylesheet parse, before the first card (37 ms to decode, 45-85 ms at the first card; response to first card 141-186 ms with it, 54-103 ms without). A gradient, or the SVG grain from css-tricks, costs the same kind of thing, so the canvas has no background image at all.
- **The keyring is read on a worker thread.** As async D-Bus calls on the main loop, each hop (service, session, lookup) queued behind a first-frame redraw: 133-581 ms between `present()` and the token for a 7 ms lookup. On a thread it is 46-101 ms in the app.

Measured and not worth it: preconnect (above), starting the other startup requests later (the first response is no slower with them: 818-1,024 ms in parallel, 818-938 alone), trimming imports (51 ms of self time in total), and building the first cards inside the first layout (more frames, no earlier card).

## API responses parsed once (2026-10-18)

`MastodonClient._on_response` used to parse each response body, re-encode the result into a `GLib.Variant` string to pass it through the `Gio.Task`, and parse that string again in `_envelope` — two thirds of all response JSON work. The task now carries the parsed object (`GObject.Value(GObject.TYPE_PYOBJECT, …)`, as `mediacache._decode_async` already did). Over three 66 s controlled timeline runs, JSON samples on the main thread fell from 82 to 28 and HTTP completions over 5 ms from 30 to 13; `client transport` checks that a response is parsed exactly once. Measurements and method are in `Docs/performance-profiling.md`, investigation log entry 11. Entry 10 there records an avatar change that was measured and dropped: setting a cached picture before an avatar's initials saves nothing on the timeline, because a card's avatars are almost never cached when it is built.

## Constant-size boost cards (2026-10-18)

Every part of a boost card has a constant size, so GTK measures a card once and never at a width: the summary is a three-line `Gtk.Inscription` (measured from font metrics, not by wrapping the text), the name is a one-line ellipsized label capped at one character of natural width, and the thumbnail is an unmeasured overlay (`_SlotPicture`, which reports `CONSTANT_SIZE`) on a fixed 96 px slot. Time in slow layout frames fell by about a quarter (777 vs 1,022 ms over three alternating runs each); the card-population idle p99 rose by about 0.9 ms, because an Inscription builds its Pango layout when it is created. Keep new card parts constant-size, and keep one-line text on labels. Two attempts that built cards after the first three were measured and dropped: each card added to a shown strip lays its row out again on the next frame (`Docs/performance-profiling.md`, entries 12 and 13).

## Card prefetch (2026-10-18)

The shells just past the visible ones (`PREFETCH_AHEAD` below, `PREFETCH_BEHIND` above) get their `StatusRow`s built hidden and their height measured while they are off screen, in a low-priority idle source (`babble-prefetch`) that takes one step per dispatch and only when the step's running average cost fits before the next frame — RecyclerView's GapWorker, in GTK. A hidden subtree queues no resize, and showing a widget later does not clear its own size caches, so a run scrolls in with cards it already has, at a height the list already knows. Over three alternating runs each, the card-population idle p99 fell from 13.1 to 4.2 ms, time in slow layout frames by a third, the worst frame from 48 to 37 ms; frame p99 rose by 0.3 ms because the shown run's cards appear in the frame that maps it. Do not build cards into a shell that is already shown: that lays the shown row out again on the next frame (`Docs/performance-profiling.md`, entries 12 and 14).

## Notifications (2026-10-27)

Two costs in the notifications list, both measured on the model alone (400, 1000 and 2000 rows) and on a real `NotificationsPage` with 20 fixtures under Xvfb and the Cairo renderer. Median of 3 fresh processes, alternating the two trees. The row/open figures spread more than the model ones: row construction is three sub-millisecond measurements and the first-open frame is dominated by the header and layout, so only the widget counts and the model numbers are read as hard.

| | Before | After |
|---|---:|---:|
| Refresh of 40 new, 400 / 1000 / 2000 rows | 35.6 / 75.7 / 148.5 ms | 1.1 / 1.0 / 1.1 ms |
| `items-changed` per refresh | 40 | 1 |
| `strptime` calls per refresh | 1,200 | 0 |
| 100-notification burst, 400 / 1000 / 2000 rows | 90.2 / 196.9 / 368.8 ms | 4.1 / 4.3 / 4.2 ms |
| Burst, per event | 0.90 / 1.97 / 3.69 ms | 0.041 / 0.043 / 0.042 ms |
| Widgets in a 19-row page | 584 | 494 |
| Follow / status / cluster row widgets | 30 / 30 / 51 | 16 / 25 / 51 |
| Build a follow / status / cluster row | 0.88 / 0.53 / 0.52 ms | 0.60 / 0.36 / 0.30 ms |

The first-painted-row figure did not move (25–74 ms on both trees); the open already spends its frame on the header, the clamp and layout, and the rows are a small part of it. What the change removes is the work on every later refresh and stream event, and the hidden widgets each row was carrying.

**Section headings come from the header, not the rows.** `NotificationModel._stamp_sections` walked the whole store after every `items-changed`, with a `strptime` per day group, and wrote the title onto each row item. `prepend_page` inserted k rows one at a time, so a refresh of k notifications walked the store k times: 40 times, and 1,200 `strptime` calls, for the 400-row refresh above. That was most of what was left of the notifications code over 5 ms in HTTP completions. The heading is now `day_heading(date_key)`, called by the header factory when `Gtk.ListHeader` binds the first item of its section. `section_title`, `section_weekday` and the stamping are gone. A cache of the formatted day labels was measured and rejected: 2.9–6.0 ms for the refresh against 1.7–3.7 ms for the `strptime` it was meant to replace.

**One splice per page.** `prepend_page` inserted the runs one at a time, so GTK reflowed the list once per row; `append_page` spliced once but stamped the whole store before it. Both now merge a run into the boundary row when it matches, then `splice` the remaining runs once and emit `items-changed` only for the merged row, which the splice does not cover. One refresh is one change notification at every size.

**Rows build their parts on demand.** `NotificationRow.__init__` built a `NotificationPost` (byline, content, poll, media) and the whole cluster strip (avatar stack, people box, revealer, expander) for every row, hidden when the notification did not need them. Most notifications are single-actor affairs with no post, so a follow row carried 14 widgets it never showed. The post is now built on the first bind that has a status, the cluster strip on the first merged notification, and both are kept and hidden when a recycled row no longer needs them. A follow row is 16 widgets instead of 30, a status row 25 instead of 30; a cluster still needs all 51, and its first bind absorbs the construction that the constructor used to pay. `src/test-notifications.py` pins both the lazy build and the recycling.


## Media playback engine (2026-11-09)

The viewer plays through a per-viewer GstPlay with `gtk4paintablesink`, wrapped in a Python `Gtk.MediaStream`, instead of `Gtk.MediaFile`. Python costs +0.06-0.12 ms per frame in the wrapper's vfuncs, with 0 main-loop stalls (E4). Creating the engine costs +74 MB (E5), so it is made on the first video, GIFV or audio and never for an image. While playing, the NVIDIA hardware decoder adds about 135 MB; the VA-API and software paths plateau at 436-458 MB (E6). Figures are from `reports/video-playback-bench/`.

## Timeline limits for inline playback (2026-11-10)

Inline muted video in the timeline (phase 2 of the video playback plan) must not make scrolling worse, so `bench/compare.py` judges a candidate run of `./autoresearch.sh` against a baseline run of main, metric by metric, with limits in `bench/limits.json`. The limits below were measured, not guessed.

**Baseline:** main at `8f7eb03`, one machine (the author's Arch Linux desktop, Xvfb 1280x1024, Cairo renderer, `BENCH_RUNS=5`). Figures are machine-specific; compare only two files measured on the same machine. Three separate invocations, run back to back with nothing else running. Each cell is the five runs of one invocation, then their median.

| metric | invocation 1 | invocation 2 | invocation 3 |
|---|---|---|---|
| `main_ms` | 1298.5, 1354, 1318.4, 1329.9, 1208.1; **1318.4** | 1322.1, 1361.7, 1367.9, 1281.7, 1321.5; **1322.1** | 1282.5, 1284.8, 1373.6, 1371.1, 1341.3; **1341.3** |
| `stall_max_ms` | 15.7, 23.2, 14.5, 21.8, 16.9; **16.9** | 17.3, 16, 22.9, 14.7, 21.4; **17.3** | 22.2, 13.7, 22.9, 15.2, 21.9; **21.9** |
| `stall_p99_ms` | 8.2, 8.5, 8.9, 8.5, 7.5; **8.5** | 8, 8.7, 8.2, 8.5, 8.1; **8.2** | 8.1, 8.1, 8.8, 9, 8; **8.1** |
| `stalls_over_16ms` | 0, 1, 0, 3, 1; **1** | 1, 0, 1, 0, 1; **1** | 1, 0, 1, 0, 1; **1** |
| `frames_with_empty_shell` | 16, 16, 14, 14, 14; **14** | 14, 14, 15, 16, 15; **15** | 13, 16, 14, 14, 17; **14** |
| `peak_rss_mb` | 388.5, 388.8, 388.4, 388.4, 389.5; **388.5** | 387.5, 389.6, 387.5, 388.9, 390.9; **388.9** | 389, 387.5, 388.6, 388.1, 387.9; **388.1** |
| `rss_growth_mb` | 78.6, 78.6, 78.3, 78.4, 78.7; **78.6** | 77.9, 79.7, 77.5, 78.7, 80.1; **78.7** | 78.3, 77.8, 78.8, 77.7, 77.8; **77.8** |
| `retained_mb` | 78.7, 78.7, 78.4, 78.5, 78.8; **78.7** | 78, 79.8, 77.6, 78.8, 80.2; **78.8** | 78.4, 77.9, 78.8, 77.8, 77.8; **77.9** |
| `cpu_ms` | 2100.9, 2180.4, 2162.6, 2227.4, 1988.5; **2162.6** | 2132.1, 2193.1, 2201.5, 2101.4, 2146.6; **2146.6** | 2065.5, 2100.4, 2180.5, 2222.5, 2176.3; **2176.3** |
| `wall_ms` | 7378.1, 7366.4, 7367.2, 7508.1, 7229.8; **7367.2** | 7481.7, 7491.5, 7501.8, 7376.8, 7381.5; **7481.7** | 7266.7, 7490.3, 7365.8, 7612.9, 7493.5; **7490.3** |

Spread of the three medians (max - min, and as a share of their median), and the widest spread inside one invocation's five runs:

| metric | median spread | within one invocation | limit (fails only if BOTH are exceeded) |
|---|---:|---:|---|
| `main_ms` | 22.9 (1.7%) | 145.9 | +10% and +100 |
| `stall_max_ms` | 5 (28.9%) | 9.2 | +40% and +8 |
| `stall_p99_ms` | 0.4 (4.9%) | 1.4 | +15% and +1.5 |
| `stalls_over_16ms` | 0 (0.0%) | 3 | +2 |
| `frames_with_empty_shell` | 1 (7.1%) | 4 | +30% and +4 |
| `peak_rss_mb` | 0.8 (0.2%) | 3.4 | +3% and +8 |
| `rss_growth_mb` | 0.9 (1.1%) | 2.6 | +5% and +5 |
| `retained_mb` | 0.9 (1.1%) | 2.6 | +5% and +5 |

**Why these limits.** Each is at least about twice the spread between invocation medians, and the absolute side is sized to the run-to-run spread, because a candidate is judged on one invocation.

- `main_ms` (spread 23 ms, 1.7%; runs within one invocation spread up to 146 ms, 11%): +10% and +100 ms.
- `stall_max_ms` is bimodal, about 14-17 ms or 21-23 ms in any five runs, so its median hops between the two (spread 5.0 ms, 29%): +40% and +8 ms, which clears the hop and still catches a 30 ms stall.
- `stall_p99_ms` is steady (spread 0.4 ms): +15% and +1.5 ms.
- `stalls_over_16ms` is a count of 0-3 per run with a median of 1: a percentage means nothing there, so only an absolute +2 applies.
- `frames_with_empty_shell` (spread 1, within one invocation 4): +30% and +4 frames.
- `peak_rss_mb`, `rss_growth_mb`, `retained_mb` are very stable (spread 0.8-0.9 MB): +3% and +8 MB for peak, +5% and +5 MB for the two growth figures. A playing video engine costs far more than this (Media playback engine, above), so inline playback has to earn its memory, or stay off.
- `cpu_ms` and `wall_ms` are printed and not gated: `cpu_ms` counts every thread (decoder threads included), and `wall_ms` includes fixed waits.

Only three medians stand behind these limits, so they are a floor on how noisy the machine can be, not a proof. If a clean main-against-main comparison ever fails, widen the limit and record the run here.

**What the gate can and cannot see.** `main_ms` and the stall figures are main-thread *CPU* time per dispatch, so a main-thread `time.sleep()` is invisible to them (it only shows up as `frames_with_empty_shell` and `wall_ms`); a busy loop is not. Forced regression, to show the gate can fail: 20 ms of main-thread CPU burned in `StatusRow.bind` (never committed) failed `main_ms` (1322 to 2387), `stall_max_ms` (17.3 to 29), `stall_p99_ms` (8.2 to 25.3), `stalls_over_16ms` (1 to 62) and `frames_with_empty_shell` (15 to 52). Main compared against itself passed in all three pairings of the invocations above.

**Wall-clock stalls (added the same day).** The figures above are thread CPU time, so they cannot see a main thread that is blocked without using CPU (a lock, I/O, a synchronous GStreamer state change): a 20 ms `time.sleep` in `StatusRow.bind` left every stall figure unchanged. `bench/timeline_bench.py` now also times each dispatch with `time.monotonic()` and emits `wall_stall_max_ms`, `wall_stall_p99_ms` and `wall_stalls_over_16ms` (the existing keys are unchanged). Three more fresh invocations of `BENCH_RUNS=5` on main with the new bench, nothing else running (the first table's invocations predate these keys; the CPU figures of this second set are in the compare outputs below and moved a little, e.g. `stall_max_ms` medians 22.6, 21.9, 22.4 against 16.9, 17.3, 21.9, and `main_ms` medians 1323.8, 1245.1, 1333.8 with a within-invocation spread up to 284 ms):

| metric | invocation 1 | invocation 2 | invocation 3 |
|---|---|---|---|
| `wall_stall_max_ms` | 23.7, 24.8, 23.7, 24.9, 22.9; **23.7** | 26.2, 23.4, 22.3, 17, 23.4; **23.4** | 24.1, 22.9, 24.6, 23.5, 23.5; **23.5** |
| `wall_stall_p99_ms` | 8.8, 8.3, 9.6, 8.9, 9.1; **8.9** | 9, 10.7, 8.4, 9, 8.8; **9** | 8.7, 9.1, 10.5, 8.8, 10.3; **9.1** |
| `wall_stalls_over_16ms` | 8, 4, 11, 7, 5; **7** | 6, 13, 4, 3, 8; **6** | 6, 10, 14, 6, 13; **10** |

| metric | median spread | within one invocation | limit |
|---|---:|---:|---|
| `wall_stall_max_ms` | 0.3 | 9.2 | +40% and +10 |
| `wall_stall_p99_ms` | 0.2 | 2.3 | +15% and +2 |
| `wall_stalls_over_16ms` | 4 | 10 | +8 |

Wall stalls also count preemption by other processes on the machine, which is why their limits are wider than the CPU ones: `wall_stall_max_ms` is +40% and +10 ms (one run in five dipped to 17.0 and one reached 26.2), `wall_stall_p99_ms` is +15% and +2 ms (within-invocation spread up to 2.3 ms), and `wall_stalls_over_16ms`, a count of 3-14 per run with medians 7, 6, 10, is an absolute +8 only. The `main_ms` limit (+10% and +100 ms) held in the three main-against-main comparisons of this set, whose medians spread 89 ms (6.7%); if it ever trips on a clean main, widen it.

Validation with the new bench: all three main-against-main pairings (invocations 1-2, 2-3, 1-3) exit 0 with PASS on every gated metric, old and new. The 20 ms sleep in `StatusRow.bind` now fails `wall_stall_max_ms` (23.4 to 40.2), `wall_stall_p99_ms` (9.0 to 28.3) and `wall_stalls_over_16ms` (6 to 64), plus `main_ms`, `stall_p99_ms` and `frames_with_empty_shell`. The 20 ms CPU burn still fails `main_ms`, `stall_p99_ms`, `stalls_over_16ms`, `frames_with_empty_shell`, `wall_stall_p99_ms` and `wall_stalls_over_16ms` (`stall_max_ms` passes at +8.0, exactly on its limit, and `wall_stall_max_ms` at +7.1).

**Memory budget for inline playback.** The limits above allow only +5 MB of growth, so any inline playback would fail memory by construction (one engine is +74 MB, E5 in the media engine section above). `bench/limits-inline.json` is identical to `bench/limits.json` except `peak_rss_mb` and `rss_growth_mb`, which allow an absolute +90 MB (the 74 MB of one engine plus margin) with no relative limit. `retained_mb` keeps +5 MB: the engine must be released at teardown, which is the leak check. More than one live engine is out of budget by design (the plan allows at most one active tile). The `bench compare` self-test checks that the two files gate the same metrics and differ only in those two.

**Running it.** Measure the baseline on the same machine, from a worktree of main, then the candidate, then compare; run nothing else meanwhile.

```sh
git worktree add ../babble-main main
(cd ../babble-main && BENCH_RUNS=5 BENCH_JSON=/tmp/base.json ./autoresearch.sh)
BENCH_RUNS=5 BENCH_JSON=/tmp/cand.json ./autoresearch.sh      # on the candidate branch
python3 bench/compare.py /tmp/base.json /tmp/cand.json         # exit 1 on a breach
# inline playback enabled: add --limits bench/limits-inline.json
```

`BENCH_JSON` also keeps every per-run value, so a failure can be read run by run. `python3 bench/compare.py --self-test` (`meson test -C build 'bench compare'`) checks the rule itself.

**STOP CONDITION.** An inline-playback change must pass `bench/compare.py` against a main baseline measured on the same machine, on the default workload with inline playback enabled. If it fails, it gets one optimization pass; if it still fails, inline playback ships off by default behind its preference, or the phase stops.

The inline-enabled comparison uses `--limits bench/limits-inline.json` (one engine's memory allowed, nothing else relaxed); a change that keeps more than one engine alive is out of budget by design.

## Inline GIFV playback: first gate (2026-11-10)

`src/mediainline.py` plays the most visible GIFV tile of a window in place (muted, looping). It was measured against main (`68b287f`, PR #100 merged) on the same machine, with the same served media: main's tree plus only `bench/fixtures.py` and `bench/fixture_server.py` from this branch, so a GIFV's `url` is a 103 KB Ogg/Theora clip (a bouncing ball, 160x120 at 10 fps, 40 s) and its `preview_url` stays the PNG poster. The workload, the statuses and the random sequence are unchanged. `BENCH_RUNS=5` each, nothing else running, `--limits bench/limits-inline.json`. With Autoplay Animated Media off, the candidate matches main (`main_ms` 1308-1465, `stall_max_ms` 15-16, `retained_mb` 79.6, three runs): the tile bookkeeping costs nothing, what costs is playing.

**Round 1** (player, `malloc_trim` after an engine stops or is dropped; the trim was added before this run because each load and stop on one engine left about 16 MB resident, flat once trimmed):

```
main_ms                      1438.6     1676.8     +238.2 +10% and +100  FAIL
stall_max_ms                   15.7      137.4     +121.7 +40% and +8    FAIL
stall_p99_ms                      9        9.3       +0.3 +15% and +1.5  PASS
stalls_over_16ms                  0          5       +5.0 +2             FAIL
frames_with_empty_shell          16         16       +0.0 +30% and +4    PASS
peak_rss_mb                   389.7      521.7     +132.0 +90            FAIL
rss_growth_mb                  77.2      178.5     +101.3 +90            FAIL
retained_mb                    77.2      178.5     +101.3 +5% and +5     FAIL
wall_stall_max_ms              19.9      146.2     +126.3 +40% and +10   FAIL
wall_stall_p99_ms              10.1       10.4       +0.3 +15% and +2    PASS
wall_stalls_over_16ms             3          7       +4.0 +8             PASS
cpu_ms                       2336.7     3158.3     +821.6 -              report-only
wall_ms                      7510.2     8048.9     +538.7 -              report-only
FAIL: main_ms, stall_max_ms, stalls_over_16ms, peak_rss_mb, rss_growth_mb, retained_mb, wall_stall_max_ms
```

**The one optimization pass.** Profiled: Python-side cost is small (`Engine.__init__` 0.16 s once; the player's evaluations and the trims are milliseconds). The stall is the engine's creation: `sink.props.paintable`, which creates the GL context, takes 64 ms of its ~85 ms by itself. The memory is the GL stack and the heap: after the engine is released and trimmed, 101 MB stay resident, of which 31 MB heap and 50 MB the GL drivers the sink loads (`libnvidia-glcore`, `libvulkan_intel`, `libLLVM`, measured by mapping). The change: a GIFV is small and decodes in software, so `Engine(gl=False)` skips `glsinkbin` for it (peak 519 MB with it, 475 MB without, three runs each).

**Round 2:**

```
main_ms                      1438.6     1621.9     +183.3 +10% and +100  FAIL
stall_max_ms                   15.7      142.7     +127.0 +40% and +8    FAIL
stall_p99_ms                      9        8.6       -0.4 +15% and +1.5  PASS
stalls_over_16ms                  0          2       +2.0 +2             PASS
frames_with_empty_shell          16         17       +1.0 +30% and +4    PASS
peak_rss_mb                   389.7      474.1      +84.4 +90            PASS
rss_growth_mb                  77.2      161.8      +84.6 +90            PASS
retained_mb                    77.2      161.9      +84.7 +5% and +5     FAIL
wall_stall_max_ms              19.9      149.2     +129.3 +40% and +10   FAIL
wall_stall_p99_ms              10.1        9.8       -0.3 +15% and +2    PASS
wall_stalls_over_16ms             3          5       +2.0 +8             PASS
cpu_ms                       2336.7     2841.7     +505.0 -              report-only
wall_ms                      7510.2     8148.5     +638.3 -              report-only
FAIL: main_ms, stall_max_ms, retained_mb, wall_stall_max_ms
```

It fails again, so by the stop condition inline playback does not ship on by default: it needs its own preference, off by default (`inline-animated-media`, because `autoplay-animated-media` also drives the viewer), or the phase stops. This is not added here. Why the failures are structural and not a tuning matter: the first engine is created on the main thread in one dispatch of 100-140 ms (the limit is 22 ms), and what the first engine loads (the GL context and drivers, GStreamer's plugins) stays resident after it is released (+85 MB against a limit of +5%). Per run, one in five had a main-thread stall of 1.3-1.4 s (`stall_max_ms` 1331 and 1428 in the two rounds, CPU time, not waiting), which the median hides; the cause was not found. The per-run values are in the `BENCH_JSON` files of the session; they are not committed.

Finding that decided the fixture: GStreamer 1.28.8 cannot seek an Ogg stream back to its start over HTTP once it has ended (`Failed to seek to 0:00:00`, "duplicate event found" in `basesrc`; it works over `file://`, and an MP4 or WebM served by the same server loops). The viewer has the same limit for Ogg; real GIFVs are MP4, so the clip is simply longer than any run.

### Shipped off by default (same day)

By the stop condition inline playback ships off: a boolean `inline-animated-media` (Preferences, Reading, Media, "Play Animated GIFs in Timelines"; default off) must be on as well as everything else the player checks. With it off no engine is created and tiles only register (a few dictionary operations); the bench shows it costs nothing. `BENCH_INLINE=1` makes `bench/timeline_bench.py` set the key before the workload (the app has no such switch). Baseline again main `68b287f` with the same served media (main's tree plus only the two fixture files), `BENCH_RUNS=5` each, nothing else running.

**The shipping configuration (key off), standard `bench/limits.json`, exit 0:**

```
main_ms                      1363.7     1390.2      +26.5 +10% and +100  PASS
stall_max_ms                   23.7       23.9       +0.2 +40% and +8    PASS
stall_p99_ms                    8.6        9.1       +0.5 +15% and +1.5  PASS
stalls_over_16ms                  5          3       -2.0 +2             PASS
frames_with_empty_shell          16         15       -1.0 +30% and +4    PASS
peak_rss_mb                   388.8      388.9       +0.1 +3% and +8     PASS
rss_growth_mb                  76.9       78.8       +1.9 +5% and +5     PASS
retained_mb                      77       78.8       +1.8 +5% and +5     PASS
wall_stall_max_ms              25.2       25.4       +0.2 +40% and +10   PASS
wall_stall_p99_ms               9.5         10       +0.5 +15% and +2    PASS
wall_stalls_over_16ms             7          7       +0.0 +8             PASS
cpu_ms                       2273.1     2318.2      +45.1 -              report-only
wall_ms                        7410     7516.7     +106.7 -              report-only
PASS
```

**The opt-in cost (key on, with the Pause Animation button), `--limits bench/limits-inline.json`, exit 1:**

```
main_ms                      1363.7     1713.8     +350.1 +10% and +100  FAIL
stall_max_ms                   23.7      147.2     +123.5 +40% and +8    FAIL
stall_p99_ms                    8.6        8.8       +0.2 +15% and +1.5  PASS
stalls_over_16ms                  5          3       -2.0 +2             PASS
frames_with_empty_shell          16         17       +1.0 +30% and +4    PASS
peak_rss_mb                   388.8        476      +87.2 +90            PASS
rss_growth_mb                  76.9      161.9      +85.0 +90            PASS
retained_mb                      77        162      +85.0 +5% and +5     FAIL
wall_stall_max_ms              25.2      158.7     +133.5 +40% and +10   FAIL
wall_stall_p99_ms               9.5       10.2       +0.7 +15% and +2    PASS
wall_stalls_over_16ms             7          6       -1.0 +8             PASS
cpu_ms                       2273.1     3014.1     +741.0 -              report-only
wall_ms                        7410     8522.2    +1112.2 -              report-only
FAIL: main_ms, stall_max_ms, retained_mb, wall_stall_max_ms
```

It fails on the same four metrics as round 2, for the same reasons; nothing was optimized further and no limit was changed. The Pause Animation button changed nothing measurable against round 2 (peak 476 against 474.1 MB, growth 161.9 against 161.8, retained 162 against 161.9, `stall_max_ms` 147 against 143; `main_ms` rose by 92 ms over a baseline that was itself 75 ms lower in this session, within the run-to-run spread of 100-150 ms seen before). The baseline moved between sessions (`main_ms` 1439 then 1364), so figures are comparable within a session only.

**Re-measured after the off state was made inert** (the player no longer watches scrolling or schedules a frame callback while the key is off), baseline re-measured from the same `68b287f` worktree, standard `bench/limits.json`, exit 0:

```
main_ms                      1273.9     1236.5      -37.4 +10% and +100  PASS
stall_max_ms                   22.6         23       +0.4 +40% and +8    PASS
stall_p99_ms                    8.2        8.1       -0.1 +15% and +1.5  PASS
stalls_over_16ms                  4          4       +0.0 +2             PASS
frames_with_empty_shell          16         16       +0.0 +30% and +4    PASS
peak_rss_mb                   389.7      388.9       -0.8 +3% and +8     PASS
rss_growth_mb                    77       78.5       +1.5 +5% and +5     PASS
retained_mb                    77.1       78.6       +1.5 +5% and +5     PASS
wall_stall_max_ms                24       24.3       +0.3 +40% and +10   PASS
wall_stall_p99_ms               9.3          9       -0.3 +15% and +2    PASS
wall_stalls_over_16ms             7          6       -1.0 +8             PASS
cpu_ms                       2108.1     2065.5      -42.6 -              report-only
wall_ms                        7395     7374.4      -20.6 -              report-only
PASS
```

## Cheaper inline playback (2026-11-10)

Baseline: main `49800d2` (PR #102), worktree of its own, same session, same machine, `BENCH_RUNS=5` each, nothing else running, same served media (a 103 KB Ogg/Theora clip). `inline on` is `BENCH_INLINE=1`. Every run is listed; figures are `[run1, ..., run5]`.

**The maintainer's candidates (`fix/inline-long-stall`, `c813489`) were measured and not adopted.** Two things in them are not what they claim:

- `autoresearch.sh` there defaults `BENCH_INLINE` to 1, and `bench/timeline_bench.py` tests `os.environ.get('BENCH_INLINE')` for truth, so `BENCH_INLINE=0` (and an empty value, which `${...:-1}` turns into 1) still turns inline on: the documented "override to measure the baseline" does not work. The off figures below use `061be78`'s `autoresearch.sh`.
- With `pause()` removed from `Engine.load()` the pipeline never starts. Standalone (`GstPlay`, `set_uri` only, 1.5 s): no state change, no media info, no frame. `play.pause()` itself returns in 0.1 ms, so there was no 120 ms to save. In the bench, a probe on `InlinePlayer` counted 7 starts and **0 streams shown** at `c813489` (7 and 7 at its base `061be78`): nothing played, which is why "inline on" looked faster and grew RSS by 10 MB instead of 85. The ~1.3 s outliers do not appear because no engine ever runs.

Measured on `061be78` (base) and `c813489`, key on, `limits-inline.json` as on main: base `stall_max_ms` [112.4, 127.3, 1297.6, 151.0, 1292.8] (two outliers of five), `retained_mb` [159.6, 159.0, 161.4, 160.0, 160.8]; `c813489` `stall_max_ms` [49.6, 55.8, 47.8, 46.5, 46.0], `retained_mb` [89.7, 87.1, 88.6, 87.8, 87.9] with no video ever shown. Key off, the two trees are indistinguishable.

**Lazy paintable (candidate 1) does not remove the stall, it moves it.** `gtk4paintablesink` creates its GL context on the main thread on its first READY/PAUSED, whatever the caps. Standalone, a 2 ms ticker on the main loop: eager `sink.props.paintable` costs 61-91 ms in `Engine.__init__`; lazy, there is no gap there, and a 68-72 ms gap at 95-100 ms, when the pipeline prerolls. The 61-91 ms and the +78 MB of driver libraries are the sink's, not the access's.

**What worked: an inline engine does not use `gtk4paintablesink`.** `Engine(gl=False)` decodes into an `appsink` (RGBA, `max-buffers=2`, `drop=true`), copies each frame in the streaming thread, and hands the newest to the main thread as a `Gdk.MemoryTexture`; `mediaengine.FrameHolder` stands in for the sink's paintable. Standalone, one engine, 160x120 Theora, 3 s, two runs each (`gtk4paintablesink` against appsink): construction 68.1 and 81.4 ms against 3.6 and 3.1 ms; longest main-thread gap 68.9 and 82.0 ms against 4.0 and 3.4 ms; RSS growth 78.1 and 77.8 MB against 6.8 and 6.7 MB; 31 against 30 frames. The viewer (`gl=True`) is unchanged.

The first play still pays for loading GStreamer on the main thread: importing the `Gst` typelib 25 ms and `Gst.init()` 11-14 ms (the other typelibs under 2 ms; `import mediaengine` 27-36 ms and `Engine(gl=False)` 14 ms in one go). `InlinePlayer` now does the two in separate idle sources once the key is on and a tile is registered.

**Timeline bench, final tree (`83fb18c`), key off, `bench/limits.json`:**

```
main_ms                      1381.9     1346.7      -35.2 +10% and +100  PASS
stall_max_ms                   22.7       23.1       +0.4 +40% and +8    PASS
stall_p99_ms                    8.6        8.7       +0.1 +15% and +1.5  PASS
stalls_over_16ms                  4          4       +0.0 +2             PASS
frames_with_empty_shell          15         16       +1.0 +30% and +4    PASS
peak_rss_mb                   389.3      389.5       +0.2 +3% and +8     PASS
rss_growth_mb                  76.9       78.6       +1.7 +5% and +5     PASS
retained_mb                      77       78.7       +1.7 +5% and +5     PASS
wall_stall_max_ms              24.4       24.2       -0.2 +40% and +10   PASS
wall_stall_p99_ms               9.7        9.6       -0.1 +15% and +2    PASS
wall_stalls_over_16ms             8          8       +0.0 +8             PASS
PASS
```

**Key on, against main with the key off, `bench/limits-inline.json` (unchanged), exit 1:**

```
main_ms                      1381.9     1428.2      +46.3 +10% and +100  PASS
stall_max_ms                   22.7       32.4       +9.7 +40% and +8    FAIL
stall_p99_ms                    8.6        8.4       -0.2 +15% and +1.5  PASS
stalls_over_16ms                  4          6       +2.0 +2             PASS
frames_with_empty_shell          15         18       +3.0 +30% and +4    PASS
peak_rss_mb                   389.3      404.5      +15.2 +90            PASS
rss_growth_mb                  76.9       90.8      +13.9 +90            PASS
retained_mb                      77         91      +14.0 +5% and +5     FAIL
wall_stall_max_ms              24.4       32.7       +8.3 +40% and +10   PASS
wall_stall_p99_ms               9.7        9.6       -0.1 +15% and +2    PASS
wall_stalls_over_16ms             8          8       +0.0 +8             PASS
FAIL: stall_max_ms, retained_mb
```

Per run, key on (main with the GL sink, then this tree), same session:

```
                     main, key on                         this tree, key on
main_ms              [1597.7, 1465.3, 1448.1, 1565.1, 1488.1]  [1298.2, 1503.1, 1476.4, 1354.9, 1428.2]
stall_max_ms         [114.0, 120.9, 119.9, 106.2, 132.6]       [32.4, 32.0, 29.9, 33.0, 33.9]
wall_stall_max_ms    [121.2, 124.5, 123.7, 110.2, 141.9]       [32.7, 32.2, 30.0, 33.2, 34.0]
retained_mb          [159.0, 159.5, 160.4, 161.1, 160.1]       [91.4, 91.0, 90.4, 90.3, 91.1]
peak_rss_mb          [473.6, 474.6, 474.4, 476.4, 474.9]       [405.4, 403.7, 404.5, 403.8, 405.2]
```

Key off, same session: main `stall_max_ms` [22.4, 22.8, 23.0, 19.7, 22.7], this tree [23.1, 23.1, 24.1, 23.0, 20.6]; `retained_mb` main [77.0, 77.0, 76.3, 76.7, 78.0], this tree [79.4, 78.7, 77.1, 78.6, 79.0].

**Gates on the final tree.** Viewer crash gate (`e3b_viewer.py`, real `present_media()`, alternating video and GIFV): 5 x 50 = 250 cycles, 250 played, 0 crashes, 0 criticals; quit while playing 20/20; `media inline` 20/20; `media inline` "poster replaced in one step, never blank" passes; full suite 104 passed, 0 failed, 1 skipped (`streaming helpers`) under headless mutter at 1280x1024; 0 python core dumps. `media engine` gained checks for the appsink engine (frame, size before frame, a load clears the previous frame, finalisation of the GstPlay and the appsink), and the clearing check was shown to fail with the clearing removed.

**The ~1.3 s outlier.** With the GL sink and the key on it showed in 2 of 5 runs of `061be78` (1297.6 and 1292.8 ms), and on main in 0, 1 (1307.7 ms, wall 2364.5 ms) and 0 of 5 runs in three sessions; with the appsink engine it showed in 0 of 15 runs (the same three sessions). It was not caught in the act (a probe with a main-thread watchdog perturbs the run and found none in 10), so the cause is still not known; it only appears on the path that creates a GL context.

**Why key on still fails `limits-inline.json`.** `stall_max_ms` 32.4 against a ceiling of about 31: the longest dispatch is now the `Gst` typelib import (25 ms), which cannot be split. `retained_mb` +14 MB: the GStreamer libraries and plugin registry stay mapped after the engine is released, which is not a leak. Measured with this tree, an inline engine created, loaded, played 0.5 s and released ten times in one process (RSS in MB, after each cycle): 307.1, 307.8, 308.1, 308.5, 308.6, 308.7, 308.8, 308.9, 308.8, 308.9 from a start of 296.9, so the first cycle costs +10.2 MB and cycles two to ten together +1.8 MB; with the GL sink (main) 378.2 for the first, 379.3 for the tenth, from 297.2, so +81.0 and +1.1. A leak check that asks `retained_mb` to stay within +5 MB of a run that never loaded GStreamer cannot tell the first from a leak; one that compares the retained memory after N engines with after one (growth per extra engine under 1 MB) can.

## Architecture candidates 5–7 — media engine slot (phase 2)

Flake baseline on unmodified `origin/main` under headless mutter 1280×1024 (`meson test -C build -j1`), twenty runs each:

```
SUMMARY media progressive pass=20 fail=0
SUMMARY media inline pass=20 fail=0
```

Inline limits compare (`BENCH_RUNS=5`, `BENCH_INLINE=1`, `--limits bench/limits-inline.json`), same machine, after the engine-slot change:

```
main_ms                      1367.7       1395      +27.3 +10% and +100  PASS
stall_max_ms                   30.4       30.9       +0.5 +40% and +8    PASS
stall_p99_ms                    7.6        7.8       +0.2 +15% and +1.5  PASS
stalls_over_16ms                  5          6       +1.0 +2             PASS
frames_with_empty_shell          15         14       -1.0 +30% and +4    PASS
peak_rss_mb                   417.8      417.7       -0.1 +90            PASS
rss_growth_mb                  97.5       98.7       +1.2 +90            PASS
retained_mb                    97.5       98.9       +1.4 +5% and +5     PASS
wall_stall_max_ms              30.6       31.1       +0.5 +40% and +10   PASS
wall_stall_p99_ms               8.5        8.5       +0.0 +15% and +2    PASS
wall_stalls_over_16ms             7          7       +0.0 +8             PASS
cpu_ms                       2485.6     2592.9     +107.3 -              report-only
wall_ms                      8150.5     8161.7      +11.2 -              report-only
PASS
```
