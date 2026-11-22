# ADR — Feed Chef v1 (Babble)

Status: accepted for implementation. Behaviour source of truth is mashdon
`master` @ `23277cd1ca40f6fe618c4f246896fd80777bf4ea`, not the PRD where they
diverge. Adversarial review:
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
| Pantry | 1 scope + ≤3 tags if `maxId is None` + MLT if focus; timeout `30+10*n`; partial | + window | **= Java** |
| UI cursor | last id after kitchen sort | oldest of merged pantry | **oldest id of scope response** (deliberate fix) |
| Base score | fav·1 + reblog·2.5 + reply·1.5 on content | same | **= Java** (not Elk `rank()`) |
| `freshness` | `score *= factor` | blend 0.4 | **= Java** |
| `heat` / `diversity` | heat after scores, before structural; diversity pre-sort, author only | PRD order / instance | **= Java** |
| `similarity_cluster` / TF-IDF / sentiment / synonyms | present | present | **gap GO or inert + UI warning** |
| Server trends | deadlocked same executor | own executor / 2 s | **separate thread + 5 min memory cache** (fix) |
| Network reach | degree 1 only; `null` disables | BFS / null=∞ | **degree 1; null=off** |
| More Like This | same timeline, inside pantry | dedicated fragment | **= Java** |
| Engine lifetime | `new` per view | factory singleton | **= Java** (no singleton) |

## Explicit Babble deviations

1. Pagination cursor = oldest status id from the **scope** fetch, not the
   last kitchen-sorted row and not the merged-pantry oldest id.
2. Server trend fetch runs on a dedicated worker; pipeline never
   `submit`+`get` on the same single-thread executor.
3. `babble://feedchef?recipe=` is handled in `BabbleApplication.on_open`
   before OAuth; Base64 URL-safe with padding restored; import confirms
   replace when the recipe id already exists.
4. Fuzzy synonyms stay **off** in v1 (no 16 MB LGPL `synonyms.json` in the
   binary) until a separate licence/size ADR.

## Out of v1

`RequestMode`, federated time window, `LocalPostRepository` in the engine,
`FeedEngineFactory`, `expected_outcome`, BFS degree ≥ 2, dedicated MLT
fragment, `focus_decay`, 450 ms search stagger, freshness blend 0.4 / floor
0.2, federated 8 h manual QA, mashdon `tools/` Python helpers.

## Module boundary

Package `src/feedchef/` may use the standard library, `api`, and
`statustext`. It must not import Catch Up UI/fetch/store/tiles,
`navigation`, or `catchup.rank*`. Catch Up must not import `feedchef`.
Enforced by prefix rules in `testkit/moduleboundary.py`.

## Kitchen policy

Silent no-ops are forbidden. Modifiers without an approved NLP gap show as
inert in the builder/import UI. Elk `rank()` / DPP / `affinity` are not
kitchen implementations.
