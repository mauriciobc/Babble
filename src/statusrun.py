# SPDX-License-Identifier: GPL-3.0-or-later

import collections

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')


from gi.repository import (GLib, GObject, Gio,  # noqa: E402
                           Gtk)

from . import filterrules  # noqa: E402
from .pages import connect_weak  # noqa: E402

"""Run shells: several posts by one author drawn as one card."""
from .timelinebase import CARD_SHELLS_KEPT
from .timelinedata import BoostCarouselItem


def _shell_stamp(run):
    """What the shell's height depends on, besides the column width.

    An edit, a toggled content warning or a carousel whose boosts
    changed must miss the cached height and be measured again. The
    stamp is a tuple of tuples, not the post text, so the cache does
    not keep a second copy of every status."""
    parts = []
    for item in run.items:
        status = item.status
        ui = item.ui
        parts.append((
            item.status_id,
            status.get('edited_at'),
            len(status.get('content') or ''),
            len(status.get('spoiler_text') or ''),
            len(status.get('media_attachments') or []),
            bool(ui.get('expanded')),
            ui.get('cw_open'),
            bool(ui.get('media_revealed')),
            bool(ui.get('filter_shown')),
            ui.get('source_tag'),
            item.translation is not None,
            len(getattr(item, 'boosts', ()) or ()),
        ))
    return tuple(parts)


def _known_shell_height(shell, run):
    """Pinned height for *run* at the shell's current width, or 0."""
    height = getattr(run, '_shell_height', 0)
    if height <= 64 or getattr(run, '_shell_stamp', None) != _shell_stamp(run):
        return 0
    width = shell.get_width()
    stored = getattr(run, '_shell_width', 0)
    if width > 1 and stored > 1 and abs(width - stored) > 4:
        return 0
    return height


# Shells mapped while their cards still have to be built. ListView maps
# every new shell on items-changed and its layout unmaps the off-screen
# ones right after, so building cards in the map handler stalls a page
# load on cards nobody sees. One idle source builds one waiting shell per
# dispatch instead; at default idle priority it runs below GDK's redraw,
# so the frame's layout has already unmapped the off-screen shells. GObject
# weak refs: the queue never keeps a shell (or its page) alive.
_pending_shells = collections.deque()
_pending_source = 0


def _populate_pending():
    """Build the cards of the next shell that is still mapped and still
    empty; keep the source while shells are waiting."""
    global _pending_source
    while _pending_shells:
        shell = _pending_shells.popleft()()
        if shell is None:
            continue
        shell._queued = False
        if shell.get_mapped() and shell._run is not None and not shell.rows:
            shell._populate()
            return GLib.SOURCE_CONTINUE
    _pending_source = 0
    return GLib.SOURCE_REMOVE


class StatusRunRow(Gtk.Box):
    """Card for one run: the author line, then every post of the run with
    its own body, media and 'n/total' badge inside the same card."""

    # Replaced with the height of posts that have actually been shown.
    _typical_height = 64
    # Width of a shell that has actually been allocated. ListView maps
    # every new shell to measure it and unmaps the off-screen ones
    # before that allocation exists, so get_width() is still 0 then.
    _column_width = 0
    # Unmapped shells that still hold cards, least recently unmapped
    # first. GObject weak refs, so the LRU never keeps a shell (and its
    # page) alive; keyed by a per-shell counter, not the object.
    _kept = collections.OrderedDict()
    _next_key = 0

    # Which view the rows belong to, so the page's filters apply to them.
    # Set by the factory that builds the shells; see TimelinePage.
    filter_context = filterrules.HOME

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.set_valign(Gtk.Align.START)
        self.set_vexpand(False)
        self.add_css_class('status-run')
        self.rail = Gtk.Box()
        self.rail.add_css_class('run-rail')
        self.rail.set_valign(Gtk.Align.FILL)
        self.rail.set_vexpand(True)
        self.set_hexpand(True)
        self.append(self.rail)
        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.column.set_valign(Gtk.Align.START)
        self.column.set_vexpand(False)
        self.column.set_hexpand(True)
        self.append(self.column)
        self.rows = []
        self.item = None
        self._run = None
        self._client = None
        kind = type(self)
        self._key = kind._next_key
        kind._next_key += 1
        # Whether _pending_shells holds a ref to this shell.
        self._queued = False
        # Built cards, kept while this run is scrolled off screen.
        # Gtk.ListView still instantiates a child for every loaded post
        # (up to a couple of hundred) and lays that whole set out on
        # every top insertion. A StatusRow is a large tree, so it is
        # hidden while the run is unmapped (measure and snapshot skip
        # it) and shown again on the way back, instead of being
        # unparented and reparented at every stop. Hidden cards still
        # cost memory (~77 MB per page at realistic image sizes), so only
        # CARD_SHELLS_KEPT unmapped shells app-wide keep theirs; the
        # rest are dropped through _kept and rebuilt on demand.
        self._cards = {}
        # Floor so an unmapped run does not collapse to 0px, which would
        # keep the list from ever mapping it. Later rows use the height
        # of posts already shown, so the scrollbar is not stuck at one
        # line per post.
        self.set_size_request(-1, type(self)._typical_height)
        connect_weak(self, 'map', self._on_map)
        connect_weak(self, 'unmap', self._on_unmap)

    def bind(self, run, client):
        self._run = run
        self._client = client
        self.item = run.items[0]
        keep = set(run.items)
        for item in [key for key in self._cards if key not in keep]:
            card = self._cards.pop(item)
            parent = card.get_parent()
            if parent is not None:
                parent.remove(card)
        # A run that changed is rebound to a shell that may be off screen
        # and still hold cards for the posts it shares: they count.
        if self._cards and not self.get_mapped():
            self._keep_cards()
        else:
            type(self)._kept.pop(self._key, None)
        known = _known_shell_height(self, run)
        self.set_size_request(-1, known or type(self)._typical_height)
        self._detach()
        # A shown run that changed is rebuilt at once, not left blank
        # until the idle queue reaches it.
        if self.get_mapped():
            self._populate()

    def _on_map(self, _widget):
        type(self)._kept.pop(self._key, None)
        run = self._run
        if run is None:
            return
        # Showing cards this shell kept is cheap; building them waits
        # for _populate_pending, which skips the shell if layout unmaps
        # it first.
        if all(item in self._cards for item in run.items):
            self._populate()
        else:
            self._queue_populate()

    def _queue_populate(self):
        global _pending_source
        # Card placeholder until the posts are built.
        self.add_css_class('status-run-pending')
        if not self._queued:
            self._queued = True
            _pending_shells.append(self.weak_ref())
        if not _pending_source:
            _pending_source = GLib.idle_add(_populate_pending)

    def _store_height(self, height, width):
        """Remember *height* so the next time this run is shown the list
        already has its final size and does not reflow every row below."""
        self.set_size_request(-1, height)
        kind = type(self)
        if kind._typical_height <= 64:
            kind._typical_height = height
        else:
            kind._typical_height = (kind._typical_height * 3 + height) // 4
        run = self._run
        if run is None or width <= 1:
            return
        run._shell_height = height
        run._shell_width = width
        run._shell_stamp = _shell_stamp(run)

    def _on_unmap(self, _widget):
        # ListView maps every shell once, measures it, and unmaps the
        # off-screen ones before they have an allocation: get_height()
        # is still 0. A shell that showed its kept cards on that map
        # would collapse when they are hidden. The scrollbar shrinks to
        # the viewport and every row jumped into view later shifts the
        # whole list. Measure while the cards are still shown and pin
        # that height.
        width = self.get_width()
        if width > 1:
            type(self)._column_width = width
        else:
            width = type(self)._column_width
        # A shell still waiting for its cards is only as tall as its
        # placeholder: that height says nothing about the run.
        if self.rows:
            height = self.get_height()
            if height <= 64 and width > 1:
                height = self.measure(Gtk.Orientation.VERTICAL,
                                      width).natural
            if height > 64:
                self._store_height(height, width if width > 1 else 0)
        self._detach()
        if self._cards:
            self._keep_cards()

    def _keep_cards(self):
        """Count this unmapped shell's cards against CARD_SHELLS_KEPT,
        dropping the cards of the least recently unmapped shells."""
        kept = type(self)._kept
        kept[self._key] = self.weak_ref()
        kept.move_to_end(self._key)
        while len(kept) > CARD_SHELLS_KEPT:
            _key, ref = kept.popitem(last=False)
            shell = ref()
            if shell is not None and not shell.get_mapped():
                shell._drop_cards()

    def _drop_cards(self):
        for card in self._cards.values():
            if card.get_parent() is self.column:
                self.column.remove(card)
        self._cards = {}

    def _detach(self):
        # Hide, rather than unparent. A hidden child is skipped by
        # measure and snapshot, so a top insertion still lays out a
        # cheap shell, but scrolling back does not reparent the card.
        for row in self.rows:
            row.set_visible(False)
            row.remove_css_class('run-lead')
            row.remove_css_class('run-post')
        self.rows = []
        self.column.remove_css_class('status-row')
        self.rail.set_visible(False)
        self.remove_css_class('status-run-pending')

    def _populate(self):
        run = self._run
        if run is None or self.rows:
            return
        # A pinned or placeholder height stands in for an empty shell
        # only. As a minimum it would outlive the cards it stood in for:
        # a post that got shorter (wider column, closed content warning,
        # a poll answered) would sit above a blank gap, and the height
        # stored on the next unmap would keep that gap for good.
        self.set_size_request(-1, -1)
        self.remove_css_class('status-run-pending')
        multi = len(run.items) > 1
        self.rail.set_visible(multi)
        # One post keeps the normal card on StatusRow; a run shares one
        # card on the column and strips chrome from each inner row.
        if multi:
            self.column.add_css_class('status-row')
        previous = None
        for position, item in enumerate(run.items):
            row = self._cards.get(item)
            if row is None:
                from .statusrow import StatusRow
                row = StatusRow()
                row.filter_context = self.filter_context
                row.bind(item, self._client)
                row.set_vexpand(False)
                self._cards[item] = row
            if multi:
                if position:
                    # Continuation posts keep the thread badge and their
                    # own menu (which carries the post actions); the
                    # author line belongs to the run card above them.
                    row.add_css_class('run-post')
                    row.profile_button.set_visible(False)
                    row.time_label.set_visible(False)
                    row.actions.set_visible(False)
                else:
                    row.add_css_class('run-lead')
            row.set_run(position + 1, len(run.items))
            row.set_visible(True)
            if row.get_parent() is None:
                self.column.insert_child_after(row, previous)
            elif row.get_prev_sibling() is not previous:
                self.column.reorder_child_after(row, previous)
            previous = row
            self.rows.append(row)


class StatusRun(GObject.Object):
    """One card holding a run of consecutive same-thread posts by the
    same author: the author line is printed once, every post keeps its
    own body, media and 'n/total' badge."""

    __gtype_name__ = 'BabbleStatusRun'

    def __init__(self, items):
        super().__init__()
        self.items = items

    @GObject.Property(type=str)
    def status_id(self):
        return self.items[0].status_id


class RunModel(GObject.Object, Gio.ListModel):
    """Presents a :class:`TimelineModel` as runs of posts, so one card
    carries a whole same-thread run the way the bar draws it."""

    __gtype_name__ = 'BabbleRunModel'

    def __init__(self, source):
        super().__init__()
        self._source = source
        self._runs = []
        # Mirror of the source's items, kept in step from items-changed:
        # regrouping then reads a Python list instead of calling back
        # into the source for every post on every change.
        self._items = []
        connect_weak(source, 'items-changed', self._on_items_changed)
        self._on_items_changed(source, 0, 0, source.get_n_items())

    def _on_items_changed(self, source, position, removed, added):
        self._items[position:position + removed] = [
            source.get_item(index)
            for index in range(position, position + added)]
        self._rebuild()

    def _rebuild(self):
        groups = []
        previous = None
        for item in self._items:
            key = run_key(item)
            if groups and key is not None and key == previous:
                groups[-1].append(item)
            else:
                groups.append([item])
            previous = key
        # Keep the StatusRun objects of unchanged runs and announce only
        # the changed middle: the list view then keeps the widgets bound
        # to every other run instead of rebuilding all of them.
        old = self._runs
        n_old, n_new = len(old), len(groups)
        start = 0
        while start < n_old and start < n_new \
                and _same_items(old[start].items, groups[start]):
            start += 1
        end = 0
        while end < n_old - start and end < n_new - start \
                and _same_items(old[n_old - 1 - end].items,
                                groups[n_new - 1 - end]):
            end += 1
        removed = n_old - start - end
        added = n_new - start - end
        if not removed and not added:
            return
        self._runs = (old[:start]
                      + [StatusRun(group)
                         for group in groups[start:n_new - end]]
                      + old[n_old - end:])
        self.items_changed(start, removed, added)

    def do_get_n_items(self):
        return len(self._runs)

    def do_get_item(self, position):
        return self._runs[position]

    def do_get_item_type(self):
        return StatusRun


def _same_items(a, b):
    return len(a) == len(b) and all(
        x is y for x, y in zip(a, b, strict=True))


def run_key(item):
    """Key that decides whether two adjacent posts belong to one run."""
    key = item.__dict__.get('_run_key', run_key)
    if key is run_key:
        # Cached: a post's author and thread never change, and reading
        # GObject properties is slow enough to show on every regroup.
        if isinstance(item, BoostCarouselItem) or item.is_boost \
                or not item.handle:
            key = None
        else:
            key = item.thread_id
        item._run_key = key
    return key

