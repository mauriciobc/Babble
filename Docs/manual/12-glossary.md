# 12 — Glossary, index and support

> **Document:** `BABBLE-MAN-001` — chapter 12 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

## 12.1 Glossary

**Avatar** — the profile image of an account. In Babble, it is also the button that opens the account.

**Content warning (CW)** — a text that hides the content of a post until the reader asks to see it.

**Block** — preventing an account or a whole server from interacting with you. The posts are hidden, and the account cannot follow you nor see what you post.

**Boost** — reposting someone else's post on your timeline. It is also the name of the button that does it.

**Bookmark** — saving a post to read later. Private.

**Cover image** — the wide image at the top of a profile.

**Profile field** — a line of custom information on the profile (website, pronouns, whatever the account chose).

**Catch Up** — the separate view that goes back in time on your timeline and ranks what you missed reading. [Chapter 6](06-catch-up.md).

**Keyring** — the system service that keeps secret data, such as the access token. On GNOME, GNOME Keyring.

**Web column** — the area inside the application that shows a web page beside the timeline. [Chapter 7](07-desktop-integration.md).

**Composer** — the dialog where a post is written. [Chapter 4](04-writing.md).

**Sensitive content** — the marking that covers a media file until the reader reveals it.

**Direct conversation** — a private conversation, with the people you mention and who mention you.

**Description (alt text)** — the text that describes an attached image, for people who cannot see the image or who use a screen reader.

**Domain** — the address of a server, such as `mastodon.social`. It is also what you block, when you block a whole server.

**Poll** — a post with options to choose from and a deadline to close it.

**Scope** — a permission requested from the server during authorization. Normal login asks for `read` and `write`.

**Like** — the favorite. A private like.

**Local feed (local timeline)** — the public timeline of your server.

**Federated feed (federated timeline)** — the public timeline of every server yours knows.

**Thread** — a post and the replies to it.

**Filter** — a server rule that hides, warns about or blurs posts that match a keyword.

**Favorite** — see **Like**.

**Hashtag** — the `#topic` inside a post, which creates a page with all the posts that use it.

**Repost** — see **Boost**. It is also the reposting of someone else's post, which appears on your timeline as if it were the booster's.

**Snapshot** — the frozen copy of a Catch Up, written to your computer.

**Saved item** — see **Bookmark**.

**List** — a named set of accounts, with a timeline of its own.

**Places** — the destinations of the sidebar. Each place has a page, built on the first visit.

**Mention** — the `@account` inside a post, which notifies the person.

**OAuth** — the authorization protocol that lets Babble use your account without knowing your password.

**PKCE** — the extension of OAuth that prevents a stolen authorization code from being used in another application.

**Interaction counters** — the counts of replies, boosts and favorites in the action bar.

**Portal** — the interface an application uses to ask the system for something it cannot do on its own (screenshot, background, global shortcuts).

**Post** — the unit of content in Mastodon.

**Direct post** — the one only the mentioned people see.

**Quote** — commenting on a post while keeping it in view for your followers.

**Draft** — the text, media and poll Babble keeps before you post. There are local drafts (on your computer) and server drafts.

**Server (instance)** — an installation of Mastodon, with its own users and its own rules.

**Mute** — hiding the posts of an account, without it knowing.

**Streaming (live update)** — the permanent connection to the server, which delivers posts and notifications the moment they happen.

**Reply chain** — see **Thread**.

**Token** — the credential the server returns on authorization. It is what Babble stores, and what gives access to your account.

**Toast** — the message bar that appears at the bottom of the window, over the content.

**Timeline** — the list of posts, from the most recent to the oldest.

**Automatic translation** — the translation the server makes of a post into the interface language.

**Spell checker** — the composer's checker, which follows the language chosen for the post.

**Visibility** — who can see the post: `Public`, `Unlisted`, `Followers only` or `Direct`.

### Abbreviations

| Abbreviation | Meaning |
|---|---|
| API | Application Programming Interface — the contract through which Babble talks to the server. |
| CW | *Content Warning*. |
| D-Bus | The message bus between GNOME applications. |
| GNOME | The desktop environment Babble was made for. |
| GTK | The interface library Babble is built on. |
| IP | The network address of your computer. |
| OAuth | The authorization protocol (see above). |
| PKCE | *Proof Key for Code Exchange* (see above). |
| SNI | Server Name Indication, used for TLS. |
| UI | *User Interface*, the interface. |
| URL | The address of a page or resource. |
| XDG | The standard for the paths and portals of GNOME and Linux. |

## 12.2 Index

| Subject | Where it is |
|---|---|
| Scheduling a post | [4.14](04-writing.md#414-scheduling-a-post) |
| Boosting | [2.4](02-reading.md#24-interacting-with-a-post) |
| Server announcements | [3.7](03-notifications-and-messages.md#37-server-announcements) |
| Attaching media | [5.1](05-media.md#51-four-ways-to-attach-media) |
| Content warning | [4.8](04-writing.md#48-content-warning), [2.6](02-reading.md#26-content-warnings-and-long-posts) |
| Sidebar | [2.1](02-reading.md#21-the-sidebar) |
| Blocking | [2.8](02-reading.md#28-someone-elses-profile), [3.6](03-notifications-and-messages.md#36-mutes-and-blocks) |
| Screenshot | [5.1](05-media.md#51-four-ways-to-attach-media), [7.5](07-desktop-integration.md#75-screenshot) |
| Boosts carousel | [2.15](02-reading.md#215-repeated-posts-the-boosts-carousel) |
| Catch Up | [6](06-catch-up.md) |
| Keyring | [1.1](01-getting-started.md#11-how-sign-in-works), [11.3](11-troubleshooting.md#113-the-keyring-does-not-keep-the-login) |
| Quoting | [4.10](04-writing.md#410-quoting-a-post) |
| Web column | [7.1](07-desktop-integration.md#71-the-web-column-beside-the-timeline) |
| Interaction counters | [2.4](02-reading.md#24-interacting-with-a-post) |
| Accounts, adding and switching | [1.3](01-getting-started.md#13-adding-switching-and-removing-accounts) |
| Direct conversations | [3.4](03-notifications-and-messages.md#34-direct-messages) |
| Media description | [5.2](05-media.md#52-the-media-description) |
| Emoji | [4.7](04-writing.md#47-emoji) |
| Poll | [4.9](04-writing.md#49-polls) |
| Hiding a sidebar place | [8.4](08-preferences.md#84-the-timelines-page) |
| Explore | [2.12](02-reading.md#212-explore) |
| Server filters | [2.7](02-reading.md#27-server-filters), [8.3](08-preferences.md#83-the-reading-page) |
| Thread | [2.3](02-reading.md#23-opening-a-thread) |
| Favoriting | [2.4](02-reading.md#24-interacting-with-a-post) |
| Dragging media out | [5.5](05-media.md#55-zoom-and-drag) |
| Photos, viewing | [5.4](05-media.md#54-the-media-viewer) |
| Keyboard shortcuts | [10.1](10-reference.md#101-keyboard-shortcuts) |
| Hashtags | [2.11](02-reading.md#211-hashtags), [4.6](04-writing.md#46-hashtags) |
| Interface language | [8.2](08-preferences.md#82-the-appearance-page) |
| Installing the application | `Docs/building.md` |
| Lists | [2.10](02-reading.md#210-lists) |
| Live updates | [2.2](02-reading.md#22-the-home-timeline), [8.4](08-preferences.md#84-the-timelines-page) |
| Local and Federated | [2.14](02-reading.md#214-favorites-bookmarks-local-and-federated) |
| Login | [1.2](01-getting-started.md#12-signing-in-for-the-first-time) |
| Mentions | [4.5](04-writing.md#45-mentions) |
| Error messages | [10.6](10-reference.md#106-messages-and-notices), [11](11-troubleshooting.md) |
| Media from other servers | [5.7](05-media.md#57-media-hosted-on-another-server) |
| Notifications | [3.1](03-notifications-and-messages.md#31-the-notifications-page) |
| Desktop notifications | [3.2](03-notifications-and-messages.md#32-filtering-what-appears) |
| Profile | [2.8](02-reading.md#28-someone-elses-profile), [2.9](02-reading.md#29-your-own-profile) |
| Plugins | [9](09-plugins.md) |
| Reading position | [2.2](02-reading.md#22-the-home-timeline) |
| Preferences | [8](08-preferences.md) |
| Drafts | [4.13](04-writing.md#413-drafts) |
| Reloading | [2.2](02-reading.md#22-the-home-timeline) |
| Label reference | [10.5](10-reference.md#105-interface-labels-in-english-and-portuguese) |
| Yearly report (Wrapped) | [10.2](10-reference.md#102-the-main-menu) |
| Saving media | [5.6](05-media.md#56-saving-media) |
| Search | [2.13](02-reading.md#213-search) |
| Muting | [3.6](03-notifications-and-messages.md#36-mutes-and-blocks) |
| About | [10.2](10-reference.md#102-the-main-menu) |
| Link timeline | [2.12](02-reading.md#212-explore) |
| Translating a post | [2.6](02-reading.md#26-content-warnings-and-long-posts) |
| Spell checker | [4.3](04-writing.md#43-posting), [7.6](07-desktop-integration.md#76-spell-checking) |
| Server version | [10.7](10-reference.md#107-limits), [11.12](11-troubleshooting.md#1112-a-feature-does-not-appear) |
| Zoom | [5.5](05-media.md#55-zoom-and-drag) |

## 12.3 Traceability: where each chapter is in the code

Each chapter of the manual corresponds to a set of the application's modules. That is where you check whether the manual still describes what the application does.

| Chapter | Corresponding modules |
|---|---|
| 1 — Getting started | `src/auth.py`, `src/accounts.py`, `src/main.py`, `src/window.py` |
| 2 — Reading | `src/timelinepage.py`, `src/statusrow.py`, `src/statusfold.py`, `src/statusactions.py`, `src/statusmenu.py`, `src/statusviews.py`, `src/profile.py`, `src/profileactions.py`, `src/explore.py`, `src/hashtags.py`, `src/lists.py`, `src/search.py`, `src/filterrules.py`, `src/statustext.py` |
| 3 — Notifications and direct messages | `src/notifications.py`, `src/notificationrow.py`, `src/notificationctrl.py`, `src/notificationcopy.py`, `src/notificationpolicy.py`, `src/conversations.py`, `src/announcements.py`, `src/relations.py` |
| 4 — Writing | `src/compose.py`, `src/composeattach.py`, `src/composeupload.py`, `src/composequote.py`, `src/composeschedule.py`, `src/composetransition.py`, `src/drafts.py`, `src/scheduled.py`, `src/mentioncompleter.py`, `src/hashtagcompleter.py`, `src/emojichooser.py`, `src/pollbox.py` |
| 5 — Media | `src/mediabox.py`, `src/mediaviewer.py`, `src/mediaviewerchrome.py`, `src/mediazoom.py`, `src/mediaexport.py`, `src/mediainline.py`, `src/mediaplayback.py`, `src/remotemedia.py` |
| 6 — Catch Up | `src/catchup/` (`rank.py`, `rank_model.py`, `rank_text.py`, and the other modules of the package) |
| 7 — Desktop integration | `src/portals.py`, `src/clipboard.py`, `src/desktopnotify.py`, `src/searchprovider.py`, `src/mediabox.py`, `src/composeattach.py`, `src/sysstate.py` |
| 8 — Preferences | `src/preferences.py`, `data/io.github.mauriciobc.Babble.gschema.xml` |
| 9 — Plugins | `src/plugin.py`, `src/pluginhost.py`, `src/pluginspage.py`, `plugins/` |
| 10 — Reference | `src/main.py`, `src/shortcuts-dialog.ui`, `src/window.ui`, `src/capabilities.py`, `po/pt_BR.po` |
| 11 — Troubleshooting | All the modules above, plus `src/accounts.py` and the system log |
| Built-in help (F1) | `data/help/C/` (English) and `data/help/pt_BR/` (Portuguese), registered in `help_languages`, in `data/meson.build` |
| The process that governs this manual | `Docs/documentation-policy.md` (the project policy), checked by `src/test-docs.py` |

## 12.4 Support

| Where | What you find |
|---|---|
| **F1**, or `Help` in the main menu | The installed help, inside the application. It summarizes the essentials. |
| `data/help/pt_BR/` | The help pages in Portuguese, installed next to the English ones. |
| `data/help/C/` | The help pages in English. |
| This manual | The complete reference, with procedures, messages and limits. |
| `Docs/features.md` | The list of project features, in technical terms. |
| `Docs/mastodon-compat.md` | Where each feature depends on the server version. |
| `Docs/desktop-integration.md` | What each GNOME integration needs. |
| `Docs/building.md` | How to build, install and run the tests. |
| `github.com/mauriciobc/Babble` | The source code and the project page. |
| The project's issue tracker | Reporting a defect. |

**When reporting a defect**, include: the application version (in `About Babble`), the version of your Mastodon server, what you did, what you expected and what happened. If the problem is about login, an account or a plugin, also include the output of `journalctl --user -g 'accounts:'`.

## 12.5 Maintaining this manual

| Rule | Reason |
|---|---|
| A label quoted in the manual has to exist in the code | The manual quotes labels in English; a label that changes breaks the correspondence. |
| The help pages and the manual do not diverge | The help (`data/help/`) is a summary; this manual is the reference. The two translations of each help page exist side by side, in `data/help/C/` and `data/help/pt_BR/`, with the same section identifiers — changing one changes the other. |
| A shortcut quoted has to be registered | The shortcuts live in `src/main.py` and in `src/shortcuts-dialog.ui`. |
| A limit quoted has to come from the server | The limits are declared by the instance, and the manual describes them as such. |
| The label table in the Brazilian Portuguese edition is generated from `po/pt_BR.po` | It reflects what is actually translated in the installed version. |
| A new application version obliges you to review the manual | New features can land in any chapter. |
| The two editions of the manual do not diverge | The English edition is the source; the Brazilian Portuguese edition mirrors it, chapter for chapter. A change is made in English and translated in the same pull request. |

The manual is updated along with the application. The version and the date are in the [header](README.md), and the revision history is next to it.

The **process** that governs these rules — who writes, who reviews, who approves, what a baseline is, when a document is reviewed and when it is withdrawn — is not in this manual, but in the [documentation policy](../documentation-policy.md). That is the project policy, and this manual is one of its formal items. The two things the policy promises are checked by `src/test-docs.py`, which runs in CI:

- the help pages in every language carry the same files and the same identifiers, and the cross-references resolve inside each set;
- every language present in `data/help/` is registered in `help_languages`, and every file the installation installs exists in every language;
- every chapter of the manual carries the identification block, and all the links and anchors resolve;
- the two editions of the manual carry the same chapters, and every link and anchor in both resolves;
- the label table of chapter 10, in the Brazilian Portuguese edition, is the one the translation catalogue produces.

To regenerate that table after a change in the catalogue:

```sh
python3 src/test-docs.py --write-table
```
