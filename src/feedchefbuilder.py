# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef recipe builder: an Adw.Dialog that edits a copy of a recipe."""

import copy

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from . import accounts, pages  # noqa: E402
from .api import lists as api_lists  # noqa: E402
from .feedback import ToastDialog  # noqa: E402
from .feedchef.recipe import Modifier  # noqa: E402
from .feedchef.sharecodec import recipe_share_uri  # noqa: E402
from .feedchef.storage import FeedRecipeStorage  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

SCOPES = ('home', 'local', 'federated', 'list')
ORDERS = ('score', 'chronological')


def scope_label(scope):
    """The reader's name for a pantry scope."""
    return {'home': _('Home'), 'local': _('Local'),
            'federated': _('Federated'), 'list': _('List')}.get(scope, scope)


def storage_for(account):
    return FeedRecipeStorage(accounts.account_key(account))


def _words(text, strip_hash=False):
    parts = [part.lower() for part in text.split() if part.strip()]
    return [part.lstrip('#') for part in parts] if strip_hash else parts


def _spin(title, subtitle, value, lower, upper, step, digits=0):
    return Adw.SpinRow(
        title=title, subtitle=subtitle, digits=digits,
        adjustment=Gtk.Adjustment(value=value, lower=lower, upper=upper,
                                  step_increment=step))


class _ModifierEditor:
    """An expander with an enable switch for one kitchen modifier.

    On: the modifier is in the recipe. Writing back touches only what the
    reader changed: an enabled modifier keeps its place in the list and
    its other fields, a value the spin row still shows as it was read is
    not rewritten, and a modifier of another type is never looked at."""

    def __init__(self, kind, title, subtitle, spin, read, write_value):
        self.kind = kind
        self._read = read
        self._write = write_value
        self.row = Adw.ExpanderRow(title=title, subtitle=subtitle,
                                   show_enable_switch=True)
        self.spin = spin
        self.row.add_row(spin)
        self.initial = spin.get_value()

    def load(self, recipe):
        mod = next((m for m in recipe.kitchen.modifiers
                    if m.type == self.kind), None)
        self.present = mod is not None
        if mod is not None:
            self.spin.set_value(self._read(mod))
        self.initial = self.spin.get_value()
        self.row.set_enable_expansion(self.present)
        self.row.set_expanded(False)

    def apply(self, recipe):
        modifiers = recipe.kitchen.modifiers
        wanted = self.row.get_enable_expansion()
        if not wanted:
            modifiers[:] = [m for m in modifiers if m.type != self.kind]
            return
        mod = next((m for m in modifiers if m.type == self.kind), None)
        if mod is None:
            mod = Modifier(type=self.kind)
            modifiers.append(mod)
        elif self.spin.get_value() == self.initial:
            return
        self._write(mod, self.spin.get_value())


def _modifier_editors():
    def fresh_write(mod, value):
        mod.decay_minutes = int(round(value)) * 60

    def heat_write(mod, value):
        mod.percentile_threshold = int(round(value))

    def diversity_write(mod, value):
        mod.max_per_cluster = int(round(value))
        mod.diversity_dimensions = ['author']

    def trending_write(mod, value):
        mod.growth_threshold = round(value, 1)

    def clamp(value, low, high):
        return max(low, min(high, value))

    return [
        _ModifierEditor(
            'freshness', _('Favor Fresh Posts'),
            _('Newer posts rank higher'),
            _spin(_('Fade-Out Time'), _('In hours'), 6, 1, 48, 1),
            lambda m: clamp(round((m.decay_minutes or 360) / 60), 1, 48),
            fresh_write),
        _ModifierEditor(
            'heat', _('Hide the Least Engaging'),
            _('Drops the posts people reacted to least'),
            _spin(_('Percent to Hide'), _('Of the posts in each batch'),
                  50, 0, 90, 5),
            lambda m: clamp(m.percentile_threshold
                            if m.percentile_threshold is not None else 50,
                            0, 90),
            heat_write),
        _ModifierEditor(
            'diversity', _('Limit Posts per Person'),
            _('Keeps one voice from filling the feed'),
            _spin(_('Posts per Person'), _('In each batch'), 3, 1, 10, 1),
            lambda m: clamp(m.max_per_cluster or 3, 1, 10),
            diversity_write),
        _ModifierEditor(
            'trending', _('Boost Trending Topics'),
            _('Posts about what is rising on the server rank higher'),
            _spin(_('Minimum Growth'), _('How fast a topic must rise'),
                  0.3, 0.1, 1.0, 0.1, digits=1),
            lambda m: clamp(m.growth_threshold or 0.3, 0.1, 1.0),
            trending_write),
    ]


def _unsupported_names(recipe):
    names = {
        'similarity_cluster': _('similarity grouping'),
        'sentiment_boost': _('sentiment'),
        'connection': _('connections'),
        'positive_only': _('positive posts only'),
        'active_focus_keywords': _('sorting by focus keywords'),
    }
    return [names.get(kind, kind) for kind in recipe.inert_modifiers()]


class FeedChefBuilderDialog(ToastDialog):
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
        self.save_button = self.add_header_buttons(
            _('_Cancel'), _('_Save'), self._on_save,
            css='suggested-action')
        page = self.page
        recipe = self.recipe
        self._build_recipe_group(page, recipe)
        self._build_pantry_group(page, recipe)
        self._build_diet_group(page, recipe)
        self._build_kitchen_group(page, recipe)
        share = Adw.PreferencesGroup()
        row = Adw.ButtonRow(title=_('Copy Share _Link'),
                            use_underline=True)
        connect_weak(row, 'activated', self._on_share)
        share.add(row)
        page.add(share)
        self._sync_scope()
        self.submit_on_enter(self.save_button, self.name_row, self.include,
                             self.exclude, self.blocklist)
        # Taken after the widgets hold the recipe's values: the baseline
        # that "unsaved changes" is measured against.
        self._initial = self._state()
        self._watch()
        self._sync_save()
        connect_weak(self, 'close-attempt', self._on_close_attempt)

    def _build_recipe_group(self, page, recipe):
        group = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title=_('Name'))
        self.name_row.set_text(recipe.meta.name)
        group.add(self.name_row)
        page.add(group)

    def _build_pantry_group(self, page, recipe):
        pantry = recipe.pantry
        group = Adw.PreferencesGroup(
            title=_('Pantry'),
            description=_('Where posts come from. Separate hashtags with '
                          'spaces.'))
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
        self.reach = Adw.SwitchRow(title=_('Only People I Follow'),
                                   subtitle=_('Plus your own posts'))
        self.reach.set_active(pantry.network_reach.enabled)
        self._reach_initial = self.reach.get_active()
        group.add(self.reach)
        page.add(group)

    def _build_diet_group(self, page, recipe):
        hard = recipe.diet.hard_filters
        group = Adw.PreferencesGroup(
            title=_('Diet'),
            description=_('Separate words with spaces.'))
        self.blocklist = Adw.EntryRow(title=_('Blocked Words'))
        self.blocklist.set_text(' '.join(recipe.diet.keyword_blocklist))
        group.add(self.blocklist)
        self.media = Adw.SwitchRow(title=_('Only Posts With Media'))
        self.media.set_active(hard.must_have_media)
        group.add(self.media)
        self.alt = Adw.SwitchRow(title=_('Only Posts With Alt Text'))
        self.alt.set_active(hard.must_have_alt_text)
        group.add(self.alt)
        self.sensitive = Adw.SwitchRow(title=_('Hide Sensitive Media'))
        self.sensitive.set_active(hard.no_sensitive_media)
        group.add(self.sensitive)
        self.min_length = _spin(
            _('Minimum Length'), _('In characters; 0 turns it off'),
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
        self.editors = _modifier_editors()
        for editor in self.editors:
            editor.load(recipe)
            group.add(editor.row)
        unsupported = _unsupported_names(recipe)
        if unsupported:
            row = Adw.ActionRow(
                title=_('Some Ingredients Are Not Supported Yet'),
                # Translators: %s is a list of features, such as
                # "sentiment, connections".
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
            self.sensitive.get_active(), self.min_length.get_value(),
            self.order.get_selected(),
            tuple((editor.row.get_enable_expansion(),
                   editor.spin.get_value()) for editor in self.editors))

    def _watch(self):
        for row in (self.name_row, self.include, self.exclude,
                    self.blocklist):
            connect_weak(row, 'changed', self._on_edited)
        for row in (self.reach, self.media, self.alt, self.sensitive):
            connect_weak(row, 'notify::active', self._on_edited)
        spins = [self.min_length] + [e.spin for e in self.editors]
        for row in spins:
            connect_weak(row, 'notify::value', self._on_edited)
        for row in (self.list_row, self.order):
            connect_weak(row, 'notify::selected', self._on_edited)
        for editor in self.editors:
            connect_weak(editor.row, 'notify::enable-expansion',
                         self._on_edited)

    def _on_edited(self, *_args):
        if self._syncing:
            return
        self._sync_save()
        # With can-close off, closing asks first (close-attempt).
        self.set_can_close(self._state() == self._initial)

    def _sync_save(self):
        """Save waits for a list while the scope is List."""
        self.save_button.set_sensitive(
            self._selected_scope() != 'list'
            or bool(self._selected_list_id()))

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
        recipe.diet.keyword_blocklist = _words(self.blocklist.get_text())
        hard = recipe.diet.hard_filters
        hard.must_have_media = self.media.get_active()
        hard.must_have_alt_text = self.alt.get_active()
        hard.no_sensitive_media = self.sensitive.get_active()
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

    def _on_share(self, _row):
        uri = recipe_share_uri(self._collect())
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(uri)
        self.toast(_('Share link copied'))
