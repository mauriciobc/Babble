# SPDX-License-Identifier: GPL-3.0-or-later
"""Poll endpoints (audit rows PL-1 and PL-2), declared once and sent through
`api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair, and the request
on the wire is the one the poll box sent before.

Carried over from the audit, not fixed here (phase 5): the vote is sent as
JSON ``{"choices": [...]}``, the docs describe form data ``choices[]``
(PL-2, accepted as undocumented).
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/polls/'

GET = core.declare('polls.get', 'GET', '/polls/%s', None, None,
                   DOCS + '#get',
                   note='public when the parent status is public; '
                        'read:statuses with a user token for a private one',
                   mastodon_version='2.8.0')
VOTE = core.declare('polls.vote', 'POST', '/polls/%s/votes', 'write:statuses',
                    None, DOCS + '#vote',
                    note='DEVIATES (audit PL-2): JSON body '
                         '{choices: [index...]}, the docs say form data '
                         'choices[]; fixed in phase 5',
                    mastodon_version='2.8.0')


def get_async(client, poll_id, callback=None, cancellable=None):
    return core.send(client, GET, poll_id, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def vote_async(client, poll_id, choices, callback=None, cancellable=None):
    """*choices* are the option indexes."""
    return core.send(client, VOTE, poll_id, json_body={'choices': choices},
                     callback=callback, cancellable=cancellable)


def vote_finish(client, result):
    return core.finish(client, result)
