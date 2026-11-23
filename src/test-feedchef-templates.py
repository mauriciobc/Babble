#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's New Recipe templates, moshidon's smart defaults, the modes and
the More Posts <-> Better slider: every template is a recipe the import
accepts, the numbers are moshidon's mapped to Babble's heat steps, nothing
is changed in place. No display, no network."""

import os
import sys
import tempfile

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from src.feedchef import recipeedit, templates  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef.recipe import (default_recipe,  # noqa: E402
                                 recipe_to_dict, validate_for_import)

from feedchefaudit.qualitykit import MARKED, plain, stored  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


TMP = tempfile.mkdtemp(prefix='feedchef-quality-')
reachmod.set_root(os.path.join(TMP, 'reach'))


def fresh_logs(name):
    root = os.path.join(TMP, name)
    logmod.set_root(root)
    return root



words = MARKED

# --- templates, smart defaults, modes and the slider ---------------------

for template in templates.TEMPLATE_IDS:
    made_recipe = templates.new_recipe(template, topic='Cats')
    again = validate_for_import(recipe_to_dict(made_recipe))
    check('template %s is a recipe the import accepts, with its own name '
          'and id' % template,
          recipe_to_dict(again) == recipe_to_dict(made_recipe)
          and made_recipe.meta.name == templates.describe(template)[0]
          and made_recipe.id)
news = templates.new_recipe('recent_news')
viral = templates.new_recipe('viral')
topic = templates.new_recipe('topic', topic='#Cats')
safe = templates.new_recipe('safe_feed')
blank = templates.new_recipe('blank', name='Mine', recipe_id='fixed')
check('Recent News: newest first with a light fade-out of 12 hours, the '
      'federated timeline, #news, no heat',
      news.kitchen.base_sort == 'chronological'
      and recipeedit.current(news, 'freshness') == 720
      and recipeedit.current(news, 'heat') is None
      and news.pantry.scope == 'federated'
      and news.pantry.hashtags_include == ['news'])
check('Viral: most engaging first, Fire, a day-long fade-out, federated',
      viral.kitchen.base_sort == 'score'
      and recipeedit.current(viral, 'heat') == 90
      and recipeedit.current(viral, 'freshness') == 1440
      and viral.pantry.scope == 'federated')
check('Topic: the reader\'s hashtag (written like a hashtag or not), '
      'Warm, and none without one',
      topic.pantry.hashtags_include == ['cats']
      and recipeedit.current(topic, 'heat') == 50
      and templates.new_recipe('topic').pantry.hashtags_include == []
      and templates.new_recipe('topic', topic=' # ').pantry
      .hashtags_include == [])
check('Safe Feed: Home, alt text, no sensitive posts, a minimum length of '
      '20, Warm',
      safe.pantry.scope == 'home'
      and safe.diet.hard_filters.must_have_alt_text
      and safe.diet.hard_filters.no_sensitive_media
      and safe.diet.hard_filters.min_content_length == 20
      and recipeedit.current(safe, 'heat') == 50)
fresh_default = default_recipe('x')
check('Blank is the default recipe (Home, a cap of 3 per person), named '
      'and given the id asked for',
      blank.meta.name == 'Mine' and blank.id == 'fixed'
      and [m.type for m in blank.kitchen.modifiers] == ['diversity']
      and blank.pantry.scope == fresh_default.pantry.scope)
check('the cap per person survives every template',
      all(any(m.type == 'diversity' and m.max_per_cluster == 3
              for m in templates.new_recipe(t).kitchen.modifiers)
          for t in templates.TEMPLATE_IDS))
check('names and lines are translatable, and an unknown template is '
      'refused',
      all(templates.describe(t, words)[0].startswith('<')
          and templates.describe(t, words)[1].startswith('<')
          for t in templates.TEMPLATE_IDS)
      and templates.new_recipe('viral', words=words).meta.name == '<Viral>')
try:
    templates.new_recipe('nope')
    refused = False
except ValueError:
    refused = True
check('unknown template: ValueError', refused)
# smart_defaults, moshidon's numbers.
sd = templates.smart_defaults
check('smart defaults: Home 40 and 8 hours, Local 55 and 12, Federated 70 '
      'and 24, List 45 and 10 (no heat suggested for a list)',
      (sd('home').heat, sd('home').decay_minutes) == (40, 480)
      and (sd('local').heat, sd('local').decay_minutes) == (55, 720)
      and (sd('federated').heat, sd('federated').decay_minutes)
      == (70, 1440)
      and (sd('list').heat, sd('list').decay_minutes) == (45, 600)
      and not sd('list').suggest_heat and sd('home').suggest_heat
      and sd('mystery').heat == 70)
check('a hashtag adds 5 to the heat (85 at most) and 2 hours to the fade-out '
      '(a day at most)',
      sd('home', 2).heat == 50 and sd('home', 2).decay_minutes == 720
      and sd('federated', 3).heat == 85
      and sd('federated', 3).decay_minutes == 1440
      and sd('local', 1).decay_minutes == 840)
check('a newest-first recipe needs no fade-out; the history moves the heat '
      '5 either way, with more than 10 scores, between 40 and 85',
      sd('home', 0, 'chronological').decay_minutes is None
      and sd('local', 0, 'score', [30.0] * 11).heat == 60
      and sd('local', 0, 'score', [1.0] * 11).heat == 50
      and sd('local', 0, 'score', [30.0] * 10).heat == 55
      and sd('federated', 3, 'score', [30.0] * 11).heat == 85
      and sd('home', 0, 'score', [1.0] * 11).heat == 40)
check('heat maps to the nearest step, a tie to the hotter, and down for '
      'the slider',
      [templates.snap_nearest(p) for p in (0, 24, 26, 40, 60, 62, 63, 82,
                                           82.5, 83, 100)]
      == [0, 0, 50, 50, 50, 50, 75, 75, 90, 90, 90]
      and [templates.snap_down(p) for p in (40, 49, 50, 60, 80, 90, 95)]
      == [0, 0, 50, 50, 75, 90, 90])
check('a quality estimate is labelled one, with moshidon\'s model',
      templates.estimate_quality(50)['estimate'] is True
      and abs(templates.estimate_quality(50)['estimated_score']
              - ((100 - 40) / 100 * 0.4 + 0.075 * 0.35 + 0.125)) < 1e-9
      and templates.estimate_quality(None) == templates.estimate_quality(50))
plain_recipe = default_recipe('slider')
plain_recipe.pantry.hashtags_include = ['cats']
plain_recipe.diet.hard_filters.must_have_alt_text = True
before = stored(plain_recipe)
bands = [(0, 'more_posts'), (24.9, 'more_posts'), (25, 'balanced'),
         (75, 'balanced'), (75.1, 'better'), (100, 'better'),
         (-5, 'more_posts'), (400, 'better')]
check('the slider: under 25 More Posts (Cool, newest first), 25 to 75 '
      'Balanced (Warm, most engaging, 12 hours), over 75 Better (Hot, 6 '
      'hours)',
      [templates.slider_preset(p) for p, _n in bands]
      == [{'more_posts': templates.MORE_POSTS, 'balanced': templates.BALANCED,
           'better': templates.BETTER}[n] for _p, n in bands]
      and (templates.MORE_POSTS.heat, templates.MORE_POSTS.base_sort,
           templates.MORE_POSTS.decay_minutes) == (0, 'chronological', None)
      and (templates.BALANCED.heat, templates.BALANCED.base_sort,
           templates.BALANCED.decay_minutes) == (50, 'score', 720)
      and (templates.BETTER.heat, templates.BETTER.base_sort,
           templates.BETTER.decay_minutes) == (75, 'score', 360)
      and (templates.MORE_POSTS.raw_heat, templates.BALANCED.raw_heat,
           templates.BETTER.raw_heat) == (40, 60, 80))
results = {}
for position, name in bands:
    out = templates.with_slider(plain_recipe, position)
    results[name] = out
    validate_for_import(recipe_to_dict(out))
check('the slider changes only heat, sort and fade-out: the cap, the '
      'hashtags and the filters stay, and the input is untouched',
      stored(plain_recipe) == before
      and all(r.pantry.hashtags_include == ['cats']
              and r.diet.hard_filters.must_have_alt_text
              and any(m.type == 'diversity' for m in r.kitchen.modifiers)
              for r in results.values())
      and recipeedit.current(results['better'], 'heat') == 75
      and recipeedit.current(results['more_posts'], 'heat') is None
      and results['more_posts'].kitchen.base_sort == 'chronological'
      and recipeedit.current(results['more_posts'], 'freshness') is None)
check('slider position of a recipe: the middle of its band, or none',
      templates.slider_position(results['more_posts']) == 12.5
      and templates.slider_position(results['balanced']) == 50.0
      and templates.slider_position(results['better']) == 87.5
      and templates.slider_position(plain_recipe) is None)
roundtrip = True
for mode in ('explore', 'focused', 'premium'):
    out = templates.with_mode(plain_recipe, mode)
    roundtrip = roundtrip and templates.mode_of(out) == mode
    validate_for_import(recipe_to_dict(out))
check('Explore, Focused and Premium are the three bands, and a recipe '
      'that has one is in that mode; Custom is the recipe as it is (a copy)',
      roundtrip and templates.mode_of(plain_recipe) == 'custom'
      and stored(templates.with_mode(plain_recipe, 'custom')) == before
      and templates.with_mode(plain_recipe, 'custom') is not plain_recipe
      and stored(plain_recipe) == before
      and templates.with_mode(plain_recipe, 'explore').kitchen.base_sort
      == 'chronological')
try:
    templates.with_mode(plain_recipe, 'turbo')
    refused = False
except ValueError:
    refused = True
check('an unknown mode is refused, and the modes have names',
      refused and set(templates.mode_names()) == set(templates.MODES)
      and all(len(v) == 2 for v in templates.mode_names().values())
      and templates.slider_labels() == ('More Posts', 'Better Posts'))
fresh = templates.with_smart_defaults(default_recipe('ctx'))
check('a new recipe gets its context\'s heat and fade-out, and keeps what '
      'it has',
      recipeedit.current(fresh, 'heat') == 50
      and recipeedit.current(fresh, 'freshness') == 480
      and stored(templates.with_smart_defaults(fresh)) == stored(fresh)
      and recipeedit.current(templates.with_smart_defaults(
          default_recipe('c'), 'best_quality'), 'heat') == 75
      and templates.with_smart_defaults(default_recipe('c'),
                                        'more_posts').kitchen.base_sort
      == 'chronological'
      and recipeedit.current(templates.with_smart_defaults(
          plain(scope='list')), 'heat') is None)
check('recommended hashtags: none for Home and a list, 1 for Local, 2 '
      'for Federated',
      [templates.recommended_hashtags(s) for s in (
          'home', 'list', 'local', 'federated')] == [0, 0, 1, 2])

# --- a recipe with a focus keeps its order -----------------------------------------

with_focus = default_recipe('focus')
with_focus.kitchen.active_focus_keywords = ['cats']
with_focus.kitchen.active_focus_weights = [1.0]
with_focus_hashtags = default_recipe('focus tags')
with_focus_hashtags.kitchen.active_focus_hashtags = ['cats']
before_focus = stored(with_focus)
kept_sort = []
for focused_recipe in (with_focus, with_focus_hashtags):
    for mode in ('explore', 'focused', 'premium'):
        out = templates.with_mode(focused_recipe, mode)
        validate_for_import(recipe_to_dict(out))
        kept_sort.append(out.kitchen.base_sort == 'score')
    kept_sort.append(templates.with_slider(focused_recipe, 5)
                     .kitchen.base_sort == 'score')
check('with a focus (More Like This, keywords or hashtags) the modes and '
      'the slider change the heat and the fade-out and never the sort: the '
      'focus decides the order, and the advice leaves it alone too',
      all(kept_sort) and stored(with_focus) == before_focus
      and recipeedit.current(templates.with_mode(with_focus, 'premium'),
                             'heat') == 75
      and recipeedit.current(templates.with_slider(with_focus, 5),
                             'freshness') is None)
check('and such a recipe is in the mode its heat and fade-out say, '
      'whatever its sort',
      templates.mode_of(templates.with_mode(with_focus, 'premium'))
      == 'premium'
      and templates.mode_of(templates.with_mode(with_focus, 'explore'))
      == 'explore'
      and templates.slider_position(templates.with_slider(with_focus, 90))
      == 87.5)
check('a slider position that is not a number is no preset: the recipe '
      'is left as it is (never "Better")',
      all(templates.slider_preset(p) is None for p in (
          float('nan'), float('inf'), -float('inf'), None, 'x', [1]))
      and stored(templates.with_slider(plain_recipe, float('nan')))
      == before
      and templates.with_slider(plain_recipe, float('nan'))
      is not plain_recipe
      and templates.slider_preset(100) is templates.BETTER
      and templates.slider_preset('30') is templates.BALANCED)

sys.exit(1 if failures else 0)
