# SPDX-License-Identifier: GPL-3.0-or-later

"""The fixture worlds: candidate sets, filler posts and diversity feeds.

Every fixture here is a plain structure the ranker takes, so the tests can
compare against the TypeScript's numbers without touching the network or a
display. Test-only; never installed.
"""

from testkit.catchuprank import HOUR, NOW, cr, make_status  # noqa: E402


FILLER_SUBJECTS = ('harbour', 'lantern', 'quarry', 'meadow', 'compass',
                   'thimble', 'lattice', 'burrow')

FIXTURE_IN_NETWORK = {'1001': True, '1002': False, '1003': True,
                      '1004': True, '1005': True, '1006': False,
                      '1007': True}

TOKEN_TEXTS = [
    'The quick brown fox https://x.example/a @alice@fed.example #tagged '
    'jumped',
    'Grüße aus München',
    'Tiếng Việt rất đẹp',
    '今日の選挙結果について議論しています',
    'a \U0001D41A \U0001F600 x ünï नमस्ते दुनिया',
    'het is een van de bir ve bu için',
]




def clustered_feed():
    """Five authors, five topics, scored so a score sort clusters them."""
    authors = ['alice', 'bob', 'carol', 'dave', 'erin']
    topics = ['rustlang', 'baking', 'birdwatching', 'typography', 'trains']
    bodies = [
        'borrow checker lifetimes async runtime executor scheduling',
        'sourdough starter hydration crumb oven spring rye levain',
        'heron kingfisher canal reeds binoculars migration ringing',
        'kerning ligature grotesque serif metrics hinting variable font',
        'catenary bogie signalling timetable gauge sleeper depot',
    ]
    return [div_candidate(
        1 - (a * 8 + i) * 0.01, id='%d-%d' % (a, i), author_id=authors[a],
        tags=[topics[a]], text='%s note %d' % (bodies[a], i))
        for a in range(5) for i in range(8)]


def div_status(id, author_id='author-1', text='', tags=(), language='en',
               media=(), links=(), spoiler='', in_reply_to=None,
               reblog_of=None):
    """``makeStatus`` of Elk's diversity tests (a ``StatusSpec``)."""
    link_html = ' '.join('<a href="%s">%s</a>' % (h, h) for h in links)
    tag_html = ' '.join(
        '<a href="https://example.social/tags/%s" class="hashtag">#%s</a>'
        % (t, t) for t in tags)
    return {
        'id': id,
        'created_at': '2024-01-01T00:00:00.000Z',
        'content': '<p>%s %s %s</p>' % (text, tag_html, link_html),
        'spoiler_text': spoiler,
        'language': language,
        'in_reply_to_id': in_reply_to,
        'in_reply_to_account_id': None,
        'account': {'id': author_id, 'acct': 'user' + author_id},
        'tags': [{'name': t, 'url': ''} for t in tags],
        'media_attachments': [{'id': '%s-m%d' % (id, i), 'type': t}
                              for i, t in enumerate(media)],
        'reblog': reblog_of,
    }

def div_candidate(score=1, **spec):
    return cr.Candidate(div_status(**spec), True, score=score)

def filler(count):
    """Posts so IDF has a corpus to measure against."""
    return [div_candidate(
        id='filler-%d' % i, author_id='filler-author-%d' % i,
        tags=['filler-tag-%d' % i],
        text='The %s and the %s are here with a few more words so that the '
             'common function words appear in every single post of this '
             'corpus, sample %d.' % (
                 FILLER_SUBJECTS[i % 8], FILLER_SUBJECTS[(i + 3) % 8], i))
        for i in range(count)]

def similarity_in_corpus(probes, filler_count=40):
    vectors = cr.embed_candidates_sparse([*probes, *filler(filler_count)])
    return [[cr.sparse_cosine_similarity(vectors[i], vectors[j])
             for j in range(len(probes))] for i in range(len(probes))]

def author_of(candidate):
    return cr.content_status(candidate)['account']['id']

def topic_of(candidate):
    tags = cr.content_status(candidate)['tags']
    return tags[0]['name'] if tags else ''

def adjacent_runs(items, key, n):
    window = items[:n]
    return sum(1 for i in range(1, len(window))
               if key(window[i]) == key(window[i - 1]))

def mean_score(items, n):
    window = items[:n]
    return sum(c.score or 0 for c in window) / len(window)

def by_score(items):
    return sorted(items, key=lambda c: -(c.score or 0))

def tail_rho(reranked, ranked_by_score, start, end):
    """Spearman correlation of a slice of the output with score rank."""
    score_rank = {c.status['id']: i for i, c in enumerate(ranked_by_score)}
    observed = [score_rank[c.status['id']] for c in reranked[start:end]]
    order = sorted(observed)
    n = len(observed)
    total = sum((i - order.index(observed[i])) ** 2 for i in range(n))
    return 1 - (6 * total) / (n * (n * n - 1))

DIV_FIXTURES = [
    (div_status('d1', 'alice', 'Shipping the caching layer today, latency '
                'down', tags=('backend',),
                links=('https://news.example/a/b/',)), 0.9),
    (div_status('d2', 'alice', 'Made sourdough focaccia with rosemary',
                tags=('baking',), media=('image',)), 0.8),
    (div_status('d3', 'carol', 'Shipping the caching layer today, latency '
                'down', tags=('backend', 'Perf'), language='de'), 0.85),
    (div_status('d4', 'dave', 'Grüße aus München \U0001F600 and '
                'http://x.y/z @bob@fed.example #tagged',
                in_reply_to='d1'), 0.7),
    (div_status('d5', 'erin', 'Tiếng Việt rất đẹp 今日の選挙結果',
                spoiler='nsfw? maybe', language='vi'), 0.6),
    (div_status('d6', 'frank', 'reply one', in_reply_to='d1'), 0.55),
    (div_status('d7', 'gina', 'reply two', in_reply_to='d6'), 0.5),
    (div_status('d8', 'hank', 'reply three', in_reply_to='d1'), 0.45),
    (div_status('d9', 'ivy', 'boosted wrapper', reblog_of=div_status(
        'd1', 'alice', 'Shipping the caching layer today, latency down',
        tags=('backend',), links=('https://news.example/a/b/',))), 0.4),
    (div_status('d10', 'jack', '', language=None), 0.35),
    (div_status('d11', 'kim', 'Rust borrow checker finally clicked',
                tags=('rustlang',),
                links=('https://www.crates.example/q?x=1',
                       'https://example.social/@zed')), 0.3),
    (div_status('d12', 'lee', 'Shipping the caching layer today, latency '
                'down', tags=('backend',)), 0.25),
]

def div_fixture_candidates():
    return [cr.Candidate(s, True, score=score) for s, score in DIV_FIXTURES]



def fixture_candidates():
    """Seven statuses (see GOLDEN) with their in-network flags."""
    return [cr.Candidate(s, FIXTURE_IN_NETWORK[s['id']])
            for s in make_fixture_statuses()]

def fixture_ctx(**params):
    return cr.RankingContext(
        now=NOW, viewer_languages=['en', 'de'], mutual_author_ids={'mut1'},
        viewer_follower_count=50, params=params or None)

def make_fixture_statuses():
    boosted = make_status(
        id='9003', author_id='a4', acct='dave@far.example',
        age_ms=30 * HOUR, favourites=300, reblogs=90, replies=10,
        tags=('news',), card=True, poll=True, content='<p>Big news?</p>')
    boost = make_status(id='1003', author_id='a3', acct='carol',
                        age_ms=HOUR // 2)
    boost['content'] = ''
    boost['reblog'] = boosted
    return [
        make_status(
            id='1001', author_id='a1', acct='alice', age_ms=3 * HOUR,
            favourites=12, reblogs=3, replies=2, images=2, tags=('art',),
            card=True, card_image=True,
            content='<p>Look at this? <a href="https://example.com/x">'
                    'link</a></p>'),
        make_status(
            id='1002', author_id='a2', acct='bob@remote.example',
            age_ms=7 * HOUR, favourites=140, reblogs=60, replies=25,
            language='de', followers=5200, following=150,
            video_duration_secs=30, verified=True, tags=('linux', 'foss'),
            spoiler_text='cw', sensitive=True,
            content='<p>Kernel news</p>'),
        boost,
        make_status(
            id='1004', author_id='a5', acct='botty', age_ms=HOUR // 2,
            followers=10, following=500, statuses=0, bot=True, note='',
            language='ja', in_reply_to_id='1001',
            in_reply_to_account_id='a5'),
        make_status(id='1005', author_id='mut1', acct='mutual',
                    age_ms=HOUR, content='<p>from a mutual</p>'),
        make_status(id='1006', author_id='a6', acct='eve@old.example',
                    age_ms=70 * HOUR, favourites=50, reblogs=5, replies=1),
        make_status(id='1007', author_id='a1', acct='alice',
                    age_ms=2 * HOUR, favourites=40, replies=8,
                    video_duration_secs=5, video_type='gifv'),
    ]





def varied_feed():
    nouns = ['harbour', 'lantern', 'quarry', 'meadow', 'compass', 'thimble',
             'lattice', 'burrow', 'cinder', 'marsh', 'anvil', 'orchard',
             'ferry', 'kettle', 'pylon', 'satchel', 'willow', 'grotto',
             'beacon', 'trellis']
    verbs = ['collapsed', 'flourished', 'drifted', 'echoed', 'hardened',
             'scattered', 'gleamed', 'settled', 'vanished', 'unfolded']
    return [div_candidate(
        1 / (1 + i * 0.05), id='v%d' % i, author_id='author-%d' % (i % 40),
        tags=['topic-%d' % (i % 25)],
        text='%s %s %s %s %s beside the %s' % (
            nouns[i % 20], verbs[i % 10], nouns[(i * 7) % 20],
            nouns[(i * 13) % 20], verbs[(i * 3) % 10],
            nouns[(i * 11) % 20])) for i in range(200)]
