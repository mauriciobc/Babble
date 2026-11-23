# SPDX-License-Identifier: GPL-3.0-or-later
"""Checking what a cook chose against the recipe that asked for it.

Every check is a hard guarantee of the recipe, with no tolerance: one
violation is a FAIL. A record is rebuilt from its facts (``records.py``), and
the engine's own predicates (``contract.violations``, ``kitchen.order_statuses``)
judge the posts it kept; the aggregate checks (order, heat, the cap per
person, hashtags, follow) and the justification of every removal are written
here from the plain meaning of each setting.
"""

import math

from src.feedchef import audit as auditmod
from src.feedchef import contract, kitchen as kitchenmod
from src.feedchef.textutil import parse_created_at
from feedchefaudit.records import Rebuilt, check_replayable, patched

RULES = ('promises', 'hashtags', 'follow', 'order', 'heat', 'cap',
         'removal_reasons', 'guard')


class Tally:
    """checked / violations per rule, with a few examples to read."""

    def __init__(self):
        self.checked = dict.fromkeys(RULES, 0)
        self.violations = dict.fromkeys(RULES, 0)
        self.examples = {rule: [] for rule in RULES}
        self.unverifiable = 0

    def add(self, rule, checked=1, violated=0, example=''):
        self.checked[rule] += checked
        self.violations[rule] += int(violated)
        if violated and len(self.examples[rule]) < 5:
            self.examples[rule].append(example)

    def merge(self, other):
        for rule in RULES:
            self.checked[rule] += other.checked[rule]
            self.violations[rule] += other.violations[rule]
            self.examples[rule] = (self.examples[rule]
                                   + other.examples[rule])[:5]
        self.unverifiable += other.unverifiable

    def verdict(self, rule):
        if self.violations[rule]:
            return 'FAIL'
        return 'PASS' if self.checked[rule] else 'n/a'

    def ok(self):
        return not any(self.violations.values())


def _tail(sid):
    return sid[-6:]


def _person(entry):
    facts = entry['facts']
    return facts['booster_id'] if facts['boost'] else facts['author_id']


def _mods(recipe, kind):
    return [mod for mod in recipe.kitchen.modifiers if mod.type == kind]


def _caps(recipe):
    """max_per_cluster of each diversity modifier that caps by author."""
    return [int(mod.max_per_cluster if mod.max_per_cluster is not None
                else 1) for mod in _mods(recipe, 'diversity')
            if 'author' in (mod.diversity_dimensions or ['author'])]


def _heat_expected(recipe, scored):
    total = 0
    left = scored
    for mod in _mods(recipe, 'heat'):
        percentile = float(mod.percentile_threshold
                           if mod.percentile_threshold is not None else 0)
        if percentile <= 0 or not left:
            continue
        hide = min(int(math.floor(percentile * left / 100.0)), left - 1)
        if hide > 0:
            total += hide
            left -= hide
    return total


def _key(entry, rebuilt):
    created = parse_created_at(entry['facts']['created_c']) \
        or kitchenmod._NO_DATE
    return (entry['score']['final'], created,
            kitchenmod._id_key(rebuilt.statuses[entry['id']]))


def _selected(rebuilt):
    by_id = {entry['id']: entry for entry in rebuilt.entries()}
    return [by_id[sid] for sid in rebuilt.record['outputs']['order']]


def _justified(entry, reason, rebuilt, env_args, survivors):
    """None when the recipe justifies the removal, else why not."""
    recipe = rebuilt.recipe
    tags = {name.lower() for name in entry['facts']['tags']}
    reach = recipe.pantry.network_reach
    if reason == 'pantry_excluded_tag':
        return None if tags & set(recipe.pantry.hashtags_exclude) \
            else 'no excluded hashtag on it'
    if reason == 'pantry_missing_included_tag':
        return None if recipe.pantry.hashtags_include and not (
            tags & set(recipe.pantry.hashtags_include)) \
            else 'it has an included hashtag'
    if reason.startswith('diet:'):
        promise = reason[5:]
        broken = contract.violations(
            recipe, rebuilt.statuses[entry['id']], only={promise},
            **env_args)
        return None if promise in broken else '%s holds' % promise
    if reason == 'reach_not_followed':
        return None if contract.reach_kind(reach) is not None and \
            entry['follow']['verdict'] == 'not_followed' \
            else 'the author is followed or the filter is off'
    if reason == 'heat_cut':
        weaker = survivors and _key(entry, rebuilt) > min(survivors)
        return 'a kept post is weaker' if weaker else None
    if reason == 'author_cap':
        caps = _caps(recipe)
        left = rebuilt.record['outputs']['author_counts_out'].get(
            _person(entry), 0)
        return None if caps and left >= min(caps) \
            else 'the person is under the cap'
    return 'not a known reason'


def evaluate_record(loaded):
    """A Tally for one loaded log (a failed cook has nothing to judge)."""
    check_replayable(loaded)
    tally = Tally()
    record = loaded.record
    if record['outcome']['status'] != 'ok':
        return tally
    rebuilt = Rebuilt(loaded)
    recipe = rebuilt.recipe
    env = record['env']
    kind = contract.reach_kind(recipe.pantry.network_reach)
    env_args = {
        'follow_ids': frozenset(rebuilt.follow_ids())
        if kind == 'follows' else None,
        'self_id': env['self_id'], 'native_bluesky': env['native_bluesky'],
        'sentiment': lambda status: rebuilt.sentiment.get(
            str(status.get('id') or ''))}
    selected = _selected(rebuilt)
    includes = set(recipe.pantry.hashtags_include)
    excludes = set(recipe.pantry.hashtags_exclude)
    with patched(rebuilt):
        for entry in selected:
            sid = entry['id']
            broken = contract.violations(
                recipe, rebuilt.statuses[sid], **env_args)
            tally.add('promises', 1, bool(broken),
                      '%s breaks %s' % (_tail(sid), ','.join(broken)))
            tags = {name.lower() for name in entry['facts']['tags']}
            if includes or excludes:
                bad = bool(tags & excludes) or (
                    bool(includes) and not tags & includes)
                tally.add('hashtags', 1, bad, '%s hashtags' % _tail(sid))
            if kind is not None:
                allowed = entry['follow']['verdict'] in (
                    ('follows', 'self') if kind == 'follows' else ('self',))
                tally.add('follow', 1, not allowed,
                          '%s author not followed' % _tail(sid))
        _check_kitchen(tally, rebuilt, selected)
        scored = [e for e in rebuilt.entries() if e['score'] is not None]
        survivors = [_key(e, rebuilt) for e in scored
                     if not (e['removal'] and
                             e['removal']['reason'] == 'heat_cut')]
        for entry in rebuilt.entries():
            removal = entry['removal']
            if removal is None:
                continue
            reason = removal['reason']
            if reason.startswith('guard:'):
                tally.add('guard', 1, 1, '%s dropped by the guard (%s)' % (
                    _tail(entry['id']), reason[6:]))
                continue
            if reason == 'viewer_hide':
                tally.unverifiable += 1
                continue
            problem = _justified(entry, reason, rebuilt, env_args, survivors)
            tally.add('removal_reasons', 1, problem is not None,
                      '%s %s: %s' % (_tail(entry['id']), reason, problem))
    tally.add('guard', record['summary']['guard_checked'], 0)
    return tally


def _check_kitchen(tally, rebuilt, selected):
    record = rebuilt.record
    recipe = rebuilt.recipe
    statuses = [rebuilt.statuses[e['id']] for e in selected]
    scores = {e['id']: e['score']['final'] for e in selected}
    again = [str(s.get('id') or '') for s in kitchenmod.order_statuses(
        statuses, scores, recipe.kitchen.base_sort)]
    tally.add('order', 1, again != record['outputs']['order'],
              'the order is not %s' % recipe.kitchen.base_sort)
    scored = [e for e in rebuilt.entries() if e['score'] is not None]
    if _mods(recipe, 'heat'):
        cut = sum(1 for e in scored if e['removal']
                  and e['removal']['reason'] == 'heat_cut')
        want = _heat_expected(recipe, len(scored))
        tally.add('heat', 1, cut != want,
                  'heat removed %d, floor(P*n/100) is %d' % (cut, want))
    caps = _caps(recipe)
    if caps:
        mine = {}
        for entry in selected:
            mine[_person(entry)] = mine.get(_person(entry), 0) + 1
        before = record['params']['author_counts_in']
        out = record['outputs']['author_counts_out']
        for person, count in mine.items():
            total = before.get(person, 0) + count
            tally.add('cap', 1, total > min(caps) or out.get(person) !=
                      total, '%s has %d, cap %d' % (
                          _tail(person), total, min(caps)))


def evaluate_cook(pages):
    """Add the check that spans a cook: the cap holds over all of its
    pages together. *pages* are the loaded logs of one cook."""
    tally = Tally()
    recipe = None
    per_person = {}
    for loaded in pages:
        tally.merge(evaluate_record(loaded))
        record = loaded.record
        if record['outcome']['status'] != 'ok':
            continue
        recipe = recipe or Rebuilt(loaded).recipe
        for entry in record['posts']:
            if entry['removal'] is None:
                person = _person(entry)
                per_person[person] = per_person.get(person, 0) + 1
    caps = _caps(recipe) if recipe else []
    for person, count in per_person.items():
        if caps:
            tally.add('cap', 1, count > min(caps),
                      '%s has %d over the cook, cap %d' % (
                          _tail(person), count, min(caps)))
    return tally


def group_cooks(loaded_list):
    """{(recipe hash, cook id): [loaded, ...]} of the readable audit
    records, pages in order."""
    cooks = {}
    for loaded in loaded_list:
        record = loaded.record
        if not isinstance(record, dict) or record.get('schema') != \
                auditmod.SCHEMA:
            continue
        key = (record['recipe']['hash'], record['cook_id'])
        cooks.setdefault(key, []).append(loaded)
    for pages in cooks.values():
        pages.sort(key=lambda p: (p.record['page_index'], p.record['hop']))
    return cooks
