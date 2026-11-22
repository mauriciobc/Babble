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

import os
import sys
import traceback
import weakref

from gi.repository import GLib, GObject, Gio  # noqa: E402

from . import plugin as api  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .plugincontext import Context as _Context  # noqa: E402
from .plugincontext import post_of  # noqa: E402

ACTIVE_KEY = 'active-plugins'
API_KEY = 'Babble-Api'  # X-Babble-Api= in the .plugin file
REVISION_KEY = 'Babble-Api-Revision'  # X-Babble-Api-Revision=, default 1
HTTP_TIMEOUT_SECONDS = 30

_host = None
_menu_listeners = []  # weak methods, told when the main menu's entries change


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


class _PageSlot:
    """A place on screen extensions fill: a hashtag page's banner box
    (*hashtag* set) or one plugin's settings page (*plugin_id* set). It
    holds the page and its box weakly: the page owns both, and its going
    is what deactivates the extensions. A subclass says which classes
    fill it (None: not this plugin's) and how they are built."""

    def __init__(self, host, page, box, hashtag=None, plugin_id=None):
        self.hashtag = hashtag
        self.plugin_id = plugin_id
        self.box_ref = box.weak_ref()
        # plugin id -> [(extension, banner or None, handler or None)]
        self.extensions = {}
        self._page_ref = page.weak_ref(host._page_gone, self)


class _HashtagSlot(_PageSlot):
    def classes(self, host, plugin_id):
        return host.page_classes.get(plugin_id, ())

    def construct(self, context, cls, box):
        return context.guard(cls, context, self.hashtag, widget=box)

    def create(self, context, extension, box):
        return context.guard(extension.create_banner, widget=box)


class _PreferencesSlot(_PageSlot):
    def classes(self, host, plugin_id):
        if self.plugin_id != plugin_id:
            return None
        return host.preference_classes.get(plugin_id, ())

    def construct(self, context, cls, box):
        return context.guard(cls, context, widget=box)

    def create(self, context, extension, box):
        return context.guard(extension.create_preferences, widget=box)


class PluginHost:

    def __init__(self, settings):
        self.settings = settings
        self.engine = None
        self.contexts = {}  # plugin id -> _Context
        self.menu_extensions = {}  # plugin id -> [PostMenuExtension]
        self.page_classes = {}  # plugin id -> [HashtagPageExtension type]
        self.preference_classes = {}  # plugin id -> [PreferencesExtension]
        self.link_extensions = {}  # plugin id -> [LinkExtension]
        self.main_extensions = {}  # plugin id -> [MainMenuExtension]
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
        _menu_changed()

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
        wanted = info.get_external_data(REVISION_KEY)
        try:
            revision = 1 if wanted is None else int(wanted)
        except ValueError:
            revision = None
        if revision is None or revision > api.API_REVISION:
            self.errors[plugin_id] = _('Needs a newer version of Babble')
            _log(GLib.LogLevelFlags.LEVEL_WARNING,
                 '%s: X-Babble-Api-Revision is %r, this Babble provides %d'
                 % (plugin_id, wanted, api.API_REVISION), plugin_id)
            return
        if not self.engine.load_plugin(info):
            # libpeas logged the traceback; it keeps the failure until
            # Babble restarts.
            self.errors[plugin_id] = _(
                'Could not be loaded; the system log has the details')
            return
        module = sys.modules.get(info.get_module_name())
        menu_classes, page_classes, preference_classes, link_classes, \
            main_classes = _extension_classes(module)
        if not (menu_classes or page_classes or preference_classes
                or link_classes or main_classes):
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
        links = []
        self.link_extensions[plugin_id] = links
        for cls in link_classes:
            extension = context.guard(cls, context)
            if extension is None or not context.active:
                return
            links.append(extension)
            context.guard(extension.activate)
        mains = []
        self.main_extensions[plugin_id] = mains
        for cls in main_classes:
            extension = context.guard(cls, context)
            if extension is None or not context.active:
                return
            mains.append(extension)
            context.guard(extension.activate)
        for slot in list(self.slots):
            if not context.active:
                return
            self._attach(plugin_id, slot)

    def _unload(self, plugin_id):
        context = self.contexts.pop(plugin_id, None)
        if context is None:
            return
        for extension in self.menu_extensions.pop(plugin_id, []) \
                + self.link_extensions.pop(plugin_id, []) \
                + self.main_extensions.pop(plugin_id, []):
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
            from . import feedback
            # Translators: %s is a plugin's name.
            feedback.show_toast(widget, _('“%s” stopped working and was '
                                          'turned off') % name)

    def _turn_off(self, plugin_id):
        message = self.errors.get(plugin_id)
        if self.is_enabled(plugin_id):
            self.set_enabled(plugin_id, False)
        else:
            self._unload(plugin_id)
            _menu_changed()
        if message is not None:
            self.errors[plugin_id] = message  # sync() does not clear it
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        """Deactivate every extension, before the windows go."""
        for plugin_id in list(self.contexts):
            self._unload(plugin_id)
        _menu_changed()

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
        slot = _HashtagSlot(self, page, box, hashtag=hashtag)
        self.slots.append(slot)
        for plugin_id in list(self.page_classes):
            self._attach(plugin_id, slot)

    # A plugin's settings

    def has_preferences(self, plugin_id):
        return bool(self.preference_classes.get(plugin_id))

    def attach_preferences(self, plugin_id, page, box):
        """Fill *box*, on *page*, with *plugin_id*'s settings; they go
        when the page does or the plugin is disabled."""
        slot = _PreferencesSlot(self, page, box, plugin_id=plugin_id)
        self.slots.append(slot)
        self._attach(plugin_id, slot)

    def _attach(self, plugin_id, slot):
        context = self.contexts.get(plugin_id)
        box = slot.box_ref()
        if context is None or box is None:
            return
        classes = slot.classes(self, plugin_id)
        if classes is None:
            return
        attached = slot.extensions.setdefault(plugin_id, [])
        for cls in classes:
            extension = slot.construct(context, cls, box)
            if extension is None:
                return
            context.guard(extension.activate, widget=box)
            banner = slot.create(context, extension, box)
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
    hashtag page, preferences, link and main menu ones."""
    menu, page, preferences, link, main = [], [], [], [], []
    if module is None:
        return menu, page, preferences, link, main
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
        elif issubclass(value, api.LinkExtension) \
                and value is not api.LinkExtension:
            link.append(value)
        elif issubclass(value, api.MainMenuExtension) \
                and value is not api.MainMenuExtension:
            main.append(value)
    return menu, page, preferences, link, main


# Entry points for the rest of the app. Each is a no-op while no plugin
# was ever enabled.

def host():
    """The one PluginHost, created on first use (but not started)."""
    global _host
    if _host is None:
        from . import appsettings
        _host = PluginHost(appsettings.settings())
    return _host


def start_if_enabled():
    """At startup: start the host only when a plugin is enabled."""
    from . import appsettings
    if appsettings.settings().get_strv(ACTIVE_KEY):
        host().start()


def menu_items(widget, status, instance):
    if _host is None or not _host.menu_extensions:
        return []
    return _host.menu_items(widget, status, instance)


def on_menu_changed(callback):
    """Call *callback*, a bound method held weakly, when plugins were
    enabled, disabled, loaded or unloaded. Creates no host and imports no
    libpeas."""
    _menu_listeners.append(weakref.WeakMethod(callback))


def _menu_changed():
    _menu_listeners[:] = [r for r in _menu_listeners if r() is not None]
    for ref in list(_menu_listeners):
        callback = ref()
        if callback is None:
            continue
        try:
            callback()
        except Exception:
            _log(GLib.LogLevelFlags.LEVEL_WARNING,
                 'main menu: %s' % traceback.format_exc())


def attach_hashtag_page(page, hashtag, box):
    if _host is not None and _host.engine is not None:
        _host.attach_hashtag_page(page, hashtag, box)


def shutdown():
    if _host is not None:
        _host.shutdown()
