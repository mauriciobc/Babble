# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom emoji of the signed-in instance: a cached ``/custom_emojis``
fetch, a "recently used" list kept in GSettings and a searchable popover
chooser grouped by category (the Tuba ``CustomEmojiChooserV2`` layout).

The popover emits ``emoji-picked`` with the bare shortcode; the composer
inserts ``:shortcode: `` itself."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

SCHEMA_ID = 'io.github.mauriciobc.Babble'
RECENT_LIMIT = 24
EMOJI_SIZE = 32
COLUMNS = 6

_cache = {}
_waiting = {}
_textures = {}
_texture_waiting = {}


def _settings():
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA_ID, True) is None:
        return None
    from .pages import settings
    return settings()


# Instance emoji list

def cached_custom_emojis(client):
    """The picker-visible custom emoji of the client's instance, or None
    while they have not been fetched yet."""
    return _cache.get(client.instance)


def load_custom_emojis(client, callback):
    """Fetch ``GET /api/v1/custom_emojis`` once per instance and call
    ``callback(emojis)`` (a list; empty on failure) on the main loop.
    Emoji flagged ``visible_in_picker: false`` are dropped."""
    key = client.instance
    if key in _cache:
        GLib.idle_add(lambda: (callback(_cache[key]), False)[1])
        return
    if key in _waiting:
        _waiting[key].append(callback)
        return
    _waiting[key] = [callback]

    def finished(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error:
            data = None
        waiting = _waiting.pop(key, [])
        if isinstance(data, list):
            emojis = [e for e in data if e.get('visible_in_picker', True)
                      and e.get('shortcode')]
            emojis.sort(key=lambda e: e['shortcode'].lower())
            _cache[key] = emojis
        else:
            emojis = []
        for waiter in waiting:
            waiter(emojis)

    client.request_async('GET', '/custom_emojis', callback=finished)


def recent_shortcodes():
    settings = _settings()
    if settings is None:
        return []
    return list(settings.get_strv('recently-used-custom-emojis'))


def remember_recent(shortcode):
    """Move *shortcode* to the front of the recently used list."""
    settings = _settings()
    if settings is None:
        return
    recent = [c for c in settings.get_strv('recently-used-custom-emojis')
              if c != shortcode]
    settings.set_strv('recently-used-custom-emojis',
                      ([shortcode] + recent)[:RECENT_LIMIT])


# Images

def load_texture(url, callback):
    """Decode *url* once; ``callback(texture)`` runs on success."""
    if url in _textures:
        callback(_textures[url])
        return
    if url in _texture_waiting:
        _texture_waiting[url].append(callback)
        return
    from .timeline import AvatarCache
    from gi.repository import Soup
    message = Soup.Message.new('GET', url)
    if message is None:
        return
    _texture_waiting[url] = [callback]

    def loaded(session, result, _data=None):
        waiting = _texture_waiting.pop(url, [])
        try:
            data = session.send_and_read_finish(result)
            texture = Gdk.Texture.new_from_bytes(data)
        except GLib.Error:
            return
        _textures[url] = texture
        for waiter in waiting:
            waiter(texture)

    AvatarCache.session().send_and_read_async(
        message, GLib.PRIORITY_DEFAULT_IDLE, None, loaded, None)


def emoji_url(emoji):
    return emoji.get('static_url') or emoji.get('url') or ''


def bind_emoji_image(image, emoji):
    """Show *emoji* in *image*; safe for recycled list items."""
    url = emoji_url(emoji)
    image._emoji_url = url
    image.set_from_paintable(None)
    if not url:
        return

    def apply(texture):
        if getattr(image, '_emoji_url', None) == url:
            image.set_from_paintable(texture)

    load_texture(url, apply)


def search_emojis(emojis, query, limit=None):
    """Shortcodes containing *query* (case-insensitive), prefix matches
    first."""
    needle = query.lower().lstrip(':')
    if not needle:
        return []
    starts, contains = [], []
    for emoji in emojis:
        code = emoji['shortcode'].lower()
        if code.startswith(needle):
            starts.append(emoji)
        elif needle in code:
            contains.append(emoji)
    result = starts + contains
    return result[:limit] if limit else result


# Chooser popover

class EmojiItem(GObject.Object):
    __gtype_name__ = 'BabbleEmojiItem'

    def __init__(self, emoji):
        super().__init__()
        self.emoji = emoji


class CategoryItem(GObject.Object):
    __gtype_name__ = 'BabbleEmojiCategory'

    def __init__(self, name, emojis, recent=False):
        super().__init__()
        self.name = name
        self.emojis = emojis
        self.recent = recent


class CustomEmojiChooser(Gtk.Popover):
    """Searchable custom emoji popover: a list of categories (recently
    used first) that opens a grid; typing in the search field searches
    every category at once."""

    __gtype_name__ = 'BabbleCustomEmojiChooser'
    __gsignals__ = {'emoji-picked': (GObject.SignalFlags.RUN_FIRST, None,
                                     (str,))}

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.emojis = []
        self.categories = []
        self._loaded = False
        self.add_css_class('emoji-picker')

        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_('Search Emoji'), margin_top=6,
            margin_bottom=6, margin_start=6, margin_end=6)
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL],
                                          [_('Search Custom Emoji')])
        self.search_entry.connect('search-changed', self.on_search_changed)

        self.category_store = Gio.ListStore.new(CategoryItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup_category)
        factory.connect('bind', self._bind_category)
        self.category_list = Gtk.ListView(
            model=Gtk.NoSelection(model=self.category_store),
            factory=factory, single_click_activate=True)
        self.category_list.add_css_class('navigation-sidebar')
        self.category_list.connect('activate', self.on_category_activated)

        self.grid_store = Gio.ListStore.new(EmojiItem)
        grid_factory = Gtk.SignalListItemFactory()
        grid_factory.connect('setup', self._setup_emoji)
        grid_factory.connect('bind', self._bind_emoji)
        self.grid = Gtk.GridView(
            model=Gtk.NoSelection(model=self.grid_store),
            factory=grid_factory, single_click_activate=True,
            min_columns=COLUMNS, max_columns=COLUMNS,
            margin_start=6, margin_end=6, margin_bottom=6)
        self.grid.connect('activate', self.on_emoji_activated)

        self.back_button = Gtk.Button(icon_name='go-previous-symbolic',
                                      valign=Gtk.Align.CENTER)
        self.back_button.add_css_class('flat')
        self.back_button.set_tooltip_text(_('Back to Categories'))
        self.back_button.update_property([Gtk.AccessibleProperty.LABEL],
                                         [_('Back to Categories')])
        self.back_button.connect('clicked', self.on_back)
        self.grid_title = Gtk.Label(xalign=0, hexpand=True)
        self.grid_title.add_css_class('heading')
        grid_header = Gtk.Box(spacing=6, margin_start=6, margin_end=6)
        grid_header.append(self.back_button)
        grid_header.append(self.grid_title)
        self.grid_header = grid_header
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid_box.append(grid_header)
        grid_box.append(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
            child=self.grid))

        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER,
                                         width_request=32,
                                         height_request=32), 'loading')
        self.stack.add_named(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
            child=self.category_list), 'categories')
        self.stack.add_named(grid_box, 'grid')
        self.empty_page = Adw.StatusPage(
            icon_name='face-smile-symbolic',
            title=_('No Custom Emoji'),
            description=_('This server has no custom emoji'))
        self.empty_page.add_css_class('compact')
        self.stack.add_named(self.empty_page, 'empty')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      width_request=320, height_request=340)
        box.append(self.search_entry)
        box.append(self.stack)
        self.set_child(box)
        self.connect('map', self._on_map)
        self.connect('closed', self._on_closed)
        self.stack.set_visible_child_name('loading')

    # Loading

    def _on_map(self, _popover):
        if self._loaded:
            self.rebuild_categories()
            return
        cached = cached_custom_emojis(self.client)
        if cached is not None:
            self._on_emojis_loaded(cached)
        else:
            load_custom_emojis(self.client, self._on_emojis_loaded)
        self.search_entry.grab_focus()

    def _on_closed(self, _popover):
        self.search_entry.set_text('')

    def _on_emojis_loaded(self, emojis):
        self._loaded = True
        self.emojis = emojis
        self.rebuild_categories()

    def rebuild_categories(self):
        by_name = {}
        for emoji in self.emojis:
            by_name.setdefault(emoji.get('category') or '', []).append(emoji)
        shortcodes = {e['shortcode']: e for e in self.emojis}
        recent = [shortcodes[c] for c in recent_shortcodes()
                  if c in shortcodes]
        items = []
        if recent:
            items.append(CategoryItem(_('Recently Used'), recent, True))
        for name in sorted(by_name, key=lambda n: (n == '', n.lower())):
            items.append(CategoryItem(name or _('Custom'), by_name[name]))
        self.categories = items
        self.category_store.splice(0, self.category_store.get_n_items(),
                                   items)
        if not self.emojis:
            self.stack.set_visible_child_name('empty')
        elif self.search_entry.get_text():
            self.on_search_changed(self.search_entry)
        elif self.stack.get_visible_child_name() != 'grid':
            self.stack.set_visible_child_name('categories')

    # Category list

    def _setup_category(self, _factory, item):
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        image = Gtk.Image(pixel_size=EMOJI_SIZE)
        box.append(image)
        label = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        box.append(label)
        count = Gtk.Label()
        count.add_css_class('dimmed')
        count.add_css_class('numeric')
        box.append(count)
        item.set_child(box)

    def _bind_category(self, _factory, item):
        category = item.get_item()
        box = item.get_child()
        image, label, count = (box.get_first_child(),
                               box.get_first_child().get_next_sibling(),
                               box.get_last_child())
        bind_emoji_image(image, category.emojis[0])
        label.set_label(category.name)
        count.set_label(str(len(category.emojis)))

    def on_category_activated(self, _view, position):
        category = self.category_store.get_item(position)
        self.show_grid(category.name, category.emojis, search=False)

    def show_grid(self, title, emojis, search):
        self.grid_title.set_label(title)
        self.grid_header.set_visible(not search)
        self.grid_store.splice(0, self.grid_store.get_n_items(),
                               [EmojiItem(e) for e in emojis])
        self.stack.set_visible_child_name('grid')

    def on_back(self, _button):
        self.stack.set_visible_child_name('categories')

    # Grid

    def _setup_emoji(self, _factory, item):
        image = Gtk.Image(pixel_size=EMOJI_SIZE, margin_top=4,
                          margin_bottom=4, margin_start=4, margin_end=4)
        item.set_child(image)

    def _bind_emoji(self, _factory, item):
        emoji = item.get_item().emoji
        image = item.get_child()
        bind_emoji_image(image, emoji)
        tooltip = ':%s:' % emoji['shortcode']
        image.set_tooltip_text(tooltip)
        image.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])

    def on_emoji_activated(self, _view, position):
        self.pick(self.grid_store.get_item(position).emoji['shortcode'])

    def pick(self, shortcode):
        remember_recent(shortcode)
        self.emit('emoji-picked', shortcode)
        self.popdown()

    # Search

    def on_search_changed(self, entry):
        query = entry.get_text().strip()
        if not self._loaded or not self.emojis:
            return
        if not query:
            self.stack.set_visible_child_name('categories')
            return
        matches = search_emojis(self.emojis, query)
        if not matches:
            self.empty_page.set_title(_('No Results'))
            self.empty_page.set_description(
                _('No custom emoji match “%s”') % query)
            self.stack.set_visible_child_name('empty')
            return
        self.show_grid(_('Results'), matches, search=True)
