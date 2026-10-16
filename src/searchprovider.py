# SPDX-License-Identifier: GPL-3.0-or-later

"""GNOME Shell search provider (``org.gnome.Shell.SearchProvider2``).

Typing in the Activities overview searches the signed-in account: people
it follows and hashtags it follows first, from a small local index, then
the instance's own search. Result ids name what to open:

* ``acct:<id>``: a profile,
* ``tag:<name>``: a hashtag timeline,
* ``status:<id>``: a post's thread,
* ``search:<terms>``: Babble's search page with the terms entered.

Ids are local to the active account (``accounts.account_key``), the one
every search runs as. Shell may start the application through D-Bus
alone, without a window; the provider then reads the active account's
token itself rather than building a hidden window for it.
"""

import collections
import re
import time

from gi.repository import Gio, GLib

from . import accounts as account_store
from .i18n import gettext as _
from .statustext import parse_content

OBJECT_PATH = '/io/github/mauriciobc/Babble/SearchProvider'

INTERFACE_XML = '''
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
'''

# Shell asks again on every keystroke; the instance only hears the
# query the typing settled on.
DEBOUNCE_MS = 300
# How long the followed accounts and hashtags are trusted.
INDEX_TTL_SECONDS = 10 * 60
# A failed index fetch is retried sooner than a good one expires.
INDEX_RETRY_SECONDS = 60
MAX_ACCOUNTS = 5
MAX_TAGS = 3
MAX_STATUSES = 3
# Metas wait this long at most for avatars before falling back to the
# generic icon; Shell shows nothing until they answer.
AVATAR_WAIT_MS = 1000
AVATAR_CACHE_SIZE = 64
ITEM_CACHE_SIZE = 256
EXCERPT_LENGTH = 140

ACCOUNT_ICON = 'avatar-default-symbolic'
TAG_ICONS = ('tag-symbolic', 'system-search-symbolic')
SEARCH_ICON = 'system-search-symbolic'


def normalize_terms(terms):
    """The query Shell's *terms* stand for: joined, without the leading
    ``@`` or ``#`` that only says which kind of thing is meant."""
    return ' '.join(terms).strip().lstrip('@#').strip()


def plain_excerpt(status):
    """One line of a post: its content warning when it has one (the
    content stays behind it, as in the timeline), its text otherwise."""
    spoiler = (status.get('spoiler_text') or '').strip()
    if spoiler:
        # Translators: a search result's description for a post behind a
        # content warning; %s is the warning's text.
        return _('Content warning: %s') % spoiler
    text = ''.join(
        ' ' if segment['text'] == '\n' else
        (' ' if segment.get('sp_before') else '') + segment['text']
        + (' ' if segment.get('sp_after') else '')
        for segment in parse_content(status.get('content') or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > EXCERPT_LENGTH:
        text = text[:EXCERPT_LENGTH - 1].rstrip() + '…'
    return text


def _match_rank(account, query):
    """0 for a name or handle starting with *query*, 1 for one containing
    it, None for no match."""
    fields = [(account.get(key) or '').lower()
              for key in ('acct', 'username')]
    fields.append(account_store.strip_shortcodes(
        account.get('display_name') or '').lower())
    if any(field.startswith(query) for field in fields):
        return 0
    if any(query in field for field in fields):
        return 1
    return None


def _status_account(status):
    """The author whose avatar and name a post result carries; a boost
    shows the post it carries."""
    return (status.get('reblog') or status).get('account') or {}


class _Query:
    """One GetInitialResultSet/GetSubsearchResultSet call in flight."""

    def __init__(self, invocation, raw, query):
        self.invocation = invocation
        # What Shell was given (the search: result), what the instance
        # is asked, and its lowercase form for local matching.
        self.raw = raw
        self.query = query
        self.folded = query.lower()
        self.cancellable = Gio.Cancellable()
        self.timer = 0
        self.pending = 0
        self.network = {}
        self.client = None
        self.key = None
        self.answered = False


class SearchProvider:
    """Serves ``org.gnome.Shell.SearchProvider2`` for *app*.

    The application calls :meth:`register` from ``do_dbus_register`` and
    :meth:`unregister` from ``do_dbus_unregister``."""

    def __init__(self, app):
        self.app = app
        self._registrations = {}
        self._current = None
        # The active account's client when no window has one yet.
        self._own_client = None
        self._own_key = None
        self._client_waiters = None
        # key -> {'time', 'accounts', 'tags'}; only the active account's.
        self._index = None
        self._index_waiters = {}
        # Result id -> the account, tag or status it names, for metas and
        # activation; tied to the account they were searched as.
        self._items = collections.OrderedDict()
        self._items_key = None
        self._followed_tags = set()
        self._avatars = collections.OrderedDict()
        info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        self._interface = info.interfaces[0]

    # Registration

    def register(self, connection, object_path=OBJECT_PATH):
        try:
            registration = connection.register_object(
                object_path, self._interface, self._on_method_call,
                None, None)
        except GLib.Error as error:
            print('Babble: search provider not registered: %s'
                  % error.message)
            return False
        self._registrations[connection] = registration
        return registration > 0

    def unregister(self, connection):
        registration = self._registrations.pop(connection, 0)
        if registration:
            connection.unregister_object(registration)
        if self._current is not None:
            self._drop(self._current)

    def _on_method_call(self, _connection, _sender, _path, _interface,
                        method, parameters, invocation):
        args = parameters.unpack()
        if method == 'GetInitialResultSet':
            self._search(invocation, args[0])
        elif method == 'GetSubsearchResultSet':
            # The narrower terms can find what the wider ones ranked out
            # (the instance search is capped), so search afresh.
            self._search(invocation, args[1])
        elif method == 'GetResultMetas':
            self._result_metas(invocation, args[0])
        elif method == 'ActivateResult':
            identifier, terms, timestamp = args
            invocation.return_value(None)
            self._activate(identifier, terms, timestamp)
        elif method == 'LaunchSearch':
            terms, timestamp = args
            invocation.return_value(None)
            self._launch_search(' '.join(terms).strip(), timestamp)
        else:
            invocation.return_dbus_error(
                'org.freedesktop.DBus.Error.UnknownMethod',
                'No such method: %s' % method)

    # Client

    def _signed_in_window(self):
        for window in self.app.get_windows():
            if getattr(window, 'client', None) is not None \
                    and getattr(window, 'account', None) is not None:
                return window
        return None

    def _with_client(self, callback):
        """``callback(client, account)`` with the active account's
        client, or ``(None, None)`` when nobody is signed in. A window's
        session is reused; without one the token is read from the
        keyring, once."""
        window = self._signed_in_window()
        if window is not None:
            callback(window.client, window.account)
            return
        from .pages import settings
        account = account_store.active_account(settings())
        if account is None:
            callback(None, None)
            return
        key = account_store.account_key(account)
        if self._own_key == key and self._own_client is not None:
            callback(self._own_client, account)
            return
        if self._own_key == key and self._client_waiters is not None:
            self._client_waiters.append(callback)
            return
        from .mastodon import MastodonClient
        client = MastodonClient(account['instance'])
        self._own_key = key
        self._own_client = None
        self._client_waiters = [callback]

        def loaded(token, error=None):
            if self._own_key != key:
                return
            waiters, self._client_waiters = self._client_waiters or [], None
            # A keyring failure is treated like a missing token here: no
            # client, no results. The account itself is left alone.
            if error is not None or token is None:
                self._own_key = None
                for waiter in waiters:
                    waiter(None, None)
                return
            client.token = token
            self._own_client = client
            for waiter in waiters:
                waiter(client, account)

        client.load_secret_token(account['id'], loaded)

    # Searching

    def _search(self, invocation, terms):
        if self._current is not None:
            self._drop(self._current)
        raw = ' '.join(terms).strip()
        query = normalize_terms(terms)
        if not query:
            invocation.return_value(GLib.Variant('(as)', ([],)))
            return
        search = self._current = _Query(invocation, raw, query)
        self.app.hold()
        self._with_client(
            lambda client, account: self._start(search, client, account))

    def _start(self, search, client, account):
        if search.answered:
            return
        if client is None:
            self._answer(search, [])
            return
        search.client = client
        search.key = account_store.account_key(account)
        search.pending = 2
        self._ensure_index(client, account, lambda: self._step(search))
        search.timer = GLib.timeout_add(
            DEBOUNCE_MS, self._fire_network, search)

    def _fire_network(self, search):
        search.timer = 0
        if search.answered:
            return GLib.SOURCE_REMOVE

        def done(client, result, _data=None):
            if search.answered:
                return
            try:
                search.network = client.request_finish(result) or {}
            except GLib.Error:
                search.network = {}
            self._step(search)

        search.client.request_async(
            'GET', '/api/v2/search',
            params={'q': search.query, 'limit': MAX_ACCOUNTS,
                    'resolve': 'false'},
            callback=done, cancellable=search.cancellable)
        return GLib.SOURCE_REMOVE

    def _step(self, search):
        if search.answered:
            return
        search.pending -= 1
        if search.pending == 0:
            self._answer(search, self._merge(search))

    def _drop(self, search):
        """A newer call supersedes *search*: stop it and answer empty."""
        if search.timer:
            GLib.source_remove(search.timer)
            search.timer = 0
        search.cancellable.cancel()
        if not search.answered:
            self._answer(search, [])

    def _answer(self, search, results):
        search.answered = True
        if self._current is search:
            self._current = None
        search.invocation.return_value(GLib.Variant('(as)', (results,)))
        self.app.release()

    def _remember(self, key, result_id, item):
        if self._items_key != key:
            self._items.clear()
            self._items_key = key
        self._items[result_id] = item
        self._items.move_to_end(result_id)
        while len(self._items) > ITEM_CACHE_SIZE:
            self._items.popitem(last=False)

    def _merge(self, search):
        query = search.folded
        index = self._index if self._index is not None \
            and self._index['key'] == search.key else None
        network = search.network if isinstance(search.network, dict) else {}

        local = []
        for account in (index or {}).get('accounts', ()):
            rank = _match_rank(account, query)
            if rank is not None:
                local.append((rank, account))
        local.sort(key=lambda pair: pair[0])
        people = []
        seen = set()
        for account in [a for _r, a in local] + list(
                network.get('accounts') or ()):
            account_id = str(account.get('id') or '')
            if account_id and account_id not in seen:
                seen.add(account_id)
                people.append(account)
        people = people[:MAX_ACCOUNTS]

        followed = [tag for tag in (index or {}).get('tags', ())
                    if query in (tag.get('name') or '').lower()]
        followed.sort(key=lambda tag: not tag['name'].lower()
                      .startswith(query))
        tags = []
        seen = set()
        for tag in followed + list(network.get('hashtags') or ()):
            name = tag.get('name') or ''
            if name and name.lower() not in seen:
                seen.add(name.lower())
                tags.append(tag)
        tags = tags[:MAX_TAGS]

        statuses = [status for status in network.get('statuses') or ()
                    if status.get('id')][:MAX_STATUSES]

        results = []
        for account in people:
            result_id = 'acct:%s' % account['id']
            self._remember(search.key, result_id, account)
            results.append(result_id)
        for tag in tags:
            result_id = 'tag:%s' % tag['name']
            self._remember(search.key, result_id, tag)
            results.append(result_id)
        for status in statuses:
            result_id = 'status:%s' % status['id']
            self._remember(search.key, result_id, status)
            results.append(result_id)
        results.append('search:%s' % search.raw)
        return results

    # Local index

    def _ensure_index(self, client, account, done):
        """Run *done* once the followed accounts and hashtags of
        *account* are known: fetched on the first search, then again
        whenever they are older than INDEX_TTL_SECONDS."""
        key = account_store.account_key(account)
        index = self._index
        if index is not None and index['key'] == key \
                and time.monotonic() - index['time'] < INDEX_TTL_SECONDS:
            done()
            return
        waiters = self._index_waiters.get(key)
        if waiters is not None:
            waiters.append(done)
            return
        self._index_waiters[key] = [done]
        fetched = {'accounts': [], 'tags': [], 'pending': 2, 'failed': False}

        def finished(field, client, result):
            try:
                data = client.request_finish(result)
            except GLib.Error:
                data = None
                fetched['failed'] = True
            fetched[field] = data if isinstance(data, list) else []
            fetched['pending'] -= 1
            if fetched['pending']:
                return
            stamp = time.monotonic()
            if fetched['failed']:
                stamp -= INDEX_TTL_SECONDS - INDEX_RETRY_SECONDS
            self._index = {'key': key, 'time': stamp,
                           'accounts': fetched['accounts'],
                           'tags': fetched['tags']}
            self._followed_tags = {(tag.get('name') or '').lower()
                                   for tag in fetched['tags']}
            for waiter in self._index_waiters.pop(key, ()):
                waiter()

        client.request_async(
            'GET', '/accounts/%s/following' % account['id'],
            params={'limit': 80},
            callback=lambda c, r, _d=None: finished('accounts', c, r))
        client.request_async(
            'GET', '/followed_tags', params={'limit': 100},
            callback=lambda c, r, _d=None: finished('tags', c, r))

    # Metas

    def _result_metas(self, invocation, identifiers):
        urls = set()
        for identifier in identifiers:
            kind, _sep, _value = identifier.partition(':')
            item = self._items.get(identifier)
            if item is None:
                continue
            if kind == 'acct':
                url = self._avatar_url(item)
            elif kind == 'status':
                url = self._avatar_url(_status_account(item))
            else:
                url = ''
            if url and url not in self._avatars:
                urls.add(url)
        if not urls:
            invocation.return_value(self._metas_variant(identifiers))
            return
        self.app.hold()
        state = {'pending': len(urls), 'answered': False, 'timer': 0}

        def answer():
            if state['answered']:
                return GLib.SOURCE_REMOVE
            state['answered'] = True
            if state['timer']:
                GLib.source_remove(state['timer'])
                state['timer'] = 0
            invocation.return_value(self._metas_variant(identifiers))
            self.app.release()
            return GLib.SOURCE_REMOVE

        def loaded():
            state['pending'] -= 1
            if state['pending'] == 0:
                answer()

        def expired():
            state['timer'] = 0
            return answer()

        state['timer'] = GLib.timeout_add(AVATAR_WAIT_MS, expired)
        for url in urls:
            self._load_avatar(url, loaded)

    @staticmethod
    def _avatar_url(account):
        # The static image: Shell shows one frame of an animated one.
        return account.get('avatar_static') or account.get('avatar') or ''

    def _store_avatar(self, url, data):
        self._avatars[url] = data
        self._avatars.move_to_end(url)
        while len(self._avatars) > AVATAR_CACHE_SIZE:
            self._avatars.popitem(last=False)

    def _load_avatar(self, url, done):
        """Image bytes for *url*: the texture a row already decoded when
        there is one, the download otherwise. *done* runs either way."""
        # timeline first: it pins the Soup version before the import.
        from .mediacache import AvatarCache
        from gi.repository import Soup
        # Through the store's getter, not its dict: this reads another
        # module's cache, and its internals are not this module's to know.
        texture = AvatarCache.store.get(url)
        if texture is not None:
            self._store_avatar(url, texture.save_to_png_bytes())
            done()
            return
        message = Soup.Message.new('GET', url)
        if message is None:
            done()
            return

        def fetched(session, result, _data=None):
            try:
                data = session.send_and_read_finish(result)
            except GLib.Error:
                data = None
            if data is not None and data.get_size() \
                    and 200 <= message.get_status() < 300:
                self._store_avatar(url, data)
            done()

        AvatarCache.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, None, fetched)

    def _avatar_icon(self, account):
        data = self._avatars.get(self._avatar_url(account))
        if data is not None:
            return Gio.BytesIcon.new(data)
        return Gio.ThemedIcon.new(ACCOUNT_ICON)

    def _meta(self, identifier):
        kind, _sep, value = identifier.partition(':')
        item = self._items.get(identifier)
        if kind == 'search':
            # Translators: the last Activities-overview search result,
            # which opens Babble's search; %s is what was typed.
            name = _('Search for “%s”') % value
            description = _('Search people, hashtags and posts in Babble')
            icon = Gio.ThemedIcon.new(SEARCH_ICON)
            clipboard = value
        elif item is None:
            return None
        elif kind == 'acct':
            name = account_store.plain_name(item) or item.get('acct', '')
            description = '@' + (item.get('acct') or '')
            icon = self._avatar_icon(item)
            clipboard = item.get('url') or description
        elif kind == 'tag':
            name = '#' + (item.get('name') or value)
            description = _('Followed hashtag') \
                if name[1:].lower() in self._followed_tags \
                else _('Hashtag')
            icon = Gio.ThemedIcon.new_from_names(list(TAG_ICONS))
            clipboard = item.get('url') or name
        elif kind == 'status':
            author = _status_account(item)
            name = account_store.plain_name(author) \
                or author.get('acct', '')
            description = plain_excerpt(item.get('reblog') or item)
            icon = self._avatar_icon(author)
            clipboard = (item.get('reblog') or item).get('url') \
                or description
        else:
            return None
        return {
            'id': GLib.Variant('s', identifier),
            'name': GLib.Variant('s', name),
            'description': GLib.Variant('s', description),
            'icon': icon.serialize(),
            'clipboardText': GLib.Variant('s', clipboard),
        }

    def _metas_variant(self, identifiers):
        metas = [meta for meta in map(self._meta, identifiers)
                 if meta is not None]
        return GLib.Variant('(aa{sv})', (metas,))

    # Activation

    def _present(self, timestamp):
        # timestamp is kept for the D-Bus ActivateResult / LaunchSearch
        # signatures; gtk_window_present_with_time() was deprecated in
        # GTK 4.14 with no timestamp-taking successor.
        window = self.app.ensure_window()
        window.present()
        return window

    @staticmethod
    def _when_signed_in(window, callback):
        """Run ``callback(window)`` once *window* has a session: at once
        when it does, after the keyring restore when the window was
        just created for this activation. A window that settles on the
        login page instead drops it."""
        if window.client is not None and window.account is not None:
            callback(window)
            return
        stack = window.main_stack
        target = window.weak_ref()
        state = {'handler': 0, 'timer': 0}

        def finish():
            if state['handler']:
                stack.disconnect(state['handler'])
                state['handler'] = 0
            if state['timer']:
                GLib.source_remove(state['timer'])
                state['timer'] = 0

        def changed(_stack, _pspec):
            owner = target()
            if owner is None:
                finish()
                return
            name = stack.get_visible_child_name()
            if name == 'shell' and owner.client is not None:
                finish()
                callback(owner)
            elif name == 'login':
                finish()

        def expired():
            state['timer'] = 0
            finish()
            return GLib.SOURCE_REMOVE

        state['handler'] = stack.connect('notify::visible-child-name',
                                         changed)
        # The restore that already ended on the login page emits nothing.
        state['timer'] = GLib.timeout_add_seconds(30, expired)

    def _activate(self, identifier, terms, timestamp):
        window = self._present(timestamp)
        kind, _sep, value = identifier.partition(':')
        item = self._items.get(identifier)
        key = self._items_key
        if kind == 'search' or not value:
            query = value if kind == 'search' else ' '.join(terms).strip()
            self._when_signed_in(
                window, lambda win: self._open_search(win, query))
            return

        def route(win):
            if account_store.account_key(win.account) != key:
                # Switched accounts since: the id means nothing here.
                self._open_search(win, ' '.join(terms).strip())
            elif kind == 'acct':
                self._open_profile(win, value, item)
            elif kind == 'tag':
                from .hashtags import push_hashtag
                push_hashtag(win, win.client, value)
            elif kind == 'status':
                self._open_status(win, value, item)

        self._when_signed_in(window, route)

    @staticmethod
    def _fetch(win, path, heading, opener):
        def done(client, result, _data=None):
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                from .pages import report_error
                report_error(win, heading, error)
                return
            opener(data)
        win.client.request_async('GET', path, callback=done)

    def _open_profile(self, win, account_id, account):
        from . import pages
        if account is not None:
            pages.open_profile(win, win.client, account)
            return
        self._fetch(win, '/accounts/%s' % account_id,
                    _('Could not open the profile'),
                    lambda data: pages.open_profile(win, win.client, data))

    def _open_status(self, win, status_id, status):
        from . import pages
        if status is not None:
            pages.open_status(win, win.client, status)
            return
        self._fetch(win, '/statuses/%s' % status_id,
                    _('Could not open the post'),
                    lambda data: pages.open_status(win, win.client, data))

    @staticmethod
    def _open_search(win, query):
        win.open_search()
        page = getattr(win, '_place_children', {}).get('search')
        if page is not None and query:
            page.set_query(query, submit=True)

    def _launch_search(self, query, timestamp):
        window = self._present(timestamp)
        self._when_signed_in(
            window, lambda win: self._open_search(win, query))
