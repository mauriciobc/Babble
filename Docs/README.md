# Babble documentation

The [README](../README.md) is for people deciding whether to install Babble.
Everything else is here.

## For users

| | |
|---|---|
| [What Babble does](features.md) | The full feature list, and which server versions each needs |
| [Building and testing](building.md) | Dependencies, meson, the Flatpak manifest, the test suite |
| [Signing in](signing-in.md) | What Babble asks for, and how the browser hands the login back |
| [Desktop integration](desktop-integration.md) | Every GNOME feature Babble uses, and what each needs |

## For contributors

| | |
|---|---|
| [Localization](localization.md) | Languages, translation status, and how to add one |
| [Mastodon API compatibility](mastodon-compat.md) | Where Babble gates on server capabilities, and why |
| [GNOME review](gnome-review.md) | HIG, GTK4/libadwaita, architecture and packaging audit of one revision |
| [Roadmap](ROADMAP.MD) | What the project is betting on |

`CLAUDE.md` in the repository root is the working notes for AI agents
editing this codebase: architecture, conventions, and the memory rules that
are easy to break by accident.

## Reference documents at the repository root

| | |
|---|---|
| `PERFORMANCE.md` | Timeline baselines and why each optimization was made |
| `MEMORY-LEAKS.md` | The leak patterns found so far, and how to check for them |
| `CATCH-UP.md` | The Catch Up feature specification |

## For translators

[po/README.md](../po/README.md).
