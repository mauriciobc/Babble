# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP metadata: Link paging and the server clock.

Both answers come from response headers rather than from the body, and
both are decoded the same way whichever endpoint asked. They keep no
state: the client holds the offset, these read it.
"""

import math
import re
import urllib.parse
from email.utils import parsedate_to_datetime

import gi

gi.require_version('GLib', '2.0')

from gi.repository import GLib  # noqa: E402
# A parsed number below this is not an instant: 2000-01-01T00:00:00Z. It
# rejects the handful of strings that read as floats without being times
# -- 'nan', 'inf', '-1', '0' -- which would otherwise reach int() in the
# error path and raise there.
EARLIEST_PLAUSIBLE_EPOCH = 946684800

# How far the server's Date header may claim to run from ours before it
# is ignored (RFC 9110 6.6.1 does not require a server's Date to be
# true). Two days is far past any real skew and far short of letting a
# broken header stall the client for hours.
MAX_CLOCK_SKEW_SECONDS = 172800

_LINK_PART = re.compile(r'<([^>]*)>([^<]*)')

_LINK_REL = re.compile(r'(?:^|[;\s])rel\s*=\s*(?:"([^"]*)"|([^\s;,]+))',
                       re.IGNORECASE)

def _query_params(query):
    """A query string as a params dict for ``request_async``: a key that
    appears once maps to its string, a repeated one (``exclude_types[]``)
    to the list of its values, in order. ``request_async`` encodes with
    ``doseq``, so both forms go back out as they came in."""
    params = {}
    for key, val in urllib.parse.parse_qsl(query):
        if key not in params:
            params[key] = val
        elif isinstance(params[key], list):
            params[key].append(val)
        else:
            params[key] = [params[key], val]
    return params

def parse_link_header(value):
    """``{'next': {...query params}, 'prev': {...}}`` from an RFC 8288
    Link header, the way Mastodon paginates bookmarks, favourites,
    follow lists and everything else not keyed by status id. A key the
    URL repeats (``exclude_types[]``) holds the list of its values, so a
    cursor spread over a request's params keeps what the first page
    asked for."""
    links = {}
    for url, params in _LINK_PART.findall(value or ''):
        found = _LINK_REL.search(params)
        if not found:
            continue
        query = urllib.parse.urlsplit(url).query
        # RFC 8288: rel is case-insensitive, may sit after other
        # parameters, and may hold several space-separated relations.
        for rel in (found.group(1) or found.group(2)).lower().split():
            links[rel] = _query_params(query)
    return links

def parse_reset_instant(value):
    """``X-RateLimit-Reset`` as a POSIX timestamp, or None.

    Mastodon builds it with ``(request_time + reset_period_offset)
    .iso8601(6)`` -- app/controllers/concerns/api/rate_limit_headers.rb --
    so it arrives as an ISO-8601 instant (``2026-10-16T11:20:00.701542Z``,
    the form observed on mastodon.social), which ``int()`` can never read.
    Every 429 therefore fell through to :data:`DEFAULT_RATE_LIMIT_SECONDS`,
    and a client that ignored the server's real window deepened the limit
    it had just been told about. Older servers and forks send a Unix epoch
    and some send a fractional one, so both are read; the docs call this
    only "Timestamp when your rate limit will reset" and never say which
    format. None means the header was absent or is not a timestamp.
    """
    text = (value or '').strip()
    if not text:
        return None
    try:
        epoch = float(text)
    except ValueError:
        pass
    else:
        # 'nan' and 'inf' are floats and not instants; math.isfinite keeps
        # both out, and int(inf) would raise inside the error path.
        return epoch if math.isfinite(epoch) \
            and epoch >= EARLIEST_PLAUSIBLE_EPOCH else None
    # PyGObject raises TypeError from the constructor when GLib returns
    # NULL, rather than handing back None, so an unparsable string has to
    # be caught: this runs on a header a server chose, inside the error
    # path of _on_response, where an exception escapes into libsoup and
    # leaves the caller's Gio.Task unfinished.
    try:
        stamp = GLib.DateTime.new_from_iso8601(text)
    except TypeError:
        return None
    return stamp.to_unix() if stamp is not None else None

def server_clock_offset(date_header, now):
    """Seconds the server's clock runs ahead of ours, from its ``Date``.

    RFC 9110 6.6.1 requires an origin server to send ``Date`` on every
    2xx, 3xx and 4xx, and the rate-limit reset is an instant on the
    *server's* clock, so the difference between the two is one
    subtraction. Zero when the header is missing or unparsable, and zero
    when it claims more than :data:`MAX_CLOCK_SKEW_SECONDS` of drift,
    both of which leave the caller no worse off than not correcting.
    """
    try:
        server_now = parsedate_to_datetime(date_header).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0
    offset = server_now - now
    if abs(offset) > MAX_CLOCK_SKEW_SECONDS:
        return 0.0
    return offset
