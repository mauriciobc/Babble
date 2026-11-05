# Mastodon API audit (phase 0)

*Every Mastodon endpoint Babble calls, checked against docs.joinmastodon.org.*

Date: 2026-11-04. Branch `refactor/api-layer-phase0`. Plan: `Docs/api-layer-plan.md`.
Note: the call-site citations in this audit (wrapper names such as
`MastodonClient.follow_async`, `verify_credentials_async`, `relationships_async`,
and the line numbers) are *at audit time*; phase 1 moved those wrappers into
`src/api/`, so a name cited here may now be `api.<module>.<name>`.
Method: static scan of `src/`, `plugins/` and `src/mastodon.py` with `ast`
plus greps for path literals; docs pages fetched one by one with WebFetch
(fetched text is a model summary of the page, so a row says what the page
said, not what the server does). Nothing under `src/` was run or changed.

## 1. Summary

### Call sites (the plan's "74")

| Mechanism | Sites |
|---|---|
| `client.request_async(...)` outside `mastodon.py`, excluding the 7 generic helpers' own inner `request_async` call (`pages.call`, `pages.mutate_row`, `PagedList` x2, `wrapped._send`, `admin.send`, `fedicollections._send`; an 8th internal site, `pages.AccountListPage.__init__`, forwards a variable path to `PagedList` and calls no `request_async`) | 68 |
| `pages.call(...)` | 15 |
| **Raw sites the `api boundary` test would see (comparable to the plan's 74)** | **83** |
| `admin.send(...)` | 14 |
| `fedicollections._send(...)` | 11 |
| `wrapped._send(...)` | 4 |
| `mutate_row(...)` (`relations.py` 4, `notifications.py` 1) | 5 |
| `PagedList` / `AccountListPage` / `ApiTimelineSource` constructors with a path | 21 |
| `_send_profile_action` / `send_relationship` (`profileactions.py`, path built from the operation name) | 13 references |
| `MastodonClient.*_async` wrappers called from UI code (`instance_info`, `verify_credentials`, `relationships`, `account_statuses`, `lookup_account`, `status_context`, `post_status`, `edit_status`, `media_upload`, `media_status`, `markers`, `notifications*`, `timelines_home/public`, `multipart`) | about 33 |
| Completer hooks returning `(path, params)` (`mentioncompleter.search_request`, `hashtagcompleter.search_request`) | 2 |
| Catch Up `_get(path, ...)` fetches | 5 |
| Streaming socket (`/api/v1/streaming?stream=user`) | 1 |
| Plugin host `Context.request` (third-party servers only, not Mastodon) | out of scope (wallabag, hashpanel) |

So the plan's 74 undercounts: **83 raw sites** (the plan most likely did not
count `pages.call` the same way, or counted before `statusmenu` and
`hashtags` grew). With the helper layers (`send`, `_send`, `mutate_row`,
constructors, wrappers) the app reaches the network from roughly **170**
syntactic places. The allow-list for `api boundary` should be keyed by
`(file, enclosing function, path literal)`, not by a count. Sites with a
variable path (22 of the 83) need the registry to name the set of paths:
`statusactions._send_toggle` (verb), `statusmenu._post_action`,
`profileactions` (operation), `hashtags._on_toggle` (follow/unfollow),
`lists._on_save/_on_toggled`, `announcements` (reaction), `notifications`
(follow request verb), `profile.reload_account`, `searchprovider._fetch`,
`desktopnotify._run` (`_ACTIONS` table), `wrapped.load`, `filters`, and the
admin `path`/`endpoint` variables.

`MastodonClient` has wrappers that no UI code calls: `favourite`,
`unfavourite`, `reblog`, `unreblog`, `bookmark`, `unbookmark`, `follow`,
`unfollow`, `reply`, `trends_statuses`, `account_statuses` partly. The
UI rebuilds those paths itself (confirmed by the scan; tests were not
searched).

### Verdicts (distinct endpoint rows in section 3)

See the tally line at the end of section 3: COMPLIANT 112, DEVIATES 0, ACCEPTED-UNDOCUMENTED 4, DEPRECATED 1, UNDOCUMENTED 0 (after 5g).

### Known bugs (not compliance deviations)

- **FIXED: blocking or unblocking a domain from the profile page never
  worked.** `ProfileActions.send_domain_block` passed `applied=` where
  `_send_profile_action` takes `apply=`, and `confirm_domain_block` passed
  `undo=` where `send_domain_block` takes `undo_label=`, so both actions
  raised `TypeError` before any request was sent. Fixed in its own commit;
  the two golden scenarios now record `POST /domain_blocks` (form `domain`)
  and `DELETE /domain_blocks` (query `domain`), each followed by a
  relationship reload. The Preferences blocked-domains page unblock
  (`relations.py`) was a separate path and unaffected.

### Top deviations to fix in phase 5

Ranked by risk to users or to correctness. None of these deviations is a
known live breakage; "deviation" means "differs from what the docs show".
The one known live bug is listed separately below.

1. **JSON bodies where the docs show form data** (`POST/PUT /api/v2/filters`,
   `PATCH /api/v2/notifications/policy`, `POST /polls/:id/votes`,
   `PUT /admin/reports/:id`, `POST /admin/measures|dimensions`). Docs list
   form parameters only. Rails accepts JSON, and Babble relies on it, but it
   is not a documented contract. Collections and `POST /statuses` do show
   JSON in the docs and are fine. Fix: either send form (`name[]` keys) or
   record "JSON accepted" as server behaviour in the registry with the
   source.
   **DONE (5e), with two carve-outs.** Moved to form: `POST /polls/:id/votes`
   (`choices[]`), `PATCH /api/v2/notifications/policy` (`for_*`, enum
   strings) and `POST /admin/measures|dimensions` (`keys[]`, `start_at`,
   `end_at`, `limit`). Each was argued per field against the controller on
   `mastodon/mastodon` main: `votes_controller` does
   `params.require(:choices)` and `VoteService` does `Integer(choice)`, so
   the strings of a form read like the integers of JSON; `policies_controller`
   does `params.permit(:for_not_following ... :for_bots)` over string enums;
   the metrics controllers read `params.require(:keys)`, `start_at`, `end_at`
   and `limit` (`to_datetime`, `to_i`), and Babble sends none of the per-key
   nested params (`tag_accounts[id]`), the one shape that is spelled
   differently. **Kept as JSON, with the reason in the endpoint note:**
   - `POST/PUT /api/v2/filters`: `expires_in: null` clears an expiry and the
     form encoder drops None. Evidence: `Expireable#expires_in=` is
     `self.expires_at = interval.present? ? interval.to_i.seconds.from_now :
     nil`, so an empty `expires_in=` would also clear it, but that value is
     not documented, and `keywords_attributes` is an array of objects (`id`,
     `keyword`, `whole_word`, `_destroy`; `filters_controller` permits
     `keywords_attributes: [:id, :keyword, :whole_word, :_destroy]`) that a
     flat form cannot carry without indexed keys the docs do not show.
   - `PUT /admin/reports/:id`: the dialog sends `rule_ids: []` to clear the
     rules before a category change (`reports_controller` permits
     `:category, rule_ids: []`). An empty array has no form encoding:
     `rule_ids[]=` arrives as `[""]`, cast to `[nil]`, and `report.rb` has
     `validates :rule_ids, absence: true` for a non-violation category and
     `validate_rule_ids` (`rules.size == rule_ids&.size`) for a violation, so
     both reject it. Only JSON `[]` clears the field.
2. **`DELETE /api/v1/domain_blocks` sends `domain` as a query parameter**
   (`profileactions.send_domain_block`, `relations.unblock`); docs say form
   data. Same for `DELETE /lists/:id/accounts` (`account_ids[]` in the
   query; docs: form) in `lists.py` 331 and 698.
   **DONE (5e):** both now send a form body on DELETE. No transport change was
   needed: `_build_message` already encodes `form` for any verb, libsoup sets
   the `Content-Type` and body on a DELETE, and a dropped DELETE is still
   repeated (it is idempotent) with the same body (`client transport`
   covers both). Servers read query and body alike
   (`params.permit(:domain)`, `params.permit(account_ids: [])`).
3. **`endorse`/`unendorse` gate is `api_versions >= 6` / "4.6.0"
   (`capabilities.ENDORSE_API_VERSION`), but the accounts page says
   `endorse`/`unendorse` were added and `pin`/`unpin` deprecated in 4.4.0.**
   **RESOLVED (5f):** the docs are right and the gate was right; the
   comment was wrong. v4.4.0 reads `api_versions` 6 and has the routes,
   v4.3.8 reads 2 and has none; 4.6.0 reads 10. See 5f item 2.
4. **`POST /api/v1/apps` app registration joins `redirect_uris` with a space**
   (`mastodon.register_app_async`). The docs allow "String or Array of
   Strings" and do not say the string delimiter. **RESOLVED (5g): kept, the
   string form is the only one every supported server reads; the array form
   would break servers before 4.3.0.** See 5g.
5. **Ungated version-specific endpoints**: `GET /annual_reports/:year/state`
   and `POST /annual_reports/:year/generate` (4.6.0, API 8) in
   `wrapped.py`, `unread_count` (4.3.0; the `mastodon.py` docstring says
   "4.0+", wrong), `notifications/policy` and `notifications/requests`
   (4.3.0), `quoted_status_id` / `quote_approval_policy` /
   `interaction_policy` / `quotes/:id/revoke` / `GET /statuses/:id/quotes`
   (4.5.0), `source[quote_policy]` (4.5.0), `GET /accounts/:id/endorsements`
   (4.4.0), `GET /accounts?id[]=` (4.3.0). All degrade through an error
   today; none is listed in `Docs/mastodon-compat.md` or
   `capabilities.py`.
   **DONE (5c):** each claim was checked in the code and against the docs and
   the server source (`lib/mastodon/version.rb` at the release tags gives the
   API number of a release). Gated, with the constant in `capabilities.py`:
   Wrapped `/state` and Generate (v8, docs state the number); notification
   policy, requests and `unread_count` (v2: the docs say only 4.3.0, and
   `api_versions` reads 2 at v4.3.0, rc.1 and v4.3.8), `when_unknown=True`
   since the page is built before the instance document may have arrived and
   a failure was already invisible; filter action `blur` (v5, docs state
   it); the default-quote-policy row and its `source[quote_policy]` PATCH
   (v7); `quoted_status_id` and `quote_approval_policy` on `POST /statuses`
   left off for a known lower number; `GET /accounts/:id/endorsements` (v6,
   4.4.0; before the gate every profile on an older server toasted "Could
   not load featured profiles"). Deliberately not gated: the `for_bots`
   policy row (it already exists only when the policy response carries the
   key, which is exact; the field arrived in 4.6.0, API 10, per
   `notification_policy.rb` at v4.6.0, absent at v4.5.11), the Quote button,
   "Who Can Quote", revoke and the quotes tab (each needs `quote_approval`,
   `quote.state` or `quotes_count` on the post), `GET /accounts?id[]=`
   (only reachable through Collections, API 10), the Wrapped report reads
   (4.3.0/4.4.0, a 404 is handled), and `supported_types[]` on
   `GET /notifications` (4.6.0, API 10): it only switches on a `fallback`
   object on notification types a client does not know, and Babble has no
   rendering for one, so sending it would change nothing visible.
6. **`GET /api/v1/instance` v1 is not used (good); `GET /api/v1/admin/accounts`
   (v1) is used as a scope probe** (`admin.py:1701`) next to the v2 list.
   Docs do not mark v1 deprecated; consistent use of v2 would be tidier.
   **DONE (5a):** probe kept as v1 (request unchanged); v2 has the same scope and 200/403 semantics and could serve, recorded in the `admin.accounts.probe` note.
7. **Docs inconsistencies Babble must not copy**: `DELETE /admin/ip_blocks/:id`
   is documented with `admin:write:domain_blocks` (probably a docs error);
   `PUT /admin/domain_blocks` / `DELETE` describe `:id` as a DomainAllow;
   the notification policy PATCH request names (`for_*`) differ from the
   example response (`filter_*` booleans); tags `feature`/`unfeature` use
   `:id` for a name. The registry should record the scope Babble actually
   requests, with a note.
   **DONE (5a):** all four oddities now stated in the registry notes (ip_blocks scope, domain_blocks/allows `:id`, policy `for_*` vs `filter_*`, featured tag `:id`).
8. **Home timeline `exclude_*` filters** (`exclude_direct`, `exclude_reblogs`,
   `exclude_quotes`, `exclude_replies`) exist from API 12 (docs label it
   5.0.0). Babble filters client-side (`homefilters`, `catchup`). Not a
   deviation, an opportunity; only worth taking behind a gate.
9. **`DELETE /statuses/:id` never sends `delete_media`** (4.4.0). Correct for
   redraft (media must survive), but plain delete leaves media for up to
   ~24 h on the server. Intentional; record it in the registry as such.
   **DONE (5a):** `statuses.delete` note says delete_media is never sent on purpose (redraft; plain delete leaves media about 24 h).
10. **`GET /announcements` `with_dismissed`** is not documented and not sent;
    announcement reactions return 400 when disabled on the server
    (5.0.0 change on the page). Check `announcements.py` shows the server
    message instead of a generic one.
   **DONE (5a), wording corrected in 5f:** the reaction failures show the
   server message (`report_error`, `summarize_error`). The parameter was in
   fact *sent* (`AnnouncementsPage` passed `with_dismissed=true`); 5f found the
   server ignores it and stopped sending it.

### 5b: findings of the independent audit

Each was checked in the code first; the test named was written to fail
before the fix.

1. **Ids compared as strings** (`notificationctrl` `str(last_read) >=
   str(newest)`). **FIXED.** Same bug found in `NotificationModel._track_ids`
   (`min`/`max` of two ids as strings across pages), `AccountStatuses.finish`
   (`oldest_regular_id`) and the Catch Up tie-break
   `rank._by_score_then_id`; all use the numeric order of
   `timelinebase.id_key` now (rank keeps a private copy, its modules may not
   import it). Tests: `notifications render`, `profile relationship helpers`,
   `catchup rank` (digit-length boundary 99999999 / 100000001).
2. **`role_is_admin` mask.** **FIXED.** `user_role.rb` FLAGS and the Role
   entity page give three non-admin bits: `invite_users` 0x10000,
   `view_feeds` 0x100000, `invite_bypass_approval` 0x200000, now named
   constants in `accounts.py`. `manage_email_subscriptions` (0x400000, server
   only, not in the docs page) counts as admin. Test: `account store`.
3. **`GET /accounts?id[]=` unchunked, errors swallowed.** **FIXED.**
   `DEFAULT_ACCOUNTS_LIMIT = 40` (`api/base_controller.rb`; the accounts
   controller raises a validation error above it), so ids go out 40 at a time;
   a failed chunk keeps the others and is toasted once (rate limited to one
   per 5 s). Golden scenarios added.
4. **`parse_link_header` collapsed repeated keys.** **FIXED.** A repeated key
   is a list of values, a single key stays a string; `request_async` encodes
   with `doseq`, so the wire is unchanged for single keys and every consumer
   that merges the cursor into its params works as it was. Test:
   `client transport`.
5. **Topic cannot be cleared.** **FIXED.** `Collection#tag_name=` with nil
   unsets the tag and the permit list takes a null scalar, so an edit sends
   `tag_name: null` (a create leaves the key out). Unverified against a live
   server. Golden scenario added.
6. **Drafts and the 25 per day / 300 total limits.** **NOT A BUG, documented.**
   Verified in `scheduled_status.rb` (DAILY_LIMIT 25 per `scheduled_at::date`,
   TOTAL_LIMIT 300 per account, 422). Save and convert already toast the
   server's sentence (`compose.on_post_done`, `ScheduledRow._put_schedule`);
   test in `compose widgets`. Dates are not spread: every draft shares one
   date by design, which caps drafts at 25 a day; recorded in the
   `scheduled.update` note.
7. **`offset=0` on the first search page.** **FIXED.** Sent only when above
   0; the golden scenario `search type pages load with offsets` changed.

Commit message for the set: `fix: numeric id order, role admin bits, chunked
account lookup, repeated Link keys, clearing a collection topic, search
offset (audit 5b)`.

### 5d: instance limits and the unread cap

Tests are in `src/test-instance-limits.py` (`instance limits`); each check
was written first and the file failed with an ImportError before the fix.
The golden file did not change (no scenario records these requests).

1. **Poll limits hard-coded.** **REAL, FIXED.** `compose.py` offered four
   options and four fixed durations and capped nothing else. Source:
   `configuration.polls` (docs.joinmastodon.org/entities/Instance/#polls).
   `MastodonClient.poll_limits()` reads `max_options`,
   `max_characters_per_option`, `min_expiration`, `max_expiration`. The
   composer reads them live (the dialog is cached per client and the
   document can arrive after it is built): option rows are capped at
   `max_options`, the option entry's max length is set, and the fixed
   durations are narrowed to the range (min and max themselves when none
   fits). Fallbacks: 4 options (Mastodon's default); no length cap and no
   duration bound when unsaid, because the server rejects what it refuses
   and a guess could block a valid poll. A `max_options` under 2 is
   treated as unsaid. Draft restore refuses more options than allowed.
   A duration restored from a saved post stays offered even when outside
   the range. A legacy draft's `expires` index is now mapped through the
   fixed list to seconds.
2. **Alt text length.** **REAL, FIXED in the composer.**
   `configuration.media_attachments.description_limit` (#media_attachments;
   1500 default since 4.4.0, 420 before) was never read for composer
   attachments. `media_description_limit()` returns it; the attachment's
   entry gets it as max length. Unsaid gives 420: a server that says
   nothing may be a pre-4.4 Mastodon that answers 422 above 420, while the
   lower cap only costs a longer description on a modern server that
   advertises nothing. The entry truncates silently (no visible counter).
   The profile editor already enforced
   `max_avatar_description_length` and `max_header_description_length`
   (`profileedit.account_limits`, stock 150): nothing to change there.
3. **`unread_count` without `limit`.** **REAL, FIXED.** The docs default
   to 100 and allow 1000, so a user with more unread saw an exact-looking
   "100". Babble now sends `limit=100` explicitly
   (`api.notifications.UNREAD_COUNT_LIMIT`, the number the display is
   derived from) and shows `100+` from that count up (badge, sidebar rail
   badge and both accessible labels; `unread_badge_text`). Counts raised
   by streamed notifications past the limit also show `100+`. The request
   changes by one query parameter; no golden scenario records the
   controller's call, and the wrapper scenario already used `limit=100`.
4. **Status limits.** `max_characters` is read (`char_limit`, 500
   unsaid) and `max_media_attachments` is read (`attachment_limit()`, 4
   unsaid): **NOT REAL.** `characters_reserved_per_url` was hard-coded at
   23: **REAL, FIXED** (`characters_reserved_per_url()` feeds the counter;
   23 unsaid). `statuses.supported_mime_types` is not part of this audit.
   Not enforced anywhere: `configuration.statuses` has no `language`
   key; the post `language` is a request field (the composer sends the
   picker's value) and nothing in the document limits it.

Unverified: no live server (a custom document with other poll limits was
not available); the 420 for pre-4.4 servers is from the docs text, not
from a server run. Translations: the new strings are not yet in `po/`
(`meson compile -C build babble-pot babble-update-po` would rewrite
every line reference, so it was left out of this change).

### 5f: remaining corrections

Each claim was checked in the code and in the server source
(`raw.githubusercontent.com/mastodon/mastodon` at `main` and the tags named).
The golden file changed in two places (listed); `feature gates` gained the
checks for items 4 and 5.

1. **`with_dismissed` on `GET /announcements`.** **REAL, FIXED.**
   `announcements_controller#set_announcements` is
   `Announcement.published.chronological` at main, v4.3.0, v4.4.0 and
   v4.5.0; no code reads the parameter, and each announcement carries its own
   `read` flag. `AnnouncementsPage` no longer sends it (golden: the six
   announcements requests lose `?with_dismissed=true`). The registry note and
   docstring now say it takes no parameters; item 10 above said "not sent",
   which was wrong.
2. **Endorse API number.** **REAL (comment only), RESOLVED.**
   `lib/mastodon/version.rb`: `api_versions.mastodon` is 2 at v4.3.8, 6 at
   v4.4.0, 7 at v4.5.0, 10 at v4.6.0. `config/routes/api.rb` has `endorse`
   and `unendorse` at v4.4.0 and not at v4.3.8. So 6 is 4.4.0, as the accounts
   page says. The `capabilities.py` comment and the `mastodon-compat.md` row
   said 4.6.0; both fixed. The registry's `mastodon_version` was already
   4.4.0. The constant stays 6. AC-14 and item 3 are marked resolved.
3. **`post_async` idempotency docstring.** **NOT REAL as written; docstring
   corrected.** The claim was that a repeated key returns the id string.
   `PostStatusService` returns the stored record at every version: v4.3.0,
   v4.4.0, v4.5.0 `return idempotency_duplicate` (an
   `@account.statuses.find(id)` or `scheduled_statuses.find`), main raises
   `IdempotencyError, idempotency_duplicate` rescued to `e.status`. Sending a
   key is therefore safe on current versions (key per account, one hour, set
   only after a successful create). Difference: main runs the check under a
   Redis lock, 4.3 to 4.5 do not, so two concurrent requests with one key can
   both create there. The key is still not sent because `IDEMPOTENT_METHODS`
   excludes POST: nothing would repeat the request, so the header would do
   nothing until the transport retries a keyed POST.
4. **`PUT /statuses/:id` body.** **REAL, FIXED.** `statuses_controller#update`
   passes only `status`, `media_ids`, `media_attributes`, `sensitive`,
   `language`, `spoiler_text`, `poll` and (from 4.5.0, when present)
   `quote_approval_policy` to `UpdateStatusService`; `visibility`,
   `in_reply_to_id`, `quoted_status_id` and `scheduled_at` are permitted
   params of create only. The scheduled-item concern does not apply: editing a
   scheduled post sets `scheduled_id`, not `editing_id` (`_clear_submission_mode`
   resets both), and it saves through `POST /statuses` followed by deleting the
   scheduled item, so `scheduled_at` is never on a PUT in practice. No real
   flow needs the dropped keys. `api.statuses.edit_async` now keeps only
   `EDIT_FIELDS`. Golden: the edit scenario now hands the composer's whole
   draft and records the PUT with only `spoiler_text` and
   `quote_approval_policy` (new key, in the permitted set); the visibility,
   reply, quote and schedule keys it was given are absent.
5. **Notification type lists.** **REAL (docs inconsistency), partly acted
   on.** The Notification entity page's `type` history is the complete one:
   `severed_relationships` and `moderation_warning` 4.3.0, `quote` and
   `quoted_update` 4.5.0 (API 7), `added_to_collection` and
   `collection_update` 4.6.0 (API 10); the methods page lists only the older
   types. It lists no `annual_report`, which the server has
   (`Notification::PROPERTIES`), so the entity page is incomplete too. All
   six are real server types, hence valid `types[]`/`exclude_types[]` values;
   `Notification.browserable` does `types & TYPES` and `requested - exclude`,
   so a type an older server does not know is dropped silently (no 422) and
   `exclude_types[]` is harmless. The quick filter sends only `mention`. What
   was wrong was offering a switch that cannot do anything: the six now show
   in the Filter menu and the "Show Notifications For" rows only on a server
   at or above the entity page's number (`capabilities.
   NOTIFICATION_TYPE_API_VERSIONS`, `notificationcopy.type_offered`), with
   `when_unknown=True` so a server that reports nothing keeps them. The stored
   exclusion list is left alone. No golden change. Limit: the menu is built
   once, so if the instance document arrives after the page the menu keeps
   the choice it made.
6. **Collections.** Limit: **RECORDED.** `collections_controller` has
   `DEFAULT_COLLECTIONS_LIMIT = 40` and `MAX_COLLECTIONS_LIMIT = 100` (main);
   the docs say max 80. Babble sends 40. Note added. `tag_name` leading `#`:
   **NOT REAL.** `Collection#tag_name=` is `Tag.find_or_create_by_names`, whose
   `HashtagNormalizer` ends in `gsub(HASHTAG_INVALID_CHARS_RE, '')`, a class
   that excludes `#`, applied to both `name` and `display_name`; the reader
   `tag_name` returns `"#" + display_name`. So `#books` and `books` store the
   same tag and Babble's `#books` round-trips; no change, no golden change.
7. **`Mastodon-Async-Refresh` on `GET /statuses/:id/context`.** **RECORDED.**
   Not handled: the header is experimental and a thread shows what the server
   holds when asked. Note on the `statuses.context` endpoint.
8. **`IDEMPOTENT_METHODS` with PUT and DELETE.** **RECORDED as an accepted
   NIT, no code change.** A retry happens only after a transport error or a
   5xx, so the case is a DELETE whose answer was lost after the server
   committed it, repeated, and answered 404. Treating that 404 as success is
   not simple: a successful `DELETE /statuses/:id` returns the deleted status
   and the redraft path needs its text, which a 404 cannot supply, so a
   synthesised success would open an empty composer. PUT repeats are exact
   duplicates. Comment added above `IDEMPOTENT_METHODS`.

### 5g: app registration and the other OAuth requests

Sources: `app/controllers/api/v1/apps_controller.rb` at v3.5.0, v4.0.0,
v4.2.0, v4.3.0, v4.4.0, v4.5.0 and main; Doorkeeper 5.7.1 (Mastodon 4.3.0),
5.8.2 (4.4.0, 4.5.0) and 5.9.9 (main), read from
`raw.githubusercontent.com/doorkeeper-gem/doorkeeper`. The live registration
was **not tried**: `POST /api/v1/apps` leaves a permanent OAuth application
on the server that no API call removes. Verification is the source reading
and a loopback fake server.

**Evidence: what each server version accepts for `redirect_uris`**

| Mastodon | Doorkeeper | `params.permit` in `apps_controller#app_params` | string (space or newline) | `redirect_uris[]` array |
|---|---|---|---|---|
| 3.5.0, 4.0.0, 4.2.0 | older than 5.7 | `:client_name, :redirect_uris, :scopes, :website` | accepted | **dropped** by `permit` (not scalar): `redirect_uri` is nil, the application is invalid, 422 |
| 4.3.0 | 5.7.1 | `... :redirect_uris, redirect_uris: []` | accepted | accepted |
| 4.4.0, 4.5.0 | 5.8.2 | same as 4.3.0 | accepted | accepted |
| main | 5.9.9 | same | accepted (spec "multiple redirect_uris as a string" uses a newline) | accepted (spec "as an array"; `Application#redirect_uri=` joins an Array with `"\n"`) |

Doorkeeper splits on any whitespace everywhere it reads the field:
`RedirectUriValidator#validate_each` does `value.split.each` and
`OAuth::Helpers::URIChecker` does `client_url.split.any?` (identical in
5.7.1, 5.8.2, 5.9.9). A space-joined and a newline-joined string therefore
validate and match identically; the array form adds nothing a supported
server needs and fails on every server before 4.3.0, which Babble still
talks to (the API-version gates are all `when_unknown` aware). Decision:
**keep the string, keep the space**. Switching to a newline is equivalent on
the server but would change a stored value and the golden for no benefit,
and a non-Mastodon server that splits on a space (the form Babble has always
sent) is not worth risking.

Babble registers at most three URIs: the loopback `http://127.0.0.1:PORT/callback`
(when the port could be bound), `babble://oauth/callback`, and
`urn:ietf:wg:oauth:2.0:oob`; two without the loopback. Every URI
`get_authorize_url` and `request_token_async` send is one of them.

Tests (no code change, so no golden change): `client transport` gained
`audit 5g` checks against the loopback fake server: the registration is a
form POST, `redirect_uris` is one string with no `[]` key, split on
whitespace it equals the expected two and three URIs in order, `scopes` is
`read write`, and the authorization `redirect_uri` is a registered one. The
golden already pins both bodies (`client oauth wrappers`).

**The other OAuth requests, read against the docs and the server**

| Request | Verdict | Evidence |
|---|---|---|
| `POST /oauth/token` (`grant_type`, `code`, `code_verifier`, `client_id`, `client_secret`, `redirect_uri`) | COMPLIANT | Doorkeeper `AuthorizationCodeRequest` reads `redirect_uri` and `code_verifier` from the parameters, validates the redirect against the grant's and PKCE against its challenge. `scope` is not sent: for this grant the token takes `grant.scopes` (`authorization_code_request.rb`: `grant.scopes`), the scopes granted at authorization (`read write`); the docs' `scope` (default `read`) applies to `client_credentials`, so omitting it cannot narrow or widen the token. |
| `POST /oauth/revoke` (`client_id`, `client_secret`, `token`, form) | COMPLIANT | Matches the documented parameters; public-client credentials are sent as form fields, no bearer header (`auth=False`). |
| `GET /oauth/authorize` (`response_type=code`, `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge`, `code_challenge_method=S256`) | COMPLIANT | Mastodon's initializer has `pkce_code_challenge_methods ['S256']`. `force_login` and `lang` are optional and not sent. `scope` is `read write`, equal to the registered scopes (`OAUTH_SCOPES`), so Mastodon cannot refuse it. |

OA-1 and item 4 are resolved. The `redirect_uris` array is recorded as the
form to use if the minimum supported server ever becomes 4.3.0.


## 2. Documentation sources and what could not be verified

Fetched and used: `/methods/` statuses, accounts, lists, timelines,
notifications, filters, search, tags, featured_tags, followed_tags, trends,
media, polls, reports, scheduled_statuses, announcements, conversations,
admin/accounts, admin/reports, admin/domain_blocks, admin/domain_allows,
admin/email_domain_blocks, admin/ip_blocks, admin/measures,
admin/dimensions, oauth, apps, instance, markers, suggestions,
follow_requests, blocks, mutes, domain_blocks, custom_emojis,
annual_reports, collections, streaming, bookmarks, favourites, profile;
`/api/guidelines/`; `/entities/Instance/`.

**Could not verify (marked UNVERIFIED in the table):**

- `https://docs.joinmastodon.org/api/versioning/` returned **404**. There
  is no docs mapping from `api_versions.mastodon` numbers to releases;
  `/entities/Instance/` says the number exists since 4.3.0 and gives only
  `6` as an example. So the gates v6 (endorse), v8 (profile, annual report
  state), v9 (profile alt text), v10 (collections), v11 are checked
  against *the page that introduces each feature* (4.6.0 notes on
  annual_reports, profile, collections), not against a docs table. v6 is
  UNVERIFIED (see deviation 3).
- `/api/guidelines/` did not mention rate limits, idempotency or
  `api_versions` (it points to a separate rate-limits page; not fetched).
  The 429 handling in `mastodon.py` is therefore UNVERIFIED against the
  docs here.
- `/methods/mutes/` was reached through the mutes page itself; `GET /mutes`
  is documented there. The blocks page contained no mutes text.
- Mastodon source (`mastodon/mastodon`) was **not** consulted in this
  phase. Every "accepted but undocumented" statement (JSON bodies, space-
  separated redirect URIs, `GET /api/v1/admin/accounts` still existing) is
  from Babble's own code comments or Rails convention and is flagged as
  such.
- Streaming WebSocket message format was taken from the page; the live
  connection (`streaming.py`) was not exercised.
- `GET /api/v1/accounts/:id/lists` is on the accounts page (read:lists,
  2.1.0), not on the lists page.
- Docs scopes in the page summaries are what the page printed. Babble asks
  for `read write` (and `admin:read admin:write` in the second
  authorization) which covers every scope in the table; the table's
  "Scope" column is informational for the registry.

## 3. Endpoint table

Legend. Verdicts: **COMPLIANT**, **DEVIATES** (details given; "[minor]"
= works against current servers, differs from the documented contract),
**ACCEPTED-UNDOCUMENTED** (the request works against current servers but is
not the documented contract: the docs show form data and Babble sends JSON,
which Rails accepts; this is Babble's own comments and Rails convention, the
Mastodon source was not consulted), **DEPRECATED**, **UNDOCUMENTED** (the
docs are silent on the point), **UNVERIFIED**. "Gate" is what Babble does
today. Paths are relative to `/api/v1` unless they start with `/api/v2` or
`/oauth`. Scope = OAuth scope the docs page states. Ids like `ST-1` are
used in section 4.

### statuses

| Id | Method path | Params Babble sends | Docs: scope; added | Gate today | Verdict |
|---|---|---|---|---|---|
| ST-1 | POST /statuses | JSON: `status`, `media_ids[]`, `poll{options,expires_in,multiple,hide_totals}`, `in_reply_to_id`, `sensitive`, `spoiler_text`, `visibility`, `language`, `scheduled_at`, `quoted_status_id` | `write:statuses`; 0.0.0 (`scheduled_at` 2.7, `poll` 2.8, `quoted_status_id`/`quote_approval_policy` 4.5) | none for `quoted_status_id` | ACCEPTED-UNDOCUMENTED: JSON body (docs show form `poll[options][]`); `quoted_status_id` ungated. No `Idempotency-Key` on purpose (documented in `api.statuses.post_async`). |
| ST-2 | GET /statuses/:id | none | public / `read:statuses`; 0.0.0 | none | COMPLIANT |
| ST-3 | PUT /statuses/:id | JSON as ST-1 (no `scheduled_at`) | `write:statuses`; 3.5.0 | none | ACCEPTED-UNDOCUMENTED: JSON body |
| ST-4 | DELETE /statuses/:id | none | `write:statuses`; 0.0.0, `delete_media` 4.4.0 | none | COMPLIANT (no `delete_media`, intentional for redraft) |
| ST-5 | GET /statuses/:id/context | none | public/`read:statuses`; 0.0.0 | none | COMPLIANT |
| ST-6 | GET /statuses/:id/history | none | public/`read:statuses`; 3.5.0 | none | COMPLIANT |
| ST-7 | GET /statuses/:id/source | none | `read:statuses`; 3.5.0 | none | COMPLIANT |
| ST-8 | POST /statuses/:id/translate | form `lang` | `read:statuses`; 4.0.0 | `configuration.translation.enabled` | COMPLIANT |
| ST-9 | POST /statuses/:id/favourite, /unfavourite | none | `write:favourites`; 0.0.0 | none | COMPLIANT |
| ST-10 | POST /statuses/:id/reblog | optional form `visibility` | `write:statuses`; 0.0.0, `visibility` 2.8.0 | none | COMPLIANT |
| ST-11 | POST /statuses/:id/unreblog | none | `write:statuses`; 0.0.0 | none | COMPLIANT |
| ST-12 | POST /statuses/:id/bookmark, /unbookmark | none | `write:bookmarks`; 3.1.0 | none | COMPLIANT |
| ST-13 | POST /statuses/:id/mute, /unmute (conversation) | none | `write:mutes`; 1.4.2 | none | COMPLIANT |
| ST-14 | POST /statuses/:id/pin, /unpin | none | `write:accounts`; 1.6.0 | none | COMPLIANT |
| ST-15 | PUT /statuses/:id/interaction_policy | form `quote_approval_policy` | `write:statuses`; 4.5.0 | none | COMPLIANT (version gate missing; see summary 5) |
| ST-16 | POST /statuses/:id/quotes/:quoting_id/revoke | none | `write:statuses`; 4.5.0 | none | COMPLIANT (ungated) |
| ST-17 | GET /statuses/:id/favourited_by, /reblogged_by | `limit` via PagedList | public/`read:statuses`; 0.0.0; limit 40/80 | none | COMPLIANT |
| ST-18 | GET /statuses/:id/quotes | `limit` via TimelinePage (Link paging) | `read:statuses`; 4.5.0; limit 20/40 | none | COMPLIANT (ungated) |

### accounts

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| AC-1 | GET /accounts/verify_credentials | none | `read:accounts`/`profile`; 0.0.0 (`profile` scope 4.3.0) | none | COMPLIANT |
| AC-2 | GET /accounts/:id | none | public; 0.0.0 | none | COMPLIANT |
| AC-3 | GET /accounts/lookup | `acct` | `read:accounts`; 3.4.0 | none | COMPLIANT |
| AC-4 | GET /accounts | `id[]` | public; 4.3.0 | none (error = empty) | COMPLIANT (ungated) |
| AC-5 | GET /accounts/:id/statuses | `limit`, `max_id`, `pinned`, `exclude_replies`, `exclude_reblogs`, `only_media` | public/`read:statuses`; 0.0.0; limit 20/40 | none | COMPLIANT |
| AC-6 | GET /accounts/:id/followers, /following | `limit` (80 in `searchprovider`) | public; limit 40/80 | none | COMPLIANT |
| AC-7 | GET /accounts/:id/featured_tags | none | public; 3.3.0 | none | COMPLIANT |
| AC-8 | GET /accounts/:id/lists | none | `read:lists`; 2.1.0 | none | COMPLIANT |
| AC-9 | POST /accounts/:id/follow | form `reblogs`, `notify` (never `languages[]`) | `write:follows`; 0.0.0, `notify` 3.3.0 | none | COMPLIANT |
| AC-10 | POST /accounts/:id/unfollow, /remove_from_followers | none | `write:follows`; remove 3.5.0 | none | COMPLIANT |
| AC-11 | POST /accounts/:id/block, /unblock | none | `write:blocks` | none | COMPLIANT |
| AC-12 | POST /accounts/:id/mute | form `notifications`, `duration` | `write:mutes`; `duration` 3.3.0 | none | COMPLIANT |
| AC-13 | POST /accounts/:id/unmute | none | `write:mutes` | none | COMPLIANT |
| AC-14 | POST /accounts/:id/endorse, /unendorse | none | `write:accounts`; 4.4.0 per page | `is_supported(ENDORSE_API_VERSION=6, when_unknown=True)` | COMPLIANT. RESOLVED (5f): v6 is v4.4.0, as the page says; the "4.6.0" comment was the error |
| AC-15 | POST /accounts/:id/pin, /unpin | none | `write:accounts`; deprecated 4.4.0 | used as 404 fallback | DEPRECATED (replacement: AC-14; kept for servers older than the endorse aliases) |
| AC-16 | POST /accounts/:id/note | form `comment` | `write:accounts`; 3.2.0 | none | COMPLIANT |
| AC-17 | GET /accounts/relationships | `id[]` | `read:follows`; 0.0.0 (`with_suspended` 4.3.0 not sent) | none | COMPLIANT |
| AC-18 | GET /accounts/familiar_followers | `id[]` | `read:follows`; 3.5.0 | none | COMPLIANT |
| AC-19 | GET /accounts/search | `q`, `limit`, `following`, `resolve` | `read:accounts`; `limit` max 80 | none | COMPLIANT |
| AC-20 | GET /accounts/:id/endorsements | `limit` 80 | public; 4.4.0 | none | COMPLIANT (ungated) |
| AC-21 | PATCH /accounts/update_credentials | form: `source[privacy|language|sensitive|quote_policy]` (preferences); multipart `display_name`, `note`, `avatar`, `header`, `locked`, `bot`, `discoverable`, `fields_attributes[i][name|value]`, `source[sensitive]` (profile edit) | `write:accounts`; `source[quote_policy]` 4.5.0 | none for `quote_policy` | COMPLIANT (quote policy ungated) |
| AC-22 | PATCH /profile | form: tab flags, alt text | `write:accounts`; 4.6.0 (API 8), alt text API 9 | `>= 8`, `>= 9` | COMPLIANT |
| AC-23 | GET /accounts/:id/collections, /in_collections | `limit`, `offset` | public/`read:collections`; 4.6.0 (API 10) | `>= 10` | COMPLIANT |

### relationships and lists of people

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| RL-1 | GET /follow_requests | `limit` | `read:follows`; 0.0.0; 40/80 | none | COMPLIANT |
| RL-2 | POST /follow_requests/:id/authorize, /reject | none | `write:follows`; 0.0.0 | none | COMPLIANT |
| RL-3 | GET /blocks | `limit` | `read:blocks`; 40/80 | none | COMPLIANT |
| RL-4 | GET /mutes | `limit` | `read:mutes`; 40/80 | none | COMPLIANT |
| RL-5 | GET /domain_blocks | `limit` | `read:blocks`; 1.4.0; 100/200 | none | COMPLIANT |
| RL-6 | POST /domain_blocks | form `domain` | `write:blocks`; 1.4.0 | none | COMPLIANT |
| RL-7 | DELETE /domain_blocks | form `domain` | `write:blocks`; form `domain` | none | COMPLIANT after 5e (was: parameter in the query) |

### lists

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| LS-1 | GET /lists | none | `read:lists`; 2.1.0 | none | COMPLIANT |
| LS-2 | POST /lists, PUT /lists/:id | form `title`, `replies_policy`, `exclusive` | `write:lists`; `exclusive` 4.2.0 | none | COMPLIANT (`exclusive` ungated) |
| LS-3 | DELETE /lists/:id | none | `write:lists` | none | COMPLIANT |
| LS-4 | GET /lists/:id/accounts | `limit=0` (all) | `read:lists`; `limit=0` documented | none | COMPLIANT |
| LS-5 | POST /lists/:id/accounts | form `account_ids[]` | `write:lists` | none | COMPLIANT |
| LS-6 | DELETE /lists/:id/accounts | form `account_ids[]` | `write:lists`; form `account_ids[]` | none | COMPLIANT after 5e (was: query instead of form) |

### timelines

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| TL-1 | GET /timelines/home | `limit`, `max_id`, `min_id`, `since_id` | `read:statuses`; `exclude_*` API 12 not sent | none | COMPLIANT |
| TL-2 | GET /timelines/public | `local`, ids, `limit` | public or `read:statuses`; 0.0.0; 4.5 access control | `configuration.timelines_access` (hides sides) | COMPLIANT |
| TL-3 | GET /timelines/tag/:hashtag | `any[]`, `all[]`, `none[]`, `local`, ids | public; `any/all/none` 2.7.0 | none | COMPLIANT |
| TL-4 | GET /timelines/link | `url` | public; 4.3.0 | `is_supported(LINK_TIMELINE_API_VERSION=2)` | COMPLIANT |
| TL-5 | GET /timelines/list/:id | ids | `read:lists` | none | COMPLIANT |
| TL-6 | GET /bookmarks, /favourites | `limit`, Link paging (`link_paged`) | `read:bookmarks` / `read:favourites`; 3.1.0 / 0.0.0; 20/40 | none | COMPLIANT |
| TL-7 | GET /conversations | `limit` via PagedList | `read:statuses`; 2.6.0; 20/40 | none | COMPLIANT |
| TL-8 | POST /conversations/:id/read, DELETE /conversations/:id | none | `write:conversations`; 2.6.0 | none | COMPLIANT |

### tags

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| TG-1 | GET /tags/:name | none | public; 4.0.0 | none | COMPLIANT |
| TG-2 | POST /tags/:name/follow, /unfollow | none | `write:follows`; 4.0.0 | none | COMPLIANT |
| TG-3 | GET /followed_tags | `limit` 100/200, Link paging in Catch Up | `read:follows`; 4.0.0; 100/200 | none | COMPLIANT |
| TG-4 | GET /featured_tags, /featured_tags/suggestions | none | `read:accounts`; 3.0.0 | none | COMPLIANT |
| TG-5 | POST /featured_tags, DELETE /featured_tags/:id | form `name` | `write:accounts`; 3.0.0 | none | COMPLIANT (docs also list `POST /tags/:id/feature`, 4.4.0, not used) |

### search

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| SR-1 | GET /api/v2/search | `q`, `type`, `limit` (20, 10, 5, 1), `offset`, `resolve`, `following` not sent | public; `resolve`/`offset` need a user token; limit max 40 | none | COMPLIANT |

### notifications

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| NT-1 | GET /notifications | `types[]`, `exclude_types[]`, `account_id`, `include_filtered`, ids, `limit` | `read:notifications`; `include_filtered` 4.3.0; 40/80 | none | COMPLIANT |
| NT-2 | GET /notifications/unread_count | `types[]`, `exclude_types[]`, `limit` (default 100, max 1000) | `read:notifications`; **4.3.0** | none | COMPLIANT after 5d (sends `limit=100`, badge shows `100+`; ungated; code comment says "4.0+", docs say 4.3.0) |
| NT-3 | POST /notifications/clear | none | `write:notifications` | none | COMPLIANT |
| NT-4 | POST /notifications/:id/dismiss | none | `write:notifications`; 1.3.0 | none | COMPLIANT |
| NT-5 | GET /api/v2/notifications/policy | none | `read:notifications`; 4.3.0 | none | COMPLIANT (ungated) |
| NT-6 | PATCH /api/v2/notifications/policy | form `for_*` values | `write:notifications`; 4.3.0; form data | none | COMPLIANT after 5e (was: JSON body) |
| NT-7 | GET /notifications/requests | `limit` via PagedList | `read:notifications`; 4.3.0 | none | COMPLIANT (ungated) |
| NT-8 | POST /notifications/requests/:id/accept, /dismiss | none | `write:notifications`; 4.3.0 | none | COMPLIANT (ungated) |
| NT-9 | GET /markers | `timeline[]=notifications` | `read:statuses`; 3.0.0 | none | COMPLIANT |
| NT-10 | POST /markers | form `notifications[last_read_id]` | `write:statuses`; 3.0.0 | none | COMPLIANT |

### filters

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| FL-1 | GET /api/v2/filters | none | `read:filters`; 4.0.0 | none | COMPLIANT |
| FL-2 | POST /api/v2/filters, PUT /api/v2/filters/:id | JSON `title`, `context[]`, `filter_action`, `expires_in`, `keywords_attributes[]` | `write:filters`; form data; `blur` 4.4.0 | none | ACCEPTED-UNDOCUMENTED: JSON body (docs form only), kept in 5e (`expires_in: null` and `keywords_attributes`; see item 1) |
| FL-3 | DELETE /api/v2/filters/:id | none | `write:filters` | none | COMPLIANT |

### media

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| MD-1 | POST /api/v2/media | multipart `file`, `description` | `write:media`; 3.1.3; 200 vs 202 | none | COMPLIANT (`thumbnail`, `focus` not sent) |
| MD-2 | GET /media/:id | none | `write:media`; 3.1.3; **206** while processing | none | COMPLIANT (check that 206 with an empty body counts as "processing" in `composeupload`; not run) |

### polls

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| PL-1 | GET /polls/:id | none | public; 2.8.0 | none | COMPLIANT |
| PL-2 | POST /polls/:id/votes | form `choices[]` | `write:statuses`; form `choices[]` | none | COMPLIANT after 5e (was: JSON body) |

### scheduled statuses

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| SC-1 | GET /scheduled_statuses | `limit` via PagedList | `read:statuses`; 2.7.0 | none | COMPLIANT |
| SC-2 | PUT /scheduled_statuses/:id | form `scheduled_at` | `write:statuses` | none | COMPLIANT |
| SC-3 | DELETE /scheduled_statuses/:id | none | `write:statuses` | none | COMPLIANT |

### announcements

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| AN-1 | GET /announcements | none | user token; 3.1.0 | none | COMPLIANT |
| AN-2 | POST /announcements/:id/dismiss | none | `write:accounts`; 3.1.0 | none | COMPLIANT |
| AN-3 | PUT/DELETE /announcements/:id/reactions/:name | name quoted? (see call site) | `write:favourites`; 5.0.0 may answer 400 | none | COMPLIANT (400 "reactions not supported" handling unchecked) |

### reports

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| RP-1 | POST /reports | form `account_id`, `category`, `status_ids[]`, `rule_ids[]`, `collection_ids[]`, `comment`, `forward` | `write:reports`; `collection_ids` 4.6.0 | sent only when non-empty | COMPLIANT |

### admin

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| AD-1 | GET /admin/reports | `resolved` | `admin:read:reports`; 2.9.1; 100/200 | admin consent flow | COMPLIANT |
| AD-2 | PUT /admin/reports/:id | JSON `category`, `rule_ids` | `admin:write:reports`; 3.5.0; form data | admin consent | ACCEPTED-UNDOCUMENTED: JSON body, kept in 5e (`rule_ids: []` has no form encoding; see item 1) |
| AD-3 | POST /admin/reports/:id/assign_to_self, /unassign, /resolve, /reopen | none | `admin:write:reports` | admin consent | COMPLIANT |
| AD-4 | GET /api/v2/admin/accounts | `origin`, `status`, `username`, `display_name`, `by_domain`, `email`, `ip` | `admin:read:accounts`; 3.5.0 | admin consent | COMPLIANT |
| AD-5 | GET /admin/accounts (v1) | `limit=1` (scope probe) | `admin:read:accounts`; 2.9.1; not marked deprecated | admin consent | COMPLIANT (v1 not deprecated per docs) |
| AD-6 | POST /admin/accounts/:id/action | form `type`, `report_id`, `text`, `send_email_notification`, `warning_preset_id` | `admin:write:accounts` | admin consent | COMPLIANT |
| AD-7 | POST /admin/accounts/:id/(approve, reject, enable, unsilence, unsuspend, unsensitive) | none | `admin:write:accounts`; `unsensitive` 3.3.0 | admin consent | COMPLIANT |
| AD-8 | GET/POST /admin/domain_blocks, DELETE /admin/domain_blocks/:id | form `domain`, `severity`, `reject_media`, `reject_reports`, `obfuscate`, comments | `admin:*:domain_blocks`; 4.0.0 | admin consent | COMPLIANT |
| AD-9 | GET/POST /admin/domain_allows, DELETE .../:id | form `domain` | `admin:*:domain_allows`; 4.0.0 | admin consent | COMPLIANT |
| AD-10 | GET/POST /admin/email_domain_blocks, DELETE .../:id | form `domain` | `admin:*:email_domain_blocks`; 4.0.0 | admin consent | COMPLIANT |
| AD-11 | GET/POST /admin/ip_blocks, DELETE .../:id | form `ip`, `severity`, `comment`, `expires_in` | `admin:*:ip_blocks`; docs list `admin:write:domain_blocks` for DELETE (probably a docs typo) | admin consent | COMPLIANT (docs scope conflict noted) |
| AD-12 | POST /admin/measures, /admin/dimensions | form `keys[]`, `start_at`, `end_at`, `limit` | `admin:read`; 3.5.0; form data | admin consent | COMPLIANT after 5e (was: JSON body) |

### oauth, apps, instance

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| OA-1 | POST /apps | form `client_name`, `redirect_uris` (space-joined string), `scopes`, `website` | public; `redirect_uris` array/string 4.3.0 | none | COMPLIANT (5g): scalar string read by every server version; Doorkeeper splits on whitespace, so the delimiter is cosmetic. Array form rejected before 4.3.0. |
| OA-2 | GET /oauth/authorize (browser URL) | `response_type`, `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge`, `code_challenge_method=S256` | PKCE 4.3.0 | PKCE always sent | COMPLIANT (servers before 4.3 ignore PKCE) |
| OA-3 | POST /oauth/token | form `grant_type=authorization_code`, `code`, `client_id`, `client_secret`, `redirect_uri`, `code_verifier` | 0.1.0 | none | COMPLIANT |
| OA-4 | POST /oauth/revoke | form `client_id`, `client_secret`, `token` | 0.1.0 | none | COMPLIANT |
| OA-5 | GET /api/v2/instance | none | public; 4.0.0, `api_versions` 4.3.0 | none | COMPLIANT (v1 instance is deprecated and not used) |
| OA-6 | Scopes `read write`, `admin:read admin:write` | n/a | `follow` listed on the oauth page, not marked deprecated there; accounts page says deprecated for granular scopes since 3.5.0 | n/a | COMPLIANT |

### other

| Id | Method path | Params | Docs: scope; added | Gate | Verdict |
|---|---|---|---|---|---|
| OT-1 | GET /custom_emojis | none | public; 2.0.0 | none | COMPLIANT |
| OT-2 | GET /api/v2/suggestions | `limit` 40 | `read`; 3.4.0 (v1 deprecated, not used) | none | COMPLIANT |
| OT-3 | DELETE /suggestions/:id | none | `read`; 2.4.3 | none | COMPLIANT |
| OT-4 | GET /trends/tags, /trends/links, /trends/statuses | `limit` 20 (tags/links max 20), 40 (statuses max 40) | public; 3.0/3.5 | none | COMPLIANT |
| OT-5 | GET /annual_reports, /annual_reports/:year | none | `read:accounts`; 4.3.0 / 4.4.0 | none | COMPLIANT |
| OT-6 | GET /annual_reports/:year/state, POST /annual_reports/:year/generate | none | `read:accounts` / `write:accounts`; 4.6.0 (API 8) | gated on API 8 since 5c (`ANNUAL_REPORT_STATE_API_VERSION`); a 404 is still treated as "empty" | COMPLIANT (was DEVIATES [minor]: ungated 4.6.0 endpoints, fixed in 5c) |
| OT-7 | (phase 3b: declared in `src/api/collections_api.py` from docs.joinmastodon.org/methods/collections/, read 2026-11-05: anchors `#create`, `#get_collection`, `#get_collections`, `#in_collections`, `#update_collection`, `#delete_collection`, `#add_account`, `#remove_account`, `#revoke_item`; the docs label create/update/add parameters form data but their examples are JSON; no new deviation) GET/POST /collections, GET/PATCH/DELETE /collections/:id, POST /collections/:id/items, DELETE .../items/:item, POST .../items/:item/revoke | JSON `name`, `description`, `language`, `tag_name`, `sensitive`, `discoverable`, `account_id` | `read/write:collections`; 4.6.0 (API 10); docs example shows JSON | `>= 10` | COMPLIANT |
| OT-8 | WebSocket /api/v1/streaming?stream=user (+ subscribe/unsubscribe messages) | `Authorization: Bearer` header | 3.3.0 websocket; header auth preferred | `configuration.timelines_access` for public | COMPLIANT (token not in the query) |

Plugin host requests (`Context.request`) go to third-party servers
(wallabag, hashpanel). They are not Mastodon endpoints and are not
audited.

**Tally (118 table rows; a row may cover a method pair or an id family; after 5g):** COMPLIANT 112, DEVIATES 0, ACCEPTED-UNDOCUMENTED 4, DEPRECATED 1, UNDOCUMENTED 0, UNVERIFIED 0. Of the COMPLIANT rows, about 15 are version-ungated 4.3 to 4.6 endpoints noted in the summary. UNVERIFIED applies to the `api_versions` number mapping (gate v6) rather than to a whole endpoint row; it is reported in section 2.

## 4. Call sites per endpoint (migration list)

`file:line` of the request statement. Wrapper = a `MastodonClient`
method. "via" names a shared helper that carries the real call.

**ST-1** POST /statuses: `compose.py` via `api.statuses.post_async`, reply uses the same (`reply_async`).
**ST-2** GET /statuses/:id: `compose.py:986`, `desktopnotify.py:449`, `searchprovider.py:685` (via `_fetch`, callers 701), `statusactions.py:126` (pages.call), `wrapped.py:588`.
**ST-3** PUT /statuses/:id: `compose.py` via `api.statuses.edit_async`.
**ST-4** DELETE /statuses/:id: `statusmenu.py:517`, `statusmenu.py:537`.
**ST-5** context: `compose.py:971`, `thread.py:271` (status_context_async).
**ST-6** history: `statusviews.py:182`.
**ST-7** source: `statusmenu.py:454`.
**ST-8** translate: `statusmenu.py:366`.
**ST-9..12** favourite/unfavourite/reblog/unreblog/bookmark/unbookmark: `statusactions.py:553` (verb variable), `statusactions.py:572` (re-boost), `desktopnotify.py:353-355` (`_ACTIONS` table, run at 517). Unused wrappers `mastodon.py:1356-1408`.
**ST-13/14** mute/unmute/pin/unpin status: `statusmenu.py:383` (via `_post_action`; callers 387-400).
**ST-15** interaction_policy: `statusmenu.py:415`.
**ST-16** quotes revoke: `statusmenu.py:433`.
**ST-17/18** favourited_by/reblogged_by/quotes: `statusviews.py:17-19` table, `statusviews.py:75/78/81-83` (AccountListPage / ApiTimelineSource).

**AC-1** verify_credentials: `auth.py:543`, `window.py:841`, `profileedit.py:129,714`, `profile.py:1194` (dynamic path), `fedicollections.py:384`.
**AC-2** account: `profile.py:1195` (reload_account), `searchprovider.py:692` (via `_fetch`).
**AC-3** lookup: `profile.py:1324` (lookup_account_async).
**AC-4** accounts?id[]: `fedicollections.py:113`.
**AC-5** statuses: `profile.py:130,142,145` (account_statuses_async), `report.py:318` (PagedList), `timelinepage.py:70` path rule.
**AC-6** followers/following: `profile.py:843`, `searchprovider.py:448`.
**AC-7** featured_tags: `profile.py:1027`.
**AC-8** lists of account: `lists.py:639`.
**AC-9** follow: `explore.py:291`, `notifications.py:1323`, `desktopnotify.py:357`, `profileactions.py` (send_relationship 'follow' at 338, set_boosts 387, set_notifying 394, undo 334). Unused wrapper `mastodon.py:1243`.
**AC-10** unfollow/remove_from_followers: `profileactions.py:211-231` via 338.
**AC-11** block/unblock: `profileactions.py:243-249` via 338; `statusmenu.py:598`; `relations.py:129`.
**AC-12/13** mute/unmute: `profileactions.py:376-382` via 338; `statusmenu.py:582,588`; `relations.py:104`.
**AC-14/15** endorse/pin: `profileactions.py:443` (names tuple at 27).
**AC-16** note: `profileactions.py:472`.
**AC-17** relationships: `notificationctrl.py:116` (raw), `catchup/fetch.py:279`, `homefilters.py:96`, `profile.py:1216`, `timelinepage.py:495` (relationships_async).
**AC-18** familiar_followers: `profile.py:1022,1079`.
**AC-19** accounts/search: `lists.py:366`, `mentioncompleter.py:84` (search_request, run at `completer.py:284`), `fedicollections.py:888`.
**AC-20** endorsements: `profile.py:1029,1134`.
**AC-21** update_credentials: `preferences.py` (`api.accounts.update_credentials_async`, urlencoded), `profileedit.py` (`update_credentials_multipart_async`).
**AC-22** profile: `profileedit.py` via `api.accounts.update_profile_async`. Its docs URL (`methods/profile/#update`) was declared from the route name and not checked against the live docs.
**AC-23** collections of account: `fedicollections.py:389,396`.

**RL-1/2** follow_requests: `relations.py:30` (list), `relations.py:64` (mutate_row), `notifications.py:1275`.
**RL-3..7** blocks/mutes/domain_blocks: `relations.py:91,116,142`; unblock domain `relations.py:158` (DELETE with params); `profileactions.py:364` (send_domain_block, POST form / DELETE params, undo 361).

**LS-1** `lists.py:569` (PagedList), `lists.py:638`, `window.py:1577`.
**LS-2** `lists.py:235`. **LS-3** `lists.py:253,547`. **LS-4** `lists.py:298`. **LS-5** `lists.py:408,693`. **LS-6** `lists.py:331,698`.

**TL-1** home: `timelinepage.py:416`, `catchup/fetch.py:149`.
**TL-2** public: `timelinepage.py:411`, `navigation.py:157` (via `window._timeline_place` 1220).
**TL-3** tag: `hashtags.py:53,146` (`_tag_path`).
**TL-4** link: `explore.py:88`. **TL-5** list: `lists.py:449`.
**TL-6** bookmarks/favourites: `navigation.py:140,144` via `window.py:1220`.
**TL-7** conversations: `conversations.py:244`. **TL-8** `conversations.py:204,229`.

**TG-1** `hashtags.py:236`. **TG-2** `hashtags.py:286,675,695`.
**TG-3** followed_tags: `timelinepage.py:446`, `searchprovider.py:451`, `catchup/fetch.py:228,266`, `hashtags.py:818`, `hashtagcompleter.py:148,198`.
**TG-4** featured_tags (+suggestions): `hashtags.py:238`, `hashtagcompleter.py:146-147,198`.
**TG-5** `hashtags.py:312,316`.

**SR-1** search: `search.py:184,471`, `pages.py:1108`, `profile.py:1341`, `searchprovider.py:314`, `hashtagcompleter.py:336` (run at `completer.py:284`).

**NT-1** `notificationctrl.py:140`, `desktopnotify.py:311`. **NT-2** `notificationctrl.py:442`. **NT-3** `notificationctrl.py:569`. **NT-4** `notificationctrl.py:548`. **NT-5** `notificationctrl.py:351`, `notifications.py:1842`. **NT-6** `notifications.py:1893`. **NT-7** `notifications.py:1719`. **NT-8** `notifications.py:1700`. **NT-9** `notificationctrl.py:421`. **NT-10** `notificationctrl.py:495`. Also `notificationctrl.py:385` is AN-1.

**FL-1** `filters.py:391`. **FL-2** `filters.py:320`. **FL-3** `filters.py:340`.
**MD-1** `composeupload.py` (`api.media.upload_async`). **MD-2** `composeupload.py` (`api.media.status_async`).
**PL-1** `pollbox.py:223`. **PL-2** `pollbox.py:211`.
**SC-1** `scheduled.py:539`. **SC-2** `scheduled.py:486`. **SC-3** `scheduled.py:517`, `compose.py:2116`.
**AN-1** `notificationctrl.py:385`, `announcements.py:304`. **AN-2** `announcements.py:289`. **AN-3** `announcements.py:156,179`.
**RP-1** `report.py:450`.

**AD-1** `admin.py:551`. **AD-2** `admin.py:747`. **AD-3** `admin.py:775,854`. **AD-4** `admin.py:1015`. **AD-5** `admin.py:1701`. **AD-6** `admin.py:943`. **AD-7** `admin.py:1095,1185`. **AD-8** `admin.py:1257,1368,1231`. **AD-9** `admin.py:1375,1398,1231`. **AD-10** `admin.py:1410,1441,1231`. **AD-11** `admin.py:1476,1567,1231`. **AD-12** `admin.py:418,422`. (`admin.py:1212` PagedList and `1231` DELETE take their path from a `path` variable shared by AD-8..11.)

**OA-1** `auth.py:261,280` (register_app_async). **OA-3** `auth.py:489`. **OA-4** `window.py:697`. **OA-5** `window.py:825`, `admin.py:724`, `announcements.py:319`, `profileedit.py:118`, `report.py:171`, `timelinebase.py:276` (instance_info_async).

**OT-1** `emojichooser.py:94`. **OT-2** `explore.py:323`. **OT-3** `explore.py:309`. **OT-4** `explore.py:346,364,409`, `catchup/pipeline.py:64` (run through `catchup/fetch.py:299`). **OT-5/6** `wrapped.py:263,350,367`. **OT-7** `fedicollections.py:231,247,482,804,844,861,977,984`. **OT-8** `streaming.py:300`.

## 5. Phase 1 module grouping

One `src/api/<resource>.py` per row (functions taking the client, `<name>_async` / `<name>_finish`). Keep each under 800 lines.

| Module | Endpoints |
|---|---|
| `statuses.py` | ST-1..ST-18 |
| `accounts.py` | AC-1..AC-8, AC-17..AC-23 (reads, profile edit, search, familiar followers, relationships GET) |
| `relationships.py` | AC-9..AC-16 (follow, unfollow, block, mute, endorse/pin, note), RL-1..RL-7 |
| `lists.py` | LS-1..LS-6, TL-5 |
| `timelines.py` | TL-1..TL-4, TL-6, TL-7, TL-8 (home, public, tag, link, bookmarks, favourites, conversations) |
| `tags.py` | TG-1..TG-5 |
| `search.py` | SR-1 (the three completers keep their own hooks, calling it) |
| `notifications.py` | NT-1..NT-10 (policy, requests, markers) |
| `filters.py` | FL-1..FL-3 |
| `media.py` | MD-1, MD-2 |
| `polls.py` | PL-1, PL-2 |
| `scheduled.py` | SC-1..SC-3 |
| `announcements.py` | AN-1..AN-3 |
| `reports.py` | RP-1 |
| `collections.py` | OT-7, AC-23 |
| `admin.py` | AD-1..AD-12 (split into `admin_reports.py`, `admin_accounts.py`, `admin_rules.py` if over 800 lines) |
| `oauth.py` | OA-1..OA-4 (moves out of `mastodon.py`; instance and token plumbing stay) |
| `instance.py` | OA-5 and the capability accessors' fetch |
| `misc.py` (or `discovery.py`, `reports_year.py`) | OT-1 custom emojis, OT-2/3 suggestions, OT-4 trends, OT-5/6 annual reports |

`streaming.py` (OT-8) stays as it is; `request_async`, the paging
envelope and `multipart_async` stay on `MastodonClient` per the plan's
escape hatch.

For the registry, every row needs these fields beyond the columns above:
`min_api_version` (from this table; AC-14 and OT-6 need a decision first),
`scope_babble_requests` (`read`, `write`, `admin:read`, `admin:write`),
`body_encoding` (`form`, `json`, `multipart`, `query`), and a free-text
`note` for the docs-vs-behaviour cases (DELETE-with-query, JSON bodies).

## 6. Golden-log notes for phase 0 recorder

Requests where the encoding differs from the docs and must be pinned
byte-for-byte before moving, so that phase 5 is the only place they
change: ST-1, ST-3 (JSON), FL-2, NT-6, PL-2, AD-2, AD-12 (JSON), RL-7,
LS-6 (query on DELETE), OA-1 (space-joined `redirect_uris`), AC-21
(multipart vs form).
