# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's recipe list: open, edit, pin, copy a link, delete.

The page of a recipe's timeline, the places a stored recipe is brought up
to date and the pushed recipe page are in ``feedchefpages``; this list
opens them. The health badges of its rows are ``feedchefquality.ListHealth``
(a controller the list owns).
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import feedback, pagenav  # noqa: E402
from . import feedchefexplain  # noqa: E402
from . import scrollkeep  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef.icons import icon_name  # noqa: E402
from .feedchef.sharecodec import handle_of, recipe_share_uri  # noqa: E402
from .feedchefbuilder import (FeedChefBuilderDialog,  # noqa: E402
                              storage_for)
from .feedchefnewrecipe import NewRecipeDialog  # noqa: E402
from .feedchefpages import (ICON, PINNED_KEY, _pin_entry,  # noqa: E402
                            open_recipe, recipe_stored, recipe_subtitle)
from .feedchefquality import ListHealth  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


class FeedChefListPage(Adw.Bin):
    """Manager: list recipes, open / edit / pin / copy link / delete."""

    __gtype_name__ = 'BabbleFeedChefListPage'

    # Set by the narrow-width breakpoint: the health badges show their
    # icon only.
    compact = GObject.Property(type=bool, default=False)

    def __init__(self, client, account):
        super().__init__()
        self.client = client
        self.account = account
        self.storage = storage_for(account)
        self._rows = []
        self._row_by_id = {}
        self.health = ListHealth(self)

        add = Gtk.Button(icon_name='list-add-symbolic',
                         tooltip_text=_('New Recipe'))
        set_accessible_label(add, _('New Recipe'))
        connect_weak(add, 'clicked', self._on_new)
        # Packed at the end of the header by pagenav.make_nav_page.
        self.header_widgets = [add]

        self.stack = Gtk.Stack()
        self.group = Adw.PreferencesGroup()
        clamp = Adw.Clamp(maximum_size=COLUMN_WIDTH, child=self.group,
                          margin_top=12, margin_bottom=24,
                          margin_start=12, margin_end=12)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')
        scrollkeep.watch(self, scrolled)
        empty = Adw.StatusPage(
            icon_name=ICON, title=_('No Recipes Yet'),
            description=_('A recipe turns your timelines into a feed of '
                          'your own'))
        pill = Gtk.Button(label=_('New Recipe'), halign=Gtk.Align.CENTER)
        pill.add_css_class('pill')
        pill.add_css_class('suggested-action')
        connect_weak(pill, 'clicked', self._on_new)
        empty.set_child(pill)
        self.stack.add_named(empty, 'empty')
        narrow = Adw.BreakpointBin(width_request=280, height_request=200)
        narrow.set_child(self.stack)
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 450sp'))
        breakpoint_.add_setter(self, 'compact', True)
        narrow.add_breakpoint(breakpoint_)
        connect_weak(self, 'notify::compact', self._on_compact)
        self.set_child(narrow)

        self._add_actions()
        self.reload()
        connect_weak(self, 'map', self._on_shown)

    def _on_compact(self, *_args):
        self.health.set_compact(self.compact)

    def _on_shown(self, *_args):
        self.health.apply_health(feedchefexplain.WORDS)
        self.health.refresh_health(feedchefexplain.WORDS,
                                   list(self._row_by_id))

    def _add_actions(self):
        group = Gio.SimpleActionGroup()
        for name, handler in (('open', self._on_open),
                              ('edit', self._on_edit),
                              ('pin', self._on_pin),
                              ('copy-link', self._on_copy_link),
                              ('delete', self._on_delete)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            connect_weak(action, 'activate', handler)
            group.add_action(action)
        self.insert_action_group('recipe', group)

    def reload(self):
        for row in self._rows:
            self.group.remove(row)
        self._rows = []
        self._row_by_id = {}
        self.health.reset()
        recipes = self.storage.load_recipes()
        self.stack.set_visible_child_name('list' if recipes else 'empty')
        pinned = set(app_settings().get_strv(PINNED_KEY))
        for recipe in recipes:
            row = self._make_row(
                recipe, _pin_entry(self.account, recipe.id) in pinned)
            self.group.add(row)
            self._rows.append(row)
            self._row_by_id[recipe.id] = row
        self.health.refresh_health(feedchefexplain.WORDS, self._row_by_id)

    def _make_row(self, recipe, pinned):
        target = GLib.Variant.new_string(recipe.id)
        row = Adw.ActionRow(title=GLib.markup_escape_text(recipe.meta.name),
                            subtitle=GLib.markup_escape_text(
                                recipe_subtitle(recipe)),
                            activatable=True)
        row.set_action_name('recipe.open')
        row.set_action_target_value(target)
        row.add_prefix(Gtk.Image.new_from_icon_name(
            icon_name(recipe.meta.icon)))
        menu = Gio.Menu()
        # Delete is alone in its section, away from the everyday entries.
        for entries in (
                ((_('_Edit'), 'recipe.edit'),
                 (_('_Unpin From Sidebar') if pinned
                  else _('_Pin to Sidebar'), 'recipe.pin'),
                 (_('_Copy Share Link'), 'recipe.copy-link')),
                ((_('_Delete'), 'recipe.delete'),)):
            section = Gio.Menu()
            for label, action in entries:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(action, target)
                section.append_item(item)
            menu.append_section(None, section)
        options = Gtk.MenuButton(
            icon_name='view-more-symbolic', menu_model=menu,
            valign=Gtk.Align.CENTER)
        options.add_css_class('flat')
        # Translators: tooltip and accessible name of a recipe's menu
        # button; %s is the recipe's name.
        set_accessible_label(options, _('Options for “%s”')
                             % recipe.meta.name)
        row.add_suffix(self.health.health_badge(
            recipe, feedchefexplain.WORDS))
        row.add_suffix(options)
        row.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
        return row

    # Actions: the parameter is the recipe id.

    def _on_open(self, _action, parameter):
        """Open the recipe above this list, on the navigation stack: the
        back button, Escape and the swipe return to it, scroll kept."""
        recipe = self.storage.get_recipe_by_id(parameter.get_string())
        if recipe is None:
            return
        scrollkeep.remember(self)
        if open_recipe(self, self.client, self.account, recipe) is None:
            # A host with no navigation stack: the recipe as a place.
            window = self.get_root()
            if window is not None and hasattr(window, 'show_page'):
                window.show_page('recipe:%s' % recipe.id)

    def _on_new(self, _button):
        """New Recipe: pick what to start from, then the builder."""
        chooser = NewRecipeDialog()
        connect_weak(chooser, 'chosen', self._on_template_chosen)
        chooser.present(self)

    def _on_template_chosen(self, chooser):
        self._edit(chooser.recipe, is_new=True)

    def _on_edit(self, _action, parameter):
        recipe = self.storage.get_recipe_by_id(parameter.get_string())
        if recipe is not None:
            self._edit(recipe)

    def _edit(self, recipe, is_new=False):
        dialog = FeedChefBuilderDialog(
            self.client, self.account, recipe, is_new=is_new)
        connect_weak(dialog, 'saved', self._on_saved)
        dialog.present(self)

    def _on_saved(self, dialog):
        self.reload()
        recipe_stored(self, self.account, dialog.recipe, skip=self)
        feedback.show_toast(self, _('Recipe saved'))

    def _on_pin(self, _action, parameter):
        entry = _pin_entry(self.account, parameter.get_string())
        settings = app_settings()
        values = list(settings.get_strv(PINNED_KEY))
        if entry in values:
            values.remove(entry)
            message = _('Removed from the sidebar')
        else:
            values.append(entry)
            message = _('Pinned to the sidebar')
        settings.set_strv(PINNED_KEY, values)
        self.reload()
        feedback.show_toast(self, message)

    def _on_copy_link(self, _action, parameter):
        recipe = self.storage.get_recipe_by_id(parameter.get_string())
        display = self.get_display()
        if recipe is None or display is None:
            return
        display.get_clipboard().set(recipe_share_uri(
            recipe, author=handle_of(self.account)))
        feedback.show_toast(self, _('Share link copied'))

    def _on_delete(self, _action, parameter):
        """Take the row out of view at once and delete the recipe only
        when the Undo toast goes away, so a slip costs nothing."""
        recipe_id = parameter.get_string()
        row = self._row_by_id.get(recipe_id)
        if row is None:
            return
        anchor = pagenav.window_of(self) or self
        page = self.weak_ref()
        storage, account = self.storage, self.account

        def commit():
            storage.delete_recipe(recipe_id)
            settings = app_settings()
            entry = _pin_entry(account, recipe_id)
            settings.set_strv(PINNED_KEY, [
                item for item in settings.get_strv(PINNED_KEY)
                if item != entry])
            listing = page()
            if listing is not None:
                listing.reload()

        def restore():
            listing = page()
            if listing is not None:
                listing._show_row(recipe_id, True)

        self._show_row(recipe_id, False)
        feedback.deferred_action(anchor, _('Recipe deleted'),
                                 commit, restore)

    def _show_row(self, recipe_id, visible):
        row = self._row_by_id.get(recipe_id)
        if row is not None:
            row.set_visible(visible)
        shown = any(r.get_visible() for r in self._rows)
        self.stack.set_visible_child_name('list' if shown else 'empty')
