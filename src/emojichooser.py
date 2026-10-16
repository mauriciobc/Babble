# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom emoji of the signed-in instance: a cached ``/custom_emojis``
fetch published to :mod:`textemoji` as shortcode -> image URL, so a
``:shortcode:`` resolves to an image wherever it is rendered, not just
where a payload happened to declare it, plus the searchable popover the
composer uses to pick one (``CustomEmojiChooser``) and the "recently
used" list kept in GSettings.

The popover emits ``emoji-picked`` with the bare shortcode; the composer
inserts ``:shortcode:`` itself."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import textemoji  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .pages import connect_weak, weak_method  # noqa: E402

SCHEMA_ID = 'io.github.mauriciobc.Babble'
RECENT_LIMIT = 24
EMOJI_SIZE = 32
COLUMNS = 6

_cache = {}
_waiting = {}

# Per-instance shortcode -> image URL for EVERY emoji the instance
# publishes (including the ones hidden from the picker); the renderer
# reads whichever one the session has activated.
_instance_banks = {}


def cached_custom_emojis(client):
    """The picker-visible custom emoji of the client's instance, or None
    while they have not been fetched yet."""
    return _cache.get(client.instance)


def activate_instance_emojis(client):
    """Make the client's instance the renderer's fallback emoji layer.

    Separate from the fetch because the active instance is the window's
    business: a fetch started for a previous account can still land
    after a switch, and publishing straight from the response would
    hand the new session the old instance's emoji."""
    textemoji.set_instance_emoji_bank(
        _instance_banks.get(client.instance) or {})


def deactivate_instance_emojis():
    """Drop the fallback layer, so nothing renders against a stale
    instance's emoji while no account is signed in."""
    textemoji.set_instance_emoji_bank({})


def load_custom_emojis(client, callback):
    """Fetch ``GET /api/v1/custom_emojis`` once per instance and call
    ``callback(emojis)`` (a list; empty on failure) on the main loop.
    Emoji flagged ``visible_in_picker: false`` are hidden from the
    picker but kept in the instance bank, because other people's posts
    use them."""
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
            _instance_banks[key] = textemoji.emoji_bank_from(data)
            emojis = [e for e in data if e.get('visible_in_picker', True)
                      and e.get('shortcode')]
            emojis.sort(key=lambda e: e['shortcode'].lower())
            _cache[key] = emojis
        else:
            emojis = []
        for waiter in waiting:
            waiter(emojis)

    client.request_async('GET', '/custom_emojis', callback=finished)


def _settings():
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA_ID, True) is None:
        return None
    from .pages import settings
    return settings()


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

class _ImageTarget:
    """Stands in for a recycled ``Gtk.Image`` while ``EmojiTextures``
    loads *url*: the texture is applied only if the image still shows
    that URL (it may have been recycled for another emoji meanwhile)."""

    def __init__(self, image, url):
        self.image = image
        self.url = url

    def set_from_paintable(self, texture):
        if getattr(self.image, '_emoji_url', None) == self.url:
            self.image.set_from_paintable(texture)


def emoji_url(emoji):
    return emoji.get('static_url') or emoji.get('url') or ''


def bind_emoji_image(image, emoji):
    """Show *emoji* in *image* through the shared texture cache; safe
    for recycled list items."""
    url = emoji_url(emoji)
    image._emoji_url = url
    image.set_from_paintable(None)
    if url:
        textemoji.EmojiTextures.bind(_ImageTarget(image, url), url)


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


# Standard (Unicode) emoji, from the data GTK's own chooser uses

# GTK's own category names, so they come with GTK's translations.
_UNICODE_GROUPS = (
    (0, 'Smileys & People'),
    (1, 'Body & Clothing'),
    (3, 'Animals & Nature'),
    (4, 'Food & Drink'),
    (5, 'Travel & Places'),
    (6, 'Activities'),
    (7, 'Objects'),
    (8, 'Symbols'),
    (9, 'Flags'),
)
# Where an emoji takes a skin tone, GTK's data holds a light-tone (or 0)
# placeholder; without it the emoji renders in the default yellow.
_TONE_PLACEHOLDERS = (0, 0x1F3FB)
_unicode_emojis = None


def _unicode_data():
    """GTK's emoji table for the UI language, falling back to the
    English one compiled into GTK."""
    for language in GLib.get_language_names():
        language = language.split('.')[0].split('@')[0].split('_')[0]
        if not language or language in ('C', 'POSIX'):
            continue
        if language == 'en':
            break
        for data_dir in GLib.get_system_data_dirs():
            path = GLib.build_filenamev([data_dir, 'gtk-4.0', 'emoji',
                                         '%s.gresource' % language])
            try:
                return Gio.Resource.load(path).lookup_data(
                    '/org/gtk/libgtk/emoji/%s.data' % language, 0)
            except GLib.Error:
                continue
        break
    return Gio.resources_lookup_data('/org/gtk/libgtk/emoji/en.data', 0)


def unicode_emojis():
    """Standard emoji as ``{'char', 'name', 'terms', 'group'}`` dicts, in
    GTK's order; loaded once."""
    global _unicode_emojis
    if _unicode_emojis is None:
        try:
            table = GLib.Variant.new_from_bytes(
                GLib.VariantType.new('a(aussasasu)'), _unicode_data(),
                True).unpack()
        except GLib.Error:
            table = []
        groups = dict(_UNICODE_GROUPS)
        emojis = []
        for codes, name, local_name, keywords, local_keywords, group \
                in table:
            if group not in groups:
                continue
            char = ''.join(chr(c) for c in codes
                           if c not in _TONE_PLACEHOLDERS)
            terms = [t.lower() for t in
                     [local_name, name] + local_keywords + keywords]
            emojis.append({'char': char, 'name': local_name or name,
                           'terms': terms, 'group': group})
        _unicode_emojis = emojis
    return _unicode_emojis


def search_unicode(emojis, query):
    """Emoji whose name or keywords contain *query*, name prefix matches
    first."""
    needle = query.lower()
    if not needle:
        return []
    starts, contains = [], []
    for emoji in emojis:
        if emoji['terms'][0].startswith(needle):
            starts.append(emoji)
        elif any(needle in term for term in emoji['terms']):
            contains.append(emoji)
    return starts + contains


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


def _emoji_widget(size):
    """A cell showing either a custom emoji image or a Unicode emoji."""
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.image = Gtk.Image(pixel_size=size)
    box.append(box.image)
    box.char = Gtk.Label()
    box.char.add_css_class('emoji-glyph')
    box.append(box.char)
    return box


def _show_emoji(box, emoji):
    if 'char' in emoji:
        box.image.set_visible(False)
        box.image._emoji_url = None
        box.image.set_from_paintable(None)
        box.char.set_visible(True)
        box.char.set_label(emoji['char'])
        return emoji['name']
    box.char.set_visible(False)
    box.image.set_visible(True)
    bind_emoji_image(box.image, emoji)
    return ':%s:' % emoji['shortcode']


class CustomEmojiChooser(Gtk.Popover):
    """Emoji popover with two tabs, standard Unicode emoji and the
    instance's custom emoji: each a list of categories (custom: recently
    used first) that opens a grid; typing in the search field searches
    every category of the current tab at once."""

    __gtype_name__ = 'BabbleCustomEmojiChooser'
    __gsignals__ = {
        # A custom emoji's bare shortcode.
        'emoji-picked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A standard emoji, as the text to insert.
        'text-picked': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.emojis = []
        self.categories = []
        self._loaded = False
        self.mode = 'emoji'
        self.add_css_class('emoji-picker')

        self.tabs = Adw.ToggleGroup(margin_top=6, margin_start=6,
                                    margin_end=6, homogeneous=True)
        self.tabs.add(Adw.Toggle(name='emoji', label=_('Emoji')))
        self.tabs.add(Adw.Toggle(name='custom', label=_('Custom')))
        self.tabs.set_active_name(self.mode)
        connect_weak(self.tabs, 'notify::active-name', self.on_tab_changed)

        self.search_entry = Gtk.SearchEntry(
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        connect_weak(self.search_entry, 'search-changed',
                     self.on_search_changed)

        self.category_store = Gio.ListStore.new(CategoryItem)
        factory = Gtk.SignalListItemFactory()
        connect_weak(factory, 'setup', self._setup_category)
        connect_weak(factory, 'bind', self._bind_category)
        self.category_list = Gtk.ListView(
            model=Gtk.NoSelection(model=self.category_store),
            factory=factory, single_click_activate=True)
        self.category_list.add_css_class('navigation-sidebar')
        self.category_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
            child=self.category_list)
        connect_weak(self.category_list, 'activate',
                     self.on_category_activated)

        self.grid_store = Gio.ListStore.new(EmojiItem)
        grid_factory = Gtk.SignalListItemFactory()
        connect_weak(grid_factory, 'setup', self._setup_emoji)
        connect_weak(grid_factory, 'bind', self._bind_emoji)
        self.grid = Gtk.GridView(
            model=Gtk.NoSelection(model=self.grid_store),
            factory=grid_factory, single_click_activate=True,
            min_columns=COLUMNS, max_columns=COLUMNS,
            margin_start=6, margin_end=6, margin_bottom=6)
        connect_weak(self.grid, 'activate', self.on_emoji_activated)

        self.back_button = Gtk.Button(icon_name='go-previous-symbolic',
                                      valign=Gtk.Align.CENTER)
        self.back_button.add_css_class('flat')
        self.back_button.set_tooltip_text(_('Back to Categories'))
        self.back_button.update_property([Gtk.AccessibleProperty.LABEL],
                                         [_('Back to Categories')])
        connect_weak(self.back_button, 'clicked', self.on_back)
        self.grid_title = Gtk.Label(xalign=0, hexpand=True)
        self.grid_title.add_css_class('heading')
        grid_header = Gtk.Box(spacing=6, margin_start=6, margin_end=6)
        grid_header.append(self.back_button)
        grid_header.append(self.grid_title)
        self.grid_header = grid_header
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid_box.append(grid_header)
        self.grid_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
            child=self.grid)
        grid_box.append(self.grid_scroller)

        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER,
                                         width_request=32,
                                         height_request=32), 'loading')
        self.stack.add_named(self.category_scroller, 'categories')
        self.stack.add_named(grid_box, 'grid')
        self.empty_page = Adw.StatusPage(icon_name='face-smile-symbolic')
        self.stack.add_named(self.empty_page, 'empty')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      width_request=320, height_request=380)
        box.append(self.tabs)
        box.append(self.search_entry)
        box.append(self.stack)
        self.set_child(box)
        connect_weak(self, 'map', self._on_map)
        connect_weak(self, 'closed', self._on_closed)
        self._update_search_hint()
        self.rebuild_categories()

    # Loading

    def _on_map(self, _popover):
        if not self._loaded:
            cached = cached_custom_emojis(self.client) \
                if getattr(self.client, 'instance', None) else []
            if cached is not None:
                self._on_emojis_loaded(cached)
            else:
                load_custom_emojis(self.client,
                                   weak_method(self._on_emojis_loaded))
        else:
            self.rebuild_categories()
        self.search_entry.grab_focus()

    def _on_closed(self, _popover):
        self.search_entry.set_text('')
        if self.stack.get_visible_child_name() == 'grid':
            self.stack.set_visible_child_name('categories')

    def _on_emojis_loaded(self, emojis):
        self._loaded = True
        self.emojis = emojis
        # An instance without custom emoji only has the standard tab.
        self.tabs.set_visible(bool(emojis))
        if not emojis and self.mode == 'custom':
            self.tabs.set_active_name('emoji')
        else:
            self.rebuild_categories()

    # Tabs

    def on_tab_changed(self, *_args):
        self.mode = self.tabs.get_active_name() or 'emoji'
        self._update_search_hint()
        self.rebuild_categories()
        # After the list has laid out the other tab's categories, or it
        # keeps the old scroll offset.
        GLib.idle_add(weak_method(self._scroll_to_top))

    def _scroll_to_top(self):
        if self.category_store.get_n_items():
            self.category_list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

    def _update_search_hint(self):
        if self.mode == 'custom':
            hint, label = _('Search Custom Emoji'), _('Search Custom Emoji')
            shown = self.emojis
            if shown:
                hint = shown[GLib.random_int_range(
                    0, len(shown))]['shortcode']
        else:
            hint, label = _('Search Emoji'), _('Search Emoji')
        self.search_entry.set_placeholder_text(hint)
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL],
                                          [label])

    def rebuild_categories(self):
        if self.mode == 'custom':
            if not self._loaded:
                self.category_store.remove_all()
                self.stack.set_visible_child_name('loading')
                return
            items = self._custom_categories()
        else:
            items = self._unicode_categories()
        self.categories = items
        self.category_store.splice(0, self.category_store.get_n_items(),
                                   items)
        self.grid_store.remove_all()
        if not items:
            self._show_empty(_('No Custom Emoji'),
                             _('This server has no custom emoji'))
        elif self.search_entry.get_text().strip():
            self.on_search_changed(self.search_entry)
        else:
            self.stack.set_visible_child_name('categories')

    def _custom_categories(self):
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
        return items

    def _unicode_categories(self):
        by_group = {}
        for emoji in unicode_emojis():
            by_group.setdefault(emoji['group'], []).append(emoji)
        return [CategoryItem(GLib.dpgettext2('gtk40', 'emoji category', name),
                             by_group[group])
                for group, name in _UNICODE_GROUPS if group in by_group]

    def _show_empty(self, title, description):
        self.empty_page.set_title(title)
        self.empty_page.set_description(description)
        self.stack.set_visible_child_name('empty')

    # Category list

    def _setup_category(self, _factory, item):
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        icon = _emoji_widget(EMOJI_SIZE)
        icon.set_size_request(EMOJI_SIZE, EMOJI_SIZE)
        box.append(icon)
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
        icon = box.get_first_child()
        label = icon.get_next_sibling()
        _show_emoji(icon, category.emojis[0])
        label.set_label(category.name)
        box.get_last_child().set_label(str(len(category.emojis)))

    def on_category_activated(self, _view, position):
        category = self.category_store.get_item(position)
        self.show_grid(category.name, category.emojis, search=False)

    def show_grid(self, title, emojis, search):
        self.grid_title.set_label(title)
        self.grid_header.set_visible(not search)
        self.grid_store.splice(0, self.grid_store.get_n_items(),
                               [EmojiItem(e) for e in emojis])
        self.grid_scroller.get_vadjustment().set_value(0)
        self.stack.set_visible_child_name('grid')

    def on_back(self, _button):
        self.stack.set_visible_child_name('categories')

    # Grid

    def _setup_emoji(self, _factory, item):
        cell = _emoji_widget(EMOJI_SIZE)
        cell.set_size_request(EMOJI_SIZE + 8, EMOJI_SIZE + 8)
        item.set_child(cell)

    def _bind_emoji(self, _factory, item):
        cell = item.get_child()
        tooltip = _show_emoji(cell, item.get_item().emoji)
        cell.set_tooltip_text(tooltip)
        cell.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])

    def on_emoji_activated(self, _view, position):
        emoji = self.grid_store.get_item(position).emoji
        if 'char' in emoji:
            self.emit('text-picked', emoji['char'])
        else:
            remember_recent(emoji['shortcode'])
            self.emit('emoji-picked', emoji['shortcode'])
        self.popdown()

    def pick(self, shortcode):
        """Pick the custom emoji *shortcode*, as a click on it would."""
        remember_recent(shortcode)
        self.emit('emoji-picked', shortcode)
        self.popdown()

    # Search

    def on_search_changed(self, entry):
        query = entry.get_text().strip()
        if self.mode == 'custom' and not (self._loaded and self.emojis):
            return
        if not query:
            self.stack.set_visible_child_name('categories')
            return
        if self.mode == 'custom':
            matches = search_emojis(self.emojis, query)
            missing = _('No custom emoji match “%s”') % query
        else:
            matches = search_unicode(unicode_emojis(), query)
            missing = _('No emoji match “%s”') % query
        if not matches:
            self._show_empty(_('No Results'), missing)
            return
        self.show_grid(_('Results'), matches, search=True)
