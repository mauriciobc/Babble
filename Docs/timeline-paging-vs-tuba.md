# Timeline paging & cache: Babble vs Tuba

A structural comparison of how the two clients page a timeline and what they
cache, written to explain the stall reported on Babble's infinite scroll.

> **Status: acted on.** Fixes 1–4 of §7 are implemented — the bottom lock is a
> self-releasing debounce, the in-flight guard is held until the page is
> applied, follow-state is cached across pages, and the fill logic is one rule.
> The home timeline cache of §6 has also been removed, so §5's causal chain no
> longer applies; §1–§4 and §7 still describe the two designs accurately.

**Sources.** Babble: this tree, `0fdfc93` + the `imagecache.py` rework on
this branch, plus `Docs/timeline-pipeline.md`. Tuba: `/home/mauriciobc/Documentos/Code/Tuba`
at `02305ed0`. Every Tuba reference below was read from that checkout, not
inherited from a summary.

**Verdict up front.** The comparison confirms your hunch and sharpens it. The
home timeline cache did not make paging *slower* — it created the **only state in
which paging is switched off at all**, and that switch has no re-arm. Tuba is
structurally immune to this class of bug not because its pagination is better
tuned, but because having no timeline cache means the state never exists.

---

## 1. Pagination at a glance

| | **Babble** | **Tuba** |
|---|---|---|
| Cursor kind | id (`max_id` / `min_id`) | full URL from the `Link` header |
| Cursor provenance | `min(id)` over the **local model** | **verbatim from the response** |
| Where parsed | `PagedPosts.load_older` (`pages.py:205`) | `get_pages()` (`Timeline.vala:271`) |
| "Last page" known? | **inferred** — `model.exhausted` | **stated** — `is_last_page` (`Timeline.vala:291`) |
| Page size | always `limit=20` | `limit` **only on the first page** (`Timeline.vala:307`) |
| Append shape | `splice` on older, **N× `insert(0,…)`** on refresh | always **one `splice`** (`Timeline.vala:341`) |
| Stale-response defence | generation counter, drops the loser | cancels the previous request up front (`Timeline.vala:320`) |
| In-flight guard | `_loading_older`, coupled to the request | `bottom_reached_locked` + **1 s timer** |
| Guard can wedge? | **yes** — see §5 | **no** — see §3 |
| Filter placement | may **defer the whole page** behind a round trip | **synchronous per-item predicate** (`Timeline.vala:339`) |
| Fill-until-visible | three divergent copies of the logic | one `force_at_least_min` loop (`Timeline.vala:350`) |
| Timeline disk cache | **yes** — `homecache.py` | **no** |

---

## 2. The cursor: server-given URL vs locally-derived id

### Tuba

```
  Timeline.request()                              Timeline.vala:319
        req = new RequestV2 (get_req_url ())
        append_params_v2 (req)

  get_req_url()                                  Timeline.vala:294
        if (page_next != null) return page_next;   ◄── the URL the server
        return url;                                     handed us last time

  append_params_v2 (req)                         Timeline.vala:307
        if (page_next == null)
            req.add_parameter ("limit", …)         ◄── first page only;
                                                       later pages use the
                                                       URL's own query string

  get_pages (response_headers.get_one ("Link"))  Timeline.vala:271,344
        split on "," → per page: strip <> → take before ";"
        "rel=\"prev\"" → page_prev ;  else → page_next
        is_last_page = (page_prev != null && page_next == null)
```

### Babble

```
  PagedPosts.load_older()                        pages.py:205
        _request ({'limit': 20, 'max_id': self.model.oldest_id})

  model.oldest_id is a LOCAL DERIVATION:
        append_old   → min(id of the page)                timelinedata.py:413
        replace_all  → min(id of the new page)            timelinedata.py:447
        trim(400)    → min(id of what SURVIVED the trim)  timelinedata.py:533
        _fill_first  → min(id of the page)
        the all-filtered fallback → min(id of the RAW page)  timelinepage.py:627
        sorted by id_key()  (timelinebase.py:29) — ids are strings

  model.exhausted is INFERRED:
        append_old returns False when the page was empty,
        or when the cursor did not move back  (timelinedata.py:400-412)
```

### Why this matters

Tuba's cursor is **the server's own answer to "where does the next page begin"**,
captured from the response being paged from. It cannot be stale, cannot be
invalidated by a local edit, and needs no inference.

Babble's cursor is a **function of local model state**. Between the response
arriving and the cursor being written, the page passes through
`_filter_unseen` (boost dedupe), `group_boosts`, the reply filter, and — on the
cached first page — `replace_all`. Every one of those transforms changes which
items exist, and therefore changes the cursor. That is why `timelinepage.py:627`
has to reach for `raw_oldest`: when the reply filter hides every post in a page,
the filtered set has no minimum, and paging would otherwise stall.

**This is the direct cause of the `raw_oldest` plumbing**, which exists only
because the cursor is derived rather than given. Tuba has no equivalent because
`should_hide` filters *into* the array before the splice and never touches the
cursor.

---

## 3. The bottom-of-screen guard — the decisive difference

### Tuba: a debounce that cannot wedge

```
  ContentBase.vala:10      private bool bottom_reached_locked = false;

  ContentBase.vala:49      on_scrolled_vadjustment_value_change ()
        if (!bottom_reached_locked
            && value > upper - page_size * 2)          ◄── 2 viewport heights
        {
            bottom_reached_locked = true;
            on_bottom_reached ();
        }

  ContentBase.vala:158     on_bottom_reached ()                ◄── THE RE-ARM
        timeout = Timeout.add (1000, () => {
            bottom_reached_locked = false;
            GLib.Source.remove (timeout);
            return true;
        }, Priority.LOW);

  Timeline.vala:313        on_request_finish ()
        has_finished_request = true;
        base.on_bottom_reached ();       ◄── released as soon as the page lands
```

Two independent releases: **the 1 s timer** (fires whether or not the request
ever completed) and **`on_request_finish`** (fires as soon as the page is
spliced, so a fast response does not cost a full second of dead scroll).

The crucial property: **the lock is a timer, not request state.** Nothing the
network, the model, or the page lifecycle does can prevent that timeout from
firing. A cancelled request, a dropped response, a rebuilt tab, an account
switch — the lock releases itself in every case.

### Babble: a mutex on request state, plus a second, independent gate

```
  PagedPosts.load_older()                        pages.py:205
        if (self._loading_older
            or self.model.exhausted
            or self.model.oldest_id is None):
            return                              ◄── silent drop, no queue
        self._loading_older = True
        self._request (…)

  on_older_loaded()                             timelinepage.py:672
        if self._superseded (generation): return
        self._loading_older = False              ◄── cleared EARLY, see below
        …
        self._with_home_reply_filter (statuses, …)   ◄── may defer the page

  TimelinePage.load_older()                     timelinepage.py:300
        if self._showing_cache:
            return                              ◄── THE SECOND GATE
        PagedPosts.load_older (self)

  the only caller:                              timelinepage.py:739
        on_scroll_position (adjustment)
            if self.on_scroll_near_end (adjustment):
                self.load_older ()              ◄── trigger is DISCARDED,
                                                     never remembered
```

`_loading_older` is coupled to the request lifecycle: set on the way out,
cleared on the way in. It has no timer. And `_showing_cache` is a **second gate
that is not request state at all** — it is true from page construction until the
first live page is applied, and while it is true `load_older` returns without
recording anything.

---

## 4. Everything that flows from that

### 4.1 A dropped page costs a full round trip

```
  Tuba:    response ─► is_cancelled()? ─► parse ─► splice      (4 lines, adjacent)
  Babble:  response ─► generation guard ─► _finish ─►
           _with_home_reply_filter ─► [relationships_async ROUND TRIP] ─► apply
```

The check "is this response still wanted?" is 4 lines from the splice in Tuba.
In Babble it is separated from the splice by an optional network round trip.
That gap is exactly where a page gets dropped (`_superseded`,
`timelinepage.py:685`), and dropping it costs a full request — which is what
makes a stall feel like *seconds* rather than a hiccup.

### 4.2 The filter is synchronous in Tuba

```
  Tuba  Timeline.vala:425   should_hide (Entity entity)
            return status_entity.formal.tuba_filter_hidden;   ◄── a field
                                                                 computed
                                                                 during from_json
        Timeline.vala:337-340
            Network.parse_array (parser, node => {
                var e = Helper.Entity.from_json (node, accepts);
                if (!(should_hide (e))) to_add += e;           ◄── pure predicate
            });
```

A predicate over data already in hand. No network, no deferral, no cursor
damage. Babble's `_with_home_reply_filter` (`timelinepage.py:522`) may issue
`relationships_async` and defer the **entire page**, which is what opens the
window in §5.

### 4.3 One fill loop instead of three

Tuba has an explicit driver (`Timeline.vala:350`):

```
        if (force_at_least_min
            && (model.get_n_items () < batch_size_min   ◄── default 10
                || to_add.length == 0))
            on_bottom_reached ();
```

Babble encodes the same intent three times, with three different rules:
`timelinepage.py:623` (fully-filtered first page), `:647` (everything hidden),
`:696` (nothing new but the cursor moved). Each re-derives
`stack.set_visible_child_name` and each has its own edge cases.

---

## 5. The causal chain for your stall

```
  "scrolling to the bottom stalls for seconds"
        │
        └─ needs a window where the list is POPULATED but paging is DISABLED
              │
              └─ the only such window in Babble is the home timeline cache
                    (model filled by _show_cached, _showing_cache = True)
                    │
                    └─ the only code that disables paging in that window is
                          TimelinePage.load_older's `if _showing_cache: return`
                          │
                          └─ added by e02459c — the SAME commit that added the
                              cache
                                │
                                └─ and the trigger (on_scroll_position) discards
                                    the call instead of remembering it, so when
                                    _showing_cache clears nothing re-arms it
```

**Tuba cannot reach this state**, because `ContentBase.clear()` does
`model.remove_all()` and nothing is ever restored from disk. There is no window.

The two amplifiers from `Docs/timeline-pipeline.md` §15.3/§15.4 then stretch it:

- `on_older_loaded` clears `_loading_older` **before** the reply filter, so a
  scroll during that round trip starts a competing request that supersedes and
  discards the page in flight.
- `_note_followed_tags` fires `/relationships` on **every** page (no follow-state
  cache), and `apply_source_tags` then emits `'changed'` per match, each one a
  synchronous full `StatusRow.bind`.

---

## 6. Cache

| | **Babble** | **Tuba** |
|---|---|---|
| API response cache | none (libsoup session, no `Soup.Cache` feature) | `Soup.Cache`, 100 MB, `~/.cache/tuba/soup/misc` (`Network.vala:26`) |
| Media cache | decoded-texture LRU in memory: 256 avatars, 48 media (`timelinebase.py:54`) | `Soup.Cache`, 100 MB, `~/.cache/tuba/soup/media` (`Image.vala:31`) |
| **Timeline cache** | **`homecache.py` — first page to `~/.cache/babble/home/<key>.json`, `0700`/`0600`** | **none** |
| Entity cache | none | `EntityCache` / `ImageCache` / `BlurhashCache` — **all commented out** (`Application.vala:26-28`, `515-521`) |
| On restart | cached page painted immediately, swapped whole by the live page | `model` empty; first request or whatever `Soup.Cache` serves |
| On account switch | cache deleted with the login | `clear_cache()` only (`AccountStore.vala:89-90`) |

Two things worth noting:

1. **Tuba's `Soup.Cache` will not serve the timeline.** Mastodon sends no cache
   directives on API responses, so those 100 MB are spent on images and other
   cacheable resources, not on timeline JSON. Your `[INFERENCE]` is right.

2. **Tuba once had entity caches and removed them.** `Application.vala:26-28`
   shows them commented out with only `clear_cache()` remaining. So Tuba's lack
   of a timeline cache is a **deliberate removal**, not an absence — which makes
   "Tuba is faster at paging" a comparison against a design decision rather than
   against a more capable implementation.

### What the cache buys, and what it costs

The cache is a real win, and the commit's own numbers are good [M, `e02459c`]:
first card at **949–1,198 ms** against **2,164–3,418 ms**, against a real instance
230 ms away. That is the single largest first-load improvement in the project.

The cost is the architectural one: it introduces a state where the list is
populated but the network is not yet authoritative — a state Tuba never has and
whose handling Babble got wrong. **The cache is not the bug; the paging gate that
had to be bolted on beside it is.**

---

## 7. What to borrow

### Borrow now — small, and fixes the reported symptom on its own

**1. A self-healing bottom lock.** This is the highest-value change available and
it is independent of the cursor design.

```
  _bottom_locked = False          # replaces _loading_older as the primary gate

  load_older():
      if _bottom_locked or model.exhausted or model.oldest_id is None:
          self._older_pending = True      # ◄── REMEMBER, don't discard
          return
      _bottom_locked = True
      GLib.timeout_add (1000, _unlock_bottom)       # ◄── Tuba's re-arm
      _request (…)

  _unlock_bottom(): _bottom_locked = False
                    if _older_pending: _older_pending = False; load_older()
                    return SOURCE_REMOVE

  on_older_loaded / on_older_failed:  _unlock_bottom()   # prompt release
```

Three properties this buys over the current guard:

- it **releases even when the page was dropped**, so a superseded response cannot
  cost a stuck scroll;
- it **remembers** a trigger that arrived during the cache window instead of
  dropping it (§5);
- it has a **deadline**, so no combination of states can wedge it.

**2. Hold `_loading_older` until the page is applied.** Clearing it before the
reply filter (timelinepage.py:675) is the specific defect; move the clear into
`_finish_older_loaded`, or replace it with the lock above.

**3. Cache follow-state.** One `_following_ids` consulted by both the reply
filter and `_apply_followed_tags` removes the per-page `/relationships` request
and the rebuild wave it causes (§15.4 in the pipeline doc).

**4. One fill loop.** Collapse the three copies into a single
`force_at_least_min`-style driver.

### Do not borrow yet

**Link-header paging for the home timeline.** The cursor quality argument is real
— a server-given URL is strictly better than a locally-derived id — but Babble's
model is not Tuba's:

| Tuba's model | Babble's model |
|---|---|
| append-only, one `splice` | `insert_new` **prepends**; refresh and streaming mutate the head |
| never drops items | `trim(400)` **drops the tail** and re-pages from the new `oldest_id` (`timelinedata.py:533`) |
| filter runs before the splice | filter can run **after**, and can empty the page |
| one page source | `replace_all` / `insert_new` / `append_old` / stream all write the cursor |

With `trim()` in the picture, a Link cursor **cannot be moved back**: once the
model has dropped posts, the `page_next` captured for the last response points
below content the reader has lost and above content the app no longer holds.
Babble already knows this — it is exactly why `ApiTimelineSource(paging='link')`
exists for bookmarks and favourites and why `_trim_old` refuses to trim a
non-`id`-paged source (`timelinepage.py:663`).

Moving the home timeline to Link paging would mean `trim()` could no longer
re-fetch dropped posts, and `replace_all` and every streamed insert would have to
invalidate `page_next`. That is a large change carrying a real functional
regression, and **it does not address the actual bug** — the bug is the dropped
trigger and the early `_loading_older` clear, both of which are orthogonal to
where the cursor comes from.

**Tuba's `is_last_page` as written.** `page_prev != null && page_next == null`
(Timeline.vala:291) is a statement about the shape of the header. Babble's
`exhausted` is a statement about whether the server has more, and Babble's own
link-paged source already has the honest version (`_next is None` only after an
empty page). Keep Babble's semantics if the link path is ever widened.

**Tuba's cancel-previous-request strategy** (`Timeline.vala:320`). It loses less
work than Babble's generation guard, but Babble's guard is the more principled
choice here: a streamed insert must not be clobbered by a racing older page, and
cancelling the in-flight request would throw away that page entirely. Keep the
guard; just make sure a dropped page cannot wedge the scroll.

---

## 8. Summary

```
  Tuba   server-given cursor  +  timer-based lock  +  synchronous filter
                             +  no timeline cache
                          =  no state in which paging can be switched off

  Babble locally-derived cursor  +  request-state lock  +  deferring filter
                             +  timeline cache
                          =  one such state, entered on every launch,
                             with no timer and no re-arm
```

The reported stall is not a tuning problem in the paging hot path. It is one
missing guard in one place: a trigger that was discarded instead of remembered,
and a lock that is coupled to a request instead of to a deadline.

Fixes 1–4 in §7 are roughly 60 lines and address it without touching the cursor
design or giving up the first-load win the cache bought.