# SPDX-License-Identifier: GPL-3.0-or-later
"""New Recipe templates, smart defaults, the modes and the "More Posts <->
Better" slider (no widgets, standard library only, every function pure: each
returns a new recipe and changes none).

Ported from moshidon's ``smart_defaults.py``, ``optimal_scope_defaults.py`` and
``feed_chef_improvements.md``, with their numbers, mapped to Babble's fields:

* **Heat.** Moshidon's heat threshold is a free percent; Babble's reader
  picks Cool (0), Warm (50), Hot (75) or Fire (90) (``heat.STEPS``). A moshidon
  value is kept as it is in ``Defaults.heat`` and mapped to a step in
  ``Defaults.step``: to the nearest step, except for the slider and the modes,
  which map *down* (40 is Cool, 60 is Warm, 80 is Hot), so "More Posts" never
  hides more than moshidon's would.
* **Freshness.** ``decay_minutes``, between 2 and 24 hours as moshidon clamps it.
* **Templates.** moshidon names four use cases and what each is made of
  (``feed_chef_improvements.md``, section 4): Recent News (chronological and a
  light freshness), Viral (score and a high heat), Topic (hashtags and a
  middle heat) and Safe Feed (strict filters and a middle heat).

Names and one-line descriptions go through the ``Words`` of the caller, as in
``sentence.py``, so this module imports no translation module.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from . import heat as heatmod
from . import recipeedit
from .auditreport import ENGLISH, Words
from .recipe import FeedRecipe, default_recipe

# moshidon's OptimalScopeDefaults.SCOPE_CONFIGS['optimal_settings'] and its
# expected metrics: (heat threshold, freshness decay in minutes, suggest
# freshness, suggest heat, expected recipe score, expected Low %, High %).
SCOPES: dict[str, tuple[int, int, bool, bool, float, int, int]] = {
    'home': (40, 480, True, True, 0.65, 40, 15),
    'local': (55, 720, True, True, 0.60, 50, 12),
    'federated': (70, 1440, True, True, 0.70, 30, 20),
    'list': (45, 600, True, False, 0.68, 45, 18),
}
# Hashtags moshidon recommends for a scope.
RECOMMENDED_HASHTAGS = {'home': 0, 'local': 1, 'federated': 2, 'list': 0}
HEAT_MIN, HEAT_MAX = 40, 85
DECAY_MIN, DECAY_MAX = 120, 1440
# Each hashtag moves the optimum: +5 percent of heat (to 85), +120 minutes.
PER_TAG_HEAT, PER_TAG_DECAY = 5, 120


class Defaults(NamedTuple):
    # moshidon's heat threshold (40 to 85), and the Babble step it maps to.
    heat: int
    step: int
    # None: no freshness (a chronological recipe has no need of it).
    decay_minutes: int | None
    base_sort: str
    suggest_heat: bool
    suggest_freshness: bool


def _scope(scope: str) -> tuple[int, int, bool, bool, float, int, int]:
    return SCOPES.get(scope) or SCOPES['federated']


def snap_nearest(percent: float) -> int:
    """The heat step (0, 50, 75 or 90) nearest to *percent* (a tie goes to
    the hotter one)."""
    values = [value for _name, value in heatmod.STEPS]
    return min(values, key=lambda value: (abs(value - percent), -value))


def snap_down(percent: float) -> int:
    """The hottest heat step that is not above *percent*."""
    return max(value for _name, value in heatmod.STEPS if value <= percent)


def optimal_heat(scope: str, hashtag_count: int = 0,
                 historical_scores: list[float] | None = None) -> int:
    """moshidon's ``calculate_optimal_heat_threshold``: the scope's optimum,
    5 more per hashtag (to 85), 5 more when the history's 75th percentile is
    over 20 and 5 less when it is under 5 (with more than 10 scores), kept
    between 40 and 85."""
    base = _scope(scope)[0]
    if hashtag_count > 0:
        base = min(base + hashtag_count * PER_TAG_HEAT, HEAT_MAX)
    if historical_scores and len(historical_scores) > 10:
        ordered = sorted(historical_scores)
        p75 = ordered[int(len(ordered) * 0.75)]
        if p75 > 20:
            base += 5
        elif p75 < 5:
            base -= 5
    return min(max(base, HEAT_MIN), HEAT_MAX)


def optimal_decay(scope: str, hashtag_count: int = 0,
                  base_sort: str = 'score') -> int | None:
    """moshidon's ``calculate_optimal_freshness_decay``: None for a
    chronological recipe, else the scope's optimum, 120 more minutes per
    hashtag, kept between 2 and 24 hours."""
    if base_sort == 'chronological':
        return None
    base = _scope(scope)[1]
    if hashtag_count > 0:
        base = min(base + hashtag_count * PER_TAG_DECAY, DECAY_MAX)
    return min(max(base, DECAY_MIN), DECAY_MAX)


def smart_defaults(scope: str, hashtag_count: int = 0,
                   base_sort: str = 'score',
                   historical_scores: list[float] | None = None
                   ) -> Defaults:
    """The heat and freshness a new recipe of this context should start
    with (see the module's docstring for the mapping to Babble's steps)."""
    heat = optimal_heat(scope, hashtag_count, historical_scores)
    entry = _scope(scope)
    return Defaults(heat, snap_nearest(heat),
                    optimal_decay(scope, hashtag_count, base_sort),
                    base_sort, entry[3], entry[2])


def recommended_hashtags(scope: str) -> int:
    """How many hashtags moshidon recommends for a scope (0 for Home and a
    list, which are already focused)."""
    return RECOMMENDED_HASHTAGS.get(scope, 2)


def estimate_quality(heat_percent: float | None) -> dict[str, Any]:
    """moshidon's ``estimate_quality_score``: a rough guess of the share of
    Low and High posts and of the recipe score for a heat threshold (50
    when there is none). It is an estimate of a model, not of the posts."""
    heat = 50 if heat_percent is None else heat_percent
    low = max(100 - heat * 1.2, 30)
    high = max(heat * 0.15, 5)
    score = (100 - low) / 100 * 0.4 + high / 100 * 0.35 + 0.5 * 0.25
    return {'estimate': True, 'estimated_score': min(score, 1.0),
            'estimated_low_quality_pct': low,
            'estimated_high_quality_pct': high}


# -- the slider and the modes ---------------------------------------------

class Preset(NamedTuple):
    heat: int            # a Babble step
    base_sort: str
    decay_minutes: int | None
    # moshidon's own numbers, for the record: heat threshold, freshness
    raw_heat: int


# feed_chef_improvements.md, section 6: "More Posts" (0-25): heat 40,
# chronological; "Balanced" (25-75): heat 60, score and a light freshness;
# "Better" (75-100): heat 80, score and a medium freshness. Light is 12
# hours, medium 6 (moshidon's two decays for "add freshness").
MORE_POSTS = Preset(snap_down(40), 'chronological', None, 40)
BALANCED = Preset(snap_down(60), 'score', 720, 60)
BETTER = Preset(snap_down(80), 'score', 360, 80)
PRESETS = {'more_posts': MORE_POSTS, 'balanced': BALANCED,
           'best_quality': BETTER}

# Explore shows everything, Focused a middle heat (and suggests hashtags),
# Premium only the best; Custom leaves the recipe as it is.
MODES = ('explore', 'focused', 'premium', 'custom')
_MODE_PRESET = {'explore': MORE_POSTS, 'focused': BALANCED,
                'premium': BETTER}


def slider_preset(position: float) -> Preset | None:
    """The preset of a "More Posts <-> Better" slider at *position* (0 to
    100): under 25 More Posts, 25 to 75 Balanced, over 75 Better; a position
    that is not a number (NaN, text) is no preset (None: the recipe is left
    as it is, like Custom)."""
    try:
        position = float(position)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(position):
        return None
    position = max(0.0, min(100.0, position))
    if position < 25:
        return MORE_POSTS
    if position <= 75:
        return BALANCED
    return BETTER


def slider_position(recipe: FeedRecipe) -> float | None:
    """Where the slider sits for a recipe: the middle of the band whose
    preset the recipe has (12.5, 50 or 87.5), or None when it has none (the
    builder then leaves the slider unset)."""
    for position, preset in ((12.5, MORE_POSTS), (50.0, BALANCED),
                             (87.5, BETTER)):
        if _has(recipe, preset):
            return position
    return None


def focused(recipe: FeedRecipe) -> bool:
    """Whether the recipe has a focus (More Like This): its order is by
    relevance to the focus, whatever the sort says."""
    return bool(recipe.kitchen.active_focus_keywords
                or recipe.kitchen.active_focus_hashtags)


def _has(recipe: FeedRecipe, preset: Preset) -> bool:
    return (recipeedit.current(recipe, 'heat') or 0) == preset.heat \
        and (focused(recipe)
             or recipe.kitchen.base_sort == preset.base_sort) \
        and recipeedit.current(recipe, 'freshness') == preset.decay_minutes


def with_preset(recipe: FeedRecipe, preset: Preset) -> FeedRecipe:
    """A copy of *recipe* with the preset's heat, sort and freshness (the
    other modifiers, the filters and the pantry are left as they are). A
    recipe with a focus keeps its sort: the focus decides the order, and
    the advice never touches the sort of such a recipe either."""
    edits = [recipeedit.Edit('heat', preset.heat or None),
             recipeedit.Edit('freshness', preset.decay_minutes)]
    if not focused(recipe):
        edits.insert(0, recipeedit.Edit('base_sort', preset.base_sort))
    return recipeedit.apply_edits(recipe, edits).recipe


def with_slider(recipe: FeedRecipe, position: float) -> FeedRecipe:
    """A copy of *recipe* at a slider position (a copy of it unchanged for
    a position that is not a number)."""
    preset = slider_preset(position)
    if preset is None:
        return recipeedit.restore(recipe, ())
    return with_preset(recipe, preset)


def with_mode(recipe: FeedRecipe, mode: str) -> FeedRecipe:
    """A copy of *recipe* in a mode: Explore, Focused, Premium; Custom is
    the recipe unchanged (a copy)."""
    if mode not in MODES:
        raise ValueError('not a mode: %r' % (mode,))
    preset = _MODE_PRESET.get(mode)
    return recipeedit.restore(recipe, ()) if preset is None \
        else with_preset(recipe, preset)


def mode_of(recipe: FeedRecipe) -> str:
    """The mode a recipe is in: the one whose preset it has, else Custom."""
    for mode, preset in _MODE_PRESET.items():
        if _has(recipe, preset):
            return mode
    return 'custom'


def mode_names(words: Words = ENGLISH) -> dict[str, tuple[str, str]]:
    """(name, one line) of each mode."""
    _ = words.gettext
    return {
        'explore': (_('Explore'), _('Show everything, newest first')),
        'focused': (_('Focused'),
                    _('Hide the least engaging half and fade older posts')),
        'premium': (_('Premium'), _('Only the most engaging posts')),
        'custom': (_('Custom'), _('Your own settings')),
    }


def slider_labels(words: Words = ENGLISH) -> tuple[str, str]:
    """The two ends of the slider."""
    _ = words.gettext
    return _('More Posts'), _('Better Posts')


# -- templates --------------------------------------------------------------

TEMPLATE_IDS = ('blank', 'recent_news', 'viral', 'topic', 'safe_feed')


def describe(template_id: str, words: Words = ENGLISH) -> tuple[str, str]:
    """(name, one line) of a template, in the caller's language."""
    _ = words.gettext
    found = {
        'blank': (_('Blank Recipe'), _('Start from your Home timeline')),
        'recent_news': (_('Recent News'),
                        _('The newest posts about the news')),
        'viral': (_('Viral'), _('Only what many people are reacting to')),
        'topic': (_('Topic'), _('Posts about the hashtags you pick')),
        'safe_feed': (_('Safe Feed'),
                      _('Strict filters on the people you follow')),
    }.get(template_id)
    if found is None:
        raise ValueError('not a template: %r' % (template_id,))
    return found


def _edits(template_id: str, topic: str | None) -> list[recipeedit.Edit]:
    edit = recipeedit.Edit
    if template_id == 'recent_news':
        return [edit('scope', 'federated'),
                edit('hashtags_include', ['news']),
                edit('base_sort', 'chronological'),
                edit('freshness', BALANCED.decay_minutes)]
    if template_id == 'viral':
        return [edit('scope', 'federated'), edit('heat', 90),
                edit('freshness', optimal_decay('federated'))]
    if template_id == 'topic':
        tags = [topic.strip().lstrip('#').lower()] if topic and \
            topic.strip().lstrip('#') else []
        return [edit('scope', 'federated'), edit('hashtags_include', tags),
                edit('heat', snap_down(60)),
                edit('freshness', optimal_decay('federated', 1))]
    if template_id == 'safe_feed':
        return [edit('scope', 'home'), edit('hard.must_have_alt_text', True),
                edit('hard.no_sensitive_media', True),
                edit('hard.min_content_length', 20),
                edit('heat', snap_down(60)),
                edit('freshness', optimal_decay('home'))]
    return []


def new_recipe(template_id: str, *, name: str | None = None,
               words: Words = ENGLISH, topic: str | None = None,
               recipe_id: str | None = None) -> FeedRecipe:
    """A new recipe from a template: its name is the template's (in the
    caller's language) unless one is given, its id a new one unless one
    is given. *topic* is the hashtag of a Topic recipe (none: the reader
    adds it). Every one is a valid recipe (the import accepts it)."""
    title = describe(template_id, words)[0]
    recipe = default_recipe(name or title)
    if recipe_id:
        recipe.id = recipe_id
    return recipeedit.apply_edits(recipe, _edits(template_id, topic)).recipe


def with_smart_defaults(recipe: FeedRecipe,
                        preference: str = 'balanced') -> FeedRecipe:
    """A copy of *recipe* with the heat and freshness of its context
    (scope, hashtags, sort) wherever it has none: a new recipe starts there
    and one that already has the modifier keeps it. *preference* is
    ``more_posts``, ``balanced`` or ``best_quality`` (moshidon's
    ``suggest_recipe_defaults``)."""
    if preference not in PRESETS:
        raise ValueError('not a preference: %r' % (preference,))
    scope = recipe.pantry.scope
    tags = len(recipe.pantry.hashtags_include)
    edits = []
    if preference == 'balanced':
        found = smart_defaults(scope, tags, recipe.kitchen.base_sort)
        heat = found.step if found.suggest_heat else None
        decay = found.decay_minutes
        if recipe.kitchen.base_sort == 'chronological':
            heat = None
    else:
        preset = PRESETS[preference]
        heat, decay = preset.heat or None, preset.decay_minutes
        edits.append(recipeedit.Edit('base_sort', preset.base_sort))
    if heat and recipeedit.current(recipe, 'heat') is None:
        edits.append(recipeedit.Edit('heat', heat))
    if decay and recipeedit.current(recipe, 'freshness') is None:
        edits.append(recipeedit.Edit('freshness', decay))
    return recipeedit.apply_edits(recipe, edits).recipe
