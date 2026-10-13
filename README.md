# Babble

*A cozy little Mastodon client for GNOME.*

Babble lets you read the fediverse the way you'd read a good book by the window:
timelines that scroll smoothly, threads that unfold nicely, and a composer that
stays out of your way until you have something to say. It's built with GTK 4 and
libadwaita, so it feels at home on your GNOME desktop.

![The home timeline](data/screenshots/timeline.png)

## What it does

**Reading**

- Home, Local and Federated timelines, updated live over streaming.
- Explore: trending posts, hashtags and news, plus people you might want to follow.
- Threads with the focused post in the middle, and profiles with their posts.
- Notifications, with a Mentions filter for when you only care who's talking to you.
- Direct messages, bookmarks, favorites, lists, followed hashtags, and saved
  multi-hashtag timelines.
- Quote posts, edit history, and who boosted or favorited something.
- Custom emoji inline wherever a `:shortcode:` turns up, and a media viewer
  with zoom, video, audio and alt text.
- Instance announcements, with reactions.
- Your yearly #Wrapstodon, if your server has one for you.

**Writing**

- Media uploads (paste, drag, or take a screenshot right from the composer),
  content warnings, polls, quotes and language selection.
- Spell checking as you type.
- `@mention` suggestions as you type.
- Drafts, and scheduled posts for when you'd rather say it tomorrow.

**Keeping things tidy**

- More than one account, even on different servers.
- Server-side filters, mutes, blocks, blocked domains and follow requests.
- Reporting accounts and posts.
- Profile editing.
- Collections (curated account recommendations) on Mastodon 4.6+.
- An admin dashboard for moderators (it asks for admin permissions separately,
  only when you open it).

![A thread](data/screenshots/thread.png)

## Feels at home on GNOME

Babble tries hard to be a good desktop citizen:

- **Paste and drop media.** Paste a screenshot (Print Screen copies one) or
  copied files into the composer, or drag images and videos onto it.
- **Take Screenshot.** The composer's camera button takes a screenshot
  through the Screenshot portal and attaches it. It's hidden when the portal
  isn't there.
- **Spell checking.** Misspelled words get underlined, and right-click shows
  corrections in the post's language. If no dictionary is installed for that
  language, spell checking turns itself off.
- **System state.** Babble notices when you're offline and reconnects
  streaming once you're back. In power-saver mode it polls less often and stops
  autoplaying GIFs. On metered connections the media viewer waits for you to
  load video and audio.
- **Clock format.** Times follow your 12/24-hour setting.
- **Media handoff.** Drag posts and media out of Babble, or copy an image
  from the viewer.
- **Audio posts.** Attach or open audio files. Mastodon wants audio posted on
  its own, so that's how Babble sends it.
- **Accessibility.** Screen readers hear about new batches of posts, and
  Babble keeps the system from logging out or suspending while a post is
  still sending.
- **Open With Babble.** Open images and videos with Babble from Files and a
  new post starts with them already attached.
- **Notification buttons.** Favorite, Boost and Follow Back right from the
  desktop notification.
- **Launcher actions.** Right-click the app icon for New Post or Notifications.
- **Search.** Accounts, hashtags and posts show up in the Activities overview
  (turn Babble on under Settings → Search).
- **Background status.** With Run in Background on, Quick Settings shows how
  many mentions came in while you were away.
- **Quick Post Shortcut** (Preferences). Super+Shift+M anywhere opens the
  composer through the GlobalShortcuts portal.

D-Bus activation, the search provider and the launcher actions only work from
an installed build (`meson install` or Flatpak), because GNOME finds them
through the installed `.desktop`, D-Bus service and search provider files.

## Getting it running

You'll need GTK 4.24+, libadwaita 1.10+ (GNOME 51), meson 1.0+, Python 3 with
PyGObject, libsoup 3, libsecret, and GtkSourceView 5 with libspelling 0.4+ for
the composer's spell checking. The GNOME 51 runtime doesn't include libspelling,
so the development Flatpak manifest builds it.

```sh
meson setup build
meson compile -C build
meson test -C build
meson install -C build
```

Or build a local Flatpak against the GNOME 51 runtime:

```sh
flatpak-builder --user --install --force-clean build-flatpak build-aux/flatpak/io.github.mauriciobc.Babble.json
flatpak run io.github.mauriciobc.Babble
```

## Signing in

Type your server's address, approve Babble in your browser, and you're in.
Babble only asks for `read write follow`, and your token goes into the system
keyring, not a file.

How it works: during login Babble listens on a `127.0.0.1` port and registers
`http://127.0.0.1:<port>/callback` as the OAuth redirect, so the browser hands
the authorization straight back. The tab then tells you it's safe to close. The
flow uses PKCE (RFC 7636) and a fresh `state` on every attempt.

If no loopback port can be opened, Babble falls back to a `babble://` redirect.
That one needs a single command, run once per machine on the host:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

The desktop entry already advertises `x-scheme-handler/babble`, but GLib only
resolves a URI scheme that's also listed in `~/.config/mimeapps.list`, which is
what `xdg-mime default` writes. Without it, the browser's handoff fails with
"Unsupported location specified" and Babble never hears back. Flatpak can't
write the host's `mimeapps.list` from inside the sandbox, so this has to run
outside it. It applies to `meson install` too. "Enter Authorization Code…" on
the login page works without either redirect.

## Speaking your language

Babble speaks Brazilian Portuguese, German, French and Spanish. These
translations were machine-assisted and still need review by native speakers, so
corrections are welcome. The interface follows your system language unless you
pick one in Preferences → General → Language, which takes effect the next time
Babble starts.

In the Flatpak, Babble's own translations ship inside the app
(`"separate-locales": false`), so every language in the picker works no matter
what your system language is. The few strings that come from GTK and libadwaita
live in the runtime's locale extension, which only installs your system's
languages. To get those too, add the language to Flatpak with
`flatpak config --set extra-languages de` (for example) and run `flatpak update`.

To update the catalogues after changing strings:

```sh
meson compile -C build babble-pot          # regenerate po/babble.pot
meson compile -C build babble-update-po    # merge it into every po/*.po
```

To add a language, create `po/<lang>.po` from the template
(`msginit -l <lang> -i po/babble.pot -o po/<lang>.po`), list it in
`po/LINGUAS`, and add it, named in its own language, to `INTERFACE_LANGUAGES`
in `src/preferences.py`.

## Flathub

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a local development manifest only.

- A manifest submitted to Flathub must be written by a human. It must not contain AI-generated or AI-assisted content, and disclosure does not exempt it.
- Disclose AI-generated application code or documentation in any Flathub submission.
- Do not use an AI agent to open or automate Flathub pull requests, or to write their commit messages, descriptions, review comments or replies.

## License

Babble is free software, released under the
[GNU General Public License v3.0 or later](LICENSE).
