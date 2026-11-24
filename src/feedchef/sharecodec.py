# SPDX-License-Identifier: GPL-3.0-or-later
"""Recipe share/import Base64 URL-safe codec (mashdon URL_SAFE | NO_PADDING)."""

from __future__ import annotations

import base64
import copy
import json
import logging
import uuid
import zlib
from urllib.parse import parse_qs, urlparse

from .recipe import (FeedRecipe, default_recipe, recipe_to_dict,
                     validate_for_import)

log = logging.getLogger(__name__)

# The compact form (``r=2.<blob>``): the recipe without what the default
# recipe already says, as minimal JSON, raw-deflated, base64url. It is what
# the links and the QR codes carry; the older ``recipe=`` form still reads.
COMPACT_VERSION = '2'
# What a compact blob may inflate to: more is refused (a zip bomb).
MAX_INFLATED = 65536
# The longest link read at all, in characters, checked before any decoding.
# The largest real compact link is under 800 characters and a legacy link
# of a 3 kB recipe still fits.
MAX_LINK_CHARS = 16384


def _too_long(text) -> None:
    if len(text or '') > MAX_LINK_CHARS:
        raise ValueError('the link is too long')


def encode_recipe(recipe: FeedRecipe) -> str:
    payload = json.dumps(recipe_to_dict(recipe), ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _compact(value, reference):
    """*value* (a dict) without the keys that are None or equal to the
    same key of *reference*; nested dicts are compacted the same way, a
    list is kept whole (its dicts without their None values)."""
    out = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, dict):
            inner = _compact(item, reference.get(key) or {})
            if inner:
                out[key] = inner
        elif isinstance(item, list):
            if item != reference.get(key):
                out[key] = [{k: v for k, v in one.items() if v is not None}
                            if isinstance(one, dict) else one
                            for one in item]
        elif key not in reference or reference[key] != item:
            out[key] = item
    return out


def _overlay(base: dict, patch: dict) -> dict:
    for key, item in patch.items():
        if isinstance(item, dict) and isinstance(base.get(key), dict):
            _overlay(base[key], item)
        else:
            base[key] = item
    return base


def encode_recipe_compact(recipe: FeedRecipe) -> str:
    """The compact blob of *recipe*: see ``COMPACT_VERSION``. A UUID id is
    kept as the 22 characters of its bytes (key ``u``)."""
    data = _compact(recipe_to_dict(recipe), recipe_to_dict(default_recipe()))
    ident = data.pop('id', '')
    try:
        data['u'] = _b64(uuid.UUID(ident).bytes)
    except (ValueError, AttributeError, TypeError):
        if ident:
            data['id'] = ident
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')
                     ).encode('utf-8')
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return _b64(packer.compress(raw) + packer.flush())


def decode_recipe_compact(blob: str) -> FeedRecipe:
    """The recipe of a compact blob, laid over the default recipe and
    validated as an import. Raises ValueError for a blob that is damaged,
    inflates to more than ``MAX_INFLATED`` bytes, or is not a recipe."""
    _too_long(blob)
    try:
        unpacker = zlib.decompressobj(-15)
        raw = unpacker.decompress(_unb64((blob or '').strip()), MAX_INFLATED)
        if unpacker.unconsumed_tail or not unpacker.eof:
            raise ValueError('the recipe is too large or cut short')
    except zlib.error as error:
        raise ValueError('the recipe cannot be read') from error
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('recipe JSON must be an object')
    ident = data.pop('u', None)
    merged = _overlay(recipe_to_dict(default_recipe()), data)
    if ident is not None:
        merged['id'] = str(uuid.UUID(bytes=_unb64(str(ident))))
    elif 'id' not in data:
        merged['id'] = ''
    return validate_for_import(merged)


def decode_recipe_b64(blob: str) -> FeedRecipe:
    _too_long(blob)
    text = (blob or '').strip()
    pad = '=' * (-len(text) % 4)
    raw = base64.urlsafe_b64decode(text + pad)
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('recipe JSON must be an object')
    return validate_for_import(data)


def handle_of(account: dict | None) -> str:
    """The full handle of an account ("@user@instance"), from the stored
    account's ``acct`` (which has no server for a local user) and its
    ``instance`` address; '' when there is nothing to build it from."""
    account = account or {}
    acct = str(account.get('acct') or account.get('username')
               or '').strip().lstrip('@')
    if not acct:
        return ''
    user, _at, host = acct.partition('@')
    if not user:
        return ''
    if not _at:
        address = str(account.get('instance') or '').strip()
        if '//' not in address:
            address = '//' + address
        host = urlparse(address).hostname or ''
    if not host or '@' in host:
        return ''
    return '@%s@%s' % (user, host)


def attributed(recipe: FeedRecipe, author: str) -> FeedRecipe:
    """*recipe* as it is shared by *author*: a copy whose chef is *author*
    when the recipe has none. A recipe that already names a chef (one that
    was imported) keeps it: the person who passes a recipe on is not its
    author."""
    shared = copy.deepcopy(recipe)
    if author and not (shared.meta.author or '').strip():
        shared.meta.author = author
    return shared


def recipe_share_uri(recipe: FeedRecipe, *, scheme: str = 'babble',
                     author: str = '') -> str:
    """The share link of *recipe*; *author* (the sharer's handle) fills the
    chef when the recipe has none (see ``attributed``)."""
    return '%s://feedchef?r=%s.%s' % (
        scheme, COMPACT_VERSION,
        encode_recipe_compact(attributed(recipe, author)))


def parse_feedchef_uri(uri: str) -> FeedRecipe | None:
    _too_long(uri)
    if not is_feedchef_uri(uri):
        return None
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    values = query.get('recipe') or []
    if values:
        return decode_recipe_b64(values[0])
    compact = query.get('r') or []
    if not compact:
        return None
    version, _dot, blob = compact[0].partition('.')
    if version != COMPACT_VERSION or not blob:
        return None
    return decode_recipe_compact(blob)


def try_parse_feedchef_uri(uri: str) -> FeedRecipe | None:
    """Like parse_feedchef_uri, but None for a malformed link.

    binascii.Error, JSONDecodeError and UnicodeDecodeError are ValueErrors.
    """
    try:
        return parse_feedchef_uri(uri)
    except (ValueError, TypeError, KeyError):
        return None
    except (MemoryError, RecursionError):
        log.debug('feedchef: link too heavy to read', exc_info=True)
        return None


def is_feedchef_uri(uri: str) -> bool:
    """True only for ``babble://feedchef?...`` (host ``feedchef``)."""
    parsed = urlparse(uri)
    if parsed.scheme != 'babble':
        return False
    host = (parsed.hostname or '').lower()
    return host == 'feedchef'
