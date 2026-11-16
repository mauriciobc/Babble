# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread and reply scaffolding: the preview, the chain, the prefill.

What the composer is answering, and what it shows above the text area: the
reply preview, the thread it belongs to, the mention target, and the
prefill a redraft or an edit needs. A mixin of ``BabbleComposeView``; the
mode entry points stay on ``ComposeDialog`` in ``composedialog``.
"""
import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .api import statuses as api_statuses  # noqa: E402
from .composedraft import _ThreadProgress  # noqa: E402
from .threadchain import own_thread, plain_text, preview_tail  # noqa: E402





class ComposeThread:
    def _build_preview(self):
        # The end of the post before, for picking a thread up where it
        # stopped. Selectable so a name or link in it can be copied.
        self.preview_label = Gtk.Label(
            xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True, visible=False)
        self.preview_label.add_css_class('dimmed')
        self.preview_label.update_property([Gtk.AccessibleProperty.LABEL],
                                           [_('End of the previous post')])
        return self.preview_label

    def _set_thread(self, thread):
        self._thread = thread
        self._thread_generation += 1

    def _own_account_id(self):
        key = self.local_draft_key
        return key.rsplit('#', 1)[-1] if key and '#' in key else None

    def _load_thread(self, status_id, status=None, editing=False):
        """Look the thread of one of the signed-in account's own posts
        up. *status* is the post when it is at hand, else it is fetched
        first. The answer is dropped if the composer moved on since: the
        thread state changes on every entry point and reset."""
        account_id = self._own_account_id()
        if account_id is None or not status_id:
            return
        generation = self._thread_generation
        view_ref = self.weak_ref()
        client = self.client

        def own(post):
            account = post.get('account') if isinstance(post, dict) else None
            return isinstance(account, dict) \
                and str(account.get('id')) == account_id

        def current():
            view = view_ref()
            if view is not None and view._thread_generation == generation:
                return view
            return None

        def got_context(post, result):
            try:
                ancestors, descendants = api_statuses.context_finish(client, result)
            except GLib.Error:
                return
            view = current()
            if view is None:
                return
            position = own_thread(post, ancestors, descendants, account_id)
            if position is None:
                return
            if editing:
                previous = position.previous
                thread = _ThreadProgress(
                    position.index, position.total,
                    preview_tail(plain_text(previous)) if previous else '')
            else:
                thread = _ThreadProgress(position.index + 1, None,
                                         preview_tail(plain_text(post)))
            view._set_thread(thread)
            view.update_compose_state()

        def fetch_context(post):
            api_statuses.context_async(
                client, status_id,
                callback=lambda _c, result: got_context(post, result))

        def got_status(_source, result):
            try:
                post = api_statuses.get_finish(client, result)
            except GLib.Error:
                return
            if current() is not None and own(post):
                fetch_context(post)

        if status is not None:
            if own(status):
                fetch_context(status)
        else:
            api_statuses.get_async(client, status_id, callback=got_status)

    def prefill_text(self, text):
        """Start a post with *text*, as shared from outside the app. A
        draft in progress is kept on the draft stack, for the banner
        above the buffer to bring back."""
        if not self._accept_transition():
            return False
        previous = self._capture_state()
        self._clear_submission_mode()
        if not self.is_empty():
            self._draft_stack = [previous]
        self._set_buffer(text)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.in_reply_to_id = None
        self._finish_begin()
        return True

    def pop_reply_state(self):
        if self._replacement is not None or self._posting:
            return
        state = self._draft_stack.pop() if self._draft_stack else None
        if state is not None:
            self._restore_state(state)
        else:
            self.in_reply_to_id = None
            self._set_thread(None)
        self.update_compose_state()

    def on_reply_cancel(self, _banner):
        self.pop_reply_state()

    def is_reply(self):
        return self.in_reply_to_id is not None

    def is_timed(self):
        return self.modes.is_timed()
