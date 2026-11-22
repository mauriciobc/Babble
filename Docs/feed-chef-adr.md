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
| `heat` / `diversity` | heat after scores, before structural; diversity pre-sort, author only | PRD order / instance | **= Java** |
| `similarity_cluster` / TF-IDF / synonyms | present | present | **inert + UI warning** (no `synonyms.json` 16 MB LGPL in the binary) |
| Sentiment lexicon | `res/raw/sentiment_words.json` (separate from synonyms) | present | **still inert in v1** until a licence/size ADR for that file; synonyms reason does not block a future sentiment-only gap |
| Server trends | deadlocked same executor | own executor / 2 s | **separate thread + 5 min memory cache**; never nest `submit`+`get` on that pool |
| Network reach | degree 1 only; `null` disables; author = **boosted** content account | BFS / null=∞ | **degree 1; null=off; boosted author** (= Java) |
| More Like This | same timeline pantry when focus is set; UI can set focus | dedicated fragment | **pantry MLT = Java**; focus via import **or** timeline “More like this” (no dedicated fragment) |
| Builder controls | full (icon, lists, diet/kitchen sliders, modifiers) | full | **thinned v1 cut** (documented below); not “= Java” |
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
   Sentiment words are a **separate** mashdon asset; leaving them out is a
   deliberate v1 cut, not implied by the synonyms licence argument.
5. Without TF-IDF, an active focus does **not** freeze merge order: kitchen
   falls back to `base_sort` (score or chronological). Relevance sort waits
   on an NLP gap.
6. Full Java builder (icon picker, GetLists picker, every modifier row) is
   a **v1 cut** — see below. Port later or not; do not claim parity.
7. The scope cursor is `pantry.scope_cursor`: the smallest **wrapper** id
   (integers when all are digits, else the last item), because Mastodon
   pages by the timeline entry id and a boost carries the old date of the
   boosted post. `oldest_id()` stays the mirror of Java's `next_page_max_id`.
8. A recipes file that cannot be read (or is not a JSON list) is renamed to
   `<name>.corrupt-<unix-ts>` (mode 0600) before a save or delete writes;
   Java wipes it. `load_recipes` stays read-only.
9. More Like This is per post (an entry in the post's overflow menu, added
   through the generic `page.` hook of `statusmenu`), not on the top post.
   Keywords are the top 5 TF-IDF terms of the post against the current
   timeline (the post alone when empty); accents are stripped and the 39
   Java stopwords apply. The relevance **sort** is still inert (item 5).
10. The timeline is a `TimelinePage` over `feedchefsource.FeedChefSource`.
    Its `RequestScope` supersedes a request in flight (the source answers
    CANCELLED and stops the engine), so no request gate of its own is kept.
    Older pages use the scope cursor of the last result, not TimelinePage's
    id cursor. A refresh cooks again and replaces the list in kitchen order.

## Builder v1 cut (item 4)

The builder is an `Adw.Dialog` (`feedchefbuilder.py`) that edits a copy of
the recipe: Cancel discards it, Save stores it, and a new recipe is not
stored before Save. It edits: name; pantry source (Home, Local, Federated or
one of the account's lists, loaded through the lists API), include and
exclude hashtags, and "Only People I Follow" (network reach on, one degree,
or off; an imported recipe's other degree count is kept until the switch is
changed); diet blocklist, media, alt text and sensitive-media filters and
the minimum length; the kitchen order (most engaging or newest first) and
the four modifiers that run without NLP (freshness in hours, heat percent,
posts per person, trending growth), each an expander with an enable switch.
Writing back keeps the modifier order, adds a newly enabled one at the end,
removes a switched-off one, and leaves unsupported modifiers and values the
reader did not touch as they were. Unsupported ingredients (similarity,
sentiment, connections, positive only, focus keyword sorting) are named in
one plain row. "Copy Share Link" sits in its own group. **Not** in the UI:
icon picker, reach include-unknown control, fetch strategy, focus weights.

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, full builder parity, sentiment lexicon port, `focus_decay`,
450 ms search stagger, freshness blend 0.4 / floor 0.2, federated 8 h
manual QA, mashdon `tools/` Python helpers, PRD-only pantry window /
`RequestMode` / `expected_outcome`.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, and
`statustext`. It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers without an approved NLP gap show as
inert in the builder/import UI. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
