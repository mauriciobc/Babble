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

- *Images that already fit the tile skip GdkPixbuf.* `_decode_covering` reads the width and height from the PNG, GIF, or JPEG header (the JPEG scan stops at 64 KiB). When the image is no larger than the cover box, it is loaded with `Gdk.Texture.new_from_bytes`, which was several times faster than GdkPixbuf's loader plus the copy into a `Gdk.MemoryTexture`. Larger originals still decode through GdkPixbuf with `set_size`. Decoding modestly oversized files at full size was tried and dropped: Cairo then scaled the full bitmap on every paint, and RSS rose about 40 MB. Glycin (`Gly.Loader` plus a Cairo downscale) was also dropped: its `FrameRequest.set_scale` is ignored by PNG and JPEG loaders, and the gain was within noise.
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
| token read (was `present()` + 133-581) | |
| home request sent | ~700 |
| home response | ~2,200 (1.2-1.5 s after it is sent) |
| first card, no cache | 2,100-3,400 |

The home request itself is the floor: TCP and TLS are about 3 round trips (~750 ms), the server another ~250 ms, and the body is only 21 KB (the client already negotiates zstd/gzip/br; 133 KB uncompressed). Preconnecting at launch does not help, because the main loop does not run during imports, window construction and `present()`, so the handshake cannot progress; run in a worker thread it finishes, but the main-thread request then never answers on that connection.

What was done:

- **The timeline canvas is a flat colour.** The 2.5 MB wallpaper PNG was decoded on the main thread, again on every stylesheet parse, before the first card (37 ms to decode, 45-85 ms at the first card; response to first card 141-186 ms with it, 54-103 ms without). A gradient, or the SVG grain from css-tricks, costs the same kind of thing, so the canvas has no background image at all.
- **The keyring is read on a worker thread.** As async D-Bus calls on the main loop, each hop (service, session, lookup) queued behind a first-frame redraw: 133-581 ms between `present()` and the token for a 7 ms lookup. On a thread it is 46-101 ms in the app.
- **The home timeline paints the page it last loaded** (`homecache.py`), then swaps in the live page whole. First card 949-1,198 ms against 2,100-3,400 ms. The file holds the account's followers-only and direct posts, so it is `0700`/`0600`, goes with the login, and is ignored when unreadable, from another version, or older than a week.

Measured and not worth it: preconnect (above), starting the other startup requests later (the first response is no slower with them: 818-1,024 ms in parallel, 818-938 alone), trimming imports (51 ms of self time in total), and building the first cards inside the first layout (more frames, no earlier card).
