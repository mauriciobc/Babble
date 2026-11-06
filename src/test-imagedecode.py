# SPDX-License-Identifier: GPL-3.0-or-later
"""How remote image bytes are decoded, and how large a bitmap they may ask for.

The bytes are untrusted: a few hundred kilobytes can declare a bitmap of
hundreds of megapixels. ``mediacache._decode_covering`` therefore decides
the decoded size from the file header *before* anything allocates it:

  * inside the budget and no larger than its box: ``Gdk.Texture`` (fast);
  * over the budget, or larger than the box: GdkPixbuf, scaling as it
    loads, so the full bitmap is never held;
  * a header it cannot read (WebP ...): GdkPixbuf with the same budget.

The tests build real PNGs whose header claims a size, and spy on
``Gdk.Texture.new_from_bytes`` to see which path ran.

Exits 77 (skip) without a display.
"""

import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
    print('SKIP - needs a display')
    sys.exit(77)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

from src import mediacache  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))
        FAILURES.append(name)


def png(width, height):
    """A valid RGB PNG of solid black. Rows of zeros compress to almost
    nothing, so a header can claim a huge bitmap in a small file."""
    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xffffffff
        return struct.pack('>I', len(body)) + kind + body \
            + struct.pack('>I', crc)

    compressor = zlib.compressobj(9)
    row = b'\x00' * (1 + width * 3)
    packed = []
    batch = row * 64
    for _ in range(height // 64):
        packed.append(compressor.compress(batch))
    packed.append(compressor.compress(row * (height % 64)))
    packed.append(compressor.flush())
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height,
                                         8, 2, 0, 0, 0))
            + chunk(b'IDAT', b''.join(packed))
            + chunk(b'IEND', b''))


# Which path ran: count the direct texture loads.
direct = []
real_new_from_bytes = Gdk.Texture.new_from_bytes


def spy(data):
    direct.append(True)
    return real_new_from_bytes(data)


Gdk.Texture.new_from_bytes = staticmethod(spy)


def decode(data, cover=None, scale=1.0):
    del direct[:]
    texture = mediacache._decode_covering(GLib.Bytes.new(data), cover, scale)
    return texture, bool(direct)


MP = mediacache.MAX_DECODE_PIXELS
SIDE = mediacache.MAX_DECODE_DIMENSION

# --- 1. a small image that fits its box skips the loader --------------

texture, fast = decode(png(100, 100), cover=(200, 200))
check('an image no larger than its box takes the direct path',
      fast and texture is not None and texture.get_width() == 100)

# --- 2. within the budget and no box: full size, direct ---------------

texture, fast = decode(png(3000, 3000))
check('a 9 MP image with no box is decoded at full size, directly',
      fast and texture is not None and texture.get_width() == 3000,
      'size=%s' % (texture and texture.get_width()))

# --- 3. a header that declares more than the budget -------------------

big = png(9000, 9000)
texture, fast = decode(big)
check('a small file declaring 81 MP is not given to the direct path',
      not fast)
check('and it is decoded inside the pixel and side budget',
      texture is not None
      and texture.get_width() * texture.get_height() <= MP
      and max(texture.get_width(), texture.get_height()) <= SIDE,
      'size=%s' % (texture and '%dx%d' % (texture.get_width(),
                                          texture.get_height()),))
check('the declared file is small next to what it declares',
      len(big) < 2 * 1024 * 1024, 'bytes=%d' % len(big))

# --- 4. an image larger than its box is scaled while it loads ---------

texture, fast = decode(png(1000, 1000), cover=(100, 100), scale=1.0)
check('an image larger than its box goes through the scaling loader',
      not fast and texture is not None and texture.get_width() <= 100,
      'size=%s' % (texture and texture.get_width()))

# --- 5. a header this module cannot read still gets the budget --------

webp = None
try:
    source = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                  4000, 4000)
    source.fill(0)
    ok, webp = source.save_to_bufferv('webp', [], [])
    if not ok:
        webp = None
except GLib.Error:
    webp = None
if webp is None:
    print('ok - (skipped) no WebP saver in this GdkPixbuf')
else:
    texture, fast = decode(webp, cover=(100, 100))
    check('a WebP, whose header is not scanned, is scaled by the loader',
          not fast and texture is not None and texture.get_width() <= 100,
          'size=%s' % (texture and texture.get_width()))
    texture, fast = decode(webp)
    check('and with no box it is not given to the direct path',
          not fast and texture is not None)

# --- 6. not an image --------------------------------------------------

texture, fast = decode(b'<html>not an image</html>', cover=(100, 100))
check('bytes that are not an image decode to nothing', texture is None)

Gdk.Texture.new_from_bytes = staticmethod(real_new_from_bytes)


if FAILURES:
    print('\n%d failure(s): %s' % (len(FAILURES), ', '.join(FAILURES)))
    sys.exit(1)
print('\nall image decode tests passed')
