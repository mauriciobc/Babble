# SPDX-License-Identifier: GPL-3.0-or-later

"""Where a link clicked in post or profile text goes: a post, profile,
hashtag or Collection inside the app, an article in the web column, or
the browser.

What can take a link over before this routes it (a plugin that claims
its origin) and what shows a web link in the app (the web column) are
asked through :mod:`linkhandlers`; this module imports neither.
"""

import re
import urllib.parse

import gi

gi.require_version('GLib', '2.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GLib  # noqa: E402

from . import linkhandlers  # noqa: E402
from .api import search as api_search  # noqa: E402
from .appsettings import settings  # noqa: E402
from .pagenav import client_of, open_profile, open_status, push_page  # noqa: E402
from .statustext import is_web_link, split_url  # noqa: E402

TRACKING_PARAMS = re.compile(
    r'^(utm_[a-z_]+|fbclid|gclid|dclid|msclkid|mc_eid|mc_cid|igshid|'
    r'_hsenc|_hsmi|mkt_tok|oly_anon_id|oly_enc_id|rb_clickid|s_cid|'
    r'vero_id|wickedid|yclid|ref_src|ref_url|si)$', re.I)

_FEDI_PATH = re.compile(
    r'^/(@[^/]+(/\d+)?|users/[^/]+(/statuses/[^/]+)?|notice/[^/]+|'
    r'notes/[^/]+|objects/[^/]+|p/[^/]+/\d+|u/[^/]+|web/statuses/\d+|'
    r'web/@[^/]+(/\d+)?)/?$')
# The profile shapes among _FEDI_PATH; every other match is a post.
_PROFILE_PATH = re.compile(r'^/(?:web/)?(?:@[^/]+|users/[^/]+|u/[^/]+)/?$')
_TAG_PATH = re.compile(r'^/(?:tags|tag)/([^/?#]+)/?$')
# A Collection URL (Mastodon 4.6+): https://host/collections/:id
_COLLECTION_PATH = re.compile(r'^/collections/([^/?#]+)/?$')


def fedi_link_kind(uri):
    """``'post'``, ``'profile'``, ``'tag'`` or ``'collection'`` when
    *uri* is an http(s) link shaped like one on a fediverse server, else
    None."""
    if not is_web_link(uri):
        return None
    parts = split_url(uri)
    if parts is None or not parts.hostname:
        return None
    if _TAG_PATH.match(parts.path):
        return 'tag'
    if _COLLECTION_PATH.match(parts.path):
        return 'collection'
    if _PROFILE_PATH.match(parts.path):
        return 'profile'
    if _FEDI_PATH.match(parts.path):
        return 'post'
    return None


def is_fediverse_link(uri):
    """True when *uri* is an http(s) link to a post, profile, hashtag or
    Collection on a fediverse server, the links :func:`open_link` can
    resolve in-app."""
    return fedi_link_kind(uri) is not None


def strip_tracking(uri):
    parts = split_url(uri)
    if parts is None or not parts.query:
        return uri
    query = [(k, v) for k, v in urllib.parse.parse_qsl(
        parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunsplit(parts._replace(
        query=urllib.parse.urlencode(query)))


def launch_external(widget, uri):
    from .textemoji import launch_uri
    launch_uri(widget, uri)


def open_web(widget, uri):
    """Open an article or other web link: in the web column beside the
    timeline when ``open-links-in-app`` is on and WebKit is available,
    in the browser otherwise.

    For article and web links only. OAuth, the explicit "Open in
    Browser" actions and non-web schemes must keep using
    :func:`launch_external`, which never opens the column. A link a
    plugin claims goes to the plugin first (see ``pluginlinks``)."""
    if linkhandlers.claim(widget, uri):
        return
    if settings().get_boolean('open-links-in-app'):
        if linkhandlers.open_in_app(widget, uri):
            return
    launch_external(widget, uri)


def open_link(widget, uri, mentions=None, tag=None, resolve=False):
    """Route a link clicked in post or profile text.

    *tag*, when the renderer recognised an ``<a class="hashtag">`` and
    kept its displayed name, opens that hashtag's timeline directly:
    the status ``tags`` payload is normalized (casefolded, transliterated)
    and cannot be compared with the href, and a server that links tags
    elsewhere (``/dashboard/search/…``, ``/feeds/search?q=…#name``)
    would never match a URL shape. Mentions (matched against the status
    ``mentions`` payload) open the profile, Collection URLs open the
    Collection, other post/profile URLs are resolved through the
    instance's search when the ``aggressive-resolving`` setting is on, or
    always with *resolve*; everything else goes to the web column when
    ``open-links-in-app`` is on (see :func:`open_web`), else the browser,
    minus tracking parameters when ``strip-tracking`` is on."""
    if not is_web_link(uri):
        launch_external(widget, uri)  # refuses it, with a toast
        return
    if settings().get_boolean('strip-tracking'):
        uri = strip_tracking(uri)
    if linkhandlers.claim(widget, uri):
        return  # before any routing: a claimed /@user@host is the plugin's
    from . import blueskycard, bskytext
    bsky = bskytext.post_ref(uri) if blueskycard.enabled() else None
    if bsky is not None:
        blueskycard.open_post(widget, *bsky)  # a post, not a profile or tag
        return
    client = client_of(widget)
    if client is None:
        launch_external(widget, uri)
        return
    if tag:
        from .hashtags import push_hashtag
        push_hashtag(widget, client, tag)
        return
    for mention in mentions or ():
        if mention.get('url') == uri:
            open_profile(widget, client, acct=mention.get('acct'))
            return
    parts = split_url(uri)
    path_tag = _TAG_PATH.match(parts.path)
    if path_tag:
        # An anchor without the hashtag class, or a plain link to a tag
        # page. Both open the timeline in-app, as Tuba does.
        from .hashtags import push_hashtag
        push_hashtag(widget, client, urllib.parse.unquote(path_tag.group(1)))
        return
    if _COLLECTION_PATH.match(parts.path):
        # A Collection link (Mastodon 4.6+). Resolution goes through the
        # instance's search, which returns the local Collection entity
        # only on a server that supports Collections; otherwise the link
        # falls back to the browser.
        if resolve or settings().get_boolean('aggressive-resolving'):
            resolve_uri(widget, client, uri)
        else:
            launch_external(widget, uri)
        return
    if (resolve or settings().get_boolean('aggressive-resolving')) \
            and _FEDI_PATH.match(parts.path):
        resolve_uri(widget, client, uri)
        return
    open_web(widget, uri)


def open_collection(widget, client, collection):
    """Open a Collection in-app (Mastodon 4.6+). *collection* is a
    Collection entity; its ``id`` is server-local, as the entity came
    from this instance."""
    collection_id = collection.get('id')
    if not collection_id:
        launch_external(widget, collection.get('url') or '')
        return
    from .fedicollections import CollectionPage
    push_page(widget, collection.get('name') or _('Collection'),
              CollectionPage(client, collection_id, collection),
              tag='collection-%s' % collection_id)


def resolve_uri(widget, client, uri):
    """Look *uri* up with ``/api/v2/search?resolve=true`` and open the
    post, profile or collection it names; fall back to the browser."""

    def resolved(_client, result, _data=None):
        try:
            data = api_search.search_finish(client, result)
        except GLib.Error:
            launch_external(widget, uri)
            return
        if data.get('statuses'):
            open_status(widget, client, data['statuses'][0])
        elif data.get('accounts'):
            open_profile(widget, client, data['accounts'][0])
        elif data.get('collections'):
            open_collection(widget, client, data['collections'][0])
        else:
            launch_external(widget, uri)

    api_search.search_async(client, uri, resolve='true', limit=1,
                            callback=resolved)
