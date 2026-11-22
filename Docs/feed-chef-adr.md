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
| Sentiment | `SimpleSentimentAnalyzer` + `sentiment_words.json` (~94 KB); often `0` if missing | present | **Vendored VADER (EN) + LeIA (pt*)** — MIT, no PyPI; activates `positive_only` / `sentiment_boost` (Babble upgrade, not Java JSON parity) |
| Server trends | deadlocked same executor | own executor / 2 s | **separate thread + 5 min memory cache**; never nest `submit`+`get` on that pool |
| Network reach | degree 1 only; `null` disables; author = **boosted** content account | BFS / null=∞ | **degree 1; null=off; boosted author** (= Java) |
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
   `pt-*` status language → LeIA; otherwise VADER. Scores are VADER
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
VADER (English and other languages) and LeIA (Portuguese). The dialog says
"works best in English and Portuguese" and names no engine.

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

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, mashdon `sentiment_words.json` (replaced by VADER/LeIA),
`focus_decay`, 450 ms search stagger, freshness blend 0.4 / floor 0.2,
federated 8 h manual QA, mashdon `tools/` Python helpers, PRD-only pantry
window / `RequestMode` / `expected_outcome`.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, and
`statustext`. It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers without an approved NLP gap show as
inert in the builder/import UI. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
