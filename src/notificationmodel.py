# SPDX-License-Identifier: GPL-3.0-or-later
"""Notification list data: items, the model, and title/detail helpers.

Rows and the page import the helpers they need; the policy dialog lives
in :mod:`babble.notificationpolicy`.
"""

import datetime
import re
import time as _time

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _, ngettext

from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from .entities import lift  # noqa: E402
from .notificationcopy import (LIST_TITLE_DEFAULT, LIST_TITLES)  # noqa: E402
from .weakrefs import connect_weak  # noqa: E402
from .statustext import relative_time  # noqa: E402
from .timelinedata import StatusItem  # noqa: E402
from .timelinebase import id_key  # noqa: E402


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
