# Picture-in-Picture for the web column — UX specification

Interaction and wording contract for the web column's PiP card
(`src/webpip.py`) and its column entry points. Implementation facts
(reparenting, teardown order, registry, coexistence rules) are in
`Docs/web-pip-plan.md`; this file only fixes what DEV builds and QA
verifies. Working note, English only.

## 1. Interaction spec

States: `column` (the web column in the sidebar), `card` (the floating
card hosting the same column), `none`.

Entry point, one:

- The column header-bar button (`pip_button`, icon
  `babble-pip-in-symbolic`), visible while the column is live and
  shows a plain page (`panel is None`, not disposed). There is no `p`
  key: the webview consumes keys, and a key bound on the column would
  steal them from the page.

Transitions:

- **column → card (minimize).** The header-bar button. The column moves
  into the card without pausing, reloading or navigating: same page,
  same position, same back/forward state. The sidebar hides. If
  `web-javascript` is on and the page has a qualifying video, the
  video-focus fill applies (section 2); otherwise the card shows the
  page as it is. The card appears at the default position
  (bottom-right, 24px margin) with no animation; focus moves to the
  card's expand button (the header button the user pressed disappears
  with the top bars — leaving focus dangling is worse than a
  deliberate move; see section 4).
- **card → column (expand).** The card's expand button. The column
  returns to `web_slot`, the injected fill is removed, the top bars
  return, the sidebar shows, and the page is exactly where it was —
  no reload (`load_uri` is never called by the expand path). Focus
  moves to the column's webview.
- **card close.** The card's close button. The column is disposed
  through its normal deferred path: while a load is still settling the
  card stays visible up to `TEARDOWN_GRACE_SECONDS` (3 s) — it must
  not disappear early, because unmapping a still-loading view is the
  crash the grace period exists for — then the card leaves the
  overlay and the sidebar stays hidden. A settled page closes
  instantly.
- **window closes while the card is out.** The per-window registry
  closes the card with the window; the column is disposed. Nothing is
  saved (position is per-window, in memory only).
- **a web link opens while the card is out.** The column is restored
  first (no second `WebColumn` is ever built) and the link loads into
  it; the sidebar shows as usual for `_show`.
- **a plugin panel loads while the card is out.** Same as a web link:
  restore, then load into the column. The panel was not offered the
  PiP button, but a load started elsewhere must not strand it.
- **the media viewer opens while the card is out.** The column is
  restored into its slot *without* showing the sidebar (the viewer
  covers the window anyway). The viewer's own PiP rules then apply;
  what the page's playback does while hidden is WebKit's visibility
  rule, and this spec promises nothing about it.
- **the engine PiP card is out when minimize is pressed.** The engine
  card is closed first (its normal release path); then the web card
  opens. Two floating cards never coexist.
- **a site fullscreen request arrives in card mode** (the page's own
  fullscreen button, or a script). It is denied: the video-focus fill
  already gives the near-fullscreen look, and WebKit's default would
  take over the whole Babble window. Fullscreen works normally in
  sidebar mode.
- There is no second web card: the registry holds one card per window,
  and the column exists exactly once.

Not spec'd here and not in v1: audio-only PiP, any settings for size,
position persistence, always-on-top, and play/pause controls of our
own (the page's player controls are the control surface — see
section 2).

## 2. Card anatomy

```
                    card, 320×180, page scaled by reflow
                 ┌──────────────────────────────────────────┐
   corner snap   │          ┌──────────────────────┐        │ ◄─ OSD bar:
            ◄─── │          │ ⤢   ✕                │        │    top-center,
            ───► │          └──────────────────────┘        │    also the
                 │                                          │    drag area
                 │       the page: video-focus fill,        │
                 │       or the page as it is               │
                 │                                          │
                 └──────────────────────────────────────────┘
                 ▲
                 └─ 24px margin from window edge on release
```

- **Page area.** The live `WebKit.WebView` of the column, interactive:
  clicks, scrolling, forms and the page's own player controls work,
  and keyboard input goes to the page once it is focused. This is the
  opposite of the engine card, whose video area does nothing: the web
  card's page area *is* the content.
- **Video-focus fill.** When `web-javascript` is on, a script in an
  isolated world picks the largest top-frame `<video>` with
  `readyState != 0`, not `disablePictureInPicture`, and at least
  160px in one dimension — the Google Picture-in-Picture extension
  heuristic. A stylesheet is injected so the chosen video fills the
  card's viewport (`position:fixed; inset:0; object-fit:contain`,
  black behind it), and a `scrollIntoView` is issued. The fill and
  its state are removed on expand. The pick re-runs on every
  committed load while in card mode, and on that page whenever a
  video starts playing, empties or loads its metadata while no filled
  video is on screen, so a navigation inside the card, or a player
  that adds its video after the load, re-fills. If nothing
  qualifies, the card simply shows the page. Site-specific layout
  quirks (transforms that break `position:fixed`) are accepted in v1
  — a per-site wrapper table is future work, Firefox-style.
- **OSD bar.** Overlaid at the **top**-center of the card — a
  player's own controls sit at the bottom and must stay clickable —
  two icon buttons in order: expand (`babble-pip-out-symbolic`),
  close (`window-close-symbolic`). Each has a tooltip that doubles as
  its accessible label (`uihelpers.set_accessible_label`). The bar is
  always present: no hover reveal, no auto-hide, so keyboard and
  screen-reader users keep the controls reachable. There is no
  play/pause button: the page's own player is the control surface,
  and a second transport under the page's would fight it.
- **Drag and snap.** Drag from the OSD bar only — the page area
  belongs to the page, and a drag gesture on it would break clicks
  and scroll. Same mechanics as the engine card: the card does not
  follow the pointer; a flick moves it once to the nearest corner of
  the drag's direction, 24px margin, on release; each axis acts on
  its own; a short drag (under 12px in both axes) does not move it.
  The default position is bottom-right, mirrored in RTL by the END/END
  margins. There is **no arrow-key movement**: the keys must reach
  the page (a YouTube player's arrows seek). Position is remembered
  per window in memory only.
- **Width.** 320×180, fixed. Wider than the engine card's 240 because
  a page must still fit its own player chrome at that width; the
  engine card shows only the video. Pages reflow to the card width;
  there is no zoom, no aspect tracking, no user sizing in v1.

## 3. Wording table

Every user-visible string. Tooltips double as accessible names via
`set_accessible_label`, so one string per control. All strings pass the
`ui strings` rules. Tooltips follow the repo's header-bar style (title
case, no mnemonic underscores).

| Where | String | Rationale |
| --- | --- | --- |
| Column header-bar `pip_button` tooltip / accessible label | `Minimize to Picture-in-Picture` | Same string as the viewer's button: one concept, one label, no new translatable string. |
| Card expand button tooltip / accessible label | `Expand` | Returns the column to the sidebar; matches the engine card's string. |
| Card close button tooltip / accessible label | `Close` | Matches the existing close strings. |

No other user-visible strings exist in v1: the card has no title, no
empty-state text, no toasts, and error paths stay silent. **This spec
adds zero new translatable strings** — all three come from the engine
PiP work, so `babble-pot` regenerates with no diff.

## 4. Accessibility

- **Roles and labels.** The card is a plain overlay child of the
  window, not a dialog: it must not declare a window or dialog role.
  The two controls are icon buttons with accessible labels from the
  wording table. The page area is a WebKit view: its accessible tree
  is WebKit's own, not ours to rebuild — we label only our buttons.
- **Focus.** On minimize, focus moves to the card's expand button: the
  pressed header button is hidden with the top bars, and a moved focus
  is predictable where a dangling one is not. Tab order inside the
  card is expand → close → the page. Once the page is focused its
  keys are the page's; Escape in card mode closes the card, and
  Ctrl+W, Alt+Left, Ctrl+R and F5 reach the column's page through the
  same routing the sidebar column has (`forward_action`), never the
  window behind it. On expand, focus returns to the column's webview;
  on close, focus falls back to the window's focused widget.
- **Reduced motion.** No animation on the handover in either
  direction: the card appears in place, the fill is a relayout, not a
  transition, and the snap on release is a direct move. No transitions
  on the OSD bar.
- **Announcements.** Minimize and expand announce nothing of their own
  (no toast, no label change to read); the moved focus is the
  announcement, the same convention the engine card uses by not
  moving focus at all.

## 5. Edge cases

- **No qualifying video.** The card shows the page as it is; the
  button, expand and close work identically. No message, no empty
  state.
- **Videos below the fold.** The pick asks neither visibility nor
  playback, only the eligibility above, so a video off screen is
  taken; the fill brings it into the card's viewport, which is also
  what stops WebKit from suspending it (the muted, invisible-video
  rule).
- **Muted autoplay.** The page keeps its own mute state; the card
  never unmutes (autoplay in the column is `ALLOW_WITHOUT_SOUND`, so
  most videos arrive muted, and unmuting is the page's own control's
  job).
- **Video ends in the card.** Nothing special: the page shows its own
  ended state; there is no replay button of ours. The card stays open
  until the user closes or expands it.
- **Page navigates inside the card** (a link clicked in the card, a
  site redirect). The column's navigation policy applies as in the
  sidebar; on each committed load in card mode the pick re-runs, so a
  navigation to another video page re-fills. A navigation away from
  video content simply shows the new page.
- **Window resized with the card out.** The card clamps into view and
  re-snaps to the nearest corner at the 24px margin, or at the free
  space left beside it when that is less than 24px (shared
  `pipgeometry` behaviour, engine card identical).
- **Close during a handover race.** A close press while the card is
  mid-minimize or mid-expand is a no-op on the already-closing side;
  exactly one close path runs. QA verifies with the minimize/expand
  cycle loop.
- **Escape and Ctrl+W in card mode.** Both close the card, whether the
  page or one of the OSD buttons holds focus (Escape through the
  column's own shortcut or the card's, Ctrl+W through
  `forward_action` routing, which counts the whole card as the
  column's). Neither ever closes the Babble window or disposes
  anything but the card's column.
- **`web-javascript` off.** No pick, no fill, no `evaluate_javascript`
  use in card mode; the card shows the page and everything else in
  this spec is unchanged. The button stays available — a video can
  play without scripts (plain `<video>` sources) and the card is still
  the way to keep watching.
- **Error page in the column.** The button stays available (live,
  plain page); the card shows the error page like any other. Closing
  from there is the normal path.
- **Deferred teardown while closing.** The card may stay visible up to
  3 s after Close while a load settles (section 1). This is accepted:
  it is rare, bounded, and the alternative is the pre-first-load
  unmapping crash.
