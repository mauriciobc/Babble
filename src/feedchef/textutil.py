# SPDX-License-Identifier: GPL-3.0-or-later
"""Status field helpers shared by diet/kitchen/pantry (stdlib only)."""

from __future__ import annotations

import functools
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


# A line break, and the elements that start and end a paragraph: the text of
# two of them must never run together into one word.
_BREAK_TAGS = frozenset(('br',))
_BLOCK_TAGS = frozenset(('p', 'div', 'li', 'blockquote', 'pre', 'ul', 'ol',
                         'h1', 'h2', 'h3', 'h4', 'h5', 'h6'))


class _StripHTML(HTMLParser):
    def __init__(self, drop_quote_inline: bool = False):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop_quote = drop_quote_inline
        self._quote_tag: str | None = None
        self._quote_depth = 0

    def _opens_quote_line(self, tag, attrs) -> bool:
        return (self._drop_quote and tag in ('span', 'p')
                and 'quote-inline' in (dict(attrs).get('class')
                                       or '').split())

    def handle_starttag(self, tag, attrs):
        if self._quote_depth:
            if tag == self._quote_tag:
                self._quote_depth += 1
            return
        if self._opens_quote_line(tag, attrs):
            self._quote_tag = tag
            self._quote_depth = 1
            return
        if tag in _BREAK_TAGS:
            self._parts.append('\n')
        elif tag in _BLOCK_TAGS:
            self._parts.append('\n\n')

    def handle_startendtag(self, tag, attrs):
        if self._quote_depth or self._opens_quote_line(tag, attrs):
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self._quote_depth:
            if tag == self._quote_tag:
                self._quote_depth -= 1
                if not self._quote_depth:
                    self._parts.append('\n\n')
            return
        if tag in _BLOCK_TAGS:
            self._parts.append('\n\n')

    def handle_data(self, data):
        if not self._quote_depth:
            self._parts.append(data)

    def text(self) -> str:
        return ''.join(self._parts)


def strip_html(html: str | None, *, drop_quote_inline: bool = False) -> str:
    """The text of status HTML. A line break is a newline and a paragraph
    boundary a blank line, so words on either side never join; entities are
    decoded. *drop_quote_inline* leaves out the "RE: <link>" line a server
    appends to a quote post (the one the row hides when it shows the quote
    as a card)."""
    if not html:
        return ''
    return _strip(html, drop_quote_inline)


@functools.lru_cache(maxsize=512)
def _strip(html: str, drop_quote_inline: bool) -> str:
    """The pure body of ``strip_html``, cached: the same post is read by the
    diet, the contract, the guard and the audit within one cook."""
    parser = _StripHTML(drop_quote_inline)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r'<[^>]*>', ' ', html)
    return parser.text()


def squash_space(text: str | None) -> str:
    """*text* with every run of whitespace (newlines and no-break spaces
    included) as one space, trimmed: what word matching and the length
    count read."""
    return ' '.join((text or '').split())


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


def focus_text(status: dict[str, Any]) -> str:
    """The text a post's focus words are read from: its body, content
    warning and every alt text, as plain text."""
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
    return text_tokens(focus_text(status))


def text_tokens(text: str) -> list[str]:
    """The words of *text* that count (see ``focus_tokens``)."""
    text = fold_text(text)
    return [word for word in re.findall(r'[^\W\d_]{3,}', text)
            if word not in STOP_WORDS]


def fold_text(text: str) -> str:
    """*text* lowercased with its accents removed: the form a focus word
    is kept and matched in."""
    text = unicodedata.normalize('NFD', text.lower())
    return ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')


def tfidf_vector(tokens: list[str], df: dict[str, int], total: int
                 ) -> dict[str, float]:
    """The TF-IDF weight of each distinct token of one document, with
    Java's formulas: the augmented term frequency 0.5 + 0.5 * count /
    most frequent count, and the smoothed inverse document frequency
    log(total / (1 + df)) + 1. *df* is the number of the *total* documents
    each term is in."""
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    top = max(counts.values())
    scores = {}
    for token, count in counts.items():
        tf = 0.5 + 0.5 * count / top
        idf = math.log(total / (1 + df.get(token, 0))) + 1
        scores[token] = tf * idf
    return scores


def _tfidf_scores(target: list[str], docs: list[list[str]]
                  ) -> dict[str, float]:
    if not target:
        return {}
    df = {token: sum(1 for doc in docs if token in doc)
          for token in dict.fromkeys(target)}
    return tfidf_vector(target, df, len(docs))


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
                    key=lambda token: (-scores[token], -len(token), token))
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
