# Babble

*A cozy little Mastodon client for GNOME.*

![The home timeline](data/screenshots/timeline.png)

Babble lets you read the fediverse the way you'd read a good book by the
window: timelines that scroll smoothly, threads that unfold nicely, and a
composer that stays out of your way until you have something to say. It's
built with GTK 4 and libadwaita, so it feels at home on your GNOME desktop.
This app was developed using AI, and is built upon the brilliant codebases of Tuba and Phanpy.

| | |
|---|---|
| ![A hashtag timeline, with a quoted post](data/screenshots/hashtag.png) | ![Writing a post, with a poll](data/screenshots/composer.png) |

![Catch Up, as a grid of the posts you missed](data/screenshots/catch-up.png)

- **Reading.** Live-updating home, local and federated timelines, threads,
  notifications, direct messages, quotes, edit history, bookmarks, lists,
  followed hashtags, and your yearly #Wrapstodon if your server has one for
  you — all without losing your scroll position.
- **Catch Up.** A separate sidebar place that pages back through the home
  timeline for a window you choose, ranks the posts you missed, and keeps that
  run frozen as a snapshot.
- **Writing.** A composer with spell checking, `@mention` suggestions,
  media uploads, polls, drafts, and scheduled posts.
- **At home on GNOME.** Paste or drag media straight into the composer,
  take a screenshot through the portal, and open posts from Files — Babble
  integrates with the desktop rather than living beside it.

## Status

- **0.3.1** is the current version. **Not on Flathub yet** — build it from
  source [below](#getting-it).
- The interface is translated into German, Spanish, French and Brazilian
  Portuguese. [Adding a language](Docs/localization.md) is a page of its own.

## Getting it

To run Babble, build it from source:

```sh
meson setup build && meson compile -C build
python3 build/src/babble
```

That needs GTK 4.24+ and libadwaita 1.10+ (GNOME 51). [The full build
guide](Docs/building.md) covers dependencies, the Flatpak manifest, and the
test suite.

## Documentation

| | | |
|---|---|---|
| [User manual](Docs/manual/README.md) | The complete guide for users: signing in, reading, writing, media, Catch Up, settings, plugins, and troubleshooting | [pt-BR](Docs/pt_BR/manual/README.md) |
| [Building and testing](Docs/building.md) | Dependencies, meson, the Flatpak manifest, running the tests | [pt-BR](Docs/pt_BR/building.md) |
| [Signing in](Docs/signing-in.md) | What Babble asks for, and how the browser hands the login back | [pt-BR](Docs/pt_BR/signing-in.md) |
| [Desktop integration](Docs/desktop-integration.md) | Every GNOME feature Babble uses, and what each needs | [pt-BR](Docs/pt_BR/desktop-integration.md) |
| [Localization](Docs/localization.md) | Languages, and how to add one | [pt-BR](Docs/pt_BR/localization.md) |
| [Mastodon API compatibility](Docs/mastodon-compat.md) | Where Babble gates on server capabilities, and why | [pt-BR](Docs/pt_BR/mastodon-compat.md) |
| [Roadmap](Docs/ROADMAP.MD) | What the project is betting on | |
| [Flathub policy](Docs/flathub.md) | Why the manifest is local-only | |

The [full feature list](Docs/features.md) covers everything below in
detail, and the [documentation index](Docs/README.md) lists the whole set.
The formal documents are written in English and in Brazilian Portuguese;
the English edition is the source, and a change is made there and
translated in the same pull request.

Translators: see [po/README.md](po/README.md).

## License

Babble is free software, released under the
[GNU General Public License v3.0 or later](LICENSE).
