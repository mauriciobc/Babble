# SPDX-License-Identifier: GPL-3.0-or-later
"""Control construction and handlers for the Catch Up page."""

import dataclasses

import functools

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from ..i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import pages  # noqa: E402
from . import store as catchupstore  # noqa: E402
from ..accounts import plain_name as _plain_name  # noqa: E402
from .digest_layout import SORTS  # noqa: E402
from .pipeline import (  # noqa: E402
    CATEGORIES, DEFAULT_PERIOD, LOW_ACTIVITY_PERIOD, MAX_CHIPS, TOAST_DELAY_MS,
    ViewState, category_name, derive_view, period_label, period_menu_label,
    snapshot_label, sort_name, view_summary)
from .rows import CatchUpRow, PostsBar, _AuthorChip  # noqa: E402

class PageControls:
    """Methods for the page's controls; state remains owned by CatchUpPage."""
    def _build_first_run(self):
        page = Adw.StatusPage(
            icon_name='babble-catch-up-symbolic', title=_('Catch Up'),
            description=_('A separate timeline for your followings, '
                          'offering a high-level view at a glance'))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)
        box.append(self._make_period_button())
        button = Gtk.Button(label=_('_Catch Up'), use_underline=True,
                            halign=Gtk.Align.CENTER)
        button.add_css_class('suggested-action')
        button.add_css_class('pill')
        pages.connect_weak(button, 'clicked', self._on_first_run_clicked)
        box.append(button)
        page.set_child(box)
        self.first_run_page = page
        self.catch_up_button = button
        return page

    def _build_scanning(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(width_request=48, height_request=48,
                                   halign=Gtk.Align.CENTER)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            halign=Gtk.Align.CENTER)
        self.scan_title = Gtk.Label(label=_('Scanning timeline…'),
                                    wrap=True,
                                    justify=Gtk.Justification.CENTER)
        self.scan_title.add_css_class('title-3')
        self.scan_count = Gtk.Label(
            label='', wrap=True, justify=Gtk.Justification.CENTER)
        self.scan_hours = Gtk.Label(
            label='', wrap=True, justify=Gtk.Justification.CENTER)
        self.scan_hours.add_css_class('dimmed')
        label_box.append(self.scan_title)
        label_box.append(self.scan_count)
        label_box.append(self.scan_hours)

        self.cancel_button = Gtk.Button(label=_('_Cancel'),
                                        use_underline=True,
                                        halign=Gtk.Align.CENTER)
        self.cancel_button.add_css_class('pill')
        pages.connect_weak(self.cancel_button, 'clicked',
                           self._on_cancel_clicked)
        box.append(self.spinner)
        box.append(label_box)
        box.append(self.cancel_button)
        return box

    # ----------------------------------------------------------------------
    # The period menu (2.2)

    def _period_choices(self):
        choices = ['1h', '3h', '6h', '12h', '24h']
        if self._metas:
            choices.append('since')
        choices.append('max')
        return choices

    def _make_period_button(self):
        button = Gtk.MenuButton(always_show_arrow=True,
                                tooltip_text=_('Time Range'),
                                valign=Gtk.Align.CENTER)
        button.set_label(period_label(self._period))
        self._period_buttons.append(button)
        self._fill_period_popover(button)
        return button

    def _fill_period_popover(self, button):
        menu = Gio.Menu()
        periods = Gio.Menu()
        for period in self._period_choices():
            item = Gio.MenuItem.new(period_menu_label(period), None)
            item.set_action_and_target_value(
                'catchup.period', GLib.Variant('s', period))
            periods.append_item(item)
        menu.append_section(None, periods)
        if self._metas:
            previous = Gio.Menu()
            for meta in self._metas:
                item = Gio.MenuItem.new(None, None)
                item.set_attribute_value(
                    'custom', GLib.Variant('s', 'snapshot:' + meta['id']))
                previous.append_item(item)
            menu.append_section(_('Previous catch-ups'), previous)
        footer = Gio.Menu()
        item = Gio.MenuItem.new(None, None)
        item.set_attribute_value('custom', GLib.Variant('s', 'footnote'))
        footer.append_item(item)
        menu.append_section(None, footer)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        for meta in self._metas:
            popover.add_child(self._snapshot_entry(meta),
                              'snapshot:' + meta['id'])
        note = Gtk.Label(
            label=_('Your server might only show a maximum of 800 posts '
                    'in the Home timeline regardless of the time range.'),
            wrap=True, max_width_chars=34, xalign=0.0, margin_start=12,
            margin_end=12, margin_top=6, margin_bottom=6, sensitive=False)
        note.add_css_class('caption')
        popover.add_child(note, 'footnote')
        button.set_popover(popover)

    def _snapshot_entry(self, meta):
        box = Gtk.Box(spacing=0)
        show = Gtk.Button(hexpand=True)
        show.add_css_class('flat')
        label = Gtk.Label(label=snapshot_label(meta), xalign=0.0,
                          wrap=True, max_width_chars=30)
        show.set_child(label)
        show.set_action_name('catchup.show')
        show.set_action_target_value(GLib.Variant('s', meta['id']))
        remove = Gtk.Button(icon_name='user-trash-symbolic',
                            valign=Gtk.Align.CENTER)
        remove.add_css_class('flat')
        pages.set_accessible_label(remove, _('Remove This Catch-Up'))
        remove.set_action_name('catchup.remove')
        remove.set_action_target_value(GLib.Variant('s', meta['id']))
        show.set_sensitive(meta['id'] != self._snapshot_id)
        box.append(show)
        box.append(remove)
        return box

    def _refresh_period_menus(self):
        for button in self._period_buttons:
            self._fill_period_popover(button)
            button.set_label(period_label(self._period))
        self._period_action.set_state(GLib.Variant('s', self._period))

    def _popdown_menus(self):
        for button in self._period_buttons:
            button.popdown()

    def _on_period_changed(self, action, value):
        period = value.get_string()
        if self._run is not None or self.state in ('loading', 'scanning'):
            return
        if self.state == 'results' and period == self._period:
            return                        # the period already shown
        if period == 'since' and not self._metas:
            return
        action.set_state(value)
        self._period = period
        self._refresh_period_menus()
        if self.state in ('results', 'error'):
            self._start_run(period)

    def _on_first_run_clicked(self, _button):
        self._start_run(self._period)

    def _on_retry(self, _button):
        self._start_run(self._run_period)

    def _on_cancel_clicked(self, _button):
        if self._run is not None:
            self.cancel_button.set_sensitive(False)
            self._run.stop()

    def _on_show_snapshot(self, _action, parameter):
        self._popdown_menus()
        snapshot_id = parameter.get_string()
        if snapshot_id == self._snapshot_id or self._run is not None:
            return
        self._open_snapshot(snapshot_id)

    def _on_remove_snapshot(self, _action, parameter):
        self._popdown_menus()
        pages.confirm(
            self, _('Remove This Catch-Up?'),
            _('It will be deleted from this device.'), _('_Remove'),
            functools.partial(pages.weak_method(self._remove_snapshot),
                              parameter.get_string()))

    def _remove_snapshot(self, snapshot_id):
        if self._disposed:
            return
        catchupstore.delete_snapshot(self.account_key, snapshot_id)
        self._view_states.pop(snapshot_id, None)
        self._metas = [m for m in self._metas if m['id'] != snapshot_id]
        if snapshot_id != self._snapshot_id:
            self._refresh_period_menus()
            return
        self._snapshot_id = None
        if self._metas:
            self._open_snapshot(self._metas[0]['id'])
        else:
            self._computed = None
            self._period = DEFAULT_PERIOD
            self._refresh_period_menus()
            self._set_state('first-run')
    def _build_top(self):
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=6, margin_start=12,
                      margin_end=12)
        self.top = top

        header = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        hexpand=True)
        self.title_label = Gtk.Label(xalign=0.0, wrap=True)
        self.title_label.add_css_class('title-1')
        self.subtitle_label = Gtk.Label(
            xalign=0.0, wrap=True,
            label=_('The most relevant posts from your network, chosen by '
                    'an algorithm that weighs engagement, reach and '
                    'relevance.'))
        self.subtitle_label.add_css_class('dimmed')
        texts.append(self.title_label)
        texts.append(self.subtitle_label)
        header.append(texts)
        controls = Gtk.Box(spacing=6, valign=Gtk.Align.START)
        controls.append(self._make_period_button())
        self.options_button = Gtk.MenuButton(
            icon_name='babble-filter-symbolic',
            valign=Gtk.Align.CENTER)
        pages.set_accessible_label(self.options_button, _('List Options'))
        self.options_button.set_popover(self._build_options())
        controls.append(self.options_button)
        header.append(controls)
        top.append(header)

        self.note_label = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        self.note_label.add_css_class('dimmed')
        top.append(self.note_label)
        self.low_banner = Adw.Banner(revealed=False)
        self.low_banner.set_action_name('catchup.period')
        self.low_banner.set_action_target_value(
            GLib.Variant('s', LOW_ACTIVITY_PERIOD))
        top.append(self.low_banner)
        self.limit_banner = Adw.Banner(revealed=False)
        top.append(self.limit_banner)
        self.save_banner = Adw.Banner(revealed=False)
        self.save_banner.set_title(_(
            'This catch-up could not be saved, so it will not be here '
            'next time.'))
        top.append(self.save_banner)

        self.highlights_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(self.highlights_slot)

        self.all_heading = Gtk.Label(label=_('All posts'), xalign=0.0,
                                     margin_top=12)
        self.all_heading.add_css_class('title-2')
        top.append(self.all_heading)
        self.bar = PostsBar()
        top.append(self.bar)
        self.chips_box = Gtk.Box(spacing=6)
        self.chips = []
        chips_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True)
        chips_scroll.set_child(self.chips_box)
        self.chips_scroll = chips_scroll
        top.append(chips_scroll)
        self.empty_label = Gtk.Label(label=_('No posts match these filters'),
                                     visible=False, margin_top=12,
                                     margin_bottom=12)
        self.empty_label.add_css_class('dimmed')
        top.append(self.empty_label)

    def _build_options(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12, width_request=320)

        def heading(text):
            """A visible heading that also names the control below it.

            The relation is what gives the control its accessible name:
            a toggle group and two drop-downs carry no label of their
            own, so without it they are three unlabelled controls in a
            popover."""
            label = Gtk.Label(label=text, xalign=0.0)
            label.add_css_class('caption-heading')
            return label

        def labelled(control, label):
            """Name *control* after *label* for assistive technologies.

            The value has to be a GValue holding a Gtk.AccessibleList:
            GTK reads a reference-list relation with g_value_get_pointer,
            so a bare widget lands in a GValue of the wrong type, the
            pointer read asserts, and the relation is dropped instead of
            set. Gtk.AccessibleList.new_from_array passes a length of 0
            through PyGObject, hence new_from_list."""
            refs = Gtk.AccessibleList.new_from_list([label])
            control.update_relation(
                [Gtk.AccessibleRelation.LABELLED_BY],
                [GObject.Value(Gtk.AccessibleList, refs)])
            return control

        category_heading = heading(_('Category'))
        box.append(category_heading)
        self.category_group = labelled(Adw.ToggleGroup(), category_heading)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True)
        scroll.set_child(self.category_group)
        box.append(scroll)
        pages.connect_weak(self.category_group, 'notify::active-name',
                           self._on_category_changed)

        author_heading = heading(_('Author'))
        box.append(author_heading)
        self.author_model = Gtk.StringList()
        self.author_dropdown = labelled(
            Gtk.DropDown(model=self.author_model, enable_search=True),
            author_heading)
        self.author_dropdown.set_expression(Gtk.PropertyExpression.new(
            Gtk.StringObject.__gtype__, None, 'string'))
        pages.connect_weak(self.author_dropdown, 'notify::selected',
                           self._on_author_changed)
        box.append(self.author_dropdown)

        sort_heading = heading(_('Sort by'))
        box.append(sort_heading)
        sort_row = Gtk.Box(spacing=6)
        self.sort_dropdown = labelled(
            Gtk.DropDown(
                model=Gtk.StringList.new([sort_name(s) for s in SORTS]),
                hexpand=True),
            sort_heading)
        pages.connect_weak(self.sort_dropdown, 'notify::selected',
                           self._on_sort_changed)
        sort_row.append(self.sort_dropdown)
        self.order_group = Adw.ToggleGroup()
        ascending = Adw.Toggle(name='asc',
                               icon_name='view-sort-ascending-symbolic',
                               tooltip=_('Ascending'))
        descending = Adw.Toggle(name='desc',
                                icon_name='view-sort-descending-symbolic',
                                tooltip=_('Descending'))
        self.order_group.add(ascending)
        self.order_group.add(descending)
        pages.connect_weak(self.order_group, 'notify::active-name',
                           self._on_order_changed)
        sort_row.append(self.order_group)
        box.append(sort_row)

        group_row = Gtk.Box(spacing=12)
        group_label = Gtk.Label(label=_('Group by author'), xalign=0.0,
                                hexpand=True)
        self.group_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.group_switch.update_property([Gtk.AccessibleProperty.LABEL],
                                          [_('Group by author')])
        pages.connect_weak(self.group_switch, 'notify::active',
                           self._on_group_changed)
        group_row.append(group_label)
        group_row.append(self.group_switch)
        box.append(group_row)

        self.reset_button = Gtk.Button(label=_('_Reset Filters'),
                                       use_underline=True)
        pages.connect_weak(self.reset_button, 'clicked', self._on_reset)
        box.append(self.reset_button)
        popover.set_child(box)
        return popover
    def _rebuild_category_toggles(self):
        counts = self._computed.counts
        self._syncing = True
        self.category_group.remove_all()
        for category in ('all',) + tuple(
                c for c in CATEGORIES if c != 'filtered') + ('filtered',):
            total = len(self._computed.visible) if category == 'all' \
                else counts.get(category, 0)
            if category in ('groups', 'quotes', 'followed_tags',
                            'filtered') and not total:
                continue
            # Translators: a category of the Catch Up list and how many
            # posts it holds, e.g. "Boosts (12)"
            label = _('%(category)s (%(count)d)') % {
                'category': category_name(category), 'count': total}
            self.category_group.add(Adw.Toggle(
                name=category, label=label, enabled=bool(total)))
        self._syncing = False

    def _sync_controls(self):
        state = self._view_state
        self._syncing = True
        known = {self.category_group.get_toggle(i).get_name()
                 for i in range(self.category_group.get_n_toggles())}
        self.category_group.set_active_name(
            state.category if state.category in known else 'all')
        self.sort_dropdown.set_selected(SORTS.index(state.sort))
        self.order_group.set_active_name(state.order)
        self.group_switch.set_active(state.group)
        self._sync_author_menu()
        index = self._author_ids.index(state.author) \
            if state.author in self._author_ids else 0
        self.author_dropdown.set_selected(index)
        for chip in self.chips:
            chip.set_active(chip.account_id == state.author)
        self.reset_button.set_sensitive(not state.is_default())
        self._syncing = False

    def _sync_author_menu(self):
        """The author dropdown and the chips list the authors of the
        category shown, most active first."""
        view = self._view
        if self._menu_category == self._view_state.category \
                and self._menu_for is self._computed:
            return
        self._menu_category = self._view_state.category
        self._menu_for = self._computed
        accounts = self._computed.accounts
        ids = [a for a in view.category_authors if a in accounts]
        self._author_ids = [None] + ids
        names = [_('All authors')] + [
            _('%(name)s (%(count)d)') % {
                'name': _plain_name(accounts[a]),
                'count': view.category_counts.get(a, 0)} for a in ids]
        self.author_model.splice(0, self.author_model.get_n_items(), names)
        shown = ids[:MAX_CHIPS]
        while len(self.chips) < len(shown):
            chip = _AuthorChip()
            pages.connect_weak(chip, 'toggled', self._on_chip_toggled)
            self.chips.append(chip)
            self.chips_box.append(chip)
        for chip in self.chips:
            chip.set_visible(False)
        for chip, author in zip(self.chips, shown, strict=False):
            chip.show_author(accounts[author], view.category_counts[author])
            chip.set_visible(True)
        self.chips_scroll.set_visible(bool(shown))

    def _apply_view(self, announce=False):
        state = self._view_state
        view = derive_view(self._computed, state)
        state.author = view.author
        self._view = view
        items = self._items
        matching = view.matching
        previous = self._matching
        for post_id in previous - matching:
            items[post_id].matches = False
        for post_id in matching - previous:
            items[post_id].matches = True
        changed = previous != matching
        self._matching = matching
        for rank, post_id in enumerate(view.order):
            item = items[post_id]
            if item.sort_rank != rank:
                item.sort_rank = rank
        if state.group:
            for post_id in view.order:
                rank = view.author_rank.get(
                    self._computed.info[post_id].author, 0)
                item = items[post_id]
                if item.author_rank != rank:
                    item.author_rank = rank
        if changed:
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        # Sorter.changed() did not make the sort model sort again once its
        # keys had been read; putting the sorters back does.
        self.sorted.set_sorter(None)
        self.sorted.set_sorter(self.sorter)
        self.sorted.set_section_sorter(None)
        if state.group:
            self.sorted.set_section_sorter(self.section_sorter)
        self._grouped = state.group
        self._sync_controls()
        self.empty_label.set_visible(not matching)
        self._sync_bar()
        self._view_states[self._snapshot_id] = dataclasses.replace(state)
        if announce:
            self._schedule_toast()

    def _sync_bar(self):
        if self._view is None:
            return
        matching = set(self._view.matching)
        # A hidden duplicate boost counts for the boost it collapsed into.
        for hidden, kept in self._computed.dup_of.items():
            if kept in matching:
                matching.add(hidden)
        self.bar.set_matching(None if self._view_state.is_default()
                              else matching)

    def _schedule_toast(self):
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(
            TOAST_DELAY_MS, _toast_tick, self.weak_ref())

    def _show_view_toast(self):
        self._toast_id = 0
        author = None
        if self._view_state.author is not None:
            account = self._computed.accounts.get(self._view_state.author)
            author = _plain_name(account) if account else None
        self._toast_text = view_summary(self._view_state, author)
        pages.show_toast(self, self._toast_text)

    # Handlers

    def _on_category_changed(self, group, _pspec):
        if self._syncing:
            return
        category = group.get_active_name() or 'all'
        if category == self._view_state.category:
            return
        self._view_state.category = category
        self._apply_view(announce=True)

    def _on_author_changed(self, dropdown, _pspec):
        if self._syncing:
            return
        index = dropdown.get_selected()
        author = self._author_ids[index] \
            if 0 <= index < len(self._author_ids) else None
        self._set_author(author)

    def _on_chip_toggled(self, chip):
        if self._syncing:
            return
        self._set_author(chip.account_id if chip.get_active() else None)

    def _set_author(self, author):
        if author == self._view_state.author:
            return
        self._view_state.author = author
        self._apply_view(announce=True)

    def _on_sort_changed(self, dropdown, _pspec):
        if self._syncing:
            return
        sort_by = SORTS[dropdown.get_selected()]
        if sort_by == self._view_state.sort:
            return
        self._view_state.sort = sort_by
        # The first thing to read is the most: likes, replies... largest
        # first; time starts from the oldest, as Phanpy does.
        self._view_state.order = 'asc' if sort_by == 'time' else 'desc'
        self._apply_view(announce=True)

    def _on_order_changed(self, group, _pspec):
        if self._syncing:
            return
        order = group.get_active_name() or 'asc'
        if order == self._view_state.order:
            return
        self._view_state.order = order
        self._apply_view(announce=True)

    def _on_group_changed(self, switch, _pspec):
        if self._syncing or switch.get_active() == self._view_state.group:
            return
        self._view_state.group = switch.get_active()
        self._apply_view(announce=True)

    def _on_reset(self, _button):
        if self._view_state.is_default():
            return
        self._view_state = ViewState()
        self._apply_view(announce=True)
    def _add_keys(self):
        # Return is not here: the Gtk.ListView opens the focused post on
        # Enter itself, which is why the results header is activatable
        # False. Binding it here as well would fight that.
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for key, handler in (('j', self._key_next_post),
                             ('k', self._key_previous_post),
                             ('l', self._key_next_author),
                             ('h', self._key_previous_author),
                             ('period', self._key_top)):
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.KeyvalTrigger.new(Gdk.keyval_from_name(key),
                                      Gdk.ModifierType(0)),
                Gtk.CallbackAction.new(pages.weak_method(handler))))
        self.add_controller(controller)

    def _current_position(self):
        """Where the focused post is in the list, or -1."""
        root = self.get_root()
        widget = root.get_focus() if root is not None else None
        while widget is not None and not isinstance(widget, CatchUpRow):
            widget = widget.get_parent()
        if widget is None or widget.item is None:
            return -1
        for position in range(self.sorted.get_n_items()):
            if self.sorted.get_item(position) is widget.item:
                return position
        return -1

    def _focus_position(self, position):
        count = self.sorted.get_n_items()
        if not 0 < position < count:      # 0 is the block above the posts
            return False
        self.list_view.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)
        return True

    def _in_entry(self):
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return isinstance(focus, Gtk.Editable)

    def _key_next_post(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        return self._focus_position(max(self._current_position() + 1, 1))

    def _key_previous_post(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        return self._focus_position(position - 1 if position > 0 else 1)

    def _author_at(self, position):
        return self._computed.info[
            self.sorted.get_item(position).raw['id']].author

    def _key_next_author(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        count = self.sorted.get_n_items()
        if position < 1:
            return self._focus_position(1)
        current = self._author_at(position)
        for later in range(position + 1, count):
            if self._author_at(later) != current:
                return self._focus_position(later)
        return False

    def _key_previous_author(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        position = self._current_position()
        if position < 1:
            return self._focus_position(1)
        current = self._author_at(position)
        earlier = position - 1
        while earlier > 0 and self._author_at(earlier) == current:
            earlier -= 1
        if earlier < 1:
            return False
        author = self._author_at(earlier)
        while earlier > 1 and self._author_at(earlier - 1) == author:
            earlier -= 1
        return self._focus_position(earlier)

    def _key_top(self, *_args):
        if self.state != 'results' or self._in_entry():
            return False
        self.scrolled.get_vadjustment().set_value(0)
        return True


def _toast_tick(page_ref):
    page = page_ref()
    if page is not None:
        page._show_view_toast()
    return GLib.SOURCE_REMOVE
