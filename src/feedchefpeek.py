# SPDX-License-Identifier: GPL-3.0-or-later
"""Smart New Posts on a recipe's timeline (Java section 12).

Two halves, one for each side of the page:

* ``SourcePeek`` is what ``FeedChefSource`` adds: the cache of scores, the
  peek (a cook of page one on the engine worker, with the person and server
  counts of the whole cook session, so the caps keep holding across the
  merged list) and the bookkeeping that keeps a held peek honest.
* ``PagePeek`` is what ``FeedChefTimelinePage`` adds: a timer that exists only
  while a recipe page is on screen with Feed Chef on, the pill, and the
  merge into the top of the list.

The decisions (when the pill is worth showing, how the window is ordered,
what the merge must keep) are in ``feedchef.smartmerge``, which has no
widget. A peek costs one request to the recipe's scope every
``PEEK_INTERVAL_S`` seconds for each open recipe page, and none while the
window is hidden, the system saves power or the connection is metered.
"""

import logging
import threading
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw  # noqa: E402

from . import appsettings, feedback, sysstate  # noqa: E402
from .feedchef import smartmerge  # noqa: E402
from .feedchef.smartmerge import (PEEK_INTERVAL_S,  # noqa: E402
                                  PEEK_PAGE_BASE, ScoreCache)
from .i18n import ngettext  # noqa: E402
from .statusrun import StatusRunRow  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

_log = logging.getLogger(__name__)

# Rows read for the viewport statistics when the list has not been laid out
# yet (no row reports being on screen): the top of the list.
UNLAID_ROWS = 5


@dataclass
class PeekResult:
    """What a peek found, held until the reader asks for it or it goes
    stale."""

    items: list
    scores: dict
    relevance: dict
    counts: dict
    servers: dict
    fire_ids: set
    logger: Any
    rev: int
    order: list = field(default_factory=list)


class SourcePeek:
    """The peeking half of ``FeedChefSource`` (a mixin)."""

    def _peek_init(self):
        # The ids of the posts in the list: their scores are never evicted
        # (the LRU only forgets posts no longer shown).
        self.shown = set()
        self.scores = ScoreCache(keep=self.shown.__contains__)
        # Bumped whenever the counts of the cook session change; a peek
        # made against another revision no longer fits the list.
        self._rev = 0
        self._peek_token = None
        self._peek_serial = 0
        self.peeked = None
        # Called with the PeekResult when one is held, None when the held
        # one is gone; set by the page with a weak method.
        self.peek_listener = None

    @property
    def busy(self):
        """A cook or a peek is out: another would supersede it."""
        return self._active is not None or self._peek_token is not None

    def cache_page(self, result):
        """Remember the final score of every post a page shows."""
        relevance = (result.kitchen_meta or {}).get('relevance_scores') or {}
        for status in result.items:
            sid = str(status.get('id') or '')
            self.shown.add(sid)
            if sid in result.score_map:
                self.scores.put(sid, result.score_map[sid],
                                relevance.get(sid))

    def _peek_forget(self, notify=True):
        """Drop what is held and what is out; its log is deleted."""
        self._peek_token = None
        self._peek_drop()
        if notify and self.peek_listener is not None:
            self.peek_listener(None)

    def _peek_drop(self):
        held, self.peeked = self.peeked, None
        if held is not None:
            self._discard(held.logger)

    def _background(self, work):
        """Run *work* off the main thread: on the engine's worker, or, when
        the engine is gone, on a short-lived thread (never skipped)."""
        run = getattr(self.engine, 'run_background', None)
        try:
            if run is None:
                raise RuntimeError('no worker')
            run(work)
        except RuntimeError:
            threading.Thread(target=work, daemon=True).start()

    def _discard(self, logger):
        if logger is not None:
            self._background(logger.discard)

    def peek(self, shown_ids):
        """Cook page one again on the engine worker, leaving out
        *shown_ids*. False when there is nothing to peek at yet or a
        cook is out."""
        if self.busy or not self._cook_id:
            return False
        token = self._peek_token = object()
        self._peek_serial += 1
        # The recipe the page on screen was cooked from (an experiment's
        # arm, not necessarily the reader's), and never a variant tag.
        self.engine.process_feed(
            getattr(self, '_session_recipe', None) or self.recipe,
            count=self.peek_count,
            on_done=partial(self._on_peek_done, token, self._rev),
            on_error=partial(self._on_peek_error, token),
            author_counts=dict(self._author_counts), hide=self.peek_hide,
            cook_id=self._cook_id,
            page_index=PEEK_PAGE_BASE + self._peek_serial,
            instance_counts=dict(self._instance_counts),
            peek=True, exclude=frozenset(shown_ids))
        return True

    def _on_peek_error(self, token, error):
        if token is self._peek_token:
            self._peek_token = None
            _log.debug('feedchef: a peek failed: %s', error)

    def _on_peek_done(self, token, rev, result):
        if token is not self._peek_token:
            self._discard(result.logger)
            return
        self._peek_token = None
        if rev != self._rev or not result.items:
            # The session moved on, or there is nothing new: what was
            # held is not worth the reader's attention any more either.
            self._discard(result.logger)
            self._peek_drop()
            if self.peek_listener is not None:
                self.peek_listener(None)
            return
        self._peek_drop()
        relevance = (result.kitchen_meta or {}).get('relevance_scores') or {}
        self.peeked = PeekResult(
            items=list(result.items), scores=dict(result.score_map),
            relevance=dict(relevance),
            counts=dict(result.author_counts or {}),
            servers=dict(getattr(result, 'instance_counts', None) or {}),
            fire_ids=set((result.kitchen_meta.get('fire') or {}).get(
                'ids') or ()),
            logger=result.logger, rev=rev,
            order=[str(s.get('id') or '') for s in result.items])
        if self.peek_listener is not None:
            self.peek_listener(self.peeked)

    def take_peek(self):
        """The held peek, if it still fits the list; it stays held until
        ``commit_merge`` (a merge that is refused leaves it for
        ``refuse_peek``)."""
        held = self.peeked
        if held is not None and held.rev != self._rev:
            self._peek_forget()
            return None
        return held

    def refuse_peek(self):
        self._peek_forget()

    def commit_merge(self, held, merge):
        """The reader has the peek's posts: its counts are the cook
        session's now, its scores are cached, its page is a page of the
        cook (outcome 'shown') and its file says so."""
        self.peeked = None
        self._rev += 1
        self._author_counts = dict(held.counts)
        self._instance_counts = dict(held.servers)
        self.fire_ids.update(held.fire_ids)
        for status in held.items:
            sid = str(status.get('id') or '')
            self.shown.add(sid)
            if sid in held.scores:
                self.scores.put(sid, held.scores[sid],
                                held.relevance.get(sid))
        logger = held.logger
        if logger is not None and logger.record is not None \
                and logger.mark_shown(merge, write=False):
            self._audits = (self._audits + [logger.record])[
                -self.audit_keep:]
            # Copies made here: the worker never sees the record change.
            self._background(logger.writer())


def paused_by(widget):
    """Why a peek must wait, or '': 'hidden' (the page is not on screen
    or its window is hidden), 'covered' (another page of its navigation
    view is the visible one), 'power-saver' or 'metered'."""
    root = widget.get_root()
    if not widget.get_mapped() or (
            root is not None and hasattr(root, 'get_visible')
            and not root.get_visible()):
        return 'hidden'
    nav_page = widget.get_ancestor(Adw.NavigationPage)
    view = (nav_page.get_ancestor(Adw.NavigationView)
            if nav_page is not None else None)
    if view is not None and view.get_visible_page() is not nav_page:
        return 'covered'
    monitor = sysstate.monitor()
    if monitor.get_property('power-saver'):
        return 'power-saver'
    if monitor.get_property('metered'):
        return 'metered'
    return ''


def _visible_rows(widget):
    """Every row shell (``StatusRunRow``) under *widget*, on screen or
    not; the caller keeps the ones that are mapped and inside the
    viewport."""
    found = []
    stack = [widget.get_first_child()]
    while stack:
        node = stack.pop()
        while node is not None:
            if isinstance(node, StatusRunRow):
                found.append(node)
            else:
                stack.append(node.get_first_child())
            node = node.get_next_sibling()
    return found


def _id_of(item):
    return str((getattr(item, 'raw', None) or {}).get('id'))


class PagePeek:
    """The peeking half of ``FeedChefTimelinePage`` (a mixin)."""

    def _peek_init(self):
        self._peek_view = None
        self.source.peek_listener = weak_method(self._on_peek_result)
        connect_weak(self, 'map', self._peek_sync)
        connect_weak(self, 'unmap', self._peek_sync)
        monitor = sysstate.monitor()
        connect_weak(monitor, 'notify::power-saver', self._peek_sync)
        connect_weak(monitor, 'notify::metered', self._peek_sync)
        connect_weak(appsettings.settings(), 'changed::feed-chef-enabled',
                     self._peek_sync)

    def _watch_view(self):
        """Follow which page of the navigation view is the visible one:
        a page pushed over this one covers it, and a covered page does
        not peek."""
        nav_page = self.get_ancestor(Adw.NavigationPage)
        view = (nav_page.get_ancestor(Adw.NavigationView)
                if nav_page is not None else None)
        if view is None or (self._peek_view is not None
                            and self._peek_view() is view):
            return
        self._peek_view = view.weak_ref()
        connect_weak(view, 'notify::visible-page', self._peek_sync)

    def _peek_sync(self, *_args):
        """The peek timer exists only while this recipe page is the one
        on screen, Feed Chef is on and nothing asks the page to wait; it
        is the poll timer of ``TimelinePage``, which ``_on_auto_refresh``
        turns into a peek."""
        self._watch_view()
        wanted = (not self._scope.closed and not paused_by(self)
                  and appsettings.settings().get_boolean(
                      'feed-chef-enabled'))
        interval = PEEK_INTERVAL_S if wanted else 0
        if interval and self._auto_refresh_id:
            return
        self.set_auto_refresh_interval(interval)

    def _on_auto_refresh(self):
        """A tick: peek at page one, unless the page is busy."""
        client = getattr(self, 'client', None)
        enabled = appsettings.settings().get_boolean('feed-chef-enabled')
        if self._scope.closed or not enabled or self.model.loading \
                or self._loading_older or self._older_pending \
                or paused_by(self) \
                or (client is not None and client.rate_limited()):
            return
        self.source.peek(self._shown_ids())

    def _shown_ids(self):
        ids = set()
        for position in range(self.model.get_n_items()):
            ids |= self.model.get_item(position).ids()
        return ids

    def _visible_scores(self):
        """Cached scores of the posts on screen that have one (the top
        rows when the list has not been laid out)."""
        height = self._scrolled.get_height()
        seen = []
        for row in _visible_rows(self.list_view):
            run = getattr(row, '_run', None)
            if run is None or not row.get_mapped():
                continue
            ok, bounds = row.compute_bounds(self._scrolled)
            if ok and bounds.get_y() < height \
                    and bounds.get_y() + bounds.get_height() > 0:
                seen.extend(run.items)
        if not seen:
            seen = [self.model.get_item(position) for position in range(
                min(UNLAID_ROWS, self.model.get_n_items()))]
        cache = self.source.scores
        scores = [cache.get(_id_of(item)) for item in seen]
        return [score for score in scores if score is not None]

    def _on_peek_result(self, held):
        """A peek landed (or the held one is gone): the pill is shown
        only when a new post scores above the viewport's mean plus one
        standard deviation; otherwise the result waits silently. A peek
        with nothing the list would take is dropped."""
        self._pill_count = 0
        if held is None:
            self.new_posts_revealer.set_reveal_child(False)
            return
        fresh = self.model.unseen(held.items)
        if not fresh:
            self.source.refuse_peek()
            return
        worth = smartmerge.worth_interrupting(
            self._visible_scores(), [held.scores.get(
                str(s.get('id') or ''), 0.0) for s in fresh])
        if worth:
            self._show_new_pill(len(fresh))
        else:
            self.new_posts_revealer.set_reveal_child(False)

    def _show_new_pill(self, count):
        """The pill of a peek, with its own label (the base pill counts
        posts that arrived, not posts worth seeing), announced as it
        reads."""
        self._pill_count = count
        # Translators: the label of the pill on a recipe's timeline when
        # new posts that score well arrived; %d is how many.
        label = ngettext('%d New Post Worth Seeing',
                         '%d New Posts Worth Seeing', count) % count
        self.new_pill.set_label(label)
        self.new_posts_revealer.set_reveal_child(True)
        feedback.announce(self, label)

    def _on_new_pill_clicked(self, button):
        self._insert_peeked()
        TimelinePage._on_new_pill_clicked(self, button)

    def _insert_peeked(self):
        """Put the held peek's posts on top and order the first
        ``RERANK_WINDOW_SIZE`` posts again by cached score, announcing only
        the spans that changed. A merge that breaks a promise of the list,
        or whose window has a post with no score, is refused."""
        held = self.source.take_peek()
        if held is None:
            return
        model, recipe, cache = self.model, self.recipe, self.source.scores
        fresh = model.unseen(held.items)
        for status in fresh:
            sid = str(status.get('id') or '')
            if sid in held.scores:
                cache.put(sid, held.scores[sid], held.relevance.get(sid))
        current = [model.get_item(i).raw
                   for i in range(model.get_n_items())]
        proposed = smartmerge.merge_head(recipe, current, fresh, cache)
        before = [str(s.get('id')) for s in current]
        after = [str(s.get('id')) for s in proposed]
        inserted = [str(s.get('id')) for s in fresh]
        statuses = {str(s.get('id')): s for s in [*current, *fresh]}
        broken = smartmerge.merge_violations(
            recipe, before=before, inserted=inserted, after=after,
            peek_order=held.order, statuses=statuses, cache=cache)
        if not fresh or broken or not model.plain(fresh):
            _log.warning('feedchef: merge refused (%s)',
                         ', '.join(broken) or 'nothing new, or grouped')
            self.source.refuse_peek()
            return
        rank = {sid: i for i, sid in enumerate(after)}
        model.merge_head(
            fresh, lambda items: sorted(
                items, key=lambda item: rank.get(_id_of(item), len(rank))),
            smartmerge.RERANK_WINDOW_SIZE)
        self.source.commit_merge(held, smartmerge.merge_record(
            recipe, inserted=inserted, after=after, statuses=statuses,
            cache=cache))
