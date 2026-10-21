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
| `api_versions.mastodon` | 4.3.0 | cached per client after first fetch | `mastodon.py instance_info_*` (see ARCH-09 for coalescing) |
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
