# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')


from gi.repository import (GObject, Gio,  # noqa: E402
                           )

from .weakconnect import connect_weak  # noqa: E402
from .statustext import (relative_time)

"""Status rows' data: StatusItem, boost grouping and TimelineModel."""
from .timelinebase import (status_filter, GROUP_BOOSTS_MIN_PAGE,
                           BOOST_DEDUPE_LIMIT, id_key, id_str)
from .entities import lift


class StatusItem(GObject.Object):
    """One row's data: a status, transparent to boosts."""

    __gtype_name__ = 'BabbleStatusItem'
    # Emitted when the payload is swapped for an edited one; rows bound to
    # the item rebind, and the list keeps its row instead of rebuilding.
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, data):
        super().__init__()
        self._set_data(data)
        # Per-post view state that must survive row recycling: expanded
        # text, opened content warning, revealed filtered post.
        self.ui = {}
        # Result of POST /statuses/:id/translate while it is shown.
        self.translation = None

    def _set_data(self, data):
        # The entity types live on the data itself, so every reader
        # downstream of an item (rows, boosts, the carousel, Catch-Up)
        # is typed without having to lift again at each read. Lift is a
        # no-op on values it has already typed.
        data = lift(data) if isinstance(data, dict) else data
        boost = data.get('reblog') if isinstance(data, dict) else None
        self.raw = data
        self.status = boost if boost else data
        # The wrapper status is the boost, authored by the booster; the
        # reblog is the post that was boosted (docs.joinmastodon.org/
        # entities/Status), so the booster is the wrapper's account.
        self.boosted_by = data['account'] if boost else None

    def update(self, data):
        """Take *data*, a newer payload of the same post (an edit): the
        view state stays, a translation of the old text goes."""
        self._set_data(data)
        self.translation = None
        self.emit('changed')

    # What the model asks of any row, a plain post, a boost or the boosts
    # carousel (BoostCarouselItem overrides each), so it takes one path.

    def ids(self):
        """Every id the row answers to: the post it shows and, for a
        boost, the wrapper it arrived as."""
        ids = {self.status_id}
        if self.raw.get('id') is not None:
            ids.add(str(self.raw['id']))
        return ids

    def wrapper_ids(self):
        """Ids of the boost wrappers this row is."""
        return {str(self.raw['id'])} if self.is_boost else set()

    def carousel_wrappers(self):
        """The boost wrappers a carousel holds; none for a plain row."""
        return ()

    def page_ids(self):
        """The ids that bound paging when the row is the oldest left."""
        return [self.raw['id']]

    def matches(self, target):
        """True when *target* is the post shown or the id it arrived as."""
        return self.status_id == target \
            or id_str(self.raw.get('id')) == target

    def without(self, target):
        """Drop *target* from this row: 'remove' when nothing of it is
        left (the model removes the row), 'changed' when it was edited in
        place, 'keep' when *target* is not in it."""
        return 'remove' if self.matches(target) else 'keep'

    def replace_status(self, status):
        """Take *status*, an edited post, when it is the one shown.
        Returns True when the row updated itself."""
        if self.status_id != id_str(status.get('id')):
            return False
        if self.is_boost:
            # The row is a boost: the edit applies to the boosted
            # post, so keep the wrapper and the booster.
            raw = dict(self.raw)
            raw['reblog'] = dict(self.status, **status)
        else:
            raw = dict(self.raw, **status)
        self.update(raw)
        return True

    @GObject.Property(type=str)
    def status_id(self):
        return self.status['id']

    @GObject.Property(type=str)
    def display_name(self):
        return self.status['account'].get('display_name') \
            or self.status['account'].get('acct', '')

    @GObject.Property(type=str)
    def handle(self):
        return '@%s' % self.status['account'].get('acct', '')

    @GObject.Property(type=str)
    def short_handle(self):
        account = self.status['account'].get('acct', '')
        return '@%s' % account.split('@', 1)[0]

    @GObject.Property(type=str)
    def header_caption(self):
        """Handle shown on the row: hidden when the short name merely
        repeats the display name, like the reference client does."""
        name = self.display_name or '?'
        if name.strip().casefold() == self.short_handle[1:].casefold():
            return ''
        return self.short_handle

    @GObject.Property(type=str)
    def timestamp(self):
        return relative_time(self.status.get('created_at'))

    @GObject.Property(type=str)
    def boost_line(self):
        if self.boosted_by is None:
            return ''
        name = self.boosted_by.get('display_name') \
            or self.boosted_by.get('acct', '')
        return name

    @GObject.Property(type=bool, default=False)
    def is_boost(self):
        return self.boosted_by is not None

    @GObject.Property(type=str)
    def thread_id(self):
        """Conversation key: consecutive posts of the same thread by the
        same author form one run, the way the bar stacks them."""
        return '%s/%s' % (self.handle,
                          self.status.get('thread') or self.status_id)

    @GObject.Property(type=str)
    def content(self):
        return self.status.get('content', '')

    @GObject.Property(type=int, default=0)
    def reply_count(self):
        return self.status.get('replies_count') or 0

    @GObject.Property(type=int, default=0)
    def boost_count(self):
        return self.status.get('reblogs_count') or 0

    @GObject.Property(type=int, default=0)
    def favourite_count(self):
        return self.status.get('favourites_count') or 0

    @GObject.Property(type=bool, default=False)
    def reblogged(self):
        return bool(self.status.get('reblogged'))

    @GObject.Property(type=bool, default=False)
    def favourited(self):
        return bool(self.status.get('favourited'))

    @GObject.Property(type=bool, default=False)
    def bookmarked(self):
        return bool(self.status.get('bookmarked'))

    @GObject.Property(type=bool, default=False)
    def sensitive(self):
        return bool(self.status.get('sensitive'))

    @GObject.Property(type=GObject.TYPE_PYOBJECT)
    def media(self):
        return [a for a in self.status.get('media_attachments', [])
                if a.get('type') in ('image', 'video', 'gifv')]

    @GObject.Property(type=str)
    def status_url(self):
        return self.status.get('url') \
            or self.status.get('uri') or ''


def is_boost_status(status):
    """True for a reblog row that is not a group-account post."""
    if not status.get('reblog'):
        return False
    return not (status.get('account') or {}).get('group')


def matching_tag(status, names):
    """The first of *names* that *status* carries as a hashtag, or None.

    Names are compared case-insensitively, since a tag is stored with the
    casing its author typed. A boost is skipped: the tags on the wrapper
    belong to the boost, not to the post that arrived."""
    if not names or status.get('reblog'):
        return None
    present = {str((tag or {}).get('name') or '').lower()
               for tag in (status.get('tags') or ())}
    if not present:
        return None
    for name in names:
        if str(name).lower() in present:
            return name
    return None


def sort_carousel_boosts(boost_wrappers):
    """Inner statuses for carousel cards; filter warnings sort last."""
    unfiltered, filtered = [], []
    for wrapper in boost_wrappers:
        action, _title = status_filter(wrapper) or (None, None)
        if action and action != 'blur':
            filtered.append(wrapper)
        else:
            unfiltered.append(wrapper)
    ordered = unfiltered + filtered
    return [(wrapper.get('reblog') or wrapper) for wrapper in ordered]


def group_boosts(statuses):
    """Phanpy-style grouping for one API page of statuses.

    When the threshold is not met, returns *statuses* unchanged (boosts
    stay inline). Otherwise returns a list of status dicts and
    :class:`BoostCarouselItem` instances with boosts removed from the
    stream and the carousel at the middle or end of that page."""
    if not statuses:
        return []
    boost_stash = []
    new_values = []
    serial_boosts = 0
    for status in statuses:
        if is_boost_status(status):
            boost_stash.append(status)
            serial_boosts += 1
        else:
            new_values.append(status)
            if serial_boosts < 3:
                serial_boosts = 0
    if len(statuses) < GROUP_BOOSTS_MIN_PAGE:
        return statuses
    if not (len(boost_stash) > len(statuses) / 4 or serial_boosts >= 3):
        return statuses
    carousel = BoostCarouselItem(sort_carousel_boosts(boost_stash),
                                 boost_stash)
    if len(boost_stash) > len(statuses) * 3 / 4:
        return new_values + [carousel]
    half = len(new_values) // 2
    return new_values[:half] + [carousel] + new_values[half:]


class BoostCarouselItem(StatusItem):
    """Synthetic timeline row: a horizontal boosts carousel.

    Subclasses StatusItem so the shared GListModel item type contract
    (test: get_item_type() == StatusItem) stays intact."""

    __gtype_name__ = 'BabbleBoostCarouselItem'

    def __init__(self, boosts, wrappers=()):
        super().__init__({'id': '__babble_boosts__',
                          'content': '',
                          'account': {'acct': 'boosts'},
                          'created_at': ''})
        self.boosts = boosts
        self.wrappers = list(wrappers)

    def set_boosts(self, boosts):
        """Swap the boosted posts in place: the carousel stays one list
        item for the life of the timeline and its bound row follows
        ``changed``, instead of the list view rebuilding the row."""
        if len(self.boosts) == len(boosts) and all(
                a is b for a, b in zip(self.boosts, boosts, strict=True)):
            return
        self.boosts = boosts
        self.emit('changed')

    def _wrapper_id_list(self):
        return [str(wrapper['id']) for wrapper in self.wrappers
                if wrapper.get('id')]

    def ids(self):
        ids = {str(status['id']) for status in self.boosts}
        ids.update(self._wrapper_id_list())
        return ids

    def wrapper_ids(self):
        return set(self._wrapper_id_list())

    def carousel_wrappers(self):
        return self.wrappers

    def page_ids(self):
        return [wrapper['id'] for wrapper in self.wrappers
                if wrapper.get('id')]

    def matches(self, target):
        # A carousel is never taken whole, so Undo has nothing to put back.
        return False

    def without(self, target):
        """Drop *target*, a boosted post or the id of one of its boost
        wrappers (what an unboost deletes)."""
        wrappers = [
            wrapper for wrapper in self.wrappers
            if id_str(wrapper.get('id')) != target
            and id_str((wrapper.get('reblog') or {}).get('id')) != target]
        shown = {str(wrapper['reblog']['id']) for wrapper in wrappers}
        kept = [s for s in self.boosts if id_str(s.get('id')) in shown]
        if len(kept) == len(self.boosts) \
                and len(wrappers) == len(self.wrappers):
            return 'keep'
        self.wrappers = wrappers
        if not kept:
            return 'remove'
        self.set_boosts(kept)
        return 'changed'

    def replace_status(self, status):
        status_id = id_str(status.get('id'))
        updated = False
        boosts = []
        for inner in self.boosts:
            if id_str(inner.get('id')) == status_id:
                boosts.append(dict(inner, **status))
                updated = True
            else:
                boosts.append(inner)
        if updated:
            self.set_boosts(boosts)
        return updated


def filter_boosts(statuses):
    """Drop boosts: statuses that only wrap another post in ``reblog``.

    Returns a new list; *statuses* is left alone."""
    return [s for s in statuses if not s.get('reblog')]


def filter_replies_from_non_follows(statuses, viewer_id, following_ids):
    """Drop reply posts whose author the viewer does not follow.

    *following_ids* maps an author id to whether the viewer follows them.

    *viewer_id* is kept visible on their own replies. Non-replies pass
    through unchanged."""
    if not statuses:
        return statuses
    viewer = str(viewer_id) if viewer_id is not None else None
    # *following_ids* maps an author id to whether the viewer follows them.
    # A mapping rather than a set of followers because the timeline has to
    # remember "asked, and the answer is no" as well, or every page asks
    # again about every author the viewer does not follow.
    following = following_ids or {}
    kept = []
    for status in statuses:
        if not status.get('in_reply_to_id'):
            kept.append(status)
            continue
        author_id = id_str((status.get('account') or {}).get('id'))
        if viewer and author_id == viewer:
            kept.append(status)
        elif following.get(author_id):
            kept.append(status)
    return kept


class TimelineModel(GObject.Object, Gio.ListModel):
    """GListModel of StatusItem with prepend-new / append-old pagination."""

    __gtype_name__ = 'BabbleTimelineModel'

    def __init__(self):
        super().__init__()
        self._store = Gio.ListStore.new(StatusItem)
        self._seen = set()
        self._boosted_reblog_ids = {}
        self.group_boosts_enabled = True
        self.dedupe_boosts_enabled = True
        self.oldest_id = None
        self.newest_id = None
        self.exhausted = False
        self.loading = False
        # Re-emit the store's changes so GListModel consumers stay in sync.
        connect_weak(self._store, 'items-changed', self._on_store_changed)

    def _on_store_changed(self, _store, position, removed, added):
        self.items_changed(position, removed, added)

    def do_get_n_items(self):
        return self._store.get_n_items()

    def do_get_item(self, position):
        return self._store.get_item(position)

    def do_get_item_type(self):
        return StatusItem

    def _filter_unseen(self, statuses):
        fresh = []
        # Boosts taken from this page so far, so two boosts of one post on
        # the same page dedupe too, the first (newest) one winning.
        taken = {}
        for status in statuses:
            status_id = id_str(status.get('id'))
            if not status_id or status_id in self._seen:
                continue
            if self.dedupe_boosts_enabled and is_boost_status(status):
                reblog_id = str(status['reblog']['id'])
                booster = taken.get(
                    reblog_id, self._boosted_reblog_ids.get(reblog_id))
                if booster is not None and booster != status_id:
                    continue
                taken[reblog_id] = status_id
            fresh.append(status)
        return fresh

    def _remember_boost_dedupe(self, status):
        if not self.dedupe_boosts_enabled or not is_boost_status(status):
            return
        reblog_id = str(status['reblog']['id'])
        self._boosted_reblog_ids[reblog_id] = str(status['id'])
        while len(self._boosted_reblog_ids) > BOOST_DEDUPE_LIMIT:
            self._boosted_reblog_ids.pop(next(iter(self._boosted_reblog_ids)))

    @staticmethod
    def _wrapper_ids(items):
        """Ids of the boost wrappers among *items*."""
        return set().union(*(item.wrapper_ids() for item in items))

    def _register_seen(self, item):
        self._seen |= item.ids()
        for wrapper in item.carousel_wrappers():
            self._remember_boost_dedupe(wrapper)

    def _statuses_to_items(self, statuses):
        if not statuses:
            return []
        if self.group_boosts_enabled:
            shaped = group_boosts(statuses)
        else:
            shaped = statuses
        items = []
        for entry in shaped:
            if isinstance(entry, BoostCarouselItem):
                items.append(entry)
            else:
                self._remember_boost_dedupe(entry)
                items.append(StatusItem(entry))
        return items

    def insert_new(self, statuses):
        """Prepend newer statuses, oldest of the new page first."""
        fresh = self._filter_unseen(statuses)
        items = self._statuses_to_items(fresh)
        for item in reversed(items):
            self._store.insert(0, item)
            self._register_seen(item)
        if fresh:
            newest = max((s['id'] for s in statuses), key=id_key)
            if self.newest_id is None \
                    or id_key(newest) > id_key(self.newest_id):
                self.newest_id = newest
        return len(fresh)

    def append_old(self, statuses):
        """Append older statuses. Returns False when there is nothing
        further to page to: the page is empty, or it held nothing new and
        its cursor did not move back. A page whose rows were all filtered
        out or already seen still moves the cursor and returns True."""
        if not statuses:
            self.exhausted = True
            return False
        oldest = min((s['id'] for s in statuses), key=id_key)
        items = self._statuses_to_items(self._filter_unseen(statuses))
        if items:
            start = self._store.get_n_items()
            self._store.splice(start, 0, items)
            for item in items:
                self._register_seen(item)
        elif self.oldest_id is not None \
                and id_key(oldest) >= id_key(self.oldest_id):
            return False
        self.oldest_id = oldest
        return True

    def _forget_boost(self, status_id):
        """A deleted post or boost is no longer a reason to hide a later
        boost of the same post."""
        self._seen.discard(status_id)
        self._boosted_reblog_ids.pop(status_id, None)
        for reblog_id, booster in list(self._boosted_reblog_ids.items()):
            if booster == status_id:
                del self._boosted_reblog_ids[reblog_id]

    def _remove(self, target):
        """Drop *target* from every row. Returns ``(changed, taken)``:
        whether any row changed or went, and the rows that went because
        they are the post, as ``(position, item)`` in list order."""
        changed = False
        taken = []
        for position in reversed(range(self._store.get_n_items())):
            item = self._store.get_item(position)
            verdict = item.without(target)
            if verdict == 'keep':
                continue
            changed = True
            if verdict == 'remove':
                if item.matches(target):
                    taken.append((position, item))
                self._seen.discard(item.status_id)
                self._store.remove(position)
        taken.reverse()
        return changed, taken

    def remove_status(self, status_id):
        """Drop a deleted post, and any boost of it, from the list.
        Returns True when a row was removed."""
        target = id_str(status_id)
        changed, _taken = self._remove(target)
        if changed:
            self._forget_boost(target)
        return changed

    def take_status(self, status_id):
        """Like :meth:`remove_status`, but returns what plain rows were
        removed as ``(position, item)`` pairs, for :meth:`put_back`."""
        target = id_str(status_id)
        changed, taken = self._remove(target)
        if changed:
            self._forget_boost(target)
        return taken

    def put_back(self, taken):
        """Return rows from :meth:`take_status` to where they were."""
        for position, item in taken:
            if item.status_id in self._seen:
                continue
            self._store.insert(min(position, self._store.get_n_items()), item)
            self._register_seen(item)

    def trim(self, keep):
        """Drop every post after the first *keep* and page from the
        oldest one left, so scrolling down fetches the dropped ones
        again. Returns True when anything was dropped."""
        total = self._store.get_n_items()
        if total <= keep:
            return False
        dropped = [self._store.get_item(i) for i in range(keep, total)]
        self._store.splice(keep, total - keep, [])
        left = [self._store.get_item(i) for i in range(keep)]
        # A post can also be shown higher up as a boost or the original:
        # its id stays seen while any copy is left.
        gone = set().union(*(item.ids() for item in dropped))
        still = set().union(*(item.ids() for item in left))
        self._seen -= gone - still
        # The dropped boosts must be fetchable again, so they stop
        # counting as the boost of their post.
        orphaned = self._wrapper_ids(dropped) - self._wrapper_ids(left)
        for reblog_id, booster in list(self._boosted_reblog_ids.items()):
            if booster in orphaned:
                del self._boosted_reblog_ids[reblog_id]
        ids = [page_id for item in left for page_id in item.page_ids()]
        self.oldest_id = min(ids, key=id_key) if ids else None
        self.exhausted = False
        return True

    def replace_status(self, status):
        """Swap in an edited post, keeping the row's view state.
        Returns True when a row was found and updated."""
        if not id_str(status.get('id')):
            return False
        for position in range(self._store.get_n_items()):
            if self._store.get_item(position).replace_status(status):
                return True
        return False

    def apply_source_tags(self, hits, following):
        """Mark home posts that arrived through a followed hashtag.

        *hits* maps a status id to the followed tag its post carries;
        *following* maps an author id to whether the viewer follows them.
        A followed author's post is not provenance: it would have been in
        the timeline anyway, so it gets None. An author the server has not
        answered for counts as not followed, which marks the post rather
        than hiding it."""
        if not hits:
            return
        following = following or {}
        for position in range(self._store.get_n_items()):
            item = self._store.get_item(position)
            status = item.status
            status_id = id_str(status.get('id'))
            if status_id not in hits:
                continue
            author_id = id_str((status.get('account') or {}).get('id'))
            value = None if following.get(author_id) else hits[status_id]
            if item.ui.get('source_tag') == value:
                continue
            item.ui['source_tag'] = value
            item.emit('changed')

