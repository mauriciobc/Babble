# The timeline pipeline, ingestion to pixels

> **Superseded in part.** This documents the tree at `0fdfc93`. The home
> timeline cache (`homecache.py`, §3.2, §12.1) has since been removed, along
> with the `_showing_cache` paging gate that came with it. The paging stage
> (§4) has also been reworked: `load_older` is now behind a self-releasing
> debounce rather than a request-state mutex. Sections 3.2, 12.1, 15.2, 15.3
> and 15.5 describe behaviour that no longer exists; everything else stands.
> See `Docs/timeline-paging-vs-tuba.md` for the reasoning.

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
                            │ 0. SESSION            window.py:402      │
                            │    keyring ──► client ──► show_shell()   │
                            └───────────────────┬──────────────────────┘
                                                │ client + account
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 1. PAGE CONSTRUCTION              timelinepage.py:190  TimelinePage.__init__   │
  │    RunModel ─► Gtk.ListView ─► ClampScrollable ─► ScrolledWindow ─► Overlay     │
  │    ─► ensure_loaded():  _show_cached()  +  load()                              │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
        ┌───────────────────────────────────────┴────────────────────────┐
        │ (1a) cached first paint, disk, synchronous                    │
        │      homecache.read ─► json.load ─► model.append_old          │
        └───────────────────────────────────────┬────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 2. REQUEST                pages.py:169  PagedPosts._request                     │
  │    _generation++ ─► TimelinePage._paging_fetch ─► mastodon.request_async         │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 3. TRANSPORT                mastodon.py:729 / :550 / :668   [worker + main]   │
  │    Soup.Session.send_and_read_async ─► retry/429 ─► envelope {"data","link"}     │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 4. RESPONSE DISPATCH        timelinepage.py:563  on_page_loaded                │
  │    on_older_loaded:672   /   on_refresh_loaded:701                              │
  │    _finish() ─► generation guard (_superseded)                                  │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 5. REPLY FILTER             timelinepage.py:522  _with_home_reply_filter       │
  │    optional GET /accounts/relationships ─► defer or pass through                │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 6. MODEL                    timelinedata.py:275  TimelineModel (Gio.ListModel) │
  │    filter_unseen ─► group_boosts ─► StatusItem ─► ListStore.splice              │
  │    cursors: oldest_id / newest_id / exhausted / loading                         │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ items-changed(0, n, 20)
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 7. RUN GROUPING             statusrun.py:340  RunModel (Gio.ListModel)          │
  │    regroup whole model ─► StatusRun ─► minimal items_changed(start,rm,add)     │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ items-changed
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 8. LIST VIEW                timelinepage.py:33  TimelineFactory                │
  │    Gtk.ListView on_setup ─► StatusRunRow()   (shell, empty)                    │
  │    Gtk.ListView on_bind  ─► StatusRunRow.bind(run, client)                      │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │ map
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 9. SHELL + BUILD THROTTLE    statusrun.py:92  StatusRunRow                      │
  │    _on_map ─► _queue_populate ─► _pending_shells ─► _populate_pending (idle)    │
  │    ONE card build per main-loop dispatch, at PRIORITY_DEFAULT_IDLE              │
  └─────────────────────────────────────────────┬───────────────────────────────────┘
                                                │
  ┌─────────────────────────────────────────────▼───────────────────────────────────┐
  │ 10. CARD                    statusrow.py:31  StatusRow (Gtk.Overlay)           │
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
  window.py:402  restore_account()
        │
        ├─► account_store.active_account(settings)          (GSettings, sync)
        │
        ▼
  window.py:409  _activate_account(account)
        │
        ├─ MastodonClient(account['instance'])              mastodon.py:~500
        │     └─ Soup.Session(), set_timeout(30)            mastodon.py:529
        │        HTTP_TIMEOUT_SECONDS = 30                  mastodon.py:46
        │
        ├─ self._session_lookup += 1     (stale-lookup guard, window.py:421)
        │
        └─ client.load_secret_token(id, loaded)             mastodon.py:343
                  │
                  ├─ Gio.Task.new(...)
                  └─ task.run_in_thread(work)      ◄── WORKER THREAD
                          Secret.password_lookup_sync(SECRET_SCHEMA, attrs, token)
                  │
                  ▼  (back on the main context)
             loaded(token, error)
                  ├─ lookup != self._session_lookup ──► DROP   (window.py:424)
                  ├─ error  ──► login screen + toast   (window.py:429)
                  ├─ None   ──► remove_account / toast  (window.py:438)
                  └─ token  ──► client.token = token; self.client = client
                                ─► show_shell()           (window.py:459)
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

`window.py:715 show_shell()` builds the sidebar and both main pages *before*
flipping `main_stack` to `'shell'` (window.py:736), so everything in stage 1 is
on the critical path to the window appearing at all.

```
  ensure_home_timeline()                             window.py:1085
        │
        ├─ if timeline_page exists and .client is self.client ──► reuse (no work)
        │
        └─ TimelinePage(client, mode='home',
                        model=self.timeline_model,        ◄── reused GListModel
                        cache_key=account_key(account))   timelinepage.py:190
                 │
                 │  ── widget tree ──
                 ├─ self.model        = TimelineModel()  or the reused one
                 ├─ RunModel(self.model)                     statusrun.py:340
                 ├─ Gtk.ListView(model=NoSelection(run_model),
                 │               factory=TimelineFactory(client, filter_ctx))
                 │        filter_ctx = filter_context_for(source)  :54
                 ├─ Adw.ClampScrollable(maximum_size=660)      ◄── the column
                 ├─ Gtk.ScrolledWindow(vexpand, h-policy=NEVER)
                 ├─ new_posts_revealer + new_pill  ("N new posts")
                 ├─ Gtk.Overlay { scrolled, revealer }
                 ├─ Gtk.Stack { 'list', 'loading', 'error', 'empty' }
                 ├─ _RefreshGesture(self, scrolled)             :777
                 └─ if autoload: ensure_loaded()                :292
                          │
                          ├─ _show_cached()                      :308
                          └─ load()          ──► stage 2
```

### 3.1 The filter context is decided by the path [R]

`filter_context_for(source)` (timelinepage.py:54) inspects `source.path`:

| `source.path` prefix | context |
|---|---|
| `/accounts/` | `filterrules.ACCOUNT` |
| `/timelines/public`, `/timelines/tag/`, `/timelines/link` | `filterrules.PUBLIC` |
| anything else (home) | `filterrules.HOME` |

The home page passes `source=None`, so `.path` is `''` and it lands on `HOME`.
The context is fixed once, at construction, and stamped onto every row the factory
makes — `StatusRow.filter_context` — which is what `status_filter` is later judged
against. It is a *view* property, so it never changes per item.

### 3.2 The cached first paint (stage 1a) — the timeline-cache addition

```
  _show_cached()                                      timelinepage.py:308
        │
        ├─ cache_key is None?          ──► return          (public/local/federated
        │                                                 pages have no cache_key)
        ├─ model.get_n_items() > 0?    ──► return          (already filled)
        │
        ├─ try:  statuses = homecache.read(cache_key)    homecache.py:67
        │          ├─ open() + json.load()               ◄── SYNCHRONOUS, main thread
        │          ├─ version check (VERSION == 1)
        │          ├─ saved_at within MAX_AGE_SECONDS (7 days)
        │          └─ every status passes _showable()    (id/account/reblog.account)
        │
        ├─ no statuses ──► return                       (first run, or stale)
        │
        ├─ _fill_first(statuses)                        :326
        │     ├─ model.append_old(statuses)   ──► stage 6 (stage 7/8 follow)
        │     ├─ model.oldest_id = min(ids)
        │     └─ model.newest_id = max(ids)
        │
        ├─ self._showing_cache = True        ◄── THE FLAG THAT GATES PAGING
        └─ stack.set_visible_child_name('list')
```

`homecache.py` stores one file per account at
`<user cache>/babble/home/<safe account key>.json`, `0700`/`0600` via
`catchupstore.write_private` (it holds followers-only and direct posts). It is
deleted with the login and holds only *the first page this launch loaded* — later
refreshes and streamed posts are corrected on screen and will be in the live first
page next launch.

`_showable()` (homecache.py:90) is the fail-safe added by `286a7b7`: a cached
status missing `id`, `account`, or a boost's `reblog.account` makes the whole file
read as "no cache", because an exception during page construction would keep the
live page from ever loading.

---

## 4. Stage 2 — The paging state machine

`pages.py:132 PagedPosts` is the one state machine; `TimelinePage` overrides the
transport (`_paging_fetch`/`_finish`) and adds the reply filter and the cache.

```
                          ┌────────────────────────────────────────┐
                          │  model.loading      (a page is in)     │
                          │  _loading_older     (an older page is) │
                          │  model.exhausted    (no more pages)    │
                          │  model.oldest_id    (backward cursor)  │
                          │  model.newest_id    (forward cursor)   │
                          │  _generation        (monotonic)        │
                          │  _showing_cache     (cache on screen)  │
                          └────────────────────────────────────────┘

  load()                    pages.py:197
    if model.loading: RETURN                    ◄── silent drop, no queue
    model.loading = True
    if n_items == 0: stack -> 'loading'
    _request({'limit': 20})  ──► on_page_loaded

  load_older()               pages.py:205   ◄── OVERRIDDEN in TimelinePage
    if _loading_older: RETURN
    if model.exhausted: RETURN
    if model.oldest_id is None: RETURN
    _loading_older = True
    _request({'limit': 20, 'max_id': oldest_id}) ──► on_older_loaded

  refresh()                 pages.py:214
    if model.loading: RETURN
    if n_items == 0: load(); return
    model.exhausted = False
    _request({'limit': 20, 'since_id': newest_id}) ──► on_refresh_loaded

  _request(params, on_done) pages.py:169
    _generation += 1
    generation = self._generation
    finished = lambda client, result, _d: on_done(client, result, generation)
    _paging_fetch(params, finished, generation)
```

`TimelinePage.load_older` (timelinepage.py:300) is the override added by `e02459c`:

```
  def load_older(self):
      if self._showing_cache:
          return                       ◄── SILENT NO-OP, not deferred
      PagedPosts.load_older(self)
```

### 4.1 The generation guard

Every response is tagged with the generation that requested it. `_superseded`
(pages.py:188) drops a response whose request was overtaken, and clears both busy
flags so a discarded page cannot leave the list reading as loading forever:

```
  on_*_loaded(client, result, generation)
      if generation is not None and self._superseded(generation):
          return                     ◄── page silently discarded
```

Because `_request` increments `_generation` on **every** request, any two
overlapping requests on one page make the older one dead on arrival. This is the
mechanism behind several hazards in §11.

### 4.2 Cursor discipline [R]

| Cursor | Set where | Meaning |
|---|---|---|
| `oldest_id` | `append_old`, `replace_all`, `trim`, `_fill_first`, the fully-filtered fallback at timelinepage.py:627 | `max_id` for the next older page |
| `newest_id` | `append_old`, `replace_all`, `insert_new` | `since_id` for the next refresh |
| `exhausted` | `append_old` (empty page), `replace_all`/`trim`/`refresh` (cleared) | the server has nothing older |

`id_key()` (timelinebase.py:29) is the sort key, because Mastodon ids are strings
"cast from an integer" and a plain string compare orders `'99999999'` after
`'100000001'`.

---

## 5. Stage 3 — Transport

```
  TimelinePage._paging_fetch()                  timelinepage.py:422
        │
        ├─ source is not None  ─► source.fetch(params, cb)     ApiTimelineSource
        ├─ mode == 'public'    ─► client.timelines_public_async
        └─ else (home)         ─► client.timelines_home_async
                │
                ▼
  mastodon._request_async(method, path, ...)    mastodon.py:729
        ├─ task = Gio.Task.new(self, cancellable, callback)
        ├─ task._rest_spec  = (method, path, params, form, auth, json_body)
        ├─ task._rest_attempt = 0
        ├─ message = _build_message(...)                       mastodon.py:595
        │     ├─ uri = instance + /api/v1 + path
        │     ├─ params urlencode'd (doseq)
        │     ├─ Authorization: Bearer <token>  when auth and self.token
        │     └─ body from json_body / form
        └─ _spawn(task, message)                              mastodon.py:550
              session.send_and_read_async(message, PRIORITY_DEFAULT,
                                          cancellable, _on_response, task)
                │
                ▼  (libsoup I/O thread; main loop free)
  _on_response(_session, result, task)          mastodon.py:668
        ├─ send_and_read_finish()
        │    ├─ CANCELLED          ─► return_error, no retry    :675
        │    └─ other transport err ─► _maybe_retry(transport_error=True)
        ├─ json.loads(body)                              ◄── on main thread
        ├─ status = message.get_status()
        ├─ 2xx ─► return_value(Variant('s', {"data":…, "link":…}))
        └─ else
             ├─ 429 ─► _note_rate_limit(headers) ─► return_error   :697
             └─ other ─► _maybe_retry() ─► return_error(summarize_error())
```

**The envelope [R]:** every successful body is re-serialised as
`{"data": <parsed>, "link": <Link header>}`. `request_finish` /
`request_finish_paged` (mastodon.py:760, :764) parse that string back and lift the
payload with `entities.lift`. Wrapping the `Link` header this way is what lets the
generic `_finish` return `(data, next_params, prev_params)` for both cursor styles.

**Retry [R]:** `_maybe_retry` (mastodon.py:650) retries only what
`rest_should_retry` (mastodon.py:88) allows, at most `REST_RETRY_MAX = 3`
(mastodon.py:61) times, with a `rest_backoff_seconds` (mastodon.py:102) delay via
`GLib.timeout_add`. Unit-test fakes carry no `_rest_spec` and therefore fail
straight through.

**Rate limiting [R]:** a 429 records `self._rate_limited_until` from
`Retry-After` or `X-RateLimit-Reset` (corrected by the response's own `Date`
header, `server_clock_offset`). `client.rate_limited()` is polled by
`_on_auto_refresh` (timelinepage.py:364), `_note_followed_tags` (:458) and
`_apply_auto_refresh` — the app refuses to deepen a limit the server just imposed.

**Timeouts:** 30 s on both sessions (`mastodon.py:46`, `imagecache.py:60`).
The comment at imagecache.py:57 records why: libsoup defaults to zero, meaning
"wait forever".

---

## 6. Stage 4 — Response dispatch

```
  on_page_loaded(client, result, generation)          timelinepage.py:563
        ├─ generation guard ─► _showing_cache = False; RETURN
        ├─ statuses = self._finish(result)             (GLib.Error ⇒ error page)
        │     ├─ error + _showing_cache ─► keep cache, toast "Refresh failed"
        │     └─ error, no cache        ─► stack 'error'  + Try Again
        ├─ raw_oldest = min(ids)          ◄── cursor survives an all-filtered page
        └─ _with_home_reply_filter(statuses,
                        lambda f: _finish_page_loaded(f, generation, raw_oldest))

  on_older_loaded(client, result, generation)         timelinepage.py:672
        ├─ generation guard ─► RETURN
        ├─ _loading_older = False      ◄── CLEARED BEFORE THE FILTER (see §11.4)
        ├─ statuses = self._finish(result)
        └─ _with_home_reply_filter(statuses,
                        lambda f: _finish_older_loaded(f, generation, before=n))

  on_refresh_loaded(client, result, generation)       timelinepage.py:701
        ├─ generation guard ─► RETURN
        ├─ model.loading = False
        ├─ statuses = self._finish(result)
        ├─ empty + n_items == 0 ─► stack 'empty'
        └─ _with_home_reply_filter(..., _finish_refresh_loaded)
```

---

## 7. Stage 5 — The reply filter

Only when `hide-replies-non-follows` is set **and** the page is the plain home
timeline (`source is None`).

```
  _with_home_reply_filter(statuses, on_done)           timelinepage.py:522
        │
        ├─ not enabled ─────────────────────────────────► on_done(statuses)   [sync]
        │
        ├─ collect author_ids of replies not already in self._following_ids
        ├─ unknown empty ─► filter_replies_from_non_follows(...)  [sync]
        │
        └─ unknown non-empty ─► client.relationships_async(ids[:40], loaded)
                                     │
                                     ▼   ◄── EXTRA ROUND TRIP before any paint
                                  loaded(client, result)
                                     ├─ generation != self._reply_filter_generation ─► RETURN
                                     ├─ GLib.Error ─► filter with what we have
                                     └─ merge following ids into _following_ids
                                        on_done(filtered)

  NOTE [R]: the guard above compares `self._reply_filter_generation` with a
  snapshot of itself. The counter is initialised at timelinepage.py:215 and is
  NEVER incremented anywhere in the tree, so the comparison is always False and
  the guard never fires. It is dead code. Harmless only because
  _finish_page_loaded / _finish_older_loaded re-check the real paging generation.
```

`_following_ids` is a per-page cache of "does the viewer follow this author",
and it is the **only** follow-state cache in the timeline — which is why
`_note_followed_tags` (§9) has to ask again for every page.

---

## 8. Stage 6 — The model

`timelinedata.py:275 TimelineModel` is a `Gio.ListModel` over a `Gio.ListStore`
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
  append_old(statuses)                     timelinedata.py:395
        ├─ empty ─► exhausted = True; return False
        ├─ oldest = min(ids)
        ├─ items  = _statuses_to_items(_filter_unseen(statuses))
        ├─ items? ─► _store.splice(n_items, 0, items)      ◄── ONE splice
        │            register every item as seen
        ├─ no items and cursor did not move back ─► return False
        ├─ oldest_id = oldest
        └─ return True

  insert_new(statuses)                     timelinedata.py:381
        ├─ fresh = _filter_unseen(statuses)
        ├─ for item in reversed(items): _store.insert(0, item)   ◄── N inserts!
        │        (each emits items-changed, so N regroup+relayout passes)
        ├─ newest_id = max(ids) if greater
        └─ return len(fresh)

  replace_all(statuses)                    timelinedata.py:416
        ├─ build `known` map of the current items by raw id
        ├─ _seen.clear(); _boosted_reblog_ids.clear()
        ├─ items = _statuses_to_items(_filter_unseen(statuses))
        ├─ for each new item, if `known` has that raw id:
        │      prior.raw != item.raw  ─► prior.update(item.raw)
        │                                 └─ emits 'changed' ► row rebinds NOW
        │      items[index] = prior        (view state survives the swap)
        ├─ _store.splice(0, count, items)
        ├─ register every item as seen
        └─ oldest_id / newest_id from the new ids; exhausted = False
```

`replace_all` is the timeline-cache addition (`e02459c`). Its `prior.update()` is
the subtle part: a post present in both the cached page and the live page keeps
its `StatusItem` **and its `ui` view state**, but `update()` emits `'changed'`,
so every one of those rows performs a full synchronous `bind` during the swap.

### 8.2 Filtering and grouping

```
  _filter_unseen(statuses)                 timelinedata.py:306
        ├─ drop ids already in self._seen
        └─ dedupe boosts of the same reblog id (dedupe-boosts setting)

  group_boosts(statuses)                   timelinedata.py:194
        ├─ page < GROUP_BOOSTS_MIN_PAGE (11) ─► unchanged
        ├─ threshold met? ─► a BoostCarouselItem replaces the boosts,
        │                     inserted at the middle or end of the page
        └─ otherwise ─► boosts stay inline

  _statuses_to_items(statuses)             timelinedata.py:365
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
  StatusRun (GObject)     statusrun.py:324
      .items = [StatusItem, …]

  RunModel (Gio.ListModel)                 statusrun.py:340
      ._items   mirror of the source model, kept in step from items-changed
      ._runs    the presented list of StatusRun

  _on_items_changed(source, position, removed, added)   :357
        ├─ self._items[position:position+removed] = [source.get_item(i) …]
        └─ _rebuild()                        ◄── WHOLE MODEL, every change

  _rebuild()                                              :363
        ├─ groups = group self._items by run_key(item)
        │     run_key cached on the item (__dict__['_run_key'])   :412
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
  TimelineFactory.on_setup(factory, list_item)     timelinepage.py:42
        card = StatusRunRow()                      ◄── EMPTY SHELL, no cards
        card.filter_context = self.filter_context
        list_item.set_child(card)
        list_item.set_activatable(True)

  TimelineFactory.on_bind(factory, list_item)      timelinepage.py:48
        card.bind(list_item.get_item(), client)
```

`Gtk.ListView` keeps roughly 205 item widgets alive per list regardless of scroll
position [M, `PERFORMANCE.md:37`], so the shell is allocated for every loaded
run whether or not it is ever seen.

### 10.2 Shell state and the build throttle

```
  StatusRunRow (Gtk.Box)                    statusrun.py:92
      .rows    StatusRow cards currently shown in this shell
      ._cards  {StatusItem → StatusRow} built but possibly hidden   ◄── LRU kept
      ._run    the StatusRun
      _queued  whether _pending_shells holds a ref to this shell

  bind(run, client)                                     statusrun.py:156
        ├─ drop cards for items no longer in the run
        ├─ known = _known_shell_height(self, run) ─► set_size_request(-1, known)
        ├─ _detach()          hide rows, clear classes
        └─ if self.get_mapped(): self._populate()    ◄── SYNCHRONOUS rebuild

  _on_map(widget)                                      statusrun.py:180
        ├─ all cards kept ─► _populate()      (cheap: just re-show)
        └─ else            ─► _queue_populate()

  _queue_populate()                                    statusrun.py:193
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
  _on_unmap(widget)                                     statusrun.py:219
        ├─ width = get_width(); if 0 use the class-wide _column_width
        ├─ if self.rows:                       ◄── only while cards are shown
        │     height = get_height()
        │     if height <= 64 and width > 1: measure()      (safety net)
        │     if height > 64: _store_height(height, width)
        ├─ _detach()
        └─ if self._cards: _keep_cards()      ──► the app-wide LRU

  _store_height(height, width)                           statusrun.py:203
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

`_populate` clears the pin (`set_size_request(-1, -1)`, statusrun.py:285): a
pinned height used as a *minimum* outlives any change that made the post shorter
— a wider column, a closed content warning, a poll answered — and the height
stored on the next unmap then keeps the blank gap for good [M, `PERFORMANCE.md:46`].

### 10.4 The card LRU

`_keep_cards` (statusrun.py:245) is an app-wide `OrderedDict` of
`CARD_SHELLS_KEPT = 100` unmapped shells, keyed by a per-shell counter, holding
**GObject weak refs** so the queue never keeps a shell or its page alive. Older
shells drop their cards and rebuild on demand.

Measured trade [M, `PERFORMANCE.md:37`]: three loaded-and-scrolled pages took
**+463 MB with 609 live `StatusRow`s** before the LRU, **+165 MB with 104** after.
Unbounded card-dropping was also rejected: 92 MB but scroll CPU doubled
(16.7 s → 32 s). Scrolling a 200-post page end-to-end four times cost 18.1 s with
the LRU against 11.5 s without.

Cards are **hidden, not unparented** (`_detach`, statusrun.py:263): a hidden
child is skipped by measure and snapshot, but scrolling back does not reparent it
[M, `PERFORMANCE.md:35` — unparenting pushed scroll-up CPU to ~690 ms].

### 10.5 Inside one card

`StatusRow` (statusrow.py:31) is a `Gtk.Overlay` holding a vertical box. Parts
are built **lazily on first use** because each costs a widget tree per row.

```
  StatusRow.__init__                          statusrow.py:48   [ALWAYS BUILT]
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

`_bind_status` (statusrow.py:416) touches, in order: the accessible label, the
source line, `status_filter()`, the filter bar, `AvatarCache.load`, the name
(custom emoji or plain), the bot badge, `_bind_indicators` (visibility,
language, **edited → `absolute_time`**), `_set_time` (**`absolute_time`**),
`_set_post_meta` (**`absolute_time`** again), `_bind_reply_line`, the content
warning and revealer, `_render_body`, `_bind_translation`, the poll, the quote,
the collections, the media, the preview card, and `actions.bind`.

### 10.6 Body rendering

```
  _render_body(item, bank)                    statusrow.py:590
        ├─ html = translation.content or status.content
        ├─ segments = parse_content(html)          statustext.py
        ├─ content.set_emoji_size(32 if 'enlarge-custom-emojis' else 20)
        ├─ (shown, collapsible) = _collapse_view(segments)   timelinebase.py:222
        │     ├─ hashtag/link-only posts ─► one summary line
        │     └─ > 700 chars or > 14 breaks ─► truncated behind Show More
        └─ content.set_segments(shown or segments, bank)

  InlineEmojiText.set_segments(segments, emojis)   textemoji.py:381
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
  AvatarCache.load(avatar, account)           mediacache.py:60
        ├─ avatar.set_custom_image(None)      ◄── always clears first
        └─ fetcher.fetch(url, arrived)        ◄── NO cancellable passed

  _TextureCache.load(url, picture, cover)     mediacache.py:346
        ├─ previous = picture._babble_cancel
        ├─ previous.cancel()                  ◄── rebuild kills the old request
        ├─ cancellable = Gio.Cancellable.new(); picture._babble_cancel = it
        └─ fetcher.fetch(url, arrived, cancellable=cancellable,
                         key=(url, cover) if cover else url)

  imagecache.Fetcher                         imagecache.py:170 (rewritten on this branch)
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

**Costs of the design, on the record [M, `PERFORMANCE.md:73`]:** the media viewer
does not share this cache; two on-screen sizes of one URL are two cache slots; and
if the scaled decode fails, the full image is decoded as a fallback.

---

## 12. The three sequences, end to end

### 12.1 First page, with a cache present

```
main thread                          worker/IO                disk
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
      _show_cached
        homecache.read  ──────────────────────────────────►  ~/.cache/babble/
        json.load         ◄────────────────────────────────  home/<key>.json
        _fill_first ─► append_old ─► items-changed
                                     ─► RunModel._rebuild
                                     ─► ListView: on_setup ×20  (empty shells)
      load()  model.loading = True; _generation=1
      _request ─► _paging_fetch ─► request_async ─► _spawn ─► Soup ─► server
main_stack -> 'shell'  ◄── WINDOW APPEARS
                                                        ◄── response
on_page_loaded(gen=1)
  _finish ─► request_finish_paged ─► entities.lift
  _with_home_reply_filter ─► [maybe relationships_async ─► round trip]
  _finish_page_loaded
    model.loading = False
    _showing_cache ─► replace_all(statuses)
        for each id in both: prior.update() ─► emit 'changed'
                                            ─► StatusRow.bind  ×N  (SYNCHRONOUS)
        _store.splice(0, 20, items) ─► items-changed
    _save_cache ─► GLib.idle_add(_write_home_cache, LOW_PRIORITY) ─► disk
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
on_scroll_position(adjustment)                 timelinepage.py:739
  ├─ on_scroll_near_end?  value + page_size >= upper − 800
  │     └─ YES ─► load_older()
  │              ├─ _showing_cache?  ──► RETURN   ◄── HAZARD §11.2
  │              ├─ _loading_older / exhausted / no oldest_id ─► RETURN
  │              └─ _request({limit:20, max_id:oldest_id})  _generation++
  └─ value <= 0 ─► _trim_old()  (only at the top, ≤ 400 items)

on_older_loaded(gen=N)
  ├─ _superseded(N)? ─► RETURN           ◄── HAZARD §11.4
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
refresh()                                   pages.py:214
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
timelinepage.py:360 — at the top every tick, scrolled away every
`AUTO_REFRESH_SCROLLED_TICKS = 2` ticks), the pull-down gesture
(`_RefreshGesture`, timelinepage.py:777), and `filters_changed` streaming events
(window.py:865).

---

## 13. Streaming

`streaming.py` is a separate WebSocket transport; `window.py:842 _on_stream_event`
routes payloads into the same page methods the HTTP path uses, so streamed posts
take the identical model → run → shell → card path.

```
  streaming.py:380 _on_message ─► :401 emit('event', stream, event, payload)
        │
  window.py:842 _on_stream_event
        ├─ 'update'          ─► the right page's on_stream_update(payload)
        │                        local/federated by stream, else timeline_page
        ├─ 'delete'          ─► every timeline page's on_stream_delete(id)
        ├─ 'status.update'   ─► every timeline page's on_stream_status_update
        ├─ 'filters_changed' ─► home + notifications refresh()
        └─ … notifications / conversations / admin

  TimelinePage.on_stream_update(status)       timelinepage.py:749
        ├─ model.insert_new([status])         ◄── one insert, one signal
        ├─ _note_followed_tags([status])
        ├─ oldest_id is None ─► oldest_id = status['id']
        ├─ scrolled away ─► _show_new_pill(added)
        │  at the top    ─► _announce_new_posts(added)   (throttled to 10 s,
        │                                                 for screen readers)
        ├─ _trim_old()
        └─ stack -> 'list'
```

`_on_stream_connection_changed` (window.py:824) holds a `_stream_dropped` flag and
`refresh()`es once when the socket comes back, so the gap is filled.

---

## 14. Stage inventory — where the main thread actually spends time

```
  ON THE MAIN THREAD, SYNCHRONOUSLY, DURING LOAD       [R unless marked]
  ────────────────────────────────────────────────────────────────
  imports, window construction, present()                [M e02459c]
  Gio.Settings reads (schema present check is cached)    timelinebase.py:94
  Secret.password_lookup_sync          ▸ WORKER         mastodon.py:366
  homecache.read → json.load           ◄── HERE         homecache.py:67
  json.loads(response body)            ◄── HERE         mastodon.py:684
  entities.lift (whole payload)        ◄── HERE         entities.py:171
  json.dumps(cache write)              ◄── HERE (idle)  homecache.py:63
  RunModel._rebuild                    ◄── HERE         statusrun.py:363
  StatusRunRow._populate               ◄── ONE/DISPATCH statusrun.py:76
  StatusRow.bind                       ◄── HERE         statusrow.py:391
  parse_content + set_segments         ◄── HERE         statustext / textemoji
  clock_format (first use only)        ◄── HERE, 200 ms statustext.py:195
  GdkPixbuf decode                     ▸ WORKER         mediacache.py:229
  Gdk.Texture.new_from_bytes           ▸ WORKER         mediacache.py:236

  NEVER ON THE MAIN THREAD
  ─────────────────────
  every HTTP request (libsoup), every image fetch, every decode,
  the keyring read, the status filter lookup round trip
```

---

## 15. Hazard catalogue

Ranked by how well they match "scrolling to the bottom of the screen stalls for
seconds". Evidence status in brackets.

### 15.1 The clock-format portal read — the one synchronous block in the app [R, high confidence]

```
  StatusRow._set_time(item)            statusrow.py:627
        └─ absolute_time(created_at)          timelinebase.py:120
              └─ format_time(moment)          statustext.py:273
                    └─ clock_format()         statustext.py:260
                          if _clock_format is None:            ◄── once/process
                              _load_clock_format()              statustext.py:246
                                  _session_bus()  ─► Gio.bus_get_sync(SESSION)
                                  _read_portal(bus)
                                      _portal_read(bus, 'ReadOne')
                                          bus.call_sync(
                                            'org.freedesktop.portal.Desktop',
                                            '/org/freedesktop/portal/desktop',
                                            'org.freedesktop.portal.Settings',
                                            'ReadOne', …, timeout=200 ms)
                                      on UnknownMethod ─► retry 'Read' (200 ms)
                                  fallback _read_gsettings()
```

The only runtime `call_sync`/`bus_get_sync` in the tree, on the row-bind path,
with a 200 ms timeout. It lands *inside* the one-card-per-dispatch populate loop,
so the timeline freezes mid-fill. Once per process, which makes it read as
intermittent. `_set_time` and `_set_post_meta` each call `absolute_time`
separately — two parses and two formats per row even after the read is warm.

### 15.2 `load_older` dropped during the cache window [R — confirmed by diff]

Introduced by `e02459c` in `TimelinePage.load_older` (timelinepage.py:300). The
only caller is `on_scroll_position`, which is a `value-changed` handler, so every
scroll event during the cache window is **discarded rather than remembered**, and
nothing re-arms `load_older` when `_showing_cache` clears. `286a7b7` widened the
window by moving `model.loading = False` out of `on_page_loaded` into
`_finish_page_loaded`, so it now also spans the reply-filter round trip.

### 15.3 `_loading_older` cleared before the async reply filter [R]

`on_older_loaded` sets `_loading_older = False` (timelinepage.py:675) *before*
`_with_home_reply_filter`. With the flag down and `oldest_id` unchanged, a scroll
at the bottom during that round trip starts a second request, `_generation` moves,
and the first page's `_finish_older_loaded` is discarded by `_superseded`
(timelinepage.py:685). The reader sees nothing. Each lost page costs a full
request plus a `/relationships` request, and it can repeat.

### 15.4 `/relationships` on every page, and the rebuild wave it causes [R]

`_note_followed_tags` (timelinepage.py:454) fires `/followed_tags` once, then a
**fresh `/relationships` per page** — `_following_ids` is the only follow-state
cache and the reply filter never consults it. When the response lands,
`apply_source_tags` (timelinedata.py:571) walks the whole model and emits
`'changed'` on every match inside one loop; each one is a synchronous full
`StatusRow.bind` (statusrow.py:660) that also restarts that row's image fetches
through `_TextureCache.load`. `source_tag` is in `_shell_stamp`, so the pinned
heights miss and the list reflows.

### 15.5 `replace_all` emits `'changed'` during the swap [R]

timelinepage.py:441 — `prior.update(item.raw)` for every post in both the cached
and live pages, inside the splice. ~20 synchronous full rebinds at swap time.

### 15.6 `insert_new` inserts one at a time [M, measured and kept]

timelinepage.py:385 — 20 `insert(0, …)` calls, so 20 `items-changed`, 20
`RunModel._rebuild` passes and 20 relayouts. Measured as 259–279 ms either way
with a single `splice`, so it was kept [M, `PERFORMANCE.md:66`].

### 15.7 The dead reply-filter guard [R]

timelinepage.py:543-547 — `generation` is a snapshot of
`self._reply_filter_generation` compared against itself, and that counter is
never incremented anywhere in the tree. The guard can never fire; the real check
is the one in `_finish_page_loaded`. The comment above it describes behaviour the
code does not have.

### 15.8 `_showing_cache` cleared on a superseded first page [R, currently unreachable]

`_superseded` paths set `_showing_cache = False` (timelinepage.py:565, :599). If
the first live page were ever superseded while the cache showed, the live page
would then take the `insert_new` branch instead of `replace_all` — a merge, not an
authoritative swap — and `_save_cache` would never run. I could not construct a
reachable supersede: `refresh()` is blocked by `model.loading` and
`load_older` is blocked by `_showing_cache`. Fragile, not firing.

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
| 1 | `timelinepage.py:300` `load_older` | entry + `showing_cache` + return reason | §15.2 — a return with no outbound request |
| 2 | `pages.py:169` `_request` | params + generation | every request that actually leaves |
| 3 | `timelinepage.py:672` `on_older_loaded` | generation + `_superseded` verdict | §15.3 — dropped pages |
| 4 | `timelinepage.py:454` `_note_followed_tags` | per page | §15.4 |
| 5 | `timelinedata.py:571` `apply_source_tags` | hits + model size | §15.4 rebuild wave |
| 6 | `statusrow.py:391` `bind` | caller + duration | §15.1, §15.4, §15.5 |
| 7 | `statustext.py:260` `clock_format` | first-call duration | §15.1 |
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
| **Generation** | `PagedPosts._generation` — monotonic request tag; a response whose tag is stale is discarded |
| **Cursor** | `oldest_id` (`max_id`, backwards) / `newest_id` (`since_id`, forwards) |

---

## 18. Cross-references

- `CLAUDE.md` — the architecture seam map (ARCH-06: why there is no `timeline.py`)
- `PERFORMANCE.md` — every measured optimisation and its rejected alternatives
- `MEMORY-LEAKS.md` — `connect_weak` / `weak_method` and the GObject-weak-ref rule
- `bench/v2/README.md` — the benchmark and its metrics
- `src/test-timeline.py` (`timeline render`), `src/test-timeline-memory.py`
  (`timeline memory`) — the behavioural and lifecycle pins