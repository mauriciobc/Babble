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

## Tradeoffs

- **Scrolling back is slower than the original 302 ms.** The old figure was cheap only because the list was already holding thousands of realized rows. Hiding is the better of the two virtualization attempts, and it is still about 190 ms more CPU than the baseline on the way up. While a timeline stays open, every post that has been on screen keeps its `StatusRow`. Posts that were never mapped do not.
- **The scrollbar is an estimate until a row is shown.** The first off-screen shells use 64 px, then the height of posts already displayed. The thumb can grow as real rows are measured. The unallocated-body test only checks that a body is not one character per line.
- **Downscaling applies to timeline tiles.** The media viewer does not go through this cache. Two on-screen sizes of the same URL occupy two cache slots. If the scaled decode fails, the full image is decoded.
- **Weak handlers return `False` once the owner is gone.** That is what lets the page be freed. A signal that used a `True` return to stop propagation will not stop it after the owner has been finalized.
- **The simple input-method context is only on read-only bodies.** Those views are not editable. The composer is unchanged. If a body later becomes editable, it will not see the platform input method until that pin is removed.
- **Replacing `Gtk.TextView` was not done.** Only 11 of the 92 fixture posts are plain text; 79 have links, custom emoji, or both. A `Gtk.Label` hybrid would have saved little and broken link focus, inline emoji images, and the tests that read the text buffer. A custom drawing widget would have been a new text, hit-testing, and accessibility implementation. The remaining per-row cost is still that text view.

`src/test-timeline-memory.py` is wired into Meson as `timeline memory`. It checks in-place edits, that a dropped page is actually freed, and that an unallocated body measures a sane height.
