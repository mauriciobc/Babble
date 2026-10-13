# Catch Up — feature specification

Status: draft, not implemented.

Sources this spec ports from:

- **Phanpy** `src/pages/catchup.jsx` at `cheeaun/phanpy@7180c00` (2026-10-13):
  the fetch loop, post categories, link grouping, the posts bar, snapshots, the
  start screen and the post list. MIT, © Lim Chee Aun.
- **Elk (fork)** `app/composables/for-you/{ranking,diversity}.ts` at
  `mauriciobc/elk@9597db5`: the relevance model. MIT, © Elk contributors.
  That code is in turn a transcription of xai-org/x-algorithm `home-mixer`.
- The **original Catch Up draft**: the bento grid layout and tile allocation
  (§5 and §6), kept as written apart from the changes marked *GTK* or *Fix*.

Ported code keeps the MIT notice of its source in the module docstring, next
to Babble's `SPDX-License-Identifier: GPL-3.0-or-later`.

---

## 1. Overview

Catch Up is a separate view over the people you follow. You choose a time
range, Babble pages back through the home timeline to that point, and saves the
result as an unchanging **snapshot**. The snapshot is shown two ways:

1. **Highlights**: a bento grid of the most relevant posts, links and hashtags.
   Posts are ranked by Elk's For You model (§4). News and hashtag tiles come
   from the same trending data and widgets as the Explore page's News and
   Hashtags tabs (§3.5). Tiles are sized and packed by the original draft's
   allocation rules (§6).
2. **All posts**: Phanpy's compact, email-like list of every post in the
   snapshot, with category and author filters, sorting and grouping.

Everything is computed on the client. Nothing beyond the Mastodon client API is
called.

---

## 2. Interface

### 2.1 Placement

- A sidebar place, tag `catch-up`, titled "Catch Up", in group 0 right after
  Home. Wire it the way the other places are wired: an entry in `PLACES` and
  `place_title()` (`src/window.py`), and a branch in `_build_place()` that
  lazily imports `CatchUpPage` from `src/catchup.py`.
- New GSettings key `hide-catch-up` (b, default `false`), added to `HIDE_KEYS`
  and to Preferences next to `hide-trending`.
- New symbolic icon `babble-catch-up-symbolic` in the gresource bundle.
- The place is a `pages.make_nav_page` like every other place. It does not
  replace Home.

### 2.2 Start screen (Phanpy)

An `Adw.StatusPage`-style screen titled "Catch Up", with a short "What is
this?" expander ("A separate timeline for your followings, offering a
high-level view at a glance…").

- **Range**: a `Gtk.Scale` with 13 marked steps: *last 1 hour* … *last 12
  hours*, then *beyond 12 hours*. Under it, the absolute start time through
  `statustext.format_time()`, or "until the max" on the last step.
- **Beyond 12 hours**: when an earlier snapshot exists, an `Adw.SwitchRow`,
  "Until the last catch-up (*time*)", on by default. On, the range ends at the
  last snapshot's `end_at`; off, paging goes as far as the server allows.
- **Overlap note**: when the chosen range reaches back past the last
  snapshot's `end_at`, show "Overlaps with your last catch-up".
- **Server note**: "Your server might only show a maximum of 800 posts in the
  Home timeline regardless of the time range."
- **Catch Up** button (`suggested-action`).
- **Previously…**: the account's last three snapshots, each as
  "*start – end* · *N posts*" with a remove button (`pages.confirm`).

### 2.3 Loading

An `Adw.Spinner` with live text, kept from the original draft:

> Scanning timeline… 160 posts evaluated (8 h back)

The post count uses `ngettext`; the hours come from the oldest `created_at`
seen so far. A **Cancel** button stops paging and shows whatever was gathered,
exactly like a 429 (§7).

### 2.4 Results layout

Top to bottom, in one `Gtk.ScrolledWindow`:

1. **Header bar**: snapshot range ("Oct 13, 08:00 – 14:00"), a "New catch-up"
   button that returns to the start screen, and a **Refresh** button that runs
   a new catch-up over the same range and saves it as a new snapshot.
2. **Posts bar** (Phanpy): one dot per post in time order. Above 160 posts it
   becomes 320 time buckets (Phanpy's `binByTime`). Dots of posts matching the
   current filter are highlighted. A `Gtk.DrawingArea`, following
   `MonthlyChart` in `src/wrapped.py`.
3. **Filters** (Phanpy, §3.3): an `Adw.ToggleGroup` of categories with counts,
   and below it a horizontally scrolling row of author chips (avatar + count,
   most active first). A "Reset filters" button appears while any filter is set.
4. **Highlights**: the bento grid (§5, §6). It reacts to the category and
   author filters.
5. **All posts**: the Phanpy list (§2.5).

### 2.5 All posts list (Phanpy)

- A `Gtk.ListView` over the snapshot's `StatusItem`s, wrapped in a
  `Gtk.FilterListModel` (category, author) and a `Gtk.SortListModel`.
- **Sort**: Time (default, oldest first, as in Phanpy), Replies, Likes, Boosts,
  Density, plus **Relevance** (the §4 score). Each with ascending/descending.
  Non-time sorts compare the boosted post (`reblog`) when there is one; ties
  fall back to time.
- **Density** (Phanpy `postDensity`):
  `(len(spoiler_text) + text_length(content) + poll option text) / 140
  + 8 × media count`, or `+ 8` when there is no media but the card has an image.
- **Group by author**: sections through the sort model's `section-sorter` and a
  list header factory. Authors are ordered by their post count in the current
  filter.
- **Rows**: a new lightweight `CatchUpRow`, not `StatusRow`. Avatar, name, a
  one-line peek of the text (with CW text instead of content when there is a
  CW), up to three 48 px media thumbnails, reply/boost/like counts, relative
  time. Boosts show the booster(s); thread starters get a thread mark.
  Activating a row opens the thread with `pages.push_page`.
- A toast on each filter/sort change, as Phanpy does ("Showing boosts, most
  likes first, grouped by authors"), built from translatable parts, not one
  concatenated string.

### 2.6 Keyboard and accessibility

- Phanpy's keys inside the results: `j`/`k` next/previous post, `h`/`l`
  next/previous author, `Enter` open, `.` scroll to top. Register them in
  `shortcuts-dialog.ui`.
- Bento tiles are focusable and announce author, excerpt and counts. Focus
  order follows score rank, not visual position.

---

## 3. Data pipeline

### 3.1 Fetching (Phanpy, with fixes)

```
GET /api/v1/timelines/home?limit=40[&max_id=…]
```

- Page with `max_id`, using `request_finish_paged` and the `Link` header.
  Paging is sequential by nature.
- Between pages, wait **1 s** (`GLib.timeout_add_seconds`), as Phanpy does.
- A post is *in range* when the wrapper's `created_at` (the boost time, for
  boosts) is at or after `start_at`. Keep in-range posts.
- **Stop** when a whole page has no in-range post, when a page is empty, on a
  429 or any other error, on Cancel, or at the safety cap of **1,000 posts / 25
  pages**. Phanpy has no cap; the cap is there for servers that never stop
  returning pages.
  *Fix*: Phanpy runs its "nothing in range, stop" check inside the per-post
  loop, so it stops at the first page whose first post is out of range. Check
  once per page, after all its posts.
- Check `client.rate_limited()` before each request; if set, stop as for a 429.
- One `Gio.Cancellable` per run. Cancel it when the page is destroyed, the
  window closes, or the active account changes.
- `start_at` is `end_at − hours` for ranges 1–12; for *beyond 12 hours* it is
  the last snapshot's `end_at` (switch on) or unbounded (switch off).

### 3.2 Post-processing (Phanpy)

For each fetched status:

1. **Filters**: `timeline.status_filter()`. Drop `hide` (except your own
   posts). Keep `warn` and record it; `blur` does not count as filtered.
2. **Followed tags**: for non-boost posts that carry one of your followed
   tags (`GET /followed_tags`, matched against `tags` and the content's
   hashtags, case-insensitively), look up the author.
3. **Relationships**: one batched `GET /accounts/relationships?id[]=…` over
   the authors from step 2 *and* the authors of boosted posts. This gives
   `following` (needed by step 2 and by §4's in-network flag) and
   `followed_by` (mutuals, needed by §4.4). A followed-tag post whose author
   you do not follow is marked `followed_tags`.
4. **Threads**: if a post replies to another post in the snapshot that is not
   itself a reply, mark that other post as a thread starter.

### 3.3 Categories (Phanpy)

Each post gets exactly one category, checked in this order:

| Category | Rule |
|---|---|
| Filtered | a `warn` filter hit |
| Groups | `status.group` set (only shown when its count > 0) |
| Boosts | `reblog` set |
| Quotes | carries a quote, on servers with native quotes |
| Followed tags | marked in §3.2 step 3 |
| Replies | `in_reply_to_id` set and not a self-reply |
| Original | everything else |

Plus "All". The author filter matches the author *or* any of a post's boosters.

### 3.4 Extraction

**Boost deduplication** (Phanpy): boosts of the same `reblog.id` collapse into
the first one, which keeps the set of everyone who boosted it. The hidden
duplicates still count in the posts bar.

**Timeline links** (Phanpy, unchanged). Used to say who in your timeline
shared a trending link (§3.5), and as the fallback news source:

- Only cards with `type == 'link'`, a `url` and an `image`, from posts not in
  the Filtered category. The card is read from the boosted post when there is
  one.
- Group by the URL with any trailing `/` removed.
- Per link: `shared` (distinct sharers; the same account sharing twice counts
  once), the sharers themselves, and `likes`/`boosts` summed over *different*
  posts sharing it.
- Sort by `shared`, then `boosts`, then `likes`, all descending. Keep at least
  10 links; past the tenth, cut at the first link shared only once.

**Timeline hashtags**. Used to annotate trending tags (§3.5), and as the
fallback hashtag source:

- Count each lowercased tag once per post, over non-Filtered posts, and track
  the distinct accounts using it.
- As a fallback source, a tag qualifies when **at least 2 distinct accounts**
  used it in the window, sorted by distinct accounts, then post count.

### 3.5 News and hashtags (Explore's trending data)

News and hashtag tiles reuse what the Explore page's **News** and **Hashtags**
tabs already show (`src/explore.py` `NewsPage`, `HashtagsPage`):

- When a catch-up runs, after paging finishes, fetch in parallel:
  - `GET /api/v1/trends/links?limit=20`: the News tab's data. Mastodon
    `PreviewCard`s with `history` (per-day `uses` and `accounts`).
  - `GET /api/v1/trends/tags?limit=20`: the Hashtags tab's data. `Tag`s with
    `history`.

  Both go into the snapshot (§7), so reopening it shows the trends as they
  were then.
- **News order**: the server's trending order, with one adjustment: links
  that people in your timeline shared (matched against the §3.4 timeline links
  on the URL with the trailing `/` removed) move to the front, ordered by
  `shared`. Every matched link carries its sharers for the tile's "Shared by"
  line.
- **Hashtag order**: the server's trending order, with tags you follow, then
  tags used in your timeline (the §3.4 timeline count), moved to the front.
  Every tag carries its timeline post count when it has one.
- **Fallback**: when trends are off on the server (an error, or an empty
  list) or the `hide-trending` setting is on, build news from the §3.4
  timeline links and hashtags from the §3.4 timeline hashtags instead. A user
  who hid Explore has asked not to see server trends, so Catch Up honours
  that.
- The same helpers as Explore, nothing duplicated: `explore.people_talking`
  for "N people talking", `explore.open_link_timeline` to open a link,
  `hashtags.stats_text` for the usage line, `hashtags.Sparkline` for the
  chart, `hashtags.push_hashtag` to open a tag.

---

## 4. Relevance model (Elk For You, ported)

The score that decides which posts make the grid and how big their tiles are.
Port `ranking.ts` and `diversity.ts` faithfully; the deviations below are the
only intended differences, and each is a named constant so it can be compared
against Elk's value.

### 4.1 Pipeline

```
candidates (deduplicated posts, Filtered category excluded)
  → extract features            (extractRankingFeatures)
  → predict action probabilities (predictActionsFrom)
  → weighted sum + offset        (scoreCandidate)
  → slate adjustments            (applyAdjustments: new-author boost,
                                  author diversity, out-of-network discount)
  → sort by score
  → dedupeConversations          (max 3 per conversation)
  → diversityRerank              (hashed-feature DPP, reorders, never drops)
```

### 4.2 Candidates and time

- A candidate is a post after boost deduplication (§3.4). `in_network` is
  `True` unless the post reached the snapshot only through a followed tag.
- **`now` is the snapshot's `end_at`**, not the wall clock, so reopening a
  snapshot gives the same scores and the same grid.
- Age is measured from the *content's* `created_at` (the boosted post's, for
  boosts), as in Elk.

### 4.3 Features (`extractRankingFeatures`)

Port as is. The ones that carry the score in practice:

- **Coverage-corrected engagement**: for remote authors (`@` in `acct`),
  favourites ÷ 0.6, boosts ÷ 0.93, replies ÷ 1.0.
- `popularity = logNorm(engagement, 10 000)`,
  `velocity = logNorm(engagement / clamp(age_h, 0.25, 48), 500)`,
  `engagementSignal = 0.5·popularity + 0.5·velocity`, where
  `logNorm(x, s) = clamp01(log1p(x) / log1p(s))`.
- `replyDensity`, `reblogDensity`: each count's share of the corrected total.
- Content flags: image/video/link card/card image/poll/spoiler/sensitive,
  reply/self-reply/boost, `?` in text, plain text length.
- Author: bot, reach (`logNorm(followers, 50 000)`), spamminess (follow ratio,
  empty bio, zero posts), verified field.
- Language prior: 0.6 for a language the viewer does not read
  (`viewer_languages`, from the interface language plus the account's
  `source.language`), else `lift(languageAffinity, 1.15, 0.6)`.

### 4.4 Action heads and weights

Port `predictActionsFrom` line for line (`ranking.ts` lines 1077–1440 at
`9597db5`), including the content priors that fade as observed engagement
grows (`shrink(prior) = 1 + (prior − 1)·(1 − popularity)`; bot 0.4, link 0.7,
media 1.25, hashtags 1.3). For reference, the three heads that dominate a home
timeline:

```
favorite = B.favorite · lift(engagementSignal, 8) · lift(authorAff, 3, 0.4)
         · lift(tagAff, 1.6, 0.7) · lift(boosterAff, 1.8, 0.7)
         · (boost ? 0.9 : 1) · botPrior · linkPrior · mediaPrior · hashtagPrior
reply    = B.reply · lift(engagementSignal, 3) · lift(replyDensity, 2.5)
         · lift(authorAff, 5, 0.3) · lift(tagAff, 1.4, 0.8)
         · (question ? 1.4 : 1) · (poll ? 1.3 : 1) · (boost ? 0.5 : 1) · botPrior
retweet  = B.retweet · lift(engagementSignal, 6) · lift(reblogDensity, 2.5)
         · lift(authorAff, 2.5, 0.5) · lift(boosterAff, 1.6, 0.8)
         · (boost ? 0.5 : 1) · (reply ? 0.6 : 1) · botPrior
```

where `lift(s, max, min=1)` maps a signed signal in [−1, 1] to a factor, with
`lift(0, …) == 1`.

Weights are Elk's `MASTODON_WEIGHTS`; base rates are Elk's `BASE_RATES`:

| Head | Weight | Base rate | | Head | Weight | Base rate |
|---|---|---|---|---|---|---|
| favorite | 0.5 | 0.03 | | photoExpand | 0.05 | 0.02 |
| reply | 5.0 (+15 for mutuals' originals) | 0.0035 | | videoOpen | 0.05 | 0.02 |
| retweet | 1.0 | 0.018 | | vqv | 0.05 (videos > 10 s only) | — |
| quote | 1.0 | 0.12 × retweet | | dwell | 0.0 | 0.5 |
| share | 0.5 | 0.001 | | followAuthor | 4.0 (0 in network) | 0.0006 |
| click | 0.4 | 0.03 | | notInterested | −43.2 | 0.00012 |
| openLink | 0.2 | 0.012 | | muteAuthor | −58.8 | 0.00002 |
| profileClick | 0.0 | 0.005 | | blockAuthor | −31.2 | 0.000015 |
| | | | | report | −234.0 | 0.000003 |
| | | | | notDwelled | −0.02 | 0.22 |

The mutual reply boost (`bidirectionalFollowReplyWeightBoost` = 15) applies
to original, non-boost posts whose author follows you back (`followed_by`
from §3.2).

### 4.5 Combining

As in `scoreCandidate`:

```
net    = Σ positive terms − Σ negative terms          (term = P(action) · weight)
scaled = net ≥ 0 ? freshness · languagePrior · net : net
score  = offsetScore(scaled, weightSums(MASTODON_WEIGHTS))
offsetScore(c) = c < 0 ? (c + negSum) / totalSum · 0.001 : c + 0.001
```

Keep the per-term `reasons` list; it backs an optional "Why is this here?"
popover on tiles (§9 phase 3).

**Freshness**: Elk's `recencyMultiplier`,
`(1 − 0.12) · 0.5^(age / 12 h) + 0.12`.
*Deviation*: Elk returns 0 past 48 h (`AgeFilter`). Catch Up's range already
bounds the candidates, and a "beyond 12 hours" run can legitimately go past
48 h, so the hard cut is **off** (`CATCHUP_MAX_POST_AGE = None`). The floor
still makes old posts need roughly 3× the score of new ones after a day.

### 4.6 Slate adjustments (`applyAdjustments`)

1. **New-author boost**: as Elk (author ≤ 1,000 followers, original post,
   ≤ 24 h old, impression proxy `followers + 25 · engagement` < 5,000, not in
   the bottom 15 %). The candidate is lifted to the score at a rank drawn from
   [15, 16).
   *Deviation*: Elk draws that rank with `Math.random()`. Seed the RNG from the
   snapshot id so the grid is stable across reopenings.
2. **Author diversity**: walking in score order, the k-th post of the same
   author is multiplied by `(1 − 0.25) · 0.5^k + 0.25`.
3. **Out-of-network discount**: × 0.75 for followed-tag posts, and for
   in-network boosts and replies (Elk's `oonRescoreInNetworkRepliesReblogs`).

### 4.7 Conversations and diversity (`diversity.ts`)

- **`dedupeConversations`**: identical posts collapse to one; at most **3**
  distinct posts per conversation (union of `in_reply_to_id` chains present in
  the slate). Survivors keep their positions.
- **`diversityRerank`**: Elk's closed-form DPP over hashed bag-of-features
  embeddings (`EMBEDDING_DIM` 4096, sign hashing; features: tags, author,
  language, media kinds, link domains, emoji, content tokens with IDF from the
  slate). Pool 200, θ = 0.5, objective
  `q_i − Σ_{j∈S} log(1 − cos(i, j)²)` with `q_i = score_i / max_score`.
  It reorders; it never drops a post. Use sparse dict vectors in Python.

### 4.8 Affinity

Elk's heads take author, tag, booster and language affinity from a local
engagement history Babble does not have. Elk's contract makes a missing
signal a no-op (`lift(0, …) == 1`), so:

- **Phase 1**: an `AffinityResolver` that returns 0 for everything. The model
  then ranks on engagement, velocity, content shape, freshness, mutuals and
  diversity.
- **Phase 2**: bootstrap affinity from the server, once per catch-up, cached
  with the snapshot:
  - `GET /favourites` (2 pages) → `favourite` signals
  - `GET /bookmarks` (1 page) → `bookmark`
  - `GET /accounts/:me/statuses` (2 pages) → `reblog` (your boosts) and
    `reply` (`in_reply_to_account_id`)

  Derive affinities with Elk's `deriveAffinities` rules (strengths: follow 5,
  reply 4, bookmark 3, reblog 2, favourite 1; ×3 for mutuals; evidence caps;
  tag share 0.5, booster share 0.5, language share 0.25; 14-day half-life using
  the status's `created_at` as the timestamp), then normalize with
  `affinitySignal` (mid-rank CDF per sign).
- Recording in-app engagements locally, as Elk does, is out of scope.

---

## 5. Bento grid layout (original draft)

### 5.1 Grid

The grid fills its width with as many columns as fit (auto-fill), each at
least 180 px wide, with 160 px rows and 12 px gaps:

```
grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
grid-auto-rows: 160px;
gap: 12px;
```

*GTK*: GTK CSS has no grid layout, so this is a custom `BentoGrid(Gtk.Widget)`
in `src/catchup.py`:

- `columns = max(1, (width + 12) // (180 + 12))`, column width
  `(width − 12·(columns − 1)) / columns`.
- `do_measure` (height-for-width) returns `rows · 160 + 12 · (rows − 1)` for
  the packing at that width; `do_size_allocate` re-packs (§6.3) when the column
  count changes and places each child at its cell rectangle.
- Tiles never ask for more than their cell: labels use `lines=` with
  `Pango.EllipsizeMode.END`, pictures use `Gtk.ContentFit.COVER` with
  `can_shrink`.

### 5.2 Tile types

| Tile type | Size (cols × rows) | Content | Visual elements |
|---|---|---|---|
| Hero Card | 3 × 2 or 2 × 2 | Top 10 % scored posts, or rich news cards | Large image preview, author avatar, engagement badges, excerpt text |
| Medium Post | 2 × 1 | Posts scored in the top 10–30 %, or media posts | Avatar, text cut to 3 lines, image thumbnail or video preview |
| Compact Highlight | 1 × 1 | Lower-scored top posts, or single media items | Compact post snippet, boost indicator |
| Topic/Hashtag Tile | 1 × 1 or 2 × 1 | Trending hashtags (§3.5) | Hashtag name, post count within timeframe, mini sparkline/trend indicator |
| News Link Tile | 2 × 2 or 2 × 1 | Trending links (§3.5) | Domain favicon, article cover image, headline, publisher domain |

Tile rules:

- **Spans are clamped** to the column count (*Fix*): a 3 × 2 hero is 3 × 2
  with ≥ 3 columns, 2 × 2 with 2, and 1 × 2 with 1.
- Hero: 3 × 2 for the first hero on each 12-unit page when it fits, 2 × 2
  for the others.
- News: the content of Explore's `LinkCard`: cover image, provider (or
  domain), title, description, "By *author*", "*N* people talking". 2 × 2 when
  the card has an image, else 2 × 1. When people in your timeline shared it,
  the footer becomes "Shared by" with up to five sharer avatars and "+N".
  Activating it calls `explore.open_link_timeline` (the link timeline on
  Mastodon 4.3+, else the browser), as the News tab does.
- Hashtag: the content of Explore's `HashtagRow` with `sparkline=True`: a
  `hash_badge()`, `#name`, the `stats_text(history)` usage line and a
  `hashtags.Sparkline` over the tag's daily `history`. When the tag appears in
  your timeline, the post count within the timeframe is added ("12 posts in
  your timeline"). 2 × 1 when the history has ≥ 3 days with uses, else 1 × 1
  without the sparkline. Activating it calls `push_hashtag`.
  In the fallback (§3.5) there is no server history, so the sparkline is drawn
  from 12 time buckets of the tag's posts in the snapshot (Phanpy's
  `binByTime`), fed to the same `Sparkline`.
- *Fix*, favicon: Mastodon preview cards carry no favicon, and fetching one
  per domain would contact third parties. Show the provider or domain as text,
  exactly as `LinkCard` does; leave room for a favicon if a future API
  provides one.
- Video: the static `preview_url` with a play badge; nothing plays in the grid.
- **Content warnings**: a post with `spoiler_text` shows the CW text instead
  of the excerpt, and its media is blurred, unless `show-spoilers` is set.
  `sensitive` media follows `show-sensitive-media`. Posts in the Filtered
  category never enter the grid.
- Images load through `_TextureCache` (scaled decode); avatars through
  `AvatarCache`.
- Activating a post tile opens its thread.

---

## 6. Tile allocation (original draft)

### 6.1 Ordering and tiers

1. Order posts by the §4 score, descending, after `diversityRerank`.
2. The posts considered are the ones the grid will show: the first
   `pages × 12` units' worth (§6.2). Percentiles are computed **over those
   posts**, not over the whole snapshot (*Fix*: over 800 posts, P90 would make
   80 heroes).
3. Tiers:
   - `S ≥ P90` → Hero
   - `P70 ≤ S < P90` → Medium
   - `S < P70` → Compact
   With fewer than 10 posts, only the first is a Hero.
4. Medium is also used for a Compact-tier post with media when there is room
   in the page's budget; Compact with a single image shows the image.

### 6.2 Budget per 12-unit page

Grid capacity is counted in pages of 12 units (one unit = one 1 × 1 cell).
With *c* columns a page is `12 / c` rows tall (3 columns × 4 rows, 4 × 3,
2 × 6, 1 × 12; for 5 or more columns, round the page up to whole rows).

Per page:

- At most **2** News Link tiles, taken in §3.5 news order.
- At most **1** Hashtag tile, taken in §3.5 hashtag order.
- Fill the remaining units with post tiles in score order.

The grid shows **3 pages** at first; a "Show more" button adds 3 at a time
until posts run out. A post shown in the grid stays in the All posts list.

### 6.3 Packing

*Fix*: the draft did not say how tiles are placed, and CSS's default row flow
leaves holes. Pack like `grid-auto-flow: row dense`:

- Keep an occupancy matrix *c* columns wide.
- For each tile in order, scan rows top to bottom and columns left to right
  for the first free position where the tile fits.
- A tile that cannot fit in the current page goes to the next page, and the
  holes it leaves are filled by later Compact tiles.
- Interleave within each page: hero first, then news and hashtag tiles at the
  first free positions after it, then the remaining posts.

Packing is a pure function of (tiles, columns), in `catchupdigest.py`, so it
is unit-tested and recomputed cheaply on resize.

---

## 7. Snapshots and state

Replaces the original draft's 15-minute stale-while-revalidate cache with
Phanpy's snapshots.

- **Where**: `GLib.get_user_cache_dir()/babble/catch-up/<account key>/<snapshot id>.json`.
  `<account key>` is `accounts.account_key()` made safe for file names. The
  directory is created `0700` and the files `0600`, because snapshots hold
  followers-only and direct posts.
  *Fix*: Phanpy names snapshots by account id, which collides across
  instances. An account is `(id, instance)`.
- **What**:

  ```json
  {
    "version": 1,
    "id": "<uuid>",
    "account": "<instance>#<id>",
    "start_at": "ISO-8601 or null",
    "end_at": "ISO-8601",
    "stopped": "complete | cap | rate-limited | cancelled | error",
    "posts": [ /* raw Status objects, with Babble's markers:
                  _filtered, _followed_tags, _thread */ ],
    "relationships": { "<account id>": {"following": true, "followed_by": false} },
    "trends": {
      "links": [ /* PreviewCard objects from /trends/links, or null */ ],
      "tags":  [ /* Tag objects from /trends/tags, or null */ ]
    },
    "affinity": null
  }
  ```

  Scores, categories, links, hashtags and the layout are **not** stored. They
  are recomputed when the snapshot opens: that is cheap, and deterministic
  because `now = end_at` and the RNG is seeded by `id`.
- **Retention**: the 3 newest per account; older ones are deleted after a new
  one is saved. "Remove" deletes one. All of an account's snapshots are
  deleted when the account is removed (`accounts.forget_account`).
- **View state** (category, author, sort, group, show-more pages, scroll
  position): kept in memory per window and snapshot id, like Phanpy's
  `sessionStorage`. Not persisted.
- Opening a post always loads it live, so edits and deletions after the
  snapshot show there.

---

## 8. Edge cases

- **Low activity** (fewer than 20 posts in the range): show an inline
  `Adw.Banner`, "Only *N* posts in the last *H* hours", with a "Catch up on
  24 hours" button. This replaces the draft's automatic expansion, which made
  the range selector show one range while displaying another.
- **Too few for a grid**: below 6 eligible tiles, hide Highlights and show the
  list only.
- **Rate limiting (429)** (original draft): stop paging, build the grid from
  the posts gathered, save the snapshot with `stopped: rate-limited`, and show
  a banner with the time `client.rate_limit_remaining()` gives.
- **Duplicate news URLs** (original draft + Phanpy): a trending link shared
  by several people in your timeline is one News Link tile with all sharers'
  avatars. In the fallback, timeline posts sharing one URL are grouped into one
  tile with combined counts (§3.4).
- **Trends unavailable or hidden**: use the timeline-derived fallback (§3.5)
  without any message. A trends request that fails does not fail the
  catch-up.
- **Account switch or page closed mid-fetch**: the cancellable stops the
  loop; nothing is saved.
- **Safety cap reached**: save with `stopped: cap` and say "Covers the last
  *H* hours (*N* posts)".

---

## 9. Implementation in Babble

### 9.1 Modules

| Module | Contents | Display needed |
|---|---|---|
| `src/catchuprank.py` | §4: features, heads, weights, `offset_score`, adjustments, conversation dedupe, diversity rerank, affinity resolver | No: no GTK imports |
| `src/catchupdigest.py` | §3.2–§3.4 and §6: categories, boost dedupe, links, hashtags, density, time buckets, tiers, budgets, packing | No: no GTK imports |
| `src/catchup.py` | `CatchUpPage`, the fetch loop, snapshot I/O, `BentoGrid`, tile widgets, `CatchUpRow`, posts bar | Yes |

Each new module goes in the `install_data` list in `src/meson.build`.

Changes to existing modules, so the tiles share Explore's widgets instead of
copying them:

- `src/explore.py`: move the body of `LinkCard.__init__` into
  `link_card_content(link, sharers=None)`, which returns the `Gtk.Box`. Both
  `LinkCard` (a `Gtk.ListBoxRow`) and the News Link tile wrap it. `LinkCard`'s
  appearance does not change.
- `src/hashtags.py`: likewise, factor what `HashtagRow` shows into a helper
  the Hashtag tile can lay out in a grid cell. Let `Sparkline` also take plain
  values (for the fallback buckets) besides a `history` list.
- `src/mastodon.py`: optional `trends_links_async`/`trends_tags_async` pairs,
  next to the existing `trends_statuses_async`, if `pages.call()` is not
  enough.

### 9.2 Conventions

- Use `pages.connect_weak` / `pages.weak_method` for every signal a page
  connects to its own methods.
- `from .i18n import gettext as _, ngettext`; `ngettext` for every count;
  `# Translators:` comments on terse strings. Add the strings to the POT and
  update `po/`.
- Score the snapshot off the main loop (a `Gio.Task` thread, or idle chunks of
  about 100 posts). Building 3 pages of tiles must keep `stall_max_ms` under
  16 ms. Read `PERFORMANCE.md` first.
- `BentoGrid` children are real widgets (no recycling), which is why the grid
  is limited to whole pages and "Show more".

### 9.3 Tests

- `src/test-catchup.py`, registered as `catchup` with the usual env, with no
  display or network needed:
  - **Parity with Elk**: port the fixture cases of Elk's
    `tests/unit/for-you-ranking.test.ts` and `for-you-diversity.test.ts`
    (`logNorm`, `lift`, `affinitySignal`, `recencyMultiplier`, `offsetScore`,
    `diversityMultiplier`, the pinned weight sums 12.75 / 367.22 / 379.97,
    `dedupeConversations`, `diversityRerank` orderings) and assert the same
    numbers.
  - Phanpy behaviour: categories, link grouping and trimming, boost dedupe,
    density, `binByTime`.
  - Trends: matching trending links to timeline sharers, the front-moving
    order for links and tags, and the fallback when trends are empty, failed
    or `hide-trending` is on.
  - Fetch stop logic against a fake client: the per-page stop check, cap,
    429, cancel.
  - Packing: no overlaps, no holes except at the end, spans clamped for
    1–6 columns, budgets respected.
  - Determinism: the same snapshot gives the same grid twice.
- Extend `src/test-timeline-memory.py` so `CatchUpPage`, its tiles and its
  rows are freed after the place is closed.
- A widget test under `xvfb-run` (like `compose widgets`) that builds the grid
  from a fixture snapshot with `G_DEBUG=fatal-criticals`.

---

## 10. Phases

1. **Phanpy port**: place, start screen, fetch loop, snapshots, categories,
   author chips, posts bar, All posts list with Phanpy's sorts.
2. **Highlights**: `catchuprank.py` with no affinity (§4.8 phase 1),
   `catchupdigest.py`, `BentoGrid`, the five tile types (news and hashtag
   tiles on the shared Explore helpers), Relevance sort in the list.
3. **Affinity and explanations**: server-bootstrapped affinity (§4.8 phase 2),
   and the "Why is this here?" popover from the score's `reasons`.

## 11. Open questions

- Should the category and author filters re-run allocation on the grid
  (current spec) or only filter the list and leave Highlights fixed?
- Is Elk's new-author boost wanted in a digest? It is exploration for an
  endless feed; in a fixed snapshot it promotes one small account to rank ~15.
  Keeping it is the faithful port; dropping it is one parameter
  (`enable_new_author_boost`).
- Elk's calibration (`CALIBRATION.md`) was measured on public and trending
  timelines. A home timeline is all in-network and has fewer bots; whether
  the bot and link priors still hold there is untested.
