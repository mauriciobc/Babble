# v2 timeline benchmark

The v1 harness (`bench/timeline_bench.py`, driven by `autoresearch.sh`) is
kept and still works. This is a second, wider one, in its own directory so
the two can be compared rather than confused.

## Why a second harness

v1 is honest about what it measures, and its optimisation work is real.
The problem is the *regime*. v1's configuration sits in a corner that real
use does not occupy:

| | v1 measures | a reader gets |
|---|---|---|
| avatars | 96x96 fixtures | 400x400 — what instances serve |
| images | PNG | JPEG, whose decode cost is nothing like PNG's |
| timeline | 80 posts | past `TIMELINE_MAX_ITEMS` = 400 |
| main loop | fully drained before every step | decode, paging and streaming in flight |
| first card | not measured | the number they actually wait for |

Each row is a blind spot, and they compound:

* **96px avatars always take the fast decode path.** At 96px an avatar is
  no bigger than its cover box, so `_decode_covering`
  (`src/mediacache.py:183-189`) loads it whole with
  `Gdk.Texture.new_from_bytes`. At 400px it is *larger* than the box and
  goes through the GdkPixbuf loader instead. v1 therefore measured the
  optimised path for every avatar in the run.
* **PNG is not JPEG.** Random bytes through zlib land close to the stored
  size, so PNG decoding is largely a memory copy; a real attachment's
  inverse DCT costs several times more for the same pixels. Benchmarking
  PNG measures a decode path readers do not take, and no figure
  extrapolated from it survives contact with real attachments. The fixture
  serves JPEG, via GdkPixbuf's own saver — no new dependency.
* **80 posts never reaches either of the app's own bounds.**
  `CARD_SHELLS_KEPT` is 100 and `TIMELINE_MAX_ITEMS` is 400. Note the
  default here is **500 posts**, not 400: `trim(400)` over at most 400
  items is a no-op, so a 400-post run does not exercise the bound it
  appears to.
* **`settle()` is load-bearing, not a convenience.** It waits for a fully
  idle main loop before every step. Remove the wait and v1 fails outright
  (`bench failure: loaded 40 of 80 statuses`) — so the harness is
  structurally incapable of measuring concurrent load, which is where
  jank lives.

## What v2 adds

| metric | why |
|---|---|
| `decode_avatar_fast`/`_slow`, `decode_media_fast`/`_slow` | proves which decode path ran, per source, so a run cannot silently measure only the fast one |
| `popin_ms_median` / `_p90` / `_max` | how long a post's cards take to get built after its shell is queued |
| `frames_during_decode` | frames painted with a scaled decode still in flight |
| `first_card_ms` | time to the first painted **card** — v1 does not measure it and the docs record it by hand |
| `http_requests` / `not_found_requests` / `duplicate_requests` | redundant or wasted fetching as a number, not a code-reading exercise |
| `soak_rss_page1_mb`, `_page2_mb`, … | what each additional open page costs once the first has taken its share of `CARD_SHELLS_KEPT` |
| `inflight_requests` | fetcher keys still outstanding once the loop is quiet — a zero-gate, because a non-zero value is a request that can never complete |
| `conditions` | the regime the run happened in, so a figure is never read without it |

### `inflight_requests`

This one exists because the harness was blind to a class of bug it should
have caught. `live_rows` and `live_items` count *widgets*; they say
nothing about the image fetcher's own bookkeeping. A key stranded in
`Fetcher._inflight` leaves no widget behind, yet means that image can
never be fetched again — every later row queues another waiter on a
request that will never answer, and the list grows. That is exactly the
bug fixed in `src/imagecache.py` (see `src/test-imagecache.py`), and the
harness had no metric that could have noticed it. Now it does, and the
gate holds it at zero.

Every v1 key is kept with the same meaning, including the per-phase CPU
deltas, so the two output streams can be diffed directly.

### Two things the decode counters must not be

**Per source.** An aggregate cannot tell a healthy run from a broken one:
with one combined `decode_slow`, serving 96px avatars reads as a *small
improvement*, because the oversized media still take the slow path and
keep the total up. This was not hypothetical — the first version of this
harness did exactly that and its own gate passed the broken run.

**Not covering the full-size path.** `frames_during_decode` counts
decodes of scaled avatars and media tiles. It does not count the
`cover=None` path (the media viewer, and every emoji), because the gauge
is opened and closed inside `_decode_covering` and that path never enters
it. An earlier version opened the gauge in `_decode_async` and closed it
when `_decode_covering` returned; that drifts permanently, because a
`Gio.Task` with `return-on-cancel` can skip its worker entirely when
cancelled before starting, leaving an increment with no decrement. After
that, every later frame counts as painted during decode.

### What `popin_ms_*` does and does not mean

The time from a run shell being put on the idle queue to its cards being
built. It is **not** strictly "time a reader stared at a blank card":
`Gtk.ListView` maps shells just past the viewport, so some queueing is for
rows nobody is looking at. A narrower claim would need GTK to report when
a shell first painted. A sample is recorded only when `_populate`
actually built cards — early returns are not samples, and a stamp is
dropped on unmap so it cannot be attributed to a much later populate.

## Scenarios

    BENCH_SCENARIO=scroll      # v1's workload, realistic images, 500 posts
    BENCH_SCENARIO=firstload   # cold start to first painted card
    BENCH_SCENARIO=concurrent  # scroll with no idle-waiting: overlap
    BENCH_SCENARIO=soak        # several pages open at once

`concurrent` is the one v1 cannot express: it pumps the main loop for
fixed slices instead of waiting for idle, so scrolling happens while
decodes and HTTP replies are still pending. It ends when the same number
of posts is loaded however many steps that took — **bounded by work, not
by the clock**, because a fixed number of time slices makes the CPU
figure a function of how fast the machine is, and a faster machine then
looks like a regression. If it cannot load them all it fails rather than
reporting a number.

## Running it

```sh
./bench/v2/run.sh                                  # scroll, 500 posts, 5 runs
BENCH_SCENARIO=concurrent ./bench/v2/run.sh
BENCH_RECORD=1 ./bench/v2/run.sh                   # re-record the baseline
BENCH_GATE=1 ./bench/v2/run.sh                     # fail outside tolerance
```

Output is `METRIC key=value` lines, the same shape `autoresearch.sh`
emits. Environment: `BENCH_RUNS`, `BENCH_TOTAL`, `BENCH_SCENARIO`,
`BENCH_AVATAR_PX`, `BENCH_MEDIA_PX`, `BENCH_LATENCY_MS`, `BENCH_RENDERER`,
`BENCH_FRAME_BUDGET_MS`, `BENCH_SOAK_PAGES`, `BENCH_SOAK_STEPS`,
`BENCH_SCROLL_STEPS`, plus the profiling knobs.

To compare against v1's regime, pin the fixture sizes back:

```sh
BENCH_AVATAR_PX=96 BENCH_MEDIA_PX=640x480 ./bench/v2/run.sh
```

`BENCH_GATE` will refuse that run against a baseline recorded with 400px
avatars, which is the point: the two regimes are not comparable.

## Profiling: sysprof

```sh
BENCH_PROFILE=/tmp/capture.syscap ./bench/v2/run.sh
```

There are **two** capture mechanisms here and they are not
interchangeable, which an earlier version of this file blurred:

* **`sysprof-cli`** gives a full capture: CPU stack samples *and* GTK's
  frame-clock marks. Call graphs, flame graphs, allocation sites — the
  "which code is hot" questions.
* **`SYSPROF_TRACE_FD` alone** gives GTK's frame-clock marks and nothing
  else. **No CPU samples, therefore no call graphs.**

An earlier version defaulted to the second and the README pitched sysprof
as the tool for attribution. That overclaimed: without sysprof running
there is no sampler, so there are no stacks to attribute. The default is
now `sysprof-cli` when it is installed (`BENCH_SYSPROF=cli` to insist,
`fd` to force the marks-only path), and the marks-only fallback says
plainly that it captured no stacks.

`GSK_RENDERER=sysprof` is **not** a renderer in GTK 4.24 — the valid values
are `help`, `broadway`, `cairo`, `opengl`/`gl` and `vulkan` — so setting it
captures nothing.

### The split with this harness

Use sysprof for **attribution**, this harness for **budgets**. That is not
a preference; it follows from what each one is:

* sysprof is a **sampling** profiler. Its own documentation is explicit
  that "the amount of time spent in each function is not captured… the
  percentage is calculated by determining how often that function showed
  up in the current stacktrace when a sample was recorded." So it answers
  *which code is hot*, with real statistical confidence, and cannot answer
  *is this release 20% slower* — it reports percentages, not
  milliseconds, and one run's sampling is not stable enough to gate on.
* The harness runs a fixed scripted workload in fresh processes and
  reports medians, so a change of 20% shows up as 20%.
* Some things here are app-specific and **no** general-purpose profiler can
  know them: how many `StatusRow`s survived teardown, whether the client
  fetched the same URL twice, whether a post sat blank, whether the run
  exercised the realistic decode path.

Two things this harness deliberately does **not** measure:

* **Per-frame CPU cost.** Tempting, and not honestly measurable from
  outside GTK: the frame clock never hands out the CPU its layout and
  paint cost, so the only measurable proxy is the CPU consumed between two
  paints, which also contains every unrelated dispatch in between. Tried
  that way it reported a p99 of 7.3 *seconds* — a number describing the
  gap between frames, not the frames. sysprof gets this right from the
  inside. `stall_p99_ms` / `stalls_over_budget` stay, because they are
  well-defined.
* **Memory leaks.** `bench/memwatch.py` and `bench/memprobe.py` already
  cover this, including the part sysprof cannot do — asking a *running*
  app what it has alive. sysprof's allocation tracer complements that for
  finding where growth comes from; it does not replace it.

## The gate

`gate.py` compares a run against `baseline.json`. It exists because
nothing checked these numbers before: every figure in `PERFORMANCE.md` was
measured by hand, on one machine, and nothing failed when one got worse.

`BUDGETS` in `gate.py` is the single source of truth — one entry per gated
metric saying which direction is bad, the relative tolerance, and the
absolute slack. Recording and comparing both read it, so a metric cannot be
recorded one way and judged another. A metric with no entry never gates.
`live_rows`/`live_items` are gates at **zero** with no tolerance, per
`MEMORY-LEAKS.md`.

Four failure modes it is built to avoid, each of which bit an earlier
version:

* **A metric whose good value is 0.** Dividing by the baseline turns
  0 → 0 into "regressed inf%". Every comparison is an absolute allowance
  (`baseline × (1 + tolerance) + slack`) instead of a ratio, and small
  counts carry slack because a percentage is meaningless at 0–2.
* **A direction nobody wrote down.** Treating everything as
  lower-is-better reported a run that painted *more* frames as a
  regression. Direction is now explicit.
* **A floor quietly becoming a ceiling.** A floor-only metric returns as
  soon as it is satisfied and never reaches the drift check.
* **Only measuring what was already suspected.** The metric list grew
  outwards from what the previous investigation had found. Adding
  `inflight_requests` only after a real leak was found and fixed shows
  the bias: the harness could confirm what someone was already looking
  for, and nothing else.
* **Comparing runs that did different work.** Post count, image sizes,
  **image format**, latency, renderer and frame budget must all match. A
  baseline recorded at 500 posts with 400px JPEG avatars says nothing
  about a 80-post run with 96px PNG ones — and the format matters more
  than it looks, since PNG and JPEG decode costs are not comparable. The
  revision is recorded but never compared, and a dirty working tree is
  warned about — figures taken with uncommitted changes describe a tree
  nobody else has.

Accepting a deliberate change is a normal step, not a hand edit:

```sh
BENCH_RECORD=1 BENCH_SCENARIO=scroll ./bench/v2/run.sh
```

## Known limits

* **Software renderer.** Everything runs under Xvfb with
  `GSK_RENDERER=cairo`, which overstates rasterisation cost and cannot see
  GPU-side stalls. `PERFORMANCE.md` says as much about `load_ms`. This is
  unchanged from v1; `BENCH_RENDERER` exists so a machine with a GPU can
  do better.
* **First load is still not the real thing.** `firstload` runs against a
  loopback fixture with no keyring, no TLS and no real RTT. `CLAUDE.md`
  says the honest first-load measurement is the real window under headless
  mutter on the GPU renderer; `first_card_ms` is a useful floor for
  regressions in *our* code, not a substitute for that.
* **`wall_ms` is close to meaningless** — dominated by the harness's fixed
  idle waits. Heavier images produced a *lower* wall time in testing.
  Judge by CPU, main-thread time and pop-in.
* **Absolute values are machine-specific.** Compare only figures recorded
  on the same machine; the tolerances are the real contract. The baseline
  records the revision it was taken at.
* **`concurrent` steps are coarse.** With a loopback fixture a page of 20
  can load inside a single 40ms slice, so at small post counts the
  overlap window is short. It is a stress shape, not a precise jank
  measurement.

## A mistake worth knowing about

`timeline_bench.py:27-30` resolves the stylesheet as
`dirname(dirname(__file__)) + '/src'`. Copy that harness one directory
deeper and the path silently points somewhere else, the stylesheet fails
to load, and GTK prints only a theme-parser warning — after which the
whole run measures an unstyled app. This cost a round of bogus numbers
during development. v2 walks upwards looking for a directory that
actually contains `src/style.css`, so where the harness lives no longer
matters.