# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure geometry and text shaping inputs for Catch Up tiles."""

import re

from .. import statustext
from . import digest_layout
MIN_COLUMN = 180
ROW_HEIGHT = 160
GAP = 12
# A column is never wider than this (a one-column grid just below two).
MAX_COLUMN = 2 * MIN_COLUMN + GAP - 1
NATURAL_COLUMNS = 4
NATURAL_WIDTH = NATURAL_COLUMNS * MIN_COLUMN + (NATURAL_COLUMNS - 1) * GAP
MIN_TILES = 6                # below this Highlights is hidden (8)
HEADLINE_CHARS = 80
QUOTE_SHORT_CHARS = 120      # a shorter text-only tile centres its text
INSET_TEXT_CHARS = 140
SHARER_AVATARS = 5
TAG_AVATARS = 3
FOOTER_HEIGHT = 34
NEWS_IMAGE_SHARE = 0.55


# --------------------------------------------------------------------------
# Geometry (pure)

def columns_for(width):
    """Columns of a grid *width* px wide: auto-fill, 180 px minimum."""
    return max(1, (int(width) + GAP) // (MIN_COLUMN + GAP))


def cell_rect(width, columns, col, row, colspan, rowspan):
    """``(x, y, w, h)`` of a cell block in a grid of *width* and *columns*.
    The edges are rounded, not the sizes, so neighbours never overlap or
    leave a pixel gap and the last column ends at *width*."""
    step = (width - GAP * (columns - 1)) / columns + GAP
    left = round(col * step)
    right = round((col + colspan) * step - GAP)
    return (left, row * (ROW_HEIGHT + GAP), right - left,
            rowspan * ROW_HEIGHT + (rowspan - 1) * GAP)


def grid_height(placed):
    """Height of a packing: ``rows * 160 + 12 * (rows - 1)``."""
    rows = digest_layout.grid_rows(placed)
    return rows * ROW_HEIGHT + (rows - 1) * GAP if rows else 0


def span_cover(colspan, rowspan):
    """The largest box a tile of this span can fill, the size its
    pictures are decoded for. Fixed per span, so a resize keeps the
    cached texture."""
    return (colspan * MAX_COLUMN + (colspan - 1) * GAP,
            rowspan * ROW_HEIGHT + (rowspan - 1) * GAP)


def tile_key(placed):
    ref = placed.ref
    return (placed.kind, ref['id'] if placed.kind == 'post' else ref['key'])

def _strip_known(text, shortcodes):
    if not shortcodes or ':' not in text:
        return text
    clean = re.sub(r':([A-Za-z0-9_+\-]+):',
                   lambda m: '' if m.group(1) in shortcodes else m.group(0),
                   text)
    return re.sub(r'\s{2,}', ' ', clean) if clean != text else text


def segments_text(segments, shortcodes=(), lines=True):
    """Plain text of ``parse_content`` segments: links shortened, the
    custom emoji in *shortcodes* dropped (a label cannot draw them).
    Line and paragraph breaks are one newline, or a space when *lines* is
    false."""
    parts = []
    last = None
    for segment in segments:
        text = segment['text']
        if text == '\n':
            if parts and parts[-1] != '\n':
                parts.append('\n' if lines else ' ')
            last = None
            continue
        if segment.get('href'):
            text = statustext.shorten_url_text(text)
        text = _strip_known(text, shortcodes).strip()
        if not text:
            continue
        if last is not None and (last.get('sp_after')
                                 or segment.get('sp_before')):
            parts.append(' ')
        parts.append(text)
        last = segment
    return ''.join(parts).strip()


def split_headline(segments):
    """``(headline, body)`` segments (5.3): when the first paragraph is at
    most 80 characters and more text follows, it is the headline;
    otherwise there is none and everything is the body."""
    for index, segment in enumerate(segments):
        if segment['text'] == '\n' and not segment.get('br'):
            first, rest = segments[:index], segments[index + 1:]
            break
    else:
        return [], list(segments)
    while rest and rest[0]['text'] == '\n':
        rest = rest[1:]
    head = segments_text(first, lines=False)
    if head and len(head) <= HEADLINE_CHARS and rest:
        return first, rest
    return [], list(segments)


def _soft_breaks(text):
    """Hard line breaks in a tile's text become spaces.

    Mastodon sends ``<br>`` for the line breaks in a list, and
    ``statustext.parse_content`` hands them on as ``\\n``. Pango counts a
    hard break as a line whatever ``set_height`` says, so a post with
    many of them measures far taller than the card no matter how few
    lines the tile asked for: one line of the sample post measures 305 px
    against a 232 px card, and the text is dropped rather than shown
    short. A tile re-flows its text to the card anyway, so the breaks
    are handed to the wrap instead.
    """
    return re.sub(r'[ \t]*\n[ \t]*', ' ', text).strip()
