#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef recipe model, golden schema, migrate, share codec."""

import json
import os
import sys
import tempfile

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

failures = 0


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef.recipe import (  # noqa: E402
    default_recipe, migrate_recipe, recipe_from_dict, recipe_to_dict,
    validate_for_import, validate_for_save,
)
from src.feedchef.sharecodec import (  # noqa: E402
    decode_recipe_b64, encode_recipe, is_feedchef_uri, parse_feedchef_uri,
    recipe_share_uri,
)
from src.feedchef import storage as storemod  # noqa: E402

golden_path = os.path.join(srcdir, 'golden', 'feed-recipe-schema.json')
with open(golden_path, encoding='utf-8') as handle:
    golden = json.load(handle)

recipe = recipe_from_dict(golden)
check('golden has id', bool(recipe.id))
check('golden pantry has no time_window_hours attr on pantry',
      not hasattr(recipe.pantry, 'time_window_hours'))
check('golden round-trip keeps name',
      recipe_from_dict(recipe_to_dict(recipe)).meta.name
      == golden['meta']['name'])

legacy = {
    'id': 'legacy-1',
    'meta': {'name': 'Legacy'},
    'pantry': {'scope': 'home'},
    'diet': {'min_content_length': 12, 'hard_filters': {}},
    'kitchen': {'base_sort': 'score', 'modifiers': []},
}
migrated = migrate_recipe(legacy)
check('legacy min_content_length migrates',
      migrated['diet']['hard_filters']['min_content_length'] == 12)
check('legacy field cleared',
      migrated['diet']['min_content_length'] is None)

fresh = default_recipe()
check('default has diversity modifier',
      fresh.kitchen.modifiers
      and fresh.kitchen.modifiers[0].type == 'diversity')

uri = recipe_share_uri(recipe)
check('share uri is babble feedchef', is_feedchef_uri(uri))
check('oauth uri is not feedchef',
      not is_feedchef_uri('babble://oauth/callback?code=1'))
parsed = parse_feedchef_uri(uri)
check('parse share uri', parsed is not None and parsed.id == recipe.id)

# NO_PADDING round-trip
blob = encode_recipe(recipe)
check('encoded has no padding', '=' not in blob)
check('decode without padding', decode_recipe_b64(blob).id == recipe.id)

try:
    validate_for_import({'pantry': {}})
    check('import without meta raises', False)
except ValueError:
    check('import without meta raises', True)

imported = validate_for_import({
    'meta': {'name': 'From Link'},
    'pantry': {'scope': 'home'},
    'diet': {'hard_filters': {}},
    'kitchen': {'modifiers': []},
})
check('import empty id gets uuid', bool(imported.id))

try:
    validate_for_save(recipe_from_dict({
        'id': '', 'meta': {'name': 'x'}, 'pantry': {}, 'diet': {},
        'kitchen': {}}))
    check('save without id raises', False)
except ValueError:
    check('save without id raises', True)

with tempfile.TemporaryDirectory() as tmp:
    storemod.set_root(tmp)
    storage = storemod.FeedRecipeStorage('https://example.com#1')
    storage.save_recipe(fresh)
    loaded = storage.load_recipes()
    check('storage round-trip', len(loaded) == 1 and loaded[0].id == fresh.id)
    bad = os.path.join(tmp, storemod._safe_name('https://example.com#2')
                       + '.json')
    with open(bad, 'w', encoding='utf-8') as handle:
        handle.write('{not-json')
    other = storemod.FeedRecipeStorage('https://example.com#2')
    check('corrupt load is empty', other.load_recipes() == [])
    check('corrupt file still on disk', os.path.isfile(bad))

    storage.save_recipe(fresh)
    check('delete removes', storage.delete_recipe(fresh.id))
    check('delete missing is false', not storage.delete_recipe(fresh.id))

    # Isolation: two account_keys keep separate files.
    a = storemod.FeedRecipeStorage('https://example.com#alice')
    b = storemod.FeedRecipeStorage('https://example.com#bob')
    ra = default_recipe()
    ra.id = 'alice-only'
    ra.meta.name = 'Alice'
    rb = default_recipe()
    rb.id = 'bob-only'
    rb.meta.name = 'Bob'
    a.save_recipe(ra)
    b.save_recipe(rb)
    check('account A sees only Alice recipe',
          [item.id for item in a.load_recipes()] == ['alice-only'])
    check('account B sees only Bob recipe',
          [item.id for item in b.load_recipes()] == ['bob-only'])
    a.delete_recipe('alice-only')
    check('deleting A does not wipe B',
          [item.id for item in b.load_recipes()] == ['bob-only'])

sys.exit(1 if failures else 0)
