# SPDX-License-Identifier: GPL-3.0-or-later

"""Search: live debounced search over accounts, hashtags and posts,
recent searches, operator hints, an advanced search dialog, and
resolving pasted URLs.

Tuba: Views/Search.vala (+ advanced_search.ui). Phanpy: pages/search.jsx
and components/search-form.jsx.
"""

import datetime
import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .lists import ToastDialog, error_text_markup  # noqa: E402

RECENT_KEY = 'recent-searches'
RECENT_MAX = 10
PREVIEW_COUNT = 5
PAGE_LIMIT = 20
SEARCH_DELAY_MS = 300
URL_PATTERN = re.compile(r'^https?://\S+$', re.I)


# Recent searches

def recent_searches():
    return list(pages.settings().get_strv(RECENT_KEY))


def add_recent(query):
    query = query.strip()
    if not query:
        return
    items = [q for q in recent_searches() if q != query]
    items.insert(0, query)
    pages.settings().set_strv(RECENT_KEY, items[:RECENT_MAX])


def remove_recent(query):
    pages.settings().set_strv(
        RECENT_KEY, [q for q in recent_searches() if q != query])


def clear_recent():
    pages.settings().set_strv(RECENT_KEY, [])


def compact_number(value):
    value = int(value or 0)
    if value >= 1_000_000:
        return '%.1fM' % (value / 1_000_000)
    if value >= 1_000:
        return '%.1fK' % (value / 1_000)
    return str(value)


def account_subtitle(account):
    from gettext import ngettext
    followers = int(account.get('followers_count') or 0)
    return '@%s · %s' % (
        account.get('acct', ''),
        ngettext('%s follower', '%s followers', followers)
        % compact_number(followers))


# Searching

def operator_hints():
    """(operator to insert, title, description) for the tips list."""
    return (
        ('from:', _('From a User'),
         _('Posts by one account, e.g. from:me or from:user@example.social')),
        ('has:', _('Containing'), _('has:media, has:poll or has:embed')),
        ('is:', _('Kind of Post'), _('is:reply or is:sensitive')),
        ('language:', _('Language'), _('Two-letter code, e.g. language:pt')),
        ('before:', _('Before a Date'), _('before:2025-01-31')),
        ('after:', _('After a Date'), _('after:2025-01-31')),
        ('during:', _('On a Date'), _('during:2025-01-31')),
        ('in:', _('Where'), _('in:all, in:library or in:public')),
    )


class SearchTypePage(Adw.Bin):
    """All results of one type (accounts, hashtags or statuses) with
    offset pagination."""

    __gtype_name__ = 'BabbleSearchTypePage'

    def __init__(self, client, query, kind):
        super().__init__()
        self.client = client
        self.query = query
        self.kind = kind
        self._offset = 0
        self._loading = False
        self._done = False
        self._generation = 0
        self.count = 0

        self.stack = Gtk.Stack()
        self.stack.add_named(pages.make_loading_page(_('Searching')),
                             'loading')
        self.error_page = pages.make_error_page(
            _('Search Failed'), lambda *_a: self.refresh())
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(Adw.StatusPage(
            icon_name='edit-find-symbolic', title=_('No Results'),
            description=_('Nothing matched “%s”') % query), 'empty')

        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scrolled.add_css_class('timeline-list')
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                         margin_top=12, margin_bottom=24)
        if kind == 'statuses':
            self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        else:
            self.container = Gtk.ListBox(
                selection_mode=Gtk.SelectionMode.NONE,
                margin_start=12, margin_end=12)
            self.container.add_css_class('boxed-list')
            self.container.connect('row-activated', self._on_activated)
        column.append(self.container)
        self.more_spinner = Adw.Spinner(visible=False,
                                        halign=Gtk.Align.CENTER,
                                        margin_top=12)
        column.append(self.more_spinner)
        self.scrolled.set_child(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH,
                                          child=column))
        self.stack.add_named(self.scrolled, 'list')
        self.set_child(self.stack)
        self.scrolled.get_vadjustment().connect('value-changed',
                                                self._on_scroll)
        self.refresh()

    def _on_activated(self, _box, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            callback()

    def _on_scroll(self, adjustment):
        if adjustment.get_value() + adjustment.get_page_size() \
                >= adjustment.get_upper() - 600:
            self._load()

    def refresh(self):
        self._generation += 1
        self._offset = 0
        self._done = False
        self._loading = False
        self.count = 0
        while (child := self.container.get_first_child()) is not None:
            self.container.remove(child)
        self.stack.set_visible_child_name('loading')
        self._load()

    def _load(self):
        if self._loading or self._done:
            return
        self._loading = True
        generation = self._generation
        if self._offset:
            self.more_spinner.set_visible(True)
        self.client.request_async(
            'GET', '/api/v2/search',
            params={'q': self.query, 'type': self.kind,
                    'limit': PAGE_LIMIT, 'offset': self._offset},
            callback=lambda c, r, _d=None: self._on_loaded(c, r, generation))

    def _on_loaded(self, client, result, generation):
        if generation != self._generation:
            return
        self._loading = False
        self.more_spinner.set_visible(False)
        try:
            data = client.request_finish(result)
        except GLib.Error as error:
            if self.count:
                pages.report_error(self, _('Could not load more'), error)
            else:
                self.error_page.set_description(error_text_markup(error))
                self.stack.set_visible_child_name('error')
            return
        items = data.get(self.kind) or []
        self._offset += len(items)
        if len(items) < PAGE_LIMIT:
            self._done = True
        for item in items:
            self.container.append(make_result_widget(
                self.client, self.kind, item))
            self.count += 1
        self.stack.set_visible_child_name('list' if self.count else 'empty')
        if self.count and not self._done:
            GLib.idle_add(self._maybe_fill)

    def _maybe_fill(self):
        adjustment = self.scrolled.get_vadjustment()
        if adjustment.get_upper() <= adjustment.get_page_size() + 600:
            self._load()
        return GLib.SOURCE_REMOVE


def make_result_widget(client, kind, item):
    """The row for one search result of *kind*."""
    if kind == 'accounts':
        return pages.AccountRow(client, item,
                                subtitle=account_subtitle(item))
    if kind == 'hashtags':
        from .hashtags import HashtagRow
        return HashtagRow(client, item)
    return make_post_row(client, item)


def make_post_row(client, status):
    """A timeline post card that opens the thread when clicked."""
    from .timeline import StatusItem, StatusRow
    item = StatusItem(status)
    row = StatusRow()
    row.bind(item, client)
    gesture = Gtk.GestureClick()
    gesture.connect('released',
                    lambda *_a: row.activate_status(item))
    row.add_controller(gesture)
    return row


class SearchPage(Adw.Bin):
    """Search entry in the header, recent searches and operator hints
    before typing, grouped results (Accounts / Hashtags / Posts) after."""

    __gtype_name__ = 'BabbleSearchPage'

    def __init__(self, client, query=None):
        super().__init__()
        self.client = client
        self.query = ''
        self._serial = 0
        self._resolved_url = None

        self.entry = Gtk.SearchEntry(
            placeholder_text=_('Search accounts, hashtags and posts'),
            search_delay=SEARCH_DELAY_MS, hexpand=True)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   [_('Search')])
        self.entry.connect('search-changed', self._on_search_changed)
        self.entry.connect('activate', self._on_activate)
        self.entry.connect('stop-search', self._on_stop_search)
        self.title_widget = Adw.Clamp(maximum_size=460, child=self.entry)
        self.advanced_button = pages.icon_button(
            'preferences-other-symbolic', _('Advanced Search'),
            self.present_advanced)
        self.header_widgets = [self.advanced_button]

        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self._build_start()
        self.stack.add_named(pages.make_loading_page(_('Searching')),
                             'loading')
        self.error_page = pages.make_error_page(
            _('Search Failed'), lambda *_a: self.search(resolve=True))
        self.stack.add_named(self.error_page, 'error')
        self.empty_page = Adw.StatusPage(
            icon_name='edit-find-symbolic', title=_('No Results'))
        self.stack.add_named(self.empty_page, 'empty')
        self.results_scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.results_scrolled.add_css_class('timeline-list')
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=12, margin_top=12,
                                   margin_bottom=24)
        self.results_scrolled.set_child(Adw.Clamp(
            maximum_size=pages.COLUMN_WIDTH, child=self.results_box))
        self.stack.add_named(self.results_scrolled, 'results')
        self.stack.set_visible_child_name('start')
        self.connect('map', lambda *_a: self.entry.grab_focus())

        if query:
            self.set_query(query, submit=True)

    def refresh(self):
        if self.query:
            self.search(resolve=True)
        else:
            self._rebuild_recent()

    # Start view: recent searches and operator tips

    def _build_start(self):
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                         margin_top=12, margin_bottom=24, margin_start=12,
                         margin_end=12)
        self.recent_group = Adw.PreferencesGroup(title=_('Recent Searches'))
        self.clear_button = Gtk.Button(label=_('_Clear'), use_underline=True,
                                       valign=Gtk.Align.CENTER)
        self.clear_button.add_css_class('flat')
        self.clear_button.connect('clicked', self._on_clear_recent)
        self.recent_group.set_header_suffix(self.clear_button)
        self.recent_rows = []
        column.append(self.recent_group)

        self.tips_group = Adw.PreferencesGroup(
            title=_('Search Tips'),
            description=_('Add operators to narrow posts down. Prefix one '
                          'with “-” to exclude it, e.g. -is:reply. Search '
                          'operators may not work on every server.'))
        for operator, title, description in operator_hints():
            row = Adw.ActionRow(title=title, subtitle=description,
                                activatable=True, use_markup=False)
            code = Gtk.Label(label=operator, valign=Gtk.Align.CENTER)
            code.add_css_class('monospace')
            code.add_css_class('dimmed')
            row.add_suffix(code)
            row.connect('activated', self._on_hint, operator)
            self.tips_group.add(row)
        advanced = Adw.ActionRow(title=_('Advanced Search…'),
                                 activatable=True)
        advanced.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        advanced.connect('activated', lambda *_a: self.present_advanced())
        self.tips_group.add(advanced)
        column.append(self.tips_group)
        scrolled.set_child(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH,
                                     child=column))
        self.stack.add_named(scrolled, 'start')
        self._rebuild_recent()

    def _rebuild_recent(self):
        for row in self.recent_rows:
            self.recent_group.remove(row)
        self.recent_rows = []
        recents = recent_searches()
        self.recent_group.set_visible(bool(recents))
        for query in recents:
            row = Adw.ActionRow(title=query, activatable=True,
                                use_markup=False, title_lines=1)
            row.add_prefix(Gtk.Image(icon_name='document-open-recent-symbolic'))
            remove = pages.icon_button('edit-clear-symbolic',
                                       _('Remove from Recent Searches'))
            remove.connect('clicked', self._on_remove_recent, query)
            row.add_suffix(remove)
            row.connect('activated', self._on_recent_activated, query)
            self.recent_group.add(row)
            self.recent_rows.append(row)

    def _on_recent_activated(self, _row, query):
        self.set_query(query, submit=True)

    def _on_remove_recent(self, _button, query):
        remove_recent(query)
        self._rebuild_recent()

    def _on_clear_recent(self, _button):
        clear_recent()
        self._rebuild_recent()

    def _on_hint(self, _row, operator):
        text = self.entry.get_text()
        if text and not text.endswith(' '):
            text += ' '
        self.entry.set_text(text + operator)
        self.entry.set_position(-1)
        self.entry.grab_focus()

    # Query handling

    def set_query(self, query, submit=False):
        self.entry.set_text(query)
        self.entry.set_position(-1)
        self.query = query.strip()
        if submit:
            self.submit()

    def _on_stop_search(self, _entry):
        self.entry.set_text('')

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if query == self.query:
            return
        self.query = query
        if not query:
            self._serial += 1
            self._rebuild_recent()
            self.stack.set_visible_child_name('start')
            return
        if URL_PATTERN.match(query):
            self._resolve_url(query)
            return
        self.search(resolve=False)

    def _on_activate(self, _entry):
        self.query = self.entry.get_text().strip()
        self.submit()

    def submit(self):
        """Search with resolve=true and remember the query."""
        if not self.query:
            return
        add_recent(self.query)
        if URL_PATTERN.match(self.query):
            self._resolve_url(self.query, force=True)
        else:
            self.search(resolve=True)

    def _request(self, params, callback):
        self._serial += 1
        serial = self._serial
        self.stack.set_visible_child_name('loading')

        def finished(client, result, _d=None):
            if serial != self._serial:
                return
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                self.error_page.set_description(error_text_markup(error))
                self.stack.set_visible_child_name('error')
                return
            callback(data)

        self.client.request_async('GET', '/api/v2/search', params=params,
                                  callback=finished)

    def _resolve_url(self, url, force=False):
        if url == self._resolved_url and not force:
            return
        self._resolved_url = url

        def resolved(data):
            if data.get('statuses'):
                pages.open_status(self, self.client, data['statuses'][0])
            elif data.get('accounts'):
                pages.open_profile(self, self.client, data['accounts'][0])
            else:
                self._show_results(data)

        self._request({'q': url, 'resolve': 'true',
                       'limit': PREVIEW_COUNT + 1}, resolved)

    def search(self, resolve=False):
        if not self.query:
            return
        params = {'q': self.query, 'limit': PREVIEW_COUNT + 1}
        if resolve:
            params['resolve'] = 'true'
        self._request(params, self._show_results)

    # Results

    def _show_results(self, data):
        while (child := self.results_box.get_first_child()) is not None:
            self.results_box.remove(child)
        sections = (
            ('accounts', _('Accounts'), _('See All Accounts')),
            ('hashtags', _('Hashtags'), _('See All Hashtags')),
            ('statuses', _('Posts'), _('See All Posts')),
        )
        total = 0
        for kind, title, see_all in sections:
            items = data.get(kind) or []
            if not items:
                continue
            total += len(items)
            self.results_box.append(self._section(
                kind, title, see_all, items))
        if not total:
            self.empty_page.set_description(
                _('Nothing matched “%s”') % self.query)
            self.stack.set_visible_child_name('empty')
            return
        self.results_scrolled.get_vadjustment().set_value(0)
        self.stack.set_visible_child_name('results')

    def _section(self, kind, title, see_all, items):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = pages.section_label(title)
        heading.set_margin_start(12)
        heading.set_margin_end(12)
        box.append(heading)
        shown = items[:PREVIEW_COUNT]
        if kind == 'statuses':
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for status in shown:
                inner.append(make_post_row(self.client, status))
            box.append(inner)
            more_parent = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                      margin_start=12, margin_end=12)
            more_parent.add_css_class('boxed-list')
        else:
            list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   margin_start=12, margin_end=12)
            list_box.add_css_class('boxed-list')
            list_box.connect('row-activated', self._on_row_activated)
            for item in shown:
                list_box.append(make_result_widget(self.client, kind, item))
            box.append(list_box)
            more_parent = list_box
        if len(items) > PREVIEW_COUNT:
            more = Adw.ButtonRow(title=see_all)
            more.connect('activated', self._on_see_all, kind, title)
            more_parent.append(more)
            if kind == 'statuses':
                box.append(more_parent)
        return box

    def _on_row_activated(self, _box, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            add_recent(self.query)
            callback()

    def _on_see_all(self, _row, kind, title):
        add_recent(self.query)
        pages.push_page(self, '%s: %s' % (title, self.query),
                        SearchTypePage(self.client, self.query, kind))

    # Advanced search

    def present_advanced(self):
        dialog = AdvancedSearchDialog(self.entry.get_text())
        dialog.on_result = lambda text: self.set_query(text, submit=True)
        dialog.present(self)


class AdvancedSearchDialog(ToastDialog):
    """Builds a query with the operators Mastodon understands, the way
    Tuba's advanced search dialog does."""

    def __init__(self, from_query=''):
        super().__init__(_('Advanced Search'), height=680)
        self.on_result = None

        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_a: self.close())
        self.header.pack_start(cancel)
        search = Gtk.Button(label=_('_Search'), use_underline=True)
        search.add_css_class('suggested-action')
        search.connect('clicked', self._on_search)
        self.header.pack_end(search)
        self.set_default_widget(search)

        intro = Adw.PreferencesGroup(
            description=_('Advanced search may not work on every server. '
                          'Invert an option by prefixing it with “-”, e.g. '
                          '-is:reply hides replies.'))
        self.query_row = Adw.EntryRow(title=_('Query'))
        self.user_row = Adw.EntryRow(title=_('From User'))
        self.reply_row = Adw.SwitchRow(title=_('Only Replies'))
        self.cw_row = Adw.SwitchRow(
            title=_('Only Posts with Content Warnings'))
        for row in (self.query_row, self.user_row, self.reply_row,
                    self.cw_row):
            intro.add(row)
        self.page.add(intro)

        containing = Adw.PreferencesGroup(title=_('Containing'))
        self.media_row = Adw.SwitchRow(title=_('Media'))
        self.poll_row = Adw.SwitchRow(title=_('Poll'))
        self.embed_row = Adw.SwitchRow(title=_('Embed'))
        for row in (self.media_row, self.poll_row, self.embed_row):
            containing.add(row)
        self.page.add(containing)

        where = Adw.PreferencesGroup(title=_('Timelines'))
        self.scope_row = Adw.ComboRow(
            title=_('Search In'),
            model=Gtk.StringList.new([
                _('Everywhere'),
                _('Library (Favorites, Bookmarks, Own Posts)'),
                _('Public')]))
        where.add(self.scope_row)
        self.language_row = Adw.EntryRow(title=_('Language Code (e.g. pt)'))
        self.language_row.set_max_length(8)
        where.add(self.language_row)
        self.page.add(where)

        dates = Adw.PreferencesGroup(title=_('Dates'))
        self.before = self._date_row(_('Before'), dates)
        self.during = self._date_row(_('During'), dates)
        self.after = self._date_row(_('After'), dates)
        self.page.add(dates)

        if from_query:
            self._load(from_query)

    def _date_row(self, title, group):
        row = Adw.ExpanderRow(title=title, show_enable_switch=True,
                              enable_expansion=False)
        calendar = Gtk.Calendar(margin_top=6, margin_bottom=6,
                                margin_start=6, margin_end=6)
        calendar.remove_css_class('view')
        inner = Gtk.ListBoxRow(activatable=False, selectable=False,
                               child=calendar)
        row.add_row(inner)
        group.add(row)
        return row, calendar

    def _set_date(self, pair, text):
        row, calendar = pair
        try:
            date = datetime.date.fromisoformat(text)
        except ValueError:
            return
        calendar.select_day(GLib.DateTime.new_local(
            date.year, date.month, date.day, 0, 0, 0))
        row.set_enable_expansion(True)

    def _date_text(self, pair):
        row, calendar = pair
        if not row.get_enable_expansion():
            return None
        date = calendar.get_date()
        return '%04d-%02d-%02d' % (date.get_year(), date.get_month(),
                                   date.get_day_of_month())

    def _load(self, text):
        rest = []
        for word in text.split():
            lowered = word.lower()
            if lowered == 'is:reply':
                self.reply_row.set_active(True)
            elif lowered == 'is:sensitive':
                self.cw_row.set_active(True)
            elif lowered == 'has:media':
                self.media_row.set_active(True)
            elif lowered == 'has:poll':
                self.poll_row.set_active(True)
            elif lowered == 'has:embed':
                self.embed_row.set_active(True)
            elif lowered == 'in:all':
                self.scope_row.set_selected(0)
            elif lowered == 'in:library':
                self.scope_row.set_selected(1)
            elif lowered == 'in:public':
                self.scope_row.set_selected(2)
            elif lowered.startswith('from:') and len(word) > 5:
                self.user_row.set_text(word[5:])
            elif lowered.startswith('language:') and len(word) > 9:
                self.language_row.set_text(word[9:])
            elif lowered.startswith('before:'):
                self._set_date(self.before, word[7:])
            elif lowered.startswith('during:'):
                self._set_date(self.during, word[7:])
            elif lowered.startswith('after:'):
                self._set_date(self.after, word[6:])
            else:
                rest.append(word)
        self.query_row.set_text(' '.join(rest))

    def build_query(self):
        parts = []
        text = self.query_row.get_text().strip()
        if text:
            parts.append(text)
        user = self.user_row.get_text().strip().lstrip('@')
        if user:
            parts.append('from:' + user)
        if self.reply_row.get_active():
            parts.append('is:reply')
        if self.cw_row.get_active():
            parts.append('is:sensitive')
        if self.media_row.get_active():
            parts.append('has:media')
        if self.poll_row.get_active():
            parts.append('has:poll')
        if self.embed_row.get_active():
            parts.append('has:embed')
        scope = self.scope_row.get_selected()
        if scope == 1:
            parts.append('in:library')
        elif scope == 2:
            parts.append('in:public')
        language = self.language_row.get_text().strip()
        if language:
            parts.append('language:' + language)
        for name, pair in (('before', self.before), ('during', self.during),
                           ('after', self.after)):
            date = self._date_text(pair)
            if date:
                parts.append('%s:%s' % (name, date))
        return ' '.join(parts)

    def _on_search(self, _button):
        query = self.build_query()
        if not query:
            self.toast(_('Enter something to search for'))
            return
        callback = self.on_result
        self.close()
        if callback is not None:
            callback(query)
