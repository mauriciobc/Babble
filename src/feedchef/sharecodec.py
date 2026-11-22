# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe share/import Base64 URL-safe codec (mashdon URL_SAFE | NO_PADDING)."""

from __future__ import annotations

import base64
import json
from typing import Any
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
    parsed = urlparse(uri)
    if parsed.scheme != 'babble':
        return None
    host = (parsed.hostname or parsed.netloc or '').lower()
    path = (parsed.path or '').strip('/')
    if host not in ('feedchef', '') and path != 'feedchef':
        # babble://feedchef?... → hostname feedchef, path empty
        if host != 'feedchef':
            return None
    if host != 'feedchef' and path != 'feedchef':
        return None
    query = parse_qs(parsed.query)
    values = query.get('recipe') or []
    if not values:
        return None
    return decode_recipe_b64(values[0])


def is_feedchef_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme != 'babble':
        return False
    host = (parsed.hostname or parsed.netloc or '').lower()
    path = (parsed.path or '').strip('/')
    return host == 'feedchef' or path == 'feedchef'
