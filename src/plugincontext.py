# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-plugin context: the object an extension talks to the host through.

Holds settings, secrets, HTTP, signals and the panel. Lives beside
``pluginhost`` so the host file stays under the line limit.
"""

import json
import traceback
from urllib.parse import urlencode

from gi.repository import GLib, GObject, Gio  # noqa: E402

from . import accounts  # noqa: E402
from . import plugin as api  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .pluginsupport import PluginSettings as _Settings  # noqa: E402
from .pluginsupport import Scope as _Scope  # noqa: E402

SECRET_PREFIX = 'babble-plugin:'


def _log(level, message, plugin_id=None):
    fields = {'MESSAGE': GLib.Variant('s', message)}
    if plugin_id:
        fields['BABBLE_PLUGIN'] = GLib.Variant('s', plugin_id)
    GLib.log_variant('Babble', level, GLib.Variant('a{sv}', fields))


def post_of(status, instance=''):
    """A :class:`plugin.Post` for *status*, a status as the API sent it
    (the boosted one for a boost)."""
    from .statustext import parse_content
    account = status.get('account') or {}
    acct = account.get('acct') or ''
    handle = ''
    if acct:
        handle = accounts.full_handle(
            {'acct': acct, 'instance': instance or ''})
    text = []
    space = False  # the source had whitespace after the last segment
    for segment in parse_content(status.get('content') or ''):
        piece = segment.get('text') or ''
        if (space or segment.get('sp_before')) and text and piece \
                and not text[-1].endswith('\n') and piece[0] != '\n':
            text.append(' ')
        text.append(piece)
        space = bool(segment.get('sp_after'))
    media = tuple(
        api.Media(kind=m.get('type') or 'unknown', url=m.get('url') or '',
                  description=m.get('description') or '')
        for m in status.get('media_attachments') or ())
    return api.Post(
        id=str(status.get('id') or ''), url=status.get('url') or '',
        uri=status.get('uri') or '', instance=instance or '',
        author=acct, author_handle=handle,
        author_name=account.get('display_name') or '',
        text=''.join(text).strip(),
        tags=tuple((t.get('name') or '').lower()
                   for t in status.get('tags') or ()),
        language=status.get('language') or '',
        created_at=status.get('created_at') or '',
        visibility=status.get('visibility') or '',
        sensitive=bool(status.get('sensitive')),
        spoiler_text=status.get('spoiler_text') or '', media=media)


class Context(api.Context):

    def __init__(self, host, info):
        self._host = host
        self.plugin_id = info.get_module_name()
        self.version = info.get_version() or ''
        self.name = info.get_name() or self.plugin_id
        self.active = True
        self._settings = None
        self._data = {}
        self._scope = _Scope()
        self._scopes = {}  # id(extension) -> _Scope
        self._panel = None

    def guard(self, func, *args, widget=None, default=None):
        """``func(*args)``, and on an exception the plugin is turned off."""
        if not self.active:
            return default
        try:
            return func(*args)
        except Exception:
            self._host.fail(self.plugin_id, traceback.format_exc(), widget)
            return default

    def quietly(self, func, *args):
        """``func(*args)`` on the way out: an exception is logged and
        nothing more, since the plugin is being dropped anyway."""
        try:
            func(*args)
        except Exception:
            _log(GLib.LogLevelFlags.LEVEL_WARNING, '%s: %s' % (
                self.plugin_id, traceback.format_exc()), self.plugin_id)

    def scope_of(self, owner):
        if owner is None:
            return self._scope
        scope = self._scopes.get(id(owner))
        if scope is None:
            scope = self._scopes[id(owner)] = _Scope()
        return scope

    def release(self, owner):
        """Close what *owner*, an extension, made."""
        scope = self._scopes.pop(id(owner), None)
        if scope is not None:
            scope.close()

    def close(self):
        self.active = False
        panel, self._panel = self._panel, None
        if panel is not None:
            self.quietly(panel.close)
        for scope in self._scopes.values():
            scope.close()
        self._scopes = {}
        self._scope.close()
        self._data.clear()

    @property
    def settings(self):
        if self._settings is None:
            self._settings = _Settings(self.plugin_id)
        return self._settings

    @property
    def account(self):
        from . import appsettings
        found = accounts.active_account(appsettings.settings())
        if found is None:
            return None
        return api.Account(handle=accounts.full_handle(found),
                           instance=found.get('instance') or '',
                           host=accounts.instance_host(found))

    @property
    def data(self):
        return self._data

    def connect(self, emitter, signal, callback, *extra, owner=None):
        scope = self.scope_of(owner)
        target = getattr(callback, '__self__', None)
        if isinstance(target, GObject.Object):
            # A widget's own method, held weakly as weakconnect.connect_weak
            # does: a dialog connecting its buttons to itself is then
            # freed when it closes, not when the plugin is disabled.
            func, target_ref = callback.__func__, target.weak_ref()

            def callback(*args):
                owner_object = target_ref()
                if owner_object is not None:
                    return func(owner_object, *args)
                return None

        def handler(*args):
            if not scope.active:
                return None
            return self.guard(callback, *args)
        handler_id = emitter.connect(signal, handler, *extra)
        scope.handlers = [(r, h) for r, h in scope.handlers
                          if r() is not None]
        scope.handlers.append((emitter.weak_ref(), handler_id))
        return handler_id

    def disconnect(self, emitter, handler_id):
        emitter.disconnect(handler_id)
        for scope in [self._scope, *self._scopes.values()]:
            scope.handlers = [(r, h) for r, h in scope.handlers
                              if h != handler_id or r() is not emitter]

    def request(self, method, url, callback, json=None, form=None,
                headers=None, owner=None):
        from gi.repository import Soup
        if not url.startswith(('https://', 'http://')):
            raise ValueError('only http and https URLs: %r' % url)
        message = Soup.Message.new(method, url)
        if message is None:
            raise ValueError('not a URL: %r' % url)
        scope = self.scope_of(owner)
        request_headers = message.get_request_headers()
        request_headers.replace('User-Agent', '%s %s/%s' % (
            self._host.user_agent(), self.plugin_id, self.version or '0'))
        request_headers.replace('Accept', 'application/json')
        for key, value in (headers or {}).items():
            request_headers.replace(key, value)
        if json is not None:
            message.set_request_body_from_bytes(
                'application/json',
                GLib.Bytes.new(_json_dumps(json).encode('utf-8')))
        elif form is not None:
            message.set_request_body_from_bytes(
                'application/x-www-form-urlencoded',
                GLib.Bytes.new(urlencode(form).encode('utf-8')))

        def done(session, result, _data):
            response = _response(session, message, result)
            if self.active and scope.active:
                self.guard(callback, response)
        self._host.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, scope.cancellable, done, None)

    def store_secret(self, name, value, callback=None):
        from . import keyringstore

        def done(error):
            if callback is not None and self.active:
                self.guard(callback, error.message if error else None)
        keyringstore.store_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                    value, done)

    def lookup_secret(self, name, callback):
        from . import keyringstore

        def done(value, error=None):
            if self.active:
                self.guard(callback, value,
                           error.message if error else None)
        keyringstore.load_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                   done)

    def clear_secret(self, name, callback=None):
        from . import keyringstore

        def done(error):
            if callback is not None and self.active:
                self.guard(callback, error.message if error else None)
        keyringstore.clear_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                    done)

    def toast(self, widget, text):
        from . import feedback
        feedback.show_toast(widget, text)

    def open_link(self, widget, url):
        from . import linkrouting
        linkrouting.open_link(widget, url, resolve=True)

    def compose(self, widget, text):
        if not self.active:
            return
        from . import pluginmenu
        pluginmenu.compose(widget, text)

    def open_panel(self, widget, url, title=None):
        from . import pluginpanel
        panel = pluginpanel.open_panel(self, widget, url, title)
        self._panel = panel
        return panel

    def log(self, message):
        _log(GLib.LogLevelFlags.LEVEL_MESSAGE,
             '%s: %s' % (self.plugin_id, message), self.plugin_id)


_json_dumps = json.dumps


def _response(session, message, result):
    try:
        body = session.send_and_read_finish(result)
    except GLib.Error as error:
        return api.Response(status=0, error=error.message)
    status = message.get_status()
    text = (body.get_data() or b'').decode('utf-8', 'replace')
    content_type = message.get_response_headers().get_content_type()[0] \
        or ''
    data = None
    error = None
    if text and ('json' in content_type or text.lstrip()[:1] in '{['):
        try:
            data = json.loads(text)
        except ValueError:
            if 'json' in content_type:
                error = _('The server sent a reply that could not be read')
    if status >= 400:
        error = '%d %s' % (status, message.get_reason_phrase() or '')
    return api.Response(status=status, data=data, text=text, error=error)
