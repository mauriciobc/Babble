# SPDX-License-Identifier: GPL-3.0-or-later
"""The top of the recipe builder: the recipe in one sentence, and how many
posts it keeps right now.

The sentence follows every edit at once (it is only text). The count is
cooked again from one fetched pantry (``feedchef.livecook``) on a worker,
shortly after the last edit (a debounce), so a change costs no request; until
the new number arrives the old one stays, dimmed, and says it is updating.
Nothing here waits on the worker: the answer comes back through the main
loop, and the builder never lags behind the form."""

import copy

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import accounts  # noqa: E402
from .feedchef.livecook import LiveCook  # noqa: E402
from .feedchef.sentence import recipe_sentence  # noqa: E402
from .feedchefbridge import make_engine  # noqa: E402
from .feedchefexplain import WORDS  # noqa: E402
from .feedchefsource import viewer_hides  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from . import feedback  # noqa: E402
from .uihelpers import icon_button, set_accessible_label  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

# How long after the last edit the count is made again: the answer lands
# well inside half a second of it.
DEBOUNCE_MS = 300


def make_live_cook(client, account):
    """The worker that counts for one builder (a test replaces this)."""
    key = accounts.account_key(account)
    account_id = str(account.get('id') or '')

    def engine_for(recipe):
        return make_engine(client, key, account_id, recipe, audit=False)

    return LiveCook(engine_for, deliver=lambda callback: GLib.idle_add(
        callback), hide=viewer_hides)


class LiveSummary:
    """Mixin of the builder: the sentence row and the live count row.
    The builder offers ``_collect()`` (the recipe on screen),
    ``_selected_scope()`` and ``_selected_list_id()``."""

    sentence_label = None
    count_row = None
    _live = None
    _timer = 0
    _shown_count = None

    def build_summary_group(self, page):
        group = Adw.PreferencesGroup()
        # A wrapped label, not a row: the sentence is text to read.
        self.sentence_label = Gtk.Label(
            xalign=0.0, wrap=True, selectable=True, margin_bottom=6)
        group.add(self.sentence_label)
        self.count_row = Adw.ActionRow(
            title=_('Counting posts…'), use_markup=False, activatable=False)
        self.count_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        set_accessible_label(self.count_spinner, _('Updating the count'))
        self.count_row.add_suffix(self.count_spinner)
        self.count_retry = icon_button('view-refresh-symbolic',
                                       _('Count Again'))
        self.count_retry.add_css_class('flat')
        self.count_retry.set_visible(False)
        connect_weak(self.count_retry, 'clicked', self._on_count_retry)
        self.count_row.add_suffix(self.count_retry)
        group.add(self.count_row)
        page.add(group)
        return group

    # Starting and stopping

    def start_summary(self):
        """Show the sentence and ask for the first count (this fetches the
        pantry once)."""
        connect_weak(self, 'closed', self._stop_summary)
        self.summary_changed(first=True)

    def _stop_summary(self, *_args):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        live, self._live = self._live, None
        if live is not None:
            live.close()

    # Edits

    def summary_changed(self, first=False):
        """The recipe on screen changed: the sentence at once, the count
        stale until it is cooked again."""
        recipe = self._collect()
        self.sentence_label.set_label(recipe_sentence(recipe, WORDS))
        if self._live is not None:
            # An answer to what was asked before this edit is not about the
            # recipe on screen: it must never show as fresh.
            self._live.supersede()
        if not self._counts():
            self._stop_pending()
            self._count_idle(_('Choose a list to count posts'))
            return
        self._count_stale()
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(
            0 if first else DEBOUNCE_MS, self._tick_summary)

    def _counts(self):
        return self._selected_scope() != 'list' \
            or bool(self._selected_list_id())

    def _stop_pending(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._live is not None:
            # The answer of the last request is not wanted any more.
            self._live.supersede()

    def _tick_summary(self):
        self._timer = 0
        if self._live is None:
            self._live = make_live_cook(self.client, self.account)
        recipe = copy.deepcopy(self._collect())
        self._live.request(recipe, weak_method(self._on_count),
                           weak_method(self._on_count_error))
        return GLib.SOURCE_REMOVE

    # The count row

    def _count_stale(self):
        self.count_row.add_css_class('dimmed')
        self.count_row.set_subtitle(_('Updating…'))
        self.count_spinner.set_spinning(True)
        self.count_spinner.set_visible(True)
        self.count_retry.set_visible(False)
        if self._shown_count is None:
            self.count_row.set_title(_('Counting posts…'))

    def _count_idle(self, text):
        self.preview_row.set_subtitle('')
        self.count_row.remove_css_class('dimmed')
        self.count_row.set_title(text)
        self.count_row.set_subtitle('')
        self.count_spinner.set_spinning(False)
        self.count_spinner.set_visible(False)
        self.count_retry.set_visible(False)
        self._shown_count = None

    def _on_count(self, count):
        self._shown_count = count
        self.count_row.remove_css_class('dimmed')
        title = ngettext('%d post matches', '%d posts match',
                         count.matching) % count.matching
        changed = title != self.count_row.get_title()
        self.count_row.set_title(title)
        self.preview_row.set_subtitle(ngettext(
            # Translators: under Preview Recipe; how many of the recent
            # posts the recipe keeps.
            '%(matching)d of %(fetched)d posts matches',
            '%(matching)d of %(fetched)d posts match', count.matching) % {
                'matching': count.matching, 'fetched': count.fetched})
        if changed:
            feedback.announce(self.count_row, title)
        self.count_row.set_subtitle(ngettext(
            # Translators: %d is how many recent posts were looked at.
            'Out of the latest %d post', 'Out of the latest %d posts',
            count.fetched) % count.fetched)
        self.count_spinner.set_spinning(False)
        self.count_spinner.set_visible(False)
        self.count_retry.set_visible(False)

    def _on_count_error(self, _error):
        """Fail soft: the form works, the row says it could not count."""
        self._shown_count = None
        self.preview_row.set_subtitle('')
        self.count_row.remove_css_class('dimmed')
        self.count_row.set_title(_('Could not count posts'))
        self.count_row.set_subtitle(
            _('The recipe can still be saved or previewed'))
        self.count_spinner.set_spinning(False)
        self.count_spinner.set_visible(False)
        self.count_retry.set_visible(True)

    def _on_count_retry(self, _button):
        self.summary_changed(first=True)
