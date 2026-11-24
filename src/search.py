# SPDX-License-Identifier: GPL-3.0-or-later

"""Search: live debounced search over accounts, hashtags and posts,
recent searches, operator hints, an advanced search dialog, and
resolving pasted URLs.

Tuba: Views/Search.vala (+ advanced_search.ui). Phanpy: pages/search.jsx
and components/search-form.jsx.
"""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import (accountrows, appsettings, feedback, pagenav,  # noqa: E402
               pages, uihelpers)
from .api import search as api_search  # noqa: E402
from .lists import error_text_markup  # noqa: E402
from .requestscope import RequestScope  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .statustext import compact_count  # noqa: E402

RECENT_KEY = 'recent-searches'
RECENT_MAX = 10
PREVIEW_COUNT = 5
PAGE_LIMIT = 20
SEARCH_DELAY_MS = 300
URL_PATTERN = re.compile(r'^https?://\S+$', re.I)


# Recent searches

def recent_searches():
    return list(appsettings.settings().get_strv(RECENT_KEY))


def add_recent(query):
    query = query.strip()
    if not query:
        return
    items = [q for q in recent_searches() if q != query]
    items.insert(0, query)
    appsettings.settings().set_strv(RECENT_KEY, items[:RECENT_MAX])


def remove_recent(query):
    appsettings.settings().set_strv(
        RECENT_KEY, [q for q in recent_searches() if q != query])


def clear_recent():
    appsettings.settings().set_strv(RECENT_KEY, [])


def compact_number(value):
    """An interaction count, in the reader's locale.

    Was its own formatter with hardcoded 'K' and 'M', which put an
    English suffix and an English decimal point in front of a German or
    Brazilian Portuguese reader. statustext.compact_count is the one the
    rest of the app uses and is translated."""
    return compact_count(value)


def account_subtitle(account):
    from .i18n import ngettext
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
         # Translators: keep the "from:" operator and the examples as-is
         _('Posts by one account, for example from:me or '
           'from:user@example.social')),
        ('has:', _('Containing'), _('has:media, has:poll or has:embed')),
        ('is:', _('Kind of Post'), _('is:reply or is:sensitive')),
        ('language:', _('Language'),
         _('Two-letter code, for example language:pt')),
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
        self._scope = RequestScope()
        self.count = 0

        self.stack = Gtk.Stack()
        self.stack.add_named(uihelpers.make_loading_page(_('Searching')),
                             'loading')
        self.error_page = uihelpers.make_error_page(
            _('Search Failed'), self._on_retry)
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
            connect_weak(self.container, 'row-activated',
                               self._on_activated)
        column.append(self.container)
        self.more_spinner = Adw.Spinner(visible=False,
                                        halign=Gtk.Align.CENTER,
                                        margin_top=12)
        column.append(self.more_spinner)
        self.scrolled.set_child(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH,
                                          child=column))
        self.stack.add_named(self.scrolled, 'list')
        self.set_child(self.stack)
        connect_weak(self.scrolled.get_vadjustment(), 'value-changed',
                           self._on_scroll)
        self.refresh()

    def _on_retry(self, _button):
        self.refresh()

    def _on_activated(self, _box, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            callback()

    def _on_scroll(self, adjustment):
        if pages.near_bottom(adjustment):
            self._load()

    def close_requests(self):
        self._scope.close()

    def do_dispose(self):
        self.close_requests()
        Adw.Bin.do_dispose(self)

    def refresh(self):
        self._scope.begin()
        self._offset = 0
        self._done = False
        self._loading = False
        self.count = 0
        while (child := self.container.get_first_child()) is not None:
            self.container.remove(child)
        self.stack.set_visible_child_name('loading')
        self._load()

    def _load(self):
        if self._loading or self._done or self._scope.closed:
            return
        self._loading = True
        token = self._scope.start()
        self._load_token = token
        if self._offset:
            self.more_spinner.set_visible(True)
        api_search.search_async(
            self.client, self.query, type=self.kind, limit=PAGE_LIMIT,
            offset=self._offset,
            callback=token.callback(self._on_loaded,
                                    on_drop=self._on_load_dropped),
            cancellable=token.cancellable)

    def _on_load_dropped(self, _owner):
        # An older cancelled load must not clear loading while a newer
        # request (after refresh/begin) still owns _load_token.
        token = getattr(self, '_load_token', None)
        if token is not None and token.wanted():
            return
        self._loading = False
        self.more_spinner.set_visible(False)

    def _on_loaded(self, client, result):
        self._loading = False
        self.more_spinner.set_visible(False)
        try:
            data = api_search.search_finish(client, result)
        except GLib.Error as error:
            if self.count:
                feedback.report_error(self, _('Could not load more'), error)
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
        if adjustment.get_upper() <= adjustment.get_page_size() + pages.PRELOAD:
            self._load()
        return GLib.SOURCE_REMOVE


def make_result_widget(client, kind, item):
    """The row for one search result of *kind*."""
    if kind == 'accounts':
        return accountrows.AccountRow(client, item,
                                subtitle=account_subtitle(item))
    if kind == 'hashtags':
        from .hashtaglists import HashtagRow
        return HashtagRow(client, item)
    return make_post_row(client, item)


def make_post_row(client, status):
    """A timeline post card that opens the thread when clicked."""
    from .timelinedata import StatusItem
    from .statusrow import StatusRow
    item = StatusItem(status)
    row = StatusRow()
    row.bind(item, client)
    gesture = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
    # The gesture belongs to the row, so a handler that holds the row
    # strongly is the cycle through C that connect_weak exists to
    # prevent. connect_weak takes a bound method; this is a closure, so
    # hold the row by a GObject weak reference instead.
    row_ref = row.weak_ref()

    def open_thread(*_args):
        owner = row_ref()
        if owner is not None:
            owner.activate_status(item)

    gesture.connect('released', open_thread)
    row.add_controller(gesture)
    # Tab reaches the card (the .status-row focus ring shows it), and
    # Enter or space opens the thread, as the click does.
    row.set_focusable(True)
    uihelpers.activate_on_key(row, open_thread)
    return row


class SearchPage(Adw.Bin):
    """Search entry in the header, recent searches and operator hints
    before typing, grouped results (Accounts / Hashtags / Posts) after."""

    __gtype_name__ = 'BabbleSearchPage'

    def __init__(self, client, query=None):
        super().__init__()
        self.client = client
        self.query = ''
        self._scope = RequestScope()
        self._on_data = None
        self._resolved_url = None

        self.entry = Gtk.SearchEntry(
            placeholder_text=_('Search accounts, hashtags and posts'),
            search_delay=SEARCH_DELAY_MS, hexpand=True)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   [_('Search')])
        connect_weak(self.entry, 'search-changed',
                           self._on_search_changed)
        connect_weak(self.entry, 'activate', self._on_activate)
        connect_weak(self.entry, 'stop-search', self._on_stop_search)
        self.title_widget = Adw.Clamp(maximum_size=460, child=self.entry)
        self.advanced_button = uihelpers.icon_button(
            'preferences-other-symbolic', _('Advanced Search'))
        connect_weak(self.advanced_button, 'clicked',
                           self._on_advanced_clicked)
        self.header_widgets = [self.advanced_button]

        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self._build_start()
        self.stack.add_named(uihelpers.make_loading_page(_('Searching')),
                             'loading')
        self.error_page = uihelpers.make_error_page(
            _('Search Failed'), self._on_retry)
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
        connect_weak(self, 'map', self._on_map)

        if query:
            self.set_query(query, submit=True)

    def _on_map(self, _widget):
        self.entry.grab_focus()

    def _on_retry(self, _button):
        self.search(resolve=True)

    def _on_advanced_clicked(self, _widget):
        self.present_advanced()

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
        connect_weak(self.clear_button, 'clicked',
                           self._on_clear_recent)
        self.recent_group.set_header_suffix(self.clear_button)
        self.recent_rows = []
        column.append(self.recent_group)

        self.tips_group = Adw.PreferencesGroup(
            title=_('Search Tips'),
            description=_('Add operators to narrow posts down. Prefix one '
                          'with “-” to exclude it, for example -is:reply. '
                          'Search operators may not work on every server.'))
        for operator, title, description in operator_hints():
            row = Adw.ActionRow(title=title, subtitle=description,
                                activatable=True, use_markup=False)
            code = Gtk.Label(label=operator, valign=Gtk.Align.CENTER)
            code.add_css_class('monospace')
            code.add_css_class('dimmed')
            row.add_suffix(code)
            connect_weak(row, 'activated', self._on_hint, operator)
            self.tips_group.add(row)
        advanced = Adw.ActionRow(title=_('Advanced Search…'),
                                 activatable=True)
        advanced.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        connect_weak(advanced, 'activated', self._on_advanced_clicked)
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
            remove = uihelpers.icon_button('edit-clear-symbolic',
                                       _('Remove from Recent Searches'))
            connect_weak(remove, 'clicked', self._on_remove_recent,
                               query)
            row.add_suffix(remove)
            connect_weak(row, 'activated', self._on_recent_activated,
                               query)
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

    def close_requests(self):
        """Close the request scope (Navigation ``_stop_work`` on teardown).

        This is the sole ``SearchPage.close_requests``: place-teardown
        must not ship a serial-bump stub alongside it.
        """
        self._scope.close()

    def do_dispose(self):
        self.close_requests()
        Adw.Bin.do_dispose(self)

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if query == self.query:
            return
        self.query = query
        if not query:
            # Cancel in flight without starting another read, so a late
            # answer cannot paint over the start view.
            self._scope.begin()
            self._on_data = None
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

    def _request(self, params, on_data):
        token = self._scope.begin()
        self._on_data = on_data
        self.stack.set_visible_child_name('loading')
        api_search.search_async(
            self.client,
            callback=token.callback(self._on_request_done),
            cancellable=token.cancellable,
            **params)

    def _on_request_done(self, client, result):
        try:
            data = api_search.search_finish(client, result)
        except GLib.Error as error:
            self.error_page.set_description(error_text_markup(error))
            self.stack.set_visible_child_name('error')
            return
        on_data = self._on_data
        self._on_data = None
        if on_data is not None:
            on_data(data)

    def _resolve_url(self, url, force=False):
        if url == self._resolved_url and not force:
            return
        self._resolved_url = url
        self._request({'q': url, 'resolve': 'true',
                       'limit': PREVIEW_COUNT + 1}, self._apply_resolved)

    def _apply_resolved(self, data):
        if data.get('statuses'):
            pagenav.open_status(self, self.client, data['statuses'][0])
        elif data.get('accounts'):
            pagenav.open_profile(self, self.client, data['accounts'][0])
        else:
            self._show_results(data)

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
        heading = uihelpers.section_label(title)
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
            connect_weak(list_box, 'row-activated',
                               self._on_row_activated)
            for item in shown:
                list_box.append(make_result_widget(self.client, kind, item))
            box.append(list_box)
            more_parent = list_box
        if len(items) > PREVIEW_COUNT:
            more = Adw.ButtonRow(title=see_all)
            connect_weak(more, 'activated', self._on_see_all, kind,
                               title)
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
        pagenav.push_page(self, '%s: %s' % (title, self.query),
                        SearchTypePage(self.client, self.query, kind))

    # Advanced search

    def present_advanced(self):
        dialog = AdvancedSearchDialog(self.entry.get_text())
        dialog.on_result = lambda text: self.set_query(text, submit=True)
        dialog.present(self)


from .searchadvanced import AdvancedSearchDialog  # noqa: E402, F401
