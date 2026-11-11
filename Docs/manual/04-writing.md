# 4 — Writing: the composer, polls, quotes and drafts

> **Document:** `BABBLE-MAN-001` — chapter 4 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

The Babble composer is a dialog on top of the main window. It keeps what you have written, checks the spelling, completes mentions and hashtags, and knows what your server accepts.

## 4.1 Opening the composer

There are four ways:

| Path | Note |
|---|---|
| The `New Post` button, in the header | Appears in the sidebar and in the header of the pages in narrow windows. |
| **Ctrl+N** | From anywhere. |
| `Mention {account}`, in a post's menu | Opens the composer with `@account` already written. |
| `Direct Message`, in a profile's menu | Opens the composer with `@account` and the `Direct` visibility. |

The **Quick Post Shortcut** (Preferences → Notifications) registers a system-wide shortcut — **Super+Shift+M** by default — that opens the composer from any application.

## 4.2 The anatomy of the composer

```
┌──────────────────────────────────────────────────────────────┐
│ [trash]           Dialog title              [clock] [Post]   │
├──────────────────────────────────────────────────────────────┤
│ (banner "Replying to a post")                                │
│ (banner "Scheduled for …")                                   │
│                                                              │
│   Text area: "What's on your mind?"                          │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ [content warning]  ("Content Warning" field)                 │
│ (poll group)                                                 │
├──────────────────────────────────────────────────────────────┤
│ [attach][screenshot][warning][poll][mention][emoji][thread]  │
│ [visibility] [language]                         [characters] │
└──────────────────────────────────────────────────────────────┘
```

The dialog **title** says what you are doing: `New Post`, `Reply`, `Quote Post`, `Edit Post`, `Edit Post · 1/3` (when the post has more than one editable part), `Edit Scheduled Post` or `Thread · Post 2` (when the composer stays open for a thread).

The trash button, in the top left corner, is `Discard Draft`: it throws the draft away, after a confirmation — *"The text, attachments and poll of this post will be lost"*.

## 4.3 Posting

**Objective:** send your post.

**Pre-conditions:** the text, the media or the poll are ready, and nothing is pending validation.

1. Type in the text field.
2. Review what Babble has flagged. Words with a spelling mistake are underlined; right-click to see the suggestions. The spell checker follows the language chosen in the `Post Language` selector.
3. Click `_Post` (the default button, at the top right) or press **Ctrl+Enter**.

**Result:** a `Post sent` toast confirms it. The composer closes and the post appears in your `Home`.

**If it goes wrong:** Babble refuses the post and explains why, with a toast:

| Message | Cause |
|---|---|
| `Post is too long` | The text went over the server's limit. |
| `Add content warning text` | The content warning is on, but the warning text is empty. |
| `Add at least two poll choices` | The poll has fewer than two choices. |
| `Poll choices must be unique` | Two poll choices have the same text. |
| `A poll cannot be combined with media` | A poll and an attachment in the same post. |

To close the composer without posting, use the `Discard Draft` button (which deletes it) or press **Escape** (which closes it and keeps the draft — see [4.13](#413-drafts)).

## 4.4 The character counter

The bottom right corner shows `Characters Left`. The count is the server's, not an estimate:

- The limit comes from the server (`max_characters`), 500 by default.
- Each URL counts as a fixed block of characters (23, or whatever the server declares), not by its real length.
- Each mention of someone on another server counts as `@user`.
- The content warning text counts as well.

The counter turns amber when 20% or less remain, and red when it goes past the limit.

## 4.5 Mentions

**Objective:** write someone's `@` without mistyping the identifier.

**Pre-conditions:** the composer is open.

1. Click the `Mention` button (person icon) in the bottom bar, or type `@` followed by the beginning of the name.
2. The `Suggested Accounts` list appears. Give it a name to narrow it down: *"Type a name to search"*.
3. Click the account you want.

**Result:** the `@account@server` is inserted into the text, with a space after it.

**If it goes wrong:** if the account does not appear in the list, type more letters: the search only brings back accounts your server knows. For an account on another server that yours has never seen, type the full identifier (`someone@example.social`) and Babble resolves it. If the mention comes out with no bold and no colour, the server did not recognise it as a mention — but it still works as text.

If you type the full identifier (`someone@example.social`), Babble resolves the account even if your server has never seen it.

To mention someone without notifying them, write the `@` inside a code block, or use the `(@someone)` form. The server understands both.

## 4.6 Hashtags

1. Type `#` at the start of a line or after a space.
2. The `Suggested Hashtags` list appears, ordered by usefulness: first the exact match for what you typed; then the hashtags you used recently, the ones on your profile and the ones you follow; finally the server's — the followed ones first, and then by number of people.
3. Choose one from the list.

**Result:** the hashtag is inserted. The capitalisation you typed is kept: typing `GamingD` and choosing `gamingdeals` gives `GamingDeals`.

The suggestion captions say where they came from: `Recently used`, `On your profile`, `Followed`, and `{n} people, last 2 days`.

## 4.7 Emoji

1. Click `Insert Emoji` (smiley icon).
2. The panel has two tabs: `Emoji`, with the system emojis, and `Custom`, with your server's emojis. The `Custom` tab disappears when the server has no custom emojis.
3. Search in `Search Emoji` or `Search Custom Emoji`, or browse the categories. The `Recently Used` category keeps your most recent emojis.

**Result:** a standard emoji is inserted as a character; a custom one goes in as `:short_name:`, with spaces around it.

## 4.8 Content warning

1. Click the `Content Warning` button (triangle icon) in the bottom bar.
2. Type the warning in the field that appears.

**Result:** the text stays hidden from your followers until they click `Show Content`. The warning also marks the post as sensitive, automatically.

Reply to a post that has a content warning and the composer opens with the same warning, filled in — you do not start from scratch.

## 4.9 Polls

**Objective:** publish a poll.

**Pre-conditions:** the server allows polls.

1. Click the `Poll` button (bars icon) in the bottom bar.
2. The poll group appears with two choices, called `Choice 1` and `Choice 2`.
3. Type the text of each choice.
4. Click `_Add Choice` for more choices — up to the server's limit (4 by default).
5. In `Duration`, choose `30 minutes`, `1 hour`, `1 day` or `7 days`. The server may restrict this set.

**Result:** the poll appears in the post, with the choices as radio buttons (or as check boxes, if it allows more than one answer).

**Restrictions:** a poll cannot live together with media, and the choices have to differ from one another. The server refuses with `A poll cannot be combined with media` and `Poll choices must be unique`.

**If it goes wrong:** if the `Poll` button does not exist or has no effect, your server may not allow polls. The server also refuses the post with `Add at least two poll choices` and `Poll choices must be unique`. See [10.6](10-reference.md#106-messages-and-notices).

### Voting

In the post, an open poll is a form: choose the options and click `_Vote`. The `Show Results` button shows the result without voting.

After you vote, or when the poll closes, bars appear with the percentages, your choice marked as `Your vote`, and the winner in bold. The footer shows `{n} votes`, `Closed` or `{time} left`, and a reload button (`Refresh Results`) to refresh the result. The `_Back to Voting` button returns to the form.

If the connection fails while you vote, the notice is `Vote failed`.

## 4.10 Quoting a post

**Objective:** comment on a post while keeping it in view of your followers.

**Pre-conditions:** the post can be quoted — the server has to support quotes (Mastodon 4.5 or later) and the author has to allow it.

1. Click `Quote` in the post's action bar, or `Quote` in the `Boost With Visibility` menu.
2. Write your comment. The quoted post goes along, with your text in front of it.
3. Post.

**Result:** the composer title becomes `Quote Post`, and the post goes out as a quote.

**If it goes wrong:** if the quote does not appear in the post after you send it, the server predates Mastodon 4.5 and does not support quotes. If the pasted address did not turn into a card, either the post cannot be quoted or you typed the address instead of pasting it — typing does not convert. See [10.7](10-reference.md#107-limits).

### Quoting by pasting the address

If you **paste** the address of a post — and only paste, never type it — Babble swaps the text for the quote card and tells you: `Post attached as a quote`, with an `Undo` button that undoes the swap and gives the address back as text.

This does not happen if you typed the address, if you undid it, if you dragged it, or if the draft was restored. And it does not happen if the post cannot be quoted: in that case the address stays as text.

Quoting requires Mastodon 4.5 or later. On older servers, the quote fields are omitted and the post goes out without a quote.

## 4.11 Visibility

The `Post Visibility` selector, in the bottom bar, has four options:

| Option | Who sees it |
|---|---|
| `Public` | Everyone, and it appears in the server's public timelines. |
| `Unlisted` | Anyone with the address, but it does not appear in the public timelines. |
| `Followers only` | Your followers only. |
| `Direct` | The mentioned people only. |

The initial value comes from Preferences → Posting → `Default Visibility`. That value is **stored on your server**, so it applies to any other Mastodon client as well.

**Note when quoting:** the quote uses whatever visibility is selected in the composer, which starts at the `Default Visibility` value. The quoted post does not impose its own — a public quote stays public even if the original is `Followers only`.

## 4.12 Language

The `Post Language` selector sets the language of the post. It has two jobs: the server uses it to filter and to translate, and Babble uses it to **pick the spell checker's dictionary**.

The initial value comes from Preferences → Posting → `Default Language`, which offers `System language` and about sixty languages.

## 4.13 Drafts

**Objective:** not lose what you started writing.

**Pre-conditions:** the composer has text, media or a poll.

1. Close the composer — with the close button or with **Escape**. *Do not* use the trash button, which discards on purpose.

**Result:** Babble stores the draft locally and shows a banner at the top of the main window: `Draft from {time}` — for example, `Draft from 5m` — or `You have an unsent draft`. Clicking `Restore` brings the draft back into the composer.

Babble keeps up to **5 drafts**, and each account has its own: a draft from one account is never restored in another.

**If it goes wrong:** the trash button (`Discard Draft`) deletes the stored draft, after a confirmation.

## 4.14 Scheduling a post

**Objective:** post later, without needing the application open at the time.

**Pre-conditions:** the server allows scheduling.

1. Write the post as usual.
2. Click the alarm button in the top right corner of the composer.
3. Choose the date, the time and the time zone. The tooltip is: *"Choose a time at least 5 minutes in the future."*
4. Click `_Schedule`.

**Result:** the composer shows the `Scheduled for {date}` banner with a `_Clear Schedule` button. The `_Post` button becomes `_Schedule`, with the tooltip `Schedule (Ctrl+Enter)`. The post stays scheduled on the server.

**If it goes wrong:** if the picker refuses the date, the time has to be at least 5 minutes in the future — and the time zone has to be right. If the scheduled post does not appear on the `Scheduled Posts` page, the server may have refused the schedule; try again. See [11.12](11-troubleshooting.md#1112-a-feature-does-not-appear).

To schedule without opening the date picker, the alarm button also works once the date is set: the tooltip becomes `Change Time`.

### The `Scheduled Posts` page

The main menu has `Scheduled Posts`. Each row shows `Scheduled for {date}`, plus chips with the visibility, the number of attachments, `Poll · {n} options` and `Reply`, when applicable. The actions are `Edit`, `Reschedule`, `Convert into a Draft` and `Delete`.

Editing a scheduled post **reschedules** it: the new entry replaces the old one. If the server cannot remove the original, Babble warns with the banner *"The replacement was saved, but the original scheduled post could not be removed. Both may be published."*, with a `Retry Removal` button. If you close the composer in that state, the application asks `Leave Duplicate Scheduled Posts?` and offers `Keep Open` or `Close Anyway`.

Deleting a scheduled post asks for confirmation: `Delete Scheduled Post?` — *"The post will not be published."*

## 4.15 Editing and deleting a post

### Editing

1. Open the `More Options` menu of your post and choose `Edit`.
2. The composer opens with the post's text and media, editable. The title is `Edit Post` or `Edit Post · {n}/{total}`.
3. Click `_Save`.

The media come back as editable rows: you can change the description and remove attachments. The sensitive marking is preserved.

If the post cannot be loaded for editing, the notice is `Could not load post for editing`.

### Deleting

1. Open the `More Options` menu and choose `Delete`.
2. Confirm in `Delete Post?` — *"This post will be removed from your profile and from the timelines of other servers."*

**Result:** the post disappears at once, and a `Post deleted` toast appears with an `Undo` button. The removal is only sent to the server when the toast expires — which gives you a window to change your mind.

### Deleting and redrafting

`Delete and Redraft` deletes the post and copies the text, the attachments and the poll into a new draft. The confirmation warns: *"This post will be deleted and its text, attachments and poll copied into a new draft."* The media can be re-attached to the draft for about 24 hours.

## 4.16 Posting a thread

**Objective:** write several posts that reply to one another.

**Pre-conditions:** the composer is open and the first post has not gone out yet.

1. Turn on the `Keep Open for a Thread` button, in the bottom bar of the composer.
2. Post the first one.
3. The composer **stays open**, with the text cleared and the title `Thread · Post 2`. The `End of the previous post` line shows the end of what you have just posted, so you know what you are replying to.

**Result:** each following post goes out as a reply to the one before it. When you are done, close the composer.

**If it goes wrong:** if the title does not become `Thread · Post 2`, the `Keep Open for a Thread` switch was not turned on. If the composer closes by itself halfway through, the server refused the previous post — see the toast. To undo a thread that is already published, delete the posts one by one, through the `More Options` menu.

## 4.17 Pinning and quoting on your posts

Two actions in the `More Options` menu control who can quote what you post:

- **`Pin to Profile`** — pins the post to your profile. It only applies to your own posts, public or unlisted.
- **`Who Can Quote`** — chooses between `_Anyone`, `My Followers` and `_Nobody`. It is the per-post equivalent of the `Default Quote Policy` preference.

The default value comes from Preferences → Posting → `Default Quote Policy` (`Everyone`, `Followers` or `Only me`), which is stored on the server and applies to all your clients. The preference only appears on Mastodon 4.5 servers or later.

To take one of your posts out of someone else's quote, use `Remove My Post From Quote` in the quote's menu. The confirmation says *"The quote will no longer show your post."* and the final notice is `Your post was removed from the quote`.

## 4.18 Summary: what the composer offers

| Feature | How to enable | Where the setting lives |
|---|---|---|
| Attach media | `Attach Media`, drag or paste | [Chapter 5](05-media.md) |
| Screenshot | `Take Screenshot` | [Chapter 5](05-media.md) |
| Content warning | `Content Warning` | Preferences → Posting |
| Poll | `Poll` | Preferences → Posting |
| Mention | `Mention` | — |
| Emoji | `Insert Emoji` | — |
| Thread | `Keep Open for a Thread` | — |
| Visibility | `Post Visibility` | Preferences → Posting → `Default Visibility` |
| Language | `Post Language` | Preferences → Posting → `Default Language` |
| Mark media as sensitive | automatic, if enabled | Preferences → Posting → `Media Marked Sensitive` |
| Schedule | alarm button | Preferences → Posting → `Old Post Reply Warning` and `Private Post Link Warning` (for the warnings) |
