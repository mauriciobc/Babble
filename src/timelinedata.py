# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')


from gi.repository import (GObject, Gio,  # noqa: E402
                           )

from .pages import connect_weak  # noqa: E402
from .statustext import (relative_time)

"""Status rows' data: StatusItem, boost grouping and TimelineModel."""
from .timelinebase import (status_filter, GROUP_BOOSTS_MIN_PAGE,
                           BOOST_DEDUPE_LIMIT, id_key)
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
        author_id = str((status.get('account') or {}).get('id') or '')
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
            status_id = str(status.get('id') or '')
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
    def _item_ids(item):
        """Every id a row answers to: the posts it shows and, for boosts,
        the wrappers they arrived as."""
        if isinstance(item, BoostCarouselItem):
            ids = {str(status['id']) for status in item.boosts}
            ids.update(str(wrapper['id']) for wrapper in item.wrappers
                       if wrapper.get('id'))
            return ids
        ids = {item.status_id}
        if item.raw.get('id') is not None:
            ids.add(str(item.raw['id']))
        return ids

    @staticmethod
    def _wrapper_ids(items):
        """Ids of the boost wrappers among *items*."""
        ids = set()
        for item in items:
            if isinstance(item, BoostCarouselItem):
                ids.update(str(wrapper['id']) for wrapper in item.wrappers
                           if wrapper.get('id'))
            elif item.is_boost:
                ids.add(str(item.raw['id']))
        return ids

    def _register_seen(self, item):
        self._seen |= self._item_ids(item)
        if isinstance(item, BoostCarouselItem):
            for wrapper in item.wrappers:
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

    def _remove_from_carousel(self, item, position, target):
        """Drop *target*, a boosted post or the id of one of its boost
        wrappers (what an unboost deletes), from a carousel. Returns True
        when the carousel changed."""
        if item.wrappers:
            wrappers = [
                wrapper for wrapper in item.wrappers
                if str(wrapper.get('id')) != target
                and str((wrapper.get('reblog') or {}).get('id')) != target]
            shown = {str(wrapper['reblog']['id']) for wrapper in wrappers}
            kept = [s for s in item.boosts if str(s.get('id')) in shown]
        else:
            wrappers = []
            kept = [s for s in item.boosts if str(s.get('id') or '') != target]
        if len(kept) == len(item.boosts) \
                and len(wrappers) == len(item.wrappers):
            return False
        item.wrappers = wrappers
        if kept:
            item.set_boosts(kept)
        else:
            self._store.remove(position)
        return True

    def remove_status(self, status_id):
        """Drop a deleted post, and any boost of it, from the list.
        Returns True when a row was removed."""
        target = str(status_id)
        removed = False
        for position in reversed(range(self._store.get_n_items())):
            item = self._store.get_item(position)
            if isinstance(item, BoostCarouselItem):
                if self._remove_from_carousel(item, position, target):
                    removed = True
                continue
            if item.status_id != target \
                    and str(item.raw.get('id') or '') != target:
                continue
            self._seen.discard(item.status_id)
            self._store.remove(position)
            removed = True
        if removed:
            self._forget_boost(target)
        return removed

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
        gone = set().union(*(self._item_ids(item) for item in dropped))
        still = set().union(*(self._item_ids(item) for item in left))
        self._seen -= gone - still
        # The dropped boosts must be fetchable again, so they stop
        # counting as the boost of their post.
        orphaned = self._wrapper_ids(dropped) - self._wrapper_ids(left)
        for reblog_id, booster in list(self._boosted_reblog_ids.items()):
            if booster in orphaned:
                del self._boosted_reblog_ids[reblog_id]
        ids = []
        for item in left:
            if isinstance(item, BoostCarouselItem):
                ids.extend(wrapper['id'] for wrapper in item.wrappers
                           if wrapper.get('id'))
            else:
                ids.append(item.raw['id'])
        self.oldest_id = min(ids, key=id_key) if ids else None
        self.exhausted = False
        return True

    def replace_status(self, status):
        """Swap in an edited post, keeping the row's view state.
        Returns True when a row was found and updated."""
        status_id = str(status.get('id') or '')
        if not status_id:
            return False
        for position in range(self._store.get_n_items()):
            item = self._store.get_item(position)
            if isinstance(item, BoostCarouselItem):
                updated = False
                boosts = []
                for inner in item.boosts:
                    if str(inner.get('id') or '') == status_id:
                        boosts.append(dict(inner, **status))
                        updated = True
                    else:
                        boosts.append(inner)
                if updated:
                    item.set_boosts(boosts)
                    return True
                continue
            if item.status_id != status_id:
                continue
            if item.is_boost:
                # The row is a boost: the edit applies to the boosted
                # post, so keep the wrapper and the booster.
                raw = dict(item.raw)
                raw['reblog'] = dict(item.status, **status)
            else:
                raw = dict(item.raw, **status)
            item.update(raw)
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
            status_id = str(status.get('id') or '')
            if status_id not in hits:
                continue
            author_id = str((status.get('account') or {}).get('id') or '')
            value = None if following.get(author_id) else hits[status_id]
            if item.ui.get('source_tag') == value:
                continue
            item.ui['source_tag'] = value
            item.emit('changed')

