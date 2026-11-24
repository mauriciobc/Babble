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

# --- input limits: refused before any decoding, in no time ---
import base64  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402

from src.feedchef import sharecodec  # noqa: E402

real_decode = base64.urlsafe_b64decode
decoded = []


def spy(*args, **kwargs):
    decoded.append(1)
    return real_decode(*args, **kwargs)


base64.urlsafe_b64decode = spy
try:
    huge_legacy = 'babble://feedchef?recipe=' + 'A' * (20 * 1024 * 1024)
    started = time.perf_counter()
    got = try_parse_feedchef_uri(huge_legacy)
    elapsed = (time.perf_counter() - started) * 1000
    print('ok - 20 MB legacy link refused in %.2f ms' % elapsed)
    check('20 MB legacy link gives None, nothing decoded',
          got is None and not decoded)
    check('20 MB legacy link refused in under 5 ms', elapsed < 5)
    try:
        parse_feedchef_uri(huge_legacy)
        check('parse raises ValueError for a long link', False)
    except ValueError as error:
        check('parse raises ValueError for a long link',
              'too long' in str(error))
    big_compact = 'babble://feedchef?r=2.' + 'A' * (
        sharecodec.MAX_LINK_CHARS + 1)
    check('oversized compact link gives None, nothing decoded',
          try_parse_feedchef_uri(big_compact) is None and not decoded)
    for name, func in (('legacy', sharecodec.decode_recipe_b64),
                       ('compact', sharecodec.decode_recipe_compact)):
        try:
            func('A' * (sharecodec.MAX_LINK_CHARS + 1))
            check('%s decoder refuses a long blob' % name, False)
        except ValueError:
            check('%s decoder refuses a long blob, nothing decoded' % name,
                  not decoded)
finally:
    base64.urlsafe_b64decode = real_decode
check('a 3 kB legacy recipe still reads',
      try_parse_feedchef_uri(
          'babble://feedchef?recipe=' + sharecodec.encode_recipe(
              default_recipe('x' * 3000))) is not None)

# 100,000-deep JSON inside an otherwise well-formed compact blob.
deep = '[' * 100000 + ']' * 100000
packer = zlib.compressobj(9, zlib.DEFLATED, -15)
blob = base64.urlsafe_b64encode(
    packer.compress(('{"a":%s}' % deep).encode()) + packer.flush()
).decode().rstrip('=')
started = time.perf_counter()
got = try_parse_feedchef_uri('babble://feedchef?r=2.' + blob)
print('ok - 100k-deep JSON gave %r in %.1f ms'
      % (got, (time.perf_counter() - started) * 1000))
check('deep nesting gives None', got is None)
# Small enough to inflate (under MAX_INFLATED), deep enough to recurse.
deep = '[' * 30000 + ']' * 30000
packer = zlib.compressobj(9, zlib.DEFLATED, -15)
blob = base64.urlsafe_b64encode(
    packer.compress(('{"a":%s}' % deep).encode()) + packer.flush()
).decode().rstrip('=')
check('inflated nesting reaches the JSON parser',
      len(deep) * 2 // 2 < sharecodec.MAX_INFLATED)
check('deep nesting that inflates gives None',
      try_parse_feedchef_uri('babble://feedchef?r=2.' + blob) is None)
real_loads = json.loads


def too_deep(*_args, **_kwargs):
    raise RecursionError


json.loads = too_deep
try:
    check('a RecursionError from the parser gives None',
          try_parse_feedchef_uri(uri) is None)
finally:
    json.loads = real_loads

real_unb64 = sharecodec._unb64


def out_of_memory(_text):
    raise MemoryError


sharecodec._unb64 = out_of_memory
try:
    check('a MemoryError from the decoder gives None',
          try_parse_feedchef_uri(uri) is None)
finally:
    sharecodec._unb64 = real_unb64
# Values of the wrong type, found by fuzzing every key of a recipe: each
# used to escape as AttributeError or OverflowError.
from src.feedchef.recipe import recipe_to_dict  # noqa: E402


def with_value(path, value):
    data = recipe_to_dict(default_recipe('fuzz'))
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return json.dumps(data).encode()


def compact_of(raw):
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.urlsafe_b64encode(
        packer.compress(raw) + packer.flush()).decode().rstrip('=')


escapes = [
    (('meta',), 1), (('meta',), 'x'), (('meta',), [1]), (('meta',), True),
    (('pantry',), True), (('pantry',), [1]),
    (('meta', 'version'), float('inf')),
    (('diet', 'hard_filters', 'min_content_length'), float('inf')),
    (('pantry', 'hashtags_include'), [1]),
    (('pantry', 'hashtags_exclude'), [1]),
]
for path, value in escapes:
    raw = with_value(path, value)
    for form, link in (
            ('legacy', 'babble://feedchef?recipe=' + base64.urlsafe_b64encode(
                raw).decode().rstrip('=')),
            ('compact', 'babble://feedchef?r=2.' + compact_of(raw))):
        try:
            got = try_parse_feedchef_uri(link)
            raised = None
        except BaseException as error:
            got, raised = 'x', error
        check('%s %s = %r: None, nothing escapes%s' % (
            form, '.'.join(path), value,
            '' if raised is None else ' (%s)' % type(raised).__name__),
            got is None and raised is None)
check('and the codec still works afterwards',
      try_parse_feedchef_uri(uri) is not None)

sys.exit(1 if failures else 0)
