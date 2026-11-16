# The timeline pipeline, ingestion to pixels

> **Superseded in part.** This documents the tree at `0fdfc93`. The home
> timeline cache (`homecache.py`, §3.2, §12.1) has since been removed, along
> with the `_showing_cache` paging gate that came with it. The paging stage
> (§4) has also been reworked: `load_older` is now behind a self-releasing
> debounce rather than a request-state mutex. Sections 3.2, 12.1, 15.2, 15.3
> and 15.5 describe behaviour that no longer exists; everything else stands.
> See `Docs/timeline-paging-vs-tuba.md` for the reasoning.
>
> Stale reads in flight are now owned by `RequestScope` tickets rather than a
> generation counter (§4, §4.1). The `file.py:NNN` references were re-checked
> against the current tree; passages about code that no longer exists (the
> cache, `_showing_cache`, the `load_older` override, `replace_all`) are marked
> *removed* in place and carry no line number.

A complete map of how a post travels from the network to a painted card in
Babble: every stage, every hand-off, which thread each runs on, what it costs,
and where it can stall.

This is a reading of the tree at `0fdfc93` plus the `imagecache.py` rework on
this branch. Everything below is traceable to `file:line`. Claims that are *measured*
are marked **[M]** and cite `PERFORMANCE.md` or a commit message; claims that
are *inferred from the code* are marked **[R]** (read) and are the things worth
instrumenting before acting on.

---

## 1. The ten stages

```
                            ┌──────────────────────────────────────────┐
                            │ 0. SESSION            window.py:446      │
                            │    keyring ──► client ──► show_shell()   │
                            └───────────────────┬──────────────────────┘
                                                │ client + account
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 1. PAGE CONSTRUCTION              timelinepage.py:168  TimelinePage.__init__   │
  │    RunModel ─► Gtk.ListView ─► ClampScrollable ─► ScrolledWindow ─► Overlay     │
  │    ─► ensure_loaded():  load()          (the cached first paint was removed)    │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 2. REQUEST                pages.py:281  PagedPosts._request                     │
  │    scope.begin() ─► TimelinePage._paging_fetch ─► mastodon.request_async         │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 3. TRANSPORT                mastodon.py:810 / :618 / :736   [worker + main]   │
  │    Soup.Session.send_and_read_async ─► retry/429 ─► envelope {"data","link"}     │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 4. RESPONSE DISPATCH        timelinepage.py:434  on_page_loaded                │
  │    on_older_loaded:509   /   on_refresh_loaded:568                              │
  │    Ticket.callback ─► wanted() guard, then _finish()                            │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 5. REPLY FILTER             homefilters.py:56    _with_home_reply_filter       │
  │    optional GET /accounts/relationships ─► defer or pass through                │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 6. MODEL                    timelinedata.py:288  TimelineModel (Gio.ListModel) │
  │    filter_unseen ─► group_boosts ─► StatusItem ─► ListStore.splice              │
  │    cursors: oldest_id / newest_id / exhausted / loading                         │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ items-changed(0, n, 20)
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 7. RUN GROUPING             statusrun.py:525  RunModel (Gio.ListModel)          │
  │    regroup whole model ─► StatusRun ─► minimal items_changed(start,rm,add)     │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ items-changed
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 8. LIST VIEW                timelinepage.py:37  TimelineFactory                │
  │    Gtk.ListView on_setup ─► StatusRunRow()   (shell, empty)                    │
  │    Gtk.ListView on_bind  ─► StatusRunRow.bind(run, client)                      │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ map
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 9. SHELL + BUILD THROTTLE    statusrun.py:214  StatusRunRow                      │
  │    _on_map ─► _queue_populate ─► _pending_shells ─► _populate_pending (idle)    │
  │    ONE card build per main-loop dispatch, at PRIORITY_DEFAULT_IDLE              │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 10. CARD                    statusrow.py:36  StatusRow (Gtk.Overlay)           │
  │    bind ─► _bind_status ─► ~15 subsystems, several lazily built                 │
  │    ├─ avatars  mediacache.AvatarCache ─► imagecache.Fetcher                     │
  │    ├─ body     parse_content ─► InlineEmojiText.set_segments                     │
  │    ├─ media    MediaBox.set_media ─► _TextureCache.load ─► Fetcher              │
  │    └─ actions  StatusActions.bind / StatusMenuButton.bind                       │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ PAINT                        GSK/Renderer, frame clock, snapshot                │
  └─────────────────────────────────────────────────────────────────────────────────┘
```

**The one-sentence version:** a page arrives as JSON, becomes typed dicts, then
`StatusItem`s, then `StatusRun`s (runs of same-thread posts by one author), then
`StatusRunRow` shells, then `StatusRow` cards, then pixels — with the card build
deferred one-per-frame on purpose, and every image arriving later over its own
async fetch.

---

## 2. Stage 0 — Session

```
  window.py:446  restore_account()
        │
        ├─► account_store.active_account(settings)          (GSettings, sync)
        │
        ▼
  window.py:459  _activate_account(account)
        │
        ├─ MastodonClient(account['instance'])              mastodon.py:569
        │     └─ Soup.Session(), set_timeout(30)            mastodon.py:597
        │        HTTP_TIMEOUT_SECONDS = 30                  mastodon.py:62
        │
        ├─ self._session_lookup += 1     (stale-lookup guard, window.py:471)
        │
        └─ client.load_secret_token(id, loaded)             mastodon.py:1096
                  │
                  ├─ Gio.Task.new(...)
                  └─ task.run_in_thread(work)      ◄── WORKER THREAD
                          Secret.password_lookup_sync(SECRET_SCHEMA, attrs, token)
                  │
                  ▼  (back on the main context)
             loaded(token, error)
                  ├─ lookup != self._session_lookup ──► DROP   (window.py:474)
                  ├─ error  ──► login screen + toast   (window.py:488)
                  ├─ None   ──► remove_account / toast  (window.py:500)
                  └─ token  ──► client.token = token; self.client = client
                                ─► show_shell()           (window.py:517)
```

**Why it is on a thread [M]:** commit `e02459c` measured the keyring read as
async D-Bus hops that each queued behind a first-frame redraw — token arriving
133–581 ms after `present()` for a 7 ms lookup. Moving it to a worker thread
brought it to 46–101 ms. `src/test-streaming.py` pins that the read runs on a
worker thread, answers on the main one, and does not wait on `Secret.Service.get`.

**Stall surface [R]:** none on the main loop any more. `Secret.password_lookup_sync`
blocks only the worker. But **everything downstream is gated on this callback** —
no timeline page exists until it fires. A wedged keyring is an indefinitely blank
window, not a slow one.

---

## 3. Stage 1 — Page construction

`window.py:813 show_shell()` builds the sidebar and both main pages *before*
flipping `main_stack` to `'shell'` (window.py:834), so everything in stage 1 is
on the critical path to the window appearing at all.

```
  ensure_home_timeline()                             window.py:1102
        │
        ├─ if timeline_page exists and .client is self.client ──► reuse (no work)
        │
        └─ TimelinePage(client, mode='home',
                        model=self.timeline_model)        timelinepage.py:168
                                                          ◄── reused GListModel
                 │
                 │  ── widget tree ──
                 ├─ self.model        = TimelineModel()  or the reused one
                 ├─ RunModel(self.model)                     statusrun.py:525
                 ├─ Gtk.ListView(model=NoSelection(run_model),
                 │               factory=TimelineFactory(client, filter_ctx))
                 │        filter_ctx = filter_context_for(source)  :59
                 ├─ Adw.ClampScrollable(maximum_size=660)      ◄── the column
                 ├─ Gtk.ScrolledWindow(vexpand, h-policy=NEVER)
                 ├─ new_posts_revealer + new_pill  ("N new posts")
                 ├─ Gtk.Overlay { scrolled, revealer }
                 ├─ Gtk.Stack { 'list', 'loading', 'error', 'empty' }
                 ├─ _RefreshGesture(self, scrolled)             :716
                 └─ if autoload: ensure_loaded()                :293
                          │
                          └─ load()          ──► stage 2
```

### 3.1 The filter context is decided by the path [R]

`filter_context_for(source)` (timelinepage.py:59) inspects `source.path`:

| `source.path` prefix | context |
|---|---|
| `/accounts/` | `filterrules.ACCOUNT` |
| `/timelines/public`, `/timelines/tag/`, `/timelines/link` | `filterrules.PUBLIC` |
| anything else (home) | `filterrules.HOME` |

The home page passes `source=None`, so `.path` is `''` and it lands on `HOME`.
The context is fixed once, at construction, and stamped onto every row the factory
makes — `StatusRow.filter_context` — which is what `status_filter` is later judged
against. It is a *view* property, so it never changes per item.

### 3.2 The cached first paint (stage 1a) — removed

Commit `e02459c` added a disk cache of the home timeline's first page
(`homecache.py`, `TimelinePage._show_cached`, the `_showing_cache` flag that
gated paging, `TimelineModel.replace_all`). `286a7b7` hardened its failure
edges. `2fffbb7` reverted it ("Revert the home cache and let paging release
itself"): none of those names exists in `src/` any more. `ensure_loaded()` now
calls `load()` directly, `TimelinePage` does not override `load_older`, and
`PagedPosts.load_older` is gated only by the bottom lock and `model.exhausted`.
`TimelinePage._fill_first` (timelinepage.py:300) survives, now as the helper
that puts a first page in an empty model and sets both cursors.

---

## 4. Stage 2 — The paging state machine

`pages.py:156 PagedPosts` is the one state machine; `TimelinePage` overrides the
transport (`_paging_fetch`/`_finish`) and adds the reply filter.

```
                          ┌────────────────────────────────────────┐
                          │  model.loading      (a page is in)     │
                          │  _loading_older     (an older page is) │
                          │  model.exhausted    (no more pages)    │
                          │  model.oldest_id    (backward cursor)  │
                          │  model.newest_id    (forward cursor)   │
                          │  _ticket            (newest request)   │
                          │  _bottom_locked     (older-page debounce)│
                          └────────────────────────────────────────┘

  load()                    pages.py:318
    if _scope.closed or model.loading: RETURN   ◄── silent drop, no queue
    model.loading = True
    if n_items == 0: stack -> 'loading'
    _request({'limit': 20}, on_page_loaded,
             on_drop=_drop_loading, owns_loading=True)

  load_older()               pages.py:330
    if _scope.closed: RETURN                    ◄── a disposed page starts nothing
    if model.exhausted or oldest_id is None: RETURN
    if _bottom_locked: _older_pending = True; RETURN   ◄── remembered, not lost
    _lock_bottom()                              ◄── debounce, self-releasing
    _loading_older = True
    _request({'limit': 20, 'max_id': oldest_id}, on_older_loaded,
             on_drop=_drop_older)

  refresh()                 pages.py:395
    if _scope.closed or model.loading: RETURN
    if n_items == 0: load(); return
    model.exhausted = False
    model.loading = True
    _refresh_ticket = None                      ◄── disown the previous refresh
    ticket = _begin_request(True)
    _refresh_ticket = ticket                    ◄── BEFORE the send: an answer
    _send_request(ticket, {'limit': 20,             delivered synchronously (a
                  'since_id': newest_id},           test's fake) must already
                  on_refresh_loaded, _drop_refresh) see it as the owner

  _request(params, on_done, *, on_drop, owns_loading=False)   pages.py:281
    ticket = _begin_request(owns_loading)
    _send_request(ticket, params, on_done, on_drop)
    return ticket

  _begin_request(owns_loading)                                pages.py:296
    ticket = self._scope.begin()          # supersedes and cancels the last
    self._ticket = ticket                 # the newest request, the only
    self._ticket_owns_loading = owns_loading     # one that can be wanted

  _send_request(ticket, params, on_done, on_drop)             pages.py:307
    _paging_fetch(params, ticket.callback(on_done, on_drop=on_drop),
                  ticket.cancellable)
```

`on_drop` is always a function of the owner (`PagedPosts._drop_*`), never a
bound method: the ticket holds the owner weakly. A first page and a refresh
own `model.loading` (`owns_loading=True`); an older page does not.

`TimelinePage` no longer overrides `load_older`. The override that `e02459c`
added (`if self._showing_cache: return`, a silent no-op while a cached page was
on screen) went with the cache (§3.2). What replaced the old request-state gate
is the bottom lock: `_lock_bottom()` holds a debounce that is released when the
page lands, when the request is dropped (`_drop_older`), or by a deadline
(`BOTTOM_LOCK_MS`), whichever comes first, and a scroll that arrives while it is
held is remembered in `_older_pending` and replayed, not discarded.

`_drop_older` leaves the flag and the lock alone when a newer older request is
live: a stale older request cancelled by a reload must not end the live one.

### 4.1 The ticket guard

Every request holds a `RequestScope` ticket. `Ticket.callback` asks `wanted()`
before the handler runs and, for an answer whose request was overtaken, calls
the request's `on_drop` instead, which clears the busy flags (unless a newer
request owns them) so a discarded page cannot leave the list reading as loading
forever:

```
  Ticket.callback(client, result)
      if not ticket.wanted():
          on_drop(owner)             ◄── page silently discarded
          return
```

Because `_request` calls `begin()` on **every** request, any two overlapping
requests on one page make the older one dead on arrival. This is the
mechanism behind several hazards in §11.

### 4.2 Cursor discipline [R]

| Cursor | Set where | Meaning |
|---|---|---|
| `oldest_id` | `append_old`, `trim`, `_fill_first`, the fully-filtered fallback at timelinepage.py:466 | `max_id` for the next older page |
| `newest_id` | `append_old`, `insert_new` | `since_id` for the next refresh |
| `exhausted` | `append_old` (empty page), `trim`/`refresh` (cleared) | the server has nothing older |

`id_key()` (timelinebase.py:29) is the sort key, because Mastodon ids are strings
"cast from an integer" and a plain string compare orders `'99999999'` after
`'100000001'`.

---

## 5. Stage 3 — Transport

```
  TimelinePage._paging_fetch()                  timelinepage.py:410
        │
        ├─ source is not None  ─► source.fetch(params, cb)     ApiTimelineSource
        ├─ mode == 'public'    ─► api_timelines.public_async
        └─ else (home)         ─► api_timelines.home_async
                │
                ▼
  mastodon._request_async(method, path, ...)    mastodon.py:810
        ├─ task = Gio.Task.new(self, cancellable, callback)
        ├─ task._rest_spec  = (method, path, params, form, auth, json_body)
        ├─ task._rest_attempt = 0
        ├─ message = _build_message(...)                       mastodon.py:663
        │     ├─ uri = instance + /api/v1 + path
        │     ├─ params urlencode'd (doseq)
        │     ├─ Authorization: Bearer <token>  when auth and self.token
        │     └─ body from json_body / form
        └─ _spawn(task, message)                              mastodon.py:618
              session.send_and_read_async(message, PRIORITY_DEFAULT,
                                          cancellable, _on_response, task)
                │
                ▼  (libsoup I/O thread; main loop free)
  _on_response(_session, result, task)          mastodon.py:736
        ├─ send_and_read_finish()
        │    ├─ CANCELLED          ─► return_error, no retry    :675
        │    └─ other transport err ─► _maybe_retry(transport_error=True)
        ├─ json.loads(body)                              ◄── on main thread
        ├─ status = message.get_status()
        ├─ 2xx ─► return_value(Variant('s', {"data":…, "link":…}))
        └─ else
             ├─ 429 ─► _note_rate_limit(headers) ─► return_error   :776
             └─ other ─► _maybe_retry() ─► return_error(summarize_error())
```

**The envelope [R]:** every successful body is re-serialised as
`{"data": <parsed>, "link": <Link header>}`. `request_finish` /
`request_finish_paged` (mastodon.py:841, :845) parse that string back and lift the
payload with `entities.lift`. Wrapping the `Link` header this way is what lets the
generic `_finish` return `(data, next_params, prev_params)` for both cursor styles.

**Retry [R]:** `_maybe_retry` (mastodon.py:718) retries only what
`rest_should_retry` (mastodon.py:119) allows, at most `REST_RETRY_MAX = 3`
(mastodon.py:84) times, with a `rest_backoff_seconds` (mastodon.py:133) delay via
`GLib.timeout_add`. Unit-test fakes carry no `_rest_spec` and therefore fail
straight through.

**Rate limiting [R]:** a 429 records `self._rate_limited_until` from
`Retry-After` or `X-RateLimit-Reset` (corrected by the response's own `Date`
header, `server_clock_offset`). `client.rate_limited()` is polled by
`_on_auto_refresh` (timelinepage.py:340), `_note_followed_tags` (timelinetags.py:32) and
`_apply_auto_refresh` — the app refuses to deepen a limit the server just imposed.

**Timeouts:** 30 s on both sessions (`mastodon.py:62`, `imagecache.py:60`).
The comment at imagecache.py:57 records why: libsoup defaults to zero, meaning
"wait forever".

---

## 6. Stage 4 — Response dispatch

```
  (Ticket.callback has already asked ticket.wanted(): an unwanted answer
   went to its on_drop and never reaches a handler below.)

  on_page_loaded(client, result)                      timelinepage.py:434
        ├─ ticket = self._ticket        (the newest, wanted one)
        ├─ statuses = self._finish(result)             (GLib.Error ⇒ error page)
        │     └─ error                  ─► stack 'error'  + Try Again
        ├─ raw_oldest = min(ids)          ◄── cursor survives an all-filtered page
        └─ _with_home_reply_filter(statuses,
                        lambda f: _finish_page_loaded(f, ticket, raw_oldest))
  _finish_page_loaded(statuses, ticket, raw_oldest)   timelinepage.py:459
        └─ not ticket.wanted() ─► _drop_loading(self); RETURN

  on_older_loaded(client, result)                     timelinepage.py:509
        ├─ ticket = self._ticket
        ├─ _loading_older = False      ◄── CLEARED BEFORE THE FILTER (see §11.4)
        ├─ statuses = self._finish(result)
        └─ _with_home_reply_filter(statuses,
                        lambda f: _finish_older_loaded(f, ticket, before=n))
  _finish_older_loaded(statuses, ticket, before, ...) timelinepage.py:540
        └─ not ticket.wanted() ─► _drop_older(self); RETURN

  on_refresh_loaded(client, result)                   timelinepage.py:568
        ├─ ticket = self._ticket
        ├─ model.loading = False
        ├─ statuses = self._finish(result)
        ├─ empty + n_items == 0 ─► stack 'empty'
        └─ _with_home_reply_filter(..., _finish_refresh_loaded)
  _finish_refresh_loaded(..., ticket)                 timelinepage.py:597
        └─ ticket is not self._refresh_ticket ─► RETURN   (a newer refresh
           owns the chain; a load_older that cancelled this ticket leaves it
           in place, so the page still lands)
```

---

## 7. Stage 5 — The reply filter

Only when `hide-replies-non-follows` is set **and** the page is the plain home
timeline (`source is None`).

```
  _with_home_reply_filter(statuses, on_done)           homefilters.py:56
        │
        ├─ not enabled ─────────────────────────────────► on_done(statuses)   [sync]
        │
        ├─ collect author_ids of replies not already in self._following_ids
        ├─ unknown empty ─► filter_replies_from_non_follows(...)  [sync]
        │
        └─ unknown non-empty ─► api.accounts.relationships_async(client, ids[:40], loaded)
                                     │
                                     ▼   ◄── EXTRA ROUND TRIP before any paint
                                  loaded(client, result)
                                     ├─ GLib.Error ─► filter with what we have
                                     └─ merge following ids into _following_ids
                                        on_done(filtered)

  NOTE: an earlier version of `loaded` compared a `_reply_filter_generation`
  counter with a snapshot of itself. Nothing ever incremented it, so the
  guard could not fire, and it has been removed (see 15.7). A superseded page
  is dropped where the lookup's `on_done` lands, in the `_finish_*` methods.
```

`_following_ids` is a per-page cache of "does the viewer follow this author",
and it is the **only** follow-state cache in the timeline — which is why
`_note_followed_tags` (§9) has to ask again for every page.

---

## 8. Stage 6 — The model

`timelinedata.py:288 TimelineModel` is a `Gio.ListModel` over a `Gio.ListStore`
of `StatusItem`, re-emitting the store's `items-changed`.

```
  StatusItem (GObject)                    timelinedata.py:25
    .raw     the status dict as the API sent it   (typed Status)
    .status  the boost's inner post, or .raw
    .boosted_by  the booster, for a boost
    .ui      per-row view state that survives recycling:
             expanded / cw_open / media_revealed / filter_shown / source_tag
    .translation  POST /statuses/:id/translate result
    signal 'changed'  ──► every bound StatusRow rebinds
```

### 8.1 The three mutations

```
  append_old(statuses)                     timelinedata.py:408
        ├─ empty ─► exhausted = True; return False
        ├─ oldest = min(ids)
        ├─ items  = _statuses_to_items(_filter_unseen(statuses))
        ├─ items? ─► _store.splice(n_items, 0, items)      ◄── ONE splice
        │            register every item as seen
        ├─ no items and cursor did not move back ─► return False
        ├─ oldest_id = oldest
        └─ return True

  insert_new(statuses)                     timelinedata.py:394
        ├─ fresh = _filter_unseen(statuses)
        ├─ for item in reversed(items): _store.insert(0, item)   ◄── N inserts!
        │        (each emits items-changed, so N regroup+relayout passes)
        ├─ newest_id = max(ids) if greater
        └─ return len(fresh)

```

`TimelineModel.replace_all` (the authoritative swap of a cached page for the
live one, with `prior.update()` re-binding every row present in both) was the
timeline-cache addition (`e02459c`) and was removed with it (§3.2).

### 8.2 Filtering and grouping

```
  _filter_unseen(statuses)                 timelinedata.py:319
        ├─ drop ids already in self._seen
        └─ dedupe boosts of the same reblog id (dedupe-boosts setting)

  group_boosts(statuses)                   timelinedata.py:194
        ├─ page < GROUP_BOOSTS_MIN_PAGE (11) ─► unchanged
        ├─ threshold met? ─► a BoostCarouselItem replaces the boosts,
        │                     inserted at the middle or end of the page
        └─ otherwise ─► boosts stay inline

  _statuses_to_items(statuses)             timelinedata.py:378
        └─ group_boosts ─► BoostCarouselItem | StatusItem  (one GObject each)
```

`entities.lift` (entities.py:171) runs on the way in, retyping the four API
objects into `Status`/`Account`/`Notification`/`Relationship` **in place within
their containers** so every downstream reader is typed without re-checking. It
walks the whole payload: the wrapper, its `account`, its `reblog` and that one's
`account`, plus `mentions`, `emojis`, `media_attachments`, `poll`, `quote`,
`card`, `tagged_collections`.

---

## 9. Stage 7 — Run grouping

A *run* is consecutive same-thread posts by one author, drawn as one card with a
shared author line and `n/total` badges.

```
  StatusRun (GObject)     statusrun.py:509
      .items = [StatusItem, …]

  RunModel (Gio.ListModel)                 statusrun.py:525
      ._items   mirror of the source model, kept in step from items-changed
      ._runs    the presented list of StatusRun

  _on_items_changed(source, position, removed, added)   :357
        ├─ self._items[position:position+removed] = [source.get_item(i) …]
        └─ _rebuild()                        ◄── WHOLE MODEL, every change

  _rebuild()                                              :363
        ├─ groups = group self._items by run_key(item)
        │     run_key cached on the item (__dict__['_run_key'])   :599
        │     a boost, a carousel, or an authorless post keys None
        ├─ find the common prefix:  _same_items(old[start], groups[start])
        ├─ find the common suffix
        ├─ splice only the changed middle into self._runs
        └─ items_changed(start, removed, added)   ◄── MINIMAL notification
```

`_same_items` compares by **identity** (`x is y`), so a run that did not change is
announced as unchanged and the ListView keeps its widget bound to it. This is the
single most important optimisation in the middle of the pipeline: a refresh that
inserts at the top does not invalidate the rest of the list.

---

## 10. Stages 8–10 — Shells, throttle, cards

### 10.1 The factory

```
  TimelineFactory.on_setup(factory, list_item)     timelinepage.py:46
        card = StatusRunRow()                      ◄── EMPTY SHELL, no cards
        card.filter_context = self.filter_context
        list_item.set_child(card)
        list_item.set_activatable(True)

  TimelineFactory.on_bind(factory, list_item)      timelinepage.py:53
        card.bind(list_item.get_item(), client)
```

`Gtk.ListView` keeps roughly 205 item widgets alive per list regardless of scroll
position [M, `PERFORMANCE.md:37`], so the shell is allocated for every loaded
run whether or not it is ever seen.

### 10.2 Shell state and the build throttle

```
  StatusRunRow (Gtk.Box)                    statusrun.py:214
      .rows    StatusRow cards currently shown in this shell
      ._cards  {StatusItem → StatusRow} built but possibly hidden   ◄── LRU kept
      ._run    the StatusRun
      _queued  whether _pending_shells holds a ref to this shell

  bind(run, client)                                     statusrun.py:286
        ├─ drop cards for items no longer in the run
        ├─ known = _known_shell_height(self, run) ─► set_size_request(-1, known)
        ├─ _detach()          hide rows, clear classes
        └─ if self.get_mapped(): self._populate()    ◄── SYNCHRONOUS rebuild

  _on_map(widget)                                      statusrun.py:311
        ├─ all cards kept ─► _populate()      (cheap: just re-show)
        └─ else            ─► _queue_populate()

  _queue_populate()                                    statusrun.py:325
        ├─ add_css_class('status-run-pending')     (empty card frame)
        ├─ _pending_shells.append(self.weak_ref())   GObject weak ref
        └─ if not _pending_source: GLib.idle_add(_populate_pending)

  _populate_pending()   ◄── THE THROTTLE              statusrun.py:76
        while _pending_shells:
            shell = deque.popleft()()      (GObject weak ref, None ⇒ skip)
            shell._queued = False
            if shell.get_mapped() and shell._run and not shell.rows:
                shell._populate()
                return SOURCE_CONTINUE      ◄── ONE CARD PER DISPATCH
            # not mapped ⇒ already discarded by layout; skip it
        _pending_source = 0
        return SOURCE_REMOVE
```

This throttle is the deliberate answer to the worst stalls the app had [M,
`PERFORMANCE.md:64`]: building every `StatusRow` in `on_map` cost ~5 ms each and
peaked at an 82 ms dispatch, because ListView maps each new shell on
`items-changed` and the very next layout unmaps most of them. The measured cost
is a visible empty card for a frame: **52 of 265 frames** in the benchmark had a
shown run still empty.

Rejected alternatives, with numbers [M, `PERFORMANCE.md:64`]: a tick callback
(runs *before* layout, so it still built everything — longest dispatch rose to
**139 ms**), and an 8 ms per-burst budget (19 stalls over 16 ms).

### 10.3 Height pinning — why `frames_with_empty_shell` is not the only metric

```
  _on_unmap(widget)                                     statusrun.py:351
        ├─ width = get_width(); if 0 use the class-wide _column_width
        ├─ if self.rows:                       ◄── only while cards are shown
        │     height = get_height()
        │     if height <= 64 and width > 1: measure()      (safety net)
        │     if height > 64: _store_height(height, width)
        ├─ _detach()
        └─ if self._cards: _keep_cards()      ──► the app-wide LRU

  _store_height(height, width)                           statusrun.py:335
        ├─ set_size_request(-1, height)
        ├─ class-wide _typical_height = running 3/4 average, floor 64
        ├─ run._shell_height  = height
        ├─ run._shell_width   = width
        └─ run._shell_stamp   = _shell_stamp(run)

  _known_shell_height(shell, run)                        statusrun.py:53
        ├─ height <= 64 ──────────────► 0  (no pin for a placeholder)
        ├─ _shell_stamp != _shell_stamp(run) ─► 0  (content changed ⇒ remeasure)
        ├─ |width − run._shell_width| > 4 ──► 0   (column resized)
        └─ return height
```

`_shell_stamp` (statusrun.py:25) is a tuple-of-tuples over everything the height
depends on: status id, `edited_at`, content length, spoiler length, media count,
`expanded`, `cw_open`, `media_revealed`, `filter_shown`, `source_tag`,
`translation is not None`, and carousel size. It deliberately does **not** hold
the post text, so the cache does not keep a second copy of every status.

`_populate` clears the pin (`set_size_request(-1, -1)`, statusrun.py:470): a
pinned height used as a *minimum* outlives any change that made the post shorter
— a wider column, a closed content warning, a poll answered — and the height
stored on the next unmap then keeps the blank gap for good [M, `PERFORMANCE.md:46`].

### 10.4 The card LRU

`_keep_cards` (statusrun.py:430) is an app-wide `OrderedDict` of
`CARD_SHELLS_KEPT = 100` unmapped shells, keyed by a per-shell counter, holding
**GObject weak refs** so the queue never keeps a shell or its page alive. Older
shells drop their cards and rebuild on demand.

Measured trade [M, `PERFORMANCE.md:37`]: three loaded-and-scrolled pages took
**+463 MB with 609 live `StatusRow`s** before the LRU, **+165 MB with 104** after.
Unbounded card-dropping was also rejected: 92 MB but scroll CPU doubled
(16.7 s → 32 s). Scrolling a 200-post page end-to-end four times cost 18.1 s with
the LRU against 11.5 s without.

Cards are **hidden, not unparented** (`_detach`, statusrun.py:448): a hidden
child is skipped by measure and snapshot, but scrolling back does not reparent it
[M, `PERFORMANCE.md:35` — unparenting pushed scroll-up CPU to ~690 ms].

### 10.5 Inside one card

`StatusRow` (statusrow.py:36) is a `Gtk.Overlay` holding a vertical box. Parts
are built **lazily on first use** because each costs a widget tree per row.

```
  StatusRow.__init__                          statusrow.py:53   [ALWAYS BUILT]
    filter_bar  (label + Show Anyway button)
    main
      header
        profile_button
          avatar            Adw.Avatar(size=46, show_initials=True)
          name_plain       Gtk.Label (or name_label, lazy InlineEmojiText)
          bot_badge, handle_label
        meta
          badge            '2/5' run position
          pinned_badge, language_label, visibility_icon,
          edited_button, time_label  + Gtk.DragSource
        _overflow           StatusMenuButton
      reply_box
      guarded
        content            StatusBody (InlineEmojiText, a Gtk.TextView!)
        expand_button
        post_meta
        poll_box           PollBox
        quote_box, collections_box, card_box
        media_box          MediaBox
      actions              StatusActions  (reply/boost/quote/fav/bookmark)

  LAZY [R]:  source_line (_ensure_source_line)
             name_label   (_ensure_name_label, custom-emoji display names)
             cw_box + cw_label + cw_toggle + guarded_revealer (_ensure_cw_box)
             translation_box (_ensure_translation_box)
             carousel     (_ensure_carousel, boosts-carousel mode)
```

`_bind_status` (statusrow.py:398) touches, in order: the accessible label, the
source line, `status_filter()`, the filter bar, `AvatarCache.load`, the name
(custom emoji or plain), the bot badge, `_bind_indicators` (visibility,
language, **edited → `absolute_time`**), `_set_time` (**`absolute_time`**),
`_set_post_meta` (**`absolute_time`** again), `_bind_reply_line`, the content
warning and revealer, `_render_body`, `_bind_translation`, the poll, the quote,
the collections, the media, the preview card, and `actions.bind`.

### 10.6 Body rendering

```
  _render_body(item, bank)                    statusrow.py:576
        ├─ html = translation.content or status.content
        ├─ segments = parse_content(html)          statustext.py
        ├─ content.set_emoji_size(32 if 'enlarge-custom-emojis' else 20)
        ├─ (shown, collapsible) = _collapse_view(segments)   timelinebase.py:231
        │     ├─ hashtag/link-only posts ─► one summary line
        │     └─ > 700 chars or > 14 breaks ─► truncated behind Show More
        └─ content.set_segments(shown or segments, bank)

  InlineEmojiText.set_segments(segments, emojis)   textemoji.py:394
        ├─ remove the previous run's link tags from the buffer's tag table
        │     (create_tag only ever adds; a recycled row would grow it forever)
        ├─ buf.set_text('', -1)
        ├─ per segment: paragraph gaps, link tags, :shortcode: → child anchor
        │     + Gtk.Image + EmojiTextures.bind(image, url)
        ├─ self._raw = plain text; accessible LABEL
        └─ set_focusable/set_cursor_visible if it has links
```

`InlineEmojiText` uses height-for-width with a cached Pango estimate until the
first allocation [M, `PERFORMANCE.md:31`]. Before that fix, `Gtk.TextView` was
wrapping at 1 px and rows measured ~5400 px, putting the scrollable height at
~440,000 px; it is ~33,000 px now. That is the reason scroll-down and streaming no
longer realise thousands of rows.

Its input-method context is pinned to `gtk-im-context-simple` on purpose — an
IBus context leaked read-only text views [M, `CLAUDE.md:79`].

---

## 11. Stage 10.5 — Images

```
  AvatarCache.load(avatar, account)           mediacache.py:75
        ├─ avatar.set_custom_image(None)      ◄── always clears first
        └─ fetcher.fetch(url, arrived)        ◄── NO cancellable passed

  _TextureCache.load(url, picture, cover)     mediacache.py:396
        ├─ previous = picture._babble_cancel
        ├─ previous.cancel()                  ◄── rebuild kills the old request
        ├─ cancellable = Gio.Cancellable.new(); picture._babble_cancel = it
        └─ fetcher.fetch(url, arrived, cancellable=cancellable,
                         key=(url, cover) if cover else url)

  imagecache.Fetcher                         imagecache.py:199 (rewritten on this branch)
        fetch(url, waiter, cancellable, key)
          ├─ store.get(key) ─► waiter(texture, None)          CACHE HIT
          ├─ live request?  ─► _attach(request, waiter, cancellable)
          └─ else           ─► _Request(key, url) + _attach + _start

        _start(request)
          ├─ request.cancellable.connect_after('cancelled', _on_request_cancelled)
          ├─ Soup.Message.new('GET', url)  ─► None ⇒ _finish(.., 'invalid URL')
          └─ session().send_and_read_async(message, DEFAULT,
                                           request.cancellable,
                                           _on_loaded, request)
                                             ◄── 30 s timeout, libsoup thread

        _on_loaded(session, result, request)
          ├─ transport error / non-2xx / empty ─► _finish(request, None, reason)
          └─ decode ─► _decode_async(data, cover, request.cancellable,
                                     lambda tex, err: _finish(request, tex, err))
                        │
                        │  Gio.Task.run_in_thread   ◄── WORKER THREAD
                        │    _decode_covering(data, cover, scale)
                        │      ├─ header sniff for PNG/GIF/JPEG dimensions
                        │      └─ GdkPixbuf with set_size, if it must shrink
                        │    fallback Gdk.Texture.new_from_bytes
                        ▼  (main context)
        _finish(request, texture, reason)         ◄── THE ONLY TEARDOWN PATH
          ├─ if request.finished: return              (idempotent)
          ├─ request.finished = True
          ├─ self._inflight.pop(request.key, None)   ◄── ALWAYS forgets the key
          ├─ disconnect every watched caller cancellable (deferred to an idle)
          ├─ no texture:
          │     ├─ on_failure(key, reason) unless reason is CANCELLED
          │     └─ every waiter(None, reason)
          └─ texture:
                ├─ store.put(key, texture)         LRU: 256 avatars / 48 media
                └─ every waiter(texture, None)
```

**The coalescing contract [M, commit message + module docstring]:** one request
per URL at a time, whoever asks. A second caller joins the waiters instead of
starting a second fetch, which is why an avatar "stays in flight while it
decodes".

**The bug the uncommitted rewrite fixes [M]:** the old code handed the *first
caller's* cancellable to both libsoup and the decoder. Recycling one row
therefore aborted the fetch for every *other* row sharing it; and because
`_decode_async` drops its callback when its task is cancelled, a cancellation
landing mid-decode ran neither teardown path and left the key in `_inflight`
**for the life of the process**. No later request for that URL could ever succeed,
and every attempt queued another waiter on the dead key. The image simply never
appeared again. The rewrite gives each request its own cancellable and routes
every ending — success, HTTP failure, decode failure, cancellation — through the
single `_finish`.

**Decoding cost policy [M, `PERFORMANCE.md:33`/`:41`]:** attachments decode only
large enough to cover their tile at the largest monitor scale (`_display_scale`,
read on the main thread because GDK is not thread-safe here). Avatars decode to
`AVATAR_DECODE_SIZE = 96`; they used to decode at full 400×400, and a full
256-entry cache held ~156 MB. Images that already fit skip GdkPixbuf entirely and
go straight to `Gdk.Texture.new_from_bytes`, several times faster [M,
`PERFORMANCE.md:45`].

**The decoded size is bounded before it is allocated.** The bytes are untrusted,
and a small file can declare a huge bitmap, so `_decode_covering` reads the size
from the header first. An image inside 16 MP and 8192 px per side that fits its
box (or has none) goes to `Gdk.Texture.new_from_bytes`; one over the budget, or
larger than its box, goes through GdkPixbuf with `set_size`, so the full bitmap is
never held; a header it cannot read (WebP, AVIF) goes through GdkPixbuf with the
same budget applied from `size-prepared`. There is no unbounded fallback. The
encoded body is capped separately at 16 MiB while it streams
(`imagecache.MAX_IMAGE_RESPONSE_BYTES`). `test-imagedecode.py` pins both paths.

**Costs of the design, on the record [M, `PERFORMANCE.md:73`]:** the media viewer
does not share this cache; two on-screen sizes of one URL are two cache slots; and
if the scaled decode fails, the full image is decoded as a fallback.

---

## 12. The three sequences, end to end

### 12.1 First page (the cached first paint it once began with was removed, §3.2)

```
main thread                          worker/IO
────────────────────────────────────────────────────────────────────────
restore_account()
_activate_account()
load_secret_token ─────────────────────────────────────────► keyring (7 ms)
                                                     ◄──────┘
show_shell()
ensure_home_timeline()
  TimelinePage.__init__
    RunModel / ListView / Stack / …
    ensure_loaded
      load()  model.loading = True; _ticket = ticket 1 (owns loading)
      _request ─► _paging_fetch ─► request_async ─► _spawn ─► Soup ─► server
main_stack -> 'shell'  ◄── WINDOW APPEARS
                                                        ◄── response
on_page_loaded(ticket 1, wanted)
  _finish ─► request_finish_paged ─► entities.lift
  _with_home_reply_filter ─► [maybe relationships_async ─► round trip]
  _finish_page_loaded
    model.loading = False
    _fill_first(statuses) ─► append_old ─► _store.splice ─► items-changed
                                         ─► RunModel._rebuild
                                         ─► ListView: on_setup ×20  (empty shells)
    _note_followed_tags ─► GET /followed_tags ─► GET /relationships ─►
                          apply_source_tags ─► emit 'changed' ×hits ─► bind again
    stack -> 'list'
                                                     ═══ first frame ═══
    ListView maps shells ─► _on_map ─► _queue_populate
    _populate_pending (idle, one per dispatch)
      _populate ─► StatusRow.bind ─► AvatarCache.load ─► Fetcher ─► Soup ─► server
                             ─► parse_content ─► set_segments
                             ─► MediaBox.set_media ─► _TextureCache.load ─► Soup
      … 1 card per dispatch …
                                                     ═══ cards appear ═══
                                    decode (worker) ─► _finish ─► set_paintable
```

### 12.2 Older page (infinite scroll)

```
main thread                                     IO
──────────────────────────────────────────────────────────────
on_scroll_position(adjustment)                 timelinepage.py:670
  ├─ on_scroll_near_end?  value + page_size >= upper − 800
  │     └─ YES ─► load_older()
  │              ├─ scope closed / exhausted / no oldest_id ─► RETURN
  │              ├─ _bottom_locked ─► _older_pending = True, RETURN (replayed)
  │              └─ _request({limit:20, max_id:oldest_id})  scope.begin()
  └─ value <= 0 ─► _trim_old()  (only at the top, ≤ 400 items)

on_older_loaded(ticket N)
  ├─ ticket N no longer wanted? ─► _drop_older, RETURN   ◄── HAZARD §11.4
  ├─ _loading_older = False               ◄── BEFORE the reply filter
  ├─ _finish ─► statuses
  └─ _with_home_reply_filter ─► [relationships_async round trip]
       _finish_older_loaded(before=n_items)
         appended = model.append_old(statuses)
             _store.splice(n_items, 0, items)     ◄── ONE splice, one signal
             ─► RunModel._rebuild  (whole model, minimal announcement)
             ─► ListView binds/maps the new shells
         _note_followed_tags ─► /relationships ─► apply_source_tags
                                        ─► emit 'changed' ×hits ─► bind ×N
         not appended ─► model.exhausted = True      (stop paging)
         nothing new   ─► load_older()               (keep paging)
```

### 12.3 Refresh (`since_id`)

```
refresh()                                   pages.py:395
  ├─ model.loading ─► RETURN
  ├─ n_items == 0 ─► load()
  ├─ model.exhausted = False
  └─ _request({limit:20, since_id:newest_id})

on_refresh_loaded ─► _finish_refresh_loaded
  ├─ insert_new(statuses)
  │     for item in reversed: _store.insert(0, item)   ◄── 20 signals
  ├─ added and scrolled > 24 px ─► _show_new_pill(added)   "N new posts"
  ├─ _trim_old()          (only while on the first screen)
  └─ n_items == 0 ─► stack 'empty'
```

Also reachable from: the auto-refresh timer (`_on_auto_refresh`,
timelinepage.py:340 — at the top every tick, scrolled away every
`AUTO_REFRESH_SCROLLED_TICKS = 2` ticks), the pull-down gesture
(`_RefreshGesture`, timelinepage.py:716), and `filters_changed` streaming events
(routed by `streamrouter.py`).

---

## 13. Streaming

`streaming.py` is a separate WebSocket transport. The window hands each event
to the stream router (`streamrouter.py`, `StreamRouter.route`), which routes it
by its `ROUTES` table into the same page methods the HTTP path uses, so
streamed posts take the identical model → run → shell → card path. The router
reaches pages through a host of callables the window passes in and holds none
of them.

```
  streaming.py  StreamingManager._on_message ─► emit('event', stream, event, payload)
        │
  window.py  _on_stream_event ─► StreamRouter.route(stream, name, payload)
        ├─ 'update'          ─► the right page's on_stream_update(payload):
        │                        local/federated/list/hashtag by stream,
        │                        else the home timeline
        ├─ 'delete'          ─► every timeline page's on_stream_delete(id)
        ├─ 'status.update'   ─► every timeline page's on_stream_status_update
        ├─ 'filters_changed' ─► home + notifications refresh()
        └─ … notifications / conversations / admin

  TimelinePage.on_stream_update(status)
        ├─ model.insert_new([status])         ◄── one insert, one signal
        ├─ _note_followed_tags([status])
        ├─ oldest_id is None ─► oldest_id = status['id']
        ├─ scrolled away ─► _show_new_pill(added)
        │  at the top    ─► _announce_new_posts(added)   (throttled to 10 s,
        │                                                 for screen readers)
        ├─ _trim_old()
        └─ stack -> 'list'
```

`BabbleWindow._on_stream_connection_changed` (which stays in the window, with
the rest of the stream lifecycle) holds a `_stream_dropped` flag and, when the
socket comes back, `window._catch_up_after_outage()` fills the gap: the home
timeline `refresh()`es, the notifications page catches up, and every other open
place does too — each built place's timeline that has loaded something
`refresh()`es (`has_loaded()`; a never-loaded place sends nothing) (the stream router's
`timeline_pages()`, which unwraps a list or hashtag page to its timeline) and
the conversations reload. The socket does not replay, so a place that had no
catch-up of its own (a list, a hashtag, the conversations) would keep showing
what was there when the stream went down.

A place the reader can no longer reach is not caught up, and is not fed either:
the sidebar rebuild passes every built place it does not show (hidden by a
preference, unpinned, or closed by the server) to
`Navigation.forget_many()`, which drops its page, stops its work in flight (a
catch-up, the auto-refresh timer), closes its request scope at once and syncs
the stream once. The cost is that the place is built again, and
loses its scroll position, when the reader opens it.

---

## 14. Stage inventory — where the main thread actually spends time

```
  ON THE MAIN THREAD, SYNCHRONOUSLY, DURING LOAD       [R unless marked]
  ────────────────────────────────────────────────────────────────
  imports, window construction, present()                [M e02459c]
  Gio.Settings reads (schema present check is cached)    timelinebase.py:108
  Secret.password_lookup_sync          ▸ WORKER         mastodon.py:420
  json.loads(response body)            ◄── HERE         mastodon.py:753
  entities.lift (whole payload)        ◄── HERE         entities.py:171
  RunModel._rebuild                    ◄── HERE         statusrun.py:548
  _populate_pending                    ◄── ONE/DISPATCH statusrun.py:76
  StatusRow.bind                       ◄── HERE         statusrow.py:372
  parse_content + set_segments         ◄── HERE         statustext / textemoji
  clock_format (first use only)        ◄── async, no wait statustext.py:354
  GdkPixbuf decode                     ▸ WORKER         mediacache.py:247
  Gdk.Texture.new_from_bytes           ▸ WORKER         mediacache.py:233

  NEVER ON THE MAIN THREAD
  ─────────────────────
  every HTTP request (libsoup), every image fetch, every decode,
  the keyring read, the status filter lookup round trip
```

---

## 15. Hazard catalogue

Ranked by how well they match "scrolling to the bottom of the screen stalls for
seconds". Evidence status in brackets.

### 15.1 The clock-format portal read — was a synchronous block, now asynchronous [R, fixed]

```
  StatusRow._set_time(item)            statusrow.py:612
        └─ absolute_time(created_at)          timelinebase.py:129
              └─ format_time(moment)          statustext.py:369
                    └─ clock_format()         statustext.py:354
                          if _clock_format is None:            ◄── once/process
                              _clock_format = _read_gsettings() or '24h'
                              _session_bus_async(_watch_portal)   statustext.py:275
                                  ─► the portal's answer updates the cache
                                     later; nothing here waits on the bus
```

This used to be a synchronous `bus_get_sync` plus `call_sync` with a 200 ms
timeout on the row-bind path, landing inside the one-card-per-dispatch populate
loop. It is asynchronous now: the first call answers at once from GSettings (or
`'24h'`), and the portal's answer and later changes update the cache. What
remains of the hazard is cost, not a block: `_set_time` and `_set_post_meta` each
call `absolute_time` separately, two parses and two formats per row.

### 15.2 `load_older` dropped during the cache window [removed]

Introduced by `e02459c` in a `TimelinePage.load_older` override, which returned
while `_showing_cache` was set: every scroll event in that window was
**discarded rather than remembered**, and nothing re-armed `load_older` when the
flag cleared. The override, the flag and the cache are gone (§3.2). The bottom
lock that now gates `PagedPosts.load_older` remembers a scroll that arrives while
it is held (`_older_pending`) and replays it when the lock is released
(`pages.py:330`).

### 15.3 `_loading_older` cleared before the async reply filter [R]

`on_older_loaded` sets `_loading_older = False` (timelinepage.py:545) *before*
`_with_home_reply_filter`. With the flag down and `oldest_id` unchanged, a scroll
at the bottom during that round trip starts a second request, whose `begin()`
supersedes the first ticket, and the first page's `_finish_older_loaded` is
discarded because `ticket.wanted()` is False (timelinepage.py:540). The reader
sees nothing. Each lost page costs a full request plus a `/relationships`
request, and it can repeat.

### 15.4 `/relationships` on every page, and the rebuild wave it causes [R]

`_note_followed_tags` (timelinetags.py:32) fires `/followed_tags` once, then a
**fresh `/relationships` per page** — `_following_ids` is the only follow-state
cache and the reply filter never consults it. When the response lands,
`apply_source_tags` (timelinedata.py:571) walks the whole model and emits
`'changed'` on every match inside one loop; each one is a synchronous full
`StatusRow.bind` (statusrow.py:372) that also restarts that row's image fetches
through `_TextureCache.load`. `source_tag` is in `_shell_stamp`, so the pinned
heights miss and the list reflows.

### 15.5 `replace_all` emits `'changed'` during the swap [removed]

`TimelineModel.replace_all` called `prior.update(item.raw)` for every post in both
the cached and the live page, inside the splice: ~20 synchronous full rebinds at
swap time. It went with the cache (§3.2).

### 15.6 `insert_new` inserts one at a time [M, measured and kept]

timelinedata.py:399 — 20 `insert(0, …)` calls, so 20 `items-changed`, 20
`RunModel._rebuild` passes and 20 relayouts. Measured as 259–279 ms either way
with a single `splice`, so it was kept [M, `PERFORMANCE.md:66`].

### 15.7 The dead reply-filter guard [removed]

`_with_home_reply_filter` used to snapshot `self._reply_filter_generation` and
compare it with itself when the lookup answered. Nothing ever incremented the
counter (it arrived with the reply filter, ARCH-11, and has never had an
increment), so the guard could not fire, and it has been deleted.

The lookup still calls `on_done` on success and failure. A refresh carries its
ticket through that asynchronous callback and `_finish_refresh_loaded`
compares it with `_refresh_ticket`, so a refresh overtaken by a newer one is
ignored when its lookup answers rather
than moving the catch-up chain. Older pages and first pages are dropped where
the lookup's `on_done` lands, in the `_finish_*` methods, by the paging guard.
`src/test-home-filters.py` holds relationship and timeline answers back to pin
these boundaries.

### 15.8 `_showing_cache` cleared on a superseded first page [removed]

This was a latent hazard of the cache: a superseded first page cleared
`_showing_cache`, so the live page would have been merged instead of swapped in
and the cache never written. It was never reachable, and the cache, the flag and
`replace_all` no longer exist (§3.2). What is left of "a superseded first page"
is the ticket rule: `_finish_page_loaded` calls `PagedPosts._drop_loading`, which
leaves `model.loading` to a newer request that owns it.

### 15.9 Known-and-accepted costs, for calibration [M]

| | |
|---|---|
| A run can show an empty card for a frame | 52 of 265 benchmark frames |
| Scroll-back CPU | 1360 ms vs 302 ms baseline |
| Re-scrolling a 200-post page 4× | 18.1 s with the card LRU, 11.5 s without |
| Off-screen shells with cached heights | 64 px floor, then a running typical height |

---

## 16. Where to instrument

The pipeline already has a harness: `bench/v2` (`timeline_bench2.py`) reports
`main_ms`, `stall_max_ms`, `stall_p99_ms`, `stalls_over_budget`,
`frames_with_empty_shell` and `inflight_requests`, and `PERFORMANCE.md` records
baseline vs current. It drives `GLib.MainContext.iteration` directly, because
PyGObject's override pauses an asyncio loop on every call (~0.25 s of CPU per run
that `Adw.Application.run` never pays).

For the reported symptom the following probe points separate the hazards cleanly:

| # | Location | Log | Distinguishes |
|---|---|---|---|
| 1 | `pages.py:330` `load_older` | entry + `_bottom_locked` + return reason | §15.2 — a return with no outbound request |
| 2 | `pages.py:281` `_request` | params + ticket | every request that actually leaves |
| 3 | `timelinepage.py:509` `on_older_loaded` | ticket + its `wanted()` verdict | §15.3 — dropped pages |
| 4 | `timelinetags.py:32` `_note_followed_tags` | per page | §15.4 |
| 5 | `timelinedata.py:571` `apply_source_tags` | hits + model size | §15.4 rebuild wave |
| 6 | `statusrow.py:372` `bind` | caller + duration | §15.1, §15.4 |
| 7 | `statustext.py:354` `clock_format` | first-call duration | §15.1 |
| 8 | `statusrun.py:76` `_populate_pending` | queue length per dispatch | §15.1 amplification |

Report **every run**, not a median: single runs spread 60–100 ms on the real
window and ±14 % on the benchmark.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Envelope** | `{"data": …, "link": …}` — the shape every successful API body is re-wrapped into so `Link` paging survives the generic finish path |
| **Item** | `StatusItem` — one `Status` dict plus per-row UI state, a `GObject` so it can emit `'changed'` |
| **Run** | consecutive same-thread posts by one author, drawn as one card |
| **Shell** | `StatusRunRow` — the `ListView`'s item widget; a placeholder that may or may not hold built cards |
| **Card** | `StatusRow` — the real post widget tree, built lazily and cached on the shell |
| **Stamp** | `_shell_stamp` — what a shell's height depends on, so a changed post is remeasured |
| **Cover** | `(width, height)` the largest box a texture fills; rides on the cache key so one Fetcher serves every tile |
| **Ticket** | `PagedPosts._ticket` — the newest `RequestScope` ticket; an answer whose ticket is no longer wanted is discarded |
| **Cursor** | `oldest_id` (`max_id`, backwards) / `newest_id` (`since_id`, forwards) |

---

## 18. Cross-references

- `CLAUDE.md` — the architecture seam map (ARCH-06: why there is no `timeline.py`)
- `PERFORMANCE.md` — every measured optimisation and its rejected alternatives
- `MEMORY-LEAKS.md` — `connect_weak` / `weak_method` and the GObject-weak-ref rule
- `bench/v2/README.md` — the benchmark and its metrics
- `src/test-timeline.py` (`timeline render`), `src/test-timeline-memory.py`
  (`timeline memory`) — the behavioural and lifecycle pins