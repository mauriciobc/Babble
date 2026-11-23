# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, reversible edits to a recipe (no widgets, standard library only).

The advice (``advice.py``), the modes and the slider (``templates.py``) and the
A/B optimizer (``optimizer.py``) all change a recipe the same way: an
``Edit`` names a field and the value it should have, and ``apply_edits``
returns a modified *copy* together with the edits that put every touched
field back. The recipe it is given is never changed, and applying the undo
to the result gives a recipe whose stored form is exactly the original's
(the same modifiers in the same places).

The fields are the ones the builder shows, by the names below:

* ``heat`` and ``freshness``: a percent (0 to 100) or a number of minutes, or
  None to remove the modifier;
* ``base_sort`` ('score' or 'chronological') and ``scope``;
* ``hard.<name>``: one of the diet's hard filters (a switch, or the minimum
  length);
* ``hashtags_include``: the list.
"""

from __future__ import annotations

import copy
from typing import Any, NamedTuple

from . import heat as heatmod
from .recipe import (FeedRecipe, Modifier, recipe_to_dict,
                     validate_for_import)

SORTS = ('score', 'chronological')
SCOPES = ('home', 'local', 'federated', 'list')
HARD = ('must_have_media', 'must_have_alt_text', 'no_sensitive_media',
        'min_content_length', 'positive_only')


class Edit(NamedTuple):
    field: str
    # The value the field gets; for ``heat`` and ``freshness`` None removes the
    # modifier, and a dict {'index', 'mod'} is the exact restore an undo
    # carries (the modifier as it was, and where).
    value: Any


class Applied(NamedTuple):
    recipe: FeedRecipe
    undo: tuple[Edit, ...]


_MODIFIER_FIELDS = {'heat': 'heat', 'freshness': 'freshness'}


def valid_field(field: Any) -> bool:
    """Whether *field* names a field of a recipe edit."""
    return isinstance(field, str) and (
        field in ('heat', 'freshness', 'base_sort', 'scope',
                  'hashtags_include')
        or (field.startswith('hard.') and field[5:] in HARD))


def edits_to_json(edits) -> list[list[Any]]:
    return [[edit.field, edit.value] for edit in edits]


def edits_from_json(rows) -> tuple[Edit, ...]:
    """The edits of a stored list; ValueError when it is not one."""
    found = []
    for row in rows:
        if not (isinstance(row, (list, tuple)) and len(row) == 2
                and valid_field(row[0])):
            raise ValueError('not an edit: %r' % (row,))
        found.append(Edit(row[0], row[1]))
    return tuple(found)


def _find(recipe: FeedRecipe, kind: str) -> int:
    for index, mod in enumerate(recipe.kitchen.modifiers):
        if mod.type == kind:
            return index
    return -1


def _mod_dict(mod: Modifier) -> dict[str, Any]:
    return dict(vars(mod))


def current(recipe: FeedRecipe, field: str) -> Any:
    """The value of *field* now (None for a modifier the recipe lacks)."""
    if field in _MODIFIER_FIELDS:
        index = _find(recipe, _MODIFIER_FIELDS[field])
        if index < 0:
            return None
        mod = recipe.kitchen.modifiers[index]
        if field == 'heat':
            return heatmod.whole(heatmod.clean(mod.percentile_threshold))
        return int(mod.decay_minutes or 360)
    if field == 'base_sort':
        return recipe.kitchen.base_sort
    if field == 'scope':
        return recipe.pantry.scope
    if field == 'hashtags_include':
        return list(recipe.pantry.hashtags_include)
    if field.startswith('hard.') and field[5:] in HARD:
        return getattr(recipe.diet.hard_filters, field[5:])
    raise ValueError('not a field of a recipe edit: %s' % field)


def _put_modifier(recipe: FeedRecipe, field: str, value: Any) -> None:
    kind = _MODIFIER_FIELDS[field]
    mods = recipe.kitchen.modifiers
    index = _find(recipe, kind)
    if isinstance(value, dict):
        # An undo: the modifier as it was, where it was.
        if index >= 0:
            del mods[index]
        if value['mod'] is not None:
            mods.insert(min(value['index'], len(mods)), Modifier(**value['mod']))
        return
    if value is None:
        if index >= 0:
            del mods[index]
        return
    if index < 0:
        mods.append(Modifier(type=kind))
        index = len(mods) - 1
    if field == 'heat':
        mods[index].percentile_threshold = heatmod.whole(heatmod.clean(value))
    else:
        mods[index].decay_minutes = int(value)


def put(recipe: FeedRecipe, field: str, value: Any) -> None:
    """Set *field* on *recipe* itself (use ``apply_edits`` for a copy)."""
    if field in _MODIFIER_FIELDS:
        _put_modifier(recipe, field, value)
    elif field == 'base_sort':
        if value not in SORTS:
            raise ValueError('not a sort: %r' % (value,))
        recipe.kitchen.base_sort = value
    elif field == 'scope':
        if value not in SCOPES:
            raise ValueError('not a scope: %r' % (value,))
        recipe.pantry.scope = value
    elif field == 'hashtags_include':
        recipe.pantry.hashtags_include = list(value)
    elif field.startswith('hard.') and field[5:] in HARD:
        setattr(recipe.diet.hard_filters, field[5:], value)
    else:
        raise ValueError('not a field of a recipe edit: %s' % field)


def _before(recipe: FeedRecipe, field: str) -> Any:
    if field in _MODIFIER_FIELDS:
        index = _find(recipe, _MODIFIER_FIELDS[field])
        return {'index': index, 'mod': None if index < 0 else _mod_dict(
            recipe.kitchen.modifiers[index])}
    return current(recipe, field)


def apply_edits(recipe: FeedRecipe, edits) -> Applied:
    """A copy of *recipe* with *edits* made, and the undo (the edits that
    restore each touched field, in reverse order). Raises ValueError for an
    edit that is not one (an unknown field, a value out of range) or that
    leaves a recipe the import would refuse; nothing is changed then."""
    done = copy.deepcopy(recipe)
    undo = []
    for edit in edits:
        undo.append(Edit(edit.field, _before(done, edit.field)))
        put(done, edit.field, edit.value)
    validate_for_import(recipe_to_dict(done))
    return Applied(done, tuple(reversed(undo)))


def restore(recipe: FeedRecipe, undo) -> FeedRecipe:
    """A copy of *recipe* with the fields of *undo* put back."""
    done = copy.deepcopy(recipe)
    for edit in undo:
        put(done, edit.field, edit.value)
    return done
