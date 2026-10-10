# SPDX-License-Identifier: GPL-3.0-or-later
"""Autocomplete popover for the composer text view.

* ``@acct``  accounts, ``GET /api/v1/accounts/search``
* ``#tag``   hashtags, ``GET /api/v2/search?type=hashtags``
* ``:code``  custom emoji of the instance, filtered locally

Up/Down move the selection, Tab/Enter accept it, Escape dismisses."""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from .emojichooser import (bind_emoji_image, cached_custom_emojis,  # noqa: E402
                           load_custom_emojis, remember_recent,
                           search_emojis)

LIMIT = 5
SEARCH_DELAY_MS = 250

_TOKENS = (
    ('@', re.compile(
        r'(?<![\w@/=])@([A-Za-z0-9_][A-Za-z0-9_.-]*(?:@[A-Za-z0-9.-]*)?)$')),
    ('#', re.compile(r'(?<![\w&/#=])#(\w+)$')),
    (':', re.compile(r'(?<![\w:])(?::([A-Za-z0-9_+-]{2,}))$')),
)


def find_token(text_before_cursor):
    """``(trigger, query, start)`` for the mention, hashtag or emoji
    being typed at the end of *text_before_cursor*, or None. *start* is
    the index of the trigger character."""
    for trigger, pattern in _TOKENS:
        match = pattern.search(text_before_cursor)
        if match:
            return trigger, match.group(1), match.start()
    return None


class CompletionPopover(Gtk.Popover):
    """Suggestions under the caret of a Gtk.TextView."""

    __gtype_name__ = 'BabbleCompletionPopover'

    def __init__(self, text_view, client):
        super().__init__(autohide=False, has_arrow=False,
                         position=Gtk.PositionType.BOTTOM, can_focus=False)
        self.text_view = text_view
        self.client = client
        self.buffer = text_view.get_buffer()
        self.kind = None
        self.query = None
        self.start_offset = 0
        self._request = 0
        self._timeout = 0
        self._suggestions = []
        self._suppress = False

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE,
                                    can_focus=False,
                                    activate_on_single_click=True)
        self.list_box.add_css_class('navigation-sidebar')
        self.list_box.connect('row-activated', self._on_row_activated)
        self.list_box.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_('Suggestions')])
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=260,
            propagate_natural_width=True, child=self.list_box)
        self.set_child(scrolled)
        self.set_parent(text_view)

        keys = Gtk.EventControllerKey(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._on_key)
        text_view.add_controller(keys)
        self.buffer.connect('changed', self._on_buffer_changed)
        self.buffer.connect('notify::cursor-position',
                            self._on_buffer_changed)
        text_view.connect('destroy', lambda _w: self._teardown())
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', lambda _c: self.dismiss())
        text_view.add_controller(focus)

    def _teardown(self):
        self._cancel_timeout()
        if self.get_parent() is not None:
            self.unparent()

    # Detecting what is being typed

    def _text_before_cursor(self):
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return self.buffer.get_text(self.buffer.get_start_iter(), cursor,
                                    False)

    def _on_buffer_changed(self, *_args):
        if self._suppress:
            return
        if self.buffer.get_has_selection():
            self.dismiss()
            return
        token = find_token(self._text_before_cursor())
        if token is None:
            self.dismiss()
            return
        trigger, query, start = token
        if trigger == self.kind and query == self.query \
                and self.get_visible():
            return
        self.kind, self.query = trigger, query
        self.start_offset = start
        self._cancel_timeout()
        self._request += 1
        request = self._request
        if trigger == ':':
            self._suggest_emoji(query, request)
        else:
            self._timeout = GLib.timeout_add(
                SEARCH_DELAY_MS, self._search_remote, trigger, query,
                request)

    def _cancel_timeout(self):
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0

    def dismiss(self):
        self._cancel_timeout()
        self._request += 1
        self.kind = self.query = None
        self._suggestions = []
        if self.get_visible():
            self.popdown()

    # Fetching

    def _search_remote(self, trigger, query, request):
        self._timeout = 0
        if request != self._request:
            return GLib.SOURCE_REMOVE

        def finished(_client, result, _data=None):
            if request != self._request:
                return
            try:
                data = self.client.request_finish(result)
            except GLib.Error:
                self.dismiss()
                return
            if trigger == '@':
                items = [('account', a) for a in (data or [])]
            else:
                items = [('tag', t) for t in (data or {}).get('hashtags', [])]
            self._show(items[:LIMIT])

        if trigger == '@':
            self.client.request_async(
                'GET', '/accounts/search',
                params={'q': query, 'limit': LIMIT}, callback=finished)
        else:
            self.client.request_async(
                'GET', '/api/v2/search',
                params={'q': query, 'type': 'hashtags', 'limit': LIMIT},
                callback=finished)
        return GLib.SOURCE_REMOVE

    def _suggest_emoji(self, query, request):
        emojis = cached_custom_emojis(self.client)
        if emojis is None:
            def loaded(loaded_emojis):
                if request == self._request:
                    self._show([('emoji', e) for e in search_emojis(
                        loaded_emojis, query, LIMIT)])
            load_custom_emojis(self.client, loaded)
            return
        self._show([('emoji', e) for e in search_emojis(emojis, query,
                                                        LIMIT)])

    # Showing

    def _show(self, items):
        self._suggestions = items
        while (row := self.list_box.get_first_child()) is not None:
            self.list_box.remove(row)
        if not items:
            if self.get_visible():
                self.popdown()
            return
        for kind, data in items:
            self.list_box.append(self._make_row(kind, data))
        self.list_box.select_row(self.list_box.get_row_at_index(0))
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        location = self.text_view.get_iter_location(cursor)
        x, y = self.text_view.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, location.x, location.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y = x, y
        rect.width, rect.height = 1, location.height
        self.set_pointing_to(rect)
        if not self.get_visible():
            self.popup()

    def _make_row(self, kind, data):
        box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4,
                      margin_start=6, margin_end=6)
        if kind == 'account':
            avatar = Adw.Avatar(size=28, text=data.get('display_name')
                                or data.get('username', ''),
                                show_initials=True)
            from .timeline import AvatarCache
            AvatarCache.load(avatar, data)
            box.append(avatar)
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            title = Gtk.Label(label=data.get('display_name')
                              or data.get('username', ''), xalign=0,
                              ellipsize=Pango.EllipsizeMode.END)
            subtitle = Gtk.Label(label='@' + data.get('acct', ''), xalign=0,
                                 ellipsize=Pango.EllipsizeMode.END)
            subtitle.add_css_class('dimmed')
            subtitle.add_css_class('caption')
            names.append(title)
            names.append(subtitle)
            box.append(names)
            description = '@' + data.get('acct', '')
        elif kind == 'tag':
            image = Gtk.Image.new_from_icon_name('tag-symbolic')
            box.append(image)
            label = Gtk.Label(label='#' + data['name'], xalign=0)
            box.append(label)
            history = data.get('history') or []
            if history:
                people = sum(int(day.get('accounts', 0))
                             for day in history[:2])
                note = Gtk.Label(label=_('%d people talking') % people,
                                 hexpand=True, xalign=1)
                note.add_css_class('dimmed')
                note.add_css_class('caption')
                box.append(note)
            description = '#' + data['name']
        else:
            image = Gtk.Image(pixel_size=24)
            bind_emoji_image(image, data)
            box.append(image)
            box.append(Gtk.Label(label=':%s:' % data['shortcode'],
                                 xalign=0))
            description = ':%s:' % data['shortcode']
        row = Gtk.ListBoxRow(child=box, focusable=False)
        row.update_property([Gtk.AccessibleProperty.LABEL], [description])
        return row

    # Accepting

    def _insertion(self, kind, data):
        if kind == 'account':
            return '@%s ' % data['acct']
        if kind == 'tag':
            return '#%s ' % data['name']
        remember_recent(data['shortcode'])
        return ':%s: ' % data['shortcode']

    def accept(self, index=None):
        if index is None:
            row = self.list_box.get_selected_row()
            if row is None:
                return False
            index = row.get_index()
        if index < 0 or index >= len(self._suggestions):
            return False
        kind, data = self._suggestions[index]
        insertion = self._insertion(kind, data)
        self._suppress = True
        try:
            start = self.buffer.get_iter_at_offset(self.start_offset)
            end = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            self.buffer.begin_user_action()
            self.buffer.delete(start, end)
            start = self.buffer.get_iter_at_offset(self.start_offset)
            self.buffer.insert(start, insertion)
            self.buffer.end_user_action()
        finally:
            self._suppress = False
        self.dismiss()
        self.text_view.grab_focus()
        return True

    def _on_row_activated(self, _list_box, row):
        self.accept(row.get_index())

    # Keyboard

    def _move(self, step):
        count = len(self._suggestions)
        if not count:
            return
        row = self.list_box.get_selected_row()
        index = (row.get_index() if row is not None else -1) + step
        index %= count
        self.list_box.select_row(self.list_box.get_row_at_index(index))

    def _on_key(self, _controller, keyval, _keycode, state):
        if not self.get_visible():
            return False
        state &= Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Down:
            self._move(1)
            return True
        if keyval == Gdk.KEY_Up:
            self._move(-1)
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return self.accept()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state:
            return self.accept()
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False
