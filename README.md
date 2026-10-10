# Babble

A GNOME application built with GTK 4 and libadwaita.

Babble is a Mastodon client for the GNOME desktop: timelines, thread and profile views,
notifications, a composer with media upload, content warnings, polls and drafts, and
preferences backed by GSettings. Accounts sign in with Mastodon's OAuth 2.0
authorization-code flow using PKCE (RFC 7636) and a per-attempt `state`, and tokens are
kept in the system keyring through libsecret without blocking the main loop.

`src/mastodon.py` is the API client (libsoup 3, asynchronous), `src/window.py` the shell
and navigation, and the pages live in `src/timeline.py`, `src/notifications.py`,
`src/thread.py`, `src/profile.py` and `src/compose.py`. Custom emoji in post and profile
text are rendered inline by `src/textemoji.py`: the instance's whole set is fetched once
per session and used as the fallback under whatever each payload declares, so a
`:shortcode:` resolves wherever it appears.

## Build and run

Requires GTK 4.24+, libadwaita 1.10+ (GNOME 51), meson 1.0+, Python 3 with PyGObject, libsoup 3 and libsecret.

```sh
meson setup build
meson compile -C build
meson test -C build
meson install -C build
```

Local Flatpak build against the GNOME 51 runtime:

```sh
flatpak-builder --user --install --force-clean build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
flatpak run io.github.mauriciobc.Babble
```

One step is needed once per machine before logging in works, because the OAuth
redirect comes back to the desktop as a `babble://` link:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

The desktop entry already advertises `x-scheme-handler/babble`, but that alone is
not enough: GLib only resolves a URI scheme when it is also listed in
`~/.config/mimeapps.list`, which is what `xdg-mime default` writes. Without the
entry, `xdg-open babble://…` — and the GTK launcher every browser uses to hand a
custom scheme to its app — fails with "Unsupported location specified". The
authorize page opens in the browser, but the callback finds no application and
Babble is never brought back. Flatpak cannot write the host's `mimeapps.list` from
inside the sandbox, so this has to run on the host. It applies to `meson install`
as well.

## Flathub

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only.

- A manifest submitted to Flathub must be written by a human. It must not contain AI-generated or AI-assisted content, and disclosure does not exempt it.
- Disclose AI-generated application code or documentation in any Flathub submission.
- Do not use an AI agent to open or automate Flathub pull requests, or to write their commit messages, descriptions, review comments or replies.
