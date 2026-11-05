# SPDX-License-Identifier: GPL-3.0-or-later
"""Poll endpoints (audit rows PL-1 and PL-2), declared once and sent through
`api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair, and the request
on the wire is the one the poll box sent before.

The vote is form data ``choices[]``, as the docs describe (PL-2, audit 5e).
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
                    note='form choices[] (index...), as the docs show '
                         '(audit PL-2, moved from JSON in 5e). Server: '
                         '`params.require(:choices)` then `Integer(choice)`, '
                         'so the strings of a form read like the integers '
                         'of JSON; an empty list is refused either way',
                    mastodon_version='2.8.0')


def get_async(client, poll_id, callback=None, cancellable=None):
    return core.send(client, GET, poll_id, callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def vote_async(client, poll_id, choices, callback=None, cancellable=None):
    """*choices* are the option indexes."""
    return core.send(client, VOTE, poll_id,
                     form={'choices[]': [str(c) for c in choices]},
                     callback=callback, cancellable=cancellable)


def vote_finish(client, result):
    return core.finish(client, result)
