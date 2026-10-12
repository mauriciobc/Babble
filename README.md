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

Requires GTK 4.24+, libadwaita 1.10+ (GNOME 51), meson 1.0+, Python 3 with PyGObject, libsoup 3, libsecret, and GtkSourceView 5 with libspelling 0.4+ for spell checking in the composer. The GNOME 51 runtime has no libspelling, so the development Flatpak manifest builds it.

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

Logging in needs no setup: Babble listens on a `127.0.0.1` port for the length
of the login, registers `http://127.0.0.1:<port>/callback` as the OAuth redirect,
and the browser hands the authorization straight back to it. The tab then says
it can be closed.

Only when no loopback port can be opened does the login fall back to a
`babble://` redirect, which needs one step once per machine:

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
as well. "Enter Authorization Code…" on the login page works without either
redirect.

## Desktop integration

- **Paste and drop media**: paste a screenshot (Print Screen copies it) or
  copied files into the composer, or drag images and videos onto it.
- **Take Screenshot**: the composer's camera button takes a screenshot
  through the Screenshot portal and attaches it (hidden when the portal is
  missing).
- **Spell checking**: misspelled words in the composer are underlined, with
  corrections on right-click, in the language picked for the post. With no
  dictionary installed for that language, spell checking is off.
- **Open With Babble**: images and videos opened with Babble from Files
  start a new post with them attached.
- **Notification buttons**: Favourite, Boost and Follow Back act straight
  from the notification.
- **Launcher actions**: right-click the app icon for New Post or
  Notifications.
- **Search**: accounts, hashtags and posts show up in the Activities
  overview search (enable Babble under Settings → Search).
- **Background status**: with Run in Background on, Quick Settings shows
  how many mentions arrived.
- **Quick Post Shortcut** (Preferences): a system-wide Super+Shift+M
  opens the composer through the GlobalShortcuts portal.

D-Bus activation, the search provider and the launcher actions only work
from an installed build (`meson install` or Flatpak), since GNOME finds
them through the installed `.desktop`, D-Bus service and search provider
files.

## Translations

Babble ships Brazilian Portuguese, German, French and Spanish. These were
machine-assisted and still need review by native speakers; corrections are
welcome. The interface language follows the system unless one is chosen in
Preferences → General → Language, which applies on the next start.

In the Flatpak, Babble's own translations ship inside the app
(`"separate-locales": false`), so every language in the picker works whatever
the system language is. The few strings that come from GTK and libadwaita
themselves live in the runtime's locale extension, which only installs the
system's languages. To get those too, add the language to Flatpak with
`flatpak config --set extra-languages de` (for example) and run
`flatpak update`.

To update the catalogues after changing strings:

```sh
meson compile -C build babble-pot          # regenerate po/babble.pot
meson compile -C build babble-update-po    # merge it into every po/*.po
```

To add a language, create `po/<lang>.po` from the template
(`msginit -l <lang> -i po/babble.pot -o po/<lang>.po`), list it in
`po/LINGUAS`, and add it, named in its own language, to
`INTERFACE_LANGUAGES` in `src/preferences.py`.

## Flathub

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only.

- A manifest submitted to Flathub must be written by a human. It must not contain AI-generated or AI-assisted content, and disclosure does not exempt it.
- Disclose AI-generated application code or documentation in any Flathub submission.
- Do not use an AI agent to open or automate Flathub pull requests, or to write their commit messages, descriptions, review comments or replies.
