# Babble

A GNOME application built with GTK 4 and libadwaita.

Babble is a Mastodon client for the GNOME desktop: timelines, thread and profile views,
notifications, a composer with media upload, content warnings, polls and drafts, and
preferences backed by GSettings. Accounts sign in with Mastodon's OAuth 2.0 flow and
tokens are kept in the keyring.

`src/mastodon.py` is the API client (libsoup 3, asynchronous), `src/window.py` the shell
and navigation, and the pages live in `src/timeline.py`, `src/notifications.py`,
`src/thread.py`, `src/profile.py` and `src/compose.py`. Custom emoji in post and profile
text are rendered inline by `src/textemoji.py`.

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

## Flathub

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only.

- A manifest submitted to Flathub must be written by a human. It must not contain AI-generated or AI-assisted content, and disclosure does not exempt it.
- Disclose AI-generated application code or documentation in any Flathub submission.
- Do not use an AI agent to open or automate Flathub pull requests, or to write their commit messages, descriptions, review comments or replies.
