# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef recipe builder: an Adw.Dialog that edits a copy of a recipe."""

import copy
import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import accounts, pages  # noqa: E402
from .api import lists as api_lists  # noqa: E402
from .feedback import ToastDialog  # noqa: E402
from .feedchef import deps  # noqa: E402
from .feedchef.cluster import STRATEGIES, strategy_of  # noqa: E402,F401
from .feedchef.icons import ICONS, icon_name  # noqa: E402
from .feedchef.storage import FeedRecipeStorage  # noqa: E402
from .feedchefbuilderlive import LiveSummary  # noqa: E402
from .feedchefmodes import ModesMixin  # noqa: E402
from .feedchefmodifiers import modifier_editors  # noqa: E402
from .feedchefmodifiers import spin_row as _spin  # noqa: E402
from .feedchefpreview import (CloseWhenFeedChefOff,  # noqa: E402
                              FeedChefPreviewDialog)
from .feedchefshare import FeedChefShareDialog  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

log = logging.getLogger('babble.feedchef')

SCOPES = ('home', 'local', 'federated', 'list')
ORDERS = ('score', 'chronological')


def scope_label(scope):
    """The reader's name for a pantry scope."""
    return {'home': _('Home'), 'local': _('Local'),
            'federated': _('Federated'), 'list': _('List')}.get(scope, scope)


def icon_label(enum):
    """The reader's name for a recipe icon; empty for an unknown one."""
    return {
        'PIZZA': _('Food'), 'HEART': _('Heart'), 'STAR': _('Star'),
        'PEOPLE': _('People'), 'NEWS': _('News'), 'IMAGE': _('Images'),
        'MUSIC': _('Music'), 'GAMES': _('Games'), 'TAG': _('Hashtag'),
        'PIN': _('Pin'), 'WEATHER': _('Weather'), 'LEAVES': _('Nature'),
        'SPORT': _('Sports'), 'TRAIN': _('Travel'), 'LAUGH': _('Humor'),
        'SHIELD': _('Safety'), 'IMPORTANT': _('Important'),
        'CODE': _('Code'), 'MICROPHONE': _('Audio'), 'BOOK': _('Books'),
    }.get(enum, '')


def current_icon_label(enum):
    """The label for what a recipe's icon shows: the chef hat stands in
    for a name that is not in the table."""
    return icon_label(str(enum or '').strip().upper()) or _('Chef Hat')


def storage_for(account):
    return FeedRecipeStorage(accounts.account_key(account))


def _words(text, strip_hash=False):
    parts = [part.lower() for part in text.split() if part.strip()]
    return [part.lstrip('#') for part in parts] if strip_hash else parts


def _unsupported_names(recipe):
    names = {'connection': _('connections')}
    return [names.get(kind, kind) for kind in recipe.inert_modifiers()]


class FeedChefBuilderDialog(ModesMixin, LiveSummary, CloseWhenFeedChefOff,
                           ToastDialog):
    """Edits a copy of a recipe. Cancel drops the copy; Save stores it,
    emits ``saved`` and closes. A new recipe is not stored before Save."""

    __gtype_name__ = 'BabbleFeedChefBuilderDialog'
    __gsignals__ = {'saved': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, client, account, recipe, is_new=False):
        super().__init__(
            _('New Recipe') if is_new else _('Edit Recipe'),
            width=520, height=720, title_buttons=False)
        self.client = client
        self.account = account
        self.storage = storage_for(account)
        self.recipe = copy.deepcopy(recipe)
        self.saved = False
        self._lists = None
        self._lists_loading = False
        self._list_ids = []
        self._initial_list_id = (str(recipe.pantry.list_id)
                                 if recipe.pantry.list_id else None)
        # The "Discard Changes?" alert while it is on screen.
        self.discard_alert = None
        self._syncing = False
        self._icon = recipe.meta.icon
        self.save_button = self.add_header_buttons(
            _('_Cancel'), _('_Save'), self._on_save,
            css='suggested-action')
        page = self.page
        recipe = self.recipe
        self.build_summary_group(page)
        self._build_recipe_group(page, recipe)
        self._build_pantry_group(page, recipe)
        self._build_diet_group(page, recipe)
        self.build_modes_group(page)
        self._build_kitchen_group(page, recipe)
        actions = Adw.PreferencesGroup()
        # An action row, not a button row: its subtitle carries the live
        # count ("12 of 40 posts match").
        self.preview_row = Adw.ActionRow(
            title=_('_Preview Recipe'), use_underline=True, activatable=True)
        self.preview_row.add_prefix(Gtk.Image.new_from_icon_name(
            'view-reveal-symbolic'))
        self.preview_row.add_suffix(Gtk.Image.new_from_icon_name(
            'go-next-symbolic'))
        connect_weak(self.preview_row, 'activated', self._on_preview)
        actions.add(self.preview_row)
        row = Adw.ButtonRow(title=_('S_hare Recipe'),
                            use_underline=True,
                            start_icon_name='send-to-symbolic')
        connect_weak(row, 'activated', self._on_share)
        actions.add(row)
        page.add(actions)
        self._sync_scope()
        self.watch_feed_chef_key()
        self._sync_subtitles()
        self.sync_mode(force=True)
        self.submit_on_enter(self.save_button, self.name_row, self.include,
                             self.exclude, self.blocklist)
        # Taken after the widgets hold the recipe's values: the baseline
        # that "unsaved changes" is measured against.
        self._initial = self._state()
        self._watch()
        self._sync_save()
        self.start_summary()
        connect_weak(self, 'close-attempt', self._on_close_attempt)

    def _build_recipe_group(self, page, recipe):
        group = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title=_('Name'))
        self.name_row.set_text(recipe.meta.name)
        group.add(self.name_row)
        group.add(self._build_icon_row())
        page.add(group)

    def _build_icon_row(self):
        """The recipe's icon: shown as the row's prefix, chosen from the
        table of icons in a popover."""
        row = Adw.ActionRow(title=_('Icon'),
                            subtitle=current_icon_label(self._icon))
        self.icon_row = row
        # Decorative: the row's subtitle says what is chosen.
        self.icon_image = Gtk.Image(
            icon_name=icon_name(self._icon),
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        row.add_prefix(self.icon_image)
        actions = Gio.SimpleActionGroup()
        choose = Gio.SimpleAction.new_stateful(
            'choose', GLib.VariantType.new('s'),
            GLib.Variant.new_string(self._icon.strip().upper()))
        connect_weak(choose, 'activate', self._on_icon_chosen)
        actions.add_action(choose)
        self.insert_action_group('icon', actions)
        grid = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
            min_children_per_line=4, max_children_per_line=6,
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        for enum, icon in ICONS:
            button = Gtk.ToggleButton(
                icon_name=icon, tooltip_text=icon_label(enum),
                action_name='icon.choose',
                action_target=GLib.Variant.new_string(enum))
            button.add_css_class('flat')
            set_accessible_label(button, icon_label(enum))
            grid.append(button)
        self.icon_grid = grid
        self.icon_menu = Gtk.MenuButton(
            icon_name='pan-down-symbolic', valign=Gtk.Align.CENTER,
            popover=Gtk.Popover(child=grid))
        self.icon_menu.add_css_class('flat')
        set_accessible_label(self.icon_menu, _('Choose Icon'))
        row.add_suffix(self.icon_menu)
        row.set_activatable_widget(self.icon_menu)
        return row

    def _on_icon_chosen(self, action, parameter):
        self._icon = parameter.get_string()
        action.set_state(parameter)
        self.icon_image.set_from_icon_name(icon_name(self._icon))
        self.icon_row.set_subtitle(current_icon_label(self._icon))
        self.icon_menu.popdown()
        self._on_edited()

    def _build_pantry_group(self, page, recipe):
        pantry = recipe.pantry
        group = Adw.PreferencesGroup(
            title=_('Pantry'),
            description=_('Where posts come from. Separate hashtags with '
                          'spaces. With included hashtags, only posts with '
                          'at least one of them show; a post with an '
                          'excluded hashtag never does.'))
        self.scope = Adw.ComboRow(
            title=_('Posts From'),
            model=Gtk.StringList.new([scope_label(s) for s in SCOPES]))
        self.scope.set_selected(
            SCOPES.index(pantry.scope) if pantry.scope in SCOPES else 0)
        connect_weak(self.scope, 'notify::selected', self._on_scope_changed)
        group.add(self.scope)
        self.list_row = Adw.ComboRow(title=_('List'))
        group.add(self.list_row)
        self.include = Adw.EntryRow(title=_('Include Hashtags'))
        self.include.set_text(' '.join(pantry.hashtags_include))
        group.add(self.include)
        self.exclude = Adw.EntryRow(title=_('Exclude Hashtags'))
        self.exclude.set_text(' '.join(pantry.hashtags_exclude))
        group.add(self.exclude)
        self.reach = Adw.SwitchRow(
            title=_('Only People I Follow'),
            subtitle=_('Plus your own posts. Boosts are judged by the '
                       'original author.'))
        self.reach.set_active(pantry.network_reach.enabled)
        self._reach_initial = self.reach.get_active()
        group.add(self.reach)
        page.add(group)

    def _build_diet_group(self, page, recipe):
        hard = recipe.diet.hard_filters
        group = Adw.PreferencesGroup(
            title=_('Diet'),
            description=_('Separate words with spaces. Whole words only, '
                          'in the post and its content warning, ignoring '
                          'case.'))
        self.blocklist = Adw.EntryRow(title=_('Blocked Words'))
        self.blocklist.set_text(' '.join(recipe.diet.keyword_blocklist))
        # What the field showed when the dialog opened: while it still
        # shows that, the recipe's own entries stand (an imported entry of
        # several words would be split into words by re-reading it).
        self._blocklist_text = self.blocklist.get_text()
        group.add(self.blocklist)
        self.media = Adw.SwitchRow(title=_('Only Posts With Media'))
        self.media.set_active(hard.must_have_media)
        group.add(self.media)
        self.alt = Adw.SwitchRow(
            title=_('Require Alt Text on Media'),
            subtitle=_('Posts without media still show'))
        self.alt.set_active(hard.must_have_alt_text)
        group.add(self.alt)
        self.sensitive = Adw.SwitchRow(
            title=_('Hide Posts Marked Sensitive'),
            subtitle=_('Hides posts flagged as sensitive, often those with '
                       'a content warning'))
        self.sensitive.set_active(hard.no_sensitive_media)
        group.add(self.sensitive)
        self.positive = Adw.SwitchRow(
            title=_('Only Positive Posts'),
            subtitle=_('Hides English and Portuguese posts that read as '
                       'negative'))
        self.positive.set_active(hard.positive_only)
        group.add(self.positive)
        self.min_length = _spin(
            _('Minimum Length'),
            _('In characters of text, links counted in full; 0 turns '
              'it off'),
            float(hard.min_content_length or 0), 0, 5000, 10)
        group.add(self.min_length)
        page.add(group)

    def _build_kitchen_group(self, page, recipe):
        group = Adw.PreferencesGroup(
            title=_('Kitchen'),
            description=_('How posts are ordered and balanced'))
        self.order = Adw.ComboRow(
            title=_('Order'),
            model=Gtk.StringList.new(
                [_('Most engaging'), _('Newest first')]))
        self.order.set_selected(
            1 if recipe.kitchen.base_sort == 'chronological' else 0)
        group.add(self.order)
        self.editors = modifier_editors()
        # Grouping needs two optional packages; whether they are there is
        # asked without importing them (this runs on the main thread).
        missing = deps.unavailable_reason('numpy', 'kmedoids')
        for editor in self.editors:
            if missing and editor.kind == 'similarity_cluster':
                editor.block(missing)
        for editor in self.editors:
            editor.load(recipe)
            group.add(editor.row)
        unsupported = _unsupported_names(recipe)
        if unsupported:
            row = Adw.ActionRow(
                title=_('Some Ingredients Are Not Supported Yet'),
                # Translators: %s is a list of features, such as
                # "similarity grouping, connections".
                subtitle=_('Not applied: %s') % ', '.join(unsupported))
            row.add_prefix(Gtk.Image.new_from_icon_name(
                'dialog-information-symbolic'))
            group.add(row)
        page.add(group)

    # Lists

    def _on_scope_changed(self, _row, _pspec):
        self._sync_scope()
        self._on_edited()

    def _selected_scope(self):
        return SCOPES[self.scope.get_selected()]

    def _sync_scope(self):
        is_list = self._selected_scope() == 'list'
        self.list_row.set_visible(is_list)
        if is_list and self._lists is None and not self._lists_loading:
            self._load_lists()
        elif is_list:
            self._show_lists()

    def _load_lists(self):
        self._lists_loading = True
        self.list_row.set_model(Gtk.StringList.new([_('Loading…')]))
        self.list_row.set_sensitive(False)
        self._sync_save()
        client = self.client
        pages.call_api(
            self, client,
            lambda cb: api_lists.index_async(client, callback=cb),
            self._lists_loaded, _('Could not load lists'),
            finish=self._finish_lists)

    def _finish_lists(self, client, result):
        try:
            return api_lists.index_finish(client, result)
        except GLib.Error:
            self._lists_loading = False
            self.list_row.set_model(Gtk.StringList.new([]))
            raise

    def _lists_loaded(self, data):
        self._lists_loading = False
        self._lists = [item for item in data or [] if item.get('id')]
        clean = self.get_can_close()
        self._syncing = True
        try:
            self._show_lists()
        finally:
            self._syncing = False
        if clean:
            # The row's own choice of its first item is not an edit.
            self._initial = self._state()
        self._on_edited()

    def _show_lists(self):
        lists = self._lists or []
        self._list_ids = [str(item['id']) for item in lists]
        self.list_row.set_model(Gtk.StringList.new(
            [str(item.get('title') or '') for item in lists]))
        self.list_row.set_sensitive(bool(lists))
        if not lists:
            # An empty combo row reads as broken: say why there is nothing
            # to pick, and that Save waits for a list.
            self.list_row.set_model(Gtk.StringList.new([_('No lists')]))
            self.list_row.set_subtitle(_('Create a list first'))
            self.list_row.set_selected(0)
            return
        self.list_row.set_subtitle('')
        # A combo row always shows an item once it has some, so a recipe
        # without a list (or whose list is gone) shows the first one: what
        # the reader sees is what Save stores.
        current = self._initial_list_id
        if current is not None and current in self._list_ids:
            self.list_row.set_selected(self._list_ids.index(current))

    def _selected_list_id(self):
        """The list the recipe would use, or None. Until the account's
        lists arrive the recipe's own list stands, so that opening the
        dialog is not an edit."""
        if self._lists is None:
            return self._initial_list_id
        selected = self.list_row.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION \
                or selected >= len(self._list_ids):
            return None
        return self._list_ids[selected]

    # Unsaved changes

    def _state(self):
        """Everything the reader can edit, comparable with ``==``."""
        return (
            self.name_row.get_text().strip(), self._selected_scope(),
            self._selected_list_id(),
            _words(self.include.get_text(), True),
            _words(self.exclude.get_text(), True),
            self.reach.get_active(), _words(self.blocklist.get_text()),
            self.media.get_active(), self.alt.get_active(),
            self.sensitive.get_active(), self.positive.get_active(),
            self.min_length.get_value(), self._icon,
            self.order.get_selected(),
            tuple(editor.state() for editor in self.editors))

    def _watch(self):
        for row in (self.name_row, self.include, self.exclude,
                    self.blocklist):
            connect_weak(row, 'changed', self._on_edited)
        for row in (self.reach, self.media, self.alt, self.sensitive,
                    self.positive):
            connect_weak(row, 'notify::active', self._on_edited)
        spins = [self.min_length] + [e.spin for e in self.editors]
        for row in spins:
            connect_weak(row, 'notify::value', self._on_edited)
        for row in (self.list_row, self.order):
            connect_weak(row, 'notify::selected', self._on_edited)
        for editor in self.editors:
            connect_weak(editor.row, 'notify::enable-expansion',
                         self._on_edited)
            for extra in editor.extras:
                connect_weak(extra.widget, 'notify::active'
                             if isinstance(extra.widget, Adw.SwitchRow)
                             else 'notify::selected', self._on_edited)
        for row in spins[1:]:
            connect_weak(row, 'notify::value', self._sync_subtitles)
        for editor in self.editors:
            connect_weak(editor.row, 'notify::enable-expansion',
                         self._sync_subtitles)

    def _sync_subtitles(self, *_args):
        for editor in self.editors:
            editor.sync_subtitle()

    def _on_edited(self, *_args):
        if self._syncing:
            return
        self._sync_save()
        self.summary_changed()
        if not self._mode_syncing:
            self.sync_mode()
        # With can-close off, closing asks first (close-attempt).
        self.set_can_close(self._state() == self._initial)

    def _sync_save(self):
        """Save waits for a list while the scope is List."""
        ready = (self._selected_scope() != 'list'
                 or bool(self._selected_list_id()))
        self.save_button.set_sensitive(ready)
        self.preview_row.set_sensitive(ready)

    def _on_cancel(self, _button):
        # Through close(), so that unsaved edits are asked about.
        self.close()

    def _on_close_attempt(self, _dialog):
        if self.discard_alert is not None:
            return
        alert = Adw.AlertDialog(
            heading=_('Discard Changes?'),
            body=_('Your changes to this recipe will be lost.'))
        alert.add_response('keep', _('_Keep Editing'))
        alert.add_response('discard', _('_Discard'))
        alert.set_response_appearance('discard',
                                      Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response('keep')
        alert.set_close_response('keep')
        connect_weak(alert, 'response', self._on_discard_response)
        self.discard_alert = alert
        alert.present(self)

    def _on_discard_response(self, _alert, response):
        self.discard_alert = None
        if response == 'discard':
            self.force_close()

    # Collecting

    def _collect(self):
        recipe = self.recipe
        recipe.meta.name = self.name_row.get_text().strip() or _(
            'Untitled Recipe')
        recipe.meta.icon = self._icon
        pantry = recipe.pantry
        pantry.scope = self._selected_scope()
        selected = self.list_row.get_selected()
        if pantry.scope == 'list' and selected != Gtk.INVALID_LIST_POSITION \
                and selected < len(self._list_ids):
            pantry.list_id = self._list_ids[selected]
        pantry.hashtags_include = _words(self.include.get_text(), True)
        pantry.hashtags_exclude = _words(self.exclude.get_text(), True)
        reach = self.reach.get_active()
        if reach != self._reach_initial:
            pantry.network_reach.enabled = reach
            if reach:
                pantry.network_reach.max_degrees = 1
        if self.blocklist.get_text() != self._blocklist_text:
            recipe.diet.keyword_blocklist = _words(self.blocklist.get_text())
        hard = recipe.diet.hard_filters
        hard.must_have_media = self.media.get_active()
        hard.must_have_alt_text = self.alt.get_active()
        hard.no_sensitive_media = self.sensitive.get_active()
        hard.positive_only = self.positive.get_active()
        hard.min_content_length = int(self.min_length.get_value())
        recipe.kitchen.base_sort = ORDERS[self.order.get_selected()]
        for editor in self.editors:
            editor.apply(recipe)
        return recipe

    def _on_save(self, _button):
        recipe = self._collect()
        try:
            self.storage.save_recipe(recipe)
        except Exception as error:
            self.error(_('Could not save the recipe'), error)
            return
        self.saved = True
        self.emit('saved')
        self.force_close()

    def _on_preview(self, _row):
        """Cook what is on screen, saved or not: a copy goes to the
        preview, so it cannot change the recipe being edited."""
        recipe = copy.deepcopy(self._collect())
        FeedChefPreviewDialog(self.client, self.account,
                              recipe).present(self)

    def _on_share(self, _row):
        """Share Recipe: the link, Copy Link and the QR code of what is on
        screen, saved or not."""
        FeedChefShareDialog(copy.deepcopy(self._collect()),
                            self.account).present(self)
