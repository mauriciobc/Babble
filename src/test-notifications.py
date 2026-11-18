#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the Babble notifications widgets with documented API-shape
fixtures (docs.joinmastodon.org/entities/Notification) and performs one
LIVE anonymous fetch through MastodonClient.

The /api/v1/notifications endpoints always require authentication, so
the live fetch proves the transport plus the error-page path: the HTTP
unauthorized response arrives as a GLib.Error with the HTTP status code.
Rendering with real server payloads is proven by the timeline test,
which drives the exact StatusRow/ContentLabel path embedded inside
notification rows here.

Exits 77 (skip) when the display or network is unavailable."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.api import notifications as api_notifications  # noqa: E402
from src.mastodon import MastodonClient  # noqa: E402
from src import notificationcopy  # noqa: E402
from src.notificationmodel import (  # noqa: E402
    NotificationItem, NotificationModel, day_heading, notification_detail,
    notification_title)
from src.notificationrow import NotificationPost, NotificationRow  # noqa: E402
from src.notifications import (NotificationsPage)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


ACTOR = {'id': '9', 'acct': 'alice', 'display_name': 'Alice Doe',
         'url': 'https://fosstodon.org/@alice'}
ACTOR2 = {'id': '10', 'acct': 'bob', 'display_name': 'Bob',
          'url': 'https://fosstodon.org/@bob'}
ACTOR3 = {'id': '11', 'acct': 'carol', 'display_name': 'Carol',
          'url': 'https://fosstodon.org/@carol'}
STATUS = {'id': '42', 'content': '<p>My original post</p>',
          'account': {'id': '1', 'acct': 'me', 'display_name': 'Me'},
          'created_at': '2026-10-01T10:00:00.000Z'}


def make_notification(id, type, account, created, status=None):
    """Shape documented at docs.joinmastodon.org/entities/Notification:
    {id, type, created_at, account, status?}."""
    data = {'id': id, 'type': type, 'created_at': created,
            'account': account}
    if status is not None:
        data['status'] = status
    return data


MENTION = make_notification('100', 'mention', ACTOR3,
                            '2026-10-05T12:05:00.000Z',
                            {**STATUS, 'id': '50',
                             'content': '<p>Hello @me</p>'})
FOLLOW = make_notification('99', 'follow', ACTOR3,
                           '2026-10-05T12:04:00.000Z')
FAV_A = make_notification('98', 'favourite', ACTOR2,
                          '2026-10-05T12:03:00.000Z', STATUS)
FAV_B = make_notification('97', 'favourite', ACTOR3,
                          '2026-10-05T12:02:00.000Z', STATUS)
FAV_C = make_notification('96', 'favourite', ACTOR,
                          '2026-10-05T12:01:00.000Z', STATUS)
REBLOG = make_notification('95', 'reblog', ACTOR2,
                           '2026-10-05T11:00:00.000Z', STATUS)
POLL = make_notification('94', 'poll', ACTOR, '2026-10-05T10:30:00.000Z',
                         {**STATUS, 'id': '52'})
EDIT = make_notification('93', 'update', ACTOR2, '2026-10-05T10:20:00.000Z',
                         {**STATUS, 'id': '53'})
REQUEST = make_notification('92', 'follow_request', ACTOR,
                            '2026-10-05T10:10:00.000Z')
SIGNUP = make_notification('91', 'admin.sign_up', ACTOR,
                           '2026-10-05T10:00:00.000Z')
MYSTERY = make_notification('90', 'future.mystery', ACTOR,
                            '2026-10-05T09:00:00.000Z')

# Mastodon 4.6 notification types that carry a `collection` object.
COLLECTION = {'id': '300', 'name': 'Nice accounts',
              'description': 'These accounts are very nice'}
COLLECTION_ADD = make_notification('89', 'added_to_collection', ACTOR,
                                   '2026-10-05T08:00:00.000Z')
COLLECTION_ADD['collection'] = COLLECTION
COLLECTION_UPDATE = make_notification('88', 'collection_update', ACTOR2,
                                      '2026-10-05T07:00:00.000Z')
COLLECTION_UPDATE['collection'] = COLLECTION

FIXTURES = [MENTION, FOLLOW, FAV_A, FAV_B, FAV_C, REBLOG, POLL, EDIT,
            REQUEST, SIGNUP, MYSTERY]

# Single-actor group then a two-actor cross-page merge.
FAV_D = make_notification('945', 'favourite', ACTOR3,
                          '2026-10-05T11:30:00.000Z', STATUS)
from testkit import notificationskit as kit  # noqa: E402
from testkit.notificationskit import body_text, collect, notification_rows  # noqa: E402

# Unit checks: grouping, pagination, titles.
check('single title',
      notification_title(NotificationItem(FAV_A))
      == 'Bob favorited your post')

group = NotificationItem(FAV_A)
group.merge(FAV_B)
group.merge(FAV_C)
check('grouped title three actors',
      notification_title(group) == '3 people favorited your post')

group2 = NotificationItem(FAV_A)
group2.merge(FAV_D)
check('grouped title two actors',
      notification_title(group2) == '2 people favorited your post')

model = NotificationModel()
check('append page groups same status',
      model.append_page([FAV_A, FAV_B]))
check('grouped into one row', model.get_n_items() == 1)
check('group has two actors', len(model.get_item(0).actors) == 2)
check('cross-type cluster is one row',
      model.append_page([FAV_C, REBLOG])
      and model.get_n_items() == 1)
check('reblog and favourite share one row', (
    model.get_item(0).kinds == ['favourite', 'favourite', 'favourite',
                                'reblog']
    and model.get_item(0).title
    == '4 people boosted and favorited your post'))
check('unrelated statuses appended as separate rows',
      model.append_page([POLL, EDIT]) and model.get_n_items() == 3)
check('duplicate page adds nothing', not model.append_page([FAV_A]))
check('empty page exhausts', not model.append_page([]))
check('exhausted flag set', model.exhausted)
check('ids tracked', model.newest_id == '98' and model.oldest_id == '93')

# Mastodon ids are strings cast from integers of varying digit length, so
# a string comparison orders '99999999' after '100000001'. The cursors
# feed min_id/max_id and the read marker, so they must compare as numbers.
_ids = NotificationModel()
_ids.append_page([make_notification(str(_i), 'mention', ACTOR,
                                    '2026-10-05T11:30:00.000Z', STATUS)
                  for _i in (100000001, 100000000, 99999999, 99999998)])
check('newest id compares numerically', _ids.newest_id == '100000001')
check('oldest id compares numerically', _ids.oldest_id == '99999998')
check('row id is the numeric newest',
      _ids.get_item(0).notification_id == '100000001')

# The cursors widen across pages too: an older page whose ids are one
# digit shorter must still lower oldest_id, and a newer page whose ids
# are longer must raise newest_id.
_wide = NotificationModel()
_wide.append_page([make_notification(str(_i), 'mention', ACTOR,
                                     '2026-10-05T11:30:00.000Z', STATUS)
                   for _i in (100000001, 100000000)])
_wide.append_page([make_notification(str(_i), 'favourite', ACTOR2,
                                     '2026-10-04T11:30:00.000Z', STATUS)
                   for _i in (99999999, 99999998)])
check('oldest id lowers across a digit boundary',
      _wide.oldest_id == '99999998')
_narrow = NotificationModel()
_narrow.append_page([make_notification(str(_i), 'mention', ACTOR,
                                       '2026-10-05T11:30:00.000Z', STATUS)
                     for _i in (99999999, 99999998)])
_narrow.prepend_page([make_notification('100000001', 'favourite', ACTOR2,
                                        '2026-10-06T11:30:00.000Z',
                                        STATUS)])
check('newest id rises across a digit boundary',
      _narrow.newest_id == '100000001')

# The unread badge asks "is the marker at or past the newest id?" and
# that is a numeric question too.
import types  # noqa: E402
from src.notificationctrl import NotificationController  # noqa: E402


def _marker_case(last_read, newest):
    calls = []
    emitted = []
    real = api_notifications.unread_count_async
    api_notifications.unread_count_async = \
        lambda *args, **kwargs: calls.append(args)
    try:
        stub = types.SimpleNamespace(
            model=types.SimpleNamespace(newest_id=newest),
            _marked_read=False, _unread=5, _applied_excluded=(),
            client=types.SimpleNamespace(
                is_supported=lambda *_a, **_k: True),
            emit=lambda *args: emitted.append(args),
            on_unread_count=None)
        stub.request_unread_count = types.MethodType(
            NotificationController.request_unread_count, stub)
        marker_api = sys.modules['src.api.markers']
        real_finish = marker_api.get_finish
        marker_api.get_finish = lambda client, result: {
            'notifications': {'last_read_id': last_read}}
        try:
            NotificationController._on_markers_before_unread(
                stub, None, None)
        finally:
            marker_api.get_finish = real_finish
    finally:
        api_notifications.unread_count_async = real
    return stub._marked_read, bool(calls)


check('marker past the newest id is caught up across digit lengths',
      _marker_case('100000001', '99999999') == (True, False))
check('marker behind the newest id still asks the server',
      _marker_case('99999999', '100000001') == (False, True))
check('marker equal to the newest id is caught up',
      _marker_case('42', '42') == (True, False))

# Date sections: one section per calendar day.
check('one section per day',
      model.get_section(0)[:2] == (0, 3))
check('date heading derived from the day key',
      day_heading(model.get_item(0).date_key)[0] in ('Oct 5', 'out 5')
      and day_heading(model.get_item(2).date_key)[0] in ('Oct 5', 'out 5'))

fresh = NotificationModel()
check('prepend page', fresh.prepend_page([FAV_A, FAV_B]) == 2)
check('prepend keeps newest first',
      fresh.get_item(0).notification_id == '98')
check('prepend dedupes', fresh.prepend_page([FAV_A]) == 0)
fresh.clear()
check('clear resets', fresh.get_n_items() == 0
      and fresh.oldest_id is None and not fresh.exhausted)

# A refresh prepends every new row in one splice. Inserting them one at a
# time reflowed the list once per row (and, before headings were derived,
# walked the whole model once per row too).
refresh_model = NotificationModel()
refresh_model.append_page([
    make_notification(str(1019 - i), 'favourite', ACTOR,
                      '2026-10-05T11:%02d:00.000Z' % (19 - i),
                      {**STATUS, 'id': str(7000 + i)})
    for i in range(20)])
changes = []
refresh_model.connect('items-changed',
                      lambda _m, pos, removed, added:
                      changes.append((pos, removed, added)))
check('a refresh adds its rows',
      refresh_model.prepend_page([
          make_notification(str(2039 - i), 'mention', ACTOR2,
                            '2026-10-05T12:%02d:00.000Z' % (39 - i),
                            {**STATUS, 'id': str(8000 + i)})
          for i in range(40)]) == 40)
check('a refresh is one splice',
      len(changes) == 1 and changes[0] == (0, 0, 40))
check('a refresh keeps newest first',
      refresh_model.get_item(0).notification_id == '2039'
      and refresh_model.get_item(40).notification_id == '1019')

check('mention title', notification_title(
    NotificationItem(MENTION)) == 'Carol mentioned you')
check('follow title', notification_title(
    NotificationItem(FOLLOW)) == 'Carol followed you')
check('every list title has a desktop pair',
      set(notificationcopy.LIST_TITLES)
      <= set(notificationcopy.DESKTOP_TITLES))
check('unknown type falls back to generic title',
      notification_title(
          NotificationItem(MYSTERY)) == 'Alice Doe interacted with you')
check('added to collection names the collection',
      notification_title(NotificationItem(COLLECTION_ADD))
      == 'Alice Doe added you to Nice accounts')
check('collection update names the collection',
      notification_title(NotificationItem(COLLECTION_UPDATE))
      == 'Bob updated Nice accounts')
check('a collection notification carries its description',
      notification_detail(NotificationItem(COLLECTION_ADD), '')
      == 'These accounts are very nice')
collection_model = NotificationModel()
collection_model.append_page([COLLECTION_ADD, COLLECTION_UPDATE])
check('collection notifications stay separate rows',
      collection_model.get_n_items() == 2)
check('timestamp rendered',
      NotificationItem(FAV_A).timestamp != '')

# Widget rendering with the same fixtures through the real factory path.
Adw.init()
Gtk.init()

client = MastodonClient('https://fosstodon.org')  # no token: idle client

# Built at sign-in and loaded while hidden, the page builds no rows: the
# window keeps it in a stack behind the timeline, and a homogeneous stack
# measures its hidden children too.
hidden = NotificationsPage(client)
shown_child = Gtk.Label(label='timeline')
switcher = Gtk.Stack(hhomogeneous=True, vhomogeneous=True)
switcher.add_named(shown_child, 'timeline')
switcher.add_named(hidden, 'notifications')
switcher.set_visible_child_name('timeline')
hidden_window = Gtk.Window(child=switcher, default_width=600,
                           default_height=900)
hidden_window.present()
hidden.model.append_page(FIXTURES)
hidden.stack.set_visible_child_name('list')
deadline = GLib.get_monotonic_time() + 1000000
while GLib.get_monotonic_time() < deadline:
    if not GLib.MainContext.default().iteration(False):
        GLib.usleep(5000)


check('a hidden notifications page builds no rows',
      hidden.model.get_n_items() == 8
      and not notification_rows(hidden.list_view))
switcher.set_visible_child_name('notifications')
deadline = GLib.get_monotonic_time() + 5000000
while GLib.get_monotonic_time() < deadline \
        and not notification_rows(hidden.list_view):
    if not GLib.MainContext.default().iteration(False):
        GLib.usleep(5000)
check('showing it builds them',
      bool(notification_rows(hidden.list_view)))
hidden_window.destroy()
del hidden, switcher, shown_child, hidden_window

page = NotificationsPage(client)
page.model.append_page(FIXTURES)
page.stack.set_visible_child_name('list')

window = Gtk.Window()
window.set_child(page)
window.set_default_size(600, 900)
window.present()


# Activation: rows push a real ThreadPage (status types) or profile
# page (follow types) onto a navigation view on the root widget.
class NavRoot(Gtk.Window):
    pass


nav_root = NavRoot()
nav_root.navigation_view = Adw.NavigationView()
window.set_child(None)
nav_root.set_child(page)
nav_root.set_default_size(600, 900)
nav_root.present()


row_widgets = []
loop = GLib.MainLoop()


def poll():
    collect(page.list_view, row_widgets)
    if any(isinstance(w, NotificationRow) for w in row_widgets) \
            and any(w.get_visible()
                    for w in row_widgets if isinstance(w, NotificationRow)):
        loop.quit()
    return True


GLib.timeout_add(200, poll)
GLib.timeout_add_seconds(20, loop.quit)
loop.run()

row_widgets = []
collect(page.list_view, row_widgets)

labels = [w.get_text() or '' for w in row_widgets if isinstance(w, Gtk.Label)]
check('model bound to list', page.model.get_n_items() == 8)
check('cross-type cluster title rendered',
      any('boosted and favorited your post' in t for t in labels))
check('mention title rendered',
      any('mentioned you' in t for t in labels))
check('follow title rendered', any('followed you' in t for t in labels))
check('embedded post snippet rendered',
      any('My original post' in t for t in labels))
check('mention snippet rendered', any('Hello @me' in t for t in labels))
check('no raw html leaks', all('<p>' not in t for t in labels))
check('embedded post snapshots rendered', sum(
    1 for w in row_widgets if isinstance(w, NotificationPost)) >= 4)
check('embedded posts carry no nested cards or actions', not any(
    'status-row' in w.get_css_classes()
    or 'status-actions' in w.get_css_classes() for w in row_widgets))
check('notification cards rendered', any(
    'notification-row' in w.get_css_classes() for w in row_widgets))
check('date heading rendered', any(
    'heading' in w.get_css_classes() for w in row_widgets
    if isinstance(w, Gtk.Label)))
check('date heading appears once for the whole day', sum(
    1 for w in row_widgets if isinstance(w, Gtk.Label)
    and w.get_text() in ('Oct 5', 'out 5')) == 1)
check('embedded post snapshots styled', any(
    'notification-post' in w.get_css_classes() for w in row_widgets))
check('per-person rows under clusters', any(
    w.people_box is not None and w.people_box.get_first_child() is not None
    and w.cluster_expander is not None and w.cluster_expander.get_visible()
    for w in row_widgets if isinstance(w, NotificationRow)))
check('avatar stacks rendered', any(
    'notification-avatar-stack' in w.get_css_classes()
    for w in row_widgets))
stack_avatars = []
for widget in row_widgets:
    if ('notification-avatar-stack' in widget.get_css_classes()):
        child = widget.get_first_child()
        while child is not None:
            stack_avatars.append(child)
            child = child.get_next_sibling()
check('stacked avatars with badge overlays', all(
    isinstance(slot.get_child(), Adw.Avatar)
    and any(c == 'notification-badge' for c in
            slot.get_last_child().get_css_classes())
    for slot in stack_avatars)
    and len(stack_avatars) >= 4)
check('cluster expander rendered, collapsed and named', all(
    not w.get_expanded() and w.get_tooltip_text()
    for w in row_widgets if isinstance(w, Gtk.Expander)
    and w.get_visible()) and any(
    isinstance(w, Gtk.Expander) and w.get_visible()
    for w in row_widgets))
check('no colour-only unread marker or kind classes', not any(
    css.startswith(('notification-marker', 'notification-icon-',
                    'notification-kind-'))
    for w in row_widgets for css in w.get_css_classes()))
check('avatars rendered',
      sum(1 for w in row_widgets if isinstance(w, Adw.Avatar)) >= 8)
check('type icons rendered', any(
    w.get_icon_name() == 'starred-symbolic'
    for w in row_widgets if isinstance(w, Gtk.Image)))
check('unknown type icon falls back', any(
    w.get_icon_name() == 'user-info-symbolic'
    for w in row_widgets if isinstance(w, Gtk.Image)))
check('rows keyboard focusable', all(
    w.get_focusable() for w in row_widgets if isinstance(w, NotificationRow)))

# 'Read More': long plain content collapses behind a flat button.
LONG_MENTION = make_notification(
    '101', 'mention', ACTOR2, '2026-10-05T12:30:00.000Z',
    {**STATUS, 'id': '54',
     'content': '<p>' + ('TootThursday roundup: today I posted my '
                         'backup scripts, a small noise-cancelling test '
                         'and a couple of Argentina photos, plus the '
                         'usual morning walk report. ') * 2 + '</p>'})
long_item = NotificationItem(LONG_MENTION)
long_row = NotificationRow()
long_row.bind(long_item, client)
long_post = long_row.post
check('long content collapses behind Read More button',
      long_post.more_button.get_visible()
      and long_post.more_button.get_label() == 'Read More')


from src.statustext import parse_content  # noqa: E402
plain_full = ''.join(
    s['text'] for s in parse_content(LONG_MENTION['status']['content'])
    if s['text'] != '\n')


check('Read More truncates the >240-char fixture',
      len(plain_full) > NotificationPost.READ_MORE_CHARS
      and len(body_text(long_post)) < len(plain_full)
      and body_text(long_post).count('usual morning walk') == 1)
long_post.more_button.emit('clicked')
check('Read More shows the full post',
      body_text(long_post).count('usual morning walk') == 2
      and not long_post.more_button.get_visible())
LINKED = make_notification(
    '102', 'mention', ACTOR2, '2026-10-05T12:31:00.000Z',
    {**STATUS, 'id': '55', 'emojis': [{'shortcode': 'blobcat',
                                       'url': 'https://x/e.png'}],
     'content': '<p>Hi :blobcat: <a href="https://example.org/a?b=1&amp;c=2">'
                'https://example.org/a</a> :unknown:</p>'})
linked_row = NotificationRow()
linked_row.bind(NotificationItem(LINKED), client)
linked_text = linked_row.post.content
check('post body links stay activatable',
      linked_text.get_use_markup()
      and 'href="https://example.org/a?b=1&amp;c=2"'
      in linked_text.get_label())
check('declared custom emoji shortcodes are dropped, others kept',
      ':blobcat:' not in linked_text.get_text()
      and ':unknown:' in linked_text.get_text())
# The row holds no section state; a standalone row draws no heading.
check('standalone row has no stamped section',
      not hasattr(long_item, 'section_title') and not hasattr(
          long_item, 'section_weekday'))


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all notifications render checks passed')
