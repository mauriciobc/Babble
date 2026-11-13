# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer schedule chrome.

The schedule banner, picker and timed-post labelling live here. Mode
transitions are data in :mod:`composetransition` and are applied by
:meth:`BabbleComposeView.begin`. This module never imports ``compose``.
"""

from dataclasses import dataclass

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GObject  # noqa: E402

from . import pages  # noqa: E402


@dataclass(frozen=True)
class _ScheduledReplacement:
    original_id: str
    accepted: dict
    on_done: object
    message: str


class ComposeSchedule(GObject.Object):
    """Schedule chrome for one composer.

    A GObject so :func:`pages.connect_weak` / :func:`pages.weak_method`
    can hold it without a C-level cycle through the chrome it wires.
    The host is the composer view, held weakly; this object never
    imports ``compose``.
    """

    def __init__(self, host):
        super().__init__()
        self._host_ref = host.weak_ref()
        self.schedule_button = None
        self.schedule_banner = None

    def _host(self):
        host = self._host_ref()
        if host is None:
            raise RuntimeError('composer gone')
        return host

    def bind(self, *, schedule_button, schedule_banner):
        """Wire the schedule button and banner the view already built."""
        self.schedule_button = schedule_button
        self.schedule_banner = schedule_banner
        pages.connect_weak(schedule_button, 'clicked', self.on_schedule_clicked)
        pages.connect_weak(schedule_banner, 'button-clicked',
                           self.on_schedule_clear)

    def is_timed(self):
        """True when the post goes out at a chosen time rather than now.
        A server-side draft also carries a date, but one that is never
        reached, so it is not a time to show."""
        from .scheduled import is_draft
        view = self._host()
        return view.scheduled_at is not None \
            and not is_draft({'scheduled_at': view.scheduled_at})

    def on_schedule_clicked(self, _button):
        view = self._host()
        if view.is_frozen():
            return
        from .scheduled import SchedulePickerDialog
        dialog = SchedulePickerDialog(
            view.scheduled_at, _('_Set Time'),
            on_picked=pages.weak_method(self._on_schedule_picked))
        dialog.present(view)

    def _on_schedule_picked(self, iso):
        view = self._host()
        if view.is_frozen():
            return
        view.scheduled_at = iso
        view.update_compose_state()

    def on_schedule_clear(self, _banner):
        view = self._host()
        if view.is_frozen():
            return
        # Back to sending now. An entry already on the server is still
        # replaced: the post goes out and the scheduled copy is dropped.
        view.scheduled_at = None
        view.update_compose_state()

    def update_chrome(self):
        """Refresh the schedule banner, post label and schedule button
        from the view's current timed/edit/posting state."""
        view = self._host()
        timed = self.is_timed()
        if timed:
            from .scheduled import format_scheduled_at
            # Translators: %s is a date and time, e.g. "October 9, 2026 ·
            # 14:30".
            self.schedule_banner.set_title(
                _('Scheduled for %s') % format_scheduled_at(
                    view.scheduled_at))
        self.schedule_banner.set_revealed(timed)
        view.post_button.set_label(_('_Schedule') if timed else _('_Post'))
        view.post_button.set_tooltip_text(
            _('Schedule (Ctrl+Enter)') if timed else _('Post (Ctrl+Enter)'))
        # An edit of a published post cannot be moved to another time.
        self.schedule_button.set_visible(view.editing_id is None)
        self.schedule_button.set_sensitive(
            not view.is_posting() and not view.is_frozen())
        self.schedule_button.set_tooltip_text(
            _('Change Time') if timed else _('Schedule Post'))