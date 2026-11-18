# SPDX-License-Identifier: GPL-3.0-or-later
"""The composer's draft: the snapshot it saves and the restore.

A draft is a settings dictionary plus the text, and the composer's job is
to take that snapshot, store it locally when the session goes away, and
put it back without accepting a transition it never started. A mixin of
``BabbleComposeView``; ``_ComposeState`` and ``_ThreadProgress`` live here
because the draft and the thread restore are the two readers.
"""
import datetime
from copy import deepcopy
from dataclasses import dataclass

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')


from .i18n import gettext as _  # noqa: E402

from . import appsettings  # noqa: E402
from .composeoptions import POLL_DURATIONS, VISIBILITIES  # noqa: E402
from .drafts import Attachment, Draft, drop_local_draft  # noqa: E402
from .preferences import LANGUAGES  # noqa: E402
from .drafts import store_local_draft  # noqa: E402



@dataclass(frozen=True)
class _ThreadProgress:
    """Where the composer stands in a thread: the number shown in the
    title, the length of an edited post's thread, and the end of the
    post before."""
    number: int
    total: int | None
    preview: str

@dataclass
class _ComposeState:
    draft: Draft
    editing_id: str | None
    quoted_status_id: str | None
    scheduled_id: str | None
    scheduled_at: str | None
    scheduled_on_done: object
    media_ids: tuple[str, ...]
    scheduled_fields: dict
    thread: _ThreadProgress | None


class ComposeDraft:
    def snapshot(self):
        return Draft(
            text=self.text(),
            visibility=self.visibility_row.get_selected(),
            language=self.language_row.get_selected(),
            language_code=self._languages[self.language_row.get_selected()][0],
            cw_visible=self.cw_toggle.get_active(),
            cw_text=self.spoiler_entry.get_text(),
            attachments=[Attachment(path=row.path,
                                    alt=row.alt_entry.get_text())
                         for row in self.attachments() if not row.existing],
            poll=self.poll_state(),
            in_reply_to_id=self.in_reply_to_id)

    def _valid_draft_settings(self, draft):
        if not 0 <= draft.visibility < len(VISIBILITIES) \
                or (draft.language_code is None
                    and not 0 <= draft.language < len(LANGUAGES)) \
                or (draft.poll.duration_seconds is None
                    and not 0 <= draft.poll.expires < len(POLL_DURATIONS)) \
                or len(draft.poll.options) > self.poll_limits()['max_options']:
            self._show_error(_('Could not restore draft'),
                             _('The saved draft has unsupported settings.'))
            return False
        return True

    def restore(self, draft):
        """Put *draft*, a :class:`drafts.Draft`, back into the composer."""
        if not self._valid_draft_settings(draft):
            return False
        self._set_buffer(draft.text)
        self.visibility_row.set_selected(draft.visibility)
        self._select_language(draft.language_code or LANGUAGES[draft.language][0])
        self.cw_toggle.set_active(draft.cw_visible)
        self.spoiler_entry.set_text(draft.cw_text)
        self.clear_attachments()
        for attachment in draft.attachments:
            self.add_attachment_path(attachment.path, alt=attachment.alt)
        self.in_reply_to_id = draft.in_reply_to_id
        for entry in self.poll_entries:
            entry.set_text('')
        self.poll_toggle.set_active(draft.poll.active)
        for index, text in enumerate(draft.poll.options):
            if index >= len(self.poll_entries):
                self.on_poll_add_choice(self.poll_add)
            self.poll_entries[index].set_text(text or '')
        if draft.poll.duration_seconds is not None:
            self._select_poll_duration(draft.poll.duration_seconds)
        else:
            # A legacy draft names a position in the fixed list.
            self._select_poll_duration(POLL_DURATIONS[draft.poll.expires][0])
        self._scheduled_fields['poll_flags'] = {
            'multiple': draft.poll.multiple, 'hide_totals': draft.poll.hide_totals}
        self.update_compose_state()
        return True

    def is_empty(self):
        return not (self.text() or self.attachments()
                    or self.spoiler_entry.get_text()
                    or any(e.get_text() for e in self.poll_entries))

    def save_local_draft(self, *_args):
        """Keep the unsent draft for the next launch; the dialog calls
        this when it closes with content still in it. One account's
        drafts cannot be restored on another one: ids in them are
        server-local."""
        if self._posting or self.editing_id is not None \
                or self.scheduled_id is not None or self.is_empty():
            return
        content = self.snapshot()
        if content == self._saved_local_content:
            # Closing again without touching anything must not fill the
            # list with copies of one draft.
            return
        # A changed draft replaces its earlier saved version, so posting
        # it later leaves no stale copy behind to restore.
        previous = self._saved_local_raw
        self._saved_local_raw = content.as_stored(
            self.local_draft_key,
            datetime.datetime.now(datetime.timezone.utc).isoformat())
        self._saved_local_content = content
        store_local_draft(appsettings.settings(), self._saved_local_raw, previous)

    def drop_saved_local_draft(self):
        """The saved copy of a draft that was just sent has to stop
        being offered as something to restore."""
        if self._saved_local_raw is None:
            return
        drop_local_draft(appsettings.settings(), self._saved_local_raw)
        self._saved_local_raw = None
        self._saved_local_content = None

    def _accept_transition(self):
        if self._replacement is not None:
            self._toast(_('Remove the original scheduled post before '
                          'starting another post.'))
            return False
        if self._posting:
            self._toast(_('Finish sending this post first.'))
            return False
        return True

    def _clear_submission_mode(self):
        if self._replacement is not None:
            return False
        self.editing_id = None
        self.quoted_status_id = None
        self.scheduled_id = None
        self.scheduled_at = None
        self.scheduled_on_done = None
        self._pending_media_ids = []
        self._scheduled_fields = {}
        self._quote_state_generation += 1
        self._pre_paste_offset = None
        self._set_thread(None)
        return True

    def _capture_state(self):
        return _ComposeState(
            self.snapshot(), self.editing_id, self.quoted_status_id,
            self.scheduled_id, self.scheduled_at, self.scheduled_on_done,
            tuple(self._pending_media_ids)
            + tuple(row.uploaded_id for row in self.attachments()
                    if row.existing),
            deepcopy(self._scheduled_fields),
            self._thread)

    def _restore_state(self, state):
        if not self._clear_submission_mode():
            return False
        self.restore(state.draft)
        self.editing_id = state.editing_id
        self.quoted_status_id = state.quoted_status_id
        self.scheduled_id = state.scheduled_id
        self.scheduled_at = state.scheduled_at
        self.scheduled_on_done = state.scheduled_on_done
        self._pending_media_ids = list(state.media_ids)
        self._scheduled_fields = deepcopy(state.scheduled_fields)
        self._set_thread(state.thread)
        self.update_compose_state()
        return True

    def on_draft_changed(self, *_args):
        self.update_compose_state()

    def restore_stashing(self, draft):
        """Restore a recovered draft, keeping the content in progress
        on the draft stack so the banner above the buffer can bring it
        back."""
        if not self._accept_transition():
            return False
        if not self._valid_draft_settings(draft):
            return False
        if not self.is_empty():
            self._draft_stack = [self._capture_state()]
        self._clear_submission_mode()
        self.restore(draft)
        return True
