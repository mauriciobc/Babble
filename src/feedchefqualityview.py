# SPDX-License-Identifier: GPL-3.0-or-later
"""The Quality section of the Kitchen Report: the recipe's score and what it
measures, how the posts engaged, how many reached each stage, the issues
the health check found and the suggestions, each with an Apply button.

It is a controller of ``KitchenReportDialog`` (``feedchefcontroller``; the
dialog owns it). The dialog shows its groups at
once and the numbers when the worker has them (``feedchefquality``): the
section only lays out what ``feedchef.history``, ``quality``, ``health`` and
``advice`` word (through the ``Words`` the dialog gives), and does no
arithmetic of its own beyond the share a bar draws.
"""

import copy
from functools import partial

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .feedchef import advice, history, optimizer, quality  # noqa: E402
from .feedchef import health as healthmod  # noqa: E402
from .feedchefcontroller import PageController, guarded  # noqa: E402
from .feedchefquality import HealthBadge, worker  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .uihelpers import set_accessible_label  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

# The most suggestions the section lists: the rest are less severe.
MAX_SUGGESTIONS = 6


class QualityContext:
    """What the section needs of the page it reports on. The callbacks are
    weak methods of the page, so the dialog never keeps it alive:
    ``apply(rec, anchor, undone)`` makes a suggestion (True when it did; its
    Undo toast is on *anchor*, and ``undone()`` is called when it is
    undone), ``try_change(rec)`` proposes it as an experiment; ``running``
    is the text of an experiment in progress (or None); ``others()`` the
    reader's other recipes (called on the worker)."""

    def __init__(self, recipe, records, others, apply, try_change,
                 running=None, declined=()):
        self.recipe = copy.deepcopy(recipe)
        self.records = list(records)
        self.others = others
        self.apply = apply
        self.try_change = try_change
        self.running = running
        # Ids of suggestions the reader already said no to for this recipe.
        self.declined = set(declined)


def score_word(state):
    return {'excellent': _('Excellent'), 'good': _('Good'),
            'moderate': _('Moderate'), 'poor': _('Poor')}.get(state, state)


def _bar(value, maximum):
    # The number beside it says the same: the bar is for a glance.
    bar = Gtk.LevelBar(min_value=0.0, max_value=max(maximum, 1e-9),
                       value=min(max(value, 0.0), max(maximum, 1e-9)),
                       valign=Gtk.Align.CENTER, width_request=96,
                       accessible_role=Gtk.AccessibleRole.PRESENTATION)
    # One colour: the default offsets recolour a bar by its share.
    for name in ('low', 'high', 'full'):
        bar.remove_offset_value(name)
    return bar


def _number(text):
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER, xalign=1.0)
    label.add_css_class('numeric')
    return label


def _row(title, subtitle='', icon=None):
    row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False,
                        activatable=False)
    if icon:
        row.add_prefix(Gtk.Image(
            icon_name=icon, accessible_role=Gtk.AccessibleRole.PRESENTATION))
    return row


class QualityReport(PageController):
    """The quality half of ``KitchenReportDialog``. The dialog offers
    ``page`` (its preferences page), ``insert_action_group()`` and
    ``force_close()``."""

    def __init__(self, owner, ctx, words, focus=False):
        """Add the section's groups (empty) and ask the worker for the
        numbers."""
        super().__init__(owner)
        self._quality_job = None
        self.quality_ctx = ctx
        self._quality_words = words
        self._quality_focus = focus
        self._quality_recs = {}
        self._quality_buttons = {}
        self._quality_rows = []
        self.quality_group = Adw.PreferencesGroup(
            title=_('Quality'), description=quality.caveat_text(words))
        self.engagement_group = Adw.PreferencesGroup(
            title=_('How People Engaged'),
            description=_('The posts shown, by how much people reacted'))
        self.stages_group = Adw.PreferencesGroup(
            title=_('Posts at Each Stage'),
            description=_('From your source, through your diet, to your '
                          'timeline'))
        self.issues_group = Adw.PreferencesGroup(title=_('Issues'))
        self.suggestions_group = Adw.PreferencesGroup(
            title=_('Suggestions'))
        for group in (self.engagement_group, self.stages_group,
                      self.issues_group, self.suggestions_group):
            group.set_visible(False)
        wait = _row(_('Analyzing…'))
        wait.add_prefix(Gtk.Spinner(spinning=True))
        self._add(self.quality_group, wait)
        for group in (self.quality_group, self.engagement_group,
                      self.stages_group, self.issues_group,
                      self.suggestions_group):
            self.owner.page.add(group)
        actions = Gio.SimpleActionGroup()
        apply = Gio.SimpleAction.new('apply', GLib.VariantType.new('s'))
        connect_weak(apply, 'activate', self._on_quality_apply)
        actions.add_action(apply)
        propose = Gio.SimpleAction.new('try', None)
        connect_weak(propose, 'activate', self._on_quality_try)
        actions.add_action(propose)
        self.owner.insert_action_group('quality', actions)
        recipe, records, others = ctx.recipe, ctx.records, ctx.others
        self._quality_job = worker().submit(
            lambda: history.analyze(recipe, records, others()),
            weak_method(self._quality_fill))

    def _add(self, group, row):
        group.add(row)
        self._quality_rows.append((group, row))
        return row

    def _quality_clear(self):
        for group, row in self._quality_rows:
            group.remove(row)
        self._quality_rows = []

    def _quality_fill(self, found):
        """The numbers are here: lay them out."""
        self._quality_job = None
        self._quality_clear()
        if found is None:
            self._add(self.quality_group, _row(
                _('Could Not Analyze This Recipe'),
                _('The rest of the report is not affected'),
                'dialog-warning-symbolic'))
            return
        self._quality_summary(found)
        self._quality_issues(found)
        self._quality_suggestions(found)
        row = getattr(self, 'quality_score_row', None)
        if self._quality_focus and row is not None:
            row.grab_focus()

    def _quality_summary(self, found):
        words = self._quality_words
        group = self.quality_group
        if found.fresh_fallback:
            self._add(group, _row(
                _('Freshness Minimum Applied'),
                _('Some posts were older than the freshness window; a '
                  'minimum was applied'), 'dialog-information-symbolic'))
        metrics = found.metrics
        if metrics is None:
            self._add(group, _row(
                _('No Score Yet'),
                _('The score comes from the posts the last cook showed')))
        else:
            score = metrics['recipe_quality_score']
            row = _row(_('Latest Cook'), score_word(
                metrics.get('state') or quality.state_of(score)))
            row.add_suffix(_number('%.2f' % score))
            row.add_suffix(_bar(score, 1.0))
            row.set_focusable(self._quality_focus)
            self.quality_score_row = self._add(group, row)
            self._quality_bands(metrics)
            self._quality_stages(metrics)
        state = found.health
        # Translators: under the recipe's health; %(count)d is how many
        # cooks the average is over and %(mean)s the average score.
        sub = ngettext('Average over the last cook: %(mean)s',
                       'Average over the last %(count)d cooks: %(mean)s',
                       state.cooks) % {
            'mean': '%.2f' % (state.mean or 0.0), 'count': state.cooks} \
            if state.cooks else _('Needs a few more cooks to judge')
        if metrics is not None and state.mean is not None:
            latest = metrics['recipe_quality_score']
            if latest >= healthmod.ALERT_BELOW > state.mean:
                sub += '\n' + _('One good cook does not change the average')
            elif latest < healthmod.ALERT_BELOW <= state.mean:
                sub += '\n' + _('One poor cook does not change the average')
        row = _row(_('Recipe Health'), sub)
        badge = HealthBadge()
        badge.show_health(state, words)
        row.add_suffix(badge)
        if not badge.get_visible():
            row.add_suffix(_number(healthmod.state_text(state.state, words)))
        self._add(group, row)

    def _quality_bands(self, metrics):
        words = self._quality_words
        kitchen = metrics['kitchen']
        total = kitchen['total_toots']
        self.engagement_group.set_visible(total > 0)
        names = quality.band_names(words)
        for band in quality.BANDS:
            count = kitchen['quality_distribution'].get(band, 0)
            row = _row(names[band])
            row.add_suffix(_number(ngettext('%d post', '%d posts', count)
                                   % count))
            row.add_suffix(_bar(count, total))
            self._add(self.engagement_group, row)

    def _quality_stages(self, metrics):
        self.stages_group.set_visible(True)
        for key, title, sub in (
                ('pantry', _('Pantry'),
                 _('Posts from your source and hashtags')),
                ('diet', _('Diet'), _('Posts that passed your diet')),
                ('kitchen', _('Kitchen'), _('Posts shown'))):
            row = _row(title, sub)
            row.add_suffix(_number(str(metrics[key]['total_toots'])))
            self._add(self.stages_group, row)

    def _quality_issues(self, found):
        words = self._quality_words
        state = found.health
        self.issues_group.set_visible(True)
        if state.state == healthmod.UNKNOWN:
            self._add(self.issues_group, _row(
                _('Not Enough Cooks Yet'),
                _('Issues show once this recipe has been cooked a few '
                  'times'), 'dialog-information-symbolic'))
        for reason in state.reasons:
            self._add(self.issues_group, _row(
                healthmod.reason_text(reason, words),
                icon='dialog-warning-symbolic'))
        if state.state == healthmod.WORKING:
            self._add(self.issues_group, _row(
                _('No Issues Found'), icon='object-select-symbolic'))

    def _quality_suggestions(self, found):
        words = self._quality_words
        group = self.suggestions_group
        group.set_visible(True)
        recs = found.recommendations[:MAX_SUGGESTIONS]
        self._quality_recs = {rec.id: rec for rec in recs}
        if not recs:
            self._add(group, _row(_('No Suggestions'),
                                  _('This recipe looks fine'),
                                  'object-select-symbolic'))
        for rec in recs:
            text = advice.describe(rec, words)
            title = '%s: %s' % (text.title, text.change) if text.change \
                else text.title
            if rec.severity == 'high':
                # Translators: the title of an important suggestion; %s is
                # what it changes. The icon alone would not say it.
                title = _('Important: %s') % title
            lines = [text.reason] + ([text.impact] if text.impact else [])
            if rec.manual:
                lines.append(_('Choose this yourself in the recipe '
                               'builder'))
            row = _row(title, '\n'.join(lines), 'dialog-information-symbolic'
                       if rec.severity != 'high'
                       else 'dialog-warning-symbolic')
            if not rec.manual:
                button = Gtk.Button(
                    label=_('Apply'), valign=Gtk.Align.CENTER,
                    action_name='quality.apply',
                    action_target=GLib.Variant.new_string(rec.id))
                # Translators: the accessible name of a suggestion's Apply
                # button; %s is what it changes, such as "Favor Fresh
                # Posts: 6 hours → 12 hours".
                set_accessible_label(button, _('Apply: %s') % title)
                row.add_suffix(button)
                self._quality_buttons[rec.id] = button
            self._add(group, row)
        self._quality_experiment(found)

    def _quality_experiment(self, found):
        ctx = self.quality_ctx
        if ctx.running:
            self._add(self.suggestions_group, _row(
                _('Experiment Running'), ctx.running,
                'media-playback-start-symbolic'))
            return
        testable = [rec for rec in found.recommendations
                    if optimizer.testable(rec)
                    and rec.id not in ctx.declined]
        if not testable:
            return
        row = _row(_('Try a Change'),
                   _('Test the most useful suggestion on your next '
                     'refreshes, and keep it only if it does better'),
                   'view-refresh-symbolic')
        button = Gtk.Button(label=_('Try…'), valign=Gtk.Align.CENTER,
                            action_name='quality.try')
        set_accessible_label(button, _('Try a Change'))
        row.add_suffix(button)
        self._testable = testable
        self._add(self.suggestions_group, row)

    # Actions

    @guarded
    def _on_quality_apply(self, _action, parameter):
        """Apply stays in the report: its Undo toast is the dialog's own,
        and the button says it was done."""
        rec = self._quality_recs.get(parameter.get_string())
        if rec is None:
            return
        undone = partial(weak_method(self._on_quality_undone), rec.id)
        button = self._quality_buttons.get(rec.id)
        if self.quality_ctx.apply(rec, self.owner, undone) and button is not None:
            button.set_label(_('Applied'))
            button.set_sensitive(False)

    def _on_quality_undone(self, rec_id):
        button = self._quality_buttons.get(rec_id)
        if button is not None:
            button.set_label(_('Apply'))
            button.set_sensitive(True)

    @guarded
    def _on_quality_try(self, _action, _parameter):
        testable = getattr(self, '_testable', None)
        if testable:
            self.owner.force_close()
            self.quality_ctx.try_change(testable)
