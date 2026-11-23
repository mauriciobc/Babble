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
import os
from datetime import datetime

from src.feedchef import cluster as clustermod
from src.feedchef import langguess
from src.feedchef import contract, kitchen as kitchenmod
from src.feedchef import relevance as relevancemod
from src.feedchef.recipe import max_per
from src.feedchef.textutil import parse_created_at
from feedchefaudit import validate
from feedchefaudit.records import (LogError, Rebuilt, check_replayable,
                                   patched, recipe_of, structure_problem)

RULES = ('record', 'promises', 'hashtags', 'follow', 'order', 'relevance',
         'cluster', 'floor', 'language', 'heat', 'cap', 'removal_reasons',
         'guard')


class Tally:
    """checked / violations per rule, with a few examples to read."""

    def __init__(self):
        self.checked = dict.fromkeys(RULES, 0)
        self.violations = dict.fromkeys(RULES, 0)
        self.examples = {rule: [] for rule in RULES}
        self.unverifiable = 0
        self.text_unverified = 0

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
        self.text_unverified += other.text_unverified

    def verdict(self, rule):
        if self.violations[rule]:
            return 'FAIL'
        return 'PASS' if self.checked[rule] else 'n/a'

    def ok(self):
        return not any(self.violations.values())

    def overall(self):
        """FAIL, or PASS: never a plain PASS when some text could not be
        checked against its hash."""
        if not self.ok():
            return 'FAIL'
        if self.text_unverified:
            return 'PASS (text unverified: %d)' % self.text_unverified
        return 'PASS'


def _tail(sid):
    return sid[-6:]


def _person(entry):
    facts = entry['facts']
    return facts['booster_id'] if facts['boost'] else facts['author_id']


def _mods(recipe, kind):
    return [mod for mod in recipe.kitchen.modifiers if mod.type == kind]


def _caps(recipe):
    """max_per_cluster of each diversity modifier that caps by author."""
    return [max_per(mod.max_per_cluster) for mod in _mods(recipe, 'diversity')
            if 'author' in (mod.diversity_dimensions or ['author'])]


def _host(entry):
    return entry['facts'].get('host') or ''


def _removed_by(entry, *reasons):
    return bool(entry['removal']) and entry['removal']['reason'] in reasons


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
    if reason == 'cluster_cap':
        if entry['score'] is None or 'cluster' not in entry:
            return 'it was never grouped'
        keep = _cluster_keep(rebuilt)
        if keep is None:
            return None
        return 'the strategy keeps it' if entry['id'] in keep else None
    if reason == 'instance_cap':
        cap = contract.instance_cap(recipe)
        left = rebuilt.record['outputs'].get('instance_counts_out', {}).get(
            _host(entry), 0)
        return None if cap is not None and _host(entry) and left >= cap \
            else 'the server is under the cap'
    if reason == 'author_cap':
        caps = _caps(recipe)
        left = rebuilt.record['outputs']['author_counts_out'].get(
            _person(entry), 0)
        return None if caps and left >= min(caps) \
            else 'the person is under the cap'
    return 'not a known reason'


def evaluate_record(loaded):
    """A Tally for one loaded log. A record that is malformed, says
    something that cannot be true of itself, or whose recipe is not the
    one it hashes to is a FAIL of the ``record`` rule with the reason,
    never an exception (a failed or cancelled cook has nothing else to
    judge)."""
    tally = Tally()
    try:
        check_replayable(loaded)
        record = loaded.record
        problems = validate.consistency(record)
        tally.add('record', 1, 0)
        for problem in problems:
            tally.add('record', 0, 1, problem)
        if problems or record['outcome']['status'] != 'ok':
            return tally
        rebuilt = Rebuilt(loaded)
    except LogError as error:
        tally.add('record', 1, 1, str(error))
        return tally
    tally.text_unverified += len(rebuilt.unverified)
    try:
        _judge(tally, rebuilt, record)
    except (LookupError, TypeError, ValueError, AttributeError) as error:
        tally.add('record', 0, 1, 'the record could not be judged (%s)'
                  % type(error).__name__)
    return tally


def _judge(tally, rebuilt, record):
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
        _check_language(tally, rebuilt)
        scored = [e for e in rebuilt.entries() if e['score'] is not None]
        survivors = [_key(e, rebuilt) for e in scored
                     if not _removed_by(e, 'heat_cut', 'cluster_cap')]
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


def _relevance_map(rebuilt):
    """{post id: relevance} recomputed from the recipe's focus and the
    words of the posts that reached the order (not cut by the grouping or
    heat); None when the recipe has no focus, False when the words of the
    posts are not all in the record."""
    recipe = rebuilt.recipe
    keywords = relevancemod.focus_keywords(recipe.kitchen)
    if not keywords:
        return None
    if not rebuilt.words_whole():
        return False
    corpus = [rebuilt.statuses[e['id']] for e in rebuilt.entries()
              if e['score'] is not None
              and not _removed_by(e, 'cluster_cap', 'heat_cut')]
    found = relevancemod.relevance_scores(
        corpus, keywords, recipe.kitchen.active_focus_weights)
    if corpus and not relevancemod.matched(found):
        # No post matched the focus: the page keeps the recipe's order.
        return None
    return found


def _check_order(tally, rebuilt, selected):
    recipe = rebuilt.recipe
    record = rebuilt.record
    statuses = [rebuilt.statuses[e['id']] for e in selected]
    found = _relevance_map(rebuilt)
    if found is False:
        tally.unverifiable += 1
        return
    if found is None:
        scores = {e['id']: e['score']['final'] for e in selected}
        again = kitchenmod.order_statuses(statuses, scores,
                                          recipe.kitchen.base_sort)
        wanted = recipe.kitchen.base_sort
    else:
        again = kitchenmod.order_statuses(statuses, found, 'score')
        wanted = 'relevance to the focus'
    tally.add('order', 1, [str(s.get('id') or '') for s in again]
              != record['outputs']['order'], 'the order is not %s' % wanted)
    if found is None:
        return
    mine = [e for e in rebuilt.entries() if e['score'] is not None
            and not _removed_by(e, 'cluster_cap', 'heat_cut')]
    wrong = sum(1 for e in mine
                if e['score'].get('relevance') != found[e['id']])
    tally.add('relevance', 1, wrong, '%d posts have a relevance that is not '
              'the cosine of their words with the focus' % wrong)
    shown = [found[e['id']] for e in selected]
    rising = any(a < b for a, b in zip(shown, shown[1:], strict=False))
    tally.add('relevance', 1, rising,
              'the page is not non-increasing by relevance')


def _cluster_keep(rebuilt):
    """The ids of the posts the grouping's strategy keeps, chosen again
    from the groups the record names; None when they cannot be."""
    if rebuilt.keep_known:
        return rebuilt.keep
    rebuilt.keep_known = True
    page = rebuilt.record.get('cluster')
    mods = _mods(rebuilt.recipe, 'similarity_cluster')
    if not mods or not page or page.get('status') != 'ok' \
            or not rebuilt.words_whole():
        return None
    entries = [e for e in rebuilt.entries() if e['score'] is not None]
    if any('cluster' not in e for e in entries):
        return None
    where = {e['id']: index for index, e in enumerate(entries)}
    labels = [e['cluster']['id'] for e in entries]
    medoids = {}
    for e in entries:
        medoids[e['cluster']['id']] = where.get(e['cluster']['medoid'], -1)
    if sorted(medoids) != list(range(len(medoids))) or -1 in medoids.values():
        return None
    statuses = [rebuilt.statuses[e['id']] for e in entries]
    keep = clustermod.select(
        statuses, labels, [medoids[g] for g in sorted(medoids)],
        clustermod.strategy_of(mods[0].cluster_strategy),
        clustermod.max_per_cluster(mods[0].max_per_cluster),
        kitchenmod.base_score, kitchenmod._tie_key)
    rebuilt.keep = {entries[index]['id'] for index in keep}
    return rebuilt.keep


def _check_cluster(tally, rebuilt):
    recipe = rebuilt.recipe
    page = rebuilt.record.get('cluster')
    mods = _mods(recipe, 'similarity_cluster')
    scored = [e for e in rebuilt.entries() if e['score'] is not None]
    cut = [e for e in scored if _removed_by(e, 'cluster_cap')]
    if not mods:
        tally.add('cluster', 1, bool(cut), 'a post was cut by a grouping '
                  'the recipe does not have')
        return
    if page is None:
        tally.add('cluster', 1, 1, 'the recipe groups posts and the record '
                  'has no grouping')
        return
    if page['status'] != 'ok':
        # Nothing may have been cut, and "too few" must be true.
        short = page['status'] == 'skipped' and page['n'] >= \
            clustermod.MIN_POSTS
        tally.add('cluster', 1, bool(cut) or short,
                  'the grouping did not run (%s) but posts were cut or it '
                  'had enough posts' % page['status'])
        return
    if not rebuilt.words_whole():
        tally.unverifiable += 1
        return
    count = len(scored)
    groups = {e['cluster']['id'] for e in scored if 'cluster' in e}
    shape = (page['n'] == count and page['k'] == clustermod.group_count(count)
             and len(groups) <= page['k'] and all(
                 'cluster' in e for e in scored))
    tally.add('cluster', 1, not shape, 'the groups are not the k of the '
              'recipe, or a post has none')
    per = clustermod.max_per_cluster(mods[0].max_per_cluster)
    over = {}
    for e in scored:
        if not _removed_by(e, 'cluster_cap'):
            over[e['cluster']['id']] = over.get(e['cluster']['id'], 0) + 1
    tally.add('cluster', 1, any(n > per for n in over.values()),
              'a group keeps more than %d posts' % per)
    keep = _cluster_keep(rebuilt)
    again = keep is not None and {e['id'] for e in scored} - keep != {
        e['id'] for e in cut}
    tally.add('cluster', 1, keep is None or again,
              'the posts cut are not the ones the strategy leaves out')


def _check_floor(tally, rebuilt):
    """"Favor Fresh Posts" raises a factor under 0.2 exactly when the
    recipe also has "Boost Trending Topics" (and says so)."""
    recipe = rebuilt.recipe
    mods = _mods(recipe, 'freshness')
    if not mods:
        return
    now = datetime.fromisoformat(rebuilt.record['clock']['now_utc'])
    trending = kitchenmod.has_trending(recipe)
    for entry in rebuilt.entries():
        if entry['score'] is None:
            continue
        status = rebuilt.statuses[entry['id']]
        wanted = None
        for mod in mods:
            factor = kitchenmod.freshness_factor(status, mod, now, trending)
            if factor is not None:
                wanted = factor if wanted is None else wanted * factor
        wanted = 1.0 if wanted is None else wanted
        lifted = trending and any(
            kitchenmod.floor_applied(status, mod, now) for mod in mods)
        score = entry['score']
        bad = (score['fresh'] != wanted
               or bool(score.get('fresh_floor')) != bool(lifted)
               or (trending and len(mods) == 1
                   and not contract.floor_holds(True, score['fresh'], lifted)))
        tally.add('floor', 1, bad, '%s: freshness %r, floor %s' % (
            _tail(entry['id']), score['fresh'], score.get('fresh_floor')))


def _check_language(tally, rebuilt):
    """A post with no declared language that was judged was judged on a
    guess of 'en' (VADER) or 'pt' (LeIA) with a probability of at least
    ``langguess.MIN_P`` and at least ``MIN_CHARS`` characters of text."""
    engines = {'en': 'vader', 'pt': 'leia'}
    for entry in rebuilt.entries():
        sent = entry['sent']
        facts = entry['facts']
        if sent is None or sent['score'] is None or facts.get('lang'):
            continue
        found = facts.get('lang_guess')
        bad = found is None or found['p'] < langguess.MIN_P \
            or engines.get(found['lang']) != sent['engine']
        if not bad:
            if entry['id'] in rebuilt.unreliable:
                bad = facts['text_len'] < langguess.MIN_CHARS
            else:
                bad = len(langguess.visible_text(
                    rebuilt.statuses[entry['id']])) < langguess.MIN_CHARS
        tally.add('language', 1, bad, '%s judged on a guess that does not '
                  'pass the gates' % _tail(entry['id']))


def _check_kitchen(tally, rebuilt, selected):
    record = rebuilt.record
    recipe = rebuilt.recipe
    _check_order(tally, rebuilt, selected)
    _check_cluster(tally, rebuilt)
    _check_floor(tally, rebuilt)
    scored = [e for e in rebuilt.entries() if e['score'] is not None]
    if _mods(recipe, 'heat'):
        cut = sum(1 for e in scored if _removed_by(e, 'heat_cut'))
        # The grouping runs before heat: heat judges what is left of it.
        left = sum(1 for e in scored if not _removed_by(e, 'cluster_cap'))
        want = _heat_expected(recipe, left)
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
    _check_instance_cap(tally, rebuilt, selected)


def _check_instance_cap(tally, rebuilt, selected):
    cap = contract.instance_cap(rebuilt.recipe)
    if cap is None:
        return
    record = rebuilt.record
    mine = {}
    for entry in selected:
        if _host(entry):
            mine[_host(entry)] = mine.get(_host(entry), 0) + 1
    before = record['params'].get('instance_counts_in', {})
    out = record['outputs'].get('instance_counts_out', {})
    for host, count in mine.items():
        total = before.get(host, 0) + count
        tally.add('cap', 1, total > cap or out.get(host) != total,
                  'a server has %d, cap %d' % (total, cap))


def dedupe_pages(pages):
    """The pages of one cook with one log per (cook, page, hop): a page
    cooked twice (a retry) counts once, by the latest log."""
    latest = {}
    for loaded in pages:
        record = loaded.record
        key = (record['cook_id'], record['page_index'], record['hop'])
        mine = (loaded.timestamp, loaded.path)
        if key not in latest or mine > (latest[key].timestamp,
                                        latest[key].path):
            latest[key] = loaded
    return sorted(latest.values(), key=lambda p: (
        p.record['page_index'], p.record['hop']))


def evaluate_cook(pages):
    """Add the check that spans a cook: the cap holds over all of its
    pages together. *pages* are the loaded logs of one cook (a page cooked
    twice counts once, by its latest log). A cook whose record cannot be
    read is a FAIL of the ``record`` rule; one that was cancelled is no
    page of the cook."""
    tally = Tally()
    recipe = None
    per_person = {}
    per_host = {}
    for loaded in dedupe_pages(pages):
        mine = evaluate_record(loaded)
        tally.merge(mine)
        record = loaded.record
        if record['outcome']['status'] != 'ok' or mine.violations['record']:
            continue
        try:
            recipe = recipe or recipe_of(record)
        except LogError:
            continue
        for entry in record['posts']:
            if entry['removal'] is None:
                person = _person(entry)
                per_person[person] = per_person.get(person, 0) + 1
                if _host(entry):
                    per_host[_host(entry)] = per_host.get(
                        _host(entry), 0) + 1
    caps = _caps(recipe) if recipe else []
    for person, count in per_person.items():
        if caps:
            tally.add('cap', 1, count > min(caps),
                      '%s has %d over the cook, cap %d' % (
                          _tail(person), count, min(caps)))
    cap = contract.instance_cap(recipe) if recipe else None
    for count in per_host.values():
        if cap is not None:
            tally.add('cap', 1, count > cap,
                      'a server has %d over the cook, cap %d' % (count, cap))
    return tally


def group_cooks(loaded_list, rejects=None):
    """{(recipe hash, cook id): [loaded, ...]} of the readable audit
    records, pages in order, a page cooked twice once. A log with no audit
    record (v1) is skipped; one whose record cannot be read is appended to
    *rejects* as (loaded, why) when given."""
    cooks = {}
    for loaded in loaded_list:
        record = loaded.record
        if record is None:
            continue
        why = structure_problem(record)
        if why:
            if rejects is not None:
                rejects.append((loaded, why))
            continue
        key = (record['recipe']['hash'], record['cook_id'])
        cooks.setdefault(key, []).append(loaded)
    for key, pages in cooks.items():
        cooks[key] = dedupe_pages(pages)
    return cooks


def reject_tally(rejects):
    """A Tally holding one ``record`` FAIL for each log that could not be
    read."""
    tally = Tally()
    for loaded, why in rejects:
        tally.add('record', 1, 1, '%s: %s' % (
            os.path.basename(loaded.path), why))
    return tally
