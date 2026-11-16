# SPDX-License-Identifier: GPL-3.0-or-later
"""Posting: validation, upload, send, cleanup and reset.

The last half of the composer's life: what is checked before an upload
starts, the posting UI, the send and its completion, the scheduled-post
cleanup, the retry, and the reset that makes the view reusable. A mixin of
``BabbleComposeView``; ``_end_inhibition`` lives here because the send and
the dialog's abandon path are its only other callers.
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import appsettings, pagenav  # noqa: E402
from .api import scheduled as api_scheduled  # noqa: E402
from .api import statuses as api_statuses  # noqa: E402
from .capabilities import QUOTES_API_VERSION  # noqa: E402
from .composedraft import _ThreadProgress  # noqa: E402
from .composeoptions import VISIBILITIES, _has_setting  # noqa: E402
from .composeschedule import _ScheduledReplacement  # noqa: E402
from .hashtagcompleter import remember  # noqa: E402
from .threadchain import preview_tail  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from src.composeoptions import default_language, default_visibility  # noqa: E402



def _end_inhibition(inhibition):
    """Give back the logout and suspend inhibition a composer holds in
    the dict *inhibition*, if any."""
    app, cookie = inhibition['app'], inhibition['cookie']
    inhibition.update(app=None, cookie=0)
    if cookie:
        app.uninhibit(cookie)


class ComposePost:
    def on_post_clicked(self, _button):
        if self._posting or self._replacement is not None:
            return
        if not (self.text() or self.attachments() or self.quoted_status_id
                or self._pending_media_ids):
            return
        if not self._validate_before_upload():
            return
        self._posting = True
        self._inhibit_session()
        pending = [row for row in self.attachments()
                   if row.uploaded_id is None]
        self.uploads.begin(len(pending))
        self.set_posting_ui(True)
        for row in pending:
            self.uploads.upload_one(row)
        if not pending:
            self.send_status()

    def _validate_before_upload(self):
        """Refuse an invalid draft before anything is uploaded, so a
        rejected post never leaves orphaned media behind."""
        if not self._within_char_limit():
            self._toast(_('Post is too long'))
            return False
        if self.cw_toggle.get_active() and not self.spoiler_entry.get_text():
            self._toast(_('Add content warning text'))
            return False
        if self.poll_toggle.get_active():
            options = self._poll_options()
            if len(options) < 2:
                self._toast(_('Add at least two poll choices'))
                return False
            if len(set(options)) != len(options):
                self._toast(_('Poll choices must be unique'))
                return False
            if self.attachments() or self._pending_media_ids:
                self._toast(_('A poll cannot be combined with media'))
                return False
        return True

    def set_posting_ui(self, active):
        cleanup = self._replacement is not None
        locked = active or cleanup
        # Only the cleanup-only state freezes the content: disabling it on
        # every post would take keyboard focus out of the text view.
        self._content.set_sensitive(not cleanup)
        self.text_view.set_editable(not cleanup)
        self.reply_banner.set_sensitive(not locked)
        self.schedule_banner.set_sensitive(not locked)
        for widget in (self.cw_toggle, self.poll_toggle,
                       self.mention_button, self.emoji_button,
                       self.thread_toggle, self.visibility_row,
                       self.language_row):
            widget.set_sensitive(not locked)
        if active:
            self.mention_completer.dismiss()
            self.hashtag_completer.dismiss()
        self.attach.set_locked(locked)
        self.progress_spinner.set_visible(active)
        self.update_compose_state()

    def _finish_posting(self):
        self._posting = False
        _end_inhibition(self._inhibition)
        self.set_posting_ui(False)

    def _inhibit_session(self):
        """Keep the session from logging out or suspending while the
        post is uploaded and sent: the round would be cut off half way."""
        app = Gio.Application.get_default()
        if self._inhibition['cookie'] or not isinstance(app, Gtk.Application):
            return
        root = self.get_root()
        # Translators: why logging out or suspending is held back, as the
        # session shows it while a post is being uploaded and sent.
        reason = _('Sending a post')
        cookie = app.inhibit(root if isinstance(root, Gtk.Window) else None,
                             Gtk.ApplicationInhibitFlags.LOGOUT
                             | Gtk.ApplicationInhibitFlags.SUSPEND, reason)
        if cookie:
            self._inhibition.update(app=app, cookie=cookie)

    def _on_upload_failed(self, message):
        """Toast half of an upload failure. The round already counted it."""
        self._finish_posting()
        self._show_error(_('Could not upload media'), message)

    def send_status(self):
        params = {
            'visibility': VISIBILITIES[self.visibility_row.get_selected()][0],
            'language': self._languages[self.language_row.get_selected()][0],
        }
        for key in ('sensitive', 'quote_approval_policy'):
            if key in self._scheduled_fields:
                params[key] = self._scheduled_fields[key]
        if self.cw_toggle.get_active():
            cw_text = self.spoiler_entry.get_text()
            if cw_text:
                params['spoiler_text'] = cw_text
                params['sensitive'] = True
        elif _has_setting('default-sensitive') \
                and 'sensitive' not in params \
                and appsettings.settings().get_boolean('default-sensitive'):
            # The preference is mirrored to the account's server defaults,
            # but a client that leaves the field out inherits whatever the
            # server's web client happened to send last -- and Babble is
            # not it. So the setting that is already in Preferences
            # decides here too.
            params['sensitive'] = True
        if self.poll_toggle.get_active():
            options = [entry.get_text() for entry in self.poll_entries
                       if entry.get_text().strip()]
            params['poll'] = {
                'options': options,
                'expires_in': self._poll_durations[
                    self.poll_expiry.get_selected()][0],
            }
            params['poll'].update(self._scheduled_fields.get('poll_flags', {}))
        if self.in_reply_to_id is not None:
            params['in_reply_to_id'] = self.in_reply_to_id
        if self.quoted_status_id is not None:
            params['quoted_status_id'] = self.quoted_status_id
        # Quote posts are Mastodon 4.5.0 (API 7). A server whose number is
        # known to be lower would ignore both fields and post a plain
        # status, so they are left off rather than sent for nothing.
        # when_unknown=True: a quote can only have been started from a
        # status carrying `quote_approval`, which is evidence the server
        # has the feature, and a scheduled post restored from the server
        # carries them back -- an unreported version must not strip them.
        if not self.client.is_supported(QUOTES_API_VERSION, when_unknown=True):
            params.pop('quoted_status_id', None)
            params.pop('quote_approval_policy', None)
        if self.scheduled_at is not None:
            params['scheduled_at'] = self.scheduled_at
        # Uploaded rows first, then media reused from the edited post.
        media_ids = [row.uploaded_id for row in self.attachments()
                     if row.uploaded_id]
        media_ids += [mid for mid in self._pending_media_ids
                      if mid not in media_ids]
        if media_ids or self.editing_id is not None:
            # An edit sends the list even when empty: that is how every
            # attachment is taken off.
            params['media_ids'] = media_ids
        if self.editing_id is not None:
            # The server keeps a kept attachment's old description unless
            # it is sent again.
            params['media_attributes'] = [
                {'id': row.uploaded_id,
                 'description': row.alt_entry.get_text()}
                for row in self.attachments() if row.existing]
            context = (self.editing_id, self.uploads.generation)
            api_statuses.edit_async(self.client, self.editing_id,
                                    self.text(), params=params,
                                    callback=lambda c, r:
                                    self.on_post_done(c, r, context))
        else:
            context = (None, self.uploads.generation)
            api_statuses.post_async(self.client, self.text(), params=params,
                                    callback=lambda c, r:
                                    self.on_post_done(c, r, context))

    def on_post_done(self, client, result, _user_data=None):
        editing_id, generation = _user_data
        try:
            if editing_id is not None:
                status = api_statuses.edit_finish(client, result)
            else:
                status = api_statuses.post_finish(client, result)
        except GLib.Error as e:
            if generation != self.uploads.generation:
                return
            self._finish_posting()
            self._show_error(_('Could not post'), e.message)
            return
        if generation != self.uploads.generation:
            return
        self.drop_saved_local_draft()
        if editing_id is not None:
            message = _('Post edited')
        elif self.scheduled_at is not None:
            message = _('Post scheduled')
        else:
            message = _('Post sent')
        if self.scheduled_id is not None:
            self._replacement = _ScheduledReplacement(
                self.scheduled_id, status, self.scheduled_on_done, message)
            self._finish_posting()
            self._retry_scheduled_removal()
            return status
        self._finish_posting()
        return self._complete_post(status, message)

    def _complete_post(self, status, message):
        if isinstance(status, dict):
            remember(self.client, status.get('tags'))
        # Resolve the toast target before the dialog closes.
        window = pagenav.window_of(self)
        root = self.get_root()
        was_edit = self.editing_id is not None
        was_scheduled = self.scheduled_id is not None
        # Still on the server, timed or as a draft; not an entry whose
        # time was cleared, which went out now. It is not a status yet,
        # so nothing can reply to it: it never chains a thread's next post.
        kept_scheduled = self.scheduled_at is not None
        if self.thread_toggle.get_active() and not was_edit \
                and not kept_scheduled and not was_scheduled:
            # Chain the next post in the thread; media and poll must not
            # be sent twice.
            self.in_reply_to_id = str(status.get('id') or '') or None
            number = self._thread.number if self._thread is not None else 1
            self._set_thread(_ThreadProgress(
                number + 1, None, preview_tail(self.text())))
            self._set_buffer('')
            self.clear_attachments()
            self._pending_media_ids = []
            self.quoted_status_id = None
            self._scheduled_fields = {}
            self._set_attach_sensitive(True)
            self.poll_toggle.set_active(False)
            self.uploads.clear_results()
            self.update_compose_state()
        else:
            self.reset()
            dialog = self.get_ancestor(Adw.Dialog)
            if dialog is not None and dialog.get_parent() is not None:
                dialog.close()
        if window is not None:
            window.show_toast(message)
        elif root is not None and hasattr(root, 'show_toast'):
            root.show_toast(message)
        return status

    def _retry_scheduled_removal(self, *_args):
        transaction = self._replacement
        if transaction is None or self._cleanup_busy:
            return
        self._cleanup_busy = True
        self._cleanup_cancellable = Gio.Cancellable()
        if self._cleanup_banner is not None:
            self._cleanup_banner.set_sensitive(False)
        view_ref = self.weak_ref()
        client = self.client

        def finished(_source, result, _data=None):
            error = None
            try:
                api_scheduled.delete_finish(client, result)
            except GLib.Error as failure:
                if failure.domain != 'mastodon-http-error-quark' or failure.code != 404:
                    error = failure
            view = view_ref()
            if view is None or view._replacement is not transaction:
                return
            view._cleanup_busy = False
            view._cleanup_cancellable = None
            if error is not None:
                view._show_cleanup_warning()
                view._show_error(_('Could not remove original scheduled post'),
                                 error.message)
                return
            view._replacement = None
            if view._cleanup_banner is not None:
                view._cleanup_banner.set_revealed(False)
            view.set_posting_ui(False)
            if transaction.on_done is not None:
                transaction.on_done()
            view._complete_post(transaction.accepted, transaction.message)

        api_scheduled.delete_async(
            client, transaction.original_id, callback=finished,
            cancellable=self._cleanup_cancellable)

    def _show_cleanup_warning(self):
        if self._cleanup_banner is None:
            self._cleanup_banner = Adw.Banner(
                use_markup=False,
                title=_('The replacement was saved, but the original scheduled '
                        'post could not be removed. Both may be published.'),
                button_label=_('_Retry Removal'))
            connect_weak(self._cleanup_banner, 'button-clicked',
                               self._retry_scheduled_removal)
            self._toolbar_view.add_top_bar(self._cleanup_banner)
        self._cleanup_banner.set_sensitive(True)
        self._cleanup_banner.set_revealed(True)
        self.update_compose_state()

    def reset(self):
        if self._replacement is not None:
            return False
        predecessor = self._draft_stack[-1] if self._draft_stack else None
        self._draft_stack = []
        # Discarding the draft abandons any upload it started. The
        # generation bump is what tells a still-in-flight reply that it
        # belongs to a round that no longer exists; cancelling alone only
        # turns the request into an error the reply would still act on.
        self.uploads.abandon()
        # With its uploads gone, a posting round in flight is over.
        if self._posting:
            self._finish_posting()
        self.mention_completer.dismiss()
        self.hashtag_completer.dismiss()
        self._set_buffer('')
        self.spoiler_entry.set_text('')
        self.cw_toggle.set_active(False)
        self.poll_toggle.set_active(False)
        for entry in self.poll_entries:
            entry.set_text('')
        self._select_default_poll_duration()
        self.thread_toggle.set_active(False)
        self.clear_attachments()
        self._set_attach_sensitive(True)
        self._clear_submission_mode()
        # Back to the presets, so a DM or a reply's visibility does not
        # carry over; before the pop, so a stacked draft keeps its own.
        self._select_visibility(default_visibility())
        self._select_language(default_language())
        self.in_reply_to_id = None
        if predecessor is not None:
            self._restore_state(predecessor)
        self.update_compose_state()

    def on_discard_clicked(self, _button):
        if self._replacement is not None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Discard Draft?'),
            body=_('The text, attachments and poll of this post will be '
                   'lost'))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('discard', _('_Discard'))
        dialog.set_response_appearance('discard',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_discard_response)
        dialog.present(self)

    def on_discard_response(self, _dialog, response):
        if response == 'discard':
            self.reset()
            self.text_view.grab_focus()
