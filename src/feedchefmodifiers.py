# SPDX-License-Identifier: GPL-3.0-or-later
"""The kitchen modifiers of the recipe builder: one expander per modifier
(an enable switch, a value row, optional extra controls), and the heat
level's toggle group (Cool, Warm, Hot, Fire)."""

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from .feedchef import heat as heatmod  # noqa: E402
from .feedchef.cluster import (MAX_GROUPS, MIN_POSTS,  # noqa: E402
                                STRATEGIES, strategy_of)
from .feedchef.recipe import Modifier  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

log = logging.getLogger('babble.feedchef')


def step_label(name):
    """The reader's name for a heat step."""
    return {'warm': _('Warm'), 'hot': _('Hot'),
            'fire': _('Fire')}.get(name, name)


def heat_label(percent):
    """What a heat level is called: its step, "every post" for none (0),
    or "Imported level (62%)" for a percent that is not a step."""
    name = heatmod.step_of(percent)
    if name == 'cool':
        return _('every post')
    if name is not None:
        return step_label(name)
    # Translators: the heat level of a recipe imported with its own
    # percent, kept as it is; %s is that percent, such as "62".
    return _('Imported level (%s%%)') % ('%g' % heatmod.clean(percent))


class HeatStepsRow(Adw.PreferencesRow):
    """The heat level: Warm, Hot and Fire as one toggle group (off is the
    modifier's switch, not a level).

    It stands where a spin row did: ``value`` is the exact percent (a float
    property, so ``notify::value`` works), ``get_value`` and ``set_value``
    read and write it. A percent that is not a step (an imported recipe's)
    selects no toggle and is said under the title, and kept as it is until
    the reader picks a level."""

    __gtype_name__ = 'BabbleFeedChefHeatSteps'

    value = GObject.Property(type=float, default=50.0)

    def __init__(self):
        super().__init__(activatable=False, focusable=False,
                         title=_('Heat Level'))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12)
        title = Gtk.Label(label=_('Heat Level'), xalign=0.0)
        # Translators: what each heat level hides, and the page minimum;
        # %d is the fewest posts a page keeps.
        self.hint = Gtk.Label(
            label=_('Warm hides half of the posts, Hot three in four, Fire '
                    'nine in ten. A page never keeps fewer than %d posts: '
                    'it steps down to a cooler level when needed.')
            % heatmod.MIN_LEFT, xalign=0.0, wrap=True)
        self.hint.add_css_class('dimmed')
        self.hint.add_css_class('caption')
        self.imported = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        self.imported.add_css_class('caption')
        self.group = Adw.ToggleGroup(homogeneous=True)
        self.group.add_css_class('round')
        tips = {
            'warm': _('Hides the weaker half'),
            'hot': _('Hides three posts in four'),
            'fire': _('Hides nine posts in ten'),
        }
        for name, _percent in reversed(heatmod.STEPS[:-1]):
            self.group.add(Adw.Toggle(label=step_label(name), name=name,
                                      tooltip=tips[name]))
        set_accessible_label(self.group, _('Heat Level'))
        box.append(title)
        box.append(self.hint)
        box.append(self.imported)
        box.append(self.group)
        self.set_child(box)
        self._syncing = False
        self.set_value(50)
        connect_weak(self.group, 'notify::active-name', self._on_toggled)

    def get_value(self):
        return self.value

    def set_value(self, percent):
        percent = float(heatmod.clean(percent))
        self._syncing = True
        try:
            name = heatmod.step_of(percent)
            if name in (None, 'cool'):
                self.group.set_active_name(None)
                # Translators: under a heat level an imported recipe set to
                # its own percent; %s is that percent, such as "62.5".
                self.imported.set_label(
                    _('Imported value: %s%%. Choose a level to replace it.')
                    % ('%g' % percent))
                self.imported.set_visible(True)
            else:
                self.imported.set_visible(False)
                self.group.set_active_name(name)
            self.value = percent
        finally:
            self._syncing = False

    def _on_toggled(self, group, _pspec):
        if self._syncing:
            return
        for step, percent in heatmod.STEPS:
            if step == group.get_active_name():
                self._syncing = True
                try:
                    self.imported.set_visible(False)
                    self.value = float(percent)
                finally:
                    self._syncing = False


def spin_row(title, subtitle, value, lower, upper, step, digits=0):
    return Adw.SpinRow(
        title=title, subtitle=subtitle, digits=digits,
        adjustment=Gtk.Adjustment(value=value, lower=lower, upper=upper,
                                  step_increment=step))


class Extra:
    """A second control of a modifier's expander: a switch or a choice,
    written back only when the reader changed it."""

    def __init__(self, widget, read, write):
        self.widget = widget
        self._read = read
        self._write = write
        self.initial = self.value()

    def value(self):
        if isinstance(self.widget, Adw.SwitchRow):
            return self.widget.get_active()
        return self.widget.get_selected()

    def load(self, mod):
        if mod is None:
            return
        value = self._read(mod)
        if isinstance(self.widget, Adw.SwitchRow):
            self.widget.set_active(value)
        else:
            self.widget.set_selected(value)
        self.initial = self.value()

    def changed(self):
        return self.value() != self.initial

    def write(self, mod):
        self._write(mod, self.value())


class ModifierEditor:
    """An expander with an enable switch for one kitchen modifier.

    On: the modifier is in the recipe. Writing back touches only what the
    reader changed: an enabled modifier keeps its place in the list and
    its other fields, a value the spin row still shows as it was read is
    not rewritten, and a modifier of another type is never looked at."""

    def __init__(self, kind, title, subtitle, spin, read, write_value,
                 show_value, extras=()):
        self.kind = kind
        self._read = read
        self._write = write_value
        self._subtitle = subtitle
        self._show_value = show_value
        # Why the modifier cannot be switched on here (a package it needs
        # is missing), or '': the row is then shown but not editable.
        self.blocked = ''
        self.row = Adw.ExpanderRow(title=title, subtitle=subtitle,
                                   show_enable_switch=True)
        self.spin = spin
        self.row.add_row(spin)
        self.extras = list(extras)
        for extra in self.extras:
            self.row.add_row(extra.widget)
        self.initial = spin.get_value()

    def block(self, reason):
        """Show the modifier as not available. *reason* is the technical
        detail (a package name, an import error): it goes to the log, and
        the reader sees a plain sentence. The row stays focusable and
        expandable, so the explanation is reachable with the keyboard; only
        what would change the setting is turned off."""
        log.info('feedchef: %s is not available: %s', self.kind, reason)
        self.blocked = reason
        self.row.set_show_enable_switch(False)
        self.spin.set_sensitive(False)
        for extra in self.extras:
            extra.widget.set_sensitive(False)
        note = Adw.ActionRow(title=_('Needs an optional component that is not '
                                'installed'), activatable=False)
        note.add_prefix(Gtk.Image.new_from_icon_name(
            'dialog-information-symbolic'))
        self.row.add_row(note)

    def state(self):
        return (self.row.get_enable_expansion(), self.spin.get_value(),
                *(extra.value() for extra in self.extras))

    def load(self, recipe):
        mod = next((m for m in recipe.kitchen.modifiers
                    if m.type == self.kind), None)
        self.present = mod is not None
        if mod is not None:
            self.spin.set_value(self._read(mod))
        for extra in self.extras:
            extra.load(mod)
        self.initial = self.spin.get_value()
        # A blocked row has no switch and always opens, to show why.
        self.row.set_enable_expansion(self.present or bool(self.blocked))
        self.row.set_expanded(False)

    def sync_subtitle(self):
        """The subtitle names what the modifier does, and, while it is
        on, the value it is set to."""
        if self.blocked:
            self.row.set_subtitle(_('Needs an optional component that is '
                                    'not installed'))
            return
        if not self.row.get_enable_expansion():
            self.row.set_subtitle(self._subtitle)
            return
        # Translators: %(what)s is what a setting does and %(value)s its
        # value, such as "Newer posts rank higher · 6 hours".
        value = self.spin.get_value()
        self.row.set_subtitle(_('%(what)s · %(value)s') % {
            'what': self._subtitle,
            'value': self._show_value(
                value if isinstance(self.spin, HeatStepsRow)
                else int(round(value)))})

    def apply(self, recipe):
        if self.blocked:
            # Nothing here can be edited: a modifier the recipe has (an
            # imported one) is kept as it is, and none is added.
            return
        modifiers = recipe.kitchen.modifiers
        wanted = self.row.get_enable_expansion()
        if not wanted:
            modifiers[:] = [m for m in modifiers if m.type != self.kind]
            return
        mod = next((m for m in modifiers if m.type == self.kind), None)
        fresh = mod is None
        if fresh:
            mod = Modifier(type=self.kind)
            modifiers.append(mod)
        if fresh or self.spin.get_value() != self.initial:
            self._write(mod, self.spin.get_value())
            # The recipe has it now: the control going back to what it
            # first showed is a change again, not "as it was read".
            self.initial = self.spin.get_value()
        for extra in self.extras:
            if fresh or extra.changed():
                extra.write(mod)
                extra.initial = extra.value()


def modifier_editors():
    def fresh_write(mod, value):
        mod.decay_minutes = int(round(value)) * 60

    def heat_write(mod, value):
        mod.percentile_threshold = heatmod.whole(value)

    def diversity_write(mod, value):
        mod.max_per_cluster = int(round(value))
        if not mod.diversity_dimensions:
            mod.diversity_dimensions = ['author']

    def servers_read(mod):
        return 'instance' in (mod.diversity_dimensions or [])

    def servers_write(mod, on):
        dimensions = [d for d in (mod.diversity_dimensions or ['author'])
                      if d != 'instance']
        mod.diversity_dimensions = dimensions + ['instance'] if on \
            else dimensions

    def group_write(mod, value):
        mod.max_per_cluster = int(round(value))

    def strategy_read(mod):
        return STRATEGIES.index(strategy_of(mod.cluster_strategy))

    def strategy_write(mod, index):
        mod.cluster_strategy = STRATEGIES[index]

    def trending_write(mod, value):
        mod.growth_threshold = round(value / 100.0, 2)

    def sentiment_write(mod, value):
        mod.sentiment_boost = round(value / 100.0, 2)

    def clamp(value, low, high):
        return max(low, min(high, value))

    def hours(value):
        return ngettext('%d hour', '%d hours', value) % value

    def percent(value):
        # Translators: a percentage, such as "50%".
        return _('%d%%') % value

    def per_person(value):
        return ngettext('%d per person', '%d per person', value) % value

    def per_group(value):
        return ngettext('%d per group', '%d per group', value) % value

    servers = Adw.SwitchRow(
        title=_('Count Servers Too'),
        subtitle=_('Also limits the posts from each server, so one server '
                   'cannot fill the feed'))
    strategy = Adw.ComboRow(
        title=_('Post That Stays'),
        subtitle=_('In each group'),
        model=Gtk.StringList.new([_('Most engaging'), _('Newest'),
                                  _('Most representative')]))

    return [
        ModifierEditor(
            'freshness', _('Favor Fresh Posts'),
            _('Newer posts rank higher'),
            spin_row(_('Fade-Out Time'),
                  _('In hours; older posts score zero'), 6, 1, 48, 1),
            lambda m: clamp(round((m.decay_minutes or 360) / 60), 1, 48),
            fresh_write, hours),
        ModifierEditor(
            'heat', _('Hide the Least Engaging'),
            _('Hides the least engaging posts'),
            HeatStepsRow(),
            lambda m: heatmod.clean(m.percentile_threshold),
            heat_write, heat_label),
        ModifierEditor(
            'diversity', _('Limit Posts per Person'),
            _('Keeps one voice from filling the feed'),
            spin_row(_('Posts per Person'),
                  _('Until you refresh; a boost counts for the person who '
                    'boosted'), 3, 1, 10, 1),
            lambda m: clamp(m.max_per_cluster or 3, 1, 10),
            diversity_write, per_person,
            [Extra(servers, servers_read, servers_write)]),
        ModifierEditor(
            'trending', _('Boost Trending Topics'),
            _('Posts on hashtags many people are using now, and posts '
              'trending on the server, rank higher'),
            spin_row(_('Minimum Momentum'), _('How fast a hashtag must be '
                                          'growing, in percent'),
                  30, 10, 100, 10),
            lambda m: clamp(round((m.growth_threshold or 0.3) * 100),
                            10, 100),
            trending_write, percent),
        ModifierEditor(
            'sentiment_boost', _('Favor Positive Posts'),
            _('English and Portuguese posts that read as positive rank '
              'higher'),
            spin_row(_('Strength'), _('In percent'), 50, 10, 100, 10),
            lambda m: clamp(round(float(m.sentiment_boost or 0.5) * 100),
                            10, 100),
            sentiment_write, percent),
        ModifierEditor(
            'similarity_cluster', _('Group Similar Posts'),
            # Translators: %(groups)d is the most groups a page is split into
            # and %(posts)d the fewest posts it needs to be split at all.
            _('Posts that read alike form up to %(groups)d groups, and only '
              'some of each group show. Needs %(posts)d posts or more.')
            % {'groups': MAX_GROUPS, 'posts': MIN_POSTS},
            spin_row(_('Posts per Group'), _('How many posts of each group '
                                          'stay'), 3, 1, 10, 1),
            lambda m: clamp(m.max_per_cluster or 1, 1, 10),
            group_write, per_group,
            [Extra(strategy, strategy_read, strategy_write)]),
    ]
