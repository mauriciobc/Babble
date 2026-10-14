# Building Babble

## Dependencies

| | |
|---|---|
| GTK | 4.24+ |
| libadwaita | 1.10+ (GNOME 51) |
| GtkSourceView | 5, with libspelling 0.4+ |
| libsoup | 3 |
| libsecret | for the token keyring |
| meson | 1.0+ |
| Python | 3, with PyGObject |

The GNOME 51 runtime does not ship libspelling, so the development Flatpak
manifest builds it from source. On a host, install it from your distribution
(`libspelling` / `libspelling-devel`).

## Building

```sh
meson setup build
meson compile -C build
meson test -C build
meson install -C build
```

`python3 build/src/babble` runs the uninstalled build. It is not executable
until installed, and it only works with the build directory directly inside
the source tree (it resolves `../../src`).

## Flatpak

The manifest at `build-aux/flatpak/io.github.mauriciobc.Babble.json` is a
local development manifest only — see [flathub.md](flathub.md).

```sh
flatpak-builder --user --install --force-clean \
    build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
flatpak run io.github.mauriciobc.Babble
```

CI builds this manifest on every push, so a renamed source URL or a moved
tarball fails there rather than on someone's machine.

## Tests

`meson test -C build` runs everything: the meson tests plus the
desktop/metainfo/gschema/catalogue validators.

```sh
meson test -C build                        # everything
meson test -C build 'timeline memory'      # one test, by name
meson test -C build -v 'compose widgets'   # -v shows each test's ok/FAIL lines
xvfb-run -a meson test -C build            # headless
```

Each test is a standalone `src/test-*.py` printing `ok - …` / `FAIL - …`
lines. It exits 77 (a Meson skip) when there is no `DISPLAY`, or — for
`timeline render` and `thread profile render` — no network, since those two
fetch live public data from mastodon.social.

A new test is registered in `src/meson.build` with the same environment
pattern. **A new Python module must also be added to the `install_data` list
there**, or the installed app will not have it, and listed in
`po/POTFILES.in` if it contains translatable strings.

## Benchmarking and profiling

```sh
./autoresearch.sh    # timeline workload against a local fixture server
```

Judge smoothness by the main-thread figures (`main_ms`, `stall_max_ms`,
`stall_p99_ms`, `stalls_over_16ms`), not `cpu_ms`, which counts every thread.
`PERFORMANCE.md` records the baselines and why each optimization was made.

Memory: follow `MEMORY-LEAKS.md`. `python3 bench/memwatch.py --trim` launches
the uninstalled app and reports live objects, caches, textures and heap on
Enter. The app is single-instance, so quit any running copy first.
