# SPDX-License-Identifier: GPL-3.0-or-later

import collections
import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, Gdk, GdkPixbuf, GLib, GObject, Gio,  # noqa: E402
                           Graphene, Gtk, Pango, Soup)

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .mastodon import HTTP_TIMEOUT_SECONDS, summarize_error_markup  # noqa: E402
from .statustext import (is_hashtag, parse_content, relative_time,  # noqa: E402
                         shorten_url_text, split_url, url_host)
from .textemoji import (InlineEmojiText, emoji_bank, launch_uri,  # noqa: E402
                        poll_emoji_bank, status_emoji_bank)

PAGE_SIZE = 20
AUTO_REFRESH_SCROLLED_TICKS = 2  # 30s when the user is scrolled away
COLUMN_WIDTH = pages.COLUMN_WIDTH
BOOST_WINDOW = 60
BOOST_CAP = 12

# Decoded-texture budgets. Avatars repeat across a timeline so they are
# cheap to keep; media are large, so far fewer stay resident.
AVATAR_CACHE_SIZE = 256
# Instances serve 400px avatars; the largest one shown is 96px. Decoded
# at full size, a full avatar cache held ~156 MB.
AVATAR_DECODE_SIZE = 96
MEDIA_TEXTURE_CACHE_SIZE = 48
# Gtk.ListView keeps up to ~200 list items alive per page whatever the
# scroll position, and each unmapped one holds its built StatusRow cards.
# Only this many unmapped shells, across all pages, keep theirs; the
# least recently unmapped drop them and rebuild if scrolled back to.
CARD_SHELLS_KEPT = 100
# Posts a timeline keeps. Streaming and refreshes only ever prepend, so
# an open timeline grew for the whole session; past this many, the
# oldest are dropped while the reader is at the top, and infinite scroll
# fetches them again.
TIMELINE_MAX_ITEMS = 400


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
        boost = data.get('reblog')
        self.raw = data
        self.status = boost if boost else data
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


class BoostCarouselItem(StatusItem):
    """Synthetic first row of the timeline: the boosts carousel.

    Subclasses StatusItem so the shared GListModel item type contract
    (test: get_item_type() == StatusItem) stays intact."""

    __gtype_name__ = 'BabbleBoostCarouselItem'

    def __init__(self, boosts):
        super().__init__({'id': '__babble_boosts__',
                          'content': '',
                          'account': {'acct': 'boosts'},
                          'created_at': ''})
        self.boosts = boosts

    def set_boosts(self, boosts):
        """Swap the boosted posts in place: the carousel stays one list
        item for the life of the timeline and its bound row follows
        ``changed``, instead of the list view rebuilding the row."""
        if len(self.boosts) == len(boosts) and all(
                a is b for a, b in zip(self.boosts, boosts, strict=True)):
            return
        self.boosts = boosts
        self.emit('changed')


class TimelineModel(GObject.Object, Gio.ListModel):
    """GListModel of StatusItem with prepend-new / append-old pagination."""

    __gtype_name__ = 'BabbleTimelineModel'

    def __init__(self):
        super().__init__()
        self._store = Gio.ListStore.new(StatusItem)
        self._seen = set()
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

    def insert_new(self, statuses):
        """Prepend newer statuses, oldest of the new page first."""
        fresh = [StatusItem(s) for s in statuses
                 if s['id'] not in self._seen]
        added = 0
        offset = 0
        if self._store.get_n_items() \
                and isinstance(self._store.get_item(0), BoostCarouselItem):
            offset = 1
        for item in reversed(fresh):
            self._store.insert(offset, item)
            self._seen.add(item.status_id)
            added += 1
        if fresh:
            newest = max(s['id'] for s in statuses)
            if self.newest_id is None or newest > self.newest_id:
                self.newest_id = newest
        return added

    def append_old(self, statuses):
        """Append older statuses; returns False when the page is empty."""
        if not statuses:
            self.exhausted = True
            return False
        fresh = [StatusItem(s) for s in statuses if s['id'] not in self._seen]
        if not fresh:
            return False
        start = self._store.get_n_items()
        self._store.splice(start, 0, fresh)
        for item in fresh:
            self._seen.add(item.status_id)
        self.oldest_id = min(s['id'] for s in statuses)
        return True

    def remove_status(self, status_id):
        """Drop a deleted post, and any boost of it, from the list.
        Returns True when a row was removed."""
        target = str(status_id)
        removed = False
        for position in reversed(range(self._store.get_n_items())):
            item = self._store.get_item(position)
            if isinstance(item, BoostCarouselItem):
                continue
            if item.status_id != target \
                    and str(item.raw.get('id') or '') != target:
                continue
            self._seen.discard(item.status_id)
            self._store.remove(position)
            removed = True
        return removed

    def trim(self, keep):
        """Drop every post after the first *keep* and page from the
        oldest one left, so scrolling down fetches the dropped ones
        again. Returns True when anything was dropped."""
        total = self._store.get_n_items()
        if total <= keep:
            return False
        dropped = {self._store.get_item(i).status_id
                   for i in range(keep, total)}
        self._store.splice(keep, total - keep, [])
        left = [self._store.get_item(i) for i in range(keep)]
        # A post can also be shown higher up as a boost or the original:
        # its id stays seen while any copy is left.
        self._seen -= dropped - {item.status_id for item in left}
        ids = [item.raw['id'] for item in left
               if not isinstance(item, BoostCarouselItem)]
        self.oldest_id = min(ids) if ids else None
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

    def set_boosts(self, boosts):
        """Keep the boosts carousel as the first row (None removes it)."""
        if not self._store.get_n_items():
            return
        head = self._store.get_item(0)
        has = isinstance(head, BoostCarouselItem)
        if not boosts:
            if has:
                self._store.remove(0)
            return
        if has:
            head.set_boosts(boosts)
        else:
            self._store.insert(0, BoostCarouselItem(boosts))


class StatusRunRow(Gtk.Box):
    """Card for one run: the author line, then every post of the run with
    its own body, media and 'n/total' badge inside the same card."""

    # Replaced with the height of posts that have actually been shown.
    _typical_height = 64
    # Unmapped shells that still hold cards, least recently unmapped
    # first. GObject weak refs, so the LRU never keeps a shell (and its
    # page) alive; keyed by a per-shell counter, not the object.
    _kept = collections.OrderedDict()
    _next_key = 0

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
        self.set_size_request(-1, type(self)._typical_height)
        self._detach()
        if self.get_mapped():
            self._populate()

    def _on_map(self, _widget):
        type(self)._kept.pop(self._key, None)
        if self._run is not None:
            self._populate()

    def _on_unmap(self, _widget):
        # Keep the height the list just measured, or dropping the cards
        # makes the scrollbar jump and every row below this one relayout.
        height = self.get_height()
        kind = type(self)
        if height > 64:
            # This shell keeps the height it just had, and the next
            # off-screen shells start from the posts already shown.
            self.set_size_request(-1, height)
            if kind._typical_height <= 64:
                kind._typical_height = height
            else:
                kind._typical_height = (kind._typical_height * 3 + height) // 4
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

    def _populate(self):
        run = self._run
        if run is None or self.rows:
            return
        # The placeholder height is for an empty shell only. As a
        # minimum it would outlive the cards it stood in for and leave a
        # short post at the top of a tall slot.
        self.set_size_request(-1, -1)
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
                row = StatusRow()
                row.bind(item, self._client)
                row.set_vexpand(False)
                self._cards[item] = row
            if multi:
                if position:
                    # Continuation posts keep only the thread badge: the
                    # author line belongs to the run card above them.
                    row.add_css_class('run-post')
                    row.profile_button.set_visible(False)
                    row.time_label.set_visible(False)
                    row.actions.set_visible(False)
                    row._overflow.set_visible(False)
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


class AvatarCache:
    """Shares avatar textures across rows; one Soup session, one HTTP hit."""

    # Bounded like the media viewer's: a long session on a big timeline
    # meets hundreds of avatars, and decoded textures are expensive to
    # keep. Least recently used goes first.
    _textures = collections.OrderedDict()
    _session = None
    _inflight = {}

    @classmethod
    def session(cls):
        if cls._session is None:
            cls._session = Soup.Session()
            cls._session.set_timeout(HTTP_TIMEOUT_SECONDS)
        return cls._session

    @classmethod
    def _remember(cls, url, texture):
        cls._textures[url] = texture
        cls._textures.move_to_end(url)
        while len(cls._textures) > AVATAR_CACHE_SIZE:
            cls._textures.popitem(last=False)

    @classmethod
    def load(cls, avatar, account):
        """Fill *avatar* with the cached or freshly fetched image."""
        url = account.get('avatar') or account.get('avatar_static') or ''
        avatar.set_custom_image(None)
        if not url:
            return
        if url in cls._textures:
            cls._textures.move_to_end(url)
            avatar.set_custom_image(cls._textures[url])
            return
        if url in cls._inflight:
            cls._inflight[url].append(avatar)
            return
        cls._inflight[url] = [avatar]
        message = Soup.Message.new('GET', url)
        if message is None:
            cls._inflight.pop(url, None)
            log_media_failure(url, 'invalid URL')
            return
        cls.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT_IDLE, None, cls._on_loaded,
            (url, message))

    @classmethod
    def _on_loaded(cls, session, result, extra):
        # A failure leaves the avatars on their initials, the avatar's
        # own placeholder, and uncached so a later row retries.
        url, message = extra
        waiting = cls._inflight.pop(url, [])
        try:
            data = session.send_and_read_finish(result)
        except GLib.Error as error:
            log_media_failure(url, error.message)
            return
        status = message.get_status()
        if not 200 <= status < 300:
            # A 404 page can be an image too; never show or cache it.
            log_media_failure(url, 'HTTP %d' % status)
            return
        if not data or data.get_size() == 0:
            log_media_failure(url, 'empty response')
            return
        texture = _decode_covering(data, (AVATAR_DECODE_SIZE,) * 2)
        if texture is None:
            try:
                texture = Gdk.Texture.new_from_bytes(data)
            except GLib.Error as error:
                log_media_failure(url, error.message)
                return
        cls._remember(url, texture)
        for avatar in waiting:
            avatar.set_custom_image(texture)


class StatusBody(InlineEmojiText):
    """Post body through the shared emoji/text machinery: :shortcode:
    custom emojis declared in the status payload render as inline
    Gtk.Images, links stay underlined and keyboard reachable, URLs keep
    their compact form and paragraph blank-line flow is preserved.
    The raw string (shortcodes intact) remains the accessible label."""

    def __init__(self):
        super().__init__(emoji_size=20, shorten_links=True,
                         paragraph_gap=True)
        self.add_css_class('body')
        self.add_css_class('status-content')


def _static_content_labels(segments, limit=3):
    """Summary string for collapsed hashtag-stuffed posts."""
    parts = []
    for segment in segments:
        if segment['text'] == '\n':
            continue
        text = segment['text']
        if segment['href']:
            text = shorten_url_text(text)
        parts.append(text)
        if len(parts) >= limit:
            break
    if not parts:
        return ''
    return ' '.join(parts)


def _should_collapse(segments):
    """True for posts made almost only of hashtags/links."""
    plain = [s for s in segments
             if s['text'] != '\n' and s['href'] is None]
    tagged = [s for s in segments if s['href'] is not None]
    hashtags = [s for s in tagged if is_hashtag(s)]
    return not plain and len(tagged) >= 4 or len(hashtags) >= 6


def _display_scale():
    """Largest scale of any monitor: a decoded image must stay sharp on
    whichever one the window ends up on."""
    display = Gdk.Display.get_default()
    monitors = display.get_monitors() if display is not None else None
    scale = 1.0
    for index in range(monitors.get_n_items() if monitors else 0):
        monitor = monitors.get_item(index)
        scale = max(scale, monitor.get_scale())
    return scale


def _decode_covering(data, cover):
    """Texture of the image in *data*, decoded no larger than it takes to
    cover a *cover* (width, height) box of logical pixels. None when
    GdkPixbuf cannot read it."""
    scale = _display_scale()
    box_width, box_height = cover[0] * scale, cover[1] * scale

    def prepared(loader, width, height):
        factor = max(box_width / width, box_height / height)
        if factor < 1:
            loader.set_size(max(1, round(width * factor)),
                            max(1, round(height * factor)))

    loader = GdkPixbuf.PixbufLoader()
    loader.connect('size-prepared', prepared)
    try:
        loader.write_bytes(data)
        loader.close()
    except GLib.Error:
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    if pixbuf.get_has_alpha():
        memory_format = Gdk.MemoryFormat.R8G8B8A8
    else:
        memory_format = Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(),
                                 memory_format, pixbuf.read_pixel_bytes(),
                                 pixbuf.get_rowstride())


class _BrokenImage(GObject.Object, Gdk.Paintable, Gtk.SymbolicPaintable):
    """Stand-in for an image that failed to load: image-missing centred
    at its natural size, whatever the picture's size or content fit. It
    has no intrinsic size, so the picture keeps the box it was given,
    and as a symbolic paintable it takes the picture's colours."""

    ICON_SIZE = 32
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def do_get_flags(self):
        return (Gdk.PaintableFlags.STATIC_SIZE
                | Gdk.PaintableFlags.STATIC_CONTENTS)

    def do_snapshot(self, snapshot, width, height):
        self.do_snapshot_symbolic(snapshot, width, height, [])

    def do_snapshot_symbolic(self, snapshot, width, height, colors, *_n):
        display = Gdk.Display.get_default()
        if display is None:
            return
        icon = Gtk.IconTheme.get_for_display(display).lookup_icon(
            'image-missing-symbolic', None, self.ICON_SIZE, 1,
            Gtk.TextDirection.NONE, 0)
        size = min(self.ICON_SIZE, width, height)
        snapshot.save()
        snapshot.translate(Graphene.Point().init((width - size) / 2,
                                                 (height - size) / 2))
        icon.snapshot_symbolic(snapshot, size, size, colors)
        snapshot.restore()


def log_media_failure(url, reason):
    # Shown with G_MESSAGES_DEBUG=Babble: a host outage or a codec gap
    # otherwise only surfaces as placeholders.
    GLib.log_variant('Babble', GLib.LogLevelFlags.LEVEL_DEBUG,
                     GLib.Variant('a{sv}', {'MESSAGE': GLib.Variant(
                         's', 'Media %s failed: %s' % (url, reason))}))


def _show_broken(picture, url=None, reason=None):
    if reason is not None:
        log_media_failure(url, reason)
    picture.set_paintable(_BrokenImage.get())
    picture.add_css_class('broken-image')


class _TextureCache:
    """Decoded media textures shared between rows by URL."""

    # Bounded LRU: scrolling a media-heavy timeline would otherwise keep
    # every attachment ever decoded alive for the life of the process.
    _textures = collections.OrderedDict()
    _session = None

    @classmethod
    def session(cls):
        if cls._session is None:
            cls._session = Soup.Session()
            cls._session.set_timeout(HTTP_TIMEOUT_SECONDS)
        return cls._session

    @classmethod
    def load(cls, url, picture, cover=None):
        """Show *url* in *picture*. With *cover* (width, height), the
        largest box in logical pixels the picture fills, the image is
        decoded only as large as that box needs: an original attachment
        can be tens of megapixels, its thumbnail a few hundred."""
        key = url if cover is None else (url, cover)
        if key in cls._textures:
            cls._textures.move_to_end(key)
            picture.remove_css_class('broken-image')
            picture.set_paintable(cls._textures[key])
            return
        message = Soup.Message.new('GET', url)
        if message is None:
            _show_broken(picture, url, 'invalid URL')
            return
        # A rebuilt row replaces the request it already had in flight;
        # cancelling the old one stops the bytes for a thumbnail nobody
        # will display any more.
        previous = getattr(picture, '_babble_cancel', None)
        if previous is not None:
            previous.cancel()
        cancellable = Gio.Cancellable.new()
        picture._babble_cancel = cancellable
        cls.session().send_and_read_async(
            message, GLib.PRIORITY_DEFAULT, cancellable, cls._on_loaded,
            (picture, key, cover, cancellable, message, url))

    @classmethod
    def _on_loaded(cls, session, result, extra):
        picture, key, cover, cancellable, message, url = extra
        # Check the cancellable this request was issued with, not
        # whichever one the picture happens to hold now.
        if cancellable.is_cancelled():
            return
        try:
            data = session.send_and_read_finish(result)
        except GLib.Error as error:
            # Also how a cancel the check above missed would surface.
            if not cancellable.is_cancelled():
                _show_broken(picture, url, error.message)
            return
        status = message.get_status()
        if not 200 <= status < 300:
            # A 404 page can be an image too; never show or cache it.
            _show_broken(picture, url, 'HTTP %d' % status)
            return
        if data is None or data.get_size() == 0:
            _show_broken(picture, url, 'empty response')
            return
        texture = _decode_covering(data, cover) if cover else None
        if texture is None:
            try:
                texture = Gdk.Texture.new_from_bytes(data)
            except GLib.Error as error:
                _show_broken(picture, url, error.message)
                return
        cls._textures[key] = texture
        cls._textures.move_to_end(key)
        while len(cls._textures) > MEDIA_TEXTURE_CACHE_SIZE:
            cls._textures.popitem(last=False)
        picture.remove_css_class('broken-image')
        if picture.get_paintable() is None \
                or picture.get_paintable() is not texture:
            picture.set_paintable(texture)


class PollBox(Gtk.Box):
    """Renders status.poll. An open poll the reader has not answered is
    a vote form (radio buttons, or check buttons for a multiple-choice
    poll, plus Vote); an expired or answered one shows the result bars
    with the reader's own choices marked. ``set_poll`` takes the client
    and the post's view state ``ui`` so a vote or a refresh can update
    the post: ``on_update(new_poll)`` stores the server's answer."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('status-poll')
        self._poll = None
        self._bank = {}
        self._client = None
        self._ui = {}
        self._on_update = None
        self._choices = []
        self._animate_bars = False
        self._bar_animations = []

    def set_poll(self, poll, bank=None, client=None, ui=None,
                 on_update=None):
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        self._bar_animations = []
        self._poll = poll
        if not poll:
            self._animate_bars = False
            self.set_visible(False)
            return
        self.set_visible(True)
        # A poll declares its own emoji for the option titles; they win
        # over the post's bank, which only covers the body.
        self._bank = dict(bank or {})
        self._bank.update(poll_emoji_bank(poll))
        self._client = client
        self._ui = ui if ui is not None else {}
        self._on_update = on_update
        self._choices = []
        closed = bool(poll.get('expired') or poll.get('closed'))
        can_vote = (client is not None and bool(getattr(client, 'token', None))
                    and not closed and not poll.get('voted')
                    and not self._ui.get('poll_results'))
        if can_vote:
            self._build_form(poll)
        else:
            self._build_results(poll, closed)
        self._build_footer(poll, closed, can_vote)
        for animation in self._bar_animations:
            animation.play()
        self._animate_bars = False

    def _label(self, option):
        text = InlineEmojiText(emoji_size=15)
        text.set_segments([{'text': option.get('title') or '', 'href': None}],
                          self._bank)
        text.set_hexpand(True)
        return text

    def _build_form(self, poll):
        multiple = bool(poll.get('multiple'))
        first = None
        for index, option in enumerate(poll.get('options') or []):
            button = Gtk.CheckButton()
            if not multiple:
                if first is None:
                    first = button
                else:
                    button.set_group(first)
            button.set_child(self._label(option))
            connect_weak(button, 'toggled', self._on_choice_toggled)
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [option.get('title') or ''])
            button.babble_index = index
            self._choices.append(button)
            self.append(button)

    def _selected(self):
        return [b.babble_index for b in self._choices if b.get_active()]

    def _on_choice_toggled(self, _button):
        self.vote_button.set_sensitive(bool(self._selected()))

    def _build_results(self, poll, closed):
        options = poll.get('options') or []
        own = set(poll.get('own_votes') or [])
        total = poll.get('votes_count') or 0
        counts = [(o.get('votes_count') or 0) for o in options]
        winner = counts.index(max(counts)) \
            if closed and counts and max(counts) > 0 else -1
        for index, option in enumerate(options):
            count = option.get('votes_count') or 0
            share = (count / total) if total else 0.0
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line = Gtk.Box(spacing=8)
            if index in own:
                mark = Gtk.Image(icon_name='object-select-symbolic',
                                 accessible_role=Gtk.AccessibleRole.IMG)
                mark.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_('Your vote')])
                mark.set_tooltip_text(_('Your vote'))
                line.append(mark)
            label = self._label(option)
            if index == winner:
                label.add_css_class('heading')
            line.append(label)
            percent = Gtk.Label(label='%d%%' % round(share * 100))
            percent.add_css_class('numeric')
            percent.add_css_class('caption')
            line.append(percent)
            row.append(line)
            bar = Gtk.ProgressBar(
                fraction=0.0 if self._animate_bars else share)
            if self._animate_bars:
                self._bar_animations.append(Adw.TimedAnimation(
                    widget=bar, value_from=0.0, value_to=share,
                    duration=400, easing=Adw.Easing.EASE_OUT_CUBIC,
                    target=Adw.PropertyAnimationTarget.new(bar, 'fraction')))
            bar.add_css_class('poll-bar')
            if index in own:
                bar.add_css_class('accent')
            bar.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ['%s, %d%%' % (option.get('title') or '', round(share * 100))])
            row.append(bar)
            self.append(row)

    def _build_footer(self, poll, closed, can_vote):
        footer = Gtk.Box(spacing=8)
        if can_vote:
            self.vote_button = Gtk.Button(label=_('_Vote'),
                                          use_underline=True)
            self.vote_button.add_css_class('suggested-action')
            self.vote_button.add_css_class('pill')
            self.vote_button.set_sensitive(False)
            connect_weak(self.vote_button, 'clicked', self._on_vote)
            footer.append(self.vote_button)
            show = Gtk.Button(label=_('Show _Results'), use_underline=True)
            show.add_css_class('flat')
            connect_weak(show, 'clicked', self._on_show_results)
            footer.append(show)
        elif not closed and not poll.get('voted') and self._ui.get(
                'poll_results'):
            back = Gtk.Button(label=_('_Back to Voting'), use_underline=True)
            back.add_css_class('flat')
            connect_weak(back, 'clicked', self._on_show_results)
            footer.append(back)
        votes = poll.get('votes_count') or 0
        voters = poll.get('voters_count')
        count = voters if voters is not None else votes
        detail = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        detail.add_css_class('caption')
        detail.add_css_class('dimmed')
        if voters is not None:
            parts = [ngettext('%d voter', '%d voters', count) % count]
        else:
            parts = [ngettext('%d vote', '%d votes', votes) % votes]
        if closed:
            parts.append(_('Closed'))
        else:
            left = time_left(poll.get('expires_at'))
            parts.append(_('%s left') % left if left else _('Open'))
        detail.set_text(' · '.join(parts))
        footer.append(detail)
        if not closed:
            refresh = pages.icon_button('view-refresh-symbolic',
                                        _('Refresh Results'))
            connect_weak(refresh, 'clicked', self._on_refresh)
            footer.append(refresh)
        self.append(footer)

    def _on_show_results(self, _button):
        self._ui['poll_results'] = not self._ui.get('poll_results')
        self._animate_bars = True
        self.set_poll(self._poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _apply(self, poll):
        if self._on_update is not None:
            self._on_update(poll)
        self._animate_bars = True
        self.set_poll(poll, self._bank, self._client, self._ui,
                      self._on_update)

    def _on_vote(self, _button):
        choices = self._selected()
        if not choices or self._poll is None:
            return
        self.vote_button.set_sensitive(False)
        pages.call(self, self._client, 'POST',
                   '/polls/%s/votes' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Vote failed'),
                   json_body={'choices': choices})
        # A failed vote leaves the form usable again.
        GLib.timeout_add_seconds(
            3, lambda: self.vote_button.set_sensitive(
                bool(self._selected())) or GLib.SOURCE_REMOVE)

    def _on_refresh(self, _button):
        if self._poll is None:
            return
        pages.call(self, self._client, 'GET', '/polls/%s' % self._poll['id'],
                   on_done=self._apply, error_heading=_('Refresh failed'))


CARD_THUMB_W = 200
CARD_THUMB_H = 96
BOOST_CARD_WIDTH = 272


class CardPreview(Gtk.Button):
    """Link/quote preview card built from status.card, Phanpy-style:
    portrait-cropped thumbnail on the LEFT (~40% of the card), text block
    (title / description / muted domain) on the right."""

    def __init__(self, card):
        super().__init__(halign=Gtk.Align.FILL)
        self.add_css_class('card')
        self.add_css_class('status-card')
        self._href = card.get('url') or card.get('uri') or ''
        title = Gtk.Label(label=card.get('title') or '', xalign=0.0,
                          wrap=True, lines=2,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class('heading')
        description = Gtk.Label(label=card.get('description') or '',
                                xalign=0.0, wrap=True, lines=2,
                                ellipsize=Pango.EllipsizeMode.END)
        description.add_css_class('caption')
        description.add_css_class('dimmed')
        parts = split_url(self._href)
        info = Gtk.Label(label=url_host(parts) if parts else '', xalign=0.0)
        info.add_css_class('caption')
        info.add_css_class('dimmed')
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       hexpand=True, valign=Gtk.Align.CENTER)
        text.append(title)
        text.append(description)
        text.append(info)
        content = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER,
                          margin_top=8, margin_bottom=8, margin_start=8,
                          margin_end=8)
        if card.get('image'):
            image = Gtk.Picture(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            image.add_css_class('status-media')
            image.add_css_class('darkenable')
            image.set_content_fit(Gtk.ContentFit.COVER)
            image.set_can_shrink(True)
            # Square at minimum, CARD_THUMB_W wide when there is room.
            image.set_size_request(CARD_THUMB_H, CARD_THUMB_H)
            _TextureCache.load(card['image'], image,
                               (CARD_THUMB_W, CARD_THUMB_W))
            thumb = Adw.Clamp(maximum_size=CARD_THUMB_W,
                              tightening_threshold=CARD_THUMB_W,
                              child=image)
            thumb.set_valign(Gtk.Align.CENTER)
            content.append(thumb)
        content.append(text)
        self.set_child(content)
        if self._href:
            self.connect('clicked', lambda _b: launch_uri(self, self._href))
        else:
            self.set_sensitive(False)


class _BoostCard(Gtk.Button):
    """One numbered boost in the carousel; opens the boosted post."""

    def __init__(self, number, status, client):
        super().__init__(width_request=BOOST_CARD_WIDTH)
        self.add_css_class('card')
        self.add_css_class('boost-card')
        self.status = status
        self.client = client
        account = status['account']
        author = account.get('display_name') or account.get('acct', '')
        media = [a for a in status.get('media_attachments', [])
                 if a.get('type') in ('image', 'gifv')]
        if media:
            self.add_css_class('has-media')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=8,
                      margin_end=8)
        self.set_child(box)

        header = Gtk.Box(spacing=6)
        self._badge = Gtk.Label(valign=Gtk.Align.CENTER)
        self._badge.add_css_class('boost-num')
        self._badge.add_css_class('caption-heading')
        self._badge.add_css_class('numeric')
        header.append(self._badge)
        avatar = Adw.Avatar(size=24, show_initials=True, text=author or '?')
        AvatarCache.load(avatar, account)
        header.append(avatar)
        name = Gtk.Label(label=author, xalign=0.0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class('caption-heading')
        header.append(name)
        box.append(header)

        texts = []
        for segment in parse_content(status.get('content', '')):
            if segment['text'] == '\n':
                continue
            texts.append(shorten_url_text(segment['text'])
                         if segment['href'] else segment['text'])
            if len(texts) >= 3:
                break
        summary = ' '.join(texts) or '…'
        label = Gtk.Label(label=summary, xalign=0.0, wrap=True, lines=3,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          ellipsize=Pango.EllipsizeMode.END)
        box.append(label)
        self._author = author
        self._summary = summary
        self.set_number(number)

        if media:
            picture = Gtk.Picture(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(True)
            picture.set_size_request(-1, 96)
            picture.add_css_class('status-media')
            picture.add_css_class('darkenable')
            url = (media[0].get('url') if media[0].get('type') == 'image'
                   else media[0].get('preview_url')) \
                or media[0].get('preview_url') or media[0].get('url')
            if url:
                _TextureCache.load(url, picture, (BOOST_CARD_WIDTH, 96))
            box.append(picture)
        self.connect('clicked', self._on_clicked)

    def set_number(self, number):
        self._badge.set_label(str(number))
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ['%d. %s: %s' % (number, self._author,
                                              self._summary)])

    def _on_clicked(self, _button):
        view = getattr(self.get_root(), 'navigation_view', None)
        if view is not None:
            from .thread import push_thread
            push_thread(view, self.client, StatusItem(self.status))


class BoostCarousel(Gtk.Box):
    """Phanpy-style boosts section: a horizontally scrolling strip of
    numbered boost cards with previous and next buttons."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('boosts-carousel')

        header = Gtk.Box(spacing=6)
        self.title = Gtk.Label(xalign=0.0, hexpand=True)
        self.title.add_css_class('heading')
        header.append(self.title)
        self.prev = self._arrow('go-previous-symbolic',
                                _('Show Previous Boosts'), -1)
        self.next = self._arrow('go-next-symbolic',
                                _('Show Next Boosts'), 1)
        header.append(self.prev)
        header.append(self.next)
        self.append(header)

        self.cards_box = Gtk.Box(spacing=8)
        self.scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_child(self.cards_box)
        adjustment = self.scroll.get_hadjustment()
        connect_weak(adjustment, 'changed', self._update_arrows)
        connect_weak(adjustment, 'value-changed', self._update_arrows)
        self.append(self.scroll)

    def _arrow(self, icon_name, tooltip, direction):
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        button.add_css_class('flat')
        button.add_css_class('circular')
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        connect_weak(button, 'clicked', self._page_by, direction)
        return button

    def set_boosts(self, statuses, client=None):
        # Cards are kept for posts still in the strip and only renumbered:
        # a new boost shifts every number but changes no other card.
        current = {}
        child = self.cards_box.get_first_child()
        while child is not None:
            current[id(child.status)] = child
            child = child.get_next_sibling()
        cards = []
        seen = set()
        for status in statuses:
            key = status.get('id')
            if key in seen:
                continue
            seen.add(key)
            card = current.pop(id(status), None)
            if card is None or card.client is not client:
                card = _BoostCard(len(seen), status, client)
            else:
                card.set_number(len(seen))
            cards.append(card)
        for stale in current.values():
            self.cards_box.remove(stale)
        previous = None
        for card in cards:
            if card.get_parent() is None:
                self.cards_box.insert_child_after(card, previous)
            else:
                self.cards_box.reorder_child_after(card, previous)
            previous = card
        self.set_visible(bool(seen))
        self.title.set_text(
            ngettext('%d Boost', '%d Boosts', len(seen)) % len(seen))
        self._update_arrows()

    def _page_by(self, _button, direction):
        adjustment = self.scroll.get_hadjustment()
        value = adjustment.get_value() \
            + direction * adjustment.get_page_increment()
        adjustment.set_value(max(0, min(
            value, adjustment.get_upper() - adjustment.get_page_size())))

    def _update_arrows(self, *_args):
        adjustment = self.scroll.get_hadjustment()
        self.prev.set_sensitive(adjustment.get_value() > 0.5)
        self.next.set_sensitive(
            adjustment.get_value()
            < adjustment.get_upper() - adjustment.get_page_size() - 0.5)




def _media_badge(text, halign):
    badge = Gtk.Label(label=text, halign=halign, valign=Gtk.Align.END,
                      margin_bottom=6, margin_start=6, margin_end=6,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    badge.add_css_class('osd')
    badge.add_css_class('caption-heading')
    badge.add_css_class('media-badge')
    return badge


MEDIA_KINDS = {'image': _('Image'), 'video': _('Video'),
               'gifv': _('Animated Image'), 'audio': _('Audio')}


class BlurredPicture(Gtk.Picture):
    """A Gtk.Picture blurred at snapshot time: server filters set to
    'blur' reveal the shape of the media without its content, while
    the filter badge above it names what matched."""

    def __init__(self, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG,
                         **kwargs)

    def do_snapshot(self, snapshot):
        snapshot.push_blur(10.0)
        Gtk.Picture.do_snapshot(self, snapshot)
        snapshot.pop()


class MediaBox(Gtk.Box):
    """Adaptive attachment grid: 1 full / 2 side-by-side / 3-4 in a 2x2
    grid, with ALT and GIF badges. Clicking a tile opens the media
    viewer at that attachment. Sensitive media stays covered until the
    reader reveals it (or ``show-sensitive-media`` is on); ``ui`` keeps
    that choice across row recycling. Audio and unknown attachments are
    listed as rows."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('status-media-box')
        self._attachments = []

    def set_media(self, attachments, sensitive, ui=None, blur=False):
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        ui = ui if ui is not None else {}
        self._attachments = list(attachments or [])
        if not self._attachments:
            self.set_visible(False)
            return
        self.set_visible(True)
        visual = [(i, a) for i, a in enumerate(self._attachments)
                  if a.get('type') in ('image', 'video', 'gifv')][:4]
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if sensitive:
            covered = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                child=content)
            self.append(covered)
        else:
            self.append(content)
        if visual:
            content.append(self._build_grid(visual, blur))
        for index, attachment in enumerate(self._attachments):
            if attachment.get('type') not in ('image', 'video', 'gifv'):
                content.append(self._file_row(index, attachment))
        if sensitive and not _flag('show-sensitive-media') \
                and not ui.get('media_revealed'):
            pages.reveal(covered, False)
            reveal = Gtk.Button(label=_('Show Sensitive Content'),
                                halign=Gtk.Align.START)
            connect_weak(reveal, 'clicked', self._toggle_sensitive,
                         covered, ui)
            self.append(reveal)
        elif sensitive:
            pages.reveal(covered, True)
            hide = Gtk.Button(label=_('Hide Sensitive Content'),
                              halign=Gtk.Align.START)
            hide.add_css_class('flat')
            connect_weak(hide, 'clicked', self._toggle_sensitive,
                         covered, ui)
            self.append(hide)

    def _build_grid(self, visual, blur=False):
        count = len(visual)
        if count == 1:
            columns, tile_height = 1, 260
        elif count == 2:
            columns, tile_height = 2, 160
        else:
            columns, tile_height = 2, 130
        fit = Gtk.ContentFit.CONTAIN if _flag('letterbox-media') \
            else Gtk.ContentFit.COVER
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        box.append(grid)
        footnotes = []
        for slot, (index, attachment) in enumerate(visual):
            kind = attachment.get('type')
            description = attachment.get('description')
            caption = description or MEDIA_KINDS.get(kind, _('Image'))
            picture = BlurredPicture(alternative_text=caption) if blur \
                else Gtk.Picture(alternative_text=caption)
            picture.set_content_fit(fit)
            picture.set_can_shrink(True)
            picture.set_hexpand(True)
            picture.set_size_request(-1, tile_height)
            picture.add_css_class('status-media')
            picture.add_css_class('darkenable')
            overlay = Gtk.Overlay()
            overlay.set_child(picture)
            if description:
                mark = '¹²³⁴'[slot]
                overlay.add_overlay(
                    _media_badge('ALT%s' % mark, Gtk.Align.START))
                footnotes.append('%s %s' % (mark, description))
            if kind == 'gifv':
                overlay.add_overlay(_media_badge('GIF', Gtk.Align.END))
            elif kind == 'video':
                play = Gtk.Image(
                    icon_name='media-playback-start-symbolic',
                    pixel_size=24, halign=Gtk.Align.CENTER,
                    valign=Gtk.Align.CENTER,
                    accessible_role=Gtk.AccessibleRole.PRESENTATION)
                play.add_css_class('osd')
                overlay.add_overlay(play)
            click = Gtk.GestureClick()
            connect_weak(click, 'released', self._on_tile_clicked, index)
            overlay.add_controller(click)
            overlay.set_cursor_from_name('pointer')
            grid.attach(overlay, slot % columns, slot // columns, 1, 1)
            if kind == 'image':
                # Gdk.Texture decodes still images from `url` directly;
                # video/gifv `url` is an mp4 stream, the decodable frame
                # lives in preview_url.
                url = attachment.get('url') \
                    or attachment.get('preview_url')
            else:
                url = attachment.get('preview_url') \
                    or attachment.get('url')
            if url:
                _TextureCache.load(url, picture,
                                   (COLUMN_WIDTH // columns, tile_height))
        if footnotes:
            notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for note in footnotes:
                label = Gtk.Label(label=note, xalign=0.0, wrap=True)
                label.add_css_class('caption')
                label.add_css_class('dimmed')
                notes.append(label)
            box.append(notes)
        return box

    def _file_row(self, index, attachment):
        kind = attachment.get('type')
        icon = 'audio-x-generic-symbolic' if kind == 'audio' \
            else 'text-x-generic-symbolic'
        title = attachment.get('description') \
            or MEDIA_KINDS.get(kind, _('Attachment'))
        button = Gtk.Button()
        button.add_css_class('flat')
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Image(icon_name=icon,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION))
        label = Gtk.Label(label=title, xalign=0.0, hexpand=True,
                          ellipsize=Pango.EllipsizeMode.END)
        line.append(label)
        button.set_child(line)
        connect_weak(button, 'clicked', self._on_file_clicked, index)
        return button

    def _on_file_clicked(self, _button, index):
        self._open(index)

    def _on_tile_clicked(self, _gesture, _n_press, _x, _y, index):
        self._open(index)

    def _open(self, index):
        from .mediaviewer import present_media
        present_media(self, self._attachments, index)

    def _toggle_sensitive(self, button, covered, ui):
        revealed = not covered.get_reveal_child()
        ui['media_revealed'] = revealed
        pages.reveal(covered, revealed, animate=True)
        if revealed:
            button.set_label(_('Hide Sensitive Content'))
            button.add_css_class('flat')
        else:
            button.set_label(_('Show Sensitive Content'))
            button.remove_css_class('flat')


# Settings, locale and status helpers

DEFAULT_FLAGS = {
    'collapse-long-posts': True,
    'show-preview-cards': True,
    'show-interaction-counters': True,
    'reply-to-old-post-reminder': True,
    'copy-private-link-reminder': True,
    'boost-alt-text-reminder': True,
}
LONG_POST_CHARS = 700
LONG_POST_BREAKS = 14
OLD_POST_DAYS = 90
_schema_present = None


def _has_schema():
    """True when the application's GSettings schema is installed (it
    is not in bare widget tests, which then get the stock defaults)."""
    global _schema_present
    if _schema_present is None:
        source = Gio.SettingsSchemaSource.get_default()
        _schema_present = bool(
            source and source.lookup(pages.SCHEMA_ID, True))
    return _schema_present


def _flag(name):
    """A boolean application setting."""
    if not _has_schema():
        return DEFAULT_FLAGS.get(name, False)
    return pages.settings().get_boolean(name)


def ui_language():
    """Two-letter language of the user's locale ('pt' for pt_BR)."""
    for name in GLib.get_language_names():
        if name and name != 'C':
            return name.split('.')[0].split('@')[0].split('_')[0].lower()
    return 'en'


def absolute_time(iso_timestamp):
    """'9 Oct 2026, 10:47' in local time, '' when unparsable."""
    moment = GLib.DateTime.new_from_iso8601(iso_timestamp or '', None)
    if moment is None:
        return ''
    # Translators: a GDateTime format string, e.g. "9 Oct 2026, 10:47";
    # see the g_date_time_format() docs
    return moment.to_local().format(_('%-d %b %Y, %H:%M'))


def time_left(iso_timestamp):
    """Compact distance until *iso_timestamp* ('3h', '2d'), '' when it
    is in the past or unparsable."""
    try:
        then = datetime.datetime.fromisoformat(
            iso_timestamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        return ''
    seconds = int((then - datetime.datetime.now(
        datetime.timezone.utc)).total_seconds())
    if seconds <= 0:
        return ''
    if seconds < 3600:
        return _('%dm').replace('%d', str(max(1, seconds // 60)))
    if seconds < 86400:
        return _('%dh').replace('%d', str(seconds // 3600))
    return _('%dd').replace('%d', str(seconds // 86400))


def days_since(iso_timestamp):
    try:
        then = datetime.datetime.fromisoformat(
            iso_timestamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        return 0
    return (datetime.datetime.now(datetime.timezone.utc) - then).days


VISIBILITY_INFO = {
    'public': (_('Public'), 'network-workgroup-symbolic'),
    'unlisted': (_('Unlisted'), 'view-conceal-symbolic'),
    'private': (_('Followers Only'), 'changes-prevent-symbolic'),
    'direct': (_('Mentioned People Only'), 'mail-unread-symbolic'),
}

QUOTE_POLICIES = (('public', _('Anyone')), ('followers', _('My Followers')),
                  ('nobody', _('Nobody')))

MERGE_KEYS = (
    'favourited', 'reblogged', 'bookmarked', 'muted', 'pinned',
    'favourites_count', 'reblogs_count', 'replies_count', 'quotes_count',
    'edited_at', 'content', 'spoiler_text', 'sensitive', 'emojis',
    'media_attachments', 'poll', 'quote', 'quote_approval', 'card')


def status_filter(data):
    """Strongest filter hit on a status as ``(action, title)``, or None.
    'hide' wins over 'warn'/'blur'; boosts are checked on both the
    wrapper and the boosted post."""
    best = None
    for source in (data, data.get('reblog') or {}):
        for result in source.get('filtered') or []:
            rule = result.get('filter') or {}
            action = rule.get('filter_action') or 'warn'
            if action == 'hide':
                return ('hide', rule.get('title') or '')
            if best is None:
                best = (action, rule.get('title') or '')
    return best


def quote_policy_of(status):
    automatic = (status.get('quote_approval') or {}).get('automatic') or []
    if 'public' in automatic:
        return 'public'
    if 'followers' in automatic:
        return 'followers'
    return 'nobody'


def can_quote(status):
    approval = status.get('quote_approval')
    if approval:
        return approval.get('current_user') not in ('denied', 'unknown')
    return status.get('visibility') in ('public', 'unlisted')


def plain_text(html_text, limit=None):
    """Readable plain text of status HTML, optionally truncated."""
    parts = []
    for segment in parse_content(html_text):
        if segment['text'] == '\n':
            parts.append('\n')
        else:
            if parts and parts[-1] != '\n':
                parts.append(' ')
            parts.append(shorten_url_text(segment['text'])
                         if segment['href'] else segment['text'])
    text = ''.join(parts).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(' ', 1)[0] + '…'
    return text


def _collapse_view(segments):
    """``(segments to show, collapsed)``: hashtag-stuffed and very long
    posts shrink behind Show More while ``collapse-long-posts`` is on."""
    if not segments or not _flag('collapse-long-posts'):
        return segments, False
    if _should_collapse(segments):
        return [{'text': _static_content_labels(segments),
                 'href': None}], True
    length = sum(len(s['text']) for s in segments if s['text'] != '\n')
    breaks = sum(1 for s in segments if s['text'] == '\n')
    if length <= LONG_POST_CHARS and breaks <= LONG_POST_BREAKS:
        return segments, False
    shown, budget, lines = [], LONG_POST_CHARS // 2, 0
    for segment in segments:
        if segment['text'] == '\n':
            lines += 1
            if lines > 8:
                break
            shown.append(segment)
            continue
        text = segment['text']
        if len(text) > budget:
            cut = text[:budget].rsplit(' ', 1)[0] or text[:budget]
            shown.append(dict(segment, text=cut + '…'))
            break
        shown.append(segment)
        budget -= len(text)
    while shown and shown[-1]['text'] == '\n':
        shown.pop()
    return shown, True


def _translation_enabled(client):
    """True when the instance offers post translation. The instance
    document is fetched once in the background if it is not cached."""
    info = getattr(client, 'instance_info', None)
    if info is None:
        if hasattr(client, 'instance_info_async') \
                and not getattr(client, '_babble_info_requested', False):
            client._babble_info_requested = True

            def loaded(_client, result, _data=None):
                try:
                    client.instance_info_finish(result)
                except GLib.Error:
                    pass

            client.instance_info_async(loaded)
        return False
    translation = (info.get('configuration') or {}).get('translation') or {}
    return bool(translation.get('enabled'))


def _own_account_id(widget):
    window = pages.window_of(widget)
    account = getattr(window, 'account', None) or {}
    return account.get('id')


class QuoteCard(Gtk.Button):
    """Compact card for a post's ``quote``: the quoted post (author, text,
    first picture) opening its thread, or a notice for quotes that are
    pending, declined, removed or hidden."""

    NOTICES = {
        'pending': _('Quote Pending'),
        'rejected': _('Quote Not Approved'),
        'revoked': _('Quote Removed by Author'),
        'deleted': _('Quoted Post Was Deleted'),
        'unauthorized': _('Quote Unavailable'),
        'blocked_account': _('Quote Hidden: You Blocked the Author'),
        'blocked_domain': _('Quote Hidden: You Blocked the Server'),
        'muted_account': _('Quote Hidden: You Muted the Author'),
    }

    def __init__(self, quote, client):
        super().__init__()
        self.add_css_class('card')
        self.add_css_class('quote-card')
        self.client = client
        self.quote = quote
        quoted = quote.get('quoted_status')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=8, margin_start=10,
                      margin_end=10)
        self.set_child(box)
        if quoted:
            self._fill(box, quoted)
            self.connect('clicked', self._open_quoted, quoted)
            return
        state = quote.get('state') or ''
        if state == 'accepted' and quote.get('quoted_status_id'):
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Image(icon_name='format-indent-more-symbolic'))
            row.append(Gtk.Label(label=_('Quoted a Post'), xalign=0.0))
            box.append(row)
            self.connect('clicked', self._resolve_quoted)
            return
        text = self.NOTICES.get(state, _('Quote Unavailable'))
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image(icon_name='dialog-warning-symbolic'))
        label = Gtk.Label(label=text, xalign=0.0, wrap=True, hexpand=True)
        label.add_css_class('dimmed')
        row.append(label)
        box.append(row)
        self.set_sensitive(False)
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _fill(self, box, quoted):
        account = quoted.get('account') or {}
        name = account.get('display_name') or account.get('acct', '')
        head = Gtk.Box(spacing=6)
        avatar = Adw.Avatar(size=22, show_initials=True, text=name or '?')
        AvatarCache.load(avatar, account)
        head.append(avatar)
        title = InlineEmojiText(emoji_size=14)
        title.add_css_class('caption-heading')
        title.set_can_target(False)
        title.set_segments([{'text': name or '?', 'href': None}],
                           emoji_bank(account))
        head.append(title)
        handle = Gtk.Label(label='@%s' % account.get('acct', ''),
                           xalign=0.0, hexpand=True,
                           ellipsize=Pango.EllipsizeMode.END)
        handle.add_css_class('caption')
        handle.add_css_class('dimmed')
        head.append(handle)
        box.append(head)
        spoiler = quoted.get('spoiler_text')
        text = (_('Content warning: %s') % spoiler) if spoiler \
            else plain_text(quoted.get('content'), 280)
        body = Gtk.Label(label=text or '…', xalign=0.0, wrap=True,
                         lines=4, ellipsize=Pango.EllipsizeMode.END,
                         wrap_mode=Pango.WrapMode.WORD_CHAR)
        if spoiler:
            body.add_css_class('dimmed')
        box.append(body)
        media = [a for a in quoted.get('media_attachments') or []
                 if a.get('type') in ('image', 'video', 'gifv')]
        if media and not spoiler and not quoted.get('sensitive'):
            picture = Gtk.Picture(
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(True)
            picture.set_size_request(-1, 110)
            picture.add_css_class('status-media')
            picture.add_css_class('darkenable')
            url = media[0].get('preview_url') or media[0].get('url')
            if url:
                _TextureCache.load(url, picture, (COLUMN_WIDTH, 110))
            box.append(picture)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Quoted post by %(author)s: %(text)s') % {
                'author': name, 'text': text}])

    def _open_quoted(self, _button, quoted):
        pages.open_status(self, self.client, quoted)

    def _resolve_quoted(self, _button):
        pages.call(
            self, self.client, 'GET',
            '/statuses/%s' % self.quote['quoted_status_id'],
            lambda data: pages.open_status(self, self.client, data),
            _('Could not open quoted post'))


def _display_name_uses_custom_emoji(account, display_name):
    """True when the visible name contains a custom emoji shortcode."""
    bank = emoji_bank(account)
    if not bank or not display_name:
        return False
    for shortcode in bank:
        if ':%s:' % shortcode in display_name:
            return True
    return False


def _open_row_menu(_button, row_ref):
    """Gtk.MenuButton create-popup callback of a StatusRow's overflow
    button; holds the row weakly so the button does not keep it alive."""
    row = row_ref()
    if row is not None:
        row._prepare_menu()


class StatusRow(Gtk.Overlay):
    """Post card: boost strip, author header with indicators and the
    overflow menu, content warning, body, poll, quote, card, media and
    an action row; like the reference client the bar is off on patrols
    and always on detail views."""

    __gtype_name__ = 'BabbleStatusRow'

    ICON_REPLY = 'mail-reply-sender-symbolic'
    ICON_BOOST = 'media-playlist-repeat-symbolic'
    ICON_QUOTE = 'format-indent-more-symbolic'
    ICON_FAVOURITE = ('non-starred-symbolic', 'starred-symbolic')
    ICON_BOOKMARK = ('bookmark-new-symbolic', 'user-bookmarks-symbolic')

    MENU_ACTIONS = (
        'view', 'open-browser', 'copy', 'stats', 'history', 'translate',
        'mute-conversation', 'pin', 'edit', 'delete', 'redraft',
        'revoke-quote', 'report', 'mention', 'mute', 'block')

    def __init__(self, detail=False, **kwargs):
        super().__init__(accessible_role=Gtk.AccessibleRole.ARTICLE,
                         **kwargs)
        # A card is sized by its content; without this it stretches to
        # whatever box it lands in (thread view, notification insets).
        self.set_valign(Gtk.Align.START)
        self.set_vexpand(False)
        self.set_hexpand(True)
        self.add_css_class('status-row')
        self.item = None
        self.client = None
        self.detail = detail
        self.media_blur = False
        self._busy = False
        self._emoji_bank = {}
        self._body_animation = None
        self._body_tick = None
        self._body_overflow = Gtk.Overflow.VISIBLE

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_hexpand(True)
        self.body = body
        self.set_child(body)

        # The boost strip, display-name emoji text, content-warning bar,
        # translation note and boosts carousel are built on first use:
        # most posts need none of them, and each costs a text view or a
        # scrolled window per row.
        self.boost_strip = None
        self.boost_label = None

        # Filtered posts collapse into this bar until shown anyway.
        self.filter_bar = Gtk.Box(spacing=8)
        self.filter_bar.add_css_class('filter-bar')
        self.filter_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        self.filter_label.add_css_class('dimmed')
        self.filter_bar.append(self.filter_label)
        self.filter_button = Gtk.Button(label=_('Show _Anyway'),
                                        use_underline=True,
                                        valign=Gtk.Align.CENTER)
        self.filter_button.add_css_class('flat')
        connect_weak(self.filter_button, 'clicked', self._on_show_filtered)
        self.filter_bar.append(self.filter_button)
        self.filter_bar.set_visible(False)
        body.append(self.filter_bar)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.main = main
        body.append(main)

        header = Gtk.Box(spacing=6)
        self.header = header
        # Fills the header up to the time and menu, so its hover pill
        # reads as the header's left side, not a box hugging the name.
        profile_button = Gtk.Button(tooltip_text=_('View Profile'))
        profile_button.add_css_class('flat')
        profile_button.add_css_class('profile-opener')
        profile_button.set_hexpand(True)
        opener = Gtk.Box(spacing=10)
        opener.set_hexpand(True)
        self.avatar = Adw.Avatar(size=46, show_initials=True)
        opener.append(self.avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.CENTER)
        names.set_hexpand(True)
        name_line = Gtk.Box(spacing=6)
        name_line.set_hexpand(True)
        self._name_line = name_line
        self.name_plain = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.name_plain.add_css_class('heading')
        name_line.append(self.name_plain)
        self.name_label = None
        self.bot_badge = Gtk.Label(label=_('Bot'), valign=Gtk.Align.CENTER)
        self.bot_badge.set_hexpand(False)
        self.bot_badge.add_css_class('status-chip')
        self.bot_badge.add_css_class('caption')
        self.bot_badge.set_visible(False)
        name_line.append(self.bot_badge)
        self.handle_label = Gtk.Label(xalign=0.0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.handle_label.add_css_class('caption')
        self.handle_label.add_css_class('dimmed')
        names.append(name_line)
        names.append(self.handle_label)
        opener.append(names)
        profile_button.set_child(opener)
        connect_weak(profile_button, 'clicked', self._on_profile_clicked)
        self.profile_button = profile_button
        header.append(profile_button)
        meta = Gtk.Box(spacing=6, halign=Gtk.Align.END,
                       valign=Gtk.Align.CENTER)
        # Thread position badge: '2/5' on posts of a same-thread run.
        self.badge = Gtk.Label()
        self.badge.add_css_class('run-badge')
        self.badge.add_css_class('caption-heading')
        self.badge.add_css_class('numeric')
        self.badge.set_visible(False)
        meta.append(self.badge)
        self.pinned_badge = Gtk.Label(label=_('Pinned'))
        self.pinned_badge.add_css_class('status-chip')
        self.pinned_badge.add_css_class('caption')
        self.pinned_badge.set_visible(False)
        meta.append(self.pinned_badge)
        self.language_label = Gtk.Label()
        self.language_label.add_css_class('caption')
        self.language_label.add_css_class('dimmed')
        self.language_label.set_visible(False)
        meta.append(self.language_label)
        self.visibility_icon = Gtk.Image(pixel_size=14)
        self.visibility_icon.add_css_class('dimmed')
        self.visibility_icon.set_visible(False)
        meta.append(self.visibility_icon)
        self.edited_button = Gtk.Button(label=_('Edited'))
        self.edited_button.add_css_class('flat')
        self.edited_button.add_css_class('caption')
        self.edited_button.add_css_class('edited-marker')
        connect_weak(self.edited_button, 'clicked', self._on_edited_clicked)
        self.edited_button.set_visible(False)
        meta.append(self.edited_button)
        self.time_label = Gtk.Label()
        self.time_label.add_css_class('caption')
        self.time_label.add_css_class('dimmed')
        self.time_label.add_css_class('numeric')
        meta.append(self.time_label)
        header.append(meta)
        # Overflow menu sits with the timestamp, the way the bar keeps it
        # in the post header rather than in the action row.
        self._overflow = self._overflow_button()
        header.append(self._overflow)
        main.append(header)

        self.reply_box = Gtk.Box(spacing=6)
        reply_icon = Gtk.Image(
            icon_name=self.ICON_REPLY, pixel_size=12,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        reply_icon.add_css_class('dimmed')
        self.reply_box.append(reply_icon)
        self.reply_label = Gtk.Label(xalign=0.0,
                                     ellipsize=Pango.EllipsizeMode.END)
        self.reply_label.add_css_class('caption')
        self.reply_label.add_css_class('dimmed')
        self.reply_box.append(self.reply_label)
        main.append(self.reply_box)

        self.cw_box = None
        self.cw_label = None
        self.cw_toggle = None

        guarded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.guarded = guarded
        self.guarded_revealer = None
        main.append(guarded)

        self.content = StatusBody()
        guarded.append(self.content)

        self.expand_button = Gtk.Button(label=_('Show More'),
                                        halign=Gtk.Align.START)
        self.expand_button.add_css_class('flat')
        self.expand_button.add_css_class('caption-heading')
        self.expand_button.set_visible(False)
        connect_weak(self.expand_button, 'clicked', self._on_expand)
        guarded.append(self.expand_button)

        self.post_meta = Gtk.Label(xalign=0.0, wrap=True)
        self.post_meta.add_css_class('caption')
        self.post_meta.add_css_class('dimmed')
        self.post_meta.set_visible(False)
        guarded.append(self.post_meta)

        self.translation_box = None
        self.translation_label = None

        self.poll_box = PollBox()
        guarded.append(self.poll_box)

        self.quote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        guarded.append(self.quote_box)

        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=0)
        guarded.append(self.card_box)

        self.media_box = MediaBox()
        guarded.append(self.media_box)

        # WrapBox, not Box: with a count next to each icon the five
        # actions need ~314 px, which overflows the 360 px window
        # minimum (350 px of content) instead of flowing to a second
        # line.
        actions = Adw.WrapBox(child_spacing=4, line_spacing=4,
                              halign=Gtk.Align.START)
        self.reply_button = self._action_button(
            self.ICON_REPLY, _('Reply'), self._on_reply,
            nouns=('%d reply', '%d replies'))
        self.boost_button = self._action_button(
            (self.ICON_BOOST,) * 2, _('Boost'), self._on_boost,
            nouns=('%d boost', '%d boosts'), toggle=True)
        self.quote_button = self._action_button(
            self.ICON_QUOTE, _('Quote'), self._on_quote,
            nouns=('%d quote', '%d quotes'))
        self.favourite_button = self._action_button(
            self.ICON_FAVOURITE, _('Favorite'), self._on_favourite,
            nouns=('%d favorite', '%d favorites'), toggle=True)
        self.bookmark_button = self._action_button(
            self.ICON_BOOKMARK, _('Bookmark'), self._on_bookmark,
            toggle=True)
        actions.append(self.reply_button)
        actions.append(self.boost_button)
        actions.append(self.quote_button)
        actions.append(self.favourite_button)
        actions.append(self.bookmark_button)
        self.actions = actions
        actions.add_css_class('status-actions')
        main.append(actions)

        self.carousel = None
        self._item_watch = None

    # Parts built on first use

    def _ensure_boost_strip(self):
        if self.boost_strip is None:
            strip = Gtk.Box(spacing=6)
            icon = Gtk.Image(
                icon_name=self.ICON_BOOST, pixel_size=12,
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            icon.add_css_class('boost-accent')
            strip.append(icon)
            self.boost_label = InlineEmojiText(emoji_size=14,
                                               single_line=True)
            self.boost_label.add_css_class('caption-heading')
            self.boost_label.add_css_class('dimmed')
            self.boost_label.set_can_target(False)
            strip.append(self.boost_label)
            self.boost_strip = strip
            self.body.prepend(strip)
        return self.boost_strip

    def _ensure_name_label(self):
        if self.name_label is None:
            label = InlineEmojiText(emoji_size=18, single_line=True)
            label.add_css_class('heading')
            # Clicks fall through to the profile button.
            label.set_can_target(False)
            self.name_label = label
            self._name_line.insert_child_after(label, self.name_plain)
        return self.name_label

    def _ensure_cw_box(self):
        """Content warning: the spoiler line with its Show/Hide toggle."""
        if self.cw_box is None:
            box = Gtk.Box(spacing=8)
            box.add_css_class('cw-bar')
            self.cw_label = InlineEmojiText(emoji_size=16, shorten_links=True)
            self.cw_label.set_hexpand(True)
            self.cw_label.add_css_class('heading')
            self.cw_label.set_valign(Gtk.Align.CENTER)
            box.append(self.cw_label)
            self.cw_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
            self.cw_toggle.add_css_class('pill')
            self.cw_toggle.add_css_class('cw-toggle')
            connect_weak(self.cw_toggle, 'clicked', self._on_cw_toggle)
            box.append(self.cw_toggle)
            self.cw_box = box
            self.main.insert_child_after(box, self.reply_box)
            # Only rows that have shown a content warning pay for the
            # revealer the body slides in and out of.
            revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
            self.main.insert_child_after(revealer, self.guarded)
            self.main.remove(self.guarded)
            revealer.set_child(self.guarded)
            self.guarded_revealer = revealer
        return self.cw_box

    def _ensure_translation_box(self):
        if self.translation_box is None:
            box = Gtk.Box(spacing=8)
            self.translation_label = Gtk.Label(xalign=0.0, hexpand=True,
                                               wrap=True)
            self.translation_label.add_css_class('caption')
            self.translation_label.add_css_class('dimmed')
            box.append(self.translation_label)
            original = Gtk.Button(label=_('Show _Original'),
                                  use_underline=True)
            original.add_css_class('flat')
            original.add_css_class('caption')
            connect_weak(original, 'clicked', self._menu_translate)
            box.append(original)
            self.translation_box = box
            self.guarded.insert_child_after(box, self.post_meta)
        return self.translation_box

    def _ensure_carousel(self):
        """Boosts-carousel mode: the first list row can render as the
        horizontal boosts carousel instead of a normal post."""
        if self.carousel is None:
            self.carousel = BoostCarousel()
            self.body.append(self.carousel)
        return self.carousel

    # Overflow menu

    def _overflow_button(self):
        button = Gtk.MenuButton(icon_name='view-more-symbolic',
                                tooltip_text=_('More Options'),
                                valign=Gtk.Align.CENTER)
        button.add_css_class('flat')
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [_('More Options')])
        # The actions and the menu are built when the menu opens: a list
        # binds rows far more often than anyone opens their menu.
        self._menu_actions = None
        button.set_create_popup_func(_open_row_menu, self.weak_ref())
        return button

    def _ensure_menu_actions(self):
        if self._menu_actions is not None:
            return
        group = Gio.SimpleActionGroup()
        self._menu_actions = {}
        for name in self.MENU_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            handler = getattr(self, '_menu_' + name.replace('-', '_'))
            connect_weak(action, 'activate', handler)
            group.add_action(action)
            self._menu_actions[name] = action
        policy = Gio.SimpleAction.new_stateful(
            'quote-policy', GLib.VariantType.new('s'),
            GLib.Variant.new_string('public'))
        connect_weak(policy, 'activate', self._menu_quote_policy)
        group.add_action(policy)
        self._menu_actions['quote-policy'] = policy
        self._overflow.insert_action_group('row', group)

    def _prepare_menu(self):
        """Actions and menu for the bound post, as the menu opens."""
        if self.item is None:
            return
        self._ensure_menu_actions()
        self._sync_menu_state(self.item)
        self._overflow.set_menu_model(self._build_menu(self.item))

    def _is_own(self):
        own = _own_account_id(self)
        account = self.item.status['account'] if self.item else {}
        return bool(own) and account.get('id') == own

    def _can_mute_conversation(self):
        status = self.item.status
        own = _own_account_id(self)
        if not own or not self._can_act():
            return False
        if self._is_own():
            return True
        return any(m.get('id') == own for m in status.get('mentions') or [])

    def _can_translate(self):
        status = self.item.status
        language = status.get('language')
        return bool(
            self._can_act() and not self._is_own() and language
            and language.lower().split('-')[0] != ui_language()
            and status.get('visibility') in ('public', 'unlisted')
            and _translation_enabled(self.client))

    def _revocable_quote(self):
        quote = self.item.status.get('quote') or {}
        quoted = quote.get('quoted_status')
        own = _own_account_id(self)
        return bool(
            quote.get('state') == 'accepted' and quoted and own
            and (quoted.get('account') or {}).get('id') == own
            and not self._is_own())

    def _build_menu(self, item):
        status = item.status
        own = self._is_own()
        signed_in = self._can_act()
        # Underscores in a handle would read as access keys.
        handle = item.handle.replace('_', '__')
        menu = Gio.Menu()

        links = Gio.Menu()
        links.append(_('_View Post'), 'row.view')
        links.append(_('Open in _Browser'), 'row.open-browser')
        links.append(_('_Copy Link'), 'row.copy')
        if status.get('visibility') != 'direct' or own:
            links.append(_('View S_tats'), 'row.stats')
        if status.get('edited_at'):
            links.append(_('View Edit _History'), 'row.history')
        menu.append_section(None, links)

        post = Gio.Menu()
        if self._can_translate():
            post.append(_('Show _Original') if item.translation
                        else _('_Translate'), 'row.translate')
        if self._can_mute_conversation():
            post.append(_('Un_mute Conversation') if status.get('muted')
                        else _('_Mute Conversation'),
                        'row.mute-conversation')
        if own and status.get('visibility') in ('public', 'unlisted'):
            post.append(_('_Unpin From Profile') if status.get('pinned')
                        else _('_Pin to Profile'), 'row.pin')
        if own and status.get('quote_approval') \
                and status.get('visibility') in ('public', 'unlisted'):
            submenu = Gio.Menu()
            for value, title in QUOTE_POLICIES:
                entry = Gio.MenuItem.new(title, None)
                entry.set_action_and_target_value(
                    'row.quote-policy', GLib.Variant.new_string(value))
                submenu.append_item(entry)
            post.append_submenu(_('Who Can _Quote'), submenu)
        if signed_in and self._revocable_quote():
            post.append(_('_Remove My Post From Quote'), 'row.revoke-quote')
        if post.get_n_items():
            menu.append_section(None, post)

        if own:
            mine = Gio.Menu()
            mine.append(_('_Edit'), 'row.edit')
            mine.append(_('Delete and _Redraft'), 'row.redraft')
            mine.append(_('_Delete'), 'row.delete')
            menu.append_section(None, mine)
        elif signed_in:
            account = Gio.Menu()
            account.append(_('Mention %s') % handle, 'row.mention')
            account.append(_('_Mute %s') % handle, 'row.mute')
            account.append(_('_Block %s') % handle, 'row.block')
            account.append(_('_Report Post…'), 'row.report')
            menu.append_section(None, account)
        return menu

    def _can_act(self):
        return self.client is not None \
            and bool(getattr(self.client, 'token', None))

    def _author(self):
        if self.item is None or not self._can_act():
            return None
        account = self.item.status['account']
        return account if account.get('id') else None

    def _toast(self, text, undo=None):
        pages.show_toast(self, text, undo=undo)

    def _menu_view(self, *_args):
        if self.item is not None:
            self.activate_status(self.item)

    def _menu_open_browser(self, *_args):
        if self.item is not None and self.item.status_url:
            launch_uri(self, self.item.status_url)

    def _menu_copy(self, *_args):
        if self.item is None or not self.item.status_url:
            return
        status = self.item.status
        visibility = status.get('visibility')
        if visibility in ('direct', 'private') \
                and _flag('copy-private-link-reminder'):
            self._confirm_copy(status)
            return
        self._copy_url()

    def _copy_url(self):
        clipboard = self.get_clipboard()
        if clipboard and self.item is not None:
            clipboard.set(self.item.status_url)
            self._toast(_('Link copied'))

    def _confirm_copy(self, status):
        visibility = status.get('visibility')
        label = VISIBILITY_INFO[visibility][0]
        if visibility == 'direct':
            detail = _('Only mentioned people will be able to view it.')
        elif self._is_own():
            detail = _('Only mentioned people and your followers will be '
                       'able to view it.')
        else:
            detail = _('Only mentioned people and people that follow %s '
                       'will be able to view it.') % self.item.handle
        dialog = Adw.AlertDialog(
            heading=_('Copy “%s” Post Link?') % label, body=detail)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('never', _('_Don’t Remind Me Again'))
        dialog.add_response('copy', _('_Copy'))
        dialog.set_response_appearance('copy',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('copy')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response == 'never':
                pages.settings().set_boolean('copy-private-link-reminder',
                                             False)
            if response in ('never', 'copy'):
                self._copy_url()

        dialog.connect('response', on_response)
        dialog.present(self)

    def _menu_stats(self, *_args):
        if self.item is None or self.client is None:
            return
        from .statusviews import push_status_stats
        push_status_stats(self, self.client, self.item.status,
                          'favourites')

    def _menu_history(self, *_args):
        if self.item is None or self.client is None:
            return
        from .statusviews import push_edit_history
        push_edit_history(self, self.client, self.item.status)

    def _on_edited_clicked(self, _button):
        self._menu_history()

    def _menu_translate(self, *_args):
        item = self.item
        if item is None:
            return
        if item.translation:
            item.translation = None
            self.bind(item, self.client)
            return

        def translated(data):
            item.translation = data
            if self.item is item:
                self.bind(item, self.client)
                # A post collapsed by a filter binds no translation box.
                if self.translation_box is not None:
                    pages.flash_class(self.translation_box, 'fade-in')
                pages.flash_class(self.content, 'fade-in')

        pages.call(self, self.client, 'POST',
                   '/statuses/%s/translate' % item.status_id, translated,
                   _('Could not translate'),
                   form={'lang': ui_language()})

    def _post_action(self, action, heading):
        """POST /statuses/:id/<action> and merge the returned status."""
        item = self.item

        def done(data):
            self._merge_status(item, data)
            if self.item is item:
                self.bind(item, self.client)

        pages.call(self, self.client, 'POST',
                   '/statuses/%s/%s' % (item.status_id, action), done,
                   heading)

    def _menu_mute_conversation(self, *_args):
        if self.item is None:
            return
        muted = bool(self.item.status.get('muted'))
        self._post_action(
            'unmute' if muted else 'mute',
            _('Could not unmute conversation') if muted
            else _('Could not mute conversation'))

    def _menu_pin(self, *_args):
        if self.item is None:
            return
        pinned = bool(self.item.status.get('pinned'))
        self._post_action('unpin' if pinned else 'pin',
                          _('Could not unpin post') if pinned
                          else _('Could not pin post'))

    def _menu_quote_policy(self, action, parameter):
        item = self.item
        if item is None or parameter is None:
            return
        value = parameter.get_string()

        def done(data):
            self._merge_status(item, data)
            if self.item is item:
                self.bind(item, self.client)

        pages.call(self, self.client, 'PUT',
                   '/statuses/%s/interaction_policy' % item.status_id, done,
                   _('Could not change quote policy'),
                   form={'quote_approval_policy': value})

    def _menu_revoke_quote(self, *_args):
        item = self.item
        if item is None:
            return
        quoted = item.status['quote']['quoted_status']

        def revoke():
            def done(data):
                self._merge_status(item, data)
                if self.item is item:
                    self.bind(item, self.client)
                self._toast(_('Your post was removed from the quote'))

            pages.call(self, self.client, 'POST',
                       '/statuses/%s/quotes/%s/revoke'
                       % (quoted['id'], item.status_id), done,
                       _('Could not remove your post'))

        pages.confirm(self, _('Remove Your Post From the Quote?'),
                      _('The quote will no longer show your post.'),
                      _('_Remove'), revoke)

    def _menu_edit(self, *_args):
        item = self.item
        if item is None:
            return

        def loaded(source):
            status = dict(item.status)
            status['text'] = source.get('text', '')
            if 'spoiler_text' in source:
                status['spoiler_text'] = source['spoiler_text']
            self._open_composer('start_edit', status)

        pages.call(self, self.client, 'GET',
                   '/statuses/%s/source' % item.status_id, loaded,
                   _('Could not load post for editing'))

    def _menu_delete(self, *_args):
        item = self.item
        if item is None:
            return
        pages.confirm(
            self, _('Delete Post?'),
            _('This post will be removed from your profile and from the '
              'timelines of other servers.'),
            _('_Delete'), lambda: self._delete(item, None))

    def _menu_redraft(self, *_args):
        item = self.item
        if item is None:
            return
        pages.confirm(
            self, _('Delete and Redraft?'),
            _('This post will be deleted and its text, attachments and '
              'poll copied into a new draft.'),
            _('_Delete and Redraft'),
            lambda: self._delete(item, 'start_redraft'))

    def _delete(self, item, composer_method):
        """DELETE the post; for a redraft hand the returned source to
        the composer."""

        def done(data):
            self._after_deleted(item)
            if composer_method:
                status = dict(item.status)
                if isinstance(data, dict):
                    status.update(data)
                self._open_composer(composer_method, status)
            else:
                self._toast(_('Post deleted'))

        pages.call(self, self.client, 'DELETE',
                   '/statuses/%s' % item.status_id, done,
                   _('Could not delete post'))

    def _after_deleted(self, item):
        widget = self.get_parent()
        while widget is not None and not isinstance(widget, TimelinePage):
            widget = widget.get_parent()
        if widget is not None:
            widget.on_stream_delete(item.status_id)
        self.set_visible(False)

    def _open_composer(self, method, status):
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        if dialog is None:
            return
        getattr(dialog, method)(status)
        window.present_composer()

    def _menu_report(self, *_args):
        account = self._author()
        if account is None:
            return
        from .report import present_report
        present_report(self, self.client, account, self.item.status)

    def _menu_mention(self, *_args):
        account = self._author()
        if account is None:
            return
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        if dialog is None:
            return
        dialog.start_mention(account)
        window.present_composer()

    def _menu_mute(self, *_args):
        account = self._author()
        if account is None:
            return
        client, handle = self.client, self.item.handle

        def muted(_data):
            def undo():
                pages.call(self, client, 'POST',
                           '/accounts/%s/unmute' % account['id'], None,
                           _('Unmute failed'))

            self._toast(_('Muted %s') % handle, undo)

        pages.call(self, client, 'POST', '/accounts/%s/mute' % account['id'],
                   muted, _('Mute failed'))

    def _menu_block(self, *_args):
        account = self._author()
        if account is None:
            return
        client, handle = self.client, self.item.handle

        def block():
            pages.call(self, client, 'POST',
                       '/accounts/%s/block' % account['id'],
                       lambda _data: self._toast(_('Blocked %s') % handle),
                       _('Block failed'))

        pages.confirm(
            self, _('Block %s?') % handle,
            _('They will no longer be able to follow your account or '
              'see your posts, and their posts will be hidden.'),
            _('_Block'), block)

    # Buttons

    def _action_button(self, icons, tooltip, callback, nouns=None,
                       toggle=False):
        if isinstance(icons, str):
            icons = (icons, icons)
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.add_css_class('flat')
        button.set_child(Adw.ButtonContent(icon_name=icons[0]))
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        connect_weak(button, 'clicked', callback)
        button.icons = icons
        button.action_name_text = tooltip
        button.nouns = nouns
        return button

    def set_run(self, position, total):
        """Lay this post out as part of a run of same-thread posts by
        the same author: the ``n/total`` badge appears, and the run card
        decides which of the other header pieces to keep."""
        self.badge.set_visible(bool(total) and total >= 2)
        if self.badge.get_visible():
            text = _('Post %(position)d of %(total)d in Thread') % {
                'position': position, 'total': total}
            self.badge.set_text('%d/%d' % (position, total))
            self.badge.set_tooltip_text(text)
            self.badge.update_property([Gtk.AccessibleProperty.LABEL],
                                       [text])

    # Binding

    def bind(self, item, client):
        self._stop_body_animation()
        if self._item_watch is not None and self._item_watch[0] is not item:
            watched, handler = self._item_watch
            watched.disconnect(handler)
            self._item_watch = None
        if self._item_watch is None:
            # Edits and carousel updates change the item in place.
            self._item_watch = (item, connect_weak(
                item, 'changed', self._on_item_changed))
        if item is not self.item:
            self._clear_flashes()
        self.item = item
        self.client = client
        if self._overflow.get_menu_model() is not None:
            # Rebuilt for this post the next time the menu opens.
            self._overflow.set_menu_model(None)
        if isinstance(item, BoostCarouselItem):
            self._bind_carousel(item, client)
            return
        if self.carousel is not None:
            self.carousel.set_visible(False)
        self._bind_status(item)

    def _clear_flashes(self):
        """End a pop or fade still playing for the post this recycled row
        showed before, so the next post does not inherit it."""
        for widget, css in ((self.favourite_button, 'just-toggled'),
                            (self.boost_button, 'just-toggled'),
                            (self.bookmark_button, 'just-toggled'),
                            (self.content, 'fade-in'),
                            (self.translation_box, 'fade-in')):
            if widget is not None:
                widget.remove_css_class(css)

    def _bind_status(self, item):
        status = item.status
        account = status['account']
        ui = item.ui
        if item.is_boost:
            self._ensure_boost_strip().set_visible(True)
            self.boost_label.set_segments(
                [{'text': _('%s boosted') % item.boost_line,
                  'href': None}], emoji_bank(item.boosted_by))
            label = _('Post by %(author)s, boosted by %(booster)s') % {
                'author': item.display_name, 'booster': item.boost_line}
        else:
            if self.boost_strip is not None:
                self.boost_strip.set_visible(False)
            label = _('Post by %s') % item.display_name
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

        rule = status_filter(item.raw)
        shown = ui.get('filter_shown')
        # 'blur' never collapses: the post stays readable and only the
        # media is covered; 'warn' and 'hide' collapse until shown.
        blurred_rule = rule is not None and rule[0] == 'blur'
        collapsed = rule is not None and not blurred_rule and not shown
        blur = blurred_rule and not shown
        self.media_blur = blur
        self.filter_bar.set_visible(collapsed or blur)
        self.actions.set_visible(
            self.detail or _flag('show-action-bar'))
        if collapsed:
            self.filter_label.set_text(
                _('Filtered: %s') % rule[1] if rule[1] else _('Filtered'))
            self.main.set_visible(False)
            return
        self.main.set_visible(True)
        if blur:
            # The badge stays so the filter names itself; the Show
            # Anyway click clears it together with the blur.
            self.filter_label.set_text(
                _('Filtered: %s') % rule[1] if rule[1] else _('Filtered'))

        AvatarCache.load(self.avatar, account)
        display = item.display_name or '?'
        if _display_name_uses_custom_emoji(account, display):
            self.name_plain.set_visible(False)
            self._ensure_name_label().set_visible(True)
            self.name_label.set_segments(
                [{'text': display, 'href': None}], emoji_bank(account))
        else:
            if self.name_label is not None:
                self.name_label.set_visible(False)
            self.name_plain.set_visible(True)
            self.name_plain.set_label(display)
            self.name_plain.set_tooltip_text(display)
        caption = item.header_caption
        self.handle_label.set_text(caption)
        self.handle_label.set_visible(bool(caption))
        self.handle_label.set_tooltip_text(item.handle)
        self.bot_badge.set_visible(bool(account.get('bot')))
        self.profile_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ['%s %s' % (item.display_name, item.handle)])
        self._bind_indicators(item)
        self._set_time(item)
        self._set_post_meta(item)
        self._bind_reply_line(item)

        translation = item.translation
        spoiler = (translation or {}).get('spoiler_text') \
            or status.get('spoiler_text') or ''
        bank = status_emoji_bank(status)
        self._emoji_bank = bank
        has_cw = bool(spoiler)
        revealed = True
        if has_cw:
            self._ensure_cw_box().set_visible(True)
            self.cw_label.set_segments(
                [{'text': spoiler, 'href': None}], bank)
            revealed = ui.get('cw_open')
            if revealed is None:
                revealed = _flag('show-spoilers')
            self._update_cw_toggle(spoiler, revealed)
        elif self.cw_box is not None:
            self.cw_box.set_visible(False)
        if self.guarded_revealer is not None:
            pages.reveal(self.guarded_revealer, bool(revealed))

        self._render_body(item, bank)
        self._bind_translation(item)

        poll = status.get('poll')
        if poll and translation and translation.get('poll'):
            titles = [o.get('title') for o in
                      translation['poll'].get('options') or []]
            options = [dict(o, title=titles[i] if i < len(titles)
                            and titles[i] else o.get('title'))
                       for i, o in enumerate(poll.get('options') or [])]
            poll = dict(poll, options=options)
        self.poll_box.set_poll(poll, bank, self.client, ui,
                               lambda new: status.__setitem__('poll', new))
        quote = status.get('quote')
        self._show_quote(quote)
        attachments = status.get('media_attachments') or []
        self.media_box.set_media(
            attachments, bool(status.get('sensitive')) and not has_cw, ui,
            blur=self.media_blur)
        show_card = _flag('show-preview-cards') and not poll and not quote \
            and not attachments
        self._show_card(status.get('card') if show_card else None)
        self._bind_actions(item)

    def _bind_translation(self, item):
        """The 'Translated from …' note above the body, when shown."""
        translation = item.translation
        if not translation:
            if self.translation_box is not None:
                self.translation_box.set_visible(False)
            return
        self._ensure_translation_box().set_visible(True)
        detected = (translation.get('detected_source_language') or '').upper()
        provider = translation.get('provider') or ''
        if detected and provider:
            text = _('Translated from %(language)s using %(provider)s') \
                % {'language': detected, 'provider': provider}
        elif provider:
            text = _('Translated using %s') % provider
        else:
            text = _('Translated')
        self.translation_label.set_text(text)

    def _bind_indicators(self, item):
        status = item.status
        info = VISIBILITY_INFO.get(status.get('visibility'))
        show_visibility = info is not None \
            and status.get('visibility') != 'public'
        self.visibility_icon.set_visible(show_visibility)
        if show_visibility:
            self.visibility_icon.set_from_icon_name(info[1])
            self.visibility_icon.set_tooltip_text(info[0])
            self.visibility_icon.update_property(
                [Gtk.AccessibleProperty.LABEL], [info[0]])
        language = status.get('language')
        show_language = bool(language) \
            and language.lower().split('-')[0] != ui_language()
        self.language_label.set_visible(show_language)
        if show_language:
            self.language_label.set_text(language.upper())
            self.language_label.set_tooltip_text(
                _('Language: %s') % language)
        edited = status.get('edited_at')
        self.edited_button.set_visible(bool(edited))
        if edited:
            tip = _('Edited %s') % absolute_time(edited)
            self.edited_button.set_tooltip_text(tip)
            self.edited_button.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [_('Edited. Show edit history')])
        self.pinned_badge.set_visible(bool(status.get('pinned')))

    def _bind_reply_line(self, item):
        status = item.status
        target = status.get('in_reply_to_account_id')
        show = bool(target and status.get('in_reply_to_id'))
        self.reply_box.set_visible(show)
        if not show:
            return
        if target == status['account'].get('id'):
            text = _('Replying to themselves')
        else:
            acct = next((m.get('acct') for m in status.get('mentions') or []
                         if m.get('id') == target), None)
            text = _('Replying to @%s') % acct if acct \
                else _('Replying to a post')
        self.reply_label.set_text(text)

    def _render_body(self, item, bank):
        status = item.status
        html = (item.translation or {}).get('content') \
            or status.get('content', '')
        segments = parse_content(html)
        self.content.link_mentions = status.get('mentions')
        self.content.link_tags = status.get('tags')
        self.content.set_emoji_size(
            32 if _flag('enlarge-custom-emojis') else 20)
        shown, collapsible = _collapse_view(segments)
        expanded = bool(item.ui.get('expanded'))
        if collapsible and not expanded:
            self.content.set_segments(shown, bank)
        else:
            self.content.set_segments(segments, bank)
        self.expand_button.set_visible(collapsible)
        self.expand_button.set_label(
            _('Show Less') if expanded else _('Show More'))
        self.content.set_visible(bool(segments))

    def _show_quote(self, quote):
        while (child := self.quote_box.get_first_child()) is not None:
            self.quote_box.remove(child)
        self.quote_box.set_visible(bool(quote))
        if quote:
            self.quote_box.append(QuoteCard(quote, self.client))

    def _bind_actions(self, item):
        status = item.status
        can_act = self._can_act()
        counters = _flag('show-interaction-counters')
        self._set_count(self.reply_button,
                        item.reply_count if counters else 0)
        self._set_count(self.boost_button,
                        item.boost_count if counters else 0)
        self._set_count(self.favourite_button,
                        item.favourite_count if counters else 0)
        self._set_count(self.bookmark_button, 0)
        self._set_active(self.boost_button, item.reblogged)
        self._set_active(self.favourite_button, item.favourited)
        self._set_active(self.bookmark_button, item.bookmarked)
        quotable = 'quote_approval' in status
        self.quote_button.set_visible(quotable)
        if quotable:
            self._set_count(self.quote_button,
                            (status.get('quotes_count') or 0)
                            if counters else 0)
            allowed = can_quote(status)
            self.quote_button.set_sensitive(can_act and allowed)
            self.quote_button.set_tooltip_text(
                _('Quote') if allowed else _('This post can’t be quoted'))
        boostable = item.reblogged or self.can_be_boosted(item)
        self.boost_button.set_tooltip_text(
            _('Boost') if boostable else _('This post can’t be boosted'))
        self.favourite_button.set_sensitive(can_act and not self._busy)
        self.bookmark_button.set_sensitive(can_act and not self._busy)
        self.boost_button.set_sensitive(
            can_act and boostable and not self._busy)

    def can_be_boosted(self, item):
        visibility = item.status.get('visibility')
        return visibility != 'direct' and (
            visibility != 'private' or self._is_own())

    def _sync_menu_state(self, item):
        status = item.status
        can_act = self._can_act()
        self._menu_actions['open-browser'].set_enabled(
            bool(item.status_url))
        self._menu_actions['copy'].set_enabled(bool(item.status_url))
        for name in ('mute', 'block', 'report', 'mention'):
            self._menu_actions[name].set_enabled(can_act)
        self._menu_actions['quote-policy'].set_state(
            GLib.Variant.new_string(quote_policy_of(status)))

    def _set_time(self, item):
        self.time_label.set_text(item.timestamp)
        created = GLib.DateTime.new_from_iso8601(
            item.status.get('created_at') or '', None)
        full = created.to_local().format('%c') if created else None
        self.time_label.set_tooltip_text(full)
        if full:
            self.time_label.update_property([Gtk.AccessibleProperty.LABEL],
                                            [full])
        # A continuation post of a run card leaves the time to the lead;
        # it can rebind on its own when the post is edited.
        self.time_label.set_visible(not self.has_css_class('run-post'))

    def _set_post_meta(self, item):
        """Phanpy-style line under the body: only the visibility and the
        relative time; the absolute date stays in the tooltip."""
        self.post_meta.set_visible(True)
        status = item.status
        visibility = VISIBILITY_INFO.get(status.get('visibility'),
                                       VISIBILITY_INFO['public'])[0]
        absolute = absolute_time(status.get('created_at'))
        self.post_meta.set_label('%s · %s' % (visibility, item.timestamp)
                                 if item.timestamp else visibility)
        self.post_meta.set_tooltip_text(absolute or None)

    def _bind_carousel(self, item, client):
        for widget in (self.boost_strip, self.filter_bar, self.main):
            if widget is not None:
                widget.set_visible(False)
        self.update_property([Gtk.AccessibleProperty.LABEL], [_('Boosts')])
        carousel = self._ensure_carousel()
        carousel.set_visible(True)
        carousel.set_boosts(item.boosts, client)

    def _on_item_changed(self, item):
        if item is self.item:
            self.bind(item, self.client)

    def _on_show_filtered(self, _button):
        if self.item is None:
            return
        self.item.ui['filter_shown'] = True
        self.bind(self.item, self.client)

    def _update_cw_toggle(self, spoiler, revealed):
        self.cw_toggle.set_label(_('Hide Content') if revealed
                                 else _('Show Content'))
        self.cw_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Hide content warning: %s') % spoiler if revealed
             else _('Show content warning: %s') % spoiler])

    def _on_cw_toggle(self, _button):
        item = self.item
        if item is None:
            return
        current = item.ui.get('cw_open')
        if current is None:
            current = _flag('show-spoilers')
        item.ui['cw_open'] = not current
        spoiler = (item.translation or {}).get('spoiler_text') \
            or item.status.get('spoiler_text') or ''
        self._update_cw_toggle(spoiler, not current)
        pages.reveal(self.guarded_revealer, not current, animate=True)

    def _on_expand(self, _button):
        item = self.item
        if item is None:
            return
        content = self.content
        h0 = content.get_height()
        animate = h0 > 0 and content.get_mapped()
        self._stop_body_animation()
        item.ui['expanded'] = not item.ui.get('expanded')
        self._render_body(item, self._emoji_bank)
        if not animate:
            return
        # The text view lays the new text out before the next frame;
        # measured now it still answers with the old height. Hold the
        # old height until then and size the animation from the layout.
        self._body_overflow = content.get_overflow()
        content.set_overflow(Gtk.Overflow.HIDDEN)
        content._height_override = h0
        self._body_tick = content.add_tick_callback(
            pages.weak_method(self._on_body_laid_out), h0)

    def _on_body_laid_out(self, content, _clock, h0):
        self._body_tick = None
        h1 = Gtk.TextView.do_measure(
            content, Gtk.Orientation.VERTICAL, content.get_width())[1]
        if abs(h1 - h0) < 4:
            self._clear_body_height()
            return GLib.SOURCE_REMOVE
        animation = Adw.TimedAnimation(
            widget=content, value_from=h0, value_to=h1, duration=250,
            easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.CallbackAnimationTarget.new(
                pages.weak_method(self._on_body_height)))
        connect_weak(animation, 'done', self._on_body_animation_done)
        self._body_animation = animation
        animation.play()
        return GLib.SOURCE_REMOVE

    def _on_body_height(self, value):
        self.content._height_override = round(value)
        self.content.queue_resize()

    def _on_body_animation_done(self, _animation):
        self._body_animation = None
        self._clear_body_height()

    def _clear_body_height(self):
        content = self.content
        if content._height_override is not None:
            content._height_override = None
            content.set_overflow(self._body_overflow)
            content.queue_resize()

    def _stop_body_animation(self):
        if self._body_tick is not None:
            self.content.remove_tick_callback(self._body_tick)
            self._body_tick = None
        animation = self._body_animation
        if animation is not None:
            self._body_animation = None
            animation.skip()
        self._clear_body_height()

    def _show_card(self, card):
        while (child := self.card_box.get_first_child()) is not None:
            self.card_box.remove(child)
        if not card or not (card.get('title') or card.get('description')):
            self.card_box.set_visible(False)
            return
        self.card_box.set_visible(True)
        self.card_box.append(CardPreview(card))

    def _set_active(self, button, active):
        button.set_active(active)
        child = button.get_child()
        child.set_icon_name(button.icons[bool(active)])
        if button is self.boost_button:
            # add/remove rather than set_css_classes: the icon may
            # carry other classes on it.
            if active:
                child.add_css_class('boost-accent')
            else:
                child.remove_css_class('boost-accent')

    def _set_count(self, button, count):
        """Show *count* next to the icon and name it in the accessible
        label, so the button reads as 'Boost, 12 boosts'."""
        button.get_child().set_label(str(count) if count else '')
        label = button.action_name_text
        if count and button.nouns:
            label = '%s, %s' % (label, ngettext(
                button.nouns[0], button.nouns[1], count) % count)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    # Actions

    def _on_reply(self, _button):
        item = self.item
        if item is None:
            return
        window = pages.window_of(self)
        if window is None or not hasattr(window, 'open_compose_reply'):
            self.activate_status(item)
            return
        status = item.status

        def reply():
            window.open_compose_reply(
                item.status_id, status['account'].get('acct', ''),
                status.get('visibility') or 'public')

        age = days_since(status.get('created_at'))
        if age > OLD_POST_DAYS and _flag('reply-to-old-post-reminder'):
            self._confirm_old_reply(age, reply)
        else:
            reply()

    def _confirm_old_reply(self, days, reply):
        dialog = Adw.AlertDialog(
            heading=_('Reply to an Old Post?'),
            body=_('This post is %(age)s old. You can still reply, but it '
                   'may no longer be relevant.') % {
                'age': ngettext('%d day', '%d days', days) % days})
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('never', _('_Don’t Remind Me Again'))
        dialog.add_response('reply', _('_Reply'))
        dialog.set_response_appearance('reply',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('reply')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response == 'never':
                pages.settings().set_boolean('reply-to-old-post-reminder',
                                             False)
            if response in ('never', 'reply'):
                reply()

        dialog.connect('response', on_response)
        dialog.present(self)

    def _on_quote(self, _button):
        if self.item is not None:
            self._open_composer('start_quote', self.item.status)

    def _on_profile_clicked(self, _button):
        if self.item is not None:
            self.activate_profile(self.item)

    def _merge_status(self, item, data):
        """Copy the fields a status action changes into *item*."""
        if not isinstance(data, dict):
            return
        fresh = data.get('reblog') or data
        if fresh.get('id') != item.status_id:
            return
        for key in MERGE_KEYS:
            if key in fresh:
                item.status[key] = fresh[key]

    def _set_busy(self, busy):
        self._busy = busy
        if self.item is not None and not isinstance(
                self.item, BoostCarouselItem):
            can_act = self._can_act()
            self.favourite_button.set_sensitive(can_act and not busy)
            self.bookmark_button.set_sensitive(can_act and not busy)
            self.boost_button.set_sensitive(
                can_act and not busy
                and (self.item.reblogged or self.can_be_boosted(self.item)))

    def _send_toggle(self, kind, active, form=None):
        """Set favourite/reblog/bookmark to *active* with the matching
        do/undo endpoint, optimistic with rollback on failure."""
        item, client = self.item, self.client
        flag = {'favourite': 'favourited', 'reblog': 'reblogged',
                'bookmark': 'bookmarked'}[kind]
        count_key = {'favourite': 'favourites_count',
                     'reblog': 'reblogs_count'}.get(kind)
        verb = kind if active else {'favourite': 'unfavourite',
                                    'reblog': 'unreblog',
                                    'bookmark': 'unbookmark'}[kind]
        previous = {flag: item.status.get(flag)}
        item.status[flag] = active
        if count_key:
            previous[count_key] = item.status.get(count_key)
            item.status[count_key] = max(
                0, (item.status.get(count_key) or 0) + (1 if active else -1))
        self._set_busy(True)
        self.bind(item, client)

        def finished(_client, result, _data=None):
            self._set_busy(False)
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                item.status.update(previous)
                pages.report_error(self, _('Action failed'), error)
            else:
                self._merge_status(item, data)
            if self.item is item:
                self.bind(item, client)

        client.request_async(
            'POST', '/statuses/%s/%s' % (item.status_id, verb), form=form,
            callback=finished)

    def _on_favourite(self, button):
        item = self.item
        self._set_active(button, item.favourited)
        if not item.favourited:
            pages.flash_class(button, 'just-toggled')
        self._send_toggle('favourite', not item.favourited)

    def _on_bookmark(self, button):
        item = self.item
        self._set_active(button, item.bookmarked)
        if not item.bookmarked:
            pages.flash_class(button, 'just-toggled')
        self._send_toggle('bookmark', not item.bookmarked)

    def _missing_alt(self):
        return any(
            a.get('type') in ('image', 'video', 'gifv', 'audio')
            and not a.get('description')
            for a in self.item.status.get('media_attachments') or [])

    def _on_boost(self, button):
        item = self.item
        # The toggle flipped itself; the request decides the real state.
        self._set_active(button, item.reblogged)
        if item.reblogged:
            self._send_toggle('reblog', False)
            return

        def proceed():
            if _flag('advanced-boost-dialog'):
                self._present_boost_dialog()
            else:
                pages.flash_class(button, 'just-toggled')
                self._send_toggle('reblog', True)

        if _flag('boost-alt-text-reminder') and self._missing_alt():
            self._present_alt_reminder(proceed)
        else:
            proceed()

    def _present_alt_reminder(self, proceed):
        dialog = Adw.AlertDialog(
            heading=_('This Post Has Media Without Alt Text'),
            body=_('Some of your followers might not be able to '
                   'understand it.'))
        never = Gtk.CheckButton(label=_('Don’t _remind me again'),
                                use_underline=True, halign=Gtk.Align.CENTER)
        dialog.set_extra_child(never)
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('boost', _('_Boost'))
        dialog.set_response_appearance('boost',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if never.get_active():
                pages.settings().set_boolean('boost-alt-text-reminder',
                                             False)
            if response == 'boost':
                proceed()

        dialog.connect('response', on_response)
        dialog.present(self)

    def _present_boost_dialog(self):
        """Boost with a visibility choice and the option to quote."""
        item = self.item
        status = item.status
        original = status.get('visibility')
        allowed = {'public': ['public', 'unlisted', 'private'],
                   'unlisted': ['unlisted', 'private']}.get(
                       original, ['private'])
        descriptions = {
            'public': _('Visible for all'),
            'unlisted': _('Visible for all, but not in public timelines'),
            'private': _('Visible for your followers only')}
        preferred = pages.settings().get_string('default-visibility') \
            if _has_schema() else 'public'
        default = preferred if preferred in allowed else allowed[0]
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        checks = {}
        group = None
        for value in allowed:
            title, icon = VISIBILITY_INFO[value]
            check = Gtk.CheckButton(active=value == default,
                                    valign=Gtk.Align.CENTER)
            if group is None:
                group = check
            else:
                check.set_group(group)
            checks[value] = check
            row = Adw.ActionRow(title=title, subtitle=descriptions[value],
                                activatable_widget=check)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_prefix(check)
            listbox.append(row)
        dialog = Adw.AlertDialog(heading=_('Boost With Visibility'))
        dialog.set_extra_child(listbox)
        dialog.add_response('cancel', _('_Cancel'))
        if can_quote(status):
            dialog.add_response('quote', _('_Quote'))
        dialog.add_response('boost', _('_Boost'))
        dialog.set_response_appearance('boost',
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('boost')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response == 'boost':
                chosen = next((v for v, c in checks.items()
                               if c.get_active()), default)
                pages.flash_class(self.boost_button, 'just-toggled')
                self._send_toggle('reblog', True,
                                  form={'visibility': chosen})
            elif response == 'quote':
                self._on_quote(None)

        dialog.connect('response', on_response)
        dialog.present(self)

    def activate_status(self, item):
        window = pages.window_of(self)
        if window is None:
            return
        from .thread import push_thread
        push_thread(window.navigation_view, self.client, item)

    def activate_profile(self, item):
        window = pages.window_of(self)
        if window is None:
            return
        from .profile import push_profile
        push_profile(window.navigation_view, self.client,
                     item.status['account'])


class TimelineFactory(Gtk.SignalListItemFactory):

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.connect('setup', self.on_setup)
        self.connect('bind', self.on_bind)

    def on_setup(self, _factory, list_item):
        card = StatusRunRow()
        list_item.set_child(card)
        list_item.set_activatable(True)

    def on_bind(self, _factory, list_item):
        run = list_item.get_item()
        card = list_item.get_child()
        card.bind(run, self.client)


class ApiTimelineSource:
    """TimelinePage source over any endpoint returning a Status array.

    ``paging='id'`` passes TimelinePage's max_id/since_id through (home,
    public, tag, list, account timelines). ``paging='link'`` follows the
    response's ``Link: rel=next`` instead, for endpoints whose cursor is
    not a status id (bookmarks, favourites, trends with ``offset``).
    ``paging='none'`` loads one batch. ``extract`` maps a non-array
    response (e.g. search results) to the status list."""

    def __init__(self, client, path, params=None, paging='id',
                 extract=None):
        self.client = client
        self.path = path
        self.base_params = dict(params or {})
        self.paging = paging
        self.paginated = paging != 'none'
        self.extract = extract
        self._next = None

    def fetch(self, params, callback):
        merged = dict(self.base_params)
        if self.paging == 'id':
            merged.update(params)
        elif self.paging == 'link':
            merged['limit'] = params.get('limit', PAGE_SIZE)
            if 'max_id' in params:
                if not self._next:
                    # No older page: answer with an empty batch.
                    task = Gio.Task.new(self.client, None, callback)
                    task.return_value(GLib.Variant(
                        's', '{"data": [], "link": ""}'))
                    return
                merged.update(self._next)
            self._older_request = 'max_id' in params
        self.client.request_async('GET', self.path, params=merged,
                                  callback=callback)

    def finish(self, result):
        data, next_params, _prev = self.client.request_finish_paged(result)
        statuses = self.extract(data) if self.extract else data
        if self.paging == 'link' and (
                getattr(self, '_older_request', False) or self._next is None):
            self._next = next_params if statuses else None
        return statuses or []


def _auto_refresh_tick(page_ref):
    page = page_ref()
    if page is None:
        return GLib.SOURCE_REMOVE
    page._on_auto_refresh()
    return GLib.SOURCE_CONTINUE


class TimelinePage(Adw.Bin):
    """Scrollable timeline with infinite scroll, refresh and status pages.

    mode: 'home' (authenticated) with automatic public fallback, or
    'public'. A custom ``source`` (with fetch(params, callback) and
    finish(result)) overrides both, e.g. account statuses on profiles.
    New posts arriving through a since_id refresh are announced with a
    'N new posts' pill instead of jumping the scroll position. Automatic
    polling is off until set_auto_refresh_interval() turns it on.
    """

    __gtype_name__ = 'BabbleTimelinePage'

    def __init__(self, client, mode='home', model=None, source=None,
                 **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.mode = mode
        self.model = model if model is not None else TimelineModel()
        self.source = source
        self._loading_older = False
        self._fallback_done = False
        self._auto_refresh_id = 0
        self._auto_refresh_ticks = 0
        self._pill_count = 0

        self.stack = Gtk.Stack()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.run_model = RunModel(self.model)
        self.list_view = Gtk.ListView(model=Gtk.NoSelection.new(self.run_model),
                                      factory=TimelineFactory(client),
                                      single_click_activate=True)
        self.list_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Posts')])
        connect_weak(self.list_view, 'activate', self.on_row_activated)
        # Phanpy-style: a single centered column with grey gutters. The
        # cap is COLUMN_WIDTH in sp, the clamp's default unit, so it is
        # 660px at the default text size and follows the text-scaling
        # factor. The clamp is the scrollable, so the column follows the
        # viewport and never raises the page's minimum width.
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        scrolled.set_child(clamp)
        scrolled.add_css_class('timeline-list')

        self.new_posts_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.new_posts_revealer.set_halign(Gtk.Align.CENTER)
        self.new_posts_revealer.set_valign(Gtk.Align.START)
        self.new_posts_revealer.set_margin_top(6)
        self.new_pill = Gtk.Button()
        self.new_pill.add_css_class('pill')
        self.new_pill.add_css_class('suggested-action')
        connect_weak(self.new_pill, 'clicked', self._on_new_pill_clicked)
        self.new_posts_revealer.set_child(self.new_pill)
        self.new_posts_revealer.set_reveal_child(False)

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(scrolled)
        self.overlay.add_overlay(self.new_posts_revealer)

        self.stack.add_named(self.overlay, 'list')
        self.set_child(self.stack)

        self.loading_page = pages.make_loading_page(
            _('Loading Timeline'), _('Fetching the latest posts'))
        self.error_page = pages.make_error_page(
            _('Could Not Load Timeline'), self._on_retry)
        self.empty_page = Adw.StatusPage(
            icon_name='user-home-symbolic',
            title=_('No Posts Yet'),
            description=_('New posts will appear here'))
        self.stack.add_named(self.loading_page, 'loading')
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(self.empty_page, 'empty')

        _RefreshGesture(self, scrolled)
        self._scrolled = scrolled
        adjustment = scrolled.get_vadjustment()
        connect_weak(adjustment, 'value-changed', self.on_scroll_position)
        self.load()

    # New posts pill / auto refresh

    def set_auto_refresh_interval(self, seconds):
        """Poll for new posts every *seconds* while the page is shown;
        0 turns polling off."""
        if self._auto_refresh_id:
            GLib.source_remove(self._auto_refresh_id)
            self._auto_refresh_id = 0
        self._auto_refresh_ticks = 0
        if seconds > 0:
            self._auto_refresh_id = GLib.timeout_add_seconds(
                seconds, _auto_refresh_tick, self.weak_ref())

    def _on_auto_refresh(self):
        if not self.get_mapped() or self.model.loading:
            return
        client = getattr(self, 'client', None)
        if client is not None and client.rate_limited():
            # A 429 is still in force; polling into it would only deepen
            # the limit the server just asked us to respect.
            return
        self._auto_refresh_ticks += 1
        at_top = self._scrolled.get_vadjustment().get_value() <= 0.0
        if at_top or self._auto_refresh_ticks \
                % AUTO_REFRESH_SCROLLED_TICKS == 0:
            self.refresh()

    def scroll_to_top(self):
        self._scrolled.get_vadjustment().set_value(0.0)

    def _show_new_pill(self, count):
        self._pill_count += count
        self.new_pill.set_label(
            ngettext('%d New Post', '%d New Posts', self._pill_count)
            % self._pill_count)
        self.new_posts_revealer.set_reveal_child(True)

    def _on_new_pill_clicked(self, _button):
        self._pill_count = 0
        self.new_posts_revealer.set_reveal_child(False)
        self.scroll_to_top()

    # Boosts carousel data

    def _update_boosts(self):
        """Refresh the boosts carousel row from the loaded window."""
        boosts = []
        seen = set()
        for index in range(min(self.model.get_n_items(), BOOST_WINDOW)):
            item = self.model.get_item(index)
            if isinstance(item, BoostCarouselItem):
                continue
            if not item.is_boost:
                continue
            key = item.status_id
            if key in seen:
                continue
            seen.add(key)
            boosts.append(item.status)
            if len(boosts) >= BOOST_CAP:
                break
        self.model.set_boosts(boosts or None)

    # Data

    def _fetch(self, params, callback):
        if self.source is not None:
            self.source.fetch(params, callback)
            return
        if self.mode == 'public':
            self.client.timelines_public_async(params=params,
                                               callback=callback)
        else:
            self.client.timelines_home_async(params=params, callback=callback)

    def _finish(self, result):
        if self.source is not None:
            return self.source.finish(result)
        if self.mode == 'public':
            return self.client.timelines_public_finish(result)
        return self.client.timelines_home_finish(result)

    def _on_retry(self, _button):
        self.load()

    def load(self):
        if self.model.loading:
            return
        self.model.loading = True
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('loading')
        self._fetch({'limit': PAGE_SIZE}, self.on_page_loaded)

    def on_page_loaded(self, client, result, _user_data=None):
        self.model.loading = False
        try:
            statuses = self._finish(result)
        except GLib.Error as e:
            if self.mode == 'home' and self.source is None \
                    and not self._fallback_done:
                # Unauthenticated or private home timeline: fall back to
                # the public timeline through the same rendering path.
                self._fallback_done = True
                self.mode = 'public'
                self.load()
                return
            self.error_page.set_description(
                summarize_error_markup(e.message))
            self.stack.set_visible_child_name('error')
            return
        if not statuses:
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
            else:
                self.model.exhausted = True
            return
        if self.model.get_n_items() == 0:
            self.model.append_old(statuses)
            self.model.oldest_id = min(s['id'] for s in statuses)
            self.model.newest_id = max(s['id'] for s in statuses)
        else:
            added = self.model.insert_new(statuses)
            if added and self._scrolled.get_vadjustment().get_value() > 24:
                self._show_new_pill(added)
        if self.source is not None and not self.source.paginated:
            self.model.exhausted = True
        if self.model.get_n_items() == 0:
            # Every post of this page was hidden by a filter: keep paging.
            if self.model.exhausted:
                self.stack.set_visible_child_name('empty')
            else:
                self.stack.set_visible_child_name('loading')
                self.load_older()
            return
        self._update_boosts()
        self.stack.set_visible_child_name('list')

    def load_older(self):
        if self._loading_older or self.model.exhausted \
                or self.model.oldest_id is None:
            return
        self._loading_older = True
        self._fetch({'limit': PAGE_SIZE, 'max_id': self.model.oldest_id},
                    self.on_older_loaded)

    def _trim_old(self):
        """Keep the timeline to TIMELINE_MAX_ITEMS while the reader is on
        its first screen, so the dropped tail is far out of sight. Only
        id-paged timelines: a Link cursor cannot be moved back, and an
        unpaginated source could never fetch the dropped posts again.
        An older page in flight would land after the gap, so wait."""
        if self._loading_older or (
                self.source is not None
                and getattr(self.source, 'paging', None) != 'id'):
            return
        adjustment = self._scrolled.get_vadjustment()
        if adjustment.get_value() > adjustment.get_page_size():
            return
        self.model.trim(TIMELINE_MAX_ITEMS)

    def on_older_loaded(self, client, result, _user_data=None):
        self._loading_older = False
        try:
            statuses = self._finish(result)
        except GLib.Error:
            return
        if not self.model.append_old(statuses):
            self.model.exhausted = True
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')
        else:
            self._update_boosts()
            if self.model.get_n_items():
                self.stack.set_visible_child_name('list')
            else:
                self.load_older()

    def refresh(self):
        """Load the posts newer than the newest one shown."""
        if self.model.loading:
            return
        if self.model.get_n_items() == 0:
            self.load()
            return
        self.model.exhausted = False
        params = {'limit': PAGE_SIZE}
        if self.model.newest_id is not None:
            params['since_id'] = self.model.newest_id
        self.model.loading = True
        self._fetch(params, self.on_refresh_loaded)

    def on_refresh_loaded(self, client, result, _user_data=None):
        self.model.loading = False
        try:
            statuses = self._finish(result)
        except GLib.Error as e:
            root = self.get_root()
            if root is not None and hasattr(root, 'show_error'):
                root.show_error(_('Refresh failed'), e.message)
            return
        if statuses:
            added = self.model.insert_new(statuses)
            if added and self._scrolled.get_vadjustment().get_value() > 24:
                self._show_new_pill(added)
            self._trim_old()
        self._update_boosts()
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('empty')

    def on_row_activated(self, _view, position):
        run = self.run_model.get_item(position)
        item = None if run is None else run.items[0]
        if item is None or isinstance(item, BoostCarouselItem):
            return
        from .thread import push_thread
        view = getattr(self.get_root(), 'navigation_view', None)
        if view is not None:
            push_thread(view, self.client, item)

    def on_scroll_position(self, adjustment):
        upper = adjustment.get_upper()
        bottom = adjustment.get_value() + adjustment.get_page_size()
        if bottom >= upper - 800:
            self.load_older()
        if adjustment.get_value() <= 0.0:
            self._pill_count = 0
            self.new_posts_revealer.set_reveal_child(False)
            self._trim_old()

    # Streaming hooks (driven by the shell's streaming connection)

    def on_stream_update(self, status):
        """Insert one streamed post at the top, the way a refresh would:
        filters apply and a scrolled-away reader gets the new-posts
        pill instead of a jump."""
        added = self.model.insert_new([status])
        if self.model.oldest_id is None:
            self.model.oldest_id = status['id']
        if not added:
            return
        if self._scrolled.get_vadjustment().get_value() > 24:
            self._show_new_pill(added)
        self._trim_old()
        self._update_boosts()
        self.stack.set_visible_child_name('list')

    def on_stream_delete(self, status_id):
        """Drop a deleted post (and boosts of it) from the list."""
        if self.model.remove_status(status_id):
            self._update_boosts()
            if self.model.get_n_items() == 0:
                self.stack.set_visible_child_name('empty')

    def on_stream_status_update(self, status):
        """Replace an edited post in place."""
        if self.model.replace_status(status):
            self._update_boosts()


class _RefreshGesture:
    """Pull down at the top of the list to refresh (Phanpy-style)."""

    def __init__(self, page, scrolled):
        # Held weakly: the controller lives on the scrolled window inside
        # the page, so strong references here would keep the page alive
        # through the handler. A GObject weak reference, because PyGObject
        # re-creates the page's Python wrapper while GTK alone holds it.
        self._page = page.weak_ref()
        controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL)
        controller.connect('scroll', self.on_scroll)
        scrolled.add_controller(controller)

    def on_scroll(self, controller, _dx, dy):
        page = self._page()
        adjustment = controller.get_widget().get_vadjustment()
        if page is None or adjustment is None:
            return False
        at_top = adjustment.get_value() <= 0.0
        if at_top and dy < 0:
            page.refresh()
        return False
