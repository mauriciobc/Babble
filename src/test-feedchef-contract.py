#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: every promise the recipe builder makes, held to the engine.

Display-free. A seeded generator of realistic statuses (boosts, media with
and without alt text, sensitive, content warnings, languages, tags, counts,
dates, followed / unfollowed / own authors) and of random recipes runs
through the real FeedEngine with fake fetch functions over three pages
(scope pages, hashtag timelines, More Like This results), and the combined
output is checked against oracles written here, not against the contract
module. The guard must never drop anything on that data; with a stage
broken on purpose it must drop and record every violator.
"""

import html
import math
import os
import unicodedata
import random
import re
import sys
import tempfile
from datetime import datetime

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from feedchefaudit.fuzz import (  # noqa: E402
    ME, NOW, World, cook, random_recipe,
)
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import deps  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import sentiment as sentmod  # noqa: E402
from src.feedchef.textutil import focus_tokens  # noqa: E402

CASES = int(os.environ.get('FEEDCHEF_CONTRACT_CASES', '240'))
# Grouping needs the optional kmedoids package: without it the kitchen says
# so and removes nothing, and there is no group to check (CI installs it).
HAVE_KMEDOIDS_MISSING = bool(deps.unavailable_reason('numpy', 'kmedoids'))
failures = 0

TMP = tempfile.mkdtemp(prefix='feedchef-contract-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


# --- the oracles (nothing here calls the contract module) ----------------

def plain(content):
    return html.unescape(re.sub(r'<[^>]*>', '', content.get('content') or ''))


def content_of(status):
    return status.get('reblog') or status


def percentile(values, pct):
    ordered = sorted(values)
    rank = pct / 100.0 * (len(ordered) - 1)
    low, high = int(math.floor(rank)), int(math.ceil(rank))
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def judged(content):
    lang = (content.get('language') or '').lower()
    return lang == 'en' or lang.startswith('en-') or lang == 'pt' \
        or lang.startswith('pt-')


def oracle_violations(recipe, status, follows):
    """The promises a post breaks, from the plain text of the promise."""
    content = content_of(status)
    broken = []
    body = plain(content).lower()
    spoiler = (content.get('spoiler_text') or '').lower()
    for word in recipe.diet.keyword_blocklist:
        pattern = r'(?<!\w)%s(?!\w)' % re.escape(word.lower())
        if re.search(pattern, body) or re.search(pattern, spoiler):
            broken.append('blocked words')
            break
    hard = recipe.diet.hard_filters
    media = content.get('media_attachments') or []
    if hard.must_have_media and not media:
        broken.append('only with media')
    if hard.must_have_alt_text and any(
            not (m.get('description') or '').strip() for m in media):
        broken.append('alt text')
    if hard.no_sensitive_media and content.get('sensitive'):
        broken.append('sensitive')
    if hard.min_content_length and len(body.strip()) < hard.min_content_length:
        broken.append('minimum length')
    if hard.positive_only and judged(content):
        text = '%s %s' % (plain(content), content.get('spoiler_text') or '')
        if sentmod.compound_score(text, content['language']) < 0:
            broken.append('only positive')
    names = {t['name'] for t in content.get('tags') or []}
    if set(recipe.pantry.hashtags_exclude) & names:
        broken.append('exclude hashtags')
    if recipe.pantry.hashtags_include \
            and not set(recipe.pantry.hashtags_include) & names:
        broken.append('include hashtags')
    reach = recipe.pantry.network_reach
    if reach.enabled and reach.max_degrees is not None \
            and not reach.include_unknown:
        author = content['account']['id']
        if author != ME and author not in follows:
            broken.append('only people I follow')
    return broken


def non_increasing(values):
    return all(values[i] >= values[i + 1] for i in range(len(values) - 1))


def key_of(status):
    content = content_of(status)
    return (datetime.fromisoformat(content['created_at']),
            int(status['id']))


def oracle_relevance(corpus, keywords, weights):
    """Relevance of each post of *corpus* to a focus, from Java's formulas
    written out again here (the tokens are textutil's: they have their own
    tests): smoothed IDF over the corpus, augmented TF, a reference vector
    from the keywords and weights, the cosine."""
    total = len(corpus)
    tokens = [focus_tokens(s) for s in corpus]
    df = {}
    for words in tokens:
        for word in set(words):
            df[word] = df.get(word, 0) + 1

    def vector(words):
        counts = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1
        top = max(counts.values()) if counts else 1
        return {word: (0.5 + 0.5 * n / top)
                * (math.log(total / (1 + df[word])) + 1)
                for word, n in counts.items()}

    def fold(word):
        word = unicodedata.normalize('NFD', word.lower())
        return ''.join(c for c in word if not unicodedata.combining(c))

    equal = not weights or len(weights) != len(keywords)
    ref = {}
    for index, word in enumerate(keywords):
        weight = 1.0 if equal else (
            weights[index] if weights[index] > 0 else 0.1)
        ref[fold(word)] = weight

    def cosine(a, b):
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if not na or not nb:
            return 0.0
        return sum(v * b[k] for k, v in a.items() if k in b) / (na * nb)

    return {s['id']: cosine(ref, vector(words))
            for s, words in zip(corpus, tokens, strict=True)}


def host_of(status):
    return status['account']['url'].split('/')[2]


# --- the fuzz ------------------------------------------------------------

problems = {}
seen = {}


def fail(promise, seed, detail):
    problems.setdefault(promise, []).append('seed %d: %s' % (seed, detail))


def count_seen(name, amount=1):
    seen[name] = seen.get(name, 0) + amount


total_items = 0
for case in range(CASES):
    seed = 1000 + case
    rng = random.Random(seed)
    recipe = random_recipe(rng)
    world = World(rng, recipe)
    results = cook(world, recipe, case, now=NOW)
    by_id = {s['id']: s for s in [*world.pool, *world.focus_pool, *(
        s for found in world.tag_pool.values() for s in found)]}
    combined = [item for r in results for item in r.items]
    total_items += len(combined)
    reach = recipe.pantry.network_reach
    for result in results:
        if result.guard['dropped'] or result.guard['checked'] < len(
                result.items):
            fail('guard silent', seed, 'guard dropped %r'
                 % (result.guard['dropped'],))
    count_seen('guard checked', sum(r.guard['checked'] for r in results))
    for item in combined:
        broken = oracle_violations(recipe, item, world.follows)
        for promise in broken:
            fail(promise, seed, 'post %s breaks it' % item['id'])
    ids = [i['id'] for i in combined]
    if len(ids) != len(set(ids)):
        fail('no duplicates', seed, 'duplicate ids')
    for name, active in (
            ('blocked words', bool(recipe.diet.keyword_blocklist)),
            ('only with media', recipe.diet.hard_filters.must_have_media),
            ('alt text', recipe.diet.hard_filters.must_have_alt_text),
            ('sensitive', recipe.diet.hard_filters.no_sensitive_media),
            ('minimum length',
             recipe.diet.hard_filters.min_content_length > 0),
            ('only positive', recipe.diet.hard_filters.positive_only),
            ('include hashtags', bool(recipe.pantry.hashtags_include)),
            ('exclude hashtags', bool(recipe.pantry.hashtags_exclude)),
            ('only people I follow', reach.enabled)):
        if active:
            count_seen(name, len(combined))
    # The person cap holds over the whole session.
    for mod in recipe.kitchen.modifiers:
        if mod.type == 'diversity':
            per = {}
            for item in combined:
                account = item['account']['id']
                per[account] = per.get(account, 0) + 1
            if any(n > mod.max_per_cluster for n in per.values()):
                fail('per person', seed, 'counts %r over %d'
                     % (per, mod.max_per_cluster))
            count_seen('per person', len(combined))
    focus = bool(recipe.kitchen.active_focus_hashtags
                 or recipe.kitchen.active_focus_keywords)
    keywords = recipe.kitchen.active_focus_keywords
    kinds = {mod.type: mod for mod in recipe.kitchen.modifiers}
    # The cap per server holds over the whole session.
    server_cap = next((m.max_per_cluster for m in recipe.kitchen.modifiers
                       if m.type == 'diversity'
                       and 'instance' in (m.diversity_dimensions or [])),
                      None)
    if server_cap is not None:
        per_server = {}
        for item in combined:
            per_server[host_of(item)] = per_server.get(host_of(item), 0) + 1
        if any(n > server_cap for n in per_server.values()):
            fail('per server', seed, 'counts %r over %d'
                 % (per_server, server_cap))
        count_seen('per server', len(combined))
    for result in results:
        items = result.items
        meta = result.kitchen_meta
        if keywords and meta.get('focus_unmatched'):
            # No post matched the focus: the recipe's own order stands.
            count_seen('focus unmatched', len(items))
        if keywords and not meta.get('focus_unmatched'):
            # Posts are ordered by relevance to the focus, which is the
            # cosine of Java's formulas (recomputed here over the posts
            # that were left after the grouping and the heat cut).
            found = meta['relevance_scores']
            shown = [found[i['id']] for i in items]
            if not non_increasing([round(s, 9) for s in shown]):
                fail('order by relevance', seed, 'page not by relevance')
            again = oracle_relevance(
                [by_id[sid] for sid in found], keywords,
                recipe.kitchen.active_focus_weights)
            if any(abs(again[sid] - found[sid]) > 1e-9 for sid in found):
                fail('relevance', seed, 'a cosine is not Java\'s')
            count_seen('order by relevance', len(items))
            count_seen('relevance', len(found))
        groups = meta.get('groups')
        if groups is not None and 'similarity_cluster' in kinds:
            per = max(1, kinds['similarity_cluster'].max_per_cluster or 1)
            kept = {}
            for item in items:
                kept[groups[item['id']]] = kept.get(
                    groups[item['id']], 0) + 1
            if any(n > per for n in kept.values()):
                fail('per group', seed, 'groups %r over %d' % (kept, per))
            count_seen('per group', len(items))
        if 'freshness' in kinds:
            decay = kinds['freshness'].decay_minutes
            for sid, (factor, lifted) in meta['fresh'].items():
                age = (NOW - datetime.fromisoformat(
                    content_of(by_id[sid])['created_at'])
                    ).total_seconds() / 60.0
                raw = max(0.0, min(1.0, 1.0 - age / decay)) \
                    if age >= 0 else None
                if raw is None:
                    continue
                want = max(raw, 0.2) if 'trending' in kinds else raw
                if abs(factor - want) > 1e-12 \
                        or lifted != ('trending' in kinds and raw < 0.2):
                    fail('freshness floor', seed, 'factor %r, expected %r'
                         % (factor, want))
                count_seen('freshness floor')
                if 'trending' in kinds and raw < 0.2:
                    count_seen('floor lifted')
                elif raw < 0.2:
                    count_seen('floor not lifted without trending')
        chronological = recipe.kitchen.base_sort == 'chronological'
        if keywords and not meta.get('focus_unmatched'):
            ordered, name = True, 'order by relevance'
        elif chronological:
            keys = [key_of(i) for i in items]
            ordered = non_increasing(keys)
            name = 'order newest first'
        else:
            scores = [result.score_map[i['id']] for i in items]
            ordered = non_increasing([round(s, 9) for s in scores])
            name = 'order most engaging'
        if not ordered:
            fail(name, seed, 'page out of order')
        if not keywords or meta.get('focus_unmatched'):
            count_seen(name, len(items))
        if focus and (not keywords or meta.get('focus_unmatched')):
            # A focus of hashtags only does not change the order.
            count_seen('order with focus', len(items))
        for mod in recipe.kitchen.modifiers:
            if mod.type != 'heat' or not mod.percentile_threshold:
                continue
            page_ids = list(result.kitchen_meta['ranks'])
            page_scores = [result.score_map[i] for i in page_ids]
            if not page_scores:
                continue
            # Exactly floor(P x n / 100) of the n posts go, the lowest
            # first, and never all of them.
            n = len(page_scores)
            clustered = meta.get('groups')
            if clustered is not None and 'similarity_cluster' in kinds:
                # The grouping runs first: each group keeps
                # min(max_per_cluster, its size) posts, and heat judges
                # what is left.
                per = max(1, kinds['similarity_cluster'].max_per_cluster
                          or 1)
                sizes = {}
                for group in clustered.values():
                    sizes[group] = sizes.get(group, 0) + 1
                n = sum(min(per, size) for size in sizes.values())
            hide = min(math.floor(mod.percentile_threshold * n / 100), n - 1)
            info = result.kitchen_meta['heat']
            if (info['before'], info['after']) != (n, n - hide):
                fail('heat', seed, 'kept %d of %d, expected %d'
                     % (info['after'], info['before'], n - hide))
            if hide and clustered is None:
                cutoff = sorted(page_scores)[hide - 1]
                for item in items:
                    if result.score_map[item['id']] < cutoff - 1e-9:
                        fail('heat', seed, 'kept %s below a hidden post'
                             % item['id'])
            count_seen('heat', len(page_ids))
    # Included hashtags reach every source, a focus included.
    if recipe.pantry.hashtags_include and focus:
        count_seen('include hashtags with focus', len(combined))

check('the generator ran %d cases and produced posts (%d)'
      % (CASES, total_items), CASES >= 200 and total_items > 1000)
check('the guard checked every returned post (%d) and dropped none'
      % seen.get('guard checked', 0), 'guard silent' not in problems)
for name in ('blocked words', 'only with media', 'alt text', 'sensitive',
             'minimum length', 'only positive', 'include hashtags',
             'exclude hashtags', 'only people I follow', 'per person',
             'order newest first', 'order most engaging', 'order with focus',
             'order by relevance', 'relevance', 'per group', 'per server',
             'freshness floor', 'heat'):
    bad = problems.get(name, [])
    check('promise: %s (%d posts checked)%s'
          % (name, seen.get(name, 0),
             ' -- ' + '; '.join(bad[:3]) if bad else ''),
          not bad and (seen.get(name, 0) > 50 or (
              name == 'per group' and HAVE_KMEDOIDS_MISSING)))
check('the freshness floor lifted a factor with trending (%d) and never '
      'without it (%d posts under 0.2 left alone)'
      % (seen.get('floor lifted', 0),
         seen.get('floor not lifted without trending', 0)),
      seen.get('floor lifted', 0) > 5
      and seen.get('floor not lifted without trending', 0) > 5)
check('no duplicate posts across pages' + ' -- %s' % problems.get(
    'no duplicates', ''), 'no duplicates' not in problems)
check('the seeded focus cases bind included hashtags (%d posts)'
      % seen.get('include hashtags with focus', 0),
      seen.get('include hashtags with focus', 0) > 0)



sys.exit(1 if failures or problems else 0)
