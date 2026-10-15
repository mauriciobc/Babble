# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the version gates, with no network.

They can tell you `api_versions` exists. They cannot tell you that
GoToSocial's OAuth scopes behave differently, that Pixelfed's API is
partial, or what "200 with an empty array" means on any of them. These
cover the one mechanism Mastodon itself defines: the
`api_versions.mastodon` integer in GET /api/v2/instance, and how a
client decides from it without conflating "older than 4.3", "not
Mastodon" and "just an int that looks older than it is".
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.capabilities import COLLECTIONS_API_VERSION  # noqa: E402
from src.mastodon import MastodonClient  # noqa: E402

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def client_with(instance_info):
    """A client that already holds *instance_info*, no server call."""
    client = MastodonClient('https://example.social')
    client.instance_info = instance_info
    return client


# --- What the integer reports, and what a missing key reports -----------

check('api version 12 reads as 12',
      client_with({'api_versions': {'mastodon': 12}}
                  ).mastodon_api_version() == 12)
check('api version reads through a string the instance sent anyway',
      client_with({'api_versions': {'mastodon': '9'}}
                  ).mastodon_api_version() == 9)
check('no api_versions key says nothing, not zero',
      client_with({}).mastodon_api_version() is None)
check('no instance_info yet says nothing',
      client_with(None).mastodon_api_version() is None)
check('a version the server did not give an integer for is unknown',
      client_with({'api_versions': {'mastodon': '12-and-a-half'}}
                  ).mastodon_api_version() is None)

# --- The tri-state, so a caller does not re-derive it everywhere --------

known_10 = client_with({'api_versions': {'mastodon': 10}})
known_9 = client_with({'api_versions': {'mastodon': 9}})
unknown = client_with({'api_versions': {}})

check('current Mastodon supports the Collections gate',
      known_10.is_supported(COLLECTIONS_API_VERSION) is True)
check('one version too early is caught',
      known_9.is_supported(COLLECTIONS_API_VERSION) is False)
check('a server that walks in with the key empty and says nothing '
      'is unproven, distinct from a clear no',
      unknown.mastodon_api_version() is None)
check('and the gate refuses an unproven server by the policy its caller '
      'stated, not by comparing a guessed 0',
      unknown.is_supported(COLLECTIONS_API_VERSION) is False)
check('the caller states its policy for an unproven server',
      unknown.is_supported(COLLECTIONS_API_VERSION,
                           when_unknown=True) is True)
check('no instance info at all is the same unproven answer',
      client_with(None).is_supported(COLLECTIONS_API_VERSION) is False)

# --- A support could have been read as 0, which would have hidden the
# feature on every non-Mastodon server. That was the trap. ----------------

gtosocial = client_with({'version': '0.16.0rc3'})
check('GoToSocial answers nothing about api_versions: not old, not Mastodon',
      gtosocial.mastodon_api_version() is None)
check('and its Collections gate refuses an unproven server without '
      'guessing 0',
      gtosocial.is_supported(COLLECTIONS_API_VERSION) is False)

# --- These are named so a future reader can be told what they mean ------

check('the version the Collections feature needs is 10',
      COLLECTIONS_API_VERSION == 10)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all capability checks passed')
