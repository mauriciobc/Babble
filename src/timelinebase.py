# SPDX-License-Identifier: GPL-3.0-or-later

import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _

from gi.repository import (GLib, Gio,  # noqa: E402
                           )

from . import filterrules  # noqa: E402
from .entities import (QUOTE_POLICIES as QUOTE_POLICY_CODES,  # noqa: E402
                       Status,
                       VISIBILITIES, attachment_still_url)
from . import pages  # noqa: E402
from . import statustext  # noqa: E402
from .statustext import (format_time, is_hashtag, parse_content,  # noqa: E402
                         shorten_url_text)

"""Shared timeline constants and the small helpers every seam uses."""

def id_key(value):
    """Sort key for Mastodon ids.

    Ids are strings "cast from an integer, but not guaranteed to be a
    number" (docs.joinmastodon.org/entities/Status), so a plain
    string comparison orders '99999999' after '100000001'. Numeric
    values sort numerically, anything else after them as text."""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


PAGE_SIZE = 20
AUTO_REFRESH_SCROLLED_TICKS = 2  # 30s when the user is scrolled away
# Screen readers hear about posts arriving at the top of a timeline at
# most this often per page; the counts in between add up.
NEW_POSTS_ANNOUNCE_INTERVAL_US = 10 * 1000 * 1000
COLUMN_WIDTH = pages.COLUMN_WIDTH
BOOST_DEDUPE_LIMIT = 100
GROUP_BOOSTS_MIN_PAGE = 11
# How many "does the viewer follow this author" answers one timeline page
# remembers. The reply filter and the followed-hashtag pass read the same
# table, so a timeline that has met an author once does not ask the server
# about them again on the next page. Bounded because a long session can
# scroll through thousands of authors.
FOLLOW_CACHE_LIMIT = 400

# Decoded-texture budgets. Avatars repeat across a timeline so they are
# cheap to keep; media are large, so far fewer stay resident.
AVATAR_CACHE_SIZE = 256
# Instances serve 400px avatars; the largest one shown is 96px. Decoded
# at full size, a full avatar cache held ~156 MB.
AVATAR_DECODE_SIZE = 96
MEDIA_TEXTURE_CACHE_SIZE = 48
# Gtk.ListView keeps up to ~200 list items alive per page whatever the
# scroll position, and each unmapped one holds its built StatusRow cards.
# Only this many unmapped shells, across all pages, keep theirs; the
# least recently unmapped drop them and rebuild if scrolled back to.
CARD_SHELLS_KEPT = 100

# Envelope shape for :meth:`mastodon.MastodonClient.request_finish_paged`.
_EMPTY_PAGED_ENVELOPE = GLib.Variant('s', '{"data": [], "link": ""}')


def decodable_attachment_url(attachment):
    """Still-frame URL Gdk can decode for a media attachment.

    The rule lives on :func:`entities.attachment_still_url`."""
    return attachment_still_url(attachment)
# Posts a timeline keeps. Streaming and refreshes only ever prepend, so
# an open timeline grew for the whole session; past this many, the
# oldest are dropped while the reader is at the top, and infinite scroll
# fetches them again.
TIMELINE_MAX_ITEMS = 400


DEFAULT_FLAGS = {
    'collapse-long-posts': True,
    'show-preview-cards': True,
    'show-interaction-counters': True,
    'reply-to-old-post-reminder': True,
    'copy-private-link-reminder': True,
    'boost-alt-text-reminder': True,
}
LONG_POST_CHARS = 700
LONG_POST_BREAKS = 14
OLD_POST_DAYS = 90
_schema_present = None


def _has_schema():
    """True when the application's GSettings schema is installed (it
    is not in bare widget tests, which then get the stock defaults)."""
    global _schema_present
    if _schema_present is None:
        source = Gio.SettingsSchemaSource.get_default()
        _schema_present = bool(
            source and source.lookup(pages.SCHEMA_ID, True))
    return _schema_present


def _flag(name):
    """A boolean application setting."""
    if not _has_schema():
        return DEFAULT_FLAGS.get(name, False)
    return pages.settings().get_boolean(name)


def ui_language():
    """Two-letter language of the user's locale ('pt' for pt_BR)."""
    for name in GLib.get_language_names():
        if name and name != 'C':
            return name.split('.')[0].split('@')[0].split('_')[0].lower()
    return 'en'


def absolute_time(iso_timestamp):
    """'9 Oct 2026, 10:47' (or '…, 10:47 AM' on a 12-hour desktop) in
    local time, '' when unparsable."""
    # GLib returns NULL for a string it cannot read, which PyGObject
    # surfaces as a TypeError from the constructor rather than as None --
    # so the guard has to catch the raise, not the value, or a status
    # with no created_at takes the row down with it.
    try:
        moment = GLib.DateTime.new_from_iso8601(iso_timestamp or '', None)
    except TypeError:
        return ''
    if moment is None:
        return ''
    return format_time(moment)


def time_left(iso_timestamp):
    """Compact distance until *iso_timestamp* ('3h', '2d'), '' when it
    is in the past or unparsable."""
    then = statustext.parse_time(iso_timestamp)
    if then is None:
        return ''
    seconds = int((then - datetime.datetime.now(
        datetime.timezone.utc)).total_seconds())
    if seconds <= 0:
        return ''
    if seconds < 3600:
        return _('%dm').replace('%d', str(max(1, seconds // 60)))
    if seconds < 86400:
        return _('%dh').replace('%d', str(seconds // 3600))
    return _('%dd').replace('%d', str(seconds // 86400))


def days_since(iso_timestamp):
    then = statustext.parse_time(iso_timestamp)
    if then is None:
        return 0
    return (datetime.datetime.now(datetime.timezone.utc) - then).days


# Labels and icons are this menu's; the codes are the entity's, so a
# visibility added on the type shows up here or the import fails.
_VISIBILITY_LABELS = {
    'public': (_('Public'), 'network-workgroup-symbolic'),
    'unlisted': (_('Unlisted'), 'view-conceal-symbolic'),
    'private': (_('Followers Only'), 'changes-prevent-symbolic'),
    'direct': (_('Mentioned People Only'), 'mail-unread-symbolic'),
}
VISIBILITY_INFO = {code: _VISIBILITY_LABELS[code] for code in VISIBILITIES}

_QUOTE_LABELS = {
    'public': _('_Anyone'),
    'followers': _('My _Followers'),
    'nobody': _('_Nobody'),
}
QUOTE_POLICIES = tuple((code, _QUOTE_LABELS[code]) for code in QUOTE_POLICY_CODES)


def status_filter(data, context=filterrules.HOME, now=None):
    """Strongest filter hit on a status as ``(action, title)``, or None.
    'hide' wins over 'warn'/'blur'; boosts are checked on both the
    wrapper and the boosted post.

    What a rule means lives in :mod:`filterrules`, which Catch Up reads
    too. *context* is the view being shown -- a rule scoped to another
    one does not apply here, so a filter made for, say, an account page
    leaves the home timeline alone. *now* judges expiry; pass it to have
    it honoured.
    """
    return filterrules.strongest(data, context, now)


def quote_policy_of(status):
    """``public``, ``followers`` or ``nobody``. :meth:`Status.quote_policy`
    is the reading; this accepts a plain dict the same way."""
    return Status.quote_policy(status)


def can_quote(status):
    approval = status.get('quote_approval')
    if approval:
        return approval.get('current_user') not in ('denied', 'unknown')
    return status.get('visibility') in ('public', 'unlisted')


def plain_text(html_text, limit=None):
    """Readable plain text of status HTML, optionally truncated."""
    parts = []
    for segment in parse_content(html_text):
        if segment['text'] == '\n':
            parts.append('\n')
        else:
            if parts and parts[-1] != '\n':
                parts.append(' ')
            parts.append(shorten_url_text(segment['text'])
                         if segment['href'] else segment['text'])
    text = ''.join(parts).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(' ', 1)[0] + '…'
    return text


def _collapse_view(segments):
    """``(segments to show, collapsed)``: hashtag-stuffed and very long
    posts shrink behind Show More while ``collapse-long-posts`` is on."""
    if not segments or not _flag('collapse-long-posts'):
        return segments, False
    if _should_collapse(segments):
        return [{'text': _static_content_labels(segments),
                 'href': None}], True
    length = sum(len(s['text']) for s in segments if s['text'] != '\n')
    breaks = sum(1 for s in segments if s['text'] == '\n')
    if length <= LONG_POST_CHARS and breaks <= LONG_POST_BREAKS:
        return segments, False
    shown, budget, lines = [], LONG_POST_CHARS // 2, 0
    for segment in segments:
        if segment['text'] == '\n':
            lines += 1
            if lines > 8:
                break
            shown.append(segment)
            continue
        text = segment['text']
        if len(text) > budget:
            cut = text[:budget].rsplit(' ', 1)[0] or text[:budget]
            shown.append(dict(segment, text=cut + '…'))
            break
        shown.append(segment)
        budget -= len(text)
    while shown and shown[-1]['text'] == '\n':
        shown.pop()
    return shown, True


def _translation_enabled(client):
    """True when the instance offers post translation. The instance
    document is fetched once in the background if it is not cached."""
    from .capabilities import translation_enabled
    if client.instance_info is None:
        if not getattr(client, '_babble_info_requested', False):
            client._babble_info_requested = True

            def loaded(_client, result, _data=None):
                try:
                    _client.instance_info_finish(result)
                except GLib.Error:
                    pass

            client.instance_info_async(loaded)
        return False
    return translation_enabled(client)


def _own_account_id(widget):
    window = pages.window_of(widget)
    account = getattr(window, 'account', None) or {}
    return account.get('id')


def _static_content_labels(segments, limit=3):
    """Summary string for collapsed hashtag-stuffed posts."""
    parts = []
    for segment in segments:
        if segment['text'] == '\n':
            continue
        text = segment['text']
        if segment['href']:
            text = shorten_url_text(text)
        parts.append(text)
        if len(parts) >= limit:
            break
    if not parts:
        return ''
    return ' '.join(parts)


def _should_collapse(segments):
    """True for posts made almost only of hashtags/links."""
    plain = [s for s in segments
             if s['text'] != '\n' and s['href'] is None]
    tagged = [s for s in segments if s['href'] is not None]
    hashtags = [s for s in tagged if is_hashtag(s)]
    return not plain and len(tagged) >= 4 or len(hashtags) >= 6


