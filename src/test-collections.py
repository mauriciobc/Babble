# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Collections helpers and link routing (no network).

Covers the pieces the Collections client guide leans on: the list
envelope, the per-role creation limit, consent through
``feature_approval``, the item states that count as members, the report
payload's ``collection_ids[]`` evidence, and Collection URL recognition.
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import pages  # noqa: E402
from src.fedicollections import (  # noqa: E402
    DEFAULT_LIMIT, _is_member, _items, can_invite, collection_limit)
from src.report import build_report_payload  # noqa: E402

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL', label)
    else:
        print('ok', label)


# --- The list envelope: the docs' own example spells the key
# "collections:", but the server sends "collections".

COLLECTION = {'id': '7', 'name': 'Nice accounts', 'item_count': 2,
              'items': [{'id': 'a', 'account_id': '1', 'state': 'accepted'}]}
check('a "collections" array is read',
      _items({'collections': [COLLECTION]}) == [COLLECTION])
check('the documented "collections:" spelling is tolerated',
      _items({'collections:': [COLLECTION]}) == [COLLECTION])
check('a bare array is read', _items([COLLECTION]) == [COLLECTION])
check('an empty or missing envelope is an empty list',
      _items({}) == [] and _items(None) == [])

# --- Creation limit: role.collection_limit, defaulting to 10.

check('the role limit is used',
      collection_limit({'role': {'collection_limit': 3}}) == 3)
check('a missing role falls back to the default',
      collection_limit({}) == DEFAULT_LIMIT)
check('a malformed limit falls back to the default',
      collection_limit({'role': {'collection_limit': 'lots'}}) == DEFAULT_LIMIT)

# --- Consent (Account#feature_approval.current_user): only automatic and
# manual may be added; denied/unknown/missing must not.

for value, expected in (('automatic', True), ('manual', True),
                        ('denied', False), ('unknown', False),
                        ('missing', False), (None, False)):
    account = {} if value is None else {'feature_approval':
                                        {'current_user': value}}
    check('current_user=%r is %s' % (value, 'invitable' if expected
                                     else 'not invitable'),
          can_invite(account) is expected)

# --- Item states: pending and accepted are members; rejected and
# revoked are not. A missing state is treated as a member.

for state, expected in (('accepted', True), ('pending', True),
                        ('rejected', False), ('revoked', False),
                        (None, True)):
    item = {} if state is None else {'state': state}
    check('state=%r is %s' % (state, 'shown' if expected else 'hidden'),
          _is_member(item) is expected)

# --- Report payload: Collections ride along as collection_ids[].

payload = build_report_payload('1', 'spam', collection_ids=['7'])
check('a reported collection is attached',
      payload['collection_ids[]'] == ['7'])
check('no collection means no key',
      'collection_ids[]' not in build_report_payload('1', 'spam'))
check('statuses and collections attach together',
      build_report_payload('1', 'spam', status_ids=['9'],
                           collection_ids=['7'])
      == {'account_id': '1', 'category': 'spam', 'status_ids[]': ['9'],
          'collection_ids[]': ['7']})
check('collection ids are de-duplicated and stringified',
      build_report_payload('1', 'spam', collection_ids=['7', 7, '7'])
      ['collection_ids[]'] == ['7'])

# --- Link routing: a Collection URL is recognized, the older shapes are
# unchanged.

check('a collection URL is recognized',
      pages.fedi_link_kind('https://example.test/collections/123')
      == 'collection')
check('a trailing slash is still a collection',
      pages.fedi_link_kind('https://example.test/collections/123/')
      == 'collection')
check('a post URL is still a post',
      pages.fedi_link_kind('https://example.test/@ana/112')
      == 'post')
check('a profile URL is still a profile',
      pages.fedi_link_kind('https://example.test/@ana') == 'profile')
check('a hashtag URL is still a tag',
      pages.fedi_link_kind('https://example.test/tags/rust') == 'tag')
check('an unrelated URL is not a fediverse link',
      pages.fedi_link_kind('https://example.test/about') is None)

if failures:
    print('%d failure(s)' % failures)
    sys.exit(1)
print('All collections checks passed.')
