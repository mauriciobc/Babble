# SPDX-License-Identifier: GPL-3.0-or-later
"""Notifications view: grouped notification rows with embedded post
snippets, all/mentions filtering, infinite scroll and an unread badge
signal consumed by the window sidebar."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, GLib, Gio, GObject, Gtk, Pango  # noqa: E402
import datetime  # noqa: E402
import re  # noqa: E402
import time as _time  # noqa: E402

from .mastodon import summarize_error_markup  # noqa: E402
from . import pages  # noqa: E402
from .pages import (PagedList, PagedPosts, connect_weak,  # noqa: E402
                    launch_external,
                    push_page, report_error, reveal, settings, show_toast)
from .textemoji import EMOJI_RE, status_emoji_bank  # noqa: E402
from .notificationcopy import (FILTER_NOTIFICATION_TYPES,  # noqa: E402
                               LIST_TITLE_DEFAULT, LIST_TITLES)
from .notificationctrl import (NotificationController,  # noqa: E402
                               _RELATIONSHIPS)
from .entities import lift
from .timelinedata import StatusItem  # noqa: E402
from .mediacache import AvatarCache, _TextureCache  # noqa: E402
from .timelinepage import _RefreshGesture  # noqa: E402
from .statustext import parse_content, relative_time, shorten_url_text  # noqa: E402
from .timelinebase import COLUMN_WIDTH, id_key  # noqa: E402


# Most avatars drawn in a cluster's stacked strip before the rest
# collapse into a "+N" counter. Each is 32 px and a row must stay inside
# the column at the 360 px minimum window width.
AVATAR_STRIP_MAX = 6


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

# Types rendered dimmed when the dim-trivial-notifications setting is on.
TRIVIAL_TYPES = frozenset({'favourite', 'reblog', 'follow'})

EXCLUDED_TYPES_KEY = 'notification-excluded-types'

# (type, label) entries of the header Filter submenu and the settings
# dialog.
NOTIFICATION_TYPES = FILTER_NOTIFICATION_TYPES

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
        self._section_title = None
        self._section_weekday = None
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

    @property
    def section_title(self):
        """Date group heading, e.g. 'Today' or 'Oct 4' (None when the
        model marks this date group as already introduced)."""
        return self._section_title

    @property
    def section_weekday(self):
        return self._section_weekday

    def _stamp_section(self, title, weekday):
        self._section_title = title
        self._section_weekday = weekday

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
        self._stamp_sections()
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

    def _stamp_sections(self):
        """Mark each day's first row with the heading text ('Today',
        'Yesterday' or the full date) and clear it on continuations."""
        n = self._store.get_n_items()
        today = datetime.date.today()
        last_key = None
        for index in range(n):
            item = self._store[index]
            key = item.date_key
            if key == last_key:
                item._stamp_section(None, None)
                continue
            last_key = key
            try:
                day = _time.strptime(key, '%Y-%m-%d')
                date = datetime.date(*day[:3])
            except ValueError:
                day = None
            if day is None:
                item._stamp_section('', None)
                continue
            if date == today:
                title = _('Today')
            elif date == today - datetime.timedelta(days=1):
                title = _('Yesterday')
            else:
                title = _('%(month)s %(day)d') % {
                    'month': date.strftime('%b'), 'day': day.tm_mday}
            item._stamp_section(title, _WEEKDAYS[day.tm_wday])

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
            else min(self.oldest_id, page_oldest)
        self.newest_id = page_newest if self.newest_id is None \
            else max(self.newest_id, page_newest)

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
        if position:
            last = self._store.get_item(position - 1)
            if last.group_key == runs[0][0]:
                for n in runs[0][1]:
                    last.merge(n)
                self._stamp_sections()
                self.items_changed(position - 1, 1, 1)
                runs = runs[1:]
        self._store.splice(position, 0, self._items(runs))
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
        if self._store.get_n_items():
            first = self._store.get_item(0)
            if first.group_key == runs[-1][0]:
                for n in runs[-1][1]:
                    first.merge(n)
                self._stamp_sections()
                self.items_changed(0, 1, 1)
                runs = runs[:-1]
        for item in reversed(self._items(runs)):
            self._store.insert(0, item)
        self._track_ids(fresh)
        return len(fresh)

    def clear(self):
        self._seen.clear()
        self._store.remove_all()
        self.oldest_id = None
        self.newest_id = None
        self.exhausted = False


class ColumnLayout(Gtk.BoxLayout):
    """`Gtk.BoxLayout` that pins its widget's requisition to a fixed
    column width.

    Root cause of the round-3 full-bleed regressions (1396/1168/787px
    rows against the 618px cap): rows are children of a
    GtkListItemWidget (halign CENTER, default valign FILL), and GTK's
    alignment step (gtkwidget.c, gtk_widget_adjust_size_allocation)
    measures their natural width *for the allocated height*
    (width-for-height). Rows whose embedded StatusRow carries wrapped
    GtkLabels report the label's unmarried single-line width when that
    height is tall, so adjust_for_align() was centering the row at the
    label's full text width; rows without wrapped content (follow)
    stayed at 618px. Types like Gtk.Box do not re-dispatch the widget
    measure vfunc from Python — the box layout manager does the
    measuring — so the cap lives here, on the layout manager's
    measure vfunc.

    Vertical measures are also served at the capped width: the
    ListView measures item heights for the full viewport width
    (gtklistview.c gtk_list_view_size_allocate, for_size=self->list_width),
    which lets wrapped content pretend to fit in fewer lines than the
    rendered, narrower row allows.
    """

    def do_measure(self, widget, orientation, for_size):
        cap = widget.column_cap
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum, natural, min_b, nat_b = Gtk.BoxLayout.do_measure(
                self, widget, orientation, for_size)
            return (min(minimum, cap), min(natural, cap), min_b, nat_b)
        if for_size > cap:
            for_size = cap
        return Gtk.BoxLayout.do_measure(self, widget, orientation,
                                        for_size)


class ColumnBox(Gtk.Box):
    """Fixed-column Gtk.Box backed by ColumnLayout."""

    def __init__(self, cap, **kwargs):
        layout = ColumnLayout()
        layout.set_orientation(kwargs.pop('orientation',
                                          Gtk.Orientation.HORIZONTAL))
        layout.set_spacing(kwargs.pop('spacing', 0))
        super().__init__()
        self.column_cap = cap
        self.set_layout_manager(layout)
        for key, value in kwargs.items():
            self.set_property(key, value)


ROW_MARGIN = 12


def make_date_heading():
    """Date group heading: title ('Today' / 'Oct 4') beside a dimmed
    weekday."""
    box = ColumnBox(COLUMN_WIDTH - 2 * ROW_MARGIN, spacing=8)
    box.set_halign(Gtk.Align.FILL)
    box.set_margin_start(ROW_MARGIN)
    box.set_margin_end(ROW_MARGIN)
    box.set_margin_top(12)
    box.set_margin_bottom(6)
    title = Gtk.Label(xalign=0.0)
    title.add_css_class('heading')
    box.append(title)
    weekday = Gtk.Label(xalign=0.0)
    weekday.add_css_class('dimmed')
    box.append(weekday)
    return box, title, weekday


class NotificationFactory(Gtk.SignalListItemFactory):

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.connect('setup', self.on_setup)
        self.connect('bind', self.on_bind)

    def on_setup(self, _factory, list_item):
        if isinstance(list_item, Gtk.ListHeader):
            box, _title, _weekday = make_date_heading()
            list_item.set_child(box)
            return
        row = NotificationRow()
        list_item.set_child(row)
        list_item.set_activatable(True)

    def on_bind(self, _factory, list_item):
        item = list_item.get_item()
        if isinstance(list_item, Gtk.ListHeader):
            box = list_item.get_child()
            title, weekday = box.get_first_child(), \
                box.get_first_child().get_next_sibling()
            title.set_text(item.section_title)
            weekday.set_text(item.section_weekday)
            return
        row = list_item.get_child()
        row.bind(item, self.client)


class NotificationPost(Gtk.Box):
    """Compact read-only snapshot of the post a notification is about;
    the surrounding row opens the thread."""

    __gtype_name__ = 'BabbleNotificationPost'

    # Truncation for long plain content behind a 'Read More' button.
    READ_MORE_CHARS = 240
    MAX_THUMBNAILS = 4
    THUMBNAIL_HEIGHT = 96

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class('notification-post')
        self._segments = []
        self._bank = {}

        byline = Gtk.Box(spacing=6)
        self.name_label = Gtk.Label(xalign=0.0,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.name_label.add_css_class('caption-heading')
        byline.append(self.name_label)
        self.handle_label = Gtk.Label(xalign=0.0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.handle_label.add_css_class('caption')
        self.handle_label.add_css_class('dimmed')
        byline.append(self.handle_label)
        self.append(byline)

        self.content = Gtk.Label(xalign=0.0, hexpand=True, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 use_markup=True)
        self.append(self.content)

        self.more_button = Gtk.Button(label=_('Read More'),
                                      halign=Gtk.Align.START,
                                      visible=False)
        self.more_button.add_css_class('flat')
        self.more_button.add_css_class('caption')
        connect_weak(self.more_button, 'clicked', self._on_read_more)
        self.append(self.more_button)

        self.poll_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=2)
        self.append(self.poll_box)

        self.media_box = Gtk.Box(spacing=6)
        self.append(self.media_box)

    def _preview(self, segments):
        """Leading segments within the character budget, or None when
        the content fits. Links are kept whole so a clipped match never
        breaks a URL or handle."""
        plain_chars = sum(len(s['text']) for s in segments
                          if s['text'] != '\n')
        if plain_chars <= self.READ_MORE_CHARS:
            return None
        preview, used = [], 0
        for segment in segments:
            text = segment['text']
            if text == '\n':
                if preview:
                    preview.append({'text': text, 'href': None})
                continue
            remaining = self.READ_MORE_CHARS - used
            if remaining <= 0:
                break
            if segment['href'] is None and len(text) > remaining:
                preview.append({'text': text[:remaining], 'href': None})
                break
            preview.append(segment)
            used += len(text)
        return preview

    def _on_read_more(self, _button):
        self._show(self._segments)
        self.more_button.set_visible(False)

    def _show(self, segments):
        """Render segments as Pango markup: links stay focusable and
        activatable, custom emoji shortcodes are dropped."""
        parts = []
        for segment in segments:
            text = segment['text']
            if text == '\n':
                if parts and not parts[-1].endswith('\n'):
                    parts.append('\n\n')
                continue
            text = EMOJI_RE.sub(
                lambda m: '' if m.group(1) in self._bank else m.group(0),
                shorten_url_text(text) if segment['href'] else text)
            text = GLib.markup_escape_text(text)
            if segment['href']:
                text = '<a href="%s">%s</a>' % (
                    GLib.markup_escape_text(segment['href']), text)
            if parts and not parts[-1].endswith(('\n', ' ')):
                parts.append(' ')
            parts.append(text)
        self.content.set_markup(''.join(parts).strip())

    def _set_poll(self, poll):
        while (child := self.poll_box.get_first_child()) is not None:
            self.poll_box.remove(child)
        self.poll_box.set_visible(bool(poll))
        if not poll:
            return
        votes = poll.get('votes_count') or 0
        for option in poll.get('options') or []:
            share = (option.get('votes_count') or 0) * 100 // votes \
                if votes else 0
            self.poll_box.append(Gtk.Label(
                label='%s (%d%%)' % (option.get('title'), share),
                xalign=0.0, wrap=True))
        total = Gtk.Label(
            label=ngettext('%d vote', '%d votes', votes) % votes,
            xalign=0.0)
        total.add_css_class('caption')
        total.add_css_class('dimmed')
        self.poll_box.append(total)

    def _set_media(self, item):
        while (child := self.media_box.get_first_child()) is not None:
            self.media_box.remove(child)
        attachments = item.media[:self.MAX_THUMBNAILS]
        self.media_box.set_visible(bool(attachments))
        if attachments and item.sensitive:
            label = Gtk.Label(
                label=ngettext('%d sensitive attachment',
                               '%d sensitive attachments',
                               len(attachments)) % len(attachments),
                xalign=0.0)
            label.add_css_class('caption')
            label.add_css_class('dimmed')
            self.media_box.append(label)
            return
        for attachment in attachments:
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER,
                                  can_shrink=True, hexpand=True)
            picture.set_size_request(-1, self.THUMBNAIL_HEIGHT)
            picture.set_alternative_text(
                attachment.get('description') or _('Attachment'))
            picture.add_css_class('card')
            picture.add_css_class('darkenable')
            self.media_box.append(picture)
            url = attachment.get('preview_url') or attachment.get('url')
            if url:
                _TextureCache.load(url, picture)

    def bind(self, item):
        self.name_label.set_text(item.display_name or '?')
        self.handle_label.set_text(item.handle)
        self._bank = status_emoji_bank(item.status)
        self._segments = parse_content(item.content)
        preview = self._preview(self._segments)
        self._show(preview or self._segments)
        self.more_button.set_visible(preview is not None)
        self._set_poll(item.status.get('poll'))
        self._set_media(item)


class NotificationRow(ColumnBox):
    """Notification card: actor avatar, type icon, grouped summary line,
    relative timestamp, a compact post snapshot for status-bearing types
    and an expander listing the people of merged clusters."""

    __gtype_name__ = 'BabbleNotificationRow'

    def __init__(self, **kwargs):
        super().__init__(cap=COLUMN_WIDTH - 2 * ROW_MARGIN,
                         orientation=Gtk.Orientation.VERTICAL,
                         spacing=0, **kwargs)
        self.set_focusable(True)
        self.add_css_class('notification-row')
        self.add_css_class('card')
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.START)
        self.set_vexpand(False)
        self.set_margin_start(ROW_MARGIN)
        self.set_margin_end(ROW_MARGIN)
        self.set_margin_bottom(ROW_MARGIN)
        self.item = None
        self.client = None
        # Recycle state, cleared by _clear_actions on every rebind.
        self._request_buttons = ()
        self._follow_button = None

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)

        header = Gtk.Box(spacing=12)
        self.avatar = Adw.Avatar(
            size=40, show_initials=True,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        header.append(self.avatar)
        summary = Gtk.Box(spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(
            pixel_size=16, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.icon.add_css_class('dimmed')
        summary.append(self.icon)
        self.title_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True,
                                     wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title_label.add_css_class('heading')
        summary.append(self.title_label)
        header.append(summary)
        self.time_label = Gtk.Label(valign=Gtk.Align.START)
        self.time_label.add_css_class('caption')
        self.time_label.add_css_class('dimmed')
        header.append(self.time_label)
        content.append(header)

        self.detail_label = Gtk.Label(xalign=0.0, wrap=True, visible=False,
                                      wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.detail_label.add_css_class('notification-detail')
        content.append(self.detail_label)

        self.post = NotificationPost()
        self.post.set_visible(False)
        content.append(self.post)

        self.actions_box = Gtk.Box(spacing=8, halign=Gtk.Align.START,
                                   visible=False)
        self.actions_box.add_css_class('notification-actions')
        content.append(self.actions_box)

        # The strip must not force a row wider than the column: cap the
        # visible avatars and count the rest, instead of letting a
        # nine-person cluster set a 352 px minimum on a 360 px window.
        self.avatar_stack = Gtk.Box()
        self.avatar_stack.add_css_class('notification-avatar-stack')
        self.people_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=6, margin_top=6)
        self.people_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            child=self.people_box)
        self.cluster_expander = Gtk.Expander(
            label_widget=self.avatar_stack, child=self.people_revealer,
            visible=False)
        connect_weak(self.cluster_expander, 'notify::expanded',
                     self._on_expanded_changed)
        content.append(self.cluster_expander)

        self.append(content)

    def _on_expanded_changed(self, expander, _pspec):
        expanded = expander.get_expanded()
        reveal(self.people_revealer, expanded, animate=expanded)

    def _person_row(self, name, kind):
        """One named person row under the expander: action icon and name."""
        person = Gtk.Box(spacing=8)
        person.append(Gtk.Image(
            icon_name=type_icon(kind),
            accessible_role=Gtk.AccessibleRole.PRESENTATION))
        person.append(Gtk.Label(label=name, xalign=0.0, hexpand=True,
                                ellipsize=Pango.EllipsizeMode.END))
        return person

    def _populate_people(self, item):
        """Build the stacked avatars with per-action badges and the
        named per-person rows behind the expander."""
        while (child := self.people_box.get_first_child()) is not None:
            self.people_box.remove(child)
        while (child := self.avatar_stack.get_first_child()) is not None:
            self.avatar_stack.remove(child)
        is_cluster = len(item.notifications) > 1
        self.cluster_expander.set_visible(is_cluster)
        self.cluster_expander.set_expanded(False)
        reveal(self.people_revealer, False)
        self.avatar.set_visible(not is_cluster)
        if not is_cluster:
            return
        names = []
        actors = list(zip(item.actors, item.kinds, strict=False))
        shown = actors[:AVATAR_STRIP_MAX]
        for actor, kind in shown:
            name = _clean_name(actor)
            names.append(name)
            avatar = Adw.Avatar(
                size=32, text=name, show_initials=True,
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            AvatarCache.load(avatar, actor)
            slot = Gtk.Overlay(child=avatar)
            badge = Gtk.Image.new_from_icon_name(type_icon(kind))
            badge.set_pixel_size(10)
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.END)
            badge.add_css_class('notification-badge')
            slot.add_overlay(badge)
            self.avatar_stack.append(slot)
            self.people_box.append(self._person_row(name, kind))
        remaining = len(actors) - len(shown)
        if remaining > 0:
            more = Gtk.Label(label='+%d' % remaining)
            more.add_css_class('notification-avatar-more')
            more.set_valign(Gtk.Align.CENTER)
            more.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [ngettext('%d more person', '%d more people', remaining)
                 % remaining])
            self.avatar_stack.append(more)
        for actor, kind in actors[len(shown):]:
            name = _clean_name(actor)
            names.append(name)
            self.people_box.append(self._person_row(name, kind))
        people = ', '.join(names)
        self.cluster_expander.set_tooltip_text(people)
        self.cluster_expander.update_property(
            [Gtk.AccessibleProperty.LABEL], [people])

    # Inline actions

    def _clear_actions(self):
        """Empty the actions box and forget the buttons it held.

        Clearing the box is not enough: this row is recycled, and the
        buttons remembered here outlived their removal. ``_request_buttons``
        and ``_follow_button`` were then found again on the *next* item --
        greyed out and, for a follow request, wired to a request that is
        no longer on screen.
        """
        while (child := self.actions_box.get_first_child()) is not None:
            self.actions_box.remove(child)
        self._request_buttons = ()
        self._follow_button = None

    def _action_button(self, label, callback, *extra, css=None):
        button = Gtk.Button(label=label, use_underline=True)
        button.add_css_class('pill')
        if css:
            button.add_css_class(css)
        connect_weak(button, 'clicked', callback, *extra)
        self.actions_box.append(button)
        return button

    def _action_note(self, text):
        label = Gtk.Label(label=text, xalign=0.0)
        label.add_css_class('dimmed')
        self.actions_box.append(label)

    def _bind_extras(self, item, client):
        """Detail text and inline actions (Accept/Reject, Follow Back,
        View Report…) of the types that have them."""
        self._clear_actions()
        instance = getattr(client, 'instance', '') or ''
        detail = notification_detail(item, instance)
        self.detail_label.set_visible(bool(detail))
        self.detail_label.set_text(detail or '')
        if set(item.kinds) <= TRIVIAL_TYPES:
            self.add_css_class('can-be-dimmed')
        else:
            self.remove_css_class('can-be-dimmed')
        single = len(item.notifications) == 1
        kind = item.kind
        if kind == 'follow_request' and single:
            self._add_request_buttons(item)
        elif kind == 'follow' and single:
            self._add_follow_back(item)
        elif kind == 'admin.report':
            self._action_button(_('_View Reports'), self._on_view_reports)
        elif kind == 'annual_report':
            self._action_button(_('_View'), self._on_view_report_year,
                                css='suggested-action')
        for label, uri in notification_links(item, instance):
            self._action_button(label, self._on_link_clicked, uri)
        self.actions_box.set_visible(
            self.actions_box.get_first_child() is not None)

    def _on_view_reports(self, _button):
        from .admin import present_admin
        present_admin(self, self.client)

    def _on_link_clicked(self, _button, uri):
        launch_external(self, uri)

    def _on_view_report_year(self, _button):
        from .wrapped import present_wrapped
        report = self.item.payload.get('annual_report') or {}
        present_wrapped(self, self.client, report.get('year'))

    def _add_request_buttons(self, item):
        state = item.request_state
        if state == 'accepted':
            self._action_note(_('Request accepted'))
            return
        if state == 'rejected':
            self._action_note(_('Request rejected'))
            return
        reject = self._action_button(
            _('_Reject'), self._on_request_clicked, item, 'reject')
        accept = self._action_button(
            _('_Accept'), self._on_request_clicked, item, 'authorize',
            css='suggested-action')
        self._request_buttons = (reject, accept)

    def _on_request_clicked(self, _button, item, action):
        self._answer_request(item, action)

    def _answer_request(self, item, action):
        """POST /api/v1/follow_requests/:account_id/(authorize|reject)."""
        client = self.client
        # Held locally: by the time the reply lands the row may have been
        # rebound, and re-enabling a button that belongs to a different
        # item is worse than leaving this one greyed.
        buttons = self._request_buttons
        for button in buttons:
            button.set_sensitive(False)

        def done(_client, result, _data=None):
            try:
                client.request_finish(result)
            except GLib.Error as error:
                for button in buttons:
                    button.set_sensitive(True)
                report_error(self, _('Could not answer the request'), error)
                return
            item.request_state = 'accepted' if action == 'authorize' \
                else 'rejected'
            if self.item is item:
                self._bind_extras(item, client)

        client.request_async(
            'POST', '/follow_requests/%s/%s' % (item.actors[0]['id'],
                                                action), callback=done)


    def _add_follow_back(self, item):
        actor = item.actors[0]
        client = self.client
        key = (getattr(client, 'instance', ''), actor.get('id'))
        relationship = _RELATIONSHIPS.get(key)
        if relationship is None:
            # The page asked for it when the notification arrived; the
            # row is re-emitted once the answer is in.
            return
        if relationship.get('following'):
            self._action_note(_('Following'))
        elif relationship.get('requested'):
            self._action_note(_('Follow request sent'))
        elif relationship.get('blocking') or relationship.get('blocked_by'):
            return
        else:
            self._follow_button = self._action_button(
                _('_Follow Back'), self._on_follow_back_clicked,
                item, actor, key, css='suggested-action')

    def _on_follow_back_clicked(self, _button, item, actor, key):
        self._follow_back(item, actor, key)

    def _follow_back(self, item, actor, key):
        """POST /api/v1/accounts/:id/follow."""
        client = self.client
        # Held locally for the same reason as _answer_request.
        button = self._follow_button
        if button is None:
            return
        button.set_sensitive(False)

        def done(_client, result, _data=None):
            try:
                relationship = client.request_finish(result)
            except GLib.Error as error:
                if self.item is item:
                    self._bind_extras(item, client)
                report_error(self, _('Could not follow'), error)
                return
            _RELATIONSHIPS[key] = relationship
            if self.item is item:
                self._bind_extras(item, client)

        client.request_async('POST', '/accounts/%s/follow' % actor['id'],
                             callback=done)

    def bind(self, item, client):
        self.item = item
        self.client = client
        actor = item.actors[0]
        self.avatar.set_text(_clean_name(actor))
        AvatarCache.load(self.avatar, actor)
        self.icon.set_from_icon_name(type_icon(item.kind))
        self.title_label.set_text(item.title)
        self.time_label.set_text(item.timestamp)
        has_status = item.status_item is not None
        self.post.set_visible(has_status)
        if has_status:
            self.post.bind(item.status_item)
        self._populate_people(item)
        self._bind_extras(item, client)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('%(title)s, %(time)s') % {'title': item.title,
                                         'time': item.timestamp}])


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
        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection.new(self.model),
            factory=NotificationFactory(client),
            header_factory=NotificationFactory(client),
            single_click_activate=True)
        connect_weak(self.list_view, 'activate', self.on_row_activated)
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
            filter_menu.append(label, 'notifs.' + name)
        for name, handler in (('policy', self.on_open_policy),
                              ('announcements', self.on_announcement_view)):
            action = Gio.SimpleAction.new(name, None)
            connect_weak(action, 'activate', handler)
            self.actions.add_action(action)

        menu = Gio.Menu()
        filters = Gio.Menu()
        filters.append_submenu(_('Filter'), filter_menu)
        menu.append_section(None, filters)
        more = Gio.Menu()
        more.append(_('Notification Settings'), 'notifs.policy')
        more.append(_('Announcements'), 'notifs.announcements')
        menu.append_section(None, more)
        self.menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                          menu_model=menu)
        self.menu_button.add_css_class('flat')
        self.menu_button.set_tooltip_text(_('Notification Options'))
        self.menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Notification Options')])
        self.menu_button.insert_action_group('notifs', self.actions)

        clear_button = Gtk.Button(icon_name='edit-clear-all-symbolic')
        clear_button.add_css_class('flat')
        clear_button.set_tooltip_text(_('Clear Notifications'))
        clear_button.update_property([Gtk.AccessibleProperty.LABEL],
                                     [_('Clear Notifications')])
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
        self.add_suffix(pages.icon_button(
            icon_name, label,
            callback=lambda *_: self._on_answer_clicked(None, action),
            css=('circular',)))

    def _on_answer_clicked(self, _button, action):
        self.answer(action)

    @staticmethod
    def _preview(status):
        """One-line plain text of the newest post (content warning
        instead of the body when there is one)."""
        if not status:
            return ''
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

        mutate_row with remove=False: the page decides what leaving the
        list means here (a toast, and the request list refreshes), so the
        row stays until request_answered takes it.
        """
        self.page.mutate_row(
            self, 'POST',
            '/notifications/requests/%s/%s' % (self.request['id'], action),
            on_done=lambda: self.page.request_answered(self, action),
            error_heading=_('Could not update the request'), remove=False)


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
            client, '/notifications/requests', self._make_row,
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
        dismiss = pages.icon_button(
            'user-trash-symbolic', _('Dismiss'),
            callback=lambda *_: self._on_answer_clicked(None, 'dismiss'))
        accept = pages.icon_button(
            'object-select-symbolic', _('Accept'),
            callback=lambda *_: self._on_answer_clicked(None, 'accept'))
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


class NotificationPolicyDialog(Adw.Dialog):
    """Edits ``GET/PATCH /api/v2/notifications/policy`` (what to do with
    notifications from each kind of sender: accept, filter into the
    requests inbox or drop) and the per-type visibility switches. The
    policy is saved when the dialog closes, only if something changed."""

    __gtype_name__ = 'BabbleNotificationPolicyDialog'

    def __init__(self, client, parent=None):
        super().__init__(title=_('Notification Settings'),
                         content_width=480, content_height=620)
        self.client = client
        self._parent = parent
        self.policy = None
        self.filter_rows = {}
        self.type_rows = {}

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self.toasts)
        self.set_child(toolbar)

        page = Adw.PreferencesPage()
        self.toasts.set_child(page)

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
        page.add(self.filters_group)

        types_group = Adw.PreferencesGroup(
            title=_('Show Notifications For'))
        excluded = set(settings().get_strv(EXCLUDED_TYPES_KEY))
        for kind, label in NOTIFICATION_TYPES:
            row = Adw.SwitchRow(title=label, active=kind not in excluded)
            row.add_prefix(Gtk.Image(icon_name=type_icon(kind)))
            connect_weak(row, 'notify::active', self._on_type_switched, kind)
            self.type_rows[kind] = row
            types_group.add(row)
        page.add(types_group)

        self.connect('closed', lambda _d: self.save())
        self.load()

    # Policy

    def load(self):
        self.client.request_async('GET', '/api/v2/notifications/policy',
                                  callback=self.on_loaded)

    def on_loaded(self, client, result, _user_data=None):
        try:
            policy = client.request_finish(result)
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

        pages.call(self, self.client, 'PATCH',
                   '/api/v2/notifications/policy',
                   on_done=saved,
                   error_heading=_('Could not save notification settings'),
                   json_body=payload)

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
