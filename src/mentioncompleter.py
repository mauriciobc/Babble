# SPDX-License-Identifier: GPL-3.0-or-later
"""Account suggestions for the ``@mention`` being typed in a text view.

:class:`MentionCompleter` watches the buffer of the text view it is
attached to: while the cursor ends an ``@word``, it searches the
instance's accounts and lists them in a popover under the mention. The
arrow keys pick one, Enter or Tab inserts it, Escape closes the list;
the keyboard never leaves the text view."""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402

SEARCH_DELAY_MS = 250
MAX_SUGGESTIONS = 6

# The mention being typed, ending at the cursor: an @ not glued to a
# word, a path or another @ (Mastodon's own mention rule), then the
# name typed so far, with the domain of a remote account if any.
_MENTION_RE = re.compile(r'(?<![\w/@])@([\w.-]*(?:@[\w.-]*)?)\Z')

# Characters a mention token continues with, past the cursor.
_TOKEN_CHARS = re.compile(r'[\w.@-]')

_ENTER_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
               Gdk.KEY_Tab, Gdk.KEY_KP_Tab)


def mention_query(text):
    """``(start, query)`` for the mention *text* (the text before the
    cursor) ends with, *start* being the offset of its ``@``; None when
    the cursor is not at the end of a mention."""
    match = _MENTION_RE.search(text)
    if match is None:
        return None
    return match.start(), match.group(1)


def _wants_resolve(query):
    """A full ``user@domain`` is worth a WebFinger lookup, which finds
    accounts the instance has never seen; anything shorter is not."""
    _user, _at, domain = query.partition('@')
    return '.' in domain.strip('.')


class MentionCompleter(Gtk.Popover):
    """Suggestion popover for *text_view*, parented to it. Call
    :meth:`prompt` after inserting a bare ``@`` to show a hint before
    anything is typed, :meth:`dismiss` to close it for good."""

    __gtype_name__ = 'BabbleMentionCompleter'

    def __init__(self, client, text_view, **kwargs):
        super().__init__(autohide=False, has_arrow=False, can_focus=False,
                         position=Gtk.PositionType.BOTTOM,
                         halign=Gtk.Align.START, **kwargs)
        self.client = client
        self.text_view = text_view
        self.buffer = text_view.get_buffer()
        self._search_id = 0
        self._serial = 0
        self._cancellable = None
        # (start, query) of the mention the list is for.
        self._token = None
        # A mention closed with Escape stays closed until it changes.
        self._suppressed = None
        self._hint = False

        self.list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, can_focus=False)
        self.list_box.add_css_class('navigation-sidebar')
        self.list_box.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_('Suggested Accounts')])
        pages.connect_weak(self.list_box, 'row-activated',
                           self._on_row_activated)
        self.hint_label = Gtk.Label(
            label=_('Type a name to search'), visible=False,
            margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        self.hint_label.add_css_class('dimmed')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.hint_label)
        box.append(self.list_box)
        self.set_child(box)
        self.set_parent(text_view)

        text_view.update_property([Gtk.AccessibleProperty.AUTOCOMPLETE],
                                  [int(Gtk.AccessibleAutocomplete.LIST)])
        pages.connect_weak(self.buffer, 'changed', self._on_buffer_changed)
        pages.connect_weak(self.buffer, 'mark-set', self._on_mark_set)
        keys = Gtk.EventControllerKey(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pages.connect_weak(keys, 'key-pressed', self._on_key_pressed)
        text_view.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        pages.connect_weak(focus, 'leave', self._on_focus_leave)
        text_view.add_controller(focus)
        pages.connect_weak(text_view, 'destroy', self._on_parent_destroy)

    # Public

    def prompt(self):
        """Show the hint for an ``@`` just inserted at the cursor."""
        self._hint = True
        self._suppressed = None
        # The insertion already looked at this token, hint off.
        self._token = None
        self.update()

    def dismiss(self):
        """Close the list and forget any search in flight."""
        self._cancel_search()
        self._token = None
        self._hint = False
        self._clear_rows()
        self.hint_label.set_visible(False)
        if self.get_visible():
            self.popdown()

    def update(self):
        """Follow the cursor: search for the mention it ends, if any."""
        token = self._current_token()
        if token is None or token == self._suppressed \
                or not self.text_view.is_focus():
            self.dismiss()
            return
        if token == self._token:
            self._place(token[0])
            return
        self._token = token
        self._cancel_search()
        _start, query = token
        if not query:
            # Nothing to search for yet: a bare @ the button inserted
            # shows the hint; a typed one waits for a name.
            self._clear_rows()
            self.hint_label.set_visible(self._hint)
            self._show_if_filled()
            return
        self._hint = False
        self._search_id = GLib.timeout_add(SEARCH_DELAY_MS,
                                           pages.weak_method(self._search))

    # Token

    def _cursor(self):
        return self.buffer.get_iter_at_mark(self.buffer.get_insert())

    def _current_token(self):
        if self.buffer.get_has_selection():
            return None
        cursor = self._cursor()
        line_start = cursor.copy()
        line_start.set_line_offset(0)
        before = self.buffer.get_text(line_start, cursor, False)
        found = mention_query(before)
        if found is None:
            return None
        start, query = found
        return line_start.get_offset() + start, query

    # Search

    def _cancel_search(self):
        if self._search_id:
            GLib.source_remove(self._search_id)
            self._search_id = 0
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        self._serial += 1

    def _search(self):
        self._search_id = 0
        if self._token is None:
            return GLib.SOURCE_REMOVE
        query = self._token[1]
        params = {'q': query, 'limit': MAX_SUGGESTIONS}
        if _wants_resolve(query):
            params['resolve'] = 'true'
        self._serial += 1
        serial = self._serial
        self._cancellable = Gio.Cancellable()
        done = pages.weak_method(self._on_search_done)
        self.client.request_async(
            'GET', '/accounts/search', params=params,
            cancellable=self._cancellable,
            callback=lambda c, r, _d=None: done(c, r, serial))
        return GLib.SOURCE_REMOVE

    def _on_search_done(self, client, result, serial):
        try:
            accounts = client.request_finish(result)
        except GLib.Error:
            # Cancelled, or the instance refused: a suggestion list is
            # not worth an error toast on every keystroke.
            accounts = None
        if serial != self._serial:
            return
        self._cancellable = None
        if not accounts:
            self.dismiss()
        elif self._token is not None:
            self._fill(accounts)

    # List

    def _clear_rows(self):
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)

    def _fill(self, accounts):
        from .timeline import AvatarCache
        self._clear_rows()
        self.hint_label.set_visible(False)
        for account in accounts[:MAX_SUGGESTIONS]:
            acct = account.get('acct') or account.get('username')
            if not acct:
                continue
            name = re.sub(r':[A-Za-z0-9_+\-]+:', '',
                          pages.display_name(account)).strip() or acct
            avatar = Adw.Avatar(size=32, text=name, show_initials=True)
            AvatarCache.load(avatar, account)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             valign=Gtk.Align.CENTER)
            labels.append(Gtk.Label(label=name, xalign=0,
                                    max_width_chars=28,
                                    ellipsize=Pango.EllipsizeMode.END))
            handle = Gtk.Label(label='@' + acct, xalign=0,
                               max_width_chars=28,
                               ellipsize=Pango.EllipsizeMode.END)
            handle.add_css_class('dimmed')
            handle.add_css_class('caption')
            labels.append(handle)
            box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4)
            box.append(avatar)
            box.append(labels)
            row = Gtk.ListBoxRow(child=box, can_focus=False)
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                ['%s, @%s' % (name, acct)])
            row.acct = acct
            self.list_box.append(row)
        self.list_box.select_row(self.list_box.get_row_at_index(0))
        self._show_if_filled()

    def _show_if_filled(self):
        filled = (self.hint_label.get_visible()
                  or self.list_box.get_row_at_index(0) is not None)
        if not filled or self._token is None:
            if self.get_visible():
                self.popdown()
            return
        self._place(self._token[0])
        if not self.get_visible():
            self.popup()

    def _place(self, offset):
        """Point at the ``@`` the suggestions are for."""
        location = self.text_view.get_iter_location(
            self.buffer.get_iter_at_offset(offset))
        x, y = self.text_view.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, location.x, location.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y = x, y
        rect.width, rect.height = 1, max(location.height, 1)
        self.set_pointing_to(rect)

    def _move(self, step):
        selected = self.list_box.get_selected_row()
        index = selected.get_index() + step if selected is not None else 0
        row = self.list_box.get_row_at_index(index)
        if row is not None:
            self.list_box.select_row(row)

    def _accept(self, row):
        """Replace the mention being typed with *row*'s account."""
        token = self._current_token()
        if token is None:
            self.dismiss()
            return
        start = self.buffer.get_iter_at_offset(token[0])
        end = self._cursor()
        # A cursor moved back into a name replaces all of it.
        while not end.is_end() and _TOKEN_CHARS.match(end.get_char()):
            end.forward_char()
        text = '@' + row.acct
        spaced = not end.is_end() and end.get_char().isspace()
        self.buffer.begin_user_action()
        self.buffer.delete(start, end)
        self.buffer.insert(start, text if spaced else text + ' ')
        if spaced:
            start.forward_char()
            self.buffer.place_cursor(start)
        self.buffer.end_user_action()
        self.dismiss()

    # Events

    def _on_buffer_changed(self, _buffer):
        self._suppressed = None
        self.update()

    def _on_mark_set(self, _buffer, _location, mark):
        if mark == self.buffer.get_insert():
            self.update()

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if not self.get_visible():
            return False
        if keyval == Gdk.KEY_Escape:
            self._suppressed = self._token
            self.dismiss()
            return True
        modifiers = state & (Gdk.ModifierType.CONTROL_MASK
                             | Gdk.ModifierType.ALT_MASK
                             | Gdk.ModifierType.SUPER_MASK)
        selected = self.list_box.get_selected_row()
        if modifiers or selected is None:
            return False
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._move(-1)
            return True
        if keyval in _ENTER_KEYS:
            self._accept(selected)
            return True
        return False

    def _on_row_activated(self, _list_box, row):
        self._accept(row)
        self.text_view.grab_focus()

    def _on_focus_leave(self, _controller):
        self.dismiss()

    def _on_parent_destroy(self, _text_view):
        self.dismiss()
        self.unparent()
