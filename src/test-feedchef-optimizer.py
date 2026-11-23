#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's A/B optimizer: the state machine (permission, seeded Thompson
sampling, keep or revert, exact restore of the change that was made, a
focus that fades during the experiment kept), its sidecar (private, apart
from the recipes and the share link) and a real experiment through the engine,
where every cook records the variant it cooked. No display, no network."""

import copy
import json
import os
import random
import stat
import sys
import threading
import tempfile

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from src.feedchef import advice, focus, optimizer, quality  # noqa: E402
from src.feedchef import audit as auditmod  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import sharecodec, storage  # noqa: E402
from src.feedchef.recipe import Modifier  # noqa: E402

from feedchefaudit import evaluate as evalmod  # noqa: E402
from feedchefaudit import fuzz  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from feedchefaudit.qualitykit import metrics, plain, stored  # noqa: E402
from feedchefaudit.records import expand, load  # noqa: E402

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


NOW = fuzz.NOW

# --- the A/B optimizer ---------------------------------------------------------------

def make(**kwargs):
    recipe = plain(scope='home', mods=[Modifier(
        type='freshness', decay_minutes=300)], **kwargs)
    recipe.id = 'opt-1'
    return recipe


def proposal(seed=5, trials=10, recipe=None):
    recipe = recipe or make()
    found = advice.recommend(recipe, metrics(total=20, avg=1, median=1))
    return recipe, optimizer.propose(recipe, found, seed=seed,
                                     trials=trials)


def run_experiment(recipe, state, rewards):
    """Cook until the experiment ends. *rewards* is the recipe score of the
    arm: {'A': a, 'B': b}. Returns the final state and the plans."""
    plans = []
    guard = 0
    while optimizer.phase(state) == 'running' and guard < 60:
        guard += 1
        state, plan = optimizer.next_cook(state, recipe)
        plans.append(plan)
        state = optimizer.record_result(state, plan.tag, rewards[plan.arm])
    return state, plans


recipe, state = proposal()
original = stored(recipe)
check('a proposal is a state with the change (edits and undo, not whole '
      'recipes) and nothing runs',
      optimizer.phase(state) == 'proposed'
      and state['change']['field'] == 'freshness'
      and state['edits'] == [['freshness', 600]]
      and state['undo'] and 'original' not in state
      and 'variant' not in state
      and state['identity'] != state['variant_identity']
      and stored(recipe) == original)
mid, plan = optimizer.next_cook(state, recipe)
check('before the reader says yes every cook is the original, untagged',
      mid is state and plan.tag is None and plan.arm == 'A'
      and plan.recipe is recipe
      and optimizer.accept(optimizer.decline(state))['phase'] == 'declined')
heat_only = plain(scope='home', mods=[Modifier(
    type='heat', percentile_threshold=50)])
found = advice.recommend(heat_only, metrics(total=20, high=10))
check('a change that is only about heat is advice, never an experiment '
      '(it raises the engagement of what is shown by construction); other '
      'changes are testable',
      any(r.field == 'heat' and not r.manual for r in found)
      and not any(optimizer.testable(r) for r in found if r.field == 'heat')
      and optimizer.propose(heat_only, found) is None
      and all(optimizer.testable(r) for r in advice.recommend(
          make(), metrics(total=20, avg=1, median=1))
          if r.field in ('freshness', 'base_sort') and not r.manual))
check('a proposal needs a testable recommendation; none: nothing',
      optimizer.propose(recipe, []) is None
      and optimizer.propose(recipe, [r for r in advice.recommend(
          plain(), metrics(low=90)) if r.manual]) is None)
refused = []
for trials in (0, 3, 51, 10.0, True, None, '10'):
    try:
        optimizer.propose(recipe, advice.recommend(
            recipe, metrics(total=20, avg=1, median=1)), trials=trials)
        refused.append(False)
    except ValueError:
        refused.append(True)
check('fewer than 4 trials (an arm needs 2), more than 50, or not a whole '
      'number are refused; 4 and 50 are not',
      all(refused) and proposal(trials=4)[1]['trials'] == 4
      and proposal(trials=50)[1]['trials'] == 50)
running = optimizer.accept(state)
check('only accept starts the experiment (and accepting twice is the '
      'same)', optimizer.phase(running) == 'running'
      and optimizer.accept(running) == running)
final, plans = run_experiment(recipe, running, {'A': 0.4, 'B': 0.7})
tags = [p.tag for p in plans]
check('10 trials, each arm at least twice, then a verdict: B won every '
      'cook and is kept',
      len(plans) == 10 and optimizer.phase(final) == 'kept'
      and final['arms']['A']['n'] >= 2 and final['arms']['B']['n'] >= 2
      and final['arms']['B']['wins'] == final['arms']['B']['n']
      and final['arms']['A']['wins'] == 0 and final['outcome'] == 'better')
check('every cook says which recipe it cooks, and it is the original or '
      'the proposed one, by hash, with the seed; B is the recipe with the '
      'edits',
      all(t['seed'] == 5 and t['n'] == i for i, t in enumerate(tags))
      and all(auditmod.recipe_hash(p.recipe) == (
          p.tag['original'] if p.arm == 'A' else p.tag['proposed'])
          for p in plans)
      and all(p.recipe is recipe for p in plans if p.arm == 'A')
      and all(p.recipe.kitchen.modifiers[0].decay_minutes == 600
              for p in plans if p.arm == 'B')
      and stored(recipe) == original)
kept = optimizer.result_recipe(final, recipe)
check('the kept recipe is the current one with the edits (the one to '
      'save), and turning it off gives the original back exactly',
      kept.kitchen.modifiers[0].decay_minutes == 600
      and stored(optimizer.restore(optimizer.turn_off(final), kept))
      == original and optimizer.result_recipe(running, recipe) is None)
reverted, _p = run_experiment(recipe, running, {'A': 0.7, 'B': 0.4})
tied, _p = run_experiment(recipe, running, {'A': 0.6, 'B': 0.6})
never, _p = run_experiment(recipe, running, {'A': 0.1, 'B': 0.1})
check('B worse, a tie (both at the 0.6 reward) and nobody winning are all '
      'reverted: the recipe is never changed without evidence',
      optimizer.phase(reverted) == 'reverted'
      and optimizer.phase(tied) == 'reverted'
      and optimizer.phase(never) == 'reverted'
      and optimizer.result_recipe(reverted, recipe) is None
      and optimizer.REWARD_AT == 0.6
      and optimizer.reward(0.6) and not optimizer.reward(0.599))
first, plans_a = run_experiment(recipe, running, {'A': 0.5, 'B': 0.65})
again, plans_b = run_experiment(recipe, running, {'A': 0.5, 'B': 0.65})
check('the same state and the same results give the same arms, the same '
      'log and the same verdict (deterministic, no hidden state)',
      [p.arm for p in plans_a] == [p.arm for p in plans_b]
      and first == again)
other = [''.join(p.arm for p in run_experiment(
    recipe, optimizer.accept(proposal(seed=s)[1]), {'A': 0.5, 'B': 0.65}
)[1]) for s in range(12)]
check('another seed draws another sequence (%d different of 12)'
      % len(set(other)), len(set(other)) > 3)
every = []
for trials in (4, 5, 7, 10, 50):
    end, made = run_experiment(recipe, optimizer.accept(proposal(
        seed=trials, trials=trials)[1]), {'A': 0.5, 'B': 0.65})
    every.append(len(made) == trials and end['arms']['A']['n'] >= 2
                 and end['arms']['B']['n'] >= 2)
check('every allowed number of trials ends with both arms cooked twice',
      all(every))
lean = {**running, 'trials': 50, 'arms': {
    'A': {'n': 30, 'wins': 1}, 'B': {'n': 30, 'wins': 29}},
    'log': [{}] * 60}
back = {**lean, 'arms': {'A': {'n': 30, 'wins': 29},
                         'B': {'n': 30, 'wins': 1}}}
lean['trials'] = back['trials'] = 1000
picks = [optimizer.choose({**lean, 'seed': s}) for s in range(100)]
picks_back = [optimizer.choose({**back, 'seed': s}) for s in range(100)]
check('Thompson sampling prefers the arm that wins (%d and %d of 100)'
      % (picks.count('B'), picks_back.count('A')),
      picks.count('B') >= 97 and picks_back.count('A') >= 97
      and len(set(optimizer.choose({
          **running, 'seed': s, 'trials': 1000, 'log': [{}] * 4,
          'arms': {'A': {'n': 2, 'wins': 1}, 'B': {'n': 2, 'wins': 1}}})
          for s in range(40))) == 2)
mid, plan = optimizer.next_cook(running, recipe)
check('a result with a stale tag, no tag or another seed is ignored; so is '
      'one after the experiment ended',
      optimizer.record_result(running, {**plan.tag, 'n': 3}, 0.9) == running
      and optimizer.record_result(running, None, 0.9) == running
      and optimizer.record_result(running, {**plan.tag, 'seed': 99}, 0.9)
      == running
      and optimizer.record_result(final, plans[0].tag, 0.9) == final
      and optimizer.record_result(running, plan.tag, 0.9)['log'] != [])
check('a score that is not a finite number is no result (the trial stays '
      'open, nothing is stored)',
      all(optimizer.record_result(running, plan.tag, bad) == running
          for bad in (float('nan'), float('inf'), -float('inf'), None, 'x',
                      True, [0.7]))
      and 'NaN' not in optimizer.dumps(optimizer.record_result(
          running, plan.tag, float('nan'))))

# --- a recipe that changes by itself during the experiment -----------------------

faded = make()
faded.kitchen.active_focus_keywords = ['cats', 'dogs']
faded.kitchen.active_focus_weights = [1.0, 1.0]
faded.kitchen.focus_decay = 0.9
faded.kitchen.active_focus_strength = 1.0
faded_recipe, faded_state = proposal(recipe=faded)
original_faded = stored(faded_recipe)
state_f = optimizer.accept(faded_state)
current = copy.deepcopy(faded_recipe)
aborted = False
focus_kept = True
cooked = []
for _i in range(10):
    state_f, plan = optimizer.next_cook(state_f, current)
    if optimizer.phase(state_f) != 'running':
        aborted = True
        break
    cooked.append(plan)
    # B is the recipe as it is now, with the change: the faded focus is in
    # both arms.
    focus_kept = focus_kept and (
        plan.recipe.kitchen.active_focus_strength
        == current.kitchen.active_focus_strength
        and plan.recipe.kitchen.active_focus_weights
        == current.kitchen.active_focus_weights)
    state_f = optimizer.record_result(state_f, plan.tag,
                                      0.7 if plan.arm == 'B' else 0.3)
    focus.decay_focus(current.kitchen)       # the refresh fades the focus
check('a focus that fades on every refresh does not end the experiment, '
      'and both arms cook the recipe as it is now (the strength went from '
      '1.0 to %.3f)' % current.kitchen.active_focus_strength,
      not aborted and focus_kept and len(cooked) == 10
      and optimizer.phase(state_f) == 'kept'
      and current.kitchen.active_focus_strength < 0.4
      and len({p.tag['original'] for p in cooked}) > 5)
saved = optimizer.result_recipe(state_f, current)
check('the kept recipe has the change and the focus as it is now (not '
      'the focus of the day the experiment began)',
      saved.kitchen.modifiers[0].decay_minutes == 600
      and saved.kitchen.active_focus_strength
      == current.kitchen.active_focus_strength
      and saved.kitchen.active_focus_keywords
      == current.kitchen.active_focus_keywords)
undone = optimizer.restore(state_f, saved)
check('undoing it puts the fade-out back and leaves the faded focus alone',
      undone.kitchen.modifiers[0].decay_minutes == 300
      and undone.kitchen.active_focus_strength
      == current.kitchen.active_focus_strength
      and stored(undone) == stored(current))
gone = copy.deepcopy(current)
focus.decay_focus(gone.kitchen)
gone.kitchen.active_focus_keywords = None
gone.kitchen.active_focus_weights = None
check('a focus that has faded away entirely is still the same experiment',
      optimizer.restore(state_f, gone) is not None
      and optimizer.next_cook(optimizer.accept(proposal(recipe=faded)[1]),
                              gone)[0]['phase'] == 'running')
renamed = copy.deepcopy(recipe)
renamed.meta.name = 'Renamed by the reader'
state_r, plan_r = optimizer.next_cook(running, renamed)
check('renaming the recipe is not an edit that ends the experiment',
      optimizer.phase(state_r) == 'running' and plan_r.tag is not None
      and plan_r.tag['original'] == auditmod.recipe_hash(renamed))
edited = copy.deepcopy(recipe)
edited.pantry.scope = 'local'
state_e, plan_e = optimizer.next_cook(running, edited)
check('changing anything else during the experiment ends it (aborted), '
      'cooks the edited recipe untagged, and restore refuses to overwrite '
      'it',
      optimizer.phase(state_e) == 'aborted' and plan_e.tag is None
      and plan_e.recipe is edited and state_e['outcome'] == 'edited'
      and optimizer.restore(running, edited) is None
      and optimizer.restore(final, edited) is None
      and optimizer.result_recipe(final, edited) is None)
off = optimizer.turn_off(running)
check('turning it off, in any phase, ends it and restores the original '
      'exactly; with nothing running there is nothing to restore',
      optimizer.phase(off) == 'off'
      and stored(optimizer.restore(off, recipe)) == original
      and optimizer.turn_off(None) is None
      and optimizer.phase(optimizer.turn_off(optimizer.turn_off(off)))
      == 'off' and optimizer.restore(None, recipe) is None
      and optimizer.phase(optimizer.turn_off(state)) == 'off')
partial = running
for _i in range(4):
    partial, plan = optimizer.next_cook(partial, recipe)
    partial = optimizer.record_result(partial, plan.tag, 0.65)
partial = optimizer.turn_off(partial)
mid, plan = optimizer.next_cook(partial, recipe)
check('switched off after 4 trials, the next cook is the original recipe, '
      'untagged, and the original is restored exactly',
      plan.tag is None and plan.recipe is recipe
      and stored(optimizer.restore(partial, recipe)) == original
      and len(partial['log']) == 4)

# --- the sidecar -------------------------------------------------------------------

text = optimizer.dumps(final)
check('the state round-trips as text, and survives a restart (the same next '
      'cook after a reload)',
      optimizer.loads(text) == final
      and optimizer.next_cook(optimizer.loads(optimizer.dumps(running)),
                              recipe)[1].arm
      == optimizer.next_cook(running, recipe)[1].arm)


def broken(**changes):
    return json.dumps({**json.loads(text), **changes})


bad_log = json.loads(text)
bad_log['log'] = bad_log['log'][:3]
bad_wins = json.loads(text)
bad_wins['arms']['A']['wins'] = 99
bad_edit = json.loads(text)
bad_edit['edits'] = [['not_a_field', 1]]
bad_undo = json.loads(text)
bad_undo['undo'] = [['heat']]
check('a sidecar that is not an experiment is none: corrupt text, another '
      'version or phase, a seed or trials that are not whole numbers, '
      'trials out of 4 to 50, a change that is not a record with a field, '
      'an outcome that is not text, edits that are not edits, identities '
      'that are not hashes, a log that does not add up, wins over trials',
      all(optimizer.loads(t) is None for t in (
          '', 'nope', '[]', '{}', 'null', broken(v=9), broken(phase='x'),
          broken(seed='x'), broken(seed=True), broken(trials=3),
          broken(trials=51), broken(trials=10.5), broken(trials=0),
          broken(change=None), broken(change=[]), broken(change={}),
          broken(change={'field': 'nonsense'}), broken(outcome=5),
          broken(outcome=['x']), broken(edits=[]), broken(identity='ab'),
          broken(variant_identity=None), broken(recipe_id=7),
          json.dumps(bad_log), json.dumps(bad_wins), json.dumps(bad_edit),
          json.dumps(bad_undo), broken(trials=4)))
      and optimizer.loads(broken(outcome=None)) is not None
      and optimizer.loads(broken(trials=50)) is not None)
check('the summary is counts and rates only',
      set(optimizer.summary(final)) == {
          'phase', 'trials', 'done', 'left', 'A', 'B', 'outcome', 'field'}
      and optimizer.summary(final)['left'] == 0
      and optimizer.summary(running)['left'] == 10)
storage.set_root(os.path.join(TMP, 'recipes'))
store = storage.FeedRecipeStorage('https://x.example#1')
store.save_recipe(recipe)
recipes_path = storage._path('https://x.example#1')
with open(recipes_path, 'rb') as handle:
    recipes_before = handle.read()
link_before = sharecodec.recipe_share_uri(recipe)
os.umask(0o000)         # a loose umask: the sidecar must still be private
store.save_optimizer(recipe.id, running)
with open(recipes_path, 'rb') as handle:
    recipes_after = handle.read()
side = storage._optimizer_path('https://x.example#1')
mode = stat.S_IMODE(os.stat(side).st_mode)
check('the experiment is kept in a sidecar, private to the user even with '
      'a loose umask, apart from the recipes: their file and the share '
      'link do not change, and neither carries the seed',
      store.load_optimizer(recipe.id) == running
      and recipes_before == recipes_after and mode == 0o600
      and stat.S_IMODE(os.stat(os.path.dirname(side)).st_mode) == 0o700
      and sharecodec.recipe_share_uri(recipe) == link_before
      and '"seed"' not in recipes_after.decode()
      and not os.path.exists(side + '.tmp')
      and store.load_optimizer('no-such') is None)
store.save_optimizer(recipe.id, None)
check('forgetting it, and deleting a recipe, remove it',
      store.load_optimizer(recipe.id) is None
      and (store.save_optimizer(recipe.id, running) or True)
      and store.delete_recipe(recipe.id)
      and store.load_optimizer(recipe.id) is None)
with open(side, 'w', encoding='utf-8') as handle:
    handle.write('not json')
check('an unreadable sidecar is no experiment, never an error',
      store.load_optimizer(recipe.id) is None)
with open(side, 'w', encoding='utf-8') as handle:
    json.dump({recipe.id: {**running, 'trials': 2}}, handle)
check('a stored experiment that does not validate is none',
      store.load_optimizer(recipe.id) is None)

# --- through the engine: what counts as a trial ------------------------------------

fresh_logs('variant')
ab_recipe = make()
ab_recipe.id = 'ab'
state = optimizer.accept(proposal(seed=3, recipe=ab_recipe)[1])
seen_arms = set()
variant_ok = True
scored = []
for index in range(10):
    state, plan = optimizer.next_cook(state, ab_recipe)
    seen_arms.add(plan.arm)
    pages = fuzz.cook(fuzz.World(random.Random(index), plan.recipe),
                      plan.recipe, 700 + index, pages=2, now=NOW,
                      variant=plan.tag)
    records = [p.logger.record for p in pages]
    variant_ok = variant_ok and (
        records[0]['variant'] == plan.tag
        and records[0]['recipe']['hash'] == (
            plan.tag['original'] if plan.arm == 'A' else plan.tag['proposed'])
        and records[0]['variant']['seed'] == 3
        # A page loaded further down is not a trial.
        and all('variant' not in r for r in records[1:]))
    score = optimizer.score_of(records, plan.tag)
    scored.append(score == quality.cook_summary(records[:1])['score'])
    state = optimizer.record_result(state, plan.tag, 0.7 if plan.arm == 'B'
                                    else 0.3)
check('10 real cooks of an experiment: the first page of each carries its '
      'variant, the seed and the recipe the variant names (both arms %s), '
      'later pages carry none, the reward is the first page\'s score, and '
      'B, which scored better, is kept' % sorted(seen_arms),
      variant_ok and all(scored) and seen_arms == {'A', 'B'}
      and optimizer.phase(state) == 'kept')
tally = evalmod.Tally()
loaded = [load(p) for p in expand([logmod._dir()])]
for pages in evalmod.group_cooks([item for item in loaded
                                  if item.record]).values():
    tally.merge(evalmod.evaluate_cook(pages))
check('and evaluate passes them all, the variant rule checked %d times'
      % tally.checked['variant'], tally.ok() and tally.checked['variant']
      == 10)
# A peek is never a trial: the engine records no variant for it.
world = fuzz.World(random.Random(1), ab_recipe)
engine = FeedEngine(
    account_key='peek#1', self_account_id=fuzz.ME,
    fetch_scope=world.fetch_scope, fetch_hashtag=world.fetch_hashtag,
    search_statuses=world.search, fetch_trends=world.trends,
    fetch_following_page=world.following_page)
engine.search_stagger_ms = 0
engine.clock = lambda: NOW
engine.init_preload()
tag = {'id': 'B', 'seed': 3, 'n': 0, 'original': 'a' * 64,
       'proposed': auditmod.recipe_hash(ab_recipe)}
try:
    peek = engine._process_sync(
        ab_recipe, max_id=None, count=40, on_stage=None,
        cancel=threading.Event(), generation='peek', peek=True,
        variant=tag, cook_id='peekcook', page_index=0)
    shown = engine._process_sync(
        ab_recipe, max_id=None, count=40, on_stage=None,
        cancel=threading.Event(), generation='shown', variant=tag,
        cook_id='shown', page_index=0)
    deeper = engine._process_sync(
        ab_recipe, max_id=None, count=40, on_stage=None,
        cancel=threading.Event(), generation='deeper', variant=tag,
        cook_id='deeper', page_index=1)
finally:
    engine.shutdown()
check('a peek and a page past the first record no variant; the first page '
      'of a refresh does; score_of takes the reward from the page that '
      'does, never from a peek',
      'variant' not in peek.logger.record
      and peek.logger.record['outcome']['status'] == 'peek'
      and shown.logger.record['variant'] == tag
      and 'variant' not in deeper.logger.record
      and optimizer.score_of([peek.logger.record], tag) is None
      and optimizer.score_of([deeper.logger.record], tag) is None
      and optimizer.score_of([shown.logger.record], tag) is not None
      and optimizer.score_of([shown.logger.record], None) is None
      and optimizer.score_of([shown.logger.record], {**tag, 'id': 'A'})
      is None)

sys.exit(1 if failures else 0)
