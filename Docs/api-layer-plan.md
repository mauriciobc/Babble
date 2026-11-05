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

**Phase 2 progress.** Step 2a done: lists (`lists.py`), filters, announcements and
scheduled statuses moved (20 endpoints, registry 73), and the housekeeping below
(marked DONE). Step 2b done: tags (`tags.py`: 9 endpoints, including followed and trending
tags) and search (`search.py`: 1), registry 83. Step 2c done: timelines
(`timelines.py`: home, public, tag, link, favourites, bookmarks, with the list
timeline reused from `lists.py`), markers (`markers.py`), trending statuses
(`trends.py`) and notifications (`notifications.py`: 9, with the policy and
the filtered-notification requests), registry 101; the client's `timelines_*`,
`trends_statuses_*`, `markers_*` and `notifications_*` wrappers are gone, and
`PagedList.mutate_row` with them. `ApiTimelineSource.fetch` and the completer's
`_search` are generic helpers' own sends of a caller-supplied path, relabelled
"phase 4: internal". Step 2d done (the last move step of phase 2):
`conversations.py` (3), `suggestions.py` (2), `instance.py` (custom emojis, 1),
`annual_reports.py` (4: the report, one year, state, generate) and
`trends.links` (1) plus `tags.trending_async`; explore, the inbox, the emoji
chooser, Wrapped and Catch Up's trend reads call them. Registry 112. Catch Up's
`_get` now takes `(key, start, done)` like `_reply` (guard order unchanged:
aborted, then rate limited, then send), and `wrapped._send` is gone. No
"phase 2" allow-list entry is left; the only path-taking `pages.call` callers
are the two in `pollbox.py` (phase 3). The completers' `search_request` hooks still
return a path (from `search_path()`), so `completer.TokenCompleter._search`
keeps its one raw send until the hook protocol changes.

**Carried over from the phase 1 review (do these at the start or end of phase 2):**
- DONE (2a): Collapse the parallel helper pairs into the `start(callback)` form and
  delete the path-taking versions when their last raw caller goes:
  `pages.call` / `pages.call_api`, `PagedList.mutate_row` / `mutate_row_api`,
  `fedicollections._send` / `_send_api`, `catchup/fetch._get` / `_reply`.
  `call_api` should take an optional `finish=` so an api `*_finish`
  (validation, tuple unpacking) is honoured instead of bypassed by
  `client.request_finish`. Done for `pages.call` / `call_api` and
  `PagedList.mutate_row` / `mutate_row_api` (the path-taking ones now wrap the
  `start` form and stay for their last raw callers: hashtags, the notification
  policy, polls, and the notification requests). `fedicollections._send_api`
  and `catchup/fetch._get` / `_reply` move with their endpoints.
- DONE (2a): Converge the naming before it spreads: one name for the table-pick
  (`ACTIONS`), one keyword (`operation`), one accessor shape
  (`list_path(kind, *ids)`), for `statuses.toggle_async`/`post_action_async`,
  `relationships.action_async`/`answer_request_async` and the three
  `LISTS` tables. Name `lists.py` and `hashtags.py` consistently from the
  start.
- DONE (2a): Add `core.finish_paged` with the first paged read (scheduled).
- DONE (2a, field added and filled): Add a `mastodon_version` (release string) field to `Endpoint`, or decide
  the generated version column comes from `src/capabilities.py`; today
  `min_version` is None everywhere and versions live in free-text notes.

### Phase 3: write paths
- Compose and edit, media, polls, profile edit, reports, admin.
- **Progress.** Step 3a done: admin (`src/api/admin.py`, 29 endpoints,
  registry 141): accounts v2 and the v1 scope probe, the review and action
  verbs, reports (list, update, assign, resolve...), measures, dimensions and
  the four rule lists (federation blocks and allows, blocked e-mail domains,
  IP rules). Six golden scenarios were added first and the rest of the file
  is byte-identical after the move. `admin.send` now takes `start(callback)`
  like `pages.call_api` and `RuleList` takes a rule kind instead of a path, so
  the `admin.send` mechanism and its internal allow-list entry are gone
  (17 "phase 3: admin" entries plus 1 internal, 41 to 23).
- Step 3b done: collections (`src/api/collections_api.py`, 9 endpoints), polls
  (`polls.py`, 2) and reports (`reports.py`, 1), registry 153. Eleven golden
  scenarios (collections page, page, delete, remove myself, edit, create,
  poll refresh and votes, two report submits) were added first; the log is
  unchanged by the move. `fedicollections._send` and the path-taking
  `pages.call` had no caller left and are gone with their mechanisms in
  `api boundary` (23 to 8 allow-list entries: 10 collections, 2 polls, 1
  reports, 1 `_send` internal, 1 `call` internal). Left: 3 "phase 3: accounts"
  (profileedit, preferences), 2 phase 4 internals, 3 phase 4 helper
  internals; compose and media are 3c.
- Step 3c done, so all of phase 3's move steps are done: `core.send_multipart`
  (over the client's public `multipart_async`, which stays as transport);
  `statuses.post_async`/`reply_async`/`edit_async` (registry entries `create`
  and `edit`), `media.py` (upload v2, status; new module) and
  `accounts.update_credentials` (one endpoint, two wire forms: urlencoded for
  the posting defaults, multipart for the profile editor) plus
  `accounts.update_profile` (`PATCH /profile`). Four golden scenarios (profile
  edit with avatar, header, fields and tabs; credentials only; profile only;
  posting defaults) were added first, 224 lines and no deletion; the log is
  unchanged by the move. `MastodonClient` lost `post_status_*`, `reply_*`,
  `edit_status_*`, `media_upload_*` and `media_status_*`. Registry 159;
  allow-list 8 to 5 (the 3 "phase 3: accounts" entries). The `multipart_async`
  mechanism in `api boundary` stays as the guard against a new multipart call
  outside `src/api`.
- **Done when:** as phase 1; multipart/media paths covered by the recorder.
- **Left for phase 4:** the 5 allow-list sites (`completer` search, the
  `PagedList`/`AccountListPage` internals, `ApiTimelineSource.fetch`) and the
  Mastodon verbs still on `MastodonClient` (see its list in the phase 4 notes).

### Phase 4: close (done)
- Done: the allow-list file is `entries: []`. `GET /api/v2/instance` is
  `api.instance.info_async` (`MastodonClient.instance_info_async` keeps the
  cache and coalescing and sends through it). The five generic-helper sites
  send through `core.send_path`, which `api boundary` allows only with a bare
  variable path in `completer.py`, `pages.py` and `timelinepage.py`. The
  compat table is generated by `python3 src/test-api-registry.py
  --write-table` and `api registry` fails when it is stale. Registry: 160.
  `core.InvalidResponse` was not done (it touches the translated error
  path, not a closing step). `mastodon.py` stays a declared exception for
  its size (transport, OAuth, keyring), not for endpoints.
- Allow-list empty; `api boundary` has no exceptions.
- `CLAUDE.md` and `Docs/mastodon-compat.md` updated (registry-generated
  table); remove the "bench-only API" wording.
- **Done when:** zero raw sites outside the allowed internals.
- Option: replace `MastodonClient.invalid_response` (kept so the translated
  error text stays out of `src/api`) with a plain `core.InvalidResponse`
  that the transport or UI layer translates where it catches it.
- Update the `mastodon.py` entry in `src/test-file-size.py` to whatever it
  still holds.

### Phase 5: compliance fixes (remaining)
- Done before phase 1: `send_domain_block` (`applied=` to `apply=`) and
  `confirm_domain_block` (`undo=` to `undo_label=`) fixed in their own
  commit; the two golden scenarios changed deliberately. See "Known bugs" in
  `Docs/mastodon-api-audit.md`.
- One commit per deviation found in phase 0 (deprecated endpoints, wrong
  parameters, missing version gates, missing scopes). Each states the docs
  page and the observable change.
- 5b (independent audit): seven findings, six fixed and one documented; see
  "5b" in `Docs/mastodon-api-audit.md`. Commit: `fix: numeric id order, role
  admin bits, chunked account lookup, repeated Link keys, clearing a
  collection topic, search offset (audit 5b)`.
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
