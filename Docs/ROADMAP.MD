# Babble — Roadmap

*A cozy little Mastodon client for GNOME. Read the fediverse like a good book.*

**Positioning.** Babble is the reader's client for the fediverse: native GNOME,
local-first, calm by default. Every phase below serves one of three bets —
reading, desktop power, calm.

## Principles

- **Reading beats scrolling.**
- **Native GNOME first** — libadwaita idioms over custom chrome.
- **Local-first.** No telemetry, nothing leaves the machine.
- **Degrade gracefully:** feature-detect the server, hide what it can't do.

## Phase 1 — The Window Seat (reading) — v0.9

**Goal:** make the tagline true.

- **Reader mode** — `src/reader.py`. Extract the article behind a link card
  locally (`GET /api/v1/statuses/:id/card`), render it distraction-free and
  full-screen. Widgets: `AdwNavigationPage`, `GtkTextView`, `AdwToolbarView`.
- **Typography controls** — serif/sans, measure (line length), paper/sepia
  palette, reduced-motion respect.
- **Reading queue** — folders and tags layered over `GET /api/v1/bookmarks`,
  with unread and progress state persisted locally.
- **Markdown export** — a thread or the whole queue to Markdown, ready to drop
  into a notes vault.

**Exit criteria:** a user can spend a whole session reading and never open a
timeline.

## Phase 2 — Offline (local-first) — v1.0

- `src/store.py` — SQLite cache for timelines, bookmarks, threads and
  profiles; warm-filled on sync.
- Offline search across everything cached.
- Compose offline, queue sends, flush on reconnect — pairs with the
  existing `src/sysstate.py`.
- Eviction policy plus a visible cache-size control in Preferences.

**Exit criteria:** airplane-mode reading works.

## Phase 3 — Desktop power — v1.1

- **Command palette** — `src/palette.py`, `Ctrl+K`. Actions plus
  `GET /api/v2/search?resolve=true`. Cheap, and it makes the app feel premium.
- **Deck mode + saved workspaces** — `AdwTabView` with split views and
  `AdwBreakpoint`; tear a thread off into its own window; columns built from
  home, lists, tags and collections.
- **Undo everywhere** — `AdwToastOverlay` with a countdown.
  `DELETE /api/v1/statuses/:id`, `POST /api/v1/statuses/:id/unreblog`,
  `POST /api/v1/accounts/:id/unfollow`.
- **MPRIS** — `src/mpris.py`, on `org.mpris.MediaPlayer2.*`. Media keys and
  top-bar now-playing for audio posts.
- **Print / export** — `GtkPrintOperation`, thread to PDF.

**Exit criteria:** the app is fully operable by keyboard alone.

## Phase 4 — Calm — v1.2

*A wellbeing stance no other fediverse client takes, and it fits the brand.*

- **Quiet mode** — hide boost and favorite counts, no infinite scroll, a real
  "You're all caught up" end state.
- **Per-column calm presets** — hide boosts, hide replies, language filter,
  mutuals-only, one toggle.
- **Notification rules** — group and mute by type and account, on top of
  `GET /api/v1/notifications` and `POST /api/v1/push`.

## Phase 5 — Fediverse breadth — v1.3

- **Capability matrix** — `src/capabilities.py`, derived from
  `GET /api/v2/instance` (`api_versions`, `configuration`). This generalises the
  work already done in PR #18 for `live_feeds`.
- **Non-Mastodon support** — GoToSocial, Akkoma, Pixelfed, driven by capability
  detection rather than software-name branching.
- **Paste-a-URL resolver** — drop any fediverse post URL, resolve it against
  your instance (`GET /api/v2/search?q=<url>&resolve=true`), open natively.
- **Account media grid** — `GET /api/v1/accounts/:id/statuses?only_media=true`
  with a lightbox.
- **Scheduled-post calendar** — week view with drag-to-reschedule
  (`GET`/`PUT /api/v1/scheduled_statuses`).
- Follows export via `GET /api/v1/accounts/:id/following`; import only where
  the server exposes an endpoint, otherwise document the manual path.

## Phase 6 — Trust & polish — v1.4 → 2.0

- **Profile trust panel** — limited or blocked by your instance, instance
  health, migration history, first seen.
- **One-menu thread hygiene** — mute thread, mute domain, filter author.
- **Private local notes on accounts** — "met at UX Camp", pronouns, context.
  Local only, searchable, shown on hover.
- **Alt-text preflight** and reusable alt-text templates.
- **Orca/ATK accessibility audit** with reduced-motion and high-contrast passes.
- GNOME Circle / Flathub readiness pass.

## Cross-cutting

- Every phase mirrors the existing `src/test-*.py` pattern: **no feature lands
  without tests.**
- New strings go through `po/` and the `INTERFACE_LANGUAGES` picker.
- Feature visibility is always capability-gated — never show a control the
  server will reject.
- Flathub rules from the README hold: the manifest stays human-written, and
  AI-assisted code is disclosed.

## Order of attack (cheap wins first)

1. **Command palette** — cheap, high perceived polish.
2. **Undo via toasts** — cheap, and it makes everything else feel safer.
3. **MPRIS** — small surface, very visible.
4. **Reader mode** — the brand bet.
5. **Offline store** — the big one, start behind a flag.
6. **Calm mode** — largely a preferences surface over existing views.

## Risks

- **Offline store** is the largest change and touches every view. Land it
  incrementally, behind a flag, with the cache treated as disposable.
- **Non-Mastodon support** can sprawl. Gate on capabilities; never fork code
  paths by software name.
- **Reader extraction** will fail on some pages. Always keep the "open in
  browser" escape hatch visible.
