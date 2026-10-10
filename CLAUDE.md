# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Babble is a Mastodon client for GNOME written in Python with PyGObject, on GTK 4.24+ and libadwaita 1.10+ (GNOME 51), with libsoup 3 for HTTP and libsecret for tokens. Meson is the build system. There is no Python packaging, linter config, or pytest suite.

## Commands

```sh
meson setup build                         # once
meson compile -C build                    # builds the gresource bundle, the launcher, and the schemas
meson test -C build                       # all tests
meson test -C build 'timeline memory'     # one test, by the name given in src/meson.build
meson test -C build -v 'compose widgets'  # -v shows the test's ok/FAIL lines
python3 build/src/babble                  # uninstalled run; not executable until installed, and only works with the build dir directly inside the source tree (it resolves ../../src)
```

Test names (besides the `validate …` and `po check <lang>` tests): `timeline render`, `timeline memory`, `thread profile render`, `compose widgets`, `notifications render`, `oauth callback`, `error summary`, `streaming helpers`.

- Each test is a standalone script, `src/test-*.py`, that prints `ok - …` / `FAIL - …` lines. It exits 77 (a Meson skip) when there is no `DISPLAY` or, for `timeline render` and `thread profile render`, no network: those two fetch live public data from mastodon.social. To run one headless, use `xvfb-run -a meson test -C build '<name>'`.
- Tests import modules as `src.<module>` through `BABBLE_SRCDIR`. Several need `GSETTINGS_SCHEMA_DIR=build/data`, so they need a compiled build tree. They also run with `G_DEBUG=fatal-criticals`, so any GTK critical fails the test.
- A new test is registered in `src/meson.build` with the same env pattern. A new Python module has to be added to the `install_data` list there too, or the installed app won't have it.

Performance benchmark (timeline workload against a local fixture server, run under Xvfb with the Cairo renderer, median of `BENCH_RUNS` runs, default 3):

```sh
./autoresearch.sh            # prints METRIC key=value lines
```

`bench/timeline_bench.py` is the workload, and `bench/fixture_server.py` with `bench/fixtures.py` serve the fake instance. `PERFORMANCE.md` records baseline vs. current figures and why each optimization was made. Read it before changing timeline rendering.

Flatpak (local dev manifest only):
```sh
flatpak-builder --user --install --force-clean build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
```
OAuth login needs `xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble` run once on the host. Without it the `babble://` redirect never reaches the app.

## Architecture

- **Launch.** `src/babble.in` is configured into `build/src/babble`. It registers the `babble.gresource` bundle (`window.ui`, `shortcuts-dialog.ui`, `style.css`, icons) and imports the package `babble`. Uninstalled, it binds the name `babble` to `src/` directly. `src/main.py` holds `BabbleApplication` (`Adw.Application`, `HANDLES_OPEN` for the OAuth callback URI, and all accelerators). `src/window.py` holds `BabbleWindow`, a `Gtk.Template` over `window.ui` that owns the login flow, the sidebar, and navigation. `_build_place(tag)` lazily imports and builds each sidebar page.
- **API client.** `src/mastodon.py` `MastodonClient`: one per instance, fully async over a `Soup.Session`. Every operation is a `<name>_async`/`<name>_finish` pair following the Gio.Task convention. The generic call is `request_async`/`request_finish` (`request_finish_paged` also returns the parsed `Link` header). It handles 429 rate limiting, OAuth with PKCE plus `state`, and keyring storage through the `*_secret_async` helpers. Accounts are stored in GSettings (`accounts`, `active-account`) through `src/accounts.py`.
- **Shared page toolkit.** `src/pages.py` is used by every secondary view. It provides the `settings()` singleton, navigation (`push_page`, `make_nav_page`, `window_of`, `client_of`), feedback (`show_toast`, `report_error`, `confirm`), `call()` for one-shot API calls, `PagedList` for Link-paginated boxed lists, `AccountRow`/`AccountListPage`, and `open_link` (routes post/profile/hashtag URLs inside the app). Reuse these instead of writing new plumbing.
- **Timeline core.** `src/timeline.py` is the biggest and hottest module:
  - Data: `StatusItem` (GObject wrapping a status dict, emits `changed` on in-place edits), `TimelineModel` (`Gio.ListModel`), and `RunModel`, which groups consecutive same-thread posts by one author into a `StatusRun` and emits minimal splices.
  - View: `TimelinePage` holds a `Gtk.ListView` with `TimelineFactory`. Each list item is a `StatusRunRow` containing one or more `StatusRow` cards. `ApiTimelineSource` adapts any Status-array endpoint, with `paging='id'|'link'|'none'`.
  - Other parts: post text is rendered by `InlineEmojiText` from `src/textemoji.py` (a `Gtk.TextView` with inline custom emoji and height-for-width). Images go through `AvatarCache` and `_TextureCache` (scaled decode, capped size).
  - Other views (notifications, profile, thread, hashtags, lists, …) reuse `StatusRow` and `TimelinePage`.
- **Streaming.** `src/streaming.py` `StreamingManager` multiplexes Mastodon streaming subscriptions (user, public, list, hashtag) and feeds inserts, edits, and deletes into open pages.
- **i18n.** Always import `from .i18n import gettext as _` (and `ngettext`), never the stdlib `gettext` directly. The cached catalogue is a measured hot-path optimization. Use `ngettext` for any count, and add a `# Translators:` comment right above the `_()` line for terse or format strings.
- **Translations.** `po/LINGUAS` lists de, es, fr and pt_BR. After changing strings, run `meson compile -C build babble-pot` and then `babble-update-po`. The `po check <lang>` tests run `msgfmt --check-format`. The `interface-language` key (Preferences → Language) is applied in `src/babble.in` by setting `LANGUAGE` before anything translates, which is why it only takes effect on restart. A new language also goes into `INTERFACE_LANGUAGES` in `src/preferences.py`.

## Conventions that matter

- **Memory and leak discipline (important).** When a widget connects a signal on something it owns to one of its own methods, use `pages.connect_weak(emitter, signal, self.method)` or `pages.weak_method(...)`, not `emitter.connect(signal, self.method)`. A plain connect forms a C-level cycle that Python's GC cannot see, and the row, page, or dialog leaks. These helpers use GObject weak refs, not Python `weakref`, because PyGObject 3.58 re-creates wrappers of GTK-held widgets. `src/test-timeline-memory.py` checks that pages and rows are actually freed. Run it after touching timeline, notifications, profile, or paged views.
- Read-only `InlineEmojiText` bodies are pinned to `gtk-im-context-simple` on purpose (an IBus context leaked them). Don't make them editable without revisiting that.
- `StatusRow` builds optional parts (boost strip, CW bar, carousel, overflow menu, …) lazily. Keep new row features lazy, and prefer mutating the existing `StatusItem` and emitting `changed` over replacing items in the model.
- In `src/style.css`, use libadwaita palette variables (`var(--card-bg-color)`, `var(--border-color)`, …), not deprecated `shade()`/`alpha()`/`@named_colors`.
- Files carry `# SPDX-License-Identifier: GPL-3.0-or-later`. Imports after `gi.require_version` are marked `# noqa: E402`.
- App ID and resource base path: `io.github.mauriciobc.Babble` and `/io/github/mauriciobc/Babble`. GSettings keys live in `data/io.github.mauriciobc.Babble.gschema.xml`. Add the key there before reading it.

## Flathub policy (from README)

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only. A manifest submitted to Flathub must be written by a human with no AI assistance. Do not write or automate Flathub pull requests, commit messages, descriptions, or review replies.
