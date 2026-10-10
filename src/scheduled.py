# SPDX-License-Identifier: GPL-3.0-or-later

"""Scheduled posts and server-side drafts.

Mastodon keeps scheduled statuses on the server
(``/api/v1/scheduled_statuses``). Like Tuba, Babble stores *drafts* in the
same place, dated far in the future (more than :data:`DRAFT_YEAR`), so
they are never published. This module provides

* :func:`is_draft`, :func:`draft_scheduled_at`, :func:`format_scheduled_at`
* :class:`SchedulePickerDialog`, a date/time picker used by the composer
  and by *Reschedule*
* :class:`ScheduledPostsPage` and :class:`DraftPostsPage`, the two lists.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error  # noqa: E402

# Tuba: scheduled statuses dated in a year after this one are drafts.
# NOTE: revisit in the year 3000.
DRAFT_YEAR = 5000
DRAFT_OFFSET_YEARS = 3000

# Mastodon rejects schedules less than 5 minutes ahead; keep a margin for
# the time the user takes to press the button.
MIN_LEAD_SECONDS = 5 * 60 + 30
# Tuba hides *Edit* for posts that go out within the hour.
EDIT_MIN_LEAD_SECONDS = 60 * 60
MAX_THUMBNAILS = 4
THUMBNAIL_SIZE = 64


# Date helpers

def _parse(iso):
    """GLib.DateTime for an ISO 8601 string (UTC when it has no offset)."""
    if not iso:
        return None
    return GLib.DateTime.new_from_iso8601(iso, GLib.TimeZone.new_utc())


def _iso_utc(date_time):
    """'YYYY-MM-DDTHH:MM:SS.mmmZ' for a GLib.DateTime."""
    utc = date_time.to_utc()
    return '%s.%03dZ' % (utc.format('%Y-%m-%dT%H:%M:%S'),
                         utc.get_microsecond() // 1000)


def is_draft(scheduled):
    """True when *scheduled* is dated in a year after :data:`DRAFT_YEAR`."""
    date_time = _parse((scheduled or {}).get('scheduled_at'))
    return date_time is not None and date_time.get_year() > DRAFT_YEAR


def draft_scheduled_at():
    """ISO 8601 UTC time 3000 years from now: the draft marker date."""
    return _iso_utc(GLib.DateTime.new_now_utc().add_years(DRAFT_OFFSET_YEARS))


def format_scheduled_at(iso):
    """Localized date and time of *iso* in the local time zone."""
    date_time = _parse(iso)
    if date_time is None:
        return ''
    local = date_time.to_local()
    # Translators: strftime format of a scheduled date and time, e.g.
    # "October 9, 2026 · 14:30".
    formatted = local.format(_('%B %-e, %Y · %H:%M'))
    return formatted if formatted is not None else local.format('%c')


def _seconds_until(iso):
    date_time = _parse(iso)
    if date_time is None:
        return 0
    return date_time.difference(GLib.DateTime.new_now_utc()) / 1000000


# Picker

class SchedulePickerDialog(Adw.Dialog):
    """Pick the date, time and time zone for a scheduled post.

    *initial_iso* preselects an existing schedule (default: one hour from
    now, rounded up to five minutes). *on_picked(iso8601_utc)* runs once
    the user accepts a time that is far enough in the future."""

    __gtype_name__ = 'BabbleSchedulePickerDialog'

    def __init__(self, initial_iso=None, button_label=None, title=None,
                 on_picked=None):
        super().__init__(title=title or _('Schedule Post'),
                         content_width=400, follows_content_size=True)
        self.on_picked = on_picked
        self._result = None

        initial = _parse(initial_iso)
        if initial is None or initial.get_year() > DRAFT_YEAR:
            initial = self._default_initial()
        initial = initial.to_local()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_a: self.close())
        header.pack_start(cancel)
        self.schedule_button = Gtk.Button(
            label=button_label or _('_Schedule'), use_underline=True)
        self.schedule_button.add_css_class('suggested-action')
        self.schedule_button.connect('clicked', self._on_accept)
        header.pack_end(self.schedule_button)
        toolbar.add_top_bar(header)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                         margin_top=6, margin_bottom=18, margin_start=18,
                         margin_end=18)

        self.calendar = Gtk.Calendar(halign=Gtk.Align.CENTER)
        self.calendar.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_('Date')])
        self.calendar.select_day(initial)
        column.append(self.calendar)

        group = Adw.PreferencesGroup()
        time_row = Adw.ActionRow(title=_('Time'))
        time_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.hour_spin = self._make_spin(23, initial.get_hour(), _('Hour'))
        self.minute_spin = self._make_spin(59, initial.get_minute(),
                                           _('Minute'))
        time_box.append(self.hour_spin)
        time_box.append(Gtk.Label(label=':'))
        time_box.append(self.minute_spin)
        time_row.add_suffix(time_box)
        group.add(time_row)

        local_id = GLib.TimeZone.new_local().get_identifier() or 'UTC'
        self._zones = [local_id] + (['UTC'] if local_id != 'UTC' else [])
        self.zone_row = Adw.ComboRow(title=_('Time Zone'),
                                     model=Gtk.StringList.new(self._zones))
        group.add(self.zone_row)
        column.append(group)

        self.hint = Gtk.Label(
            label=_('Choose a time at least 5 minutes in the future.'),
            wrap=True, xalign=0.0)
        self.hint.add_css_class('dimmed')
        self.hint.add_css_class('caption')
        column.append(self.hint)

        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, child=column)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)
        self.set_default_widget(self.schedule_button)

        self.calendar.connect('day-selected', self._validate)
        self.hour_spin.connect('value-changed', self._validate)
        self.minute_spin.connect('value-changed', self._validate)
        self.zone_row.connect('notify::selected', self._validate)
        self._validate()

    @staticmethod
    def _default_initial():
        """Now plus one hour, rounded up to the next five minutes."""
        target = GLib.DateTime.new_now_local().add_hours(1)
        minutes = (target.get_minute() // 5 + 1) * 5
        return target.add_minutes(minutes - target.get_minute()) \
            .add_seconds(-target.get_second())

    @staticmethod
    def _make_spin(upper, value, label):
        spin = Gtk.SpinButton.new_with_range(0, upper, 1)
        spin.set_value(value)
        spin.set_wrap(True)
        spin.set_numeric(True)
        spin.update_property([Gtk.AccessibleProperty.LABEL], [label])
        spin.connect('output', SchedulePickerDialog._on_output)
        return spin

    @staticmethod
    def _on_output(spin):
        spin.set_text('%02d' % spin.get_value_as_int())
        return True

    def _chosen(self):
        date = self.calendar.get_date()
        index = self.zone_row.get_selected()
        zone_id = self._zones[index] if index < len(self._zones) else 'UTC'
        if zone_id == 'UTC':
            zone = GLib.TimeZone.new_utc()
        else:
            zone = GLib.TimeZone.new_local()
        return GLib.DateTime.new(
            zone, date.get_year(), date.get_month(),
            date.get_day_of_month(), self.hour_spin.get_value_as_int(),
            self.minute_spin.get_value_as_int(), 0)

    def _validate(self, *_args):
        self._result = self._chosen()
        lead = self._result.difference(
            GLib.DateTime.new_now_utc()) / 1000000
        valid = lead >= MIN_LEAD_SECONDS
        self.schedule_button.set_sensitive(valid)
        self.hint.set_visible(not valid)

    def _on_accept(self, *_args):
        self._validate()
        if not self.schedule_button.get_sensitive():
            return
        iso = _iso_utc(self._result)
        self.close()
        if self.on_picked is not None:
            self.on_picked(iso)


# Row

class _Thumbnail(Gtk.Picture):
    """Picture with a fixed square size, whatever the image's own size."""

    __gtype_name__ = 'BabbleScheduledThumbnail'

    def do_measure(self, _orientation, _for_size):
        return THUMBNAIL_SIZE, THUMBNAIL_SIZE, -1, -1


class ScheduledRow(Gtk.ListBoxRow):
    """One scheduled post or draft; ``.scheduled`` is its JSON."""

    __gtype_name__ = 'BabbleScheduledRow'

    def __init__(self, client, scheduled, draft=False, on_removed=None,
                 on_refresh=None):
        super().__init__(activatable=draft)
        self.client = client
        self.scheduled = scheduled
        self.draft = draft
        self.on_removed = on_removed
        self.on_refresh = on_refresh
        self._busy = False
        self.add_css_class('scheduled-row')

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_top=10, margin_bottom=10, margin_start=12,
                         margin_end=6)
        header = Gtk.Box(spacing=6)
        self.title_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True,
                                     wrap_mode=Pango.WrapMode.WORD_CHAR,
                                     valign=Gtk.Align.CENTER)
        self.title_label.add_css_class('heading')
        header.append(self.title_label)
        self.buttons = []
        if draft:
            header.append(self._add_button(
                'document-edit-symbolic', _('Open'), self.on_activate))
            header.append(self._add_button(
                'user-trash-symbolic', _('Delete'), self._on_delete,
                destructive=True))
        else:
            self.edit_button = self._add_button(
                'document-edit-symbolic', _('Edit'), self._on_edit)
            header.append(self.edit_button)
            header.append(self._add_button(
                'alarm-symbolic', _('Reschedule'), self._on_reschedule))
            header.append(self._add_button(
                'folder-new-symbolic', _('Convert into a Draft'),
                self._on_convert))
            header.append(self._add_button(
                'user-trash-symbolic', _('Delete'), self._on_delete,
                destructive=True))
        column.append(header)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                            margin_end=6)
        column.append(self.body)
        self.set_child(column)
        self.bind(scheduled)

    def _add_button(self, icon, tooltip, callback, destructive=False):
        button = pages.icon_button(icon, tooltip, callback)
        if destructive:
            button.add_css_class('destructive-action')
        self.buttons.append(button)
        return button

    # Rendering

    def bind(self, scheduled):
        """Show *scheduled* (a fresh server copy after rescheduling)."""
        self.scheduled = scheduled
        params = scheduled.get('params') or {}
        if self.draft:
            self.title_label.set_label(_('Draft'))
        else:
            self.title_label.set_label(_('Scheduled for %s') %
                                       format_scheduled_at(
                                           scheduled.get('scheduled_at')))
            self.edit_button.set_visible(
                _seconds_until(scheduled.get('scheduled_at'))
                >= EDIT_MIN_LEAD_SECONDS)

        while (child := self.body.get_first_child()) is not None:
            self.body.remove(child)

        spoiler = (params.get('spoiler_text') or '').strip()
        if spoiler:
            label = Gtk.Label(label=spoiler, xalign=0.0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            label.add_css_class('heading')
            self.body.append(label)

        text = (params.get('text') or '').strip()
        if text:
            label = Gtk.Label(label=text, xalign=0.0, wrap=True, lines=4,
                              wrap_mode=Pango.WrapMode.WORD_CHAR,
                              ellipsize=Pango.EllipsizeMode.END)
            self.body.append(label)

        attachments = scheduled.get('media_attachments') or []
        if attachments:
            self.body.append(self._make_thumbnails(attachments))

        chips = self._make_chips(params, attachments)
        if chips is not None:
            self.body.append(chips)

        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [self.title_label.get_label() + '. ' + text[:200]])

    @staticmethod
    def _make_thumbnails(attachments):
        from .timeline import _TextureCache
        box = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        for attachment in attachments[:MAX_THUMBNAILS]:
            picture = _Thumbnail(
                content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                overflow=Gtk.Overflow.HIDDEN,
                alternative_text=attachment.get('description') or '')
            picture.add_css_class('scheduled-thumb')
            url = attachment.get('preview_url') or (
                attachment.get('url')
                if attachment.get('type') == 'image' else None)
            if url:
                _TextureCache.load(url, picture)
            box.append(picture)
        return box

    @staticmethod
    def _chip(icon, text):
        box = Gtk.Box(spacing=4)
        box.add_css_class('dimmed')
        box.add_css_class('caption')
        box.append(Gtk.Image(icon_name=icon, pixel_size=12))
        box.append(Gtk.Label(label=text))
        return box

    def _make_chips(self, params, attachments):
        chips = []
        visibility = params.get('visibility') or 'public'
        icon, name = {
            'public': ('network-workgroup-symbolic', _('Public')),
            'unlisted': ('channel-insecure-symbolic', _('Unlisted')),
            'private': ('channel-secure-symbolic', _('Followers Only')),
            'direct': ('mail-unread-symbolic', _('Direct')),
        }.get(visibility, ('network-workgroup-symbolic', visibility))
        chips.append(self._chip(icon, name))

        count = len(attachments) or len(params.get('media_ids') or [])
        if count:
            chips.append(self._chip(
                'mail-attachment-symbolic',
                ngettext('%d attachment', '%d attachments', count) % count))
        poll = params.get('poll')
        if poll:
            options = len(poll.get('options') or [])
            chips.append(self._chip(
                'view-list-bullet-symbolic',
                ngettext('Poll · %d option', 'Poll · %d options', options)
                % options))
        if params.get('in_reply_to_id'):
            chips.append(self._chip('mail-reply-sender-symbolic',
                                    _('Reply')))
        wrap = Adw.WrapBox(child_spacing=12, line_spacing=2)
        for chip in chips:
            wrap.append(chip)
        return wrap

    # Actions

    def _set_busy(self, busy):
        self._busy = busy
        for button in self.buttons:
            button.set_sensitive(not busy)

    def _open_in_composer(self, draft):
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        dialog.start_scheduled(self.scheduled, draft=draft,
                               on_done=self.on_refresh)
        window.present_composer()

    def on_activate(self):
        """Draft rows open in the composer."""
        if self.draft and not self._busy:
            self._open_in_composer(True)

    def _on_edit(self):
        self._open_in_composer(False)

    def _on_reschedule(self):
        dialog = SchedulePickerDialog(
            self.scheduled.get('scheduled_at'), _('_Reschedule'),
            _('Reschedule Post'), self._on_rescheduled)
        dialog.present(self)

    def _on_rescheduled(self, iso):
        self._put_schedule(iso, _('Post rescheduled'))

    def _on_convert(self):
        self._put_schedule(draft_scheduled_at(),
                           _('Converted into a draft'))

    def _put_schedule(self, iso, success_text):
        self._set_busy(True)

        def finished(client, result, _data=None):
            self._set_busy(False)
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                pages.report_error(self, _('Could not reschedule'), error)
                return
            if not isinstance(data, dict) or 'scheduled_at' not in data:
                data = dict(self.scheduled, scheduled_at=iso)
            # Resolve the toast target while the row is still in the
            # tree: _remove() unparents it and show_toast would no-op.
            window = pages.window_of(self)
            if is_draft(data):
                self._remove()
            else:
                self.bind(data)
            if window is not None:
                window.show_toast(success_text)

        self.client.request_async(
            'PUT', '/scheduled_statuses/%s' % self.scheduled['id'],
            form={'scheduled_at': iso}, callback=finished)

    def _on_delete(self):
        if self.draft:
            heading = _('Delete Draft Post?')
            body = _('The draft will be permanently deleted.')
        else:
            heading = _('Delete Scheduled Post?')
            body = _('The post will not be published.')
        pages.confirm(self, heading, body, _('_Delete'), self._delete)

    def _delete(self):
        self._set_busy(True)

        def finished(client, result, _data=None):
            self._set_busy(False)
            try:
                client.request_finish(result)
            except GLib.Error as error:
                pages.report_error(self, _('Could not delete'), error)
                return
            # Resolve the window before _remove() unparents the row.
            window = pages.window_of(self)
            self._remove()
            if window is not None:
                window.show_toast(
                    _('Draft deleted') if self.draft
                    else _('Scheduled post deleted'))

        self.client.request_async(
            'DELETE', '/scheduled_statuses/%s' % self.scheduled['id'],
            callback=finished)

    def _remove(self):
        if self.on_removed is not None:
            self.on_removed(self)


# Pages

class _FilteredList(pages.PagedList):
    """PagedList that filters each page client-side.

    The endpoint mixes scheduled posts and drafts, so a page can filter to
    nothing while more pages follow: keep fetching until something shows
    up or the pages run out."""

    __gtype_name__ = 'BabbleScheduledFilteredList'

    def __init__(self, client, make_row, want_drafts, **kwargs):
        self.want_drafts = want_drafts
        super().__init__(client, '/scheduled_statuses', make_row, **kwargs)

    def _on_loaded(self, client, result, generation):
        if generation != self._generation:
            return
        self._loading = False
        self.more_spinner.set_visible(False)
        try:
            data, next_params, _prev = client.request_finish_paged(result)
        except GLib.Error as error:
            if not self.rows:
                self.error_page.set_description(
                    summarize_error(error.message))
                self.stack.set_visible_child_name('error')
            else:
                pages.report_error(self, _('Could not load more'), error)
            return
        data = data or []
        self._next = next_params if data else None
        items = [item for item in data
                 if is_draft(item) == self.want_drafts]
        if not items and self._next:
            self.load_more()
            return
        self.append_items(items)


class _ScheduledPage(Adw.Bin):
    """Shared implementation of the two pages."""

    DRAFTS = False

    def __init__(self, client, empty_title, empty_icon, empty_description):
        super().__init__()
        self.client = client
        self.list = _FilteredList(
            client, self._make_row, self.DRAFTS,
            empty_title=empty_title, empty_icon=empty_icon,
            empty_description=empty_description)
        self.set_child(self.list)

    def _make_row(self, scheduled):
        return ScheduledRow(self.client, scheduled, draft=self.DRAFTS,
                            on_removed=self.list.remove_row,
                            on_refresh=self.refresh)

    def refresh(self):
        self.list.refresh()


class ScheduledPostsPage(_ScheduledPage):
    """Posts waiting to be published at a set time."""

    __gtype_name__ = 'BabbleScheduledPostsPage'
    DRAFTS = False

    def __init__(self, client):
        super().__init__(
            client, _('No Scheduled Posts'), 'alarm-symbolic',
            _('Posts you schedule will appear here until they are '
              'published.'))


class DraftPostsPage(_ScheduledPage):
    """Posts saved on the server but not published."""

    __gtype_name__ = 'BabbleDraftPostsPage'
    DRAFTS = True

    def __init__(self, client):
        super().__init__(
            client, _('No Draft Posts'), 'document-edit-symbolic',
            _('Posts you save as drafts will appear here.'))
