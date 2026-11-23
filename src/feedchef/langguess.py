# SPDX-License-Identifier: GPL-3.0-or-later
"""Guessing the language of a post that declares none (Feed Chef).

Mastodon's `language` field is empty on some posts. For those, and only
those, a guess may let the sentiment engines read the post (VADER for
English, LeIA for Portuguese); see ADR deviation 15 for the measurements and
the maintainer's decision. The rules, all of them gates a post must pass:

* the post (the boosted post for a boost, else the wrapper) declares **no**
  language: a declared language is never second-guessed;
* its visible text, with links and handles removed as
  `bench/feedchefaudit/langbench.py` removes them (`clean`), is at least
  `MIN_CHARS` characters;
* py3langid, restricted to `RUNTIME_LANGS` and with normalised probabilities,
  answers with a probability of at least `MIN_P`;
* the answer is 'en' or 'pt'. Any other language, or a lower probability, is
  "not judged", as before.

The guess is deterministic (a pure function of the cleaned text) and is
memoised by it. py3langid is imported with `feedchef.deps`, on the engine
worker; without it `status()` says unavailable and no post is guessed.
No GTK.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any

from . import deps
from .textutil import content_of, squash_space, strip_html

# The languages the detector is restricted to: the same set as
# `langbench.RUNTIME_LANGS` (a test holds the two equal).
RUNTIME_LANGS = ('en', 'pt', 'es', 'de', 'fr', 'tr', 'it', 'ar', 'nl', 'ca',
                 'fi', 'gl', 'eo', 'da', 'pl', 'nn')
MIN_P = 0.63
MIN_CHARS = 40
# The only guesses that are acted on.
ACTED = ('en', 'pt')

_URL = re.compile(r'https?://\S+|www\.\S+')
_MENTION = re.compile(r'(?<!\w)@[\w.-]+(?:@[\w.-]+)?')
_lock = threading.Lock()
_identifier = None
# Keyed by a hash of the text, so that it holds no post text; capped.
_memo: dict[bytes, tuple[str, float]] = {}
_MEMO_MAX = 4096


def clean(text: str) -> str:
    """The text the detector reads: no links, no handles (as langbench)."""
    text = _MENTION.sub(' ', _URL.sub(' ', text))
    return ' '.join(text.split())


def declared(status: dict[str, Any]) -> str | None:
    """The language the post declares, or None (the boosted post's, else
    the wrapper's, as `sentiment.language_of`)."""
    language = content_of(status).get('language') or status.get('language')
    if isinstance(language, str) and language.strip():
        return language
    return None


def visible_text(status: dict[str, Any]) -> str:
    """The cleaned visible text of the post the reader sees."""
    return clean(squash_space(strip_html(content_of(status).get('content'))))


def status() -> tuple[str, str]:
    """('ok' | 'unavailable', reason): whether guessing can run here. Never
    imports anything."""
    reason = deps.unavailable_reason('py3langid')
    return ('unavailable', reason) if reason else ('ok', '')


def version() -> str:
    """The installed py3langid version ('' when there is none); reads the
    metadata, imports nothing."""
    from importlib import metadata
    try:
        return metadata.version('py3langid')
    except metadata.PackageNotFoundError:
        return ''


def _classify(text: str) -> tuple[str, float] | None:
    global _identifier
    if deps.get('py3langid') is None:
        return None
    key = hashlib.blake2b(text.encode('utf-8'), digest_size=16).digest()
    found = _memo.get(key)
    if found is not None:
        return found
    with _lock:
        if _identifier is None:
            from py3langid.langid import MODEL_FILE, LanguageIdentifier
            ident = LanguageIdentifier.from_model_file(
                MODEL_FILE, norm_probs=True)
            ident.set_languages(sorted(set(RUNTIME_LANGS)
                                       & set(ident.labels)))
            _identifier = ident
        lang, prob = _identifier.classify(text)
    result = (str(lang), float(prob))
    if len(_memo) >= _MEMO_MAX:
        _memo.clear()
    _memo[key] = result
    return result


def guess(status_: dict[str, Any]) -> tuple[str, float] | None:
    """(language, probability) for a post that declares none and has enough
    text, whatever the probability is (the caller applies `MIN_P`); None
    for a post that is not eligible or when py3langid is unavailable."""
    if declared(status_) is not None:
        return None
    text = visible_text(status_)
    if len(text) < MIN_CHARS:
        return None
    return _classify(text)


def acted(found: tuple[str, float] | None) -> str | None:
    """The language of a guess when it is one to act on ('en' or 'pt' at
    `MIN_P` or more), else None."""
    if found is not None and found[1] >= MIN_P and found[0] in ACTED:
        return found[0]
    return None
