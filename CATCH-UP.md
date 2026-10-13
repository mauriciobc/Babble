# Catch Up — feature specification

Status: draft, not implemented.

Sources this spec ports from:

- **Phanpy** `src/pages/catchup.jsx` at `cheeaun/phanpy@7180c00` (2026-10-13):
  the fetch loop, post categories, link grouping, the posts bar, snapshots, the
  "since last catch-up" and "until the max" ranges, and the post list. MIT, © Lim Chee Aun.
- **Elk (fork)** `app/composables/for-you/{ranking,diversity}.ts` at
  `mauriciobc/elk@9597db5`: the relevance model. MIT, © Elk contributors.
  That code is in turn a transcription of xai-org/x-algorithm `home-mixer`.
- The **original Catch Up draft**: the bento grid layout and tile allocation
  (§5 and §6), kept as written apart from the changes marked *GTK*, *Fix* or
  *Reference*.
- The **visual reference** (mockup "Destaques das últimas 24 horas", 2026-10-13):
  the results header and the tile anatomy (§2.4, §5.3). Changes it makes to
  the draft are marked *Reference*.

Decisions taken on the first draft of this spec:

1. The category, author, sort and group controls act on the **All posts list
   only**. The Highlights grid is computed once per catch-up and recomputed
   only when a new period is chosen.
2. Elk's **new-author boost is not used**.
3. Elk's **calibration values are kept as they are** (saturation points,
   federation coverage, content priors, weights, base rates).

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
   snapshot, with category and author filters, sorting and grouping. These
   controls never change the grid.

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

### 2.2 Period menu and first run

*Reference*: the period is picked from a dropdown-style button in the header
("Last 24 hours ⌄"), not from Phanpy's slider. It is a `Gtk.MenuButton`
styled like a `Gtk.DropDown`, with a popover menu:

- **Periods** (radio items): Last hour, Last 3 hours, Last 6 hours, Last 12
  hours, **Last 24 hours** (default), Since the last catch-up (only when an
  earlier snapshot exists: the range ends at its `end_at`, as Phanpy's
  "until the last catch-up" switch does), As far back as possible (paging goes
  until the server stops, Phanpy's "until the max").
- **Choosing a period runs a new catch-up** and saves a new snapshot. This is
  the only thing that recomputes the grid. Choosing the period already shown
  does nothing; there is no separate Refresh button.
- **Previous catch-ups** (a second section): the account's last three
  snapshots, each as "*start – end* · *N posts*". Choosing one shows it.
  Each can be removed from the list (`pages.confirm`).
- A footnote item, insensitive: "Your server might only show a maximum of 800
  posts in the Home timeline regardless of the time range." (Phanpy).

**First run**: when the account has no snapshot yet, the page shows an
`Adw.StatusPage` ("Catch Up"; description from Phanpy: "A separate timeline
for your followings, offering a high-level view at a glance") with the period
menu and a **Catch Up** button (`suggested-action`). Nothing is fetched until
it is pressed, because a catch-up costs up to ~30 requests.

**Later visits** open the newest snapshot directly.

### 2.3 Loading

An `Adw.Spinner` with live text, kept from the original draft:

> Scanning timeline… 160 posts evaluated (8 h back)

The post count uses `ngettext`; the hours come from the oldest `created_at`
seen so far. A **Cancel** button stops paging and shows whatever was gathered,
exactly like a 429 (§8).

### 2.4 Results layout

Top to bottom, in one `Gtk.ScrolledWindow`:

1. **Header** (*Reference*), inside the scrolled content, not the header bar:
   - Title, `title-1`: "Highlights of the last 24 hours". It follows the
     period: "Highlights of the last hour", "… of the last 6 hours", "…
     since your last catch-up", "Highlights" for as far back as possible. Each
     is its own translatable string, not a concatenation; the hour counts use
     `ngettext`.
   - Subtitle, `dimmed`: "The most relevant posts from your network, chosen
     by an algorithm that weighs engagement, reach and relevance."
   - On the right: the period menu (§2.2), then a **list options** button
     (sliders icon) whose popover holds the All posts controls (§2.5):
     category, author, sort, group, and "Reset filters". It is insensitive
     while the list is empty.
   - Under the subtitle when the snapshot did not finish (§8): a short note,
     e.g. "Covers the last 7 hours (400 posts)".
2. **Highlights**: the bento grid (§5, §6). Fixed for the snapshot.
3. **All posts**, a section heading, then:
   - **Posts bar** (Phanpy): one dot per post in time order; above 160 posts,
     320 time buckets (Phanpy's `binByTime`). Dots of posts matching the list
     filters are highlighted. A `Gtk.DrawingArea`, following `MonthlyChart`
     in `src/wrapped.py`.
   - **Author chips** (Phanpy): a horizontally scrolling row (avatar + count,
     most active first), the same filter as the popover's author item.
   - The list (§2.5).

### 2.5 All posts list (Phanpy)

- A `Gtk.ListView` over the snapshot's `StatusItem`s, wrapped in a
  `Gtk.FilterListModel` (category, author) and a `Gtk.SortListModel`.
  Changing a filter, sort or grouping only changes these models; the grid and
  the scores are untouched.
- **Categories** (§3.3) are shown with their counts, in an `Adw.ToggleGroup`
  inside the list options popover.
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
  order follows score rank, not visual position. The tile's own buttons
  (§5.3) are reachable with `Tab` inside the tile.

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
- `start_at` is `end_at − hours` for the hour periods (§2.2), the last
  snapshot's `end_at` for "Since the last catch-up", and unbounded for "As far
  back as possible".

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
  - `GET /api/v1/trends/statuses?limit=40`: the Posts tab's data. Only the
    ids and URIs are kept; they drive the **Trending** badge on post tiles
    (§5.3).

  All three go into the snapshot (§7), so reopening it shows the trends as
  they were then.
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
  → slate adjustments            (applyAdjustments: author diversity,
                                  out-of-network discount)
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
bounds the candidates, and an "as far back as possible" run can go past
48 h, so the hard cut is **off** (`CATCHUP_MAX_POST_AGE = None`). The floor
still makes old posts need roughly 3× the score of new ones after a day.

### 4.6 Slate adjustments (`applyAdjustments`)

*Deviation*: Elk's **new-author boost is not ported** (decision 2):
`enableNewAuthorBoost = False`. It is exploration for an endless feed; in a
fixed digest it would promote one small account to about rank 15 by a random
draw. Without it the whole pipeline is deterministic.

1. **Author diversity**: walking in score order, the k-th post of the same
   author is multiplied by `(1 − 0.25) · 0.5^k + 0.25`.
2. **Out-of-network discount**: × 0.75 for followed-tag posts, and for
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
| Medium Post | 2 × 1 or 1 × 2 | Posts scored in the top 10–30 %, or media posts | Avatar, text cut to 3 lines, image thumbnail or video preview |
| Compact Highlight | 1 × 1 | Lower-scored top posts, or single media items | Compact post snippet, boost indicator |
| Topic/Hashtag Tile | 1 × 1 or 2 × 1 | Trending hashtags (§3.5) | Hashtag name, post count within timeframe, mini sparkline/trend indicator |
| News Link Tile | 2 × 2 or 2 × 1 | Trending links (§3.5) | Domain favicon, article cover image, headline, publisher domain |

Tile rules:

- **Spans are clamped** to the column count (*Fix*): a 2-column tile is
  1 column wide in a 1-column grid, and so on.
- **Hero** (*Reference*): 2 × 2, as in the mockup at 4 columns. 3 × 2 only for
  the first hero of a page when the grid has 6 or more columns, so a hero
  never takes more than half the width.
- **Medium** (*Reference*): the draft's 2 × 1, plus a 1 × 2 portrait form,
  chosen by content (§6.1): 2 × 1 for a post led by one landscape image with
  short text (the mockup's "Fotografia" tile); 1 × 2 for every other medium
  post, i.e. posts with media and text, with a link card, or with long text
  (the mockup's "Ciência Hoje", "Bruno Rocha", "Arte Brasileira",
  "Tecnologia e Sociedade" and "Viagens e Lugares" tiles).
- **Compact**: text-only posts that fit in 1 × 1 (the mockup's "Linux Brasil"
  and "Design Livre" tiles). A compact post with media uses the image-led
  form of §5.3 at 1 × 1.
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

### 5.3 Tile anatomy (*Reference*)

Every post tile, from 1 × 1 to hero, is built from the same parts, top to
bottom. The visual reference is the mockup "Destaques das últimas 24 horas".

- **Card**: libadwaita's `card` style (rounded, `var(--card-bg-color)`,
  `var(--border-color)`), clipping its children (`overflow: hidden`). Light and
  dark styles come from the palette; nothing is hardcoded.
- **Boost line** (only for boosts): "*Name* boosted", `caption`, `dimmed`,
  above the header. It is the draft's "boost indicator". With several
  boosters (§3.4): "*Name* and *N* others boosted".
- **Header**: avatar (32 px; 40 px on a hero), display name (`heading`, with
  custom emoji), then `@acct · 6h` (`caption`, `dimmed`, ellipsized in the
  middle of the handle as the mockup does). At the right end, the overflow
  "⋯" button with the same menu as `StatusRow`.
- **Trending badge**: a pill reading "Trending", with a flame icon, in the
  accent colour, when the post's id or URI is in the snapshot's
  `/trends/statuses` set (§3.5). It sits in the top-right corner of image-led
  tiles (the mockup's "Em alta" on the hero) and at the end of the header line
  otherwise. Trends hidden or unavailable: no badge.
- **Text**: when the first paragraph of the plain text is at most 80
  characters and more text follows, the first paragraph is a **headline**
  (`title-2` on a hero, `heading` elsewhere) and the rest is the body (the
  mockup's "Fim de tarde no Rio de Janeiro." and "IA e o futuro do trabalho:
  cenários e caminhos"). Otherwise it is all body. Body lines are limited by
  the tile's height (`lines=` plus ellipsis). The text is rendered from
  `statustext.parse_content` segments into a `Gtk.Label` with markup, not an
  `InlineEmojiText`, so tiles stay light; custom emoji are shown as in
  `CatchUpRow`. Links in the text are not clickable in a tile; the tile opens
  the thread.
- **Media**:
  - *Image-led* form, for a hero with an image, the 2 × 1 medium form and
    compact posts with media: the first image fills the tile (`COVER`). The
    header (hero only), headline and body are drawn over it on a dark gradient
    at the bottom, in light text. On a hero the image is full bleed; on 1 × 2
    tiles it is inset with rounded corners under the header (the mockup's
    "Ciência Hoje" and "Viagens e Lugares").
  - *Inline* form, for everything else: up to 2 images side by side under
    the text (the mockup's "Bruno Rocha"), or one image above the text (the
    mockup's "Arte Brasileira").
  - Image-led tiles get the small media badge in the top-right corner (an
    image icon, or a play icon for video and GIFV); with more than one
    attachment it also shows the count.
  - Images carry the `darkenable` class, as `LinkCard` does, so
    `darken-images-on-dark-mode` applies.
- **Link card**: a post with a card and no media shows a compact card under
  the text: thumbnail on the left, title and domain on the right (the mockup's
  "Tecnologia e Sociedade"). Reuse `timeline.CardPreview`. Hidden when
  `show-preview-cards` is off.
- **Action bar**: reply, boost and favourite buttons, each with its count,
  and the bookmark button at the right end. On a hero they are pill buttons
  (`osd`) over the image. Boost and favourite icons are tinted green
  (`var(--success-color)`) and red (`var(--error-color)`) as in the mockup;
  whether *you* boosted or favourited is shown by the filled icon, not by the
  colour. Counts use `statustext.compact_count()` (§9.1), which gives "3,4 mil"
  in pt_BR and "3.4K" in English. With `show-interaction-counters` off, the
  numbers are hidden and the buttons stay.
- **Behaviour**: the buttons act exactly as `StatusRow`'s (reply opens the
  composer; boost, favourite and bookmark toggle), and the tile updates when
  the `StatusItem` emits `changed`. Activating the tile elsewhere opens the
  thread.

### 5.4 News and hashtag tiles in the mockup's style

The mockup shows post tiles only. News and hashtag tiles follow the same
visual language (same card, corner radius, header height, type scale,
badge position and action-bar height), so they line up with post tiles in a
row. They keep the content and helpers of §5.2. Each has a small **kind
label** where a post tile has its author, because there is no avatar to
identify it.

**News Link tile**

- *Kind label* (the header row): a `caption-heading` "News" with a newspaper
  icon, then `provider_name` (or the domain) · relative `published_at` when
  the card has one, `dimmed`. Top-right: the Trending pill when the link came
  from `/trends/links` (§3.5), as on the hero. In the fallback, no pill.
- *2 × 2, with an image* (image-led, like the hero): the cover image fills
  the top ~55 % full bleed, `COVER`, `darkenable`. Below it: the title as
  `title-3`, up to 3 lines; the description, `body`, up to 2 lines.
- *2 × 1, no image*: title as `heading`, up to 2 lines; description, 1 line.
- *Footer* (where a post tile has its action bar, same height):
  - When people in your timeline shared it: up to five 24 px sharer avatars,
    overlapping, then "Shared by *Name* and *N* others".
  - Otherwise: `people_talking` as "*N* people talking", and "By *author*"
    when the card has `author_name`.
  - At the right end, where post tiles have the bookmark: an "Open in
    browser" icon button, so the link can be opened directly even where the
    tile itself opens the link timeline.
- Activating the tile: `explore.open_link_timeline`, as the News tab.

**Topic/Hashtag tile**

- *Kind label*: `hash_badge()` and "Hashtag" (`caption-heading`). Top-right:
  the Trending pill when the tag came from `/trends/tags`; a "Following"
  pill instead when you follow it.
- *Name*: `#name` as `title-3` (1 × 1) or `title-2` (2 × 1), ellipsized.
- *Counts*: the `stats_text(history)` line, `caption`, `dimmed` ("*N* people
  in the past 2 days"), and, when the tag is used in your timeline, "*N*
  posts in your timeline" (`ngettext`) in the accent colour. This is the
  draft's "post count within timeframe".
- *Sparkline*: `hashtags.Sparkline` across the tile's full width at the
  bottom, in the accent colour, taller than in `HashtagRow` (the tile's
  remaining height). 1 × 1 tiles without enough history show the counts
  only.
- *2 × 1 extra*: up to three 24 px avatars of accounts in your timeline
  that used the tag, on the right of the counts.
- Activating the tile: `push_hashtag`, as the Hashtags tab.

**In a page**: with the budget of §6.2, a 4-column page is typically one
hero (2 × 2), one or two news tiles (2 × 2 or 2 × 1), one hashtag tile
(1 × 1 or 2 × 1), and post tiles filling the rest.

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
4. **Size within the tier** (*Reference*), from the content:
   - Hero → 2 × 2 (3 × 2 per §5.2 on very wide grids).
   - Medium → 2 × 1 when the post is led by one landscape image (aspect
     ratio ≥ 1.3 from `meta.original`) and its text is at most 140
     characters; otherwise 1 × 2.
   - Compact → 1 × 1; text-only posts whose plain text exceeds ~180
     characters are promoted to 1 × 2 when the page budget has room, since
     1 × 1 shows only about three lines.

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
  because `now = end_at` and nothing in the pipeline is random.
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
- `src/timeline.py`: move `StatusRow`'s action buttons and overflow menu
  (`_action_button`, `_on_reply`/`_on_boost`/`_on_favourite`/`_on_bookmark`,
  `_overflow_button`, `_build_menu`) into a component both `StatusRow` and the
  post tiles use, bound to a `StatusItem`. It keeps building the menu lazily
  and connecting with `connect_weak`. `StatusRow`'s behaviour does not change;
  run `timeline memory` and `timeline render` after the move.
- `src/statustext.py`: `compact_count(n)`, localized: below 1,000 the number
  with the locale's grouping; above, one decimal with the locale's decimal
  separator (dropped when it is 0) and a translatable suffix (`_('%sK')`,
  `_('%sM')`, `_('%sB')`, with `# Translators:` comments, so pt_BR can say "3,4 mil" and
  "1,2 mi"). It needs no display, like the rest of the module.
  `wrapped.shorten`, `search.compact_number` and `profile.format_count` stay
  as they are; moving them onto it is a separate change.
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
  - Tiles: size within the tier (§6.1 step 4), the headline split (§5.3),
    `compact_count` for en and pt_BR (`test-statustext.py`).
  - Trends: matching trending links to timeline sharers, the Trending badge
    set from `/trends/statuses`, the front-moving
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

1. **Phanpy port**: place, period menu and first-run page, fetch loop, snapshots, categories,
   author chips, posts bar, All posts list with Phanpy's sorts.
2. **Highlights**: `catchuprank.py` with no affinity (§4.8 phase 1),
   `catchupdigest.py`, `BentoGrid`, the five tile types (news and hashtag
   tiles on the shared Explore helpers), Relevance sort in the list.
3. **Affinity and explanations**: server-bootstrapped affinity (§4.8 phase 2),
   and the "Why is this here?" popover from the score's `reasons`.

## 11. Decided

- **Filters act on the list only** (§2.4, §2.5). The grid is recomputed only
  when a new period is chosen (§2.2).
- **No new-author boost** (§4.6).
- **Elk's calibration values are kept.** They were measured on public and
  trending timelines (`CALIBRATION.md` in the fork), and are used unchanged
  for the home timeline. The parity tests (§9.3) pin them.
