# SPDX-License-Identifier: GPL-3.0-or-later
"""Mastodon ``api_versions.mastodon`` gates used across Babble.

Every feature version lives here so a new gate is one constant and one
call site, not a scattered comparison. Callers still ask
``client.is_supported(...)`` — this module only names the thresholds.
"""

# Collections (Mastodon 4.6.0, api_versions 10).
COLLECTIONS_API_VERSION = 10

# Trending-link timeline (Mastodon 4.3.0, api_versions 2).
LINK_TIMELINE_API_VERSION = 2

# Profile tabs on PATCH /api/v1/profile (Mastodon 4.6.0, api_versions 8).
PROFILE_TABS_API_VERSION = 8

# Avatar/header alt text on /profile (Mastodon 4.6.0, api_versions 9).
PROFILE_ALT_TEXT_API_VERSION = 9

# endorse/unendorse aliases vs pin/unpin (Mastodon 4.6.0, api_versions 6).
ENDORSE_API_VERSION = 6

# What the numbers below are derived from. The docs name an API number only
# on features added from 4.4.0 on; for the older ones the number is read off
# the release itself: ``api_versions`` in ``lib/mastodon/version.rb`` is 2 at
# every v4.3.x tag, 6 at v4.4.0, 7 at v4.5.0 and 10 at v4.6.0. A gate is the
# lowest number at which a *release* has the feature.

# annual_reports/:year/state and /generate (Mastodon 4.6.0, api_versions 8).
# docs.joinmastodon.org/methods/annual_reports/ tags both "4.6.0 (mastodon
# API version 8)". The report itself (GET /annual_reports, 4.3.0, and
# /:year, 4.4.0) is not gated: Wrapped is useful on a 4.3+ server that
# already published its reports, and a missing one is a 404 it handles.
ANNUAL_REPORT_STATE_API_VERSION = 8

# Notification policy, filtered-notification requests and unread_count
# (Mastodon 4.3.0, api_versions 2). The docs say only "4.3.0 - added", and
# 4.3.0 is the release that introduced ``api_versions``: the key reads 2 at
# v4.3.0, v4.3.0-rc.1 and v4.3.8, so no server that has these reports less.
# A Mastodon older than 4.3 has no key at all and so lands in "unknown".
NOTIFICATION_POLICY_API_VERSION = 2

# Filter action ``blur`` (Mastodon 4.4.0, api_versions 5). Stated by the
# docs on POST/PUT /api/v2/filters: "4.4.0 (mastodon API version 5) - added
# blur value to filter_action attribute".
FILTER_BLUR_API_VERSION = 5

# Quote posts' settings: ``quoted_status_id`` and ``quote_approval_policy``
# on POST /statuses, ``source[quote_policy]`` on update_credentials
# (Mastodon 4.5.0, api_versions 7: v4.5.0 reads 7, v4.4.x reads 6).
QUOTES_API_VERSION = 7

# GET /accounts/:id/endorsements (Mastodon 4.4.0). The docs give no API
# number; the endpoint landed in mastodon/mastodon ff0990ec9f (2025-04-16,
# #34421) and v4.4.0 reads 6, so 6 is the first number a release with it
# reports (a main build between 5 and 6 may lack it; that is not a release).
ENDORSEMENTS_API_VERSION = 6


def translation_enabled(client):
    """Whether the instance advertises post translation."""
    info = client.instance_info or {}
    translation = (info.get('configuration') or {}).get('translation') or {}
    return bool(translation.get('enabled'))
