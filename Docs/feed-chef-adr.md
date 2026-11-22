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
| Pantry fetch shape | 1 scope + ≤3 tags if `maxId is None` + MLT search if focus; timeout `30+10*n`; partial | + window | **≈ Java fetch**; Babble `has_more` follows **scope page fullness** (not “single_task only”) so scope-oldest pagination works with tags/MLT |
| UI cursor | last id after kitchen sort | oldest of merged pantry | **oldest id of scope response** (deliberate fix) |
| Base score | fav·1 + reblog·2.5 + reply·1.5 on content | same | **= Java** (not Elk `rank()`) |
| `freshness` | `score *= factor` | blend 0.4 | **= Java** |
| `heat` / `diversity` | heat after scores, before structural; diversity pre-sort, author only | PRD order / instance | **= Java** |
| `similarity_cluster` / TF-IDF / synonyms | present | present | **inert + UI warning** (no `synonyms.json` 16 MB LGPL in the binary) |
| Sentiment | `SimpleSentimentAnalyzer` + `sentiment_words.json` (~94 KB); often `0` if missing | present | **Vendored VADER (EN) + LeIA (pt*)** — MIT, no PyPI; activates `positive_only` / `sentiment_boost` (Babble upgrade, not Java JSON parity) |
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
5. Sentiment is **vendored VADER + LeIA** (`src/feedchef/vendor/`, MIT
   NOTICE), not mashdon’s JSON lexicon and not a PyPI dependency. `pt` /
   `pt-*` status language → LeIA; otherwise VADER. Scores are VADER
   `compound` in [-1, 1].
6. Without TF-IDF, an active focus does **not** freeze merge order: kitchen
   falls back to `base_sort` (score or chronological). Relevance sort waits
   on an NLP gap.
7. Full Java builder (icon picker, GetLists picker, every modifier row) is
   a **v1 cut** — see below. Port later or not; do not claim parity.

## Builder v1 cut (item 4)

Shipped builder: name, pantry scope/list id/include/exclude, reach on/off +
max degrees, diet blocklist + hard media filters, `base_sort`, inert
warnings, save/share. **Not** in v1 UI: icon picker, live list picker,
`positive_only` toggle, min-length slider, reach include-unknown control,
per-modifier heat/similarity/diversity/sentiment/trending/freshness editors.
Defaults and imported JSON still carry Java modifiers; inert ones warn.

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, full builder parity, mashdon `sentiment_words.json` (replaced by
VADER/LeIA), `focus_decay`, 450 ms search stagger, freshness blend 0.4 /
floor 0.2, federated 8 h manual QA, mashdon `tools/` Python helpers,
PRD-only pantry window / `RequestMode` / `expected_outcome`.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, and
`statustext`. It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers without an approved NLP gap show as
inert in the builder/import UI. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
