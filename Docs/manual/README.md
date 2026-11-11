# The Babble User Manual

| | |
|---|---|
| **Title** | The Babble User Manual — reading, writing and integrating with GNOME |
| **Identifier / version** | `BABBLE-MAN-001` — v1.0 — approved |
| **Date of issue** | 2026-11-11 |
| **Author(s)** | The Babble team |
| **Approved by** | Approved in the merge of [PR #106](https://github.com/mauriciobc/Babble/pull/106), on 2026-11-11, by mauriciobc. The process that governs this approval is in [the documentation policy](../documentation-policy.md), and the checker that enforces it is `src/test-docs.py`. |
| **Software described** | Babble 0.3.0 (`io.github.mauriciobc.Babble`) |
| **Standards applied** | ISO/IEC 26514 (user documentation); ISO/IEC/IEEE 15289 (identification of the information item); ISO/IEC/IEEE 12207 (documentation process governance, through the [documentation policy](../documentation-policy.md)) |
| **Scope** | Covers the use of Babble 0.3.0 as a Mastodon client for GNOME: signing in, reading, interacting, posting, handling media, installing and configuring plugins, and integrating the application with the desktop environment. **It does not cover** installation from the source tree, development, the internal architecture, the Mastodon API, the translation policy, nor the project's publishing process. The corresponding technical documentation is in `Docs/building.md`, `Docs/features.md`, `Docs/mastodon-compat.md` and `Docs/desktop-integration.md`. |
| **Purpose** | To let a person who uses Babble for the first time, or who knows the application only in broad strokes, carry out the everyday tasks — read the feed, reply, post, recover what was missed — without relying on unstated knowledge. It is aimed at the end user; it is not a development document. |
| **Audience** | People who use Babble on a GNOME 51 or later desktop. |
| **Language** | English. Interface labels are quoted in their original English form; this is the English edition of the manual, and the Brazilian Portuguese edition lives in `Docs/pt_BR/manual/`, where chapter 10 carries the Portuguese equivalent of each label quoted. |
| **Traceability** | Chapter → source in the code: see the table in [chapter 12](12-glossary.md). |
| **Review** | On every stable version of the application. Interface changes that alter a label, a shortcut or a limit require a review of this manual. |

## Contents

| Chapter | Title | Nature of the information |
|---|---|---|
| [1](01-getting-started.md) | Getting started | Conceptual + procedural |
| [2](02-reading.md) | Reading: timelines, threads, profiles and search | Conceptual + procedural |
| [3](03-notifications-and-messages.md) | Notifications and direct messages | Conceptual + procedural |
| [4](04-writing.md) | Writing: the composer, polls, quotes and drafts | Conceptual + procedural |
| [5](05-media.md) | Media: attaching, viewing, saving | Conceptual + procedural |
| [6](06-catch-up.md) | Catch Up: recovering what you missed | Conceptual + procedural |
| [7](07-desktop-integration.md) | Desktop integration | Conceptual + procedural |
| [8](08-preferences.md) | Preferences: complete reference | Reference |
| [9](09-plugins.md) | Plugins | Conceptual + procedural |
| [10](10-reference.md) | Reference: shortcuts, menus, messages and limits | Reference |
| [11](11-troubleshooting.md) | Troubleshooting | Reference |
| [12](12-glossary.md) | Glossary, index and support | Reference |

The manual separates three kinds of information, as ISO/IEC 26514 requires:

- **Conceptual** — explains *what it is* and *why*. It appears in chapters 1 to 7 and 9, as running text.
- **Procedural** — numbered steps to *do* something. It appears in every section marked **How…**, in the format *Objective / Pre-conditions / Steps / Result / If it goes wrong*.
- **Reference** — data to look up. It appears in chapters 8, 10, 11 and 12, almost always in tables.

## Introduction

### Purpose and audience

This manual accompanies Babble 0.3.0, a Mastodon client for GNOME. It was written for the person who uses the application: the one who wants to know *how* to do something with it, not how it was built. No chapter requires knowledge of programming, of OAuth, or of the Mastodon protocol.

### The product scope, in one page

Babble is a client for a federated social network. It reads and posts on any Mastodon server, and it also sees the content that server exchanges with the other servers of the federation — what you see as the *federated timeline*.

Three functional blocks summarize the application:

1. **Reading.** *Home* gathers the posts of the accounts you follow; *Local* shows the public timeline of your server; *Federated* shows the public timeline of every known server. *Notifications* gathers mentions, boosts, favorites and followers; *Conversations* keeps the direct messages. *Catch Up* is the recovery tool: it goes back in time and ranks what you left unread.
2. **Writing.** The composer checks spelling, completes mentions and hashtags, attaches media, builds polls, quotes other posts, keeps drafts and schedules posts.
3. **Settings.** Preferences, plugins and shortcuts. Everything that is a preference of the application lives in the Preferences window; everything that is a preference of the *server* (default visibility, default language) lives on the server and follows you to other clients.

### Prerequisites

| Prerequisite | Detail |
|---|---|
| Graphical environment | GNOME 51 or later (GTK 4.24+ and libadwaita 1.10+). The application also runs on other GTK 4 environments, but the portal and background features depend on GNOME. |
| Mastodon server | An account on any Mastodon server. For testing, `mastodon.social` serves. |
| Keyring service | To keep the access token. On GNOME, GNOME Keyring. Without it the application warns and cannot keep the session. |
| Browser | Used once, at login, to authorize the application on the server. |
| Network | Needed for the server; the application works without a network and warns when it comes back. |

The application is **not** a browser in disguise, nor a hub of changes: it talks to the Mastodon API directly, and the browser shows up only in the initial authorization and when you open a link outside the application.

### Notation conventions

| Notation | Meaning |
|---|---|
| `New Post` | The name of an interface element, exactly as it appears in the source code (in English). The Brazilian Portuguese edition of chapter 10 brings the Portuguese equivalent of each one. |
| **Ctrl+N** | A key combination. Modifier keys are written as they appear in GNOME. |
| *Term in italics* | A concept defined in the [glossary](12-glossary.md). |
| `~/.local/share/babble/plugins` | A literal path on disk. |
| [chapter 10](10-reference.md) | A cross-reference. |

**Why the labels appear in English.** The manual quotes the labels exactly as the source code defines them, because that is how the interface is built and translated: the screen translation lives in `po/`, and the original entry is the reference. The application is translated into German, Spanish, French and Brazilian Portuguese; a reader with the interface in Portuguese will see, for example, `New Post` as *Nova publicação*. The [label table in the Brazilian Portuguese edition of chapter 10](10-reference.md#105-interface-labels-in-english-and-portuguese) relates the two.

## Concepts

This section is the mental model of the application. Read it before chapter 1 if you have never used Babble; skip it if you already know Mastodon.

### What a federated network is

A federated network is a set of independent servers that exchange messages with each other.

- A **server** (Mastodon calls it an *instance*) is an installation with its own users, its own rules and its own database. `mastodon.social` and `hachyderm.io` are different servers.
- An **account** belongs to a single server. The same e-mail address on two servers is two different accounts, and the numeric identifiers can collide — which is why Babble always identifies an account by the *server + identifier* pair.
- The **federation** is what circulates between servers: when you follow someone on another server, their posts reach your server, and your server passes them on to you.

Two practical consequences come up all the time in this manual:

- **Not everything exists everywhere.** A poll, a quote or a moderation dashboard depends on the Mastodon version of your server. Babble hides what the server does not support instead of showing a broken button. The list of what depends on what is in `Docs/mastodon-compat.md`.
- **Media is not always on your server.** When your server has no copy of an image that came from another server, loading it tells the origin server that you saw it, and that is why there is a specific permission for it — chapter 5.

### Posts, and what you can do with them

A **post** is the unit of content. It has an author, text, a time, and it can have media, a poll, a content warning and a quote.

| Term | What it means in practice |
|---|---|
| **Post** (*post*, *status*) | The post. |
| **Thread** (*thread*) | A post and its replies. A thread has ancestors above and nested replies below. |
| **Reply** (*reply*) | A post that answers another. It appears with the line "Replying to @account". |
| **Boost** (*boost*, *reblog*) | Reposting someone else's post on your feed. |
| **Favorite** (*favorite*) | A like. It is private: nobody is told. |
| **Bookmark** (*bookmark*) | Saving something to read later. Private, like a favorite. |
| **Quote** (*quote*) | Posting a comment along with the quoted post, for your followers. |
| **Mention** (*mention*) | The `@account` inside a post. It becomes a notification for whoever was mentioned. |
| **Hashtag** | The `#topic` inside a post. It creates a page with every post that uses it. |
| **List** | A named set of accounts, with its own timeline. |
| **Content warning** (*content warning*, CW) | A text that hides the content until you ask to see it. |
| **Description** (*alt text*) | The textual description of an image, attached to the media. It is what a screen reader reads and what someone who cannot see the image uses. |
| **Sensitive content** | A media marker that covers it until it is revealed. |

The four main verbs — `Reply`, `Boost`, `Quote`, `Favorite` — and the `Bookmark` are in the action bar of every post. If you turn off *Post Actions on Timelines* in Preferences, they move to the post's `More Options` menu, but stay available.

### The window: sidebar, pages and overlays

The Babble window has three layers, and it is worth knowing which one you are in when something happens:

1. **Sidebar.** The column of destinations: `Home`, `Catch Up`, `Notifications`, `Conversations`, `Search`, `Favorites`, `Bookmarks`, `Hashtags`, `Explore`, `Local`, `Federated`, `Lists`, plus the lists and hashtags you pin. At the top sits the account button, which switches accounts; at the bottom, the button that changes the sidebar mode.
2. **Page.** The content of the chosen destination. Pages such as the profile, the thread and search open over it, and `Alt+Left` goes back.
3. **Overlay.** The media viewer and the web page viewer are not windows: they cover the main window and the close button is always at hand. The *toasts* — the bars that appear at the bottom — are the only thing that appears above everything.

The sidebar has three modes, chosen with the button at the bottom (`Sidebar`): `Full Sidebar` (with labels), `Icons Only` (a strip of icons) and `Hidden` (it leaves the screen; the `Sidebar`, `Main Menu`, `New Post` and `Notifications` buttons move to the header of each page).

### Live updates and reloading

- **Live updates** deliver posts and notifications the moment they happen, over a permanent connection. It is on by default, and it is turned off in Preferences → Timelines → *Live Timelines*.
- **Automatic refresh** is the substitute when live is not available: the page fetches again every 30 seconds, every minute or every 5 minutes, according to the setting.
- **Pull to refresh** at the top of a page reloads on the spot, in any mode.

When new posts arrive while you are scrolled further down, a `N New Posts` button appears at the top of the list; clicking it takes you to the top.

### An account is always a pair

Every account in Babble is `(server, identifier)`. Two accounts with the same identifier on different servers are different accounts, and the application never mixes them up. This shows in the account switcher, which displays the avatar, the name and the `@account@server` of each one.

### Where things are kept

| What | Where it lives |
|---|---|
| Access token and OAuth credentials | The system keyring (GNOME Keyring or equivalent) |
| Account list and active account | GSettings, in `dconf` |
| Local drafts (up to 5, per account) | Files in the application data directory |
| Drafts, scheduled posts, lists, filters | On your Mastodon server |
| Catch Up snapshots | On your computer, per account |
| Preferences | GSettings; the server ones, on the server |
| Pasted screenshots and temporary files | The application cache |

The point that matters: **none of this is a password file**. Babble never keeps your server password. The password is typed on your server's own page, in the browser.

## Revision history

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-11-11 | The Babble team | Initial version, written from Babble 0.3.0 (`main` on 2026-11-11). |
