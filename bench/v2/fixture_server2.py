# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixture instance for the v2 benchmark, with realistic image sizes.

Why this exists rather than reusing ``bench/fixture_server.py``: that one
serves **96x96** avatars and 640x480 media, which is not what a Mastodon
instance serves. The size decides which decode path ``_decode_covering``
takes (``src/mediacache.py:183-189``): at 96px an avatar is no bigger than
its cover box, so it is loaded whole with ``Gdk.Texture.new_from_bytes``,
the fast path. A real 400x400 avatar is *larger* than the box and goes
through the GdkPixbuf loader instead. The old fixture therefore measured
the optimised path for every avatar in the run and could not see the cost
the "avatars decoded to size" change is meant to pay.

Sizes here default to what instances actually serve (400px avatars,
1600x1200 media) and can be overridden, so the old regime stays
reproducible for comparison:

    BENCH_AVATAR_PX=96 BENCH_MEDIA_PX=640x480 python3 fixture_server2.py

Beyond sizes it adds the two things the old server could not report:

* **Request accounting.** Every request is counted by path+query so the
  driver can show duplicated fetches. ``/__stats`` returns the JSON, read
  over plain urllib so it never touches the client under test.
* **Simulated latency.** ``--latency-ms`` delays every response, so a run
  can be made to behave like a real instance rather than a loopback.

Images are generated from ``random.randbytes`` (C speed) and compressed
at level 1: the pixel data is incompressible, so the decode cost tracks a
real photo instead of collapsing the way a synthetic gradient would.
"""

import argparse
import collections
import http.server
import json
import os
import random
import sys
import threading
import time
import urllib.parse
import zlib

V2_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(V2_DIR))

import fixtures  # noqa: E402

# What a real instance serves. Avatars are 400px on every instance worth
# the name; media is a camera photo, not a thumbnail.
DEFAULT_AVATAR_PX = (400, 400)
DEFAULT_MEDIA_PX = (1600, 1200)
DEFAULT_CARD_PX = (400, 200)
EMOJI_PX = (32, 32)   # already right: servers publish emoji at ~32px
# JPEG, because that is what an instance actually serves and because PNG's
# decode cost is not a stand-in for it. See _image().
DEFAULT_FORMAT = 'jpeg'


def _image(width, height, seed, fmt, quality):
    """A deterministic image of exactly *width* x *height* in *fmt*.

    Content is incompressible noise, so the file is about as large as the
    format allows for those dimensions -- which is the point: an image
    that compressed to nothing would decode in microseconds and flatter
    the decoder past usefulness.

    The default format is **JPEG**, not PNG, because their decode costs
    are not interchangeable. Random bytes through zlib land close to the
    stored size, so PNG decoding is mostly a memory copy; real
    attachments are JPEG, whose inverse-DCT decoding costs several times
    more for the same pixels. Benchmarking PNG measures a decode path real
    readers do not take. GdkPixbuf writes JPEG itself, so this needs no
    new dependency; if the JPEG saver is missing the format falls back to
    PNG and ``formats`` reports it, rather than quietly measuring the
    cheaper path.
    """
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf, GLib

    rng = random.Random(seed)
    stride = width * 3
    pixels = rng.randbytes(stride * height)
    # GdkPixbuf.Pixbuf.new_from_data wants packed rows; the filter byte
    # belongs to the PNG writer below, not to the pixbuf.
    pixbuf = GdkPixbuf.Pixbuf.new_from_data(
        pixels, GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride)
    try:
        ok, buffer = pixbuf.save_to_bufferv(fmt, ['quality'], [str(quality)])
        # Depending on the PyGObject version this hands back either raw
        # bytes or a GLib.Bytes.
        if isinstance(buffer, (bytes, bytearray)):
            data = bytes(buffer)
        elif ok and buffer is not None and buffer.get_size():
            data = buffer.get_data()
        else:
            data = b''
        if ok and data:
            return data, fmt
    except GLib.Error:
        pass
    raw = b''.join(b'\x00' + pixels[y * stride:(y + 1) * stride]
                   for y in range(height))
    return _png(width, height, zlib.compress(raw, 1)), 'png'


def _png(width, height, data):
    def chunk(kind, body):
        return (len(body).to_bytes(4, 'big') + kind + body
                + zlib.crc32(kind + body).to_bytes(4, 'big'))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', width.to_bytes(4, 'big')
                    + height.to_bytes(4, 'big') + bytes((8, 2, 0, 0, 0)))
            + chunk(b'IDAT', data) + chunk(b'IEND', b''))


def _px(spec, fallback):
    """'1600x1200' or '400' -> (width, height)."""
    if not spec:
        return fallback
    text = str(spec).lower().replace('*', 'x')
    if 'x' in text:
        width, _sep, height = text.partition('x')
        return int(width), int(height)
    size = int(text)
    return size, size


class Accounting:
    """Per-path request counts, for spotting duplicated network work.

    Every request is counted, including ones this server does not
    implement: a 404 is still a request the client made, and hiding them
    makes a client that calls endpoints the fixture lacks look like one
    that made fewer requests than it did. Those are counted under their
    own path and summarised as ``not_found``, so they are visible rather
    than silently dropped.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.by_path = collections.Counter()
        self.by_exact = collections.Counter()
        self.bytes = 0
        self.images = 0
        self.not_found = 0
        self.total = 0

    def note(self, key, path, size):
        with self.lock:
            self.total += 1
            self.by_path[path] += 1
            self.by_exact[key] += 1
            self.bytes += size

    def note_image(self, key, path, size):
        with self.lock:
            self.images += 1
        self.note(key, path, size)

    def note_not_found(self, key, path):
        with self.lock:
            self.not_found += 1
        self.note(key, path, 0)

    def report(self):
        with self.lock:
            # A key seen more than once is a request the client could have
            # answered from something it already had.
            repeated = {k: n for k, n in self.by_exact.items() if n > 1}
            extra = sum(n - 1 for n in repeated.values())
            return {
                # total, not sum(by_path.values()): the two used to be the
                # same thing and should not drift apart silently.
                'requests': self.total,
                'bytes': self.bytes,
                'image_requests': self.images,
                'not_found': self.not_found,
                'duplicate_requests': extra,
                'duplicate_urls': len(repeated),
                'top_duplicated': sorted(
                    repeated.items(), key=lambda kv: -kv[1])[:5],
                'by_path': dict(self.by_path),
            }


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _send(self, code, body, kind):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.strip('/').split('/')
        if self.server.latency:
            time.sleep(self.server.latency)
        if url.path == '/__stats':
            body = json.dumps(self.server.stats()).encode()
            self._send(200, body, 'application/json')
            return
        if parts[0] == 'img' and len(parts) == 3 and parts[1] in self.server.images:
            bank = self.server.images[parts[1]]
            key = sum(ord(c) for c in parts[2])
            blob = bank[key % len(bank)]
            self.server.accounting.note_image(self.path, '/img', len(blob))
            self._send(200, blob, self.server.content_type)
            return
        if url.path == '/api/v1/timelines/public':
            base = 'http://%s:%d' % self.server.server_address[:2]
            limit = int(query.get('limit', 20))
            top = self.server.total
            if 'max_id' in query:
                top = int(query['max_id']) - 1
            bottom = int(query.get('since_id', 0)) + 1
            ids = range(top, max(bottom, 1) - 1, -1)[:limit]
            statuses = [fixtures.status(base, i, self.server.anchor)
                        for i in ids]
            body = json.dumps(statuses).encode()
            self.server.accounting.note(self.path, url.path, len(body))
            self._send(200, body, 'application/json')
            return
        if url.path == '/api/v2/instance':
            body = {'domain': 'bench.local', 'title': 'Bench',
                    'configuration': {'translation': {'enabled': False},
                                      'statuses': {'max_characters': 500}}}
            body = json.dumps(body).encode()
            self.server.accounting.note(self.path, url.path, len(body))
            self._send(200, body, 'application/json')
            return
        # Counted, not ignored: the client still spent a request and a
        # round trip on this.
        self.server.accounting.note_not_found(self.path, url.path)
        self._send(404, b'{"error": "Record not found"}', 'application/json')


def build_images(avatar_px, media_px, card_px, fmt, quality):
    """Every bank at one format, so the run decodes one way throughout."""
    banks = {
        'avatar': [400, 400],
        'emoji': list(EMOJI_PX),
        'media': list(media_px),
        'card': list(card_px),
    }
    sizes = {'avatar': avatar_px, 'media': media_px, 'card': card_px,
             'emoji': EMOJI_PX}
    images, used = {}, {}
    for name, count in (('avatar', 8), ('emoji', 6), ('media', 6), ('card', 4)):
        bank = []
        for i in range(count):
            data, used_fmt = _image(sizes[name][0], sizes[name][1], 100 * i + 7,
                                    fmt, quality)
            bank.append(data)
            used[name] = used_fmt
        images[name] = bank
    return images, used, banks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('total', type=int)
    parser.add_argument('anchor', help='ISO timestamp for id 0')
    parser.add_argument('--latency-ms', type=float, default=0.0,
                        help='delay added to every response')
    parser.add_argument('--avatar-px', default=str(DEFAULT_AVATAR_PX[0]))
    parser.add_argument('--media-px', default='%dx%d' % DEFAULT_MEDIA_PX)
    parser.add_argument('--card-px', default='%dx%d' % DEFAULT_CARD_PX)
    parser.add_argument('--format', default=DEFAULT_FORMAT,
                        choices=('jpeg', 'png'),
                        help='image format to serve (default %s: real'
                             ' attachments are JPEG and its decode cost is'
                             ' nothing like PNG\'s)' % DEFAULT_FORMAT)
    parser.add_argument('--quality', default='82')
    args = parser.parse_args()

    import datetime

    images, used, sizes = build_images(
        _px(args.avatar_px, DEFAULT_AVATAR_PX),
        _px(args.media_px, DEFAULT_MEDIA_PX),
        _px(args.card_px, DEFAULT_CARD_PX),
        args.format, args.quality)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.images = images
    server.total = args.total
    server.anchor = datetime.datetime.fromisoformat(args.anchor)
    server.latency = args.latency_ms / 1000.0
    server.accounting = Accounting()
    server.used_formats = used
    server.image_sizes = {k: list(v) for k, v in sizes.items()}
    # One content type for everything, so the client never has to guess.
    server.content_type = ('image/jpeg'
                           if all(f == 'jpeg' for f in used.values())
                           else 'image/png')

    def stats():
        report = server.accounting.report()
        report['formats'] = used
        report['image_sizes'] = server.image_sizes
        report['content_type'] = server.content_type
        return report

    server.stats = stats
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
