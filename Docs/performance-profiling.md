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
| T1 | Layout after new posts is the main source of slow frames: GTK measuring row text | timeline | D4–D6: layout dominates 67–75% of slow frames; inside them 98% GTK, ~55% Pango text measurement (log, entry 7). The C1–C3 function breakdown was an artefact (entry 1) | high | investigating which widgets |
| T2 | Card-population idles (`_populate_pending`) run over the frame budget | timeline | D4–D6: idle p99 18.8 ms, max 26 ms; `_BoostCard.__init__` and avatar initials layout (`update_font_size`) lead (log, entries 3, 7) | high | investigated; fix options listed |
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
3. flicks down 30 times (D4–D6 used 12) — each flick moves the list 0.9 of a screen over
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

For runs recorded with both layers (`runN.syscap` + `runN.perf.data`):

```sh
DEBUGINFOD_URLS=https://debuginfod.archlinux.org \
    python3 perf_windows.py ~/babble-profiles/run{4,5,6}
```

finds the same three kinds of window in the marks and attributes the
main thread's perf samples inside them to Babble Python functions and C
functions. Both files share `CLOCK_MONOTONIC` and the process ID.

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

### The reference baseline: `main` at `81fa874`

Measure every change against this. Recorded with
`bench/profiling/controlled-run.sh 10|11|12` on the code merged in PR #35
(`81fa874`, "Revert the home cache, and make paging and image fetches
reliable"): the scripted `timeline` scenario (30 flicks, waiting at the
end of the loaded list for the next page), the stored account, the user
stylesheet hidden, translucent sidebar off, `performance` power profile,
sysprof marks plus perf stacks. Files: `~/babble-profiles/run1{0,1,2}.*`.

| Metric | B10 | B11 | B12 | Median [min–max] |
|---|---:|---:|---:|---|
| Scenario length (s) | 68.1 | 67.3 | 66.2 | |
| First page (s after the driver starts) | 1.50 | 1.81 | 1.69 | |
| Posts loaded at the bottom | 82 | 83 | 76 | |
| Frames | 3,728 | 3,697 | 3,574 | |
| Frame p50 (ms) | 1.09 | 1.22 | 1.19 | |
| Frame p95 (ms) | 4.64 | 4.17 | 4.60 | **4.60** [4.17–4.64] |
| Frame p99 (ms) | 6.03 | 6.54 | 6.19 | **6.19** [6.03–6.54] |
| Frame max (ms) | 44.6 | 44.0 | 42.7 | |
| Frames over 6.94 ms | 0.70% | 0.70% | 0.84% | **0.70%** [0.70–0.84] |
| Dispatches ≥ 50 ms | 0 | 0 | 0 | **0** |
| Card-population idle p99 / max (ms) | 10.33 / 18.9 | 9.56 / 16.0 | 9.57 / 17.1 | p99 **9.57** [9.56–10.33] |
| HTTP completion p99 / max (ms) | 0.18 / 31.7 | 0.20 / 37.1 | 0.17 / 37.4 | p99 **0.18** [0.17–0.20] |

Slow frames were dominated by layout in 50–58% of cases, by
unattributed time (no single phase over 40%) in 35–47%, and by CSS
validation in 3–8%.

Inside the slow windows (`perf_windows.py`, all three runs):

* *Slow layout frames* — 45 windows, 1,076 ms, 765 main-thread samples:
  98.8% with no Babble Python on the stack; Pango text measurement 61%
  inclusive, height-for-width (`get_width_for_height`) 35%; CSS selector
  matching leads the self time.
* *Card-population idles over budget* — 23 windows, 284 ms, 237 samples:
  `_BoostCard.__init__` 30.0%, `StatusRow.__init__` 9.7%,
  `parse_content` 5.9%, `InlineEmojiText.__init__` 4.6%; C:
  **`update_font_size` 23.6%** (`AdwAvatar.set_text` 16.0%), CSS style
  creation about 17%, a Pango context per new widget 11%.
* *HTTP completions over 5 ms* — 30 windows, 304 ms, 307 samples:
  `_on_response` 19.9% (JSON parse 13%, re-encode 6.8%), `entities.lift`
  6.8%, `_envelope` 6.2%; the notifications page building rows inside
  completion callbacks (`NotificationRow`, `NotificationPost`,
  `_stamp_sections`, `_build_header`) about 12%; list-factory `setup`
  16% and `items-changed` handling 35.5% inclusive.

Reproduce a comparison run with:

```sh
bench/profiling/controlled-run.sh N
cd bench/profiling
python3 timeline_report.py ~/babble-profiles/run{10,11,12,N}.syscap
DEBUGINFOD_URLS=https://debuginfod.archlinux.org python3 perf_windows.py ~/babble-profiles/runN
```

Single runs agree closely here (frame p95 within 0.5 ms), but record
three after a change and compare medians.

### Earlier runs: C1–C3

All figures from `timeline_report.py` on C1–C3. Budget 6.94 ms (144 Hz).
These predate the scripted driver and PR #35, ran signed out (log,
entry 6), and their CPU stacks are unusable (entry 1); they are kept for
the record only.

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

### 7. New baseline: scripted runs D4–D6, with marks and complete stacks

**Done.** After the timeline cache was unwired and a loading issue fixed
(by the maintainer, not part of this investigation), three runs were
recorded with `controlled-run.sh 4|5|6`: the scripted `timeline` scenario,
the stored account, sysprof marks plus perf stacks, 12 flicks. They
supersede C1–C3 (manual steps, signed out, different code), and are in
turn superseded as the baseline by the 30-flick D7–D9 (entry 8).

The scenario repeated closely: first page at 1.10 / 1.30 / 1.30 s, the
scroll-down phase ending at 15.51 / 15.74 / 15.70 s, 36 / 36 / 37 posts
loaded at the bottom, the same scroll position there (8,544 px), complete
at 31.35 / 31.54 / 31.49 s. The window came out 1373×838 (the compositor's
choice, logged each run), not the 1280×900 requested.

`timeline_report.py` (median [min–max] across the three):

| Metric | D4 | D5 | D6 | Median [min–max] |
|---|---:|---:|---:|---|
| Frames | 1,666 | 1,450 | 1,492 | |
| Frame p95 (ms) | 3.91 | 1.96 | 1.88 | 1.96 [1.88–3.91] |
| Frame p99 (ms) | 6.93 | 5.27 | 4.90 | 5.27 [4.90–6.93] |
| Frame max (ms) | 47.8 | 46.2 | 37.9 | |
| Frames over 6.94 ms | 0.90% | 0.76% | 0.54% | 0.76% [0.54–0.90] |
| Dispatches ≥ 50 ms | 0 | 0 | 0 | 0 |
| Card-population idle p99 / max (ms) | 19.6 / 26.0 | 18.8 / 25.3 | 13.5 / 15.3 | p99 18.8 [13.5–19.6] |
| HTTP completion p99 / max (ms) | 0.55 / 35.7 | 0.51 / 35.9 | 0.66 / 37.8 | p99 0.55 [0.51–0.66] |

Slow frames were dominated by layout in 67–75% of cases.

**Inside the slow windows** (`perf_windows.py`, all three runs):

*Slow layout frames* — 24 windows, 567 ms, 396 main-thread samples.
**98% have no Babble Python on the stack.** About 55% is Pango measuring
text (`gtk_layout_manager_measure` → `pango_layout_get_extents_internal` /
`pango_layout_get_size`), reached through the window-wide allocation chain;
the top self-time functions are HarfBuzz positioning (`GPOS` pair
adjustment) and CSS selector matching (`gtk_css_selector_tree_get_change`,
`gtk_css_selector_tree_match`). `adw_clamp_layout_measure` is 35%
inclusive as a pass-through to the list. `InlineEmojiText.do_measure` is
1%. So the slow layout frames are GTK measuring the text of rows as they
come into view.

*Card-population idles over budget* — 10 windows, 155 ms, 102 samples.
Innermost Babble functions: `_BoostCard.__init__` 23.5%,
`StatusRow.__init__` 10.8%, `InlineEmojiText.__init__` 5.9%, and the first
import of `statusrow` and `mediabox` (4.9% and 2.9%, once per session,
because `_populate` imports `StatusRow` lazily). C: Pango text measurement
29%, **`AdwAvatar.set_text` → `update_font_size` 20.6%**, and a read-only
`Gtk.TextView` computing its input-method cursor spot when mapped
(`gtk_text_view_update_im_spot_location` → `gtk_text_layout_get_cursor_locations`)
17.6%.

*HTTP completions over 5 ms* — 15 windows, 221 ms, 227 samples. These are
mostly **not the timeline**: the notifications page builds its rows
inside the completion callback (`NotificationRow.__init__` 11%,
`NotificationModel._stamp_sections` 3%, `NotificationsPage._build_header`
2.6%; the list factory's `setup` 35% inclusive). The API handling itself is
`_on_response` 17.6% (JSON parsing 9.7%), `_envelope` 4.4%, `entities.lift`
4.4%. `update_font_size` appears here too (15.4%).

**`update_font_size`, read.** libadwaita 1.10's `update_font_size`
(`adw-avatar.c:199`) measures the initials with Pango unless the avatar
already has a custom image, initials are off, or the text is empty. Babble
sets the text first and the image later on both paths: `_BoostCard`
creates `Adw.Avatar(size=24, show_initials=True, text=author)`
(`src/mediabox.py:164`), and `AvatarCache.load` (`src/mediacache.py:60-73`)
starts with `set_custom_image(None)` and applies the texture from the
fetch callback — even when it is already cached. Every card therefore
lays out initials it may never show.

**Fix options (not applied).**

* Avatars: apply a cached texture *before* setting the text — look it up
  in `AvatarCache.store` first, `set_custom_image(texture)`, then
  `set_text()` — so `update_font_size` returns early; leave the fallback
  order as it is for avatars that really have to be fetched.
* Card building: split `_populate_pending`'s work so one dispatch builds
  at most one `StatusRow` (or stops after about 4 ms), and build boost
  cards lazily — `_BoostCard.__init__` is the single largest Babble
  function in the over-budget idles.
* Read-only bodies: check whether the read-only `InlineEmojiText` can
  avoid the input-method spot update on map (it already pins
  `gtk-im-context-simple`; see the conventions in `CLAUDE.md`).
* Notifications: build rows outside the completion callback, the way the
  timeline already does through `_populate_pending`.
* JSON: return the parsed object through the task instead of a
  re-encoded string (entry 2).

### 8. Longer runs D7–D9 (30 flicks): the current baseline

**Done.** The default scenario was lengthened from 12 to 30 flicks so a
run reaches further down the timeline, and three runs were recorded
(`controlled-run.sh 7|8|9`). D4–D6 stay as the 12-flick reference.

All three completed in 65.5–65.7 s. They diverged at the bottom — 76, 82
and 64 posts loaded — because in D9 the list ran out of loaded posts
before the last flick and the next page arrived only during the scroll
back up. The driver waited for a page only when a fetch was already in
progress at the end of a pause. It now also waits, on the way down,
whenever the list is at the end of what is loaded and the timeline is
not exhausted, until the list grows or `--page-timeout` (20 s) fails the
run. D7–D9 were recorded before that change.

| Metric | D7 | D8 | D9 | Median [min–max] |
|---|---:|---:|---:|---|
| Posts at the bottom | 76 | 82 | 64 | |
| Frames | 2,900 | 3,409 | 3,459 | |
| Frame p95 (ms) | 2.00 | 4.84 | 4.63 | 4.63 [2.00–4.84] |
| Frame p99 (ms) | 4.68 | 6.46 | 6.38 | 6.38 [4.68–6.46] |
| Frame max (ms) | 44.4 | 46.1 | 44.3 | |
| Frames over 6.94 ms | 0.62% | 0.88% | 0.87% | 0.87% [0.62–0.88] |
| Dispatches ≥ 50 ms | 0 | 0 | 0 | 0 |
| Card-population idle p99 / max (ms) | 9.97 / 15.9 | 9.50 / 16.8 | 10.53 / 16.7 | p99 9.97 [9.50–10.53] |
| HTTP completion p99 / max (ms) | 0.19 / 33.5 | 0.16 / 33.2 | 0.19 / 33.2 | p99 0.19 [0.16–0.19] |

Slow frames were dominated by layout in 50–83% of cases.

Inside the slow windows, the same picture as D4–D6, over twice as many
windows:

* *Slow layout frames* (47 windows, 1,073 ms, 771 samples): 98% no Babble
  Python; Pango text measurement about 60% inclusive
  (`pango_layout_get_size`), height-for-width (`get_width_for_height`)
  36%.
* *Card-population idles over budget* (22 windows, 274 ms, 242 samples):
  `_BoostCard.__init__` 24.8%, `StatusRow.__init__` 9.9%,
  `parse_content` 9.5%, `InlineEmojiText.__init__` 3.7%; C:
  `update_font_size` 18.2%, the read-only `TextView`'s input-method spot
  update 8.7%.
* *HTTP completions over 5 ms* (28 windows, 303 ms, 315 samples):
  `_on_response` 18.7%, `_envelope` 8.3%, `entities.lift` 7.3%; the
  notifications page building rows inside completion callbacks
  (`NotificationRow`, `NotificationPost`, `_stamp_sections`) about 10%;
  the list factory's `setup` 15% and `items-changed` handling 32%
  inclusive.

### 9. The reference baseline on `main` (`81fa874`)

**Done.** PR #35 merged; with no other work in progress, runs B10–B12
were recorded on it with the corrected driver. The tables are in
[the reference baseline](#the-reference-baseline-main-at-81fa874).
The end-of-list wait worked: B10 paused twice at the bottom of the loaded
list (700 and 1,400 ms) until the next page arrived. The three runs
reached 82, 83 and 76 posts; the remaining spread is content (posts of
different heights over the same 30 flicks), not the driver. Run to run,
frame p95 varied by under 0.5 ms and the over-budget share by 0.14
points, so a change of a few tenths of a millisecond at p95 or p99 is
within noise; the card-population and HTTP-completion maxima move by a
few milliseconds between runs.

The working tree's `src/` and `data/` were identical to `81fa874` (`git
diff HEAD origin/main -- src data` empty); only the profiling tools and
this document had local changes.

### 10. Avatars: a cached image before the initials — no effect, reverted

**Tried.** libadwaita 1.10's `update_font_size` (`adw-avatar.c:199`) runs
on `set_text`, `set_size`, `set_show_initials` and on every
`notify::root` — each time an avatar is attached to the window — and
returns early only when a custom image is already set;
`set_custom_image` itself never measures. `AvatarCache.load` gained a
`text` argument and applied a texture already in `AvatarCache.store`
before setting it, and the 20 call sites that built
`Adw.Avatar(text=…)` and then called `load` passed the text to `load`
instead. A test confirmed that a cached avatar's internal label then
carries no size attribute (the measurement never ran), and that the
attribute is there with the old order. `StatusRow`'s own 46 px avatar
was never affected: it is given no text.

**Result.** Runs B20–B22 (`controlled-run.sh 20|21|22`), against B10–B12:

| Metric | B10–B12 | B20 | B21 | B22 | After, median [min–max] |
|---|---|---:|---:|---:|---|
| Frame p95 (ms) | 4.60 [4.17–4.64] | 2.44 | 4.41 | 4.01 | 4.01 [2.44–4.41] |
| Frame p99 (ms) | 6.19 [6.03–6.54] | 4.76 | 5.96 | 5.31 | 5.31 [4.76–5.96] |
| Frames over 6.94 ms | 0.70% [0.70–0.84] | 0.56% | 0.66% | 0.78% | 0.66% [0.56–0.78] |
| Card-population idle p99 (ms) | 9.57 [9.56–10.33] | 10.04 | 9.81 | 9.74 | 9.81 [9.74–10.04] |
| Card-population idle max (ms) | 16.0–18.9 | 27.6 | 22.6 | 23.7 | |
| `update_font_size` in over-budget idles | 23.6% | | | | **26.4%** (24 windows) |

The target did not move: `update_font_size` is still a quarter of the
over-budget card-population idles, `adw_avatar_set_text` 17.3% of them.
When a boost card or a quote is built, its author's avatar is almost
never cached yet — boosted authors are mostly new to the session — so
the fallback path, text before image, is the common one. Reverted; the
patch is not kept.

**What it changes.** The initials are measured because they really are
shown until the picture arrives. Avoiding the cost means not showing
initials meanwhile (`show_initials=False` until a fetch fails — a
visible change), or fewer avatars per card build (entry 3, boost cards).

**Also found: a first-flick layout frame of 74–92 ms.** Every run of
B20–B22 and B30–B32 has one frame-clock dispatch over 50 ms, 130–150 ms
after the driver's first flick, while the list grows from 9 to 24
items; B10–B12 have none (their equivalent is 32–33 ms, about 1 s after
the flick). Layout is 92–93% of it, with repeated `Pango wait for
FcFontSort` marks of about 3 ms each, and its samples are HarfBuzz
shaping and fontconfig sorting with no Babble Python on the stack. It
is font fallback for whatever the home timeline held that day, not
either change. The "⇒ destroy" in the report's label is the frame-clock
source's destroy notifier, on every frame-clock dispatch, not a widget
being destroyed. The same content drift shows in frame counts (2,8xx–
3,4xx against 3,5xx–3,7xx), so frame percentiles are compared with care
from here on.

### 11. JSON: each API response parsed once

**Done.** `MastodonClient._on_response` returns the parsed envelope
through the task as `GObject.Value(GObject.TYPE_PYOBJECT, …)`, as
`mediacache._decode_async` returns its texture, instead of re-encoding it
into a `GLib.Variant` string; `_envelope` returns it as is and still
parses the string form, which the transport test's fake hands back. A new
check in `client transport` drives `_on_response` through a real
`Gio.Task` to `request_finish_paged` and counts `json.loads` calls: one,
against two before.

**Result.** Runs B30–B32 (`controlled-run.sh 30|31|32`). Shares are
main-thread perf samples whose stack contains the function (inclusive);
"JSON C" is any of the `json` accelerator's scanner or encoder
functions.

| Metric | B10–B12 | B30 | B31 | B32 | After, median [min–max] |
|---|---|---:|---:|---:|---|
| HTTP completions > 5 ms (all runs) | 30 windows, 304 ms | 5 | 4 | 4 | **13 windows, 201 ms** |
| `_on_response` in them | 19.9% | 11.6% | 2.6% | 11.3% | **8.2%** |
| `_envelope` in them | 6.2% | 0 | 0 | 0 | **0** |
| JSON C in them | 19.9% | 4.3% | 1.3% | 3.2% | **2.9%** |
| JSON C samples, whole session (3 runs) | 82 | 10 | 10 | 8 | **28 (−66%)** |
| HTTP completion p99 (ms) | 0.18 [0.17–0.20] | 0.16 | 0.18 | 0.15 | 0.16 [0.15–0.18] |
| HTTP completion max (ms) | 31.7–37.4 | 34.8 | 42.8 | 35.3 | |
| Frame p95 (ms) | 4.60 [4.17–4.64] | 2.36 | 1.93 | 1.94 | 1.94 [1.93–2.36] |
| Frame p99 (ms) | 6.19 [6.03–6.54] | 4.91 | 4.55 | 4.52 | 4.55 [4.52–4.91] |
| Frames over 6.94 ms | 0.70% [0.70–0.84] | 0.57% | 0.48% | 0.52% | 0.52% [0.48–0.57] |
| Card-population idle p99 (ms) | 9.57 [9.56–10.33] | 10.58 | 9.98 | 9.84 | 9.98 [9.84–10.58] |

The redundant two thirds of the JSON work is gone, as entry 2
predicted, and with it more than half of the HTTP completions over 5 ms.
The frame percentiles are lower too, but that is not this change: JSON
runs in the completion callback, not in the frame clock, and B20–B22 —
on the old JSON path — already spread from 2.44 to 4.41 ms at p95 on
the same day's content (entry 10). The worst completion, 35–43 ms, is
unchanged; it is not JSON. What is left in the slow completions:
`NotificationRow.__init__` 10.6%, `NotificationPost.__init__` 5.8%,
`_stamp_sections` and `_build_header` 4.3% — the notifications page
building rows in the callback — `entities.lift` 5.3%, and
`update_font_size` 16.9%, again through the notification rows' avatars.

### 12. Boost cards built after the first three — target met, frames lost, reverted

**Tried.** `BoostCarousel.set_boosts` built at most `BOOST_CARDS_EAGER`
(3) new cards at once, about a column's width; the rest waited in a tail
built one card per idle dispatch while the carousel was mapped (a
module-level queue of GObject weak refs, like `statusrun`'s
`_pending_shells`), reusing existing cards and keeping strip order. Two
variants: (a) each card added to the strip as it was built; (b) the
waiting cards built off the strip and added together once all were
built. A widget test pinned the order, reuse, mapping and freeing.

**Result.** Runs B40, B41, B44 for (a) and B47, B48 for (b). B42, B43
and B46 were throttled by the compositor (see below) and are left out.

| Metric | B10–B12 | B30–B32 (same day, no change) | (a) B40/41/44 | (b) B47/48 |
|---|---|---|---|---|
| Card-population idle p99 (ms) | 9.57 [9.56–10.33] | 9.98 [9.84–10.58] | **5.41** [5.19–6.21] | **5.25** [5.25–5.25] |
| Over-budget idles (all runs) | 23 windows, 284 ms | | 12, 165 ms | |
| `_BoostCard.__init__` in them | 30.0% | | 9.1% | |
| Slow layout frames (all runs) | 45 | 39 | **61** | **41** (2 runs) |
| Frame p99 (ms) | 6.19 [6.03–6.54] | 4.55 [4.52–4.91] | 6.27 [5.81–6.85] | 7.17, 8.32 |
| Frames over 6.94 ms | 0.70% | 0.52% | 0.81% | 1.06%, 1.32% |

The target moved: the idle p99 fell by 4 ms, below the frame budget. But
the work moved into frames. With (a), 31 of the 61 slow layout frames
start within 40 ms after a dispatch that added a card, against none when
the same times are shifted by 300 ms; each costs 18–20 ms. A card added
to a shown strip queues a resize, and the next frame lays the whole row
out again — Pango measuring all its text. Over three runs that is about
580 ms of frames over budget to save about 120 ms of idle time. With
(b), one relayout per carousel instead of one per card, 5–7 slow frames
per run still follow the dispatch that adds the cards, now about 36 ms
each. Either way the reader gets dropped frames while scrolling in
exchange for shorter idles they never see. Reverted.

**What it changes.** Anything that adds widgets to a row already on
screen pays for a relayout of that row in the next frame, and the row's
text measurement is the dominant cost of layout (entry 7). Spreading
card building over several dispatches is therefore not free: fix 4 —
bounding `_populate_pending` to one `StatusRow` per dispatch — would add
rows to a shown shell one at a time and is likely to make the same
trade. A build that is cheaper, or finished before the row's first
layout, does not have this problem; one that is deferred does.

**Measurement: the compositor throttled some runs.** B42, B43 and B46
had 880–1,360 frames instead of 2,300–3,700: from some point on, each
flick got 5–13 frames instead of about 30 (about 39 fps), the waits for
the compositor (`frameclock frozen` marks) were 30–60 ms instead of
10–15 ms, and a 211 ms dispatch in B42 had no CPU samples at all. In B43
it started at 0.7 s, before any timeline content existed, so it is not
Babble. The screen dims when idle (`idle-dim`, `idle-delay` 300 s) and
these runs ran unattended for longer than that, so B44–B48 were recorded
under `gnome-session-inhibit --inhibit idle`; B46 was throttled
nonetheless, so dimming is not the only cause — a window covering
Babble's is another candidate. Check the frame count of every run before
comparing its percentiles.

### 13. Constant-size boost cards

**Why.** Entry 12 showed that deferring card building trades idle time
for layout frames. The other lever is making cards cheaper to lay out.
In GTK 4.24.1 a resize always propagates to the window
(`gtk_widget_queue_resize`, `gtkwidget.c:3741`); nothing stops it when
the measured size is unchanged. But each ancestor that has to measure
again only re-measures children at sizes it has not cached. A box sized
height-for-width can measure its wrapping children at 10–20 widths
(`distribute_remaining_size`, `gtkboxlayout.c`), each one a Pango
re-wrap. A child that is constant-size is measured once, at -1.

**Done.** Every part of a `_BoostCard` is constant-size, so the card's
request mode is `CONSTANT_SIZE`:

* the three-line summary is a `Gtk.Inscription` (`nat_lines=3`,
  ellipsized), measured from font metrics, never by wrapping its text;
* the author's name stays a one-line ellipsized `Gtk.Label`, already
  constant-size, with `max_width_chars=1` so a long name no longer widens
  its card past `BOOST_CARD_WIDTH` (one did, to 307 px);
* the thumbnail sits as an unmeasured overlay on a fixed 96 px slot, and
  is a `_SlotPicture` that reports `CONSTANT_SIZE`: a container takes its
  request mode from all its children, overlays included, and a plain
  `Gtk.Picture` is height-for-width. A texture landing no longer changes
  what the card asks for.

A first version also made the name an Inscription. It was dropped: an
Inscription builds its Pango context and layout in its constructor
(`gtkinscription.c:821`) and again when styled, where a label waits until
it is measured, so it costs more to create.

`boost card` checks that a card is constant-size with or without a
thumbnail, the same height whatever its text, unchanged when a picture
lands, and exactly `BOOST_CARD_WIDTH` wide.

**Result.** Six runs recorded alternately, change and control, so that
content drift falls on both: B60, B62, B64 with the change; B61, B63,
B65 without. All healthy (2,590–2,833 frames).

| Metric | Control B61/63/65 | Change B60/62/64 |
|---|---|---|
| Slow layout frames (all runs) | 37 windows, **1,022 ms**, 732 samples | 36 windows, **777 ms**, 488 samples |
| Frame p99 (ms) | 4.68 [4.65–4.70] | 4.43 [4.25–4.62] |
| Frames over 6.94 ms | 0.54% [0.48–0.57] | 0.50% [0.46–0.52] |
| Frame max (ms) | 60.8–64.4 | 49.1–51.8 |
| Card-population idle p99 (ms) | **10.98** [10.83–11.15] | **11.92** [11.83–12.07] |
| Over-budget idles (all runs) | 24 windows, 327 ms | 23 windows, 320 ms |
| `_BoostCard.__init__` in them | 28.0% | 33.6% |

Time in slow layout frames fell by 24%, as it did in the Inscription-only
set recorded an hour earlier (B53–B55: 760 ms against 1,023 ms for
controls B56–B58), and the worst frame by about 12 ms. Over-budget frames
and frame p99 moved less than run-to-run noise. The card-population idle
p99 got 0.9 ms worse, with the same total over-budget idle time: the
summary's Inscription builds its layout up front. Kept, for the frames;
the idle target of the original fix 3 is not met.

### Still open

* **T1**: the slow layout frames are GTK measuring row text (entry 7).
  Which widgets' text — and whether some measurements repeat — needs a
  per-widget count, for example with the GTK inspector's layout
  statistics or a test that counts measure calls per row type.
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
