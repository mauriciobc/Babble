# SPDX-License-Identifier: GPL-3.0-or-later
"""The builder's Style group: the modes (Explore, Focused, Premium, Custom)
and the "More Posts <-> Better Posts" slider.

Both set the same three controls the Kitchen group shows (the heat level,
the freshness fade-out and the order), through ``feedchef.templates``: the
controls are the truth, the mode and the slider only write them, and read
them back to say which mode the recipe is in. Editing one of those controls
by hand turns the mode to Custom (or to the mode whose settings the reader
has dialled in). A recipe with a focus keeps its order, as everywhere else.

A controller of ``FeedChefBuilderDialog`` (the builder owns it, see
``feedchefcontroller``): it reads the builder's ``editors`` and ``order``,
asks it for ``current_recipe()`` and tells it ``controls_edited()``.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from .feedchef import recipeedit, templates  # noqa: E402
from .feedchefcontroller import PageController, guarded  # noqa: E402
from .feedchefexplain import WORDS  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


def mapped(recipe):
    """What a mode or the slider sets, as it is in *recipe*."""
    return (recipeedit.current(recipe, 'heat'),
            recipeedit.current(recipe, 'freshness'),
            recipe.kitchen.base_sort)


class Modes(PageController):
    """The Style group of the builder: its widgets and what they say."""

    def __init__(self, builder, page):
        """Build the Style group into *page* (the preferences page)."""
        super().__init__(builder)
        self.syncing = False
        self._mapped_seen = None
        names = templates.mode_names(WORDS)
        group = Adw.PreferencesGroup(
            title=_('Style'),
            description=_('A starting point for how many posts you see and '
                          'how engaging they are. The settings below '
                          'follow it.'))
        self.mode_row = Adw.ComboRow(
            title=_('Mode'),
            model=Gtk.StringList.new([names[mode][0]
                                      for mode in templates.MODES]))
        group.add(self.mode_row)
        more, better = templates.slider_labels(WORDS)
        self.slider = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.slider.set_draw_value(False)
        self.slider.set_hexpand(True)
        for position, text in ((0, more), (50, _('Balanced')),
                               (100, better)):
            self.slider.add_mark(position, Gtk.PositionType.BOTTOM, text)
        set_accessible_label(self.slider, _('More Posts or Better Posts'))
        slider_row = Adw.PreferencesRow(
            activatable=False, focusable=False, child=self.slider)
        self.slider.set_margin_top(12)
        self.slider.set_margin_bottom(12)
        # The end marks' labels are centred on the ends: room for them.
        self.slider.set_margin_start(40)
        self.slider.set_margin_end(40)
        group.add(slider_row)
        self.slider_row = slider_row
        page.add(group)
        connect_weak(self.mode_row, 'notify::selected',
                     self._on_mode_selected)
        connect_weak(self.slider, 'value-changed', self._on_slider_moved)

    def get_mode(self):
        return templates.MODES[self.mode_row.get_selected()]

    def set_mode(self, mode):
        self.mode_row.set_selected(templates.MODES.index(mode))

    @guarded
    def editor(self, kind):
        return next(e for e in self.owner.editors if e.kind == kind)

    # Reading the controls

    @guarded
    def sync(self, force=False):
        """Show the mode the controls are in (Custom when none), and where
        the slider sits. Only when what a mode sets changed."""
        recipe = self.owner.current_recipe()
        seen = mapped(recipe)
        if not force and seen == self._mapped_seen:
            return
        self._mapped_seen = seen
        self._show_mode(templates.mode_of(recipe),
                        templates.slider_position(recipe))

    def _show_mode(self, mode, position, move_slider=True):
        self.syncing = True
        try:
            self.set_mode(mode)
            # Not while the reader drags it: it would jump under the hand.
            if position is not None and move_slider:
                self.slider.set_value(position)
            if position is None:
                self.slider_row.add_css_class('dimmed')
            else:
                self.slider_row.remove_css_class('dimmed')
            self.mode_row.set_subtitle(templates.mode_names(WORDS)[mode][1])
        finally:
            self.syncing = False

    # Writing them

    @guarded
    def _write_controls(self, target, move_slider=True):
        """Put the heat, freshness and order of *target* into the
        controls."""
        self.syncing = True
        try:
            heat = recipeedit.current(target, 'heat')
            editor = self.editor('heat')
            editor.row.set_enable_expansion(heat is not None)
            if heat is not None:
                editor.spin.set_value(heat)
            minutes = recipeedit.current(target, 'freshness')
            editor = self.editor('freshness')
            editor.row.set_enable_expansion(minutes is not None)
            if minutes is not None:
                editor.spin.set_value(max(1, min(48, round(minutes / 60))))
            if not templates.focused(target):
                self.owner.order.set_selected(
                    1 if target.kitchen.base_sort == 'chronological' else 0)
        finally:
            self.syncing = False
        self._mapped_seen = mapped(target)
        self._show_mode(templates.mode_of(target),
                        templates.slider_position(target), move_slider)
        self.owner.controls_edited()

    @guarded
    def _on_mode_selected(self, _row, _pspec):
        if self.syncing:
            return
        mode = self.get_mode()
        self.mode_row.set_subtitle(templates.mode_names(WORDS)[mode][1])
        if mode != 'custom':
            self._write_controls(templates.with_mode(
                self.owner.current_recipe(), mode))

    @guarded
    def _on_slider_moved(self, scale):
        if self.syncing:
            return
        current = self.owner.current_recipe()
        target = templates.with_slider(current, scale.get_value())
        if mapped(target) != mapped(current):
            self._write_controls(target, move_slider=False)
