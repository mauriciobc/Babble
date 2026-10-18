# AGENTS.md

Babble is a Mastodon client for GNOME: Python with PyGObject on GTK 4.24+
and libadwaita 1.10+ (GNOME 51), Meson as the build system. `CLAUDE.md`
holds the full project context (commands, architecture, conventions) and
is the file to read first. This file records the test harness, which is
the part that is easy to get wrong.

## Test harness

The suite runs under **headless mutter**, not Xvfb. CI and local
verification use the same command:

```sh
XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- \
  mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- \
  meson test -C build -j1
```

- `--virtual-monitor` must be **1280x1024**. `compose widgets` and
  `timeline memory` assert window geometry; xvfb-run's 640x480 default is
  why they used to flake. The geometry tests fail at any other size.
- Xvfb remains a fallback: `xvfb-run -a dbus-run-session -- meson test -C
  build -j1`. Both give 49 passed / 1 skipped (`streaming helpers`, which
  needs a secret service).
- `thread profile render` (`src/test-thread-profile.py`) and `oauth
  callback` (`src/test-oauth-callback.py`) are the exception: each spawns
  its own Xvfb at 1280x1024 and forces `GDK_BACKEND=x11` for a
  deterministic screen. The `Xvfb` binary must therefore be installed
  even though the suite runs under mutter; without it they crash with
  `FileNotFoundError`, they do not skip.
- Display guards in `src/test-*.py` accept `DISPLAY` or `WAYLAND_DISPLAY`,
  so no test silently skips under either setup.
- `src/meson.build` pins `LANG=C`, `LC_ALL=C` and `GSK_RENDERER=cairo` in
  `test_env` and `fatal_test_env`. Translated strings change widget
  geometry, and a GL renderer without a context aborts the process. A test
  that needs a translation sets `LANGUAGE` itself and restarts.
- A new test is a standalone `src/test-*.py` printing `ok - …` / `FAIL - …`
  lines, registered in `src/meson.build` with the same environment. A new
  Python module also goes in the `install_data` list there, or the
  installed app will not have it; the `file size rule` test checks that
  list against `src/*.py`.
