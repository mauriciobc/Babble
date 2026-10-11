# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic Mastodon payloads for the timeline benchmark.

Every status is a pure function of its numeric id (plus the base URL and
the anchor timestamp), so the fixture server and the benchmark driver
produce identical data without sharing state.
"""

import datetime
import random
import struct
import zlib

ACCOUNTS = 64
EMOJI_CODES = ('blobcat', 'ferris', 'gopher', 'python', 'party', 'neko')
WORDS = ('the', 'gnome', 'desktop', 'release', 'notes', 'timeline', 'rust',
         'python', 'gtk', 'widget', 'memory', 'layout', 'scrolling', 'today',
         'coffee', 'weekend', 'federation', 'server', 'birds', 'garden',
         'photo', 'camera', 'music', 'library', 'patch', 'review', 'merge',
         'tests', 'build', 'flatpak', 'kernel', 'driver', 'linux', 'free')
LANGS = ('en', 'en', 'en', 'pt', 'de', 'fr', 'ja')
VISIBILITIES = ('public', 'public', 'public', 'unlisted', 'private')


def _account(base, n, rng):
    acct = 'user%d' % n
    if n % 3:
        acct += '@remote%d.example' % (n % 5)
    name = 'User %d' % n
    emojis = []
    if n % 7 == 0:
        name += ' :%s:' % EMOJI_CODES[n % len(EMOJI_CODES)]
        emojis.append(_emoji(base, EMOJI_CODES[n % len(EMOJI_CODES)]))
    return {
        'id': str(1000 + n),
        'username': 'user%d' % n,
        'acct': acct,
        'display_name': name,
        'url': '%s/@user%d' % (base, n),
        'avatar': '%s/img/avatar/%d.png' % (base, n),
        'avatar_static': '%s/img/avatar/%d.png' % (base, n),
        'bot': n % 17 == 0,
        'emojis': emojis,
        'followers_count': rng.randrange(5000),
        'following_count': rng.randrange(800),
        'statuses_count': rng.randrange(20000),
    }


def _emoji(base, code):
    url = '%s/img/emoji/%s.png' % (base, code)
    return {'shortcode': code, 'url': url, 'static_url': url,
            'visible_in_picker': True}


def _sentence(rng, n):
    return ' '.join(rng.choice(WORDS) for _ in range(n))


def _content(base, rng, sid):
    paragraphs = []
    mentions, tags, emojis = [], [], []
    for _p in range(rng.choice((1, 1, 2, 3, 5))):
        parts = [_sentence(rng, rng.randrange(6, 40))]
        if rng.random() < 0.3:
            m = rng.randrange(ACCOUNTS)
            mentions.append({'id': str(1000 + m), 'username': 'user%d' % m,
                             'acct': 'user%d' % m,
                             'url': '%s/@user%d' % (base, m)})
            parts.insert(0, '<span class="h-card"><a href="%s/@user%d" '
                            'class="u-url mention">@<span>user%d</span>'
                            '</a></span>' % (base, m, m))
        if rng.random() < 0.3:
            parts.append('<a href="https://example.org/articles/%d/'
                         'a-rather-long-path-that-gets-shortened" '
                         'rel="nofollow noopener" target="_blank">'
                         '<span class="invisible">https://</span>'
                         '<span class="ellipsis">example.org/articles/%d'
                         '</span><span class="invisible">/a-rather-long-'
                         'path</span></a>' % (sid, sid))
        if rng.random() < 0.25:
            code = rng.choice(EMOJI_CODES)
            emojis.append(_emoji(base, code))
            parts.append(':%s:' % code)
        if rng.random() < 0.35:
            for _ in range(rng.choice((1, 2, 3, 7))):
                tag = rng.choice(WORDS)
                tags.append({'name': tag, 'url': '%s/tags/%s' % (base, tag)})
                parts.append('<a href="%s/tags/%s" class="mention hashtag" '
                             'rel="tag">#<span>%s</span></a>'
                             % (base, tag, tag))
        if rng.random() < 0.15:
            parts.append('&amp; &lt;escaped&gt; &quot;text&quot;<br>'
                         'second line<br>third line')
        paragraphs.append('<p>%s</p>' % ' '.join(parts))
    return ''.join(paragraphs), mentions, tags, emojis


def _media(base, rng, sid):
    out = []
    for i in range(rng.choice((1, 1, 2, 3, 4))):
        kind = rng.choice(('image', 'image', 'image', 'gifv', 'video'))
        url = '%s/img/media/%d-%d.png' % (base, sid, i)
        out.append({
            'id': '%d%d' % (sid, i), 'type': kind, 'url': url,
            'preview_url': url, 'remote_url': None,
            'description': _sentence(rng, 8) if rng.random() < 0.7 else None,
            'blurhash': 'UBL_:rOpGG-oBUNG,qRj2so|=eE1w^n4S5NH',
            'meta': {'original': {'width': 640, 'height': 480,
                                  'aspect': 1.3333},
                     'small': {'width': 640, 'height': 480,
                               'aspect': 1.3333}},
        })
    return out


def status(base, sid, anchor):
    """The status with numeric id *sid*; *anchor* is the creation time of
    id 0, newer ids are newer posts."""
    rng = random.Random(sid * 7919 + 17)
    author = _account(base, rng.randrange(ACCOUNTS), rng)
    content, mentions, tags, emojis = _content(base, rng, sid)
    created = anchor + datetime.timedelta(minutes=sid % 100000)
    data = {
        'id': '%018d' % sid,
        'uri': '%s/users/x/statuses/%d' % (base, sid),
        'url': '%s/@x/%d' % (base, sid),
        'created_at': created.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'account': author,
        'content': content,
        'spoiler_text': _sentence(rng, 4) if rng.random() < 0.08 else '',
        'sensitive': rng.random() < 0.1,
        'visibility': rng.choice(VISIBILITIES),
        'language': rng.choice(LANGS),
        'replies_count': rng.randrange(40),
        'reblogs_count': rng.randrange(200),
        'favourites_count': rng.randrange(500),
        'quotes_count': rng.randrange(5),
        'favourited': rng.random() < 0.1,
        'reblogged': rng.random() < 0.05,
        'bookmarked': rng.random() < 0.05,
        'mentions': mentions,
        'tags': tags,
        'emojis': emojis,
        'media_attachments': [],
        'card': None,
        'poll': None,
        'in_reply_to_id': None,
        'in_reply_to_account_id': None,
        'edited_at': None,
        'quote_approval': {'automatic': ['public'], 'manual': [],
                           'current_user': 'automatic'},
    }
    roll = rng.random()
    if roll < 0.35:
        data['media_attachments'] = _media(base, rng, sid)
    elif roll < 0.45:
        data['poll'] = {
            'id': str(sid), 'expires_at': None, 'expired': rng.random() < 0.5,
            'multiple': rng.random() < 0.3, 'votes_count': 30,
            'voters_count': 30, 'voted': False, 'own_votes': [],
            'options': [{'title': _sentence(rng, 3), 'votes_count': v}
                        for v in (10, 12, 8)],
            'emojis': []}
    elif roll < 0.6:
        data['card'] = {
            'url': 'https://example.org/card/%d' % sid,
            'title': _sentence(rng, 6), 'description': _sentence(rng, 20),
            'type': 'link', 'provider_name': 'Example',
            'image': '%s/img/card/%d.png' % (base, sid),
            'width': 400, 'height': 200}
    if rng.random() < 0.2:
        data['in_reply_to_id'] = '%018d' % max(1, sid - 3)
        data['in_reply_to_account_id'] = author['id'] \
            if rng.random() < 0.5 else str(1000 + rng.randrange(ACCOUNTS))
    if rng.random() < 0.05:
        data['edited_at'] = data['created_at']
    if rng.random() < 0.2:
        # A boost: the wrapper is by someone else and carries the post.
        booster = _account(base, rng.randrange(ACCOUNTS), rng)
        inner = dict(data, id='%018d' % (sid + 50_000_000))
        return dict(data, account=booster, reblog=inner, content='',
                    media_attachments=[], card=None, poll=None)
    return data


def png(width, height, seed):
    """A small deterministic RGB PNG."""
    rng = random.Random(seed)
    base = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    rows = []
    for y in range(height):
        row = bytearray(b'\x00')
        for x in range(width):
            row += bytes(((base[0] + x) & 255, (base[1] + y) & 255,
                          (base[2] + x + y) & 255))
        rows.append(bytes(row))
    raw = zlib.compress(b''.join(rows), 6)

    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body
                + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2,
                                         0, 0, 0))
            + chunk(b'IDAT', raw) + chunk(b'IEND', b''))
