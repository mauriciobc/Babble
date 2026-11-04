# API layer plan

*One typed, documented, tested way to talk to Mastodon, used everywhere.*

Status: approved, not started. Starts after the notification-filter fix
merges (it touches `notifications.py`).

## Problem

83 call sites outside `src/mastodon.py` hand a method and a literal path to
`request_async` or `pages.call` (about 32 modules; `lists.py` 10,
`hashtags.py` 6). The first count was 74; the audit corrected it to 83.
Beyond them, 8 internal calls inside the generic helpers and 84
helper-layer sites (`PagedList`/`AccountListPage` paths, `mutate_row`,
`admin.send`, ...) name a path that is sent later; the allow-list keys all
three layers. `mastodon.py` has roughly 20 real endpoint wrappers, and
the UI mostly ignores them: `statusactions._send_toggle` and
`profileactions` rebuild the paths the wrappers already define. Nothing
records, endpoint by endpoint, that method, path, parameters, scope and
deprecation status match docs.joinmastodon.org. `mastodon.py` is already
past the 800-line norm, so wrappers cannot simply be added to it.

## Decisions (approved)

1. **Layout.** `src/api/<resource>.py` modules of functions that take the
   client, each an `<name>_async` / `<name>_finish` pair on the Gio.Task
   convention. Not mixins on `MastodonClient`.
2. **Plugins.** `src/plugin.py`'s generic request is unchanged.
3. **Compliance is in scope**, as its own commits (phase 5).
4. **Timing.** After the notification-filter fix merges.

## Design

- **Registry.** Each endpoint is declared once: method, path template,
  required scope, minimum `api_versions.mastodon`, docs URL, deprecation
  status. The wrapper builds its request from the declaration.
  `Docs/mastodon-compat.md`'s endpoint table is generated from it.
- **Escape hatch.** `MastodonClient.request_async` stays for the paging,
  streaming and plugin internals. UI modules stop calling it.
- **Gates** go through `client.is_supported(...)` with constants in
  `src/capabilities.py`, as today.
- **Conventions kept:** `from .i18n import gettext as _`, `connect_weak`,
  SPDX headers, 800-line limit, new modules in `src/meson.build`'s install
  list.

## Enforcement

- **`api boundary`** (new test, `ast`-based, like `module boundary`): fails
  on any literal Mastodon path passed to `request_async` / `pages.call`
  outside `src/api/` and `mastodon.py`. Begins with an allow-list of the 83 raw
  sites (plus helper and internal layers); every phase shrinks it; phase 4 empties it.
- **`api registry`** (new test): every endpoint has all metadata fields; no
  deprecated endpoint without a recorded reason; gates use `is_supported`.
- **Fixture server** (`bench/fixture_server.py`) resolves routes from the
  registry, so a path typo fails there.

## Behaviour-preserving proof

Before a module moves, a recorder captures `(method, path, query, body)` for
each of its UI actions (golden request log, committed under `src/` test
data). After the move the log must be byte-identical. A difference is a
compliance fix and belongs in phase 5 with its own justification.

## Phases

Each phase is one or more commits on its own branch; Dev, QA and Tech Lead
roles run in sequence; the main session accepts against the criteria below.

### Phase 0: baseline and audit
- Full suite under headless mutter; record the pass/skip counts.
- Inventory every endpoint in use (method, path, caller, params).
- Audit table: used vs documented (path, method, params, scope, version,
  deprecation). Output: `Docs/mastodon-api-audit.md`.
- Golden request recorder and the `api boundary` test with the full
  allow-list.
- **Done when:** suite unchanged, audit reviewed, the allow-list reproduces the audit's 83 raw sites plus the helper and
  internal layers (currently 175 entries: 83 raw, 8 internal, 84 helper).

### Phase 1: status and account actions
Three commits, each leaving `api golden` byte-identical:
- **1a core and registry.** `src/api/` package with `core.py` (an `Endpoint`
  declaration: method, path template, scope, minimum API version, docs URL,
  deprecation note; and a `send()` that formats the path and calls
  `client.request_async`, so the golden recorder's chokepoint is unchanged),
  and an `api registry` test (every endpoint complete, docs URL on
  docs.joinmastodon.org, valid method, path template placeholders match).
- **1b statuses.** `src/api/statuses.py`: the status wrappers moved out of
  `MastodonClient` (favourite, reblog, bookmark, context, post/reply/edit stay
  for phase 3) plus the verbs `statusactions`, `statusmenu` and `statusviews`
  build by hand. Callers, `bench/rank_eval.py` and the golden scenarios call
  the api functions; no compatibility shims left on the client.
- **1c accounts and relationships.** `src/api/accounts.py` and
  `src/api/relationships.py`: the account wrappers and the follow, block,
  mute, endorse, note, domain-block, follow-request verbs used by
  `profileactions`, `profile`, `relations`, `explore` and `notifications`.
- Two allow-list entries were relabelled during phase 1 because they were
  not status or account verbs: `desktopnotify.DesktopNotifier._poll`
  (`GET /notifications`, "phase 1: statuses" to "phase 2: notifications") and
  `hashtags.TagRow._on_unfollow.undo` (`/tags/%s/follow`, "phase 1:
  relationships" to "phase 2: tags").
- After each commit the `api boundary` allow-list is pruned
  (`BABBLE_BOUNDARY_PRUNE=1`) and the diff reviewed.
- `mastodon.py` is a recorded exception in `src/test-file-size.py`; its size
  is not a phase 1 criterion. Moving OAuth and the keyring out of it is an
  optional later step, not part of this plan.
- **Done when:** golden identical, suite green, phase 1 allow-list entries
  gone, no status or account verb path built outside `src/api/`.

### Phase 2: read paths
- Lists, hashtags, search, explore, conversations, announcements,
  notifications, scheduled posts, filters, timelines helpers.
- **Done when:** as phase 1 for those modules.

### Phase 3: write paths
- Compose and edit, media, polls, profile edit, reports, admin.
- **Done when:** as phase 1; multipart/media paths covered by the recorder.

### Phase 4: close
- Allow-list empty; `api boundary` has no exceptions.
- `CLAUDE.md` and `Docs/mastodon-compat.md` updated (registry-generated
  table); remove the "bench-only API" wording.
- **Done when:** zero raw sites outside the allowed internals.

### Phase 5: compliance fixes
- Done before phase 1: `send_domain_block` (`applied=` to `apply=`) and
  `confirm_domain_block` (`undo=` to `undo_label=`) fixed in their own
  commit; the two golden scenarios changed deliberately. See "Known bugs" in
  `Docs/mastodon-api-audit.md`.
- One commit per deviation found in phase 0 (deprecated endpoints, wrong
  parameters, missing version gates, missing scopes). Each states the docs
  page and the observable change.
- **Done when:** audit table has no open rows.

## Definition of done (whole plan)

- `meson test -C build -j1` under headless mutter passes at least the
  phase 0 baseline plus the new tests.
- `ruff check src/ bench/ plugins/` clean.
- `api boundary` allow-list is empty.
- Every endpoint in use appears in the registry and in the audit table.
- Po tests and `ui strings` pass (no new generic labels).

## Risks

- **Refactor regressions in live UI.** Mitigated by golden logs and one
  module family per commit.
- **Docs drift.** Mastodon docs lag the code (docs cover API v11, main is
  v12); the audit records the source (docs page or server source) per row.
- **Plugins.** Unchanged API; `plugin boundary` must stay green.
