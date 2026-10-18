#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-level tests for MastodonClient (no network, no display).

These drive the real libsoup calls against a loopback listener, because
the bugs they cover are all in the bytes on the wire: a multipart body a
server cannot split, a rate-limit window read off the wrong clock, a
lost response turned into a second post. A test that stops at the
request object would have passed every one of those.
"""

import email.utils
import json
import os
import socket
import sys
import threading
import time

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

import gi  # noqa: E402

gi.require_version('Soup', '3.0')

from gi.repository import Gio, GLib  # noqa: E402

from src.mastodon import (DEFAULT_RATE_LIMIT_SECONDS,  # noqa: E402
                          MastodonClient, parse_reset_instant,
                          server_clock_offset)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


class Server:
    """A loopback HTTP/1.1 listener that records what it was sent.

    ``script`` is a list of behaviours, one per accepted connection:
    ``'drop'`` reads the request and closes without answering, anything
    else is answered with ``reply``.
    """

    def __init__(self, reply=b'{"id":"1"}', script=None):
        self.requests = []
        self.reply = reply
        self.script = list(script or ['reply'])
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn):
        try:
            data = b''
            while b'\r\n\r\n' not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _, body = data.partition(b'\r\n\r\n')
            length = 0
            for line in head.split(b'\r\n'):
                if line.lower().startswith(b'content-length:'):
                    length = int(line.split(b':', 1)[1])
            while len(body) < length:
                body += conn.recv(65536)
            self.requests.append((head, body))
            behaviour = (self.script.pop(0) if self.script else 'reply')
            if behaviour == 'drop':
                return
            self.reply = behaviour if isinstance(behaviour, bytes) \
                else self.reply
            conn.sendall(
                b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                b'Content-Length: %d\r\nConnection: close\r\n\r\n%s'
                % (len(self.reply), self.reply))
        finally:
            conn.close()

    def close(self):
        self.sock.close()


def run_loop(function, seconds=20):
    """Run *function* on the main loop, which returns the loop to run."""
    context = GLib.MainContext.default()
    GLib.idle_add(function)
    deadline = time.monotonic() + seconds
    while context.pending() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


def client_for(port, token='t'):
    """A client pointed at a loopback port.

    normalize_instance() refuses plaintext on purpose, so the endpoint is
    set directly. The session timeout is cut to a second: libsoup does not
    report a peer that closes without answering until the session timeout
    expires, and HTTP_TIMEOUT_SECONDS is 30, which no test should sit
    through.
    """
    client = MastodonClient('https://placeholder.invalid', token=token)
    client.instance = 'http://127.0.0.1:%d' % port
    client.api_base = client.instance + '/api/v1'
    client._session.set_timeout(1)
    return client


# --- multipart upload: the request must be one a server can split ---

server = Server(reply=b'{"id":"7","url":"https://x/1.png"}')
client = client_for(server.port)
outcome = {}
task = client.media_upload_async(b'\x89PNG\r\n\x1a\n' + b'payload' * 8,
                                 'shot.png', 'image/png',
                                 description='a screenshot')


def finish_upload():
    try:
        outcome['data'] = client.media_upload_finish(task)
    except GLib.Error as failure:
        outcome['error'] = failure.message
    return GLib.SOURCE_REMOVE


run_loop(finish_upload)
head, body = server.requests[0] if server.requests else (b'', b'')
headers = {}
for line in head.split(b'\r\n')[1:]:
    name, _, value = line.partition(b':')
    headers[name.strip().lower()] = value.strip()

check('multipart upload completes', 'data' in outcome, str(outcome))
check('multipart upload is not an error', 'error' not in outcome,
      str(outcome.get('error', '')))
check('Content-Type names a boundary',
      headers.get(b'content-type', b'').startswith(b'multipart/form-data')
      and b'boundary=' in headers.get(b'content-type', b''),
      repr(headers.get(b'content-type')))
boundary = headers.get(b'content-type', b'').split(b'boundary=', 1)[-1]
check('boundary actually delimits the body', boundary in body)
check('the file part is named', b'name="file"' in body)
check('the description field travelled', b'name="description"' in body
      and b'a screenshot' in body)
check('the filename travelled', b'filename="shot.png"' in body)
check('the payload is intact', b'payload' * 8 in body)
check('the bearer token travelled', headers.get(b'authorization') == b'Bearer t',
      repr(headers.get(b'authorization')))
server.close()


# --- a GLib.Bytes body is accepted, so an upload is not copied twice ---

server = Server(reply=b'{"id":"8"}')
client = client_for(server.port)
client.media_upload_async(GLib.Bytes.new(b'raw-gbytes-body'),
                          'a.bin', 'application/octet-stream')
run_loop(lambda: (time.sleep(0.4), GLib.SOURCE_REMOVE)[1])
check('a GLib.Bytes file body is sent as-is',
      bool(server.requests) and b'raw-gbytes-body' in server.requests[0][1])
server.close()


# --- a lost response must not become a second post ---

server = Server(script=['drop', b'{"id":"9"}'])
client = client_for(server.port)
client.post_status_async('hello world')
# The dropped connection only surfaces at the session timeout, and the
# retry would add a backoff on top of that.
run_loop(lambda: (time.sleep(3.0), GLib.SOURCE_REMOVE)[1])
check('a dropped POST is not repeated', len(server.requests) == 1,
      '%d requests arrived' % len(server.requests))
server.close()

server = Server(script=['drop', b'{"id":"9"}'])
client = client_for(server.port)
client.timelines_home_async()
run_loop(lambda: (time.sleep(3.0), GLib.SOURCE_REMOVE)[1])
check('a dropped GET is repeated', len(server.requests) == 2,
      '%d requests arrived' % len(server.requests))
server.close()


# --- a form field that is None is dropped, not sent as "None" ---

server = Server(reply=b'{"access_token":"t"}')
client = client_for(server.port)
client.request_token_async('code')
run_loop(lambda: (time.sleep(0.4), GLib.SOURCE_REMOVE)[1])
sent = server.requests[0][1] if server.requests else b''
check('no field reads client_secret=None', b'client_secret=None' not in sent,
      repr(sent))
server.close()


# --- X-RateLimit-Reset, in the two shapes servers actually send ---

def iso(epoch):
    """The shape Mastodon 4.0 and later send for X-RateLimit-Reset."""
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(epoch)) + '.000000Z'


SERVER_NOW = time.time()
reset_iso = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(SERVER_NOW + 600))
check('an ISO-8601 reset parses to its instant',
      parse_reset_instant(reset_iso + '.701542Z') == int(SERVER_NOW + 600))
check('an ISO-8601 reset with zeroed fraction parses',
      parse_reset_instant(iso(SERVER_NOW + 600)) == int(SERVER_NOW + 600))

# Captured verbatim from https://mastodon.social/api/v1/timelines/public on
# 2026-10-16, so a change in Mastodon's spelling breaks this rather than
# quietly reverting the client to DEFAULT_RATE_LIMIT_SECONDS.
check('the literal mastodon.social reset parses',
      parse_reset_instant('2026-10-16T11:20:00.701542Z') == 1792149600)
check('a Unix-epoch reset still parses',
      parse_reset_instant(str(int(SERVER_NOW + 600)))
      == int(SERVER_NOW + 600))
check('a fractional epoch reset parses',
      abs(parse_reset_instant('%.6f' % (SERVER_NOW + 600))
          - (SERVER_NOW + 600)) < 1)
check('an absent reset is None', parse_reset_instant(None) is None)
check('an empty reset is None', parse_reset_instant('   ') is None)
check('a junk reset is None', parse_reset_instant('soon') is None)
check('a malformed calendar date is None',
      parse_reset_instant('2026-13-45T99:99:99Z') is None)
check('an invalid month is None',
      parse_reset_instant('2026-02-30T00:00:00Z') is None)
check('an HTTP-date reset is not an instant',
      parse_reset_instant('Wed, 16 Oct 2026 12:00:00 GMT') is None)
check('a nan reset is None', parse_reset_instant('nan') is None)
check('an infinite reset is None', parse_reset_instant('inf') is None)
check('a negative reset is None', parse_reset_instant('-1') is None)


check('a garbage reset does not raise out of the error path',
      parse_reset_instant('soon') is None)


# --- the Date header corrects the skew ---

date_now = SERVER_NOW + 7200  # the server's clock runs two hours ahead
date_header = email.utils.formatdate(date_now, usegmt=True)
check('Date reports the server running ahead',
      abs(server_clock_offset(date_header, SERVER_NOW) - 7200) < 2)
check('an absent Date corrects nothing',
      server_clock_offset(None, SERVER_NOW) == 0.0)
check('a junk Date corrects nothing',
      server_clock_offset('whenever', SERVER_NOW) == 0.0)
check('a Date two days out is ignored, not obeyed',
      server_clock_offset(
          email.utils.formatdate(SERVER_NOW + 400000, usegmt=True),
          SERVER_NOW) == 0.0)


# --- and the two together give the window the server actually asked for ---

# The 429 bookkeeping is reached through the client's own header reader
# rather than a server, because a real 429 needs a server that holds the
# connection and answers late.
class Headers:
    def __init__(self, **values):
        self._values = values

    def get_one(self, name):
        return self._values.get(name)


server.close()
client = MastodonClient('https://example.social')
seconds = client._note_rate_limit(Headers(
    **{'X-RateLimit-Reset': reset_iso + '.701542Z',
       'Date': email.utils.formatdate(SERVER_NOW, usegmt=True)}))
check('a Mastodon-shaped 429 is honoured, not defaulted',
      595 <= seconds <= 605, '%s seconds (default is %s)'
      % (seconds, DEFAULT_RATE_LIMIT_SECONDS))

client = MastodonClient('https://example.social')
# The scenario is a server whose clock runs two hours ahead, so its reset
# is in the *server's* frame: two hours plus ten minutes on. Uncorrected
# that reads as twelve hours and clamps to the one-hour cap.
seconds = client._note_rate_limit(Headers(
    **{'X-RateLimit-Reset': iso(SERVER_NOW + 7200 + 600),
       'Date': email.utils.formatdate(SERVER_NOW + 7200, usegmt=True)}))
check('a 429 answered by a clock two hours ahead keeps the window',
      595 <= seconds <= 605, '%s seconds' % seconds)

client = MastodonClient('https://example.social')
# And the mirror: a server two hours behind has a reset that looks two
# hours stale to us, which uncorrected collapses to a single second and
# deepens the limit the server just described.
seconds = client._note_rate_limit(Headers(
    **{'X-RateLimit-Reset': iso(SERVER_NOW - 7200 + 600),
       'Date': email.utils.formatdate(SERVER_NOW - 7200, usegmt=True)}))
check('a 429 answered by a clock two hours behind keeps the window',
      595 <= seconds <= 605, '%s seconds' % seconds)

client = MastodonClient('https://example.social')
check('a 429 with nothing usable falls back to the default',
      client._note_rate_limit(Headers()) == DEFAULT_RATE_LIMIT_SECONDS)

client = MastodonClient('https://example.social')
check('Retry-After still wins when a server sends it',
      client._note_rate_limit(Headers(**{'Retry-After': '42'})) == 42)


# --- register_app_finish raises GLib.Error, never KeyError ---

class Task:
    def __init__(self, payload):
        self.payload = payload

    def propagate_value(self):
        return GLib.Variant('s', json.dumps(
            {'data': self.payload, 'link': ''}))


client = MastodonClient('https://example.social')
try:
    client.register_app_finish(Task({'client_name': 'Babble'}))
except GLib.Error:
    check('a registration without client_id raises GLib.Error', True)
except KeyError as failure:
    check('a registration without client_id raises GLib.Error', False,
          'raised KeyError(%s) through the callback' % failure)
else:
    check('a registration without client_id raises GLib.Error', False,
          'raised nothing')

client = MastodonClient('https://example.social')
client.register_app_finish(Task({'client_id': 'a', 'client_secret': 'b'}))
check('a normal registration still records the credentials',
      client.client_id == 'a' and client.client_secret == 'b')


# --- a response is parsed once and handed through the task as is ---

class Body:
    def __init__(self, text):
        self.text = text

    def get_data(self):
        return self.text.encode()


class Session:
    def __init__(self, text):
        self.text = text

    def send_and_read_finish(self, _result):
        return Body(self.text)


class ResponseHeaders:
    def get_one(self, name):
        return ('<https://example.social/api/v1/timelines/home?max_id=7>;'
                ' rel="next"') if name == 'Link' else None


class Path:
    def get_path(self):
        return '/api/v1/timelines/home'


class Message:
    def get_status(self):
        return 200

    def get_response_headers(self):
        return ResponseHeaders()

    def get_method(self):
        return 'GET'

    def get_uri(self):
        return Path()


loads = []
real_loads = json.loads


def counting_loads(*args, **kwargs):
    loads.append(1)
    return real_loads(*args, **kwargs)


finished = []
client = MastodonClient('https://example.social')
client._session = Session(json.dumps([{'id': '9', 'content': '<p>hi</p>'}]))
task = Gio.Task.new(client, None,
                    lambda c, result: finished.append(
                        c.request_finish_paged(result)))
client._pending[task] = Message()
json.loads = counting_loads
try:
    client._on_response(None, None, task)
    context = GLib.MainContext.default()
    while not finished and context.iteration(False):
        pass
finally:
    json.loads = real_loads
data, older, _newer = finished[0] if finished else (None, None, None)
check('a page arrives through the task with its Link cursor',
      bool(data) and data[0]['id'] == '9' and older == {'max_id': '7'},
      repr(finished))
check('a response body is parsed exactly once', len(loads) == 1,
      '%d json.loads calls' % len(loads))

if failures:
    print('%d failure(s): %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all transport checks passed')
