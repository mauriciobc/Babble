# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef list and timeline UI (Adwaita)."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from . import accounts, feedback, filterrules, timelinebase  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef.feed_model import FeedChefModel  # noqa: E402
from .feedchef.recipe import default_recipe  # noqa: E402
from .feedchef.storage import FeedRecipeStorage  # noqa: E402
from .feedchef.textutil import extract_focus  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefbuilder import FeedChefBuilderDialog  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .statusrow import StatusRow  # noqa: E402
from .timelinedata import StatusItem  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


def _account_key(account):
    return accounts.account_key(account)


def _storage_for(account):
    return FeedRecipeStorage(_account_key(account))


def _home_filter(statuses):
    filtered = []
    for status in statuses:
        hit = timelinebase.status_filter(status, filterrules.HOME)
        if hit is not None and hit[0] == 'hide':
            continue
        filtered.append(status)
    return filtered


class FeedChefListPage(Gtk.Box):
    """Manager: list recipes, open / edit / pin / delete."""

    __gtype_name__ = 'BabbleFeedChefListPage'

    def __init__(self, client, account):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.client = client
        self.account = account
        self.storage = _storage_for(account)

        header = Gtk.Box(spacing=8, margin_top=12, margin_bottom=8,
                         margin_start=12, margin_end=12)
        # Translators: Feed Chef is a recipe-driven timeline; Catch Up is a
        # period digest of home — they coexist.
        blurb = Gtk.Label(
            label=_('Feed Chef builds continuous timelines from recipes. '
                    'Catch Up remains a period digest of Home.'),
            wrap=True, xalign=0.0, hexpand=True)
        blurb.add_css_class('dim-label')
        header.append(blurb)
        add = Gtk.Button(label=_('New Recipe'))
        add.add_css_class('suggested-action')
        connect_weak(add, 'clicked', self._on_new)
        header.append(add)
        self.append(header)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class('boxed-list')
        self.list.set_margin_start(12)
        self.list.set_margin_end(12)
        self.list.set_margin_bottom(12)
        scrolled.set_child(self.list)
        self.append(scrolled)
        self.reload()

    def reload(self):
        while child := self.list.get_first_child():
            self.list.remove(child)
        recipes = self.storage.load_recipes()
        if not recipes:
            empty = Adw.ActionRow(
                title=_('No recipes yet'),
                subtitle=_('Create a recipe to cook a custom timeline'))
            self.list.append(empty)
            return
        for recipe in recipes:
            inert = recipe.inert_modifiers()
            subtitle = recipe.pantry.scope
            if inert:
                subtitle = _('%s — some modifiers need NLP (shown as inactive)'
                             ) % subtitle
            row = Adw.ActionRow(title=recipe.meta.name, subtitle=subtitle)
            open_btn = Gtk.Button(label=_('Open'), valign=Gtk.Align.CENTER)
            open_btn.recipe_id = recipe.id
            connect_weak(open_btn, 'clicked', self._open_clicked)
            row.add_suffix(open_btn)
            edit_btn = Gtk.Button(label=_('Edit'), valign=Gtk.Align.CENTER)
            edit_btn.recipe_id = recipe.id
            connect_weak(edit_btn, 'clicked', self._edit_clicked)
            row.add_suffix(edit_btn)
            pin_btn = Gtk.Button(label=_('Pin'), valign=Gtk.Align.CENTER)
            pin_btn.recipe_id = recipe.id
            connect_weak(pin_btn, 'clicked', self._pin_clicked)
            row.add_suffix(pin_btn)
            del_btn = Gtk.Button(label=_('Delete'), valign=Gtk.Align.CENTER)
            del_btn.add_css_class('destructive-action')
            del_btn.recipe_id = recipe.id
            connect_weak(del_btn, 'clicked', self._delete_clicked)
            row.add_suffix(del_btn)
            self.list.append(row)

    def _on_new(self, _button):
        recipe = default_recipe()
        self.storage.save_recipe(recipe)
        self._edit_recipe(recipe.id)

    def _open_clicked(self, button):
        self._open_recipe(getattr(button, 'recipe_id', ''))

    def _edit_clicked(self, button):
        self._edit_recipe(getattr(button, 'recipe_id', ''))

    def _pin_clicked(self, button):
        self._pin_recipe(getattr(button, 'recipe_id', ''))

    def _delete_clicked(self, button):
        self._delete_recipe(getattr(button, 'recipe_id', ''))

    def _open_recipe(self, recipe_id):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_page'):
            window.show_page('recipe:%s' % recipe_id)

    def _edit_recipe(self, recipe_id):
        recipe = self.storage.get_recipe_by_id(recipe_id)
        if recipe is None:
            return
        dialog = FeedChefBuilderDialog(self.client, self.account, recipe)
        connect_weak(dialog, 'closed', self._on_builder_closed)
        dialog.present(self.get_root())

    def _on_builder_closed(self, _dialog):
        self.reload()

    def _pin_recipe(self, recipe_id):
        settings = app_settings()
        key = 'favorite-feedchef-ids'
        account_id = str(self.account.get('id') or '')
        entry = '%s/%s' % (account_id, recipe_id)
        values = list(settings.get_strv(key))
        if entry not in values:
            values.append(entry)
            settings.set_strv(key, values)
        feedback.show_toast(self, _('Pinned to the sidebar'))
        window = self.get_root()
        if window is not None and hasattr(window, 'rebuild_sidebar'):
            window.rebuild_sidebar()

    def _delete_recipe(self, recipe_id):
        self._pending_delete = recipe_id
        dialog = Adw.AlertDialog(
            heading=_('Delete recipe?'),
            body=_('This removes the recipe from this account.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('delete', _('Delete'))
        dialog.set_response_appearance(
            'delete', Adw.ResponseAppearance.DESTRUCTIVE)
        connect_weak(dialog, 'response', self._on_delete_response)
        dialog.present(self.get_root())

    def _on_delete_response(self, _dialog, response):
        if response != 'delete':
            return
        recipe_id = getattr(self, '_pending_delete', None)
        if not recipe_id:
            return
        self.storage.delete_recipe(recipe_id)
        settings = app_settings()
        account_id = str(self.account.get('id') or '')
        prefix = '%s/' % account_id
        key = 'favorite-feedchef-ids'
        values = [item for item in settings.get_strv(key)
                  if not (item.startswith(prefix)
                          and item[len(prefix):] == recipe_id)]
        settings.set_strv(key, values)
        self.reload()


class FeedChefTimelinePage(Gtk.Box):
    __gtype_name__ = 'BabbleFeedChefTimelinePage'

    def __init__(self, client, account, recipe):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.client = client
        self.account = account
        self.recipe = recipe
        self.model = FeedChefModel()
        self._generation = None
        self._paging = False
        self.storage = _storage_for(account)
        self.engine = make_engine(
            client, _account_key(account),
            str(account.get('id') or ''), recipe)
        self.engine.init_preload()

        toolbar = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8,
                          margin_start=12, margin_end=12)
        self.status = Gtk.Label(label=_('Ready'), xalign=0, hexpand=True)
        self.status.add_css_class('dim-label')
        toolbar.append(self.status)
        if recipe.inert_modifiers():
            warn = Gtk.Label(
                label=_('Some modifiers are inactive'), xalign=0)
            warn.add_css_class('warning')
            toolbar.append(warn)
        mlt = Gtk.Button(label=_('More like this'))
        connect_weak(mlt, 'clicked', self._more_like_this)
        toolbar.append(mlt)
        clear = Gtk.Button(label=_('Clear focus'))
        connect_weak(clear, 'clicked', self._clear_focus)
        toolbar.append(clear)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic')
        connect_weak(refresh, 'clicked', self._refresh)
        toolbar.append(refresh)
        self.append(toolbar)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class('timeline')
        self.scroller.set_child(self.list)
        self.append(self.scroller)
        connect_weak(self.scroller.get_vadjustment(), 'value-changed',
                     self._maybe_page)
        self._refresh()

    def close_requests(self):
        self.cancel()

    def cancel(self):
        self.engine.cancel()

    def do_dispose(self):
        self.engine.shutdown()
        Gtk.Box.do_dispose(self)

    def _more_like_this(self, *_args):
        if not self.model.items:
            feedback.show_toast(self, _('Cook the timeline first'))
            return
        tags, keywords = extract_focus(self.model.items[0])
        self.recipe.kitchen.active_focus_hashtags = tags or None
        self.recipe.kitchen.active_focus_keywords = keywords or None
        self.storage.save_recipe(self.recipe)
        feedback.show_toast(self, _('Focus set from the top post'))
        self._refresh()

    def _clear_focus(self, *_args):
        self.recipe.kitchen.active_focus_hashtags = None
        self.recipe.kitchen.active_focus_keywords = None
        self.storage.save_recipe(self.recipe)
        self._refresh()

    def _refresh(self, *_args):
        self._paging = False
        self.engine.cancel()
        self.status.set_label(_('Cooking…'))
        self._generation = self.engine.process_feed(
            self.recipe,
            max_id=None,
            count=40,
            on_stage=self._on_stage,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _load_older(self):
        if self._paging or not self.model.has_more \
                or not self.model.scope_oldest_id:
            return
        self._paging = True
        self.status.set_label(_('Loading older…'))
        self._generation = self.engine.process_feed(
            self.recipe,
            max_id=self.model.scope_oldest_id,
            count=40,
            on_stage=None,
            on_done=self._on_page,
            on_error=self._on_error,
        )

    def _on_stage(self, stage, _items):
        self.status.set_label(_('Cooking… Stage: %s') % stage)

    def _on_done(self, result):
        self._paging = False
        filtered = _home_filter(result.items)
        self.model.replace(
            filtered,
            scope_oldest_id=result.scope_oldest_id,
            has_more=result.has_more,
            score_map=result.score_map,
        )
        self._rebuild_rows()
        self.status.set_label(_('%d posts') % len(self.model))

    def _on_page(self, result):
        self._paging = False
        filtered = _home_filter(result.items)
        self.model.append_page(
            filtered,
            scope_oldest_id=result.scope_oldest_id,
            has_more=result.has_more,
            score_map=result.score_map,
        )
        self._rebuild_rows()
        self.status.set_label(_('%d posts') % len(self.model))

    def _on_error(self, error):
        self._paging = False
        self.status.set_label(_('Could not cook this recipe'))
        feedback.report_error(self, _('Feed Chef'), error)

    def _rebuild_rows(self):
        while child := self.list.get_first_child():
            self.list.remove(child)
        for status in self.model.items:
            item = StatusItem(status)
            row = StatusRow()
            row.filter_context = filterrules.HOME
            row.bind(item, self.client)
            self.list.append(row)

    def _maybe_page(self, adjustment):
        if adjustment.get_upper() <= 0:
            return
        if adjustment.get_value() + adjustment.get_page_size() \
                >= adjustment.get_upper() - 200:
            self._load_older()


def present_import(parent, account, recipe, *, replace_existing=False):
    """Always confirm import (Java). Upsert then replaces silently."""
    del replace_existing  # callers may pass it; confirm is always shown
    author = (recipe.meta.author or '').strip()
    if author:
        body = _('Import “%s” by %s into this account?') % (
            recipe.meta.name, author)
    else:
        body = _('Import “%s” into this account?') % recipe.meta.name

    dialog = Adw.AlertDialog(
        heading=_('Import Feed Chef recipe?'),
        body=body)
    dialog.add_response('cancel', _('Cancel'))
    dialog.add_response('import', _('Import'))
    dialog.set_response_appearance(
        'import', Adw.ResponseAppearance.SUGGESTED)

    def response(_dialog, answer):
        if answer != 'import':
            return
        try:
            _storage_for(account).save_recipe(recipe)
        except Exception as error:
            feedback.report_error(parent, _('Feed Chef'), error)
            return
        feedback.show_toast(parent, _('Recipe imported'))

    dialog.connect('response', response)
    dialog.present(parent)
