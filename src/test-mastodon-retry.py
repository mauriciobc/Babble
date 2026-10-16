#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.mastodon import (IDEMPOTENT_METHODS, REST_RETRY_MAX,  # noqa: E402
                          rest_backoff_seconds, rest_should_retry)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('502 is retriable', rest_should_retry('GET', 502))
check('503 is retriable', rest_should_retry('GET', 503))
check('404 is not retriable', not rest_should_retry('GET', 404))
check('429 is not retriable', not rest_should_retry('GET', 429))
check('transport errors retriable',
      rest_should_retry('GET', None, transport_error=True))

# RFC 9110 9.2.2: a client SHOULD NOT retry a non-idempotent method
# automatically. A retried POST /statuses is a second status.
for method in ('POST', 'post', 'Post'):
    check('%s is never retried' % method, not rest_should_retry(
        method, 500))
    check('%s is not retried after a lost response' % method,
          not rest_should_retry(method, None, transport_error=True))
check('PUT is retriable', rest_should_retry('PUT', 503))
check('DELETE is retriable', rest_should_retry('DELETE', 500))
check('lowercase put is retriable', rest_should_retry('put', 500))
check('method decides, not the status',
      not rest_should_retry('POST', 200))

check('backoff grows', rest_backoff_seconds(2) >= rest_backoff_seconds(0))
check('retry cap is positive', REST_RETRY_MAX >= 1)
check('POST is not in the idempotent set',
      'POST' not in IDEMPOTENT_METHODS)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
