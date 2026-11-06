# SPDX-License-Identifier: GPL-3.0-or-later
"""Notifications view: grouped notification rows with embedded post
snippets, all/mentions filtering, infinite scroll and an unread badge
signal consumed by the window sidebar."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, GLib, Gio, GObject, Gtk  # noqa: E402
import datetime  # noqa: E402
import re  # noqa: E402
import time as _time  # noqa: E402

from .mastodon import summarize_error_markup  # noqa: E402
from . import pages  # noqa: E402
from .api import notifications as api_notifications  # noqa: E402
from .pages import (PagedList, PagedPosts, connect_weak,  # noqa: E402
                    launch_external,
                    push_page, report_error, settings, show_toast)
from .notificationcopy import (FILTER_NOTIFICATION_TYPES,  # noqa: E402
                               LIST_TITLE_DEFAULT, LIST_TITLES,
                               type_offered)
from .notificationctrl import (NotificationController,  # noqa: E402
                               filtering_supported)
from .entities import lift
from .timelinedata import StatusItem  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402
from .timelinepage import _RefreshGesture  # noqa: E402
from .statustext import parse_content, relative_time  # noqa: E402
from .timelinebase import COLUMN_WIDTH, id_key, status_filter  # noqa: E402
from . import filterrules  # noqa: E402


# Icon for each Mastodon notification type; unknown types fall back to
# a generic interaction icon.
TYPE_ICONS = {
    'favourite': 'starred-symbolic',
    'reblog': 'send-to-symbolic',
    'follow': 'contact-new-symbolic',
    'follow_request': 'avatar-default-symbolic',
    'mention': 'chat-message-new-symbolic',
    'poll': 'document-open-recent-symbolic',
    'update': 'document-edit-symbolic',
    'status': 'user-available-symbolic',
    'quote': 'mail-reply-sender-symbolic',
    'quoted_update': 'document-edit-symbolic',
    'admin.sign_up': 'user-info-symbolic',
    'admin.report': 'emoji-flags-symbolic',
    'severed_relationships': 'network-wired-disconnected-symbolic',
    'moderation_warning': 'dialog-warning-symbolic',
    'annual_report': 'emoji-symbols-symbolic',
    'added_to_collection': 'list-add-symbolic',
    'collection_update': 'view-list-symbolic',
}

# One actor: '%s'. Two actors: names joined. More: two names plus
# ngettext-chosen 'other(s)' count. Types with their own wording
# (reports, severed relationships, warnings, annual reports) are
# handled by _special_title.
TEMPLATES = LIST_TITLES
DEFAULT_TEMPLATE = LIST_TITLE_DEFAULT

# Types that never fold into a cluster: each one carries its own
# per-notification actions or payload.
UNGROUPED_TYPES = frozenset({
    'follow_request', 'admin.report', 'severed_relationships',
    'moderation_warning', 'annual_report',
    'added_to_collection', 'collection_update'})


EXCLUDED_TYPES_KEY = 'notification-excluded-types'

# (type, label) entries of the header Filter submenu and the settings
# dialog.
NOTIFICATION_TYPES = FILTER_NOTIFICATION_TYPES

# Menu-only labels: SwitchRow titles keep the unadorned strings above.
_FILTER_MENU_LABELS = {
    'mention': _('_Mentions'),
    'status': _('_New Posts'),
    'reblog': _('_Boosts'),
    'favourite': _('_Favorites'),
    'follow': _('Foll_ows'),
    'follow_request': _('Follow _Requests'),
    'poll': _('_Polls'),
    'update': _('Post _Edits'),
    'quote': _('_Quotes'),
    'quoted_update': _('Quote E_dits'),
    'admin.sign_up': _('_Sign-Ups'),
    'admin.report': _('Repor_ts'),
    'severed_relationships': _('Se_vered Relationships'),
    'moderation_warning': _('Moderation _Warnings'),
    'annual_report': _('_Annual Reports'),
    'added_to_collection': _('Added to _Collections'),
    'collection_update': _('Collection _Updates'),
}

REPORT_CATEGORIES = {
    'spam': _('Spam'),
    'legal': _('Illegal content'),
    'violation': _('Server rule violation'),
    'other': _('Other'),
}

# Account warnings, worded like Mastodon's web interface.
WARNING_TEXTS = {
    'none': _('You have received a warning.'),
    'disable': _('Your account has been frozen.'),
    'mark_statuses_as_sensitive':
        _('Some of your posts have been marked as sensitive.'),
    'delete_statuses': _('Some of your posts have been removed.'),
    'sensitive': _('All of your posts will be marked as sensitive from '
                   'now on.'),
    'silence': _('Your account has been limited.'),
    'suspend': _('Your account has been suspended.'),
}


def _actor_name(account):
    return account.get('display_name') or account.get('acct') or ''


# Title labels stay emoji-free like the bar: display names often carry
# decorative pictographs ('Mastodon Toot 🦣') that add raw-glyph noise
# to the grouped summary line.
_EMOJI_RE = re.compile(
    '[\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff\u2600-\u27bf\u2b00-'
    '\u2bff\ufe0f\u200d\u20e3\u2b50\u2049\u3299]+')


def _clean_name(account):
    plain = _EMOJI_RE.sub('', _actor_name(account)).strip()
    return plain or _actor_name(account) or '?'


def _subject(actors):
    """Multi-actor clusters read like the bar ('2 people boosted and
    liked your post'); the named detail lives behind the expander."""
    if len(actors) <= 1:
        return _clean_name(actors[0]) if actors else '?'
    count = len(actors)
    return ngettext('%d person', '%d people', count) % count


def _special_title(item):
    """Wording for the types that do not follow '<actors> <verb>'."""
    payload = item.payload
    kind = item.kind
    if kind == 'admin.report':
        target = (payload.get('report') or {}).get('target_account')
        if target:
            return _('%(account)s reported %(target)s') % {
                'account': _subject(item.actors),
                'target': _clean_name(target)}
        return _('%s filed a report') % _subject(item.actors)
    if kind == 'severed_relationships':
        name = (payload.get('relationship_severance_event') or {}) \
            .get('target_name')
        if name:
            return _('Relationships severed with %s') % name
        return _('Relationships severed')
    if kind == 'moderation_warning':
        return _('Moderation warning')
    if kind == 'annual_report':
        year = (payload.get('annual_report') or {}).get('year')
        if year:
            return _('Your %s #Wrapstodon is here!') % year
        return _('Your #Wrapstodon is here!')
    if kind in ('added_to_collection', 'collection_update'):
        name = (payload.get('collection') or {}).get('name')
        if name:
            if kind == 'added_to_collection':
                return _('%(actor)s added you to %(collection)s') % {
                    'actor': _subject(item.actors), 'collection': name}
            return _('%(actor)s updated %(collection)s') % {
                'actor': _subject(item.actors), 'collection': name}
    return None


def notification_title(item):
    """Grouped one-line summary: 'X favorited your post', '2 people
    boosted and favorited your post'. Cross-type clusters on the same status
    collapse like the bar; per-person detail sits behind the row's
    expander."""
    kinds = set(item.kinds)
    if len(kinds) == 1:
        special = _special_title(item)
        if special is not None:
            return special
        template = TEMPLATES.get(item.kind, DEFAULT_TEMPLATE)
    elif kinds == {'favourite', 'reblog'}:
        template = _('%s boosted and favorited your post')
    else:
        template = _('%s interacted with your post')
    return template % _subject(item.actors)


def _instance_domain(instance):
    return (instance or '').removeprefix('https://') \
        .removeprefix('http://').rstrip('/')


def notification_detail(item, instance=None):
    """Explanatory body text for the types that carry one (reports,
    severed relationships, moderation warnings); None otherwise."""
    payload = item.payload
    kind = item.kind
    if kind == 'admin.report':
        report = payload.get('report') or {}
        lines = []
        category = report.get('category')
        if category:
            lines.append(_('Category: %s')
                         % REPORT_CATEGORIES.get(category, category))
        comment = (report.get('comment') or '').strip()
        if comment:
            lines.append(comment)
        return '\n'.join(lines) or None
    if kind == 'severed_relationships':
        event = payload.get('relationship_severance_event') or {}
        data = {'instance': _instance_domain(instance),
                'target': event.get('target_name') or '?',
                'count': event.get('relationships_count') or 0}
        kind_of_event = event.get('type')
        if kind_of_event == 'account_suspension':
            return _('An admin of %(instance)s suspended %(target)s. You '
                     'can no longer receive updates from them or interact '
                     'with them.') % data
        if kind_of_event == 'user_domain_block':
            return ngettext(
                'You blocked %(target)s, removing %(count)d of your '
                'followers and followed accounts.',
                'You blocked %(target)s, removing %(count)d of your '
                'followers and followed accounts.',
                data['count']) % data
        return ngettext(
            'An admin of %(instance)s blocked %(target)s, removing '
            '%(count)d of your followers and followed accounts.',
            'An admin of %(instance)s blocked %(target)s, removing '
            '%(count)d of your followers and followed accounts.',
            data['count']) % data
    if kind == 'moderation_warning':
        warning = payload.get('moderation_warning') or {}
        lines = [WARNING_TEXTS.get(warning.get('action'),
                                   WARNING_TEXTS['none'])]
        text = (warning.get('text') or '').strip()
        if text:
            lines.append(text)
        return '\n'.join(lines)
    if kind in ('added_to_collection', 'collection_update'):
        collection = payload.get('collection') or {}
        description = (collection.get('description') or '').strip()
        if collection.get('sensitive') and description:
            return _('Sensitive collection') + '\n' + description
        return description or None
    return None


def notification_links(item, instance):
    """[(label, uri)] web pages that explain a notification."""
    base = (instance or '').rstrip('/')
    payload = item.payload
    if item.kind == 'severed_relationships':
        return [(_('View Details'), base + '/severed_relationships')]
    if item.kind == 'moderation_warning':
        warning = payload.get('moderation_warning') or {}
        if warning.get('id'):
            return [(_('Learn More'),
                     '%s/disputes/strikes/%s' % (base, warning['id']))]
    return []


def type_icon(kind):
    return TYPE_ICONS.get(kind, 'user-info-symbolic')


def _date_key_of(notification):
    """Local calendar date (YYYY-MM-DD) of the notification, the
    section key for the date group headings."""
    try:
        day = datetime.datetime.fromisoformat(
            notification['created_at'].replace('Z', '+00:00'))
    except (KeyError, ValueError):
        return '????-??-??'
    if day.tzinfo is not None:
        day = day.astimezone(datetime.datetime.now().astimezone().tzinfo)
    return day.strftime('%Y-%m-%d')

_WEEKDAYS = [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'),
             _('Friday'), _('Saturday'), _('Sunday')]


def day_heading(date_key):
    """The heading of the day *date_key* names: (title, weekday).

    The heading belongs to the section, not to the row: ``Gtk.ListHeader``
    is bound with the first item of its section, so the header factory
    asks this when it draws, instead of the model stamping every row on
    every change. An unparsable key gets an empty title and no weekday.
    """
    today = datetime.date.today()
    try:
        day = _time.strptime(date_key, '%Y-%m-%d')
        date = datetime.date(*day[:3])
    except ValueError:
        return '', ''
    if date == today:
        title = _('Today')
    elif date == today - datetime.timedelta(days=1):
        title = _('Yesterday')
    else:
        title = _('%(month)s %(day)d') % {
            'month': date.strftime('%b'), 'day': day.tm_mday}
    return title, _WEEKDAYS[day.tm_wday]


def group_key_of(notification):
    """Cluster key: the embedded status id joins every interaction type
    on the same post; status-less types (follows, signups) cluster only
    among themselves; UNGROUPED_TYPES never cluster."""
    kind = notification.get('type', '')
    if kind in UNGROUPED_TYPES:
        return ('n', notification.get('id'))
    status = notification.get('status')
    return ('s', status['id']) if status else ('t', kind)


class NotificationItem(GObject.Object):
    """One row's data: a notification, merged with consecutive
    same-status (cross-type) or same-type notifications into a group of
    actors."""

    __gtype_name__ = 'BabbleNotificationItem'

    def __init__(self, notification):
        super().__init__()
        # Entities at the boundary: the payload and every Account inside
        # it are typed once; the read sites downstream keep working.
        notification = lift(notification)
        self.kind = notification.type
        self._kinds_by_actor = [self._kind]
        self._date_keys = [_date_key_of(notification)]
        self._group_key = group_key_of(notification)
        self.actors = [notification.account]
        self.notifications = [notification]
        self.request_state = None
        self.status = notification.status
        if self.status is not None:
            self._status_item = StatusItem(self.status)
        else:
            self._status_item = None

    @property
    def payload(self):
        """The first notification of the row: unmergeable types carry
        their extra object (report, severance event…) there."""
        return self.notifications[0]

    @GObject.Property(type=str)
    def kind(self):
        """The head kind of the group; mixed clusters keep their first
        kind for the fall-back/colour classes."""
        return self._kind

    @kind.setter
    def kind(self, value):
        self._kind = value

    @property
    def kinds(self):
        return self._kinds_by_actor

    def _recompute_date_key(self):
        """After a merge the cluster's date comes from its newest
        notification."""
        self._date_keys.pop(0)
        self._date_keys.insert(0, _date_key_of(self._newest()))

    @property
    def date_key(self):
        return self._date_keys[0]

    @GObject.Property(type=GObject.TYPE_PYOBJECT)
    def actors(self):
        return self._actors

    @actors.setter
    def actors(self, value):
        self._actors = value

    @GObject.Property(type=str)
    def title(self):
        return notification_title(self)

    @GObject.Property(type=str)
    def timestamp(self):
        return relative_time(self._newest()['created_at'])

    @GObject.Property(type=GObject.TYPE_PYOBJECT)
    def status_item(self):
        return self._status_item

    @property
    def group_key(self):
        """Model cluster key: see :func:`group_key_of`."""
        return self._group_key

    @property
    def notification_id(self):
        return self._newest()['id']

    def _newest(self):
        return max(self.notifications, key=lambda n: id_key(n['id']))

    def merge(self, notification):
        """Fold another notification (same status/type cluster) into
        this row. Typed the moment it arrives, so the Account inside is
        the same object the row's own payload holds."""
        notification = lift(notification)
        self.notifications.append(notification)
        self._kinds_by_actor.append(notification.type)
        self._date_keys.append(_date_key_of(notification))
        self.actors = self.actors + [notification.account]
        self._recompute_date_key()
        self.notify('title')
        self.notify('timestamp')


class NotificationModel(GObject.Object, Gio.ListModel, Gtk.SectionModel):
    """GListModel of NotificationItem; consecutive pages merge into
    clusters sharing the same embedded status (any interaction type, so
    a reblog + favourite on one post is ONE row) or, for status-less
    types, the same type. Also a Gtk.SectionModel: one section per
    calendar day so the list renders date group headings."""

    __gtype_name__ = 'BabbleNotificationModel'

    def __init__(self):
        super().__init__()
        self._store = Gio.ListStore.new(NotificationItem)
        self._seen = set()
        self.oldest_id = None
        self.newest_id = None
        self.exhausted = False
        self.loading = False
        connect_weak(self._store, 'items-changed', self._on_store_changed)

    def _on_store_changed(self, _store, position, removed, added):
        self.items_changed(position, removed, added)

    def do_get_n_items(self):
        return self._store.get_n_items()

    def do_get_item(self, position):
        return self._store.get_item(position)

    def do_get_item_type(self):
        return NotificationItem

    def do_get_section(self, position):
        """Gtk.SectionModel: [start, end) of the calendar day that owns
        ``position``. Out-of-range positions get the documented
        ``[n_items, G_MAXUINT)`` range instead of an IndexError."""
        n = self._store.get_n_items()
        if position >= n:
            return (n, GLib.MAXUINT)
        end = position
        key = self._store[position].date_key
        while end < n and self._store[end].date_key == key:
            end += 1
        start = position
        while start > 0 and self._store[start - 1].date_key == key:
            start -= 1
        return (start, end)

    def _runs(self, fresh):
        """Group a newest-first page into runs of consecutive matching
        notifications: [group_key, [newest, ..., oldest]]."""
        runs = []
        for n in fresh:
            key = group_key_of(n)
            if runs and runs[-1][0] == key:
                runs[-1][1].append(n)
            else:
                runs.append([key, [n]])
        return runs

    @staticmethod
    def _items(runs):
        items = []
        for _key, run in runs:
            item = NotificationItem(run[0])
            for n in run[1:]:
                item.merge(n)
            items.append(item)
        return items

    def _track_ids(self, fresh):
        """Widen the oldest/newest cursors over a page's ids."""
        ids = [n['id'] for n in fresh]
        page_oldest = min(ids, key=id_key)
        page_newest = max(ids, key=id_key)
        self.oldest_id = page_oldest if self.oldest_id is None \
            else min(self.oldest_id, page_oldest, key=id_key)
        self.newest_id = page_newest if self.newest_id is None \
            else max(self.newest_id, page_newest, key=id_key)

    def append_page(self, notifications):
        """Append an older page (newest-first); returns False when the
        page is empty or adds nothing new. Consecutive notifications
        sharing a status (any type) or a type are merged, including
        across the page boundary with the current trailing row."""
        fresh = [n for n in notifications if n['id'] not in self._seen]
        if not fresh:
            self.exhausted = True
            return False
        for n in fresh:
            self._seen.add(n['id'])
        runs = self._runs(fresh)
        position = self._store.get_n_items()
        merged = None
        if position:
            last = self._store.get_item(position - 1)
            if last.group_key == runs[0][0]:
                for n in runs[0][1]:
                    last.merge(n)
                merged = last
                runs = runs[1:]
        items = self._items(runs)
        if items:
            self._store.splice(position, 0, items)
        if merged is not None:
            self.items_changed(position - 1, 1, 1)
        self._track_ids(fresh)
        return True

    def prepend_page(self, notifications):
        """Prepend a newer page; returns the number of new items."""
        fresh = [n for n in notifications if n['id'] not in self._seen]
        if not fresh:
            return 0
        for n in fresh:
            self._seen.add(n['id'])
        runs = self._runs(fresh)
        merged = None
        if self._store.get_n_items():
            first = self._store.get_item(0)
            if first.group_key == runs[-1][0]:
                old_key = first.date_key
                for n in runs[-1][1]:
                    first.merge(n)
                merged = first
                runs = runs[:-1]
        items = self._items(runs)
        if items:
            # One splice, one items-changed: inserting the runs one at a
            # time made a refresh of k rows reflow the list k times.
            self._store.splice(0, 0, items)
        if merged is not None:
            position = len(items)
            self.items_changed(position, 1, 1)
            if merged.date_key != old_key:
                # The merged row moved to a newer day, so it may have left
                # the section it started: the row after it can now start
                # one, and that heading is not drawn until GTK is told.
                n = self._store.get_n_items()
                self.sections_changed(position, min(2, n - position))
        self._track_ids(fresh)
        return len(fresh)

    def remove(self, item):
        """Take *item* out of the list; returns the position it held, or
        None when it was not there. Its ids stay in ``_seen``: a refresh
        must not bring a notification back while its dismissal is still in
        flight."""
        for position in range(self._store.get_n_items()):
            if self._store.get_item(position) is item:
                self._store.remove(position)
                return position
        return None

    def restore(self, item, position):
        """Put back an item :meth:`remove` took out (a dismissal failed)."""
        self._store.insert(min(position, self._store.get_n_items()), item)

    def clear(self):
        self._seen.clear()
        self._store.remove_all()
        self.oldest_id = None
        self.newest_id = None
        self.exhausted = False


def _slug(kind):
    """Gio action names allow letters, digits and '-' only."""
    return re.sub(r'[^a-z0-9]+', '-', kind)


class NotificationsPage(NotificationController, PagedPosts, Adw.Bin):
    """Scrollable notifications list with all/mentions chips, a header
    menu (type filter, notification settings, announcements), infinite
    scroll, refresh, clear, unread badge signal, streaming and status
    pages.

    ``account_id`` restricts the list to one sender, including filtered
    notifications (the detail of a notification request).

    Emits ``unread-changed`` with the current unread count whenever it
    changes; the window shows the count as a sidebar badge.
    """

    __gtype_name__ = 'BabbleNotificationsPage'
    __gsignals__ = {
        'unread-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    FILTERS = {
        'all': None,
        'mentions': ['mention'],
    }

    def __init__(self, client, model=None, account_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.account_id = account_id
        self.filter = 'all'
        self.model = model if model is not None else NotificationModel()
        # Paging flags and cursors; min_id is what this endpoint wants
        # for "newer than". See PagedPosts.
        self._paging_init(newer_cursor='min_id')
        self._shown = False
        self._marked_read = False
        # The id of the marker POST in flight, and whether another was
        # asked for meanwhile. See NotificationController.mark_read.
        self._marking = None
        self._mark_again = False
        # The generation of a catch-up request, whose failure is quiet.
        # See NotificationController.catch_up.
        self._quiet_generation = None
        self._pin_top = False
        self._unread = 0
        self._applied_excluded = self._excluded_key()
        # Accounts whose relationship is unknown, waiting to be asked for
        # in one batch. See NotificationController._request_relationships.
        self._relationship_queue = {}
        self._relationship_busy = False

        self.loading_page = pages.make_loading_page(_('Loading Notifications'))
        # pages' own button says "_Try Again" with a mnemonic; this one
        # did not, so the same action read as two different strings in
        # the catalogue and only one of them had an underline.
        self.error_page = pages.make_error_page(
            _('Could Not Load Notifications'), self.refresh)
        self.empty_page = Adw.StatusPage(
            icon_name='babble-notifications-symbolic',
            title=_('No Notifications'),
            description=_('Mentions, boosts and follows will appear here'))

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.loading_page, 'loading')
        self.stack.add_named(self.error_page, 'error')
        self.stack.add_named(self.empty_page, 'empty')

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class('notification-list-scroller')
        # No model until the list is first shown. The page is built at
        # sign-in for the sidebar badge and loads while hidden; with its
        # model attached, every page and streamed notification made the
        # view build and bind rows inside the HTTP callback, for a list
        # nobody was looking at. The model itself (and the badge, and the
        # sections) keeps up as before.
        # Lazy: notificationrow imports helpers from this module.
        from .notificationrow import NotificationFactory
        self.list_view = Gtk.ListView(
            factory=NotificationFactory(client),
            header_factory=NotificationFactory(client),
            single_click_activate=True)
        self.list_view.add_css_class('canvas-list')
        connect_weak(self.list_view, 'activate', self.on_row_activated)
        connect_weak(self.list_view, 'map', self._attach_model)
        clamp = Adw.ClampScrollable(maximum_size=COLUMN_WIDTH,
                                    tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.list_view)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')

        # Filtered notifications (Mastodon 4.3+): pending requests.
        self.requests_banner = Adw.Banner(button_label=_('View'),
                                          use_markup=False)
        self.requests_banner.set_button_style(
            Adw.BannerButtonStyle.SUGGESTED)
        connect_weak(self.requests_banner, 'button-clicked',
                     self.on_requests_banner_clicked)

        # Instance announcements: the button opens the Announcements page.
        self.banner = Adw.Banner(button_label=_('View'), use_markup=False)
        self.banner.set_button_style(Adw.BannerButtonStyle.SUGGESTED)
        connect_weak(self.banner, 'button-clicked', self.on_announcement_view)

        self.filter_group = Adw.ToggleGroup(
            halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        for name, label in (('all', _('All')), ('mentions', _('Mentions'))):
            self.filter_group.add(Adw.Toggle(name=name, label=label))
        self.filter_group.set_active_name('all')
        self.filter_group.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Filter Notifications')])
        connect_weak(self.filter_group, 'notify::active-name',
                     self.on_filter_changed)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.requests_banner)
        column.append(self.banner)
        column.append(self.filter_group)
        column.append(self.stack)
        self.set_child(column)

        _RefreshGesture(self, scrolled)
        adjustment = scrolled.get_vadjustment()
        connect_weak(adjustment, 'value-changed', self.on_scroll_position)

        self.header_widget = None
        if account_id is None:
            self._build_header()
            connect_weak(settings(), 'changed::' + EXCLUDED_TYPES_KEY,
                         self._on_excluded_changed)
        else:
            self.filter_group.set_visible(False)
        connect_weak(settings(), 'changed::dim-trivial-notifications',
                     self._update_dim)
        self._update_dim()

        if client.token:
            if account_id is None:
                self.fetch_unread()
                self.load_announcements()
                self.fetch_policy()
            self.load()

    # Header: type filter menu and clear button

    def _build_header(self):
        """Widgets packed into the page's header bar by the window: the
        options menu (Filter submenu with one check item per type) and
        the clear button."""
        self.actions = Gio.SimpleActionGroup()
        excluded = set(settings().get_strv(EXCLUDED_TYPES_KEY))
        filter_menu = Gio.Menu()
        for kind, label in NOTIFICATION_TYPES:
            name = 'show-' + _slug(kind)
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(kind not in excluded))
            connect_weak(action, 'change-state', self._on_type_toggled, kind)
            self.actions.add_action(action)
            if type_offered(self.client, kind):
                filter_menu.append(_FILTER_MENU_LABELS.get(kind, label),
                                   'notifs.' + name)
        for name, handler in (('policy', self.on_open_policy),
                              ('announcements', self.on_announcement_view)):
            action = Gio.SimpleAction.new(name, None)
            connect_weak(action, 'activate', handler)
            self.actions.add_action(action)

        menu = Gio.Menu()
        filters = Gio.Menu()
        filters.append_submenu(_('_Filter'), filter_menu)
        menu.append_section(None, filters)
        more = Gio.Menu()
        if filtering_supported(self.client):
            more.append(_('Notification _Settings'), 'notifs.policy')
        more.append(_('_Announcements'), 'notifs.announcements')
        menu.append_section(None, more)
        self.menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                          menu_model=menu)
        self.menu_button.add_css_class('flat')
        pages.set_accessible_label(self.menu_button,
                                   _('Notification Options'))
        self.menu_button.insert_action_group('notifs', self.actions)

        clear_button = Gtk.Button(icon_name='edit-clear-all-symbolic')
        clear_button.add_css_class('flat')
        pages.set_accessible_label(clear_button, _('Clear Notifications'))
        connect_weak(clear_button, 'clicked', self.on_clear_requested)

        box = Gtk.Box(spacing=0)
        box.append(self.menu_button)
        box.append(clear_button)
        self.header_widget = box

    def _excluded_key(self):
        if getattr(self, 'account_id', None) is not None:
            return ()
        return tuple(sorted(settings().get_strv(EXCLUDED_TYPES_KEY)))

    def _on_type_toggled(self, action, value, kind):
        """Check items show a type: unchecking adds it to the persisted
        exclusion list (sent as ``exclude_types[]``)."""
        action.set_state(value)
        _store_type_visibility(kind, value.get_boolean())

    def _on_excluded_changed(self, *_args):
        """The exclusion list changed (here or in Preferences): sync the
        check items and reload the list."""
        key = self._excluded_key()
        if key == self._applied_excluded:
            return
        self._applied_excluded = key
        for kind, _label in NOTIFICATION_TYPES:
            action = self.actions.lookup_action('show-' + _slug(kind))
            action.set_state(GLib.Variant.new_boolean(kind not in key))
        self._reload()

    def _update_dim(self, *_args):
        if settings().get_boolean('dim-trivial-notifications'):
            self.add_css_class('dim-enabled')
        else:
            self.remove_css_class('dim-enabled')

    def on_open_policy(self, *_args):
        present_notification_policy(self, self.client)

    # Activation

    def _attach_model(self, view):
        if view.get_model() is None:
            view.set_model(Gtk.NoSelection.new(self.model))

    def on_row_activated(self, _view, position):
        item = self.model.get_item(position)
        if item is None:
            return
        kind = item.kind
        if kind == 'admin.report':
            from .admin import present_admin
            present_admin(self, self.client)
            return
        if kind == 'annual_report':
            from .wrapped import present_wrapped
            report = item.payload.get('annual_report') or {}
            present_wrapped(self, self.client, report.get('year'))
            return
        if kind in ('severed_relationships', 'moderation_warning'):
            links = notification_links(item, self.client.instance)
            if links:
                launch_external(self, links[0][1])
            return
        if item.status_item is None and len(item.actors) > 1:
            # A merged group with no post (new followers, sign-ups) has no
            # single account to open; picking actors[0] opened a profile
            # nobody chose. Activating the card toggles its people list,
            # like the expander itself.
            factory = self.list_view.get_factory()
            row = factory.row_at(position) if factory is not None else None
            if row is not None and row.cluster_expander is not None:
                row.cluster_expander.set_expanded(
                    not row.cluster_expander.get_expanded())
            return
        root = self.get_root()
        view = getattr(root, 'navigation_view', None) if root else None
        if view is None:
            return
        if item.status_item is not None:
            from .thread import push_thread
            push_thread(view, self.client, item.status_item)
        else:
            from .profile import push_profile
            push_profile(view, self.client, item.actors[0])


# Filtered notification requests

class NotificationRequestRow(Adw.ActionRow):
    """One sender whose notifications were filtered: avatar, handle,
    notification count and the newest post, with Accept and Dismiss
    buttons; activating lists the sender's notifications."""

    __gtype_name__ = 'BabbleNotificationRequestRow'

    def __init__(self, page, request):
        super().__init__(activatable=True, use_markup=False)
        self.page = page
        self.request = request
        account = request['account']
        name = _clean_name(account)
        self.set_title(name)
        subtitle = '@' + account.get('acct', '')
        preview = self._preview(request.get('last_status'))
        if preview:
            subtitle += '\n' + preview
        self.set_subtitle(subtitle)
        self.set_title_lines(1)
        self.set_subtitle_lines(3)
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)

        count = int(request.get('notifications_count') or 0)
        count_label = Gtk.Label(
            label=ngettext('%d notification', '%d notifications', count)
            % count, valign=Gtk.Align.CENTER)
        count_label.add_css_class('caption')
        count_label.add_css_class('dimmed')
        self.add_suffix(count_label)

        self._answer_button('user-trash-symbolic', _('Dismiss'), 'dismiss')
        self._answer_button('object-select-symbolic', _('Accept'), 'accept')

    def _answer_button(self, icon_name, label, action):
        """Flat circular suffix button answering the request."""
        button = pages.icon_button(icon_name, label, css=('circular',))
        pages.connect_weak(button, 'clicked', self._on_answer_clicked, action)
        self.add_suffix(button)

    def _on_answer_clicked(self, _button, action):
        self.answer(action)

    @staticmethod
    def _preview(status):
        """One-line plain text of the newest post (content warning
        instead of the body when there is one)."""
        if not status:
            return ''
        rule = status_filter(status, filterrules.NOTIFICATIONS,
                             datetime.datetime.now(datetime.timezone.utc))
        if rule is not None and rule[0] != 'blur':
            return _('Filtered: %s') % rule[1] if rule[1] else _('Filtered')
        if status.get('spoiler_text'):
            return _('CW: %s') % status['spoiler_text']
        text = ' '.join(s['text'] for s in
                        parse_content(status.get('content') or '')
                        if s['text'] != '\n')
        return text.strip()

    def on_activate(self):
        title = _('Notifications from %s') % ('@' + self.request['account']
                                              .get('acct', ''))
        push_page(self, title,
                  RequestNotificationsPage(self.page.client, self.request,
                                           self.answer))

    def answer(self, action):
        """POST /api/v1/notifications/requests/:id/(accept|dismiss).

        mutate_row_api with remove=False: the page decides what leaving the
        list means here (a toast, and the request list refreshes), so the
        row stays until request_answered takes it.
        """
        self.page.mutate_row_api(
            self, lambda callback: api_notifications.answer_request_async(
                self.page.client, self.request['id'], action,
                callback=callback),
            on_done=lambda: self.page.request_answered(self, action),
            error_heading=_('Could not update the request'), remove=False,
            finish=api_notifications.answer_request_finish)


class NotificationRequestsPage(PagedList):
    """GET /api/v1/notifications/requests: senders whose notifications
    were filtered by the notification policy."""

    __gtype_name__ = 'BabbleNotificationRequestsPage'

    def __init__(self, client, on_changed=None, **kwargs):
        self._on_changed = on_changed
        intro = Gtk.Label(
            label=_('Notifications from people matching your filters wait '
                    'here until you accept or dismiss them'),
            xalign=0.0, wrap=True)
        intro.add_css_class('dimmed')
        super().__init__(
            client, api_notifications.list_path('requests'), self._make_row,
            empty_title=_('No Filtered Notifications'),
            empty_icon='babble-notifications-symbolic',
            empty_description=_('Notifications held back by your filters '
                                'will appear here'),
            header=intro, **kwargs)

    def _make_row(self, request):
        return NotificationRequestRow(self, request)

    def request_answered(self, row, action):
        from .pages import show_toast
        self.remove_row(row)
        show_toast(self, _('Notification request accepted')
                   if action == 'accept'
                   else _('Notification request dismissed'))
        if self._on_changed is not None:
            self._on_changed()


class RequestNotificationsPage(Adw.Bin):
    """The notifications of one filtered sender, with Accept and Dismiss
    in the header; either answer returns to the request list."""

    __gtype_name__ = 'BabbleRequestNotificationsPage'

    def __init__(self, client, request, on_answer):
        super().__init__()
        self._on_answer = on_answer
        self.inner = NotificationsPage(
            client, account_id=request['account']['id'])
        self.set_child(self.inner)
        dismiss = pages.icon_button('user-trash-symbolic', _('Dismiss'))
        pages.connect_weak(dismiss, 'clicked', self._on_answer_clicked,
                           'dismiss')
        accept = pages.icon_button('object-select-symbolic', _('Accept'))
        pages.connect_weak(accept, 'clicked', self._on_answer_clicked,
                           'accept')
        self.header_widgets = [dismiss, accept]

    def _on_answer_clicked(self, _button, action):
        self._answer(action)

    def _answer(self, action):
        from .pages import window_of
        self._on_answer(action)
        window = window_of(self)
        if window is not None:
            window.navigation_view.pop()

    def refresh(self):
        self.inner.refresh()


# Notification policy

POLICY_FIELDS = [
    ('for_not_following', _('People You Don’t Follow'),
     _('Accounts you do not follow')),
    ('for_not_followers', _('People Not Following You'),
     _('Accounts that do not follow you, or followed you less than three '
       'days ago')),
    ('for_new_accounts', _('New Accounts'),
     _('Accounts created within the last 30 days')),
    ('for_private_mentions', _('Unsolicited Private Mentions'),
     _('Unless in reply to your own mention or sent by someone you follow')),
    ('for_limited_accounts', _('Moderated Accounts'),
     _('Accounts limited by the server moderators')),
    ('for_bots', _('Bots'), _('Accounts marked as automated')),
]

POLICY_CHOICES = [
    ('accept', _('Accept')),
    ('filter', _('Filter')),
    ('drop', _('Ignore')),
]


class NotificationPolicyDialog(pages.ToastDialog):
    """Edits ``GET/PATCH /api/v2/notifications/policy`` (what to do with
    notifications from each kind of sender: accept, filter into the
    requests inbox or drop) and the per-type visibility switches. The
    policy is saved when the dialog closes, only if something changed."""

    __gtype_name__ = 'BabbleNotificationPolicyDialog'

    def __init__(self, client, parent=None):
        super().__init__(_('Notification Settings'), 480, 620)
        self.client = client
        self._parent = parent
        self.policy = None
        self.filter_rows = {}
        self.type_rows = {}

        self.filters_group = Adw.PreferencesGroup(
            title=_('Filter Notifications From'),
            description=_('Loading…'))
        for key, title, subtitle in POLICY_FIELDS:
            row = Adw.ComboRow(
                title=title, subtitle=subtitle, visible=False,
                model=Gtk.StringList.new(
                    [label for _code, label in POLICY_CHOICES]))
            self.filter_rows[key] = row
            self.filters_group.add(row)
        self.page.add(self.filters_group)

        types_group = Adw.PreferencesGroup(
            title=_('Show Notifications For'))
        excluded = set(settings().get_strv(EXCLUDED_TYPES_KEY))
        for kind, label in NOTIFICATION_TYPES:
            row = Adw.SwitchRow(title=label, active=kind not in excluded,
                                visible=type_offered(self.client, kind))
            row.add_prefix(Gtk.Image(icon_name=type_icon(kind)))
            connect_weak(row, 'notify::active', self._on_type_switched, kind)
            self.type_rows[kind] = row
            types_group.add(row)
        self.page.add(types_group)

        connect_weak(self, 'closed', self._on_closed)
        self.load()

    def _on_closed(self, _dialog):
        self.save()

    # Policy

    def load(self):
        api_notifications.policy_async(self.client, callback=self.on_loaded)

    def on_loaded(self, client, result, _user_data=None):
        try:
            policy = api_notifications.policy_finish(client, result)
        except GLib.Error as error:
            self.filters_group.set_description(
                _('This server does not support filtering notifications')
                if getattr(error, 'code', 0) == 404
                else summarize_error_markup(error))
            return
        self.policy = policy
        self.filters_group.set_description(_(
            'Filtered notifications wait in a separate inbox until you '
            'accept them. Ignored ones are discarded.'))
        codes = [code for code, _label in POLICY_CHOICES]
        for key, row in self.filter_rows.items():
            if key not in policy:
                continue
            value = policy.get(key)
            row.set_selected(codes.index(value) if value in codes else 0)
            row.set_visible(True)

    def changed_payload(self):
        """The policy fields whose selection differs from the loaded
        values (empty before the policy has loaded)."""
        if self.policy is None:
            return {}
        payload = {}
        for key, row in self.filter_rows.items():
            if key not in self.policy:
                continue
            choice = POLICY_CHOICES[row.get_selected()][0]
            if choice != self.policy.get(key):
                payload[key] = choice
        return payload

    def save(self):
        """PATCH /api/v2/notifications/policy with the changed fields."""
        payload = self.changed_payload()
        if not payload:
            return
        parent = self._parent

        def saved(_data=None):
            # Reported on the window behind the dialog: a toast raised on
            # the dialog itself would be covered when it closes.
            if parent is not None:
                show_toast(parent, _('Notification settings updated'))

        pages.call_api(
            self, self.client,
            lambda callback: api_notifications.update_policy_async(
                self.client, payload, callback=callback),
            on_done=saved,
            error_heading=_('Could not save notification settings'),
            finish=api_notifications.update_policy_finish)

    # Visible types

    def _on_type_switched(self, row, _pspec, kind):
        _store_type_visibility(kind, row.get_active())


def _store_type_visibility(kind, visible):
    """Persist one type's visibility in the exclusion list."""
    current = settings().get_strv(EXCLUDED_TYPES_KEY)
    excluded = [k for k in current if k != kind]
    if not visible:
        excluded.append(kind)
    if excluded != list(current):
        settings().set_strv(EXCLUDED_TYPES_KEY, excluded)


def present_notification_policy(parent, client):
    """Show the notification policy dialog over *parent*."""
    dialog = NotificationPolicyDialog(client, parent)
    dialog.present(parent)
    return dialog
