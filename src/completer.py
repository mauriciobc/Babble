# SPDX-License-Identifier: GPL-3.0-or-later
"""The suggestion popover behind ``@mention`` and ``#hashtag`` completion.

:class:`TokenCompleter` watches the buffer of the text view it is
attached to: while the cursor ends a token (an ``@word``, a ``#word``),
it asks for suggestions and lists them in a popover under the token. The
arrow keys pick one, Enter or Tab inserts it, Escape closes the list;
the keyboard never leaves the text view. What the token looks like, where
the suggestions come from and how a row is drawn is left to a subclass."""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402

SEARCH_DELAY_MS = 250
MAX_SUGGESTIONS = 6

_ENTER_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter,
               Gdk.KEY_Tab, Gdk.KEY_KP_Tab)


class TokenCompleter(Gtk.Popover):
    """Suggestion popover for *text_view*, parented to it. Call
    :meth:`dismiss` to close it for good. A subclass provides:

    - ``token_query(text)``: ``(start, query)`` for the token *text* (the
      line before the cursor) ends with, or None
    - ``continues_token(char)``: whether *char* belongs to the token, for
      the part of it past the cursor
    - ``instant_items(query)``: items shown at once, with no request
    - ``wants_search(query)``: whether *query* is worth a request
    - ``search_request(query)``: ``(path, params)`` of the GET to make
    - ``parse(result)``: the items in a response
    - ``build_row(item)``: a ``Gtk.ListBoxRow`` for *item*
    - ``completion(item, typed)``: the text that replaces the token
    - ``list_label``: the accessible name of the list
    - ``default_selection(items, query)``, ``item_key(item)`` and
      ``merge_results(query, items)``: which row starts selected, how an
      item is told apart from another, and how an answer is combined with
      the rows already shown"""

    #: Shown above the list for a bare trigger the user has not typed.
    hint_text = ''
    #: The accessible name of the list.
    list_label = ''

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
        # (start, query) of the token the list is for.
        self._token = None
        # A token closed with Escape stays closed until it changes.
        self._suppressed = None
        self._hint = False
        # An input method is composing (the preedit is not empty): its
        # keys are its own.
        self._preedit = False
        # The user picked a row with the arrow keys, so a refill keeps it.
        self._moved = False
        # The completion is being written into the buffer: its own edits
        # are not a token to complete.
        self._accepting = False
        # Whether this completer has set the text view's list relations,
        # so it clears only what it set (the other completer shares the
        # view).
        self._a11y_set = False

        self.list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=True, can_focus=False)
        self.list_box.add_css_class('navigation-sidebar')
        self.list_box.update_property([Gtk.AccessibleProperty.LABEL],
                                      [self.list_label])
        pages.connect_weak(self.list_box, 'row-activated',
                           self._on_row_activated)
        pages.connect_weak(self.list_box, 'row-selected',
                           self._on_row_selected)
        self.hint_label = Gtk.Label(
            label=self.hint_text, visible=False,
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
        pages.connect_weak(text_view, 'preedit-changed',
                           self._on_preedit_changed)
        keys = Gtk.EventControllerKey(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pages.connect_weak(keys, 'key-pressed', self._on_key_pressed)
        text_view.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        pages.connect_weak(focus, 'leave', self._on_focus_leave)
        text_view.add_controller(focus)
        pages.connect_weak(text_view, 'destroy', self._on_parent_destroy)

    # Hooks

    def token_query(self, text):
        raise NotImplementedError

    def continues_token(self, char):
        raise NotImplementedError

    def instant_items(self, query):
        return []

    def wants_search(self, query):
        return True

    def search_request(self, query):
        raise NotImplementedError

    def parse(self, result):
        raise NotImplementedError

    def build_row(self, item):
        raise NotImplementedError

    def completion(self, item, typed):
        raise NotImplementedError

    def default_selection(self, items, query):
        """Index of the row selected when *items* are shown, or None."""
        return 0

    def item_key(self, item):
        return id(item)

    def prepare(self, query):
        """Called when the token changes to a new, non-empty *query*,
        and for a bare trigger once the user has paused on it, before
        anything is shown: a place to start loading what
        ``instant_items`` will use."""

    #: Whether a bare trigger, left alone for the debounce, shows
    #: ``bare_items()``. Off, a bare trigger shows only the hint.
    completes_bare = False

    def bare_items(self):
        """The rows for a bare trigger the user has paused on."""
        return []

    def cached_items(self, query):
        """The parsed answer to *query* if one is already known, else
        None; a hit is shown without a request."""
        return None

    def merge_results(self, query, items):
        """The rows to show for an answer to *query*: *items* parsed, or
        None when the request failed."""
        return items or []

    # Public

    def dismiss(self):
        """Close the list and forget any search in flight."""
        self._cancel_search()
        self._token = None
        self._hint = False
        self._reset_a11y()
        self._clear_rows()
        self.hint_label.set_visible(False)
        self._hide()

    def update(self):
        """Follow the cursor: look for the token it ends, if any."""
        if self._accepting:
            self._cancel_search()
            return
        token = self._current_token()
        if token is None or token == self._suppressed \
                or not self.text_view.is_focus():
            self.dismiss()
            return
        if token == self._token:
            self._place(token[0])
            if self.get_visible():
                self._sync_a11y()
            return
        self._token = token
        self._cancel_search()
        self._moved = False
        # The rows shown answer the previous query: keep them in view,
        # so the list does not flicker on every keystroke, but out of
        # reach until the new results replace them.
        self.list_box.unselect_all()
        self.list_box.set_sensitive(False)
        _start, query = token
        if not query:
            # Nothing to search for yet: a bare trigger the button
            # inserted shows the hint; a typed one waits for a name.
            self._clear_rows()
            self.hint_label.set_visible(self._hint)
            self._show_if_filled()
            if self.completes_bare:
                self._search_id = GLib.timeout_add(
                    SEARCH_DELAY_MS, pages.weak_method(self._pause_on_bare))
            return
        self._hint = False
        self.prepare(query)
        # Rows that answer this query without a request show at once,
        # and usable.
        instant = self.instant_items(query)
        if instant:
            self._fill(instant)
        elif not self.wants_search(query):
            # Nothing to show and nothing to ask: no stale rows left.
            self._clear_rows()
            self._show_if_filled()
        if self.wants_search(query):
            self._search_id = GLib.timeout_add(
                SEARCH_DELAY_MS, pages.weak_method(self._search))

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
        found = self.token_query(before)
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

    def _pause_on_bare(self):
        self._search_id = 0
        if self._token is not None and not self._token[1]:
            self.prepare('')
            # An input method that is composing is not a user who paused.
            items = [] if self._preedit else self.bare_items()
            if items:
                self._fill(items)
        return GLib.SOURCE_REMOVE

    def _search(self):
        self._search_id = 0
        if self._token is None:
            return GLib.SOURCE_REMOVE
        cached = self.cached_items(self._token[1])
        if cached is not None:
            self._show_answer(cached)
            return GLib.SOURCE_REMOVE
        path, params = self.search_request(self._token[1])
        self._serial += 1
        serial = self._serial
        self._cancellable = Gio.Cancellable()
        done = pages.weak_method(self._on_search_done)
        self.client.request_async(
            'GET', path, params=params,
            cancellable=self._cancellable,
            callback=lambda c, r, _d=None: done(c, r, serial))
        return GLib.SOURCE_REMOVE

    def _on_search_done(self, client, result, serial):
        try:
            items = self.parse(client.request_finish(result))
        except GLib.Error:
            # Cancelled, or the instance refused: a suggestion list is
            # not worth an error toast on every keystroke.
            items = None
        if serial != self._serial:
            return
        self._cancellable = None
        self._show_answer(items)

    def _show_answer(self, items):
        """Show the answer to the token's query: *items* parsed, or None
        when the request failed."""
        query = self._token[1] if self._token is not None else ''
        items = self.merge_results(query, items)
        if not items:
            self.dismiss()
        elif self._token is not None:
            self._fill(items)

    # List

    def _clear_rows(self):
        # A relation must not outlive the row it points at.
        if self._a11y_set:
            self.text_view.reset_relation(
                Gtk.AccessibleRelation.ACTIVE_DESCENDANT)
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)

    def _fill(self, items):
        items = items[:MAX_SUGGESTIONS]
        query = self._token[1] if self._token else ''
        picked = self.list_box.get_selected_row()
        keep = self.item_key(picked.item) \
            if self._moved and picked is not None else None
        self._clear_rows()
        self.hint_label.set_visible(False)
        for item in items:
            row = self.build_row(item)
            row.item = item
            self.list_box.append(row)
        self.list_box.set_sensitive(True)
        # The row the user moved to stays selected, whatever its new
        # place; otherwise the default decides.
        index = next((i for i, item in enumerate(items)
                      if keep is not None and self.item_key(item) == keep),
                     None)
        if index is None:
            index = self.default_selection(items, query)
        self.list_box.select_row(
            None if index is None else self.list_box.get_row_at_index(index))
        self._show_if_filled()

    def _show_if_filled(self):
        filled = (self.hint_label.get_visible()
                  or self.list_box.get_row_at_index(0) is not None)
        if not filled or self._token is None:
            self._hide()
            return
        self._place(self._token[0])
        if not self.get_visible():
            self.popup()
        self._sync_a11y()

    def _hide(self):
        if not self.get_visible():
            return
        self._reset_a11y()
        self.popdown()
        # A popover shown again keeps its wl_surface, and when the
        # frame it last drew is still waiting on its GPU fence the
        # compositor rejects the new popup role ("already has a buffer
        # committed") and drops the connection. This list hides and
        # shows again within a frame, so give every showing a fresh
        # surface.
        self.unrealize()

    def _sync_a11y(self):
        """Tell assistive technologies that the text view is driving a
        list, and which row of it is the current one: the keyboard
        never leaves the text view, so the row cannot be focus."""
        self._a11y_set = True
        selected = self.list_box.get_selected_row()
        if selected is None:
            self.text_view.reset_relation(
                Gtk.AccessibleRelation.ACTIVE_DESCENDANT)
        else:
            self.text_view.update_relation(
                [Gtk.AccessibleRelation.ACTIVE_DESCENDANT], [selected])
        # A reference list has to be a GValue holding a
        # Gtk.AccessibleList (see the catch-up filters' labelled()); a
        # bare widget asserts in GTK and the relation is dropped.
        refs = Gtk.AccessibleList.new_from_list([self.list_box])
        self.text_view.update_relation(
            [Gtk.AccessibleRelation.CONTROLS],
            [GObject.Value(Gtk.AccessibleList, refs)])

    def _reset_a11y(self):
        if not self._a11y_set:
            return
        self._a11y_set = False
        self.text_view.reset_relation(
            Gtk.AccessibleRelation.ACTIVE_DESCENDANT)
        self.text_view.reset_relation(Gtk.AccessibleRelation.CONTROLS)

    def _place(self, offset):
        """Point at the trigger the suggestions are for."""
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
        """Replace the token being typed with *row*'s completion."""
        token = self._current_token()
        if token is None:
            self.dismiss()
            return
        start = self.buffer.get_iter_at_offset(token[0])
        end = self._cursor()
        # A cursor moved back into a name replaces all of it.
        while not end.is_end() and self.continues_token(end.get_char()):
            end.forward_char()
        text = self.completion(row.item, token[1])
        spaced = not end.is_end() and end.get_char().isspace()
        # What is written in between is half a token (the completion
        # without its space): do not suggest from it.
        self._accepting = True
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(start, end)
            self.buffer.insert(start, text if spaced else text + ' ')
            if spaced:
                start.forward_char()
                self.buffer.place_cursor(start)
        finally:
            # An action left open would fold every later edit into this
            # one undo step.
            self.buffer.end_user_action()
            self._accepting = False
        self.dismiss()

    # Events

    def _on_buffer_changed(self, _buffer):
        self._suppressed = None
        self.update()

    def _on_mark_set(self, _buffer, _location, mark):
        if mark == self.buffer.get_insert():
            self.update()

    def _on_preedit_changed(self, _text_view, preedit):
        self._preedit = bool(preedit)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if not self.get_visible():
            return False
        # This controller runs before the input method's: Enter that
        # confirms a conversion is not a request to accept a row, and
        # Escape that cancels one is the input method's too (the list
        # closes with the next edit, or on a second Escape).
        if self._preedit:
            return False
        if keyval == Gdk.KEY_Escape:
            self._suppressed = self._token
            self.dismiss()
            return True
        # Shift too: Shift+Enter is a line break, Shift+Tab moves focus.
        modifiers = state & (Gdk.ModifierType.CONTROL_MASK
                             | Gdk.ModifierType.ALT_MASK
                             | Gdk.ModifierType.SUPER_MASK
                             | Gdk.ModifierType.SHIFT_MASK)
        # A list still waiting for its answer is not for the keyboard.
        if modifiers or not self.list_box.get_sensitive():
            return False
        selected = self.list_box.get_selected_row()
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            # With no row selected, Down is how one is chosen.
            first = self.list_box.get_row_at_index(0)
            if selected is None and first is None:
                return False
            self._moved = True
            if selected is None:
                self.list_box.select_row(first)
            else:
                self._move(1)
            return True
        if selected is None:
            return False
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._moved = True
            self._move(-1)
            return True
        if keyval in _ENTER_KEYS:
            self._accept(selected)
            return True
        return False

    def _on_row_selected(self, _list_box, _row):
        if self.get_visible():
            self._sync_a11y()

    def _on_row_activated(self, _list_box, row):
        self._accept(row)
        self.text_view.grab_focus()

    def _on_focus_leave(self, _controller):
        self._preedit = False
        self.dismiss()

    def _on_parent_destroy(self, _text_view):
        self.dismiss()
        self.unparent()
