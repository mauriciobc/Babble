"""Range-capable HTTP server with a bandwidth cap and a byte counter.

GET /stats -> bytes served since last /reset. GET /reset clears it.
Usage: server.py PORT MEDIA_DIR BYTES_PER_SEC
"""
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, ROOT, RATE = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
lock = threading.Lock()
served = {'bytes': 0, 'requests': 0, 'ranges': 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _small(self, body):
        body = body.encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        if self.path == '/stats':
            with lock:
                return self._small('%(bytes)d %(requests)d %(ranges)d' % served)
        if self.path == '/reset':
            with lock:
                served.update(bytes=0, requests=0, ranges=0)
            return self._small('ok')
        path = os.path.join(ROOT, os.path.basename(self.path.split('?')[0]))
        if not os.path.isfile(path):
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r'bytes=(\d*)-(\d*)', self.headers.get('Range', ''))
        with lock:
            served['requests'] += 1
        if m:
            with lock:
                served['ranges'] += 1
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = size - int(m.group(2))
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        else:
            self.send_response(200)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        if head:
            return
        chunk = 16384
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            t0 = time.monotonic()
            sent = 0
            try:
                while left > 0:
                    data = f.read(min(chunk, left))
                    self.wfile.write(data)
                    left -= len(data)
                    sent += len(data)
                    with lock:
                        served['bytes'] += len(data)
                    ahead = sent / RATE - (time.monotonic() - t0)
                    if ahead > 0:
                        time.sleep(ahead)
            except (BrokenPipeError, ConnectionResetError):
                pass


ThreadingHTTPServer.daemon_threads = True
ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
