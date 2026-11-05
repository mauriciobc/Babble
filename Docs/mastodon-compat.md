# Mastodon API compatibility

*Where Babble gates on the server's capabilities, and why.*

Verified against Mastodon **4.7.3** (stable) and `mastodon/mastodon@main`
(4.8.0-alpha.3, `api_versions.mastodon` **12**). The published docs at
docs.joinmastodon.org document up to API version 11; individual pages
annotate the version a feature was added in.

The rules from `docs.joinmastodon.org/api/guidelines/`: prefer
`api_versions.mastodon` from `GET /api/v2/instance` over parsing version
strings, treat ids as opaque strings, follow the `Link` header for
paging, and honour the `Deprecation` header (RFC 9745, sent since
Mastodon 4.4.0). Babble does all four.

## Capability gates

| Feature | Introduced | Gate | Where |
|---|---|---|---|
| `api_versions.mastodon` | 4.3.0 | cached per client after first fetch | `MastodonClient.instance_info_*` (caching and coalescing, ARCH-09), sending `api.instance.info_async` |
| Collections | 4.6.0 (v10) | `>= 10` | `window._update_session_actions` |
| Profile tabs (`PATCH /api/v1/profile`) | 4.6.0 (v8) | `>= 8` | `profileedit.PROFILE_TABS_VERSION` |
| Avatar/header alt text (`PATCH /profile`) | 4.6.0 (v9) | `>= 9` | `profileedit.PROFILE_ALT_TEXT_VERSION` |
| Avatar/header alt text (`PATCH /accounts/update_credentials`) | 4.6.1 (v11) | not used; carried by `/profile` instead | `profileedit.collect` |
| `endorse`/`unendorse` aliases | 4.6.0 (v6) | `is_supported(ENDORSE_API_VERSION, when_unknown=True)`; a 404 retries `pin`/`unpin` | `ProfilePage.set_endorsed` |
| Translation (`POST /statuses/:id/translate`) | 4.0.0 | `configuration.translation.enabled` | `timeline._translation_enabled` |
| Live feed access (`live_feeds`) | 4.5.0 | `configuration.timelines_access` | `streaming.public_feed_access` |
| Trending-link timeline | 4.3.0 | `is_supported(LINK_TIMELINE_API_VERSION)` | `explore.supports_link_timeline` |

A gate is answered through `client.is_supported(min_version)`, never by
comparing `mastodon_api_version()` at the call site: that accessor
returns `None` when the instance says nothing, and `None` does not
compare. `is_supported` defaults to `when_unknown=False`, so an unproven
server is told no — the versioned part stays out of the UI instead of
being asked for and 404ing. `ProfilePage.set_endorsed` is the one caller
that wants the other policy, and states it as
`is_supported(ENDORSE_API_VERSION, when_unknown=True)`.
`explore.supports_link_timeline` uses the default
`when_unknown=False` policy instead.

Two tests hold this in place: `version gate call sites` fails on a
comparison written against the accessor, and on the human `version` string
being *parsed* to decide a gate (as `explore.py` once did).

## Endpoints

Every endpoint Babble declares in `src/api/`, generated from
`api.core.registry`. Do not edit the block: change the declaration and run
`python3 src/test-api-registry.py --write-table`; the `api registry` test
fails while the block is out of date. Mastodon is the release the docs name
for the endpoint (blank when they name none).

<!-- api-table:start -->
| Method | Path | Scope | Mastodon | Deprecated | Docs |
|---|---|---|---|---|---|
| GET | `/accounts` | none | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#index) |
| GET | `/accounts/%s` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#get) |
| POST | `/accounts/%s/block` | `write:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#block) |
| GET | `/accounts/%s/collections` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#get_collections) |
| POST | `/accounts/%s/endorse` | `write:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#endorse) |
| GET | `/accounts/%s/endorsements` | none | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#endorsements) |
| GET | `/accounts/%s/featured_tags` | none | 3.3.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#featured_tags) |
| POST | `/accounts/%s/follow` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#follow) |
| GET | `/accounts/%s/followers` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#followers) |
| GET | `/accounts/%s/following` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#following) |
| GET | `/accounts/%s/in_collections` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#in_collections) |
| GET | `/accounts/%s/lists` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#lists) |
| POST | `/accounts/%s/mute` | `write:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#mute) |
| POST | `/accounts/%s/note` | `write:accounts` | 3.2.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#note) |
| POST | `/accounts/%s/pin` | `write:accounts` |  | endorse | [docs](https://docs.joinmastodon.org/methods/accounts/#pin) |
| POST | `/accounts/%s/remove_from_followers` | `write:follows` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#remove_from_followers) |
| GET | `/accounts/%s/statuses` | none |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#statuses) |
| POST | `/accounts/%s/unblock` | `write:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unblock) |
| POST | `/accounts/%s/unendorse` | `write:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unendorse) |
| POST | `/accounts/%s/unfollow` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unfollow) |
| POST | `/accounts/%s/unmute` | `write:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#unmute) |
| POST | `/accounts/%s/unpin` | `write:accounts` |  | unendorse | [docs](https://docs.joinmastodon.org/methods/accounts/#unpin) |
| GET | `/accounts/familiar_followers` | `read:follows` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#familiar_followers) |
| GET | `/accounts/lookup` | `read:accounts` | 3.4.0 |  | [docs](https://docs.joinmastodon.org/methods/accounts/#lookup) |
| GET | `/accounts/relationships` | `read:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#relationships) |
| GET | `/accounts/search` | `read:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#search) |
| PATCH | `/accounts/update_credentials` | `write:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#update_credentials) |
| GET | `/accounts/verify_credentials` | `read:accounts` |  |  | [docs](https://docs.joinmastodon.org/methods/accounts/#verify_credentials) |
| GET | `/admin/accounts` | `admin:read:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#v1) |
| POST | `/admin/accounts/%s/action` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#action) |
| POST | `/admin/accounts/%s/approve` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#approve) |
| POST | `/admin/accounts/%s/enable` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#enable) |
| POST | `/admin/accounts/%s/reject` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#reject) |
| POST | `/admin/accounts/%s/unsensitive` | `admin:write:accounts` | 3.3.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsensitive) |
| POST | `/admin/accounts/%s/unsilence` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsilence) |
| POST | `/admin/accounts/%s/unsuspend` | `admin:write:accounts` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#unsuspend) |
| POST | `/admin/dimensions` | `admin:read` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/dimensions/#get) |
| GET | `/admin/domain_allows` | `admin:read:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#get) |
| POST | `/admin/domain_allows` | `admin:write:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#create) |
| DELETE | `/admin/domain_allows/%s` | `admin:write:domain_allows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_allows/#delete) |
| GET | `/admin/domain_blocks` | `admin:read:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#get) |
| POST | `/admin/domain_blocks` | `admin:write:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#create) |
| DELETE | `/admin/domain_blocks/%s` | `admin:write:domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/domain_blocks/#delete) |
| GET | `/admin/email_domain_blocks` | `admin:read:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#get) |
| POST | `/admin/email_domain_blocks` | `admin:write:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#create) |
| DELETE | `/admin/email_domain_blocks/%s` | `admin:write:email_domain_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/email_domain_blocks/#delete) |
| GET | `/admin/ip_blocks` | `admin:read:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#get) |
| POST | `/admin/ip_blocks` | `admin:write:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#create) |
| DELETE | `/admin/ip_blocks/%s` | `admin:write:ip_blocks` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/ip_blocks/#delete) |
| POST | `/admin/measures` | `admin:read` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/measures/#get) |
| GET | `/admin/reports` | `admin:read:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#get) |
| PUT | `/admin/reports/%s` | `admin:write:reports` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#update) |
| POST | `/admin/reports/%s/assign_to_self` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#assign_to_self) |
| POST | `/admin/reports/%s/reopen` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#reopen) |
| POST | `/admin/reports/%s/resolve` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#resolve) |
| POST | `/admin/reports/%s/unassign` | `admin:write:reports` | 2.9.1 |  | [docs](https://docs.joinmastodon.org/methods/admin/reports/#unassign) |
| GET | `/announcements` | none | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#get) |
| POST | `/announcements/%s/dismiss` | `write:accounts` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#dismiss) |
| DELETE | `/announcements/%s/reactions/%s` | `write:favourites` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#delete-reactions) |
| PUT | `/announcements/%s/reactions/%s` | `write:favourites` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/announcements/#put-reactions) |
| GET | `/annual_reports` | `read:accounts` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#get) |
| GET | `/annual_reports/%d` | `read:accounts` | 4.4.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#get-one) |
| POST | `/annual_reports/%d/generate` | `write:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#generate) |
| GET | `/annual_reports/%d/state` | `read:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/annual_reports/#state) |
| GET | `/api/v2/admin/accounts` | `admin:read:accounts` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/admin/accounts/#v2) |
| GET | `/api/v2/filters` | `read:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#get) |
| POST | `/api/v2/filters` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#create) |
| DELETE | `/api/v2/filters/%s` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#delete) |
| PUT | `/api/v2/filters/%s` | `write:filters` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/filters/#update) |
| GET | `/api/v2/instance` | none | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/instance/#v2) |
| POST | `/api/v2/media` | `write:media` | 3.1.3 |  | [docs](https://docs.joinmastodon.org/methods/media/#v2) |
| GET | `/api/v2/notifications/policy` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get-policy) |
| PATCH | `/api/v2/notifications/policy` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#update-the-filtering-policy) |
| GET | `/api/v2/search` | `read:search` | 2.4.1 |  | [docs](https://docs.joinmastodon.org/methods/search/#v2) |
| GET | `/api/v2/suggestions` | `read` | 3.4.0 |  | [docs](https://docs.joinmastodon.org/methods/suggestions/#v2) |
| GET | `/blocks` | `read:blocks` |  |  | [docs](https://docs.joinmastodon.org/methods/blocks/#get) |
| GET | `/bookmarks` | `read:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/bookmarks/#get) |
| POST | `/collections` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#create) |
| DELETE | `/collections/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#delete_collection) |
| GET | `/collections/%s` | `read:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#get_collection) |
| PATCH | `/collections/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#update_collection) |
| POST | `/collections/%s/items` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#add_account) |
| DELETE | `/collections/%s/items/%s` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#remove_account) |
| POST | `/collections/%s/items/%s/revoke` | `write:collections` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/collections/#revoke_item) |
| GET | `/conversations` | `read:statuses` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#get) |
| DELETE | `/conversations/%s` | `write:conversations` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#delete) |
| POST | `/conversations/%s/read` | `write:conversations` | 2.6.0 |  | [docs](https://docs.joinmastodon.org/methods/conversations/#read) |
| GET | `/custom_emojis` | none | 2.0.0 |  | [docs](https://docs.joinmastodon.org/methods/custom_emojis/#get) |
| DELETE | `/domain_blocks` | `write:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#unblock) |
| GET | `/domain_blocks` | `read:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#get) |
| POST | `/domain_blocks` | `write:blocks` | 1.4.0 |  | [docs](https://docs.joinmastodon.org/methods/domain_blocks/#block) |
| GET | `/favourites` | `read:favourites` | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/favourites/#get) |
| GET | `/featured_tags` | `read:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#get) |
| POST | `/featured_tags` | `write:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#feature) |
| DELETE | `/featured_tags/%s` | `write:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#unfeature) |
| GET | `/featured_tags/suggestions` | `read:accounts` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/featured_tags/#suggestions) |
| GET | `/follow_requests` | `read:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#get) |
| POST | `/follow_requests/%s/authorize` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#accept) |
| POST | `/follow_requests/%s/reject` | `write:follows` |  |  | [docs](https://docs.joinmastodon.org/methods/follow_requests/#reject) |
| GET | `/followed_tags` | `read:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/followed_tags/#get) |
| GET | `/lists` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#get) |
| POST | `/lists` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#create) |
| DELETE | `/lists/%s` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#delete) |
| PUT | `/lists/%s` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#update) |
| DELETE | `/lists/%s/accounts` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts-remove) |
| GET | `/lists/%s/accounts` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts) |
| POST | `/lists/%s/accounts` | `write:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/lists/#accounts-add) |
| GET | `/markers` | `read:statuses` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/markers/#get) |
| POST | `/markers` | `write:statuses` | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/markers/#create) |
| GET | `/media/%s` | `write:media` | 3.1.3 |  | [docs](https://docs.joinmastodon.org/methods/media/#get) |
| GET | `/mutes` | `read:mutes` |  |  | [docs](https://docs.joinmastodon.org/methods/mutes/#get) |
| GET | `/notifications` | `read:notifications` | 1.0.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get) |
| POST | `/notifications/%s/dismiss` | `write:notifications` | 1.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#dismiss) |
| POST | `/notifications/clear` | `write:notifications` | 1.0.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#clear) |
| GET | `/notifications/requests` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#get-requests) |
| POST | `/notifications/requests/%s/accept` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#accept-request) |
| POST | `/notifications/requests/%s/dismiss` | `write:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#dismiss-request) |
| GET | `/notifications/unread_count` | `read:notifications` | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/notifications/#unread-count) |
| GET | `/polls/%s` | none | 2.8.0 |  | [docs](https://docs.joinmastodon.org/methods/polls/#get) |
| POST | `/polls/%s/votes` | `write:statuses` | 2.8.0 |  | [docs](https://docs.joinmastodon.org/methods/polls/#vote) |
| PATCH | `/profile` | `write:accounts` | 4.6.0 |  | [docs](https://docs.joinmastodon.org/methods/profile/#update) |
| POST | `/reports` | `write:reports` | 1.1.0 |  | [docs](https://docs.joinmastodon.org/methods/reports/#post) |
| GET | `/scheduled_statuses` | `read:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#get) |
| DELETE | `/scheduled_statuses/%s` | `write:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#cancel) |
| PUT | `/scheduled_statuses/%s` | `write:statuses` | 2.7.0 |  | [docs](https://docs.joinmastodon.org/methods/scheduled_statuses/#update) |
| POST | `/statuses` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#create) |
| DELETE | `/statuses/%s` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#delete) |
| GET | `/statuses/%s` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#get) |
| PUT | `/statuses/%s` | `write:statuses` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#edit) |
| POST | `/statuses/%s/bookmark` | `write:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#bookmark) |
| GET | `/statuses/%s/context` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#context) |
| POST | `/statuses/%s/favourite` | `write:favourites` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#favourite) |
| GET | `/statuses/%s/favourited_by` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#favourited_by) |
| GET | `/statuses/%s/history` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#history) |
| PUT | `/statuses/%s/interaction_policy` | `write:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#update_interaction_policy) |
| POST | `/statuses/%s/mute` | `write:mutes` | 1.4.2 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#mute) |
| POST | `/statuses/%s/pin` | `write:accounts` | 1.6.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#pin) |
| GET | `/statuses/%s/quotes` | `read:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#quotes) |
| POST | `/statuses/%s/quotes/%s/revoke` | `write:statuses` | 4.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#revoke_quote) |
| POST | `/statuses/%s/reblog` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#boost) |
| GET | `/statuses/%s/reblogged_by` | none |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#reblogged_by) |
| GET | `/statuses/%s/source` | `read:statuses` | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#source) |
| POST | `/statuses/%s/translate` | `read:statuses` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#translate) |
| POST | `/statuses/%s/unbookmark` | `write:bookmarks` | 3.1.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unbookmark) |
| POST | `/statuses/%s/unfavourite` | `write:favourites` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unfavourite) |
| POST | `/statuses/%s/unmute` | `write:mutes` | 1.4.2 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unmute) |
| POST | `/statuses/%s/unpin` | `write:accounts` | 1.6.0 |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unpin) |
| POST | `/statuses/%s/unreblog` | `write:statuses` |  |  | [docs](https://docs.joinmastodon.org/methods/statuses/#unreblog) |
| DELETE | `/suggestions/%s` | `read` | 2.4.3 |  | [docs](https://docs.joinmastodon.org/methods/suggestions/#remove) |
| GET | `/tags/%s` | none | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#get) |
| POST | `/tags/%s/follow` | `write:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#follow) |
| POST | `/tags/%s/unfollow` | `write:follows` | 4.0.0 |  | [docs](https://docs.joinmastodon.org/methods/tags/#unfollow) |
| GET | `/timelines/home` | `read:statuses` | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#home) |
| GET | `/timelines/link` | none | 4.3.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#link) |
| GET | `/timelines/list/%s` | `read:lists` | 2.1.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#list) |
| GET | `/timelines/public` | none | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#public) |
| GET | `/timelines/tag/%s` | none | 0.0.0 |  | [docs](https://docs.joinmastodon.org/methods/timelines/#tag) |
| GET | `/trends/links` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#links) |
| GET | `/trends/statuses` | none | 3.5.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#statuses) |
| GET | `/trends/tags` | none | 3.0.0 |  | [docs](https://docs.joinmastodon.org/methods/trends/#tags) |
<!-- api-table:end -->

## `configuration.timelines_access` (4.5+)

Three settings, each with `local`/`remote` sides. Only `live_feeds`
covers the two sidebar timelines Babble hides; the two *topic* settings
do **not** gate anything in Babble, by design (see below). They read
from the same two admin settings (`*_topic_feed_access`), so
`hashtag_feeds` and `trending_link_feeds` always carry identical values.

| Setting | Server-side effect on a signed-in account |
|---|---|
| `live_feeds` (`local`/`remote`) | A side set to `disabled` closes that side: `GET /timelines/public` returns empty (or is left to the other side) unless the role has "View live and topic feeds" (`0x100000`). |
| `hashtag_feeds` (`remote`; `local` is only `public`/`authenticated`) | When `remote` is `disabled`, `TagFeed` **falls back to local-only** — `GET /timelines/tag/:hashtag` answers with local posts, never an error. |
| `trending_link_feeds` (`remote`; `local` likewise) | Same local-only fallback for `LinkFeed` (`GET /timelines/link`). It does **not** affect `GET /trends/links`. |

`streaming.public_feed_access` therefore returns only `local`/`federated`
(the two feeds the sidebar can hide). The topic settings are
informational for a future capability matrix — see `Docs/ROADMAP.MD`
Phase 5 — and must not be used to hide hashtag or trending pages, because
the account can still read their local content.

## Collections (4.6+, API v10)

The full method surface is implemented in `fedicollections.py`: CRUD,
membership items, consent and the creation limit. The client-guide
integration points:

- **Consent** — `Account#feature_approval.current_user`; only
  `automatic`/`manual` may be added (`can_invite`), `denied`/`unknown`/
  `missing` are refused.
- **Member states** — `pending`/`accepted` are members; `rejected`/
  `revoked` are hidden (`_is_member`).
- **Sharing** — a post's `Status#tagged_collections` renders as a
  `CollectionCard` under the body (`timeline._show_collections`), and a
  `/collections/:id` URL resolves through `/api/v2/search?resolve=true`
  and opens `CollectionPage` in-app (`pages.fedi_link_kind`,
  `pages.open_collection`, `pages.resolve_uri`).
- **Reporting** — `POST /reports` carries `collection_ids[]`
  (`report.build_report_payload`), offered by the "Report" action on a
  Collection (`fedicollections.CollectionActions.report`).
- **Account handle** — `Account#invalid_handle` (4.7.0) shows a warning
  icon on account rows and an "Unverified Handle" chip on the profile.

## Deliberately not deprecated

- **Media upload** uses `POST /api/v2/media` (v1 was deprecated in
  3.1.3). Small images process synchronously (HTTP 200, `url` present);
  large ones return 202 with `url: null`, and the composer polls
  `GET /api/v1/media/:id` until it appears. OAuth scope `write:media`.
- **Filters** use the v2 API (server-side `filtered` results), never the
  client-side v1 matching.
- **OAuth scopes** are `read write` + `admin:read admin:write` for the
  moderation authorization only. The `follow` scope has been deprecated
  since Mastodon 3.5; `read`/`write` grant every granular scope Babble
  needs, collections included.

## Hashtag suggestions in the composer

The composer completes the `#hashtag` being typed from four requests.
None of them is gated on `api_versions`: a server that lacks one answers
an error, which is treated as an empty list, and the others still work.

| Request | Used for | If it is missing |
|---|---|---|
| `GET /api/v2/search?type=hashtags&q=…&limit=10&resolve=false` | Suggestions for what has been typed. `exclude_unreviewed` is not sent: it leaves only the exact name on a server whose admin has reviewed no tags. | No server suggestions; the account's own tags below still show. Silent, no toast. |
| `GET /api/v1/featured_tags/suggestions` (3.0) | Tags the account used lately (`Recently used`) | That source is empty. |
| `GET /api/v1/featured_tags` | Tags on the profile (`On your profile`) | That source is empty. |
| `GET /api/v1/followed_tags?limit=200` (4.0) | Followed tags (`Followed`); one page only | That source is empty. |

The three lists are fetched together the first time a `#` is typed for an
account (the pool belongs to the account's client), kept for ten minutes
(one minute if all three failed), and dropped when a tag is followed,
unfollowed, featured or unfeatured. Tags of a post just published are added at once. A query
with no letter in it (`#2024`) is not sent to the server.

## Notification types

Babble renders the documented types including the 4.5 additions (`quote`,
`quoted_update`) and the 4.6 additions (`added_to_collection`,
`collection_update`, both v10), and ignores the server's grouped-
notifications API (a deliberate choice: it clusters client-side). New
streaming events handled: `status.update`, `filters_changed` (2.4.3, no
payload) and `notifications_merged` (4.3.0, payload ignored). Both arrive
on the `user` stream.

Dismissing one notification is `POST /api/v1/notifications/:id/dismiss`
(Mastodon 1.3.0, scope `write:notifications`). It needs no capability gate:
the older `POST /notifications/dismiss` with an `id` parameter was removed in
3.0, and the docs list no `DELETE` form. A grouped row sends one request per
notification it holds.

## Paging by an id near the one you hold

Mastodon keeps a feed in Redis sorted by a float score, and ids are past
2**53 (about 9e15), so beside one neighbours are 16 apart. Asking for
``max_id`` one above an id therefore rounds to that id, and the exclusive
bound skips the post. Measured on a live server for the resume position:
``+1`` and ``+16`` skipped it, ``+32`` and more returned it first. The
resume position adds 64 (``readingposition.anchor_params``). The same
rounding can in principle affect any request that bounds on an id at the
edge of a page, such as ``min_id`` for newer posts; that has not been seen
to lose a post.

## Deprecation watch

`mastodon.log_deprecation` logs any RFC 9745 `Deprecation` header (and a
matching `Sunset`, when present) under `G_MESSAGES_DEBUG=Babble`, so a
route Mastodon retires shows up in development before it breaks in the
field.
