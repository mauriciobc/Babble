# Babble documentation

The [README](../README.md) is for people deciding whether to install Babble.
Everything else is here.

## For users

| | |
|---|---|
| [The user manual](manual/README.md) | The complete guide, in Portuguese (pt-BR): signing in, reading, writing, media, Catch Up, settings, plugins, troubleshooting |
| [What Babble does](features.md) | The full feature list, and which server versions each needs |
| [Building and testing](building.md) | Dependencies, meson, the Flatpak manifest, the test suite |
| [Signing in](signing-in.md) | What Babble asks for, and how the browser hands the login back |
| [Desktop integration](desktop-integration.md) | Every GNOME feature Babble uses, and what each needs |

## For contributors

| | |
|---|---|
| [Localization](localization.md) | Languages, translation status, and how to add one |
| [Mastodon API compatibility](mastodon-compat.md) | Where Babble gates on server capabilities, and why |
| [Improvement register](IMPROVEMENTS.md) | Open findings, decisions, and what was deferred |
| [Glossary of domain terms](glossary.md) | The concepts the code is built around (the media policy and its gates), so module and test names stay consistent; the manual's glossary is the one for users |
| [Profiling the running app](performance-profiling.md) | The scripted-UI baseline on `main`, how to record a comparison, and every finding so far |
| [GNOME review](gnome-review.md) | HIG, GTK4/libadwaita, architecture and packaging audit of one revision |
| [GTK 4/libadwaita UI patterns](gtk4-ui-patterns.md) | How eight GTK 4 apps (chosen for coverage, not popularity) build their UI, plus how Babble compares to them and to our internal skill |
| [UI proposals: implementation study](ui-proposals-implementation-study.md) | How to implement each proposal from that report, with spike results and the order to do them in |
| [UI proposals: task-level plan](ui-proposals-plan.md) | Work packages and tasks with files, acceptance criteria and order |
| [GNOME review remediation plan](gnome-review-remediation-plan.md) | Phased fixes for that audit |
| [Ranked timeline: design](timeline-algorithm.md) | A proposed ranked sidebar place: BYOTA and X's For You compared, the hypothesis, evaluation gates, and the boundary with Catch Up |
| [Ranked timeline: implementation plan](ranked-timeline-plan.md) | Slices, user stories and agent-ready tasks with acceptance criteria and a definition of done |
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

## How the documentation is governed

| | |
|---|---|
| [Documentation policy](documentation-policy.md) | What gets documented, who writes it, who approves it, and when it changes |

The policy is a formal item of its own, and the rules it says can be
checked mechanically are checked by `src/test-docs.py`, which runs with
the suite.

## For translators

[po/README.md](../po/README.md).

The help pages are not translated through `po/`: a Mallard page is a
document of its own, so a translation is a translated page beside the
English one under `data/help/<lang>/`, added to `help_languages` in
`data/meson.build`. The two sets are kept in step -- the same files, the
same page and section ids -- and `src/test-docs.py` is what holds them
there.
