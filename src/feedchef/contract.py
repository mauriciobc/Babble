# SPDX-License-Identifier: GPL-3.0-or-later
"""The promises of a Feed Chef recipe, declared once (no widgets).

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

from .. import quoteline
from . import cluster as clustermod
from . import relevance as relevancemod
from .recipe import FeedRecipe, NetworkReachConfig, max_per
from .textutil import (account_of, content_of, host_of_account, squash_space,
                       status_created_at, strip_html)

# What a freshness factor is raised to when the recipe also boosts
# trending posts, so that an old viral post is not scored zero (Java's
# FRESHNESS_FLOOR_FOR_TRENDING).
FRESHNESS_FLOOR = 0.2

# status -> compound score in [-1, 1], or None when the post is not judged
# (a language the sentiment engines do not read).
SentimentJudge = Callable[[dict], 'float | None']


@dataclass(frozen=True)
class Facts:
    """What a predicate may need besides the recipe and the post."""

    follow_ids: frozenset[str] | None = None
    self_id: str = ''
    sentiment: SentimentJudge | None = None
    # Whether the native Bluesky setting is on: it decides whether the row
    # shows a quote as a card and hides its "RE: <link>" line.
    native_bluesky: bool = False


@dataclass(frozen=True)
class Promise:
    id: str
    description: str
    active: Callable[[FeedRecipe], bool]
    holds: Callable[[FeedRecipe, dict, Facts], bool]


# Reading a post

def post_text(status: dict[str, Any], native_bluesky: bool = False) -> str:
    """The text of the post without HTML, whitespace squashed to single
    spaces: what the minimum length counts, and what blocked words are
    matched in. A link counts in full (its hidden parts included), a
    mention and a hashtag by their written form; a line or paragraph break
    counts as one space. The "RE: <link>" line is left out exactly when
    the row hides it (``quoteline.hidden``), so the count is the text the
    reader sees."""
    content = content_of(status)
    return squash_space(strip_html(
        content.get('content'),
        drop_quote_inline=quoteline.hidden(content, native_bluesky)))


def text_length(status: dict[str, Any], native_bluesky: bool = False) -> int:
    return len(post_text(status, native_bluesky))


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
    spoiler = squash_space(spoiler_of(status))
    for word in words:
        # An entry of several words matches across a line break or a
        # no-break space: both sides are read with single spaces.
        needle = squash_space(word)
        if not needle:
            continue
        pattern = re.compile(r'(?<!\w)%s(?!\w)' % re.escape(needle), re.I)
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
    return text_length(status, facts.native_bluesky) \
        >= recipe.diet.hard_filters.min_content_length


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


def _guessed(recipe, status, facts):
    """A post with no declared language is only read by the sentiment
    engines on a guess that passes the gates: enough text, 'en' or 'pt',
    a probability of at least ``langguess.MIN_P``."""
    from . import langguess
    from . import sentiment as sentmod
    if langguess.declared(status) is not None:
        return True
    judge = facts.sentiment or sentmod.score_status
    # What the engine did (a post judged, or not) against what the gates
    # allow, worked out here from the guess alone: a judge that reads a post
    # it should not, or skips one it should read, is caught.
    found = langguess.guess(status)
    allowed = (found is not None and found[1] >= langguess.MIN_P
               and found[0] in langguess.ACTED)
    return (judge(status) is not None) == allowed


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
    Promise('language_guess',
            'a post with no declared language is judged only on a guess of '
            'English or Portuguese with enough text and confidence',
            lambda r: r.diet.hard_filters.positive_only or any(
                m.type == 'sentiment_boost' for m in r.kitchen.modifiers),
            _guessed),
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
    native_bluesky: bool = False,
) -> list[str]:
    """Ids of the promises of *recipe* that *status* breaks, in order.

    *follow_ids* is the viewer's follow set (None when it is not known,
    which fails "Only People I Follow" closed), *sentiment* the judge of
    the post's language, *only* narrows the check to some ids and *first*
    stops at the first broken promise."""
    facts = Facts(
        follow_ids=None if follow_ids is None else frozenset(follow_ids),
        self_id=str(self_id or ''), sentiment=sentiment,
        native_bluesky=native_bluesky)
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


# The promises about the page as a whole. A post that breaks one is dropped
# by the guard like one that breaks a promise of its own, and the removal
# reason is `guard:<id>`.

LIST_IDS = ('relevance_order', 'cluster_cap', 'fresh_floor',
            'instance_cap')


@dataclass(frozen=True)
class ListFacts:
    """What the stages decided that the list promises are checked against
    (the kitchen's ``meta``); the guard never recomputes the grouping or
    the relevance, the audit's ``evaluate`` does."""

    # Relevance of each post to the focus, by the id of its entry; None
    # when the recipe has no focus (or no post matched it).
    relevance: dict[str, float] | None = None
    # Group of each post, by id; None when the grouping did not run.
    groups: dict[str, int] | None = None
    # (freshness factor, raised by the floor) of each post that has one.
    fresh: dict[str, tuple[float, bool]] | None = None
    # Posts per server shown by the earlier pages of the cook session.
    instance_counts: dict[str, int] | None = None
    # The page's one clock reading, which the freshness check needs.
    now: Any = None


def _sid(status: dict[str, Any]) -> str:
    return str(status.get('id') or '')


def _grouping(recipe: FeedRecipe):
    return next((mod for mod in recipe.kitchen.modifiers
                 if mod.type == 'similarity_cluster'), None)


def floor_holds(trending: bool, factor: float, lifted: bool) -> bool:
    """"Favor Fresh Posts" raises a factor under 0.2 to 0.2 exactly when
    the recipe also has "Boost Trending Topics": with it no factor is
    under the floor, without it the floor never lifted one."""
    return factor >= FRESHNESS_FLOOR if trending else not lifted


def expected_freshness(status: dict[str, Any], decay_minutes: Any,
                       now, trending: bool) -> float | None:
    """What the freshness factor of a post must be, written out here and
    not read from the kitchen: 1 - age / fade-out, kept in [0, 1], raised to
    the floor with trending; None when freshness leaves the post be (no
    date, a date in the future, or no fade-out)."""
    try:
        decay = float(decay_minutes or 360)
    except (TypeError, ValueError):
        return None
    created = status_created_at(status, content=True)
    if decay <= 0 or created is None or now is None:
        return None
    age = (now - created).total_seconds() / 60.0
    if age < 0:
        return None
    factor = max(0.0, min(1.0, 1.0 - age / decay))
    return max(factor, FRESHNESS_FLOOR) if trending else factor


def instance_caps(recipe: FeedRecipe) -> list[int]:
    """The cap per server of every diversity modifier that caps them: all
    of them must hold on the page."""
    return [max_per(mod.max_per_cluster) for mod in recipe.kitchen.modifiers
            if mod.type == 'diversity'
            and 'instance' in (mod.diversity_dimensions or [])]


def instance_cap(recipe: FeedRecipe) -> int | None:
    """The tightest of ``instance_caps``, or None."""
    caps = instance_caps(recipe)
    return min(caps) if caps else None


def list_violations(recipe: FeedRecipe, ordered: list[dict[str, Any]],
                    facts: ListFacts) -> dict[str, list[str]]:
    """The posts of *ordered* that break a promise about the page, with the
    ids of the promises (see ``LIST_IDS``), in page order:

    * ``relevance_order``: with a focus the page is non-increasing by
      relevance; a post more relevant than the one before it, or whose
      relevance is not a number, breaks it.
    * ``cluster_cap``: at most ``max_per_cluster`` posts of each group;
      the ones past the cap break it.
    * ``fresh_floor``: each post's freshness factor is the one the recipe
      calls for, recomputed here: raised to 0.2 with trending and only then.
    * ``instance_cap``: at most ``max_per_cluster`` posts per server over
      the cook session, for each diversity modifier that caps servers; the
      ones past the cap break it."""
    found: dict[str, list[str]] = {}

    def add(status, promise):
        found.setdefault(_sid(status), []).append(promise)

    if facts.relevance is not None \
            and relevancemod.focus_keywords(recipe.kitchen):
        last = None
        for status in ordered:
            score = facts.relevance.get(_sid(status), 0.0)
            if score != score or (last is not None and score > last):
                add(status, 'relevance_order')
            else:
                last = score
    grouping = _grouping(recipe)
    if facts.groups is not None and grouping is not None:
        per = clustermod.max_per_cluster(grouping.max_per_cluster)
        seen: dict[int, int] = {}
        for status in ordered:
            group = facts.groups.get(_sid(status))
            if group is None:
                continue
            seen[group] = seen.get(group, 0) + 1
            if seen[group] > per:
                add(status, 'cluster_cap')
    freshness = [mod for mod in recipe.kitchen.modifiers
                 if mod.type == 'freshness']
    if facts.fresh is not None and freshness:
        trending = any(mod.type == 'trending'
                       for mod in recipe.kitchen.modifiers)
        for status in ordered:
            row = facts.fresh.get(_sid(status))
            if row is None:
                continue
            wanted = None
            for mod in freshness:
                one = expected_freshness(status, mod.decay_minutes,
                                         facts.now, trending)
                if one is not None:
                    wanted = one if wanted is None else wanted * one
            if wanted is None or not abs(row[0] - wanted) <= 1e-12:
                add(status, 'fresh_floor')
    for cap in instance_caps(recipe):
        counts = dict(facts.instance_counts or {})
        for status in ordered:
            server = host_of_account(account_of(status, wrapper=True))
            if not server:
                continue
            counts[server] = counts.get(server, 0) + 1
            if counts[server] > cap and 'instance_cap' not in found.get(
                    _sid(status), ()):
                add(status, 'instance_cap')
    return found
