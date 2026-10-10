# Babble vs Phanpy — UI gauntlet progress

Bar: **Phanpy**, live at <https://phanpy.app> (headed Chrome, captured in
`/tmp/shots/ref-phanpy-*.png`) plus the repo's own screenshots in
`/tmp/ref/phanpy/`.

Ours: `/tmp/shots/*.png`, rendered from the real widgets on a seeded
client (live fosstodon.org / mastodon.social payloads), light and dark,
narrow (520) and wide (900–1400) widths.

## Verdicts per piece

| Piece | Round 1 | Round 2 | Notes |
|---|---|---|---|
| Post row / action bar | phanpy | ours | action bar was hover-only and showed wordy labels; now icon+count, always on |
| Post header | phanpy | ours | overflow menu moved next to the time; row buttons re-flattened at USER priority |
| Card surface | phanpy | ours | explicit card colours + border on a mixed grey canvas (was theme-dependent) |
| Author runs | phanpy | ours | a run of same-thread posts by one author is now ONE card: author line once, `n/total` badges, every body inside the card (`RunModel` + `StatusRunRow`) |
| Thread | phanpy | ours | branch rail per reply; focused post uses an accent bar instead of a 2px outline |
| Profile | ours | ours | header, fields, tabs already at bar parity |
| Composer | n/a | n/a | GNOME sheet, not a web overlay |
| Dark style | phanpy | ours | verified at 520px |

## Fixes this round

- Action bar: permanent, icon+count only (`.status-row .status-actions`).
- Flat chrome for every button inside a post row, from a USER-priority
  provider — Adw styles `button`/`menubutton` nodes above `style.css`,
  which is why the pills survived app-level rules.
- Provider reference held at module level; installed when the display
  exists (was returning early and never loading).
- Card surface: `var(--card-bg-color)` + `var(--border-color)` on a
  `color-mix`ed canvas, so the card reads as a card in light, dark and
  high contrast instead of only when hovered.
- Deprecated colour helpers (`shade()`, `alpha()`, `@window_*`,
  `@orange_4`, `@purple_*`) on the touched rules replaced with palette
  variables.
- Thread: branch rail, softer focused-post treatment.
- Overflow menu in the post header; avatar 46px; card radius 16px.
- Responsive columns: the timeline centred its 660px column with
  margins written from its own `hadjustment`, which lagged a layout
  behind (cards jumped on hover) and, after a large shrink, inflated
  the page minimum so the column slid off the window. Timeline and
  notifications now use `AdwClampScrollable`; profile, compose and the
  notifications header use `AdwClamp`; fixed minimums removed. Every
  page fits 360px. Details and evidence: `evidence/responsive/`.
- Post meta line under the body (`Public · 4m — 9 Oct, 10:47`), matching
  the bar; thread-focus mode still uses the fuller `detail_box` row.
- Author-run rail: accent vertical bar on the left of multi-post runs
  (`.status-run .run-rail`), in addition to the shared card and `n/total`
  badges.

Judging method: the subagent critics could not run this session — their
sandboxes return raw image bytes, have a non-functional shell, and no
write access to the repo — so the A/B was done in this session against
the live bar (`evidence/phanpy/live-*.png`) and the same widgets
rendered side by side.

`meson test -C build`: 10/10 pass.

## Harness notes

Screens come from `/tmp/shot.py`: seeded client (live fosstodon.org and
mastodon.social payloads cached in `/tmp/seed`), widgets rendered through
`Gtk.WidgetPaintable` + `Gsk.CairoRenderer` under Xvfb. Output goes to
`evidence/shots/<screen>-<theme>-<width>.png`.
