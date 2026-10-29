# A ranked timeline for Babble

Status: proposal, second revision. Nothing here is built. The first revision
was reviewed and sent back; §0 lists what changed and why.

**Catch Up is not changed by this plan.** No Catch Up module, test or spec
is edited at any stage. The new code calls the ranker through the parameters
it already exposes, and has its own fetch, store and threading code. A test
enforces the boundary (§5.0).

Markers used below:

- **[R]**: read from this tree, or from upstream code where named.
- **[M]**: measured; the method is given where the number appears.
- **[E]**: an estimate, to be measured before anything depends on it.

---

## 0. Changes from the first revision

| Review point | Resolution |
|---|---|
| No definition of success | §1 states the hypothesis. §3 defines an offline evaluation, and §4 has gates with numbers fixed *before* any data is collected. A result that is inconclusive counts as a fail. |
| The heuristic ranker may just be a popularity feed | Measured, not argued: the evaluation compares `rank()` against an engagement-only ordering and a simple affinity ordering, and checks author diversity and small-account share (§3.3). If the simple ordering does as well, it ships instead (gate A4). |
| Doc contradicted the code | Every claim about the tree is now marked [R] and was checked. Two of the review's own claims were wrong, and are corrected here: a refresh does **not** replace the list (`TimelineModel.insert_new` prepends unseen posts in the order given, `timelinedata.py:394`), and a source with no `path` already gets the home filter context (`filter_context_for`, `timelinepage.py:56-76`). |
| Served set threw posts away | The served, seen and cursor state is gone. Every request ranks *(pool − posts already in the list)*. Nothing is excluded that the reader has not been shown, and the list never reorders (§5.2). |
| Feedback loop, no exploration | Team-draft interleaving with chronological order during the trial (§5.3), then fixed chronological slots after it. Both are deterministic. They also provide the online measurement (gate B). |
| Learned heads trained on position-biased negatives | Removed (§7). |
| Embeddings need numpy, contradicting "no bundle cost" | Removed (§7). |
| Local action log reversed a decision in passing | Removed. `CATCH-UP.md` §4.8 stands: affinity comes from the server only. "Not interested" is an explicit command, stored as a short list the reader can clear (§5.4). |
| Guessed numbers; the measurable one not measured | `rank()` was timed [M]. Requests are counted from the code, not estimated (§6). |
| Scope | Five stages, each ending in a gate or a release. No Preferences UI, new icon, explanations or menu items before gate B. The feed server is cut (§7). |
| Duplicates Catch Up's Relevance sort | §1.2 says what the place adds over it, and why a Relevance toggle on Home was rejected. |
| Shared code could change Catch Up's behaviour | Catch Up's modules are not edited. The new code has its own fetch loop, store and thread helper rather than sharing Catch Up's, and imports only the ranker. The rules are in §5.0. |
| "Two weeks of replay" (from the review) | Not possible: Mastodon keeps at most 800 entries in a home feed (`FeedManager::MAX_ITEMS = 800`, `mastodon/mastodon` `main`). Data has to be collected going forward (§3.1). |

---

## 1. Hypothesis

### 1.1 What we are testing

> For a reader whose home timeline brings more posts than they read in a
> sitting, ordering the last day of home posts with engagement affinity puts
> the posts they go on to engage with nearer the top than chronological
> order does, without narrowing whose posts they see.

"Engage" means favourite, boost, bookmark or reply. These four are visible
through the API, so nothing has to be recorded on the reader's machine to
measure them.

Non-goals:
- replacing Home, which stays chronological and stays the landing place;
- finding posts from accounts the reader does not follow;
- maximizing time spent;
- learned models and embeddings.

### 1.2 Why a new place, and not something we have

**Catch Up's Relevance sort** already ranks a period of home posts with the
same `rank()` (`src/catchup/pipeline.py`). But it is a different thing:
- It is a skim list. `CatchUpRow` is "light on purpose": one line of text,
  thumbnails and counts, with no actions (`src/catchup/rows.py`).
- It covers a fixed snapshot of a chosen period, and is recomputed only when
  a new period is chosen.

The hypothesis is about a *reading* surface: full `StatusRow` cards with
their actions, which keep up as posts arrive. Catch Up's grid remains useful
for judging the ranker by eye during stage 0, at no cost.

**A Relevance toggle on Home** was considered and rejected. Home's machinery
assumes id order in three places [R]:
- paging: `append_old` moves the cursor to the smallest id of a page
  (`timelinedata.py:408-427`);
- trimming: `_trim_old` only runs for id-paged timelines
  (`timelinepage.py:568-581`);
- resume: the reading position re-opens Home with `max_id`
  (`readingposition.py`).

Re-sorting Home breaks all three. A separate page with its own source
breaks none.

If gate A fails, the place is not built, and Catch Up is exactly as it was.

---

## 2. What exists

All [R].

- **The ranker is a library.** Its implementation is the Catch Up package:
  - `src/catchup/rank.py` exposes `rank(candidates, ctx, *, max_per_conversation, diversity)`, which scores candidates, applies slate adjustments, dedupes conversations and runs the DPP reorder.
  - `src/catchup/rank_model.py` owns features, action probabilities, `RankingContext`, affinity resolution and candidate wrapping.
  - `src/catchup/rank_text.py` owns JavaScript text parity, Unicode tokenization and embeddings.
  These modules have no GTK imports; the rank modules use the standard
  library plus sibling rank modules only.
- **Shared filtering**: `timelinebase.status_filter(data, context, now)`
  (`timelinebase.py:186`) gives the strongest filter hit through
  `filterrules`, as Home's rows use it. Catch Up's `is_filtered_post` is not
  usable here: it reads `_filtered`, a marker only Catch Up's fetch sets.
- **Catch Up's own plumbing**, which the new code does **not** use:
  - `CatchUpRun`, whose caps are fixed at 1,000 posts / 25 pages;
  - `src/catchup/store.py`, whose root is module-level and ends in
    `catch-up`;
  - `catchup.run_in_thread`.
- **How `TimelinePage` takes a source's pages**:
  - **Refresh**: `insert_new` prepends the posts in the order given and
    skips any id already in the list (`timelinedata.py:394-406`). If the
    reader is scrolled down, the new-posts pill appears
    (`timelinepage.py:555-556`); clicking it scrolls to the top
    (`timelinepage.py:394-398`).
  - **Older pages**: `append_old` appends in the order given
    (`timelinedata.py:408`).
  - **Trimming**: link-paged sources are never trimmed
    (`timelinepage.py:568-581`).
  - **Page size**: `PAGE_SIZE` is 20 (`timelinebase.py:42`).
- **How a sidebar place is wired**:
  - A built place goes into `_place_children` (`window.py:1268`). From
    there it is part of `_timeline_pages()` (`window.py:869-873`), so stream
    deletes and edits reach it with no new code.
  - Stream `update` events go to Home only (`window.py:877-885`).
  - Auto-refresh is applied to Home and the public pages only
    (`window.py:2124-2128`).
  - Ctrl+R and F5 (`win.reload`) refresh the visible page through its
    `refresh()` method (`main.py:164`, `window.py:1773-1797`).
- **The home feed's server limit**: Mastodon keeps at most 800 entries per
  home feed (`FeedManager::MAX_ITEMS`). Home in Babble keeps 400 rows
  (`TIMELINE_MAX_ITEMS`, `timelinebase.py:85`).

---

## 3. Evaluation before any UI

### 3.1 The harness

**`bench/rank_eval.py`**: a script, not installed, that imports `src.*` the
way `bench/timeline_bench.py` does. Of Catch Up's modules it imports only
`src.catchup.rank`; affinity comes from the new `src/affinity.py` (§4). It has
two commands.

**`collect`** runs hourly, from a systemd user timer or by hand. Each run:

- pages the whole home feed: at most 800 entries, 20 requests;
- fetches the first page of the account's own statuses (for boosts and
  replies, which carry exact times);
- fetches the first page of favourites;
- fetches relationships for authors not seen before;
- appends a snapshot of every post's counters and of the reader's
  `favourited`, `reblogged` and `bookmarked` flags. These are part of the
  Status entity for the authenticated user, so engagement needs no
  in-app recording.

At about 25 requests an hour, this is far below Mastodon's 300 per 5
minutes.

The data stays in a `0700` directory with `0600` files. The token is read
from the keyring entry Babble already stores, so it is not copied anywhere.

**`evaluate`** runs once a day, at a fixed hour *T*:

- **Candidates**: posts created in [T − 24 h, T) that are not engaged
  before *T*. Their counters come from the last snapshot at or before *T*,
  so nothing the ranker reads comes from the future.
- **Labels**: posts first seen engaged in (T, T + 24 h].
  - Favourite and bookmark times are the first hourly snapshot that shows
    the flag, so they are accurate to within an hour.
  - Boost and reply times come from the reader's own statuses.
- **Affinity**: derived only from engagements before *T*, either observed
  by the harness or older than collection itself.
- **Mutuals**: from the collected relationships.

### 3.2 What is compared

Each day, five orderings of the same candidates:

| Ordering | Why it is there |
|---|---|
| Chronological, newest first | What Home shows; the baseline |
| `rank()`, no affinity | What Catch Up does today |
| `rank()`, with affinity | The hypothesis |
| Engagement only (`favourites_count + reblogs_count`, descending) | The popularity straw man: if `rank()` tracks this, it is a popularity feed |
| Affinity, then chronological (author affinity descending, ties by time) | The simplest ranker that could work |

### 3.3 Metrics, per day

- **recall@20** and **recall@50**: the share of the day's labelled posts
  that appear in the top 20 or top 50.
- **Distinct authors** in the top 20.
- **Small-account share**: the share of the top 20 from authors whose
  `followers_count` is below the day's median.
- **Popularity overlap**: how many of the top 20 are also in the
  engagement-only top 20.

### 3.4 Known bias

The reader produced these engagements while reading chronologically.
Posts at the top of chronological order when they opened the app were more
likely to be seen, so the offline test favours the baseline. That makes
gate A conservative: a ranker that passes it is likely better, but one that
fails it might not be worse. Gate B (§4) measures online, without this bias.

### 3.5 Accounts and length

- **Accounts**: three, with different follow-list sizes (under 200, 200 to
  1,000, over 1,000 follows): the maintainer's and two volunteers'. Each
  volunteer runs the harness on their own machine and shares only the
  metrics summary, never posts.
- **Length**: 14 days of collection, so 14 evaluation days per account.

---

## 4. Stages and gates

The gate numbers are proposals. They are fixed before collection starts
(§8), and not adjusted after the results are seen.

**Stage 0: the affinity module (new code).**
- Port Elk's `deriveAffinities` into a new `src/affinity.py`. It is pure,
  imports no GTK or GLib, and returns a `ForYouSignals`.
- Strengths, mutual multiplier and half-life follow the bootstrap
  `CATCH-UP.md` §4.8 phase 2 describes: follow 5, reply 4, bookmark 3,
  reblog 2, favourite 1; ×3 for mutuals; 14-day half-life. The spec is read
  for the numbers; nothing in Catch Up changes.
- A new `affinity` test covers it.

Whether Catch Up later uses `affinity.py` for its own phase 3 is Catch Up's
decision, under `CATCH-UP.md`. This plan does not make it. Gate A3's result
is useful data for it.

**Stage 1: harness and gate A.** Build `bench/rank_eval.py` (§3), collect
for 14 days, evaluate. Gate A passes when, on at least 2 of 3 accounts:

- **A1, quality.** `rank()` with affinity beats chronological on recall@20
  on at least 11 of 14 non-tied days (one-sided sign test, p ≈ 0.03), and
  its mean recall@20 is at least 1.25× chronological's.
- **A2, breadth.** Mean distinct authors in the top 20 is at least
  chronological's minus 2, and small-account share is at least 0.8×
  chronological's.
- **A3, affinity** (does not block): `rank()` with affinity vs. without. If
  affinity adds nothing, stage 2 runs without it.
- **A4, simplicity.** If "affinity, then chronological" is not beaten by
  `rank()` under A1's test, stage 2 uses the simple ordering instead of
  `rank()`.

Fewer than 11 non-tied days, for example because the reader rarely engages
late, is **inconclusive, and counts as a fail**.

**Stage 2: a hidden place, and gate B.** Build the place described in §5:

- it is hidden by default (`hide-<place>` defaults to true, and there is no
  UI to show it);
- it uses an existing symbolic icon;
- it has no Preferences, no "Not interested" and no explanations;
- its pages are team-draft interleaved (§5.3).

The three accounts use it for 14 days. Gate B passes when, on at least 2 of
3 accounts, the ranked team wins at least 11 of 14 non-tied days on
credited engagements (§5.3). Inconclusive counts as a fail.

**Stage 3: release.**
- Replace interleaving with fixed chronological slots.
- Add "Not interested" and the "Why is this here?" text (built once, for
  this place and Catch Up).
- Draw the icon and choose the name.
- Decide, with stage 2's numbers in hand, whether the place is shown by
  default.

**Kill rule.** If a gate fails, the stage's result is written up in this
document, the work stops there, and the new modules are removed. Catch Up
was never edited, so there is nothing to roll back in it.

---

## 5. The place (stage 2, with stage 3 additions marked)

### 5.0 Boundary with Catch Up

**Current boundary after the Catch Up package cutover:**

- `src/affinity.py`, `bench/rank_eval.py` and ranked-place modules may import
  `src.catchup.rank`, `rank_model` and `rank_text`.
- Catch Up may not import the ranked-place modules.
- The three rank modules may import the standard library and their sibling
  rank modules only. `src/test-module-boundary.py` enforces these rules.

The older implementation plan below predates the package cutover; its
file-level exclusion list and `git diff` gate are historical, not current
acceptance criteria.

**The new code:**

| Module | Holds | GTK |
|---|---|---|
| `src/affinity.py` | The port of Elk's `deriveAffinities`: server data in, `ForYouSignals` out (stage 0) | No; no GLib either |
| `src/rankedfeed.py` | The source, the pool, the home fetch loop, the relationships and affinity fetches, the interleaver, the counters and a small thread helper | No widgets; GLib/Gio for requests and the worker |
| `src/rankedstore.py` | Persistence of the pool, the list, the affinity cache, the counters and "Not interested", in one SQLite database (stdlib `sqlite3`) | No; GLib for the cache directory |
| `bench/rank_eval.py` | The harness (§3) | No |

Module names follow the working name (§8), and change with it.

**Allowed imports:**

| From | May import | Must not import |
|---|---|---|
| New modules and the harness | `src/catchup/rank.py`, through its existing public interface (§2); the shared modules every page uses (`pages`, `timelinepage`, `timelinebase`, `filterrules`, `mastodon`, `accounts`, `i18n`) | `src/catchup/page.py`, `digest.py`, `digest_layout.py`, `store.py`, `tiles.py`, `tiles_render.py`, `geometry.py` |
| Catch Up's modules | (unchanged) | Any new module |

**The ranker is used as it is.** The place configures `rank()` only through
what `RankingContext` and `rank()` already take: affinity, weights, params,
base rates, `max_per_conversation` and `diversity`. If the place ever needs
something the ranker does not expose, that is a separate change to
`src/catchup/rank*.py`, made and reviewed as a Catch Up change under
`CATCH-UP.md` and its tests. It is not part of this plan.

**Some duplication is accepted.** The home fetch loop and the 15-line
thread helper exist on both sides. Sharing them would mean giving
`CatchUpRun` a cap parameter, or making the place depend on Catch Up's
behaviour. The store is not a copy: it is SQLite (see **Store** in §5.1),
where Catch Up writes JSON snapshots. Everything else the new code needs
comes from the standard library or Babble's shared modules. The
implementation plan's §3.1 lists what to use, and which packages were
evaluated and rejected.

**At runtime the two share nothing but the client.**
- **Separate data**: `…/babble/ranked/<account key>`, not `…/catch-up/`.
- **Separate lifetime**: the place has its own `Gio.Cancellable`, and never
  reads Catch Up's state.
- **The rate limit is shared through the client.** Both check
  `client.rate_limited()`. The worst case is about 20 requests for the
  place's first fill plus about 40 for a catch-up running at the same time
  (25 home pages at its cap, relationships, followed tags, 3 trends),
  against Mastodon's 300 per 5 minutes. So no coordination is added;
  coordinating would mean reading Catch Up's state.

**Enforcement.** A `module boundary` test reads every `src/**/*.py` and
`bench/rank_eval.py` with `ast`, as `ui strings` does. It fails if:
- a new module imports a Catch Up module other than `rank`, `rank_model` or
  `rank_text`;
- a Catch Up module imports a new module;
- a rank module imports anything outside the standard library and sibling
  rank modules.

Catch Up's tests (`catchup digest`, `catchup rank`, `catchup page`,
`catchup grid`) must pass, with their golden fixtures unchanged, at every stage.

### 5.1 Pool

**Window.** The pool covers the last 24 h of home posts, capped at
**400**. 400 is Home's own row limit (`TIMELINE_MAX_ITEMS`), so the place
adds no new memory budget: link-paged lists are never trimmed, so the pool
cap is the list's only bound.

**Posts are not shared with Home.** Home trims to 400 rows and holds only
what the reader has scrolled through, so it cannot serve as the pool.

**Fetching** happens in `rankedfeed.py`'s own loop, not in `CatchUpRun`:
- **First fill**: page `/timelines/home?limit=40` with `max_id`. Stop when a
  page's oldest post is older than 24 h, when 400 posts are kept, when a
  page is empty, or when `client.rate_limited()` is set. There is no pause
  between pages, as there is none in Home's paging.
- **Refresh**: page with `min_id` from the newest pooled post until a page
  comes back empty. `min_id` is contiguous where `since_id` is not; Home
  relies on this for its own refresh (`timelinepage.py:203-209`).
- **Relationships**: `/accounts/relationships` over
  `itertools.batched(ids, 40)`, for the authors and boosters not looked up
  before. They give `following` and `followed_by`.
- **In-network flag**: a non-boost post whose author is not followed came
  through a followed hashtag, and is passed to `candidate_from_status`
  with `in_network=False`. Everything else is in network.
- **Mutuals**: authors with `followed_by` become `mutual_author_ids`.

**Filtering**, before ranking:
- `timelinebase.status_filter(status, filterrules.HOME, now)`. A `hide` hit
  is dropped. `warn` and `blur` posts stay, and their rows show them as
  Home's rows do.
- Boosts of the same post: only the newest is kept.
- Home's `hide-boosts-home` and `hide-replies-non-follows` settings,
  passed in as flags, through Home's own helpers `timelinedata.filter_boosts`
  and `timelinedata.filter_replies_from_non_follows` (`timelinedata.py:253`,
  `:260`).

Filtered posts never reach `rank()`. No change to `filter_context_for` is
needed: a source with no `path` falls back to the home context.

**Store.** `rankedstore.py` keeps the pool and the ids shown in one SQLite
database (stdlib `sqlite3`; SQLite 3.53 in the GNOME 51 runtime), in its own
directory:
- **Atomicity** comes from transactions. Showing a page is one `INSERT` of
  its ids, not a rewrite of a 400-post JSON file on the worker while it
  holds the GIL.
- **Privacy** follows `src/catchup/store.py`: a `0700` directory and a `0600` file,
  created before SQLite opens it, so its journal inherits the mode.
- **Deletion** goes with the account through the call in
  `accounts.forget_account` next to `src.catchup.store.delete_account`.

Opening the place shows the stored list at once, then refreshes in the
background if it is more than 15 minutes old.

### 5.2 Requests: rank what is left

The source implements `fetch(params, callback)` and `finish(result)` like
`ApiTimelineSource`, with `paging='link'`. It ignores the ids
`TimelinePage` sends: every request is answered the same way, on a worker
thread:

```
remaining = pool − ids already in the list
page      = top PAGE_SIZE of rank(remaining, ctx)
```

What follows from this:

- **A refresh** (Ctrl+R, F5, or re-entry after 15 minutes) first fetches
  new posts (§5.1), then answers as above. `insert_new` prepends
  the page in rank order. The rows already shown stay where they are, and
  the pill offers the way up.
- **An older page** appends the next best of what is left.
- **The end** comes when nothing is left. The empty batch marks the list
  exhausted, and the end of the list says the reader has seen the last 24
  hours. The feed ends rather than falling back to chronological, because
  Home already is chronological.
- **No served, seen or cursor state.** The list *is* the served set, and it
  is persisted with the pool.
- **Diversity is per request**, as in X: each request ranks only what is
  left, with X's served filter applied before the slate adjustments.
- **No stream inserts and no auto-refresh.** Neither is wired for this
  place today, and none is added. Stream deletes and edits still arrive,
  through `_timeline_pages()`.

`append_old` reads a page that held nothing new and whose smallest id did
not move back as the end (`timelinedata.py:408-427`). Ranked pages have no
id order, so the source must never return a page made only of posts
already in the list. Since `remaining` excludes them and boosts are deduped
before ranking, this holds; the `ranked source` test pins it (§5.6).

### 5.3 Exploration and measurement

**Stage 2: team-draft interleaving.** Each page of 20 is built in pairs:

1. A deterministic coin flips:
   `hash(account_key, request_number, pair_index)`.
2. The winner picks first. One team picks the highest-ranked remaining post,
   the other the newest remaining post, each skipping posts already picked.
3. Each picked post records its team.

An engagement on a post in the list credits that post's team. The credits
are stored as one counter per day and team. No post ids are kept after a
post leaves the list.

This gives the reader half chronological exposure (exploration), and it is
the standard online comparison of two rankers (Radlinski, Kurup and
Joachims, 2008).

**Stage 3: fixed chronological slots.** Positions 5, 10, 15 and 20 of each
page take the newest remaining post by an author not already on the page.
This keeps one in five slots for exploration, and keeps small accounts and
fresh posts in view. The slots stay a team, so the daily counters remain a
running check that ranking still earns its place.

Nothing in either scheme is random, so tests can pin every position.

### 5.4 Affinity

**The bootstrap.** `rankedfeed.py` fetches it with the first fill: two
pages of favourites, one of bookmarks, two of the account's own statuses.
`affinity.py` turns it into a `ForYouSignals`, which is cached in
`rankedstore`. Catch Up's snapshots keep their `'affinity': null`.

**Incremental update.** Each refresh fetches the first page of favourites
and of the account's own statuses: two requests. A favourite, boost, reply
or bookmark therefore shapes the next refresh.

**Nothing behavioural is recorded.** Opens, link clicks and dwell are not
stored, and `CATCH-UP.md` §4.8 is unchanged.

**"Not interested" (stage 3).** It is an explicit command in the overflow
menu, on this place's rows only. It is stored in `rankedstore`, and it acts
as Elk's does:
- the post id goes into `ForYouSignals.not_interested`, the post penalty;
- the dismissal lowers the author's affinity at Elk's
  `NOT_INTERESTED_STRENGTH`;
- it feeds a saturating author penalty, a port of Elk's `authorPenaltyIn`:
  `min(1, 1 − 2^−Σ decay)`, which is 0.5 after one fresh dismissal, 0.875
  after three, fading with the 14-day half-life. It is passed in as the
  resolver's `author_penalty`, a field `AffinityResolver` already has
  (`src/catchup/rank_model.py`);
- and it retracts the dismissed post's own positive history, as Elk's
  `applyNotInterestedToSignals` does (`signals.ts:842-847`): the post's id
  is removed from every engaged kind before the affinities are re-derived,
  so dismissing a post the reader had favourited does not leave that
  positive signal standing. The retraction is of the *post*, not of the
  author, and undo restores it.

Nobody is muted automatically: Elk has no such rule, and a hard
`muted_for_you` entry would be far harsher than its penalty. The list is
short, is shown in Preferences, and can be cleared there. Catch Up does not
read it.

### 5.5 Changes to existing files

| File | Change |
|---|---|
| `window.py` | Five changes: a `PLACES` entry, a `HIDE_KEYS` entry, a title in the title map, a `_build_place` branch, and a line cancelling the place's in-flight fetch on account switch, added next to Catch Up's at `window.py:610` (which is left as it is). About 20 lines [E]. |
| `accounts.py` | One call deleting the place's store in `forget_account`, next to Catch Up's (`accounts.py:157`). |
| gschema | `hide-<place>`, default true in stage 2. |
| `src/meson.build` | The three new modules in `install_data`, and the new tests. |

No Catch Up file is in this table, by design (§5.0). The three new modules
(§5.0) each stay under 800 lines.

### 5.6 Tests

**`ranked source`** (new; no display, no network), run against a fixture
pool. It checks that:
- pages never repeat a post;
- a refresh page arrives in rank order;
- filtered posts never reach `rank()`;
- an empty remainder returns an empty batch;
- no page consists only of posts already listed;
- interleaving and slot positions are deterministic;
- credits go to the right team.

**`timeline memory`**: the place is added to the pages it checks are freed.

**`affinity`** (new, stage 0; no display): `deriveAffinities` against Elk's
strengths, mutual multiplier, evidence caps and half-life.

**`module boundary`** (new): the import rules of §5.0.

**Catch Up's tests**: not edited, and they must pass unchanged.

The harness gets a `--self-test` that runs the evaluation on a synthetic
pool with planted labels, and checks that recall@k is computed correctly.

---

## 6. Cost

### 6.1 Ranking [M]

`rank()` was timed on synthetic home posts: random text of 8 to 60 words,
one tag, one link, 150 authors, 48 h of ages, and no media. It ran on one
thread with the default context, on a 16-core desktop (the development
machine). The timing script **is** in the tree: `rank_eval.py bench`,
added by stage 1, so the figure can be reproduced. Three runs each:

| Pool | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| 400 posts | 191 ms (first call, includes warm-up) | 71 ms | 72 ms |
| 800 posts | 97 ms | 97 ms | 96 ms |

Reproduced on 2026-10-21 with `python3 -I bench/rank_eval.py bench`:
400 posts 206 / 86 / 86 ms, 800 posts 113 / 110 / 110 ms. The same
shape — a warm-up first call, then a flat cost, and 800 posts costing
barely more than 400 — on a different machine, so the figure is the
algorithm's and not one machine's. The command prints **every run** and
no median, because the median would hide the warm-up the table names.

Real posts with media, cards and long threads may cost more; the stage 2
build measures this on real pools.

**The main thread.** The worker holds the GIL while it ranks, and CPython
hands the GIL over every 5 ms (`sys.getswitchinterval()` is 0.005 [R]). So
the main loop should see delays in 5 ms slices, not a 70 ms block. That is
reasoning, not measurement: stage 2 confirms it with `autoresearch.sh`'s
`stall_max_ms` and `stalls_over_16ms` while a refresh ranks.

**Repeated ranking.** Filling the first screen makes `_fill_loop` send a
few requests in a row, each ranking about 70 ms. If that shows in first
load, measured end to end under headless mutter as `CLAUDE.md` describes,
the order is cached per pool version.

### 6.2 Requests [R]

| Event | Requests |
|---|---|
| First fill | Up to 10 home pages (400 / 40), plus one relationships call per 40 new authors and reply targets (about 4–5 for 150 to 200 accounts), plus the 5-request bootstrap: about 20. The requests are sequential with no pause, so a few seconds [E]. |
| Refresh | 1–2 home pages, relationships for new authors, 2 affinity pages: about 4–5. |
| Harness `collect` | About 25 an hour. |

The first fill happens once per account: later opens show the stored list
first.

### 6.3 Disk [E]

A 400-post pool holds the same raw Status JSON as a 400-post Catch Up
snapshot, plus SQLite's page overhead, so it should be a little larger.
Stage 2 records the real figure.

---

## 7. Not proposed, and why

- **Embeddings (BYOTA-style).** There is no evidence yet that topic
  blindness is the ranker's failure. The k-means and similarity maths also
  need numpy, which is not in the GNOME runtime, so the bundle grows even
  with an external model server. Revisit only if stage 2's misses are
  visibly on-topic posts ranked low.
- **Learned heads.** Engagement under a ranked feed is biased by position.
  A model trained on "shown but ignored" posts learns its own ranking back.
  Training would need data from the exploration slots alone, which means
  months of stage 3.
- **A feed server or relay.** It is a different project, with token
  custody, operations, and fediverse consent questions (`indexable`,
  `discoverable`, `#nobot`). It is also the only way to get posts from
  outside the network, which is a non-goal here.
- **A local behaviour log.** It is not needed. Every engagement the
  hypothesis counts is visible through the API.

### Where the ideas came from

- **X's For You** (`xai-org/x-algorithm`): ranking only what is left
  (`PreviouslyServedPostsFilter`); deterministic holdouts per post and viewer
  (`InventoryHoldoutFilter`), the model for the deterministic interleave and
  slots; and keeping filtering separate from ranking.
- **BYOTA** (`mozilla-ai/byota`): nothing in this revision. Its embedding
  approach is deferred, as above.

---

## 8. Decisions needed before stage 1

1. **The gates**: confirm or change A1–A4 and B, then freeze them.
2. **The volunteers**: two accounts with different follow-list sizes, run on
   their own machines.
3. **The name.** "For You" is X's product name. The tag and the title are
   open; Catch Up's grid already uses "Highlights".
4. **Default visibility in stage 3**: deferred until gate B, and decided
   with its numbers.
