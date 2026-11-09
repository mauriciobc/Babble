# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures for the Bluesky tests: the status a bridged quote post arrives
as (copied from mastodon.social) and an AppView thread.  Loaded by path,
not installed."""

import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

DID = 'did:plc:ubji6txf3gdzwztlt2zegzdi'
RKEY = '3mzv5qizm622q'
QUOTED_URL = ('https://bsky.app/profile/did:plc:6zulsu3flxsklsaoxdsx52h4/'
              'post/3mzuxm3z6nk24')

CARD = {
    'url': QUOTED_URL,
    'title': 'o que ta acontecendo aqui (@acordeitarde.bsky.social)',
    'description': 'será se essa espiã chinesa gosta de mim ou só está '
                   'sendo muito simpática',
    'type': 'link', 'provider_name': 'Bluesky Social',
    'width': 128, 'height': 128,
    'image': 'https://files.mastodon.social/cache/preview_cards/images/'
             '214/097/293/original/ba9629378a2eca5d.webp',
    'blurhash': 'UeEVBAt70NNGE1jZ%Lfk9aWB-oof-pWVIpoe',
}

STATUS = {
    'id': '116000000000000001',
    'uri': 'https://bsky.brid.gy/convert/ap/at://%s/app.bsky.feed.post/%s'
           % (DID, RKEY),
    'url': 'https://fed.brid.gy/r/https://bsky.app/profile/%s/post/%s'
           % (DID, RKEY),
    'created_at': '2026-11-08T12:00:00.000Z',
    'visibility': 'public', 'sensitive': False, 'spoiler_text': '',
    'language': 'pt', 'quote': None, 'poll': None,
    'media_attachments': [], 'mentions': [], 'tags': [], 'emojis': [],
    'reblogs_count': 0, 'favourites_count': 0, 'replies_count': 0,
    'account': {'id': '1', 'acct': 'crisvector.com@bsky.brid.gy',
                'username': 'crisvector.com', 'display_name': 'Cris Vector',
                'avatar': '', 'emojis': [], 'bot': False,
                'url': 'https://bsky.brid.gy/r/https://bsky.app/profile/'
                       + DID},
    'content': '<p>Aquela comunidade do Orkut: "Quero apenas seus segredos '
               'industriais, não estou te dando mole"<span '
               'class="quote-inline"><br><br>RE: <a href="%s" '
               'rel="nofollow noopener" target="_blank">%s</a></span></p>'
               % (QUOTED_URL, QUOTED_URL),
    'card': CARD,
}


def author(handle='alice.bsky.social', name='Alice'):
    return {'did': 'did:plc:' + handle.split('.')[0].ljust(24, 'a'),
            'handle': handle, 'displayName': name, 'avatar': '',
            'labels': []}


def post(text, handle='alice.bsky.social', name='Alice', rkey='3aaa',
         **extra):
    who = author(handle, name)
    data = {'uri': 'at://%s/app.bsky.feed.post/%s' % (who['did'], rkey),
            'author': who,
            'record': {'text': text, 'createdAt': '2026-11-08T12:00:00Z'},
            'replyCount': 2, 'repostCount': 1, 'likeCount': 1500,
            'quoteCount': 0, 'labels': [], 'indexedAt': '2026-11-08T12:00:00Z'}
    data.update(extra)
    return data


def node(post_view, parent=None, replies=()):
    return {'$type': 'app.bsky.feed.defs#threadViewPost', 'post': post_view,
            'parent': parent, 'replies': list(replies)}


NOT_FOUND = {'$type': 'app.bsky.feed.defs#notFoundPost',
             'uri': 'at://x/app.bsky.feed.post/y', 'notFound': True}


def thread():
    """A parent, the focus and nested replies, one reply not found."""
    root = node(post('Root post', rkey='3root'))
    deep = node(post('Deep reply', rkey='3deep'))
    reply = node(post('First reply', rkey='3rep1'), replies=[deep])
    return node(post('Focused post', rkey='3focus'), parent=root,
                replies=[reply, NOT_FOUND])
