# SPDX-License-Identifier: GPL-3.0-or-later
"""Local stand-in for a Mastodon instance: the public timeline with
max_id/since_id paging, a minimal instance document and PNG images for
avatars, emoji, media and cards, and an Ogg/Theora clip for every GIFV
attachment (``/clip/*``, with Range). Prints the bound port on stdout."""

import datetime
import http.server
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402

TOTAL = int(sys.argv[1])
ANCHOR = datetime.datetime.fromisoformat(sys.argv[2])

IMAGES = {
    'avatar': [fixtures.png(96, 96, i) for i in range(8)],
    'emoji': [fixtures.png(32, 32, 100 + i) for i in range(6)],
    'media': [fixtures.png(640, 480, 200 + i) for i in range(6)],
    'card': [fixtures.png(400, 200, 300 + i) for i in range(4)],
}


def make_clip(seconds=40):
    """An Ogg/Theora clip of a bouncing ball, 160x120 at 10 fps (the
    4:3 of the PNG posters), encoded once with GStreamer's base plugins.

    Long on purpose: GStreamer 1.28.8 cannot seek an Ogg stream back to its
    start over HTTP once it has ended, so a clip that ends inside a run
    would fail its loop. The workload lasts about 8 s; the clip, 40 s."""
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    directory = tempfile.mkdtemp(prefix='babble-bench-clip-')
    path = os.path.join(directory, 'clip.ogg')
    try:
        pipeline = Gst.parse_launch(
            'videotestsrc num-buffers=%d pattern=ball ! '
            'video/x-raw,width=160,height=120,framerate=10/1 ! '
            'theoraenc bitrate=64 ! oggmux ! filesink location=%s'
            % (seconds * 10, path))
        pipeline.set_state(Gst.State.PLAYING)
        message = pipeline.get_bus().timed_pop_filtered(
            60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        pipeline.set_state(Gst.State.NULL)
        if message is None or message.type == Gst.MessageType.ERROR:
            raise SystemExit('fixture server: could not encode the clip')
        with open(path, 'rb') as handle:
            return handle.read()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


CLIP = make_clip()


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

    def _send_clip(self):
        """The clip, with Range answers: souphttpsrc reads an Ogg stream
        with them."""
        start, end, code = 0, len(CLIP) - 1, 200
        header = self.headers.get('Range')
        if header and header.startswith('bytes='):
            first, _dash, last = header[6:].partition('-')
            if first:
                start = int(first)
                end = int(last) if last else len(CLIP) - 1
            else:
                start = max(len(CLIP) - int(last), 0)
            end = min(end, len(CLIP) - 1)
            code = 206
        self.send_response(code)
        self.send_header('Content-Type', 'video/ogg')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        if code == 206:
            self.send_header('Content-Range',
                             'bytes %d-%d/%d' % (start, end, len(CLIP)))
        self.end_headers()
        try:
            self.wfile.write(CLIP[start:end + 1])
        except OSError:
            pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.strip('/').split('/')
        if parts[0] == 'img' and len(parts) == 3 and parts[1] in IMAGES:
            bank = IMAGES[parts[1]]
            key = sum(ord(c) for c in parts[2])
            self._send(200, bank[key % len(bank)], 'image/png')
            return
        if parts[0] == 'clip' and len(parts) == 2:
            self._send_clip()
            return
        if url.path == '/api/v1/timelines/public':
            base = 'http://%s:%d' % self.server.server_address[:2]
            limit = int(query.get('limit', 20))
            top = TOTAL
            if 'max_id' in query:
                top = int(query['max_id']) - 1
            bottom = int(query.get('since_id', 0)) + 1
            ids = range(top, max(bottom, 1) - 1, -1)[:limit]
            body = [fixtures.status(base, i, ANCHOR) for i in ids]
            self._send(200, json.dumps(body).encode(), 'application/json')
            return
        if url.path == '/api/v2/instance':
            body = {'domain': 'bench.local', 'title': 'Bench',
                    'configuration': {'translation': {'enabled': False},
                                      'statuses': {'max_characters': 500}}}
            self._send(200, json.dumps(body).encode(), 'application/json')
            return
        self._send(404, b'{"error": "Record not found"}', 'application/json')


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server.daemon_threads = True
print(server.server_address[1], flush=True)
server.serve_forever()
