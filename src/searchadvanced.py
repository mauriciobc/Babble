# SPDX-License-Identifier: GPL-3.0-or-later

"""Advanced search dialog for the search page."""

import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


class AdvancedSearchDialog(feedback.ToastDialog):
    """Builds a query with the operators Mastodon understands, the way
    Tuba's advanced search dialog does."""

    def __init__(self, from_query=''):
        super().__init__(_('Advanced Search'), height=680)
        self.on_result = None

        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        search = Gtk.Button(label=_('_Search'), use_underline=True)
        search.add_css_class('suggested-action')
        connect_weak(search, 'clicked', self._on_search)
        self.header.pack_end(search)

        intro = Adw.PreferencesGroup(
            description=_('Advanced search may not work on every server. '
                          'Invert an option by prefixing it with “-”, for '
                          'example -is:reply hides replies.'))
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
        self.language_row = Adw.EntryRow(
            title=_('Language Code (for example pt)'))
        self.language_row.set_max_length(8)
        where.add(self.language_row)
        self.page.add(where)
        self.submit_on_enter(search, self.query_row, self.user_row,
                             self.language_row)

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
        calendar.set_date(GLib.DateTime.new_local(
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

    def _on_cancel(self, _button):
        self.close()

    def _on_search(self, _button):
        query = self.build_query()
        if not query:
            self.toast(_('Enter something to search for'))
            return
        callback = self.on_result
        self.close()
        if callback is not None:
            callback(query)
