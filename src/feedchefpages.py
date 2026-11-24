# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef list and timeline UI (Adwaita)."""

import copy

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import accounts, feedback, pagenav  # noqa: E402
from . import feedchefexplain  # noqa: E402
from .appsettings import settings as app_settings  # noqa: E402
from .feedchef import relax  # noqa: E402
from .feedchef.focus import decay_focus  # noqa: E402
from .feedchef.icons import icon_name  # noqa: E402
from .feedchef.sentence import chef_line, list_subtitle  # noqa: E402
from .feedchef.sharecodec import handle_of, recipe_share_uri  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefbuilder import (FeedChefBuilderDialog,  # noqa: E402
                              storage_for)
from .feedchefexperiment import ExperimentMixin  # noqa: E402
from .feedcheffocus import FocusMixin  # noqa: E402
from .feedchefmodifiers import heat_label  # noqa: E402
from .feedchefnewrecipe import NewRecipeDialog  # noqa: E402
from .feedchefpeek import PagePeek  # noqa: E402
from .feedchefrelax import undo_relax  # noqa: E402
from .feedchefpreview import FeedChefPreviewDialog  # noqa: E402
from .feedchefquality import ListHealthMixin  # noqa: E402
from .feedchefsource import FeedChefSource  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from . import scrollkeep  # noqa: E402
from .pages import PagedPosts  # noqa: E402
from .rowdecor import Decoration  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .uihelpers import icon_button, set_accessible_label  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

ICON = 'babble-feed-chef-symbolic'
PINNED_KEY = 'favorite-feedchef-ids'
# The symbolic icons the Hot badge tries, in order; none: text only.
FIRE_ICONS = ('fire-symbolic', 'flame-symbolic', 'babble-trending-symbolic')


def _pin_entry(account, recipe_id):
    return '%s/%s' % (str(account.get('id') or ''), recipe_id)


def recipe_subtitle(recipe):
    """What the list shows under a recipe's name: its sentence ("Posts from
    Home about #linux #python, most engaging first") and its chef."""
    return list_subtitle(recipe, feedchefexplain.WORDS)


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


class FeedChefListPage(ListHealthMixin, Adw.Bin):
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
        self.watch_shown(self._on_shown)

    def _on_compact(self, *_args):
        self.set_badges_compact(self.compact)

    def _on_shown(self, *_args):
        self.apply_health(feedchefexplain.WORDS)
        self.refresh_health(feedchefexplain.WORDS,
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
        self._badges = {}
        self._slots = {}
        recipes = self.storage.load_recipes()
        self.stack.set_visible_child_name('list' if recipes else 'empty')
        pinned = set(app_settings().get_strv(PINNED_KEY))
        for recipe in recipes:
            row = self._make_row(
                recipe, _pin_entry(self.account, recipe.id) in pinned)
            self.group.add(row)
            self._rows.append(row)
            self._row_by_id[recipe.id] = row
        self.refresh_health(feedchefexplain.WORDS, self._row_by_id)

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
        row.add_suffix(self._health_badge(recipe, feedchefexplain.WORDS))
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


def open_recipe(widget, client, account, recipe):
    """Push a recipe's timeline onto the window's navigation stack (so it
    has the back button); its navigation page, or None without a window. It
    is its own instance, not the cached place of a pinned sidebar entry (one
    widget cannot be a root place and a pushed page), freed on going back."""
    window = pagenav.window_of(widget)
    if window is None or getattr(window, 'navigation_view', None) is None:
        return None
    page = FeedChefTimelinePage(client, account, recipe)
    page.watch_feed_chef_key()
    return pagenav.push_page(widget, recipe.meta.name, page,
                             tag='recipe-open:%s' % recipe.id)


class FeedChefTimelinePage(ExperimentMixin, FocusMixin, PagePeek,
                          TimelinePage):
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
        self.quality_init(column, feedchefexplain.WORDS)
        column.append(stack)
        self.set_child(column)
        self._update_focus_ui()
        self._peek_init()
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
        self._update_focus_ui()
        self._say_heat_fallback()
        self.after_cook()
        return statuses

    def _say_heat_fallback(self):
        """A page that had too few posts for the heat level steps down to a
        cooler one, and says which, once per cook session."""
        found = self.source.heat_fallback()
        if found is None:
            return
        # Translators: a toast; the first %s is the heat level the recipe
        # asks for, such as "Fire", and the second the cooler one the page
        # used instead, such as "Hot" (or "every post"), because the page
        # would have kept fewer than five posts.
        feedback.show_toast(self, _('Too few posts for %(asked)s. Showing '
                                    '%(used)s.') % {
            'asked': heat_label(found[0]), 'used': heat_label(found[1])})

    def row_decorations(self, status):
        """The chips a post wears on this page (see ``rowdecor``): "Popular"
        for a post above the page's 90th percentile of scores."""
        if str(status.get('id') or '') not in self.source.fire_ids:
            return ()
        theme = Gtk.IconTheme.get_for_display(self.get_display())
        icon = next((name for name in FIRE_ICONS if theme.has_icon(name)),
                    None)
        return (Decoration('fire', _('Popular'), icon,
                           _('Popular, among the most engaging posts')),)

    def watch_feed_chef_key(self):
        """For a page pushed above the list (not a place the sidebar drops
        itself): when Feed Chef is turned off it stops its work and leaves
        the stack."""
        connect_weak(app_settings(), 'changed::feed-chef-enabled',
                     self._on_feed_chef_key)

    def _on_feed_chef_key(self, settings, key):
        if settings.get_boolean(key):
            return
        self.close_requests()
        nav_page = self.get_ancestor(Adw.NavigationPage)
        window = pagenav.window_of(self)
        view = getattr(window, 'navigation_view', None)
        if nav_page is not None and view is not None:
            below = view.get_previous_page(nav_page)
            if below is not None:
                view.pop_to_page(below)

    def close_requests(self):
        """Stop the cook and its worker threads now, for a place the
        sidebar drops (Feed Chef turned off); a page held by a reference
        cycle would otherwise keep them until the next collection."""
        TimelinePage.close_requests(self)
        self.close_quality()
        self.set_auto_refresh_interval(0)
        engine = getattr(self, 'engine', None)
        if engine is not None:
            self.source.close()
            engine.shutdown()

    def do_dispose(self):
        self.set_auto_refresh_interval(0)
        self.close_quality()
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
        # Relax Filters: one click turns off the rule that removed the most
        # posts (named on the button), with Undo.
        # A label that wraps: a rule's name can be long, and the button
        # must fit a narrow window and a large text size.
        self.relax_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.relax_button = Gtk.Button(halign=Gtk.Align.CENTER,
                                       child=self.relax_label)
        self.relax_button.add_css_class('pill')
        self.relax_button.add_css_class('suggested-action')
        self.relax_button.set_visible(False)
        connect_weak(self.relax_button, 'clicked', self._on_relax)
        self._relax_rule = None
        box.append(self.relax_button)
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
        records = self.source.cook_audits()
        rule = None if paused else relax.suggestion(self.recipe, records)
        self._relax_rule = rule
        self.relax_button.set_visible(rule is not None)
        if rule is not None:
            text = relax.label(rule, feedchefexplain.WORDS)
            self.relax_label.set_label(text)
            self.relax_button.update_property(
                [Gtk.AccessibleProperty.LABEL], [text])
        # One suggested action at a time: Edit Recipe is it when nothing
        # else is offered.
        if paused or rule is not None:
            self.empty_edit.remove_css_class('suggested-action')
        else:
            self.empty_edit.add_css_class('suggested-action')
        if paused:
            self.empty_page.set_title(_('No Matching Posts Yet'))
            self.empty_page.set_description(
                _('Older posts may still match this recipe'))
        elif rule is None and relax.source_is_empty(records):
            self.empty_page.set_title(_('No Matching Posts'))
            self.empty_page.set_description(
                _('The source has no posts yet'))
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
        """The reader's refresh (the pull, the window's reload): a focus
        fades one step (see ``feedchef.focus``) and the recipe is saved
        with it, then the page is cooked again."""
        if self._scope.closed:
            return
        if self._fading():
            # The cook in flight is over before the focus it cooks changes
            # (the engine also works on a copy of the recipe).
            self._scope.begin()
            self.source.close()
        if decay_focus(self.recipe.kitchen):
            try:
                self.storage.save_recipe(self.recipe)
            except Exception as error:
                feedback.report_error(
                    self, _('Could not save the recipe'), error)
            self._update_focus_ui()
        self._cook()

    def _fading(self):
        """Whether a refresh now would fade the focus."""
        kitchen = self.recipe.kitchen
        return bool(kitchen.active_focus_keywords
                    or kitchen.active_focus_hashtags) \
            and isinstance(kitchen.focus_decay, (int, float)) \
            and kitchen.focus_decay < 1.0

    def _cook(self):
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
            self.source.cook_audits(),
            self._quality_context()).present(self)

    # Relax Filters

    def _on_relax(self, _button):
        """Turn off the rule that removed the most posts, save, cook again
        and offer Undo."""
        rule = self._relax_rule
        if rule is None or self._scope.closed:
            return
        before = copy.deepcopy(self.recipe)
        after = copy.deepcopy(self.recipe)
        relax.apply(after, rule)
        if not self._switch_to(after):
            return
        ref = self.weak_ref()
        anchor = pagenav.window_of(self) or self
        storage, account = self.storage, self.account

        def undo():
            undo_relax(ref, anchor, storage, account, before, rule)

        feedback.show_toast(self, relax.done(rule, feedchefexplain.WORDS),
                            undo=undo)

    def _switch_to(self, recipe):
        """Store *recipe* and cook it; False (and the reader told) when it
        could not be stored."""
        if self.storage.get_recipe_by_id(recipe.id) is None:
            # Deleted since (in another window, or under an Undo toast):
            # saving would bring it back.
            feedback.show_toast(self, _('This recipe no longer exists'))
            return False
        try:
            self.storage.save_recipe(recipe)
        except Exception as error:
            feedback.report_error(self, _('Could not save the recipe'),
                                  error)
            return False
        self.use_recipe(recipe)
        recipe_stored(self, self.account, recipe, skip=self)
        return True

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
        self._cook()


def present_import(parent, account, recipe):
    """Ask before a shared recipe is stored. A recipe with the same id is
    replaced, which is said, since the old one is lost; otherwise the
    recipe is added. Preview cooks the incoming recipe first (nothing is
    stored), and the question comes back after it.
    Returns the dialog."""
    existing = storage_for(account).get_recipe_by_id(recipe.id)
    chef = chef_line(recipe, feedchefexplain.WORDS)
    if existing is not None:
        heading = _('Replace Recipe?')
        body = _('This replaces your recipe “%s”.') % existing.meta.name
        action, label = 'replace', _('_Replace')
        appearance = Adw.ResponseAppearance.DESTRUCTIVE
        if chef:
            body = '%s\n%s' % (body, chef)
    else:
        heading = _('Import Recipe?')
        body = _('Import “%s” into this account?') % recipe.meta.name
        action, label = 'import', _('_Import')
        appearance = Adw.ResponseAppearance.SUGGESTED
        if chef:
            body = '%s\n%s' % (body, chef)

    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('preview', _('_Preview'))
    dialog.add_response(action, label)
    dialog.set_response_appearance(action, appearance)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def response(_dialog, answer):
        if answer == 'preview':
            _preview_import(parent, account, recipe)
            return
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


def _preview_import(parent, account, recipe):
    """Cook the incoming recipe in the preview (a copy: nothing is stored,
    and the preview leaves no audit record or decision log), then ask again
    if the reader closed it and Feed Chef is still on."""
    client = pagenav.client_of(parent)
    if client is None:
        present_import(parent, account, recipe)
        return
    preview = FeedChefPreviewDialog(client, account, copy.deepcopy(recipe))
    def back(_dialog):
        if app_settings().get_boolean('feed-chef-enabled'):
            present_import(parent, account, recipe)

    preview.connect('closed', back)
    preview.present(parent)
