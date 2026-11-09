# SPDX-License-Identifier: GPL-3.0-or-later

"""Bluesky data helpers: URL forms, the quote a Mastodon card carries, and
the pieces of an AppView post a read-only thread page shows.

Standard library only, and no widgets: everything here works without a
display and without a network.  Nothing in this module contacts Bluesky;
``bluesky.py`` does that, and only for the thread page."""

import re
import urllib.parse

BSKY_HOSTS = ('bsky.app', 'www.bsky.app', 'staging.bsky.app')
BRIDGE_HOSTS = ('bsky.brid.gy', 'fed.brid.gy')
BRIDGE_ACCT_SUFFIX = '@bsky.brid.gy'
POST_COLLECTION = 'app.bsky.feed.post'

_DID = r'did:[a-z]+:[A-Za-z0-9._:%-]+'
_HANDLE = r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+' \
          r'[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
_ACTOR = r'(?:%s|%s)' % (_DID, _HANDLE)
_RKEY = r'[A-Za-z0-9._~:-]{1,512}'
_WEB_PATH = re.compile(r'^/profile/(%s)/post/(%s)/?$' % (_ACTOR, _RKEY))
_AT_URI = re.compile(r'^at://(%s)/%s/(%s)$' % (_ACTOR, re.escape(POST_COLLECTION),
                                              _RKEY))
_TITLE = re.compile(r'^(.*) \(@([^()\s]+)\)$', re.S)
_DID_RE = re.compile(r'^%s$' % _DID)
_DOTS = ('.', '..')

# A post-picture card is anything but the author's square avatar.
AVATAR_MAX_SIDE = 256

SENSITIVE_LABELS = ('porn', 'sexual', 'nudity', 'graphic-media')
WITHHELD_LABELS = ('!hide', '!no-unauthenticated')

FACET_LINK = 'app.bsky.richtext.facet#link'
FACET_MENTION = 'app.bsky.richtext.facet#mention'
FACET_TAG = 'app.bsky.richtext.facet#tag'


def _dicts(items):
    """The dict elements of *items*, which may be anything."""
    return [item for item in items if isinstance(item, dict)] \
        if isinstance(items, (list, tuple)) else []


def _text(value):
    return value if isinstance(value, str) else ''


def _is_web(uri):
    return isinstance(uri, str) and uri.lower().startswith(
        ('http://', 'https://'))


def is_did(actor):
    return bool(isinstance(actor, str) and _DID_RE.match(actor))


def web_url(actor, rkey):
    return 'https://bsky.app/profile/%s/post/%s' % (actor, rkey)


def at_uri(did, rkey):
    return 'at://%s/%s/%s' % (did, POST_COLLECTION, rkey)


def _ref(match):
    if match is None or match.group(2) in _DOTS:
        return None
    return match.group(1), match.group(2)


def post_ref(url):
    """``(actor, rkey)`` for a Bluesky post URL, else None.

    Accepts bsky.app (and staging) post pages, ``at://`` post URIs and the
    Bridgy Fed forms (``{bsky,fed}.brid.gy/r/<url>`` and
    ``bsky.brid.gy/convert/ap/at://…``).  A repost record is not a post.
    The actor is a DID or a handle."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith('at://'):
        return _ref(_AT_URI.match(url))
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https'):
        return None
    host = (parts.hostname or '').lower()
    if host in BSKY_HOSTS:
        return _ref(_WEB_PATH.match(urllib.parse.unquote(parts.path)))
    if host in BRIDGE_HOSTS:
        path = parts.path
        for prefix in ('/r/', '/convert/ap/'):
            if path.startswith(prefix):
                inner = url.split(prefix, 1)[1]
                if inner.startswith(('http://', 'https://', 'at://')):
                    return post_ref(inner)
    return None


def bridged_ref(status):
    """``(actor, rkey)`` for a post bridged from Bluesky by Bridgy Fed, else
    None: the author's ``acct`` ends in ``@bsky.brid.gy`` and the post's
    ``uri`` is the AT URI form (a repost record is rejected)."""
    if not isinstance(status, dict):
        return None
    account = status.get('account') or {}
    if not (account.get('acct') or '').lower().endswith(BRIDGE_ACCT_SUFFIX):
        return None
    return post_ref(status.get('uri') or '')


def _split_title(title, actor):
    match = _TITLE.match(title)
    if match and match.group(1).strip():
        return match.group(1).strip(), match.group(2)
    handle = '' if is_did(actor) else actor
    return title.strip() or handle, handle


def _side(value):
    return value if isinstance(value, int) \
        and not isinstance(value, bool) else 0


def card_post(card):
    """What a Mastodon preview card says about the Bluesky post it links
    to, or None when it is not one.  A dict with ``name``, ``handle``,
    ``text``, ``url``, ``blurhash`` and one of ``avatar`` or ``picture``
    (or neither): Mastodon re-hosts the og:image, so its shape is the
    signal; a square of at most 256 px is the author's avatar, anything
    else (or a size that is not known) is a picture of the post."""
    if not isinstance(card, dict):
        return None
    ref = post_ref(card.get('url') or '')
    if ref is None:
        return None
    name, handle = _split_title(_text(card.get('title')), ref[0])
    result = {'name': name, 'handle': handle,
              'text': _text(card.get('description')),
              'url': card.get('url'), 'actor': ref[0], 'rkey': ref[1],
              'blurhash': card.get('blurhash'),
              'avatar': None, 'picture': None}
    image = card.get('image')
    if image and isinstance(image, str):
        width, height = _side(card.get('width')), _side(card.get('height'))
        if width and width == height and width <= AVATAR_MAX_SIDE:
            result['avatar'] = image
        else:
            result['picture'] = image
    return result


# Thread page helpers

def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def segments(text, facets):
    """Text and link segments for ``InlineEmojiText``.  Facet offsets are
    UTF-8 byte offsets, so the text is sliced as bytes; a facet that does
    not fit, or overlaps an earlier one, is ignored.  A link keeps its text
    but gets an href only for http and https, a mention only for a real
    DID.  A newline becomes a ``br`` segment."""
    text = _text(text)
    data = text.encode('utf-8')
    marks = []
    for facet in _dicts(facets):
        index = facet.get('index')
        index = index if isinstance(index, dict) else {}
        start, end = index.get('byteStart'), index.get('byteEnd')
        if not _int(start) or not _int(end) or not 0 <= start < end \
                <= len(data):
            continue
        href = None
        for feature in _dicts(facet.get('features')):
            kind = feature.get('$type')
            if kind == FACET_LINK:
                href = feature.get('uri') if _is_web(feature.get('uri')) \
                    else None
            elif kind == FACET_MENTION and is_did(feature.get('did')):
                href = 'https://bsky.app/profile/%s' % feature['did']
            elif kind == FACET_TAG and feature.get('tag') \
                    and isinstance(feature['tag'], str):
                href = 'https://bsky.app/hashtag/%s' % urllib.parse.quote(
                    feature['tag'])
            if href:
                break
        # Kept without an href when unsafe: the slice is still the text.
        marks.append((start, end, href))
    marks.sort(key=lambda mark: mark[:2])
    out = []
    pos = 0

    def put(chunk, href):
        for number, line in enumerate(chunk.split('\n')):
            if number:
                out.append({'text': '\n', 'href': None, 'br': True})
            if line:
                out.append({'text': line, 'href': href, 'sp_before': False,
                            'sp_after': False})

    for start, end, href in marks:
        if start < pos:
            continue
        put(data[pos:start].decode('utf-8', 'replace'), None)
        put(data[start:end].decode('utf-8', 'replace'), href)
        pos = end
    put(data[pos:].decode('utf-8', 'replace'), None)
    return out


def _vals(items):
    return {label.get('val') for label in _dicts(items)
            if isinstance(label.get('val'), str)}


def moderation(post):
    """``{'sensitive': bool, 'withheld': bool}`` from the labels of a post
    or record view and of its author."""
    post = post if isinstance(post, dict) else {}
    author = post.get('author')
    values = _vals(post.get('labels')) | _vals(
        author.get('labels') if isinstance(author, dict) else None)
    return {'sensitive': bool(values & set(SENSITIVE_LABELS)),
            'withheld': bool(values & set(WITHHELD_LABELS))}


def embed_kind(embed):
    kind = embed.get('$type') if isinstance(embed, dict) else None
    return (kind if isinstance(kind, str) else '').split('#')[0]


def _media_embed(embed):
    """The images/video/external part of an embed, looking through
    ``recordWithMedia``."""
    if embed_kind(embed) == 'app.bsky.embed.recordWithMedia':
        media = embed.get('media')
        return media if isinstance(media, dict) else {}
    return embed if isinstance(embed, dict) else {}


def attachments(embed):
    """Mastodon-shaped image attachments for ``MediaBox``.  Video is not
    one: its playlist is HLS, so see :func:`video_poster`."""
    media = _media_embed(embed)
    out = []
    if embed_kind(media) == 'app.bsky.embed.images':
        for number, image in enumerate(_dicts(media.get('images'))):
            url = image.get('fullsize') or image.get('thumb')
            if not _is_web(url):
                continue
            ratio = image.get('aspectRatio')
            ratio = ratio if isinstance(ratio, dict) else {}
            width, height = ratio.get('width'), ratio.get('height')
            sized = _int(width) and _int(height) and width > 0 \
                and height > 0
            out.append({
                'id': 'bsky-%d' % number, 'type': 'image', 'url': url,
                'preview_url': image.get('thumb') or url,
                'description': _text(image.get('alt')),
                'meta': {'small': {'width': width, 'height': height,
                                   'aspect': width / height}}
                if sized else {}})
    return out


def video_poster(embed):
    """``{'poster', 'alt'}`` for a video embed, which is shown as a poster
    only, or None."""
    media = _media_embed(embed)
    if embed_kind(media) != 'app.bsky.embed.video':
        return None
    return {'poster': media.get('thumbnail'), 'alt': _text(media.get('alt'))}


def external(embed):
    """A preview-card dict (``CardPreview``) for an external embed.  A
    link that is not http or https keeps its text and loses its url."""
    media = _media_embed(embed)
    if embed_kind(media) != 'app.bsky.embed.external':
        return None
    ext = media.get('external')
    ext = ext if isinstance(ext, dict) else {}
    if not ext.get('uri'):
        return None
    return {'url': ext['uri'] if _is_web(ext['uri']) else '',
            'title': _text(ext.get('title')),
            'description': _text(ext.get('description')),
            'image': ext.get('thumb') if _is_web(ext.get('thumb')) else None,
            'type': 'link'}


def quoted(embed):
    """The quoted record view (author, value, embeds) of a ``record`` or
    ``recordWithMedia`` embed, or None when it is gone, blocked or not a
    post."""
    kind = embed_kind(embed)
    if kind == 'app.bsky.embed.record':
        record = embed.get('record')
    elif kind == 'app.bsky.embed.recordWithMedia':
        record = embed.get('record')
        record = record.get('record') if isinstance(record, dict) else None
    else:
        return None
    if not isinstance(record, dict) \
            or not str(record.get('$type', '')).endswith('#viewRecord'):
        return None
    return record


def quote_card_data(record):
    """The ``card_post``-shaped dict for a quoted record view.  A sensitive
    record has no picture."""
    record = record if isinstance(record, dict) else {}
    author = record.get('author')
    author = author if isinstance(author, dict) else {}
    value = record.get('value')
    value = value if isinstance(value, dict) else {}
    picture = None
    if not moderation(record)['sensitive']:
        embeds = record.get('embeds')
        for embed in embeds if isinstance(embeds, list) else ():
            found = attachments(embed)
            if found:
                picture = found[0]['preview_url']
                break
    ref = post_ref(record.get('uri') or '')
    avatar = author.get('avatar')
    return {'name': _text(author.get('displayName'))
            or _text(author.get('handle')),
            'handle': _text(author.get('handle')),
            'text': _text(value.get('text')),
            'url': web_url(_text(author.get('did')) or ref[0], ref[1])
            if ref else None,
            'avatar': avatar if _is_web(avatar) else None,
            'picture': picture, 'blurhash': None,
            'actor': ref[0] if ref else '', 'rkey': ref[1] if ref else ''}
