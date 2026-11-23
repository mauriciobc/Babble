# SPDX-License-Identifier: GPL-3.0-or-later
"""Recommendations for a recipe, from its audit records (no widgets, no GTK,
standard library only; every function is pure).

moshidon's four recommenders in one place, with their conditions and
thresholds, applied to the metrics of ``quality.py`` and to the recipe's own
settings:

* **rule** (``rule_based_recommendations.py``): the share of posts the diet
  removes, how aggressive the settings are, a search that is too wide, an
  unreachable heat;
* **quality** (``quality_based_recommendations.py``): the bands of the kitchen
  stage, a low average, freshness that zeroes scores, a heat that does not
  pay, a low validity;
* **analyzer** (``algorithm_improvement_analyzer.py``): the improvements it
  suggests (a hotter step, a longer fade-out, a newest-first order);
* **comparison** (``comparison_recommendations.py``): what the reader's most
  similar recipes do that this one does not.

Each is a ``Recommendation``: the ``field`` it changes, its ``current`` and
``suggested`` values, why, the ``severity`` and an impact that is always
labelled an *estimate* (moshidon's percentages are guesses, not
measurements). A recommendation carries its ``edits`` (see ``recipeedit.py``):
``apply`` gives a changed copy of the recipe and the undo that restores it.

What moshidon suggests that Babble cannot or must not do is not suggested
(each decision is in the ADR): a heat step the page would fall back from, a
change that leaves a recipe the import refuses, a sort that would replace the
reader's focus, a loosened accessibility rule (alt text), a cut of blocked
words nobody chose, and a no-op. A suggestion that needs the reader's choice
(which hashtags) has ``manual`` set and no edits.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from . import heat as heatmod
from . import recipeedit
from . import templates
from .auditreport import ENGLISH, Words
from .recipe import FeedRecipe
from .recipeedit import Edit

SEVERITIES = ('high', 'medium', 'low')
SOURCES = ('rule', 'quality', 'analyzer', 'comparison')
IMPACT_KIND = 'estimate'
# moshidon: freshness fade-outs are doubled up to a day.
DECAY_CAP = 1440


class Recommendation(NamedTuple):
    id: str
    source: str
    kind: str                     # moshidon's ``type``
    severity: str
    field: str
    current: Any
    suggested: Any
    edits: tuple[Edit, ...]
    reason: str                   # a reason id (see ``describe``)
    params: tuple[tuple[str, Any], ...] = ()
    impact: str = ''              # an impact id
    manual: bool = False

    @property
    def impact_kind(self) -> str:
        return IMPACT_KIND if self.impact else ''

    def apply(self, recipe: FeedRecipe) -> recipeedit.Applied:
        """A copy of *recipe* with this change made, and the undo that puts
        it back (``recipeedit.restore``). Raises ValueError for a manual
        recommendation."""
        if self.manual or not self.edits:
            raise ValueError('this suggestion needs the reader to choose')
        return recipeedit.apply_edits(recipe, self.edits)

    def to_json(self) -> dict[str, Any]:
        """Numbers and ids only."""
        return {'id': self.id, 'source': self.source, 'kind': self.kind,
                'severity': self.severity, 'field': self.field,
                'current': self.current, 'suggested': self.suggested,
                'reason': self.reason, 'impact': self.impact,
                'impact_kind': self.impact_kind, 'manual': self.manual}


def _rec(rid, source, kind, severity, field, current, suggested, reason,
         params=None, impact='', edits=None, manual=False):
    if edits is None:
        edits = () if manual else (Edit(field, suggested),)
    return Recommendation(rid, source, kind, severity, field, current,
                          suggested, tuple(edits), reason,
                          tuple(sorted((params or {}).items())), impact,
                          manual)


# -- context ----------------------------------------------------------------

class Context(NamedTuple):
    recipe: FeedRecipe
    kitchen: dict[str, Any]       # quality.aggregate of the kitchen stage
    entering: int                 # posts that entered the kitchen, per page
    page: int                     # posts shown, per page
    score: float                  # the recipe score
    filter_rate: float            # percent of the pantry's posts the diet cut


def context(recipe: FeedRecipe, metrics: dict[str, Any] | None) -> Context:
    """What the generators read, from ``quality.assess_pages`` (None: no
    cook yet, so only the settings can be judged)."""
    metrics = metrics or {}
    kitchen = metrics.get('kitchen') or {}
    pantry = (metrics.get('pantry') or {}).get('total_toots', 0)
    diet = (metrics.get('diet') or {}).get('total_toots', 0)
    rate = (pantry - diet) / pantry * 100 if pantry else 0.0
    # moshidon's absolute thresholds (fewer than 5 or 10 posts, more than
    # 20) and the heat fallback are about *a page*: the smallest page of the
    # cook is the one judged, so a step a page would fall back from is never
    # suggested. Metrics with no page sizes (made by hand) use the totals.
    sizes = metrics.get('page_sizes') or {}

    def smallest(stage, total):
        found = [n for n in sizes.get(stage, ()) if n > 0]
        return min(found) if found else total
    return Context(recipe, kitchen, smallest('diet', diet),
                   smallest('kitchen', kitchen.get('total_toots', 0)),
                   metrics.get('recipe_quality_score', 0.0), rate)


def _heat(recipe):
    return recipeedit.current(recipe, 'heat')


def _decay(recipe):
    return recipeedit.current(recipe, 'freshness')


def _focused(recipe) -> bool:
    kitchen = recipe.kitchen
    return bool(kitchen.active_focus_keywords
                or kitchen.active_focus_hashtags)


def complexity(recipe: FeedRecipe) -> float:
    """moshidon's search complexity: 1, 0.5 more for federated and 0.3 for
    local, 0.2 per included hashtag and 0.1 per excluded one."""
    pantry = recipe.pantry
    base = 1.0 + {'federated': 0.5, 'local': 0.3}.get(pantry.scope, 0.0)
    return round(base + len(pantry.hashtags_include) * 0.2
                 + len(pantry.hashtags_exclude) * 0.1, 2)


def aggressiveness(recipe: FeedRecipe) -> float:
    """moshidon's ``algorithm_aggressiveness``: 0.3 for a score order, 0.4 x
    the heat percent, and up to 0.3 for a fade-out under 6 hours."""
    found = 0.3 if recipe.kitchen.base_sort == 'score' else 0.0
    heat = _heat(recipe)
    if heat:
        found += heat / 100.0 * 0.4
    decay = _decay(recipe)
    if decay:
        found += max(0, (360 - decay) / 360.0) * 0.3
    return round(found, 2)


def _heat_edit(recipe, ctx, percent, direction, severity, kind, source,
               reason, params, impact):
    """A heat suggestion at the step nearest to moshidon's *percent*, or
    nothing: it must change the recipe in the direction asked (up: 1, down:
    -1) and, going up, be a step the page would not fall back from."""
    step = templates.snap_nearest(percent)
    current = _heat(recipe) or 0
    if (step - current) * direction <= 0:
        return []
    if direction > 0 and ctx.entering \
            and heatmod.used(step, ctx.entering) != step:
        return []
    return [_rec('heat_up' if direction > 0 else 'heat_down', source, kind,
                 severity, 'heat', current, step, reason, params, impact)]


def _longer_fade(recipe, severity, kind, source, reason, params, impact):
    decay = _decay(recipe)
    if not decay or decay >= DECAY_CAP:
        return []
    return [_rec('fade_longer', source, kind, severity, 'freshness', decay,
                 min(decay * 2, DECAY_CAP), reason, params, impact)]


def _add_fade(recipe, minutes, severity, kind, source, reason, params,
              impact):
    if _decay(recipe) is not None:
        return []
    return [_rec('fade_add', source, kind, severity, 'freshness', None,
                 minutes, reason, params, impact)]


def _newest_first(recipe, severity, kind, source, reason, params, impact):
    if recipe.kitchen.base_sort == 'chronological' or _focused(recipe):
        return []
    return [_rec('sort_newest', source, kind, severity, 'base_sort', 'score',
                 'chronological', reason, params, impact)]


def _add_tags(recipe, severity, kind, source, reason, params, impact):
    return [_rec('hashtags_add', source, kind, severity, 'hashtags_include',
                 list(recipe.pantry.hashtags_include), None, reason, params,
                 impact, manual=True)]



# -- rule-based (moshidon's RuleBasedRecommender) ---------------------------------

def rule_based(ctx: Context) -> list[Recommendation]:
    recipe = ctx.recipe
    found: list[Recommendation] = []
    # The share of posts the diet removed (the actual rate of the last
    # cook; moshidon's estimate from the settings is not used).
    rate = ctx.filter_rate
    if rate > 50:
        severity = 'high' if rate > 70 else 'medium'
        impact = 'more_posts_a' if rate > 70 else 'more_posts_b'
        params = {'pct': '%d%%' % round(rate)}
        length = recipe.diet.hard_filters.min_content_length
        if length > 20:
            found.append(_rec(
                'length_down', 'rule', 'filter_relaxation', severity,
                'hard.min_content_length', length, max(10, length - 10),
                'filters_hide', params, impact))
        else:
            found.append(_rec(
                'filters_relax', 'rule', 'filter_relaxation', severity,
                'filters', rate, None, 'filters_hide', params, impact,
                manual=True))
    # Too aggressive: fade over six hours at least, and a gentler heat.
    # (The opposite rule, "too conservative", is not made: it only ever
    # fires on a newest-first recipe, whose order is the reader's choice.)
    if aggressiveness(recipe) > 0.8:
        decay = _decay(recipe)
        if decay and decay < 360:
            found.append(_rec('fade_longer', 'rule', 'algorithm_tuning',
                              'high', 'freshness', decay, 360, 'aggressive',
                              None, 'relevant'))
        found += _heat_edit(recipe, ctx, 75, -1, 'high', 'algorithm_tuning',
                            'rule', 'aggressive', None, 'relevant')
    count = len(recipe.pantry.hashtags_include)
    if complexity(recipe) > 2.5 and count > 3:
        # The engine searches only the first three hashtags.
        found.append(_rec(
            'hashtags_fewer', 'rule', 'complexity_reduction', 'medium',
            'hashtags_include', list(recipe.pantry.hashtags_include),
            list(recipe.pantry.hashtags_include[:3]), 'too_many_tags',
            {'count': count}, 'fewer_requests'))
    if recipe.pantry.scope == 'federated' and not count:
        found += _add_tags(recipe, 'medium', 'hashtag_addition', 'rule',
                           'wide_scope', {'count': templates
                                          .recommended_hashtags('federated')},
                           'focus')
    heat = _heat(recipe)
    if heat and heat > 90 and 0 < ctx.page < 5:
        found += _heat_edit(recipe, ctx, 75, -1, 'high', 'heat_threshold',
                            'rule', 'heat_few',
                            {'count': ctx.page},
                            'more_posts_keep')
    return found


# -- quality-based (moshidon's QualityBasedRecommender) --------------------------

def _shares(kitchen):
    total = kitchen.get('total_toots', 0)
    band = kitchen.get('quality_distribution') or {}
    if not total:
        return total, 0.0, 0.0
    return (total, band.get('low', 0) / total * 100,
            (band.get('high', 0) + band.get('very_high', 0)) / total * 100)


def _distribution(ctx: Context) -> list[Recommendation]:
    recipe = ctx.recipe
    total, low, high = _shares(ctx.kitchen)
    if not total:
        return []
    found = []
    if low > 80:
        found += _heat_edit(recipe, ctx, 75, 1, 'high', 'quality_distribution',
                            'quality', 'low_share', {'pct': '%d%%' % low},
                            'quality_a')
        found += _add_fade(recipe, 360, 'high', 'quality_distribution',
                           'quality', 'low_share', {'pct': '%d%%' % low},
                           'quality_a')
        found += _add_tags(recipe, 'high', 'quality_distribution', 'quality',
                           'low_share', {'pct': '%d%%' % low}, 'quality_a')
    elif low > 60:
        found += _heat_edit(recipe, ctx, 60, 1, 'medium',
                            'quality_distribution', 'quality', 'low_share',
                            {'pct': '%d%%' % low}, 'quality_b')
    if high < 10 and ctx.page > 20:
        found += _heat_edit(recipe, ctx, 85, 1, 'medium',
                            'quality_distribution', 'quality', 'few_high',
                            {'pct': '%d%%' % high}, 'high_share')
    return found


def _scores(ctx: Context) -> list[Recommendation]:
    recipe, kitchen = ctx.recipe, ctx.kitchen
    found = []
    avg = kitchen.get('avg_quality_score', 0)
    median = kitchen.get('median_quality_score', 0)
    if kitchen.get('total_toots') and avg < 5 and median < 3:
        params = {'avg': '%.1f' % avg}
        if _decay(recipe) is not None:
            found += _longer_fade(recipe, 'high', 'low_quality_scores',
                                  'quality', 'avg_low_fresh', params,
                                  'time_to_engage')
            found += _newest_first(recipe, 'high', 'low_quality_scores',
                                   'quality', 'avg_low_fresh', params,
                                   'time_to_engage')
        else:
            found += _heat_edit(recipe, ctx, 75, 1, 'high',
                                'low_quality_scores', 'quality', 'avg_low',
                                params, 'quality_a')
            found += _add_tags(recipe, 'high', 'low_quality_scores',
                               'quality', 'avg_low', params, 'quality_a')
            if recipe.pantry.scope not in ('home', 'list'):
                found.append(_rec('scope_home', 'quality',
                                  'low_quality_scores', 'high', 'scope',
                                  recipe.pantry.scope, 'home', 'avg_low',
                                  params, 'quality_a'))
    if kitchen.get('total_toots') and ctx.score < 0.5:
        found.append(_rec('review_criteria', 'quality',
                          'recipe_quality_score', 'high', 'recipe',
                          ctx.score, None, 'low_score',
                          {'score': '%.2f' % ctx.score}, 'score_up',
                          manual=True))
    return found


def _freshness(ctx: Context) -> list[Recommendation]:
    recipe, kitchen = ctx.recipe, ctx.kitchen
    if not kitchen.get('total_toots'):
        return []
    avg = kitchen.get('avg_quality_score', 0)
    if _decay(recipe) is not None and avg == 0:
        found = _longer_fade(recipe, 'high', 'freshness_impact', 'quality',
                             'zero_fresh', None, 'nonzero')
        found.append(_rec('fade_remove', 'quality', 'freshness_impact',
                          'high', 'freshness', _decay(recipe), None,
                          'zero_fresh', None, 'nonzero'))
        return found + _newest_first(recipe, 'high', 'freshness_impact',
                                     'quality', 'zero_fresh', None,
                                     'nonzero')
    if _decay(recipe) is None and avg < 10:
        return _add_fade(recipe, 720, 'low', 'freshness_impact', 'quality',
                         'add_fresh', None, 'fresher')
    return []


def _heat_pays(ctx: Context) -> list[Recommendation]:
    recipe, kitchen = ctx.recipe, ctx.kitchen
    heat = _heat(recipe)
    total, _low, high = _shares(kitchen)
    if not heat or not total:
        return []
    page = ctx.page
    found = []
    if heat < 60 and high < 15:
        found += _heat_edit(recipe, ctx, 75, 1, 'medium', 'heat_threshold',
                            'quality', 'heat_pays',
                            {'heat': heat, 'pct': '%d%%' % high},
                            'high_share')
    if heat > 85 and page < 10:
        found += _heat_edit(recipe, ctx, 70, -1, 'high', 'heat_threshold',
                            'quality', 'heat_few', {'count': page},
                            'more_posts_keep')
    return found


def _validity(ctx: Context) -> list[Recommendation]:
    kitchen = ctx.kitchen
    rate = kitchen.get('validity_rate', 100)
    if not kitchen.get('total_toots') or rate >= 90:
        return []
    found = []
    for reason in sorted(kitchen.get('rejection_reasons') or ()):
        field = ('hashtags_include' if 'hashtags' in reason
                 else 'keyword_blocklist' if 'keyword' in reason
                 else 'hard.must_have_alt_text' if 'alt_text' in reason
                 else None)
        if field is not None:
            found.append(_rec('review_' + field, 'quality', 'validity_issues',
                              'high', field, None, None, 'validity',
                              {'pct': '%d%%' % rate}, 'valid', manual=True))
    return found


def quality_based(ctx: Context) -> list[Recommendation]:
    found: list[Recommendation] = []
    for make in (_distribution, _scores, _freshness, _heat_pays, _validity):
        found += make(ctx)
    return found


# -- the analyzer (moshidon's AlgorithmImprovementAnalyzer) ----------------------

def analyzer(ctx: Context) -> list[Recommendation]:
    """Its improvements: a hotter step when most posts are Low (20 points
    more, to 90 at most), a longer fade-out when freshness zeroes the
    scores, hashtags for a bare federated recipe, newest first when the
    scores are very low."""
    recipe, kitchen = ctx.recipe, ctx.kitchen
    total, low, _high = _shares(kitchen)
    if not total:
        return []
    avg = kitchen.get('avg_quality_score', 0)
    found = []
    heat = _heat(recipe)
    if low > 80 and heat:
        found += _heat_edit(recipe, ctx, min(heat + 20, 90), 1, 'high',
                            'too_many_low_quality', 'analyzer', 'low_share',
                            {'pct': '%d%%' % low}, 'quality_a')
    if avg == 0 and _decay(recipe):
        found += _longer_fade(recipe, 'high', 'zero_scores', 'analyzer',
                              'zero_fresh', None, 'nonzero')
    if avg < 5:
        found += _newest_first(recipe, 'medium', 'few_high_quality',
                               'analyzer', 'avg_low_fresh',
                               {'avg': '%.1f' % avg}, 'time_to_engage')
    return found


# -- comparison (moshidon's ComparisonRecommender) -------------------------------

_SCOPE_AXIS = {'home': 0, 'local': 1, 'federated': 2, 'list': 1.5}


def similarity(one: FeedRecipe, other: FeedRecipe) -> float:
    """1 / (1 + the euclidean distance) over the scope, the number of
    included hashtags, alt text, the number of blocked words and the order
    (moshidon's features)."""
    def features(recipe):
        return [_SCOPE_AXIS.get(recipe.pantry.scope, 2),
                len(recipe.pantry.hashtags_include),
                1 if recipe.diet.hard_filters.must_have_alt_text else 0,
                len(recipe.diet.keyword_blocklist),
                1 if recipe.kitchen.base_sort == 'score' else 0]
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(
        features(one), features(other), strict=True)))
    return 1 / (1 + distance)


def most_similar(recipe: FeedRecipe, others: list[FeedRecipe],
                 top: int = 3) -> list[tuple[FeedRecipe, float]]:
    """The *top* recipes of *others* most like *recipe* (not itself), the
    most similar first, ties in the order given."""
    found = [(other, similarity(recipe, other)) for other in others
             if other.id != recipe.id]
    return sorted(found, key=lambda pair: -pair[1])[:top]


def comparison(ctx: Context, others: list[FeedRecipe]
               ) -> list[Recommendation]:
    recipe = ctx.recipe
    near = most_similar(recipe, others)
    if not near:
        return []
    count = len(near)
    heats = sum(1 for other, _s in near if _heat(other))
    fades = sum(1 for other, _s in near if _decay(other) is not None)
    found = []
    if heats > count * 0.5 and not _heat(recipe) and recipe.kitchen \
            .base_sort == 'score':
        found += _heat_edit(recipe, ctx, 50, 1, 'medium', 'similar_pattern',
                            'comparison', 'similar_heat',
                            {'have': heats, 'total': count}, 'relevant')
    if fades > count * 0.5 and _decay(recipe) is None:
        found += _add_fade(recipe, 360, 'low', 'similar_pattern',
                           'comparison', 'similar_fresh',
                           {'have': fades, 'total': count}, 'fresher')
    if ctx.score < 0.3 and ctx.kitchen.get('total_toots'):
        found.append(_rec('review_similar', 'comparison', 'score_comparison',
                          'low', 'recipe', ctx.score, None, 'review_similar',
                          {'count': count}, 'learn', manual=True))
    return found


# -- all together ------------------------------------------------------------

def _doable(recipe: FeedRecipe, rec: Recommendation) -> bool:
    """A recommendation with edits must change the recipe and leave one the
    import accepts; a manual one is always doable."""
    if rec.manual:
        return True
    try:
        done = recipeedit.apply_edits(recipe, rec.edits).recipe
    except ValueError:
        return False
    return recipeedit.current(done, rec.field) != recipeedit.current(
        recipe, rec.field)


def recommend(recipe: FeedRecipe, metrics: dict[str, Any] | None,
              others: list[FeedRecipe] | None = None
              ) -> list[Recommendation]:
    """Every recommendation for *recipe*, most severe first (the order the
    rule, quality, analyzer and comparison recommenders gave them within
    a severity): the same change is made once (the first source to say it
    keeps it), and one that would not change the recipe is left out.
    *metrics* is ``quality.assess_pages`` of the recipe's last cook (None:
    only the settings are judged); *others* are the reader's other recipes,
    for the comparison."""
    ctx = context(recipe, metrics)
    found = rule_based(ctx)
    if metrics:
        found += quality_based(ctx) + analyzer(ctx)
    if others and len(others) > 0:
        found += comparison(ctx, others)
    # Most severe first (a stable sort: within a severity the rule,
    # quality, analyzer and comparison order stands), so of two sources
    # that say the same thing the more severe one is the one kept.
    found.sort(key=lambda rec: SEVERITIES.index(rec.severity))
    seen = set()
    unique = []
    for rec in found:
        key = (rec.field, repr(rec.suggested), rec.manual and rec.id)
        if key in seen or not _doable(recipe, rec):
            continue
        seen.add(key)
        unique.append(rec)
    return unique



# -- words --------------------------------------------------------------------

class Text(NamedTuple):
    title: str      # the setting, as the builder names it
    change: str     # "Warm to Hot", "6 hours to 12 hours" (empty: manual)
    reason: str
    impact: str     # always starts with the word Estimate


def field_title(field: str, words: Words = ENGLISH) -> str:
    _ = words.gettext
    return {
        'heat': _('Hide the Least Engaging'),
        'freshness': _('Favor Fresh Posts'),
        'base_sort': _('Sort Order'),
        'scope': _('Source'),
        'hashtags_include': _('Include Hashtags'),
        'hard.min_content_length': _('Minimum Length'),
        'hard.must_have_alt_text': _('Require Alt Text on Media'),
        'keyword_blocklist': _('Blocked Words'),
        'filters': _('Filters'),
        'recipe': _('Recipe'),
    }.get(field, field)


def value_text(field: str, value: Any, words: Words = ENGLISH) -> str:
    """A value of a field as the builder shows it."""
    _ = words.gettext
    if value is None:
        return _('Off')
    if field == 'heat':
        step = heatmod.step_of(value)
        names = {'cool': _('Cool'), 'warm': _('Warm'), 'hot': _('Hot'),
                 'fire': _('Fire')}
        if step is None:
            # Translators: %d is a percent the reader set by hand.
            return _('Custom (%d)') % round(value)
        return names[step]
    if field == 'freshness':
        hours = max(1, round(value / 60))
        return words.ngettext('%d hour', '%d hours', hours) % hours
    if field == 'base_sort':
        return {'chronological': _('Newest first'),
                'score': _('Most engaging')}.get(value, str(value))
    if field == 'scope':
        return {'home': _('Home'), 'local': _('Local'),
                'federated': _('Federated'), 'list': _('List')
                }.get(value, str(value))
    if isinstance(value, bool):
        return _('On') if value else _('Off')
    if isinstance(value, list):
        return ' '.join('#%s' % tag for tag in value) or _('None')
    return str(value)


def describe(rec: Recommendation, words: Words = ENGLISH) -> Text:
    """The words of a recommendation, in the caller's language."""
    _ = words.gettext
    p = dict(rec.params)
    reasons = {
        'low_share': lambda: _('%(pct)s of the posts got little '
                               'engagement') % p,
        'few_high': lambda: _('Only %(pct)s of the posts got a lot of '
                              'engagement') % p,
        'avg_low': lambda: _('The average engagement is very low '
                             '(%(avg)s)') % p,
        'avg_low_fresh': lambda: _('Posts are so recent that they have '
                                   'little engagement yet'),
        'zero_fresh': lambda: _('Fading older posts gives every post a '
                                'score of zero'),
        'add_fresh': lambda: _('Favoring fresh posts puts newer posts '
                               'first'),
        'heat_pays': lambda: _('Hiding so few leaves only %(pct)s of the '
                               'posts with a lot of engagement') % p,
        'heat_few': lambda: _('Hiding so many leaves only %(count)d '
                              'posts') % p,
        'filters_hide': lambda: _('The filters remove %(pct)s of the '
                                  'posts') % p,
        'aggressive': lambda: _('The settings fade and hide posts '
                                'aggressively'),
        'too_many_tags': lambda: _('Only the first 3 of %(count)d '
                                   'hashtags are searched') % p,
        'wide_scope': lambda: _('The federated timeline with no hashtag '
                                'is very wide'),
        'low_score': lambda: _('The recipe score is low (%(score)s)') % p,
        'similar_heat': lambda: _('%(have)d of %(total)d similar recipes '
                                  'hide the least engaging') % p,
        'similar_fresh': lambda: _('%(have)d of %(total)d similar recipes '
                                   'favor fresh posts') % p,
        'review_similar': lambda: _('Similar recipes may have settings '
                                    'that work better'),
        'validity': lambda: _('Only %(pct)s of the posts meet the '
                              'rules') % p,
    }
    impacts = {
        'quality_a': _('about 30 to 40 percent better average engagement'),
        'quality_b': _('about 20 percent better average engagement'),
        'high_share': _('a larger share of posts with a lot of '
                        'engagement'),
        'time_to_engage': _('posts get time to gather engagement'),
        'more_posts_a': _('about 30 to 50 percent more posts'),
        'more_posts_b': _('about 20 to 30 percent more posts'),
        'relevant': _('more relevant posts'),
        'fewer_requests': _('fewer requests and a faster page'),
        'focus': _('a more focused feed'),
        'fresher': _('newer posts first'),
        'nonzero': _('new posts get scores above zero'),
        'more_posts_keep': _('more posts and the same relevance'),
        'learn': _('learn from similar recipes'),
        'valid': _('more posts that meet your rules'),
        'score_up': _('the score may rise above 0.7'),
    }
    # Translators: the label before a guess of what a change would do.
    label = _('Estimate: %s')
    change = '' if rec.manual else '%s → %s' % (
        value_text(rec.field, rec.current, words),
        value_text(rec.field, rec.suggested, words))
    return Text(field_title(rec.field, words), change,
                reasons.get(rec.reason, lambda: rec.reason)(),
                label % impacts[rec.impact] if rec.impact else '')
