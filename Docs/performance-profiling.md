# Profiling the running app: findings, 2026-10-18

This records one day of profiling Babble as a real desktop app — the
actual window on GNOME, a real account, real network — rather than through
the benchmark harness. It covers what was measured, how, what each
recording can and cannot show, every finding with its evidence and
confidence, and what was ruled out. `PERFORMANCE.md` records the harness's
figures and the optimisations behind them; this is the complementary
view, and the baseline future fixes are measured against.

Nothing here has been fixed yet. Each finding has a status; the
**Investigation log** at the end is updated as each one is worked on.

## Contents

1. [Summary](#summary)
2. [Environment](#environment)
3. [The recordings](#the-recordings)
4. [Reproducing a recording](#reproducing-a-recording)
5. [What the tools can and cannot see](#what-the-tools-can-and-cannot-see)
6. [Baseline: the controlled timeline runs](#baseline-the-controlled-timeline-runs)
7. [Findings: timeline and post loading](#findings-timeline-and-post-loading)
8. [Findings: other views](#findings-other-views)
9. [Findings outside Babble](#findings-outside-babble)
10. [Ruled out](#ruled-out)
11. [Consequences for the benchmark harness](#consequences-for-the-benchmark-harness)
12. [Investigation log](#investigation-log)
13. [Appendix: marks, counters and file formats](#appendix-marks-counters-and-file-formats)

## Summary

Under controlled conditions the timeline is healthy most of the time:
median frame p95 is 3.1 ms against a 6.9 ms budget, scrolling input is
handled in under half a millisecond, and fewer than 1% of frames run
over. The problems are a handful of specific stalls, nearly all of them
around **new posts arriving**.

| ID | Finding | Where | Evidence | Confidence | Status |
|---|---|---|---|---|---|
| T1 | Layout after new posts is the main source of slow frames | timeline | C1–C3 marks: 177 slow layout frames, 3.9 s; 72% of slow frames in C1 and C3. B stacks: Pango text measurement ~26% of layout, popover shape ~17% | high that it is layout; **the C1–C3 function breakdown is invalid** (see log, entry 1) | investigating |
| T2 | Card-population idles (`_populate_pending`) run over the frame budget | timeline | C1–C3 marks: 81 over budget, p99 7.4–9.5 ms, max 22 ms; B: p95 37 ms, cost spread over per-widget setup (log, entry 3) | high | investigated; fix options listed |
| T3 | Slow HTTP completions (27–35 ms); every API response is JSON-parsed twice and serialised once | timeline | B: `json.dumps` 27% of `_on_response`; 2/3 of response JSON work redundant; completions are mostly image work (log, entry 2) | high | investigated; cheap fix identified |
| T4 | One 530 ms freeze in layout, with the main thread mostly *off* CPU | timeline | C2 only, 69 CPU samples in 530 ms | high that it blocked; cause unknown | open |
| T5 | The display is 144 Hz: the budget is 6.9 ms, not 16.7 ms | all | GTK `fps` counter peaks at 144.1 | high | documented |
| V1 | First web view click blocks for 1.6 s | web column | A: one `button-release` of 1,623 ms; WebKit `ProcessLaunch` 1,517 ms + first GL init | high (A only) | open, not re-measured |
| V2 | Catch-Up's first build: 0.5–1.1 s of main-thread work | Catch-Up | A: 275 ms dispatch + 208 ms frame, 8,128 invalidated CSS nodes; B: 1.1 s burst from `_on_item_bind` | high (both A and B) | deprioritised by request |
| V3 | Opening a profile costs 60–120 ms | profile | B: `ProfileDialog.__init__`, mostly GTK styling | medium | open |
| V4 | Startup: ~250 ms of Python, window presented at 0.62 s | startup | A, B | medium | open |
| V5 | `focus-change` handling p95 35 ms | window | A only | low | open |
| E1 | The user-wide GTK theme override decodes an SVG on the main thread on every style computation | environment | A: 3.7% of main thread; gone in B with a clean `XDG_CONFIG_HOME` | high | not Babble's |
| E2 | The translucent sidebar's blur costs 9.2% of the main thread | opt-in setting | A only (setting on) | high | open (document or reduce) |
| E3 | 16% of the main thread in the Intel Vulkan driver's per-frame swapchain sync | driver | A (kernel time) | medium | not Babble's |
| E4 | GTK compiles a shader on first use (298 ms) and `fsync`s its pipeline cache on the main thread (565 ms) | GTK | A | high | not Babble's |
| E5 | Babble's scaled-decode path (`PixbufLoader`) goes through glycin: sandboxed loader processes (1.7 s CPU in B) plus ~0.8 s of client threads and IPC in Babble's process | images | B (log, entry 5) | high | investigated; open |
| R1 | `AdwAvatar` construction (`g_rand_new` opening `/dev/urandom`) | — | inflated in A by speedtrack; 0.0–0.1% in B | high | ruled out |
| R2 | 23.7% of main-thread samples in `g_wakeup_signal`/`acknowledge` | — | A only, speedtrack's own hooks | high | ruled out |

## Environment

| | |
|---|---|
| Machine | 16 CPUs (hybrid), Intel TigerLake-H UHD + NVIDIA RTX 3060 Mobile |
| Display | 144 Hz panel (GTK `fps` counter peaks at 144.1) |
| Kernel | 7.2.10-arch1-1 |
| GTK / libadwaita / GLib | 4.24.1 / 1.10.0 / 2.90.1 |
| Python / PyGObject | 3.14.8 (built with `-fno-omit-frame-pointer`) / 3.58.1 |
| Mesa | 26.2.5 (GTK renders with Vulkan through `anv`) |
| WebKitGTK / libsoup / Pango | 2.54.1 / 3.8.0 / 1.58.2 |
| gdk-pixbuf / glycin | 2.44.8 / 2.2.3 |
| sysprof / perf | 51.0 / 7.2.11 |
| Code | working tree around `0fdfc93`, plus the `imagecache.py` rework on this branch |

## The recordings

Five recordings were made. They differ in conditions, so they were used
to check each other's findings, never as a before/after pair.

| Run | Tool | Length | Conditions | Use it for |
|---|---|---|---|---|
| A | sysprof, system-wide | 195 s of Babble | personal GTK theme override loaded, translucent sidebar on, `--speedtrack` and `--scheduler` (failed), **a `bench/v2` run going at the same time**, 6% of samples lost | GTK/GLib marks, counters; C-level stacks. Discount anything heavy in `open`/`read`/`write` |
| B | `perf record` + `python3 -X perf` | 222 s | clean `XDG_CONFIG_HOME`, user-space samples only | **Python function names**; who owns the work |
| C1 | sysprof, controlled | 135 s | `bench/profiling/controlled-run.sh` (below) | baseline |
| C2 | sysprof, controlled | 285 s | same | baseline |
| C3 | sysprof, controlled | 145 s | same | baseline |

C1–C3 all did the same timeline-only steps by hand: wait for the home
timeline, scroll down 10–15 flicks so further pages are fetched, scroll
back up, quit. No Catch-Up and no web column.

**C1–C3 started signed out, with default settings.** They ran with an
empty `XDG_CONFIG_HOME` to hide the user-wide GTK stylesheet, and dconf
keeps its database in `$XDG_CONFIG_HOME/dconf/user`, so Babble found no
stored account and every setting at its default: each run began with a
browser sign-in. Their timelines are those of a fresh session (no
restored state) under default settings. See the investigation log,
entry 6; the run script no longer does this.

Files, none of them committed:

* `~/babble-profiles/sysprof-run{1,2,3}.syscap` — C1–C3 (124, 299 and ~130 MB).
* `babble.syscap` (433 MB) and `babble.perf.data` (8.8 MB) in the repository root — A and B. Untracked and not ignored: keep them out of commits.

## Reproducing a recording

### Controlled run

```sh
bench/profiling/controlled-run.sh 4                  # scripted UI (default)
bench/profiling/controlled-run.sh 4 -- --flicks 20   # pass options to the driver
bench/profiling/controlled-run.sh 4 --manual         # drive the window by hand
```

It refuses to start while any Babble (the build tree's or the Flatpak's)
or any bench process is running — Babble is single-instance, and the
Flatpak shares the app ID, so a second launch would hand off to the
running one — or while `kernel.perf_event_paranoid` is above 1 (perf
cannot record call stacks then). It switches the power profile to
`performance`, turns the translucent sidebar off, and gives Babble a
config directory that links to everything in `~/.config` except
`gtk-4.0/gtk.css*`, so the user-wide stylesheet is hidden while dconf —
the stored accounts and settings — stays visible. It records in two
layers: `sysprof-cli --no-perf --no-sysprofd` for GTK's and GLib's marks
and counters, with `perf record -X perf` inside it for CPU stacks, and
writes `~/babble-profiles/runN.syscap`, `runN.perf.data` and, when
scripted, the driver's event log `runN.jsonl`. It restores the power
profile and sidebar setting on exit, and exits 3 when the scripted
scenario did not complete. No polkit prompt.

### The scripted scenario

`bench/profiling/uidriver.py` runs inside Babble's process: it schedules
the scenario on the GLib main loop, then starts Babble exactly as
`build/src/babble` does. No ptrace, accessibility bus or input injection
is involved, so it works on Wayland and under any recorder. The
`timeline` scenario:

1. waits for the home timeline's first page (up to 60 s) and sets the
   window to 1280×900 (unmaximising it first);
2. settles 3 s;
3. flicks down 12 times — each flick moves the list 0.9 of a screen over
   250 ms on the frame clock with an ease-out curve, then pauses 700 ms;
   when a flick triggers a fetch, it waits for the page (up to 20 s) so
   every run scrolls through the same number of pages;
4. pauses 1.4 s at the bottom, flicks back up to the top the same way;
5. settles 3 s and quits through `app.quit`.

Every step goes to the event log with `time.monotonic_ns()` — the clock
sysprof's marks and `perf record -k CLOCK_MONOTONIC` use — so phases can
be cut out of a capture. All timings are options (`--help`).

It moves the list's `Gtk.Adjustment`, not the pointer, so the input path
(kinetic scrolling, the scroll controller) is not exercised; everything
the timeline does in response to scrolling is, because `PagedPosts`
listens to the adjustment's `value-changed`. The actions are identical
between runs; the content is whatever the home timeline holds, so
compare runs by per-frame and per-page distributions, not totals.

C1–C3 were recorded by the first version of this script, which used
sysprof's own sampler; see the investigation log, entry 1, for why its
stacks are not usable.

The recording normally ends by itself when Babble quits. Run A did not
— it needed Ctrl+C — and the difference was not established, so if it
does not end, press Ctrl+C **once** and wait.

### Reports

```sh
cd bench/profiling
python3 timeline_report.py ~/babble-profiles/sysprof-run{1,2,3}.syscap
DEBUGINFOD_URLS=https://debuginfod.archlinux.org \
    python3 window_cpu.py ~/babble-profiles/sysprof-run{1,2,3}.syscap
```

`timeline_report.py` gives, per run and then across runs: frame
percentiles against the 144 Hz budget, the dominant phase of each slow
frame, dispatch latency by source kind, scroll handling, and every
dispatch of 50 ms or more. `window_cpu.py` resolves the main thread's CPU
stacks inside three kinds of window — slow layout frames, HTTP
completions over 5 ms, and idle callbacks over budget — to function
names. `syscap.py` is the capture reader both use.

### Python-level attribution with perf

```sh
sudo sysctl kernel.perf_event_paranoid=1
mkdir -p /tmp/babble-xdg
perf record -e cycles:u -F 999 -g -k CLOCK_MONOTONIC -o babble.perf.data -- \
    env XDG_CONFIG_HOME=/tmp/babble-xdg GSETTINGS_SCHEMA_DIR="$PWD/build/data" \
    python3 -X perf build/src/babble
sudo sysctl kernel.perf_event_paranoid=2
```

At `perf_event_paranoid=2` (the default here) `perf record -e cycles:u`
works but `-g` is refused with a misleading "Failure to open event" —
call-chain sampling is rejected at that level. `1` allows it.

## What the tools can and cannot see

These were established by reading sysprof's source (master at `507dcdc`)
and by testing on this machine. Several contradict what is commonly
assumed, including by `bench/v2`.

### sysprof

* **GTK and GLib talk to sysprof through `SYSPROF_CONTROL_FD`, not
  `SYSPROF_TRACE_FD`.** The control FD is a socket; the in-process
  collector sends `"CreatRing\0"` and receives a shared-memory ring
  buffer it writes marks into. The installed `libgtk-4.so.1` and
  `libglib-2.0.so.0` contain the string `SYSPROF_CONTROL_FD` and not
  `SYSPROF_TRACE_FD`. A GTK window run with `SYSPROF_TRACE_FD=9` pointing
  at a file wrote **0 bytes**. `SYSPROF_TRACE_FD` is for apps that write a
  whole capture themselves through `sysprof_capture_writer_new_from_env()`.
* The profiler always sets up the control socket for a child it spawns
  (`sysprof-profiler.c:141`), so `sysprof-cli … -- cmd` gets GTK's marks
  with no flag. **`--gtk` does nothing**: it is parsed into a variable that
  is never read. `--use-trace-fd` is unrelated to GTK.
* Syntax: `sysprof-cli [--force] [options] CAPTURE -- COMMAND`. There is no
  `capture` subcommand and no `-o`; an existing file is refused without
  `--force`. `main()` returns success whatever the child's exit status,
  so a wrapped command's failure is lost.
* `--no-perf --no-sysprofd` records marks and counters without
  privileges. CPU stacks need sysprofd and polkit.
* Symbol decoding at the end fetches debug info for **every library of
  every process sampled** (sampling is system-wide). With debuginfod on,
  this took over two hours on run A, mostly WebKit's (~400 MB for
  JavaScriptCore, >1 GB for `libwebkitgtk`). Use `--no-debuginfod`; names
  can be resolved afterwards (below).
* `--scheduler` fails here: sysprofd reads
  `/sys/kernel/debug/tracing/events/sched/sched_switch/format`, and on this
  kernel tracefs is at `/sys/kernel/tracing`. sysprofd reports a missing
  file as `AccessDenied`.
* `--speedtrack` (an `LD_PRELOAD` module) captures a stack on every
  main-thread `open`/`read`/`write`/`close`/`fsync` and marks every
  `g_main_context_iteration`, *including the time spent blocked in poll*.
  It inflated `g_rand_new` (R1) and GLib's wakeup writes (R2) many times
  over. Do not use it for cost; it is only for spotting I/O on the main
  thread.
* The sampler is system-wide on CPU cycles every 1.2 M cycles (falling back
  to 1 ms of CPU time), so sample counts follow clock frequency and are
  not comparable between runs. Gate on mark durations, which are exact.
  Run A lost 33,970 samples (about 6%).
* sysprof has **no Python awareness**: it does not read
  `/tmp/perf-<pid>.map`, so Python frames appear only as
  `_PyEval_EvalFrameDefault`. Use perf with `-X perf` for Python names.
* `sysprof-diff`, `sysprof-flamegraph` and the helpers in
  `src/libsysprof/tests/` (`mark-summary`, `top-offenders`) are built with
  `install: false` and exist only in a source build.

### perf

* With `python3 -X perf`, stacks contain both Python functions
  (`py::StatusRunRow._populate:…/statusrun.py`) and C frames. 91% of
  main-thread stacks unwind down to `g_application_run` and stop there
  because the next frame is libffi; everything above that is complete.
* `cycles:u` counts user-space time only, so kernel-heavy costs (E3, file
  I/O) do not appear.
* Arch strips static function names from GTK, libadwaita, Mesa and
  friends, so a plain `perf report` shows 68% of frames as `[unknown]`.
  `perf script --debuginfod` is not accepted by this build. What works:
  `perf script -F time,ip,sym,dsoff`, then resolve each `library+offset`
  with `eu-addr2line -f -e LIB OFFSET…` under
  `DEBUGINFOD_URLS=https://debuginfod.archlinux.org`, which reads the
  cache sysprof filled. For these libraries the code segments' file
  offsets equal their addresses (checked with `readelf -lW`), so the
  offset can be passed as is.

## Baseline: the controlled timeline runs

All figures from `timeline_report.py` on C1–C3. Budget 6.94 ms (144 Hz).

| Metric | C1 | C2 | C3 | Median [min–max] |
|---|---:|---:|---:|---|
| Length (s) | 135 | 285 | 145 | |
| Frames | 7,643 | 13,888 | 9,529 | |
| Frame p50 (ms) | 0.54 | 0.73 | 0.56 | |
| Frame p95 (ms) | 1.86 | 5.45 | 3.13 | 3.13 [1.86–5.45] |
| Frame p99 (ms) | 3.80 | 8.38 | 6.42 | 6.42 [3.80–8.38] |
| Frame max (ms) | 53.9 | 529.9 | 58.1 | |
| Frames over budget | 0.42% | 1.78% | 0.89% | 0.89% [0.42–1.78] |
| Dispatches ≥ 50 ms | 3 | 4 | 2 | 0.84 per minute [0.83–1.33] |
| Scroll event p95 / max (ms) | 0.32 / 0.7 | 0.38 / 2.8 | 0.36 / 1.2 | |
| Layout passes / total ms / over budget | 2,774 / 966 / 17 | 9,046 / 3,897 / 46 | 4,820 / 2,459 / 36 | |

Dispatch latency by source kind (p99 in ms, median [min–max] across runs):

| Source kind | p99 | Notes |
|---|---|---|
| Frame clock | 6.42 [3.80–8.38] | the frame itself |
| Idle callbacks (card population, deferred work) | 8.47 [7.37–9.52] | over budget at p99; max 18.6–22.6 ms |
| I/O completion (HTTP body read, then the Python handler) | 0.20 [0.17–0.22] | max 27.5–34.8 ms: the API pages |
| libsoup / sockets | 0.26 [0.19–0.28] | |
| Input / Wayland events | 0.30 [0.27–0.32] | |

What made slow frames slow (dominant phase, more than 40% of the frame):

| | C1 | C2 | C3 |
|---|---|---|---|
| Frameclock layout | 72% | 38% | 72% |
| Unattributed | 9% | 22% | 16% |
| Frameclock update | 3% | 19% | 1% |
| Validate CSS | 6% | 19% | 6% |
| Widget render / paint | 6% / – | 1% / 1% | 1% / 4% |

## Findings: timeline and post loading

### T1. Layout after new posts arrive

**Evidence.** 177 slow frames dominated by layout across C1–C3, 3.9 s in
total. 94 of them (53%) start within 250 ms of a slow HTTP completion or
a card-population idle, so about half are the frame after posts arrive.

> **Withdrawn below this point:** the two tables that follow came from
> C1–C3's CPU stacks, which lack `libgtk-4`'s mappings, so the clamp and
> closure-notifier self time is an artefact. Kept for the record; see the
> investigation log, entry 1, for the corrected breakdown from run B.

Main-thread CPU inside them (8,340 samples, `window_cpu.py`):

| Inclusive | |
|---|---|
| 43% | libadwaita allocation chain: `adw_navigation_view_size_allocate` → `adw_toolbar_view_size_allocate` → `adw_toast_overlay_size_allocate` → `adw_breakpoint_bin_size_allocate` → `adw_dialog_host_size_allocate` |
| 28% | `adw_clamp_layout_allocate` |
| 18% | `adw_clamp_layout_measure` |
| 5% | cairo glyph metrics (`cairo_scaled_font_glyph_extents`) |

| Self | |
|---|---|
| 31% | `closure_invoke_notifiers` (GObject) |
| 25% | `adw_clamp_layout_allocate` |
| 12% | `adw_clamp_layout_measure` |

**Reading.** The timeline is `Gtk.ScrolledWindow` →
`Adw.ClampScrollable` → `Gtk.ListView` (`src/timelinepage.py:230-251`),
the correct structure for a recycled list. In run B, whose stacks are
complete, layout's cost is mostly Pango measuring and shaping text
through height-for-width, and the clamp only passes calls through.

**Confidence.** High that layout is the cost (from marks); what runs
inside the slow layout frames after posts arrive needs the new
timeline-only recording.

**Next.** See the investigation log.

### T2. Card population over budget

**Evidence.** In C1–C3, 81 idle dispatches over the 6.9 ms budget, 980 ms
in total; idle p99 7.4–9.5 ms, max 22 ms. In B, `_populate_pending`
(`src/statusrun.py:76`) had 50 busy stretches with p95 37 ms and p99
50 ms. The function breakdown is in the investigation log, entry 3 (from
run B; the C1–C3 stacks behind an earlier version of this paragraph lack
GTK's mappings).

**Reading.** `_populate_pending` builds the cards of one waiting run per
idle dispatch (`statusrun.py:72-88`). A run with several posts builds
several `StatusRow`s in one go; at 144 Hz each over-budget dispatch drops
one to three frames. `update_font_size` is libadwaita recomputing an
avatar's initials font, triggered by setting its text and size.

**Confidence.** High.

### T3. API page responses

**Evidence.** 56 HTTP completions over 5 ms in C1–C3, 500 ms in total,
max 27–35 ms per run. Typical completions are cheap: p99 0.2 ms. The
breakdown, and the redundant JSON round trip found in it, are in the
investigation log, entry 2.

**Confidence.** High for the durations and the redundant work; which
long completions are API pages and which are image work needs the new
recording.

### T4. A 530 ms layout freeze, mostly blocked

**Evidence.** C2 at t+186.8 s: a 529.9 ms frame, of which 520.5 ms was
layout, but only 69 main-thread CPU samples landed in it. At the
sampler's rate that is a small fraction of 530 ms, so the main thread
was off CPU — waiting — for most of it.

**Reading.** Something in layout blocked. Run A showed Pango waiting on
the fontconfig thread (`wait for FcFontSort`) inside slow layouts, which
fits, but this capture has no mark that names it. Once in three runs.

**Confidence.** High that it blocked; the cause is unknown.

### T5. The frame budget is 6.9 ms

GTK's `fps` counter in run A peaks at 144.1. At 144 Hz, run A had 266
frames over budget (2.9%), not the 56 (0.61%) counted against 16.7 ms.
Everything in this document uses 6.94 ms.

## Findings: other views

### V1. The first web view blocks the click that opens it for 1.6 s

**Evidence (A only).** One `button-release` handler took 1,622.8 ms. Inside
it: WebKit `ProcessLaunch` 1,516.6 ms, GTK `Init OpenGL` 20.8 ms. Main-
thread samples in the window: 48% in `drmModeGetConnector` ioctls under
`gdk_display_init_gl` (GTK initialises GL the first time WebKit needs
EGL, while GTK itself renders with Vulkan) and about 35% in Mesa reading
its driver configuration XML.

**Reading.** First-time WebKit setup happens on the click path in
`src/webcolumn.py` (`WebKit.NetworkSession.new_ephemeral()` at line 46,
`WebKit.WebView` at line 222). Doing it at idle after startup would move
the cost off the click. C1–C3 never opened a web view, so this has not
been re-measured under clean conditions.

### V2. Catch-Up's first build

**Evidence.** A: a 275 ms idle dispatch through
`gtk_sort_list_model_items_changed_cb` → `gtk_list_item_manager_ensure_items`
(Catch-Up holds Babble's only `Gtk.SortListModel`, `src/catchup.py:1647`),
followed by a 208 ms frame (53 ms CSS, 150 ms layout); the GTK counters
show 8,128 invalidated CSS nodes and 7,300 styles created in that frame.
B: about 1.1 s of continuous work (0.74 s CPU) from
`CatchUpPage._on_item_bind` (`catchup.py:1852`), which builds a
`CatchUpRow` the first time each slot is bound; `CatchUpRow.__init__` and
`_Stat.__init__` are the heaviest Python, and two thirds of the samples
are GTK styling and allocating the new widgets.

**Status.** Deprioritised: the current focus is the timeline.

### V3. Opening a profile: 60–120 ms

**Evidence (B).** `SearchPage._on_row_activated` (65 ms) and
`PagedList._on_row_activated` (p95 61 ms, 5 times) → `ProfileDialog.__init__`.
Most of each stretch has no Babble Python on the stack: GTK styling the
new dialog. Within the 100 ms response target, with little margin.

### V4. Startup

**Evidence.** A: window presented 0.62 s after the first mark; Vulkan
instance creation 100 ms; first frame over 5 ms at 0.50 s (88 ms). B:
`<module>` of `build/src/babble` 99 ms and `BabbleApplication.do_activate`
91 ms, mostly imports.

### V5. Focus changes

**Evidence (A only).** `focus-change` events: 7, p95 35 ms. Not seen in
the controlled runs' reports; low confidence.

## Findings outside Babble

### E1. The user-wide theme override

`~/.config/gtk-4.0/gtk.css` (a link to a personal theme build) defines
`--ov-texture-image` as an inline SVG noise texture and uses it in
`background-image` through `var()`. GTK appears to re-parse a `var()`
value each time it computes a style, and every parse decodes the SVG
through gdk-pixbuf → glycin (a sandboxed loader process) on the main
thread: `gtk_css_image_url_parse` → `gdk_texture_new_from_bytes_pixbuf` →
`gdk_pixbuf_new_from_stream` → `gdk_pixbuf__glycin_image_stop_load`. 3.7%
of the main thread in A, plus slow style frames; 0.0% in B with a clean
`XDG_CONFIG_HOME`. Every GTK 4 app on this machine pays it. The fix is in
the theme (put the `url()` in the rule, or use a PNG).

### E2. The translucent sidebar

With `translucent-sidebar` on (default off), `backdrop-filter: blur(30px)`
(`src/style.css:713`) makes GTK compute the region to blur in
`gdk_vulkan_context_wayland_end_frame` → `gsk_render_node_compute_background_blur`
on every frame: 8,890 samples, 9.2% of the main thread in A, one call
path. This is the cost of an opt-in feature; worth stating in its
description or reducing (for example only while focused).

### E3. Vulkan swapchain sync

`gdk_vulkan_context_begin_frame` → `anv_AcquireNextImage2KHR` →
`wsi_create_sync_for_image_syncobj`: 16.3% of the main thread in A, almost
all in the kernel, which is why B (user space only) shows 0.1%. Driver
behaviour on this hybrid machine. A comparison under `GSK_RENDERER=gl`
would show whether the GL renderer avoids it.

### E4. Shader compilation and the pipeline cache

A: a 297.9 ms frame compiling a Vulkan pipeline for a radial gradient
(`style.css` has none, so it is the theme's), once; and a 565.5 ms
main-thread dispatch in which GTK saved its pipeline cache with an
`fsync` of 562 ms.

### E5. Image decoding through glycin

gdk-pixbuf 2.44 decodes through glycin, which runs loaders as sandboxed
processes (`bwrap` → `glycin-image-rs`, `glycin-svg`) and keeps a client
side in-process. In B, Babble's process had 228 threads, 203 of them
named `blocking-N`, plus `gly-global-exec`; 818 samples had `libglycin`
on the stack. Investigation (log, entry 5) traced them to Babble's own
`PixbufLoader` decode path on GLib's worker threads. In A, the GIO
counters show the thread pool peaking at 1,817 and "tasks running" at
3,259, but the peaks fall in quiet periods and do not line up with
visible work.

## Ruled out

### R1. `AdwAvatar` and `/dev/urandom`

In A, `adw_avatar_init` → `g_rand_new` → `open("/dev/urandom")` looked
like 61% of a 275 ms idle (236 opens). speedtrack's own timing of those
opens was 1.5 ms in total, and in B — no speedtrack, user space only —
`adw_avatar_init` is 0.1% and `g_rand_new` 0.0%. The cost was the hook
plus kernel time. About 435 avatars were created over A's session; the
count is real, the cost is not.

### R2. Wakeup writes

In A, `g_wakeup_signal` and `g_wakeup_acknowledge` were 23.7% of the main
thread, while speedtrack timed all main-thread `read`/`write` calls at
about 31 ms for the whole session. Some wakeups come from WebKit's
timers; the cost is speedtrack's.

## Consequences for the benchmark harness

These affect `bench/v2` (and partly v1):

* **Its profiling path cannot work.** `BENCH_PROFILE` sets
  `SYSPROF_TRACE_FD`, which GTK does not read (0 bytes, tested). Its
  `sysprof-cli capture -o` form does not exist. Use
  `sysprof-cli --force --no-perf --no-sysprofd OUT -- …` for marks without
  privileges, and check the bench's own exit status separately.
* **The user's GTK theme leaks into every run.** The harness isolates
  GSettings but not `XDG_CONFIG_HOME`; E1 is in its numbers on this
  machine. The harness already uses `GSETTINGS_BACKEND=memory`, so it can
  point `XDG_CONFIG_HOME` at an empty directory. A run of the real app
  cannot: that hides dconf too (log, entry 6).
* **`cpu_ms` cannot see glycin.** Decoding done in glycin's loader
  processes is not in `RUSAGE_SELF`.
* **The budget is 6.9 ms here,** not 16.67; `stalls_over_16ms` undercounts
  on a 144 Hz panel.
* **It leaves fixture servers behind.** Three `fixture_server2.py`
  processes outlived their runs.
* GTK's frame marks (`Frameclock cycle/update/layout/paint`, `Validate CSS`)
  and GLib's `GSource.dispatch` give exact per-frame and per-dispatch
  durations that the harness currently approximates from outside.

## Investigation log

Each entry: what was tried, the result, and what it changes.

*(Started 2026-10-18; entries are added as each finding is worked on.)*

### 1. T1: the "self time inside `AdwClamp`" was an artefact — C1–C3 stacks lack GTK's mappings

**Tried.** Read `adw-clamp-layout.c` at libadwaita 1.10.0: `measure` and
`allocate` each make one or two `gtk_widget_measure`/`size_allocate` calls
plus arithmetic and CSS class toggles. That cannot be 36% of layout. Then
checked the raw samples behind the slow layout frames in C3.

**Result.** In 1,958 of 2,799 samples (70%) the innermost user frame fell
in **no recorded mapping**. `window_cpu.py` dropped unmapped frames, so
the next resolvable frame up — the clamp, or `closure_invoke_notifiers` —
was credited with the self time. Only 3% of those samples were kernel
time. The cause: sysprof 51.0 recorded only 80–108 mappings for Babble's
process in C1–C3, **missing `libgtk-4`, `libpango`, `libharfbuzz`** (and
in C3 `libadwaita`), against 260 complete mappings in run A. sysprof
master fixed this after 51.0 (`611b21a`, "libsysprof: fix mmap tracking
of spawned applications when racing", 2026-10-08). The missing libraries'
positions cannot be inferred from the recorded ones: their offsets
relative to run A are not constant.

**What it changes.**

* Everything measured from **marks** in C1–C3 stands: the baseline
  tables, frame and dispatch durations, the slow-frame phase split, T4's
  off-CPU observation, and the T2/T3 counts and durations.
* Every **function-level** breakdown from C1–C3 stacks is withdrawn: the
  T1 clamp and closure-notifier self time, and the T2/T3 inclusive lists
  where GTK, Pango or HarfBuzz would appear.
* Run B (perf) has complete mappings. Its layout phase
  (`surface_layout_cb`, 617 samples ≈ 0.62 s over 222 s, including Catch-Up
  activity) breaks down as: the libadwaita allocation chain down to
  `gtk_list_view_size_allocate` (24%); **Pango text measurement 26%**
  (`pango_layout_get_size`; HarfBuzz shaping 16%), reached through
  height-for-width in `gtk_box_layout_compute_opposite_size`; and
  **`gtk_popover_size_allocate` → `gtk_popover_update_shape` 17%**, which
  repaints a popover's shape mask with cairo/pixman on every allocation.
  `adw_clamp_layout_*` only passes calls through.
* **Recording method changed.** sysprof now records marks only
  (`--no-perf`), with `perf record -X perf` inside it for stacks: perf
  tracks mappings reliably, gives Python names, and needs no polkit.
  Tested end to end — GTK and GLib marks arrived from the Python process
  through `perf` and `xvfb-run`, and perf captured the stacks with `py::`
  frames. `controlled-run.sh` uses this mode now. `window_cpu.py` now
  warns when more than 5% of samples have an unmapped innermost frame
  (36% for C3), instead of silently moving their time to the caller.

### 2. T3: every API response is parsed twice and serialised once

**Tried.** Broke down run B's samples under `MastodonClient._on_response`
by Python function, then read `src/mastodon.py:668-727`.

**Result.** Inside `_on_response`, `json.loads` is 47% and **`json.dumps`
27%**. The handler parses the body (`mastodon.py:684`), re-encodes the
parsed object into a `GLib.Variant` string to pass it through the
`Gio.Task` (`:693`), and `_envelope` parses that string again when the
caller finishes the task (`:723`). This has been the design since the
initial commit. Timed with `timeit` on fixture pages: a 20-post page
(59 KB) costs 0.25 ms to parse, 0.22 ms to re-encode and 0.26 ms to parse
again; a 40-post page (110 KB) 0.49 / 0.43 / 0.50 ms. **Two thirds of
the JSON work is redundant** — about 0.5–0.9 ms per fixture page, more on
real instance pages, which are larger. Over run B the redundant part was
about 45 ms (`json.dumps` under `_on_response` plus `_envelope`'s 26
samples).

The redundancy is not what makes an HTTP completion take 27–35 ms. Of
run B's 273 samples under `complete_in_idle_cb`, `_on_response` is
27.5%; the rest is the image pipeline: `imagecache.Fetcher._on_loaded`
(15.8%), `mediacache._decode_async.<locals>.done` (8.8%), and C-level
callbacks with no Babble Python on the stack (41.4%).
`mediacache._display_scale` is 5.9% of those samples but only 16 ms over
the session — per-call PyGObject overhead.

**What it changes.** A fix is cheap and safe: return the parsed object
through the task (`GObject.Value(GObject.TYPE_PYOBJECT, …)`, the way
`mediacache._decode_async` already returns its result) and have
`_envelope` accept it, keeping the string form for any fake in the tests.
Expected gain: the redundant ~2/3 of response JSON time, small in total.
T3's long completions need the new recording (timeline-only, with perf
stacks) to separate API pages from image work.

### 3. T2: what card building spends its time on

**Tried.** Broke down run B's 290 samples (about 290 ms) under
`statusrun._populate_pending` by Python and C function.

**Result.** Python, inclusive: `StatusRunRow._populate` 99%, of which
`StatusRow.__init__` 58% (27% in its own body), `StatusRow.bind` 39%
(`_bind_status` 25%), `StatusBody.__init__` 15.5%,
`InlineEmojiText.__init__` 15% (14% in its own body),
`_bind_carousel` → `BoostCarousel.set_boosts` → `_BoostCard.__init__`
13%, `StatusActions.__init__` 10% (`_action_button` 8%), `_render_body`
7%, `parse_content` 5% (HTML parsing 4%), `MediaBox.set_media` 4%,
`StatusMenuButton.__init__` 3%. C, inclusive (construction plumbing
hidden): `gtk_widget_init` 11%; `Gtk.TextView` setup 6.6%
(`ensure_layout`, `set_vadjustment`, `get_first_para_iter`); a Pango
context per widget 6.2%; CSS style creation about 6%; an accessibility
context per widget (`gtk_at_spi_create_context`) 5.2%;
`AdwAvatar.set_text` → `update_font_size` 4%.

**Reading.** The cost is spread across many small widgets rather than one
hot spot: each card builds a `Gtk.TextView` for its body, an action bar
of buttons, and — for boosts — carousel cards, each widget paying GTK's
per-widget setup (CSS node, Pango context, accessibility context). The
levers are fewer widgets per card, or building them across several idle
dispatches with a time budget instead of one whole run per dispatch.

### 4. T1: the popover in layout is not the timeline's

**Tried.** Found the parents of `gtk_popover_size_allocate` in run B.

**Result.** All 106 samples (about 106 ms) are in a popover's own surface
layout (`surface_layout_cb` → `maybe_request_motion_event`), so a popover
was open — a menu, for example — not something inside the timeline's
allocation. GTK redraws the popover's shape mask with cairo on each of its
layouts. Not timeline-related; low priority.

### 5. E5: the glycin work is Babble's own decode path

**Tried.** Followed run B's 818 samples with `libglycin` on the stack to
their callers, and measured the loader processes.

**Result.** The callers are GLib worker-pool threads in
`gdk_pixbuf_loader_close` — the `GdkPixbuf.PixbufLoader` branch of
`mediacache._decode_covering`, taken for images larger than their box
(see `PERFORMANCE.md` on decoding to size) — and nested
`g_main_loop_run`s on those threads while they wait for the sandboxed
loader to reply. The earlier statement that "Babble's decode stacks barely
touch gdk-pixbuf" was wrong: stacks do not unwind through glycin's Rust
code back up to the Python frames, so the Python decode functions were
missing from those samples. In run B the loader processes used 1,686
samples (6.4% of all CPU recorded, about 1.7 s), nearly all inside the
`glycin-image-rs` binary — the decoding itself — and the in-process client
side (worker threads, IPC, `blocking-N` and `gly-global-exec` threads)
about 0.8 s more.

**What it changes.** Every oversized avatar or media decode is an IPC
round trip to a sandboxed process plus glycin's thread pool, on top of
the decode. The fast path (`Gdk.Texture.new_from_bytes`, for images
already small enough) uses GTK's own in-process loaders. Whether a
different scaled-decode route would be cheaper overall is open; it would
have to keep the memory bound the scaled decode exists for.

### 6. The controlled runs were signed out: an empty `XDG_CONFIG_HOME` hides dconf

**Found.** Every controlled run needed a browser sign-in. dconf's user
database is `$XDG_CONFIG_HOME/dconf/user`, so the empty config directory
used to hide `~/.config/gtk-4.0/gtk.css` also hid every stored account
and setting. (The earlier claim here that accounts were unaffected
because they live in GSettings and the keyring was wrong: GSettings is
dconf, and dconf is under `XDG_CONFIG_HOME`. The token itself is in the
keyring, reached over D-Bus, and was never the problem.)

**What it changes.** C1–C3 measured fresh, signed-out sessions under
default settings, including a sign-in at the start — still valid for the
timeline's steady behaviour, but not the user's configuration. The run
script now builds a config directory of symbolic links to everything in
`~/.config` except `gtk-4.0/gtk.css*`; checked that GSettings sees the
stored account through it. For the benchmark harness the advice stands
as it was: it already uses `GSETTINGS_BACKEND=memory`, so hiding the
user config there costs nothing.

### Still open

* **T1** needs a timeline-only recording with the new method to say what
  runs inside the slow layout frames after posts arrive.
* **T4** (blocked layout) needs an off-CPU view: sysprof's `--scheduler`
  fails on this kernel (above), and perf's scheduler tracepoints need
  `perf_event_paranoid=-1` or root. Next recording:
  `perf record --off-cpu` or `-e sched:sched_switch` with root.
* **V1** (first web view) has not been re-measured under controlled
  conditions.

## Appendix: marks, counters and file formats

### Marks seen in Babble's process

GTK: `Frameclock cycle`, `Frameclock update`, `Frameclock layout`,
`Frameclock paint`, `Widget snapshot`, `Widget render`, `Validate CSS`,
`Event` (message names the event, `button-release {…}`),
`frameclock frozen` (waiting for the compositor; not work),
`Create Vulkan pipeline`, `Save Vulkan pipeline cache`, `Load jpeg`/`png`,
`CSS theme load`, `Init OpenGL`, `Presented window`, `Wayland frame event`.
GLib: `GSource.dispatch` (message `source name ⇒ keep|destroy`),
`GSource.prepare`/`check`, `g_main_context_iterate`, `g_source_attach`,
`GThread created`. Pango: `FcFontSetMatch`, `wait for FcFontSort`.
WebKit (UI): `ProcessLaunch`, `HandleMouseEvent`, `DisplayLinkUpdate`.
Spelling: `Check`.

### Counters

System (pid −1): per-CPU and combined CPU percent, memory
(Used/Available/Cached/…), disk, network, battery, the sampler's lost
samples. Babble's process: GTK `fps`, `frame pixels`, `buffer-uploads`
(cumulative), `invalidated-nodes`, `created-styles`; GIO
`task pool max size`, `tasks running`.

### Capture format, for `syscap.py`

A 256-byte file header (`magic 0xFDCA975E`, start and end times), then
8-byte-aligned frames with a 24-byte header: `len:u16, cpu:i16, pid:i32,
time:i64 (CLOCK_MONOTONIC ns), type:u8`. Types used here: 2 sample
(`n_addrs:u16, tid:i32, addrs[]`), 3 map (`start, end, offset, inode,
filename`), 4 process (`cmdline`), 5 fork (`child_pid`), 8 counter
definitions (128 bytes each: `category[32] name[32] description[52]
id:24 type:8 value`), 9 counter values (groups of 8 ids then 8 values),
10 mark (`duration:i64, group[24], name[40], message`). Timestamps share
`CLOCK_MONOTONIC` with Python's `time.monotonic_ns()` and with
`perf record -k CLOCK_MONOTONIC`.
