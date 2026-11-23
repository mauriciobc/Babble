# ADR — Feed Chef v1 (Babble)

Status: accepted for implementation. Behaviour source of truth is mashdon
`master` @ `23277cd1ca40f6fe618c4f246896fd80777bf4ea`, not the PRD where they
diverge. The PRD describes newer behaviour that is **not** on that master
SHA; Babble v1 does not implement PRD-only rows. Adversarial review:
Project store `internal/feed-chef-plan-adversarial-review-deep.md`.

## Product one-liner

**Catch Up** is a period digest of the home timeline (skim grid/list).
**Feed Chef** is a continuous recipe-driven timeline with a chosen scope and
client-side modifiers. They coexist; neither replaces the other. Ranked-home
(`Docs/timeline-algorithm.md`) and mashdon `AlgorithmicTimeline` stay out of
v1.

## Contract table (Java × PRD × Babble v1)

| Capability | Java `master` | PRD | Babble v1 |
|---|---|---|---|
| `pantry.time_window_hours` | absent | pantry 1–12 h | **absent** |
| `RequestMode` / `onTimelineCacheUpdated` | absent | yes | **absent** |
| `expected_outcome` | absent | yes | **absent** |
| Pantry fetch shape | 1 scope + ≤3 tags if `maxId is None` + MLT search if focus; timeout `30+10*n`; partial | + window | **= Java** (parallel, enforced timeout); Babble `has_more` follows **scope page fullness** (not “single_task only”) so scope-oldest pagination works with tags/MLT |
| UI cursor | last id after kitchen sort | oldest of merged pantry | **smallest wrapper id of the scope response** (deliberate fix) |
| Base score | fav·1 + reblog·2.5 + reply·1.5 on content | same | **= Java** (not Elk `rank()`) |
| `freshness` | `score *= factor` | blend 0.4 | **= Java** |
| `heat` / `diversity` | heat after scores, before structural; diversity pre-sort, author only | PRD order / instance | **Java order; heat hides exactly floor(P x n / 100), the lowest first** (see Promises) |
| `similarity_cluster` / TF-IDF / synonyms | present | present | **inert + UI warning** (no `synonyms.json` 16 MB LGPL in the binary) |
| Sentiment | `SimpleSentimentAnalyzer` + `sentiment_words.json` (~94 KB); often `0` if missing | present | **Vendored VADER (EN) + LeIA (pt*)** — MIT, no PyPI; activates `positive_only` / `sentiment_boost` (Babble upgrade, not Java JSON parity) |
| Server trends | deadlocked same executor | own executor / 2 s | **separate thread + 5 min memory cache**; never nest `submit`+`get` on that pool |
| Network reach | degree 1 only; `null` disables; author = **boosted** content account; everyone passes while the list loads or is empty | BFS / null=∞ | **degree 1; null=off; boosted author** (= Java), but **fails closed** (deviation 12) |
| More Like This | same timeline pantry when focus is set; UI can set focus | dedicated fragment | **pantry MLT = Java**; focus via import **or** timeline “More like this” (no dedicated fragment) |
| Builder controls | full (icon, lists, diet/kitchen sliders, modifiers) + preview sheet | full | Java form fields in a HIG `Adw.Dialog` (`src/feedchefbuilder.py`), without `include_unknown`, similarity and the diversity dimension (deviation 7), plus an icon table, `positive_only`, `sentiment_boost` and a **preview** (`FeedChefPreviewDialog`, own engine) |
| Import confirm | always confirm (name/author); then upsert replaces silently | varies | **= Java** (always confirm; replace without a second prompt) |
| Engine lifetime | `new` per view | factory singleton | **= Java** (no singleton) |

## Explicit Babble deviations

1. Pagination cursor = oldest status id from the **scope** fetch, not the
   last kitchen-sorted row and not the merged-pantry oldest id.
2. Server trend fetch runs on a dedicated worker; the trends worker calls
   `fetch_trends` directly (no nested `submit`+`get` on the same pool).
3. `babble://feedchef?recipe=` is handled in `BabbleApplication.on_open`
   before OAuth; Base64 URL-safe with padding restored; host must be
   `feedchef` (not an arbitrary path segment).
4. Fuzzy synonyms stay **off** in v1 (no 16 MB LGPL `synonyms.json`).
5. Sentiment is **vendored VADER + LeIA** (`src/feedchef/vendor/`, MIT
   NOTICE), not mashdon’s JSON lexicon and not a PyPI dependency. `pt` /
   `pt-*` status language → LeIA, `en` / `en-*` → VADER, any other or no language
   → not judged (deviation 15). Scores are VADER
   `compound` in [-1, 1].
6. Without TF-IDF, an active focus does **not** freeze merge order: kitchen
   falls back to `base_sort` (score or chronological). Relevance sort waits
   on an NLP gap.
7. The Java builder is ported in part, on purpose (see Builder below). Not
   exposed, each for a reason in the code, not for want of time:
   `include_unknown` (with degree 1 the only thing it would do is switch the
   follow filter off, which "Only People I Follow" already does);
   similarity clustering (inert without embeddings or TF-IDF, so a control
   would promise what the kitchen does not do); the diversity dimension
   toggle (only the author dimension has an effect, and it is what the
   "Limit Posts per Person" modifier writes).
8. The scope cursor is `pantry.scope_cursor`: the smallest **wrapper** id
   (integers when all are digits, else the last item), because Mastodon
   pages by the timeline entry id and a boost carries the old date of the
   boosted post. `oldest_id()` stays the mirror of Java's `next_page_max_id`.
9. A recipes file that cannot be read (or is not a JSON list) is renamed to
   `<name>.corrupt-<unix-ts>` (mode 0600) before a save or delete writes;
   Java wipes it. `load_recipes` stays read-only.
10. More Like This is per post (an entry in the post's overflow menu, added
   through the generic `page.` hook of `statusmenu`), not on the top post.
   Keywords are the top 5 TF-IDF terms of the post against the current
   timeline (the post alone when empty); accents are stripped and the 39
   Java stopwords apply. The relevance **sort** is still inert (item 6).
11. The timeline is a `TimelinePage` over `feedchefsource.FeedChefSource`.
    Its `RequestScope` supersedes a request in flight (the source answers
    CANCELLED and stops the engine), so no request gate of its own is kept.
    Older pages use the scope cursor of the last result, not TimelinePage's
    id cursor. A refresh cooks again and replaces the list in kitchen order.

12. **"Only People I Follow" fails closed.** Java let everyone in while the
    follow list was loading or empty. Here the content author (the boosted
    account for a boost) must be followed or be the viewer. While the list
    is not known the cook **waits** for the preload (`FeedEngine.reach_wait_timeout`,
    20 s, cancellable); if it cannot be loaded (a failed page, no way to
    load it, or the timeout) the cook fails with `FollowListError` and the
    page shows its error page, titled "Could Not Check Who You Follow", with
    Try Again (a retry restarts the load). A list that loaded and is empty
    is a valid answer: only the viewer's own posts show. The list is cached
    on disk per account: a cache read from disk is used at once and is
    **stale** until this session's preload replaces it (it may miss a follow
    made since it was written, or keep an unfollowed account, until then;
    the preload runs whenever a timeline opens, and the decision log records
    the `network_reach_stale_cache` fallback). A failed refresh keeps a
    stale list; only "no list at all" is an error. A failed page fails the
    whole load (a partial list would hide followed people and be saved as
    complete). The page cap is 250 x 80 accounts, above Mastodon's default
    follow limit of 7,500; a hit is logged and flagged `truncated`.
    `include_unknown` is a recipe field and is **honoured as written** (true
    lets strangers in and needs no list), but the builder never sets it. An
    own post always passes, `include_self` or not (the viewer is degree
    zero, as in Java's `degree()`).
13. **Included hashtags bind every source** except a hashtag timeline, which
    carries the tag by construction (the final guard asserts it): the scope
    and the More Like This search results are restricted to posts with at
    least one included hashtag, so a focus narrows within the recipe and
    does not escape it. Excluded hashtags apply to every source. One limit
    remains: More Like This posts come from the server-wide search, so they
    obey every filter but are not bound to "Posts From".
14. **"Limit Posts per Person" is a cap on the cook session, applied after the
    order.** Java capped each batch in arrival order. Here the cap runs after
    the kitchen's ordering, so each person keeps their best-ranked posts, and
    the per-person counts travel with the session: `FeedChefSource` passes
    `author_counts` to every page and keeps `FeedResult.author_counts` only
    for a page it showed (a cancelled cook is not counted), and a refresh
    resets them. The person is the account that put the post in the feed
    (the booster for a boost, as in Java); the builder says so.
15. **Posts in other languages are not judged.** `sentiment.score_status`
    returns `None` unless the post's language is English or Portuguese (the
    boosted post's, else the wrapper's). A missing language is not guessed:
    the English lexicon over text of an unknown language reads nearly
    everything as neutral, a judgment nobody made. "Only Positive Posts"
    neither hides nor "Favor Positive Posts" boosts an unjudged post.
16. **Order is total and tied by id.** "Newest first" is non-increasing by the
    created_at of the post the reader sees, ties by id, newest id first;
    "Most engaging" is non-increasing by final score (after the freshness,
    trending and sentiment boosts), ties newest first, then by id. A focus
    does not change either (the relevance sort is inert).
17. **Blocked words and the minimum length read the visible text.** Java
    matched the HTML source (a blocked word such as `href` hit every link).
    The text is the post's HTML stripped, with the content warning; a word
    matches as a whole word ignoring case, where the boundary is "not next
    to a letter or digit" in Unicode (so `café` and `c++` match). The
    minimum length counts the characters of that text, with a link counted
    in full (the hidden parts of its markup included); the builder says
    "links counted in full". Block boundaries separate words: a `<br>` is a
    newline, a paragraph, `<div>`, list item or blockquote a blank line, and
    every run of whitespace (newlines and no-break spaces included) is read
    as one space before a word is matched or the length counted, so a blocked
    entry of several words matches across a line break and `<p>a</p><p>b</p>`
    is five characters, not four. The minimum length counts what the reader
    sees: the "RE: <link>" line of a quote post is left out exactly when the
    row hides it because it shows the quote as a card
    (`quoteline.hidden`, the one decision the row's `wants_inline_dropped`
    reads too, with the native Bluesky setting asked once per cook).
18. **A scope that fails is an error, a page is never cut, a refresh keeps
    the session.** A scope fetch that raises or misses its deadline fails the
    cook (`ScopeFetchError` for the deadline), on every page: the page shows
    its error page with Try Again, because an empty page with `has_more`
    false ended the paging for good while older posts existed. This holds on
    page one even when hashtag or More Like This posts arrived: without the
    scope there is no cursor to continue from, so returning only those would
    show a partial feed that looks complete; Try Again is the honest answer.
    A hashtag or More Like This fetch that fails stays soft (logged). The
    engine never truncates a page to `count`: the scope's cursor has already
    moved past every post it returned, so a post cut after the merge (page one
    holds the scope's page plus hashtag and More Like This posts) would be
    skipped forever; `count` is only the size asked of the scope. `FeedChefSource`
    changes its cursor, `has_more` and person counts only when a page lands;
    a refresh that fails or is cancelled leaves the session as it was and the
    older pages go on from the old cursor. The viewer's own filters are
    applied before the kitchen (`feedchefsource.viewer_hides`, the HOME
    context `TimelinePage` gives a source without a path, action 'hide'
    only; 'warn' and 'blur' posts stay, shown collapsed or covered), so a
    hidden post uses no slot of "Limit Posts per Person" and no place in the
    heat percentile.
19. **The follow list on disk is used at once, and may be stale.** The first
    cook after the app starts reads the list written by the last session and
    does not wait for the preload that replaces it (waiting would make
    opening a recipe depend on a few hundred requests every time). Until the
    preload lands, "Only People I Follow" can still show an account
    unfollowed since the list was written, and miss one followed since. The
    reader's next refresh after the preload is exact; the decision log marks
    the first cook `network_reach_stale_cache`. Accepted: the list is as old
    as the last time a timeline was opened, and a fail-closed wait on every
    open would cost more than the briefly stale answer; a recipe that must
    be exact is refreshed once.

## Promises and enforcement

Each promise the builder makes is a predicate in `src/feedchef/contract.py`
(one id each, the name of the recipe field). The stage listed decides; the
guard runs every predicate again on every returned post
(`FeedEngine._guard`), drops a violator, logs `contract:<id>:<post id>` in
the decision log's reliability errors and at warning level, and reports
`FeedResult.guard = {'checked', 'dropped'}`. The oracles in
`src/test-feedchef-contract.py` are written without the contract module; the
fuzz runs 240 seeded cases over three pages.

| UI wording | Stage | Guard predicate | Test (`feedchef contract`) |
|---|---|---|---|
| Blocked Words: whole words, in the post and its content warning, ignoring case | diet | `keyword_blocklist` | fuzz: blocked words |
| Only Posts With Media | diet | `must_have_media` | fuzz: only with media |
| Require Alt Text on Media: posts without media still show | diet | `must_have_alt_text` (every attachment described; no media passes) | fuzz: alt text |
| Hide Posts Marked Sensitive: posts flagged as sensitive, often those with a content warning | diet | `no_sensitive_media` (the `sensitive` flag) | fuzz: sensitive |
| Minimum Length: characters of text, links counted in full | diet | `min_content_length` (the hidden "RE:" line is not counted) | fuzz: minimum length; link, padding, boost and quote-line cases |
| Only Positive Posts: hides English and Portuguese posts that read as negative | diet | `positive_only` (None = not judged passes) | fuzz: only positive; per-language cases |
| Include Hashtags: at least one of them | pantry merge (scope, More Like This); hashtag timelines by construction | `hashtags_include` | fuzz: include hashtags (focus cases included); a lying hashtag timeline; More Like This case |
| Exclude Hashtags: never | pantry merge (every source) | `hashtags_exclude` | fuzz: exclude hashtags |
| Only People I Follow: plus your own posts, boosts judged by the original author | reach (after the wait for the follow list) | `network_reach` (unknown list fails closed) | loading, error, timeout, retry, empty, no-source, stale-disk and include_unknown cases; fuzz: only people I follow |
| Order: Newest first | kitchen order | (list level) | fuzz: order newest first; tie-by-id case |
| Order: Most engaging | kitchen order | (list level) | fuzz: order most engaging; order with focus |
| Hide the Least Engaging: percent of each batch; engagement is replies, boosts and favorites, weighted, after freshness and trending | kitchen heat | (list level) | fuzz: heat (exact count); `feedchef regressions`: ties, P=1, P=10, P=90, one and two posts |
| Favor Fresh Posts: newer posts rank higher; older than the fade-out score zero | kitchen score | (list level) | 300 equal pairs |
| Boost Trending Topics: hashtags many people use now, and posts trending on the server | kitchen score | (list level) | 300 equal pairs; a server-trending post |
| Favor Positive Posts: English and Portuguese posts that read as positive rank higher | kitchen score | (list level) | per-language cases |
| Limit Posts per Person: until you refresh; a boost counts for the person who boosted | kitchen cap, after the order, session counts | (list level) | fuzz: per person; 3-page best-ranked case |

"Hide the Least Engaging" hides exactly floor(P x n / 100) of the n posts of
a batch: the lowest by score, the older first among equal scores, and never
all of them (a batch of one is never emptied). The rest keep their order.
This replaces Java's percentile cut with a relax rule: it hid a count that
depended on ties, and nothing at all when every score was equal.

## Builder (= Java form + HIG)

The builder is an `Adw.Dialog` (`feedchefbuilder.py`) that edits a copy of
the recipe: Cancel discards it, Save stores it, and a new recipe is not
stored before Save. It edits: name and icon; pantry source (Home, Local,
Federated or one of the account's lists, loaded through the lists API),
include and exclude hashtags, and "Only People I Follow" (network reach on,
one degree, or off; an imported recipe's other degree count is kept until
the switch is changed); diet blocklist, media, alt text and sensitive-media
filters, "Only Positive Posts" (`positive_only`) and the minimum length;
the kitchen order (most engaging or newest first) and the five modifiers
that run today (freshness in hours, heat percent, posts per person,
trending growth in percent, and "Favor Positive Posts" as a strength of 10
to 100 percent, written as `sentiment_boost` 0.1 to 1.0), each an expander
with an enable switch. Writing back keeps the modifier order, adds a newly
enabled one at the end, removes a switched-off one, and leaves unsupported
modifiers and values the reader did not touch as they were. Unsupported
ingredients (similarity grouping, connections, focus keyword sorting) are
named in one plain row; sentiment is no longer among them.

**Sentiment is active.** `positive_only` hides posts whose score is
negative, and `sentiment_boost` lifts positive ones, scored by the vendored
VADER (English) and LeIA (Portuguese). Only those two languages are judged
(deviation 15); the dialog says "English and Portuguese" and names no engine.

**The icon** is `recipe.meta.icon`, which keeps a mashdon icon enum name
(default `PIZZA`) so a shared recipe round-trips. `src/feedchef/icons.py`
holds the table (23 entries, one icon each, every one present in the
Adwaita icon theme or Babble's own icons); the builder gives each a
translated label and shows them in a popover of flat buttons. A name not in
the table shows the chef hat (`babble-feed-chef-symbolic`). The icon is the
prefix of the recipe's row and of its pinned sidebar entry; the Feed Chef
place itself keeps the chef hat.

**The preview** ("Preview Recipe", beside "Copy Share Link") opens
`FeedChefPreviewDialog` (`feedchefpreview.py`): a `ToastDialog` holding a
`TimelinePage` over a `FeedChefSource` of a copy of the unsaved recipe,
with an engine of its own that is shut down when the dialog closes. It has
no focus banner, no edit button and no More Like This entry.

## Audit: a replayable record of every page

The decision log (v1 `stages`) could not answer "why is this post here, or
not": no recipe snapshot, no origin, no reason for a removal after the diet,
no score breakdown, a clock read inside the stages. Every engine page now
also writes a schema-2 record under the `audit` key of its log
(`src/feedchef/audit.py`, GTK-free). `bench/feedchef_audit.py` reads it.

**The audit only observes.** The engine hooks take a recorder; the default
`NullAudit` does nothing and no selection code reads from it. Neutrality is a
test (`feedchef audit record`): the same world with the audit on and off
gives the same items, order, scores, counts and kitchen meta over the fuzz
seeds. Capture adds under 5 ms (p95) per 40-post page, measured by that test
and run on the engine worker, never the UI thread. `FeedEngine.audit = False`
turns it off; `FeedEngine.clock` is the one reading of the time per page (the
kitchen and the viewer filter take it as `now`).

**What a record holds** (one entry per unique post):

- identity: `session_id`, `cook_id` (one per refresh session), `page_index`,
  `hop` (an empty page followed by the next older one), cursors;
- `app.version` (`babble.VERSION`, "dev" when unset), the recipe id, its
  sha256 (`recipe_hash`, canonical JSON) and the full snapshot, the account;
- the environment a replay needs: the clock, `self_id`, the Bluesky setting,
  the follow state (fresh, stale, none or error, the count and a 16-hex
  fingerprint: the list itself is never stored), the server trends of the page,
  the local tag heat, the author counts the page started from;
- per post: `origin` (`scope`, `hashtag:<tag>`, `focus:<term>`) and
  `also_in`, `facts` (dates, counts, tags, language, media and alt, sensitive,
  text length and a reference into the sidecar, author and booster ids), the
  follow verdict, a pass/fail/n-a result for each promise in the order the
  stages ask them, the sentiment (engine and score), the score breakdown
  (`base`, `fresh`, `sent`, `trend` factors, `final`), the removal and the
  rank.

**Each removed post has exactly one reason, from a closed set** (`is_reason`):
`pantry_excluded_tag`, `pantry_missing_included_tag`, `diet:<promise>`,
`reach_not_followed`, `viewer_hide`, `heat_cut`, `author_cap`,
`guard:<promise>`. The guard is the only stage that lists every promise a post
breaks (`removal.also`). Two deviations from the first plan: a post fetched
twice is one entry (the duplicate is an `also_in` and a count in
`summary.duplicates`, not a removal), and a post with no id is only counted
(`summary.no_id`). `summary.pantry_in == selected + sum(by_reason)` holds.

**Replay is the proof.** `replay` rebuilds each post from its facts and the
sidecar, runs the real engine over fake fetches that hand them back in the
sources the record names, at the recorded clock, and compares the record it
writes with the one it replays: posts, reasons, rules, scores, order, bit for
bit (floats go out as `repr`, which round-trips). What is not replayable is
recorded and fed back: the viewer's own filter verdicts, the sentiment score of
each post (the analyser is re-run on the rebuilt text only to warn about
drift). A record made by another `ENGINE_REV` (bumped by hand when a stage
changes meaning) is refused unless `--force`. When the text is not whole (a
post over 2,000 characters, or the sidecar deleted) the two rules that read it
(blocked words, minimum length) use their recorded verdicts and the replay says
how many posts it did so for ("partial"). Known limits: the follow list is
snapshotted once, after the wait, so a preload that lands mid-cook can make
the reach stage and the record disagree; a post cannot be proven
when its text cannot be checked.

**The record vouches for itself.** Each source's list of posts is recorded
(`sources`: the scope, each hashtag and each More Like This term, in order, a
post twice or with no id included), so a replay rebuilds the exact order and
counts, ids that are not digits included. Each post carries the sha256 of its
sidecar text; `evaluate` and `replay` report a text that is missing or does
not match as unverified (`PASS (text unverified: N)`, and `replay` exits 3
unless `--allow-partial`). The viewer filter's verdict is recorded per post,
and `evaluate` first checks the record's own consistency (every post selected
or removed once, the summary adding up, a failed cook with no output) and its
shape, so a malformed or contradictory log is a FAIL with a reason, never a
traceback. A page cooked twice counts once; a cook cancelled after its record
was written is rewritten as `cancelled`. A preview leaves no audit record.

**`evaluate`** checks the output against the recipe snapshot with no
tolerance: every kept post keeps every promise, the order is the recipe's,
heat removed exactly floor(P x n / 100), the cap per person holds across the
pages of a cook, the hashtags and the follow setting are respected, no post
was dropped by the guard (a stage let a violator through), and each removal is
justified by the recipe. `viewer_hide` is the one reason it cannot judge.

**Privacy.** The visible text (the stripped text the contract reads, 2,000
characters at most, and the content warning) goes to `<log>.text.json` beside
the log, mode 0600 in the 0700 log folder; it is deleted with its log and never
exported. `collect --json` and Copy Report carry counts, verdicts, the recipe
hash and dates: no post id, handle, text, account or recipe name (the test
greps the export for every id, handle and text it recorded).

**Retention.** The last 20 cooks of each recipe (grouped by `cook_id`, so every
page and hop of a cook stays together), then a 50 MB cap on the folder, oldest
cook first and the newest always kept; one busy recipe never evicts another's
history. `decisionlog.clear_all()` and `clear_recipe(id)` back Clear Audit
Logs.

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, mashdon `sentiment_words.json` (replaced by VADER/LeIA),
`focus_decay`, 450 ms search stagger, freshness blend 0.4 / floor 0.2,
federated 8 h manual QA, mashdon `tools/` Python helpers, PRD-only pantry
window / `RequestMode` / `expected_outcome`.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, `statustext`
and `quoteline` (display-free). It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers without an approved NLP gap show as
inert in the builder/import UI. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
