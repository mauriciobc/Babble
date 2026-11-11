# 8 — Preferences: complete reference

> **Document:** `BABBLE-MAN-001` — chapter 8 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

This chapter is **reference** material: it lists every preference, what it does, and when it takes effect. There is no step-by-step here — for that, see the feature chapters.

## 8.1 Opening and using Preferences

Open it with **Ctrl+,** or from the `Preferences` item of the main menu.

The Preferences window has a **search box**: type and the pages filter themselves. That is the fastest way to find a preference whose name you do not remember.

The pages are `Appearance`, `Reading`, `Timelines`, `Posting` and `Notifications`. The `Posting` page only exists when an account is connected.

## 8.2 The `Appearance` page

### Main group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Language` | Takes effect the next time Babble starts | List: `System language`, `English`, `Deutsch`, `Español`, `Français`, `Português (Brasil)` | The language of the whole interface. **It only takes effect the next time the application starts.** |
| `Color Scheme` | — | List: `System`, `Light`, `Dark` | The colour scheme. |
| `Text Size` | Set the text size used in the interface | List: `Small`, `Normal`, `Large` | The text size of the interface. There are shortcuts for this: **Ctrl++**, **Ctrl+-** and **Ctrl+0**. |
| `Large Line Height` | Increase the line height in posts | Switch | More spaced-out lines in the body of posts. |
| `Dimmed Trivial Notifications` | Notifications like favorites and boosts will be less visible | Switch | Favorites, boosts and followers appear less prominently on the notifications page. |
| `Translucent Sidebar` | Blur the desktop behind the sidebar. Needs a compositor that supports background blur | Switch | A blurred background in the sidebar. Depends on the compositor. |

### `Images and Emojis` group

| Preference | Control | Effect |
|---|---|---|
| `Darkened Images in Dark Mode` | Switch | Darkens images in the dark theme. |
| `Large Custom Emojis` | Switch | Larger custom emojis. |
| `Custom Emoji Zoom on Hover` | Switch | The custom emoji grows a little when the pointer passes over it. |

## 8.3 The `Reading` page

### `Posts` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Collapsed Long Posts` | — | Switch (on) | Long posts made up mostly of links or hashtags arrive collapsed, with the `Show More` button. |
| `Spoilers` | Reveal posts with a content warning | Switch | **On**, the content of a post with a content warning arrives revealed. Off, it arrives hidden. |
| `Link Preview Cards` | — | Switch (on) | Shows the link preview card. |
| `Bluesky Posts` | Bluesky links open in Babble, which contacts Bluesky’s servers | Switch (on) | Opens Bluesky posts inside the application. Off, it opens in the browser. **When on, the application contacts Bluesky’s servers.** |
| `Interaction Counters` | Reply, boost and favorite counts on post actions | Switch (on) | Shows the counts in the action bar. |
| `Post Actions on Timelines` | Actions stay available in each post’s menu | Switch (off) | When off, the action bar disappears from the timelines and the buttons move to the post’s `More Options` menu. |

### `Media` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Letterboxed Media` | Scale down and letterbox media to fill the image containers instead of cropping them | Switch | The media is fitted whole, with bars, instead of being cropped. |
| `Photo Viewer` | How a photo opens in the viewer: as a box over the window, or filling it | Two options: `Box`, `Window` | How the image opens in the viewer: as a box over the window, or filling it. |
| `Media From Other Servers` | Loading media your server has no copy of lets the author’s server see your IP address | List: `Ask Each Time`, `Always Load`, `Never Load` | Consent for media your server does not have. See [5.7](05-media.md#57-media-hosted-on-another-server). |
| `Sensitive Media` | Reveal sensitive media instead of hiding it behind a warning | Switch | Reveals sensitive media automatically. |
| `Autoplay Animated Media` | Play animated GIFs by themselves. Reduced motion and power saving always stop them | Switch (on) | Animated GIFs play on their own. |
| `Play Animated GIFs in Timelines` | Plays one at a time. Uses more memory | Switch (off) | The most visible GIF in the timeline plays, one at a time. |
| `Load Media Automatically` | For video, audio and animated GIFs. Images always load | List: `Always`, `Not on Metered Connections`, `Never` | When heavy media is downloaded. |

### `Home Timeline` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Hidden Replies From Non-Follows` | On the home timeline, hide posts that are replies from accounts you do not follow | Switch | Hides replies from accounts you do not follow, on the `Home`. |
| `Hidden Boosts on Home` | On the home timeline, show only posts and replies by the people you follow, without their boosts | Switch | Hides boosts, on the `Home`. |
| `Remembered Reading Position` | Reopen the home timeline at the post you were reading, with a button for newer posts | Switch (on) | The `Home` reopens where you stopped. |

### `Links` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Tracking Parameter Removal` | This option can lead to broken links when enabled | Switch (on) | Removes tracking parameters from links. It can break them. |
| `Remote Links in Babble` | Open links to posts and profiles on other servers inside the app | Switch (on) | Post and profile links from other servers open in Babble. |
| `Web Links Beside the Timeline` | Show other links in a column instead of the browser. Pages keep no cookies once Babble closes | Switch | Only appears when WebKitGTK is installed. Opens pages in a column beside the timeline. |
| `JavaScript on Web Pages` | Many sites do not work without it | Switch | JavaScript in the web column. Turning it off increases security and breaks many sites. |
| `Copied Link Suggestions` | Check the clipboard for post and profile links when Babble is focused | Switch (on) | Offers to open the copied post or profile. |

### `Filters` group

It appears when an account is connected, with the description *"Hide or warn about posts that match keywords, on every device you use"*.

| Item | Effect |
|---|---|
| `Manage Filters` | Opens the `Filters` dialog, where you create, edit and delete filters. See [2.7](02-reading.md#27-server-filters). |

## 8.4 The `Timelines` page

### `Live Updates` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Live Timelines` | Receive new posts and notifications in real time | Switch (on) | A permanent connection to the server. |
| `Live Public Timelines` | This will increase memory usage when browsing high traffic timelines | Switch | Live updating also on the public timelines (`Local` and `Federated`). Depends on the previous one. |

### `Refresh` group

The group description is *"Used while live updates are off or unavailable"*.

| Preference | Control | Effect |
|---|---|---|
| `Automatic Refresh` | List: `Off`, `Every 30 seconds`, `Every minute`, `Every 5 minutes` | How often it reloads. |

### `Sidebar` group

The group description is *"Choose which timelines the sidebar offers"*.

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Catch Up` | Summarize what you missed in your home timeline | Switch | Shows or hides the `Catch Up` place. |
| `Explore` | Show posts trending on your server | Switch | Shows or hides the `Explore` place. |
| `Local Timeline` | Show posts from your server | Switch | Shows or hides the `Local` place. |
| `Federated Timeline` | Show posts from all known servers | Switch | Shows or hides the `Federated` place. |
| `Disabled Timelines` | Your server has turned these timelines off, but this account may still read them | Switch | Only appears when the server actually turned a timeline off. Re-enables the restricted timelines this account can still read. |

### `Boosts` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Boosts Carousel` | When a page of posts is mostly boosts, fold them into a horizontal carousel instead of one row each. Applies to timelines opened afterwards | Switch (on) | Groups the boosts into a horizontal carousel. |
| `Single Boost per Post` | Show only the first boost of a post that several people boosted. Applies to timelines opened afterwards | Switch (on) | Shows only the first boost of each post. |

## 8.5 The `Posting` page

It only exists with a connected account. The `Defaults for New Posts` group has the description *"Changes are also saved to your server preferences"*.

### `Defaults for New Posts` group

| Preference | Control | Effect |
|---|---|---|
| `Default Visibility` | List: `Public`, `Unlisted`, `Followers only`, `Direct` | The initial visibility of the composer. **Saved on the server.** |
| `Default Quote Policy` | List: `Everyone`, `Followers`, `Only me` | Who may quote your posts. Only on Mastodon 4.5+. **Saved on the server.** |
| `Default Language` | List: `System language` and about 60 languages | The initial language of the composer. **Saved on the server.** |
| `Media Marked Sensitive` | Switch | Marks new media as sensitive. |

### `Boosts and Reminders` group

| Preference | Subtitle | Control | Effect |
|---|---|---|---|
| `Advanced Boost Dialog` | Change boost visibility, quote and confirm boosting | Switch | Opens the `Boost With Visibility` box before boosting. |
| `Old Post Reply Warning` | — | Switch (on) | Warns when replying to a post older than 90 days. |
| `Private Post Link Warning` | — | Switch (on) | Warns before copying the link of a private post. |
| `Missing Alt Text Boost Warning` | — | Switch (on) | Warns before boosting a post with media that has no description. |

### Shortcut to the server preferences

The `Server Preferences` item, at the end of the page, opens `https://{your-server}/settings/preferences` in the browser. That is where the server keeps its own posting preferences — the same ones Babble writes when you change the default visibility, language or quote policy.

## 8.6 The `Notifications` page

### `Desktop Notifications` group

The group description is *"Announced while the window is not focused"*.

| Preference | Subtitle | Effect |
|---|---|---|
| `Mentions` | — | Notifies mentions. |
| `New Followers` | — | Notifies new followers. |
| `Follow Requests` | — | Notifies follow requests. |
| `Favorites` | — | Notifies favorites. |
| `Boosts` | — | Notifies boosts. |
| `Poll Results` | Polls you voted in or created | Notifies the end of polls you voted in or created. |
| `New Posts` | From accounts you turned notifications on for | Notifies posts from accounts you turned notifications on for. |
| `Edits` | Posts you boosted that were edited | Notifies edits to posts you boosted. |
| `Quotes` | — | Notifies quotes. |
| `Quote Edits` | Posts you quoted that were edited | Notifies edits to quotes you made. |
| `New Sign-Ups` | Server administrators only | Notifies new accounts. Administrators only. |
| `New Reports` | Server administrators only | Notifies moderation reports. Administrators only. |
| `Grouped Notifications` | Combine notifications of the same type and post into one | Groups notifications of the same type about the same post into one. |

### `Server` group

It only appears on Mastodon 4.3 or later.

| Item | Subtitle | Effect |
|---|---|---|
| `Notification Filtering` | Choose whose notifications reach you | Opens the `Notification Settings` dialog, with the server rules. See [3.3](03-notifications-and-messages.md#33-the-servers-rules-for-notifications). |

### `Background` group

| Preference | Subtitle | Effect |
|---|---|---|
| `Background Mode` | Keep running after the window is closed to deliver notifications | The application keeps running with the window closed. |
| `Quick Post Shortcut` | Open the composer from anywhere with a system-wide shortcut, which can be changed in the system settings | Registers a system-wide shortcut that opens the composer. The row only appears if the global shortcuts portal exists. |

## 8.7 Preferences with no button

Some options of the application have no switch in the Preferences window, because they are adjusted somewhere else:

| Option | Where to adjust it |
|---|---|
| Sidebar mode (`Full Sidebar`, `Icons Only`, `Hidden`) | The `Sidebar` button, at the bottom of the sidebar. |
| Account list and active account | The account button, at the top of the sidebar. |
| Enabled plugins | `Plugins`, in the main menu. |
| Pinned lists and hashtags | The star, in the header of each one's page. |
| Video and audio volume | The media viewer. |
| Search recents | The `Search` page. |
| Local drafts | The composer, when you close it. |
