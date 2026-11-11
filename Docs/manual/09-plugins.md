# 9 — Plugins

> **Document:** `BABBLE-MAN-001` — chapter 9 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

A plugin adds a feature to Babble: an item in the menu of a post, a panel on a hashtag page, a pair of entries in the main menu, a settings page of its own. It is third-party code running inside the application, with the same permissions as the application.

## 9.1 The `Plugins` page

**Objective:** see, enable and disable plugins.

**Pre-conditions:** `libpeas 2` and its Python loader are installed.

1. Open the main menu and choose `Plugins`. The page splits into two groups:
   - **`Included With Babble`** — the plugins that ship with the application, reviewed along with it.
   - **`Installed by You`** — the plugins you installed.
2. Turn each plugin on or off with the switch next to its name.
3. If the plugin has settings, the button with a gear opens their subpage.

**Result:** the change takes effect the next time the plugin is used; turning a plugin off removes what it had added.

**If it goes wrong:** if `libpeas` is not installed, the page shows `Plugins Are Unavailable` — *"Babble needs libpeas 2 and its Python loader to load plugins"*. Nothing else happens, and the rest of the application works normally.

## 9.2 Where to put a plugin

The `Open Plugins Folder` button opens the folder where Babble looks for plugins:

| Situation | Path |
|---|---|
| Regular installation | `~/.local/share/babble/plugins` |
| Flatpak installation | `~/.var/app/io.github.mauriciobc.Babble/data/babble/plugins` |

Babble also looks for plugins in `BABBLE_PLUGIN_PATH` (the environment variable) and in the `plugins/` folder next to the installed package.

A plugin is a folder holding a `.plugin` file and a Python module. The folder needs a name of its own, and the module is named `babble_<name>` so that it does not collide with any installed package.

## 9.3 Important warning

> **Only turn on plugins you trust.**

A plugin runs as part of Babble and can do everything Babble does — including reading and posting with your account. The plugins that ship with the application are reviewed along with it; any other one is as safe as whoever wrote it.

The `Installed by You` group carries the description: *"Plugins you install can do anything Babble can, including reading and posting with your account. Only turn on plugins you trust."*

## 9.4 The plugins that ship with Babble

### Wallabag

Adds `Save to Wallabag` to the menu of every post, to keep the address of the post and read it later in [Wallabag](https://wallabag.org).

**Setup, the first time:** the plugin asks for the address of the Wallabag server (`app.wallabag.it` for the hosted service, or your own), the **ID** and **secret** of an API client — which you create in Wallabag, under `API Clients Management` — and your username and password.

The password is used once, to sign in, and **is not stored**. The keyring keeps the client secret and a token that keeps you signed in; if the token expires, the plugin asks you to sign in again. The `Sign Out` item is in the plugin's settings.

### Hashpanel

[Hashpanel](https://github.com/mauriciobc/hashpanel) follows the daily hashtags of the Brazilian fediverse — `#segundaficha`, for instance, on Mondays.

On the page of a hashtag it follows, the plugin shows whose day it is, the day's favorites, boosts and replies, the week's comparison with the previous one, and the most popular post of the day.

The plugin **only asks Hashpanel about the hashtags it follows**: opening any other hashtag sends nothing to Hashpanel. The `Hashtag Panel` page in the settings lets you change the Hashpanel server.

### Na Real

[Na Real](https://codeberg.org/thiagojedi/na-real) is an anonymous question-and-answer application for the fediverse: someone asks a question of an account, without identifying themselves, and the account answers.

The plugin adds two entries to the **main menu** of Babble:

- `My Questions` — the panel with the questions addressed to you;
- `Share My Questions Link` — the composer, with an invitation and your public address.

It adds nothing to posts or profiles. Because Na Real is a web application that needs JavaScript, the plugin shows it in Babble's web column and does a little programming of the page: in the sign-in form, the server of your account is already filled in (clicking `Log in` is still yours); in the question lists, the `Ver postagem` link opens the thread inside Babble; Na Real's share button opens Babble's composer instead of copying the address; and Na Real's own hashtag gains a line that opens it.

Each installation of Na Real is a server of its own, and which one to use is a plugin setting.

## 9.5 When a plugin does not work

| Situation | What appears |
|---|---|
| The plugin could not be loaded | A message under its name, with a warning sign. It is tried again the next time Babble starts. |
| The plugin stopped working | The reason appears under the name, with a warning sign, **and the plugin is turned off**. Babble tells you that it turned it off. |
| The plugin was made for a different version of Babble | It is not loaded. |

The technical detail of each case is in the system log — see [11.6](11-troubleshooting.md#116-the-system-log).

One important detail: **Python cannot unload a module**. A plugin that was loaded and failed stays registered until Babble restarts. That is why the failure message only really goes away after you restart the application.
