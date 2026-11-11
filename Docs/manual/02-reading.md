# 2 — Reading: timelines, threads, profiles and search

> **Document:** `BABBLE-MAN-001` — chapter 2 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

This chapter is the heart of the manual: the six answers to the question "where does what I read come from".

## 2.1 The sidebar

The sidebar is the list of destinations in the application. Each item is a *place*: a named destination, with a page that Babble builds the first time you open it and keeps for the rest of the session, so that the scroll position and the loaded content stay where they were.

| Place | What it shows |
|---|---|
| `Home` | Posts from the accounts you follow, plus their boosts. |
| `Catch Up` | A ranked summary of what you missed. See [chapter 6](06-catch-up.md). |
| `Notifications` | Mentions, boosts, favorites, followers, and the rest. See [chapter 3](03-notifications-and-messages.md). |
| `Conversations` | Direct messages. See [chapter 3](03-notifications-and-messages.md). |
| `Search` | Search for accounts, hashtags and posts. |
| `Favorites` | The posts you favorited. |
| `Bookmarks` | The posts you saved. |
| `Hashtags` | The hashtags you follow and the combined hashtag timelines. |
| `Explore` | What is trending on your server. |
| `Local` | The public timeline **of your server**. |
| `Federated` | The public timeline of **all known servers**. |
| `Lists` | Your lists of accounts. |
| `Pinned` | The lists and hashtags you pinned, in a separate section. |

**Shortcut:** **Ctrl+1** through **Ctrl+9** jump between `Home`, `Notifications`, `Conversations`, `Search`, `Favorites`, `Bookmarks`, `Hashtags`, `Explore` and `Local`.

### Changing the sidebar size

**Objective:** make room for the timeline, or keep the labels in view.

**Pre-conditions:** the sidebar is visible.

1. Click the `Sidebar` button at the bottom of the sidebar.
2. Choose one of the three modes:
   - `Full Sidebar` — icons **and** labels.
   - `Icons Only` — a narrow strip of 64-pixel icons.
   - `Hidden` — the sidebar disappears from the screen.

**Result:** the change is immediate. With the sidebar hidden, the `Sidebar`, `Main Menu`, `New Post` and `Notifications` buttons appear in the header of every page — the same set of controls, in the same place.

**If it goes wrong:** in narrow windows (less than 601 pixels), the sidebar closes itself to give room to the content; **F9** toggles it.

### Pinning a list or a hashtag to the sidebar

**Objective:** keep a list or a hashtag always at hand.

**Pre-conditions:** you already have the list or the hashtag.

1. Open the list or the hashtag.
2. Click the star button in the page header. Its tooltip is `Pin to Sidebar`.
3. To undo it, click the same button again (`Unpin from Sidebar`).

**Result:** the item appears in a `Pinned` section at the end of the sidebar. A pinned list shows the list's actual title; a pinned hashtag shows `#name`.

**If it goes wrong:** if the star does not appear in the header, the page has not finished loading — go back to `Home` and open the list again. To undo it, click the same star (`Unpin from Sidebar`).

## 2.2 The `Home` timeline

`Home` is the timeline of posts from the accounts you follow. The order is chronological, newest first, and it is the only page that updates live by default.

Scrolling has no end: as you near the bottom, Babble fetches the next page. Pulling the page down at the top reloads it immediately.

When new posts arrive while you are scrolled further down, a `N New Posts` button appears at the top (or `N New Post`, in the singular). Clicking it takes you to the top.

### Options that filter the `Home`

Two preferences change what appears in `Home`, and only there:

- **Hidden Replies From Non-Follows** (`hide-replies-non-follows`) — hides replies coming from accounts you do not follow. It is the most useful option for anyone who wants `Home` to be a conversation between the accounts they follow.
- **Hidden Boosts on Home** (`hide-boosts-home`) — hides boosts and leaves only what the accounts you follow wrote.

A third one, **Remembered Reading Position** (`resume-reading-position`), makes `Home` reopen exactly where you left off, with a button for the newest posts. It is on by default.

### Reading position

With the reading position on, reopening `Home` makes Babble scroll to the post you were reading. A `N New Posts` button appears so you can jump to what came after it.

### Reloading the page

There are four ways, and they all do the same thing:

| Path | When to use |
|---|---|
| Pull the page down at the top | You are at the top and want to refresh now. |
| **Ctrl+R** or **F5** | You are anywhere on the page. |
| `Reload`, from the main menu | The same thing, from the keyboard, for anyone who prefers the menu. |
| Automatic refresh | Preferences → Timelines → `Automatic Refresh`: `Off`, `Every 30 seconds`, `Every minute` or `Every 5 minutes`. It applies while live update is off or unavailable. |

## 2.3 Opening a thread

**Objective:** read a post and the replies to it, with the context above.

**Pre-conditions:** you are in a timeline or on a profile.

1. Click the post. It opens on the thread page on its own.
2. Read from top to bottom: the original post sits in the middle, its ancestors above it and the nested replies below.
3. Use **Alt+Left** to go back to the page you came from.

**Result:** the thread page shows the original post with more context than the timeline does. The post header shows the position within the thread (`n/total`), which helps you tell whether you missed anything.

**If it goes wrong:** the thread page shows `Loading Thread` while it fetches, and `Could Not Load Thread` if it fails. In that case, go back and try again; if the server is overloaded, wait a few seconds.

## 2.4 Interacting with a post

Every post has an action bar at the bottom, with five buttons:

| Button | What it does | Notes |
|---|---|---|
| `Reply` | Opens the composer with a reply. | Fills in `@account ` and inherits the original post's visibility. If the post carries a content warning, the composer opens behind the same warning. |
| `Boost` | Reposts the post. | It is a two-state button: clicking it again undoes the boost. When you undo it, a `Boost removed` toast appears with `Undo`. Direct posts cannot be boosted. |
| `Quote` | Posts a comment along with the quote. | It appears only when the server allows it and the post can be quoted. |
| `Favorite` | Marks it as a favorite. | Private. |
| `Bookmark` | Saves it to read later. | Private. |

The counts (`12 boosts`, `4 favorites`) appear only when the **Interaction Counters** preference is on.

When **Post Actions on Timelines** is off — the default — the action bar disappears from the timelines and the buttons move to the post's `More Options` menu, as a row of icons. On thread pages, the bar always appears.

### Before you boost

Two permissions may ask for your confirmation before the boost:

- **Advanced Boost Dialog** (`advanced-boost-dialog`) — opens the `Boost With Visibility` dialog, where you choose who sees the boost: `Visible for all`, `Visible for all, but not in public timelines` or `Visible for your followers only`. It is also where you choose between `Boost` and `Quote`.
- **Missing Alt Text Boost Warning** (`boost-alt-text-reminder`) — if the post has media with no description, Babble warns you: *"Some of your followers might not be able to understand it."* You can tick `Don't remind me again`.

### Replying to an old post

Replies to posts older than 90 days ask for confirmation: `Reply to an Old Post?` — *"This post is {age} old. You can still reply, but it may no longer be relevant."*. The `Old Post Reply Warning` preference turns this notice off.

## 2.5 A post's menu

The `More Options` button, at the top right of every post, opens a menu that is built each time it opens — its contents depend on the post and on who you are. The items that appear:

### Links

| Item | What it does |
|---|---|
| `View Post` | Opens the thread. |
| `Open in Browser` | Opens the post's URL in the browser. |
| `Copy Link` | Copies the URL. For private or direct posts, a confirmation appears first, warning that the link only shows the post to the people mentioned (and, depending on the visibility, to your followers). |
| `View on Bluesky` | Only on posts that come from Bluesky through the *Bridgy Fed* bridge. |
| `View Stats` | Opens the tab with who favorited, who boosted and who quoted. It does not appear on direct posts that are not yours. |
| `View Edit History` | Only if the post has already been edited. |

### Post

| Item | What it does |
|---|---|
| `Translate` / `Show Original` | Translates the post into the interface language. `Show Original` goes back to the original text. |
| `Mute Conversation` / `Unmute Conversation` | Mutes the thread. Only on your own posts and on posts that mention you. |
| `Pin to Profile` / `Unpin From Profile` | Pins the post to your profile. Only on your own public or unlisted posts. |
| `Who Can Quote` | Who can quote it: `_Anyone`, `My Followers` or `Nobody`. Only on your own posts. |
| `Remove My Post From Quote` | Removes your post from someone else's quote. |

### Your posts

| Item | What it does |
|---|---|
| `Edit` | Opens the post in the composer, with the text and the media editable. |
| `Delete and Redraft` | Deletes the post and copies the text, the attachments and the poll into a new draft. |
| `Delete` | Deletes the post. It disappears at once, and a `Post deleted` toast offers `Undo` — the removal only reaches the server when the toast expires. |

### Posts by other people

| Item | What it does |
|---|---|
| `Mention {account}` | Opens the composer with the mention. |
| `Mute {account}` | Mutes the account. A `Muted {account}` toast offers `Undo`. |
| `Block {account}` | Blocks the account, after a confirmation. |
| `Report Post…` | Opens the report dialog. |

Active plugins can add their own items to this menu.

## 2.6 Content warnings and long posts

### Content warnings

A post with a content warning hides the text behind a bar carrying the warning and a button. The button reads `Show Content` while the text is hidden and `Hide Content` once it has been revealed; the text slides into view.

By default, the content **is already revealed**. Anyone who prefers the opposite turns off the **Spoilers** preference (`show-spoilers`), in Preferences → Reading. The state is remembered per post: if you opened one, it stays open.

The button also hides the content again, without waiting for the scroll to finish.

### Long posts

Posts longer than 700 characters, or with more than 14 line breaks, or that are almost only links or hashtags, are collapsed. The `Show More` button expands them; `Show Less` collapses them again. The **Collapsed Long Posts** preference (`collapse-long-posts`) controls the collapsing; it is on by default.

### Translating a post

The `Translate` item in the post menu asks the server for a translation into the interface language, and the translated text appears below the original, with the note `Translated from {language} using {provider}`. The `Show Original` button goes back to the original text. If the server cannot translate, `Could not translate` appears.

Not every server translates: the translation providers are configured by the instance administrator.

## 2.7 Server filters

Your server may apply automatic filters, and Babble respects them. A filter has a **context** — `Home`, `Public`, `Notifications`, `Threads`, `Account` or `Conversations` — and an **action**:

| Action | What appears |
|---|---|
| `Hide` | The post disappears from the page. |
| `Warn` | The post becomes a `Filtered: {filter name}` bar with a `Show Anyway` button. |
| `Blur` | The post stays readable, but the media is covered. |

If more than one filter matches, the strongest one wins: `Hide` over `Warn`, and `Warn` over `Blur`. `Blur` applies only when **all** the matching filters ask for a blur.

You manage filters in Preferences → Reading → `Manage Filters`, or through the menu item the server offers. There you create filters with a title, keywords, a context and an action, and set when they expire.

## 2.8 Someone else's profile

**Objective:** see what someone posts and what they have done with your posts.

**Pre-conditions:** you are in a post or in a search result.

1. Click the author's name or avatar. The profile opens as a sheet over the window.
2. Use the tabs at the top: `Profile`, `Posts`, `Replies`, `Media`, `Featured`, `Likes`, `Follows`, `Followers`. Which tabs exist depends on the server and on the account's own settings.
3. The button in the bottom bar is the relationship action: `Follow`, `Unfollow`, `Follow Back`, `Withdraw Request`, `Unblock` or `Unblock Domain`.

**Result:** the profile opens as a sheet over the window, with the header, the tabs and the relationship button. Clicking `Follow` makes the account appear in your `Home`.

**If it goes wrong:** if the profile does not load, the server may be slow or rate-limiting requests: close the sheet and open it again. If the message `This user has chosen to not make this information available` appears, the account has hidden its follower counts — the `Follow` button still works. See [11.12](11-troubleshooting.md#1112-a-feature-does-not-appear).

**What the header shows:** avatar and cover image (both clickable, they open in the viewer), name, `@account`, bio, profile fields, verified links (`Verified Link`), `Joined {date}`, `Last post {date}`, and the account migration note, when there is one. A long bio gets a `Show More` / `Show Less`.

**Badges that appear on the profile:** `In Memoriam`, `Suspended`, `Unverified Handle`, `Locked`, `Automated`, `Group`, `Requested`, `Mutuals`, `Follows you`, `Blocks you`, `Blocked`, `Domain Blocked` and `Muted`.

**Clickable counters:** `following` and the follower count open lists titled `Followers` and `Following`.

**The profile's `More Options` menu:**

| Item | What it does |
|---|---|
| `Mention` | Opens the composer with `@account`. |
| `Direct Message` | Opens the composer with the mention and the `Direct` visibility. |
| `Copy Handle` | Copies the `@account`. |
| `Copy Link` | Copies the profile URL. |
| `Open in Browser` | Opens the profile in the browser. |
| `Receive Post Notifications` | Turns notifications about this account's posts on or off. |
| `Feature on Profile` | Features the account on your profile. |
| `Show Boosts` / `Hide Boosts` | Shows or hides boosts on your profile. |
| `Add or Remove from Lists…` | Opens the `Add to Lists` sheet. |
| `Private Note…` | Opens a private note, which only you can see. |
| `Remove from Followers` | Removes the account from your followers. |
| `Mute…` / `Unmute` | Mutes the account, with a choice of duration (`Never`, `Hours`, `Days`) and the `Hide Notifications` option. |
| `Block…` / `Unblock` | Blocks the account, after a confirmation. |
| `Block Domain…` / `Unblock Domain` | Blocks the entire server. |
| `Report…` | Opens the report dialog, in the `Moderation` section. |

## 2.9 Your own profile

`Open Profile`, in the main menu, opens your own profile. The difference from other people's profiles: the bottom bar button becomes `Edit Profile`, and the `More Options` menu loses the items that only make sense for someone else (`Follow`, `Mention`, `Direct Message`).

The `Edit Profile` dialog has the following groups:

- **Images** — `Avatar`, `Avatar Description`, `Header Image`, `Header Description`, each with a `Choose…` button.
- **About** — `Display Name` and `Bio`.
- **Profile Fields** — custom fields, with `Add Field`.
- **Privacy and Discovery** — `Require Follow Requests`, `Automated Account`, `Suggest Account to Others`, `Include Public Posts in Search Results`, `Hide Followers and Following`.
- **Posting Defaults** — `Default Visibility`, `Mark Media as Sensitive`, `Default Language`.
- **Profile Tabs** — `Show Featured Tab`, `Show Media Tab`, `Include Replies in Media Tab` (Mastodon 4.6 servers or later).

The buttons are `_Cancel` and `_Save`.

## 2.10 Lists

A **list** is a named set of accounts, with a timeline of its own. Lists are the most direct way to follow a group — a team, a project, a topic — without mixing it in with the rest of the feed.

### Creating a list

**Objective:** have a timeline of your own for a group of accounts.

**Pre-conditions:** you are on the `Lists` page.

1. Click `New List` in the page header — or the `_New List` button on the empty page.
2. Type the `Title`.
3. In `Show Replies To`, choose who appears alongside: `Members of the List`, `People You Follow` or `Nobody`.
4. Turn on `Hide Members from Home` if you want the members' posts to appear **only** in the list, and not in `Home` as well.
5. In `Members`, use `Add Members` to search among the accounts you follow and add them.
6. Click `_Save` (or `C_reate`, on a new list).

**Result:** the list appears on the `Lists` page and can be pinned to the sidebar.

**If it goes wrong:** if the list does not appear after you save it, the server may have refused the title — try a different title. To fix it, use `Edit List` on the list itself. An empty list is normal: it only gets posts once you add members.

### A list's timeline

A list page has `Edit List` and the pin button in the header. When it is empty, it says `No Posts in This List` and explains: *"Add members to the list to see their posts"*.

To remove a list, use the trash button on the `Lists` page: the confirmation warns that *"“{title}” will be deleted. The posts of its members are not affected."*

### Adding an account to a list

From the person's profile: `Add or Remove from Lists…` opens the `Add to Lists` sheet, where the lists that already contain the account are ticked. Ticking and unticking takes effect immediately; `Close` only closes the sheet.

## 2.11 Hashtags

### A hashtag page

Clicking a hashtag inside a post opens its page, with the timeline of the people who use it. In the header:

- `Follow` / `Unfollow` — follow the hashtag. Posts with a hashtag you follow start appearing in your `Home`, and the post's source shows `From hashtag {tag}`.
- The `Pin to Sidebar` star — pins the hashtag to the sidebar.
- `More Options` — `Feature on Profile` and `Remove from Featured Tags`.

A usage line shows the size of the tag: `"{posts} · {people}, last {days} days"`.

When the page is empty: `No Posts With This Hashtag` — *"No one has posted anything with this tag yet"*.

### The `Hashtags` page

It has two sections:

- **Followed Hashtags** — the hashtags you follow, each with the pin star and an `_Unfollow` button.
- **Multi-Hashtag Timelines** — the saved timelines that combine hashtags.

### Combining hashtags

**Objective:** a timeline that gathers hashtags together, with inclusion and exclusion conditions.

**Pre-conditions:** you are on the `Hashtags` page.

1. Click `New Multi-Hashtag Timeline` in the header.
2. Type the `Title`.
3. Turn on `Local Only` to restrict it to posts from your server.
4. In `Hashtags`, add the timeline's hashtags — the first one is the main one.
5. In `Also Requiring`, add the hashtags a post needs **in addition** to the first one.
6. In `Excluding`, add the hashtags that **exclude** the post.
7. Click `_Save`.

**Result:** the timeline appears under `Hashtags` and can be pinned to the sidebar.

**If it goes wrong:** if nothing is saved, the server refused — check that the three lists add up to at most 5 hashtags, the application's limit. The warning `At most {n} hashtags can be combined` appears when the limit is exceeded.

The limit is 5 hashtags across the three lists combined; when you exceed it, the application warns `At most {n} hashtags can be combined`.

## 2.12 Explore

`Explore` shows what your server considers trending, in four tabs:

| Tab | What it shows |
|---|---|
| `Posts` | Trending posts. |
| `Hashtags` | Trending hashtags, with a usage chart. |
| `News` | The most shared links, as preview cards. |
| `For You` | Accounts the server suggests you follow, **with the reason**: `Featured by the server staff`, `You interacted with them before`, `Popular lately`, `Popular on the server`, `Similar to people you followed`, `Followed by people you follow`. |

The `News` tab is the one that pays off most: clicking a card opens the **link timeline**, which gathers every post that mentions that link — not the whole web, only what the federation is saying about it. The `Open in Browser` button in the header opens the link in the browser.

The `For You` tab has an `_Follow` button for each account and a dismiss button. The **Explore** preference, in Preferences → Timelines → Sidebar, hides this place from the sidebar.

## 2.13 Search

**Objective:** find accounts, hashtags and posts.

**Pre-conditions:** you are on any page.

1. Click `Search` in the sidebar, or press **Ctrl+F** (**Ctrl+K** works too).
2. Type in the search box.
3. Results appear grouped under `Accounts`, `Hashtags` and `Posts`. Each group has a `See All …` button.

**Result:** the `Search` page shows the three groups. If nothing matches, `No Results` — *"Nothing matched “{term}”"*.

**If it goes wrong:** if the page shows `Search Failed`, the server did not respond. Try again, or reload the window with **Ctrl+R**. If an operator search returns nothing, the reason is almost always the server: not every server implements every operator. See [2.13](#213-search).

### Search operators

Mastodon search accepts operators in the text, and Babble documents them on the search home page:

| Operator | What it does | Example |
|---|---|---|
| `from:` | Restricts to an author. | `from:ana` |
| `has:` | Restricts to the content type. | `has:media` |
| `is:` | Restricts to the post type. | `is:reply` |
| `language:` | Restricts to the language. | `language:pt` |
| `before:` | Before a date. | `before:2026-01-01` |
| `after:` | After a date. | `after:2026-01-01` |
| `during:` | On a date. | `during:2026-01-01` |
| `in:` | Restricts to a collection. | `in:bookmarks` |

A `-` prefix negates the operator: `-is:reply` hides replies.

**Not every server implements every operator.** That is why the search page says *"Search operators may not work on every server"*.

### Advanced search

The `Advanced Search` button, in the search page header, opens a query builder with fields: `Query`, `From User`, `Only Replies`, `Only Posts with Content Warnings`; the `Containing` group (`Media`, `Poll`, `Embed`); the `Timelines` group (`Search In`: `Everywhere`, `Library (Favorites, Bookmarks, Own Posts)`, `Public`, and `Language Code`); and the `Dates` group (`Before`, `During`, `After`).

The dialog says so plainly: *"Advanced search may not work on every server. Invert an option by prefixing it with “-”, for example -is:reply hides replies."*

### Recent searches

The search home page keeps your recent searches, with a `_Clear` button to erase them all and one button per entry to remove a single one.

### Pasting a link

Pasting the address of a post or a profile into the search field resolves the address and opens the matching content — inside Babble.

## 2.14 Favorites, bookmarks, `Local` and `Federated`

- **`Favorites`** — the posts you favorited. Empty: `No Favorites Yet`.
- **`Bookmarks`** — the posts you saved. Empty: `No Bookmarks Yet`.
- **`Local`** — your server's public timeline: what the users of your server post, and what it receives from the others. Empty: `No Posts Yet`.
- **`Federated`** — the public timeline of every server yours knows. Empty: `No Posts Yet`.

`Favorites` and `Bookmarks` are paginated by the server's `Link` header, which means that scrolling to the end takes you through the whole list, without gaps.

## 2.15 Repeated posts: the boosts carousel

When a page is made up mostly of boosts, Babble groups them into a horizontal carousel instead of one row each. The carousel arrows read `Show Previous Boosts` and `Show Next Boosts`.

Two independent controls:

- **Boosts Carousel** (`boosts-carousel`) — groups the boosts. It is on by default.
- **Single Boost per Post** (`dedupe-boosts`) — shows only the first boost of a post that several people boosted. It is on by default.

Both apply to pages opened **after** the change: reload the page to see the effect.

## 2.16 Summary: where everything is

| If you want… | Go to |
|---|---|
| Read what the accounts you follow posted | `Home` |
| See what your server publishes | `Local` |
| See what the whole federation publishes | `Federated` |
| Recover what you missed | `Catch Up` |
| Know who mentioned you | `Notifications` |
| Read direct messages | `Conversations` |
| Read again something you favorited | `Favorites` |
| Read again something you saved | `Bookmarks` |
| Follow a topic | `Hashtags` |
| Follow a group | `Lists` |
| Discover what is trending | `Explore` |
| Find an account, a tag or a post | `Search` |
