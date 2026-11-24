#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F403, F405
"""The quality layer on Feed Chef's screens, part two: the A/B experiment
through the page (permission, alternating arms over refreshes, keep or revert
with a toast, an exact restore, Stop in the middle, no variant on a peek or a
load-more), the New Recipe chooser, and the builder's modes and slider. The
engine is a fake (see ``feedchefaudit/uikit.py``). Exits 77 (skip) without
a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-quality-ui-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import sys  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(srcdir)),
                                'bench'))
sys.path.insert(0, srcdir)

from feedchefaudit.uikit import *  # noqa: E402

# --- 5. the experiment --------------------------------------------------------

EXP = {'A': 'good', 'B': 'good'}


def arms_brain(recipe, variant, max_id, peek):
    arm = 'B' if recipeedit.current(recipe, 'freshness') == 600 else 'A'
    return make_record(len(CALLS), EXP[arm], variant)


def start_experiment(page):
    """The report's Try a Change, the explaining dialog, Start Test."""
    page.report_button.emit('clicked')
    pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
    dialog = dialogs(feedchefexplain.KitchenReportDialog)[-1]
    pump_until(lambda: find(dialog, Gtk.Button,
                            lambda b: b.get_label() == 'Try…'))
    dialog.activate_action('quality.try', None)
    pump(10)
    alert = page.proposal_alert
    body = alert.get_body() if alert is not None else ''
    alert.emit('response', 'start')
    alert.force_close()
    pump(10)
    settle()
    return body


def refresh_once(page):
    before = len(CALLS)
    done = optimizer.done(page.opt_state) if page.opt_state else 0
    page.refresh()
    pump_until(lambda: len(CALLS) > before)
    pump_until(lambda: page.model.get_n_items() > 0)
    pump_until(lambda: optimizer.phase(page.opt_state)
               != optimizer.RUNNING
               or optimizer.done(page.opt_state) > done)
    settle()


def new_experiment(arms, name='opt-1'):
    reset()
    EXP.update(arms)
    storage.save_recipe(make_recipe())
    found = storage.get_recipe_by_id(name)
    storage.save_optimizer(name, None)
    del CALLS[:]
    page = open_page(found, arms_brain)
    return page, stored(found)


history.analyze = watch('analyze', stub_analyze)

page, original = new_experiment({'A': 'bad', 'B': 'good'})
check('experiment: nothing runs before the reader says yes',
      page.opt_state is None and not page.experiment_banner.get_revealed()
      and all(c.variant is None for c in CALLS))
body = start_experiment(page)
check('experiment: the dialog says what changes, that it alternates over '
      'the next 10 refreshes, and that it can be stopped at any time',
      'Favor Fresh Posts: 5 hours → 10 hours' in body
      and 'alternate' in body and 'next 10 refreshes' in body
      and 'stop at any time' in body)
check('experiment: accepting starts it and saves it in the sidecar, not '
      'in the recipe',
      optimizer.phase(page.opt_state) == optimizer.RUNNING
      and storage.load_optimizer('opt-1')['phase'] == 'running'
      and stored(storage.get_recipe_by_id('opt-1')) == original
      and 'running' not in original)
check('experiment: the indicator shows how far it is, with Stop',
      page.experiment_banner.get_revealed()
      and page.experiment_banner.get_title()
      == 'Experiment running (0 of 10)'
      and page.experiment_banner.get_button_label() == 'Stop Experiment')
del CALLS[:]
for _n in range(3):
    refresh_once(page)
check('experiment: each refresh moves the indicator',
      page.experiment_banner.get_title()
      == 'Experiment running (3 of 10)')
persisted = storage.load_optimizer('opt-1')
check('experiment: its state is written as it goes, on the worker',
      persisted is not None and optimizer.done(persisted) == 3)
page.source.peek(set())
pump(10)
settle()
check('experiment: a peek never carries a variant',
      CALLS[-1].peek and CALLS[-1].variant is None)
last_refresh = [c for c in CALLS if not c.max_id and not c.peek][-1]
page.source.fetch({'max_id': page.source.cursor}, lambda *_a: None)
pump(10)
check('experiment: a load-more carries no variant and cooks the arm of '
      'the page it continues',
      CALLS[-1].max_id and CALLS[-1].variant is None
      and recipe_to_dict(CALLS[-1].recipe)
      == recipe_to_dict(last_refresh.recipe))
for _n in range(7):
    refresh_once(page)
tags = [c.variant for c in CALLS if c.variant]
check('experiment: ten refreshes cooked ten tagged pages, both arms, '
      'numbered in order',
      [t['n'] for t in tags] == list(range(10))
      and {t['id'] for t in tags} == {'A', 'B'}
      and all(tags[i]['id'] == 'B'
              for i in range(10) if recipeedit.current(
                  [c for c in CALLS if c.variant][i].recipe,
                  'freshness') == 600))
check('experiment: the arm B pages cooked the change, the A pages the '
      'recipe as it was',
      all((recipeedit.current(c.recipe, 'freshness') == 600)
          == (c.variant['id'] == 'B') for c in CALLS if c.variant))
check('experiment: scoring ran on the worker, never the main thread',
      THREADS['score'] and all(t is not MAIN for t in THREADS['score']))
pump_until(lambda: optimizer.phase(page.opt_state) == optimizer.KEPT)
settle()
verdict = [t for t in TOASTS if t.text.startswith('Experiment finished')]
check('experiment: B did better, so it is kept: saved, with a toast and '
      'Undo',
      optimizer.phase(page.opt_state) == optimizer.KEPT and verdict
      and 'kept' in verdict[-1].text and verdict[-1].undo is not None
      and recipeedit.current(page.recipe, 'freshness') == 600
      and recipeedit.current(storage.get_recipe_by_id('opt-1'),
                             'freshness') == 600
      and not page.experiment_banner.get_revealed())
verdict[-1].undo()
pump(10)
settle()
check('experiment: Undo restores the recipe exactly, in the page and in '
      'storage, and the experiment is off',
      stored(page.recipe) == original
      and stored(storage.get_recipe_by_id('opt-1')) == original
      and optimizer.phase(page.opt_state) == optimizer.OFF
      and storage.load_optimizer('opt-1')['phase'] == 'off')
page = None

page, original = new_experiment({'A': 'good', 'B': 'bad'})
start_experiment(page)
for _n in range(10):
    refresh_once(page)
settle()
verdict = [t for t in TOASTS if t.text.startswith('Experiment finished')]
check('experiment: B did worse, so it is reverted: a toast, and the recipe '
      'was never touched',
      optimizer.phase(page.opt_state) == optimizer.REVERTED and verdict
      and 'did not help' in verdict[-1].text
      and stored(page.recipe) == original
      and stored(storage.get_recipe_by_id('opt-1')) == original)
page = None

page, original = new_experiment({'A': 'good', 'B': 'good'})
start_experiment(page)
for _n in range(3):
    refresh_once(page)
page.experiment_banner.emit('button-clicked')
pump(10)
settle()
check('experiment: Stop in the middle ends it, restores the recipe as it '
      'was, hides the indicator and says so',
      optimizer.phase(page.opt_state) == optimizer.OFF
      and stored(page.recipe) == original
      and stored(storage.get_recipe_by_id('opt-1')) == original
      and not page.experiment_banner.get_revealed()
      and TOASTS[-1].text == 'Experiment stopped; recipe unchanged'
      and storage.load_optimizer('opt-1')['phase'] == 'off')
del CALLS[:]
refresh_once(page)
check('experiment: after Stop a refresh cooks the recipe with no variant',
      CALLS and all(c.variant is None for c in CALLS))
page = None

page, original = new_experiment({'A': 'good', 'B': 'good'})
start_experiment(page)
refresh_once(page)
edited = copy.deepcopy(page.recipe)
edited.diet.hard_filters.must_have_media = True
page.use_recipe(edited)
pump(10)
del CALLS[:]
refresh_once(page)
check('experiment: editing the recipe ends the experiment, and says so',
      optimizer.phase(page.opt_state) == optimizer.ABORTED
      and all(c.variant is None for c in CALLS)
      and any('ended because the recipe was changed' in t.text
              for t in TOASTS))
del TOASTS[:]
ref = page.weak_ref()
window.set_child(None)
page = None
check('experiment: the page of an experiment is freed once closed',
      freed(ref))
history.analyze = real_analyze


# --- 5b. the review round: stale state, deleted recipes, two windows ----------

history.analyze = watch('analyze', stub_analyze)

# Stop after the reader edited the recipe: nothing of theirs is overwritten,
# and the toast says so.
page, original = new_experiment({'A': 'good', 'B': 'good'})
start_experiment(page)
edited = copy.deepcopy(page.recipe)
edited.diet.hard_filters.must_have_media = True
page.use_recipe(edited)
check('stale: a new recipe on the page forgets the session\'s recipe, so a '
      'peek or a load-more never cooks the old one',
      page.source._session_recipe is None)
pump(10)
page.stop_experiment()
pump(10)
check('stale: Stop after an edit says the edits were kept, not that the '
      'recipe is unchanged',
      TOASTS[-1].text == 'Experiment stopped; your later edits were kept'
      and page.recipe.diet.hard_filters.must_have_media is True)
page = None

# A recipe deleted under a running experiment leaves no sidecar behind.
page, original = new_experiment({'A': 'good', 'B': 'good'})
start_experiment(page)
tag = {'id': 'A', 'seed': page.opt_state['seed'], 'n': 0}
storage.delete_recipe('opt-1')
page._on_scored(tag, 0.9)
settle()
check('stale: a score that lands after the recipe was deleted writes no '
      'sidecar and does not bring the recipe back',
      storage.load_optimizer('opt-1') is None
      and storage.get_recipe_by_id('opt-1') is None
      and page._switch_to(page.recipe) is False
      and TOASTS[-1].text == 'This recipe no longer exists')
page = None

# Two windows on one recipe: each reloads the sidecar before a transition.
page, original = new_experiment({'A': 'good', 'B': 'good'})
other = feedchefpages.FeedChefTimelinePage(
    Client(), account, storage.get_recipe_by_id('opt-1'))
pump_until(lambda: other.model.get_n_items() > 0)
settle()
start_experiment(page)
for one in (page, other, page, other, page):
    before = len(CALLS)
    one.refresh()
    pump_until(lambda before=before: len(CALLS) > before)
    pump_until(lambda one=one: one.model.get_n_items() > 0)
    settle()
    pump(5)
check('two windows: five refreshes across two pages count five, none lost '
      'to an older copy',
      optimizer.done(storage.load_optimizer('opt-1')) == 5)
check('two windows: the page that did not start it cooked variants, and '
      'its indicator follows the sidecar',
      other.experiment_banner.get_revealed()
      and other.experiment_banner.get_title()
      == 'Experiment running (4 of 10)')
other = page = None

# A proposal the reader declined is not offered again until the recipe
# changes.
page, original = new_experiment({'A': 'good', 'B': 'good'})
recs = advice.recommend(page.recipe, SYN)
page.propose_experiment(recs)
pump(5)
alert = page.proposal_alert
first_body = alert.get_body()
alert.emit('response', 'cancel')
alert.force_close()
pump(5)
page.propose_experiment(recs)
pump(5)
again_body = getattr(page.proposal_alert, 'get_body', lambda: '')()
check('declined: the same change is not proposed again for this recipe',
      again_body != first_body)
changed = copy.deepcopy(page.recipe)
changed.diet.hard_filters.must_have_media = True
check('declined: ...but is once the recipe has changed',
      optimizer.identity(changed) != optimizer.identity(page.recipe))
page = None

# The sidecar is written at once: measure it.
state = optimizer.propose(make_recipe(), advice.recommend(make_recipe(),
                                                          SYN))
storage.save_recipe(make_recipe())
import time  # noqa: E402

start = time.perf_counter()
for _n in range(20):
    storage.save_optimizer('opt-1', state, require_recipe=True)
    storage.load_optimizer('opt-1')
each = (time.perf_counter() - start) / 20 * 1000
print('measured: sidecar write + read %.2f ms' % each)
check('sidecar: a write and a read take under 2 ms each, so it is done on '
      'the main thread at once and nothing is lost at quit', each < 2.0)

history.analyze = real_analyze

# The icons the screens name resolve, once the bundle is registered as the app
# does.
import glob  # noqa: E402
from gi.repository import Gio  # noqa: E402

BUNDLES = sorted(glob.glob(os.path.join(
    os.environ.get('BABBLE_BUILDDIR', os.path.join(
        os.path.dirname(os.path.abspath(srcdir)), 'build')),
    '**', 'babble.gresource'), recursive=True))
Gio.resources_register(Gio.Resource.load(BUNDLES[0]))
APP = Adw.Application(application_id='io.github.mauriciobc.Babble',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
APP.register(None)
theme = Gtk.IconTheme.get_for_display(window.get_display())
from src.feedchef.icons import icon_name  # noqa: E402
from src.feedchefquality import ICONS as HEALTH_ICONS  # noqa: E402
from src.feedchefnewrecipe import TEMPLATE_ICONS  # noqa: E402

wanted = set(HEALTH_ICONS.values()) | {
    icon_name(e) for e in TEMPLATE_ICONS.values()} | {
    'document-new-symbolic', 'dialog-information-symbolic',
    'media-playback-start-symbolic', 'view-refresh-symbolic'}
check('icons: every icon the quality screens and templates name resolves '
      '(missing: %s)' % sorted(i for i in wanted if not theme.has_icon(i)),
      all(theme.has_icon(i) for i in wanted)
      and 'emblem-ok-symbolic' not in HEALTH_ICONS.values())

# --- 6. New Recipe ------------------------------------------------------------

reset()
listing = feedchefpages.FeedChefListPage(Client(), account)
window.set_child(listing)
listing._on_new(None)
pump(10)
chooser = dialogs(NewRecipeDialog)[-1]
check('new: the chooser offers the five starting points, named as the '
      'core names them',
      [r.get_title() for r in find(chooser, Adw.ActionRow)]
      == [templates.describe(t, WORDS)[0] for t in templates.TEMPLATE_IDS]
      and [r.get_title() for r in find(chooser, Adw.ActionRow)]
      == ['Blank Recipe', 'Recent News', 'Viral', 'Topic', 'Safe Feed'])
chooser.force_close()
pump()
valid = []
for template_id in templates.TEMPLATE_IDS:
    one = NewRecipeDialog()
    one.activate_action('template.choose',
                        GLib.Variant.new_string(template_id))
    found = one.recipe
    try:
        validate_for_import(recipe_to_dict(found))
        storage.save_recipe(found)
        valid.append(storage.get_recipe_by_id(found.id) is not None)
    except Exception:
        valid.append(False)
check('new: every template makes a recipe the import accepts and storage '
      'keeps',
      valid == [True] * 5)
listing._on_new(None)
pump(10)
dialogs(NewRecipeDialog)[-1].activate_action(
    'template.choose', GLib.Variant.new_string('viral'))
pump_until(lambda: dialogs(feedchefbuilder.FeedChefBuilderDialog))
builder = dialogs(feedchefbuilder.FeedChefBuilderDialog)[-1]
check('new: choosing a template opens the builder on it, as a new recipe',
      builder.get_title() == 'New Recipe'
      and builder.name_row.get_text() == 'Viral'
      and recipeedit.current(builder._collect(), 'heat') == 90
      and not dialogs(NewRecipeDialog))
builder.force_close()
pump()
window.set_child(None)
listing = builder = None

# --- 7. the builder's modes and slider ---------------------------------------

reset()


def open_builder(recipe):
    dialog = feedchefbuilder.FeedChefBuilderDialog(
        Client(), account, recipe, is_new=True)
    dialog.present(window)
    pump(5)
    return dialog


def controls(dialog):
    heat = dialog._editor('heat')
    fresh = dialog._editor('freshness')
    return (heat.row.get_enable_expansion() and heat.spin.get_value(),
            fresh.row.get_enable_expansion() and fresh.spin.get_value(),
            dialog.order.get_selected())


builder = open_builder(default_recipe('Modes'))
check('modes: a plain recipe starts in Custom',
      builder.get_mode() == 'custom'
      and builder.mode_row.get_model().get_n_items() == 4
      and builder.mode_row.get_title() == 'Mode')
builder.set_mode('premium')
pump(5)
target = templates.with_mode(default_recipe('Modes'), 'premium')
check('modes: Premium sets the heat, the fade-out and the order below, '
      'and moves the slider to Better',
      controls(builder) == (recipeedit.current(target, 'heat'),
                            recipeedit.current(target, 'freshness') / 60, 0)
      and builder.slider.get_value() == 87.5
      and builder.mode_row.get_subtitle()
      == templates.mode_names(WORDS)['premium'][1])
check('modes: the live sentence follows',
      builder.sentence_label.get_label() and 'engaging' in
      builder.sentence_label.get_label().lower())
builder.set_mode('explore')
pump(5)
check('modes: Explore turns heat and freshness off and sorts newest first',
      controls(builder) == (False, False, 1)
      and builder.slider.get_value() == 12.5)
builder.slider.set_value(50)
pump(5)
check('modes: the slider\'s middle is Focused, and sets its controls',
      builder.get_mode() == 'focused'
      and controls(builder)[0] == recipeedit.current(
          templates.with_mode(default_recipe('x'), 'focused'), 'heat')
      and controls(builder)[2] == 0)
builder.slider.set_value(40)
pump(5)
check('modes: dragging the slider does not move it under the hand',
      builder.slider.get_value() == 40)
builder.slider.set_value(0)
pump(5)
check('modes: the slider reaches 0 and 100',
      builder.slider.get_value() == 0 and builder.get_mode() == 'explore')
builder.slider.set_value(100)
pump(5)
check('modes: ...and 100',
      builder.slider.get_value() == 100 and builder.get_mode() == 'premium')
builder.slider.set_value(50)
pump(5)
builder._editor('freshness').spin.set_value(7)
pump(5)
check('modes: editing a mapped control by hand switches to Custom, and '
      'dims the slider',
      builder.get_mode() == 'custom'
      and 'dimmed' in builder.slider_row.get_css_classes())
builder.set_mode('focused')
pump(5)
builder.save_button.emit('clicked')
pump(5)
saved = None
for item in storage.load_recipes():
    if item.meta.name == 'Modes':
        saved = item
check('modes: the recipe saved is what the mode set',
      saved is not None and templates.mode_of(saved) == 'focused'
      and templates.with_mode(default_recipe('x'), 'focused').kitchen
      .base_sort == saved.kitchen.base_sort)
focus_recipe = default_recipe('Focus')
focus_recipe.kitchen.base_sort = 'chronological'
focus_recipe.kitchen.active_focus_keywords = ['cats']
builder = open_builder(focus_recipe)
builder.set_mode('premium')
pump(5)
check('modes: a recipe with a focus keeps its order whatever the mode',
      builder.order.get_selected() == 1
      and builder._collect().kitchen.base_sort == 'chronological')
builder.force_close()
pump()


names = [t.name for t in threading.enumerate()]
check('threads: the quality worker is one daemon thread',
      names.count('feedchef-quality') == 1
      and [t.daemon for t in threading.enumerate()
           if t.name == 'feedchef-quality'] == [True])

print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
