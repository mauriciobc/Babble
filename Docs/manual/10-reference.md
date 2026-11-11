# 10 — Reference: shortcuts, menus, messages and limits

> **Document:** `BABBLE-MAN-001` — chapter 10 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

This chapter is reference material. It is not read from beginning to end: you look up the line you need.

## 10.1 Keyboard shortcuts

The complete list, kept up to date by the application itself, opens with **Ctrl+?** (the `Keyboard Shortcuts` item of the main menu).

### `General`

| Action | Shortcut |
|---|---|
| `Preferences` | **Ctrl+,** |
| `Keyboard Shortcuts` | **Ctrl+?** |
| `Help` | **F1** |
| `Main Menu` | **F10** |
| `Close Window` | **Ctrl+W** |
| `Quit` | **Ctrl+Q** |

### `Timelines`

| Action | Shortcut |
|---|---|
| `New Post` | **Ctrl+N** |
| `Reload` | **Ctrl+R**, **F5** |
| `Toggle Sidebar` | **F9** |
| `Go Back` | **Alt+Left** |
| `Search` | **Ctrl+F**, **Ctrl+K** |
| `Go to Home` | **Ctrl+1**, **Alt+Home** |
| `Go to Notifications` | **Ctrl+2**, **Ctrl+Shift+N** |
| `Go to Conversations` | **Ctrl+3** |
| `Go to Search` | **Ctrl+4** |
| `Go to Favorites` | **Ctrl+5** |
| `Go to Bookmarks` | **Ctrl+6** |
| `Go to Hashtags` | **Ctrl+7** |
| `Go to Explore` | **Ctrl+8** |
| `Go to Local` | **Ctrl+9** |
| `Scroll Page Down` | **Page Down** |
| `Scroll Page Up` | **Page Up** |
| `Increase Text Size` | **Ctrl++**, **Ctrl+=**, **Ctrl+Keypad +** |
| `Decrease Text Size` | **Ctrl+-**, **Ctrl+Keypad −** |
| `Reset Text Size` | **Ctrl+0**, **Ctrl+Keypad 0** |

### `Catch Up`

| Action | Shortcut |
|---|---|
| `Next Post` | **j** |
| `Previous Post` | **k** |
| `Next Author` | **l** |
| `Previous Author` | **h** |
| `Open Post` | **Return** |
| `Scroll to Top` | **.** |

### `Web Column`

| Action | Shortcut |
|---|---|
| `Hide Web Column` | **Escape**, **Ctrl+W** |
| `Back` | **Alt+Left** |
| `Forward` | **Alt+Right** |
| `Reload Page` | **Ctrl+R**, **F5** |

### `Composer`

| Action | Shortcut |
|---|---|
| `Send Post` | **Ctrl+Return** |
| `Close Composer` | **Escape** |

### Shortcuts inside the media viewer

| Action | Shortcut |
|---|---|
| Previous / next attachment | **←**, **→** |
| Fullscreen | **F** |
| Close | **Escape** |
| Zoom in / out | **+**, **-** |
| Fit to window | **0** |

### About `Escape`

`Escape` is **not** a global application shortcut, and that is deliberate: as an application shortcut it would be resolved before the focused widget and would swallow the `Escape` that closes a dialog, dismisses the mention list and does the other things `Escape` is supposed to do. That is why "back" is **Alt+Left**.

## 10.2 The main menu

The three-line button, at the top of the sidebar, opens the main menu. The same menu appears on the login screen and in the control bar of the pages.

| Item | What it is for |
|---|---|
| `Open Profile` | Opens your own profile. |
| `Announcements` | The announcements published by your server. |
| `Follow Requests` | Who wants to follow you. |
| `Mutes & Blocks` | Your muted or blocked accounts, domains and conversations. |
| `Draft Posts` | The drafts saved on the server. |
| `Scheduled Posts` | The posts scheduled on the server. |
| `Collections` | Collections of accounts suggested by the server (Mastodon 4.6+). |
| `#FediWrapped` | Your yearly report (the *Wrapstodon*). |
| `Admin Dashboard` | The moderation dashboard. Only appears for those with an administrator role. |
| `Preferences` | Opens Preferences. |
| `Plugins` | Opens the plugins page. |
| `Keyboard Shortcuts` | Opens the shortcut list. |
| `Help` | Opens the application help. |
| `About Babble` | About the application. |

**Not in the main menu:** `New Post`, `Reload` and `Quit`. The first two have a button in the header and a shortcut; the third has **Ctrl+Q**.

Active plugins can add their own items to the main menu.

## 10.3 The login menu

On the welcome screen, the main menu shows only: `Preferences`, `Plugins`, `Keyboard Shortcuts`, `Help` and `About Babble`. Every other action requires a connected account.

## 10.4 Where the other lists live

| List | Where to open it |
|---|---|
| The items of a post | The `More Options` button, in the corner of the post. See [2.5](02-reading.md#25-a-posts-menu). |
| The items of a profile | The `More Options` button, on the profile page. See [2.8](02-reading.md#28-someone-elses-profile). |
| Notification options | The gear button in the header of the `Notifications` page. |
| Catch Up options | The `List Options` button, in the header of the list of posts. |
| Media viewer options | The `More Actions` button, in the viewer header. |
| Sidebar mode | The `Sidebar` button, at the bottom of the sidebar. |

## 10.5 Interface labels in English and Portuguese

The manual quotes interface labels as the source code defines them, in English. The application is translated into German, Spanish, French and Brazilian Portuguese; the Brazilian Portuguese edition of this manual carries the label-by-label table with the Portuguese equivalent of each label quoted here. Labels that do not appear in that table have no Brazilian Portuguese translation in application 0.3.0, so the interface shows them in English; the translation is incomplete in 0.3.0, and the table grows with every release.

## 10.6 Messages and notices

The application's messages are the ones the server returns, plus the ones Babble itself produces. The tables below are for reference.

### Login and session

| Message | Where it appears |
|---|---|
| `Remove Account?` | When removing an account from the device. |
| `The login for @account will be removed from this device.` | The body of that confirmation. |
| `Enter Authorization Code…` | The button that lets you paste the code by hand. |
| `Restart Babble to use the new language` | The notice after choosing the interface language. |

### Posting

| Message | Meaning |
|---|---|
| `Post sent` | The post went out. |
| `Post edited` | The post was edited. |
| `Post scheduled` | The post was scheduled. |
| `Post deleted` | The post was deleted; the toast offers `Undo`. |
| `Post is too long` | The text went over the server limit. |
| `Add content warning text` | The content warning is on and empty. |
| `Add at least two poll choices` | The poll needs two options. |
| `Poll choices must be unique` | Two options with the same text. |
| `A poll cannot be combined with media` | A poll and an attachment do not go together. |
| `Could not load post for editing` | The server did not return the post for editing. |
| `Vote failed` / `Refresh failed` | Failure while voting or while refreshing the result. |
| `Could not translate` | The server could not translate. |
| `Could not mute conversation` / `Could not unmute conversation` | Failure while muting or reactivating a thread. |
| `Could not pin post` / `Could not unpin post` | Failure while pinning a post on the profile. |
| `Could not change quote policy` | Failure while changing who may quote. |

### Media

| Message | Meaning |
|---|---|
| `Attachment removed` | The attachment was removed; the toast offers `Undo`. |
| `Only images, videos and audio can be attached` | Unsupported file type. |
| `A post can have at most {n} attachments` | The attachment limit of the server. |
| `A video or audio file must be the only attachment` | Video and audio go alone. |
| `Wait for the post to be sent to attach files` | A post is under way. |
| `Could not paste the image` / `Could not paste the files` | The clipboard had nothing usable in it. |
| `Could not take a screenshot` | The portal refused the capture. |
| `Could not open the file picker` | The file picker did not open. |
| `“{name}” is too large for this server ({limit} is the limit)` | The file goes over the server limit. |
| `The server is still processing the media` | The server is processing; Babble waits up to two minutes. |
| `The server did not accept the media` | The server refused the file. |
| `Could not upload media` | Failure while uploading. |
| `Could not save media` | Failure while writing the file. |
| `Saved “{name}”` | The file was saved; the toast offers `Show in Folder`. |
| `Could not open media` | The "Open with…" did not find an application. |
| `Media Still Processing` | The server is still processing the file. |
| `Preview Not Available` | This kind of attachment cannot be shown here. |
| `Could Not Load Image` / `Could Not Load Media` | The media did not load. |
| `Media Not Stored by Your Server` | Your server has no copy, and you refused to load it from the origin server. |
| `Loading media from other servers is turned off` | The `Never Load` setting is in force. |
| `Load Media From Another Server?` | The consent request. |
| `Your server has no copy of this media. Loading it from {host} lets that server see your IP address and that you viewed it.` | The body of the consent request. |

### Notifications and messages

| Message | Meaning |
|---|---|
| `Request accepted` / `Request rejected` | The answer to a follow request. |
| `Notification request accepted` / `Notification request dismissed` | The decision on a held notification. |
| `Notification settings updated` | The server rules were saved. |
| `Could not save notification settings` | Failure while saving. |
| `Could not mark as read` | Failure while marking the conversation as read. |
| `Conversation deleted` | The conversation left your inbox. |
| `This conversation has no messages` | The conversation is empty. |

### Contact and moderation

| Message | Meaning |
|---|---|
| `Mute failed` / `Muted {account}` | Muting the account. |
| `Blocked {account}` | Blocking the account. |
| `Report sent` | The report was sent. |
| `Link copied` | The address was copied. |
| `URL copied` / `Image copied` | In the media viewer. |
| `Copy “{…}” Post Link?` | The confirmation before copying the link of a private post. |

### Plugin

| Message | Meaning |
|---|---|
| `Plugins Are Unavailable` | `libpeas 2` is not installed. |
| `Babble needs libpeas 2 and its Python loader to load plugins` | The body of the message. |

### Catch Up

| Message | Meaning |
|---|---|
| `Scanning timeline…` | The scan is under way. |
| `The server is limiting requests, so this catch-up is incomplete. Try again in {n} minutes.` | The server limited the rate of requests. |
| `The server limited requests, so this catch-up is incomplete.` | The same notice, without a deadline. |
| `This catch-up could not be saved, so it will not be here next time.` | The snapshot could not be written. |
| `Remove This Catch-Up?` | When removing a snapshot. |
| `It will be deleted from this device.` | The body of that confirmation. |
| `At most {n} hashtags can be combined` | The hashtag limit of a combined timeline. |

### Connection

| Message | Meaning |
|---|---|
| `You’re offline` | The bar shown when the network goes down. |

## 10.7 Limits

The limits come from the server, and Babble respects what the server declares.

| Limit | Source | Typical value |
|---|---|---|
| Characters per post | The server's `max_characters` | 500 |
| Characters per URL | The server's `characters_reserved_per_url` | 23 |
| Attachments per post | The server's `max_media_attachments` | 4 |
| Characters of the media description | The server's `description_limit` | 1500 |
| Options in a poll | The server's `max_options` | 4 |
| Duration of a poll | The server's `min_expiration` and `max_expiration` | 30 minutes to 7 days |
| Maximum size of an attachment | The server | varies by type |
| Posts of the `Home` per page | The server | 800 |
| Hashtags per combined timeline | Babble | 5 |
| Local drafts kept | Babble | 5 |
| Catch Up snapshots kept | Babble | the last three, listed in the menu |

### Features that depend on the server version

| Feature | Minimum Mastodon version (API) |
|---|---|
| Collections | 10 (4.6) |
| Profile tabs | 8 (4.4) |
| Alt text for avatar and header | 9 (4.6.1) |
| Endorsement | 6 (4.0) |
| Link timeline | 2 (3.5) |
| Yearly report state | 8 (4.4) |
| Notification policy | 2 (3.5) |
| Blurred filter | 5 (3.7) |
| Quotes | 7 (4.3) |
| Endorsements | 6 (4.0) |

When the server does not have the feature, Babble **hides** the option instead of showing something broken. The complete and up-to-date list is in `Docs/mastodon-compat.md`.
