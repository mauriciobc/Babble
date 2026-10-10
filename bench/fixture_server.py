# SPDX-License-Identifier: GPL-3.0-or-later
"""Local stand-in for a Mastodon instance: the public timeline with
max_id/since_id paging, a minimal instance document and PNG images for
avatars, emoji, media and cards. Prints the bound port on stdout."""

import datetime
import http.server
import json
import os
import sys
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
        if parts[0] == 'img' and len(parts) == 3 and parts[1] in IMAGES:
            bank = IMAGES[parts[1]]
            key = sum(ord(c) for c in parts[2])
            self._send(200, bank[key % len(bank)], 'image/png')
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
