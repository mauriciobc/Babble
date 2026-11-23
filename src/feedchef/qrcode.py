# SPDX-License-Identifier: GPL-3.0-or-later
"""The QR code of a share link, as pixels (no widgets, no GTK).

segno (an optional package, loaded through ``deps`` on the caller's worker
only) makes the symbol; this module reads its matrix and gives back one
byte per module (0 black, 255 white) with the quiet zone around it, which
the interface paints as it is: dark modules black on white, whatever the
theme, so that a phone can read it.
"""

from __future__ import annotations

from . import deps

# The quiet zone, in modules, that the standard asks for around a symbol.
QUIET_ZONE = 4
# The largest symbol that is still scanned reliably from a screen; a longer
# link is not drawn.
MAX_VERSION = 25


class NoQr(Exception):
    """No code could be made: the package is missing or the link is too
    long for a symbol (``too_large``). The message says which, for the log."""

    too_large = False


def _too_large(message):
    error = NoQr(message)
    error.too_large = True
    return error


def render(text: str) -> tuple[int, bytes]:
    """(side, pixels) of the code of *text*: a square of *side* by *side*
    modules, row by row, one byte each (0 for a dark module, 255 for a
    light one), the quiet zone included. Raises ``NoQr``. Imports segno on
    the thread it is called from: call it from a worker."""
    segno = deps.get('segno')
    if segno is None:
        raise NoQr(deps.reason('segno') or 'segno is not installed')
    code = None
    for level in ('m', 'l'):
        try:
            code = segno.make(text, error=level, micro=False)
            break
        except Exception as error:  # DataOverflowError: too long, try L
            failure = error
    if code is None:
        raise _too_large('the link is too long for a QR code (%s)' % failure)
    if code.version is None or code.version > MAX_VERSION:
        raise _too_large('the link needs version %s, more than %d'
                         % (code.version, MAX_VERSION))
    side = code.symbol_size(border=QUIET_ZONE)[0]
    out = bytearray()
    for row in code.matrix_iter(border=QUIET_ZONE):
        # matrix_iter gives 1 for a dark module, 0 for a light one.
        out.extend(0 if module else 255 for module in row)
    if len(out) != side * side:
        raise NoQr('the code is not square')
    return side, bytes(out)
