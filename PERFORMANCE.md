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

**List updates.** An edit mutates the existing `StatusItem` and emits `changed`; the bound row rebinds itself and keeps view state such as whether the content warning is expanded. `RunModel` keeps a Python mirror of the source, caches each item’s run key, and emits one splice for the changed middle instead of replacing every run. The boosts carousel updates one item in place and reuses its cards (11 carousels and 106 cards became 1 and 16 in the earlier measurement).

**Cheaper rows.** A plain post no longer builds the boost strip, emoji display name, content-warning bar, translation note, or carousel until those parts are needed. The 17-action overflow menu is built on popup. `StatusRow()` went from about 3.5 ms to 1.5 ms. Gettext is resolved once in `src/i18n.py` instead of probing the filesystem on every `_()` call, which had been about 37% of `StatusRow.bind`.

**Scroll height.** `InlineEmojiText` uses height-for-width and a cached Pango estimate until the first allocation. `Gtk.TextView` was wrapping at 1 px, so rows measured about 5400 px and the scrollable height was about 440,000 px. It is about 33,000 px now. That is why scroll-down and streaming stopped realizing thousands of rows.

**Images.** Attachments, cards, boosts, and quotes are decoded with GdkPixbuf only large enough to cover the tile at the largest monitor scale. The cache key is URL plus box, capped at 48 textures. Avatars stay on their own 256-entry cache. Explore, notifications, and scheduled posts that do not pass a cover size still decode the full image.

**Off-screen cards.** `Gtk.ListView` still creates a shell for every loaded run and lays all of them out when a post is inserted at the top. The `StatusRow` tree is built once, kept on the shell, and hidden while the run is unmapped, so measure and snapshot skip it. An earlier version unparented those cards. Streaming CPU fell the same way, but scrolling back reparented them and scroll-up CPU rose to about 690 ms. Hiding instead of unparenting brought scroll-up back to 494 ms. Off-screen shells keep the height they last measured; later shells start from a running typical height, with a 64 px floor so an unmapped run cannot collapse to zero and never be mapped.

**Bounded kept cards.** `Gtk.ListView` keeps about 205 item widgets alive per list whatever the scroll position (a bare `ListView` does the same), and sidebar pages live for the whole session. Every one of those shells held its hidden cards, so memory grew by a full page of cards for each page opened. On a soak with realistic images (400 px avatars, 1600×1200 media, 200 posts per page), three loaded and scrolled pages took +463 MB with 609 live `StatusRow`s. About half of a hidden card's cost is the decoded images it keeps referenced beyond `MEDIA_TEXTURE_CACHE_SIZE`; the rest is its widget tree. Dropping every shell's cards on unmap cut a page from 169 MB to 92 MB but doubled scroll CPU (16.7 s to 32 s). So `StatusRunRow` keeps an app-wide LRU of the last `CARD_SHELLS_KEPT` unmapped shells that still hold cards, as GObject weak refs so it never keeps a page alive. Older shells drop their cards and rebuild them if scrolled back to, keeping their measured height. Pages the reader has left are the least recently unmapped, so they are evicted first, and 100 covers about one page of rows. With it, the same three pages take +165 MB with 104 live `StatusRow`s. The standard benchmark is unchanged (median of 5: CPU 3372 vs 3450 ms, scroll-up 932 vs 928 ms), since its 92 shells fit the budget. The worst case is re-scrolling a 200-post page end to end over and over: four passes took 18.1 s of CPU against 11.5 s unbounded. A budget of 40 saved little more memory per page (65 vs 86 MB) and cost 63% on the standard scroll-up.

**Avatars decoded to size.** `AvatarCache` decoded avatars at full size: instances serve 400×400 and the largest one shown is 96 px, so a full 256-entry cache held about 156 MB. They are now decoded to cover `AVATAR_DECODE_SIZE` (96 px) at the display scale, like media thumbnails.

## Tradeoffs

- **Scrolling back is slower than the original 302 ms.** The old figure was cheap only because the list was already holding thousands of realized rows. Hiding is the better of the two virtualization attempts, and it is still about 190 ms more CPU than the baseline on the way up. Only the `CARD_SHELLS_KEPT` most recently unmapped shells, across all pages, keep their `StatusRow`s; scrolling back further rebuilds them.
- **The scrollbar is an estimate until a row is shown.** The first off-screen shells use 64 px, then the height of posts already displayed. The thumb can grow as real rows are measured. The unallocated-body test only checks that a body is not one character per line.
- **Downscaling applies to timeline tiles.** The media viewer does not go through this cache. Two on-screen sizes of the same URL occupy two cache slots. If the scaled decode fails, the full image is decoded.
- **Weak handlers return `False` once the owner is gone.** That is what lets the page be freed. A signal that used a `True` return to stop propagation will not stop it after the owner has been finalized.
- **The simple input-method context is only on read-only bodies.** Those views are not editable. The composer is unchanged. If a body later becomes editable, it will not see the platform input method until that pin is removed.
- **Replacing `Gtk.TextView` was not done.** Only 11 of the 92 fixture posts are plain text; 79 have links, custom emoji, or both. A `Gtk.Label` hybrid would have saved little and broken link focus, inline emoji images, and the tests that read the text buffer. A custom drawing widget would have been a new text, hit-testing, and accessibility implementation. The remaining per-row cost is still that text view.

`src/test-timeline-memory.py` is wired into Meson as `timeline memory`. It checks in-place edits, that a dropped page is actually freed, and that an unallocated body measures a sane height.
