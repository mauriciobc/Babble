# SPDX-License-Identifier: GPL-3.0-or-later
"""The "paste a link to a post" flow for the composer, as a mixin for
:class:`BabbleComposeView`.

A paste whose whole content is a single link to a post on a fediverse
server becomes a quote, like the reference frontends do: the URL is not
inserted, the post is attached and a toast says so, with an Undo that
detaches the quote and puts the link back as text. Typing never
converts (a link arrives one keystroke at a time), nor do undo,
drag-and-drop or completion, nor a programmatic buffer write; anything
that is not the paste's own insert is left as plain text.

Split out of ``compose.py``, which holds the rest of the composer."""

import gi

gi.require_version('Adw', '1')

from gi.repository import Adw, GLib  # noqa: E402

from .i18n import gettext as _  # noqa: E402
from . import pages  # noqa: E402


class QuotePaste:
    """Needs ``client``, ``buffer``, ``editing_id``, ``quoted_status_id``,
    ``_in_programmatic_insert``, ``_pre_paste_offset``, ``_paste_serial``,
    ``_quote_state_generation``, ``update_compose_state()`` and
    ``_add_toast()`` from the composer view it is mixed into."""

    def _on_text_inserted(self, _buffer, iter_, text, _length):
        """A plain-text paste inserts once, at the cursor position
        recorded in ``on_paste_clipboard``; the insertion is paired
        with that offset here, so a single lone link to a post becomes
        a quote and nothing else ever converts. Typing a link never
        fires this with the whole URL at once."""
        pre = self._pre_paste_offset
        if pre is None:
            return
        self._pre_paste_offset = None
        # The handler may run before the text lands or after it; the
        # iter is the insertion point in the former, just past the
        # inserted text in the latter.
        offset = iter_.get_offset()
        insert_offset = offset if offset == pre \
            else pre if offset == pre + len(text) else None
        if insert_offset is None:
            # Not the paste's own insertion (something moved it along
            # first): leave the paste as text.
            return
        if self.editing_id is not None or self.quoted_status_id is not None:
            return
        uri = (text or '').strip()
        # A single token that is a link to a post, not a profile, a tag,
        # a collection or a pile of text.
        if not uri or len(uri) > 2048 or any(c.isspace() for c in uri):
            return
        if pages.fedi_link_kind(uri) != 'post':
            return
        GLib.idle_add(self._convert_pasted_link, uri,
                      self._quote_state_generation,
                      insert_offset)

    def _convert_pasted_link(self, uri, state_generation, offset):
        """Take the just-inserted link back out and attach the post it
        names as a quote. If the composer moved on since the paste (or
        the region no longer holds the link), the link stays as text."""
        if (state_generation != self._quote_state_generation
                or self.editing_id is not None
                or self.quoted_status_id is not None):
            return GLib.SOURCE_REMOVE
        if self.buffer.get_char_count() < offset + len(uri):
            return GLib.SOURCE_REMOVE
        start = self.buffer.get_iter_at_offset(offset)
        end = self.buffer.get_iter_at_offset(offset + len(uri))
        # The paste landing is asynchronous; whatever holds that range
        # now may not be the link any more if the buffer moved on.
        if self.buffer.get_text(start, end, False) != uri:
            return GLib.SOURCE_REMOVE
        self.buffer.delete(start, end)
        self._begin_quote_attach(uri, offset)
        return GLib.SOURCE_REMOVE

    def _begin_quote_attach(self, uri, insert_offset):
        """Resolve *uri* and attach the post it names as a quote; the
        toast says so, with an Undo. If the composer moved on while the
        search was out, the paste is dropped; if the quote slot is
        already taken by then, the link goes back as text, at the site
        of the paste."""
        state_generation = self._quote_state_generation
        client = self.client
        if client is None:
            self._restore_url(uri, insert_offset)
            return
        view_ref = self.weak_ref()

        def resolved(_client, result, _data=None):
            from .timelinebase import can_quote
            view = view_ref()
            if (view is None
                    or state_generation != view._quote_state_generation):
                # The composer moved on while the search was out (a
                # draft was discarded, an edit started): drop the paste.
                return
            try:
                data = client.request_finish(result)
            except GLib.Error:
                data = None
            for status in (data or {}).get('statuses') or []:
                if can_quote(status) and status.get('id'):
                    if (view.quoted_status_id is not None
                            or view.editing_id is not None):
                        view._restore_url(uri, insert_offset)
                        return
                    view._attach_quote(str(status['id']), uri)
                    return
            # Nothing quotable behind the link: the link goes back in as
            # text, at the site of the paste.
            view._restore_url(uri, insert_offset)

        client.request_async('GET', '/api/v2/search',
                             params={'q': uri, 'resolve': 'true',
                                     'limit': 1},
                             callback=resolved)

    def _restore_url(self, uri, at_offset=None):
        """Insert *uri* back at *at_offset* (default: the end), as text.
        Nothing becomes a quote here, and the paste offset was consumed,
        so the insert watcher ignores it."""
        self._in_programmatic_insert += 1
        try:
            count = self.buffer.get_char_count()
            if at_offset is None or at_offset > count:
                at_offset = count
            position = self.buffer.get_iter_at_offset(at_offset)
            self.buffer.insert(position, uri, -1)
        finally:
            self._in_programmatic_insert -= 1

    def _attach_quote(self, status_id, uri):
        self.quoted_status_id = status_id
        # The title and the Post button follow the quote.
        self.update_compose_state()
        toast = Adw.Toast(title=_('Post attached as a quote'),
                          use_markup=False)
        toast.set_button_label(_('_Undo'))
        toast.set_priority(Adw.ToastPriority.HIGH)
        pages.connect_weak(toast, 'button-clicked',
                           self._undo_quote_attach, status_id, uri)
        self._add_toast(toast)

    def _undo_quote_attach(self, _toast, status_id, uri):
        """Undo detaches the quote and puts the link back as text."""
        if self.quoted_status_id != status_id:
            return
        self.quoted_status_id = None
        self._restore_url(uri)
        self.update_compose_state()

    def _clear_pre_paste_offset(self, serial):
        """The watchdog of the paste numbered *serial*: un-arms the
        offset only if no newer paste has re-armed it since."""
        if serial == self._paste_serial:
            self._pre_paste_offset = None
        return GLib.SOURCE_REMOVE
