#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixture of the media tests: a tiny Ogg/Theora clip.

Not a test (the name keeps it out of the install list) and not run by
Meson; the media tests load it with ``load()``. Theora and Ogg are in
GStreamer's base plugins, so no decoder beyond what GTK needs is required.
"""

import base64
import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import tempfile  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

# A one-second 32x32 Theora clip in Ogg: enough for GstPlay to have a
# pipeline with a decoder and a sink in it, small enough to carry in the
# test. Theora and the Ogg demuxer are in GStreamer's base plugins,
# which GTK itself needs -- an H.264 clip also needs a decoder that GTK
# does not, and without one the stream never plays (see `prime`).
CLIP = (
    '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'
)

def write_clip(directory=None):
    """The clip written to *directory* (a fresh temp dir) as clip.ogg;
    returns its path."""
    directory = directory or tempfile.mkdtemp(prefix='babble-media-')
    path = os.path.join(directory, 'clip.ogg')
    with open(path, 'wb') as handle:
        handle.write(base64.b64decode(CLIP))
    return path


def serve(directory):
    """Serve *directory* on 127.0.0.1 from a thread; returns
    (base_url, stop). Plain http.server: no Range support."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
    return 'http://127.0.0.1:%d' % server.server_address[1], stop


def write_long_clip(directory, seconds=20, kbps=300):
    """A *seconds*-long Theora/Ogg clip encoded now with GStreamer's
    base plugins (theoraenc, oggmux), written as long.ogg: big enough that
    a throttled server needs seconds to send it, while the first frame is
    in the first few KB. Returns its path."""
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    path = os.path.join(directory, 'long.ogg')
    pipeline = Gst.parse_launch(
        'videotestsrc num-buffers=%d pattern=snow ! '
        'video/x-raw,width=160,height=120,framerate=10/1 ! '
        'theoraenc bitrate=%d ! oggmux name=mux' % (seconds * 10, kbps))
    sink = Gst.ElementFactory.make('filesink')
    sink.set_property('location', path)
    pipeline.add(sink)
    pipeline.get_by_name('mux').link(sink)
    pipeline.set_state(Gst.State.PLAYING)
    bus = pipeline.get_bus()
    message = bus.timed_pop_filtered(
        60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipeline.set_state(Gst.State.NULL)
    if message is None or message.type == Gst.MessageType.ERROR:
        raise RuntimeError('could not encode the long clip')
    return path


class MediaServer:
    """A loopback server that streams one file the way a CDN does.

    * ``/media.ogg``: the file, with ``Accept-Ranges`` and 206 answers.
      When ``rate`` is set, every response sends its first ``burst`` bytes
      at once (the head and the tail a demuxer reads to find the
      duration) and the rest at ``rate`` bytes per second;
    * ``/redirect.ogg``: a 302 to ``/media.ogg``;
    * ``/norange.ogg``: the file, always 200, ``Accept-Ranges: none``.
      A server that ignores Range and sends no ``Accept-Ranges`` at all
      fails the preroll for a demuxer that reads the tail (Ogg: "Server
      does not support seeking"); it plays a faststart MP4, only
      unseekable (measured). Babble has deliberately no download
      fallback: the user gets the error page with Retry and Open in
      Browser. A server that says "none" plays;
    * anything else: 404.

    ``bytes_served`` counts the body bytes written, ``agents`` holds the
    User-Agent of every request, and ``requests`` the paths.
    """

    CHUNK = 256

    def __init__(self, path, rate=None, burst=65536):
        with open(path, 'rb') as handle:
            self.data = handle.read()
        self.rate = rate
        self.burst = burst
        self.bytes_served = 0
        self.agents = []
        self.requests = []
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def handle(self):
                # A client that hangs up mid-body (a closed viewer) is
                # the point of some tests, not an error.
                try:
                    super().handle()
                except OSError:
                    pass

            def do_GET(self):
                outer._handle(self)

            def do_HEAD(self):
                outer._handle(self, head=True)

        self.server = http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def _handle(self, request, head=False):
        with self._lock:
            self.agents.append(request.headers.get('User-Agent'))
            self.requests.append(request.path)
        path = request.path
        if path == '/redirect.ogg':
            request.send_response(302)
            request.send_header('Location', '/media.ogg')
            request.send_header('Content-Length', '0')
            request.end_headers()
            return
        if path not in ('/media.ogg', '/norange.ogg'):
            body = b'not found'
            request.send_response(404)
            request.send_header('Content-Length', str(len(body)))
            request.end_headers()
            request.wfile.write(body)
            return
        data = self.data
        start, end, status = 0, len(data) - 1, 200
        ranges = path == '/media.ogg'
        header = request.headers.get('Range')
        if ranges and header and header.startswith('bytes='):
            first, _dash, last = header[6:].partition('-')
            if first:
                start = int(first)
                end = int(last) if last else len(data) - 1
            else:
                start = max(len(data) - int(last), 0)
            end = min(end, len(data) - 1)
            status = 206
        request.send_response(status)
        request.send_header('Content-Type', 'video/ogg')
        request.send_header('Content-Length', str(end - start + 1))
        if ranges:
            request.send_header('Accept-Ranges', 'bytes')
        else:
            request.send_header('Accept-Ranges', 'none')
        if status == 206:
            request.send_header(
                'Content-Range', 'bytes %d-%d/%d' % (start, end, len(data)))
        request.end_headers()
        if head:
            return
        position = start
        try:
            while position <= end:
                chunk = data[position:min(position + self.CHUNK, end + 1)]
                request.wfile.write(chunk)
                request.wfile.flush()
                with self._lock:
                    self.bytes_served += len(chunk)
                position += len(chunk)
                if self.rate and position - start >= self.burst:
                    time.sleep(len(chunk) / self.rate)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
