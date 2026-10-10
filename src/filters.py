# SPDX-License-Identifier: GPL-3.0-or-later

"""Server-side filters (docs.joinmastodon.org/methods/filters, API v2).

``present_filters(parent_widget, client)`` opens a dialog listing the
account's filters with create, edit and delete. A filter has a title,
keywords (each optionally whole-word), the contexts it applies to, an
action (warn, hide, blur media) and an optional expiry.
"""

import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402

FILTERS_PATH = '/api/v2/filters'

CONTEXTS = (
    ('home', _('Home and Lists')),
    ('notifications', _('Notifications')),
    ('public', _('Public Timelines')),
    ('thread', _('Conversations')),
    ('account', _('Profiles')),
)

ACTIONS = (
    ('warn', _('Warn'), _('Collapsed behind a warning that can be opened')),
    ('hide', _('Hide'), _('Removed completely')),
    ('blur', _('Blur Media'), _('Text stays, media is blurred')),
)

# Seconds, None meaning never.
EXPIRIES = (
    (None, _('Never')),
    (30 * 60, _('30 Minutes')),
    (60 * 60, _('1 Hour')),
    (6 * 60 * 60, _('6 Hours')),
    (12 * 60 * 60, _('12 Hours')),
    (24 * 60 * 60, _('1 Day')),
    (7 * 24 * 60 * 60, _('1 Week')),
)


def build_payload(title, contexts, action, expires_in, keywords,
                  original=None, keep_expiry=False):
    """The JSON body for POST/PUT /api/v2/filters.

    *keywords* is a list of ``{'id'?, 'keyword', 'whole_word'}``;
    *original* the keywords the filter had when editing started, so a
    keyword no longer listed is sent with ``_destroy``. With
    *keep_expiry* the ``expires_in`` field is left out, keeping the
    server's expiry date."""
    attributes = []
    kept = set()
    for keyword in keywords:
        text = keyword['keyword'].strip()
        if not text:
            continue
        entry = {'keyword': text, 'whole_word': bool(keyword['whole_word'])}
        if keyword.get('id'):
            entry['id'] = keyword['id']
            kept.add(keyword['id'])
        attributes.append(entry)
    for old in original or ():
        if old['id'] not in kept:
            attributes.append({'id': old['id'], '_destroy': True})
    payload = {
        'title': title.strip(),
        'context': list(contexts),
        'filter_action': action,
        'keywords_attributes': attributes,
    }
    if not keep_expiry:
        payload['expires_in'] = expires_in
    return payload


def describe_filter(item):
    """One line for the list: contexts, keyword count, expiry."""
    parts = []
    count = len(item.get('keywords') or ())
    if count:
        parts.append(ngettext('%d keyword', '%d keywords', count) % count)
    posts = len(item.get('statuses') or ())
    if posts:
        parts.append(ngettext('%d post', '%d posts', posts) % posts)
    names = dict(CONTEXTS)
    parts.append(', '.join(names.get(c, c) for c in item.get('context', ())))
    expires = _parse_time(item.get('expires_at'))
    if expires is not None:
        parts.append(_('Expires %s') % expires.astimezone().strftime('%x %H:%M'))
    return ' · '.join(part for part in parts if part)


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


# Editor

class _KeywordRow(Adw.EntryRow):
    """Entry row for one keyword with a whole-word toggle and a remove
    button."""

    __gsignals__ = {'removed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, keyword=None):
        super().__init__(title=_('Keyword'))
        keyword = keyword or {}
        self.keyword_id = keyword.get('id')
        self.set_text(keyword.get('keyword', ''))
        self.whole_word = Gtk.CheckButton(
            label=_('Whole word'), active=bool(keyword.get('whole_word')),
            valign=Gtk.Align.CENTER)
        self.add_suffix(self.whole_word)
        remove = Gtk.Button(icon_name='list-remove-symbolic',
                            valign=Gtk.Align.CENTER,
                            tooltip_text=_('Remove Keyword'))
        remove.add_css_class('flat')
        remove.add_css_class('circular')
        remove.update_property([Gtk.AccessibleProperty.LABEL],
                               [_('Remove Keyword')])
        remove.connect('clicked', lambda _b: self.emit('removed'))
        self.add_suffix(remove)

    def value(self):
        return {'id': self.keyword_id, 'keyword': self.get_text(),
                'whole_word': self.whole_word.get_active()}


class FilterEditPage(Adw.NavigationPage):
    """Create (``item`` None) or edit one filter."""

    __gtype_name__ = 'BabbleFilterEditPage'

    def __init__(self, client, item=None, on_saved=None):
        super().__init__(title=_('Edit Filter') if item else _('New Filter'),
                         tag='filter-edit')
        self.client = client
        self.item = item
        self.on_saved = on_saved
        self._busy = False
        item = item or {}
        self._original_keywords = list(item.get('keywords') or ())

        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self._on_save_clicked)
        header = Adw.HeaderBar()
        header.pack_end(self.save_button)

        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title=_('_Title'), use_underline=True,
                                      text=item.get('title', ''))
        self.title_row.connect('changed', lambda *_a: self._validate())
        group.add(self.title_row)
        page.add(group)

        self.keywords_group = Adw.PreferencesGroup(
            title=_('Keywords'),
            description=_('Posts containing any of these are filtered'))
        self._keyword_rows = []
        for keyword in self._original_keywords:
            self._add_keyword_row(keyword)
        add = Adw.ButtonRow(title=_('_Add Keyword'), use_underline=True,
                            start_icon_name='list-add-symbolic')
        add.connect('activated', lambda _r: self._add_keyword_row(
            None, focus=True))
        self._add_row = add
        self.keywords_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.keywords_list.add_css_class('boxed-list')
        for row in self._keyword_rows:
            self.keywords_list.append(row)
        self.keywords_list.append(add)
        self.keywords_group.add(self.keywords_list)
        page.add(self.keywords_group)

        contexts = Adw.PreferencesGroup(
            title=_('Filter In'),
            description=_('Where the filter applies'))
        self._context_rows = {}
        active = set(item.get('context') or ('home', 'public'))
        for name, label in CONTEXTS:
            row = Adw.SwitchRow(title=label, active=name in active)
            row.connect('notify::active', lambda *_a: self._validate())
            self._context_rows[name] = row
            contexts.add(row)
        page.add(contexts)

        options = Adw.PreferencesGroup()
        self.action_row = Adw.ComboRow(
            title=_('Action'),
            model=Gtk.StringList.new([label for _n, label, _d in ACTIONS]))
        current = item.get('filter_action') or 'warn'
        self.action_row.set_selected(
            [name for name, _l, _d in ACTIONS].index(current)
            if current in [name for name, _l, _d in ACTIONS] else 0)
        self.action_row.set_subtitle(
            ACTIONS[self.action_row.get_selected()][2])
        self.action_row.connect('notify::selected', self._on_action_changed)
        options.add(self.action_row)

        labels = [label for _s, label in EXPIRIES]
        self._expires_at = _parse_time(item.get('expires_at'))
        self._keep_expiry = self._expires_at is not None
        if self._keep_expiry:
            labels.insert(0, _('Until %s') % self._expires_at.astimezone()
                          .strftime('%x %H:%M'))
        self.expiry_row = Adw.ComboRow(
            title=_('Expire After'), model=Gtk.StringList.new(labels))
        self.expiry_row.set_selected(0)
        options.add(self.expiry_row)
        page.add(options)

        if self.item:
            danger = Adw.PreferencesGroup()
            delete = Adw.ButtonRow(title=_('_Delete Filter'),
                                   use_underline=True)
            delete.add_css_class('destructive-action')
            delete.connect('activated', self._on_delete_clicked)
            danger.add(delete)
            page.add(danger)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(page)
        self.set_child(toolbar)
        self._validate()

    # Keywords

    def _add_keyword_row(self, keyword, focus=False):
        row = _KeywordRow(keyword)
        row.connect('removed', self._on_keyword_removed)
        self._keyword_rows.append(row)
        if focus:
            self.keywords_list.insert(row, len(self._keyword_rows) - 1)
            row.grab_focus()
        return row

    def _on_keyword_removed(self, row):
        self._keyword_rows.remove(row)
        self.keywords_list.remove(row)

    def _on_action_changed(self, row, _pspec):
        row.set_subtitle(ACTIONS[row.get_selected()][2])

    # Saving

    def _validate(self):
        valid = bool(self.title_row.get_text().strip()) and any(
            row.get_active() for row in self._context_rows.values())
        self.save_button.set_sensitive(valid and not self._busy)

    def collect(self):
        """The request body for the form as it stands."""
        selected = self.expiry_row.get_selected()
        keep = self._keep_expiry and selected == 0
        expires = None
        if not keep:
            index = selected - 1 if self._keep_expiry else selected
            expires = EXPIRIES[index][0]
        return build_payload(
            self.title_row.get_text(),
            [name for name, row in self._context_rows.items()
             if row.get_active()],
            ACTIONS[self.action_row.get_selected()][0],
            expires,
            [row.value() for row in self._keyword_rows],
            original=self._original_keywords, keep_expiry=keep)

    def _on_save_clicked(self, _button):
        if self._busy:
            return
        self._busy = True
        self._validate()
        body = self.collect()
        method, path = ('PUT', '%s/%s' % (FILTERS_PATH, self.item['id'])) \
            if self.item else ('POST', FILTERS_PATH)

        def saved(client, result, _data=None):
            self._busy = False
            self._validate()
            try:
                saved_item = client.request_finish(result)
            except GLib.Error as error:
                pages.report_error(self, _('Could not save the filter'),
                                   error)
                return
            if self.on_saved is not None:
                self.on_saved(saved_item)
            view = self.get_ancestor(Adw.NavigationView)
            if view is not None:
                view.pop()

        self.client.request_async(method, path, json_body=body,
                                  callback=saved)

    def _on_delete_clicked(self, _row):
        item = self.item

        def delete():
            def deleted(client, result, _data=None):
                try:
                    client.request_finish(result)
                except GLib.Error as error:
                    pages.report_error(self, _('Could not delete the filter'),
                                       error)
                    return
                if self.on_saved is not None:
                    self.on_saved(None)
                view = self.get_ancestor(Adw.NavigationView)
                if view is not None:
                    view.pop()

            self.client.request_async(
                'DELETE', '%s/%s' % (FILTERS_PATH, item['id']),
                callback=deleted)

        pages.confirm(self, _('Delete Filter?'),
                      _('“%s” will stop filtering your timelines.')
                      % item.get('title', ''), _('_Delete'), delete)


# List

class FiltersList(Adw.Bin):
    """The filter list: loading, empty and error states, and one row per
    filter opening the editor."""

    __gtype_name__ = 'BabbleFiltersList'

    def __init__(self, client, navigation_view):
        super().__init__()
        self.client = client
        self.navigation_view = navigation_view
        self.items = []
        self.stack = Gtk.Stack()
        self.stack.add_named(pages.make_loading_page(_('Loading Filters')),
                             'loading')
        self.error_page = pages.make_error_page(
            _('Could Not Load Filters'), lambda _b: self.refresh())
        self.stack.add_named(self.error_page, 'error')
        self.empty_page = Adw.StatusPage(
            icon_name='view-filter-symbolic', title=_('No Filters'),
            description=_('Hide or warn about posts that match keywords'))
        self.stack.add_named(self.empty_page, 'empty')

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class('boxed-list')
        self.listbox.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_('Filters')])
        clamp = Adw.Clamp(maximum_size=600, margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
        clamp.set_child(self.listbox)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')
        self.set_child(self.stack)
        self.refresh()

    def refresh(self):
        self.stack.set_visible_child_name('loading')
        self.client.request_async('GET', FILTERS_PATH,
                                  callback=self._on_loaded)

    def _on_loaded(self, client, result, _data=None):
        try:
            self.items = client.request_finish(result)
        except GLib.Error as error:
            self.error_page.set_description(
                pages.summarize_error(error.message))
            self.stack.set_visible_child_name('error')
            return
        self._populate()

    def _populate(self):
        while (row := self.listbox.get_first_child()) is not None:
            self.listbox.remove(row)
        for item in self.items:
            self.listbox.append(self._make_row(item))
        self.stack.set_visible_child_name('list' if self.items else 'empty')

    def _make_row(self, item):
        row = Adw.ActionRow(title=GLib.markup_escape_text(item['title']),
                            subtitle=GLib.markup_escape_text(
                                describe_filter(item)),
                            activatable=True)
        row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        row.connect('activated', lambda _r: self.edit(item))
        return row

    def edit(self, item=None):
        self.navigation_view.push(
            FilterEditPage(self.client, item, on_saved=self._on_saved))

    def _on_saved(self, _item):
        self.refresh()


def present_filters(parent_widget, client):
    """Open the filter manager over *parent_widget*'s window."""
    navigation_view = Adw.NavigationView()
    listing = FiltersList(client, navigation_view)

    add = Gtk.Button(icon_name='list-add-symbolic',
                     tooltip_text=_('New Filter'))
    add.update_property([Gtk.AccessibleProperty.LABEL], [_('New Filter')])
    add.connect('clicked', lambda _b: listing.edit(None))
    header = Adw.HeaderBar()
    header.pack_start(add)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(header)
    toolbar.set_content(listing)
    root = Adw.NavigationPage(title=_('Filters'), child=toolbar,
                              tag='filters')
    navigation_view.add(root)

    dialog = Adw.Dialog(title=_('Filters'), content_width=520,
                        content_height=640)
    dialog.set_child(navigation_view)
    dialog.filters_list = listing
    dialog.present(parent_widget)
    return dialog
