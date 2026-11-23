# SPDX-License-Identifier: GPL-3.0-or-later
"""A seeded world of realistic statuses and random recipes, cooked through
the real FeedEngine over fake fetch functions.

Shared by the fuzz of ``src/test-feedchef-contract.py`` (which checks each
promise against oracles of its own) and by the audit's tests and
self-test (neutrality of the audit, replay of every record it makes).
"""

from datetime import datetime, timedelta, timezone

from src.feedchef import kitchen as kitchenmod
from src.feedchef import pantry as pantrymod
from src.feedchef.engine import FeedEngine
from src.feedchef.recipe import Modifier, NetworkReachConfig, default_recipe

NOW = datetime.now(timezone.utc)


def _content_of(status):
    return status.get('reblog') or status


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
    (None, 'I really love this wonderful sunny morning, everything feels '
           'great and I am so happy about it'),
    (None, 'This is the worst terrible awful day, I hate everything about '
           'this horrible situation right now'),
    (None, 'Estou muito feliz hoje, o dia está maravilhoso e eu adorei '
           'passear com os meus amigos na praia'),
    (None, 'Un café bien largo para pensar en todo lo que pasó hoy y ayer'),
    ('en', 'Check https://example.com/some/long/path?x=1 for a long, long '
           'story about the café scene downtown that I enjoyed'),
]
WORDS = ['spam', 'crypto', 'terrible', 'café', 'weather']
# Words of the texts above, for a focus (More Like This) to look for.
FOCUS_WORDS = ['wonderful', 'weather', 'terrible', 'cafe', 'story', 'spam',
               'downtown', 'amazing', 'maravilhoso', 'check']
# The servers the accounts of the world are on (an account is on the same
# one every time, chosen by its id).
HOSTS = ['ex.ample', 'b.example', 'c.example']


def host_of(account_id):
    return HOSTS[sum(map(ord, account_id)) % len(HOSTS)]

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
        'account': {'id': author,
                    'url': 'https://%s/@%s' % (host_of(author), author)},
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
                        'url': 'https://%s/@%s' % (host_of(booster),
                                                   booster)},
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
                                 [0, 10, 25, 50, 60, 75, 90])))
    if rng.random() < 0.5:
        mods.append(Modifier(type='diversity',
                             max_per_cluster=rng.choice([1, 2, 3, 4]),
                             diversity_dimensions=rng.choice(
                                 [['author'], ['author'],
                                  ['author', 'instance'], ['instance']])))
    if rng.random() < 0.35:
        mods.append(Modifier(type='trending',
                             growth_threshold=rng.choice([0.1, 0.3, 1.0])))
    if rng.random() < 0.35:
        mods.append(Modifier(type='sentiment_boost',
                             sentiment_boost=rng.choice([0.2, 0.5, 1.0])))
    if rng.random() < 0.45:
        mods.append(Modifier(
            type='similarity_cluster', max_per_cluster=rng.choice([1, 2, 3]),
            cluster_strategy=rng.choice(
                ['best_score', 'most_recent', 'centroid', None])))
    rng.shuffle(mods)
    kit.modifiers = mods
    if rng.random() < 0.3:
        if rng.random() < 0.3:
            kit.active_focus_hashtags = [rng.choice(TAGS)]
        else:
            words = rng.sample(FOCUS_WORDS, rng.choice([1, 2, 3]))
            kit.active_focus_keywords = words
            if rng.random() < 0.5:
                kit.active_focus_weights = [
                    rng.choice([0.2, 0.5, 1.0, 0, -1]) for _ in words]
    return recipe


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
                              _content_of(s).get('tags') or []}
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


def cook(world, recipe, case, pages=3, count=40, audit=True, now=None,
         hide=None, variant=None):
    """Cook up to *pages* pages of *recipe* over *world* through the real
    engine. *audit* turns the audit record on or off; *now* (a datetime)
    pins the page clock, so two runs can be compared bit for bit. *variant*
    is what the A/B optimizer says of the cook (recorded, never read)."""
    engine = FeedEngine(
        account_key='case#%d' % case, self_account_id=ME,
        fetch_scope=world.fetch_scope, fetch_hashtag=world.fetch_hashtag,
        search_statuses=world.search, fetch_trends=world.trends,
        fetch_following_page=world.following_page)
    engine.audit = audit
    engine.search_stagger_ms = 0
    if now is not None:
        engine.clock = lambda: now
    engine.init_preload()
    pantrymod._mlt_cache.clear()
    results = []
    counts = {}
    instances = {}
    cursor = None
    try:
        for page in range(pages):
            result = engine.process_feed_sync(
                recipe, max_id=cursor, count=count, author_counts=counts,
                instance_counts=instances,
                cook_id='case%d' % case, page_index=page, hide=hide,
                variant=variant)
            results.append(result)
            counts = result.author_counts
            instances = result.instance_counts
            cursor = result.scope_oldest_id
            if not result.has_more or not cursor:
                break
    finally:
        engine.shutdown()
    return results
