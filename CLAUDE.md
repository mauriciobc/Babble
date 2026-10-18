# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Babble is a Mastodon client for GNOME written in Python with PyGObject, on GTK 4.24+ and libadwaita 1.10+ (GNOME 51), with libsoup 3 for HTTP, libsecret for tokens, and libspelling 0.4+ (on a GtkSourceView 5 buffer) for spell checking in the composer. Meson is the build system. There is no Python packaging, linter config, or pytest suite.

## Commands

```sh
meson setup build                         # once
meson compile -C build                    # builds the gresource bundle, the launcher, and the schemas
meson test -C build                       # all tests
meson test -C build 'timeline memory'     # one test, by the name given in src/meson.build
meson test -C build -v 'compose widgets'  # -v shows the test's ok/FAIL lines
GSETTINGS_SCHEMA_DIR=$PWD/build/data python3 build/src/babble  # uninstalled run; not executable until installed, and only works with the build dir directly inside the source tree (it resolves ../../src). Without the schema dir it aborts: "Settings schema … is not installed"
```

Test names (besides the `validate …` and `po check <lang>` tests): `timeline render`, `timeline memory`, `home cache`, `thread profile render`, `compose widgets`, `notifications render`, `oauth callback`, `oauth loopback`, `error summary`, `streaming helpers`, `account store`, `status text`, `search provider`, `catchup digest`, `catchup rank`, `catchup page`, `catchup grid`, `app lifecycle`, `schedule dialog`, `style css`, `ui strings`.

Four of those are rules rather than behaviour, and each exists because the rule was broken once or is invisible otherwise:

- **`ui strings`** reads every translatable string (via `ast`, so comments and docstrings are never read) and fails on a generic action label ("OK", "Yes", "No", "Done"), a British spelling, `e.g.`/`i.e.`, and `...` where `…` belongs. A value use of a generic word is excepted by the exact source snippet it appears in, so an exception cannot outlive its reason.
- **`style css`** loads `src/style.css` through a real `Gtk.CssProvider` and collects its `parsing-error` signal — which GTK 4.24 does emit, and which is the only way to see a malformed rule, since a bad declaration is otherwise just ignored. It also runs under `G_DEBUG=fatal-warnings`, as a second net for warnings GTK raises outside that signal.
- **`app lifecycle`** asserts the action set: that the app registers no `app.shortcuts` of its own (AdwApplication builds it from the `shortcuts-dialog.ui` resource), that F1 is `app.help` and not the shortcuts dialog, and that `app.preferences` reuses the open dialog.
- **`schedule dialog`** reads back the picker's spin rows' ranges and values, because `AdwSpinRow` is a row that owns a spin button rather than one: it has `get_value` and no `get_value_as_int`, and no `value-changed` signal (`notify::value` instead).

- **First load is measured end to end, not with `load_ms`.** `load_ms` is CPU on a software renderer against a loopback server, so it cannot see what a reader waits for. The wall-clock tracer drives the real window under headless mutter (`mutter --headless --wayland --virtual-monitor 1280x1024 -- <cmd>`, on the GPU renderer) and logs each stage: window constructed, `present()` returned, token read, request sent, response handled, first card painted. Report every run, not a median: single runs spread 60-100 ms.
- Each test is a standalone script, `src/test-*.py`, that prints `ok - …` / `FAIL - …` lines. It exits 77 (a Meson skip) when there is no `DISPLAY` or, for `timeline render` and `thread profile render`, no network: those two fetch live public data from mastodon.social. To run one headless, use `xvfb-run -a meson test -C build '<name>'`.
- Tests import modules as `src.<module>` through `BABBLE_SRCDIR`. Several need `GSETTINGS_SCHEMA_DIR=build/data`, so they need a compiled build tree. They also run with `G_DEBUG=fatal-criticals`, so any GTK critical fails the test.
- A new test is registered in `src/meson.build` with the same env pattern. A new Python module has to be added to the `install_data` list there too, or the installed app won't have it — `file size rule` checks that list against `src/*.py`, so a forgotten module fails the build rather than a user's install.
- **The stylesheet is loaded by AdwApplication, not by us.** `style.css` in the resource is loaded automatically, so do not add a provider for it: a second copy parses twice and the manual one does not take part in libadwaita's dark/contrast/reduced-motion reloads.
- **The help pages are not translated through `po/`.** A Mallard page is its own document; a translation is a translated page beside the English one under `data/help/<lang>/`, added to `help_languages` in `data/meson.build`. `po/babble.pot`'s header is rewritten by xgettext on every regeneration — the bugtracker address comes from `--msgid-bugs-address` in `po/meson.build` for that reason, and anything else typed into that header is lost.

Performance benchmark (timeline workload against a local fixture server, run under Xvfb with the Cairo renderer, median of `BENCH_RUNS` runs, default 5). Besides CPU and RSS it reports main-thread time and stalls (`main_ms`, `stall_max_ms`, `stall_p99_ms`, `stalls_over_16ms`) and `frames_with_empty_shell`; judge smoothness by those, since `cpu_ms` counts every thread:

```sh
./autoresearch.sh            # prints METRIC key=value lines
```

`bench/timeline_bench.py` is the workload, and `bench/fixture_server.py` with `bench/fixtures.py` serve the fake instance. `PERFORMANCE.md` records baseline vs. current figures and why each optimization was made. Read it before changing timeline rendering.

Memory leaks: follow `MEMORY-LEAKS.md`. `python3 bench/memwatch.py --trim` launches the uninstalled app (quit any running copy first; it is single-instance), samples RSS/CPU, and on Enter injects `bench/memprobe.py` through `sys.remote_exec` to report live objects, caches, textures and heap.

Flatpak (local dev manifest only):
```sh
flatpak-builder --user --install --force-clean build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
```
OAuth login redirects to a `127.0.0.1` loopback listener the login opens (`window._start_loopback`), so it needs no setup. Only if that port cannot be bound does it fall back to `babble://`, which needs `xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble` run once on the host; without it that redirect never reaches the app.

## Architecture

- **Launch.** `src/babble.in` is configured into `build/src/babble`. It registers the `babble.gresource` bundle (`window.ui`, `shortcuts-dialog.ui`, `style.css`, icons) and imports the package `babble`. Uninstalled, it binds the name `babble` to `src/` directly. `src/main.py` holds `BabbleApplication` (`Adw.Application`, `HANDLES_OPEN` for the OAuth callback URI, and all accelerators). `src/window.py` holds `BabbleWindow`, a `Gtk.Template` over `window.ui` that owns the login flow, the sidebar, and navigation. `_build_place(tag)` lazily imports and builds each sidebar page.
- **API client.** `src/mastodon.py` `MastodonClient`: one per instance, fully async over a `Soup.Session`. Every operation is a `<name>_async`/`<name>_finish` pair following the Gio.Task convention. The generic call is `request_async`/`request_finish` (`request_finish_paged` also returns the parsed `Link` header). It handles 429 rate limiting, OAuth with PKCE plus `state`, and keyring storage through the `*_secret_async` helpers. Accounts are stored in GSettings (`accounts`, `active-account`) through `src/accounts.py`.
- **Shared page toolkit.** `src/pages.py` is used by every secondary view. It provides the `settings()` singleton, navigation (`push_page`, `make_nav_page`, `window_of`, `client_of`), feedback (`show_toast`, `report_error`, `confirm`), `call()` for one-shot API calls, `PagedList` for Link-paginated boxed lists, `AccountRow`/`AccountListPage`, and `open_link` (routes post/profile/hashtag URLs inside the app). Reuse these instead of writing new plumbing.
- **Status text.** `src/statustext.py` holds `parse_content` (status HTML to safe text/link segments), `relative_time`, `shorten_url_text`, `is_hashtag`, `clock_format()` and `format_time()`. It builds no widget; it uses GLib/Gio only, so content parsing and absolute time formatting work without a display. Use `format_time()` for absolute dates/times instead of hardcoding `%H:%M`; it reads GNOME's 12/24-hour preference through the Settings portal.
- **Timeline core.** There is no `timeline.py`; import from the module that defines the name. It was split along five seams (ARCH-06), and `test-file-size.py` keeps each under the 800-line limit:
  - Data, `src/timelinedata.py`: `StatusItem` (GObject wrapping a status dict, emits `changed` on in-place edits) and `TimelineModel` (`Gio.ListModel`). `src/statusrun.py` holds `RunModel`, which groups consecutive same-thread posts by one author into a `StatusRun` and emits minimal splices, and `StatusRunRow`, the list shell. Shared constants, `status_filter` and time helpers are in `src/timelinebase.py`.
  - View: `src/timelinepage.py` holds `TimelinePage` (a `Gtk.ListView` with `TimelineFactory`) and `ApiTimelineSource`, which adapts any Status-array endpoint with `paging='id'|'link'|'none'`. Each list item is a `StatusRunRow` containing one or more `StatusRow` cards (`src/statusrow.py`). The action bar and overflow menu are `StatusActions` (`src/statusactions.py`) and `StatusMenuButton` (`src/statusmenu.py`), shared with Catch-Up's tiles.
  - Media: `src/mediacache.py` (`AvatarCache`, `_TextureCache`, scaled decode, capped size) and `src/mediabox.py` (`MediaBox`, `BoostCarousel`, previews). The poll widget is `src/pollbox.py` (`PollBox`). Post text is rendered by `InlineEmojiText` from `src/textemoji.py` (a `Gtk.TextView` with inline custom emoji and height-for-width).
  - Other views (notifications, profile, thread, hashtags, lists, …) reuse `StatusRow` and `TimelinePage`.
- **Streaming.** `src/streaming.py` `StreamingManager` multiplexes Mastodon streaming subscriptions (user, public, list, hashtag) and feeds inserts, edits, and deletes into open pages.
- **i18n.** Always import `from .i18n import gettext as _` (and `ngettext`), never the stdlib `gettext` directly. The cached catalogue is a measured hot-path optimization. Use `ngettext` for any count, and add a `# Translators:` comment right above the `_()` line for terse or format strings.
- **Translations.** `po/LINGUAS` lists de, es, fr and pt_BR. After changing strings, run `meson compile -C build babble-pot` and then `babble-update-po`. The `po check <lang>` tests run `msgfmt --check-format`; `msgfmt` and the other validators are `required: false` in meson so a missing one warns instead of breaking the build — `.github/workflows/ci.yml` installs them and fails the run on a skipped test, which is where the guarantee lives. The `interface-language` key (Preferences → Language) is applied in `src/babble.in` by setting `LANGUAGE` before anything translates, which is why it only takes effect on restart. A new language also goes into `INTERFACE_LANGUAGES` in `src/preferences.py`.

## Conventions that matter

- **Memory and leak discipline (important).** When a widget connects a signal on something it owns to one of its own methods, use `pages.connect_weak(emitter, signal, self.method)` or `pages.weak_method(...)`, not `emitter.connect(signal, self.method)`. A plain connect forms a C-level cycle that Python's GC cannot see, and the row, page, or dialog leaks. These helpers use GObject weak refs, not Python `weakref`, because PyGObject 3.58 re-creates wrappers of GTK-held widgets. `src/test-timeline-memory.py` checks that pages and rows are actually freed. Run it after touching timeline, notifications, profile, or paged views.
- Read-only `InlineEmojiText` bodies are pinned to `gtk-im-context-simple` on purpose (an IBus context leaked them). Don't make them editable without revisiting that.
- `StatusRow` builds optional parts (boost strip, CW bar, carousel, overflow menu, …) lazily. Keep new row features lazy, and prefer mutating the existing `StatusItem` and emitting `changed` over replacing items in the model.
- In `src/style.css`, use libadwaita palette variables (`var(--card-bg-color)`, `var(--border-color)`, …), not deprecated `shade()`/`alpha()`/`@named_colors`.
- Files carry `# SPDX-License-Identifier: GPL-3.0-or-later`. Imports after `gi.require_version` are marked `# noqa: E402`.
- App ID and resource base path: `io.github.mauriciobc.Babble` and `/io/github/mauriciobc/Babble`. GSettings keys live in `data/io.github.mauriciobc.Babble.gschema.xml`. Add the key there before reading it.
- **An account is `(id, instance)`, never the id alone.** Mastodon ids are server-local, so two instances both hand out `42`. `accounts.account_key()` builds the `'<instance>#<id>'` name a stored account is switched and removed under; `active-account` holds that key. A bare id left over from before the pairing still resolves when it is unambiguous.
- **Style is `ruff check src/`**, configured in `ruff.toml`. It encodes what the code already does: `%` formatting is kept on purpose, and `E402` is off because every module calls `gi.require_version()` between its imports.
- **File size.** The modules the window was broken into — `portals.py`, `auth.py`, `drafts.py`, `clipboard.py` — stay under 800 lines, enforced by the `file size rule` test rather than a review convention, because ruff has no module-length rule and a silent limit is one nobody re-reads. **`window.py` is a declared exception** at ~1650 lines: four of five seams are out, the fifth (streaming) is already split at the transport (`streaming.py` imports nothing from window or timeline), and what remains is view routing over page containers the window owns — moving it would invert that dependency rather than cut a seam. Reach for a new module when a cluster has a boundary, not to move a line count.
- **Capability gating.** Ask the client, never the dict: `client.mastodon_api_version()` returns the instance's `api_versions.mastodon`, or `None` when the server says nothing — 0 and None are not the same answer, and only one of them means "Mastodon that old". `client.is_supported(min_version, when_unknown=False)` collapses the tri-state with the caller's policy, so a call site does not spell out `is True`. Every gate constant lives in `src/capabilities.py` (`COLLECTIONS_API_VERSION = 10`, `PROFILE_TABS_API_VERSION = 8`, `PROFILE_ALT_TEXT_API_VERSION = 9`, `ENDORSE_API_VERSION = 6`, `LINK_TIMELINE_API_VERSION = 2`). `Docs/mastodon-compat.md` is the list; the `capability gates` test is the reason the parse cannot drift back to three different spellings, and `version gate call sites` is the reason a caller cannot compare the accessor directly instead of asking it.
- **OAuth.** The login asks for `read write` and nothing else — every requested scope is listed on the instance's consent screen, so the admin scopes belong only in the extra authorization `window.authorize_admin()` runs. (`follow` was dropped when Mastodon 3.5 deprecated it; `read`/`write` cover every granular follow, block and mute scope.) Scope strings must agree between `register_app_async(scopes=…)` and `get_authorize_url(scopes=…)`; Mastodon refuses the authorization otherwise, and one unrecognised scope fails the whole registration with a 422.

## Flathub policy (from README)

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only. A manifest submitted to Flathub must be written by a human with no AI assistance. Do not write or automate Flathub pull requests, commit messages, descriptions, or review replies.
