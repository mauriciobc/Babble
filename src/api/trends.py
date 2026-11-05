# SPDX-License-Identifier: GPL-3.0-or-later
"""Trending posts and links (audit row OT-4 and the status half of TR-1),
declared once and sent through `api.core`. The trending tags are
`tags.TRENDING` (`tags.trending_async`, `tags.list_path('trending')`).

`statuses_async(client, params=None, callback=None, cancellable=None)` /
`statuses_finish(client, result)` and the same pair for `links`; the request
on the wire is the one the client and the explore page sent before.
`list_path('statuses')` / `list_path('links')` are for the views that page
through a path themselves.
"""

from . import core

STATUSES = core.declare(
    'trends.statuses', 'GET', '/trends/statuses', None, None,
    'https://docs.joinmastodon.org/methods/trends/#statuses',
    note='public; Link paged (`offset`)', mastodon_version='3.5.0')

LINKS = core.declare(
    'trends.links', 'GET', '/trends/links', None, None,
    'https://docs.joinmastodon.org/methods/trends/#links',
    note='public; limit 20 is the most it returns; Link paged',
    mastodon_version='3.5.0')

LISTS = {'statuses': STATUSES, 'links': LINKS}


def list_path(kind, *ids):
    return LISTS[kind].path % ids


def statuses_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, STATUSES, params=params, callback=callback,
                     cancellable=cancellable)


def statuses_finish(client, result):
    return core.finish(client, result)


def links_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, LINKS, params=params, callback=callback,
                     cancellable=cancellable)

