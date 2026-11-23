# SPDX-License-Identifier: GPL-3.0-or-later
"""Relax Filters: the one setting of a recipe that removed the most posts,
and how to turn it off (no widgets, no GTK).

The empty page of a recipe offers it: it reads the removals the last cook's
audit counted (``by_reason``), picks the rule behind most of them among the
ones a reader can switch off, and ``apply`` switches it off on a copy of the
recipe. The rules are named exactly as the builder names them, so the
button points at a row the reader can find.
"""

from __future__ import annotations

import copy
from typing import Any, NamedTuple

from .auditreport import ENGLISH, Words
from .recipe import FeedRecipe


class Rule(NamedTuple):
    reason: str
    # The builder's name for the setting (its English text, which
    # ``setting_name`` translates).
    name: str
    # 'off' for a switch, 'clear' for a field of words.
    verb: str


# In the order that breaks a tie: what the reader most likely meant to be
# strict about first.
RULES: tuple[Rule, ...] = (
    Rule('diet:must_have_media', 'Only Posts With Media', 'off'),
    Rule('diet:must_have_alt_text', 'Require Alt Text on Media', 'off'),
    Rule('diet:no_sensitive_media', 'Hide Posts Marked Sensitive', 'off'),
    Rule('diet:positive_only', 'Only Positive Posts', 'off'),
    Rule('diet:min_content_length', 'Minimum Length', 'off'),
    Rule('diet:keyword_blocklist', 'Blocked Words', 'clear'),
    Rule('pantry_missing_included_tag', 'Include Hashtags', 'clear'),
    Rule('pantry_excluded_tag', 'Exclude Hashtags', 'clear'),
    Rule('reach_not_followed', 'Only People I Follow', 'off'),
    Rule('heat_cut', 'Hide the Least Engaging', 'off'),
    Rule('author_cap', 'Limit Posts per Person', 'off'),
    Rule('instance_cap', 'Count Servers Too', 'off'),
    Rule('cluster_cap', 'Group Similar Posts', 'off'),
)
_BY_REASON = {rule.reason: rule for rule in RULES}


def tally(records: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    """(removals by reason, posts fetched) over the audit records of the
    pages of a cook."""
    reasons: dict[str, int] = {}
    fetched = 0
    for record in records:
        summary = (record or {}).get('summary') or {}
        fetched += int(summary.get('raw_fetched') or 0)
        for reason, count in (summary.get('by_reason') or {}).items():
            reasons[reason] = reasons.get(reason, 0) + int(count)
    return reasons, fetched


def is_on(recipe: FeedRecipe, rule: Rule) -> bool:
    """Whether the recipe still has the setting of *rule* switched on."""
    hard = recipe.diet.hard_filters
    kinds = {mod.type: mod for mod in recipe.kitchen.modifiers}
    reason = rule.reason
    return {
        'diet:must_have_media': hard.must_have_media,
        'diet:must_have_alt_text': hard.must_have_alt_text,
        'diet:no_sensitive_media': hard.no_sensitive_media,
        'diet:positive_only': hard.positive_only,
        'diet:min_content_length': hard.min_content_length > 0,
        'diet:keyword_blocklist': bool(recipe.diet.keyword_blocklist),
        'pantry_missing_included_tag': bool(recipe.pantry.hashtags_include),
        'pantry_excluded_tag': bool(recipe.pantry.hashtags_exclude),
        'reach_not_followed': recipe.pantry.network_reach.enabled,
        'heat_cut': 'heat' in kinds,
        'author_cap': 'diversity' in kinds and 'author' in (
            getattr(kinds['diversity'], 'diversity_dimensions', None)
            or ['author']),
        'instance_cap': 'instance' in (
            getattr(kinds.get('diversity'), 'diversity_dimensions', None)
            or []),
        'cluster_cap': 'similarity_cluster' in kinds,
    }[reason]


def suggestion(recipe: FeedRecipe,
               records: list[dict[str, Any]]) -> Rule | None:
    """The rule that removed the most posts in the last cook, among the ones
    *recipe* still has on; None when no single rule removed any (an empty
    scope, or the posts were all removed by something that is not a
    setting, such as the viewer's own filters)."""
    reasons, _fetched = tally(records)
    best = None
    best_count = 0
    for rule in RULES:
        count = reasons.get(rule.reason, 0)
        if count > best_count and is_on(recipe, rule):
            best, best_count = rule, count
    return best


def source_is_empty(records: list[dict[str, Any]]) -> bool:
    """Whether the last cook fetched no post at all (an empty list, a quiet
    hashtag): there is nothing to relax."""
    return bool(records) and tally(records)[1] == 0


def apply(recipe: FeedRecipe, rule: Rule) -> None:
    """Switch the setting of *rule* off, on *recipe* itself (the caller
    passes a copy)."""
    hard = recipe.diet.hard_filters
    mods = recipe.kitchen.modifiers
    reason = rule.reason
    if reason == 'diet:must_have_media':
        hard.must_have_media = False
    elif reason == 'diet:must_have_alt_text':
        hard.must_have_alt_text = False
    elif reason == 'diet:no_sensitive_media':
        hard.no_sensitive_media = False
    elif reason == 'diet:positive_only':
        hard.positive_only = False
    elif reason == 'diet:min_content_length':
        hard.min_content_length = 0
    elif reason == 'diet:keyword_blocklist':
        recipe.diet.keyword_blocklist = []
    elif reason == 'pantry_missing_included_tag':
        recipe.pantry.hashtags_include = []
    elif reason == 'pantry_excluded_tag':
        recipe.pantry.hashtags_exclude = []
    elif reason == 'reach_not_followed':
        recipe.pantry.network_reach.enabled = False
    elif reason == 'heat_cut':
        mods[:] = [mod for mod in mods if mod.type != 'heat']
    elif reason == 'author_cap':
        for mod in mods:
            if mod.type == 'diversity':
                dims = [d for d in (mod.diversity_dimensions or ['author'])
                        if d != 'author']
                mod.diversity_dimensions = dims
        mods[:] = [mod for mod in mods if mod.type != 'diversity'
                   or mod.diversity_dimensions]
    elif reason == 'instance_cap':
        for mod in mods:
            if mod.type == 'diversity':
                mod.diversity_dimensions = [
                    d for d in (mod.diversity_dimensions or ['author'])
                    if d != 'instance']
    elif reason == 'cluster_cap':
        mods[:] = [mod for mod in mods if mod.type != 'similarity_cluster']


def restore(current: FeedRecipe, before: FeedRecipe, rule: Rule) -> None:
    """Turn the setting of *rule* back on in *current* (the recipe as it is
    stored now) as it was in *before*: only that setting, so whatever else
    was edited since stays."""
    reason = rule.reason
    hard, was = current.diet.hard_filters, before.diet.hard_filters
    if reason == 'diet:must_have_media':
        hard.must_have_media = was.must_have_media
    elif reason == 'diet:must_have_alt_text':
        hard.must_have_alt_text = was.must_have_alt_text
    elif reason == 'diet:no_sensitive_media':
        hard.no_sensitive_media = was.no_sensitive_media
    elif reason == 'diet:positive_only':
        hard.positive_only = was.positive_only
    elif reason == 'diet:min_content_length':
        hard.min_content_length = was.min_content_length
    elif reason == 'diet:keyword_blocklist':
        current.diet.keyword_blocklist = list(before.diet.keyword_blocklist)
    elif reason == 'pantry_missing_included_tag':
        current.pantry.hashtags_include = list(
            before.pantry.hashtags_include)
    elif reason == 'pantry_excluded_tag':
        current.pantry.hashtags_exclude = list(
            before.pantry.hashtags_exclude)
    elif reason == 'reach_not_followed':
        current.pantry.network_reach = copy.deepcopy(
            before.pantry.network_reach)
    else:
        kind = {'heat_cut': 'heat', 'cluster_cap': 'similarity_cluster'
                }.get(reason, 'diversity')
        current.kitchen.modifiers[:] = [
            mod for mod in current.kitchen.modifiers if mod.type != kind
        ] + [copy.deepcopy(mod) for mod in before.kitchen.modifiers
             if mod.type == kind]


def setting_name(rule: Rule, words: Words = ENGLISH) -> str:
    """The name the builder shows for the setting of *rule*, translated
    (the same texts as the builder's rows)."""
    _ = words.gettext
    return {
        'Only Posts With Media': _('Only Posts With Media'),
        'Require Alt Text on Media': _('Require Alt Text on Media'),
        'Hide Posts Marked Sensitive': _('Hide Posts Marked Sensitive'),
        'Only Positive Posts': _('Only Positive Posts'),
        'Minimum Length': _('Minimum Length'),
        'Blocked Words': _('Blocked Words'),
        'Include Hashtags': _('Include Hashtags'),
        'Exclude Hashtags': _('Exclude Hashtags'),
        'Only People I Follow': _('Only People I Follow'),
        'Hide the Least Engaging': _('Hide the Least Engaging'),
        'Limit Posts per Person': _('Limit Posts per Person'),
        'Count Servers Too': _('Count Servers Too'),
        'Group Similar Posts': _('Group Similar Posts'),
    }[rule.name]


def label(rule: Rule, words: Words = ENGLISH) -> str:
    """The button's text: "Turn off “Only Posts With Media”"."""
    _ = words.gettext
    name = setting_name(rule, words)
    if rule.verb == 'clear':
        # Translators: a button that empties a field of the recipe; %s is
        # the field's name, such as "Blocked Words".
        return _('Clear “%s”') % name
    # Translators: a button that switches a setting of the recipe off; %s
    # is the setting's name, such as "Only Posts With Media".
    return _('Turn Off “%s”') % name


def done(rule: Rule, words: Words = ENGLISH) -> str:
    """The toast after it: what was relaxed."""
    # Translators: a toast after Relax Filters; %s is the setting that was
    # switched off or emptied, such as "Only Posts With Media".
    return words.gettext('Relaxed “%s”') % setting_name(rule, words)
