#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Status, Account, Notification and Relationship stay dicts and share
one encoding of visibility, quote policy and the edit-merge keys."""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.entities import (Account, Notification, QUOTE_POLICIES,  # noqa: E402
                          Relationship, STATUS_MERGE_KEYS, Status,
                          VISIBILITIES, attachment_still_url, lift)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


account = {'id': '1', 'acct': 'ada', 'username': 'ada',
           'display_name': 'Ada'}
reblog = {'id': '2', 'content': '<p>inner</p>', 'created_at': 't',
          'account': dict(account), 'visibility': 'unlisted'}
status = {'id': '3', 'content': '<p>outer</p>', 'created_at': 't',
          'account': dict(account), 'reblog': reblog,
          'visibility': 'public',
          'quote_approval': {'automatic': ['followers']}}
note = {'id': '9', 'type': 'favourite', 'created_at': 't',
        'account': dict(account), 'status': dict(status)}
relation = {'id': '1', 'following': True, 'followed_by': False,
            'blocking': False, 'muting': False, 'endorsed': True}

lifted = lift(status)
check('lift returns a Status and does not retype the original',
      isinstance(lifted, Status) and isinstance(lifted, dict)
      and type(status) is dict)
check('the nested account is an Account',
      isinstance(lifted['account'], Account))
check('the boosted post is a Status', isinstance(lifted['reblog'], Status))
check('named fields read the same keys',
      lifted.id == '3' and lifted.account.acct == 'ada'
      and lifted.visibility == 'public'
      and lifted.quote_policy() == 'followers')
check('lifting twice returns the same object', lift(lifted) is lifted)

note = lift(note)
check('a notification lifts its status',
      isinstance(note, Notification) and isinstance(note.status, Status)
      and note.type == 'favourite')
relation = lift(relation)
check('a relationship reads its flags',
      isinstance(relation, Relationship) and relation.following
      and relation.endorsed and not relation.followed_by)

search = {'statuses': [dict(id='4', content='x', created_at='t',
                            account=dict(account))],
          'accounts': [dict(account)]}
search = lift(search)
check('a search result lifts both lists',
      isinstance(search['statuses'][0], Status)
      and isinstance(search['accounts'][0], Account))

check('visibility codes are the four server names',
      VISIBILITIES == ('public', 'unlisted', 'private', 'direct'))
check('quote policies are public, followers, nobody',
      QUOTE_POLICIES == ('public', 'followers', 'nobody'))
check('merge keys cover an edit and a favourite',
      'content' in STATUS_MERGE_KEYS and 'favourited' in STATUS_MERGE_KEYS
      and 'media_attachments' in STATUS_MERGE_KEYS)
check('an image decodes from its url',
      attachment_still_url({'type': 'image', 'url': 'u', 'preview_url': 'p'})
      == 'u')
check('a video decodes from its preview',
      attachment_still_url({'type': 'video', 'url': 'u', 'preview_url': 'p'})
      == 'p')
check('a non-dict is left alone', lift('nope') == 'nope')
check('an unknown object is left a plain dict',
      type(lift({'error': 'x'})) is dict)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
