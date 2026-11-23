# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's transparency dialogs: Why This Post? explains one post of a
recipe's timeline and the Kitchen Report sums up its last cook. Both only
lay out what ``feedchef.auditreport`` words; they hold no page, client or
source, so closing one frees it."""

import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from .feedchef import auditreport, decisionlog  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

WORDS = auditreport.Words(_, ngettext)
STATUS_ICON = 'dialog-information-symbolic'


def _row(row):
    widget = Adw.ActionRow(title=row.title, subtitle=row.subtitle,
                           use_markup=False)
    if row.value:
        value = Gtk.Label(label=row.value, valign=Gtk.Align.CENTER,
                          xalign=1.0)
        value.add_css_class('numeric')
        widget.add_suffix(value)
    return widget


def add_sections(page, sections):
    """A preferences group per section, a row per row; the groups."""
    groups = []
    for section in sections:
        group = Adw.PreferencesGroup(title=section.title)
        for row in section.rows:
            group.add(_row(row))
        page.add(group)
        groups.append(group)
    return groups


def launch_folder(parent, path, on_done):
    """Open *path* in the file manager (through the portal when
    sandboxed); *on_done(launcher, result)* answers."""
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
    launcher.launch(parent, None, on_done)


class WhyThisPostDialog(feedback.ToastDialog):
    """The checks a post passed and how it was ranked. *found* is
    ``FeedChefSource.audit_for(id)``: ``(entry, record)``, or None."""

    def __init__(self, found):
        super().__init__(_('Why This Post?'))
        self.found = found is not None
        if found is None:
            self.set_content(Adw.StatusPage(
                icon_name=STATUS_ICON, title=_('No Record for This Post'),
                description=_('Feed Chef keeps a record of the pages it '
                              'shows while this timeline is open, and this '
                              'post is not in one of them')))
            return
        entry, record = found
        add_sections(self.page,
                     auditreport.explain_post(entry, record, WORDS))


class KitchenReportDialog(feedback.ToastDialog):
    """What the last cook did, with Copy Report, Open Logs Folder and
    Clear Audit Logs. *records* are the audit records of its pages."""

    def __init__(self, records):
        super().__init__(_('Kitchen Report'))
        self.alert = None
        self.report = auditreport.cook_report(records, WORDS)
        if not self.report.pages:
            self.set_content(Adw.StatusPage(
                icon_name=STATUS_ICON, title=_('No Report Yet'),
                description=_('Load some posts in this timeline, then '
                              'check back here')))
            return
        groups = add_sections(self.page, self.report.sections)
        # The recipe's settings sit under the cook's title.
        groups[0].set_description(self.report.settings)
        group = Adw.PreferencesGroup()
        self.copy_row = Adw.ButtonRow(
            title=_('_Copy Report'), use_underline=True,
            start_icon_name='edit-copy-symbolic')
        connect_weak(self.copy_row, 'activated', self._on_copy)
        group.add(self.copy_row)
        self.folder_row = Adw.ButtonRow(
            title=_('Open _Logs Folder'), use_underline=True,
            start_icon_name='folder-open-symbolic')
        connect_weak(self.folder_row, 'activated', self._on_folder)
        group.add(self.folder_row)
        self.clear_row = Adw.ButtonRow(
            title=_('Clear _Audit Logs'), use_underline=True,
            start_icon_name='edit-delete-symbolic')
        self.clear_row.add_css_class('destructive-action')
        connect_weak(self.clear_row, 'activated', self._on_clear)
        group.add(self.clear_row)
        self.page.add(group)

    def _on_copy(self, _row):
        display = self.get_display()
        if display is None:
            return
        display.get_clipboard().set(self.report.text)
        self.toast(_('Report copied'))

    def _on_folder(self, _row):
        path = decisionlog.log_dir()
        if not os.path.isdir(path):
            self.toast(_('There are no audit logs yet'))
            return
        launch_folder(self.get_root(), path, weak_method(self._on_launched))

    def _on_launched(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as error:
            self.error(_('Could not open the folder'), error)

    def _on_clear(self, _row):
        self.alert = feedback.confirm(
            self.clear_row, _('Clear Audit Logs?'),
            _('This deletes the Feed Chef audit history kept on this '
              'computer, for all of your recipes. Your recipes and '
              'timelines are not affected.'),
            _('_Clear'), weak_method(self._clear_confirmed))

    def _clear_confirmed(self):
        decisionlog.clear_all()
        self.toast(_('Audit logs cleared'))
