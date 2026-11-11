# 6 — Catch Up: recovering what you missed

> **Document:** `BABBLE-MAN-001` — chapter 6 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

`Catch Up` is Babble's answer to the question every social network leaves behind: *what did I miss?*

## 6.1 What it is

`Catch Up` is a **separate timeline**, not your `Home`. The difference lies in two decisions it takes on your behalf:

1. **It goes back in time.** You choose a window — the last hour, the last 6 hours, since your last catch-up — and Babble walks your `Home` backwards until it reaches it.
2. **It ranks.** What it finds does not come in chronological order: it comes sorted by how much it is worth your attention.

The result is stored as a **snapshot**: a local file, per account, that never changes again. You can come back to it days later and see exactly the same set of posts, even if the feed has moved on.

Everything is computed on your computer. Babble talks to the server only through the client API — the same path it uses to read `Home`.

The `Catch Up` place sits right after `Home` in the sidebar. If you do not want it in sight, Preferences → Timelines → Sidebar → `Catch Up` hides it.

## 6.2 Running a catch-up

**Objective:** see a summary of what you missed.

**Pre-conditions:** you are signed in to your account.

1. Click `Catch Up` in the sidebar.
2. The home page shows the title `Catch Up` and the description *"A separate timeline for your followings, offering a high-level view at a glance"*.
3. Choose the period in the button at the top right corner (or in the start menu): `Last hour`, `Last 3 hours`, `Last 6 hours`, `Last 12 hours`, `Last 24 hours`, `Since the last catch-up` or `As far back as possible`.
4. Click `_Catch Up`.

**Result:** Babble walks the timeline and shows the progress — `Scanning timeline…`, with the count `"{n} posts evaluated"` and the span `({n} h back)`. When it finishes, the results appear.

**If it goes wrong:** the `_Cancel` button stops the scan and shows what has been gathered so far. A catch-up uses up to about 30 server requests, and servers with a rate limit may answer `The server is limiting requests, so this catch-up is incomplete. Try again in {n} minutes.`

## 6.3 The highlights grid

The first thing on the page is a grid of *highlights*: a set of cards of varying sizes, chosen by the ranking. Each card is a different kind of information:

| Card type | What it shows |
|---|---|
| Post | The post, with the counts of replies, boosts and favorites, and who boosted it (`{account} boosted`, `{account} and {n} other boosted`). |
| News | A trending link, with the `News` label and an `Open in Browser` button. |
| Hashtag | A trending tag, with the `Hashtag` label, the name `#tag`, the usage line and the `Following` mark if you follow the tag. |

Posts that are trending at the moment get the `Trending` badge.

The `Show More` button expands the grid — how many pages appear is remembered for each snapshot.

## 6.4 The list of all posts

Below the grid, the `All posts` section lists **all** the posts in the snapshot, one per row, in a compact form: avatar, name, a one-line summary (the content warning text, when there is one; the body, when there is not), up to three thumbnails, the counts of replies, boosts and favorites, the relative time and the `Thread` mark on posts that started a thread.

Clicking a row opens the thread.

Above the list there are two controls:

- **A row of dots** — one dot per post, in chronological order, showing the distribution over time. The dots that pass the current filters stand out. The screen reader announces `Posts over time` and `"{shown} of {total}"`.
- **A row of avatars** — the authors, with the count of posts by each, from the most active to the least.

## 6.5 Filtering and sorting

The list options button (`List Options`) opens a panel with three controls:

### `Category`

Filters by post type. The categories are `All`, `Original`, `Replies`, `Boosts`, `Quotes`, `Followed tags`, `Groups` and `Filtered`. Each one shows the count.

### `Author`

A selector with `All authors` and, below it, each author with their post count.

### `Sort by`

| Sort | What it means |
|---|---|
| `Time` | Chronological. It is the default, oldest first. |
| `Replies` | How many replies the post attracted. |
| `Likes` | How many favorites. |
| `Boosts` | How many boosts. |
| `Density` | Conversation concentration — how many people talked about the same topic. |
| `Relevance` | What the ranking found most relevant. |

Two keys toggle the direction: `Ascending` and `Descending`. And there is the `Group by author` switch, which brings the posts by the same author together.

The `_Reset Filters` button puts everything back to the default.

Every change is confirmed by a toast that describes what changed — for example, *"Showing boosts, most likes first, grouped by authors"*.

## 6.6 Previous snapshots

The period menu has a `Previous catch-ups` section, with the account's three most recent snapshots. Each one is labelled `{start} – {end} · {n} posts` and has a remove button (`Remove This Catch-Up`).

Removing asks for confirmation: `Remove This Catch-Up?` — *"It will be deleted from this device."*

Opening an old snapshot does not rescan anything: it simply reopens the file that was already written.

## 6.7 The notices Catch Up can show

| Banner | What it means |
|---|---|
| `Covers {interval} ({n} posts)` | The coverage was smaller than the requested window — the server delivers at most 800 posts from `Home`. |
| `Only {n} post in {interval}` | There was little activity in the period. The banner offers `Catch up on 24 hours`. |
| `The server is limiting requests…` | The scan was cut short by the server's rate limit. |
| `This catch-up could not be saved…` | The snapshot could not be written, and it will not be there next time. |

The footer of the period menu warns about the limit of 800: *"Your server might only show a maximum of 800 posts in the Home timeline regardless of the time range."*

## 6.8 How the ranking works

This section is conceptual: it explains the criterion, so that you understand what sits at the top of the grid.

The ranking is a **port, function by function,** of the "For You" model of Elk — a Mastodon client whose ordering is a transcription of X's open source ranker (the `home-mixer` of `x-algorithm`). Babble trains no model at all: it computes, on your computer, a set of *action heads* (reply, favorite, boost, dwell, interaction speed, reach, author volume) and combines them with the weights X uses in production.

Three rules deserve a mention:

- **Mutual follower.** An original post by someone you follow and who follows you gets a strong boost in the reply head.
- **Video.** The video quality signal only counts for videos longer than 10 seconds, and for authors with few followers.
- **Diversity.** After scoring, Babble reorders so that you do not get five replies from the same thread, or the same author all the time.

The `Relevance` sorting in the list of all posts uses exactly that score. The other sortings — `Replies`, `Likes`, `Boosts`, `Density` — are direct counts, with no model.

## 6.9 Summary

| Question | Answer |
|---|---|
| I missed posts, how do I recover them? | `Catch Up` in the sidebar. |
| How far back does it go? | From 1 to 24 hours, since the last catch-up, or as far as the server allows. |
| Does it change over time? | No. The result is stored as a snapshot. |
| Where is it kept? | On your computer, per account. |
| Does it cost server requests? | Yes, up to about 30 per catch-up. |
