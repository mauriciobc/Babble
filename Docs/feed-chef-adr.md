# ADR — Feed Chef v1 (Babble)

Status: accepted for implementation. Behaviour source of truth is mashdon
`master` @ `23277cd1ca40f6fe618c4f246896fd80777bf4ea`, not the PRD where they
diverge. **Two Java revisions are pinned.** `23277cd` is the reference of the
v1 rows below (contract table, deviations 1 to 19). `bc0b874` (moshidon,
branch `claude/review-feed-recipe-timelines-hZSbi`) is the reference of Phase 1
(engine revision 3): the relevance sort, the similarity grouping and its
weights (0.1 / 0.7 / 0.2), the freshness floor and the 450 ms stagger
(deviations 6, 20 to 24, and the freshness and diversity rows). A row or
deviation that names one SHA means that one. The PRD describes newer behaviour that is **not** on that master
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
| `freshness` | `23277cd`: `score *= factor`. `bc0b874`: `score *= 0.6 + 0.4 * factor`, with the floor of 0.2 on the factor | blend 0.4 | **`score *= factor`, no blend, with the floor of 0.2 under trending (deviation 21).** In Java at `bc0b874` the blend keeps a score at 60% or more of itself, so its floor is nearly inert; here, with no blend, the floor is what keeps an old viral post from scoring zero |
| `heat` / `diversity` | heat after scores, before structural; diversity pre-sort, author only | PRD order / instance | **Java order; heat hides exactly floor(P x n / 100), the lowest first** (see Promises) |
| `similarity_cluster` / TF-IDF / synonyms | present | present | **active** since engine revision 3: TF-IDF relevance sort and similarity grouping (deviations 6, 20, 21); **synonyms and trigrams stay off** (no `synonyms.json` 16 MB LGPL in the binary); grouping needs the optional `kmedoids` package and says so when it is missing |
| Sentiment | `SimpleSentimentAnalyzer` + `sentiment_words.json` (~94 KB); often `0` if missing | present | **Vendored VADER (EN) + LeIA (pt*)** — MIT, no PyPI; activates `positive_only` / `sentiment_boost` (Babble upgrade, not Java JSON parity) |
| Server trends | deadlocked same executor | own executor / 2 s | **separate thread + 5 min memory cache**; never nest `submit`+`get` on that pool |
| Network reach | degree 1 only; `null` disables; author = **boosted** content account; everyone passes while the list loads or is empty | BFS / null=∞ | **degree 1; null=off; boosted author** (= Java), but **fails closed** (deviation 12) |
| More Like This | same timeline pantry when focus is set; UI can set focus | dedicated fragment | **pantry MLT = Java**; focus via import **or** timeline “More like this” (no dedicated fragment) |
| Builder controls | full (icon, lists, diet/kitchen sliders, modifiers) + preview sheet | full | Java form fields in a HIG `Adw.Dialog` (`src/feedchefbuilder.py`), without `include_unknown` (deviation 7), plus an icon table, `positive_only`, `sentiment_boost`, the similarity grouping (posts per group, which post stays), the server dimension of the person cap, and a **preview** (`FeedChefPreviewDialog`, own engine) |
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
   `pt-*` status language → LeIA, `en` / `en-*` → VADER, any other language, or none
   and no confident guess → not judged (deviation 15). Scores are VADER
   `compound` in [-1, 1].
6. **A focus orders the page by relevance** (engine revision 3; this lifts the
   first version's fallback to `base_sort`). Java's `applyRelevanceSort` is
   ported (`src/feedchef/relevance.py`): a TF-IDF vector per post (augmented
   TF, smoothed IDF `log(N / (1 + df)) + 1`, at most 100 terms) over the posts
   that are left after the grouping and the heat cut, the reference vector of
   `active_focus_keywords` with `active_focus_weights` (all 1.0 when they are
   not given for every keyword, 0.1 for a weight not above zero), and the
   cosine; the page is ordered by it, then by the tie rule of deviation 16.
   The focus replaces `base_sort` while it is set, as in Java. What differs:
   synonyms and trigrams (Java's fuzzy matching, on by default there) stay
   off, as for deviation 4, and `enable_fuzzy_matching` is read as off; a
   keyword is lowercased and stripped of accents like the words of a post
   (Java compares it as written); sums run over sorted terms, so a score does
   not depend on dictionary order. The code is plain Python, not numpy: at the
   size of a page (120 posts: 2 ms measured by `feedchef relevance`) a loop is
   fast enough, and a BLAS sum need not round like a loop, which a replay
   would show. A recipe that sets no focus is untouched. The audit records
   `score.relevance` for every post that reached the order.
7. The Java builder is ported in part, on purpose (see Builder below). Not
   exposed, each for a reason in the code, not for want of time:
   `include_unknown` (with degree 1 the only thing it would do is switch the
   follow filter off, which "Only People I Follow" already does). The
   similarity grouping ("Group Similar Posts": posts per
   group and which post stays) and the server dimension of the person cap
   ("Count Servers Too") are exposed since engine revision 3, each active in
   the kitchen; a control for the grouping is shown but not editable, with the
   reason, while the optional packages it needs are missing.
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
   Java stopwords apply. The focus those keywords make orders the page by
   relevance (deviation 6) and fades with `focus_decay` (deviation 23).
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
15. **Posts in other languages are not judged; a post with no declared
    language is judged on a confident guess** (maintainer's decision,
    2026-11-23). `sentiment.score_status` reads a post in English (VADER) or
    Portuguese (LeIA) by its declared language (the boosted post's, else the
    wrapper's); any other declared language is not judged, and a declared
    language is never second-guessed. For a post that declares **none**, a
    guess by py3langid (`src/feedchef/langguess.py`, through `feedchef.deps`,
    on the engine worker; restricted to `RUNTIME_LANGS`, the set
    `bench/feedchefaudit/langbench.py` measured, held equal by a test; text
    cleaned as langbench cleans it: links and handles removed) is acted on only
    when **all** hold: the cleaned visible text is at least 40 characters, the
    normalised probability is at least 0.63, and the guess is `en` (VADER) or
    `pt` (LeIA). Any other guess, a lower probability, or a short text is "not
    judged", as before: "Only Positive Posts" neither hides nor "Favor Positive
    Posts" boosts it. **Why these numbers** (`langbench`, 1,371 posts that
    declare a language, once per distinct text): the labels are the authors'
    declared languages, which are noisy (clients default the field), so the
    precisions are a lower-bound proxy; posts under 40 characters are excluded
    because the detector is unreliable on them; 0.63 gave the best worst-case
    Wilson 95% lower bound of precision over `en` and `pt` (0.929) with
    coverage 0.89; no threshold reached the 0.97 the plan hoped for, and the
    maintainer chose to enable guessing at that point rather than leave these
    posts unjudged. The guess is deterministic. **Recorded:**
    `facts.lang_guess {lang, p}` (p a float) for every post that declares no
    language and has 40 characters of text, whatever the answer, **when the
    recipe reads sentiment** (positive-only or favor-positive; a recipe that
    does not never imports py3langid, Principle 6), `sent.engine`
    for a judged one, and `env.lang_guess {status, reason, min_p, min_chars, version}`
    per page; without py3langid the status is `unavailable`, the Kitchen Report
    says so for a recipe that reads sentiment, and such posts stay unjudged.
    **Enforced:** the contract promise `language_guess` (a judged post with no
    declared language must have an acted-on guess: enough text, `en` or `pt`,
    at least 0.63) is checked by the guard, and `evaluate` has a `language`
    rule that holds each such post to p >= 0.63, a matching engine and 40
    characters. **Replay** feeds the recorded guess back and runs the guesser
    again to compare: a guesser that answers otherwise is a drift warning (as for
    sentiment); a selection that changes (the guard dropping a post whose guess
    is below the gate) is a mismatch. Why This Post? says "Guessed as English,
    78% sure" for such a post.
16. **Order is total and tied by id.** "Newest first" is non-increasing by the
    created_at of the post the reader sees, ties by id, newest id first;
    "Most engaging" is non-increasing by final score (after the freshness,
    trending and sentiment boosts), ties newest first, then by id. A focus of
    keywords replaces both: the page is non-increasing by relevance, with the
    same tie rule (deviation 6); a focus of hashtags only changes nothing.
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

20. **Similarity grouping** (`src/feedchef/cluster.py`; the "Group Similar
    Posts" modifier `similarity_cluster`), Java's `applySimilarityClustering`
    with these pins and changes. *Similarity* of two posts is
    `0.1 * Jaccard(hashtags) + 0.7 * cosine(TF-IDF) + 0.2 * same person`:
    the weights of the current Java (`PostSimilarityCalculator` at bc0b874,
    rebalanced from 0.3 / 0.5 / 0.2 because hashtags are rare; the audit
    records the weights, so a change of them is a one-line edit and old
    records still replay). *k* is `min(5, max(1, n // 10))`, and fewer than ten
    posts are not grouped (recorded as `skipped`). `similarity_threshold` is
    stored by Java and read by nothing; it is not read here either. The posts
    are grouped by **k-medoids (FasterPAM, the `kmedoids` package) over
    `1 - similarity`**, from `init='build'` and `random_state=0` (both
    recorded), where Java runs a randomly started PAM-like loop: the same
    posts **in the same order** always give the same groups (the distances are
    built in page order and a tie between equally good medoids is settled by
    position, so another order may group differently), and a replay reproduces
    them (a record made with other settings replays with the ones it names).
    The record holds `max_iter` and the versions of `kmedoids` and numpy; a
    replay where a version differs and the page differs gives no verdict
    (`skipped`, the versions named), not a mismatch, and one made on a machine
    without `kmedoids` is "cannot be replayed here". The library's answer is
    checked (labels and medoids in range, one medoid per group, a finite
    loss); a malformed one is `unavailable`. Groups are
    numbered by the page position of their medoid. *Selection* is Java's
    round robin (`ClusterSelector`): each group keeps up to
    `max_per_cluster` posts (1 when missing; Java returned nothing for 0, here
    it is at least 1), `best_score` by engagement (Java's raw replies, boosts
    and favourites count, not the score after the modifiers), `most_recent` by
    date, `centroid` (Java's enum is `CENTROID_THEN_SCORE`; a recipe may spell it `centroid`, as Java's builder writes it, or `centroid_then_score`, and both are accepted) the medoid first and then by
    engagement; equal keys are broken newest first, then by id, where Java
    kept the order of arrival; the kept posts keep their order in the page.
    A post is read as the post it shows (hashtags, words), and "the same
    person" is the account that put it in the feed, as for the cap
    (deviation 14); Java reads the wrapper's own fields, which are empty for a
    boost. The stage
    sits where Java puts it: **grouping, then the heat cut, then the order,
    then the person cap**, so heat hides floor(P x n / 100) of what is left of
    the grouping. A removed post has the reason `cluster_cap`, and the record
    holds each post's group and medoid and, per page, the loss, k, start,
    seed, weights and strategy. **Without `numpy` and `kmedoids`, or when the
    call fails, the modifier is marked `unavailable` with the reason (the
    kitchen meta, the audit record, and the builder, which shows the row but
    not editable) and removes nothing: never a quiet no-op.** Only the first
    grouping modifier of a recipe applies; another is listed as inert. K-means
    and e-means were considered (the plan, "Dependencies"): the dissimilarity
    mixes sets and a same-person term that are not vectors, and a real post is
    needed as the centre of a group, which a medoid is; at 40 to 120 posts
    FasterPAM takes under a millisecond. The loss is recorded so that another
    algorithm can be compared with it later.
21. **The freshness floor.** When a recipe has both "Favor Fresh Posts" and
    "Boost Trending Topics", a freshness factor under 0.2 is raised to 0.2
    (Java's `FRESHNESS_FLOOR_FOR_TRENDING`), so an old viral post is not scored
    zero. The audit records `score.fresh_floor: true` for each post whose
    factor it raised. Java also blends the factor (`0.6 + 0.4 * factor`); that
    stays out (see Out of v1).
22. **The server dimension of the person cap.** `diversity_dimensions` may hold
    `'author'` (the default), `'instance'` or both; the cap (`max_per_cluster`)
    applies to each. The server is the host of the account that put the post
    in the feed; a post whose account has no host is never capped by it (Java).
    Only a kept post is counted for either dimension (Java counted the person
    of a post the server cap then refused). The counts of both travel with the
    session: `FeedResult.instance_counts` and the engine's `instance_counts`
    parameter sit beside `author_counts` (which is unchanged, so old callers
    and records read as before; `params.instance_counts_in` and
    `outputs.instance_counts_out` are new and optional in the record). The
    removal reason is `instance_cap`.
22b. **A cap is read tolerantly and imported strictly.** `max_per_cluster` of a
    `similarity_cluster` or `diversity` modifier is read by `recipe.max_per`: a
    whole number of 1 or more, and 1 for anything else (missing, 0, negative,
    text, infinite), never an exception, the same for both modifiers (Java
    returned no post at all for a grouping cap of 0). `validate_for_import`
    rejects a value that is not a whole number of 1 or more, with a message
    that says so.
23. **`focus_decay` is Babble's definition.** Java keeps the field in the recipe
    and nothing applies it. Here (`src/feedchef/focus.py`): on each refresh
    the reader asks for while a focus is active, every focus weight is
    multiplied by `focus_decay` (a keyword with no weight counts as 1.0); a
    keyword whose weight is then below 0.1 is dropped; when none is left the
    focus clears (hashtags too). The recipe is saved and the banner follows.
    A value of 1.0 or more never decays. Every weight is multiplied by the same
    number, so the order does not change while keywords remain: the decay is the
    focus's lifetime (22 refreshes at 0.9 from equal weights), and a keyword
    imported with a smaller weight goes first. A hashtag has no weight: it
    stays until the keywords are gone. It runs in `FeedChefTimelinePage.pull_refresh`
    (the reader's refresh), never in the engine, so a page always cooks with
    the weights its record shows.
    **Revision (review):** the focus has a **strength**
    (`kitchen.active_focus_strength`, 1.0 when it is set, serialised; a recipe
    without it reads 1.0) that is multiplied by `focus_decay` on each refresh,
    so a focus of hashtags only, which has no weights, fades too: the whole
    focus (hashtags and keywords) clears when the strength is under 0.1, or when
    it had keywords and none is left. Setting a focus or clearing it resets the
    strength. A weight is read as the relevance sort reads it (0, negative or
    not a number is 0.1), so the two never disagree. The order does not change
    while keywords remain: the relevance reference vector is scaled by its
    largest weight and relevances are rounded to 12 significant digits
    (deviation 6), so a uniform fade cannot reorder a page (400 random pages, 0
    reordered). The engine cooks a **copy** of the recipe taken when the cook
    starts, and a refresh cancels the cook in flight before it fades the focus,
    so a record always shows the weights the page was cooked with. Known and
    left as it is: a wheel pull at the top of the page is a refresh, and fades
    the focus too.
24. **The 450 ms stagger is Java's, for the searches of More Like This.** Java
    pauses 450 ms between the *searches* of its More Like This (the hashtag
    searches, then the keyword one); the hashtag *timelines* of the pantry are
    fetched in parallel with no pause. Babble does the same:
    `FeedEngine.search_stagger_ms = 450` is the pause between two searches of
    `pantry.fetch_more_like_this` (none before the first, none on a cache hit),
    and it is a wait on the cook's cancel event, so a cancelled cook ends at
    once with `pantry.Cancelled` (not a failed search; nothing is cached; any
    other `InterruptedError`, a search's own, is a soft failure). Java searches
    by keyword only when there are no hashtags, so its pause before the keyword
    search never fires; here too a focus searches hashtags or the top keyword,
    never both. Java runs these searches before it waits for the pantry; here the
    scope and hashtag fetches are **started first** and the searches run while
    they are under way, so page one is not delayed by up to 0.9 s, and the
    pantry's deadline (30 s plus 10 s per hashtag timeline) starts when the
    searches end. A replay sets the pause to 0.
25. **A focus that matches nothing keeps the recipe's order** (Babble, not Java:
    Java sorts by relevance whatever the scores, which makes every score 0 and
    the page "newest first" with the recipe's order lost). When no post of the
    page has any relevance, the kitchen orders it as the recipe says, records
    `focus_unmatched` (kitchen meta and the record), the focus banner says none
    of the posts here match it, and Why This Post? says the page is in the
    recipe's order. The relevance promise is then not made for that page.

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
| Hide the Least Engaging: Warm, Hot or Fire (50, 75, 90 percent of each batch; off is the switch; an imported percent is kept and said); engagement is replies, boosts and favorites, weighted, after freshness and trending. A page never keeps fewer than 5 posts: the cut steps down to the first level that leaves them, or to every post, and the page says which | kitchen heat | `heat_count` and `heat_fallback` (page level, guard: recorded, no post to drop) | fuzz: heat (exact count at the level used, fallback, imported percents); `feedchef contract`: a stage that skips or overdoes the fallback, a wrong count; `feedchef steps`; the audit's `heat` rule; `feedchef regressions` |
| Popular badge: a post whose score is above the 90th percentile of the page's scores (none when every score is equal) | kitchen (after the cap) | `fire_badge` (list level, guard, both directions, against the kitchen's own threshold) | fuzz: hot badge; `feedchef contract`: a badge on a post that is not above, none on one that is, a threshold that is not the percentile; the audit's `fire` rule; `feedchef experience` |
| Favor Fresh Posts: newer posts rank higher; older than the fade-out score zero | kitchen score | (list level) | 300 equal pairs |
| Boost Trending Topics: hashtags many people use now, and posts trending on the server | kitchen score | (list level) | 300 equal pairs; a server-trending post |
| Favor Positive Posts: English and Portuguese posts that read as positive rank higher | kitchen score | (list level) | per-language cases |
| Limit Posts per Person: until you refresh; a boost counts for the person who boosted | kitchen cap, after the order, session counts | (list level) | fuzz: per person; 3-page best-ranked case |
| Count Servers Too: at most N posts per server, over the session | kitchen cap, after the order, session counts | `instance_cap` (list level, guard) | fuzz: per server; `feedchef cluster`: honest and broken stage |
| Group Similar Posts: at most N posts of each group of similar posts | kitchen grouping, before heat | `cluster_cap` (list level, guard) | fuzz: per group; `feedchef cluster`: honest and broken stage; the audit's `cluster` rule |
| More Like This focus: the page is non-increasing by relevance to the focus | kitchen order | `relevance_order` (list level, guard) | fuzz: order by relevance and an oracle that writes Java's formulas again; `feedchef relevance`; the audit's `relevance` rule |
| Favor Fresh Posts with Boost Trending Topics: a factor under 0.2 is raised to 0.2, and only then | kitchen score | `fresh_floor` (list level, guard) | fuzz: freshness floor, both directions; `feedchef relevance`; the audit's `floor` rule |
| New posts worth seeing, merged into the top of the list: no post twice, every inserted post from a recorded peek page, only the first 50 posts ordered again and by the recipe's order rule, the rest of the list untouched | page (`smartmerge.merge_violations`, before the model is touched) | `merge_duplicate`, `merge_foreign`, `merge_members`, `merge_window`, `merge_tail`, `merge_order` (merge level: a merge that breaks one is refused, nothing is inserted; the audit's `merge` rule judges the record of a shown peek) | `feedchef peek`; the audit self-test |

The two promises about the heat cut name no post (a cut that hid the wrong
number of posts cannot be undone): `contract.PAGE_IDS`
(`heat_count`, `heat_fallback`, `contract.heat_violations`) are recorded by
the guard in `guard.dropped`, with a `contract:<id>` error in the log, and
nothing is dropped for them. `heat_fallback` is "the step asked for is the
recipe's, and the step used is the first, from it down, that leaves at least
`heat.MIN_LEFT` (5) posts, or Cool"; `heat_count` is "the cut hid exactly
floor(P_used x n / 100) of the n posts, never all of them". The audit records
one row per heat modifier (`heat`: requested, used, before, after).

The promises about the page as a whole (`relevance_order`, `cluster_cap`,
`fresh_floor`, `instance_cap`, `fire_badge`; `contract.LIST_IDS`, `contract.list_violations`) are
checked by the guard after the per-post ones, against what the stages decided
(`kitchen_meta['relevance_scores']`, `['groups']`, `['fresh']`, the session's
server counts), and a post that breaks one is dropped and logged as
`guard:<id>`, like any other. `evaluate` recomputes them from the record
(see Audit).

"Hide the Least Engaging" hides exactly floor(P x n / 100) of the n posts of
a batch (what is left of it after "Group Similar Posts" took its share): the
lowest by score, the older first among equal scores, and never all of them (a batch of one is never emptied). The rest keep their order.
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
the kitchen order (most engaging or newest first) and the six modifiers
that run today (freshness in hours, heat percent, posts per person with a
"Count Servers Too" switch for the server dimension, trending growth in
percent, "Favor Positive Posts" as a strength of 10 to 100 percent, written as
`sentiment_boost` 0.1 to 1.0, and "Group Similar Posts" with posts per group
and which post stays: most engaging, newest, or most typical), each an
expander with an enable switch. The grouping row needs numpy and kmedoids:
asked without importing them (`deps.unavailable_reason`), it is shown but not
editable, with the reason, when they are missing. Writing back keeps the modifier order, adds a newly
enabled one at the end, removes a switched-off one, and leaves unsupported
modifiers and values the reader did not touch as they were. Unsupported
ingredients (connections) are named in one plain row; sentiment, the
similarity grouping and the focus sort are no longer among them.

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
`reach_not_followed`, `viewer_hide`, `cluster_cap`, `heat_cut`, `author_cap`,
`instance_cap`, `guard:<promise>` (a promise of a post, or one about the page). The guard is the only stage that lists every promise a post
breaks (`removal.also`). Two deviations from the first plan: a post fetched
twice is one entry (the duplicate is an `also_in` and a count in
`summary.duplicates`, not a removal), and a post with no id is only counted
(`summary.no_id`). `summary.pantry_in == selected + sum(by_reason)` holds.

**Engine revision 3** (`audit.ENGINE_REV`) added, all optional in the schema so
records of revision 2 still load (they replay only with `--force`, as ever,
and evaluate refuses them for the same reason): `score.relevance` and
`score.fresh_floor`; per post `cluster: {id, medoid}` and
`facts.host` (the server the cap counts) and, for a recipe that reads words,
`facts.focus_sha256`; per page `cluster: {status, n, k, strategy, max_per, init,
seed, weights, loss}` (`status` is `ok`, `skipped` for fewer than ten posts, or
`unavailable` with a `reason`); `params.instance_counts_in`,
`outputs.instance_counts_out`; the reasons `cluster_cap` and `instance_cap`.
The sidecar gains `focus` (the words the relevance sort and the grouping read:
the body, the content warning and every alt text, 2,000 characters) for a
recipe with a focus or a grouping, because the visible text the contract
reads is not the text the TF-IDF reads; it is private like the rest. A
replay patches `textutil.focus_text` with it, sets the grouping's start, seed
and weights to the recorded ones, and turns the stagger off; a record whose
sidecar cannot give the whole focus text of every post is `skipped`, never a
false mismatch. Writing the log uses the one-shot C JSON encoder (the bytes
are the same, several times faster), which pays for the larger record: capture
costs 3.7 ms at p95 per 40-post page with grouping and focus recipes in the
fuzz.

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
tolerance: every kept post keeps every promise, the order is the recipe's (by
relevance when there is a focus, recomputed from the recorded keywords,
weights and words, and `score.relevance` must be the cosine), the grouping
kept what its strategy says from the recorded groups (`cluster`: the selection is checked against the groups the record
names; it is the replay that runs the clustering again and compares it), the
freshness factor and its floor are what the recipe says (`floor`, in both directions: raised with trending and only then), heat
removed exactly floor(P_used x n / 100) of what the grouping left, with P_used the first step from the requested one down that leaves 5 posts (or Cool), the Popular badge is on exactly the posts above the 90th percentile of the page's scores (`fire`, both directions; none when all scores are equal), the cap per
person and per server holds across the pages of a cook, the hashtags and the follow setting are respected, no post
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

**In the app.** The record is read in two places, both worded by
`src/feedchef/auditreport.py` (GTK-free: it takes the app's gettext and
ngettext as a `Words`, English by default), which `bench/feedchefaudit/show.py`
also uses, so the command line and the dialogs say the same thing.
`src/feedchefexplain.py` lays the rows out and holds no page, client or source.

- **Why This Post?** is an entry of the post menu of a recipe's timeline
  (`FeedChefTimelinePage.post_menu_entries`, access key W). It opens a dialog
  with "Where It Came From", "Checks It Passed" (each rule that applied and
  held, the follow verdict, the language and tone when they were judged) and
  "How It Was Ranked" (reactions, then freshness, positive tone and trending
  as multipliers when the recipe uses them, the score, and the position among
  the posts of its page). A post with no record (not in the pages kept by
  `FeedChefSource.audit_for`) shows "No Record for This Post". A removed
  post, which the dialog can only reach through a record, gets "Why It Was
  Removed" in place of the ranking; a guard drop is called "removed by a
  safety check".
- **Kitchen Report** is a button in the recipe page's header. It sums the
  pages of the last cook (`FeedChefSource.cook_audits`, which a refresh
  starts again): posts gathered, posts shown, each reason posts were left out
  for in plain words, and the recipe's settings as one line of counts and kinds
  (no names). Copy Report puts counts, verdicts, the recipe hash and dates on
  the clipboard, in English, and no post id, handle, name or text. Open Logs
  Folder opens the log folder with `Gtk.FileLauncher` (a portal when
  sandboxed). Clear Audit Logs asks first ("Clear Audit Logs?", Cancel by
  default) and then calls `decisionlog.clear_all()`, which deletes the
  history of every recipe on this machine. With no cook yet the dialog is a
  status page, "No Report Yet".

Test: `feedchef audit ui` (the real Recorder's record behind a fake engine).

## Heat levels, the fallback and the Popular badge (Phase 2)

Babble, not Java. Java's heat is a percentile threshold the builder sets with
a slider; the PRD asks for named levels and for a page that does not run dry.

- **Levels.** "Hide the Least Engaging" is an on/off switch with a toggle
  group Warm (50), Hot (75), Fire (90); off is the switch, not a level. A
  recipe with another percent (an imported one) selects no level, says
  "Imported value: 62.5%. Choose a level to replace it." and keeps the exact
  value until a level is picked. A percent is read one way everywhere
  (`heat.clean`: a number from 0 to 100, 0 when missing); an import with one
  outside 0 to 100 is refused (`validate_for_import`).
- **Fallback (the maintainer chose to show it).** When the cut would leave a
  page fewer than 5 posts, it steps down Fire, Hot, Warm, then to every post
  (an imported percent steps to the next lower level) until at least 5 remain.
  A page of fewer than 5 posts is therefore never cut. The page says so in a
  toast ("Too few posts for Fire. Showing Hot."), once per cook session. This
  replaces the parity port of `auto_fallback`'s heat rule. The live count of
  the builder falls back the same way.
- **Popular badge.** A post whose final score is above the 90th percentile of
  the scores of the posts the page shows (`heat.percentile`, exact between
  equal neighbours) gets a "Popular" chip with a flame icon (the first of
  `fire-symbolic`, `flame-symbolic`, `babble-trending-symbolic` the theme has,
  else text only); a page whose scores are all equal gets none. The chip is
  built lazily through a generic row hook (`rowdecor`: a page offers
  `row_decorations(status)`, `StatusRow.add_decoration_chip` places it). The
  audit records `score.fire`. The guard judges the badge against the
  threshold and the scores of the kitchen's whole page, so a post it drops
  later cannot make a false violation; `evaluate` recomputes the percentile
  from the kept posts alone.
- **Engine revision 4.** `ENGINE_REV` is 4: the record gained `heat` and
  `score.fire`, and heat and the badge changed meaning. A replay of a revision
  3 record says "made by engine revision 3" and stops.

## The builder and the recipe page (Phase 2)

- **Sentence.** `feedchef/sentence.py` writes the recipe as one sentence from
  whole translatable phrases in whole-sentence templates, one per source ("Posts
  from Home about #cats, without sensitive posts, most engaging first"); the
  phrases join with a template for each length up to four, and more settings end
  in "N more settings". It is a wrapped label at the top of the builder and the
  recipe list's subtitle, with "Chef: @handle" when the recipe has one.
- **Live count.** `feedchef/livecook.py` fetches the pantry (scope, included
  hashtags, More Like This) once on its own worker and cooks it again through
  the real engine, audit and decision log off, on every change after a 300 ms
  debounce; only a change of scope, list, hashtags, follow filter or focus
  fetches again. Each edit supersedes the request in flight. The old count
  stays, dimmed, "Updating…", until the new one arrives, is mirrored into the
  Preview Recipe row and announced; a failure is "Could not count posts" with a
  retry. **It is a view the reader asked for by editing, so it is not gated by
  `sysstate` (power saver, metered connection), unlike the peek cooks of Phase
  3.** Its cost is one pantry fetch per change of source settings.
- **Relax Filters.** The empty page offers to turn off the rule that removed
  most of the last cook's posts (the audit's `by_reason`, summed over the
  cook's pages; `feedchef/relax.py`), saves, cooks again, and shows an Undo
  toast. Undo works on the recipe as it is stored then (only that rule comes
  back, other edits stay), also when the page is gone, and never brings back a
  deleted recipe (it says so).
- **Recipe from the list.** A recipe opened from the Recipes list is pushed on
  the window's navigation stack (`open_recipe`), so the back button, Escape,
  Alt+Left and the swipe return to the list with its scroll kept
  (`scrollkeep`). It is its own page instance, not the cached place of a
  pinned sidebar entry (one widget cannot be a root place and a pushed page),
  so a recipe that is pinned and opened from the list has two pages and two
  engines while both exist; the pushed one is freed when the reader goes back,
  and goes by itself when Feed Chef is turned off. A pinned entry stays a
  top-level place, with the place cache, the stream router and `forget_many`
  as before.
- **Preview before import.** The import question has a Preview response: a
  preview of a copy of the incoming recipe (nothing stored, no audit record, no
  decision log, no follow list unless the recipe needs it), then the question
  comes back if the reader closed the preview and Feed Chef is still on.
- **Chef.** A share fills `meta.author` with the sharer's `@user@instance` when
  it is empty and never overwrites an author; the stored recipe is not changed.
  An imported name and chef are one line of plain text (no control or direction
  characters, 200 characters), and every place that shows them with markup
  escapes them. The import question reads "Chef: @handle" on its own line.
- **Share link and QR.** A link is `babble://feedchef?r=2.<blob>`: the recipe
  without the default recipe's values and without None, the id as the 22
  characters of its UUID, minimal JSON, raw deflate (level 9), base64url; the
  reader inflates at most 64 KiB, lays it over the default recipe and validates
  it as an import. The old `recipe=` links still read; an unknown `r=` version
  is not a recipe. The Share Recipe dialog (`feedchefshare.py`) shows the link,
  Copy Link, and a QR code made by segno on a worker when it opens (never
  before): black on white with a quiet zone of 4 modules, at least 3 pixels a
  module, nearest-pixel scaled, whatever the theme. A code above version 25 is
  not drawn ("This recipe is too large for a QR code. Copy the link instead.");
  without segno the dialog is the link and Copy Link.

Tests: `feedchef steps`, `feedchef live`, `feedchef experience`.

## Smart New Posts (Phase 3, Java section 12)

A recipe page asks again for page one while it is on screen, and offers the
result as a pill only when it is worth interrupting the reader. The decisions
are in `src/feedchef/smartmerge.py` (no widget); `src/feedchefpeek.py` holds
the two halves that touch the page (`SourcePeek` for `FeedChefSource`,
`PagePeek` for `FeedChefTimelinePage`).

- **Score cache.** An LRU (`collections.OrderedDict`) of
  `SCORE_CACHE_SIZE` = 100 final scores by post id in `FeedChefSource`,
  filled from every page that is shown (and from a peek once its posts are).
  The scores of posts still in the list are never evicted (`keep`: the
  source's set of shown ids); the LRU of 100 forgets only posts no longer
  shown, so the cache grows with the list. A merge whose window has a post
  with no score is refused (`merge_unscored`).
  The relevance to a focus is kept beside the score and leaves with it. A
  refresh clears the cache (a new recipe or focus makes old scores wrong).
- **Peek.** `FeedEngine.process_feed(..., peek=True, exclude=ids)`: a cook of
  page one on the engine worker (never the main thread), started with the
  cook session's person and server counts, so "Limit Posts per Person" and
  "Count Servers Too" keep holding across the merged list. The ids on screen
  are dropped from the sources before the pantry (a boost counts by the post
  it shows too), so they use no one's slot of the cap and the record names
  only what the peek could add. Audit outcome `peek`; `page_index` is
  `PEEK_PAGE_BASE` (1000) plus a serial, so a peek never shares the key of a
  page of the cook. A peek starts only when no cook is out; a refresh or an
  older page cancels one in flight (one engine, one worker). The result is
  held, one at a time: a newer peek replaces it and deletes its log (on the
  worker), and an older page landing or a refresh drops it (the counts moved,
  so it no longer fits). The pill count is the number of new posts the list would take
  (`TimelineModel.unseen`); a peek with none is dropped.
- **The timer is the poll timer of `TimelinePage`.** `_peek_sync` sets it to
  300 s (`PEEK_INTERVAL_S`) and `_on_auto_refresh` peeks instead of
  refreshing. It exists only while a recipe page is mapped and is the
  visible page of its navigation view (a page pushed over it covers it; the
  page follows `notify::visible-page`), the window is visible, Feed Chef is on
  (it follows `changed::feed-chef-enabled`), and the system is neither in power saver nor on a
  metered connection (read from `sysstate`; it is removed, not just ignored,
  and comes back on the same signals). Feed Chef off, a closed page, a hidden
  window, a page left or disposed: no timer at all (Principle 6).
- **Cost.** One request to the recipe's scope (plus a hashtag fetch for each
  included hashtag, which page one asks for as always) every five minutes for
  each open recipe page that is on screen, none while paused. Page one is
  never fetched more often than that, and the cook runs off the main thread.
- **When the pill shows.** The mean and the population standard deviation of
  the cached scores of the posts on screen (the rows the list reports as laid
  out inside the viewport; the top five rows when it has not been laid out),
  and the pill only if at least one new post scores above mean + 1 SD. With no
  visible post that has a score there is nothing to compare with and no pill
  (Java would use 0.0). Otherwise the result is held
  silently, and a refresh shows those posts in order anyway. The label is
  "N New Posts Worth Seeing" (`ngettext`).
- **The click.** The held posts go on top and only the first
  `RERANK_WINDOW_SIZE` = 50 posts are ordered again, by the recipe's own
  order rule (`kitchen.order_statuses`, so the v1 tie rules: newer first, then
  the larger id; relevance when the recipe has a focus whose posts all have it
  cached; the creation date for "Newest first"); a post with no cached score
  counts as 0.0, as in Java. The model applies it with
  `TimelineModel.merge_head`: one splice for each run of rows that changed (a
  post put on top and one in the middle are two), the same `StatusItem`
  objects for every row that stays, no reload. Then the page scrolls to the
  top (`TimelinePage` sets the value, with no animation to respect).
- **What the merge keeps.** A merge is checked before the model is touched
  (`merge_violations`): no post twice, every inserted id from the peek page's
  own selection, the same set of posts plus the inserted ones, only the first
  50 changed and in order, the rest unchanged. A merge that breaks one is
  refused and the peek dropped. Caps hold because the peek started from the
  session counts, which become the peek's counts on insertion; heat and the
  Popular badge are per page as recorded (the peek page's heat cut and fire
  ids; the fire ids join the session's on insertion), and the cluster cap is
  per page.
- **Audit.** A peek's record is `outcome.status` `peek`. When its posts are
  inserted it becomes `shown` (the page's own record, changed in memory on the
  main thread and written by the worker) and gains `merge`: `{window,
  inserted, head, order, keys}`, the ids put on top, the window after the
  merge and the value (relevance or score) each of its posts was ordered by.
  `bench/feedchefaudit` treats `peek` and `shown` like `ok` for every promise
  of the page, replays them (the sources are recorded after the shown ids
  were dropped, so the replay needs no exclusion), counts only `ok` and
  `shown` pages toward the cap of the cook (`audit.SHOWN`), and judges a
  merge with the new `merge` rule (`smartmerge.record_violations`: ids from
  the peek page, none twice, the window within its size, the keys never
  rising). The Kitchen Report counts a `shown` page and ignores a `peek`.
  Why This Post? says "It arrived later as a new post" for an inserted post.
  The file is rewritten by the worker from shallow copies the page makes on
  the main thread (`FeedDecisionLogger.writer()`), so the worker never sees
  the record change. A peek cancelled while it cooks, or one that lands after
  the page closed, has its log and sidecar deleted (on the worker; a shut-down
  engine runs the work on a short-lived thread instead of skipping it).
  **Known gap:** if the write that marks a peek `shown` is lost (quit before
  the worker ran), the log still says `peek` although its posts were shown.
  `evaluate_cook` reports it as a note when it can detect it: a later page of
  the cook starts from the counts the peek ended with. A merge into a list
  whose model groups boosts into a carousel is refused (the rows would not be
  the posts that were checked).
- **Tests.** `feedchef peek` (the cache, the threshold, the merge and its
  refusals, a real engine's peek record, replay and evaluation, the timer and
  every pause, the pill, the splices, the stale peek, a freed page) and the
  audit self-test (a shown peek, an inserted id from no peek page, a post
  twice, a rising order).
- **Decisions the plan left open.** The pill counts every new post, not only
  those above the threshold; the click inserts all of them. Peeks go through
  `process_feed` so they share the worker with the cooks (a cook always wins:
  it cancels a peek). A peek's log is kept only while it is the held one.

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, mashdon `sentiment_words.json` (replaced by VADER/LeIA),
freshness blend 0.4, synonyms and trigram expansion,
federated 8 h manual QA, mashdon `tools/` Python helpers, PRD-only pantry
window / `RequestMode` / `expected_outcome`.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, `statustext`
and `quoteline` (display-free). It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers the kitchen does not apply
(`connection`) show as inert in the builder/import UI, and one that cannot run
here (grouping without numpy and kmedoids) is marked `unavailable` with the
reason in the kitchen meta and the audit record, and not editable in the
builder. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
