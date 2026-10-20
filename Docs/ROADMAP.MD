# Babble — Roadmap

*A cozy little Mastodon client for GNOME. Read the fediverse like a good book.*

**Positioning.** Babble is the reader's client for the fediverse: native GNOME,
local-first, calm by default. Every item below serves one of five bets:
**Reading**, **Desktop** power, **Calm**, **Breadth** across the fediverse, and
**Trust** and polish.

## Principles

- **Reading beats scrolling.**
- **Native GNOME first** — libadwaita idioms over custom chrome.
- **Local-first.** No telemetry, nothing leaves the machine.
- **Degrade gracefully:** feature-detect the server, hide what it can't do.

## How to read this

Work is ranked on an **effort / impact** matrix, not by version. Do the tiers
in order; inside a tier, the order is a suggestion.

- **Effort** — S: days, one module or a preference. M: a week or two, a new
  module or dialog. L: a structural change that touches many views.
- **Impact** — what a reader notices, weighted by demand in the two closest
  peers' trackers (Tuba, Phanpy; reaction counts are low in both, so treat them
  as signal, not ranking) and by fit with the bets above.
- **Status** — *Shipped* is in the tree. *Partial* has a foundation to build
  on. Blank means no code yet.

Last audited against the tree on 2026-10-20.

```
                 impact →   low              high
 effort ↓
   S (days)                 Tier 2           Tier 1  ← start here
   M (weeks)                Tier 4           Tier 3
   L (structural)           Tier 5 (defer)   Tier 6  ← big bets
```

## Tier 1 — Small effort, high impact (do first)

| Item | Bet | Status | Notes |
|---|---|---|---|
| **Resume reading position** | Reading | Partial | Remember the last-seen status per timeline and reopen there. Phanpy #269 (the most-reacted open request), Tuba #1105. `client.markers_async` already reads `/api/v1/markers` for notifications; add `home`, with local persistence as the fallback for servers without markers. |
| **Hide boosts / replies per timeline** | Calm | Partial | Phanpy #262, Tuba #1114. `hide-replies-non-follows` exists; there is no boosts counterpart. This is also the first of the Calm presets (Tier 3), so build it once. |
| **Undo via toasts, extended** | Desktop | Partial | `pages.show_toast(undo=…)` exists and mute uses it. Extend to delete (`DELETE /api/v1/statuses/:id`), unboost (`…/unreblog`) and unfollow (`…/unfollow`). Makes everything else feel safer. |
| **Per-notification dismiss** | Calm | | `POST /api/v1/notifications/:id/dismiss`. Already tracked in `IMPROVEMENTS.md`. |
| **Clear error when no secret service is running** | Trust | Shipped | Tuba #598. A "No Keyring Found" dialog at startup (once per session) and in the sign-in failure text, with a help entry; a locked keyring keeps its own message. |

## Tier 2 — Small effort, lower impact (fill-ins)

| Item | Bet | Status | Notes |
|---|---|---|---|
| **Small polish** | Desktop | | Click outside a media image to close it (Tuba #148); re-clicking the active sidebar place scrolls to top (Tuba #409); mark where the old top was after new posts load (Phanpy #419). Verify each first; some may already behave this way. |
| **Share to Babble** | Desktop | | Compose from a shared link or text via `HANDLES_OPEN`. Phanpy #449. |
| **Import / export settings** | Desktop | | GSettings and filters to a file. Phanpy #179. |
| **Follows export** | Breadth | | `GET /api/v1/accounts/:id/following`; import only where the server exposes an endpoint, otherwise document the manual path. |
| **Paste-a-URL box** | Breadth | Partial | `main.py` resolves URLs handed to the app and `offer-copied-links` exists; there is no in-app paste box. `GET /api/v2/search?q=<url>&resolve=true`. |
| **One-menu thread hygiene** | Trust | | Mute thread, mute domain, filter author. |
| **Alt-text preflight and templates** | Trust | Partial | The composer has per-attachment alt text and a boost alt-text reminder. |
| **MPRIS** | Desktop | | `src/mpris.py` on `org.mpris.MediaPlayer2.*`. Media keys and top-bar now-playing for audio posts. Small surface, very visible, but few posts are audio. |

## Tier 3 — Medium effort, high impact (next)

| Item | Bet | Status | Notes |
|---|---|---|---|
| **Command palette** | Desktop | | `src/palette.py`, `Ctrl+K`. Actions plus `GET /api/v2/search?resolve=true`. Deferred as SKIP-03 in `IMPROVEMENTS.md`; makes the app feel premium and is the keyboard path. |
| **Thread composer** | Desktop | | Queue several posts and send them as a self-reply chain. Tuba #157. Builds on `compose.py` and the local drafts. |
| **Cross-account actions + keyboard account switch** | Desktop | | Act on a post as another account, resolving it there with `resolve=true`. Phanpy #803/#974, Tuba #223 (17 comments). Accounts are already keyed `(instance, id)`. |
| **Reader mode** | Reading | | `src/reader.py`. Extract the article behind a link card locally (`GET /api/v1/statuses/:id/card`), render it distraction-free. `AdwNavigationPage`, `GtkTextView`, `AdwToolbarView`. The brand bet. Always keep "open in browser" visible, because extraction fails on some pages. |
| **Typography controls** | Reading | | Serif/sans, measure (line length), paper/sepia palette, reduced-motion respect. Pairs with reader mode. |
| **Quiet mode** | Calm | Partial | `show-interaction-counters` hides counts. Missing: no infinite scroll, and a real "You're all caught up" end state. |
| **Calm presets per column** | Calm | Partial | Hide boosts, hide replies, language filter, mutuals-only, one toggle. `hide-replies-non-follows` is the only preset today. |
| **Notification rules** | Calm | Partial | Client-side grouping, type filtering and `notification-excluded-types` exist. Missing: group and mute by account. |
| **Orca/ATK accessibility audit** | Trust | Partial | Reduced-motion and high-contrast passes. Access keys and a media-tile keyboard path are done; decorative-image labelling is deferred (DEF-02). |

## Tier 4 — Medium effort, lower impact

| Item | Bet | Status | Notes |
|---|---|---|---|
| **Reading queue** | Reading | Partial | The bookmarks page exists; folders, tags and unread/progress state persisted locally are missing. |
| **Markdown export** | Reading | | A thread or the whole queue to Markdown, ready for a notes vault. |
| **Account media grid** | Breadth | Partial | The profile has a media tab backed by `only_media`; the lightbox grid is missing. |
| **Scheduled-post calendar** | Breadth | Partial | `scheduled.py` lists scheduled posts; a week view with drag-to-reschedule (`PUT /api/v1/scheduled_statuses`) is missing. |
| **Profile trust panel** | Trust | | Limited or blocked by your instance, instance health, migration history, first seen. |
| **Local-only account notes** | Trust | Partial | `PrivateNoteDialog` edits the server-side note. The local, searchable, shown-on-hover version ("met at UX Camp", pronouns) is open. |
| **GNOME Circle / Flathub readiness pass** | Trust | | Do it once the tier 1 and 3 items have settled. |

## Tier 5 — Large effort, lower or uncertain impact (defer)

| Item | Bet | Notes |
|---|---|---|
| **Non-Mastodon support** (GoToSocial, Akkoma, Pixelfed) | Breadth | Driven by capability detection, never software-name branching. Can sprawl. The capability matrix (`src/capabilities.py`, `client.is_supported()`) is **shipped**, so this is purely the breadth work. |
| **Web Push / UnifiedPush** | Calm | Deferred on principle (SKIP-01): the payload goes through a relay, which conflicts with "nothing leaves the machine". Revisit only after deciding whether a relayed payload is acceptable, or shipping a self-hosted relay. |
| **Print / export to PDF** | Desktop | `GtkPrintOperation`, thread to PDF. Overlaps with Markdown export. |

## Tier 6 — Large effort, high impact (the big bets)

| Item | Bet | Notes |
|---|---|---|
| **Deck mode + saved workspaces** | Desktop | The most requested layout feature in both peers (Tuba #118, Phanpy's shortcuts view). `webcolumn.py` shows a side web column; there is no multi-column timeline. `AdwTabView` with split views and `AdwBreakpoint`; tear a thread off into its own window; columns built from home, lists, tags and collections. Needs its **own module**, because `window.py` is already over its size budget, and the memory tests must cover several live pages. Start with a design spike. |
| **Offline store** | Reading | `src/store.py`, a SQLite cache for timelines, bookmarks, threads and profiles, warm-filled on sync; offline search; compose offline and flush on reconnect (pairs with `src/sysstate.py`); eviction policy and a cache-size control. Exit criterion: airplane-mode reading works. Touches every view, so land it incrementally behind a flag, with the cache treated as disposable. |

## Not planned

- **Misskey / Friendica-specific support** (Tuba #32, #498): too much API
  variance for a client that gates on capabilities.
- **Cross-platform builds** (Tuba #572): out of scope for a GNOME app.
- **Emoji reactions on posts** (Phanpy #598): server-specific; reactions stay
  on announcements only.

Already shipped, so not listed above: auto-expand content warnings
(`show-spoilers`), confirm-before-boost (`advanced-boost-dialog`), the
"N new posts" pill that keeps the scroll position, window size and position
persistence, bookmarks, followed hashtags, conversations, edit history,
translation, metered-connection handling, scheduled posts, v2 filters and the
capability matrix.

## Cross-cutting

- Every item mirrors the existing `src/test-*.py` pattern: **no feature lands
  without tests.**
- New strings go through `po/` and the `INTERFACE_LANGUAGES` picker.
- Feature visibility is always capability-gated — never show a control the
  server will reject.
- Flathub rules from the README hold: the manifest stays human-written, and
  AI-assisted code is disclosed.

## Risks

- **Deck mode** touches `window.py`, already a declared size exception, and
  multiplies live pages; the leak tests (`timeline memory`) must grow with it.
- **Offline store** is the largest change and touches every view.
- **Non-Mastodon support** can sprawl. Gate on capabilities; never fork code
  paths by software name.
- **Reader extraction** will fail on some pages. Always keep the "open in
  browser" escape hatch visible.
