# What Babble does

The full feature inventory. The [README](../README.md) has the short version.

## Reading

- Home, Local and Federated timelines, updated live over streaming; a post
  that came in through a hashtag you follow says so above its header.
- Explore: trending posts, hashtags and news, plus people you might want to
  follow.
- Threads with the focused post in the middle, and profiles with their posts.
- Notifications, with a Mentions filter for when you only care who's talking
  to you.
- Direct messages, bookmarks, favorites, lists, followed hashtags, and saved
  multi-hashtag timelines.
- Quote posts, edit history, and who boosted or favorited something.
- Custom emoji inline wherever a `:shortcode:` turns up, and a media viewer
  with zoom, video, audio and alt text.
- Instance announcements, with reactions.
- Your yearly #Wrapstodon, if your server has one for you.

## Catch Up

- From its own sidebar place: pick a time range, page back through the home
  timeline to it, and read the result two ways — a bento grid of the most
  relevant posts, links and hashtags, or a compact list of every post.
- A run is saved as an unchanging snapshot that is only rebuilt when you
  choose a new period.

![A thread](../data/screenshots/thread.png)

## Writing

- Media uploads (paste, drag, or take a screenshot right from the composer),
  content warnings, polls, quotes and language selection.
- Spell checking as you type.
- `@mention` and `#hashtag` suggestions as you type. Hashtags you used
  lately, keep on your profile or follow come first. What you type after
  the `#` is sent to your own instance to look up matching hashtags, as
  it is for `@mention`.
- Drafts, and scheduled posts for when you'd rather say it tomorrow.
  Editing a scheduled post preserves its poll choices, exact duration, poll flags,
  language, sensitivity and quote policy. Unsupported settings are refused before
  changing the composer or the server entry.
  Field handling follows Mastodon's
  [ScheduledStatus contract](https://docs.joinmastodon.org/entities/ScheduledStatus/).
- Scheduled edits create a replacement before removing the original. If removal
  fails, the composer locks submission and shows **Retry Removal**, which retries
  only the original deletion. Closing requires a duplicate-post warning; reopening
  the cached composer retains this recovery state. After exit or account removal,
  use the server's scheduled-post list to resolve any remaining duplicate.
- Reply cancellation restores the preceding draft's text and posting mode. Quotes
  cannot inherit an unfinished edit's update target, and thread segments do not
  repeat a prior quote or reused attachments.
- Threads: with **Keep Open for a Thread** on, the title counts the post you are
  writing ("Thread · Post 2") and a dimmed strip shows the end of the one before.
  Replying to the last of your own posts picks the thread up with the right
  number, and editing one of them shows its place ("Edit Post · 2/5"). The
  thread is read from your instance; if that fails, the composer behaves as usual.

## Wallabag

The optional Wallabag plugin reports sign-in only after credentials reach the
system keyring, and sign-out only after their deletion succeeds. A failed initial
save leaves the previous session unchanged. If storing a rotated refresh token
fails, pending saves stop and sign-in explains the keyring failure; it does not
claim the unusable old credentials are still a durable login.

## Keeping things tidy

- More than one account, even on different servers.
- Server-side filters, mutes, blocks, blocked domains and follow requests.
- Reporting accounts and posts.
- Profile editing.
- Collections (curated account recommendations) on Mastodon 4.6+.
- An admin dashboard for moderators (it asks for admin permissions
  separately, only when you open it).

## A note on versioned features

Some features need a recent server, and Babble hides them rather than
showing something that would fail:

- **Collections** need Mastodon 4.6+.
- **Profile tabs** (Featured / Media) need Mastodon 4.6+; the alt text on
  your avatar and header needs 4.6.1+.
- **Explore link timelines** need Mastodon 4.3+.
- **Restricted public timelines** — the local, federated, hashtag and
  trending-link feeds a server can close to most accounts — need 4.5+, and
  honour the "View live and topic feeds" role permission.

Where a feed exists on an older server, or the server says nothing, Babble
treats it as open. See [Mastodon API compatibility](mastodon-compat.md) for
the full list and the reasoning.
