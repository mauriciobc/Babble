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


def translation_enabled(client):
    """Whether the instance advertises post translation."""
    info = client.instance_info or {}
    translation = (info.get('configuration') or {}).get('translation') or {}
    return bool(translation.get('enabled'))
