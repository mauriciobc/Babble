# SPDX-License-Identifier: GPL-3.0-or-later

"""The timeline test's harness: the checks, the fixtures and the settle loop.

The rendering scenario runs a real window, so it needs a way to run the
main loop for a fixed time, a failed-home client and the synthetic posts
the model checks append. Test-only; never installed.
"""

import time as time_module

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import GLib  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# A failed home request must stay an error. It used to fall back to the
# public timeline, which replaced the reader's home with posts from
# accounts they do not follow.
class _HomeFailureClient:
    def __init__(self):
        self.public_requests = 0

    def request_async(self, _method, path, *_args, **_kwargs):
        if path == '/timelines/public':
            self.public_requests += 1

    def request_finish(self, _result):
        raise GLib.Error('home failed', 'mastodon-http-error-quark', 500)


def _post(i):
    return {'id': '%05d' % i, 'content': '', 'created_at': '',
            'account': {'acct': 'a%d' % i, 'id': str(i)}}


def _boost(i, of):
    return dict(_post(i), account={'acct': 'booster', 'id': 'b'},
                reblog=_post(of))


def settle(seconds):
    """Run the main loop for *seconds*: frames land, then nothing moves."""
    end = time_module.monotonic() + seconds
    while time_module.monotonic() < end:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass
