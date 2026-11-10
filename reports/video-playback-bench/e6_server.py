"""E6 test server: plain, redirecting and no-Range endpoints.

e6_server.py PORT MEDIA_DIR
  /f/<name>        Range-capable file (206 on Range)
  /r/<name>        302 Found -> /f/<name>
  /norange/<name>  always 200 with the whole body, no Accept-Ranges
"""
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, ROOT = int(sys.argv[1]), sys.argv[2]


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def do_GET(self):
        parts = self.path.split('?')[0].split('/')
        kind, name = (parts[1], parts[2]) if len(parts) > 2 else ('', '')
        path = os.path.join(ROOT, os.path.basename(name))
        if kind == 'r':
            self.send_response(302)
            self.send_header('Location', '/f/' + name)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if kind not in ('f', 'norange') or not os.path.isfile(path):
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
        if kind == 'f' and m and m.group(1):
            start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        else:
            self.send_response(200)
        if kind == 'f':
            self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    data = f.read(min(65536, left))
                    self.wfile.write(data)
                    left -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass


ThreadingHTTPServer.daemon_threads = True
ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
