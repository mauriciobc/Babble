#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recorder and fixtures shared by the golden-request scenarios.

The entry script registers the resource bundle and calls Adw.init() before
importing this; the module replaces the transport chokepoint and every
dialog/toast stub, and keeps the scenario registry the parts append to.
"""

import json
import os
import re
import urllib.parse

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import GLib, GObject, Soup

from src import feedback
from src import profiledialogs
from src import statusactions
from src import statusmenu
from src.mastodon import MastodonClient
from src.statusactions import StatusActions
from src.timelinedata import StatusItem

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()

GOLDEN = os.path.join(srcdir, 'golden', 'api-requests.json')

UPDATE = bool(os.environ.get('BABBLE_GOLDEN_UPDATE'))

MUTATE = bool(os.environ.get('BABBLE_GOLDEN_MUTATE'))

INSTANCE = 'https://golden.example'

TOKEN = 'golden-token'

ME = '1'

failures = []

ALLOW_NO_REQUESTS = {
    'menu delete undone': 'Undo cancels the delete before it is sent',
}

def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)
        if detail:
            print(detail)

_body_of = {}

def _capture_body(self, content_type, body):
    data = body.get_data() if body is not None else b''
    self._golden_body = (content_type, bytes(data or b''))
    return _real_set_body(self, content_type, body)

_real_set_body = Soup.Message.set_request_body_from_bytes

Soup.Message.set_request_body_from_bytes = _capture_body

def _describe_body(message):
    captured = getattr(message, '_golden_body', None)
    if captured is None:
        return None
    content_type, data = captured
    base = (content_type or '').split(';')[0].strip()
    if base == 'application/json':
        return {'json': json.loads(data.decode())}
    if base == 'application/x-www-form-urlencoded':
        pairs = urllib.parse.parse_qsl(data.decode(),
                                       keep_blank_values=True)
        return {'form': sorted([k, v] for k, v in pairs)}
    if base == 'multipart/form-data':
        match = re.search(r'boundary="?([^";]+)"?', content_type or '')
        parts = []
        if match:
            boundary = match.group(1).encode()
            for chunk in data.split(b'--' + boundary):
                chunk = chunk.strip(b'\r\n')
                if not chunk or chunk == b'--':
                    continue
                head, _sep, value = chunk.partition(b'\r\n\r\n')
                text = head.decode('utf-8', 'replace')
                name = re.search(r'name="([^"]*)"', text)
                filename = re.search(r'filename="([^"]*)"', text)
                ctype = re.search(r'Content-Type:\s*([^\r\n]+)', text, re.I)
                part = {'name': name.group(1) if name else None}
                if filename:
                    part['filename'] = filename.group(1)
                    part['content_type'] = ctype.group(1) if ctype else None
                    part['bytes'] = len(value)
                else:
                    part['value'] = value.decode('utf-8', 'replace')
                parts.append(part)
        return {'multipart': parts}
    return {'raw': {'content_type': content_type, 'bytes': len(data)}}

def _headers(message):
    seen = {}

    def each(name, value, *_rest):
        if name.lower() == 'content-length':
            return  # derived from the body, and from the random boundary
        if name.lower() == 'authorization':
            value = 'Bearer <token>' if value == 'Bearer ' + TOKEN \
                else value
        elif name.lower() == 'idempotency-key':
            value = '<key>'
        elif name.lower() == 'content-type' and 'boundary=' in value:
            value = re.sub(r'boundary="?[^";]+"?', 'boundary=<boundary>',
                           value)
        seen[name] = value

    message.get_request_headers().foreach(each)
    return dict(sorted(seen.items()))

DEFAULT_INFO = {'api_versions': {'mastodon': 12}}

class Recorder:
    """The transport stand-in; one live at a time."""

    def __init__(self):
        self.log = []
        self.toasts = []
        self.fail = {}
        # Phase 2: richer answers, and queued ones (the n-th request to a
        # path gets the n-th answer, the last one repeating). An answer is
        # data, or (data, Link header).
        self.rich = False
        self.queue = {}
        # Widgets a scenario builds and does not touch again, kept alive
        # until their answers have arrived.
        self._kept = []
        self.outstanding = 0
        self._tasks = []

    def record(self, task, message):
        uri = message.get_uri()
        query = urllib.parse.parse_qsl(uri.get_query() or '',
                                       keep_blank_values=True)
        entry = {
            'method': message.get_method(),
            'host': '%s://%s' % (uri.get_scheme(), uri.get_host()),
            'path': uri.get_path(),
            'query': sorted([k, v] for k, v in query),
            'headers': _headers(message),
            'body': _describe_body(message),
        }
        if MUTATE and not self.log and entry['path'].endswith('favourite'):
            entry['path'] += 'X'
        self.log.append(entry)
        self.outstanding += 1
        self._tasks.append(task)
        GLib.idle_add(self._complete, task, entry)

    def _complete(self, task, entry):
        self.outstanding -= 1
        key = (entry['method'], entry['path'])
        codes = self.fail.get(key)
        status = codes.pop(0) if codes else 200
        if status >= 400:
            task.return_error(GLib.Error(
                'canned %d' % status, 'mastodon-http-error-quark', status))
        else:
            data, link = self.answer(entry, key)
            task.return_value(GObject.Value(
                GObject.TYPE_PYOBJECT, {'data': data, 'link': link}))
        return GLib.SOURCE_REMOVE

    def answer(self, entry, key):
        queued = self.queue.get(key)
        if queued:
            answer = queued.pop(0) if len(queued) > 1 else queued[0]
            if isinstance(answer, tuple):
                return answer
            return answer, ''
        if self.rich:
            from testkit.goldenanswers import MISSING, rich
            found = rich(entry['method'], entry['path'],
                         dict(entry['query']))
            if found is not MISSING:
                return found, ''
        return canned(entry['method'], entry['path']), ''

    def settle(self):
        context = GLib.MainContext.default()
        for _spin in range(2000):
            while context.iteration(False):
                pass
            if not self.outstanding:
                # One more pass: a completion may have queued callbacks.
                while context.iteration(False):
                    pass
                if not self.outstanding:
                    return
        raise RuntimeError('requests never settled')

    def keep(self, widget):
        self._kept.append(widget)
        return widget

    def clear(self):
        self.settle()
        self.log.clear()
        self.toasts.clear()

    def client(self, info=None):
        client = MastodonClient(INSTANCE, token=TOKEN)
        # A recorded server is a current one (API 12, Mastodon 4.8), so the
        # version gates of capabilities.py (audit 5c) are all open and the
        # scenarios record the request of the feature, not of its absence.
        # A scenario that is about an older server passes its own `info`.
        client.instance_info = DEFAULT_INFO if info is None else info
        # Remember whose client this is: see `_spawn`.
        client._golden_recorder = self
        return client

    def press_undo(self):
        toast = self.toasts[-1]
        toast['undo']()
        if toast['dismissed']:
            toast['dismissed']()
        self.settle()

    def time_out(self):
        toast = self.toasts[-1]
        if toast['dismissed']:
            toast['dismissed']()
        self.settle()

CURRENT = None

def _spawn(self, task, message):
    # A request goes to the log of the scenario that built its client, not
    # of whichever scenario is running when it is sent. A widget a scenario
    # leaves behind can send later -- the list editor's search is debounced
    # by a 300 ms timer that `_run_search()` does not cancel -- and on a
    # busy machine that landed in the next scenario's log. A client built
    # any other way still records into the running scenario.
    getattr(self, '_golden_recorder', CURRENT).record(task, message)

MastodonClient._spawn = _spawn

def account(account_id='7', acct='ada', **extra):
    data = {'id': account_id, 'username': acct.split('@')[0], 'acct': acct,
            'display_name': 'Ada', 'url': 'https://golden.example/@ada',
            'avatar': '', 'header': '', 'note': '', 'fields': [],
            'emojis': [], 'locked': False, 'bot': False,
            'followers_count': 0, 'following_count': 0, 'statuses_count': 0,
            'created_at': '2026-01-01T00:00:00.000Z'}
    data.update(extra)
    return data

def status(status_id='100', **extra):
    data = {'id': status_id, 'uri': 'https://golden.example/s/' + status_id,
            'url': 'https://golden.example/@ada/' + status_id,
            'content': '<p>hello</p>', 'language': 'fr',
            'created_at': '2026-01-01T00:00:00.000Z',
            'visibility': 'public', 'account': account(),
            'favourited': False, 'reblogged': False, 'bookmarked': False,
            'muted': False, 'pinned': False, 'favourites_count': 0,
            'reblogs_count': 0, 'replies_count': 0, 'mentions': [],
            'media_attachments': [], 'emojis': [], 'tags': [],
            'spoiler_text': '', 'sensitive': False}
    data.update(extra)
    return data

_STATUS_VERBS = {
    'favourite': ('favourited', True), 'unfavourite': ('favourited', False),
    'reblog': ('reblogged', True), 'unreblog': ('reblogged', False),
    'bookmark': ('bookmarked', True), 'unbookmark': ('bookmarked', False),
    'mute': ('muted', True), 'unmute': ('muted', False),
    'pin': ('pinned', True), 'unpin': ('pinned', False)}

def canned(method, path):
    parts = [p for p in path.split('/') if p]
    if path == '/api/v1/accounts/verify_credentials':
        return account(ME, 'me')
    if path == '/api/v1/accounts/lookup':
        return account()
    if path == '/api/v1/accounts/relationships':
        return [{'id': '7'}]
    if method == 'GET' and path.startswith('/api/v1/accounts/') \
            and len(parts) == 5:
        return []
    if path.startswith('/api/v1/accounts/') and len(parts) == 5:
        verb = parts[4]
        return {'id': parts[3], 'following': verb == 'follow',
                'muting': verb == 'mute', 'blocking': verb == 'block',
                'endorsed': verb in ('endorse', 'pin'),
                'showing_reblogs': True, 'notifying': False,
                'domain_blocking': False, 'followed_by': False,
                'requested': False, 'note': ''}
    if path == '/api/v2/media':
        return {'id': '500', 'type': 'image', 'url': None}
    if path.startswith('/api/v1/media/'):
        return {'id': parts[3], 'type': 'image', 'url': 'https://x/y'}
    if path.startswith('/api/v1/statuses/') and len(parts) >= 4:
        status_id = parts[3]
        if len(parts) == 5 and parts[4] == 'translate':
            return {'content': '<p>translated</p>', 'language': 'en',
                    'detected_source_language': 'fr', 'provider': 'x',
                    'media_attachments': [], 'poll': None,
                    'spoiler_text': ''}
        if len(parts) == 5 and parts[4] == 'source':
            return {'id': status_id, 'text': 'hello', 'spoiler_text': ''}
        if len(parts) == 5 and parts[4] in (
                'history', 'favourited_by', 'reblogged_by', 'quotes'):
            return []
        if len(parts) == 5 and parts[4] in _STATUS_VERBS:
            key, value = _STATUS_VERBS[parts[4]]
            return status(status_id, **{key: value})
        return status(status_id)
    if path == '/api/v1/statuses':
        return status('900')
    return {}

class FakeWindow:
    account = {'id': ME}
    navigation_view = None
    client = None

def fake_toast(widget, text, undo=None, undo_label=None, dismissed=None):
    CURRENT.toasts.append({'text': text, 'undo': undo,
                           'dismissed': dismissed})
    return object()

def fake_confirm(widget, heading, body, action_label, callback,
                 destructive=True):
    callback()


feedback.show_toast = fake_toast

feedback.confirm = fake_confirm

statusactions._flag = lambda name: False

statusactions._has_schema = lambda: False

statusmenu._flag = lambda name: False

statusmenu._open_composer = lambda *args: None

profiledialogs.present_mute_dialog = \
    lambda parent, handle, on_confirm: on_confirm(3600, True)

SCENARIOS = []

def scenario(function):
    SCENARIOS.append((function.__name__.replace('_', ' '), function))
    return function

def bar(rec, **extra):
    client = rec.client()
    item = StatusItem(status('100', **extra))
    actions = StatusActions()
    actions.bind(item, client)
    return actions, item, client
