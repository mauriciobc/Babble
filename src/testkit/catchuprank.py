# SPDX-License-Identifier: GPL-3.0-or-later

"""The ranker's test namespace and the status/candidate fixtures.

``cr`` is the three rank modules flattened into one namespace, because the
port and its tests name the same helper from any of them. Test-only; never
installed.
"""

import datetime
import types
import unittest

from src.catchup import rank as _rank
from src.catchup import rank_model as _model
from src.catchup import rank_text as _text

cr = types.SimpleNamespace(**{
    **vars(_text), **vars(_model), **vars(_rank)})

ACTION_KEYS = list(cr.X_WEIGHTS)

class Base(unittest.TestCase):
    def assertClose(self, a, b, places=10):
        self.assertAlmostEqual(a, b, places=places)

HOUR = 3_600_000

NOW = datetime.datetime(2026, 8, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)

cr = types.SimpleNamespace(**{
    **vars(_text), **vars(_model), **vars(_rank)})

def ctx(signals=None, **overrides):
    """``ctx()`` of the TS tests; ``signals`` feeds Elk's default resolver."""
    overrides.setdefault('viewer_languages', ['en'])
    if signals is not None:
        overrides.setdefault('affinity',
                             cr.signals_affinity_resolver(signals))
        overrides.setdefault('not_interested_ids',
                             set(signals.not_interested))
    return cr.RankingContext(now=NOW, **overrides)

def elk_ctx(**overrides):
    """A context with Elk's 48 h AgeFilter cut on."""
    params = dict(overrides.pop('params', None) or {})
    params['max_post_age_ms'] = cr.ELK_MAX_POST_AGE_MS
    return ctx(params=params, **overrides)

def final(candidate, c=None):
    return cr.rank_candidates([candidate], c or ctx())[0].score

def ids(candidates):
    return [c.status['id'] for c in candidates]

def iso_ago(age_ms, now=NOW):
    moment = now - datetime.timedelta(milliseconds=age_ms)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (
        moment.microsecond // 1000)

def make_candidate(in_network=True, boost_of=None, **options):
    status = make_status(**options)
    if boost_of is not None:
        status['content'] = ''
        status['reblog'] = make_status(**boost_of)
    return cr.Candidate(status=status, in_network=in_network)

def make_status(id='s1', author_id='a1', acct='alice', age_ms=HOUR,
                favourites=0, reblogs=0, replies=0,
                content='<p>hello world</p>', language='en',
                followers=200, following=200, statuses=500, bot=False,
                verified=False, note='<p>bio</p>', in_reply_to_id=None,
                in_reply_to_account_id=None, spoiler_text='',
                sensitive=False, tags=(), card=False, card_image=False,
                images=0, video_duration_secs=None, poll=False,
                video_type='video', now=NOW):
    """A Mastodon Status dict, the shape of ``makeStatus`` in Elk's tests."""
    media = [{'id': 'm%d' % i, 'type': 'image', 'preview_url': '',
              'meta': {'original': {'width': 100, 'height': 100,
                                    'size': '100x100', 'aspect': 1}}}
             for i in range(images)]
    if video_duration_secs is not None:
        media.append({'id': 'v0', 'type': video_type, 'preview_url': '',
                      'meta': {'original': {
                          'width': 100, 'height': 100, 'frame_rate': '30',
                          'duration': video_duration_secs,
                          'bitrate': 1000, 'aspect': 1}}})
    return {
        'id': id,
        'created_at': iso_ago(age_ms, now),
        'content': content,
        'language': language,
        'favourites_count': favourites,
        'reblogs_count': reblogs,
        'replies_count': replies,
        'in_reply_to_id': in_reply_to_id,
        'in_reply_to_account_id': in_reply_to_account_id,
        'spoiler_text': spoiler_text,
        'sensitive': sensitive,
        'media_attachments': media,
        'tags': [{'name': name, 'url': ''} for name in tags],
        'card': ({'url': 'https://example.com/card',
                  'image': 'https://img' if card_image else None}
                 if card else None),
        'poll': {'id': 'p1'} if poll else None,
        'reblog': None,
        'account': {
            'id': author_id, 'acct': acct, 'username': acct.split('@')[0],
            'note': note, 'bot': bot, 'followers_count': followers,
            'following_count': following, 'statuses_count': statuses,
            'fields': ([{'name': 'web', 'value': '',
                         'verified_at': '2024-01-01T00:00:00Z'}]
                       if verified else []),
        },
    }

def only_weights(**overrides):
    """Zero every weight, so a test can isolate one head."""
    return {**dict.fromkeys(ACTION_KEYS, 0), **overrides}

def pre_scored(raw_score, **options):
    candidate = make_candidate(**options)
    candidate.raw_score = raw_score
    return candidate

def raw(candidate, c=None):
    return cr.score_candidate(candidate, c or ctx()).raw_score
