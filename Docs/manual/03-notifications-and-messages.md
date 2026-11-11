# 3 — Notifications and direct messages

> **Document:** `BABBLE-MAN-001` — chapter 3 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

Notifications are what the server tells you happened to you: someone mentioned your post, someone boosted, someone asked to follow you. This chapter covers the notifications box, direct messages, and the three administration boxes of your account — follow requests, mutes and blocks.

## 3.1 The `Notifications` page

**Objective:** see everything the server has recorded about you.

**Pre-conditions:** you are signed in with your own account.

1. Click `Notifications` in the sidebar, or press **Ctrl+2** (**Ctrl+Shift+N** works too).
2. Use the tabs at the top to switch between `All` and `Mentions` — the second one shows only what mentions you.

**Result:** the page lists the notifications, newest first, with the avatar, a type icon, a text summary (`{account} favorited your post`, `{account} boosted your post`, `{account} followed you`, `{account} mentioned you`, `{account}’s poll has ended`, `{account} quoted your post`, `{account} edited a post you interacted with`, and so on), the relative time and a `Dismiss Notification` button.

**Possible states:** `Loading Notifications` while it loads, `Could Not Load Notifications` if it fails, and `No Notifications` — *"Mentions, boosts and follows will appear here"* — when there is nothing.

**If it goes wrong:** if the page stays on `Loading Notifications`, the server may be rate-limiting requests. If it shows `Could Not Load Notifications`, reload the window with **Ctrl+R**. See [11.4](11-troubleshooting.md#114-the-timelines-do-not-update).

### Grouped notifications

When several people do the same thing to the same post, Babble joins them into a single row. The row shows a fan of overlapping avatars and a `+N` counter; a button expands the group and shows who is in it. Clicking a person opens their profile.

### The attached content

Notifications that refer to a post carry an excerpt of it: author, text, a `Read More` button when the text is long, the poll result, and up to four thumbnails. Posts filtered by the server appear as `Filtered: {filter name}`, with `Show Anyway`. Sensitive media is announced as `{n} sensitive attachment(s)`.

### Actions inside a notification

Some notifications bring their own buttons, so you can reply without leaving the page:

| Notification type | Buttons |
|---|---|
| Follow request | `_Reject` and `_Accept` — followed by a `Request accepted` or `Request rejected` toast. |
| New follower | `_Follow Back` (or `Following`, if you already follow them, or `Follow request sent`, if the account is blocked). |
| Moderation reports | `_View Reports` — opens the panel. |
| Annual reports | `_View` — opens the report. |

## 3.2 Filtering what appears

The gear button at the top right of the page (`Notification Options`) has a `Filter` submenu, with a check mark for each type. The types are:

`Mentions` · `New Posts` · `Boosts` · `Favorites` · `Follows` · `Follow Requests` · `Polls` · `Post Edits` · `Quotes` · `Quote Edits` · `Sign-Ups` · `Reports` · `Severed Relationships` · `Moderation Warnings` · `Annual Reports` · `Added to Collections` · `Collection Updates`

The filter belongs to **Babble**: it hides the notification from your box. It does not stop the server from sending it.

### Desktop notifications

When the Babble window does not have focus, the application can show a system notification. This is controlled in Preferences → Notifications, with one button per type:

`Mentions` · `New Followers` · `Follow Requests` · `Favorites` · `Boosts` · `Poll Results` · `New Posts` · `Edits` · `Quotes` · `Quote Edits` · `New Sign-Ups` · `New Reports`

The last three concern server administrators only.

The **Grouped Notifications** option (`group-desktop-notifications`) gathers several notifications of the same type about the same post into a single one.

Notifications for favorites, boosts and followers can be shown with less prominence — the **Dimmed Trivial Notifications** option, in Preferences → Appearance, makes them visually more discreet without hiding them.

Action buttons appear on the system notification itself: `Favorite`, `Boost` and `Follow Back`.

## 3.3 The server's rules for notifications

From Mastodon 4.3 on, the server itself may decide which notifications reach you, and on whose behalf. This is different from Babble's filter: here it is the server that stops sending.

The setting lives in Preferences → Notifications → `Notification Filtering`, which opens the `Notification Settings` dialog.

### Filter Notifications From

For each class of sender, you choose between three responses:

| Class | What it means |
|---|---|
| `People You Don’t Follow` | Accounts you do not follow. |
| `People Not Following You` | Accounts that do not follow you, or that started following you less than three days ago. |
| `New Accounts` | Accounts created in the last 30 days. |
| `Unsolicited Private Mentions` | Direct mentions from people you do not follow — unless they are replying to a mention of yours. |
| `Moderated Accounts` | Accounts limited by the server administrators. |
| `Bots` | Accounts flagged as automated. |

| Response | What happens |
|---|---|
| `Accept` | The notification arrives normally. |
| `Filter` | The notification waits in a separate box, for your decision. |
| `Ignore` | The notification is discarded. |

The dialog explains the difference: *"Filtered notifications wait in a separate inbox until you accept them. Ignored ones are discarded."*

### Show Notifications For

The second part of the dialog is a list of switches, one per notification type — the same list as the `Filter` submenu in [section 3.2](#32-filtering-what-appears).

Changes are only sent to the server when you close the dialog, and only if something has changed. The application confirms with `Notification settings updated`.

**Older servers:** if your server does not have this feature, the item reads `This server does not support filtering notifications`.

### The held notifications box

The notifications you left on `Filter` wait on a page of their own. Babble shows a notice at the top of the notifications page with a `View` button that takes you there.

Each item in that box has `Accept` and `Dismiss`. Accepting delivers the notifications; dismissing discards them. After the decision, a toast confirms: `Notification request accepted` or `Notification request dismissed`.

## 3.4 Direct messages

**Objective:** exchange private messages with someone else.

**Pre-conditions:** you are on any page.

1. Click `Conversations` in the sidebar, or press **Ctrl+3**.
2. Click a conversation to open it. A conversation is a thread: the messages stay in order, and the last message is the one you see in the list summary.
3. To start a new conversation, click `New Direct Message` in the page header. The composer opens with the `Direct` visibility; type the `@account` of the recipient and write the message.

**Result:** the conversation appears in the list, with an unread dot when there is a new message. When you open a conversation, it is marked as read.

**If it goes wrong:** a conversation with no messages at all shows a `This conversation has no messages` notice.

### A conversation's menu

The button at the end of each row in the list opens a menu:

| Item | What it does |
|---|---|
| `Mark as Read` | Marks the conversation as read. |
| `Delete Conversation` | Deletes the conversation from your box. The confirmation warns: *"The conversation is removed from your inbox. Other participants keep their copy."* |

### Each conversation's summary

The row shows up to three avatars, the names, the relative time and a summary of the last message. The summary is the body of the message, or `CW: {warning}` when there is a content warning, or `{n} attachment(s)` when there are attachments, or `No messages` when the conversation is empty.

## 3.5 Follow requests

**Objective:** accept or refuse the people who ask to follow you.

**Pre-conditions:** your account may be locked — it is that setting that makes requests appear.

1. Open the main menu and choose `Follow Requests`.
2. Click `Accept` or `Reject` next to each person.

**Result:** the person starts following you (or does not). The page becomes empty, with `No Follow Requests` — *"Requests appear here when your account is locked."*

**If it goes wrong:** if the page is empty even though there are requests, your account is probably not locked — in that case the server creates no requests. Adjust the lock in the server's preferences, in `Server Preferences`.

The same buttons appear in the request notifications, without having to open the page.

## 3.6 Mutes and blocks

**Objective:** see and undo the accounts, domains and conversations you muted or blocked.

**Pre-conditions:** you are on your own account.

1. Open the main menu and choose `Mutes & Blocks`.
2. Choose one of the three tabs:
   - `Muted` — the muted accounts. Each row has `Unmute`.
   - `Blocked` — the blocked accounts. Each row has `Unblock`.
   - `Domains` — the servers blocked entirely. Each row has `Unblock`.

**Result:** the action is immediate and the row disappears from the list.

**If it goes wrong:** if the row does not disappear, the server may have refused. Close the page and open it again. To undo a mute or a block, use the same action again, or the matching item in the profile menu. See [11.4](11-troubleshooting.md#114-the-timelines-do-not-update).

To mute or block, use the post menu (`Mute {account}`, `Block {account}`) or the profile menu (`Mute…`, `Block…`, `Block Domain…`).

## 3.7 Server announcements

**Objective:** read the notices published by your server's administrator.

**Pre-conditions:** your server publishes announcements.

1. Open the main menu and choose `Announcements` — or the `_Announcements` item in the notifications options menu.

**Result:** each announcement appears as a card, with the formatted content, the publication and update dates and the event date. Unread announcements get an `Unread` badge and a `Mark as Read` button. The header has `Mark All as Read`.

If your server does not publish announcements, the page stays empty.

**If it goes wrong:** if the page errors when it opens, the server may have changed the announcement format. The `Mark All as Read` button exists only when there is at least one announcement.

## 3.8 Reference: notification types

| Type | Babble notification | Desktop notification |
|---|---|---|
| Mention | `Mentions` | `Mentions` |
| Post from someone you follow | `New Posts` | `New Posts` |
| Boost | `Boosts` | `Boosts` |
| Favorite | `Favorites` | `Favorites` |
| New follower | `Follows` | `New Followers` |
| Follow request | `Follow Requests` | `Follow Requests` |
| Poll ended | `Polls` | `Poll Results` |
| Edit of a post you interacted with | `Post Edits` | `Edits` |
| Quote | `Quotes` | `Quotes` |
| Edit of one of your quotes | `Quote Edits` | `Quote Edits` |
| New account on the server | `Sign-Ups` | `New Sign-Ups` |
| Moderation report | `Reports` | `New Reports` |
| Severed relationship | `Severed Relationships` | — |
| Moderation warning | `Moderation Warnings` | — |
| Annual report | `Annual Reports` | — |
| Added to a collection | `Added to Collections` | — |
| Collection update | `Collection Updates` | — |
