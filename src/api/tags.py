# SPDX-License-Identifier: GPL-3.0-or-later
"""Hashtag endpoints (audit rows TG-1..TG-5 and the tag half of OT-4),
declared once and sent through `api.core`.

The tag name is percent-encoded here, with nothing left unescaped, so a
name with a space or a slash is one path segment; the UI passes the name as
it has it. Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair, and the request
on the wire is the one the hashtag pages sent before: same verb, path,
form and params.

`min_version` is None throughout: the audit records no gate in the app for
these (the followed-tags page is simply absent on an older server), so the
release is in ``mastodon_version`` and nothing here changes what the app
asks of an instance.

Not used, from the docs: ``POST /tags/:id/feature`` (4.4.0).
"""

import urllib.parse

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/'


def _declare(name, method, path, scope, docs, note=None, version=None):
    return core.declare('tags.' + name, method, path, scope, None, docs,
                        note=note, mastodon_version=version)


GET = _declare('get', 'GET', '/tags/%s', None, DOCS + 'tags/#get',
               note='public; with a user token it also says whether the '
                    'account follows the tag',
               version='4.0.0')
FOLLOW = _declare('follow', 'POST', '/tags/%s/follow', 'write:follows',
                  DOCS + 'tags/#follow', version='4.0.0')
UNFOLLOW = _declare('unfollow', 'POST', '/tags/%s/unfollow', 'write:follows',
                    DOCS + 'tags/#unfollow', version='4.0.0')
FOLLOWED = _declare('followed', 'GET', '/followed_tags', 'read:follows',
                    DOCS + 'followed_tags/#get',
                    note='limit 100 or 200 (the docs: default 100, max '
                         '200); Link paged',
                    version='4.0.0')
FEATURED = _declare('featured', 'GET', '/featured_tags', 'read:accounts',
                    DOCS + 'featured_tags/#get', version='3.0.0')
FEATURED_SUGGESTIONS = _declare(
    'featured_suggestions', 'GET', '/featured_tags/suggestions',
    'read:accounts', DOCS + 'featured_tags/#suggestions',
    note='the tags the account used lately', version='3.0.0')
FEATURE = _declare('feature', 'POST', '/featured_tags', 'write:accounts',
                   DOCS + 'featured_tags/#feature',
                   note='form name; the body carries the tag name, not an '
                        ':id (audit item 7)',
                   version='3.0.0')
UNFEATURE = _declare('unfeature', 'DELETE', '/featured_tags/%s',
                     'write:accounts', DOCS + 'featured_tags/#unfeature',
                     note=':id is the id of the featured tag (from '
                          'featured), not a tag name (audit item 7)',
                     version='3.0.0')
TRENDING = _declare('trending', 'GET', '/trends/tags', None,
                    DOCS + 'trends/#tags',
                    note='public; limit 20 is the most it returns',
                    version='3.0.0')

# The two verbs on one tag, picked by `action_async`.
ACTIONS = {'follow': FOLLOW, 'unfollow': UNFOLLOW}

# Reads a paged view takes by path: `list_path('followed')` for the
# followed-hashtags page, `list_path('trending')` for the explore tab.
LISTS = {'followed': FOLLOWED, 'trending': TRENDING}


def list_path(kind, *ids):
    """The path of a paged read (``followed``, ``trending``) for the views
    that take a path rather than send a call."""
    return LISTS[kind].path % ids


def _quote(name):
    return urllib.parse.quote(name, safe='')


def get_async(client, name, callback=None, cancellable=None):
    return core.send(client, GET, _quote(name), callback=callback,
                     cancellable=cancellable)


def get_finish(client, result):
    return core.finish(client, result)


def action_async(client, name, operation, callback=None, cancellable=None):
    """Send *operation* (``follow`` or ``unfollow``, a key of ``ACTIONS``)
    on a tag; the answer is the tag. An unknown operation raises
    KeyError."""
    return core.send(client, ACTIONS[operation], _quote(name),
                     callback=callback, cancellable=cancellable)


def action_finish(client, result):
    return core.finish(client, result)


def followed_async(client, params=None, callback=None, cancellable=None):
    """One page of the followed tags; *params* ``{'limit': 200}`` or a
    ``Link`` cursor. Finish with :func:`followed_finish` or, to follow the
    cursor, ``core.finish_paged``."""
    return core.send(client, FOLLOWED, params=params, callback=callback,
                     cancellable=cancellable)


def followed_finish(client, result):
    return core.finish(client, result)


def trending_async(client, params=None, callback=None, cancellable=None):
    """The trending tags; *params* ``{'limit': 20}``."""
    return core.send(client, TRENDING, params=params, callback=callback,
                     cancellable=cancellable)


def trending_finish(client, result):
    return core.finish(client, result)


def featured_async(client, callback=None, cancellable=None):
    return core.send(client, FEATURED, callback=callback,
                     cancellable=cancellable)


def featured_finish(client, result):
    return core.finish(client, result)


def featured_suggestions_async(client, callback=None, cancellable=None):
    return core.send(client, FEATURED_SUGGESTIONS, callback=callback,
                     cancellable=cancellable)


def featured_suggestions_finish(client, result):
    return core.finish(client, result)


def feature_async(client, name, callback=None, cancellable=None):
    return core.send(client, FEATURE, form={'name': name},
                     callback=callback, cancellable=cancellable)


def feature_finish(client, result):
    return core.finish(client, result)


def unfeature_async(client, featured_id, callback=None, cancellable=None):
    return core.send(client, UNFEATURE, featured_id, callback=callback,
                     cancellable=cancellable)


def unfeature_finish(client, result):
    return core.finish(client, result)
