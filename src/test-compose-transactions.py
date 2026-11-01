#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real HTTP/GTK composer routing and scheduled replacement transactions."""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_cache = tempfile.TemporaryDirectory(prefix='babble-transactions-')
os.environ['XDG_CACHE_HOME'] = _cache.name
os.environ['GSETTINGS_BACKEND'] = 'memory'
if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.abspath(srcdir))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(root, 'build')
sys.path.insert(0, root)
os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from src.compose import ComposeDialog
from src.mastodon import MastodonClient

failures = []


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


def until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.001)
    if not condition():
        raise AssertionError('Timed out waiting for composer transaction')


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.wire = []
        self.entries = {}
        self.deletes = []
        self.post_status = 200
        self.delete_gate = None
        self.delete_entered = threading.Event()
        self.posts = 0
        self.statuses = {}
        self.gets = []
        self.context_status = 200
        self.context_gate = None
        self.context_entered = threading.Event()

    def context_of(self, status_id):
        """Mastodon's context: ancestors root first, descendants in
        creation order."""
        ancestors = []
        current = self.statuses[status_id]
        while current['in_reply_to_id'] in self.statuses:
            current = self.statuses[current['in_reply_to_id']]
            ancestors.insert(0, current)

        def below(status):
            parent = status['in_reply_to_id']
            while parent in self.statuses:
                if parent == status_id:
                    return True
                parent = self.statuses[parent]['in_reply_to_id']
            return False

        return {'ancestors': ancestors,
                'descendants': [s for s in self.statuses.values() if below(s)]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.gets.append(self.path)
        parts = self.path.split('?')[0].split('/')
        if parts[:4] == ['', 'api', 'v1', 'statuses'] and len(parts) > 4 \
                and parts[4] in self.server.statuses:
            if len(parts) == 6 and parts[5] == 'context':
                self.server.context_entered.set()
                gate = self.server.context_gate
                if gate is not None:
                    gate.wait(10)
                if self.server.context_status != 200:
                    self.reply(self.server.context_status, {'error': 'synthetic'})
                else:
                    self.reply(200, self.server.context_of(parts[4]))
                return
            if len(parts) == 5:
                self.reply(200, self.server.statuses[parts[4]])
                return
        if parts[:4] == ['', 'api', 'v1', 'statuses']:
            self.reply(404, {'error': 'not found'})
            return
        self.reply(200, [])

    def do_POST(self):
        self.submit()

    def do_PUT(self):
        self.submit()

    def submit(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.wire.append((self.command, self.path, payload))
        self.server.posts += 1
        status = {'id': 'new-%d' % self.server.posts, 'tags': []}
        if payload.get('scheduled_at') and self.server.post_status == 200:
            self.server.entries[status['id']] = payload
        self.reply(self.server.post_status, status if self.server.post_status == 200
                   else {'error': 'synthetic post refusal'})

    def do_DELETE(self):
        self.server.wire.append(('DELETE', self.path, None))
        self.server.delete_entered.set()
        gate = self.server.delete_gate
        if gate is not None:
            gate.wait(10)
        status = self.server.deletes.pop(0) if self.server.deletes else 200
        if status in (200, 404):
            self.server.entries.pop(self.path.rsplit('/', 1)[-1], None)
        self.reply(status, {} if status in (200, 404)
                   else {'error': 'synthetic removal refusal'})

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


def settle(seconds=0.3):
    """Let answers that are expected to change nothing arrive."""
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.001)


def fixture_thread(server, account='me'):
    """Five own posts in a chain, plus a stranger's reply and a second
    own reply under post 2, both of which must not change the counts."""
    words = ' '.join('word%d' % number for number in range(60))

    def post(status_id, parent, author, text):
        server.statuses[status_id] = {
            'id': status_id, 'in_reply_to_id': parent,
            'created_at': '2026-10-01T10:%02d:00.000Z' % len(server.statuses),
            'account': {'id': author}, 'content': '<p>%s</p>' % text,
            'tags': []}

    post('s1', None, account, words)
    post('s2', 's1', account, 'second post')
    post('o1', 's2', 'other', 'a stranger replies')
    post('s3', 's2', account, 'third post')
    post('s2b', 's2', account, 'a later second reply')
    post('s4', 's3', account, 'fourth post')
    post('s5', 's4', account, 'fifth ' + words)


def thread_tracker(server, client, window):
    from src import mastodon
    from src.threadchain import plain_text, preview_tail
    saved_backoff = mastodon.REST_BACKOFF_START
    mastodon.REST_BACKOFF_START = 0.01
    dialog = ComposeDialog(client)
    view = dialog.view
    view.local_draft_key = 'http://127.0.0.1:%d#me' % server.server_port
    fixture_thread(server)

    def title():
        return dialog.get_title()

    def preview():
        return view.preview_label.get_label() \
            if view.preview_label.get_visible() else ''

    def post_and_wait(text):
        view.buffer.set_text(text)
        count = server.posts
        view.on_post_clicked(None)
        until(lambda: not view._posting and server.posts == count + 1)

    def context_asked():
        return any('context' in path for path in server.gets)

    try:
        dialog.present(window)
        until(lambda: view.get_mapped())
        check('plain composer has no thread title or preview',
              title() == 'New Post' and preview() == '')
        view.thread_toggle.set_active(True)
        check('thread toggle titles the first post',
              title() == 'Thread · Post 1' and preview() == '')
        first = 'First post of a thread ' + ' '.join(
            'tail%d' % n for n in range(50))
        post_and_wait(first)
        check('second post is numbered and previews the text just sent',
              title() == 'Thread · Post 2' and preview() == preview_tail(first)
              and preview().startswith('…') and preview().endswith('tail49'))
        server.post_status = 403
        view.buffer.set_text('refused')
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        check('a failed post does not advance the count',
              title() == 'Thread · Post 2' and preview() == preview_tail(first))
        server.post_status = 200
        second = 'Second post, short'
        post_and_wait(second)
        check('third post previews the second',
              title() == 'Thread · Post 3' and preview() == second)
        view.thread_toggle.set_active(False)
        check('the closing post keeps its number with the toggle off',
              title() == 'Thread · Post 3' and preview() == second)
        post_and_wait('closing')
        until(lambda: dialog.get_parent() is None)
        check('sending the closing post clears title and preview',
              title() == 'New Post' and preview() == ''
              and view._thread is None)

        # Editing a post in the middle of the five-post thread.
        dialog.present(window)
        until(lambda: view.get_mapped())
        edited = dict(server.statuses['s2'], text='second post')
        view.start_edit(edited)
        check('edit opens as plain Edit Post before the thread arrives',
              title() == 'Edit Post' and preview() == '')
        until(lambda: title() == 'Edit Post · 2/5')
        check('edit of post 2 previews the end of post 1',
              preview() == preview_tail(plain_text(server.statuses['s1']))
              and preview().endswith('word59') and view.text() == 'second post')
        view.reset()
        check('reset clears title and preview',
              title() == 'New Post' and preview() == '')
        view.start_edit(dict(server.statuses['s1'], text='first'))
        until(lambda: title() == 'Edit Post · 1/5')
        check('editing the first post has no preview', preview() == '')
        view.reset()

        # Continuing the thread from its last post.
        view.start_reply('s5', 'me')
        check('reply opens as plain Reply before the thread arrives',
              title() == 'Reply')
        until(lambda: title() == 'Thread · Post 6')
        check('reply to post 5 previews the end of post 5',
              preview() == preview_tail(plain_text(server.statuses['s5']))
              and preview().endswith('word59'))
        wire_start = len(server.wire)
        post_and_wait('sixth')
        check('the reply is posted into the thread',
              server.wire[wire_start][2]['in_reply_to_id'] == 's5'
              and title() == 'New Post' and preview() == '')
        dialog.present(window)
        until(lambda: view.get_mapped())

        # Not a thread of one's own.
        server.gets.clear()
        view.start_reply('o1', 'other')
        until(lambda: '/api/v1/statuses/o1' in server.gets)
        settle()
        check('reply to a stranger stays a plain Reply',
              title() == 'Reply' and preview() == ''
              and not context_asked())
        view.reset()

        # A context that cannot be fetched.
        server.context_status = 500
        server.gets.clear()
        view.start_edit(edited)
        until(context_asked)
        settle(0.6)
        check('failed context leaves a plain Edit Post',
              title() == 'Edit Post' and preview() == '')
        view.reset()
        server.gets.clear()
        view.start_reply('s5', 'me')
        until(context_asked)
        settle(0.6)
        check('failed context leaves a plain Reply',
              title() == 'Reply' and preview() == '')
        view.reset()
        server.context_status = 200

        # An answer after the composer moved on.
        for late in ('edit', 'reply'):
            server.context_gate = threading.Event()
            server.context_entered.clear()
            if late == 'edit':
                view.start_edit(edited)
            else:
                view.start_reply('s5', 'me')
            until(lambda: server.context_entered.is_set())
            view.start_quote({'id': 'quoted-later'})
            server.context_gate.set()
            settle()
            check('late thread answer after %s is ignored' % late,
                  title() == 'Quote Post' and preview() == ''
                  and view._thread is None)
            server.context_gate = None
            view.reset()

        # Cancelling a reply in the middle of a thread.
        view.thread_toggle.set_active(True)
        post_and_wait('thread opener')
        check('thread under way before replying',
              title() == 'Thread · Post 2' and preview() == 'thread opener')
        opener_id = view.in_reply_to_id
        view.start_reply('o1', 'other')
        check('a reply in between shows no preview',
              preview() == '' and view.in_reply_to_id == 'o1')
        view.on_reply_cancel(None)
        check('cancelling the reply restores the thread title and preview',
              title() == 'Thread · Post 2' and preview() == 'thread opener'
              and view.in_reply_to_id == opener_id)
    finally:
        mastodon.REST_BACKOFF_START = saved_backoff
        server.context_gate = None
        if dialog.get_parent() is not None:
            dialog.force_close()
        until(lambda: dialog.get_parent() is None)
        dialog.run_dispose()


def click_label(widget, label):
    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        widget.emit('clicked')
        return True
    child = widget.get_first_child()
    while child is not None:
        if click_label(child, label):
            return True
        child = child.get_next_sibling()
    return False


def run():
    Adw.init()
    Gio.Resource.load(os.path.join(builddir, 'src',
                                   'babble.gresource'))._register()
    server = Server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = MastodonClient('https://synthetic.invalid', token='synthetic')
    client.instance = 'http://127.0.0.1:%d' % server.server_port
    client.api_base = client.instance + '/api/v1'
    client._session.set_proxy_resolver(None)
    window = Adw.Window(default_width=700, default_height=800)
    window.set_content(Gtk.Box())
    window.present()
    dialog = ComposeDialog(client)
    dialog.present(window)
    view = dialog.view
    try:
        until(lambda: view.get_mapped())
        view.start_edit({'id': 'old', 'text': 'unfinished edit'})
        dialog.close()
        until(lambda: dialog.get_parent() is None)
        dialog.present(window)
        view.start_quote({'id': 'quoted'})
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        check('edit then close/reopen then quote POSTs, never PUTs old',
              server.wire[-1][0:2] == ('POST', '/api/v1/statuses')
              and server.wire[-1][2]['quoted_status_id'] == 'quoted')
        view.start_edit({'id': 'restored-edit', 'text': 'prior text'})
        view.start_reply('reply', 'friend')
        view.on_reply_cancel(None)
        check('cancel restores prior edit text and routing',
              view.text() == 'prior text' and view.editing_id == 'restored-edit'
              and view.in_reply_to_id is None)
        view.start_reply('sent-reply', 'friend')
        view.buffer.set_text('reply body')
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        check('successful reply restores prior edit mode exactly once',
              view.text() == 'prior text' and view.editing_id == 'restored-edit'
              and view.in_reply_to_id is None)
        view.start_quote({'id': 'another-quote'})
        view.start_edit({'id': 'edit-only', 'text': 'edit'})
        check('quote to edit has no stale quote or schedule',
              view.editing_id == 'edit-only' and view.quoted_status_id is None
              and view.scheduled_id is None and view._pending_media_ids == [])
        view.start_scheduled({'id': 'scheduled', 'params': {'text': 'scheduled'}})
        view.start_reply('reply', 'friend')
        check('schedule to reply clears all old submission targets',
              view.scheduled_id is None and view.scheduled_at is None
              and view.editing_id is None and view.in_reply_to_id == 'reply')
        before = view._capture_state()
        view._posting = True
        check('entry points refuse mutations while sending',
              view.start_quote({'id': 'blocked'}) is False
              and view.restore_stashing(before.draft) is False
              and view._capture_state() == before)
        view._posting = False
        from src import pages
        from src.window import BabbleWindow
        from src.accounts import account_key
        recovered = before.draft
        host = BabbleWindow(application=None)
        host.account = {'id': '1', 'instance': 'https://synthetic.invalid'}
        host.client = client
        raw = recovered.as_stored(account_key(host.account), 'now')
        pages.settings().set_strv('local-drafts', [raw])
        host.present_composer = lambda: dialog
        view._posting = True
        host.on_draft_restore(None)
        check('rejected recovery leaves the stored local draft available',
              pages.settings().get_strv('local-drafts') == [raw])
        view._posting = False
        host.destroy()
        pages.settings().set_strv('local-drafts', [])
        from dataclasses import replace
        for invalid in (replace(recovered, language=999, language_code=None),
                        replace(recovered, visibility=999),
                        replace(recovered, poll=replace(recovered.poll,
                            expires=999, duration_seconds=None)),
                        replace(recovered, poll=replace(recovered.poll,
                            options=['a', 'b', 'c', 'd', 'e']))):
            unchanged = view._capture_state()
            check('unsupported draft settings refuse without partial restore',
                  view.restore_stashing(invalid) is False
                  and view._capture_state() == unchanged)
        view.reset()
        scheduled = {'id': 'old-poll', 'params': {
            'text': ' Umfrage ', 'visibility': 'private', 'language': 'de',
            'sensitive': False, 'quote_approval_policy': 'followers',
            'quoted_status_id': 'quote', 'in_reply_to_id': 123,
            'poll': {'options': [' Eins ', 'Zwei', 'Drei'], 'expires_in': 7200,
                     'multiple': True, 'hide_totals': True}}}
        view.start_scheduled(scheduled)
        snapshot = view.snapshot()
        from src.drafts import Draft
        restored = Draft.parse_raw(snapshot.as_stored('synthetic#1', 'now'))
        view.reset()
        view.restore(restored)
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        payload = server.wire[-1][2]
        check('German poll preserves exact options duration and flags via local JSON',
              payload['language'] == 'de'
              and payload['poll'] == scheduled['params']['poll'])
        view.start_scheduled(scheduled)
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        until(lambda: view._replacement is None)
        payload = [row[2] for row in server.wire if row[0] == 'POST'][-1]
        check('scheduled replacement retains sensitive policy quote and numeric reply',
              payload['sensitive'] is False
              and payload['quote_approval_policy'] == 'followers'
              and payload['quoted_status_id'] == 'quote'
              and payload['in_reply_to_id'] == '123')
        view.start_scheduled({'id': 'unknown-language', 'params': {
            'text': 'unknown language', 'language': 'zz'}})
        draft = Draft.parse_raw(view.snapshot().as_stored('synthetic#1', 'now'))
        view.reset()
        view.restore(draft)
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        check('unknown language survives snapshot restore and wire request',
              server.wire[-1][2]['language'] == 'zz'
              and 'poll' not in server.wire[-1][2])
        view.start_scheduled({'id': 'nullable', 'params': None})
        check('nullable scheduled settings clear previous poll and language',
              not view.poll_toggle.get_active() and view.text() == ''
              and view._languages[view.language_row.get_selected()][0] != 'zz')
        for bad_poll in ({'options': ['a'] * 5, 'expires_in': 7200},
                         {'options': ['a', 'b'], 'expires_in': True},
                         {'options': ['a', 'a'], 'expires_in': 7200},
                         {'options': ['a', 'b'], 'expires_in': 7200, 'multiple': 1}):
            before = view._capture_state()
            wire = list(server.wire)
            check('unsupported scheduled poll refuses without mutation: %r' % bad_poll,
                  not view.start_scheduled({'id': 'bad', 'params': {'poll': bad_poll}})
                  and view._capture_state() == before and server.wire == wire)
        view.reset()
        for start in ('quote', 'redraft'):
            view._draft_stack = []
            view.reset()
            if start == 'quote':
                view.start_quote({'id': 'thread-quote'})
            else:
                view.start_redraft({'id': 'deleted', 'text': 'first',
                                    'media_attachments': [{'id': 'reused'}]})
            view.buffer.set_text('first')
            view._select_visibility('private')
            view._select_language('de')
            view.thread_toggle.set_active(True)
            if dialog.get_parent() is None:
                dialog.present(window)
                until(lambda: view.get_mapped())
            view.text_view.grab_focus()
            count = server.posts
            view.on_post_clicked(None)
            until(lambda count=count: not view._posting and server.posts == count + 1)
            first_id = 'new-%d' % server.posts
            view.buffer.set_text('second')
            view.on_post_clicked(None)
            until(lambda count=count: not view._posting and server.posts == count + 2)
            check('%s thread keeps keyboard focus in the text view' % start,
                  window.get_focus() is view.text_view)
            payload = server.wire[-1][2]
            check('%s thread second segment replies with preserved selections' % start,
                  payload['in_reply_to_id'] == first_id
                  and 'quoted_status_id' not in payload and 'media_ids' not in payload
                  and payload['language'] == 'de'
                  and payload['visibility'] == 'private')
        view.reset()
        for delete_result in (200, 404):
            if dialog.get_parent() is None:
                dialog.present(window)
            transaction_post = {'id': 'old-cleanup',
                                'scheduled_at': '2099-01-01T12:00:00Z',
                                'params': {'text': 'replacement', 'language': 'de'}}
            server.entries['old-cleanup'] = transaction_post
            server.deletes = [403, delete_result]
            completed = []
            wire_start = len(server.wire)
            view.start_scheduled(transaction_post,
                                 on_done=lambda completed=completed:
                                 completed.append(True))
            view.on_post_clicked(None)
            until(lambda: view._replacement is not None and not view._cleanup_busy)
            check('failed original removal keeps warning locks and no success callback',
                  view._cleanup_banner.get_revealed() and not view.can_post()
                  and not view.post_button.get_sensitive()
                  and view._cleanup_banner.get_sensitive()
                  and not view.attach_button.get_sensitive()
                  and not view.screenshot_button.get_sensitive()
                  and not view.text_view.get_editable() and completed == [])
            view.on_post_clicked(None)
            check('cleanup cannot discard or start another post',
                  view.reset() is False
                  and view.start_quote({'id': 'blocked'}) is False)
            dialog.close()
            until(lambda: isinstance(window.get_visible_dialog(), Adw.AlertDialog))
            alert = window.get_visible_dialog()
            check('Keep Open response is actionable', click_label(alert, '_Keep Open'))
            until(lambda alert=alert: alert.get_parent() is None)
            check('Keep Open retains cleanup-only mode', view._replacement is not None)
            dialog.close()
            until(lambda: isinstance(window.get_visible_dialog(), Adw.AlertDialog))
            alert = window.get_visible_dialog()
            check('Close Anyway is actionable', click_label(alert, '_Close Anyway'))
            until(lambda: dialog.get_parent() is None)
            dialog.present(window)
            until(lambda: view.get_mapped())
            check('reopening cached composer retains retry-only warning',
                  view._replacement is not None and view._cleanup_banner.get_revealed()
                  and not view.post_button.get_sensitive())
            view._retry_scheduled_removal()
            view._retry_scheduled_removal()
            until(lambda: view._replacement is None)
            wire = server.wire[wire_start:]
            check('retry HTTP %d sends only original DELETE and completes once'
                  % delete_result,
                  [row[0] for row in wire] == ['POST', 'DELETE', 'DELETE']
                  and all(row[1] == '/api/v1/scheduled_statuses/old-cleanup'
                          for row in wire if row[0] == 'DELETE')
                  and completed == [True] and 'old-cleanup' not in server.entries)
        if dialog.get_parent() is None:
            dialog.present(window)
        server.post_status = 403
        view.start_scheduled({'id': 'not-accepted', 'params': {'text': 'refused'}})
        wire_start = len(server.wire)
        view.on_post_clicked(None)
        until(lambda: not view._posting)
        check('failed replacement POST never removes original',
              [row[0] for row in server.wire[wire_start:]] == ['POST']
              and view._replacement is None)
        server.post_status = 200
        server.delete_gate = threading.Event()
        server.delete_entered.clear()
        callbacks = []
        view.start_scheduled({'id': 'abandoned', 'params': {'text': 'pending'}},
                             on_done=lambda: callbacks.append(True))
        view.on_post_clicked(None)
        until(lambda: server.delete_entered.is_set())
        dialog.close()
        check('close while removing waits without another dialog',
              dialog.get_parent() is not None
              and not isinstance(window.get_visible_dialog(), Adw.AlertDialog))
        dialog.abandon_session()
        server.delete_gate.set()
        until(lambda: dialog.get_parent() is None)
        view.start_edit({'id': 'new-session', 'text': 'new session'})
        t = []
        GLib.idle_add(lambda: (t.append(True), GLib.SOURCE_REMOVE)[1])
        until(lambda: bool(t))
        check('abandoned cleanup cannot call old list callback or mutate new state',
              callbacks == [] and view.editing_id == 'new-session'
              and view.text() == 'new session')
        dialog.present(window)
        until(lambda: view.get_mapped())
        view.prefill_text('unsent words')
        pages.settings().set_strv('local-drafts', [])
        dialog.abandon_session()
        until(lambda: dialog.get_parent() is None)
        saved = [Draft.parse_raw(raw)
                 for raw in pages.settings().get_strv('local-drafts')]
        check('a session switch saves the open unsent draft',
              [d.text for d in saved if d is not None] == ['unsent words'])
        pages.settings().set_strv('local-drafts', [])
        thread_tracker(server, client, window)
    finally:
        client._session.abort()
        if server.delete_gate is not None:
            server.delete_gate.set()
        if dialog.get_parent() is not None:
            dialog.force_close()
        until(lambda: dialog.get_parent() is None)
        # Disposed while Python is alive, so _ComposeWidth unparents its
        # child rather than being finalized with it at exit.
        dialog.run_dispose()
        window.destroy()
        server.shutdown()
        server.server_close()
        _cache.cleanup()
    if failures:
        raise SystemExit(1)
    print('all composer transaction checks passed')


if __name__ == '__main__':
    run()
