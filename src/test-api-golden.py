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
            task.return_value(GObject.Value(
                GObject.TYPE_PYOBJECT,
                {'data': canned(entry['method'], entry['path']),
                 'link': ''}))
        return GLib.SOURCE_REMOVE

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

    def clear(self):
        self.settle()
        self.log.clear()
        self.toasts.clear()

    def client(self, info=None):
        client = MastodonClient(INSTANCE, token=TOKEN)
        client.instance_info = info or {}
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
    CURRENT.record(task, message)


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
    wrapper(rec, 'markers_async', ['home', 'notifications'])
    wrapper(rec, 'timelines_home_async', params={'limit': 20})
    wrapper(rec, 'timelines_public_async',
            params={'local': 'true', 'limit': 20})
    wrapper(rec, 'trends_statuses_async', params={'limit': 10})


@scenario
def client_notification_wrappers(rec):
    wrapper(rec, 'notifications_async', params={'limit': 30})
    wrapper(rec, 'notifications_unread_count_async',
            params={'limit': 100})
    wrapper(rec, 'notifications_clear_async')
    wrapper(rec, 'notifications_dismiss_async', '12')
    wrapper(rec, 'notifications_mark_read_async', '34')


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
