#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.mastodon import (REST_RETRY_MAX, rest_backoff_seconds,  # noqa: E402
                          rest_should_retry)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


check('502 is retriable', rest_should_retry(502))
check('503 is retriable', rest_should_retry(503))
check('404 is not retriable', not rest_should_retry(404))
check('429 is not retriable', not rest_should_retry(429))
check('transport errors retriable', rest_should_retry(None, transport_error=True))
check('backoff grows', rest_backoff_seconds(2) >= rest_backoff_seconds(0))
check('retry cap is positive', REST_RETRY_MAX >= 1)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
