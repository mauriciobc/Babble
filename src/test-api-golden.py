#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden request log: the exact wire requests each UI action sends.

The API-layer refactor (Docs/api-layer-plan.md, "Behaviour-preserving
proof") moves request construction out of the widgets and out of
``mastodon.py``. This test records, for each named scenario, every request
the real UI code (StatusActions, StatusMenuButton, ProfileActions, the
`src.api` functions and the MastodonClient transport) hands to the
transport and compares it with the committed
``golden/api-requests.json``. After a refactor the log must be identical;
a difference is a behaviour change.

Nothing touches the network. The recorder replaces the single transport
chokepoint, ``MastodonClient._spawn(task, message)``, which every request
passes through once libsoup's message is built. It records what is on that
message -- method, path, sorted query, headers and body (form pairs, JSON
or the multipart field names) -- then completes the Gio.Task with a canned
answer, so the UI's own callbacks (merge, rollback, toast, Undo, the 404
fallback) run for real. Dialogs and toasts are the only stubs: a
confirmation runs its action at once, and a toast hands the test its
Undo and timeout.

What is pinned, and what is not. Every header except Content-Length is
recorded, so a legitimate refactor that adds or changes a header (a new
User-Agent, an Accept) changes the golden deliberately and the diff is the
review. The order of form pairs is not pinned: they are compared sorted, so
a refactor may build a body in another order. Out of scope, because they
do not pass through ``MastodonClient._spawn`` as one UI action: the 429
retry respawn, the streaming socket, the ``imagecache`` and ``mediaviewer``
sessions, and the plugin host.

Every scenario must produce at least one request, unless it is named in
``ALLOW_NO_REQUESTS`` with its reason.

    BABBLE_GOLDEN_UPDATE=1   rewrite golden/api-requests.json
    BABBLE_GOLDEN_MUTATE=1   corrupt one recorded path, to show the test
                             fails with a readable diff (used by hand, to
                             prove the test can fail)

Exits 77 (skip) without a display or a compiled bundle."""

import difflib
import json
import os
import re
import sys
import types
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Soup  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

Adw.init()

from src import (explore, statusviews, hashtags, notifications, pages, profile,  # noqa: E402
                 relations, statusactions, statusmenu)
from src.mastodon import MastodonClient  # noqa: E402
from src.statusactions import StatusActions  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.timelinebase import QUOTE_POLICIES  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

GOLDEN = os.path.join(srcdir, 'golden', 'api-requests.json')
UPDATE = bool(os.environ.get('BABBLE_GOLDEN_UPDATE'))
MUTATE = bool(os.environ.get('BABBLE_GOLDEN_MUTATE'))
INSTANCE = 'https://golden.example'
TOKEN = 'golden-token'
ME = '1'

failures = []

# Scenarios that legitimately send no request, each with the reason.
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


# Recording

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
        client.instance_info = info or {}
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


# Canned answers


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


# Stubs for what is not a request


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


pages.show_toast = fake_toast
pages.confirm = fake_confirm
statusactions._flag = lambda name: False
statusactions._has_schema = lambda: False
statusmenu._flag = lambda name: False
statusmenu._open_composer = lambda *args: None
profile.present_mute_dialog = \
    lambda parent, handle, on_confirm: on_confirm(3600, True)

SCENARIOS = []


def scenario(function):
    SCENARIOS.append((function.__name__.replace('_', ' '), function))
    return function


# Status actions through StatusActions


def bar(rec, **extra):
    client = rec.client()
    item = StatusItem(status('100', **extra))
    actions = StatusActions()
    actions.bind(item, client)
    return actions, item, client


@scenario
def bar_favourite(rec):
    actions, _item, _client = bar(rec)
    actions.favourite_button.emit('clicked')


@scenario
def bar_unfavourite(rec):
    actions, _item, _client = bar(rec, favourited=True, favourites_count=1)
    actions.favourite_button.emit('clicked')


@scenario
def bar_favourite_fails_and_rolls_back(rec):
    actions, item, client = bar(rec)
    rec.fail[('POST', '/api/v1/statuses/100/favourite')] = [500]
    actions.favourite_button.emit('clicked')
    rec.settle()
    check('favourite rolled back after a 500', item.status['favourited']
          is False)


@scenario
def bar_boost(rec):
    actions, _item, _client = bar(rec)
    actions.boost_button.emit('clicked')


@scenario
def bar_unboost(rec):
    actions, _item, _client = bar(rec, reblogged=True, reblogs_count=1)
    actions.boost_button.emit('clicked')


@scenario
def bar_unboost_then_undo_reboosts(rec):
    actions, _item, _client = bar(rec, reblogged=True, reblogs_count=1)
    actions.boost_button.emit('clicked')
    rec.settle()
    rec.press_undo()


@scenario
def bar_boost_with_visibility_dialog(rec):
    statusactions._flag = lambda name: name == 'advanced-boost-dialog'
    shown = []
    real_present = Adw.AlertDialog.present
    Adw.AlertDialog.present = lambda self, parent=None: shown.append(self)
    try:
        actions, _item, _client = bar(rec, visibility='unlisted')
        actions.boost_button.emit('clicked')
        shown[-1].emit('response', 'boost')
    finally:
        Adw.AlertDialog.present = real_present
        statusactions._flag = lambda name: False


@scenario
def bar_bookmark(rec):
    actions, _item, _client = bar(rec)
    actions.bookmark_button.emit('clicked')


@scenario
def bar_unbookmark(rec):
    actions, _item, _client = bar(rec, bookmarked=True)
    actions.bookmark_button.emit('clicked')


# StatusMenuButton


def menu_for(rec, own=False, info=None, **extra):
    client = rec.client(info)
    if own:
        extra.setdefault('account', account(ME, 'me'))
    item = StatusItem(status('100', **extra))
    actions = StatusActions()
    actions.bind(item, client)
    menu = StatusMenuButton(actions=actions)
    menu.bind(item, client)
    menu.prepare()
    return menu, item


def activate(menu, name, parameter=None):
    menu.activate_action('row.' + name, parameter)


class WithWindow:
    def __enter__(self):
        self.real = pages.window_of
        pages.window_of = lambda widget: FakeWindow()

    def __exit__(self, *_exc):
        pages.window_of = self.real


@scenario
def menu_icon_row_favourite_boost_bookmark(rec):
    menu, _item = menu_for(rec)
    activate(menu, 'favourite')
    activate(menu, 'boost')
    activate(menu, 'bookmark')


@scenario
def menu_translate(rec):
    info = {'configuration': {'translation': {'enabled': True}}}
    menu, _item = menu_for(rec, info=info)
    activate(menu, 'translate')


@scenario
def menu_mute_conversation(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'mute-conversation')


@scenario
def menu_unmute_conversation(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, muted=True)
        activate(menu, 'mute-conversation')


@scenario
def menu_pin(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'pin')


@scenario
def menu_unpin(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, pinned=True)
        activate(menu, 'pin')


@scenario
def menu_quote_policy(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True, quote_approval={
            'automatic': [], 'manual': [], 'current_user': 'automatic'})
        activate(menu, 'quote-policy',
                 GLib.Variant.new_string(QUOTE_POLICIES[-1][0]))


@scenario
def menu_revoke_quote(rec):
    with WithWindow():
        quoted = status('55', account=account(ME, 'me'))
        menu, _item = menu_for(rec, quote={
            'state': 'accepted', 'quoted_status': quoted})
        activate(menu, 'revoke-quote')


@scenario
def menu_edit_loads_source(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'edit')


@scenario
def menu_delete_after_toast_times_out(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'delete')
        rec.settle()
        check('delete sends nothing while the toast is up', not rec.log)
        rec.time_out()


@scenario
def menu_delete_undone(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'delete')
        rec.press_undo()
        check('delete undone sends nothing', not rec.log)


@scenario
def menu_delete_and_redraft(rec):
    with WithWindow():
        menu, _item = menu_for(rec, own=True)
        activate(menu, 'redraft')


@scenario
def menu_mute_author_then_undo(rec):
    with WithWindow():
        menu, _item = menu_for(rec)
        activate(menu, 'mute')
        rec.settle()
        rec.press_undo()


@scenario
def menu_block_author(rec):
    with WithWindow():
        menu, _item = menu_for(rec)
        activate(menu, 'block')


# Profile relationship commands


def page_for(rec, relationship=None, acct='ada', client=None, **extra):
    client = client or rec.client()
    page = profile.ProfilePage(
        client, account('7', acct, **extra), viewer_id=ME)
    rec.settle()
    page.relationship = relationship or {}
    page._relationship_known = True
    page._content = True
    page.update_relationship_ui()
    rec.clear()
    return page


def run_action(page, name, state=None):
    action = page.actions.lookup_action(name)
    if state is None:
        action.activate(None)
    else:
        action.change_state(GLib.Variant.new_boolean(state))


@scenario
def profile_follow(rec):
    page = page_for(rec)
    page.on_follow_clicked(None)


@scenario
def profile_unfollow_then_undo_follows(rec):
    page = page_for(rec, {'following': True})
    page.on_follow_clicked(None)
    rec.settle()
    rec.press_undo()


@scenario
def profile_unfollow_locked_account(rec):
    page = page_for(rec, {'following': True}, locked=True)
    page.on_follow_clicked(None)


@scenario
def profile_withdraw_follow_request(rec):
    page = page_for(rec, {'requested': True})
    page.on_follow_clicked(None)


@scenario
def profile_block(rec):
    page = page_for(rec)
    run_action(page, 'block')


@scenario
def profile_unblock_then_undo_blocks(rec):
    page = page_for(rec, {'blocking': True})
    page.on_follow_clicked(None)
    rec.settle()
    rec.press_undo()


@scenario
def profile_mute(rec):
    page = page_for(rec)
    run_action(page, 'mute')


@scenario
def profile_unmute(rec):
    page = page_for(rec, {'muting': True})
    run_action(page, 'unmute')


@scenario
def profile_hide_and_show_boosts(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'hide-boosts')
    rec.settle()
    run_action(page, 'show-boosts')


@scenario
def profile_notify_on_and_off(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'notify', True)
    rec.settle()
    run_action(page, 'notify', False)


@scenario
def profile_remove_follower(rec):
    page = page_for(rec, {'followed_by': True})
    run_action(page, 'remove-follower')


@scenario
def profile_endorse_and_unendorse(rec):
    page = page_for(rec, {'following': True})
    run_action(page, 'endorse', True)
    rec.settle()
    run_action(page, 'endorse', False)


@scenario
def profile_endorse_404_falls_back_to_pin(rec):
    page = page_for(rec, {'following': True})
    rec.fail[('POST', '/api/v1/accounts/7/endorse')] = [404]
    run_action(page, 'endorse', True)


@scenario
def profile_unendorse_404_falls_back_to_unpin(rec):
    page = page_for(rec, {'following': True})
    rec.fail[('POST', '/api/v1/accounts/7/unendorse')] = [404]
    run_action(page, 'endorse', False)


@scenario
def profile_endorse_on_a_legacy_server(rec):
    client = rec.client({'api_versions': {'mastodon': 5}})
    page = page_for(rec, {'following': True}, client=client)
    run_action(page, 'endorse', True)


@scenario
def profile_private_note(rec):
    page = page_for(rec)

    class Dialog:
        save_button = type('Button', (), {
            'set_sensitive': lambda self, value: None})()

        def close(self):
            pass

    page.save_private_note(Dialog(), 'a note')


@scenario
def profile_block_domain(rec):
    page = page_for(rec, acct='zed@remote.example')
    page.confirm_domain_block(True)


@scenario
def profile_unblock_domain(rec):
    page = page_for(rec, {'domain_blocking': True},
                    acct='zed@remote.example')
    page.confirm_domain_block(False)


@scenario
def profile_unblock_domain_then_undo_blocks(rec):
    page = page_for(rec, {'domain_blocking': True},
                    acct='zed@remote.example')
    page.confirm_domain_block(False)
    rec.settle()
    rec.press_undo()


# The MastodonClient wrappers


def wrapper(rec, name, *args, **kwargs):
    client = rec.client()
    getattr(client, name)(*args, callback=lambda *_a: None, **kwargs)


def api_wrapper(rec, name, *args, module='statuses', **kwargs):
    """A `src.api.<module>` function: the client is its first argument."""
    import importlib
    api = importlib.import_module('src.api.' + module)
    getattr(api, name)(rec.client(), *args, callback=lambda *_a: None,
                       **kwargs)


@scenario
def client_account_wrappers(rec):
    api_wrapper(rec, 'verify_credentials_async', module='accounts')
    api_wrapper(rec, 'lookup_async', 'ada@remote.example', module='accounts')
    api_wrapper(rec, 'statuses_async', '7', params={'limit': 20},
                module='accounts')
    api_wrapper(rec, 'relationships_async', ['7', '8'], module='accounts')
    api_wrapper(rec, 'follow_async', '7', reblogs=True, notify=False,
                module='relationships')
    api_wrapper(rec, 'follow_async', '7', reblogs=False, notify=True,
                module='relationships')
    api_wrapper(rec, 'unfollow_async', '7', module='relationships')


@scenario
def client_status_wrappers(rec):
    api_wrapper(rec, 'context_async', '100')
    api_wrapper(rec, 'favourite_async', '100')
    api_wrapper(rec, 'unfavourite_async', '100')
    api_wrapper(rec, 'reblog_async', '100')
    api_wrapper(rec, 'reblog_async', '100', visibility='private')
    api_wrapper(rec, 'unreblog_async', '100')
    api_wrapper(rec, 'bookmark_async', '100')
    api_wrapper(rec, 'unbookmark_async', '100')


@scenario
def client_post_reply_edit(rec):
    wrapper(rec, 'post_status_async', 'hello',
            params={'visibility': 'unlisted', 'sensitive': True,
                    'media_ids': ['1', '2'],
                    'poll': {'options': ['a', 'b'], 'expires_in': 300}})
    wrapper(rec, 'reply_async', 100, 'answer',
            params={'visibility': 'public'})
    wrapper(rec, 'edit_status_async', '100', 'edited',
            params={'spoiler_text': 'cw'})


@scenario
def client_media_multipart(rec):
    wrapper(rec, 'media_upload_async', b'\x89PNG-bytes', 'pic.png',
            'image/png', description='alt text')
    wrapper(rec, 'media_upload_async', GLib.Bytes.new(b'abc'), 'a.ogg',
            'audio/ogg')
    wrapper(rec, 'media_status_async', '500')


@scenario
def client_generic_request(rec):
    client = rec.client()
    client.request_async('GET', '/bookmarks', params={'limit': 20},
                         callback=lambda *_a: None)
    client.request_async('POST', '/api/v1/statuses/1/translate',
                         form={'lang': 'en'}, callback=lambda *_a: None)
    client.request_async('PUT', '/statuses/1/interaction_policy',
                         json_body={'x': [1, 2]}, callback=lambda *_a: None)


# The remaining MastodonClient wrappers


def fresh_wrapper(rec, name, *args, **kwargs):
    """A client that has not loaded its instance info, so that
    instance_info_async really sends."""
    client = MastodonClient(INSTANCE, token=TOKEN)
    getattr(client, name)(*args, callback=lambda *_a: None, **kwargs)


@scenario
def client_instance_markers_timelines_trends(rec):
    fresh_wrapper(rec, 'instance_info_async')
    api_wrapper(rec, 'get_async', ['home', 'notifications'],
                module='markers')
    api_wrapper(rec, 'home_async', params={'limit': 20}, module='timelines')
    api_wrapper(rec, 'public_async', params={'local': 'true', 'limit': 20},
                module='timelines')
    api_wrapper(rec, 'statuses_async', params={'limit': 10},
                module='trends')


@scenario
def client_notification_wrappers(rec):
    api_wrapper(rec, 'index_async', params={'limit': 30},
                module='notifications')
    api_wrapper(rec, 'unread_count_async', params={'limit': 100},
                module='notifications')
    api_wrapper(rec, 'clear_async', module='notifications')
    api_wrapper(rec, 'dismiss_async', '12', module='notifications')
    api_wrapper(rec, 'save_async', 'notifications', '34', module='markers')


@scenario
def client_oauth_wrappers(rec):
    client = MastodonClient(INSTANCE)
    client.register_app_async(callback=lambda *_a: None)
    client.register_app_async('read', loopback='http://127.0.0.1:4242/cb',
                              callback=lambda *_a: None)
    client.client_id = 'cid'
    client.client_secret = 'csecret'
    client.request_token_async('the-code', callback=lambda *_a: None)
    client.revoke_token_async('tok', callback=lambda *_a: None)


# Relationship lists (relations.py), through the page's own methods


def row_for(page):
    row = Adw.ActionRow()
    page.list_box.append(row)
    page.rows.append(row)
    return row


def list_page(rec, factory):
    page = factory(rec.client())
    rec.settle()
    rec.clear()
    return page


@scenario
def relations_follow_request_authorize_and_reject(rec):
    page = list_page(rec, relations.FollowRequestsPage)
    page.answer(row_for(page), account('7'), 'authorize')
    rec.settle()
    page.answer(row_for(page), account('8', 'bob'), 'reject')


@scenario
def relations_unmute(rec):
    page = list_page(rec, relations.MutedAccountsPage)
    page.unmute(row_for(page), account('7'))


@scenario
def relations_unblock(rec):
    page = list_page(rec, relations.BlockedAccountsPage)
    page.unblock(row_for(page), account('7'))


@scenario
def relations_unblock_domain(rec):
    page = list_page(rec, relations.BlockedDomainsPage)
    page.unblock(row_for(page), 'remote.example')


@scenario
def relations_lists_load(rec):
    for factory in (relations.FollowRequestsPage,
                    relations.MutedAccountsPage,
                    relations.BlockedAccountsPage,
                    relations.BlockedDomainsPage):
        factory(rec.client())
        rec.settle()


# Notifications, suggestions, hashtags, quote cards


def request_item(**extra):
    item = types.SimpleNamespace(actors=[account('7')],
                                 request_state=None, **extra)
    return item


@scenario
def notification_answer_follow_request(rec):
    for verb in ('authorize', 'reject'):
        row = notifications.NotificationRow()
        row.client = rec.client()
        row.item = None
        row._request_buttons = (Gtk.Button(), Gtk.Button())
        row._answer_request(request_item(), verb)
        rec.settle()


@scenario
def notification_follow_back(rec):
    row = notifications.NotificationRow()
    row.client = rec.client()
    row.item = None
    row._follow_button = Gtk.Button()
    row._follow_back(request_item(), account('7'), (INSTANCE, '7'))


@scenario
def notification_request_row_accept_and_dismiss(rec):
    page = list_page(rec, notifications.NotificationRequestsPage)
    request = {'id': '9', 'account': account('7'),
               'notifications_count': 2}
    for verb in ('accept', 'dismiss'):
        row = notifications.NotificationRequestRow(page, request)
        page.list_box.append(row)
        page.rows.append(row)
        row.answer(verb)
        rec.settle()


@scenario
def suggestion_row_follow_and_dismiss(rec):
    page = types.SimpleNamespace(client=rec.client())
    holder = Gtk.Box()
    holder.client = page.client
    holder.paged = types.SimpleNamespace(remove_row=lambda row: None)
    row = explore.SuggestionRow(holder, {'account': account('7'),
                                         'source': 'global'})
    row._on_follow(row.follow_button)
    rec.settle()
    row._on_dismiss(row.dismiss_button)


@scenario
def hashtag_page_follow_unfollow_feature(rec):
    with WithWindow():
        page = hashtags.HashtagPage(rec.client(), ['golden'])
        rec.settle()
        page._on_follow_clicked(page.follow_button)
        rec.settle()
        page._on_follow_clicked(page.follow_button)
        rec.settle()
        page._on_feature()
        rec.settle()
        page.featured_id = '5'
        page._on_feature()


@scenario
def hashtag_tag_row_unfollow_then_undo(rec):
    with WithWindow():
        holder = Gtk.Box()
        holder.client = rec.client()
        holder.paged = types.SimpleNamespace(remove_row=lambda row: None)
        holder.refresh = lambda: None
        row = hashtags.TagRow(holder, {'name': 'golden', 'history': []})
        row._on_unfollow(row.unfollow)
        rec.settle()
        rec.press_undo()


@scenario
def followed_hashtags_page_loads(rec):
    with WithWindow():
        hashtags.FollowedHashtagsPage(rec.client())
        rec.settle()


@scenario
def quote_card_resolves_the_quoted_post(rec):
    real = pages.open_status
    pages.open_status = lambda *args: None
    try:
        card = statusactions.QuoteCard(
            {'state': 'accepted', 'quoted_status_id': '55'}, rec.client())
        card.emit('clicked')
    finally:
        pages.open_status = real


# Phase-1 read paths: profile extras, account lists, post views


def own_page(rec):
    page = profile.ProfilePage(rec.client(), account(ME, 'me'),
                               viewer_id=ME)
    rec.settle()
    rec.clear()
    return page


@scenario
def profile_load_extras_for_another_account(rec):
    page = page_for(rec)
    page.load_extras()


@scenario
def profile_load_extras_for_the_own_account(rec):
    page = own_page(rec)
    page.load_extras()


@scenario
def profile_reload_account_another(rec):
    page_for(rec).reload_account()


@scenario
def profile_reload_account_own(rec):
    own_page(rec).reload_account()


@scenario
def profile_account_lists(rec):
    page = page_for(rec, followers_count=3, following_count=2)
    page.push_subpage = lambda title, subpage: None
    page.on_followers_clicked(None)
    page.on_following_clicked(None)
    page.on_familiar_clicked(None)
    page.on_endorsements_clicked(None)


@scenario
def status_edit_history_view(rec):
    view = statusviews.EditHistoryView(rec.client(), status('100'))
    rec.settle()
    view.refresh()


@scenario
def status_stats_view_tabs(rec):
    view = statusviews.StatusStatsView(
        rec.client(), status('100', quotes_count=1), 'favourites')
    rec.settle()
    for kind, _key in statusviews.STATS_TABS:
        view._make_tab(kind, '100')
        rec.settle()


@scenario
def mention_completer_searches_accounts(rec):
    from src.mentioncompleter import MentionCompleter
    view = Gtk.TextView()
    completer = MentionCompleter(rec.client(), view)
    for token in ('al', 'ada@remote.example'):
        completer._token = (0, token)
        completer._search()
        rec.settle()


@scenario
def fedicollections_fetch_accounts(rec):
    from src import fedicollections
    fedicollections._ACCOUNTS.clear()
    fedicollections.fetch_accounts(rec.client(), ['7', '8', '7'],
                                   lambda _accounts: None)


# Phase 2: read paths
#
# Recorded BEFORE the read screens move into src/api, so that the move can
# be proved byte-identical. These scenarios use richer canned answers
# (``rec.rich``) so the screens build real rows and their follow-up
# actions can run; the earlier scenarios keep the plain answers, which is
# why their golden entries are unchanged.

MISSING = object()


def announcement(announcement_id='1', **extra):
    data = {'id': announcement_id, 'content': '<p>maintenance</p>',
            'published_at': '2026-01-01T00:00:00.000Z',
            'updated_at': '2026-01-01T00:00:00.000Z', 'read': False,
            'all_day': False, 'mentions': [], 'emojis': [],
            'reactions': [{'name': '\U0001F44D', 'count': 1, 'me': True}]}
    data.update(extra)
    return data


def filter_json(filter_id='4', **extra):
    data = {'id': filter_id, 'title': 'Spoilers', 'context': ['home'],
            'filter_action': 'warn', 'expires_at': None, 'statuses': [],
            'keywords': [{'id': '9', 'keyword': 'spoiler',
                          'whole_word': True}]}
    data.update(extra)
    return data


def list_json(list_id='3', **extra):
    data = {'id': list_id, 'title': 'Friends', 'replies_policy': 'list',
            'exclusive': False}
    data.update(extra)
    return data


def scheduled_json(scheduled_id, when):
    return {'id': scheduled_id, 'scheduled_at': when,
            'params': {'text': 'later', 'visibility': 'public'},
            'media_attachments': []}


def conversation_json(conversation_id='20'):
    return {'id': conversation_id, 'unread': True,
            'accounts': [account('7')], 'last_status': status('300')}


def tag_json(name='golden', **extra):
    data = {'name': name, 'url': 'https://golden.example/tags/' + name,
            'history': [], 'following': False}
    data.update(extra)
    return data


def link_json():
    return {'url': 'https://news.example/a', 'title': 'News',
            'description': 'd', 'provider_name': 'News Example',
            'image': None, 'history': [], 'type': 'link'}


def policy_json():
    return {'for_not_following': 'accept', 'for_not_followers': 'accept',
            'for_new_accounts': 'accept', 'for_private_mentions': 'accept',
            'for_limited_accounts': 'accept',
            'summary': {'pending_requests_count': 2,
                        'pending_notifications_count': 3}}


def report_json(year=2025):
    return {'annual_reports': [{'year': year, 'data': {
        'top_statuses': {'by_favourites': '321'}}}],
        'accounts': [], 'statuses': []}


_RICH = {
    ('GET', '/api/v1/announcements'): lambda q: [announcement()],
    ('GET', '/api/v2/filters'): lambda q: [filter_json()],
    ('POST', '/api/v2/filters'): lambda q: filter_json('5'),
    ('PUT', '/api/v2/filters/4'): lambda q: filter_json(),
    ('GET', '/api/v1/lists'): lambda q: [list_json()],
    ('POST', '/api/v1/lists'): lambda q: list_json('6'),
    ('PUT', '/api/v1/lists/3'): lambda q: list_json(),
    ('GET', '/api/v1/lists/3/accounts'): lambda q: [account('7')],
    ('GET', '/api/v1/accounts/search'): lambda q: [account('8', 'bob')],
    ('GET', '/api/v1/accounts/7/lists'): lambda q: [{'id': '3'}],
    ('GET', '/api/v1/timelines/list/3'): lambda q: [status('100')],
    ('GET', '/api/v1/scheduled_statuses'): lambda q: [
        scheduled_json('70', '2030-06-01T10:00:00.000Z'),
        scheduled_json('71', '5000-01-01T00:00:00.000Z')],
    ('PUT', '/api/v1/scheduled_statuses/70'): lambda q: scheduled_json(
        '70', '2031-06-01T10:00:00.000Z'),
    ('GET', '/api/v2/search'): lambda q: {
        'accounts': [], 'hashtags': [], 'statuses': []},
    ('GET', '/api/v1/followed_tags'): lambda q: [tag_json()],
    ('GET', '/api/v1/trends/tags'): lambda q: [tag_json()],
    ('GET', '/api/v1/trends/links'): lambda q: [link_json()],
    ('GET', '/api/v1/trends/statuses'): lambda q: [status('100')],
    ('GET', '/api/v1/featured_tags'): lambda q: [],
    ('GET', '/api/v1/featured_tags/suggestions'): lambda q: [],
    ('GET', '/api/v1/tags/golden'): lambda q: tag_json(),
    ('GET', '/api/v2/notifications/policy'): lambda q: policy_json(),
    ('PATCH', '/api/v2/notifications/policy'): lambda q: policy_json(),
    ('GET', '/api/v1/notifications/requests'): lambda q: [
        {'id': '9', 'account': account('7'), 'notifications_count': 2}],
    ('GET', '/api/v1/notifications'): lambda q: [],
    ('GET', '/api/v1/conversations'): lambda q: [conversation_json()],
    ('POST', '/api/v1/conversations/20/read'): lambda q: dict(
        conversation_json(), unread=False),
    ('GET', '/api/v2/suggestions'): lambda q: [
        {'source': 'global', 'account': account('7')}],
    ('GET', '/api/v1/custom_emojis'): lambda q: [
        {'shortcode': 'blob', 'url': 'https://x/blob.png',
         'static_url': 'https://x/blob.png', 'visible_in_picker': True,
         'category': None}],
    ('GET', '/api/v1/annual_reports/2025'): lambda q: report_json(),
    ('GET', '/api/v1/annual_reports/2025/state'): lambda q: {
        'state': 'eligible'},
    ('GET', '/api/v1/timelines/home'): lambda q: [status('100')],
}


def rich(method, path, query):
    """The richer answer for a phase-2 scenario, or MISSING for the plain
    one."""
    found = _RICH.get((method, path))
    if found is not None:
        return found(query)
    return MISSING


def p2(function):
    """A scenario that runs with the richer answers."""
    import functools

    @functools.wraps(function)
    def run(rec):
        rec.rich = True
        return function(rec)
    return scenario(run)


def link_header(path, **params):
    return '<%s%s?%s>; rel="next"' % (
        INSTANCE, path, urllib.parse.urlencode(params))


class Anything:
    """A stand-in widget that accepts any call."""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def bind_stub(cls, stub, *names):
    """Give *stub* the real methods *names* of *cls*."""
    for name in names:
        setattr(stub, name, types.MethodType(getattr(cls, name), stub))
    return stub


from src import lists as _lists  # noqa: E402

# A dialog that was never presented cannot be closed.
_lists.ListEditor.close = lambda self: None
pages.launch_external = lambda *args: None
pages.open_status = lambda *args: None
pages.open_profile = lambda *args: None


# Announcements

def _announcements_page(rec):
    from src import announcements
    page = announcements.AnnouncementsPage(rec.client())
    rec.settle()
    return page


@p2
def announcements_page_loads(rec):
    _announcements_page(rec)
    rec.settle()


@p2
def announcements_mark_read(rec):
    page = _announcements_page(rec)
    page.rows[0].mark_read()
    rec.settle()


@p2
def announcements_reaction_toggled_off_and_on(rec):
    page = _announcements_page(rec)
    bar = page.rows[0].reactions
    bar.buttons['\U0001F44D'].set_active(False)
    rec.settle()
    bar._on_emoji_picked(None, '\U0001F389')
    rec.settle()


@p2
def notification_controller_loads_announcements(rec):
    from src.notificationctrl import NotificationController
    stub = types.SimpleNamespace(client=rec.client(), banner=Anything())
    bind_stub(NotificationController, stub, 'on_announcements_loaded')
    NotificationController.load_announcements(stub)
    rec.settle()


# Filters

@p2
def filters_list_loads(rec):
    from src import filters
    rec.keep(filters.FiltersList(rec.client(), Adw.NavigationView()))


@p2
def filter_create_with_keywords(rec):
    from src import filters
    page = filters.FilterEditPage(rec.client(), on_saved=lambda item: None)
    page.title_row.set_text('Spoilers')
    page._add_keyword_row({'keyword': 'spoiler', 'whole_word': True})
    page._add_keyword_row({'keyword': 'leak', 'whole_word': False})
    page._context_rows['notifications'].set_active(True)
    page.action_row.set_selected(1)
    page.expiry_row.set_selected(3)
    page._on_save_clicked(None)
    rec.settle()


@p2
def filter_edit_replaces_a_keyword(rec):
    from src import filters
    item = filter_json(expires_at='2030-01-01T00:00:00.000Z')
    page = filters.FilterEditPage(rec.client(), item,
                                  on_saved=lambda saved: None)
    page.title_row.set_text('Spoilers and leaks')
    page._on_keyword_removed(page._keyword_rows[0])
    page._add_keyword_row({'keyword': 'leak', 'whole_word': False})
    page._on_save_clicked(None)
    rec.settle()


@p2
def filter_delete(rec):
    from src import filters
    page = filters.FilterEditPage(rec.client(), filter_json(),
                                  on_saved=lambda saved: None)
    page._on_delete_clicked(None)
    rec.settle()


# Lists

def _lists_page(rec):
    from src import lists
    page = lists.ListsPage(rec.client())
    rec.settle()
    return page


@p2
def lists_page_loads(rec):
    with WithWindow():
        _lists_page(rec)
    rec.settle()


@p2
def list_editor_create(rec):
    from src import lists
    with WithWindow():
        editor = lists.ListEditor(rec.client(),
                                  on_saved=lambda data, created: None)
        editor.title_row.set_text('Friends')
        editor.policy_row.set_selected(1)
        editor.exclusive_row.set_active(True)
        editor._on_save()
    rec.settle()


@p2
def list_editor_members_search_add_remove_save_delete(rec):
    from src import lists
    with WithWindow():
        editor = lists.ListEditor(rec.client(), list_json(),
                                  on_saved=lambda data, created: None,
                                  on_deleted=lambda data: None)
        rec.settle()
        editor.search_row.set_text('bo')
        editor._run_search()
        rec.settle()
        row = editor.result_rows[0]
        editor._on_add_member(Gtk.Button(), row)
        rec.settle()
        editor._on_remove_member(Gtk.Button(), editor.member_rows[0])
        rec.settle()
        editor.title_row.set_text('Close friends')
        editor._on_save()
        rec.settle()
        editor._on_delete(None)
    rec.settle()


@p2
def list_row_delete(rec):
    with WithWindow():
        page = _lists_page(rec)
        page.paged.rows[0]._on_delete(None)
    rec.settle()


@p2
def list_membership_dialog_toggles(rec):
    from src import lists
    with WithWindow():
        item = list_json()
        row = types.SimpleNamespace(handler=0)
        dialog = lists._MembershipDialog(rec.client(), account('7'))
        rec.settle()
        dialog._on_toggled(Gtk.CheckButton(active=True), item, row)
        rec.settle()
        dialog._on_toggled(Gtk.CheckButton(active=False), item, row)
    rec.settle()


@p2
def window_loads_pinned_list_titles(rec):
    from src.window import BabbleWindow
    stub = types.SimpleNamespace(
        client=rec.client(), _items={}, sidebar_rail=Anything(),
        navigation=types.SimpleNamespace(
            set_pinned_lists=lambda lists: None, pinned_lists={},
            pages={}))
    BabbleWindow._load_pinned_list_titles(stub)
    rec.settle()


@p2
def list_timeline_first_page(rec):
    from src import lists
    with WithWindow():
        rec.keep(lists.ListTimelinePage(rec.client(), list_json()))
    rec.settle()


# Scheduled posts and drafts

@p2
def scheduled_posts_reschedule_and_delete(rec):
    from src import scheduled
    page = scheduled.ScheduledPostsPage(rec.client())
    rec.settle()
    row = page.list.rows[0]
    row._on_rescheduled('2031-06-01T10:00:00.000Z')
    rec.settle()
    row._on_delete()


@p2
def draft_posts_load(rec):
    from src import scheduled
    rec.keep(scheduled.DraftPostsPage(rec.client()))
    rec.settle()


@p2
def compose_retries_the_scheduled_removal(rec):
    from src.compose import BabbleComposeView, _ScheduledReplacement
    stub = types.SimpleNamespace(
        client=rec.client(), _cleanup_busy=False, _cleanup_banner=None,
        _cleanup_cancellable=None,
        _replacement=_ScheduledReplacement('70', {}, None, 'Posted'),
        set_posting_ui=lambda busy: None,
        _complete_post=lambda status, message: status)
    stub.weak_ref = lambda: (lambda: stub)
    BabbleComposeView._retry_scheduled_removal(stub)
    rec.settle()


# Search

@p2
def search_page_requests(rec):
    from src import search
    page = search.SearchPage(rec.client())
    page.query = 'ada'
    page.search(resolve=True)
    rec.settle()
    page.search(resolve=False)
    rec.settle()
    page._resolve_url('https://remote.example/@ada/1', force=True)


@p2
def search_type_pages_load_with_offsets(rec):
    from src import search
    full = [account(str(1000 + n), 'user%d' % n) for n in range(20)]
    for kind in ('accounts', 'hashtags', 'statuses'):
        rec.queue[('GET', '/api/v2/search')] = [{kind: full}, {kind: []}] \
            if kind == 'accounts' else [{kind: []}]
        page = search.SearchTypePage(rec.client(), 'ada', kind)
        rec.settle()
        page._load()
        rec.settle()


@p2
def pages_resolve_uri(rec):
    pages.resolve_uri(Gtk.Box(), rec.client(),
                      'https://remote.example/@ada/1')
    rec.settle()


@p2
def lookup_profile_resolves_a_remote_account(rec):
    rec.fail[('GET', '/api/v1/accounts/lookup')] = [404]
    rec.keep(profile.LookupProfilePage(rec.client(), '@zed@remote.example'))
    rec.settle()


@p2
def search_provider_index_and_network(rec):
    from src import searchprovider
    provider = searchprovider.SearchProvider(None)
    provider._ensure_index(rec.client(), {'id': ME, 'instance': INSTANCE},
                           lambda: None)
    rec.settle()
    search = types.SimpleNamespace(
        answered=False, timer=0, client=rec.client(), query='ada',
        cancellable=Gio.Cancellable(), network=None, pending=5,
        invocation=None)
    provider._fire_network(search)
    rec.settle()


@p2
def hashtag_completer_searches_and_loads_the_tag_pool(rec):
    from src.hashtagcompleter import HashtagCompleter, TagPool
    view = Gtk.TextView()
    completer = HashtagCompleter(rec.client(), view)
    completer._token = (0, 'gold')
    completer._search()
    rec.settle()
    # prepare() would fill the popover, which needs a toplevel; the pool
    # it loads is the same object.
    TagPool().ensure(rec.client())
    rec.settle()


@p2
def composer_pasted_link_resolves_a_quote(rec):
    from src.composequote import QuotePaste
    client = rec.client()

    class Stub(QuotePaste):
        _quote_state_generation = 0
        quoted_status_id = None
        editing_id = None

        def weak_ref(self):
            return lambda: self

        def _attach_quote(self, status_id, uri):
            pass

        def _restore_url(self, uri, offset):
            pass

    stub = Stub()
    stub.client = client
    stub._begin_quote_attach('https://golden.example/@ada/100', 0)
    rec.settle()


# Hashtags and tags

@p2
def explore_trending_tags_load(rec):
    rec.keep(explore.HashtagsPage(rec.client()))


@p2
def home_timeline_notes_followed_tags(rec):
    from src.timelinepage import TimelinePage
    rec.keep(TimelinePage(rec.client()))
    rec.settle()


@p2
def catchup_followed_tags_trends_and_relationships(rec):
    import datetime
    from src.catchup import fetch
    tagged = status('100', tags=[{'name': 'golden'}],
                    created_at='2026-06-01T00:00:00.000Z')
    rec.queue[('GET', '/api/v1/timelines/home')] = [[tagged], []]
    # Two pages of followed tags: the Link cursor kept under
    # ``_more['followed_tags']`` is what asks for the second.
    rec.queue[('GET', '/api/v1/followed_tags')] = [
        ([{'name': 'golden'}],
         link_header('/api/v1/followed_tags', limit=200, max_id='77')),
        ([{'name': 'other'}], '')]
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
    got = []
    run = fetch.CatchUpRun(rec.client(), start, end, [ME],
                           done=got.append, pause_seconds=0)
    run.start()
    rec.settle()
    check('catch up run finishes', bool(got))
    rec.settle()
    pages_asked = [dict(entry['query']) for entry in rec.log
                   if entry['path'] == '/api/v1/followed_tags']
    check('followed tags follow the Link cursor to a second page',
          len(pages_asked) == 2 and pages_asked[1].get('max_id') == '77',
          '    asked: %r' % (pages_asked,))


# Notifications

@p2
def notification_policy_dialog_loads_and_saves(rec):
    dialog = notifications.NotificationPolicyDialog(rec.client())
    rec.settle()
    dialog.filter_rows['for_not_following'].set_selected(1)
    dialog.save()


@p2
def notification_controller_fetches_the_policy(rec):
    from src.notificationctrl import NotificationController
    stub = types.SimpleNamespace(client=rec.client(),
                                 requests_banner=Anything())
    bind_stub(NotificationController, stub, 'on_policy_loaded')
    NotificationController.fetch_policy(stub)
    rec.settle()


@p2
def notification_requests_page_rows_answer(rec):
    page = notifications.NotificationRequestsPage(rec.client())
    rec.settle()
    page.rows[0].answer('accept')
    rec.settle()


@p2
def desktop_notifier_polls(rec):
    from src.desktopnotify import DesktopNotifier
    client = rec.client()
    stub = types.SimpleNamespace(_polling_client=client, _last_id=None,
                                 _on_polled=lambda c, r, initial: None)
    DesktopNotifier._poll(stub, initial=True)
    stub._last_id = '55'
    DesktopNotifier._poll(stub)
    rec.settle()


# Timelines, conversations, explore, emoji, wrapped

@p2
def api_timeline_source_id_paging(rec):
    from src.timelinepage import ApiTimelineSource
    for path, params in (('/timelines/home', None),
                         ('/timelines/public', {'local': 'true'}),
                         ('/timelines/public', None),
                         ('/timelines/tag/golden', None),
                         ('/timelines/list/3', None),
                         ('/accounts/7/statuses', {'pinned': 'true'})):
        source = ApiTimelineSource(rec.client(), path, params)
        for page in ({'limit': 20}, {'limit': 20, 'max_id': '50'},
                     {'limit': 20, 'since_id': '60'}):
            source.fetch(page, lambda c, r, _d=None: None)


@p2
def api_timeline_source_link_and_none_paging(rec):
    from src.timelinepage import ApiTimelineSource
    path = '/api/v1/trends/statuses'
    rec.queue[('GET', path)] = [([status('100')], link_header(
        path, offset='20', limit='20')), [status('101')]]
    source = ApiTimelineSource(rec.client(), '/trends/statuses',
                               paging='link')

    def done(_c, result, _d=None):
        source.finish(result)

    source.fetch({'limit': 20}, done)
    rec.settle()
    source.fetch({'limit': 20, 'max_id': '100'}, done)
    rec.settle()
    source.fetch({'limit': 20, 'since_id': '101'}, done)
    rec.settle()
    ApiTimelineSource(rec.client(), '/timelines/link',
                      {'url': 'https://news.example/a'},
                      paging='none').fetch({'limit': 20}, done)
    rec.settle()


@p2
def window_timeline_places(rec):
    from src.window import BabbleWindow
    stub = types.SimpleNamespace(client=rec.client())
    expected = {'favourites': 'link', 'bookmarks': 'link', 'public': 'id'}
    for kind, params in (('favourites', None), ('bookmarks', None),
                         ('public', {'local': 'true'}),
                         ('public', None)):
        page = BabbleWindow._timeline_place(
            stub, kind, params, 'edit-find-symbolic', 'Title', 'Description')
        rec.keep(page)
        check('%s place pages by %s' % (kind, expected[kind]),
              page.source.paging == expected[kind],
              '    got %r' % (page.source.paging,))
    rec.settle()


@p2
def conversations_load_mark_read_and_delete(rec):
    from src import conversations
    page = conversations.ConversationsPage(rec.client())
    rec.settle()
    row = page.rows[0]
    row.mark_read()
    rec.settle()
    row._delete_confirmed()
    rec.settle()


@p2
def explore_pages_load(rec):
    page = explore.ExplorePage(rec.client())
    rec.settle()
    for name in ('hashtags', 'news', 'for-you'):
        if name in page.bins:
            page._populate(name)
            rec.settle()
    rec.keep(explore.NewsPage(rec.client()))
    rec.keep(explore.SuggestionsPage(rec.client()))
    rec.keep(explore.LinkTimelinePage(rec.client(), link_json()))
    rec.settle()


@p2
def suggestions_row_dismiss(rec):
    page = explore.SuggestionsPage(rec.client())
    rec.settle()
    page.paged.rows[0]._on_dismiss(None)
    rec.settle()


@p2
def emoji_chooser_loads_custom_emojis(rec):
    from src import emojichooser
    emojichooser._cache.clear()
    emojichooser.load_custom_emojis(rec.client(), lambda emojis: None)
    rec.settle()


@p2
def wrapped_loads_a_report_and_its_top_post(rec):
    from src import wrapped
    rec.keep(wrapped.WrappedDialog(Gtk.Box(), rec.client(), 2025))
    rec.settle()


@p2
def wrapped_without_a_year_loads_the_index(rec):
    from src import wrapped
    rec.keep(wrapped.WrappedDialog(Gtk.Box(), rec.client()))
    rec.settle()


@p2
def wrapped_generates_an_eligible_report(rec):
    from src import wrapped
    rec.fail[('GET', '/api/v1/annual_reports/2025')] = [404]
    dialog = wrapped.WrappedDialog(Gtk.Box(), rec.client(), 2025)
    rec.settle()
    dialog._generate(2025)
    rec.settle()


# Run

recorded = {}
for title, function in SCENARIOS:
    CURRENT = rec = Recorder()
    function(rec)
    rec.settle()
    recorded[title] = list(rec.log)
    if title not in ALLOW_NO_REQUESTS:
        check('%s sends at least one request' % title,
              any('method' in entry for entry in rec.log))

if UPDATE:
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, 'w', encoding='utf-8') as handle:
        json.dump({'scenarios': recorded}, handle, indent=1,
                  sort_keys=True)
        handle.write('\n')
    for title, log in recorded.items():
        print('note - %s: %d request(s) written' % (title, len(log)))
    print('note - wrote %s' % GOLDEN)
else:
    if not os.path.exists(GOLDEN):
        print('FAIL - %s is missing; run with BABBLE_GOLDEN_UPDATE=1'
              % GOLDEN)
        sys.exit(1)
    with open(GOLDEN, encoding='utf-8') as handle:
        expected = json.load(handle)['scenarios']

    def dump(log):
        return json.dumps(log, indent=1, sort_keys=True).splitlines()

    for title in recorded:
        if title not in expected:
            check('%s (%d requests)' % (title, len(recorded[title])),
                  False, '    not in the golden file')
            continue
        diff = list(difflib.unified_diff(
            dump(expected[title]), dump(recorded[title]),
            'golden', 'recorded', lineterm='', n=2))
        check('%s (%d requests)' % (title, len(recorded[title])),
              not diff, '\n'.join('    ' + line for line in diff))
    for title in expected:
        if title not in recorded:
            check('%s is still recorded' % title, False,
                  '    in the golden file but no longer driven')

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
