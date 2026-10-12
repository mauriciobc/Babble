# Memory leak investigation protocol

How to decide whether Babble's growing memory is a leak, find which layer holds it, and prove a fix. Read `PERFORMANCE.md` first. It explains why some memory is kept on purpose.

## 1. What counts as a leak

RSS going up is not a leak by itself. There are four things it can be, and the protocol separates them:

| Kind | Signature | Action |
|---|---|---|
| **Bounded retention** | Growth stops at a cap (table below) | None. Change a cap only with numbers |
| **Content growth** | Grows with posts actually loaded, and comes back when the content is dropped (page closed, timeline trimmed) | None, unless a cap is missing |
| **Allocator retention** | Python counts flat, but RSS stays high; `malloc_trim(0)` gives a lot back | Not a leak. Optionally tune the allocator (§4.4) |
| **Leak** | Repeating the **same** action and undoing it raises RSS *and* some count by about the same amount every cycle, with no plateau | Find the holder (§4), fix it (§5) |

The test is always a **repeated cycle**: do an action, undo it, N times. Steady growth per cycle after warm-up is a leak. One-off growth is not.

### Holders that are bounded on purpose

| Holder | Cap | Where |
|---|---|---|
| Avatar textures | 256 × up to 96 px at display scale | `AVATAR_CACHE_SIZE`, `src/timeline.py` |
| Media thumbnails | 48 decoded textures | `MEDIA_TEXTURE_CACHE_SIZE` |
| Custom emoji | 128 | `EMOJI_TEXTURE_CACHE_SIZE`, `src/textemoji.py` |
| Media viewer | 6 full-size textures | `TEXTURE_CACHE_SIZE`, `src/mediaviewer.py` |
| Hidden cards on unmapped shells | 100 shells across all pages | `CARD_SHELLS_KEPT` |
| `Gtk.ListView` item widgets | about 205 per list, whatever the scroll position | GTK |
| Posts in a timeline | 400, trimmed only while the reader is at the top | `TIMELINE_MAX_ITEMS` |
| Sidebar pages | live for the whole session once opened | `window._build_place` |

A timeline scrolled away from the top keeps every post it loaded until the reader returns, so infinite scroll is content growth by design.

## 2. Tools

| Tool | Answers | Notes |
|---|---|---|
| `python3 bench/memwatch.py` | RSS/CPU/threads over time; probes inside the live app | Launches the app as its child: with the default `kernel.yama.ptrace_scope = 1` a running copy cannot be attached. Babble is single-instance, so **quit any running copy first**. Enter = probe |
| `bench/memprobe.py` (injected by memwatch via `sys.remote_exec`, Python 3.14+) | Live Babble objects by type; Babble widgets alive but in no window; every module/class cache with its length; list models and sizes; texture pixels; glibc heap; what `malloc_trim` frees; tracemalloc growth; referrer chains | Runs on the main thread. A probe freezes the app for about a second, several seconds with tracemalloc, and a tracemalloc snapshot itself adds tens of MB. Use the RSS *the probe reports* (taken before it allocates), not the samples printed right after |
| `./autoresearch.sh` | Scripted timeline workload: `retained_mb`, `live_rows`, `live_items` after teardown | Regression gate; see `bench/timeline_bench.py` |
| `xvfb-run -a meson test -C build 'timeline memory'` | Pages, rows and dialogs are freed; edits happen in place | The pattern for a new regression test |
| `heaptrack` (`pacman -S heaptrack`) | Native allocation call stacks, leaked at exit | For growth with flat Python counts (§4.4) |
| GTK Inspector (`GTK_DEBUG=interactive`) | Widget tree, objects | Its *Statistics* tab needs `GOBJECT_DEBUG=instance-count` **and** a debug build of GLib. On Arch's release GLib, `GObject.type_get_instance_count()` stays 0 (checked), so it can't be used here |

memwatch options: `--interval S` sampling period; `--probe-every S` periodic probes; `--trim` also run `malloc_trim(0)`; `--tracemalloc N` start the app with N traceback frames per allocation (slow; only for §4.3); `--chain TYPE` print who holds live instances of `TYPE` that are in no window. Output goes to `build/memwatch-<time>/`: `samples.csv`, `probes.jsonl`.

**Blind spot.** `gc` only sees Python wrappers that exist right now. PyGObject drops the wrapper of an object only GTK holds and rebuilds it on demand: live `StatusRunRow` shells do not show up in `gc` at all. The usual Babble leak, a C closure holding a bound method, *does* keep the wrapper and shows up. A widget leaked purely in C does not: it shows as RSS growth with flat counts, and §4.4 applies.

## 3. Procedure

### Step 0. Setup

```sh
meson compile -C build
# quit any running Babble first
python3 bench/memwatch.py --trim
```

Log in or use an existing account. Wait for the first page, then press **Enter** once: that probe is the baseline.

### Step 1. Warm up

Do the scenario under test 2–3 times without measuring. Caches fill, lazy imports and CSS load, and the first page of each kind is built. Press **Enter**: this probe is the reference.

### Step 2. Repeat cycles

Run **10 cycles** of one scenario and press **Enter** after each cycle, at the same resting point (same page, same scroll position). One scenario per run.

| # | Scenario: one cycle is… | Expected steady state |
|---|---|---|
| S1 | Scroll down and back over posts **already loaded**, with no new page fetched | Flat RSS and counts. `StatusRunRow._kept` ≤ 100 |
| S2 | Infinite scroll a page of new posts, then return to the top and wait for a stream/refresh | `StatusItem` grows while away, then drops back toward 400 at the top. Texture caches at their caps |
| S3 | Open a thread (or profile, hashtag), go back | Every count returns to the reference after going back |
| S4 | Visit every sidebar page | Grows on the first visit only (pages are kept); later cycles flat |
| S5 | Open an image in the media viewer, close it | `mediaviewer._textures` ≤ 6; RSS flat |
| S6 | Leave a streaming home timeline at the top for 10 min (one cycle = 10 min) | `StatusItem` ≤ 400; flat |
| S7 | Open the composer, type, cancel / open preferences, close | Counts return to the reference |

memwatch prints only what changed since the previous probe, so per-cycle growth is visible directly.

### Step 3. Classify

Read the last 5 probes (after warm-up):

1. **RSS** (from the probe line) flat or bounded → not a leak. Stop.
2. RSS rising and `malloc_trim(0) released` large (comparable to the growth) → **allocator retention**, §4.4.
3. RSS rising with a **count** rising by a constant amount per cycle → **leak**; that count names the holder. Go to §4.1/§4.2.
4. RSS rising, counts flat, trim releases little → Python allocations (§4.3) or native/C (§4.4).

Write the classification down with the numbers (template in §6) before changing code.

## 4. Localize

### 4.1 Which object

| Rising in the probe | Meaning | Next |
|---|---|---|
| `widgets not in a window`: e.g. `StatusRow`, `StatusBody` | Widgets dropped from the UI but alive | `--chain StatusRow` (§4.2) |
| `babble objects`: e.g. `StatusItem`, `TimelinePage`, a dialog | Data or pages outliving their view | `--chain <Type>` |
| `module/class containers`: e.g. `AvatarCache._inflight`, `_pending_shells`, `StatusRunRow._kept` | A registry or cache without a cap or a removal path | Read the code that adds to it; every add needs a remove or a cap. Per-instance containers (e.g. a model's `_seen`) are not listed: they grow with their object's count |
| `list models` sizes | A model that only grows | Check that trim/clear paths run |
| `textures` / `texture_pixels_mb` beyond the caps' worth | Textures held outside the caches (a picture, a closure) | `--chain MemoryTexture` |

### 4.2 Who holds it

Restart with `python3 bench/memwatch.py --chain <Type>` and repeat 2–3 cycles. Each chain is the referrer path from a live instance that is in no window:

| Chain ends with | Cause | Fix |
|---|---|---|
| `builtins.method <bound method X.m of …>` → `no Python referrer` | `emitter.connect(signal, self.m)` (or `GLib.timeout_add`, `idle_add`, a callback kwarg) on something that outlives `self`: the C closure owns the bound method | `pages.connect_weak(emitter, signal, self.m)` / `pages.weak_method(self.m)`; disconnect or remove the source in teardown |
| `builtins.function` / `cell` → `no Python referrer` | A lambda or closure capturing `self`, connected the same way | Same as above; don't capture `self` in lambdas given to C |
| `module babble.x` via a dict/list | Module- or class-level cache/registry | Cap it (OrderedDict LRU like the texture caches), key by weak ref, or remove on teardown |
| `no Python referrer` straight away | GTK keeps it: still parented somewhere, a popover/menu model, an IBus-style input context, a toggle reference | Inspect with `GTK_DEBUG=interactive`; check `get_parent()`. For text views, see the `gtk-im-context-simple` pin in `src/textemoji.py` |

Confirm a chain before fixing it: delete the suspected reference in a throwaway build and repeat the cycle.

### 4.3 Python allocations without a counted type

Restart with `--tracemalloc 10` (slow: use 3–5 cycles, not 10). Each probe lists `traced_growth`, the allocation sites that grew most since the previous probe, with call stacks. A site growing by the same KB every cycle is the leak: usually a list/dict/string accumulation (history, logs, parsed JSON kept on an item).

### 4.4 Native and allocator

- **Allocator retention.** The decode workers (`Gio.Task.run_in_thread`) and libsoup threads each get their own glibc arena; freed memory stays in them. Compare a run with `MALLOC_ARENA_MAX=2 python3 bench/memwatch.py --trim`: if the plateau drops a lot and counts are unchanged, it is retention, not a leak.
- **Textures and rendering.** Compare `GSK_RENDERER=cairo` with the default GPU renderer: growth only on GL/Vulkan is renderer or driver caching.
- **C leaks.** With counts flat and trim not helping, run `heaptrack python3 build/src/babble`, do 10 cycles, quit, open `heaptrack_gui` (or `heaptrack_print`) and sort by *leaked* and by *peak*. Python itself shows up as noise; look for GTK/GdkPixbuf/Pango/libsoup stacks that grow with the cycle count.

## 5. Fix and prove

1. Fix the holder named in §4, following CLAUDE.md: `connect_weak`/`weak_method` with GObject weak refs, never Python `weakref` to a widget; caches as capped `OrderedDict` LRUs; lazy parts of `StatusRow` stay lazy.
2. Rerun the **same scenario, same cycle count**. Show: per-cycle growth before → after, and the count that was rising now flat.
3. Add a regression test to `src/test-timeline-memory.py` (or a new `src/test-*.py` registered in `src/meson.build`). Use the existing pattern: build the object, take `obj.weak_ref()`, drop it, `gc.collect()`, run the main loop, assert the ref is `None`. Test the exact path that leaked (the signal, the dialog close), not a copy of it.
4. Gate: `xvfb-run -a meson test -C build` and `./autoresearch.sh` must keep `live_rows=0`, `live_items=0`, with `retained_mb` no worse.
5. Record the finding and figures in `PERFORMANCE.md`.

## 6. Report template

```
Scenario: S? (exact steps), N cycles, build <commit>, renderer <default|cairo>
Reference probe: rss X MB
Per-cycle after warm-up: rss +A MB/cycle, <count> +B/cycle, trim released C MB
Classification: bounded | content | allocator | leak
Holder: <type>, chain: <chain lines>
Fix: <file:function>
After: rss +A' MB/cycle, <count> +B'/cycle
Tests: timeline memory OK, autoresearch live_rows=0 live_items=0 retained_mb=R
```
