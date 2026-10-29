# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up: a separate view over the people you follow (CATCH-UP.md).

You choose a time range, Babble pages back through the home timeline to
that point and saves the result as an unchanging snapshot. This module is
the page around that: the period menu, the fetch loop, the snapshot
round-trip, and the All posts list (Phanpy's compact list with category and
author filters, sorts and grouping). The Highlights grid is another module's
widget; it is handed a :class:`Digest` and nothing else.

The data work lives elsewhere and has no widgets: ``digest`` (filters,
categories, boost deduplication, trends, sort keys), ``rank`` (the
relevance score) and ``store`` (the files). What is here is plumbing and
widgets, and the two rules that keep it light:

* Nothing heavy runs on the main loop. Ranking and the snapshot file I/O run
  on a worker thread over plain data (:func:`run`); the widgets are
  built, a chunk at a time, from idle callbacks.
* Filters and sorts change only the list's models. One ``CatchUpItem`` is
  built per visible post, once per snapshot, and the list, the posts bar and
  the Highlights slot all share it.

Ported from Phanpy's ``src/pages/catchup.jsx`` (MIT, (c) Lim Chee Aun; the
notice is in ``src/catchup/digest.py``).
"""

import dataclasses
import datetime
import functools
import math

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from ..i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import accounts as account_store  # noqa: E402
from . import store as catchupstore  # noqa: E402
from .. import pages  # noqa: E402
from ..entities import lift  # noqa: E402
from ..mastodon import summarize_error_markup  # noqa: E402
from ..accounts import plain_name as _plain_name  # noqa: E402
from ..mediacache import AvatarCache  # noqa: E402
from ..timelinebase import ui_language  # noqa: E402
from .page_controls import PageControls  # noqa: E402
from .pipeline import (
    BUILD_CHUNK, COLUMN_WIDTH, DEFAULT_PERIOD, LOW_ACTIVITY_PERIOD,
    LOW_ACTIVITY_POSTS, PERIOD_HOURS, STOPPED_COMPLETE, STOPPED_ERROR,
    STOPPED_RATE_LIMITED, Digest, ViewState, run, hours_between, hours_phrase,
    now_utc, period_title, posts_phrase, _TOP_DATA)
from .fetch import CatchUpRun, run_in_thread, save_result
from .digest import parse_time  # noqa: E402
from .rows import CatchUpItem, CatchUpRow, _Slot  # noqa: E402


def _prewarm_work():
    """Build the ranker's tokenizer patterns on a worker thread."""
    from .rank_text import prewarm
    prewarm()


def _prewarm_done(_value, _error):
    """Nothing to hand back: the patterns are cached in ``rank_text``."""


class CatchUpPage(PageControls, Adw.Bin):
    """The Catch Up place (CATCH-UP.md 2): first run, scanning, results."""

    __gtype_name__ = 'BabbleCatchUpPage'

    def __init__(self, client, account=None):
        super().__init__()
        self.client = client
        self.account = account or {}
        if account:
            self.account_key = account_store.account_key(account)
        else:
            self.account_key = '%s#' % getattr(client, 'instance', '')
        self.state = 'loading'
        self._disposed = False
        self._generation = 0
        self._metas = []
        self._snapshot_id = None
        self._save_failed = False
        self._period = DEFAULT_PERIOD
        self._run = None
        self._run_period = DEFAULT_PERIOD
        self._computed = None
        self._items = {}
        self._matching = set()
        self._grouped = False
        self._view = None
        self._view_states = {}
        self._view_state = ViewState()
        self._syncing = False
        self._menu_category = None
        self._menu_for = None
        self._author_ids = [None]
        self._building = None
        self._idles = set()
        self._toast_id = 0
        self._toast_text = ''
        self._menu_buttons = []
        self._period_buttons = []
        self._highlights = None
        self._highlights_tried = False
        self._results_built = False

        self._actions = Gio.SimpleActionGroup()
        self._period_action = Gio.SimpleAction.new_stateful(
            'period', GLib.VariantType.new('s'),
            GLib.Variant('s', DEFAULT_PERIOD))
        pages.connect_weak(self._period_action, 'change-state',
                           self._on_period_changed)
        self._actions.add_action(self._period_action)
        for name, handler in (('show', self._on_show_snapshot),
                              ('remove', self._on_remove_snapshot)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            pages.connect_weak(action, 'activate', handler)
            self._actions.add_action(action)
        self.insert_action_group('catchup', self._actions)

        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self.stack.add_named(pages.make_loading_page(_('Loading')),
                             'loading')
        self.stack.add_named(self._build_first_run(), 'first-run')
        self.stack.add_named(self._build_scanning(), 'scanning')
        self.error_page = pages.make_error_page(_('Could Not Catch Up'),
                                                self._on_retry)
        self.stack.add_named(self.error_page, 'error')
        self._add_keys()

        # First, so it leads the snapshot worker, which ranks as well.
        run_in_thread(_prewarm_work, _prewarm_done)
        self._load_snapshots()

    # ----------------------------------------------------------------------
    # Lifetime

    def do_dispose(self):
        self.cancel()
        Adw.Bin.do_dispose(self)

    def cancel(self):
        """Stop everything in flight (the page is closed or the account
        changed); nothing more is saved or shown."""
        self._disposed = True
        self._generation += 1
        if self._run is not None:
            self._run.abort()
            self._run = None
        for source in list(self._idles):
            GLib.source_remove(source)
        self._idles.clear()
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        self._building = None

    def _idle(self, method):
        """Call *method* once from an idle source below GDK's redraw. The
        source is removed when the page is cancelled, and holds the page
        weakly, so a page dropped meanwhile is not kept alive."""
        idles = self._idles
        call = pages.weak_method(method)
        holder = []

        def run():
            idles.discard(holder[0])
            call()
            return GLib.SOURCE_REMOVE

        source = GLib.idle_add(run, priority=GLib.PRIORITY_DEFAULT_IDLE)
        holder.append(source)
        idles.add(source)

    def _set_state(self, name):
        self.state = name
        self.stack.set_visible_child_name(name)

    # ----------------------------------------------------------------------
    # Static pages


    # ----------------------------------------------------------------------
    # Loading snapshots from disk

    def _languages(self):
        languages = [ui_language()]
        language = (self.account.get('source') or {}).get('language')
        if language and language.lower() not in languages:
            languages.append(language.lower())
        return languages

    def _load_snapshots(self):
        key = self.account_key
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        generation = self._generation

        def work():
            found = catchupstore.list_snapshots(key)
            for path, _meta in found:
                try:
                    snapshot = lift(catchupstore.read_snapshot(path))
                except (OSError, ValueError):
                    continue
                return ([m for _p, m in found],
                        run(snapshot, languages, hide))
            return ([m for _p, m in found], None)

        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_snapshots_loaded), generation))

    def _on_snapshots_loaded(self, generation, value, error):
        if self._disposed or generation != self._generation:
            return
        if error is not None or value is None:
            self._metas = []
            self._set_state('first-run')
            return
        self._metas, computed = value
        if computed is None:
            self._refresh_period_menus()
            self._set_state('first-run')
            return
        self._begin_present(computed)

    def _open_snapshot(self, snapshot_id):
        meta = next((m for m in self._metas if m['id'] == snapshot_id), None)
        if meta is None:
            return
        path = catchupstore.snapshot_path(self.account_key, snapshot_id)
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        self._generation += 1
        generation = self._generation

        def work():
            return run(
                lift(catchupstore.read_snapshot(path)), languages, hide)

        self._scan_label(_('Opening the catch-up…'))
        self._set_state('scanning')
        self.cancel_button.set_visible(False)
        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_snapshot_opened), generation,
            snapshot_id))

    def _on_snapshot_opened(self, generation, snapshot_id, computed, error):
        if self._disposed or generation != self._generation:
            return
        self._save_failed = False
        if error is not None:
            # Drop the snapshot that would not open, not the one on
            # screen, which is still readable.
            self._metas = [m for m in self._metas
                           if m['id'] != snapshot_id]
            self._show_error(str(error))
            return
        self._begin_present(computed)

    # ----------------------------------------------------------------------
    # Running a catch-up (3.1)

    def _scan_label(self, text):
        self.scan_label.set_label(text)

    def _start_run(self, period):
        if self._run is not None or self._disposed or self.client is None:
            return
        end_at = now_utc()
        start_at = None
        hours = PERIOD_HOURS.get(period)
        if hours:
            start_at = end_at - datetime.timedelta(hours=hours)
        elif period == 'since':
            start_at = parse_time(self._metas[0].get('end_at')) \
                if self._metas else None
            if start_at is None:
                period = DEFAULT_PERIOD
                start_at = end_at - datetime.timedelta(hours=24)
        self._run_period = period
        self._period = period
        self._generation += 1
        self._popdown_menus()
        self._run = CatchUpRun(
            self.client, start_at, end_at,
            self_ids=[self.account.get('id')] if self.account.get('id')
            else [],
            hide_trending=pages.settings().get_boolean('hide-trending'),
            progress=pages.weak_method(self._on_progress),
            done=pages.weak_method(self._on_run_done))
        self._scan_label(_('Scanning timeline…'))
        self.cancel_button.set_visible(True)
        self.cancel_button.set_sensitive(True)
        self._set_state('scanning')
        self._run.start()

    def _on_progress(self, evaluated, oldest):
        if self._disposed or self._run is None:
            return
        hours = hours_between(self._run.end_at, oldest)
        # Translators: shown while Catch Up reads the home timeline;
        # "h" is hours, as in "8 h back"
        self._scan_label(ngettext(
            'Scanning timeline… %(count)d post evaluated (%(hours)d h back)',
            'Scanning timeline… %(count)d posts evaluated '
            '(%(hours)d h back)', evaluated)
            % {'count': evaluated, 'hours': hours})

    def _on_run_done(self, result):
        active, self._run = self._run, None
        if self._disposed or active is None:
            return
        if not result.posts and result.stopped in (STOPPED_ERROR,
                                                   STOPPED_RATE_LIMITED):
            self._show_error(result.error)
            return
        languages = self._languages()
        hide = pages.settings().get_boolean('hide-trending')
        self._scan_label(_('Preparing your catch-up…'))
        self.cancel_button.set_visible(False)
        generation = self._generation
        work = functools.partial(
            save_result, result, self.account_key, self._run_period,
            languages, hide)
        run_in_thread(work, functools.partial(
            pages.weak_method(self._on_run_saved), generation))

    def _on_run_saved(self, generation, value, error):
        if self._disposed or generation != self._generation:
            return
        if error is not None:
            self._show_error(str(error))
            return
        saved, metas, computed = value
        # A catch-up that could not be written is still worth showing;
        # the banner says it will not come back.
        self._save_failed = not saved
        if metas is not None:
            self._metas = metas
        self._begin_present(computed)

    def _show_error(self, message):
        if message:
            self.error_page.set_description(summarize_error_markup(message))
        else:
            self.error_page.set_description('')
        self._set_state('error')

    # ----------------------------------------------------------------------
    # Building the items, a chunk per idle

    def _begin_present(self, computed):
        self._generation += 1
        self._building = {'computed': computed, 'at': 0, 'items': {},
                          'generation': self._generation}
        self._scan_label(_('Preparing your catch-up…'))
        self.cancel_button.set_visible(False)
        if self.state not in ('scanning',):
            self._set_state('scanning')
        self._idle(self._build_chunk)

    def _build_chunk(self):
        building = self._building
        if building is None or self._disposed \
                or building['generation'] != self._generation:
            return
        visible = building['computed'].visible
        end = min(building['at'] + BUILD_CHUNK, len(visible))
        items = building['items']
        for post in visible[building['at']:end]:
            items[post['id']] = CatchUpItem(post)
        building['at'] = end
        if end < len(visible):
            self._idle(self._build_chunk)
            return
        self._building = None
        self._present(building['computed'], items)

    # ----------------------------------------------------------------------
    # The results

    def _ensure_results(self):
        if self._results_built:
            return
        self._results_built = True
        self.store = Gio.ListStore.new(CatchUpItem)
        self.top_item = CatchUpItem(_TOP_DATA, top=True)
        item_type = CatchUpItem.__gtype__
        self.filter = Gtk.BoolFilter.new(
            Gtk.PropertyExpression.new(item_type, None, 'matches'))
        self.filtered = Gtk.FilterListModel.new(self.store, self.filter)
        self.sorter = Gtk.NumericSorter.new(
            Gtk.PropertyExpression.new(item_type, None, 'sort-rank'))
        self.section_sorter = Gtk.NumericSorter.new(
            Gtk.PropertyExpression.new(item_type, None, 'author-rank'))
        self.sorted = Gtk.SortListModel.new(self.filtered, self.sorter)

        factory = Gtk.SignalListItemFactory()
        pages.connect_weak(factory, 'setup', self._on_item_setup)
        pages.connect_weak(factory, 'bind', self._on_item_bind)
        pages.connect_weak(factory, 'unbind', self._on_item_unbind)
        headers = Gtk.SignalListItemFactory()
        pages.connect_weak(headers, 'setup', self._on_header_setup)
        pages.connect_weak(headers, 'bind', self._on_header_bind)
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection.new(self.sorted), factory=factory,
            header_factory=headers, single_click_activate=True,
            show_separators=True)
        self.list_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Posts')])
        pages.connect_weak(self.list_view, 'activate',
                           self._on_row_activated)

        self._build_top()
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scrolled.set_child(clamp)
        self.stack.add_named(self.scrolled, 'results')

    # The block above the posts


    # List item factories

    def _on_item_setup(self, _factory, list_item):
        list_item.set_child(_Slot())

    def _on_item_bind(self, _factory, list_item):
        item = list_item.get_item()
        slot = list_item.get_child()
        if item.is_top:
            list_item.set_activatable(False)
            list_item.set_focusable(False)
            holder = self.top.get_parent()
            if holder is not None and holder is not slot:
                holder.set_child(None)
            slot.set_child(self.top)
            return
        list_item.set_activatable(True)
        list_item.set_focusable(True)
        if slot.row is None:
            slot.row = CatchUpRow()
        slot.set_child(slot.row)
        others = self._computed.boosters.get(item.raw['id'], ()) \
            if self._computed is not None else ()
        slot.row.bind(item, others)

    def _on_item_unbind(self, _factory, list_item):
        item = list_item.get_item()
        slot = list_item.get_child()
        if item is not None and item.is_top:
            if self.top.get_parent() is slot:
                slot.set_child(None)
        elif slot.row is not None:
            slot.row.unbind()

    def _on_header_setup(self, _factory, header):
        box = Gtk.Box(spacing=8, margin_top=12, margin_bottom=4,
                      margin_start=12, margin_end=12)
        avatar = Adw.Avatar(size=28, show_initials=True)
        name = Gtk.Label(xalign=0.0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('heading')
        count = Gtk.Label()
        count.add_css_class('caption')
        count.add_css_class('dimmed')
        for child in (avatar, name, count):
            box.append(child)
        box.avatar, box.name, box.count = avatar, name, count
        header.set_child(box)

    def _on_header_bind(self, _factory, header):
        item = header.get_item()
        box = header.get_child()
        if item is None or item.is_top:
            box.set_visible(False)
            return
        box.set_visible(True)
        account = item.status.get('account') or {}
        name = _plain_name(account)
        box.avatar.set_text(name)
        AvatarCache.load(box.avatar, account)
        box.name.set_label(name)
        box.count.set_label(posts_phrase(header.get_n_items()))

    def _on_row_activated(self, _view, position):
        item = self.sorted.get_item(position)
        if item is None or item.is_top:
            return
        pages.open_status(self, self.client, item.raw)

    # ----------------------------------------------------------------------
    # Presenting a snapshot

    def _begin_period_state(self, snapshot):
        self._snapshot_id = snapshot.get('id')
        period = snapshot.get('period')
        self._period = period if period in PERIOD_HOURS or period in (
            'since', 'max') else DEFAULT_PERIOD

    def _present(self, computed, items):
        self._ensure_results()
        snapshot = computed.snapshot
        self._computed = computed
        self._items = items
        self._begin_period_state(snapshot)
        self._view_state = dataclasses.replace(
            self._view_states.get(snapshot.get('id'), ViewState()))
        self._matching = set(items)         # new items match by default
        self._grouped = False
        self._menu_category = None
        self._rebuild_category_toggles()
        self._apply_view()
        entries = [self.top_item] + list(items.values())
        self.store.splice(0, self.store.get_n_items(), entries)
        self._refresh_period_menus()
        self._update_header()
        self.bar.set_data(computed.bar_dots, computed.bar_bins)
        self._sync_bar()
        self._present_highlights(computed)
        self._set_state('results')

    def _present_highlights(self, computed):
        if not self._highlights_tried:
            self._highlights_tried = True
            from .tiles import HighlightsView
            self._highlights = HighlightsView(self.client)
            self.highlights_slot.append(self._highlights)
            self._highlights.on_pages_changed = pages.weak_method(
                self._on_pages_changed)
        if self._highlights is None:
            return
        self._highlights.set_digest(Digest(
            posts=computed.ranked, scores=computed.scores,
            items=self._items, news=computed.news, tags=computed.tags,
            trending_keys=computed.trending_keys,
            boosters=computed.boosters, end_at=computed.end_at),
            pages=self._view_state.pages)

    def _on_pages_changed(self, pages):
        """The user expanded the Highlights with "Show More"; remember how
        far, so reopening this snapshot starts where they left it (7)."""
        if self._snapshot_id is None:
            return
        # Both: _apply_view copies the live state back over the stored one,
        # so updating only the dict would be undone on the next change.
        self._view_state.pages = pages
        self._view_states[self._snapshot_id] = dataclasses.replace(
            self._view_state)

    def _update_header(self):
        computed = self._computed
        snapshot = computed.snapshot
        period = self._period
        self.title_label.set_label(period_title(period))
        end_at = computed.end_at
        stopped = snapshot.get('stopped') or STOPPED_COMPLETE
        count = snapshot.get('count') or len(snapshot.get('posts') or ())
        oldest = parse_time(snapshot.get('oldest_at'))
        covered = hours_between(end_at, oldest)

        note = ''
        if stopped != STOPPED_COMPLETE:
            # Translators: shown under the Catch Up title when it could not
            # go back as far as asked; %(range)s is like "the last 7 hours"
            # and %(posts)s like "400 posts"
            note = _('Covers %(range)s (%(posts)s)') % {
                'range': hours_phrase(covered), 'posts': posts_phrase(count)}
        self.note_label.set_label(note)
        self.note_label.set_visible(bool(note))

        low = stopped == STOPPED_COMPLETE and count < LOW_ACTIVITY_POSTS
        if low:
            hours = PERIOD_HOURS.get(period)
            if hours:
                range_text = hours_phrase(hours)
            elif period == 'since':
                range_text = _('since your last catch-up')
            else:
                range_text = _('your timeline')
            # Translators: %(range)s is like "the last 6 hours"
            self.low_banner.set_title(ngettext(
                'Only %(count)d post in %(range)s',
                'Only %(count)d posts in %(range)s', count)
                % {'count': count, 'range': range_text})
            can_widen = period != LOW_ACTIVITY_PERIOD and period != 'max'
            self.low_banner.set_button_label(
                _('Catch up on 24 hours') if can_widen else '')
        self.low_banner.set_revealed(low)

        limited = stopped == STOPPED_RATE_LIMITED
        if limited:
            client = self.client
            wait = client.retry_after_seconds()
            if wait > 0:
                minutes = max(1, int(math.ceil(wait / 60)))
                self.limit_banner.set_title(ngettext(
                    'The server is limiting requests, so this catch-up is '
                    'incomplete. Try again in %d minute.',
                    'The server is limiting requests, so this catch-up is '
                    'incomplete. Try again in %d minutes.', minutes)
                    % minutes)
            else:
                self.limit_banner.set_title(_(
                    'The server limited requests, so this catch-up is '
                    'incomplete.'))
        self.limit_banner.set_revealed(limited)
        self.save_banner.set_revealed(self._save_failed)
        self.options_button.set_sensitive(count > 0)

    # ----------------------------------------------------------------------
    # Controls <-> view state


    # ----------------------------------------------------------------------
    # Keyboard (2.6)



