# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef list and timeline UI (Adwaita)."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import accounts, feedback, pagenav  # noqa: E402
from . import feedchefexplain  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef.icons import icon_name  # noqa: E402
from .feedchef.recipe import default_recipe  # noqa: E402
from .feedchef.sharecodec import recipe_share_uri  # noqa: E402
from .feedchef.textutil import extract_focus  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefbuilder import (FeedChefBuilderDialog, scope_label,  # noqa: E402
                              storage_for)
from .feedchefsource import FeedChefSource  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .pages import PagedPosts  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .uihelpers import icon_button, set_accessible_label  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

ICON = 'babble-feed-chef-symbolic'
PINNED_KEY = 'favorite-feedchef-ids'
SUBTITLE_TAGS = 3
# Terms the focus banner names before "and N more".
BANNER_TERMS = 3


def _pin_entry(account, recipe_id):
    return '%s/%s' % (str(account.get('id') or ''), recipe_id)


def recipe_subtitle(recipe):
    """"Home · #linux #python": the scope and up to three hashtags."""
    scope = scope_label(recipe.pantry.scope)
    tags = ' '.join('#%s' % tag
                    for tag in recipe.pantry.hashtags_include[:SUBTITLE_TAGS])
    if not tags:
        return scope
    # Translators: a recipe's source and hashtags, for example
    # "Home · #linux #python".
    return _('%(scope)s · %(tags)s') % {'scope': scope, 'tags': tags}


def recipe_stored(widget, account, recipe, skip=None):
    """Bring the places that show *recipe* up to date after it was saved
    or replaced by an import: the open recipe list reloads, a built
    timeline of it cooks the new version (its title follows a rename),
    and the sidebar is rebuilt when the recipe is pinned, since that is
    where its title is read. *skip* is the caller, which does its own."""
    window = pagenav.window_of(widget)
    navigation = getattr(window, 'navigation', None)
    if navigation is None:
        return
    listing = navigation.children.get('feed-chef')
    if listing is not None and listing is not skip:
        listing.reload()
    timeline = navigation.children.get('recipe:%s' % recipe.id)
    if isinstance(timeline, FeedChefTimelinePage) and timeline is not skip:
        timeline.use_recipe(recipe)
    if _pin_entry(account, recipe.id) in app_settings().get_strv(
            PINNED_KEY):
        host = getattr(window, 'sidebar_host', None)
        if host is not None:
            host.refresh()


class FeedChefListPage(Adw.Bin):
    """Manager: list recipes, open / edit / pin / copy link / delete."""

    __gtype_name__ = 'BabbleFeedChefListPage'

    def __init__(self, client, account):
        super().__init__()
        self.client = client
        self.account = account
        self.storage = storage_for(account)
        self._rows = []
        self._row_by_id = {}

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
        self.set_child(self.stack)

        self._add_actions()
        self.reload()

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
        recipes = self.storage.load_recipes()
        self.stack.set_visible_child_name('list' if recipes else 'empty')
        pinned = set(app_settings().get_strv(PINNED_KEY))
        for recipe in recipes:
            row = self._make_row(
                recipe, _pin_entry(self.account, recipe.id) in pinned)
            self.group.add(row)
            self._rows.append(row)
            self._row_by_id[recipe.id] = row

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
        row.add_suffix(options)
        row.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
        return row

    # Actions: the parameter is the recipe id.

    def _on_open(self, _action, parameter):
        window = self.get_root()
        if window is not None and hasattr(window, 'show_page'):
            window.show_page('recipe:%s' % parameter.get_string())

    def _on_new(self, _button):
        self._edit(default_recipe(_('New Recipe')), is_new=True)

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
        display.get_clipboard().set(recipe_share_uri(recipe))
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


class FeedChefTimelinePage(TimelinePage):
    """A recipe's timeline: a TimelinePage over a FeedChefSource.

    The cook's order is the kitchen's, so the list is never sorted by id
    and a refresh cooks again instead of inserting newer posts on top."""

    __gtype_name__ = 'BabbleFeedChefTimelinePage'

    def __init__(self, client, account, recipe):
        engine = make_engine(client, accounts.account_key(account),
                             str(account.get('id') or ''), recipe)
        engine.init_preload()
        source = FeedChefSource(engine, recipe)
        # No boost carousel: grouping boosts would reorder the kitchen's
        # list.
        super().__init__(client, source=source, boosts_carousel=False,
                         autoload=False)
        self.account = account
        self.recipe = recipe
        self.engine = engine
        self.storage = storage_for(account)
        source.stage_listener = weak_method(self._on_stage)

        # Packed at the end of the header by pagenav.make_nav_page.
        self.edit_button = icon_button('document-edit-symbolic',
                                       _('Edit Recipe'))
        connect_weak(self.edit_button, 'clicked', self._on_edit_clicked)
        self.report_button = icon_button('document-properties-symbolic',
                                         _('Kitchen Report'))
        connect_weak(self.report_button, 'clicked', self._on_report_clicked)
        self.header_widgets = [self.report_button, self.edit_button]

        self.loading_page.set_title(_('Cooking Your Timeline'))
        self.error_page.set_title(_('Could Not Load This Recipe'))
        self.empty_page.set_icon_name(ICON)
        self._build_empty_actions()
        self._sync_empty()
        self._reset_stage_hint()

        # The focus banner sits above the timeline's own stack.
        self.banner = Adw.Banner(button_label=_('Clear Focus'),
                                 use_markup=False)
        connect_weak(self.banner, 'button-clicked', self._clear_focus)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stack = self.get_child()
        self.set_child(None)
        stack.set_vexpand(True)
        column.append(self.banner)
        column.append(stack)
        self.set_child(column)
        self._update_focus_ui()
        self.ensure_loaded()

    def _finish(self, result):
        """The error page names a follow list that could not be loaded:
        a recipe limited to the people you follow cannot show anything
        without it."""
        try:
            statuses = TimelinePage._finish(self, result)
        except GLib.Error:
            self.error_page.set_title(
                _('Could Not Check Who You Follow')
                if self.source.error_kind == 'follow-list'
                else _('Could Not Load This Recipe'))
            raise
        self.error_page.set_title(_('Could Not Load This Recipe'))
        return statuses

    def do_dispose(self):
        engine = getattr(self, 'engine', None)
        if engine is not None:
            # The scope first: it cancels the request this page is waiting
            # on, which the page's callback drops without a word. Closing
            # the source first would answer it, and a page being disposed
            # would show that as an error.
            self._close_paging_scope()
            self.source.close()
            engine.shutdown()
        TimelinePage.do_dispose(self)

    # The empty page

    def _build_empty_actions(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)
        self.more_button = Gtk.Button(label=_('Load Older Posts'),
                                      halign=Gtk.Align.CENTER)
        self.more_button.add_css_class('pill')
        self.more_button.add_css_class('suggested-action')
        connect_weak(self.more_button, 'clicked', self._on_load_older)
        box.append(self.more_button)
        self.empty_edit = Gtk.Button(label=_('Edit Recipe'),
                                     halign=Gtk.Align.CENTER)
        self.empty_edit.add_css_class('pill')
        connect_weak(self.empty_edit, 'clicked', self._on_edit_clicked)
        box.append(self.empty_edit)
        self.empty_page.set_child(box)

    def _sync_empty(self):
        """The empty page is final only when the scope has nothing older.
        After the source gave up hopping over empty batches, older posts
        may still match: say so and offer to keep going."""
        paused = bool(self.source.stalled) \
            and self.model.get_n_items() == 0
        self.more_button.set_visible(paused)
        if paused:
            self.empty_page.set_title(_('No Matching Posts Yet'))
            self.empty_page.set_description(
                _('Older posts may still match this recipe'))
        else:
            self.empty_page.set_title(_('No Matching Posts'))
            self.empty_page.set_description(
                _('Try fewer filters or a wider scope'))

    def _finish_page_loaded(self, statuses, ticket=None, raw_oldest=None):
        TimelinePage._finish_page_loaded(self, statuses, ticket, raw_oldest)
        self._sync_empty()

    def _finish_older_loaded(self, statuses, ticket=None, before=0,
                             raw_oldest=None):
        TimelinePage._finish_older_loaded(
            self, statuses, ticket, before=before, raw_oldest=raw_oldest)
        if self.source.stalled and self.model.exhausted:
            # An empty batch after several hops is a pause, not the end:
            # the next scroll to the bottom continues.
            self.model.exhausted = False

    def _on_load_older(self, _button):
        """Cook on from the scope cursor when nothing is on screen yet
        (load_older needs a post to page from)."""
        cursor = self.source.cursor
        if cursor is None or self.model.loading or self._scope.closed:
            return
        self.model.loading = True
        self.stack.set_visible_child_name('loading')
        self._request({'limit': self.PAGE_SIZE, 'max_id': cursor},
                      self.on_page_loaded, on_drop=PagedPosts._drop_loading,
                      owns_loading=True)

    # Cooking

    def refresh(self):
        """Cook again: the list is replaced, in kitchen order."""
        if self._scope.closed:
            return
        self.model.loading = False
        self._older_pending = False
        self._loading_older = False
        self._release_bottom()
        self.model.clear()
        self._reset_stage_hint()
        self.load()

    def _reset_stage_hint(self):
        self.loading_page.set_description(_('Gathering posts…'))

    def _on_stage(self, stage):
        hint = {'PANTRY': _('Applying your diet…'),
                'DIET': _('Seasoning…')}.get(stage)
        if hint:
            self.loading_page.set_description(hint)

    # More Like This

    def post_menu_entries(self, status):
        """The post menu's own entry (see StatusMenuButton._page_section)."""
        # Translators: post menu entries; the underscore marks the access
        # key (L and W), which no other entry of that menu uses.
        return [(_('More _Like This'), self._more_like_this),
                (_('_Why This Post?'), self._why_this_post)]

    def _why_this_post(self, status):
        feedchefexplain.WhyThisPostDialog(
            self.source.audit_for(status.get('id'))).present(self)

    def _on_report_clicked(self, _button):
        feedchefexplain.KitchenReportDialog(
            self.source.cook_audits()).present(self)

    def _shown_statuses(self):
        found = []
        for position in range(self.model.get_n_items()):
            status = getattr(self.model.get_item(position), 'status', None)
            if isinstance(status, dict):
                found.append(status)
        return found

    def _more_like_this(self, status):
        tags, keywords = extract_focus(status, self._shown_statuses())
        if not tags and not keywords:
            feedback.show_toast(self, _('Nothing to focus on in this post'))
            return
        kitchen = self.recipe.kitchen
        kitchen.active_focus_hashtags = tags or None
        kitchen.active_focus_keywords = keywords or None
        self._save_focus()
        feedback.show_toast(self, _('Focus set from this post'))

    def _clear_focus(self, *_args):
        kitchen = self.recipe.kitchen
        kitchen.active_focus_hashtags = None
        kitchen.active_focus_keywords = None
        self._save_focus()

    def _save_focus(self):
        try:
            self.storage.save_recipe(self.recipe)
        except Exception as error:
            feedback.report_error(self, _('Could not save the recipe'),
                                  error)
            return
        self._update_focus_ui()
        self.refresh()

    def _update_focus_ui(self):
        kitchen = self.recipe.kitchen
        parts = ['#%s' % tag for tag in kitchen.active_focus_hashtags or []]
        parts += list(kitchen.active_focus_keywords or [])
        if parts:
            terms = ', '.join(parts[:BANNER_TERMS])
            extra = len(parts) - BANNER_TERMS
            if extra > 0:
                # Translators: %(terms)s lists the first hashtags and words
                # the timeline is focused on, such as "#a, #b, #c"; %(count)d
                # is how many more there are.
                terms = ngettext('%(terms)s and %(count)d more',
                                 '%(terms)s and %(count)d more',
                                 extra) % {'terms': terms, 'count': extra}
            # Translators: %s lists the hashtags and words the timeline is
            # focused on, for example "#python, decorators".
            self.banner.set_title(_('Showing more like %s') % terms)
        # The whole list, for when the title had to leave some out.
        self.banner.set_tooltip_text(', '.join(parts) if parts else None)
        self.banner.set_revealed(bool(parts))

    # Editing the recipe

    def _on_edit_clicked(self, _button):
        dialog = FeedChefBuilderDialog(self.client, self.account, self.recipe)
        connect_weak(dialog, 'saved', self._on_recipe_saved)
        dialog.present(self)

    def _on_recipe_saved(self, dialog):
        self.use_recipe(dialog.recipe)
        recipe_stored(self, self.account, self.recipe, skip=self)
        feedback.show_toast(self, _('Recipe saved'))

    def use_recipe(self, recipe):
        """Cook *recipe* from now on: a new engine, the list replaced, the
        place title following a rename."""
        self.recipe = recipe
        if self._scope.closed:
            return
        # Supersede the cook in flight first: its cancelled task is then
        # dropped by the scope instead of being shown as an error.
        self._scope.begin()
        self.source.close()
        old = self.engine
        self.engine = make_engine(
            self.client, accounts.account_key(self.account),
            str(self.account.get('id') or ''), self.recipe)
        self.engine.init_preload()
        self.source.engine = self.engine
        self.source.recipe = self.recipe
        old.shutdown()
        self._update_focus_ui()
        nav_page = self.get_ancestor(Adw.NavigationPage)
        if nav_page is not None:
            nav_page.set_title(recipe.meta.name)
        self.refresh()


def present_import(parent, account, recipe):
    """Ask before a shared recipe is stored. A recipe with the same id is
    replaced, which is said, since the old one is lost; otherwise the
    recipe is added. Returns the dialog."""
    existing = storage_for(account).get_recipe_by_id(recipe.id)
    if existing is not None:
        heading = _('Replace Recipe?')
        body = _('This replaces your recipe “%s”.') % existing.meta.name
        action, label = 'replace', _('_Replace')
        appearance = Adw.ResponseAppearance.DESTRUCTIVE
    else:
        author = (recipe.meta.author or '').strip()
        heading = _('Import Recipe?')
        if author:
            body = _('Import “%(name)s” by %(author)s into this '
                     'account?') % {'name': recipe.meta.name,
                                    'author': author}
        else:
            body = _('Import “%(name)s” into this account?') % {
                'name': recipe.meta.name}
        action, label = 'import', _('_Import')
        appearance = Adw.ResponseAppearance.SUGGESTED

    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response(action, label)
    dialog.set_response_appearance(action, appearance)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def response(_dialog, answer):
        if answer != action:
            return
        try:
            storage_for(account).save_recipe(recipe)
        except Exception as error:
            feedback.report_error(parent, _('Could not save the recipe'),
                                  error)
            return
        recipe_stored(parent, account, recipe)
        feedback.show_toast(parent, _('Recipe imported'))

    dialog.connect('response', response)
    dialog.present(parent)
    return dialog
