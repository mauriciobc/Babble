# SPDX-License-Identifier: GPL-3.0-or-later
"""The dialog around the composer view, and the width it asks for.

The sheet the view is presented in, its mode entry points, what it does
when the session underneath changes, and the close/cleanup handshake.
``compose.py`` never imports this module: the dialog imports the view, so
the edge only goes one way.
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gtk  # noqa: E402

from .compose import BabbleComposeView  # noqa: E402
from .composeoptions import default_visibility  # noqa: E402
from .composepost import _end_inhibition  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



# The composer dialog's width when the window has room for it.
COMPOSE_WIDTH = 600

class _ComposeWidth(Gtk.Widget):
    """Holds the composer at COMPOSE_WIDTH wide where the window allows
    it. A dialog that follows its content takes the natural width too,
    and the composer's own would change with the toolbar's labels; the
    minimum is left alone, so a narrow window still fits it."""

    __gtype_name__ = 'BabbleComposeWidth'

    def __init__(self, child):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = self.get_first_child().measure(
            orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, COMPOSE_WIDTH)
        return (minimum, natural, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        self.get_first_child().allocate(width, height, baseline, None)

    def do_dispose(self):
        while (child := self.get_first_child()) is not None:
            child.unparent()
        Gtk.Widget.do_dispose(self)

class ComposeDialog(Adw.Dialog):
    """Composer dialog. Keep one instance per client and present it
    repeatedly: closing keeps the draft."""

    __gtype_name__ = 'BabbleComposeDialog'

    def __init__(self, client, **kwargs):
        # The dialog follows the composer's size rather than a fixed
        # content size: no blank below the text area, and it grows and
        # shrinks with the warning, poll and attachments the toolbar
        # reveals. The width is pinned by _ComposeWidth.
        super().__init__(follows_content_size=True, **kwargs)
        self.view = BabbleComposeView(client)
        self.view._select_visibility(default_visibility())
        connect_weak(self.view, 'changed', self._on_view_changed)
        connect_weak(self, 'closed', self.view.save_local_draft)
        connect_weak(self, 'close-attempt', self._on_close_attempt)
        self.set_child(_ComposeWidth(self.view))
        self.set_focus(self.view.text_view)
        self._on_view_changed(self.view)

    @property
    def client(self):
        return self.view.client

    def start_reply(self, status_id, handle, visibility='public',
                    parent=None):
        return self.view.start_reply(status_id, handle, visibility,
                                     parent=parent)

    def start_mention(self, account, direct=False):
        return self.view.start_mention(account, direct=direct)

    def start_edit(self, status):
        return self.view.start_edit(status)

    def start_redraft(self, status):
        return self.view.start_redraft(status)

    def start_quote(self, status):
        return self.view.start_quote(status)

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        return self.view.start_scheduled(scheduled, draft=draft, on_done=on_done)

    def _on_view_changed(self, view):
        self.set_can_close(view._replacement is None)
        thread = view._thread
        if view.editing_id is not None:
            if thread is not None and thread.total:
                # Translators: dialog title when editing a post that is
                # part of a thread; the first number is its position, the
                # second the length of the thread.
                title = _('Edit Post · %(index)d/%(total)d') % {
                    'index': thread.number, 'total': thread.total}
            else:
                title = _('Edit Post')
        elif view.scheduled_id is not None:
            title = _('Edit Scheduled Post')
        elif thread is not None or (view.thread_toggle.get_active()
                                    and view.scheduled_at is None):
            # Translators: dialog title while writing a thread; %d is the
            # number of the post being written.
            title = _('Thread · Post %d') % (
                thread.number if thread is not None else 1)
        elif view.quoted_status_id is not None:
            title = _('Quote Post')
        elif view.is_reply():
            title = _('Reply')
        else:
            title = _('New Post')
        self.set_title(title)

    def _on_close_attempt(self, _dialog):
        if self.view._replacement is None or self.view._cleanup_busy:
            return
        dialog = Adw.AlertDialog(
            heading=_('Leave Duplicate Scheduled Posts?'),
            body=_('The replacement was saved, but the original is still '
                   'scheduled. Closing leaves both entries on the server.'))
        dialog.add_response('keep', _('_Keep Open'))
        dialog.add_response('leave', _('_Close Anyway'))
        dialog.set_response_appearance('leave', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('keep')
        dialog.set_close_response('keep')
        connect_weak(dialog, 'response', self._on_cleanup_close_response)
        dialog.present(self)

    def _on_cleanup_close_response(self, _dialog, response):
        if response == 'leave' and not self.view._cleanup_busy:
            self.force_close()

    def abandon_session(self):
        view = self.view
        # Saved first, while the draft is still in the composer; it skips
        # an edit, a scheduled replacement and a post still on its way.
        view.save_local_draft()
        view.uploads.abandon()
        cancellable = view._cleanup_cancellable
        view._replacement = None
        view._cleanup_busy = False
        view._cleanup_cancellable = None
        view._set_thread(None)
        if cancellable is not None:
            cancellable.cancel()
        view._draft_stack = []
        if view._cleanup_banner is not None:
            view._cleanup_banner.set_revealed(False)
        # The abandoned round never reaches _finish_posting, so the
        # logout inhibition is returned here; _posting stays set so the
        # closing dialog does not save the half-sent post as a draft.
        _end_inhibition(view._inhibition)
        view.set_posting_ui(view._posting)
        if self.get_parent() is not None:
            self.force_close()
