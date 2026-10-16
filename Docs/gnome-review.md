# Babble — GNOME review: HIG, GTK4/libadwaita, architecture, packaging

*A point-in-time audit of one revision. Findings are evidence-anchored; re-check
an anchor before acting on it, because the code moves and this file does not.*

- **Revision reviewed:** `1c0bfcb` ("Open the help pages that actually open"),
  branch `gnome-HIG-polish`, clean tree. The tree moved during the review
  (`58d9347` → `1c0bfcb`); every line number here is `1c0bfcb`. Date 2026-10-16.
- **Skills applied:** `gnome-hig-review`, `gtk4-libadwaita-ui`,
  `gnome-app-architecture`, `gnome-app-packaging` (all GNOME 51 baseline:
  GTK 4.24, libadwaita 1.10; installed 4.24.1 / 1.10.0).
  `gnome-shell-extensions` does not apply: Babble ships a D-Bus search
  provider, not a Shell extension.
- **Scope:** all of `src/`, `data/`, the meson files, the Flatpak manifest and
  the CI workflow, plus a live run under Xvfb against the real account.
- **How each finding was checked:** six parallel audits (HIG patterns and
  wording, GTK4/libadwaita API and version gates, accessibility and CSS,
  application lifecycle and async, packaging and CI, runtime visuals), then
  first-hand verification of every reported finding against the source. Three
  were settled by running code: an `xdotool` key-injection experiment for
  finding 6, a pre-cancelled `Gio.Cancellable` probe against
  `bench/fixture_server.py` for finding 1, and re-running the CI validators
  (including `flatpak-builder-lint`) for findings 4–5. Severity labels are the
  ones in `gnome-hig-review/references/review-checklist.md` §0.
- **Suite on this revision:** 42 OK, 0 FAIL, 1 skip (`streaming helpers`, no
  secret service in the test environment). Every finding below is something the
  suite cannot see.

## Summary

| # | Severity | Finding | Location |
|---|---|---|---|
| 1 | Blocker | After account switch/sign-out, logging in again is impossible until restart | `auth.py:153,240` + `window.py:175,467,608` |
| 2 | Blocker | Unreadable keyring at startup deletes the account and its Catch-Up snapshots | `mastodon.py:305-310`, `window.py:337-341`, `accounts.py:150-157` |
| 3 | Blocker | Attachments are pointer-only — no keyboard, menu or accelerator route | `mediabox.py:686,696,712` |
| 4 | Blocker (CI) | Suite exit status discarded by `\| tee`, so a red suite reports green | `.github/workflows/ci.yml:71` |
| 5 | Blocker (CI) | `flatpak` job's `builddir` lint cannot pass (`appstream-external-screenshot-url`) | `ci.yml:176-181`, `metainfo.xml.in:33,37` |
| 6 | Should fix | Page Up/Down accelerators eat the key from every focused text view; the guard is dead code | `main.py:144-147,245-250` |
| 7 | Should fix | Composer bottom bar does not adapt: clipped at 360 px with 1.5× text | `compose.py:589,636-655` |
| 8 | Should fix | `Secret.Service.get_sync()` blocks the main loop on the startup path | `mastodon.py:264` |
| 9 | Should fix | Cancelled requests are retried (3 backoffs) before cancellation surfaces | `mastodon.py:568-590` |
| 10 | Should fix | Account switcher button: tooltip, no accessible name | `window.ui:329-330` |
| 11 | Should fix | Access keys missing from every programmatically built menu (~40 items) | `profileactions.py:92-138`, `conversations.py:127`, `statusmenu.py:198`, `hashtags.py:205`, `catchup.py:1281-1297`, `notifications.py:1327-1331` |
| 12 | Should fix | Deprecated `present_with_time()` (GTK 4.14) | `searchprovider.py:600` |
| 13 | Should fix | Plain `connect()` on process-lifetime emitters, against the repo's own leak rule | `window.py:177-182,211-213,263-266,273` |
| 14 | Nice to have | Duplicate access key `D` in the primary menu | `window.ui:498,516` |
| 15 | Nice to have | Duplicate `t`/`T` key in the post overflow menu | `statusmenu.py:160,168` |
| 16 | Nice to have | `this` not header-cased in dialog headings | `admin.py:822,826,1123` |
| 17 | Nice to have | Dim opacity pinned as literals; does not follow `--dim-opacity` under high contrast | `style.css:110,422,558,711` |
| 18 | Nice to have | `24px`/`14px` sizes ignore text scaling; Catch-Up targets 28 px | `style.css:588,752-756,767` |
| 19 | Nice to have | Unnamed `object-select-symbolic` image ("your vote") | `statusviews.py:269` |
| 20 | Nice to have | Account handle truncated at 1400 px and at 1.5×, no tooltip with the instance | `window.ui:329-330` |
| 21 | Nice to have | Bare `Gtk.Window()` fallback host | `mediaviewer.py:1646` |
| 22 | Nice to have | App icon bundled in the GResource *and* installed to hicolor (two copies can drift) | `babble.gresource.xml:8-9` |
| 23 | Nice to have | GNotification never withdrawn | `desktopnotify.py:185` |
| 24 | Nice to have | Metainfo screenshots on the `main` branch; 1090×790; empty 0.1.0 release entry | `metainfo.xml.in:33,37,83` |
| 25 | Nice to have | Combo rows hand-roll GSettings sync where `bind_with_mapping` fits | `preferences.py:212-240` |
| 26 | Observe | `Gdk-WARNING: gdk_frame_timings_submitted() called on submitted frame`, once per process on the first menu open | app stderr |

Verdict: the HIG surface is already in good shape — the previous review rounds
are visible in the code (accessibility labels paired with tooltips,
`Adw.Sidebar`/`AdwNavigationView`/`AdwBreakpoint`, no deprecated widget
anywhere, dark/narrow/large-text mostly holding). What remains is not polish:
two findings lose user data or lock the user out, one makes media unreachable by
keyboard, and the CI gate the workflow calls "the gate" cannot fail.

---

## Blockers

### 1. Login is dead after any account switch or sign-out

`LoginFlow` builds one `Gio.Cancellable` in `__init__` (`auth.py:153`) and never
replaces it; `cancel()` (`auth.py:236-241`) cancels it and only calls
`restart()`. `window.py:175` creates the flow once, and
`_teardown_session()`/`_sign_out_to_login()` cancel it (`window.py:467`,
`window.py:608`) on live paths — account switch, removing the last account.
Every later login passes the same dead object (`auth.py:271,463,517,537,559,596,612`).
A cancelled `GCancellable` stays cancelled.

Rule: `gnome-app-architecture/references/async-and-concurrency.md`
§Cancellation ("one `GCancellable` per owner … per operation").

Proven, not inferred. `MastodonClient.instance_info_async` against
`bench/fixture_server.py`, with the cancellable pre-cancelled and then fresh:

```
pre-cancelled cancellable -> g-io-error-quark: Operation was cancelled
fresh cancellable         -> ok: 'Bench'
```

So after a switch, `POST /apps` fails instantly and `_on_app_registered`
(`auth.py:291-296`) reports "Could not register with <host>: Operation was
cancelled" — no way back without restarting the app. The suite cannot see it:
the stubs build `Gio.Task.new(self, cancellable, callback)` without consulting
the cancellable (`test-oauth-loopback.py:98`), and no test calls `cancel()`
followed by a login.

**Fix:** `self._cancellable = Gio.Cancellable()` in `begin()` (`auth.py:216`),
or rebuild `self.auth` in `_teardown_session()`/`_sign_out_to_login()`.

### 2. A locked keyring silently deletes the account and its snapshots

`load_secret_async` swallows the error (`mastodon.py:305-310`:
`except GLib.Error: secret = None`) and `window.py:337-341` reads `None` as
"no login", calling `remove_account(account)` →
`accounts.forget_account` (`accounts.py:141-157`), which drops the entry from
GSettings **and calls `catchupstore.delete_account()`**, wiping the on-disk
Catch-Up snapshots (followers-only and direct posts). An unlock prompt the user
dismisses at startup therefore destroys stored data with no confirmation. The
docstring at `mastodon.py:303-306` documents "unreadable keyring means signed
out" as deliberate; the deletion is the part that is not defensible, and
`GError` is exactly what distinguishes "no entry" from "cannot read".

Rule: `references/async-and-concurrency.md` §Errors; SKILL rule 9.

**Fix:** report the error through `callback(None, error)`; keep the stored
account in `window.py` (banner/toast: keyring unavailable, sign in again) and
remove it only on a genuinely absent entry.

### 3. Attachments are unreachable without a pointer

Each tile is a bare `Gtk.Overlay` + `GtkPicture` with a `GtkGestureClick`
(`mediabox.py:686-696`), never focusable, no accessible role, no key
controller; the ALT footnotes are the same (`:712`). The overflow menu
(`statusmenu.py` `MENU_ACTIONS`) has no media entry and `main.py` binds no
accelerator, so a keyboard-only user can never open an image or video.

Rule: [Pointer and touch](https://developer.gnome.org/hig/guidelines/pointer-touch.html)
("every pointer action must also be possible with the keyboard");
[GTK 4 accessibility](https://docs.gtk.org/gtk4/section-accessibility.html).
The audio/unknown attachment path already uses a real `Gtk.Button`
(`mediabox.py:723-741`), and `catchupgrid._Tile`
(`catchupgrid.py:441-454, 553-557`) is the in-repo pattern: `focusable=True`,
role `LIST_ITEM`, label, key controller for Return/KP_Enter.

**Fix:** make the tile focusable with a role and label plus an
`EventControllerKey` calling `_open(index)`, or wrap it in a flat
`Gtk.Button`.

### 4. The suite gate cannot fail (CI)

`.github/workflows/ci.yml:71` is `meson test … | tee ci.log` with no
`set -o pipefail`, and the workflow has no `shell:` and no `defaults:`.
GitHub's default is `bash -e {0}`, so the pipeline's status is `tee`'s:

```
$ bash -e -c 'false | tee /tmp/x.log; echo $?'
0
```

The follow-up step (`ci.yml:78-95`) only inspects `SKIP` lines, so a `FAIL` run
with 0 skips leaves both steps green — while `ci.yml:6-8` states "The suite is
the gate".

**Fix:** `set -o pipefail` as the first line of that `run:` block, or
`exit ${PIPESTATUS[0]}`.

### 5. The `flatpak` job's `builddir` lint fails (CI)

Re-ran the CI's own linter against the repo's `build-flatpak`:

```
$ flatpak run --command=flatpak-builder-lint org.flatpak.Builder builddir build-flatpak
{ "errors": [ "appstream-external-screenshot-url" ],
  "info":   [ "appstream-external-screenshot-url: Screenshots are not mirrored to https://dl.flathub.org/media" ] }
```

Cause: the metainfo screenshot URLs point at
`raw.githubusercontent.com/mauriciobc/Babble/main/data/screenshots/…`
(`metainfo.xml.in:33,37`) and the CI builds with bare `flatpak-builder`, which
does not mirror them; the composed catalogue keeps the upstream URLs and the
linter requires `https://dl.flathub.org/media`. The `manifest` check passes
(rc 0); this is the only error.

**Fix:** add
`--mirror-screenshots-url=https://dl.flathub.org/media --compose-url-policy=full`
to the build step (`ci.yml:140`), or build through `/app/bin/flathub-build` as
`references/flatpak.md` recommends. Do **not** use `--user-exceptions`: linter
exceptions are human-only material (`references/flathub-and-circle.md`).

---

## Should fix

### 6. Page Up/Down are dead keys in every text view

`main.py:144-147` binds `app.scroll-page-down/up` to `Page_Down`/`Page_Up`, and
`scroll_page()` (`main.py:245-250`) tries to defer to a focused
`Gtk.Editable`/`Gtk.TextView` by returning early. An application accelerator is
matched before the focused widget, so the deferral cannot happen. Controlled
experiment (Xvfb + `xdotool` key injection into a real `Adw.ApplicationWindow`
with a focused `GtkTextView`):

| Variant | Focused TextView scrolls | App action fires |
|---|---|---|
| App accelerator on `Page_Down` (Babble's shape) | no (`vadj` 0.0 → 0.0) | **yes** |
| No accelerator | yes (0.0 → 6856) | no |
| Window-level `Gtk.ShortcutController`, default (local) scope | yes (0.0 → 6856) | no |

Consequence in Babble: with the composer (GtkSourceView ⊂ Gtk.TextView) or any
other focused text view, Page Down does nothing at all — not the text (it never
sees the key), and not the page (the guard returned).

The same experiment with a focused `Gtk.ListBox` shows the tradeoff the
accelerator was presumably chosen for: leaving the keys unclaimed means list
widgets consume Page Down for row navigation and the page is not paged
(`vadj` 0.0 → 0.0).

**Fix:** move the two bindings to a window-level `Gtk.ShortcutController`
(`Gtk.Shortcut` + `Gtk.NamedAction`), verified above to restore the text-view
behaviour, then decide explicitly whether list paging is worth the dead key in
the composer.

### 7. The composer does not adapt at 360 px with large text

At 360×900 with text at 1.5×:

```
Adwaita-WARNING: AdwBottomSheet … exceeds AdwBreakpointBin width: requested 358 px, 350 px available
```

and the capture shows the sheet's right padding gone with the `2000` counter
flush against the window edge (left padding 24 px, right none). At the default
text size the same window fits. The row is a non-wrapping `Gtk.Box` of
visibility row, language row and `hexpand` counter (`compose.py:636-655`),
while the icon row above it is already an `Adw.WrapBox` (`compose.py:619`).

Rules: [A8](https://developer.gnome.org/hig/guidelines/accessibility.html)
(large text, nothing clipped),
[C6](https://developer.gnome.org/hig/guidelines/adaptive.html) (360×294).

**Fix:** `Adw.BreakpointBin` — the remedy `profile.py:489-492` already applies
to this same failure — or wrap the options row too.

### 8. `Secret.Service.get_sync()` on the main thread

`mastodon.py:264` is reached from functions whose docstrings say "without
blocking the main loop" (`:270`, `:318`) on the startup path
(`window.py:354`); a cold session also activates `gnome-keyring-daemon` there.
Rule: `references/async-and-concurrency.md` §Rule and its D-Bus decision row.

**Fix:** async `Secret.Service.get(...)` with a waiter list — the coalescing
pattern `instance_info_async` already uses in the same file.

### 9. Cancelled requests are retried

`mastodon.py:583-590` hands every transport failure to
`_maybe_retry(transport_error=True)` (`:568-581`), which schedules
`GLib.timeout_add` retries with no `is_cancelled()` test, and `_rest_retry`
(`:552-566`) never checks the task's cancellable. For idempotent methods a
cancelled request therefore schedules up to 3 backoffs (~1 s + 2 s + 4 s,
jittered) before the cancellation reaches the caller. Each retried attempt
aborts instantly (the cancellable is already cancelled), so the cost is a
delayed error and a held task rather than extra network traffic.

Rules: `references/async-and-concurrency.md` §Cancellation; SKILL rule 9
("treat `G_IO_ERROR_CANCELLED` as silent").

**Fix:** before `_maybe_retry`,
`if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
task.return_error(e); return`, plus an early return in `_rest_retry`.

### 10. Account switcher button: tooltip, no accessible name

`window.ui:329-330` gives `GtkMenuButton id="account_button"` a tooltip but no
`<accessibility><property name="label">`, unlike every other icon-only control
in the file (`:48-50`, `:60-62`, `:306-308`, `:317-319`, `:408-410`,
`:418-420`), and `window.py` never sets one (`:127` binds the child, `:255`
sets the popover). A tooltip sets the accessible description, not the name, so
Orca announces an unnamed menu button.

Rule: `references/accessibility-review.md` §1 and §11;
[coding guidelines](https://developer.gnome.org/documentation/guidelines/accessibility/coding-guidelines.html).

**Fix:** add the `accessibility` label block with "Switch Account".

### 11. Access keys missing from every programmatically built menu

About 40 items across `profileactions.py:92-138` (the whole account menu,
including the `Moderation` section title), `conversations.py:127-128`,
`statusmenu.py:198` (`Mention %s`), `hashtags.py:205`,
`catchup.py:1281-1297` (periods and "Previous catch-ups", also sentence case),
`notifications.py:1327-1331` and the 17 type items built from
`notificationcopy.py:94-111`. The app's hand-written menus do it right
(`statusmenu.py:156-157`, `mediaviewer.py:675,683`), and an underscore in a
label *is* the access key (`gtk/gtkmodelbutton.c` sets the label with
mnemonics).

Rules: [Menus](https://developer.gnome.org/hig/patterns/controls/menus.html);
`references/keyboard.md` §5.

**Fix:** prefix a mnemonic in each label, unique per menu — e.g.
`_('_Mark as Read')`, `_('Menti_on %s')` (not `_M`, already taken by
`_('_Mute %s')` in that section).

### 12. Deprecated `present_with_time()`

`searchprovider.py:600` calls `window.present_with_time(timestamp)`.
`/usr/share/gir-1.0/Gtk-4.0.gir` carries `deprecated="1"
deprecated-version="4.14"` on that method, with the documented replacement
`gtk_window_present()`; there is no timestamp-taking successor.

**Fix:** `window.present()` and drop the now-unused parameter (`_activate` and
`_launch_search` keep their D-Bus `timestamp` argument).

### 13. Plain `connect()` on process-lifetime emitters

`window.py:177-182` (six `LoginFlow` signals), `:211-213` (streaming),
`:263-266` (`pages.settings()` and `Adw.StyleManager.get_default()`) and `:273`
(`self.connect('close-request', …)`) are exactly what the repo's own memory
rule forbids (`CLAUDE.md`, "Memory and leak discipline"); the same constructor
uses `pages.connect_weak` two lines later (`:277-279`), and
`test-timeline-memory.py` asserts on pages, rows, dialogs and models, not
windows. Impact is bounded — the window is the app's singleton — but the rule
exists because this pattern leaks.

**Fix:** `pages.connect_weak(self.auth, 'succeeded', …)` and the same for the
other emitters, with the style lambda promoted to a real method.

---

## Nice to have

- **14. Duplicate access key `D` in the primary menu** — `D_raft Posts`
  (`window.ui:498`) and `Admin _Dashboard` (`window.ui:516`). Every other item
  in that menu has a unique key.
- **15. Duplicate key in the post menu** — `View S_tats` (`statusmenu.py:160`)
  and `_Translate` (`statusmenu.py:168`); mnemonics match case-insensitively.
- **16. `this` not header-cased** — `Mark this Report as Resolved?`,
  `Reopen this Report?`, `Undo this Action?` (`admin.py:822,826,1123`)
  capitalize every other word but not the four-letter `this`
  ([writing style](https://developer.gnome.org/hig/guidelines/writing-style.html)).
- **17. Dim opacity pinned as literals** — `style.css:110` copies libadwaita's
  `--dim-opacity: 55%` as a literal; `:422` 70 %, `:558` 0.6, and `:711`
  overrides `.dimmed` on an image. libadwaita raises `--dim-opacity` to 90 %
  under `prefers-contrast: more`, so these stay put where the helper would not.
- **18. Absolute sizes and small targets** — `style.css:588`
  `font-size: 24px` and `:767` `-gtk-icon-size: 14px` are the only absolute
  sizes in a sheet that otherwise uses `em` deliberately (its own comment at
  `:196-207`); `:752-756` drops Catch-Up tile buttons to 28 px where the
  neighbouring pill rule keeps 32 px.
- **19. Unnamed meaningful image** — the poll "you voted" check mark at
  `statusviews.py:269` (`GtkImage.new_from_icon_name('object-select-symbolic')`)
  has no label, where `mediabox.py:124-127` labels the identical idea
  ("Your vote") and many other images are marked `presentation`.
- **20. Account handle truncated with no fallback** — `@mauriciobc@ursal.z…`
  at 1400 px, and at 1.5× the instance is gone entirely; the button's tooltip
  is the static "Switch Account" (`window.ui:329-330`). Put the handle or
  instance in the tooltip.
- **21. Bare `Gtk.Window()` host** — `mediaviewer.py:1646`, the test-only
  fallback in `present_media`; `Adw.Window` takes the same `child` call and
  keeps libadwaita styling.
- **22. Two copies of the app icon** — `babble.gresource.xml:8-9` bundles it
  while `data/icons/meson.build` installs it to hicolor, so the two can drift.
  The skill says app icons belong in hicolor only; verify what the uninstalled
  run's 128 px logo resolves through before removing the aliases.
- **23. Notifications never withdrawn** — the only `GNotification` call in
  `src/` is `send_notification` (`desktopnotify.py:185`); `withdraw_notification`
  appears nowhere, so grouped ids and `babble-action-failed` outlive the state
  they describe (`references/platform-integration.md` §Notifications).
- **24. Metainfo details** — screenshots point at the `main` branch
  (`metainfo.xml.in:33,37`) rather than a tag; both are 1090×790 against a
  1000×700 curation guidance; `0.1.0` has an empty release entry (`:83`).
- **25. Hand-rolled combo sync** — `preferences.py:212-240` implements
  two-way combo↔GSettings mapping where `bind_with_mapping` fits
  (`references/settings-and-resources.md` §3), while the switch rows next to it
  already use `settings.bind` (`:205-208`).
- **26. Runtime warning to bisect** — `Gdk-WARNING **:
  gdk_frame_timings_submitted() called on submitted frame`, reproduced once per
  process, immediately after the first primary-menu click, in both runs. It
  cannot be attributed from Python (GDK exposes no such call). The app's only
  tick callback is `statusrow.py:721`. Make warnings fatal in the test env
  (which currently sets `G_DEBUG=fatal-criticals` only, `src/meson.build:118`)
  and bisect; otherwise it belongs upstream.

---

## Verified clean

Checked and found no finding — recorded so the next review does not redo it.

- **No deprecated GTK/libadwaita widget or API anywhere.** No `GtkDialog`,
  `GtkMessageDialog`, `AdwMessageDialog`, `AdwPreferencesWindow`,
  `AdwAboutWindow`, `GtkShortcutsWindow`, `AdwLeaflet`/`AdwFlap`/`AdwSqueezer`,
  `GtkTreeView`/`GtkIconView`/`GtkComboBox`/`GtkInfoBar`/`GtkSpinner`, no
  `set_titlebar`, no `GtkStyleContext`, no `gtk_widget_show`. Both `.ui` files
  are silent under `GTK_DEBUG=builder`.
- **No API above the declared floor.** A mechanical sweep of `Gtk-4.0.gir` and
  `Adw-1.gir` parsed every class/method/property for its `version` and found
  nothing newer than GTK 4.24 / libadwaita 1.10 among the APIs used; the
  highest thing used is `Adw.Sidebar:prefix`, `version="1.10"` in the GIR —
  exactly the floor, i.e. zero headroom.
- **Actions and keyboard.** `app.quit`, `app.about`, `app.preferences`,
  `app.help` (F1), `window.close` (`<primary>w`), `<primary>comma`; no
  `app.shortcuts` registered by hand (it comes from `AdwApplication` with
  `shortcuts-dialog.ui`); no `Alt+letter` or `Super` bindings; the primary menu
  matches the required order with no Quit/Close and no media-session actions.
- **Settings and resources.** Window width/height/maximized bound rather than
  saved on close (`window.py:184-188`); every key read exists in the schema and
  every schema key is used; the `install_data` list equals the non-test modules;
  all 15 gresource entries exist.
- **i18n.** `from .i18n import gettext as _` everywhere (stdlib `gettext` only
  inside `i18n.py`); `POTFILES.in` complete; no literal user-visible string
  bypasses `_()`; plural forms present.
- **Errors and threads.** No bare `except:`; the three `except Exception` sites
  are commented and hand the failure on; `streaming.py`, `portals.py`,
  `searchprovider.py` and `clipboard.py` all cancel properly with silent
  `CANCELLED`.
- **Accessibility affordances present.** Accessible labels on icon-only
  controls and on dynamic labels (counts, time, run badge, visibility, `+N`,
  unread count, fullscreen); `presentation` on decorative images; `pages.announce()`
  for "N new posts"; timeline rows, Catch-Up tiles, post-text links, the mention
  completer and the media viewer all keyboard-operable with documented keys;
  focus indication never removed.
- **Styling and themes.** No `.dim-label`, `.opaque`, `.large-title`,
  `@define-color`, `shade()`/`alpha()`, `style-dark.css`/`style-hc*.css`; the
  `.ui` breakpoint uses `sp`; one `@media (prefers-reduced-motion: reduce)`
  block disables every animation the sheet defines while keeping the state they
  carried; five `prefers-contrast: more` blocks; dark mode has no white surface
  and holds contrast (verified visually); meaning is never carried by colour
  alone (each with a comment saying why).
- **Runtime.** Zero GTK/Adw criticals and zero warnings beyond Mesa/Xvfb at
  startup under `G_DEBUG=fatal-criticals`; 360 px narrow layout holds at the
  default text size; the primary menu, Preferences, the composer, the post menu
  and the dark-wide window were all captured and inspected.
- **Packaging.** `validate-data.sh` 6/6; `appstreamcli validate --no-net
  --strict`, `desktop-file-validate` and `glib-compile-schemas --strict` pass;
  linter `manifest` rc 0; one app id everywhere; desktop `Exec`/`Icon`/
  `Categories` correct, no generic categories; `finish-args` minimal with
  portals before static permissions; no network during the Flatpak build.
  Nothing in the tree violates the Flathub AI policy (`Docs/flathub.md` already
  records it and the manifest is `x-comment`-labelled as local).
- **Deferred by design, not reported as new** — `Docs/IMPROVEMENTS.md` DEF-07
  (no `-Dprofile=Devel`, hard-coded app id) and DEF-08 (screenshot curation),
  and the documented choice not to pass `GCancellable` through the timeline
  loads (BUG-06/CLEAN-08 generation guards).

## Not checked

- Orca and the accessibility tree (`GTK_DEBUG=accessibility` not run, no screen
  reader): announcement of toasts, banners and `pages.announce()` output is
  unverified, as are the platform names for `AdwEntryRow`/`AdwSidebar` items.
- High contrast visually: `org.gnome.desktop.interface contrast` does not exist
  on the review machine and `Adw.StyleManager:high-contrast` is read-only, so
  the `prefers-contrast: more` branch is source-reviewed only. Note this is a
  test-environment limit, not a defect — `style css` covers the sheet's syntax.
- Large text as a live change: without an XSETTINGS daemon the value is read at
  startup, so 1.5× was tested in a fresh process.
- Pages outside the visual pass: Notifications, Catch-Up, Search, profile,
  thread, media viewer, About/Shortcuts dialogs, the login/welcome screen.
- Flatpak behaviour of the search provider (whether
  `share/gnome-shell/search-providers/*.ini` is exported to the host) and
  libsecret inside the sandbox; `flatpak-builder-lint repo`; a from-scratch
  Flatpak build.
- Mallard page validity (`yelp-check` unavailable) and translation quality
  (`po check` covers format only).
- A keyboard-only walkthrough and focus-order audit end to end (C14), and
  compositor-dependent appearance (no window manager ran: shadows, corner
  rounding, maximised/restored glyph state in the captures are not
  representative).

## Environment notes

- `build/` in this checkout is `root`-owned and was configured with source dir
  `/src`, so `meson compile -C build` and `meson test -C build` cannot run here
  at all. The suite run quoted above used a clean `/tmp` build of the identical
  tree (`meson setup`, `ninja`, then
  `xvfb-run -a dbus-run-session -- meson test -j1 --print-errorlogs`).
- Reproduce the three runtime proofs:
  - cancelled cancellable: a script that calls
    `MastodonClient.instance_info_async(callback, cancellable=c)` with `c`
    pre-cancelled, against `python3 bench/fixture_server.py 5 2026-01-01T00:00:00`
    (the client refuses plain HTTP, so patch `normalize_instance` for the probe).
  - Page Up/Down: an `Adw.Application` with a scrolled `GtkTextView` and an
    action bound three ways (app accel, no accel, window-level
    `Gtk.ShortcutController`), driven with `xdotool key Page_Down` under
    `xvfb-run` with `GDK_BACKEND=x11` (required: `WAYLAND_DISPLAY` is set in
    this session, so `xvfb-run` alone still picks Wayland), `xdotool windowfocus`
    on the window's XID from `GdkX11.X11Surface.get_xid`.
  - CI lint: `flatpak run --command=flatpak-builder-lint org.flatpak.Builder
    builddir build-flatpak` after the CI's own build command.
- Screenshots and scratch artifacts from this review lived under
  `/tmp/babble-review/` (timeline, primary menu, 360 px narrow, dark, composer
  at 1.5× text) and are not kept in the repository.
