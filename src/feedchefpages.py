# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef list and timeline UI (Adwaita)."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from functools import partial  # noqa: E402

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import accounts, feedback, filterrules, timelinebase  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef.feed_model import FeedChefModel, RequestGate  # noqa: E402
from .feedchef.recipe import default_recipe  # noqa: E402
from .feedchef.sharecodec import recipe_share_uri  # noqa: E402
from .feedchef.storage import FeedRecipeStorage  # noqa: E402
from .feedchef.textutil import extract_focus  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .i18n import ngettext  # noqa: E402
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


class FeedChefBuilderDialog(Adw.PreferencesDialog):
    __gtype_name__ = 'BabbleFeedChefBuilderDialog'

    def __init__(self, client, account, recipe):
        super().__init__(title=_('Feed Chef Recipe'))
        self.client = client
        self.account = account
        self.recipe = recipe
        self.storage = _storage_for(account)

        page = Adw.PreferencesPage()
        self.add(page)
        general = Adw.PreferencesGroup(title=_('Recipe'))
        self.name_row = Adw.EntryRow(title=_('Name'))
        self.name_row.set_text(recipe.meta.name)
        general.add(self.name_row)
        page.add(general)

        pantry = Adw.PreferencesGroup(
            title=_('Pantry'),
            description=_('Where posts come from'))
        self.scope = Adw.ComboRow(title=_('Scope'))
        model = Gtk.StringList.new(['home', 'local', 'federated', 'list'])
        self.scope.set_model(model)
        scopes = ['home', 'local', 'federated', 'list']
        try:
            self.scope.set_selected(scopes.index(recipe.pantry.scope))
        except ValueError:
            self.scope.set_selected(0)
        pantry.add(self.scope)
        self.list_id = Adw.EntryRow(title=_('List id'))
        self.list_id.set_text(recipe.pantry.list_id or '')
        pantry.add(self.list_id)
        self.include = Adw.EntryRow(title=_('Include hashtags'))
        self.include.set_text(' '.join(recipe.pantry.hashtags_include))
        pantry.add(self.include)
        self.exclude = Adw.EntryRow(title=_('Exclude hashtags'))
        self.exclude.set_text(' '.join(recipe.pantry.hashtags_exclude))
        pantry.add(self.exclude)
        reach = Adw.SwitchRow(
            title=_('Network reach (degree 1)'),
            subtitle=_('Off when degrees is unset — not “unlimited”'))
        reach.set_active(recipe.pantry.network_reach.enabled)
        self.reach_switch = reach
        pantry.add(reach)
        self.reach_degrees = Adw.SpinRow(
            title=_('Max degrees'),
            adjustment=Gtk.Adjustment(value=float(
                recipe.pantry.network_reach.max_degrees or 0),
                lower=0, upper=5, step_increment=1),
            digits=0)
        pantry.add(self.reach_degrees)
        page.add(pantry)

        diet = Adw.PreferencesGroup(title=_('Diet'))
        self.blocklist = Adw.EntryRow(title=_('Keyword blocklist'))
        self.blocklist.set_text(' '.join(recipe.diet.keyword_blocklist))
        diet.add(self.blocklist)
        self.media = Adw.SwitchRow(title=_('Must have media'))
        self.media.set_active(recipe.diet.hard_filters.must_have_media)
        diet.add(self.media)
        self.alt = Adw.SwitchRow(title=_('Must have alt text'))
        self.alt.set_active(recipe.diet.hard_filters.must_have_alt_text)
        diet.add(self.alt)
        self.sensitive = Adw.SwitchRow(title=_('No sensitive media'))
        self.sensitive.set_active(recipe.diet.hard_filters.no_sensitive_media)
        diet.add(self.sensitive)
        page.add(diet)

        kitchen = Adw.PreferencesGroup(
            title=_('Kitchen'),
            description=_('Similarity / sentiment stay inactive until NLP '
                          'is approved'))
        self.base_sort = Adw.ComboRow(title=_('Base sort'))
        self.base_sort.set_model(Gtk.StringList.new(['score', 'chronological']))
        self.base_sort.set_selected(
            0 if recipe.kitchen.base_sort != 'chronological' else 1)
        kitchen.add(self.base_sort)
        inert = recipe.inert_modifiers()
        if inert:
            warn = Adw.ActionRow(
                title=_('Inactive modifiers'),
                subtitle=', '.join(inert))
            kitchen.add(warn)
        page.add(kitchen)

        actions = Adw.PreferencesGroup()
        save = Adw.ButtonRow(title=_('Save'))
        connect_weak(save, 'activated', self._save)
        actions.add(save)
        share = Adw.ButtonRow(title=_('Copy share link'))
        connect_weak(share, 'activated', self._share)
        actions.add(share)
        page.add(actions)

    def _collect(self):
        scopes = ['home', 'local', 'federated', 'list']
        self.recipe.meta.name = self.name_row.get_text().strip() or _(
            'Untitled Recipe')
        self.recipe.pantry.scope = scopes[self.scope.get_selected()]
        self.recipe.pantry.list_id = self.list_id.get_text().strip() or None
        self.recipe.pantry.hashtags_include = [
            part.lstrip('#').lower()
            for part in self.include.get_text().split() if part.strip()]
        self.recipe.pantry.hashtags_exclude = [
            part.lstrip('#').lower()
            for part in self.exclude.get_text().split() if part.strip()]
        self.recipe.pantry.network_reach.enabled = self.reach_switch.get_active()
        degrees = int(self.reach_degrees.get_value())
        self.recipe.pantry.network_reach.max_degrees = (
            None if degrees <= 0 else degrees)
        self.recipe.diet.keyword_blocklist = [
            part.lower() for part in self.blocklist.get_text().split()
            if part.strip()]
        self.recipe.diet.hard_filters.must_have_media = self.media.get_active()
        self.recipe.diet.hard_filters.must_have_alt_text = self.alt.get_active()
        self.recipe.diet.hard_filters.no_sensitive_media = (
            self.sensitive.get_active())
        self.recipe.kitchen.base_sort = (
            'chronological' if self.base_sort.get_selected() == 1 else 'score')
        return self.recipe

    def _save(self, *_args):
        self.storage.save_recipe(self._collect())
        feedback.show_toast(self, _('Recipe saved'))
        self.close()

    def _share(self, *_args):
        recipe = self._collect()
        uri = recipe_share_uri(recipe)
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(uri)
        feedback.show_toast(self, _('Share link copied'))


class FeedChefTimelinePage(Gtk.Box):
    __gtype_name__ = 'BabbleFeedChefTimelinePage'

    def __init__(self, client, account, recipe):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.client = client
        self.account = account
        self.recipe = recipe
        self.model = FeedChefModel()
        self._gate = RequestGate()
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
        self.focus_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.focus_label.add_css_class('dim-label')
        toolbar.append(self.focus_label)
        self.clear_focus_button = Gtk.Button(label=_('Clear focus'))
        connect_weak(self.clear_focus_button, 'clicked', self._clear_focus)
        toolbar.append(self.clear_focus_button)
        self._update_focus_ui()
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

    def _update_focus_ui(self):
        kitchen = self.recipe.kitchen
        tags = kitchen.active_focus_hashtags or []
        words = kitchen.active_focus_keywords or []
        parts = ['#%s' % tag for tag in tags] + list(words)
        self.focus_label.set_visible(bool(parts))
        self.clear_focus_button.set_visible(bool(parts))
        if parts:
            # Translators: %s is the list of hashtags and keywords the
            # timeline is focused on.
            self.focus_label.set_label(_('Focus: %s') % ' '.join(parts))

    def _more_like_this_clicked(self, button):
        sid = getattr(button, 'status_id', '')
        for status in self.model.items:
            if str(status.get('id') or '') == sid:
                self._more_like_this(status)
                return

    def _more_like_this(self, status):
        tags, keywords = extract_focus(status, self.model.items)
        self.recipe.kitchen.active_focus_hashtags = tags or None
        self.recipe.kitchen.active_focus_keywords = keywords or None
        self.storage.save_recipe(self.recipe)
        self._update_focus_ui()
        feedback.show_toast(self, _('Focus set from this post'))
        self._refresh()

    def _clear_focus(self, *_args):
        self.recipe.kitchen.active_focus_hashtags = None
        self.recipe.kitchen.active_focus_keywords = None
        self.storage.save_recipe(self.recipe)
        self._update_focus_ui()
        self._refresh()

    def _refresh(self, *_args):
        generation = self._gate.begin_refresh()
        self.engine.cancel()
        self.status.set_label(_('Cooking…'))
        self.engine.process_feed(
            self.recipe,
            max_id=None,
            count=40,
            on_stage=partial(self._deliver_stage, generation),
            on_done=partial(self._deliver_done, generation),
            on_error=partial(self._deliver_error, generation),
        )

    def _load_older(self):
        if not self.model.has_more or not self.model.scope_oldest_id:
            return
        generation = self._gate.begin_page()
        if generation is None:
            return
        self.status.set_label(_('Loading older…'))
        self.engine.process_feed(
            self.recipe,
            max_id=self.model.scope_oldest_id,
            count=40,
            on_stage=None,
            on_done=partial(self._deliver_page, generation),
            on_error=partial(self._deliver_error, generation),
        )

    def _deliver_stage(self, generation, stage, items):
        if self._gate.accept(generation):
            self._on_stage(stage, items)

    def _deliver_done(self, generation, result):
        if self._gate.finish(generation):
            self._on_done(result)

    def _deliver_page(self, generation, result):
        if self._gate.finish(generation):
            self._on_page(result)

    def _deliver_error(self, generation, error):
        if self._gate.finish(generation):
            self._on_error(error)

    def _on_stage(self, stage, _items):
        self.status.set_label(_('Cooking… Stage: %s') % stage)

    def _posts_label(self):
        count = len(self.model)
        # Translators: the number of posts in a Feed Chef timeline.
        return ngettext('%d post', '%d posts', count) % count

    def _on_done(self, result):
        filtered = _home_filter(result.items)
        self.model.replace(
            filtered,
            scope_oldest_id=result.scope_oldest_id,
            has_more=result.has_more,
            score_map=result.score_map,
        )
        self._rebuild_rows()
        self.status.set_label(self._posts_label())

    def _on_page(self, result):
        filtered = _home_filter(result.items)
        self.model.append_page(
            filtered,
            scope_oldest_id=result.scope_oldest_id,
            has_more=result.has_more,
            score_map=result.score_map,
        )
        self._rebuild_rows()
        self.status.set_label(self._posts_label())

    def _on_error(self, error):
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
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(row)
            more = Gtk.Button(label=_('More like this'),
                              halign=Gtk.Align.END, margin_end=12,
                              margin_bottom=6)
            more.add_css_class('flat')
            more.status_id = str(status.get('id') or '')
            connect_weak(more, 'clicked', self._more_like_this_clicked)
            box.append(more)
            self.list.append(box)

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
