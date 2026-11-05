# SPDX-License-Identifier: GPL-3.0-or-later
"""Announcement endpoints (audit rows AN-1..AN-3), declared once and sent
through `api.core`.

The reaction name is percent-encoded here, with nothing left unescaped, so
a custom emoji shortcode or a Unicode emoji is one path segment; the UI
passes the name as it has it. A 5.0.0 server may answer 400 when reactions
are not supported: the caller reports that as any other error (audit AN-3).

`min_version` is None throughout: the audit records no gate in the app for
these, so the release is in ``mastodon_version``.
"""

import urllib.parse

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/announcements/'


def _declare(name, method, path, scope, anchor, note=None):
    return core.declare('announcements.' + name, method, path, scope, None,
                        DOCS + anchor, note=note, mastodon_version='3.1.0')


INDEX = _declare('index', 'GET', '/announcements', None, '#get',
                 note='needs a user token, but no scope. Takes no '
                      'parameters: the controller returns every published '
                      'announcement (`Announcement.published.chronological`, '
                      'identical at main and v4.3.0, v4.4.0, v4.5.0), and '
                      'each carries its own `read` flag. The app used to '
                      'send `with_dismissed=true`, which nothing reads '
                      '(audit 5f)')
DISMISS = _declare('dismiss', 'POST', '/announcements/%s/dismiss',
                   'write:accounts', '#dismiss')
ADD_REACTION = _declare('add_reaction', 'PUT',
                        '/announcements/%s/reactions/%s',
                        'write:favourites', '#put-reactions',
                        note='arguments are the announcement id, then the '
                             'reaction name')
REMOVE_REACTION = _declare('remove_reaction', 'DELETE',
                           '/announcements/%s/reactions/%s',
                           'write:favourites', '#delete-reactions',
                           note='arguments are the announcement id, then '
                                'the reaction name')

# Reads a paged view takes by path.
LISTS = {'index': INDEX}


def list_path(kind, *ids):
    """The path of the announcements list for the paged page."""
    return LISTS[kind].path % ids


def index_async(client, params=None, callback=None, cancellable=None):
    """GET /announcements. There is no parameter worth passing: the server
    lists every published announcement, read ones included, each with a
    ``read`` flag (``with_dismissed`` is ignored by the controller)."""
    return core.send(client, INDEX, params=params, callback=callback,
                     cancellable=cancellable)


def index_finish(client, result):
    return core.finish(client, result)


def dismiss_async(client, announcement_id, callback=None, cancellable=None):
    return core.send(client, DISMISS, announcement_id, callback=callback,
                     cancellable=cancellable)


def dismiss_finish(client, result):
    return core.finish(client, result)


def _quote(name):
    return urllib.parse.quote(name, safe='')


def add_reaction_async(client, announcement_id, name, callback=None,
                       cancellable=None):
    return core.send(client, ADD_REACTION, announcement_id, _quote(name),
                     callback=callback, cancellable=cancellable)


def add_reaction_finish(client, result):
    return core.finish(client, result)


def remove_reaction_async(client, announcement_id, name, callback=None,
                          cancellable=None):
    return core.send(client, REMOVE_REACTION, announcement_id, _quote(name),
                     callback=callback, cancellable=cancellable)


def remove_reaction_finish(client, result):
    return core.finish(client, result)
