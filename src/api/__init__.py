# SPDX-License-Identifier: GPL-3.0-or-later
"""The Mastodon API layer: one declared endpoint per request Babble sends.

`core` holds the `Endpoint` declaration, the registry and `send()`. The
resource modules (`statuses`, `accounts`, ...) arrive with phase 1b and
later of `Docs/api-layer-plan.md`.
"""
