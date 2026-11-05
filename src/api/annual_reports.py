# SPDX-License-Identifier: GPL-3.0-or-later
"""Annual reports, the data behind Wrapped (audit rows OT-5 and OT-6),
declared once and sent through `api.core`.

`get_async(client, year=None, ...)` reads the latest report, or the one of
*year*; `state_async` and `generate_async` take a year. All are
`<name>_async` / `<name>_finish` pairs, and the request on the wire is the
one Wrapped sent before.

Carried over from the audit, not fixed here (phase 5): `state` and
`generate` are Mastodon 4.6.0 (API 8) endpoints the app does not gate on;
Wrapped treats a 404 as "no report" (OT-6).

`min_version` is None throughout, so the release is in ``mastodon_version``.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/annual_reports/'

INDEX = core.declare('annual_reports.index', 'GET', '/annual_reports',
                     'read:accounts', None, DOCS + '#get',
                     mastodon_version='4.3.0')
GET = core.declare('annual_reports.get', 'GET', '/annual_reports/%d',
                   'read:accounts', None, DOCS + '#get-one',
                   mastodon_version='4.4.0')
STATE = core.declare('annual_reports.state', 'GET',
                     '/annual_reports/%d/state', 'read:accounts', None,
                     DOCS + '#state',
                     note='not version-gated by the caller (audit OT-6)',
                     mastodon_version='4.6.0')
GENERATE = core.declare('annual_reports.generate', 'POST',
                        '/annual_reports/%d/generate', 'write:accounts',
                        None, DOCS + '#generate',
                        note='not version-gated by the caller (audit OT-6)',
                        mastodon_version='4.6.0')


def get_async(client, year=None, callback=None, cancellable=None):
    """The latest report, or the one of *year*."""
    if year:
        return core.send(client, GET, year, callback=callback,
                         cancellable=cancellable)
    return core.send(client, INDEX, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def state_async(client, year, callback=None, cancellable=None):
    return core.send(client, STATE, year, callback=callback,
                     cancellable=cancellable)


def state_finish(client, result):
    return core.finish(client, result)


def generate_async(client, year, callback=None, cancellable=None):
    return core.send(client, GENERATE, year, callback=callback,
                     cancellable=cancellable)


def generate_finish(client, result):
    return core.finish(client, result)
