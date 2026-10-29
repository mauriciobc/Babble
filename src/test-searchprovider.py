#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GNOME Shell search provider answers over D-Bus.

Registers ``searchprovider.SearchProvider`` on the session bus the runner
already started (``dbus-run-session`` in CI, the desktop bus otherwise)
for a stub application whose window holds a stub client with canned
answers, then calls it through a second connection the way Shell does:
result ordering and caps, the debounce, the local index of followed
accounts and hashtags, result metas, and where ActivateResult and
LaunchSearch send the window.

A private ``Gio.TestDBus`` is deliberately not used. Its ``dbus-daemon``
is a child of this process, inherits meson's stdout pipe, and outlives
the interpreter, so meson waits for EOF until the per-test timeout even
though every check passed.

Exits 77 (skip) when no display or no session bus is available: the
routes and the avatar texture are GTK's."""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
# Keep GDK off the settings portal. A portal client on this bus is not
# what the checks measure, and its teardown has stalled the runner.
os.environ['GDK_DEBUG'] = ','.join(
    filter(None, (os.environ.get('GDK_DEBUG'), 'no-portals')))
os.environ['NO_AT_BRIDGE'] = '1'

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Gdk, Gio, GLib  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

from src import hashtags, pages, searchprovider  # noqa: E402
from src.mediacache import AvatarCache  # noqa: E402

failures = []
PATH = searchprovider.OBJECT_PATH
IFACE = 'org.gnome.Shell.SearchProvider2'
INSTANCE = 'https://example.social'
AVATAR_URL = 'https://example.social/avatars/alice.png'


def check(condition, label):
    print(('ok - ' if condition else 'FAIL - ') + label)
    if not condition:
        failures.append(label)


def account(account_id, acct, name='', avatar=''):
    return {'id': account_id, 'acct': acct, 'username': acct.split('@')[0],
            'display_name': name, 'avatar': avatar, 'avatar_static': avatar,
            'url': 'https://example.social/@' + acct}


FOLLOWING = [
    account('1', 'alice', 'Alice :blobcat:', AVATAR_URL),
    account('2', 'malin', 'Malin'),
    account('9', 'bob', 'Bob'),
]
FOLLOWED_TAGS = [{'name': 'alife', 'url': 'https://example.social/tags/alife'},
                 {'name': 'gnome'}]
SEARCH = {
    'accounts': [account('1', 'alice', 'Alice')] + [
        account(str(n), 'alix%d@far.example' % n) for n in range(3, 8)],
    'hashtags': [{'name': 'alisa'}, {'name': 'ALIFE'}, {'name': 'alibi'},
                 {'name': 'alix'}],
    'statuses': [
        {'id': 's1', 'account': account('1', 'alice', 'Alice', AVATAR_URL),
         'spoiler_text': '',
         'content': '<p>Hello <b>alien</b> world</p><p>again</p>'},
        {'id': 's2', 'account': account('3', 'alix3@far.example'),
         'spoiler_text': 'spoilers', 'content': '<p>hidden</p>'},
        {'id': 's3', 'account': account('4', 'x'), 'content': '<p>c</p>'},
        {'id': 's4', 'account': account('5', 'y'), 'content': '<p>d</p>'},
    ],
}


class StubClient:
    def __init__(self):
        self.calls = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.calls.append((method, path, dict(params or {})))
        if path == '/accounts/me/following':
            data = FOLLOWING
        elif path == '/followed_tags':
            data = FOLLOWED_TAGS
        elif path == '/api/v2/search':
            data = SEARCH if (params or {}).get('q') == 'ali' else {}
        else:
            data = {}

        def reply():
            if cancellable is not None and cancellable.is_cancelled():
                callback(self, GLib.Error('cancelled'))
            else:
                callback(self, data)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(5, reply)

    def request_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def paths(self, path):
        return [call for call in self.calls if call[1] == path]


class StubSearchPage:
    def __init__(self):
        self.queries = []

    def set_query(self, query, submit=False):
        self.queries.append((query, submit))


class StubWindow:
    def __init__(self):
        self.client = StubClient()
        self.account = {'id': 'me', 'acct': 'me', 'instance': INSTANCE}
        self.presented = []
        self.searches_opened = 0
        self.navigation = SimpleNamespace(children={})

    def present_with_time(self, timestamp):
        self.presented.append(timestamp)

    def present(self):
        self.presented.append(None)

    def open_search(self):
        self.searches_opened += 1
        self.navigation.children.setdefault('search', StubSearchPage())


class StubApp:
    def __init__(self, windows):
        self.windows = windows
        self.holds = 0

    def get_windows(self):
        return list(self.windows)

    def ensure_window(self):
        return self.windows[0]

    def hold(self):
        self.holds += 1

    def release(self):
        self.holds -= 1


routes = []
pages.open_profile = lambda win, client, account=None, acct=None: \
    routes.append(('profile', win, client, account))
pages.open_status = lambda win, client, status: \
    routes.append(('status', win, client, status))
hashtags.push_hashtag = lambda win, client, name: \
    routes.append(('tag', win, client, name))

loop = GLib.MainLoop()


def spin(ms):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


# Shell talks to the provider's bus name; the unique name of the
# connection the provider is registered on stands in for it.
destination = None


def call(connection, method, args, signature):
    """Call *method*; returns a box filled with the unpacked reply."""
    box = {}

    def done(source, result):
        try:
            box['raw'] = source.call_finish(result)
            box['value'] = box['raw'].unpack()
        except GLib.Error as error:
            box['error'] = error.message
        loop.quit()

    connection.call(destination, PATH, IFACE, method,
                    GLib.Variant(signature, args), None,
                    Gio.DBusCallFlags.NONE, 5000, None, done)
    return box


def wait_for(box):
    """Run the loop until *box* is filled (5 s at most)."""
    state = {'expired': False}

    def expired():
        state['expired'] = True
        box.setdefault('error', 'timed out')
        loop.quit()
        return GLib.SOURCE_REMOVE
    timer = GLib.timeout_add(5000, expired)
    while not box:
        loop.run()
    if not state['expired']:
        GLib.source_remove(timer)
    return box


def call_sync(connection, method, args, signature):
    box = wait_for(call(connection, method, args, signature))
    check('error' not in box,
          '%s answered without error (%s)' % (method, box.get('error')))
    return box.get('value')


def session_connection():
    """A private client of the bus the runner already started.

    Not ``Gio.bus_get_sync``: that returns one shared connection, and the
    second provider has to export the same object path under its own
    unique name. Not ``Gio.TestDBus``: that spawns a ``dbus-daemon`` which
    inherits meson's stdout pipe and outlives this process.
    """
    address = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    if not address:
        return None
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    return Gio.DBusConnection.new_for_address_sync(
        address, flags, None, None)


try:
    service = session_connection()
    shell = session_connection()
except GLib.Error:
    service = shell = None
if service is None or shell is None:
    print('SKIP - no session bus available')
    sys.exit(77)
connections = [service, shell]
try:
    destination = service.get_unique_name()

    window = StubWindow()
    app = StubApp([window])
    provider = searchprovider.SearchProvider(app)
    check(provider.register(service, PATH), 'provider registers its object')

    # Debounce: a keystroke superseded within 300 ms never reaches the
    # instance, and its call is answered empty.
    first = call(shell, 'GetInitialResultSet', (['a'],), '(as)')
    spin(50)
    second = call(shell, 'GetInitialResultSet', (['ali'],), '(as)')
    wait_for(second)
    wait_for(first)
    check(first.get('value') == ([],),
          'superseded search answers an empty result set')
    results = second.get('value', ([],))[0]
    searches = window.client.paths('/api/v2/search')
    check([c[2].get('q') for c in searches] == ['ali'],
          'only the settled query reaches /api/v2/search (got %r)'
          % [c[2].get('q') for c in searches])
    check(searches and searches[0][2].get('resolve') == 'false',
          'Shell searches do not resolve remote accounts')
    check(len(window.client.paths('/accounts/me/following')) == 1
          and len(window.client.paths('/followed_tags')) == 1,
          'local index fetched once, on the first search')
    expected = ['acct:1', 'acct:2', 'acct:3', 'acct:4', 'acct:5',
                'tag:alife', 'tag:alisa', 'tag:alibi',
                'status:s1', 'status:s2', 'status:s3', 'search:ali']
    check(results == expected,
          'followed first, capped 5+3+3, search last (got %r)' % results)
    check(app.holds == 0, 'application hold released after answering')

    # Leading # is dropped from the query, kept in the search: result;
    # the index is reused within its lifetime.
    results = call_sync(shell, 'GetSubsearchResultSet',
                        (results, ['#gno']), '(asas)')[0]
    check(results == ['tag:gnome', 'search:#gno'],
          'followed hashtag found for #gno (got %r)' % results)
    check(window.client.paths('/api/v2/search')[-1][2].get('q') == 'gno',
          'leading # stripped from the instance query')
    check(len(window.client.paths('/followed_tags')) == 1,
          'index reused by the next search')

    empty = call_sync(shell, 'GetInitialResultSet', (['  '],), '(as)')
    check(empty == ([],), 'blank terms give no results')

    # Metas
    texture = Gdk.MemoryTexture.new(
        1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(b'\xff\0\0\xff'), 4)
    AvatarCache.store.put(AVATAR_URL, texture)
    ids = ['acct:1', 'acct:3', 'tag:alife', 'tag:alisa', 'status:s1',
           'status:s2', 'search:ali', 'acct:unknown']
    box = wait_for(call(shell, 'GetResultMetas', (ids,), '(as)'))
    check('error' not in box, 'GetResultMetas answered without error')
    metas = box['value'][0]
    # Icons compared as GIcons: an unpacked dict loses the variant types.
    icons = {}
    raw = box['raw'].get_child_value(0)
    for index in range(raw.n_children()):
        entry = raw.get_child_value(index)
        icons[entry.lookup_value('id', None).get_string()] = \
            Gio.Icon.deserialize(entry.lookup_value('icon', None))
    by_id = {meta['id']: meta for meta in metas}
    check([meta['id'] for meta in metas] == ids[:-1],
          'metas in request order, unknown ids skipped')
    alice = by_id.get('acct:1', {})
    check(alice.get('name') == 'Alice'
          and alice.get('description') == '@alice',
          'account meta: plain display name and handle (got %r)'
          % {k: alice.get(k) for k in ('name', 'description')})
    check(isinstance(icons.get('acct:1'), Gio.BytesIcon),
          'cached avatar texture becomes a BytesIcon')
    icon = icons.get('acct:3')
    check(isinstance(icon, Gio.ThemedIcon)
          and 'avatar-default-symbolic' in icon.get_names(),
          'no avatar falls back to avatar-default-symbolic')
    check(by_id['tag:alife']['name'] == '#alife'
          and by_id['tag:alife']['description'] == 'Followed hashtag'
          and by_id['tag:alisa']['description'] == 'Hashtag',
          'hashtag metas tell followed ones apart')
    status = by_id['status:s1']
    check(status['name'] == 'Alice'
          and status['description'] == 'Hello alien world again',
          'post meta: author and plain-text excerpt (got %r)'
          % status['description'])
    check(by_id['status:s2']['description'] == 'Content warning: spoilers',
          'post behind a content warning shows the warning')
    search_meta = by_id['search:ali']
    icon = icons.get('search:ali')
    check(search_meta['name'] == 'Search for “ali”'
          and isinstance(icon, Gio.ThemedIcon),
          'search: meta names the terms')
    check(all({'id', 'name', 'description', 'icon'} <= set(meta)
              for meta in metas), 'every meta carries id/name/desc/icon')

    # Activation
    call_sync(shell, 'ActivateResult', ('acct:1', ['ali'], 42), '(sasu)')
    spin(20)
    # GTK 4.14 dropped present_with_time; the D-Bus timestamp is ignored.
    check(window.presented[-1] is None, 'window presented')
    check(routes and routes[-1][0] == 'profile'
          and routes[-1][1] is window and routes[-1][2] is window.client
          and routes[-1][3]['id'] == '1',
          'acct: opens the profile of that account')
    call_sync(shell, 'ActivateResult', ('tag:alife', ['ali'], 43), '(sasu)')
    spin(20)
    check(routes[-1][0] == 'tag' and routes[-1][3] == 'alife',
          'tag: opens the hashtag timeline')
    call_sync(shell, 'ActivateResult', ('status:s2', ['ali'], 44), '(sasu)')
    spin(20)
    check(routes[-1][0] == 'status' and routes[-1][3]['id'] == 's2',
          'status: opens the thread of that post')
    call_sync(shell, 'ActivateResult', ('search:#ali', ['#ali'], 45),
              '(sasu)')
    spin(20)
    page = window.navigation.children.get('search')
    check(page is not None and page.queries[-1] == ('#ali', True),
          'search: opens the search page with the terms submitted')
    before = len(window.presented)
    call_sync(shell, 'LaunchSearch', (['foo', 'bar'], 46), '(asu)')
    spin(20)
    check(page.queries[-1] == ('foo bar', True)
          and len(window.presented) == before + 1
          and window.presented[-1] is None,
          'LaunchSearch passes the joined terms to the search page')

    # Ensure this process-local settings backend has no active account.
    settings = pages.settings()
    settings.set_strv('accounts', [])
    settings.set_string('active-account', '')
    lonely = searchprovider.SearchProvider(StubApp([]))
    other = session_connection()
    connections.append(other)
    check(lonely.register(other, PATH), 'second provider registers')
    destination = other.get_unique_name()
    results = call_sync(shell, 'GetInitialResultSet', (['ali'],), '(as)')
    check(results == ([],), 'signed out: empty result set')
    lonely.unregister(other)
    provider.unregister(service)
    destination = service.get_unique_name()
    box = call(shell, 'GetInitialResultSet', (['ali'],), '(as)')
    wait_for(box)
    check(box.get('error', 'timed out') != 'timed out',
          'unregister removes the object')
finally:
    code = 1 if failures else 0
    for connection in connections:
        try:
            connection.close_sync(None)
        except GLib.Error:
            pass
    connections.clear()
    # Leave without GObject teardown. It is not what this test measures,
    # and a stalled finalizer holds the runner the same way the private
    # bus daemon did. Flush first: under meson stdout is a pipe, and
    # os._exit discards the buffer.
    sys.stdout.flush()
    os._exit(code)
