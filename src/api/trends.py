# SPDX-License-Identifier: GPL-3.0-or-later
"""Trending posts (the status half of audit row TR-1), declared once and
sent through `api.core`. The trending tags are `tags.TRENDING`, which the
explore tab reads by path; the trending links are still on the explore
page.

`statuses_async(client, params=None, callback=None, cancellable=None)` /
`statuses_finish(client, result)`; the request on the wire is the one the
client sent before. `list_path('statuses')` is for a timeline source.
"""

from . import core

STATUSES = core.declare(
    'trends.statuses', 'GET', '/trends/statuses', None, None,
    'https://docs.joinmastodon.org/methods/trends/#statuses',
    note='public; Link paged (`offset`)', mastodon_version='3.5.0')

LISTS = {'statuses': STATUSES}


def list_path(kind, *ids):
    return LISTS[kind].path % ids


def statuses_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, STATUSES, params=params, callback=callback,
                     cancellable=cancellable)


def statuses_finish(client, result):
    return core.finish(client, result)
