# SPDX-License-Identifier: GPL-3.0-or-later

"""Fixtures for the Catch Up digest tests: statuses, cards and tile posts.

The digest layer takes plain dictionaries, so every fixture here builds
one by hand. Test-only; never installed.
"""

import datetime
import types

from src.catchup import digest as _digest, digest_layout as _layout

cd = types.SimpleNamespace(**{**vars(_digest), **vars(_layout)})

BASE = datetime.datetime(2026, 10, 13, 12, 0, tzinfo=datetime.timezone.utc)

def iso(minutes_ago=0):
    t = BASE - datetime.timedelta(minutes=minutes_ago)
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')

def account(n, acct=None):
    return {'id': str(n), 'acct': acct or 'user%d' % n,
            'display_name': 'User %d' % n}

def status(sid, author=1, minutes_ago=0, **kw):
    s = {'id': str(sid), 'uri': 'https://x.example/%s' % sid,
         'created_at': iso(minutes_ago), 'account': account(author),
         'content': '<p>post %s</p>' % sid, 'spoiler_text': '',
         'media_attachments': [], 'tags': [], 'reblog': None,
         'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0,
         'in_reply_to_id': None, 'in_reply_to_account_id': None,
         'card': None, 'poll': None, 'filtered': []}
    s.update(kw)
    return s

def boost(sid, booster, original, minutes_ago=0):
    return status(sid, booster, minutes_ago, reblog=original, content='')

def link_card(url, image='https://img.example/a.png'):
    return {'type': 'link', 'url': url, 'image': image, 'title': 't'}

def filter_hit(action, title='f'):
    return [{'filter': {'filter_action': action, 'title': title,
                        'context': ['home']}}]

def ranked_posts(n, with_media_every=4, long_every=5):
    """``n`` posts in display order with descending scores."""
    posts, scores = [], {}
    for i in range(n):
        media = []
        if with_media_every and i % with_media_every == 1:
            media = [{'type': 'image',
                      'meta': {'original': {'width': 1600, 'height': 900}}}]
        text = 'x' * (200 if long_every and i % long_every == 2 else 30)
        posts.append(status(1000 + i, author=i % 17,
                            content='<p>%s</p>' % text,
                            media_attachments=media))
        scores[posts[-1]['id']] = 1.0 - i / (n + 1)
    return posts, scores

def make_news(n, with_image=True):
    return [{'key': 'n%d' % i, 'url': 'https://e.com/%d' % i,
             'card': link_card('https://e.com/%d' % i,
                               image='x' if with_image and i % 3 != 2
                               else None),
             'trending': True, 'sharers': [], 'shared': 0}
            for i in range(n)]

def make_tags(n):
    out = []
    for i in range(n):
        history = [{'day': str(d), 'uses': str(5 if i % 2 == 0 else 0)}
                   for d in range(3)]
        out.append({'key': 't%d' % i, 'name': 't%d' % i,
                    'tag': {'name': 't%d' % i, 'history': history},
                    'trending': True, 'following': False,
                    'timeline_posts': 0, 'timeline_accounts': 0,
                    'accounts': [], 'buckets': None})
    return out

def occupancy(placed, columns):
    """cell -> index of the tile covering it; raises on overlap."""
    cells = {}
    for i, p in enumerate(placed):
        for r in range(p.row, p.row + p.rowspan):
            for c in range(p.col, p.col + p.colspan):
                if (c, r) in cells:
                    raise AssertionError('overlap at %r' % ((c, r),))
                cells[(c, r)] = i
    return cells
