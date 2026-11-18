#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""``composeschedule.scheduled_content``: what it accepts, what it refuses.

Characterization of the validator that shapes a Mastodon scheduled-status
payload into composer fields. It needs no display.

Some cases pin current behaviour that nobody designed; they are marked
"pinned:". They keep a refactor honest and do not endorse the behaviour:
change them deliberately, with the code.
"""
import copy
import os
import sys

os.environ['GSETTINGS_BACKEND'] = 'memory'
srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, root)
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib
from src.composeschedule import scheduled_content

failures = []
MAX = 4


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


def payload(**params):
    return {'id': '7', 'params': params}


def rejects(name, scheduled):
    try:
        scheduled_content(scheduled, max_poll_options=MAX)
    except GLib.Error:
        check('rejects ' + name, True)
    except Exception as e:
        check('rejects %s (raised %r)' % (name, e), False)
    else:
        check('rejects ' + name, False)


def accepts(name, scheduled, expected):
    before = copy.deepcopy(scheduled)
    try:
        got = scheduled_content(scheduled, max_poll_options=MAX)
    except Exception as e:
        check('accepts %s (raised %r)' % (name, e), False)
        return
    check('accepts %s (got %r)' % (name, got) if got != expected
          else 'accepts ' + name, got == expected)
    check(name + ' leaves its input alone', scheduled == before)


DEFAULTS = dict(
    text='', spoiler_text='', visibility='public', language=None,
    in_reply_to_id=None, quoted_status_id=None, sensitive=None,
    quote_approval_policy=None, media_ids=[], poll=None)


def expect(**changes):
    return dict(DEFAULTS, **changes)


# Envelope
for name, value in (('a list', []), ('None', None), ('a string', 'x'),
                    ('an int', 3)):
    rejects('scheduled that is ' + name, value)
rejects('a missing id', {'params': {}})
rejects('an empty id', {'id': '', 'params': {}})
rejects('an int id', {'id': 7, 'params': {}})
rejects('a None id', {'id': None, 'params': {}})
rejects('params that is a list', {'id': '7', 'params': []})
rejects('params that is a string', {'id': '7', 'params': 'x'})

# Text fields
rejects('int text', payload(text=1))
rejects('list text', payload(text=['a']))
rejects('int spoiler_text', payload(spoiler_text=1))
rejects('bool spoiler_text', payload(spoiler_text=False))
rejects('unknown visibility', payload(visibility='friends'))
rejects('int visibility', payload(visibility=1))
rejects('list visibility', payload(visibility=['public']))
rejects('int language', payload(language=1))
rejects('list language', payload(language=['en']))

# Ids
rejects('empty in_reply_to_id', payload(in_reply_to_id=''))
rejects('zero in_reply_to_id', payload(in_reply_to_id=0))
rejects('negative in_reply_to_id', payload(in_reply_to_id=-5))
rejects('bool in_reply_to_id', payload(in_reply_to_id=True))
rejects('float in_reply_to_id', payload(in_reply_to_id=1.0))
rejects('empty quoted_status_id', payload(quoted_status_id=''))
# pinned: only in_reply_to_id turns a positive int into a string.
rejects('int quoted_status_id', payload(quoted_status_id=5))
rejects('bool quoted_status_id', payload(quoted_status_id=True))

# Flags
rejects('int sensitive', payload(sensitive=1))
rejects('string sensitive', payload(sensitive='true'))
rejects('unknown quote_approval_policy',
        payload(quote_approval_policy='everyone'))
rejects('int quote_approval_policy', payload(quote_approval_policy=1))

# Media
rejects('attachments that is a dict',
        {'id': '7', 'params': {}, 'media_attachments': {}})
rejects('attachments that is a string',
        {'id': '7', 'params': {}, 'media_attachments': 'a'})
rejects('an attachment that is a string',
        {'id': '7', 'params': {}, 'media_attachments': ['a']})
rejects('an attachment without id',
        {'id': '7', 'params': {}, 'media_attachments': [{}]})
rejects('an attachment with empty id',
        {'id': '7', 'params': {}, 'media_attachments': [{'id': ''}]})
rejects('an attachment with int id',
        {'id': '7', 'params': {}, 'media_attachments': [{'id': 1}]})
rejects('media_ids that is a string', payload(media_ids='a'))
rejects('media_ids that is a dict', payload(media_ids={}))
rejects('a media_ids entry that is empty', payload(media_ids=['a', '']))
rejects('a media_ids entry that is an int', payload(media_ids=[1]))
# pinned: media_ids are validated even when the attachments win.
rejects('bad media_ids beside good attachments',
        {'id': '7', 'params': {'media_ids': [1]},
         'media_attachments': [{'id': 'a'}]})
rejects('bad attachments beside good media_ids',
        {'id': '7', 'params': {'media_ids': ['a']},
         'media_attachments': [{'id': ''}]})

# Poll
GOOD = {'options': ['a', 'b'], 'expires_in': 300}
rejects('a poll that is a list', payload(poll=[]))
rejects('a poll that is a string', payload(poll='x'))
rejects('a poll without options', payload(poll={'expires_in': 300}))
rejects('poll options that is a string',
        payload(poll={'options': 'ab', 'expires_in': 300}))
rejects('a one-option poll',
        payload(poll={'options': ['a'], 'expires_in': 300}))
rejects('a poll with no options',
        payload(poll={'options': [], 'expires_in': 300}))
rejects('a poll with too many options',
        payload(poll={'options': list('abcde'), 'expires_in': 300}))
rejects('a poll with a blank option',
        payload(poll={'options': ['a', '  '], 'expires_in': 300}))
rejects('a poll with an empty option',
        payload(poll={'options': ['a', ''], 'expires_in': 300}))
rejects('a poll with an int option',
        payload(poll={'options': ['a', 1], 'expires_in': 300}))
rejects('a poll with duplicate options',
        payload(poll={'options': ['a', 'a'], 'expires_in': 300}))
rejects('a poll with duplicates after stripping',
        payload(poll={'options': ['a', ' a '], 'expires_in': 300}))
rejects('a poll without expires_in', payload(poll={'options': ['a', 'b']}))
rejects('a poll with bool expires_in',
        payload(poll={'options': ['a', 'b'], 'expires_in': True}))
rejects('a poll with zero expires_in',
        payload(poll={'options': ['a', 'b'], 'expires_in': 0}))
rejects('a poll with negative expires_in',
        payload(poll={'options': ['a', 'b'], 'expires_in': -1}))
rejects('a poll with string expires_in',
        payload(poll={'options': ['a', 'b'], 'expires_in': '300'}))
rejects('a poll with float expires_in',
        payload(poll={'options': ['a', 'b'], 'expires_in': 300.0}))
rejects('a poll with int multiple', payload(poll=dict(GOOD, multiple=1)))
rejects('a poll with string multiple',
        payload(poll=dict(GOOD, multiple='yes')))
rejects('a poll with int hide_totals',
        payload(poll=dict(GOOD, hide_totals=0)))
# pinned: an explicit None poll flag is refused, although a missing one
# means False (every other field treats None as its default).
rejects('a poll with None multiple', payload(poll=dict(GOOD, multiple=None)))

# Acceptances
accepts('no params at all', {'id': '7'}, expect())
accepts('params None', {'id': '7', 'params': None}, expect())
accepts('empty params', payload(), expect())
accepts('None values as defaults',
        payload(text=None, spoiler_text=None, visibility=None, language=None,
                in_reply_to_id=None, quoted_status_id=None, sensitive=None,
                quote_approval_policy=None, media_ids=None, poll=None),
        expect())
accepts('attachments None', {'id': '7', 'params': {},
                             'media_attachments': None}, expect())
accepts('a positive int in_reply_to_id',
        payload(in_reply_to_id=123), expect(in_reply_to_id='123'))
accepts('a string in_reply_to_id',
        payload(in_reply_to_id='123'), expect(in_reply_to_id='123'))
accepts('a string quoted_status_id',
        payload(quoted_status_id='9'), expect(quoted_status_id='9'))
for v in ('public', 'unlisted', 'private', 'direct'):
    accepts('visibility ' + v, payload(visibility=v), expect(visibility=v))
for p in ('public', 'followers', 'nobody'):
    accepts('quote policy ' + p, payload(quote_approval_policy=p),
            expect(quote_approval_policy=p))
accepts('sensitive True', payload(sensitive=True), expect(sensitive=True))
accepts('sensitive False', payload(sensitive=False), expect(sensitive=False))
accepts('empty strings kept',
        payload(text='', spoiler_text='', language=''),
        expect(language=''))
accepts('every key set', {
    'id': '7',
    'params': {
        'text': 'hello', 'spoiler_text': 'cw', 'visibility': 'unlisted',
        'language': 'pt', 'in_reply_to_id': 12, 'quoted_status_id': '34',
        'sensitive': True, 'quote_approval_policy': 'followers',
        'media_ids': ['m1'],
        'poll': {'options': ['a', 'b'], 'expires_in': 600,
                 'multiple': True, 'hide_totals': True},
    },
}, expect(text='hello', spoiler_text='cw', visibility='unlisted',
          language='pt', in_reply_to_id='12', quoted_status_id='34',
          sensitive=True, quote_approval_policy='followers',
          media_ids=['m1'],
          poll={'options': ['a', 'b'], 'expires_in': 600,
                'multiple': True, 'hide_totals': True}))

# Media
accepts('attachments win over media_ids',
        {'id': '7', 'params': {'media_ids': ['x']},
         'media_attachments': [{'id': 'a1', 'url': 'u'}, {'id': 'a2'}]},
        expect(media_ids=['a1', 'a2']))
accepts('media_ids without attachments',
        {'id': '7', 'params': {'media_ids': ['x', 'y']},
         'media_attachments': []}, expect(media_ids=['x', 'y']))
accepts('media_ids with attachments absent',
        payload(media_ids=['x']), expect(media_ids=['x']))
accepts('attachments alone',
        {'id': '7', 'params': {}, 'media_attachments': [{'id': 'a1'}]},
        expect(media_ids=['a1']))

# Poll
accepts('a minimal poll', payload(poll=dict(GOOD)),
        expect(poll={'options': ['a', 'b'], 'expires_in': 300,
                     'multiple': False, 'hide_totals': False}))
accepts('a poll at the option limit',
        payload(poll={'options': list('abcd'), 'expires_in': 1}),
        expect(poll={'options': list('abcd'), 'expires_in': 1,
                     'multiple': False, 'hide_totals': False}))
# pinned: options are checked stripped but returned as sent.
accepts('a poll keeps option whitespace',
        payload(poll={'options': [' a ', 'b'], 'expires_in': 5,
                      'multiple': True}),
        expect(poll={'options': [' a ', 'b'], 'expires_in': 5,
                     'multiple': True, 'hide_totals': False}))
accepts('poll extra keys are dropped',
        payload(poll=dict(GOOD, hide_totals=True, votes_count=3)),
        expect(poll={'options': ['a', 'b'], 'expires_in': 300,
                     'multiple': False, 'hide_totals': True}))

# The returned lists are copies, not the payload's own
src = {'id': '7', 'params': {'media_ids': ['x'],
                             'poll': {'options': ['a', 'b'],
                                      'expires_in': 3}}}
out = scheduled_content(src, max_poll_options=MAX)
out['media_ids'].append('z')
out['poll']['options'].append('c')
check('returned lists are copies',
      src['params']['media_ids'] == ['x']
      and src['params']['poll']['options'] == ['a', 'b'])

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
