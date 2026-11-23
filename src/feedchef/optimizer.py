# SPDX-License-Identifier: GPL-3.0-or-later
"""The A/B optimizer: a recipe tries one suggested change on some refreshes,
and keeps it only if it did better (no widgets, standard library only; every
function is pure and the choices are deterministic).

moshidon's plan (``feed_chef_improvements.md``, 5): "after 10 uses, if the
quality is under 0.5, suggest an adjustment, apply it with the reader's
permission and watch; if it improved keep it, else revert". Here:

* **What it measures.** The reward is the recipe score of a cook, which is
  the engagement of the posts *shown* (`quality.py``): not whether the recipe
  is better. A change that only hides more of the least engaging posts
  (a hotter `heat` step) raises that score by construction, so a proposal
  that is only a heat change is never made (`propose` skips it; the change
  stays one-click advice). The experiment compares engagement of shown posts
  between two recipes, and says so.
* **Proposal.** ``propose`` turns one advice recommendation (one that has
  edits) into a state in the phase ``proposed``. The state holds the *edits*
  and the *undo* of the change, not whole recipes: variant B is built at cook
  time by applying the edits to the recipe the reader has *now*, so what a
  refresh changes by itself (a fading focus, More Like This) is never frozen
  or lost. Nothing runs until the reader accepts (``accept``).
* **Identity.** The experiment belongs to a recipe by its ``identity``: the
  recipe's hash without the fields a refresh rewrites (the focus: keywords,
  weights, hashtags, strength) and without its name. Changing anything else
  aborts it.
* **Trials.** While ``running``, each refresh asks ``next_cook`` which recipe to
  cook, A or B, by Thompson sampling (a Beta(1 + wins, 1 + losses) draw per
  arm, seeded from the recorded seed and the number of cooks so far, so the
  choice is a pure function of the state and survives a restart). Each arm is
  guaranteed ``MIN_ARM`` cooks within the ``trials`` (at least 4, at most 50;
  10 by default). The reward of a cook is its recipe score at least
  ``REWARD_AT`` (0.6). Only the first page of a refresh cook counts
  (``score_of``): a peek or a load-more never consumes a trial (the engine
  records no variant for them).
* **Verdict.** After ``trials`` cooks B is kept when its reward rate is
  *strictly* higher than A's, else reverted (a tie keeps what the reader
  had). ``result_recipe`` is the current recipe with the edits applied.
* **Off.** ``turn_off`` ends the experiment in any phase. ``restore`` puts the
  change back (the undo, applied to the current recipe) when the recipe is
  the original or the kept variant, and refuses when the reader has edited
  it since.
* **Cooks.** The recipe a variant cook cooks is A or B and the audit record
  says which (``cook_tag`` goes to ``process_feed(variant=...)``): the
  ``variant`` rule of the audit's ``evaluate`` checks that the record's recipe
  is the one the variant names.

**Where the state lives.** In a sidecar beside the recipes
(``storage.FeedRecipeStorage.load_optimizer``), never in the recipe: a share
link is made from the recipe alone, so the state cannot leak into one, and a
lost or corrupt sidecar only ends an experiment (``validate`` returns None).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from typing import Any, NamedTuple

from . import quality, recipeedit
from .audit import recipe_hash
from .recipe import FeedRecipe, recipe_to_dict

VERSION = 2
# Cooks of an experiment (moshidon: "after 10 uses"), and the range a
# stored experiment may have: each arm needs MIN_ARM, and a long one is a
# state nobody made.
TRIALS = 10
MIN_TRIALS, MAX_TRIALS = 4, 50
# A cook is a win when its recipe score reaches this.
REWARD_AT = 0.6
# Each arm cooks at least this many times before the verdict.
MIN_ARM = 2

PROPOSED = 'proposed'
RUNNING = 'running'
KEPT = 'kept'
REVERTED = 'reverted'
DECLINED = 'declined'
OFF = 'off'
ABORTED = 'aborted'
PHASES = (PROPOSED, RUNNING, KEPT, REVERTED, DECLINED, OFF, ABORTED)
ENDED = (KEPT, REVERTED, DECLINED, OFF, ABORTED)
# What a refresh rewrites by itself, and a name: not part of whose
# experiment it is.
_VOLATILE = ('active_focus_keywords', 'active_focus_weights',
             'active_focus_hashtags', 'active_focus_strength')


class Plan(NamedTuple):
    arm: str                       # 'A' or 'B'
    recipe: FeedRecipe             # what to cook
    tag: dict[str, Any] | None     # for process_feed(variant=): None: no test


def identity(recipe: FeedRecipe) -> str:
    """sha256 of the recipe without its name and the focus a refresh
    rewrites: what makes an experiment belong to a recipe."""
    data = recipe_to_dict(recipe)
    data['meta'].pop('name', None)
    for key in _VOLATILE:
        data['kitchen'].pop(key, None)
    text = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def seed_of(recipe: FeedRecipe, recommendation_id: str) -> int:
    """The default seed of an experiment: 31 bits of a hash of the recipe
    and the change, so the same proposal draws the same arms."""
    digest = hashlib.sha256(('%s|%s' % (identity(recipe),
                                        recommendation_id)).encode('utf-8'))
    return int(digest.hexdigest()[:8], 16) & 0x7FFFFFFF


def testable(rec) -> bool:
    """Whether the optimizer may try a recommendation: it has edits, and it
    is not only a heat change (hiding more of the least engaging raises the
    score of what is shown by construction, so the test could not say
    anything)."""
    return bool(rec.edits) and not rec.manual and any(
        edit.field != 'heat' for edit in rec.edits)


def propose(recipe: FeedRecipe, recommendations, *, seed: int | None = None,
            trials: int = TRIALS) -> dict[str, Any] | None:
    """The state that proposes the first recommendation (the list is most
    severe first) the optimizer can test, or None. Nothing is applied.
    *trials* is between ``MIN_TRIALS`` and ``MAX_TRIALS`` (ValueError
    otherwise: each arm needs its cooks)."""
    if isinstance(trials, bool) or not isinstance(trials, int) \
            or not MIN_TRIALS <= trials <= MAX_TRIALS:
        raise ValueError('trials must be a whole number from %d to %d'
                         % (MIN_TRIALS, MAX_TRIALS))
    for rec in recommendations:
        if not testable(rec):
            continue
        try:
            done = recipeedit.apply_edits(recipe, rec.edits)
        except ValueError:
            continue
        if recipe_to_dict(done.recipe) == recipe_to_dict(recipe):
            continue
        return {
            'v': VERSION, 'phase': PROPOSED, 'recipe_id': recipe.id,
            'seed': seed_of(recipe, rec.id) if seed is None else int(seed),
            'trials': trials,
            'change': {'id': rec.id, 'field': rec.field,
                       'current': rec.current, 'suggested': rec.suggested},
            'edits': recipeedit.edits_to_json(rec.edits),
            'undo': recipeedit.edits_to_json(done.undo),
            'identity': identity(recipe),
            'variant_identity': identity(done.recipe),
            'arms': {'A': {'n': 0, 'wins': 0}, 'B': {'n': 0, 'wins': 0}},
            'log': [], 'outcome': None}
    return None


def phase(state: dict[str, Any] | None) -> str:
    return OFF if not state else state['phase']


def accept(state: dict[str, Any]) -> dict[str, Any]:
    """The reader's permission: a proposal starts running. Any other phase
    is returned as it is (no permission is ever implied)."""
    if state['phase'] != PROPOSED:
        return state
    return {**state, 'phase': RUNNING}


def decline(state: dict[str, Any]) -> dict[str, Any]:
    """The reader said no: the state is kept (so the same change is not
    proposed again) and nothing ever runs."""
    if state['phase'] != PROPOSED:
        return state
    return {**state, 'phase': DECLINED}


def done(state: dict[str, Any]) -> int:
    return len(state['log'])


def _deficit(state: dict[str, Any], arm: str) -> int:
    return max(0, MIN_ARM - state['arms'][arm]['n'])


def choose(state: dict[str, Any]) -> str:
    """The arm of the next cook: forced when the cooks left are only enough
    to give each arm its ``MIN_ARM``, else a seeded Thompson draw. A pure
    function of the state."""
    left = state['trials'] - done(state)
    if left <= _deficit(state, 'A') + _deficit(state, 'B'):
        return 'A' if _deficit(state, 'A') >= _deficit(state, 'B') else 'B'
    rng = random.Random('%d:%d' % (state['seed'], done(state)))
    draws = {}
    for arm in ('A', 'B'):
        stats = state['arms'][arm]
        draws[arm] = rng.betavariate(1 + stats['wins'],
                                     1 + stats['n'] - stats['wins'])
    return 'B' if draws['B'] > draws['A'] else 'A'


def variant_of(state: dict[str, Any], recipe: FeedRecipe) -> FeedRecipe:
    """B: *recipe* (the reader's recipe now) with the change made."""
    return recipeedit.apply_edits(
        recipe, recipeedit.edits_from_json(state['edits'])).recipe


def next_cook(state: dict[str, Any] | None, recipe: FeedRecipe
              ) -> tuple[dict[str, Any] | None, Plan]:
    """The state and the plan for a refresh of *recipe* (the one the reader
    has now). Not running: cook *recipe*, untagged. Running but the recipe
    is not the one the experiment started from (its identity changed: the
    reader edited it) or B cannot be made of it: the experiment ends,
    aborted, and the recipe is cooked as it is."""
    if not state or state['phase'] != RUNNING:
        return state, Plan('A', recipe, None)
    try:
        if identity(recipe) != state['identity']:
            raise ValueError('edited')
        variant = variant_of(state, recipe)
    except ValueError as error:
        return ({**state, 'phase': ABORTED, 'outcome': str(error)},
                Plan('A', recipe, None))
    arm = choose(state)
    tag = {'id': arm, 'seed': state['seed'], 'n': done(state),
           'original': recipe_hash(recipe), 'proposed': recipe_hash(variant)}
    return state, Plan(arm, recipe if arm == 'A' else variant, tag)


def reward(score: float) -> bool:
    return score >= REWARD_AT


def score_of(records, tag: dict[str, Any] | None) -> float | None:
    """The reward score of a refresh cook: the recipe score of its first
    page (page 0, hop 0, shown) when that page carries *tag* as its
    variant; None otherwise (a peek, a load-more, a cancelled or failed
    page, another cook's page, a page that shows nothing)."""
    if not tag:
        return None
    for record in records:
        if (record.get('page_index') == 0 and not record.get('hop')
                and (record.get('outcome') or {}).get('status') == 'ok'
                and record.get('variant') == tag):
            found = quality.cook_summary([record])
            return found['score'] if found['pages'] else None
    return None


def record_result(state: dict[str, Any], tag: dict[str, Any] | None,
                  score: float) -> dict[str, Any]:
    """The cook of *tag* finished with the recipe score *score*. A tag that
    is not the next cook of a running experiment (stale: the cook was
    superseded, the experiment ended) and a score that is not a finite
    number change nothing. After the last trial the verdict is made."""
    if isinstance(score, bool) or not isinstance(score, (int, float)) \
            or not math.isfinite(score):
        return state
    if not tag or state['phase'] != RUNNING or tag.get('n') != done(state) \
            or tag.get('seed') != state['seed'] \
            or tag.get('id') not in ('A', 'B'):
        return state
    arm = tag['id']
    win = reward(score)
    arms = copy.deepcopy(state['arms'])
    arms[arm]['n'] += 1
    arms[arm]['wins'] += int(win)
    log = [*state['log'], {'n': done(state), 'arm': arm,
                           'score': round(float(score), 3), 'win': win}]
    found = {**state, 'arms': arms, 'log': log}
    if len(log) >= state['trials']:
        found = verdict(found)
    return found


def rate(state: dict[str, Any], arm: str) -> float:
    stats = state['arms'][arm]
    return stats['wins'] / stats['n'] if stats['n'] else 0.0


def verdict(state: dict[str, Any]) -> dict[str, Any]:
    """Keep B when its reward rate is strictly above A's, else revert."""
    kept = rate(state, 'B') > rate(state, 'A')
    return {**state, 'phase': KEPT if kept else REVERTED,
            'outcome': 'better' if kept else 'not better'}


def result_recipe(state: dict[str, Any], current: FeedRecipe
                  ) -> FeedRecipe | None:
    """The recipe to save once the verdict is ``kept``: *current* with the
    change made (None when the verdict is not kept, or the change cannot be
    made of it). Whatever else *current* has, a focus that has faded since
    included, is kept."""
    if state['phase'] != KEPT or identity(current) != state['identity']:
        return None
    try:
        return variant_of(state, current)
    except ValueError:
        return None


def turn_off(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Switch the experiment off, in any phase: it ends, ``off``. The change
    comes back with ``restore``."""
    if not state or state['phase'] == OFF:
        return state
    return {**state, 'phase': OFF, 'outcome': state['outcome'] or 'off'}


def restore(state: dict[str, Any] | None, current: FeedRecipe
            ) -> FeedRecipe | None:
    """What to save to have the recipe as it was before the experiment: the
    undo of the change applied to *current* when *current* is the kept
    variant, *current* itself (a copy) when it is the original; None when
    it is neither (the reader has edited it since: nothing of theirs is
    overwritten). The fields the change did not touch, a faded focus
    included, stay as *current* has them."""
    if not state:
        return None
    mine = identity(current)
    if mine == state['identity']:
        return copy.deepcopy(current)
    if mine == state['variant_identity']:
        return recipeedit.restore(
            current, recipeedit.edits_from_json(state['undo']))
    return None


# -- numbers for the screen ---------------------------------------------------

def summary(state: dict[str, Any]) -> dict[str, Any]:
    """Counts and rates only."""
    return {'phase': state['phase'], 'trials': state['trials'],
            'done': done(state), 'left': max(0, state['trials'] - done(state)),
            'A': {**state['arms']['A'], 'rate': rate(state, 'A')},
            'B': {**state['arms']['B'], 'rate': rate(state, 'B')},
            'outcome': state['outcome'], 'field': state['change']['field']}


# -- the sidecar ---------------------------------------------------------------

def dumps(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(',', ':'))


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in '0123456789abcdef' for c in value)


def validate(state: Any) -> dict[str, Any] | None:
    """*state* when it is an experiment, else None: a wrong version, phase,
    seed or number of trials (``MIN_TRIALS`` to ``MAX_TRIALS``), a change with no
    field, an outcome that is not text, edits or an undo that are not edits
    of a recipe, identities that are not hashes, arms and a log that do not
    add up. An experiment lost is an experiment ended, never an error."""
    try:
        if not isinstance(state, dict) or state.get('v') != VERSION \
                or state.get('phase') not in PHASES:
            return None
        if not (_whole(state['seed']) and _whole(state['trials'])
                and MIN_TRIALS <= state['trials'] <= MAX_TRIALS):
            return None
        if not (isinstance(state['change'], dict)
                and recipeedit.valid_field(state['change'].get('field'))):
            return None
        if state['outcome'] is not None \
                and not isinstance(state['outcome'], str):
            return None
        if not isinstance(state['recipe_id'], str):
            return None
        recipeedit.edits_from_json(state['edits'])
        recipeedit.edits_from_json(state['undo'])
        if not state['edits'] or not (_hex(state['identity'])
                                      and _hex(state['variant_identity'])):
            return None
        total = 0
        for arm in ('A', 'B'):
            stats = state['arms'][arm]
            if not (_whole(stats['n']) and _whole(stats['wins'])
                    and 0 <= stats['wins'] <= stats['n']):
                return None
            total += stats['n']
        log = state['log']
        if not isinstance(log, list) or len(log) != total \
                or total > state['trials']:
            return None
        return state
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def loads(text: str) -> dict[str, Any] | None:
    """The state a sidecar holds (``validate``), or None."""
    try:
        return validate(json.loads(text))
    except (ValueError, TypeError):
        return None
