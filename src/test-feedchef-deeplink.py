#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef deep link must not be treated as OAuth."""

import os
import sys

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

failures = 0


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef.recipe import default_recipe  # noqa: E402
from src.feedchef.sharecodec import (  # noqa: E402
    is_feedchef_uri, parse_feedchef_uri, recipe_share_uri,
    try_parse_feedchef_uri,
)

recipe = default_recipe('Share Me')
uri = recipe_share_uri(recipe)
check('feedchef uri detected', is_feedchef_uri(uri))
check('oauth callback not feedchef',
      not is_feedchef_uri('babble://oauth/callback?code=abc&state=1'))
check('elsewhere not feedchef',
      not is_feedchef_uri('babble://elsewhere/callback'))
check('path feedchef on other host rejected',
      not is_feedchef_uri('babble://evil.example/feedchef?recipe=x'))
check('oauth path feedchef rejected',
      not is_feedchef_uri('babble://oauth/feedchef?recipe=x'))
parsed = parse_feedchef_uri(uri)
check('round-trip id', parsed is not None and parsed.id == recipe.id)
check('round-trip name', parsed.meta.name == 'Share Me')
for bad in ('babble://feedchef?recipe=%%%', 'babble://feedchef?recipe=abc',
            'babble://feedchef?recipe=e30', 'babble://feedchef?recipe='):
    check('malformed link gives None: %s' % bad,
          try_parse_feedchef_uri(bad) is None)
check('valid link still parses',
      try_parse_feedchef_uri(uri) is not None)

sys.exit(1 if failures else 0)
