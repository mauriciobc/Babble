# SPDX-License-Identifier: GPL-3.0-or-later

"""Status text: HTML to safe segments, and the timestamps that go with
them.

Split out of ``timeline.py`` because none of this builds a widget: it is
plain text in, plain data out, and six modules need it (announcements,
conversations, reports, textemoji, the notifications row and the
timeline itself) without dragging in the row and the list view. The
dependencies are GLib, for ``DateTime`` -- so a post older than a year
is formatted in the reader's timezone -- and Gio, to read the desktop's
12/24-hour clock setting; neither needs a display, leaving the whole
module readable and testable headless.
"""

import datetime
import html as html_module
import locale
import urllib.parse
from html.parser import HTMLParser

import gi

gi.require_version('GLib', '2.0')
gi.require_version('Gio', '2.0')

try:
    from .i18n import gettext as _
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _

from gi.repository import Gio, GLib  # noqa: E402


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


_INTERFACE_SCHEMA = 'org.gnome.desktop.interface'
_CLOCK_KEY = 'clock-format'
_PORTAL_NAME = 'org.freedesktop.portal.Desktop'
_PORTAL_PATH = '/org/freedesktop/portal/desktop'
_PORTAL_SETTINGS = 'org.freedesktop.portal.Settings'
# The first timestamp rendered waits on this read; a portal that is slow
# to answer must not stall the window, so it gets the GSettings fallback.
_PORTAL_TIMEOUT_MS = 200

_clock_format = None
# What keeps the clock setting live: the bus subscription to the
# portal's SettingChanged, and the GSettings object when it is the source.
_clock_subscription = None
_clock_settings = None


def _clock_value(value):
    return value if value in ('12h', '24h') else None


def _session_bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _schema_source():
    return Gio.SettingsSchemaSource.get_default()


def _portal_read(bus, method):
    reply = bus.call_sync(
        _PORTAL_NAME, _PORTAL_PATH, _PORTAL_SETTINGS, method,
        GLib.Variant('(ss)', (_INTERFACE_SCHEMA, _CLOCK_KEY)),
        GLib.VariantType('(v)'), Gio.DBusCallFlags.NONE,
        _PORTAL_TIMEOUT_MS, None)
    # unpack() opens nested variants too, so the doubly wrapped value
    # the old Read returns comes out as the plain string ReadOne gives.
    return _clock_value(reply.unpack()[0])


def _read_portal(bus):
    try:
        return _portal_read(bus, 'ReadOne')
    except GLib.Error as error:
        # ReadOne is Settings version 2; a version 1 portal only has Read.
        if (Gio.DBusError.get_remote_error(error)
                != 'org.freedesktop.DBus.Error.UnknownMethod'):
            return None
    try:
        return _portal_read(bus, 'Read')
    except GLib.Error:
        return None


def _on_portal_setting_changed(_bus, _sender, _path, _interface, _signal,
                               parameters):
    global _clock_format
    namespace, key, value = parameters.unpack()
    if namespace == _INTERFACE_SCHEMA and key == _CLOCK_KEY:
        _clock_format = _clock_value(value) or '24h'


def _on_gsettings_changed(settings, key):
    global _clock_format
    _clock_format = _clock_value(settings.get_string(key)) or '24h'


def _read_gsettings():
    """The clock format from GSettings, None when the schema (or its
    key) is not installed -- Gio aborts on an unknown schema."""
    global _clock_settings
    source = _schema_source()
    schema = source.lookup(_INTERFACE_SCHEMA, True) if source else None
    if schema is None or not schema.has_key(_CLOCK_KEY):
        return None
    settings = Gio.Settings.new_full(schema, None, None)
    settings.connect('changed::' + _CLOCK_KEY, _on_gsettings_changed)
    _clock_settings = settings
    return _clock_value(settings.get_string(_CLOCK_KEY))


def _load_clock_format():
    global _clock_subscription
    bus = _session_bus()
    value = None
    if bus is not None:
        # Subscribed before the read, so a change in between is not lost.
        _clock_subscription = (bus, bus.signal_subscribe(
            _PORTAL_NAME, _PORTAL_SETTINGS, 'SettingChanged', _PORTAL_PATH,
            _INTERFACE_SCHEMA, Gio.DBusSignalFlags.NONE,
            _on_portal_setting_changed))
        value = _read_portal(bus)
    return value or _read_gsettings() or '24h'


def clock_format():
    """'12h' or '24h', the desktop's clock format.

    Read once, on first use, from the Settings portal (which answers in
    and out of a sandbox), else from GSettings when the GNOME schema is
    installed, else '24h'; later changes update it, so the next time
    formatted follows the setting."""
    global _clock_format
    if _clock_format is None:
        _clock_format = _load_clock_format()
    return _clock_format


def format_time(moment, with_date=True):
    """*moment* -- a GLib.DateTime or an aware datetime -- in local time
    and the desktop's clock format: '9 Oct 2026, 14:30' or
    '9 Oct 2026, 2:30 PM', only the time without *with_date*. '' for
    None."""
    if moment is None:
        return ''
    if not isinstance(moment, GLib.DateTime):
        moment = GLib.DateTime.new_from_unix_utc(int(moment.timestamp()))
    local = moment.to_local()
    if clock_format() == '12h':
        if with_date:
            # Translators: date and 12-hour time, a GDateTime format
            # string, e.g. "9 Oct 2026, 2:47 PM"; see the
            # g_date_time_format() docs
            fmt = _('%-d %b %Y, %-I:%M %p')
        else:
            # Translators: 12-hour time, a GDateTime format string, e.g.
            # "2:47 PM"; see the g_date_time_format() docs
            fmt = _('%-I:%M %p')
    elif with_date:
        # Translators: a GDateTime format string, e.g. "9 Oct 2026, 10:47";
        # see the g_date_time_format() docs
        fmt = _('%-d %b %Y, %H:%M')
    else:
        # Translators: 24-hour time, a GDateTime format string, e.g.
        # "14:47"; see the g_date_time_format() docs
        fmt = _('%H:%M')
    formatted = local.format(fmt)
    if formatted is None:  # a broken translation
        formatted = local.format('%c' if with_date else '%X')
    return formatted or ''


def parse_time(value):
    """An aware ``datetime`` from an ISO-8601 string, or None.

    Mastodon sends UTC with a ``Z``; ``fromisoformat`` did not learn to
    read that until Python 3.11, so the suffix is normalised first. A
    result with no offset is taken as UTC, which is what every timestamp
    in the API is.

    The one reader of ISO-8601 in the app: timeline.py, statusviews.py,
    filters.py and the Catch-Up port each had their own, and two of them
    disagreed about whether a naive value was an error. The two exceptions
    are deliberate and say so where they are found: ``catchuprank`` keeps
    its own reader because its golden scores pin it to the original
    JavaScript, and ``catchupdigest.parse_time`` keeps the name as an
    alias because its own test calls it that way.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def relative_time(iso_timestamp):
    then = parse_time(iso_timestamp)
    if then is None:
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


def compact_count(n):
    """An interaction count for a counter: '42', '999', '3.4K', '2.1M'.

    Below 1,000 the number with the locale's digit grouping. From 1,000
    up, one decimal with the locale's decimal separator, the decimal
    dropped when it is 0, and a suffix. A value that rounds up to 1,000
    of one unit moves to the next (999,950 is '1M', not '1000K'). None
    and negatives count as 0."""
    n = max(n or 0, 0)
    if n < 1000:
        return locale.format_string('%d', n, grouping=True)
    for size in (1000, 1000000, 1000000000):
        value = round(n / size, 1)
        if value < 1000 or size == 1000000000:
            break
    if value == int(value):
        number = locale.format_string('%d', int(value), grouping=True)
    else:
        number = locale.format_string('%.1f', value, grouping=True)
    if size == 1000:
        # Translators: compact count in thousands, e.g. "3.4K"; %s is the
        # number. Write it as "3.4 Tsd." in German, "3,4 mil" in Spanish
        # and "3,4 mil" in Brazilian Portuguese. Keep the %s.
        text = _('%sK')
    elif size == 1000000:
        # Translators: compact count in millions, e.g. "2.1M"; %s is the
        # number. German uses "Mio.", French "M" is fine, Portuguese
        # uses "mi". Keep the %s.
        text = _('%sM')
    else:
        # Translators: compact count in billions, e.g. "1.2B"; %s is the
        # number. German uses "Mrd.", French "Md", Portuguese "bi".
        # Keep the %s.
        text = _('%sB')
    # %-formatting, not str.replace: a translator who changes the
    # specifier then fails loudly here instead of rendering "3.4ds".
    return text % number


_C0_AND_SPACE = "".join(map(chr, range(0x21)))
_DROP_TAB_NEWLINE = {ord(c): None for c in '\t\n\r'}


def split_url(uri):
    """*uri* split the way a browser reads it, or None when malformed.

    Browsers treat a backslash as a slash in an http(s) URL, so
    ``https://evil.test\\@bank.test`` opens evil.test; ``urlsplit``
    alone would read ``evil.test\\`` as credentials and bank.test as the
    host. A malformed authority (an unmatched ``[``) makes ``urlsplit``
    raise; servers can send one, so it is None here instead."""
    # The WHATWG URL parser drops leading and trailing C0 controls and
    # spaces and every tab and newline before it looks at the scheme, so
    # the scheme test below has to see the same string a browser does.
    uri = (uri or '').strip(_C0_AND_SPACE).translate(_DROP_TAB_NEWLINE)
    if uri[:8].lower().startswith(('http:', 'https:')):
        uri = uri.replace('\\', '/')
    try:
        return urllib.parse.urlsplit(uri)
    except ValueError:
        return None


def url_host(parts):
    """The host (and port) a split URL opens, without any credentials."""
    return parts.netloc.rpartition('@')[2]


def is_web_link(uri):
    """Whether *uri* is an http(s) address, the only kind a link from a
    post, a profile or a card may hand to the system launcher.

    A server that does not sanitise its HTML can serve ``file://``,
    ``smb://`` or any scheme some installed app handles; outside the
    Flatpak portal a click would open that handler directly."""
    parts = split_url(uri)
    return parts is not None and parts.scheme in ('http', 'https') \
        and bool(url_host(parts))


def shorten_url_text(text):
    """Compact raw URLs for display, keeping the href target intact.

    Any ``user:password@`` is dropped, so the label always starts with
    the host the link goes to: ``https://bank.test.verify.example@evil.test``
    would otherwise read as the bank, and the truncation could cut the
    real host off entirely."""
    if text.lower().startswith(('http://', 'https://')):
        parsed = split_url(text)
        if parsed is None:
            return text
        label = url_host(parsed) + parsed.path.rstrip('/')
        if len(label) > 42:
            label = label[:39] + '…'
        return label
    return text


def is_hashtag(segment):
    return (segment['href'] is not None
            and '/tags/' in segment['href']
            or segment['text'].startswith('#'))


# The window a tag's usage is counted over, in days. The hashtag page and
# the composer's suggestions say it in words ("last 2 days"), so a change
# here has to change those strings too.
STATS_DAYS = 2


def history_totals(history, days=STATS_DAYS):
    """(posts, people) over the newest *days* entries of tag.history."""
    posts = people = 0
    for entry in (history or [])[:days]:
        posts += int(entry.get('uses') or 0)
        people += int(entry.get('accounts') or 0)
    return posts, people
