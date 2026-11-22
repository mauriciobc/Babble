# SPDX-License-Identifier: GPL-3.0-or-later
"""Status field helpers shared by diet/kitchen/pantry (stdlib only)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any


class _StripHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    def text(self) -> str:
        return ''.join(self._parts)


def strip_html(html: str | None) -> str:
    if not html:
        return ''
    parser = _StripHTML()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r'<[^>]*>', '', html)
    return parser.text()


def content_of(status: dict[str, Any]) -> dict[str, Any]:
    """Effective content dict (unwrap reblog)."""
    reblog = status.get('reblog')
    return reblog if isinstance(reblog, dict) else status


def account_of(status: dict[str, Any], *, wrapper: bool = False) -> dict:
    source = status if wrapper else content_of(status)
    account = source.get('account')
    return account if isinstance(account, dict) else {}


def parse_created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value)
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def status_created_at(status: dict[str, Any], *, content: bool = True
                      ) -> datetime | None:
    source = content_of(status) if content else status
    return parse_created_at(source.get('created_at'))


def extract_focus(status: dict[str, Any], *, max_tags: int = 3,
                  max_keywords: int = 5
                  ) -> tuple[list[str], list[str]]:
    """Hashtags + plain keywords from a status for More Like This focus."""
    content = content_of(status)
    tags = []
    for tag in content.get('tags') or []:
        name = str((tag or {}).get('name') or '').lstrip('#').lower()
        if name and name not in tags:
            tags.append(name)
        if len(tags) >= max_tags:
            break
    words = []
    text = strip_html(content.get('content'))
    for token in re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}', text or ''):
        lower = token.lower()
        if lower in tags or lower in words:
            continue
        words.append(lower)
        if len(words) >= max_keywords:
            break
    return tags[:max_tags], words[:max_keywords]


def host_of_account(account: dict) -> str:
    url = account.get('url') or ''
    # https://host/@user → host
    match = re.match(r'https?://([^/]+)/', str(url))
    if match:
        return match.group(1).lower()
    acct = str(account.get('acct') or '')
    if '@' in acct:
        return acct.rsplit('@', 1)[-1].lower()
    return ''
