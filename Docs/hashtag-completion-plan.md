# Hashtag completion: benchmark and plan

Status: plan, revised after an adversarial review (2026-10-26). Nothing here is
implemented yet.

Typing `#lin` in the composer should offer `#linux` and similar tags, the same
way `@al` already offers accounts (`src/mentioncompleter.py`, commits c3856c3
and 45426de). This document compares how two reference clients do it, and
Mastodon's own web UI as a tie-breaker, takes the best idea from each, and
plans Babble's version on top of the mention completer that already works.

Sources read:

| Client | Revision | Files |
|---|---|---|
| Tuba (Vala, GTK 4) | `02305ed` (2026-09-29) | `src/Dialogs/Composer/Completion/*.vala`, `Editor.vala`, `API/Tag.vala` |
| Phanpy (Preact) | `2ea14a1` (2026-10-25) | `src/components/text-expander.jsx`, `compose-textarea.jsx`, `@github/text-expander-element` 2.9.4 `query.js` |
| Mastodon (server + web) | `main`, 2026-10-26 | `app/models/tag.rb`, `app/services/tag_search_service.rb`, `app/javascript/mastodon/{actions,reducers}/compose.js`, `config/routes/api.rb` |

## 1. Benchmark

### 1.1 Side by side

| Aspect | Tuba | Phanpy | Mastodon web | Babble's mention completer (today) |
|---|---|---|---|---|
| Machinery | `GtkSourceCompletion` provider (`HashtagProvider`) | `<text-expander>` custom element, hand-built `<ul>` | Redux `autosuggest` | Own `Gtk.Popover` over the `Gtk.TextView` |
| Trigger | `#` that starts a whitespace-delimited word | `#` or full-width `＃`, after start, whitespace, `(` or `[` | `#`/`＃` token | `@` not after `\w`, `/` or `@` |
| Where the token ends | Whitespace only, so `#foo,` queries `foo,` | Space only; a newline does not end it | Token regex | `[\w.@-]` |
| Request | `GET /api/v2/search?type=hashtags&limit=4&exclude_unreviewed=true` | `GET /api/v2/search?type=hashtags&limit=5` | Same as Tuba, `resolve=false` | `GET /accounts/search?limit=6` |
| Request rate | One per completion refresh; the `Cancellable` is not passed to the request (`req.exec (null)`) | **One per keystroke**, no debounce; stale answers are dropped by comparing text, but not aborted | Throttle of 200 ms (leading and trailing) plus `AbortController` | 250 ms debounce, cancels the request in flight, serial guard |
| Personal tags | None | None | **Yes**: the last 1,000 tags you posted, in `localStorage`; ranks them first and fills the list with them | n/a |
| Ranking | Server order | Server order | Your tags first, then server order | Server order |
| Row content | Icon, **name**, "N per week" (sum of 7 days of `uses`) | `#`**name**, compact count of all `uses` | Name, people count | Avatar, name, `@acct` |
| Insertion | Server's casing, plus a space | **Merges casing**: your capitals win, the suggestion fills the rest (`gamingdeals` + typed `GamingD` → `GamingDeals`), trigger character kept | Keeps your typed prefix; replaces nothing if the only difference is case | `@acct` plus a space, or no space when one already follows |
| Enter / Tab / Esc | Source view defaults; `page_size = 3` | combobox-nav; the textarea skips its own Enter handling while the expander is active and `isComposing` is false | Enter/Tab accept | Enter/Tab accept, Shift+Enter is a newline, Esc closes until the token changes |
| Stale rows | Handled by the completion engine | Text comparison | Token comparison | Rows answering an old query are insensitive and unselected until the new answer arrives |
| Notable flaws | Shows "0 per week" when `history` has fewer than 7 entries; `exclude_unreviewed` (see 1.2); loses camel case (an accessibility issue: mastodon/mastodon#27013) | Floods the server; `#foo\nbar` and `#foo,` are queried as is | 1,000-tag history lives in one browser | — |

### 1.2 Server facts that change the design

1. **`exclude_unreviewed=true` almost empties the list on most instances.**
   `Tag.search_for` with that flag keeps only reviewed tags *or the exact
   name* (`matching_name(term).or(reviewed)`), and the Elasticsearch path
   filters the same way. A tag is reviewed only when an admin reviewed it for
   trends, which small instances rarely do. So on a database-backed instance,
   Tuba and Mastodon web can offer `#linux` only after you have typed all of
   `linux`. Phanpy does not send the flag. Abusive tags are still kept out:
   the server always applies `Tag.listable`, the knob admins use to hide a tag
   from listings.
2. **Without Elasticsearch the order is length, then alphabetical**
   (`ORDER BY LENGTH(name), name`), not popularity. With `limit=4`, `#art`
   shows `#art`, `#arte`, `#arts`, `#artai`, and never `#artificialintelligence`
   however much it is used. Elasticsearch servers rank by usage and recency.
   The client should ask for more than it shows and re-rank by `history`.
3. **The hashtag rule is stricter than most clients assume.** On current
   `main`, `Tag::HASHTAG_RE` is `(?<=^|[[:space:]])[#＃]…`: a tag starts only at
   the start of the text or after whitespace, so `(#foo` and `C#` are not tags.
   Older releases used a looser lookbehind. A name is
   `[[:word:]]` (letters, **marks**, decimal digits, connector punctuation)
   plus the separators `_`, `·` (U+00B7), `・` (U+30FB) and ZWNJ (U+200C), and
   must contain at least one letter: `#2024` is not a tag.
4. **The server already keeps a personal tag history.**
   `GET /api/v1/featured_tags/suggestions` returns up to 10 tags you used more
   than once recently that you have not featured (Mastodon 3.0+). Together
   with `/featured_tags` and `/followed_tags`, that gives Mastodon web's
   "your tags first" ranking without a local store, and it is the same on
   every device.
5. `/api/v2/search` caps `limit` at 20 by default (`RESULTS_LIMIT`).

### 1.3 What each one does best

- **From Phanpy:** the casing merge on insertion, accepting the full-width `＃`,
  no `exclude_unreviewed`, and a short usage count.
- **From Mastodon web:** your own tags first, filling the list when the server
  has few results; keeping the typed text when the only difference is case;
  throttling and aborting requests.
- **From Tuba:** usage shown under the name as a second line, and a hashtag
  icon column so tag rows and account rows look different.
- **Already better in Babble:** stale-row insensitivity, Escape remembered for
  the token, Shift+Enter passed through, the Wayland surface fix
  (`unrealize()` on hide), focus-leave dismissal, and debounce plus cancel
  plus serial.
- **None of them do:** match marks (Devanagari, Thai) and the `·`/`・`/ZWNJ
  separators the way the server does; skip the network for digit-only
  `#123`; make Enter at the end of a finished tag a newline instead of a
  completion.

## 2. Plan

### 2.1 Structure

Extract the parts of `MentionCompleter` that do not depend on what is being
completed into a base class, and add a hashtag subclass beside it.

- **`src/completer.py`** (new, about 300 lines): `TokenCompleter(Gtk.Popover)`
  with the code that is currently in `MentionCompleter` and does not know
  about accounts: popover setup and `set_parent`, `AUTOCOMPLETE` on the view,
  the buffer, mark, key, focus and destroy handlers, `update`, `dismiss`,
  `_hide` with its `unrealize()`, `_place`, `_move`, the debounce, cancel and
  serial logic, stale-row insensitivity, Escape suppression, and the modifier
  rules. Subclasses provide:
  - `token_query(text_before_cursor) -> (start, query) | None`
  - `continues_token(char) -> bool` (used by accept to swallow the rest of the
    word)
  - `instant_items(query) -> list` (shown at once with no request; `[]` by
    default)
  - `search(query, cancellable, callback)` and `parse(result) -> list`
  - `build_row(item) -> Gtk.ListBoxRow`
  - `completion(item, typed) -> str | None` (`None` means the token already is
    the item, see 2.5)
  - `wants_search(query) -> bool`
  - `list_label` (the accessible name of the list)
- **`src/mentioncompleter.py`**: `MentionCompleter(TokenCompleter)` keeps its
  name, its public API (`prompt`, `dismiss`), `mention_query` and
  `_wants_resolve`. Its behaviour must not change: the existing mention checks
  in `test-compose.py` are the regression bar for the extraction and pass
  without edits, apart from the stub client (2.7).
- **`src/hashtagcompleter.py`** (new, about 300 lines):
  `HashtagCompleter(TokenCompleter)` plus pure functions with no GTK:
  `hashtag_query`, `merge_casing`, `rank_tags`, and the per-account
  `TagPool`.
- **`src/compose.py`**: one line next to the mention completer,
  `self.hashtag_completer = HashtagCompleter(self.client, self.text_view)`,
  `dismiss()` next to the two existing `mention_completer.dismiss()` calls,
  and `hashtagcompleter.remember(client, status.get('tags'))` in
  `on_post_done`.
- Both new modules go in `install_data` in `src/meson.build`. They stay under
  the 800-line `file size rule`.

Each completer has its own popover and key controller. At most one is
visible at a time, because `@` and `#` tokens cannot overlap, and each key
handler returns early when its popover is hidden. That is the same contract
the mention completer already relies on.

### 2.2 The token

`hashtag_query(text)` scans backwards from the cursor with
`unicodedata.category` instead of a regex. Python's `re` `\w` does not match
combining marks, so a `\w` pattern would cut `#हिन्दी` at its first vowel
sign, and `re` has no `\p{M}`. The rules:

- name characters: categories `L*`, `M*`, `Nd`, `Pc`, plus `·`, `・`, U+200C
- trigger: `#` or `＃`, at the start of the line or after whitespace (current
  Mastodon)
- returns `(offset of the trigger, query)`; `''` for a bare `#`; `None` when the
  cursor is not at the end of such a token or a selection exists (as
  `_current_token` does now)

`continues_token` uses the same character test.

### 2.3 Where suggestions come from

1. **`TagPool`**, one per account, keyed by `accounts.account_key()`, in a
   module-level dict with a 10-minute TTL (the search provider's
   `INDEX_TTL_SECONDS`). It holds plain dicts, never widgets. It is filled the
   first time a `#` token appears in a composer for that account, with three
   parallel requests whose failures count as empty (GoToSocial, Akkoma and old
   servers may lack one of them):
   - `GET /featured_tags/suggestions` (recently used): source `recent`
   - `GET /featured_tags`: source `featured`
   - `GET /followed_tags?limit=200` (one page): source `followed`

   Tags from a post that was just published are added through `remember()`,
   source `recent`. Following or unfollowing a tag drops the pool, by a call
   next to `_invalidate_home_tags` in `hashtags.py`.
2. **Server search**, when `wants_search(query)` is true (the query contains a
   letter): `GET /api/v2/search` with `type=hashtags`, `q`, `limit=10`,
   `resolve=false`, and **no `exclude_unreviewed`** (1.2 §1). It is debounced
   250 ms and cancelled when the token changes. Answers are cached per
   completer in a `{query: tags}` dict of up to 32 entries, so backspacing
   does not request again.

`instant_items(query)` are the pool's tags whose lower-cased name starts with
the lower-cased query.

- For a bare `#`, they are the pool's `recent` and `featured` tags, shown after
  the 250 ms delay so that typing `# ` for a heading never flashes a list.
- For a digit-only query (`issue #12`), only `instant_items` are used and no
  request is made. Usually nothing matches and nothing pops up.

### 2.4 Ranking and display

`rank_tags(typed, pool_hits, server_tags)` is pure and returns up to
`MAX_SUGGESTIONS` (6) items, deduplicated case-insensitively:

1. the exact match (case-insensitive), from either source
2. pool hits, in the order `recent`, `featured`, `followed`
3. server tags with `following: true`, then the others by
   `history_totals(history)` people (descending), with the server's order
   breaking ties

Local rows appear immediately and are sensitive, because they answer the
current query. When the server answers, the list is filled again. If the user
moved the selection with the arrow keys, the row with the same name stays
selected; otherwise the first row is selected.

Each row has a `#` badge built like `hashtags.hash_badge()` (not imported
from the page module, see R5), the name in bold, and one caption line,
dimmed:

- `Recently used`, `Featured` or `Followed` for pool hits
- otherwise `ngettext('%d person, last 2 days', '%d people, last 2 days')`
  from `history_totals` with `STATS_DAYS`, the same window and the same
  measure (people, not posts) as the hashtag page; people are harder to
  inflate with one account posting repeatedly
- nothing when `history` is missing or zero (unlike Tuba's "0 per week")

The accessible label is `#name, caption`. The list's name is
`Suggested Hashtags`.

### 2.5 Insertion

`completion(tag, typed)`:

- `merge_casing(typed, name)`: if `name.lower()` starts with `typed.lower()`
  and lowering does not change either length, each character takes the typed
  character when that one is uppercase, and the suggestion's otherwise
  (Phanpy). If not, the suggestion's name is used as is.
- The trigger character the user typed (`#` or `＃`) is kept.
- If `typed.lower() == name.lower()`, it returns `None`. Then **Enter is not
  consumed**: the popover is dismissed and the text view gets Enter as a
  newline. Tab does the same and moves focus. Without this, Enter at the end
  of `…today #art` would complete instead of breaking the line, which is the
  most common place to type a tag.
- Otherwise the token is replaced, and a space is added unless one already
  follows (the mention completer's existing `_accept` logic, in the base
  class).

### 2.6 Input methods

Add `preedit-changed` tracking to `TokenCompleter`. While the preedit string
is not empty, `_on_key_pressed` returns `False`, so Enter confirms the
conversion and does not accept a row. The completer's controller is in the
CAPTURE phase and runs before the text view's IM context, so today a
Japanese or Chinese user typing `#日本` would have the IME's Enter taken by
the list. The mention completer gets the same fix through the base class.

### 2.7 Tests

**Pure functions** (in `test-compose.py`, next to the `mention_query`
checks):

- `hashtag_query`: start of line; after a space; after a newline; `C#` and
  `(#x` give `None`; `example.com/#a` gives `None`; `＃tag`; `#日本語`;
  `#हिन्दी` (marks); `#a·b`, `#a・b`, ZWNJ; bare `#` gives `''`; `#tag ` gives
  `None`
- `merge_casing`: `GamingD` + `gamingdeals` → `GamingDeals`; `gaming` +
  `GamingDeals` → `GamingDeals`; a length-changing lowercase such as `İ` gives
  the suggestion unchanged
- `rank_tags`: exact match first, pool before server, deduplication, people
  ordering, cap of 6

**Widget** (`compose widgets`). The stub client's `request_finish` must
answer by path: it currently returns `SEARCH_ACCOUNTS` for every request.

- `#lin` requests `/api/v2/search` with `type=hashtags` and without
  `exclude_unreviewed`
- pool rows show before the search answers
- Enter inserts `#Linux ` with merged casing
- Enter on an exact match leaves the text unchanged plus a newline
- `#12` makes no request
- Escape suppresses the token
- a non-empty preedit lets Enter through, for both completers
- all existing mention checks pass unchanged

Also: `ui strings` passes with the new strings, and `meson compile -C build
babble-pot` and `babble-update-po` are run. `Docs/features.md` gets "`#hashtag`
suggestions as you type". `Docs/mastodon-compat.md` gets the three pool
endpoints and what happens when one is missing.

### 2.8 Order of work

1. Extract `TokenCompleter`, with no behaviour change; the mention tests pass
   unchanged. Commit.
2. Add the preedit guard in the base class, with its test. Commit.
3. Add `hashtag_query`, `merge_casing` and `rank_tags` with their tests.
   Commit.
4. Add `HashtagCompleter` with server search only. Commit.
5. Add `TagPool`, `remember` and the invalidation hook. Commit.
6. Update strings, po files and docs.

Each step is usable on its own. If step 5 gets cut, step 4 is still Phanpy's
behaviour plus the fixes in 1.3.

## 3. Adversarial review

The first draft was reviewed against the Babble code, the three references,
and the server source. Each finding is listed with what changed in section 2.

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R1 | The draft made **one** popover serve every source and renamed `mention_completer` to `completer`. That changes `compose.py`, every mention check in `test-compose.py` (which reads `completer._search_id` and `_cancellable`), and the tested `prompt()` path, all at once with the new feature. | High | A base class and two subclasses (2.1). The mention API and tests stay as they are, so the extraction is proven before any hashtag code exists. The cost is two key controllers, which are cheap and mutually exclusive. |
| R2 | The draft copied Tuba and Mastodon web's `exclude_unreviewed=true`. On a database-backed instance with no tag reviews, that returns only the exact name, so the feature looks broken exactly where users are. | High | Dropped (1.2 §1). Tags hidden by admins stay hidden through `listable`. |
| R3 | `limit=4–5` on a server that orders by length then name hides popular long tags. | Medium | `limit=10`, re-ranked by people from `history`, showing 6 (2.4). |
| R4 | A `\w` regex, like `mention_query`'s, splits Indic and Thai tags at combining marks, and misses `·`, `・` and ZWNJ. | High for those users | Character-category scan (2.2). |
| R5 | `stats_text`/`history_totals` and `hash_badge` live in `hashtags.py`, which imports `lists` and `charts`. Importing it from the composer would load a page module whenever a composer opens. | Low | Move `history_totals` to `statustext.py` (GLib only, already the home of `compact_count`), and keep a re-export in `hashtags.py`. `hash_badge` is five lines; `hashtagcompleter.py` builds its own label with the same classes, rather than importing a page. |
| R6 | Enter accepting the preselected row hijacks the newline after a finished tag. Tuba (`select_on_show = true`) has this problem. | High (daily irritation) | When the token equals the suggestion case-insensitively, Enter passes through (2.5), as Mastodon web's `COMPOSE_SUGGESTION_IGNORE` does. A narrower race remains: a server exact match can arrive and become the first row just before Enter. The result is a newline instead of a completion, and never wrong text, so it is accepted. |
| R7 | Local rows shown first and server rows arriving later can reorder the list under the user's Enter. | Medium | Selection is kept by name when the user moved it, and otherwise the new first row is selected (2.4). The only reordering left is R6's exact match, which is harmless. |
| R8 | The CAPTURE-phase key controller runs before the IM context, so Enter during CJK composition accepts a suggestion. This is already true for mentions. | High for CJK users | Preedit guard in the base class (2.6). It is step 2, so mentions are fixed even if the rest slips. |
| R9 | A bare `#` popping a list is noise for `# heading`, `issue #5` and `C#`. | Medium | `C#` never triggers (whitespace rule). A bare `#` waits the 250 ms debounce and shows only recent and featured tags; digit-only queries never touch the network; Escape suppresses as before. If it still feels noisy in use, step 5 can drop the bare-`#` case without touching anything else. |
| R10 | Fetching three endpoints on every composer open costs requests for posts with no tags. | Low | Lazy on the first `#` token, cached per account for 10 minutes across composers. |
| R11 | `followed_tags?limit=200` without paging is exactly what BUG-11 fixed elsewhere. | Low | Accepted and stated: the pool only ranks. A followed tag past 200 is still found by server search, which also sets `following: true` on it (ranked in 2.4 §3). |
| R12 | A module-level pool can keep data after the account is removed, or mix accounts. | Low | Keyed by `account_key()` (`(id, instance)`, never the id alone); plain dicts with a TTL; no widget references, so no leak. |
| R13 | Using GtkSourceView's own completion (Tuba's route) would avoid writing a popover. | Considered, rejected | The composer is a plain `Gtk.TextView` on a `GtkSource.Buffer`, not a `GtkSource.View`. Switching would discard a tested completer with its Wayland and stale-row fixes. `CompletionProvider.populate_async` from Python is awkward, and the result looks like a code editor's popup. |
| R14 | The stub client in `test-compose.py` answers every request with accounts, so a hashtag test would receive accounts and pass or fail for the wrong reason. | Medium (false confidence) | Route by path in the stub (2.7). |
| R15 | Rejected optimisation: filtering a short answer locally for longer queries ("`lin` returned 3, so `linu` is a subset"). It holds for the database's prefix match, but not certainly for Elasticsearch's edge-n-gram scoring. | — | Not done. The per-query cache covers backspacing. |

### Out of scope, noted for later

- Highlighting `#tags`, `@mentions` and URLs in the composer text, as Phanpy
  does.
- `:shortcode:` completion for custom emoji, the third provider both
  references have. `TokenCompleter` is the place for it.
- Announcing "N suggestions" to screen readers with `Gtk.Accessible.announce`,
  for both completers at once.
