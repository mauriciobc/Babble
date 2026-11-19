# Picture-in-Picture — UX specification

Interaction and wording contract for the PiP card (`src/mediapip.py`) and
its viewer entry points. Implementation facts (engine handover, teardown
order, registry) are in `Docs/pip-plan.md`; this file only fixes what DEV
builds and QA verifies. Working note, English only.

## 1. Interaction spec

States: `viewer` (media viewer showing a `video`/`gifv` stream), `card`
(PiP overlay playing the same stream), `none`.

Entry points, both visible/active only while a `video` or animated `gifv`
attachment is shown in the viewer:

- Header-bar button (`pip_button`, icon `babble-pip-in-symbolic`) in the
  media viewer chrome.
- The `p` key, bound while the viewer is open and showing eligible media.
  The key does exactly what the button does.

Transitions:

- **viewer → card (minimize).** Button or `p`. Playback moves to the card
  without pausing: same engine, same stream, same position. The viewer
  closes (handover close path — no pause, no detach, no release, no
  volume save). The card appears at the default position (bottom-right,
  24px margin) with no animation; focus does not move to the card.
- **card → viewer (expand).** The card's expand button. The card is
  removed from the overlay without stopping playback and the viewer
  reopens adopting the same engine and stream at the current position;
  nothing restarts. Focus returns to the viewer's media widget.
- **card close.** The card's close button. Stops playback and releases
  the engine through the normal deferred-release path. The card leaves
  the overlay.
- **window closes while the card is open.** The per-window registry
  closes the card with the window; the engine is released. Nothing is
  saved (position is per-window, in memory only).
- **a second media viewer opens while the card is open.** The card
  closes and releases its engine — one playing surface per window, the
  same rule `mediaviewer` already enforces. The inline GIFV player also
  stands down while a card exists.
- There is no second card: a card only exists while the viewer is closed,
  and the viewer can only be opened from the timelines.

Not spec'd here and not in v1: audio in PiP (nothing to watch), any
settings for size, position persistence, or always-on-top.

## 2. Card anatomy

```
                    card, base width 240px, height from the stream
                 ┌──────────────────────────────────────────┐
                 │                                          │
                 │            Gtk.Picture                   │
            ───► │        (contain fit, no letterbox        │
   corner snap   │         styling, click does nothing)     │
            ◄─── │                                          │
                 │          ┌──────────────────────┐        │
                 │          │ ⏸   ⤢   ✕            │        │ ◄─ OSD bar:
                 │          └──────────────────────┘        │    bottom-center
                 └──────────────────────────────────────────┘
                 ▲
                 └─ 24px margin from window edge on release
```

- **Video area.** A `Gtk.Picture` bound to the transferred
  `EngineStream`, `contain` fit. Clicks on the video area do nothing —
  no pause-on-tap; the OSD is the only control surface. No hover
  reveal, no auto-hide: the OSD bar is always present so the controls
  stay reachable for keyboard and screen-reader users.
- **OSD bar.** Overlaid at the bottom-center of the card, three icon
  buttons in order: play/pause (`media-playback-pause-symbolic` /
  `media-playback-play-symbolic`), expand (`babble-pip-out-symbolic`),
  close (`window-close-symbolic`). Each has a tooltip that doubles as
  its accessible label (`uihelpers.set_accessible_label`).
- **Drag and snap.** Drag from anywhere on the card, OSD buttons
  excepted. The card does not follow the pointer (GTK 4.24 offers no
  way to move a mapped widget per frame without reallocating all of
  the content behind it); a drag flicks it once to the nearest corner
  of the drag's direction, with a 24px margin, on release. A short
  drag (under 12px in both axes) is a tap and does not move it. The
  keyboard path is the same thing: with the card focused, the arrow
  keys move it between the corners. The default position is
  bottom-right. Position is remembered per window in memory only;
  nothing is tunable.
- **Width.** Base width ~240px; height follows the stream's aspect
  ratio, capped: a portrait video takes up to 360px of height and keeps
  the ratio, so the card never grows past the caps in either dimension.
  The card scales with no user sizing control in v1.

## 3. Wording table

Every user-visible string. Tooltips double as accessible names via
`set_accessible_label`, so one string per control. All strings pass the
`ui strings` rules: no generic action labels, US spellings, no
`e.g.`/`i.e.`, `…` never three periods. Tooltips follow the repo's
header-bar style (title case, no mnemonic underscores — see
`src/mediaviewerchrome.py`).

| Where | String | Rationale |
| --- | --- | --- |
| Viewer header-bar `pip_button` tooltip / accessible label | `Minimize to Picture-in-Picture` | Verb-first, says what the button does; matches the descriptive style of `Toggle Fit and Actual Size`. |
| Card play/pause tooltip / accessible label, playing | `Pause` | Names the action the press performs; the icon shows the action, not the state. |
| Card play/pause tooltip / accessible label, paused | `Play` | Same rule, opposite state. |
| Card play/pause accessible label, ended | `Replay` | After the stream ends the same button restarts it; a distinct name states that. |
| Card expand button tooltip / accessible label | `Expand` | Returns playback to the viewer; matches the plan's `restore` vocabulary without implementation slang. |
| Card close button tooltip / accessible label | `Close` | Matches the existing viewer close button string. |

No other user-visible strings exist in v1: the card has no title, no
empty-state text, no toasts, and error paths stay silent (see section 5).

## 4. Accessibility

- **Roles and labels.** The card is a plain overlay child, not a dialog:
  it must not declare a window or dialog role. Every control is an icon
  button with its accessible label from the wording table (tooltip +
  `update_property(LABEL)`). The video area gets
  `Gtk.AccessibleProperty.LABEL` "Video" only if the toolkit demands a
  label for the Picture; otherwise it stays unlabeled — it is not
  interactive.
- **Play/pause state.** Play/pause is a `Gtk.ToggleButton` whose checked
  state mirrors playing, and whose accessible label is updated on every
  transition (`Pause` ↔ `Play` ↔ `Replay`). A screen-reader user hears
  both the state and the next action.
- **Focus.** The card never steals focus: when it appears, focus stays
  where it was (the timeline). The card root is focusable only from a
  click on the video area or a Tab (the arrows then move the card);
  the video area itself is never focusable. Tab order inside the card
  is play/pause → expand → close. Expanding moves focus back to the
  viewer's media widget; closing moves focus to the window's focused
  widget (no crash, no grab).
- **Reduced motion.** The card appears without animation (or with the
  shortest possible fade under a "reveal" the CSS allows); the snap on
  release is a direct move, not an eased glide; there is no animation on
  the video area itself (the stream's own rendering is not ours to
  animate). No transitions on the OSD bar.

## 5. Edge cases

- **GIFV in PiP.** Plays muted and keeps looping; playback the user put
  in PiP is exempt from metered-connection and power-saver gating, as in
  the viewer. The inline GIFV player stands down while the card exists.
- **Video ends in PiP.** The play button becomes replay (`Replay` label,
  play icon); pressing it restarts the stream from the beginning in the
  card. The card stays open showing the last frame until the user
  closes or expands it; closing releases the engine normally.
- **Window resized with the card open.** The card clamps into view:
  its margins are recomputed so it never sits outside the window, then
  it re-snaps to the nearest corner at the 24px margin.
- **Video cannot be adopted back (engine dead).** On expand, if the
  engine or stream can no longer serve, the card simply closes (normal
  release path) and the viewer opens fresh: the attachment loads again
  from the beginning. No error dialog, no toast — a button press must
  never leave the screen unchanged.
- **Close during handover races.** A close button press while the card
  is mid-handover to or from the viewer is a no-op on the already-
  closing side; exactly one of the two close paths runs. QA verifies
  with the minimize/restore cycle loop.
