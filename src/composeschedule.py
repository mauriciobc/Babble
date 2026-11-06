# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer mode entry and schedule chrome.

Reply, mention, edit, redraft, quote and scheduled-post scaffolding live
here, together with the schedule banner and picker. The composer view
owns the draft state and chrome widgets; this module shapes the mode
transitions and scheduled payloads. It never imports ``compose``.
"""

from dataclasses import dataclass

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GLib, GObject  # noqa: E402

from . import pages  # noqa: E402
from .entities import VISIBILITIES as VISIBILITY_CODES  # noqa: E402


@dataclass(frozen=True)
class _ScheduledReplacement:
    original_id: str
    accepted: dict
    on_done: object
    message: str


def scheduled_content(scheduled, *, max_poll_options):
    """Shape a scheduled-status payload into composer fields.

    Raises :class:`GLib.Error` when the server returned settings the
    composer cannot restore.
    """
    def unsupported():
        raise GLib.Error(_('The server returned unsupported post settings.'),
                         'mastodon-http-error-quark', 0)

    if not isinstance(scheduled, dict) or not isinstance(scheduled.get('id'), str) \
            or not scheduled['id']:
        unsupported()
    params = scheduled.get('params')
    if params is None:
        params = {}
    if not isinstance(params, dict):
        unsupported()
    content = {}
    for key in ('text', 'spoiler_text'):
        value = params.get(key)
        if value is None:
            value = ''
        if not isinstance(value, str):
            unsupported()
        content[key] = value
    visibility = params.get('visibility')
    if visibility is None:
        visibility = 'public'
    if visibility not in VISIBILITY_CODES:
        unsupported()
    content['visibility'] = visibility
    language = params.get('language')
    if language is not None and not isinstance(language, str):
        unsupported()
    content['language'] = language
    for key in ('in_reply_to_id', 'quoted_status_id'):
        value = params.get(key)
        if key == 'in_reply_to_id' and type(value) is int and value > 0:
            value = str(value)
        if value is not None and (not isinstance(value, str) or not value):
            unsupported()
        content[key] = value
    sensitive = params.get('sensitive')
    if sensitive is not None and not isinstance(sensitive, bool):
        unsupported()
    policy = params.get('quote_approval_policy')
    if policy is not None and policy not in ('public', 'followers', 'nobody'):
        unsupported()
    content.update(sensitive=sensitive, quote_approval_policy=policy)
    attachments = scheduled.get('media_attachments')
    media_ids = params.get('media_ids')
    if attachments is None:
        attachments = []
    if media_ids is None:
        media_ids = []
    if not isinstance(attachments, list) or not isinstance(media_ids, list):
        unsupported()
    ids = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            unsupported()
        value = attachment.get('id')
        if not isinstance(value, str) or not value:
            unsupported()
        ids.append(value)
    if any(not isinstance(value, str) or not value for value in media_ids):
        unsupported()
    content['media_ids'] = ids or list(media_ids)
    poll = params.get('poll')
    if poll is not None:
        if not isinstance(poll, dict):
            unsupported()
        options = poll.get('options')
        expiry = poll.get('expires_in')
        if not isinstance(options, list) \
                or not 2 <= len(options) <= max_poll_options \
                or any(not isinstance(o, str) or not o.strip() for o in options) \
                or len({o.strip() for o in options}) != len(options) \
                or type(expiry) is not int or expiry <= 0:
            unsupported()
        flags = {key: poll.get(key, False) for key in ('multiple', 'hide_totals')}
        if any(not isinstance(value, bool) for value in flags.values()):
            unsupported()
        poll = dict(options=list(options), expires_in=expiry, **flags)
    content['poll'] = poll
    return content


class ComposeSchedule(GObject.Object):
    """Mode transitions and schedule chrome for one composer.

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

    # Mode entry

    def start_reply(self, status_id, handle, visibility='public',
                    parent=None):
        """Push the current draft (if any) and prefill a reply; only one
        level of reply stack is kept, so the prior draft is recovered
        with :meth:`BabbleComposeView.pop_reply_state` on send or cancel.

        ``status_id`` None starts a brand-new message (a direct message
        from the conversations page): the draft in progress is kept, not
        replaced by an empty buffer. ``parent`` is the status being
        replied to, when the caller has it; a reply to a post that
        carries a content warning starts behind the same CW, like
        Phanpy does -- the field is shown based solely on whether the
        parent already carries one."""
        view = self._host()
        if not view._accept_transition():
            return False
        new_message = status_id is None and not handle
        previous = view._capture_state()
        view._clear_submission_mode()
        if new_message:
            if not view.is_empty():
                view._draft_stack = [previous]
        else:
            view._set_buffer('' if not handle else '@' + handle + ' ')
            view.buffer.place_cursor(view.buffer.get_end_iter())
            view._draft_stack = [previous]
        if status_id is not None and parent is not None:
            # The reply starts behind the parent's CW, like the CW
            # behaviour claims to work, and never behind one left over
            # from the stashed draft: the draft was set aside with its
            # own CW, and the reply needs the parent's state.
            parent_cw = parent.get('spoiler_text') or ''
            if parent_cw:
                view.cw_toggle.set_active(True)
                view.spoiler_entry.set_text(parent_cw)
            else:
                view.cw_toggle.set_active(False)
                view.spoiler_entry.set_text('')
        view.in_reply_to_id = str(status_id) if status_id is not None else None
        view._select_visibility(visibility)
        view._finish_begin()
        if status_id is not None:
            view._load_thread(str(status_id))
        return True

    def start_mention(self, account, direct=False):
        """Prefill a mention of *account* (``direct`` for a DM)."""
        view = self._host()
        if not view._accept_transition():
            return False
        acct = account.get('acct') or account.get('username') or ''
        previous = view._capture_state()
        view._clear_submission_mode()
        view._set_buffer('@%s ' % acct)
        view.buffer.place_cursor(view.buffer.get_end_iter())
        view.in_reply_to_id = None
        if direct:
            view._select_visibility('direct')
        view._draft_stack = [previous]
        view._finish_begin()
        return True

    def start_edit(self, status):
        """Load an existing post into the composer for a PUT."""
        view = self._host()
        if not view._accept_transition():
            return False
        view._clear_submission_mode()
        view.editing_id = str(status.get('id') or '') or None
        self._load_status_draft(status)
        view._load_thread(view.editing_id, status, editing=True)
        return True

    def start_redraft(self, status):
        """Refill the composer from a just-deleted post. The server keeps
        the media for about 24h, so the attachment ids can be reused."""
        view = self._host()
        if not view._accept_transition():
            return False
        view._clear_submission_mode()
        self._load_status_draft(status)
        return True

    def _load_status_draft(self, status):
        """Fill in the fields shared by editing and re-drafting a post."""
        view = self._host()
        view.clear_attachments()
        view.poll_toggle.set_active(False)
        for entry in view.poll_entries:
            entry.set_text('')
        view._select_default_poll_duration()
        source = dict(status)
        view._pending_media_ids = [
            attachment['id']
            for attachment in source.get('media_attachments') or []
            if attachment.get('id')]
        view._set_buffer(source.get('text') or '')
        view.buffer.place_cursor(view.buffer.get_end_iter())
        spoiler = source.get('spoiler_text') or ''
        view.cw_toggle.set_active(bool(spoiler))
        view.spoiler_entry.set_text(spoiler)
        if source.get('sensitive'):
            # The PUT carries a "sensitive" flag only when the caller
            # sends one, and the server does not keep it from the post
            # this draft came from -- the same silent loss the CW had.
            # So the loaded post's flag is carried, as the scheduled
            # path already does, and there is no way here to *clear*
            # it: the composer has no control for it.
            view._scheduled_fields['sensitive'] = True
        view._select_visibility(source.get('visibility') or 'public')
        view.in_reply_to_id = None
        view._draft_stack = []
        view._finish_begin()

    def start_quote(self, status):
        """Quote *status*: the server prepends the quoted post."""
        view = self._host()
        if not view._accept_transition():
            return False
        view._clear_submission_mode()
        view.quoted_status_id = str(status.get('id') or '') or None
        view.in_reply_to_id = None
        view._draft_stack = []
        view._finish_begin()
        return True

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        """Load a scheduled post or draft into the composer. Editing the
        content means re-scheduling, so the old entry is dropped once the
        new one is accepted."""
        view = self._host()
        if not view._accept_transition():
            return False
        try:
            params = scheduled_content(
                scheduled, max_poll_options=view.poll_limits()['max_options'])
        except GLib.Error as error:
            view._show_error(_('Could not edit scheduled post'), error.message)
            return False
        view._clear_submission_mode()
        view.clear_attachments()
        view.poll_toggle.set_active(False)
        for entry in view.poll_entries:
            entry.set_text('')
        view._select_default_poll_duration()
        from .scheduled import draft_scheduled_at
        view.scheduled_id = scheduled['id']
        view.scheduled_at = draft_scheduled_at() if draft \
            else scheduled.get('scheduled_at')
        view.scheduled_on_done = on_done
        view._pending_media_ids = params['media_ids']
        view._set_buffer(params.get('text') or '')
        view.buffer.place_cursor(view.buffer.get_end_iter())
        spoiler = params.get('spoiler_text') or ''
        view.cw_toggle.set_active(bool(spoiler))
        view.spoiler_entry.set_text(spoiler)
        view._select_visibility(params.get('visibility') or 'public')
        view.in_reply_to_id = params.get('in_reply_to_id')
        view.quoted_status_id = params['quoted_status_id']
        view._select_language(params['language'])
        for key in ('sensitive', 'quote_approval_policy'):
            if params[key] is not None:
                view._scheduled_fields[key] = params[key]
        poll = params['poll']
        if poll is not None:
            view.poll_toggle.set_active(True)
            for index, option in enumerate(poll['options']):
                if index >= len(view.poll_entries):
                    view.on_poll_add_choice(view.poll_add)
                view.poll_entries[index].set_text(option)
            view._select_poll_duration(poll['expires_in'])
            view._scheduled_fields['poll_flags'] = {
                key: poll[key] for key in ('multiple', 'hide_totals')}
        view._draft_stack = []
        view._finish_begin()
        return True

    # Schedule chrome

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
        if view._replacement is not None:
            return
        from .scheduled import SchedulePickerDialog
        dialog = SchedulePickerDialog(
            view.scheduled_at, _('_Set Time'),
            on_picked=pages.weak_method(self._on_schedule_picked))
        dialog.present(view)

    def _on_schedule_picked(self, iso):
        view = self._host()
        if view._replacement is not None:
            return
        view.scheduled_at = iso
        view.update_compose_state()

    def on_schedule_clear(self, _banner):
        view = self._host()
        if view._replacement is not None:
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
        self.schedule_button.set_sensitive(not view._posting)
        if view._replacement is not None:
            self.schedule_button.set_sensitive(False)
        self.schedule_button.set_tooltip_text(
            _('Change Time') if timed else _('Schedule Post'))
