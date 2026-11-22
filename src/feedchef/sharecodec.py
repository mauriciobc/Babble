# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe share/import Base64 URL-safe codec (mashdon URL_SAFE | NO_PADDING)."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

from .recipe import FeedRecipe, recipe_to_dict, validate_for_import


def encode_recipe(recipe: FeedRecipe) -> str:
    payload = json.dumps(recipe_to_dict(recipe), ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def decode_recipe_b64(blob: str) -> FeedRecipe:
    text = (blob or '').strip()
    pad = '=' * (-len(text) % 4)
    raw = base64.urlsafe_b64decode(text + pad)
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('recipe JSON must be an object')
    return validate_for_import(data)


def recipe_share_uri(recipe: FeedRecipe, *, scheme: str = 'babble') -> str:
    return '%s://feedchef?recipe=%s' % (scheme, encode_recipe(recipe))


def parse_feedchef_uri(uri: str) -> FeedRecipe | None:
    if not is_feedchef_uri(uri):
        return None
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    values = query.get('recipe') or []
    if not values:
        return None
    return decode_recipe_b64(values[0])


def is_feedchef_uri(uri: str) -> bool:
    """True only for ``babble://feedchef?...`` (host ``feedchef``)."""
    parsed = urlparse(uri)
    if parsed.scheme != 'babble':
        return False
    host = (parsed.hostname or '').lower()
    return host == 'feedchef'
