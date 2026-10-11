# SPDX-License-Identifier: GPL-3.0-or-later

"""Status text: HTML to safe segments, and the timestamps that go with
them.

Split out of ``timeline.py`` because none of this builds a widget: it is
plain text in, plain data out, and six modules need it (announcements,
conversations, reports, textemoji, the notifications row and the
timeline itself) without dragging in the row and the list view. The one
dependency is GLib, for ``DateTime`` -- so a post older than a year is
formatted in the reader's timezone -- which needs no display, leaving
the whole module readable and testable headless.
"""

import datetime
import html as html_module
import urllib.parse
from html.parser import HTMLParser

import gi

gi.require_version('GLib', '2.0')

try:
    from .i18n import gettext as _
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _

from gi.repository import GLib  # noqa: E402


class _ContentParser(HTMLParser):
    """Turns status HTML into safe text/link segments.

    Paragraph boundaries become a plain ``'\n'`` segment, ``<br>`` (and
    list items) a ``'\n'`` segment flagged ``br`` so renderers can keep a
    line break distinct from a paragraph gap.

    The contents of ``<script>`` and ``<style>`` are dropped rather than
    rendered. Mastodon strips both when it ingests a post, so this only
    ever fires for a server that does not -- and without it their source
    would be read aloud as the author's words.
    """

    BLOCK_TAGS = {'p', 'div', 'blockquote', 'pre', 'h1', 'h2', 'h3', 'h4',
                  'h5', 'h6'}
    LINE_TAGS = {'br', 'li'}
    # Elements whose text is source, not prose.
    UNPRINTABLE_TAGS = {'script', 'style'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.segments = []
        self._href = None
        self._buffer = []
        self._unprintable = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.UNPRINTABLE_TAGS:
            self._unprintable += 1
            return
        if self._unprintable:
            return
        if tag == 'a':
            self._flush()
            self._href = dict(attrs).get('href')
        elif tag in self.BLOCK_TAGS:
            self._flush()
            if self.segments:
                self.segments.append({'text': '\n', 'href': None})
        elif tag in self.LINE_TAGS:
            self._flush()
            if tag == 'br' or self.segments:
                self.segments.append({'text': '\n', 'href': None,
                                      'br': True})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.UNPRINTABLE_TAGS:
            self._unprintable = max(0, self._unprintable - 1)
            return
        if self._unprintable:
            return
        if tag == 'a':
            self._flush()
            self._href = None
        elif tag in self.BLOCK_TAGS or tag in self.LINE_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._unprintable:
            return
        self._buffer.append(data)

    def _flush(self):
        text = ''.join(self._buffer)
        self._buffer = []
        if not text:
            return
        self.segments.append({'text': text, 'href': self._href})


def parse_content(html_text):
    """Convert status HTML to a list of segments.

    Text segments are ``{'text', 'href', 'sp_before', 'sp_after'}`` (the
    flags record whether the source had whitespace around the text, so
    punctuation after a link does not get a stray space). A line break
    is a ``'newline'`` segment flagged ``br`` and a paragraph break the
    same segment without the flag."""
    if not html_text:
        return []
    parser = _ContentParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        return [{'text': html_module.unescape(html_text), 'href': None}]
    # HTMLParser never flushes a trailing run of plain text that isn't
    # followed by a tag, which silently dropped tag-free payloads (e.g.
    # plain field values).
    parser._flush()
    segments = []
    pending_space = False
    for segment in parser.segments:
        if segment['text'] == '\n':
            pending_space = False
            if not segments:
                continue
            last = segments[-1]
            if not segment.get('br') and last['text'] == '\n' \
                    and not last.get('br'):
                continue
            segments.append(segment)
            continue
        raw = segment['text'].replace('\n', ' ')
        text = raw.strip()
        if not text:
            if raw:
                pending_space = True
            continue
        sp_before = raw[0].isspace() or pending_space
        sp_after = raw[-1].isspace()
        pending_space = False
        if segments and segments[-1]['href'] == segment['href'] \
                and segment['href'] is not None \
                and segments[-1]['text'] != '\n':
            segments[-1]['text'] += ' ' + text
            segments[-1]['sp_after'] = sp_after
        else:
            segments.append({'text': text, 'href': segment['href'],
                             'sp_before': sp_before, 'sp_after': sp_after})
    while segments and segments[-1]['text'] == '\n':
        segments.pop()
    return segments


def relative_time(iso_timestamp):
    try:
        then = datetime.datetime.fromisoformat(
            iso_timestamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        return ''
    delta = datetime.datetime.now(datetime.timezone.utc) - then
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        # Translators: compact age of a post in seconds, e.g. "42s"
        return _('%ds').replace('%d', str(seconds))
    minutes = seconds // 60
    if minutes < 60:
        # Translators: compact age of a post in minutes, e.g. "5m"
        return _('%dm').replace('%d', str(minutes))
    hours = minutes // 60
    if hours < 24:
        # Translators: compact age of a post in hours, e.g. "3h"
        return _('%dh').replace('%d', str(hours))
    days = hours // 24
    if days < 365:
        # Translators: compact age of a post in days, e.g. "12d"
        return _('%dd').replace('%d', str(days))
    moment = GLib.DateTime.new_from_unix_utc(int(then.timestamp()))
    # Translators: date of a post older than a year, a GDateTime format
    # string, e.g. "Oct 2025"; see the g_date_time_format() docs
    return moment.to_local().format(_('%b %Y'))


def is_web_link(uri):
    """Whether *uri* is an http(s) address, the only kind a link from a
    post, a profile or a card may hand to the system launcher.

    A server that does not sanitise its HTML can serve ``file://``,
    ``smb://`` or any scheme some installed app handles; outside the
    Flatpak portal a click would open that handler directly."""
    parts = urllib.parse.urlsplit(uri or '')
    return parts.scheme in ('http', 'https') and bool(parts.netloc)


def shorten_url_text(text):
    """Compact raw URLs for display, keeping the href target intact.

    Any ``user:password@`` is dropped, so the label always starts with
    the host the link goes to: ``https://bank.test.verify.example@evil.test``
    would otherwise read as the bank, and the truncation could cut the
    real host off entirely."""
    if text.lower().startswith(('http://', 'https://')):
        parsed = urllib.parse.urlsplit(text)
        path = parsed.path.rstrip('/')
        label = parsed.netloc.rpartition('@')[2] + path
        if len(label) > 42:
            label = label[:39] + '…'
        return label
    return text


def is_hashtag(segment):
    return (segment['href'] is not None
            and '/tags/' in segment['href']
            or segment['text'].startswith('#'))
