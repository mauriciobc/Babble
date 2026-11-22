# SPDX-License-Identifier: GPL-3.0-or-later
"""The promises of a Feed Chef recipe, declared once (standard library only).

Every control of the recipe builder promises something about the posts it
lets through. Each per-post promise is a predicate here, with a stable id
(the name of the recipe field it comes from) and a description for logs.
The diet and the network reach stages call these predicates to decide;
``FeedEngine`` calls them again, on every post it is about to return, as
an independent final guard: a post that breaks a promise at that point is
dropped and logged, because it means a stage has a bug.

A promise reads the post the reader sees: the boosted post for a boost
(``textutil.content_of``), never the wrapper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .recipe import FeedRecipe, NetworkReachConfig
from .textutil import account_of, content_of, strip_html

# status -> compound score in [-1, 1], or None when the post is not judged
# (a language the sentiment engines do not read).
SentimentJudge = Callable[[dict], 'float | None']


@dataclass(frozen=True)
class Facts:
    """What a predicate may need besides the recipe and the post."""

    follow_ids: frozenset[str] | None = None
    self_id: str = ''
    sentiment: SentimentJudge | None = None


@dataclass(frozen=True)
class Promise:
    id: str
    description: str
    active: Callable[[FeedRecipe], bool]
    holds: Callable[[FeedRecipe, dict, Facts], bool]


# Reading a post

def post_text(status: dict[str, Any]) -> str:
    """The text of the post without HTML, stripped: what the minimum
    length counts. A link counts in full (its hidden parts included), a
    mention and a hashtag by their written form."""
    return strip_html(content_of(status).get('content')).strip()


def text_length(status: dict[str, Any]) -> int:
    return len(post_text(status))


def spoiler_of(status: dict[str, Any]) -> str:
    content = content_of(status)
    return str(content.get('spoiler_text') or content.get('spoilerText')
               or '')


def attachments_of(status: dict[str, Any]) -> list[dict]:
    content = content_of(status)
    items = (content.get('media_attachments')
             or content.get('mediaAttachments') or [])
    return [item if isinstance(item, dict) else {} for item in items]


def has_tag(status: dict[str, Any], tag: str) -> bool:
    needle = tag.lower()
    for item in content_of(status).get('tags') or []:
        if str((item or {}).get('name') or '').lower() == needle:
            return True
    return False


def blocked_hit(status: dict[str, Any], words: list[str]) -> str | None:
    """The first blocked word found as a whole word, ignoring case, in the
    text of the post or in its content warning; None when there is none.
    The boundary is "not next to a letter or digit" (Unicode), so a word
    with a symbol in it, like c++, still matches."""
    if not words:
        return None
    text = post_text(status)
    spoiler = spoiler_of(status)
    for word in words:
        pattern = re.compile(r'(?<!\w)%s(?!\w)' % re.escape(word), re.I)
        if pattern.search(text) or pattern.search(spoiler):
            return word
    return None


def author_id(status: dict[str, Any]) -> str:
    """The account of the post's content: the booster is not the author."""
    return str(account_of(status, wrapper=False).get('id') or '')


# Network reach

def reach_kind(config: NetworkReachConfig) -> str | None:
    """What "Only People I Follow" lets through: None when the filter is
    off (or switched off by ``include_unknown``, which lets strangers in
    on purpose), 'follows' for degree one, 'self' for degree zero.

    The builder never sets ``include_unknown``; an imported recipe may,
    and it is honoured as written. Own posts always pass (a set
    ``include_self`` or not): the viewer is degree zero."""
    if not config.enabled or config.max_degrees is None:
        return None
    if config.include_unknown:
        return None
    return 'follows' if int(config.max_degrees) >= 1 else 'self'


def reach_allows(config: NetworkReachConfig, account_id: str,
                 follow_ids: frozenset[str] | set[str] | None,
                 self_id: str) -> bool:
    kind = reach_kind(config)
    if kind is None:
        return True
    if not account_id:
        return False
    if account_id == self_id:
        return True
    return kind == 'follows' and follow_ids is not None \
        and account_id in follow_ids


# The promises, in the order the diet checks them

def _keyword(recipe, status, facts):
    return blocked_hit(status, recipe.diet.keyword_blocklist) is None


def _media(recipe, status, facts):
    return bool(attachments_of(status))


def _alt_text(recipe, status, facts):
    # Posts without media have nothing to describe and pass.
    return all(str(item.get('description') or '').strip()
               for item in attachments_of(status))


def _sensitive(recipe, status, facts):
    return not bool(content_of(status).get('sensitive'))


def _min_length(recipe, status, facts):
    return text_length(status) >= recipe.diet.hard_filters.min_content_length


def _positive(recipe, status, facts):
    judge = facts.sentiment
    if judge is None:
        from . import sentiment as sentmod
        judge = sentmod.score_status
    score = judge(status)
    # None: a language that is not judged is neither hidden nor boosted.
    return score is None or score >= 0.0


def _excluded(recipe, status, facts):
    return not any(has_tag(status, tag)
                   for tag in recipe.pantry.hashtags_exclude)


def _included(recipe, status, facts):
    return any(has_tag(status, tag)
               for tag in recipe.pantry.hashtags_include)


def _follows(recipe, status, facts):
    return reach_allows(recipe.pantry.network_reach, author_id(status),
                        facts.follow_ids, facts.self_id)


PROMISES: tuple[Promise, ...] = (
    Promise('keyword_blocklist',
            'no blocked word, as a whole word, in the text or the content '
            'warning',
            lambda r: bool(r.diet.keyword_blocklist), _keyword),
    Promise('must_have_media', 'at least one media attachment',
            lambda r: r.diet.hard_filters.must_have_media, _media),
    Promise('must_have_alt_text',
            'every media attachment has alt text (no media: passes)',
            lambda r: r.diet.hard_filters.must_have_alt_text, _alt_text),
    Promise('no_sensitive_media', 'the post is not flagged sensitive',
            lambda r: r.diet.hard_filters.no_sensitive_media, _sensitive),
    Promise('min_content_length',
            'the text without HTML has at least the minimum characters',
            lambda r: r.diet.hard_filters.min_content_length > 0,
            _min_length),
    Promise('positive_only',
            'an English or Portuguese post does not read as negative',
            lambda r: r.diet.hard_filters.positive_only, _positive),
    Promise('hashtags_exclude', 'carries none of the excluded hashtags',
            lambda r: bool(r.pantry.hashtags_exclude), _excluded),
    Promise('hashtags_include',
            'carries at least one of the included hashtags',
            lambda r: bool(r.pantry.hashtags_include), _included),
    Promise('network_reach',
            'the content author is followed, or is the viewer',
            lambda r: reach_kind(r.pantry.network_reach) is not None,
            _follows),
)

PROMISE_IDS = tuple(promise.id for promise in PROMISES)
DIET_IDS = frozenset(PROMISE_IDS[:6])


def violations(
    recipe: FeedRecipe,
    status: dict[str, Any],
    *,
    follow_ids: frozenset[str] | set[str] | None = None,
    self_id: str = '',
    sentiment: SentimentJudge | None = None,
    only: frozenset[str] | set[str] | None = None,
    first: bool = False,
) -> list[str]:
    """Ids of the promises of *recipe* that *status* breaks, in order.

    *follow_ids* is the viewer's follow set (None when it is not known,
    which fails "Only People I Follow" closed), *sentiment* the judge of
    the post's language, *only* narrows the check to some ids and *first*
    stops at the first broken promise."""
    facts = Facts(
        follow_ids=None if follow_ids is None else frozenset(follow_ids),
        self_id=str(self_id or ''), sentiment=sentiment)
    broken = []
    for promise in PROMISES:
        if only is not None and promise.id not in only:
            continue
        if not promise.active(recipe):
            continue
        if not promise.holds(recipe, status, facts):
            broken.append(promise.id)
            if first:
                break
    return broken
