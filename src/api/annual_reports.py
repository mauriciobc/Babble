# SPDX-License-Identifier: GPL-3.0-or-later
"""Annual reports, the data behind Wrapped (audit rows OT-5 and OT-6),
declared once and sent through `api.core`.

`get_async(client, year=None, ...)` reads the latest report, or the one of
*year*; `state_async` and `generate_async` take a year. All are
`<name>_async` / `<name>_finish` pairs, and the request on the wire is the
one Wrapped sent before.

`state` and `generate` are Mastodon 4.6.0 (API 8) endpoints; Wrapped asks
``capabilities.ANNUAL_REPORT_STATE_API_VERSION`` before `state` (audit 5c)
and a 404 on the report itself is still "no report" (OT-6). The report
reads (4.3.0, 4.4.0) are not gated, so their `min_version` is None.

`min_version` is the number the caller gates on, written out here because
this package may import nothing but the standard library and `core`;
`api registry` holds it equal to the constant in `capabilities.py`.
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
                     '/annual_reports/%d/state', 'read:accounts', 8,
                     DOCS + '#state',
                     note='gated by the caller on API version 8',
                     mastodon_version='4.6.0')
GENERATE = core.declare('annual_reports.generate', 'POST',
                        '/annual_reports/%d/generate', 'write:accounts',
                        8, DOCS + '#generate',
                        note='gated by the caller on API version 8: the '
                             'Generate button only follows a /state answer',
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
