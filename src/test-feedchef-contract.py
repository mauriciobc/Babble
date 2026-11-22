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
import random
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import kitchen as kitchenmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import pantry as pantrymod  # noqa: E402
from src.feedchef import sentiment as sentmod  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import (  # noqa: E402
    Modifier, NetworkReachConfig, default_recipe,
)

CASES = int(os.environ.get('FEEDCHEF_CONTRACT_CASES', '240'))
failures = 0
NOW = datetime.now(timezone.utc)

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


# --- the generator -----------------------------------------------------

TAGS = ['coffee', 'tea', 'art', 'news', 'cats', 'music']
FOLLOWED = ['f%d' % i for i in range(1, 7)]
STRANGERS = ['u%d' % i for i in range(1, 7)]
ME = 'me'
TEXTS = [
    ('en', 'I love this wonderful amazing day!'),
    ('en', 'This is terrible, I hate it so much'),
    ('en', 'Just a plain note about the weather'),
    ('en', 'Buy crypto now, this is spam'),
    ('pt', 'Eu adoro este dia maravilhoso, estou muito feliz!'),
    ('pt-BR', 'Eu odeio isso, é horrível e terrível'),
    ('es', 'Me encanta este día maravilhoso'),
    ('es', 'Esto es terrible, lo odio'),
    (None, 'A short one'),
    (None, 'Un café bien largo para pensar en todo lo que pasó hoy y ayer'),
    ('en', 'Check https://example.com/some/long/path?x=1 for a long, long '
           'story about the café scene downtown that I enjoyed'),
]
WORDS = ['spam', 'crypto', 'terrible', 'café', 'weather']

_ids = [10 ** 6]


def next_id():
    _ids[0] += 1
    return str(_ids[0])


def html_of(rng, text):
    if rng.random() < 0.3:
        text = text.replace('https://example.com/some/long/path?x=1', (
            '<a href="https://example.com/some/long/path?x=1">'
            '<span class="invisible">https://</span>'
            '<span class="ellipsis">example.com/some/lo</span>'
            '<span class="invisible">ng/path?x=1</span></a>'))
    return '<p>%s</p>' % text.replace('&', '&amp;')


def make_content(rng, author, tags=None, created=None):
    lang, text = rng.choice(TEXTS)
    media = []
    for _i in range(rng.choice([0, 0, 0, 1, 1, 2])):
        desc = rng.choice([None, '', '  ', 'A photo of a cat'])
        media.append({'id': next_id(), 'type': 'image',
                      'description': desc})
    spoiler = rng.choice(['', '', '', 'politics', 'weather talk'])
    created = created or (NOW - timedelta(minutes=rng.randint(1, 3000)))
    return {
        'id': next_id(),
        'created_at': created.isoformat(),
        'content': html_of(rng, text),
        'spoiler_text': spoiler,
        'sensitive': bool(spoiler) and rng.random() < 0.9
        or rng.random() < 0.1,
        'language': lang,
        'favourites_count': rng.choice([0, 0, 1, 2, 5, 9, 30]),
        'reblogs_count': rng.choice([0, 0, 1, 3, 8]),
        'replies_count': rng.choice([0, 0, 1, 2, 6]),
        'account': {'id': author, 'url': 'https://ex.ample/@%s' % author},
        'tags': [{'name': t} for t in (
            tags if tags is not None
            else rng.sample(TAGS, rng.choice([0, 0, 1, 1, 2])))],
        'media_attachments': media,
    }


def make_status(rng, tags=None, created=None):
    """A timeline entry: a post, or a boost of one."""
    if rng.random() < 0.3:
        original = make_content(
            rng, rng.choice(FOLLOWED + STRANGERS + [ME]), tags, created)
        booster = rng.choice(FOLLOWED + STRANGERS + [ME])
        return {
            'id': next_id(),
            'created_at': (NOW - timedelta(minutes=rng.randint(1, 50))
                           ).isoformat(),
            'account': {'id': booster,
                        'url': 'https://ex.ample/@%s' % booster},
            'reblog': original,
            'content': '', 'tags': [], 'media_attachments': [],
        }
    return make_content(
        rng, rng.choice(FOLLOWED + STRANGERS + [ME]), tags, created)


def random_recipe(rng):
    recipe = default_recipe('fuzz')
    recipe.pantry.scope = rng.choice(['home', 'local', 'federated'])
    if rng.random() < 0.35:
        recipe.pantry.hashtags_include = rng.sample(TAGS, rng.choice([1, 2]))
    if rng.random() < 0.35:
        recipe.pantry.hashtags_exclude = rng.sample(TAGS, rng.choice([1, 2]))
    if rng.random() < 0.5:
        recipe.pantry.network_reach = NetworkReachConfig(
            enabled=True, max_degrees=1,
            include_self=rng.random() < 0.7)
    diet = recipe.diet
    if rng.random() < 0.35:
        diet.keyword_blocklist = [w.lower() for w in
                                  rng.sample(WORDS, rng.choice([1, 2, 3]))]
    hard = diet.hard_filters
    hard.must_have_media = rng.random() < 0.15
    hard.must_have_alt_text = rng.random() < 0.2
    hard.no_sensitive_media = rng.random() < 0.25
    hard.min_content_length = rng.choice([0, 0, 0, 20, 40, 80])
    hard.positive_only = rng.random() < 0.3
    kit = recipe.kitchen
    kit.base_sort = rng.choice(['score', 'chronological'])
    mods = []
    if rng.random() < 0.5:
        mods.append(Modifier(type='freshness',
                             decay_minutes=rng.choice([60, 360, 2000])))
    if rng.random() < 0.4:
        mods.append(Modifier(type='heat',
                             percentile_threshold=rng.choice(
                                 [0, 10, 25, 50, 75, 90])))
    if rng.random() < 0.5:
        mods.append(Modifier(type='diversity',
                             max_per_cluster=rng.choice([1, 2, 3, 4]),
                             diversity_dimensions=['author']))
    if rng.random() < 0.35:
        mods.append(Modifier(type='trending',
                             growth_threshold=rng.choice([0.1, 0.3, 1.0])))
    if rng.random() < 0.35:
        mods.append(Modifier(type='sentiment_boost',
                             sentiment_boost=rng.choice([0.2, 0.5, 1.0])))
    rng.shuffle(mods)
    kit.modifiers = mods
    if rng.random() < 0.2:
        if rng.random() < 0.5:
            kit.active_focus_hashtags = [rng.choice(TAGS)]
        else:
            kit.active_focus_keywords = ['coffee']
    return recipe


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


# --- the world of one case -----------------------------------------------

class World:
    def __init__(self, rng, recipe):
        self.follows = set(rng.sample(FOLLOWED, rng.choice([0, 2, 4, 6])))
        self.pool = sorted((make_status(rng) for _i in range(110)),
                           key=lambda s: int(s['id']), reverse=True)
        self.tag_pool = {tag: [make_status(rng, tags=[tag])
                               for _i in range(8)] for tag in TAGS}
        self.focus_pool = [make_status(rng) for _i in range(30)]
        self.rng = rng

    def fetch_scope(self, max_id, count):
        items = [s for s in self.pool
                 if max_id is None or int(s['id']) < int(max_id)]
        return items[:count]

    def fetch_hashtag(self, tag, count):
        return list(self.tag_pool.get(tag, []))

    def search(self, query):
        wanted = query.lstrip('#')
        return [s for s in self.focus_pool
                if wanted in {t['name'] for t in
                              content_of(s).get('tags') or []}
                or wanted not in TAGS][:20]

    def trends(self):
        return kitchenmod.ServerTrendContext(
            trending_hashtags={'coffee': 1.0, 'art': 1.0},
            trending_status_ids={self.pool[3]['id'], self.pool[9]['id']})

    def following_page(self, max_id):
        ordered = sorted(self.follows)
        start = int(max_id or 0)
        page = ordered[start:start + 3]
        nxt = str(start + 3) if start + 3 < len(ordered) else None
        return [{'id': a} for a in page], nxt


def cook(world, recipe, case, pages=3, count=40):
    engine = FeedEngine(
        account_key='case#%d' % case, self_account_id=ME,
        fetch_scope=world.fetch_scope, fetch_hashtag=world.fetch_hashtag,
        search_statuses=world.search, fetch_trends=world.trends,
        fetch_following_page=world.following_page)
    engine.init_preload()
    pantrymod._mlt_cache.clear()
    results = []
    counts = {}
    cursor = None
    try:
        for _page in range(pages):
            result = engine.process_feed_sync(
                recipe, max_id=cursor, count=count, author_counts=counts)
            results.append(result)
            counts = result.author_counts
            cursor = result.scope_oldest_id
            if not result.has_more or not cursor:
                break
    finally:
        engine.shutdown()
    return results


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
    results = cook(world, recipe, case)
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
    for result in results:
        items = result.items
        chronological = recipe.kitchen.base_sort == 'chronological'
        if chronological:
            keys = [key_of(i) for i in items]
            ordered = non_increasing(keys)
            name = 'order newest first'
        else:
            scores = [result.score_map[i['id']] for i in items]
            ordered = non_increasing([round(s, 9) for s in scores])
            name = 'order most engaging'
        if not ordered:
            fail(name, seed, 'page out of order')
        count_seen(name, len(items))
        if focus:
            count_seen('order with focus', len(items))
            if not ordered:
                fail('order with focus', seed, 'focus changed the order')
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
            hide = min(math.floor(mod.percentile_threshold * n / 100), n - 1)
            info = result.kitchen_meta['heat']
            if (info['before'], info['after']) != (n, n - hide):
                fail('heat', seed, 'kept %d of %d, expected %d'
                     % (info['after'], info['before'], n - hide))
            if hide:
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
             'heat'):
    bad = problems.get(name, [])
    check('promise: %s (%d posts checked)%s'
          % (name, seen.get(name, 0),
             ' -- ' + '; '.join(bad[:3]) if bad else ''),
          not bad and seen.get(name, 0) > 50)
check('no duplicate posts across pages' + ' -- %s' % problems.get(
    'no duplicates', ''), 'no duplicates' not in problems)
check('the seeded focus cases bind included hashtags (%d posts)'
      % seen.get('include hashtags with focus', 0),
      seen.get('include hashtags with focus', 0) > 0)



sys.exit(1 if failures or problems else 0)
