# Desktop integration

What Babble uses from GNOME, and what each feature needs.

- **Paste and drop media.** Paste a screenshot (Print Screen copies one) or
  copied files into the composer, or drag images and videos onto it.
- **Take Screenshot.** The composer's camera button takes a screenshot
  through the Screenshot portal and attaches it. Hidden when the portal isn't
  there.
- **Spell checking.** Misspelled words get underlined, and right-click shows
  corrections in the post's language. If no dictionary is installed for that
  language, spell checking turns itself off.
- **System state.** Babble notices when you're offline and reconnects
  streaming once you're back. In power-saver mode it polls less often and
  stops autoplaying GIFs. On metered connections the media viewer waits for
  you to load video and audio.
- **Clock format.** Times follow your 12/24-hour setting.
- **Media handoff.** Drag posts and media out of Babble, or copy an image
  from the viewer.
- **Audio posts.** Attach or open audio files. Mastodon wants audio posted on
  its own, so that's how Babble sends it.
- **Accessibility.** Screen readers hear about new batches of posts, and
  Babble keeps the system from logging out or suspending while a post is
  still sending.
- **Open With Babble.** Open images and videos with Babble from Files, and a
  new post starts with them already attached.
- **Notification buttons.** Favorite, Boost and Follow Back right from the
  desktop notification.
- **Launcher actions.** Right-click the app icon for New Post or
  Notifications.
- **Search.** Accounts, hashtags and posts show up in the Activities overview
  (turn Babble on under Settings → Search).
- **Background status.** With Run in Background on, Quick Settings shows how
  many mentions came in while you were away.
- **Quick Post Shortcut** (Preferences). Super+Shift+M anywhere opens the
  composer through the GlobalShortcuts portal.
- **Running in background.** Babble can keep streaming with its window
  closed, through the Background portal, and show a status line for it.
- **Help.** F1, or Help in the primary menu, opens the Mallard pages under
  `share/help/C/io.github.mauriciobc.Babble/` — the path a `yelp://<app id>`
  URI resolves to. When no help viewer answers, the online documentation opens
  instead (`window.open_help`). The pages are plain installed data, and they are
  not translated through `po/`: a Mallard page is a document, so a translation
  is a translated page beside the English one, added to `help_languages` in
  `data/meson.build`.

## What needs an installed build

D-Bus activation, the search provider and the launcher actions only work from
an installed build (`meson install` or Flatpak), because GNOME finds them
through the installed `.desktop`, D-Bus service and search provider files.

Portal-backed features (screenshot, background, quick post shortcut) need the
corresponding XDG desktop portal. A missing portal hides the feature rather
than breaking anything.
