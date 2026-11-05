# SPDX-License-Identifier: GPL-3.0-or-later
"""Report endpoint (audit row RP-1), declared once and sent through
`api.core`.

The form is built by ``report.build_report_payload`` (array fields use the
``name[]`` key, ``forward`` is the string ``true``/``false``); the request on
the wire is the one the report wizard sent before.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/reports/'

CREATE = core.declare(
    'reports.create', 'POST', '/reports', 'write:reports', None,
    DOCS + '#post',
    note='form account_id, category, status_ids[], rule_ids[], '
         'collection_ids[] (4.6.0), comment, forward (2.3.0); each is sent '
         'only when set',
    mastodon_version='1.1.0')


def create_async(client, form, callback=None, cancellable=None):
    return core.send(client, CREATE, form=form, callback=callback,
                     cancellable=cancellable)


def create_finish(client, result):
    return core.finish(client, result)
