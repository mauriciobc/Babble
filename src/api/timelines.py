# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline endpoints (audit rows TL-1..TL-4 and TL-6; TL-5, the list
timeline, is `lists.TIMELINE`), declared once and sent through `api.core`.

The timeline pages take a path and page through it themselves, so the
accessor `list_path(kind, *ids)` is the main way in; `home_async` and
`public_async` are the two `<name>_async(client, params=None,
callback=None, cancellable=None)` / `<name>_finish(client, result)` pairs
the timeline page and Catch Up send directly. The request on the wire is the
one the pages sent before: same verb, path and params, which the caller
builds (`local`, `max_id`, `limit`, ...).

Not used, from the docs: the ``exclude_*`` filters of the home timeline
(API 12).

`min_version` is None throughout: the one gate the app has (the link
timeline, `capabilities.LINK_TIMELINE_API_VERSION`) lives with its caller,
so the release is in ``mastodon_version`` and nothing here changes what the
app asks of an instance.
"""

import urllib.parse

from . import core, lists

DOCS = 'https://docs.joinmastodon.org/methods/'


def _declare(name, path, scope, docs, note=None, version=None):
    return core.declare('timelines.' + name, 'GET', path, scope, None,
                        DOCS + docs, note=note, mastodon_version=version)


HOME = _declare('home', '/timelines/home', 'read:statuses',
                'timelines/#home', note='exclude_* (API 12) is not sent',
                version='0.0.0')
PUBLIC = _declare('public', '/timelines/public', None, 'timelines/#public',
                  note='public, or `read:statuses`; `local` and `remote` '
                       'narrow it; 4.5 can require a login',
                  version='0.0.0')
TAG = _declare('tag', '/timelines/tag/%s', None, 'timelines/#tag',
               note='public; `any[]`, `all[]` and `none[]` need 2.7.0; '
                    'the name is percent-encoded by `list_path`',
               version='0.0.0')
LINK = _declare('link', '/timelines/link', None, 'timelines/#link',
                note='public; `url`; the caller gates on '
                     '`capabilities.LINK_TIMELINE_API_VERSION`',
                version='4.3.0')
FAVOURITES = _declare('favourites', '/favourites', 'read:favourites',
                      'favourites/#get',
                      note='paged by the Link header, not by status ids',
                      version='0.0.0')
BOOKMARKS = _declare('bookmarks', '/bookmarks', 'read:bookmarks',
                     'bookmarks/#get',
                     note='paged by the Link header, not by status ids',
                     version='3.1.0')

# Reads a timeline source takes by path: `list_path('home')`,
# `list_path('tag', name)`, `list_path('list', list_id)`.
LISTS = {'home': HOME, 'public': PUBLIC, 'tag': TAG, 'link': LINK,
         'favourites': FAVOURITES, 'bookmarks': BOOKMARKS,
         'list': lists.TIMELINE}

# The kinds whose cursor is the Link header and not a status id.
LINK_PAGED = ('favourites', 'bookmarks')


def list_path(kind, *ids):
    """The path of a timeline read for a source that takes a path rather
    than sending a call; *ids* fill the placeholders (a tag name, which is
    percent-encoded here and so is one path segment, or a list id)."""
    if kind == 'tag':
        ids = tuple(urllib.parse.quote(i, safe='') for i in ids)
    return LISTS[kind].path % ids


def home_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, HOME, params=params, callback=callback,
                     cancellable=cancellable)


def home_finish(client, result):
    return core.finish(client, result)


def public_async(client, params=None, callback=None, cancellable=None):
    return core.send(client, PUBLIC, params=params, callback=callback,
                     cancellable=cancellable)


def public_finish(client, result):
    return core.finish(client, result)
