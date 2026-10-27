# To do

Follow-ups from the timeline performance work. The evidence and the
method are in `Docs/performance-profiling.md` (investigation log, entries
10–15); measure each change the way those entries do — alternating runs
with and without it, frame counts checked first.

## Harden `bench/profiling/controlled-run.sh`

Three of the runs on 2026-10-18 were throttled by the compositor (about
39 fps, 880–1,360 frames instead of 2,300–3,700) and had to be thrown
away (entry 12). The screen dims when idle, and a window over Babble's
throttles it too.

* Keep the screen awake for the run: wrap the recording in
  `gnome-session-inhibit --inhibit idle`.
* Warn, or exit non-zero, when a run's frame count or the duration of its
  `frameclock frozen` marks shows it was throttled.
* Support alternating runs (change / control, with the change stashed
  between them) so the timeline's content drift falls on both sides.
