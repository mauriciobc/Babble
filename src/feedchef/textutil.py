# SPDX-License-Identifier: GPL-3.0-or-later
"""Status field helpers shared by diet/kitchen/pantry (stdlib only)."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any


# TextProcessor.java STOP_WORDS (PRD 8.1), verbatim.
STOP_WORDS = frozenset((
    'the', 'and', 'is', 'of', 'to', 'in', 'for', 'on', 'it', 'this', 'that',
    'with', 'are', 'was', 'but', 'not', 'have', 'has', 'had', 'from', 'they',
    'them', 'his', 'her', 'she', 'him', 'you', 'your', 'http', 'https', 'www',
    'com', 'net', 'org', 'mastodon', 'social', 'just', 'like', 'what'))


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


def _focus_text(status: dict[str, Any]) -> str:
    content = content_of(status)
    parts = [(content.get('content') or '').replace('<', ' <'),
             content.get('spoiler_text') or '']
    for media in content.get('media_attachments') or []:
        if isinstance(media, dict):
            parts.append(media.get('description') or '')
    return strip_html(' '.join(parts))


def focus_tokens(status: dict[str, Any]) -> list[str]:
    """Lowercase, accent-stripped letter words of 3+ characters, no
    stopwords: the post body, content warning and every alt text."""
    text = unicodedata.normalize('NFD', _focus_text(status).lower())
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return [word for word in re.findall(r'[^\W\d_]{3,}', text)
            if word not in STOP_WORDS]


def _tfidf_scores(target: list[str], docs: list[list[str]]
                  ) -> dict[str, float]:
    if not target:
        return {}
    counts: dict[str, int] = {}
    for token in target:
        counts[token] = counts.get(token, 0) + 1
    top = max(counts.values())
    total = len(docs)
    scores = {}
    for token, count in counts.items():
        df = sum(1 for doc in docs if token in doc)
        tf = 0.5 + 0.5 * count / top
        idf = math.log(total / (1 + df)) + 1
        scores[token] = tf * idf
    return scores


def extract_focus(status: dict[str, Any], corpus: list | None = None,
                  max_tags: int = 3, max_keywords: int = 5
                  ) -> tuple[list[str], list[str]]:
    """Hashtags and TF-IDF keywords of a post, for More Like This.

    Keywords are scored against *corpus* (the current timeline); with no
    corpus the post alone is the one document.
    """
    content = content_of(status)
    tags: list[str] = []
    for tag in content.get('tags') or []:
        name = str((tag or {}).get('name') or '').lstrip('#').lower()
        if name and name not in tags:
            tags.append(name)
        if len(tags) >= max_tags:
            break
    target = focus_tokens(status)
    own_id = content.get('id')
    docs = [set(focus_tokens(other)) for other in corpus or []
            if not (own_id is not None
                    and content_of(other).get('id') == own_id)]
    docs.append(set(target))
    scores = _tfidf_scores(target, docs)
    ranked = sorted((token for token in scores if token not in tags),
                    key=lambda token: (-scores[token], token))
    return tags, ranked[:max_keywords]


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
