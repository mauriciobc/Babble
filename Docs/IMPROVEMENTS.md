# Babble — audit findings and improvement register

*A working register, not a report. Every row is something to do, something to
decide, or something we looked at and chose not to do.*

This file exists to track improvement work on Babble, produced by comparing the
implementation against other Mastodon clients — **Tuba** (`GeopJr/Tuba`, Vala,
30+ typed API entity classes), **Phanpy** (`cheeaun/phanpy`, one `api.js`,
IndexedDB persistence, `supports.js` capability matrix), **Elk** (the author's
own fork, `mauriciobc/elk@9597db5`, source of the Catch-Up ranker) and the
**official Mastodon Android client** — and against the Mastodon REST API spec
(Mastodon 4.7.3 / `api_versions.mastodon` 12).

## How to use this file

- Status values: `todo` · `doing` · `done` · `blocked` · `deferred` · `won't`.
- Update the **Status** cell as work lands. Nothing else needs to change.
- Every finding carries a `file:line` evidence anchor. If an anchor no longer
  resolves, the finding was either fixed or the code moved — check before acting.
- New findings get appended to the right tier with the next free `ID`.
- **Tier 1 is correctness. Tier 2 is consolidation. Tier 3 is structural.**
  Do not mix them in one change.
- All work keeps the project's own rules: no feature lands without a test
  (`CLAUDE.md` cross-cutting), the 800-line file-size rule, capability gating
  through `client.is_supported`, `pages.connect_weak` for signal cycles.

## Reference baseline

| Reference | Verified how | Useful for |
|---|---|---|
| Phanpy `main` | `src/utils/` file list, 82 files | API-in-one-file discipline, IndexedDB persistence, capability matrix, catch-up provenance |
| Tuba `main` | `src/API/` tree, 30+ entity classes | typed entity layer, Web Push, server-side grouped notifications |
| Elk (fork) `9597db5` | cited in `CATCH-UP.md:11-12` | ranking model, reading-first UX |
| Mastodon Android | feature surface | completeness baseline, per-account settings, offline cache |

Babble's own provenance markers: `window.py:35` ("Sidebar places in Tuba's
order"), `catchuprank.py:206` (ports `MASTODON_WEIGHTS`), `CATCH-UP.md:5-18`
(Phanpy + Elk commits named).

---

## Product stance (decision, 2026-10-15)

**We lean minimal and opinionated, in the Phanpy / Tuba / Elk sense.**

Consequences already applied to this register:

- Client-side choices stay client-side. Notification clustering stays
  client-side; we deliberately do **not** adopt the server-side grouped
  notifications API that Tuba uses.
- Feature-chasing is out of scope. Items the reference clients have and Babble
  does not are only admitted when they reduce reading noise.
- **Web Push is deferred on principle, not on cost.** See `SKIP-01`.
- Tuba, Phanpy, Elk and the Android client all persist state offline. Babble
  does not, deliberately, until Roadmap Phase 2. Not a finding.

---

## Tier 1 — Correctness

Small, individually verifiable diffs. Several are user-visible bugs.
Nothing here needs a design decision.

| ID | Status | Finding | Evidence | Notes |
|---|---|---|---|---|
| BUG-01 | **done** | **`filter.context` and `filter.expires_at` are ignored by the timeline.** A filter scoped to `account` (or any context set excluding `home`) still collapses a post in the home timeline. Catch-Up honours both, so the same post is hidden in one view and visible in the other. A `blur` rule plus a `warn` rule resolves to `blur` in the timeline and `warn` in Catch-Up. | `timeline.py:2187-2200` (`status_filter`), call site `timeline.py:3914` is `status_filter(item.raw)` — the function has no context parameter. Correct behaviour lives in `catchupdigest.py:301-330` (`filter_info`), whose docstring wrongly claims to be "a pure copy". | Highest severity. `filter_info` is the correct one; move its context + expiry handling **up** into `status_filter` and make Catch-Up the caller. Also collapses the `dedupe_boosts` divergence (`catchupdigest.py:480-505` vs `timeline.py:288-313`). |
| BUG-02 | **done** | **The documented OAuth paste-code fallback is unreachable.** `paste_code_button` is created `visible=False` in the UI and every code path hides it; the only `set_visible(True)` sits in `on_url_opened`, which has no `@Gtk.Template.Callback` decorator and no caller. `auth.use_pasted_code` is reachable only from that button. | `window.ui:230-232`; `window.py:1516` (→`False`); `window.py:1548` (→`False`); `window.py:1568-1572` (dead, the only `True`); `auth.py:314-328` | `CLAUDE.md:38` tells us the `babble://`/OOB fallback works. It does not. If the loopback port cannot be bound, login dead-ends with no escape hatch. Wire `on_url_opened`, or reveal the button when `auth.fallback` flips. |
| BUG-03 | **done** | **`Escape` is swallowed app-wide.** Bound as an *application* accelerator, which is evaluated ahead of the focused widget, so Escape inside the composer and inside every `Adw.Dialog` runs `go_back()` (a no-op unless the nav stack has depth > 1) instead of closing. | `main.py:160` (`['<alt>Left', 'Escape']`) vs `shortcuts-dialog.ui:207-208` advertising Escape as "Close Composer"; handler `window.py:1289-1291` | Drop `Escape` from `main.py:160`, or scope it to the window. |
| BUG-04 | **done** | **`explore.supports_link_timeline` parses the human version string** (`instance_info['version']`) instead of asking `client.is_supported`, and returns `True` when the version is unknown — the opposite policy from every other gate. | `explore.py:46-54` | Contradicts the rule in `mastodon-compat.md:10-14`. The existing `version gate call sites` test cannot catch it because it only scans for `mastodon_api_version()` comparisons. Widen the test (see TEST-08). |
| BUG-05 | **done** | **Streaming events are decoded but never handled**: `follow_request`, `announcement`, `announcement.delete`, `announcement.reaction`, `report`. A follow request arrives and the notification list does not refresh. Additionally `JSON_EVENTS` omits `add` / `remove` (Mastodon 4.3 collection-membership events), so those payloads are emitted as raw JSON *strings*. | classified at `streaming.py:70-71`; no branch in `window.py:711-760` | |
| BUG-06 | **done** | **`TimelinePage` has no generation counter.** A `refresh()` response can land after a newer one and splice stale statuses into the live model. | `timeline.py:4356-4716` vs `notifications.py:1423-1426` and `pages.py:381,417-419`, both of which have one | |
| BUG-07 | **done** | **Stale buttons survive row recycling.** `NotificationRow._clear_actions` empties the box but never clears `_request_buttons` / `_follow_button`, which are read through `getattr(..., ())` afterwards. After recycling they point at the *previous* item's already-removed widgets. | `notifications.py:1025-1027` (clear), `:1096`, `:1139` (set), `:1104`, `:1111`, `:1169` (read) | Latent, not always visible. |
| BUG-08 | **done** | **`_discard_scheduled` never calls its `*_finish`**, leaking a `Gio.Task`, and is the only in-flight reply in `BabbleComposeView` without a generation guard — so a `reset()` does not suppress it. | `compose.py:1659-1669` vs guarded peers at `:1481`, `:1514`, `:1554` | |
| BUG-09 | **done** | **`default-sensitive` and `default-quote-policy` have no local effect.** Read only by the server-mirror in `preferences.py`; the composer never reads them. `send_status` sets `sensitive` only when a content warning is present. | `preferences.py:158-162`; `compose.py` never references either; `compose.py:1578` | The *mechanism* is intentional ("the way Tuba does", `preferences.py:140-174`). The **copy** is wrong — gschema summaries and `preferences.py:412` read as local settings. Decide: reword, or adopt in the composer. |
| BUG-10 | **done** | **Failures are silently discarded on a profile.** `_request_result` swallows `GLib.Error` and returns `None`, so a failed familiar-followers / featured-tags / endorsements / account-refresh is completely invisible — no toast, no retry, no log. The same class of request one function away *does* report and degrades safely. | `profile.py:389-395`, used at `:1013`, `:1066`, `:1090`, `:1165` vs `on_relationship_loaded` `:1183-1192` | `AccountStatuses.fetch` (`:121-125`) also swallows silently. |
| BUG-11 | **done** | **`followed_tags` is fetched once with `limit=200` and never paged**, silently truncating for accounts following more than 200 tags. | `catchup.py:639` vs correct handling in `hashtags.py:769` and `searchprovider.py:450` | |
| BUG-12 | **done** | **One `/accounts/relationships?id[]=<single>` per notification row.** The endpoint accepts up to 40. A page of 40 fresh follow notifications costs up to 40 requests. | `notifications.py:1150-1164` | The correct pattern already exists: `catchup.py:670-685` batches in 40s, with a test at `test-catchup.py:375-379`. |
| BUG-13 | **done** | **Pull-to-refresh on link-paged timelines discards the cursor.** `paging='link'` forwards only `limit`, so `since_id` is dropped and page 1 is re-downloaded. Relatedly, the two link-paged paths are hard-coded in the window. | `timeline.py:4316-4328`; `window.py:981` | |
| BUG-14 | **done** | **Untranslated UI string** in the admin account-search flow — `'IP'` is both the `placeholder_text` and the accessible label of a `Gtk.Entry`, sitting in the same tuple as three translated entries. | `admin.py:1024` | One-line fix. |
| BUG-15 | **done** | **Notifications never loaded.** `NotificationController` called `self._request_relationships`, which was never defined, so the first page load raised `AttributeError` and the list stayed empty. The per-row relationship queue also read state that only `NotificationsPage` initialised. Introduced by ARCH-03 / BUG-12. | `notificationctrl.py` `on_page_loaded`, `on_older_loaded`; `notifications.py` `NotificationRow._fetch_relationship` | Found by running the app, not by a test: no test loaded a page containing a follower. |
| BUG-16 | **done** | **Search stuck on its loading state** whenever a result set held a post. `make_post_row` passed a lambda to `pages.connect_weak`, which accepts only a bound method, so building the row raised. Present at `da2eeab`, before this register. | `search.py` `make_post_row`; `pages.py:46-64` | The other 258 `connect_weak` / `weak_method` call sites were checked; this was the only non-method. |
| BUG-17 | **done** | **Adding an account never opened the browser.** `BabbleWindow.open_url` was deleted when the OAuth flow moved into `auth.py` (`8ae87cd`) while `_on_auth_browser` still called it. Every login test replaced `win.open_url`, so nothing failed. | `window.py` `_on_auth_browser`; `test-oauth-callback.py:798`, `test-oauth-loopback.py:110` | Restored with the portal launch plus a default-handler fallback; a test now calls the real method. |
| BUG-18 | **done** | **`po/POTFILES.in` listed the deleted `src/timeline.py` and omitted the nine modules split out of it.** `xgettext` could not run, and a regeneration would have dropped those modules' strings. | `po/POTFILES.in` | `test-file-size.py` now checks that every listed file exists and every module that translates is listed. |

### Resolutions

**BUG-01.** New `src/filterrules.py` owns what a rule means. It is
deliberately gi-free so it sits *below* both readers: `catchupdigest`
cannot import `gi` (its test asserts `gi` never enters `sys.modules`),
and `timeline.py` is GTK, so the shared layer had to be the lower one.
`timeline.status_filter` and `catchupdigest.filter_info` both delegate to
it. `StatusRow` grew a `filter_context`, set by `TimelineFactory` from the
new `timeline.filter_context_for(source)` — an account source reads as
`account`, the public/hashtag/link timelines as `public`, everything else
as `home` — and `thread.py` sets `THREAD`. One design point worth
recording: **expiry is judged only against a clock the caller supplies.**
`catchupdigest` documents that it never reads the clock so a snapshot
reads the same on every open; reading it inside the shared helper broke
that and the Catch-Up tests caught it. The timeline passes
`datetime.now()` and gets live expiry. Pinned by the new `filter rules`
test, which also asserts both callers agree with the shared answer across
12 fixtures × 4 contexts.

**BUG-02.** Deeper than it looked. `on_paste_code_clicked` was wrong in
*both* branches: it showed the code entry when there was no authorization
to redeem against (`use_pasted_code()` returned False), and **hid** it in
the one case that matters — when the browser was successfully renamed to
the out-of-band flow and was on its way to showing a code, leaving the
user holding a code with nowhere to put it. It now shows the field either
way. `refresh_login_widgets` owns the button's visibility, showing it
while an authorization is in flight — the only state a pasted code can be
redeemed against, and the only state where a browser that never came back
leaves the user stuck. The dead `on_url_opened` is deleted.

**BUG-03.** `Escape` removed from `win.back`. It was undocumented (Go Back
is documented as `<Alt>Left`), it shadowed a documented binding
(Close Composer), and it competed with `mentioncompleter.py:335`, which
uses Escape to dismiss the mention list. With it gone, `Adw.Dialog`
handles Escape natively and the mention completer's key controller is
uncontested.

**BUG-04.** Now `client.is_supported(LINK_TIMELINE_API_VERSION)` with the
constant `2`, which is `api_versions.mastodon` for Mastodon 4.3.0 — read
out of `mastodon/mastodon@v4.3.0`'s `lib/mastodon/version.rb` rather than
guessed from the release notes. The policy also flipped from optimistic to
`when_unknown=False`, matching every other gate: the fallback
`open_link_timeline` already opens the link in a browser, so an unproven
server should not be asked for a route it may not have. `import re` is no
longer needed by `explore.py`.

**BUG-05.** Branches added for `follow_request` (refresh notifications),
`announcement` and `announcement.delete` (reload the banner), `report`
(refresh the open admin dashboard), `encrypted_message` (refresh the
conversation list) and `add`/`remove` (deliberate no-op, documented).
`announcement.reaction` is also a deliberate no-op — Babble renders only
your own reactions, applied optimistically. `add`, `remove`, `report` and
`encrypted_message` were added to `JSON_EVENTS`; the first two had been
reaching the owner as raw JSON *strings*. The dashboard needed a seam, so
`AdminDialog.refresh_tool(tag)` was added rather than reaching into
`_pages` from the window, and `window` tracks the open dialog weakly
through `close-request`. Pinned by the new `stream events` test, which
reads `_on_stream_event` and asserts the documented event list is covered
exactly — that list is a real tripwire, and it earned its keep on the way
in by finding the undecoded `report` payload.

**BUG-06.** `_fetch` now stamps every request with a generation number that
rides out in the task's `user_data` and back into the handler. A response
carrying a stale number is dropped. One detail worth recording: dropping it
also clears `model.loading` and `_loading_older`, because a discarded
`load()` still has to stop the page reading as loading or it never fetches
again.

**BUG-07.** `_clear_actions` now empties `_request_buttons` and
`_follow_button` rather than leaving them pointing at removed widgets, and
both `_answer_request` and `_follow_back` hold their buttons in a local for
the reply — re-enabling a button that has since been rebound to a different
item is worse than leaving the old one greyed.

**BUG-08.** `_discard_scheduled` now finishes its task (it was the only
callback in the codebase that never did, which kept the Gio.Task and the
whole closure alive) and carries the `_upload_generation` guard its
siblings all have, so a `reset()` stops the reply refreshing a list for a
draft the user no longer has.

**BUG-09.** Split decision, because the two keys are not the same. The
mirror itself is deliberate and stays (`preferences.py:140-174` says so,
and names Tuba). **`default-sensitive` is now honoured by the composer** —
a client that omits `sensitive` inherits whatever the server's *web*
client sent last, which Babble is not. **`default-quote-policy` cannot
be**: there is no per-post quote policy in the API, only
`source[quote_policy]` on the account, so the honest fix there was copy —
the gschema summary and the row subtitle now say it is saved to your
server preferences. A third string appeared and was regenerated.

**BUG-10.** `_request_result` takes an optional widget and heading and
reports through `pages.report_error`; with neither it logs under
`G_MESSAGES_DEBUG=Babble` rather than vanishing. All four call sites pass
one, so a profile missing familiar followers, featured hashtags,
endorsements or its own account now says so.

**BUG-11.** `CatchUpRun._get` finishes with `request_finish_paged` and
keeps the cursor per path; `_on_followed_tags` follows it and
de-duplicates at the end. `catchupdigest.parse_time` was already
delegating to `filterrules` (BUG-01), and this is the only other ISO reader
in that module.

**BUG-12.** Follow/follow-back relationship lookups are batched through a
queue on `NotificationsPage`: up to 40 ids per call, matching
`catchup.py`'s `RELATIONSHIP_BATCH`, and the reply re-binds the row on
screen so its Follow Back catches up. A page of follow notifications went
from up to 40 requests to one.

**BUG-13.** `ApiTimelineSource.fetch` now resets `_next` on a `since_id`
refresh instead of silently dropping the cursor and re-downloading page 1.
The two link-paged paths moved out of `window.py` into `timeline.link_paged`.

**BUG-14.** `'IP'` → `_('IP')` in the admin search tuple, where it was the
untranslated member of a translated four.

**BUG-15…18 (found by running the app, 2026-10-15).** All four were in
code paths no test reached, and three of them hid behind a test that
replaced or skipped the broken piece: the login tests substituted
`open_url`, and no notifications test fed the page a follower. Each now
has a regression test that fails on the old code. A scan of every
`self.name` use across the extracted seams, and of every
`connect_weak` / `weak_method` call, found no further instance of these
two patterns. The lesson for the seams (ARCH-01…03, 06) is that a green
suite said less than it appeared to: the split moved code that was never
exercised, and "done" was recorded before the app was run.

### Accepted, documented — do not "fix"

| ID | Status | Finding | Why accepted |
|---|---|---|---|
| ACC-01 | accepted | `TimelinePage._trim_old` silently does nothing for `link` / `none` paging. | Documented at `timeline.py:4608-4610`: a Link cursor cannot be moved back and an unpaginated source could never refetch. Correct behaviour. |
| ACC-02 | **done** | `set_endorsed` spelled out `version is not None and version < 6` rather than asking, which `test-version-gates.py:20-28` deliberately let pass. **Now `is_supported(ENDORSE_API_VERSION, when_unknown=True)`, with the policy stated at the call site and the magic 6 named as a constant** — so the `when_unknown` claim in the docs is now true rather than a description of an accident. | It went through while refactoring the neighbouring actions (CLEAN-01); the version-gate test caught the intermediate form and drove the final shape. The 404-retry ladder deliberately stays outside `_send_profile_action`: it must see the 404 while the busy state is still held. |
| ACC-03 | accepted | Catch-Up's grid hides itself below 6 eligible tiles, so 1,386 lines never run for a quiet account. | `catchupgrid.py:46`, `catchup.py:1903`. A deliberate progressive-disclosure threshold. |
| ACC-04 | accepted | ~1,500 lines of `catchuprank.py` are test-only scaffolding (`embed_*`, `diversity_rerank` parameters, `predict_actions`, `context_multiplier`). `catchup.py:285` passes none of it. | This is what makes the golden-diff test against the original TypeScript possible. **Mark them, do not delete them.** See CLEAN-19. |

---

## GNOME review 2026-10-16 (branch `gnome-HIG-polish`, shipped as 0.2.0)

A review of the app against the GNOME app-architecture, GTK4/libadwaita,
HIG and packaging skills, done before any code was touched. No Blocker was
found: no deprecated widget, no deprecated CSS, no invented API, no app-id
mismatch. What follows is what the review found and what became of it, so the
next reviewer does not re-derive it.

### Fixed in 0.2.0

| ID | Status | Finding | Evidence |
|---|---|---|---|
| HIG-01 | **done** | F1 opened the shortcuts dialog and nothing opened the help. Added `app.help` (F1), a Help item in both menus before About, and the Mallard pages under `data/help/C/`. `yelp://<app id>` with a fallback to the online docs, reusing `window.open_url`. `app lifecycle` asserts the binding. | `main.py`, `window.py:open_help`, `data/help/` |
| HIG-02 | **done** | `load_stylesheet()` duplicated `AdwApplication`'s automatic `style.css` load, through a deprecated `Gtk.StyleContext`, with a docstring that said libadwaita does not do this. Removed. | `main.py` (deleted) |
| HIG-03 | **done** | `app.shortcuts` was hand-built from the same resource `AdwApplication` reads. Removed; Ctrl+? comes from libadwaita. | `main.py` (deleted) |
| HIG-04 | **done** | `app.preferences` built a new dialog per activation, stacking windows over one another. Reuses the open one, as `profile.py` already did. `app lifecycle` asserts it. | `preferences.py:present_preferences` |
| HIG-05 | **done** | `AdwPreferencesDialog` across five pages had no search. `search_enabled=True`. | `preferences.py` |
| HIG-06 | **done** | The About dialog carried no links although the metainfo declares four. Now takes the homepage and issue URL, and reuses the open dialog. | `main.py:on_about_action` |
| HIG-07 | **done** | Three views had both a suggested and a destructive button. Save/Resolve keeps the emphasis in the list and hashtag editors; in the report page "Take Action" moved out of the header bar into the account summary it acts on, because it opens a moderation dialog rather than answering the same question as Resolve. | `lists.py`, `hashtags.py`, `admin.py` |
| HIG-08 | **done** | A header-bar button labelled "Done" in a dialog that commits nothing (the rows commit as they are toggled). Now "Close". | `lists.py:638` |
| HIG-09 | **done** | British spellings in notification strings and `e.g.` in search hints. `ui strings` fails on either from now on. | `desktopnotify.py`, `search.py` |
| A11Y-01 | **done** | The Cairo charts multiplied their alpha by a constant, putting back the contrast high contrast had taken away, and no media query reaches a `draw_func`. One `charts.alpha()` helper, with the flag injectable so it is testable without touching the a11y setting. | `charts.py` (new), `admin.py`, `wrapped.py`, `hashtags.py` |
| A11Y-02 | **done** | The tag-usage sparkline was `PRESENTATION`, which hid an informative chart from screen readers entirely. Now named, with the series in its description. | `hashtags.py:Sparkline` |
| A11Y-03 | **done** | Three Catch-Up filter controls (a toggle group, two drop-downs) had no accessible name, while a fourth control in the same function had one. Related to their visible headings now, as `window.ui` already does for the server suggestions. | `catchup.py:_build_options` |
| A11Y-04 | **done** | The wrapped year drop-down had a tooltip but no accessible label. | `wrapped.py` |
| UI-01 | **done** | The attachment viewer asked its audio pane for 420 px inside a window that declares a 360 px floor, so the control bar was clipped. The hard minimums are gone and the clamp sizes it. | `mediaviewer.py:_audio_view` |
| UI-02 | **done** | Two `AdwStatusPage`s were hand-styled `.compact`, which libadwaita applies itself. | `mediaviewer.py`, `emojichooser.py` |
| UI-03 | **done** | Two `Gtk.SpinButton`s hung off an `AdwActionRow`. Now two `AdwSpinRow`s. Note `AdwSpinRow` is a row that *owns* a spin button: `get_value` not `get_value_as_int`, and `notify::value` because there is no `value-changed`. `schedule dialog` covers both. | `scheduled.py`, `test-scheduled-dialog.py` |
| UI-04 | **done** | `profile.py` built a `GtkActionBar` and then used `add_bottom_bar` for the switcher above it. One bar type for one slot. Not deprecated, only inconsistent. | `profile.py` |
| UI-05 | **done** | The filter editor pushed a page with a fixed tag straight onto the navigation view, bypassing the dedup guard in `pages.push_page` — safe only because the editor always popped itself. Same guard, written out. (It cannot go through `push_page`: that wraps its child in another `AdwNavigationPage`, and the editor already is one.) | `filters.py:edit` |
| CSS-01 | **done** | The text-size preference scaled `label` nodes only, so entries, post bodies, the composer's source buffer and status-page descriptions ignored it. Moved to the window node in `em`, so it inherits and composes with GTK text scaling. | `style.css` |
| CSS-02 | **done** | A literal `opacity` on disabled viewer buttons overrode the `--disabled-opacity` helper that high contrast raises. | `style.css` |
| CSS-03 | **done** | Six CSS animations and transitions with no reduced-motion companion, although the file already used `prefers-contrast`. | `style.css` |
| CSS-04 | **done** | The lightbox hard-coded twelve white foregrounds with no high-contrast companion. Kept the `#000` surface (content, and the file argues it), hoisted the whites into `--viewer-fg` with a contrast companion. | `style.css` |
| CSS-05 | **done** | The decorative wallpaper survived into high contrast. | `style.css` |
| PKG-01 | **done** | Two screenshots were both `type="default"`; only one may be. | metainfo |
| PKG-02 | **done** | The Flatpak manifest had no `cleanup`, so nine kinds of build leftovers shipped in the bundle; and `build-options` came before `finish-args`. | manifest |
| PKG-03 | **done** | CI never linted the artifact it built. Added `flatpak-builder-lint` over the manifest and the built appdir, through `org.flatpak.Builder` so the linter version is Flathub's. **Unverified locally** — the tool is not installed here, so CI is its first real run. | `ci.yml` |
| PKG-04 | **done** | The gschema's `<summary>` strings never reached a translator: it was absent from `POTFILES.in` despite declaring `gettext-domain="babble"`. Added, and three keys that had no summary got one. 57 strings come from it — 55 `<summary>` and 2 `<description>`. | gschema, `POTFILES.in` |
| PKG-05 | **done** | `Report-Msgid-Bugs-To` was empty and xgettext rewrites that header on every regeneration, so it is now passed as `--msgid-bugs-address` from `po/meson.build`. The descriptive comment lines in the header are still xgettext's placeholders: meson's `i18n.gettext()` only grew `msgid_bugs_address` after 1.13, and there is no other hook for them. | `po/meson.build` |
| PKG-06 | **done** | Three symbolic icons had no `viewBox`; at 24 px the notifications one touched both edges. | `data/icons/` |
| UI-06 | **done** | `_make_dropdown` reached into `Gtk.DropDown`'s child to restyle libadwaita internals. | `compose.py` |
| UI-07 | **done** | The banners' own action was styled as the default rather than the suggested one (`AdwBanner:button-style`, since 1.7). | `window.py`, `notifications.py` |

### Deferred, with the reason

| ID | Status | Finding | Why deferred |
|---|---|---|---|
| DEF-01 | deferred | `pages.py`'s `PagedList` is a `GtkListBox` over an unbounded paginated endpoint, and `search.py` appends whole `StatusRow`s to a `Gtk.Box`, so search results are never recycled. | Real, and the one structural item. 14 modules consume `PagedList` and four of them manipulate row widgets directly, so it is its own branch (`pagedlist-listview`): extract to `src/pagedlist.py`, keep the name, migrate those four call sites to item-level operations. A boxed list is also what makes `COLUMN_WIDTH` clamping work there today. |
| DEF-02 | deferred | ~20 decorative `Gtk.Image`s sit next to a text label without `accessible-role="presentation"`. | Mechanical, but two of the listed sites (`statusrow.py:160`, `pages.py:682`) are informative rather than decorative and need a label, not a role — so this is a judgement per site, not a sweep. |
| DEF-03 | deferred | The banners sit in a plain `Gtk.Box` rather than an `AdwToolbarView` top bar. | Works; the cost is the seam with the header bar below. Fixing it means restructuring `window.ui`'s shell page and `notifications.py`'s column, which is real regression risk for a cosmetic seam. |
| DEF-04 | deferred | An `AdwSpinner` sits in two header bars where an `AdwSpinnerPaintable` sizes itself to its button. | Not wrong, and the conversion rewrites both button children for no user-visible gain. |
| DEF-05 | **won't** | "D_raft Posts" and "A_nnouncements" take their access key from a non-initial letter. | Tried it: making them initial (`_Draft Posts`, `Admin _Dashboard`) collides with About and Help, and the next spelling collides too. The non-initial letters are what keep all twelve keys unique. Not worth churning four translated strings for. |
| DEF-06 | deferred | The app icon uses eleven gradients, three baked specular strokes and a black outline; the HIG asks for flat fills. | The artwork must be redrawn, not edited, and an icon is not a review finding a code change can settle. |
| DEF-07 | deferred | No `-Dprofile=Devel`, so the app id is hard-coded in 24 places across the meson files. | Real and mechanical, and worth doing before a devel install is needed; it touches every installed filename, which is a bad pairing with a release. |
| DEF-08 | deferred | Screenshots are 1090×790; the curation guidance says ≤1000×700. | Only matters when curating for Flathub. |

### Second pass: what a review of the first pass caught

Reviewing the branch above found one thing that shipped broken and several
that were merely wrong. The broken one is the reason this section exists:
**the help pages could never open.** `HELP_URI` was `yelp://`, and no such
scheme exists — Yelp registers `ghelp`, `help`, `info` and `man`, and its URI
resolver dispatches only on those, so every F1 press failed the launch and
fell through to the online docs, with the installed Mallard pages doing
nothing. `test-lifecycle.py` had pinned that exact literal, so the suite was
green on a feature that could not work.

| ID | Status | Finding | Evidence |
|---|---|---|---|
| FIX-01 | **done** | `HELP_URI` was `yelp://…`, handled by nothing. Now `help:io.github.mauriciobc.Babble`: the scheme Yelp registers, and the form the Desktop Help System Specification gives, with the document id being the app id that `share/help/<lang>/<id>/` is keyed by. | `window.py`, verified against yelp's `org.gnome.Yelp.desktop.in` and `libyelp/yelp-uri.c` |
| FIX-02 | **done** | The test asserted the whole URI literal, so it confirmed the wrong scheme rather than the right one. It now asserts the *scheme* and the document id separately — a planted `yelp://` fails it. | `test-lifecycle.py` |
| FIX-03 | **done** | In a Flatpak the `help:` URI is resolved host-side by the OpenURI portal, so the viewer looks in host directories, not `/app/share/help`, and reports the document missing — a *successful* launch, so the fallback never fires. Sandboxed, F1 now goes straight to the online documentation instead of opening a viewer that cannot find anything. | `window.py:open_help` |
| FIX-04 | **done** | The CI lint step used `appdir`, which is not one of the linter's four targets (`appstream`, `manifest`, `builddir`, `repo`), and pointed at `build-flatpak/files` when `builddir` wants the build directory. Now `manifest` and `builddir build-flatpak`, both with `--gha-format`. Verified against the linter's `cli.py`, not from memory this time. | `ci.yml` |
| FIX-05 | **done** | `ui strings` could not see the strings it exists to police: `POTFILES.in` feeds the gschema and the metainfo too, and both are runtime-translated. `collect()` now reads every data file `POTFILES.in` lists, so the list cannot drift. It immediately caught a violation in this branch's own release notes. | `test-ui-strings.py` |
| FIX-06 | **done** | The gschema summary for the interface language contained `e.g.`. Reworded. The JSON sample `{"title", "tags": [...]}` keeps its three dots — they are JSON notation, not a typo — under a snippet-keyed exception that reports itself when the snippet changes. | gschema, `test-ui-strings.py` |
| FIX-07 | **done** | Two claims about `GtkCssProvider` were wrong in the project's own words: it *does* have a `parsing-error` signal in GTK 4.24, and the stylesheet test collects it. Fatal warnings stay as a second net, but the stated reason was fiction. | `CLAUDE.md`, `src/meson.build` |
| FIX-08 | **done** | `set_button_style` on the draft banner was set inside the refresh handler — a constant property, re-applied on every draft change. Set once at construction, as the notification banners already were. | `window.py` |
| FIX-09 | **done** | `edit()` in the filters list popped *back to* an open editor, so clicking a second filter (or "New Filter") quietly showed the first one. The user chose a destination; it now pops the stale editor and pushes the one asked for, and makes sure the split view shows its content. | `filters.py` |
| FIX-10 | **done** | The help page wrapped a single `<gui>` in a `<guiseq>`, declared two namespaces it never used, and had no `<revision>` for a translator to work from. | `data/help/` |
| FIX-11 | **done** | `<url type="translate">` pointed at the repository root; it now points at `Docs/localization.md`, which is the page a translator actually wants. | metainfo |

**Translations after this release.** Each locale reports 1411 translated, 108
fuzzy, 75 untranslated, against 1419 / 73 / 34 before. Diffed against the
previous commit, **nothing that was translated lost its translation**: the 79
new untranslated strings are all brand new — 56 of them gschema summaries
that no one has translated — and the fuzzy count grew by 38 because msgmerge
attached translations from renamed strings. Several of those suggestions are
wrong, which is what the fuzzy marker exists for: gettext omits fuzzy entries
from the compiled catalogue, so the user sees English rather than the wrong
translation. `msgattrib --clear-fuzzy` must not be used to tidy the numbers —
it promotes unverified text, and because some suggestions carry format
specifiers that do not match their new original it makes `msgfmt --check`, and
CI, fail outright.

### Not verifiable headlessly — needs a real session

These were the review's Blockers-to-check and could not be run here: dark mode,
high contrast, 1.5× text, a 360 px-wide window, Orca on the charts and the
Catch-Up popover, the help page opening in GNOME Help and its fallback. CSS-01
through CSS-05 and A11Y-01 through A11Y-04 are all in this category: the code
and the rules are checked, the pixels are not.

---

## GNOME review 2026-10-16 (third pass — login, keyring, media, menus)

A second audit of the same tree, at `1c0bfcb` (`Docs/gnome-review.md`), after
the work above had landed. Source and remediation plan:
`Docs/gnome-review.md`, `Docs/gnome-review-remediation-plan.md`. Its fixes
ride along with the 0.2.0 work on this branch rather than on a branch of their
own. Re-check each anchor before acting; the audit's line numbers are for that
revision.

### Blockers

| ID | Status | Finding | Evidence | Notes |
|---|---|---|---|---|
| ARCH-10 | **done** | After account switch/sign-out, login fails until restart: one cancelled `GCancellable` is reused. | `auth.py` `LoginFlow.begin`/`cancel` | Recreate cancellable in `begin()`. Covered by `oauth loopback`. |
| ARCH-11 | **done** | Unreadable keyring at startup deletes the account and Catch-Up snapshots. | `mastodon.py` `load_secret_async`; `window.py` `_activate_account` | Propagate `GError`; delete only on a genuine absence. Covered by `oauth callback`. |
| A11Y-10 | **done** | Attachment tiles are pointer-only (no focus, key, or menu route). | `mediabox.py` grid tiles | Focusable button role + key controller, and a `:focus-visible` ring on `.media-tile` — a tab stop with nothing drawn on it is not keyboard access. Covered by `mediabox keyboard`. |
| PKG-10 | **done** | Suite `| tee` without `pipefail` makes a red suite report green. | `.github/workflows/ci.yml` | `set -o pipefail` in the run block. |
| PKG-11 | **done** | Flatpak `builddir` lint fails on external screenshot URLs (and main had no lint step). | `ci.yml`, `metainfo.xml.in` | Mirror screenshots on build; add `flatpak-builder-lint`. |

### Should fix (queued)

| ID | Status | Finding | Evidence |
|---|---|---|---|
| ARCH-12 | **done** | Cancelled HTTP requests are retried before cancellation surfaces. | `mastodon.py` `_on_response` / `_rest_retry`; `mastodon rest retry` |
| ARCH-13 | **done** | `Secret.Service.get_sync()` blocks the main loop on startup. | `mastodon.py` `_with_secret_service`; coalesced in `streaming helpers` |
| A11Y-11 | **done** | Page Up/Down app accelerators steal keys from every text view. | `main.py` / `window.py` local `ShortcutController` |
| UI-10 | **done** | Composer options row clips at 360 px with 1.5× text. | `compose.py` `build_action_bar` wraps |
| A11Y-12 | **done** | Account switcher button: tooltip, no accessible name. | `window.ui` `account_button` |
| A11Y-13 | **done** | Access keys missing from programmatically built menus (~40 items). | `profileactions.py`, `statusmenu.py`, `conversations.py`, `hashtags.py`, `catchup.py`, `notifications.py`; primary-menu `D` and post-menu `T` duplicates fixed | Keys are unique across a whole open menu, not per section: `statusmenu` moved `Ment_ion`, `M_ute`, `Bloc_k` and `Remove My Post _From Quote` for that reason. The two *section titles* that gained one were reverted — GTK draws those as plain labels and the underscore showed. |
| UI-11 | **done** | Deprecated `present_with_time()` (GTK 4.14). | `searchprovider.py` |
| ARCH-14 | **done** | Plain `connect()` on process-lifetime emitters. | `window.py` LoginFlow/streaming/settings/style/close-request via `connect_weak` |

### Nice to have

| ID | Status | Finding | Evidence | Notes |
|---|---|---|---|---|
| COPY-10 | **done** | Header-case `This` in admin confirmations | `admin.py` resolve/reopen/undo | |
| CSS-10 | **done** | Literal opacities / absolute px in media viewer, emoji picker, Catch-Up | `style.css` | `--dim-opacity` / `--disabled-opacity`; `em` sizes; Catch-Up actions 32 px |
| A11Y-14 | **done** | Poll vote mark lacks accessible name | `statusviews.py` | |
| A11Y-15 | **done** | Account switcher tooltip drops instance | `window.py` `show_shell` / `_sign_out_to_login` | |
| UI-12 | **done** | Media viewer test host used bare `Gtk.Window` | `mediaviewer.py` `present_media` | `Adw.Window` + `set_content` |
| ARCH-15 | **deferred** | App icon in GResource *and* hicolor | `babble.gresource.xml:8-9` | Uninstalled runs resolve the logo through `IconTheme.add_resource_path`; no `meson devenv` yet. Dropping the aliases risks a missing logo outside an install prefix. Action icons stay in the resource. |
| NOTIFY-10 | **done** | Desktop notifications never withdrawn | `desktopnotify.py` | Track ids; `withdraw_all` on reset; clear `babble-action-failed` on success |
| PKG-12 | **done** | Metainfo screenshots on `main`; empty 0.1.0 notes; repo URL casing | `metainfo.xml.in`, `mastodon.py` | Pin screenshot commit; release description; `mauriciobc/Babble` |
| PREF-10 | **done** | Combo rows hand-roll GSettings sync | `preferences.py`, `gschema.xml` | Enum keys 0-based; `_combo_enum` syncs via `get_enum`/`set_enum` (GIO `bind` sees enums as `s`; PyGObject `bind_with_mapping` does not marshal `GValue`). `auto-refresh` stays a string choice (enum nick `0` is illegal). Covered by `preferences schema`. |
| OBS-10 | **observe** | `gdk_frame_timings_submitted` once per process after first primary menu | GDK warning under Xvfb | No Python call site; suite stays `fatal-criticals` only. Leave for upstream / local `fatal-warnings` bisect. |

Duplicate access keys (review 14–15) landed with A11Y-13.

Three follow-ups from the review of this list:

- `PKG-11` mirrors the screenshots without `--compose-url-policy`: the
  runner's flatpak-builder (1.4.2 on noble) does not have that flag, and the
  builddir check reads the mirror-rewritten metainfo anyway.
- `mediabox.py` crossed 800 lines with the keyboard path, so `PollBox` moved
  to `src/pollbox.py` (a poll is a body widget, not an attachment one)
  instead of the module being declared an exception.
- The `dim opacity` work left `style.css`'s `color-mix(… 55% …)` alone, and
  the access keys re-word about 55 strings: both catalogues' fuzzy counts are
  in `Docs/localization.md`.

---

## Tier 2 — Consolidation

"Less code for the same behaviour." Every item is mechanical and testable.
The recurring diagnosis: **a good primitive already exists in `pages.py` or
`statustext.py`, and a later module did not adopt it.**

### 2.1 `pages.py` adoption sweep

Highest line-count win in the register. `pages.call` has **zero** uses in
`compose`, `notifications`, `profile`, `window`, `search`, `preferences`,
`mediaviewer` or `admin`.

| ID | Status | Finding | Evidence | Approx. |
|---|---|---|---|---|
| CLEAN-01 | **done** | **Three hand-rolled "busy → POST → set relationship → toast(+Undo)" blocks** in `profile.py`, plus five near-identical `confirm_* → send_relationship` wrappers. | one `_send_profile_action` now backs `send_relationship` and `send_domain_block`; the `confirm_*` wrappers were left as thin call sites (see note) | ~120 lines |
| CLEAN-02 | **done** | **Two hand-rolled loading/error page pairs** that disagree with each other. | both now `pages.make_loading_page` / `pages.make_error_page` | ~24 lines + 1 string |
| CLEAN-03 | **done** | **`compose.py` re-implemented `show_toast` and `report_error`**, including the same ancestor-overlay-then-window fallback, and imported `summarize_error` solely to do it. | `_show_error` reuses `pages.report_error`'s formatting; the overlay lookup stays local, and why, is now written down | ~15 lines |
| CLEAN-04 | **done** | **Three copies of the icon-button shape**, one of them a 9-of-9-statement duplicate. | `pages.icon_button` gained `css=` and `valign=`; all three call sites use it | ~30 lines |
| CLEAN-05 | **done** | **Three re-implementations of `PagedList.mutate_row`.** | one now; `mutate_row` gained `remove=False` for the in-place case | ~45 lines |
| CLEAN-06 | **done** | **`NotificationsPage` hand-built the loading/error/empty/list stack** that `PagedList` assembles — while the same module used `PagedList` for the sibling requests page. | the two page pairs now share `pages`' builders (the `PagedList` re-parenting itself is CLEAN-08's job) | ~20 lines |

### CLEAN-01…06 resolutions

The slice went as planned, with one finding worth recording: **three of
the six could not be done the obvious way.**

* **CLEAN-03, the composer's toasts.** Routing `_toast` through
  `pages.show_toast` broke 18 checks at once. `show_toast` finds its
  overlay via `window_of(widget)`, which for a widget inside an
  `Adw.Dialog` returns the *window* — not the dialog's own overlay. That
  is the right answer everywhere except the composer, which is a dialog
  covering the window it would have picked. So the overlay lookup stays
  local, the dead `hasattr(root, 'add_toast')` fallback became
  `pages.window_of`, and the comment now records why the composer cannot
  use `pages.show_toast`. A shared helper is only shared once it is right
  for every caller; this one was not, and the tests said so in under a
  minute.
* **CLEAN-04.** `pages.icon_button` did not take the `circular` class or
  a `valign` that two call sites used. Widened the signature rather than
  lose the styling. `mediaviewer` passes `flat=False`, because its
  buttons sit *over* the picture and flattening them there would be wrong.
* **CLEAN-05.** `mutate_row` always removed the row, but answering a
  notification request keeps it and updates it in place. Added
  `remove=False` instead of forking the helper.
* **CLEAN-01.** `send_domain_block` answers with no Relationship at all,
  so it passes an `apply` callback instead of using the default. And
  `set_endorsed` **deliberately stays out** of the shared helper: it has to
  see its own 404 to retry with the other spelling, and that retry has to
  run while the busy state is still held — neither is expressible through
  `pages.call`. Refactoring it there would have been a behaviour change
  dressed as a cleanup.
* The five `confirm_*` wrappers were left as they are. Each is a
  distinct question with distinct wording; collapsing them into a table
  would trade readability for about thirty lines.

### CLEAN-07 and CLEAN-12 resolutions

**CLEAN-07 — six image caches became one `src/imagecache.py`.** What went
into the shared layer: one `Soup.Session` (six connection pools before),
the LRU, the in-flight coalescing, and the rule that a failure is logged
and *not* cached. What stayed with each caller, because it genuinely
differs: the cache key, whether cancellation matters, and what the widget
does with a failure — an avatar falls back to its initials, a media tile
paints the broken-image glyph, the viewer shows a translated sentence.

Two things the consolidation turned up that were not in the finding:

* `HeaderCache` was not merely a duplicate, it was **unbounded** — the
  only one of the six with no cap, so a session over many profiles grew
  without limit. Now `BANNER_CACHE_SIZE`.
* `desktopnotify` and `searchprovider` were reaching into
  `AvatarCache._textures`, a private dict. Both now go through
  `store.get()`, which is the API that exists for exactly this.

Decoding was deliberately **not** moved. It is the part that differs most
between callers, and it is already covered by `timeline`'s tests; moving a
working, tested path would have been risk without reducing duplication.
The shared `Fetcher` takes the decoder as a parameter, and passes the
cache *key* to it so a decoder that needs the box a tile has to fill can
read it — which is how one Fetcher (and so one coalescing table) serves
every tile instead of one per tile.

The tests reached into `AvatarCache._textures` and `_TextureCache._textures`
directly; those three call sites now use `store.put()` and `store.clear()`,
and `Store` gained `clear()` for the purpose rather than a test poking at
`_textures` again.

**CLEAN-12 — two user-visible localisation bugs, not just duplication.**
`search.compact_number` produced `'3.4K'` with an English suffix and an
English decimal point regardless of locale, and `wrapped.shorten` produced
`'1.2k'` — a third spelling of the same suffix, in a page nobody else
sees. Both now delegate to `statustext.compact_count`, which is
locale-aware and translated. The zoom counter was the fourth, and now
uses `locale.format_string('%d%%', …)`, because a French reader writes a
percentage as `100 %`.

`profile.format_count` was left alone deliberately: it is deliberately
*uncompacted* (an exact follower count), which is a different job rather
than a fourth copy.

**CLEAN-09 — four HTML parsers, one removed and two kept deliberately.**
The finding was that `catchupdigest.extract_hashtags` reimplemented what
`parse_content` + `is_hashtag` already do. Implementing it that way breaks
`test-catchup-digest.py`'s `test_no_gi`: `statustext` imports `gi` (for
the 12/24-hour preference behind `clock_format`), and the digest is
specifically built not to need it. **The two parsers also disagree on
purpose** — `extract_hashtags` looks for the `hashtag` class, not a
`/tags/` href, because Phanpy does. So that copy stays, and its docstring
now says so instead of looking like an oversight.

`desktopnotify.html_to_text` was the one that could go, and it had been
**reading script contents into notifications**: its parser kept every
`<script>` body, and while `notification_body` prefers `spoiler_text` for
a content warning, the body text had already been extracted. Over
`parse_content`, `<script>` and `<style>` are dropped. Five new checks in
`test-notifications.py` cover it, including the malformed-markup case
(a body that will not parse must still produce a notification).

`catchuprank`'s parser stays for the same reason as its `parse_time`: it
is pinned to the original JavaScript by the golden scores.

**CLEAN-13 — four error-formatters, and the unwrapping was the actual
duplicate.** `lists.error_text`, `admin.error_text` and `compose._show_error`
each redid `getattr(error, 'message', None) or str(error)` before calling
`summarize_error`, which only took a string. The fix that removes all of
them at once: `summarize_error` now takes a `GLib.Error` *or* a string,
which let ~20 `summarize_error(x.message)` call sites across 12 modules
collapse to `summarize_error(x)`.

`admin` deliberately keeps its own wrapper, because it is not a duplicate:
a 401/403 from a moderation call means the login lacks the admin scopes,
and that is worth a sentence rather than a bare status code. Its
docstring now says so, so it does not read as the same helper again.

**CLEAN-10 — six ISO readers became one, and two were kept on purpose.**
`statustext.parse_time` is now the one reader; `timeline.time_left`,
`timeline.days_since`, `statusviews._parse_time` and `filters._parse_time`
all delegate. This was not only duplication: **`filters._parse_time`
returned None for a stamp with no offset while `timeline` assumed UTC** —
the two readers disagreed about what an API timestamp means.

Two readers stayed, each documented where it is found:

* `catchuprank.parse_timestamp_ms` is pinned to the original JavaScript
  by `test-catchup-rank.py`'s golden scores. A change here that altered a
  score would mean the *port* had drifted, not that the code had been
  improved — so its docstring says exactly that, which is also what keeps
  SKIP-06 honest.
* `filterrules.parse_time` is the same function as `statustext`'s in all
  but name, because `filterrules` has to stay gi-free (CLEAN-01/08). The
  module docstring explains the constraint and names the test that would
  catch the two diverging.

### 2.2 Shared infrastructure that exists N times

| ID | Status | Finding | Evidence | Approx. |
|---|---|---|---|---|
| CLEAN-07 | **done** | **One image cache, implemented six times** — LRU + Soup session + in-flight coalescing. `HeaderCache` is additionally **unbounded** (no LRU cap, unlike the other two), and `desktopnotify` reaches into another module's *private* `_textures` dict. | `timeline.py:912-994` (`AvatarCache`), `timeline.py:1255-1336` (`_TextureCache`, ~80% identical body), `profile.py:155-204` (`HeaderCache`, unbounded), `textemoji.py:~135-181`, `mediaviewer.py:52-107`, `desktopnotify.py:240` | ~250 lines |
| CLEAN-08 | **done** | **The paging state machine, implemented three times** plus a fourth protocol — they had drifted: `PAGE_SIZE` 20/20/**40**; preload threshold 800/800/**600**; refresh cursor `since_id` vs `min_id`. `profile.AccountStatuses` duck-typed via `getattr(self.source, 'paging', None)`. | one `pages.PagedPosts`: page size and preload are 20 and 800 everywhere; the newline cursor differs by argument (`since_id` for timelines, `min_id` for the notification endpoint). `TimelinePage` and `NotificationsPage` now inherit it; `PagedList` keeps its own Link-cursor path because a Link cursor is not a status id. Generation guards exist on all three responses. | ~120 lines |
| CLEAN-09 | **done** | **Four HTML parsers in one repo.** | one gone: `desktopnotify.html_to_text` over `statustext.parse_content`. The other two stay on purpose and are documented where they are found (`catchupdigest` must stay free of `gi`; `catchuprank` is pinned to golden scores), the same shape as CLEAN-10. | ~40 lines |
| CLEAN-10 | **done** | **Six ISO-8601 parsers.** | one `statustext.parse_time`; the Catch-Up port keeps its own on purpose (see resolutions) | ~80 lines |
| CLEAN-11 | **rejected** | **The `client.rate_limited()` gate, "copied three times".** | `timeline.py:4487`, `catchup.py:558`, `:649`, `desktopnotify.py:285`, `:302` | **Not duplication.** The *method* was always shared; what repeats is a two-to-four-line `if`, and each site reacts differently on purpose — the timeline just returns, Catch-Up records a stop reason it shows the user, the notifier keeps a timer alive. A helper wrapping five genuinely different reactions would be indirection, not consolidation. Recorded so this is not re-investigated. |
| CLEAN-12 | **done** | **Four number formatters, two unlocalised.** | three now delegate to `statustext.compact_count`; the zoom counter uses `locale.format_string`. `profile.format_count` stays: it is deliberately uncompacted (an exact follower count), which is a different job, not a duplicate. |
| CLEAN-13 | **done** | **Four error-formatters**, and `search.py` imported `lists` only to reach one. | `summarize_error` now takes a `GLib.Error` *or* a string, so the unwrapping the copies each did lives in one place; ~20 `summarize_error(x.message)` call sites simplified across 12 modules. `admin` keeps its wrapper on purpose (see below). Covered by three new cases in `test-error-summary.py`. |

### CLEAN-08 — one paging state machine

`pages.PagedPosts` now owns the pieces three lists had been keeping
apart: the `_generation` guard, the load/older/refresh requests, the
single 20-item page, the 800px preload check, and the busy flags. The
endpoints differ in only one way that matters, and it is a constructor
argument rather than a copy: whether refresh asks for `since_id`
(timelines) or `min_id` (notifications, as Mastodon requires). Link-paged
lists remain distinct, because a Link cursor is not a status id and the
base cannot fake one. Final resolutions:

* `pages.PagedList` only borrows the base for a place to live and initializes
  the generation guard; its `load`/`load_more`/`refresh` and `_on_scroll`
  keep their own bodies, because a Link-paged end says nothing about the
  last row in the way a status id does.
* The module-level `PAGE_SIZE` in `pages.py` is *still 40* for lists. The base
  uses 20, which is the page size every (status/older, status/newer, status
  refresh) list uses; reusing the list value would have doubled those
  requests.
* Regression coverage: timeline, notification, and thread-profile test scripts
  pass under the consolidated shape.

### 2.3 Translated-string tables (duplication translators can see)

Worse than code duplication: translators meet near-duplicate msgids.

**CLEAN-14…16.** One gi-free `notificationcopy.py` now owns list titles,
desktop title pairs, filter labels and the desktop-preferences rows.
Desktop wording was aligned to the in-app list for the five divergent
types (`follow`, `follow_request`, `update`, the unknown-type fallback).
Context-specific preference headings (`New Followers`, `Poll Results`, …)
stay beside the shared labels they reuse. **CLEAN-16** was already fixed
in code (`pages.make_error_page`); regenerating the catalogue dropped the
stale bare `Try Again` entry.

| ID | Status | Finding | Evidence |
|---|---|---|---|
| CLEAN-14 | **done** | **Two notification title tables for the same 13 types: 8 identical msgids, 5 divergent.** They must be edited in lockstep. | `notifications.TEMPLATES` `notifications.py:75-89` vs `desktopnotify.TITLES` `desktopnotify.py:36-51`. Divergences include `'%s followed you'` vs `'%s now follows you'`, `'%s edited a post you interacted with'` vs `'%s edited a post'`, `'%s interacted with you'` vs `'%s sent a notification'` |
| CLEAN-15 | **done** | **Two notification type-label tables** (17 types vs 12) sharing 11 translated labels. | `notifications.py:106-124` vs `preferences.py:81-94` |
| CLEAN-16 | **done** | **Inconsistent "Try Again" spelling** across three sites — the catalogue has both a mnemonic and a non-mnemonic form. | `pages.py:296`, `profile.py:455` (canonical) vs `notifications.py:1255` |

### 2.4 `timeline.py` local duplication

**CLEAN-17.** Landed: `pages.confirm_remind` (the alt-text reminder stays
a checkbox), `status_picture`, `_rewatch`, `pages.clear_children` for the
five refill loops, and `pages.configure_empty_page` at the window's
timeline places. Left as they are, because they are not copies: the two
reconcile loops (card shells vs boost cards), the toggle handlers (they
already share `_send_toggle` and then diverge), and the weak-ref
trampolines (`pages.weak_method` for signals, a `weak_ref()` argument for
timeouts).



| ID | Status | Finding | Evidence | Approx. |
|---|---|---|---|---|
| CLEAN-17 | **done** | **Nine repeated idioms.** The real copies are `pages.confirm_remind`, `status_picture`, `_rewatch`, `pages.clear_children` and `configure_empty_page`. The reconcile loops, toggle handlers and weak-ref trampolines are different jobs; see the note above. | `pages.py`; `timeline.py` | ~200 lines |
| CLEAN-18 | **done** | **Decodable-attachment-URL selection derived three times** — and the third is a *different, subtly wrong* variant (`preview_url or url` instead of the image-first rule). | `:1672-1674`, `:1989-1997`, `:2383` | ~15 lines |
| CLEAN-19 | **rejected** | **`StatusRow._bind_status` is long, but the steps are not independent.** A filter collapse returns before the body is bound; the content warning decides both the revealer and whether media counts as sensitive; the card is shown only when there is no poll, quote or attachment. A table of gate-and-fill calls would hide those dependencies. The pieces that already repeat (`_bind_indicators`, `_set_time`, `_bind_reply_line`) are already calls. | `timeline.py` `_bind_status` | — |
| CLEAN-20 | **rejected** | **Header size scan stays.** It reads a bounded prefix so a tile-sized image can skip GdkPixbuf (`Gdk.Texture.new_from_bytes`) and an oversized one can be told `set_size` before decode. Measured 2026-10-15: a 400×300 JPEG, peek 0.01 ms vs `PixbufLoader` 0.99 ms; a 3200×2400 JPEG, peek 0.01 ms vs a full loader decode 25 ms. Replacing the scanner with the loader would throw away the fast path the comment in `_decode_covering` exists for. | `timeline.py` `_image_pixel_size`, `_jpeg_pixel_size`, `_decode_covering` | — |
| CLEAN-21 | **done** | **`ApiTimelineSource` fabricates an HTTP response** — builds `'{"data": [], "link": ""}'` as a JSON string solely so `_envelope()` can `json.loads` it back to say "no more pages". Also `_older_request` is assigned but never initialised, then read via `getattr(..., False)`. | `timeline.py:4322-4326`, `:4328`, `:4336`, `:4304-4312` | ~10 lines |
| CLEAN-22 | **done** | **`_send_toggle` bypasses `pages.call`** and hand-builds its error string, so it misses `pages.report_error`'s `summarize_error` bound. | `timeline.py:2800-2834` (string at `:2826`) vs `pages.py:238` | ~10 lines |
| CLEAN-23 | **done** | **`_after_deleted` walks parents for `isinstance(widget, TimelinePage)`** although `_PostBound.host` exists for exactly that. | `timeline.py:3413-3419` vs `:2512` | ~8 lines |

### 2.5 `compose.py`

| ID | Status | Finding | Evidence | Approx. |
|---|---|---|---|---|
| CLEAN-24 | **done** | **Six entry points with an identical three-statement tail** (`_draft_stack` → `update_compose_state` → `grab_focus`), five of six also repeating `set_text` + `place_cursor`. One `_begin_draft(...)` would collapse them. | `:773-793`, `:795-806`, `:808-819`, `:832-848`, `:850-856`, `:858-881` | ~60 lines |
| CLEAN-25 | **done** | **`local-drafts` read-modify-write duplicated in three places**, all racing with no generation guard. Belongs in `drafts.py` beside `MAX_LOCAL_DRAFTS`. | `compose.py:742-746`, `:753-757`, `window.py:436-439` | ~12 lines |
| CLEAN-26 | **done** | **Poll-choice validation runs twice**; `countable_length() > char_limit` duplicates `can_post()`. | `:1405` and `:1583`; `:1396` vs `:929-936` | ~10 lines |
| CLEAN-27 | **done** | **`MAX_LOCAL_DRAFTS` is a dead local constant** — the one use reads `drafts_module.MAX_LOCAL_DRAFTS`, and `drafts` is imported in two styles at `compose.py:27` and `:29` purely for that. | `compose.py:44`, `:746`, `:27` | 3 lines |
| CLEAN-28 | **done** | **`thread_toggle` ("Keep Open for a Thread") has no `toggled` handler and is never cleared by `reset()`**, so it stays active for the rest of the dialog's life including after a successful send. | `compose.py:641-643`, read at `:1629`, reset at `:1671-1704` | behaviour fix |
| CLEAN-29 | **done** | **`AttachmentRow` holds model state on a `Gtk.ListBox`**, and `description` is a second source of truth beside `self.alt_entry.get_text()`. | `compose.py:345-394` (`path` `:354`, `uploaded_id` `:355`, `description` `:356`); copy at `:1340-1341`, read at `:682` and `:1475` | feeds ARCH-01 |

### 2.6 Dead code and dead guards

| ID | Status | Finding | Evidence |
|---|---|---|---|
| CLEAN-30 | **done** | **Guards that can never be false in the shipped app.** The `ImportError` fallbacks around `mediaviewer`, `notifications` and `sysstate` are gone, Catch-Up calls `retry_after_seconds()` directly, and notification refresh/clear report through `pages.report_error`. The composer's `hasattr(root, 'show_toast')` stays: `window_of` returns None unless the root is a Babble window, and a post-sent toast still has to land on a stub root. | `profile.py`, `preferences.py`, `mediaviewer.py`, `catchup.py`, `notifications.py`; kept at `compose.py` `on_post_done` |
| CLEAN-31 | **done** | **Dead surface removed where nothing read it.** `STOPPED_CAP` and `HighlightsView.build_all` were already gone. `_ReadOnlyActions` and the `getattr(timeline, 'StatusActions', None)` branch are gone (the test that deleted the component went with them). `link_card_content` no longer takes `sharers`; news tiles call `link_card_footer_text`. Kept on purpose: `hashtag_summary` (the hashtag row uses it) and `X_WEIGHT_SUMS` (the golden ranker test reads it; CLEAN-32). The new-author boost was already not ported. | `catchupgrid.py`, `explore.py`, `test-catchup-grid.py` |
| CLEAN-32 | **done** | **Mark, do not delete** — the ACC-04 test-only surface. Add a comment saying so. | `catchuprank.py:756`, `:1044`, `:1796-1833`, `:1990-2101` |
| CLEAN-33 | **done** | **`profile.emoji_bank_map` is a redundant cache** — `_rich_text` recomputes `emoji_bank(account)` on every call; the only reads are the two that set it. | `profile.py:630`, `:634`, `:726` |
| CLEAN-34 | **done** | **`profile.py` re-exports four `textemoji` symbols mid-file**, the pattern `CLAUDE.md:45` disclaims for `timeline.py`. | `profile.py:206-213` |
| CLEAN-35 | **done** | **`NotificationsPage.on_hidden` is never called** by the window, yet its docstring describes behaviour that only half-happens (`_shown` stays `True` forever; visibility is decided only by `get_mapped()`). Dead hook with a misleading contract. | `notifications.py:1711-1714` vs `window.py:1260` |

### 2.7 Catch-Up re-derivations

Judged as a **Phanpy port with a different UI** — the port itself is the
best-executed code in the repo and is not a finding. What follows is only the
logic it re-derives from code the app already owns.

| ID | Status | Finding | Evidence |
|---|---|---|---|
| CLEAN-36 | **rejected** | **Catch-Up's paging loop stays.** It pauses between pages, caps the run, and stops with a reason the reader sees (`complete`, `rate-limited`, `cancelled`, `error`). `ApiTimelineSource` has none of that, and D-02 already says the port keeps its own paging because the contract is a snapshot, not a live timeline. Folding it in would either drop the pause or teach the home timeline a policy it does not have. | `catchup.py` `_on_page`, `_wait_then` |
| CLEAN-37 | **done** | **`catchup.order_ids` reimplements `catchupdigest.sort_posts`** over precomputed keys. A test exists *only* to keep the two copies in sync. Deleting the duplicate deletes the test. | `catchup.py:362-377` vs `catchupdigest.py:803-822`; `test-catchup.py:242-253` |
| CLEAN-38 | **done** | **The 6-tuple sort key is spelled three times** in the same positional order, bound only by a comment. | `catchup.py:302-305`, `catchupdigest.py:737`, `:740-760` |
| CLEAN-39 | **done** | **News/hashtag tile content is assembled twice.** `explore.link_card_content` was extracted *specifically* so `NewsTile` could reuse it, but `NewsTile` builds its own — which is why `sharers` is never passed non-`None` in production. Same story for `hashtags.hashtag_summary`. | `explore.py:145-197` vs `catchupgrid.py:1024-1079`; `hashtags.py:90-93` vs `catchupgrid.py:1123-1168` |
| CLEAN-40 | **done** | **Three parallel containers for the same digest payload**, one using `getattr(digest, 'items', None)` defensively as if the contract were optional. | `catchup.py:232-250` (`Computed`), `:57-88` (`Digest`), `catchupgrid.py:439-453` (`_Context`) |
| CLEAN-41 | **done** | **The posts-bar bucketing is computed three times** over the same data. | `catchupdigest.py:828-869`; `catchup.py:319-328`; `catchup.py:2105-2114` |
| CLEAN-42 | **rejected** | **The three post bodies are three layouts.** `CatchUpRow` is one line, three thumbs and counts, and its docstring says it is light on purpose. `PostTile` is a bento cell with overlay, inset, inline and plain forms. `StatusRow` is the full timeline card. The shared interactive part is already `StatusActions` / `StatusMenuButton`. One widget for all three would be a redesign, not a dedup. | `catchup.py` `CatchUpRow`; `catchupgrid.py` `PostTile` |

---

## Tier 3 — Structural

Each is its own piece of work with its own plan. **Sequencing decision is
recorded in the Decision log below — read that before starting anything here.**

| ID | Status | Finding | Evidence | Approx. |
|---|---|---|---|---|
| ARCH-01 | **done** | **`BabbleComposeView` is 1,312 lines and absorbs a whole async job.** `upload_one` / `on_uploaded` / `await_processing` / `_schedule_processing_check` / `_run_processing_check` / `_maybe_send_after_uploads` / `on_upload_failed` = 105 lines whose only widget contact is `self.client` and `_show_error`. State is `_upload_generation`, `_upload_results`, `_upload_failures`, `_upload_expected`, `_upload_cancellable`, `_processing_poll`. That is an `UploadRound` object. Nine "what am I posting" parameters sit as widget attributes, and `snapshot()`/`restore()` are a pure `Draft` ↔ state pair. | `compose.py:395-1706`; job `:1463-1567`; params `:406-424`; draft `:674-685`, `:697-714`; `drafts.Draft` exists at `drafts.py` | ~150 lines moved |
| ARCH-02 | **done** | **`ProfilePage` is 1,282 lines** with ~300 lines of relationship *command* logic (confirm / undo / toast / busy / 404-fallback) inside the widget, plus a `Gio.SimpleActionGroup` and two name-dispatch tables. | `profile.py:397-1678`; commands `:1175-1255`, `:1369-1415`, `:1489-1634`; actions `:1261-1368` | ~300 lines moved |
| ARCH-03 | **done** | **`NotificationsPage` is a controller inside a view.** Nine pieces of controller state as ad-hoc widget attributes; the action group and `Gio.Menu` built inside the widget; three side-channel fetchers and a policy dialog presenter hanging off it; model state written by the *view* onto items while the model reaches back into item privates. | `notifications.py:1212-1808`; state `:1239-1247`; actions `:1337-1383`; fetchers `:1610`, `:1644`, `:1686`; writes `:1115`; reaches `:502`, `:511`, `:520` | ~200 lines moved |
| ARCH-04 | **partial** | **No entity layer.** 1,312 raw `.get('key')` calls on API dicts, no typed entities. Tuba has 30+ classes. This is the root cause of the CLEAN-18 divergence class and of `MERGE_KEYS` / `VISIBILITY_INFO` / `QUOTE_POLICIES` being three private encodings of the same entity facts. **Scoped, not total — see Decision log.** | `rg -oN "\.get\('[a-z_]+'" src/*.py` → 1312 | large |
| ARCH-05 | **done** | **`src/capabilities.py`** — gates are scattered across five modules today. Already in Roadmap Phase 5. Would have caught BUG-04 on day one. | `mastodon.py:155`, `profileedit.py:36-37`, `explore.py:46`, `streaming.py:104`, `timeline.py:2268` | ~150 lines |
| ARCH-06 | **done** | **`timeline.py` is 4,741 lines and is outside the file-size rule.** Five real seams exist: data (`StatusItem`/`TimelineModel`/`RunModel`), row (`StatusRow` + 17 optional parts), media (caches + decode + `MediaBox`), actions (`_PostBound`/`StatusActions`/`StatusMenuButton`), page (`TimelinePage`/`ApiTimelineSource`/`_RefreshGesture`). | `timeline.py`; rule at `test-file-size.py:24-34` | large |
| ARCH-07 | **done** | **REST retry/backoff for 5xx and transport errors.** Exponential backoff with jitter on `_request_async` tasks (`rest_should_retry`, `_maybe_retry`, `_rest_retry`); retries require `_rest_spec` so unit-test fakes still surface errors immediately. `Retry-After` still only pauses pollers. | `mastodon.py` (`REST_RETRY_MAX`, `_on_response`); `test-mastodon-retry.py` | ~60 lines |
| ARCH-08 | **done** | **`GET /api/v1/markers` is never read.** The read marker is written but never restored, so each cycle pays an extra `unread_count` round-trip. | write `mastodon.py:845`; cost `notifications.py:1694`, `:1733` | ~30 lines |
| ARCH-09 | **done** | **`/api/v2/instance` has no cache or in-flight coalescing** and is called from six independent sites. | `window.py:641`, `announcements.py:323`, `admin.py:749`, `report.py:171`, `timeline.py:2283`, `profileedit.py:118` | ~25 lines |
| ARCH-10 | **done** | **A profile open costs ~8 requests**, two of which eagerly pre-load tabs that `Adw.ViewStack` is not showing. | `profile.py:1002-1009`, `:1122-1128` | ~40 lines |
| ARCH-11 | **done** | **Parity: hide replies from non-follows.** Absent, and it is the one reading feature the references agree on (Elk, Tuba, Android all ship it as a per-column toggle). Cheap here because the plumbing already exists — `catchuprank.py:669-685` already batches `/accounts/relationships` in 40s, and `profile.py:956` already shows a "Mutuals" chip. Fits the minimal-reader stance: it *removes* noise. | `catchuprank.py:969-983`; `profile.py:956` | ~120 lines |

---

## Test hardening

Run alongside Tier 1 and Tier 2. Without these, both tiers are unverifiable.

| ID | Status | Finding | Evidence |
|---|---|---|---|
| TEST-01 | **done** | **The file-size rule covers 5 of 46 modules.** Nine modules were over the limit and in neither `SEAMS` nor `EXCEPTIONS` — the rule held for the modules it named and nothing stopped the rest from growing. **Every module over the limit now declares a reason, and an undeclared one fails the test.** | `test-file-size.py` — `EXCEPTIONS` now carries 13 entries with the reason stated (a *split* is named as ARCH-06, ARCH-01…03 so a reader can see it is a plan, not a shrug). A declaration for a module that is gone prints a nudge to drop the entry, so the list cannot rot silently. |
| TEST-02 | **done** | **`install_data` had no test.** A module left off the hand-maintained list is missing from the installed app, and the failure is an ImportError at runtime on a user's machine. Now checked both ways: every module installed, and no entry naming a module that is gone. | `test-file-size.py` (with `re`); verified by removing `filterrules.py` from the list and watching the test catch it |
| TEST-03 | **done** | **`profile.py`'s relationship layer.** `endorse_action_names` and the endorse 404 ladder (retry `pin`, stop on any other error), plus `send_relationship` applying the returned relationship. Driven without a window: `test-profile-relationship.py`. | `profile.py` `set_endorsed`, `send_relationship` |
| TEST-04 | **done** | **`admin.py` (1,780 lines) has zero coverage**, including `permission_allows` / `role_permissions` / `needs_admin_scopes`, which are pure functions over a bitmask and a status code. | `admin.py:134-166` |
| TEST-05 | **done** | **`download()` reports an unparseable address instead of raising.** Soup 3's `Message.new` raises `TypeError` on NULL; `_Download.start` turns that into the invalid-address `GLib.Error` the callback already expected. `_texture_error` is covered in the same file. | `mediaviewer.py` `_Download.start`; `test-mediaviewer.py` |
| TEST-06 | **done** | **`compact_number` delegates to `compact_count`.** The advanced-search dialog round-trips a query, and `_resolve_url` opens a status or a profile and does not ask twice for the same URL. | `test-search.py` |
| TEST-07 | **done** | **Four different error presentations in one module.** Page-1 failure shows an error page; page-2 failure is swallowed entirely; the policy dialog distinguishes a 404; the policy summary fetch swallows every error. Pinned by `test-notifications-errors.py`, which also requires refresh and clear to go through `pages.report_error`. | `notifications.py` `on_page_loaded`, `on_older_loaded`, `on_loaded`, `on_policy_loaded` |
| TEST-08 | **done** | **The version-gate scan now catches version-*string* parsing**, not just `mastodon_api_version()` comparisons, so BUG-04 cannot come back. | `test-version-gates.py` — reads `instance_info['version']` only where it is *parsed* (a `re.*` or `int()` call fed it, directly or through a name bound to it), so `profile.py` and `admin.py` may still show a version to the reader. Verified against a throwaway file holding the old `explore.py` shape, the inline shape, and two innocent version readers: it flags the first two and leaves the other two alone. |
| TEST-09 | **done** | **The suite was load-sensitive.** Every fixed pump is a condition now, and the last two failures were the waits passing on the wrong value, not widget state. | see below | Closed 2026-10-15 with the root causes named; three consecutive green parallel runs after. |

### TEST-09 resolutions, and a correction

**What was fixed.** Three waits that expired before their condition, each
reporting a product bug that did not exist:

* **`catchup grid`.** `at_width()` waited for the new width to land and
  returned; the re-packing runs in `do_size_allocate` on the *next* frame,
  so the assertions read the old column count. Now waits for both.
* **`timeline memory`, body height.** Settled on `get_width() > 0`, but
  the height for that width lands on the following frame — it measured a
  `0` against a real `255`. Now waits for the height. `freed()` also
  collects more than once, since a widget dropped moments earlier can need
  a second pass to break a cycle.
* **`compose widgets`, the clipboard.** Two separate bugs. The deadline
  for the async read was the same `settle_ms` argument the plain-text
  paste used (300 ms), conflating *how long the read may take* with *how
  long to wait when nothing is being waited for*. And `clipboard.set()` is
  a request, not an assignment — a paste issued before the selection has
  processed the new content reads back the *previous* one. Now there are
  two conditions with their own deadlines.

**Correction.** An earlier revision of this file claimed TEST-09 was
`done` and that the suite was "29/29 green across five consecutive full
runs". That was true when written and wrong to leave standing: the paste
deadline *reduced* the flakiness, it did not remove it, and it came back
under load later. The five runs were also unfalsifiable — nothing recorded
that load was low. Compare by stashing and re-running on a clean tree,
which is what actually distinguishes a pre-existing flake from a
regression.

**Passed under load (2026-10-15).** The last race was `clipboard_owns`,
which matched on the *type* on offer. After the first paste the previous
owner is also a text or a texture, so on a busy machine the wait returned
at once and the paste read the old content. It now waits for the clipboard
to be local with a content provider other than the one held before the
set. Checked with one busy-loop per core running throughout:
`compose widgets` passed in 37 s and `timeline memory` in 20 s. Both had
already passed quiet. The widened-post check still allows about a line of
font-dependent difference rather than a fixed 16 px; a stuck narrow-column
height is several times that and fails.

**Root causes, named (2026-10-15).** One more full parallel run
reproduced both `timeline memory` checks again, this time with values:
the shell check read the *placeholder's* 700 px — the `height > 0`
wait was satisfied by the very thing the check says must be gone — and
the body check read an intermediate layout, 267 px against a real 315.
Neither is widget state; both are a wait that lets through the wrong
value. The waits now require the row's cards and the exact comparison
the check later makes, so a genuinely stuck height still fails, after
the deadline. Diagnostic notes stay so a future failure names its
values.

Separate finding from the same work: `compose widgets`' second paste
failed whenever the clipboard had already dropped the image (formats
empty, not local), which a bare X server can do; the test now sets the
clipboard afresh before that second paste.

Three consecutive parallel full-suite runs (36 passed, 1 skipped) after
both fixes.

**`catchup grid` (2026-10-15).** `at_width()` forces a measure/allocate
pass and waits until ``grid.columns`` matches ``columns_for(width)``.
``wait_show_more()`` waits for pages and tiles after invalidation.
``BentoGrid._apply_pending_layout()`` re-packs at fixed width when
``set_pages()`` runs (Show more). Five consecutive Meson runs green;
treat this slice as done for TEST-09.

**`search provider` (2026-10-15).** It timed out at the per-test limit
after every check had passed. Meson recorded `exit status 0` and still
waited out the timeout, which is a held stdout pipe, not a hung assertion.
`Gio.TestDBus.up()` spawns a `dbus-daemon` child of the test process. That
child inherits meson's pipe and outlives `os._exit`, so meson never sees
EOF. `Gio.TestDBus.down()` also blocks in this environment, and an earlier
attempt to bound it from inside `ctx.iteration()` could not: the loop that
calls a blocking function cannot time the function out.

Fixed by opening two private clients on the session bus the runner already
started (`dbus-run-session` in CI) instead of a private daemon, and by
flushing stdout before `os._exit`. A piped run of the file now returns in
about four seconds. `GDK_DEBUG=no-portals`, `NO_AT_BRIDGE`, and
`GTK_A11Y=none` stay on the widget tests so a portal client is not started
for them.

**Deliberately not done: a retry.** Meson 1.12.1 — what this project
builds with — has no per-test `retries` (added in 1.13). Adding it would
have meant bumping the build requirement, which is not a call to make
inside a test-hygiene item. The remaining fix is converting the last fixed
pumps to conditions, which is this item.

---

## Documentation

| ID | Status | Finding | Evidence |
|---|---|---|---|
| DOC-01 | **done** | **`mastodon-compat.md` has five claims the code does not match.** (a) "read once per instance" — six call sites, no cache. (b) the gate covers "`GET /profile`" — that route is never called. (c) `profile._set_endorsed` does not exist; the method is `ProfilePage.set_endorsed`. (d) "`set_endorsed` is the one caller that wants the other policy" — `explore.supports_link_timeline` is a second, undocumented exception. (e) endorse "Introduced 4.4.0" while the gate is effectively 4.6.0. | `mastodon-compat.md:20`, `:22`, `:25`, `:30-36`, `:25` |
| DOC-02 | **done** | **Catch-Up is undocumented outside its own spec.** 7,267 lines of production code, a first-class sidebar place, 103 translatable strings, a dedicated icon, keyboard shortcuts in `shortcuts-dialog.ui`, a preferences switch, 78 lines of CSS — and it appears in **no** user-facing page. `CATCH-UP.md` exists and is excellent; nobody reads it. | `window.py:39`, `:53`, `:63`, `:994-996`; `preferences.py:370`; `catchup.py:2197-2211`; `style.css:539-616`; `po/POTFILES.in:9-10`. Absent from `README.md`, `Docs/features.md`, `Docs/ROADMAP.MD` |
| DOC-03 | **done** | **Shortcuts dialog omits font-size aliases** that are really bound (`<primary>equal`, `KP_Add`, `KP_Subtract`, `KP_0`). | `shortcuts-dialog.ui:138`, `:144`, `:150` vs `main.py:132-141` |
| DOC-04 | **done** | **GSettings summaries for inert preferences read as local settings** — see BUG-09. | `gschema.xml:193-205`; `preferences.py:410-417` |
| DOC-05 | **done** | **Record the profile-open request cost** so ARCH-10's motivation survives. | `profile.py:1002-1128` |

---

## Missing endpoints (assessed, mostly not doing)

Recorded so they are not rediscovered. Under the minimal-opinionated stance,
most are deliberate omissions.

| Endpoint | Status | Assessment |
|---|---|---|
| `POST /api/v1/push/subscription` | deferred | See SKIP-01. Tuba shipped UnifiedPush in 0.11; Phanpy has an 11.7 KB implementation. **Blocked on a product decision, not on effort.** |
| `GET /api/v1/notifications/:id/dismiss` | todo (small) | Only clear-all exists. Per-notification dismiss is cheap and useful. Not yet written up as a BUG. |
| `GET /api/v1/canned_responses` | skip | Mastodon 4.4+ quick replies. Feature-chasing, not noise reduction. |
| Grouped notifications API (`/api/v2/notifications`) | won't | Tuba uses the server-side grouping. Babble clusters client-side **on purpose**. Keep the opinionated choice. |
| `GET /api/v1/preferences`, `GET /api/v1/apps/verify_credentials`, `GET /api/v1/instance/peers`, `with_suspended`, `id[]` batching on notification requests | todo (small) | Genuine but minor. Batch the requests, add `with_suspended`. |
| `GET /api/v1/statuses/:id/card`, `/api/v1/filters/:id/keywords`, `POST /api/v1/statuses/:id/quotes`, `GET /api/v1/timelines/direct` | won't | Confirmed **correctly absent**: removed in 3.0, superseded by v2 filters, done by `quoted_status_id` on post, and superseded by `/conversations`. Noted so they are not mistaken for gaps. |
| `GET /api/v2/filters/:id/statuses`, `GET /api/v1/filters/:id/statuses` | won't | v2 embeds `filter.statuses`, already read at `filters.py:93`. |

---

## Decision log

### D-01 — Minimal and opinionated (confirmed 2026-10-15)

Lean into the Phanpy / Tuba / Elk register rather than the exhaustive Android
one. Consequences: client-side notification clustering stays; feature-chasing
is out; only reference features that *reduce reading noise* are admitted
(`ARCH-11`); the missing-endpoint table above records deliberate omissions so
they are not rediscovered as gaps.

### D-02 — Entity layer **after** mechanical cleanup, and scoped down

**Recommendation: Tier 1 → Tier 2 (+ test hardening) → then ARCH-04, scoped to
`Status`, `Account`, `Notification`, `Relationship` only.**

The reasoning, since this was a judgement call:

*What the entity layer buys.* It makes a whole class of divergence impossible
to write — the CLEAN-18 triple, the `MERGE_KEYS` / `VISIBILITY_INFO` /
`QUOTE_POLICIES` three-encodings problem, and every future "someone added this
field again". It is the biggest single lever in the register. But it fixes
**zero** currently visible bugs and adds no user-visible feature.

*What it costs.* It touches nearly every module, across a codebase whose test
coverage has real holes (`admin.py`, `mediaviewer.py`, `search.py`,
`preferences.py`, `window.py` all untested; the file-size rule covering 5 of 46
modules). A large refactor under that much blind coverage is how silent
breakage happens — most dangerously in `timeline.py`, the hottest path.

*Why cleanup first.* The entity layer's cost scales with how much duplication
is still on disk. Introduce types while `decodable_attachment_url` exists in
three forms and `status_filter` exists in two, and you will type the wrong one
and make the divergence permanent. Cleanup first leaves one obvious target per
concept. And the mechanical pass is independently valuable: it includes nine
real bugs (Tier 1) and makes drift impossible going forward (TEST-01/02).

*Why scoped down.* Fifteen typed entities is a project. Four cover ~90% of the
divergence risk, because they are the ones read from raw dicts in hot paths.
The rest can be added opportunistically as files are already being edited.

*What this costs us.* We accept that some duplication of the *same* concept
survives Tier 2 in the Catch-Up port deliberately — the port keeps its own sort
and paging because it has a different contract (snapshot semantics, inter-page
pause). `catchuprank.py` is a faithful port of a foreign algorithm and should
stay legible as a port; refactoring it for Babble-idiom breaks the golden-diff
test that makes it trustworthy.

---

## Skipped, with reasons

| ID | Status | Item | Reason |
|---|---|---|---|
| SKIP-01 | deferred | **Web Push / UnifiedPush** | All four references have it and it is the single biggest functional gap. **Deferred on principle.** Mastodon Web Push relays notification payloads through a third party, which contradicts a stated position: "Local-first. No telemetry, nothing leaves the machine" (`Docs/ROADMAP.MD`, Principles). Adopt only if we first decide that a relayed push notification is acceptable, or ship a self-hosted relay. Revisit with ARCH-11. |
| SKIP-02 | skip | **Offline store** | Roadmap Phase 2. A large change touching every view. Correctly deferred. |
| SKIP-03 | skip | **Command palette, deck mode, saved workspaces, MPRIS, print/export** | Roadmap Phase 3. Product bets, not improvement findings. Not this register's job. |
| SKIP-04 | skip | **Hover quick actions** | A reading affordance the references have, but it is a UX addition rather than noise reduction. Revisit if ARCH-11 lands and reading ergonomics still feel behind. |
| SKIP-05 | skip | **Column / multi-column layout** | Phanpy and Tuba are column-based; Babble is sidebar + split view. This is a product-position decision, not a defect. Roadmap Phase 3 covers it. |
| SKIP-06 | skip | **Refactoring `catchuprank.py` for Babble idiom** | It is a faithful, well-tested port with golden scores generated from the original TypeScript. Legibility *as a port* is a feature. See D-02. |

---

## Progress

| Tier | Total | todo | doing | done | deferred / skip |
|---|---|---|---|---|---|
| Tier 1 — Correctness | 18 | 0 | 0 | 18 | 0 |
| Tier 2 — Consolidation | 42 | 0 | 0 | 37 | 5 rejected |
| Tier 3 — Structural | 11 | 0 | 0 | 10 | 1 partial |
| Test hardening | 9 | 0 | 0 | 9 | 0 |
| Documentation | 5 | 0 | 0 | 5 | 0 |
| **Total** | **85** | **0** | **0** | **79** | **6 partial/rejected** |

**Tier 1 and Tier 2.1 are complete.** 18/18 bugs fixed (BUG-15…18 were
found by running the app, after the rest), the suite made
trustworthy, and the `pages.py` adoption sweep landed.

**CLEAN-08 is also complete now**: one `pages.PagedPosts` state machine
behind `TimelinePage`, `NotificationsPage` and the `PagedList` base class,
with the endpoint-specific cursor as the only argument between them. Known
flaky tests are excluded from the green count — see TEST-09.

Tier 1 was checked against a 27-test baseline (two new tests: `filter
rules`, `stream events`), across repeated full runs.

Tier 2.1 removed ~254 lines of duplication and left `pages.py` a little
longer, which is the point: it is the module that owns "how a page is
built", and it now actually does. Three of the six items could not be
done the obvious way — the composer's toasts, `icon_button`'s signature,
and `set_endorsed`'s 404 ladder. Those are written up in
**CLEAN-01…06 resolutions**, because each one looks like an oversight if
you only see the final state.

Two things to know before Tier 2 starts:

* **TEST-09 first.** `timeline memory`, `compose widgets` and
  `catchup grid` fail intermittently under load — verified failing on a
  clean tree as well, so they are not a regression from any of this. A
  suite whose failures mean nothing cannot confirm the consolidation.
* The build dir had to be reconfigured: it was configured at
  `…/Code/babble` and the checkout is `…/Code/Babble`, so every test's
  `BABBLE_SRCDIR` pointed at nothing and 25 of 27 failed. `meson setup
  --reconfigure build` fixed it. Worth checking on any machine where the
  directory was renamed.

Separate: 4 accepted, 6 skipped/deferred.

New since the first pass: **TEST-09** (a load-sensitive suite — see the note
below the table).

**Also fixed on the way through:** `set_endorsed` was the last module
comparing `mastodon_api_version()` directly (ACC-02). It now asks
`is_supported(ENDORSE_API_VERSION, when_unknown=True)`, with the `6` named
as a constant and the policy stated at the call site — so the claim in
`mastodon-compat.md` is now true rather than a description of an
accident. The widened version-gate test caught my intermediate attempt at
it, which is the test doing its job.

### Order of attack — in progress

All tiers are done; what follows is the order the work was done in.

1. ~~**Tier 1, bug by bug**~~ — done, 18/18.
2. ~~**TEST-09, the flaky suite**~~ — done. The two recurring failures
   were waits accepting a placeholder or an intermediate height.
3. ~~**Tier 2.1 — the `pages.py` adoption sweep**~~ — done, CLEAN-01…06
   plus TEST-01 and TEST-02.
4. ~~**Tier 2.2 — shared infrastructure** (CLEAN-07…13)~~ — done, except
   **CLEAN-09** (done: one parser removed, two stay on purpose) and
   **CLEAN-11** (rejected: not duplication).
5. ~~**Tier 2.3 — the translated-string tables** (CLEAN-14…16)~~ — done;
   `notificationcopy.py` plus one `babble-pot` run.
6. ~~**Tier 2.4 — `timeline.py` idioms** (CLEAN-17…23)~~ — done.
   CLEAN-19 and CLEAN-20 rejected after looking: the bind steps are not
   independent, and the header scanner is the fast path (measured).
7. ~~**Tier 2.5 — `compose.py`** (CLEAN-24…29)~~ — done.
8. ~~**Tier 2.6 — dead code and guards** (CLEAN-30…35)~~ — done. The
   composer toast `hasattr` stays; see CLEAN-30.
9. ~~**Tier 2.7 — Catch-Up re-derivations** (CLEAN-36…42)~~ — done.
   CLEAN-36 and CLEAN-42 rejected: the paging contract and the three
   post layouts are different on purpose (D-02).
10. **ARCH-10, ARCH-11** — done (deferred profile tabs; home reply filter).
11. ~~**Test the seams**~~ — TEST-03, TEST-05, TEST-06 and TEST-07 are
    done.
12. ~~**ARCH-04, scoped.**~~ — **partial.** `entities.py` lifts Status,
    Account, Notification and Relationship at the API and streaming
    boundary (`mastodon._finish`, `mastodon.request_finish_paged`,
    `streaming`, `profile.set_relationship`), and into the list items
    the read paths already build (`StatusItem._set_data`,
    `NotificationItem.__init__` and `NotificationItem.merge`, and
    `statusactions._merge_status`). `test-entities.py` pins the codes.
    Raw `.get('…')` reads are still about 1,330, most of them on
    Catch-Up's own fixtures and in `admin.py`; adopting the four types
    there is the work that remains. A fifth entity is not part of this
    item.
13. ~~**ARCH-01…03, 06**~~ — done. `composeupload.UploadRound`,
    `profileactions.ProfileActions`, `notificationctrl.NotificationController`,
    and the timeline seams (`timelinedata`, `statusrun`, `mediacache`,
    `mediabox`, `statusactions`, `statusmenu`, `statusrow`,
    `timelinepage`). Callers import those modules directly;
    `timeline.py` is gone. ARCH-07 was already done.

### Maintainers' note

Findings in this register came from a static and structural audit. Two classes
of thing it cannot see and a reviewer should check by hand:

- **Anything performance-related.** `PERFORMANCE.md` and the benchmark in
  `bench/` are the only authority; CLEAN-20 in particular removes a fast path
  and must be measured, not reasoned about.
- **Anything asserted by a test that exists to enforce a duplicate.** Several
  Catch-Up tests assert agreement between two copies (ACC-02, CLEAN-37); when
  you delete a copy, the test goes with it, and its disappearance is not a
  regression.