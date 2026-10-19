# To do

Follow-ups from the timeline performance work. The evidence and the
method are in `Docs/performance-profiling.md` (investigation log, entries
10–15); measure each change the way those entries do — alternating runs
with and without it, frame counts checked first.

## Lighter notification rows

`NotificationRow.__init__` builds a `NotificationPost` (a post preview with
its byline, content, poll and media boxes) and the cluster widgets (avatar
stack, people box, revealer, expander) for every row, hidden when the
notification does not need them. Build the post on the first `bind` that
has a status, and the cluster widgets on the first merged notification.

Why: since the list gets its model only when first shown (entry 15),
the first open of the notifications page builds its rows in one frame —
36 ms against 12 ms before, 67 ms to the first painted rows. Lighter rows
shrink that frame, and every later open and scroll.

## `_stamp_sections` on every change

`NotificationModel._stamp_sections` walks the whole store, with a
`strptime` per section, on every `items-changed`; `prepend_page` inserts
one item at a time, so a refresh of k notifications walks it k times. It
is most of what is left of the notifications code in HTTP completions
over 5 ms (entry 15). Stamp only the rows around the change, or splice a
refresh in one call.

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
