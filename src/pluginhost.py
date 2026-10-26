# SPDX-License-Identifier: GPL-3.0-or-later

"""Babble's side of plugins: finding them, turning them on and off, and
the two places they extend (a post's menu, a hashtag page).

libpeas 2 discovers plugins, reads their ``.plugin`` files and imports
their modules; the extensions are ours. libpeas can only create
extensions of a GInterface or an abstract GType, and PyGObject can
define neither, so after libpeas has imported a plugin the host looks
in its module for subclasses of the classes in ``plugin.py`` itself.

Nothing here runs, and libpeas is not even imported, until a plugin is
enabled: with ``active-plugins`` empty the app pays one GSettings read.

Two things about the loader shape the rest:

- Python cannot unload a module. Disabling a plugin drops its
  extensions and everything it holds through its Context; its module
  stays imported, and enabling it again reuses it.
- libpeas remembers a failed import for the life of the process, so a
  plugin that would not load stays that way until Babble restarts.
"""

import json
import os
import sys
import traceback
from urllib.parse import urlencode

from gi.repository import GLib, GObject, Gio  # noqa: E402

from . import plugin as api  # noqa: E402
from .i18n import gettext as _  # noqa: E402

ACTIVE_KEY = 'active-plugins'
API_KEY = 'Babble-Api'  # X-Babble-Api= in the .plugin file
SECRET_PREFIX = 'babble-plugin:'
HTTP_TIMEOUT_SECONDS = 30

_host = None


def _log(level, message, plugin_id=None):
    fields = {'MESSAGE': GLib.Variant('s', message)}
    if plugin_id:
        fields['BABBLE_PLUGIN'] = GLib.Variant('s', plugin_id)
    GLib.log_variant('Babble', level, GLib.Variant('a{sv}', fields))


def search_paths():
    """Where plugins are looked for, first match wins: each directory
    of ``BABBLE_PLUGIN_PATH``, the user's plugins, then the ones shipped
    with Babble (``plugins/`` beside the package, which is the source
    tree's top level uninstalled and ``pkgdatadir`` installed)."""
    paths = [p for p in os.environ.get('BABBLE_PLUGIN_PATH', '').split(
        os.pathsep) if p]
    paths.append(os.path.join(GLib.get_user_data_dir(), 'babble',
                              'plugins'))
    paths.append(os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'plugins')))
    return paths


def post_of(status, instance=''):
    """A :class:`plugin.Post` for *status*, a status as the API sent it
    (the boosted one for a boost)."""
    from .statustext import parse_content
    account = status.get('account') or {}
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
        author=account.get('acct') or '',
        author_name=account.get('display_name') or '',
        text=''.join(text).strip(),
        tags=tuple((t.get('name') or '').lower()
                   for t in status.get('tags') or ()),
        language=status.get('language') or '',
        created_at=status.get('created_at') or '',
        visibility=status.get('visibility') or '',
        sensitive=bool(status.get('sensitive')),
        spoiler_text=status.get('spoiler_text') or '', media=media)


class _Settings(api.Settings):
    """A JSON file per plugin, under the user's config directory."""

    def __init__(self, plugin_id):
        self.path = os.path.join(GLib.get_user_config_dir(), 'babble',
                                 'plugins', plugin_id + '.json')
        self._values = None

    def _load(self):
        if self._values is None:
            try:
                with open(self.path, encoding='utf-8') as source:
                    values = json.load(source)
                self._values = values if isinstance(values, dict) else {}
            except (OSError, ValueError):
                self._values = {}
        return self._values

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        GLib.file_set_contents(self.path, json.dumps(
            self._values, ensure_ascii=False, indent=1).encode('utf-8'))

    def get(self, key, default=None):
        return self._load().get(key, default)

    def set(self, key, value):
        json.dumps(value)  # refuse what could not be saved, here
        self._load()[key] = value
        self._save()

    def remove(self, key):
        if self._load().pop(key, None) is not None:
            self._save()


class _Scope:
    """What one owner made through the Context: the plugin itself, or
    one of its extensions. Closing it disconnects its handlers and
    cancels its requests, whose callbacks then never run."""

    def __init__(self):
        self.active = True
        self.handlers = []  # (GObject weak ref to the emitter, id)
        self.cancellable = Gio.Cancellable()

    def close(self):
        self.active = False
        self.cancellable.cancel()
        for ref, handler_id in self.handlers:
            emitter = ref()
            if emitter is not None and GObject.signal_handler_is_connected(
                    emitter, handler_id):
                emitter.disconnect(handler_id)
        self.handlers = []


class _Context(api.Context):

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
    def data(self):
        return self._data

    def connect(self, emitter, signal, callback, *extra, owner=None):
        scope = self.scope_of(owner)
        target = getattr(callback, '__self__', None)
        if isinstance(target, GObject.Object):
            # A widget's own method, held weakly as pages.connect_weak
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
        from . import mastodon

        def done(error):
            if callback is not None and self.active:
                self.guard(callback, error.message if error else None)
        mastodon.store_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                    value, done)

    def lookup_secret(self, name, callback):
        from . import mastodon

        def done(value, error=None):
            if self.active:
                self.guard(callback, value,
                           error.message if error else None)
        mastodon.load_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                   done)

    def clear_secret(self, name, callback=None):
        from . import mastodon

        def done(error):
            if callback is not None and self.active:
                self.guard(callback, error.message if error else None)
        mastodon.clear_secret_async(SECRET_PREFIX + self.plugin_id, name,
                                    done)

    def toast(self, widget, text):
        from . import pages
        pages.show_toast(widget, text)

    def open_link(self, widget, url):
        from . import pages
        pages.open_link(widget, url, resolve=True)

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


class _PageSlot:
    """A place on screen extensions fill: a hashtag page's banner box
    (*hashtag* set), or one plugin's settings page (*plugin_id* set). It
    holds the page and its box weakly: the page owns both, and its going
    is what deactivates the extensions."""

    def __init__(self, host, page, box, hashtag=None, plugin_id=None):
        self.hashtag = hashtag
        self.plugin_id = plugin_id
        self.box_ref = box.weak_ref()
        self.extensions = {}  # plugin id -> [(extension, banner or None)]
        self._page_ref = page.weak_ref(host._page_gone, self)


class PluginHost:

    def __init__(self, settings):
        self.settings = settings
        self.engine = None
        self.contexts = {}  # plugin id -> _Context
        self.menu_extensions = {}  # plugin id -> [PostMenuExtension]
        self.page_classes = {}  # plugin id -> [HashtagPageExtension type]
        self.preference_classes = {}  # plugin id -> [PreferencesExtension]
        self.slots = []
        self.errors = {}  # plugin id -> why it is not running
        self._session = None
        self._user_agent = None

    # Loading

    def start(self):
        """Create the engine and load the enabled plugins. False when
        libpeas 2 or its Python loader is not installed."""
        if self.engine is not None:
            return True
        try:
            import gi
            gi.require_version('Peas', '2')
            from gi.repository import Peas
        except (ImportError, ValueError) as error:
            _log(GLib.LogLevelFlags.LEVEL_WARNING,
                 'Plugins are unavailable: libpeas 2 is not installed (%s)'
                 % error)
            return False
        # A plugin imports `babble.plugin`; make that name reach this
        # very module however Babble itself was imported (the tests
        # import it as `src`), or the plugin's base classes would be
        # different classes from the ones looked for below.
        package = sys.modules[api.__package__]
        sys.modules.setdefault('babble', package)
        sys.modules.setdefault('babble.plugin', api)
        self.engine = Peas.Engine.new()
        self.engine.enable_loader('python')
        for path in search_paths():
            self.engine.add_search_path(path, None)
        self.settings.connect('changed::' + ACTIVE_KEY,
                              lambda *_args: self.sync())
        self.sync()
        return True

    def plugins(self):
        """Every plugin found, as libpeas PluginInfo, hidden ones left
        out."""
        if self.engine is None:
            return []
        return [self.engine.get_item(i)
                for i in range(self.engine.get_n_items())
                if not self.engine.get_item(i).is_hidden()]

    def is_enabled(self, plugin_id):
        return plugin_id in self.settings.get_strv(ACTIVE_KEY)

    def set_enabled(self, plugin_id, enabled):
        active = [p for p in self.settings.get_strv(ACTIVE_KEY)
                  if p != plugin_id]
        if enabled:
            # Asked for by name: try it again even if it failed before.
            self.errors.pop(plugin_id, None)
            active.append(plugin_id)
        self.settings.set_strv(ACTIVE_KEY, active)  # sync() follows

    def sync(self):
        """Load what ``active-plugins`` names and unload the rest. A
        plugin that was refused or failed is not retried until it is
        enabled again by name."""
        if self.engine is None:
            return
        wanted = list(dict.fromkeys(self.settings.get_strv(ACTIVE_KEY)))
        for plugin_id in list(self.contexts):
            if plugin_id not in wanted:
                self._unload(plugin_id)
        for plugin_id in wanted:
            if plugin_id not in self.contexts \
                    and plugin_id not in self.errors:
                self._load(plugin_id)

    def _load(self, plugin_id):
        info = self.engine.get_plugin_info(plugin_id)
        if info is None:
            self.errors[plugin_id] = _('Not installed')
            return
        wanted = info.get_external_data(API_KEY)
        if wanted != str(api.API_VERSION):
            self.errors[plugin_id] = _(
                'Made for another version of Babble')
            _log(GLib.LogLevelFlags.LEVEL_WARNING,
                 '%s: X-Babble-Api is %r, this Babble provides %d'
                 % (plugin_id, wanted, api.API_VERSION), plugin_id)
            return
        if not self.engine.load_plugin(info):
            # libpeas logged the traceback; it keeps the failure until
            # Babble restarts.
            self.errors[plugin_id] = _(
                'Could not be loaded; the system log has the details')
            return
        module = sys.modules.get(info.get_module_name())
        menu_classes, page_classes, preference_classes = \
            _extension_classes(module)
        if not menu_classes and not page_classes:
            self.engine.unload_plugin(info)
            self.errors[plugin_id] = _('Adds nothing Babble can use')
            return
        self.errors.pop(plugin_id, None)
        context = _Context(self, info)
        self.contexts[plugin_id] = context
        self.page_classes[plugin_id] = page_classes
        self.preference_classes[plugin_id] = preference_classes
        extensions = []
        self.menu_extensions[plugin_id] = extensions
        for cls in menu_classes:
            extension = context.guard(cls, context)
            if extension is None or not context.active:
                return
            extensions.append(extension)
            context.guard(extension.activate)
        for slot in list(self.slots):
            if not context.active:
                return
            self._attach(plugin_id, slot)

    def _unload(self, plugin_id):
        context = self.contexts.pop(plugin_id, None)
        if context is None:
            return
        for extension in self.menu_extensions.pop(plugin_id, []):
            context.quietly(extension.deactivate)
            context.release(extension)
        self.page_classes.pop(plugin_id, None)
        self.preference_classes.pop(plugin_id, None)
        for slot in list(self.slots):
            self._detach(plugin_id, slot, context)
        context.close()
        info = self.engine.get_plugin_info(plugin_id)
        if info is not None and info.is_loaded():
            self.engine.unload_plugin(info)

    def fail(self, plugin_id, details, widget=None):
        """A plugin raised: log it, turn it off, tell the user."""
        context = self.contexts.get(plugin_id)
        if context is None or not context.active:
            return
        context.active = False  # nothing more of it runs from here on
        _log(GLib.LogLevelFlags.LEVEL_WARNING,
             'Plugin %s failed and was turned off:\n%s'
             % (plugin_id, details), plugin_id)
        self.errors[plugin_id] = _(
            'Stopped working and was turned off; the system log has the '
            'details')
        name = context.name
        GLib.idle_add(self._turn_off, plugin_id)
        if widget is None:
            app = Gio.Application.get_default()
            window = app.get_active_window() if app is not None and \
                hasattr(app, 'get_active_window') else None
            widget = window
        if widget is not None:
            from . import pages
            # Translators: %s is a plugin's name.
            pages.show_toast(widget, _('“%s” stopped working and was '
                                       'turned off') % name)

    def _turn_off(self, plugin_id):
        message = self.errors.get(plugin_id)
        if self.is_enabled(plugin_id):
            self.set_enabled(plugin_id, False)
        else:
            self._unload(plugin_id)
        if message is not None:
            self.errors[plugin_id] = message  # sync() does not clear it
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        """Deactivate every extension, before the windows go."""
        for plugin_id in list(self.contexts):
            self._unload(plugin_id)

    # HTTP

    def session(self):
        if self._session is None:
            from gi.repository import Soup
            self._session = Soup.Session()
            self._session.set_timeout(HTTP_TIMEOUT_SECONDS)
        return self._session

    def user_agent(self):
        if self._user_agent is None:
            from .mastodon import USER_AGENT
            self._user_agent = USER_AGENT % getattr(
                sys.modules.get(api.__package__), 'VERSION', '0.1')
        return self._user_agent

    # Post menu

    def menu_items(self, widget, status, instance):
        """``[(label, activate)]`` from every enabled plugin for the post
        a menu opens on. *activate* holds *widget* weakly."""
        post = None
        entries = []
        for plugin_id, extensions in list(self.menu_extensions.items()):
            context = self.contexts.get(plugin_id)
            for extension in extensions:
                if context is None or not context.active:
                    break
                if post is None:
                    post = post_of(status, instance)
                items = context.guard(extension.menu_items, post,
                                      widget=widget, default=None) or []
                for item in items:
                    if isinstance(item, api.MenuItem):
                        entries.append((item.label, _menu_activate(
                            context, item, post, widget)))
        return entries

    # Hashtag pages

    def attach_hashtag_page(self, page, hashtag, box):
        slot = _PageSlot(self, page, box, hashtag=hashtag)
        self.slots.append(slot)
        for plugin_id in list(self.page_classes):
            self._attach(plugin_id, slot)

    # A plugin's settings

    def has_preferences(self, plugin_id):
        return bool(self.preference_classes.get(plugin_id))

    def attach_preferences(self, plugin_id, page, box):
        """Fill *box*, on *page*, with *plugin_id*'s settings; they go
        when the page does or the plugin is disabled."""
        slot = _PageSlot(self, page, box, plugin_id=plugin_id)
        self.slots.append(slot)
        self._attach(plugin_id, slot)

    def _attach(self, plugin_id, slot):
        context = self.contexts.get(plugin_id)
        box = slot.box_ref()
        if context is None or box is None:
            return
        if slot.hashtag is not None:
            classes = self.page_classes.get(plugin_id, ())
        elif slot.plugin_id == plugin_id:
            classes = self.preference_classes.get(plugin_id, ())
        else:
            return
        attached = slot.extensions.setdefault(plugin_id, [])
        for cls in classes:
            if slot.hashtag is not None:
                extension = context.guard(cls, context, slot.hashtag,
                                          widget=box)
            else:
                extension = context.guard(cls, context, widget=box)
            if extension is None:
                return
            context.guard(extension.activate, widget=box)
            create = extension.create_banner if slot.hashtag is not None \
                else extension.create_preferences
            banner = context.guard(create, widget=box)
            if not context.active:
                return
            from gi.repository import Gtk
            if banner is not None and not isinstance(banner, Gtk.Widget):
                banner = None
            handler = None
            if banner is not None:
                box.append(banner)
                # A banner may stay hidden until it knows it has
                # something to say; the box shows only while one shows.
                handler = banner.connect('notify::visible', _sync_box,
                                         slot.box_ref)
                _sync_box(banner, None, slot.box_ref)
            attached.append((extension, banner, handler))

    def _detach(self, plugin_id, slot, context, page_gone=False):
        box = None if page_gone else slot.box_ref()
        for extension, banner, handler in slot.extensions.pop(plugin_id, []):
            context.quietly(extension.deactivate)
            context.release(extension)
            if banner is None:
                continue
            if GObject.signal_handler_is_connected(banner, handler):
                banner.disconnect(handler)
            if box is not None and banner.get_parent() is box:
                box.remove(banner)
        if box is not None:
            _sync_box(None, None, slot.box_ref)

    def _page_gone(self, slot):
        if slot in self.slots:
            self.slots.remove(slot)
        for plugin_id in list(slot.extensions):
            context = self.contexts.get(plugin_id)
            if context is not None:
                self._detach(plugin_id, slot, context, page_gone=True)
            else:
                slot.extensions.pop(plugin_id, None)


def _sync_box(_banner, _pspec, box_ref):
    """Show a banner box while any banner in it is visible."""
    box = box_ref()
    if box is None:
        return
    child = box.get_first_child()
    while child is not None and not child.get_visible():
        child = child.get_next_sibling()
    box.set_visible(child is not None)


def _menu_activate(context, item, post, widget):
    widget_ref = widget.weak_ref()

    def activate(*_args):
        anchor = widget_ref()
        if anchor is not None:
            context.guard(item.activate, post, anchor, widget=anchor)
    return activate


def _extension_classes(module):
    """The extension classes *module* (a plugin) defines: post menu,
    hashtag page and preferences ones."""
    menu, page, preferences = [], [], []
    if module is None:
        return menu, page, preferences
    name = module.__name__
    for value in list(vars(module).values()):
        if not isinstance(value, type) or not (
                value.__module__ == name
                or value.__module__.startswith(name + '.')):
            continue
        if issubclass(value, api.PostMenuExtension) \
                and value is not api.PostMenuExtension:
            menu.append(value)
        elif issubclass(value, api.HashtagPageExtension) \
                and value is not api.HashtagPageExtension:
            page.append(value)
        elif issubclass(value, api.PreferencesExtension) \
                and value is not api.PreferencesExtension:
            preferences.append(value)
    return menu, page, preferences


# Entry points for the rest of the app. Each is a no-op while no plugin
# was ever enabled.

def host():
    """The one PluginHost, created on first use (but not started)."""
    global _host
    if _host is None:
        from . import pages
        _host = PluginHost(pages.settings())
    return _host


def start_if_enabled():
    """At startup: start the host only when a plugin is enabled."""
    from . import pages
    if pages.settings().get_strv(ACTIVE_KEY):
        host().start()


def menu_items(widget, status, instance):
    if _host is None or not _host.menu_extensions:
        return []
    return _host.menu_items(widget, status, instance)


def attach_hashtag_page(page, hashtag, box):
    if _host is not None and _host.engine is not None:
        _host.attach_hashtag_page(page, hashtag, box)


def shutdown():
    if _host is not None:
        _host.shutdown()
