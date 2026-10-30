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
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gio, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src import notificationcopy, pages  # noqa: E402
from src.notifications import (NotificationItem, NotificationModel,  # noqa: E402
                               NotificationPost, NotificationRow,
                               NotificationsPage, day_heading,
                               notification_detail, notification_title)

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
client.notifications_mark_read_finish = lambda result: {}

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


def notification_rows(widget):
    found = [widget] if isinstance(widget, NotificationRow) else []
    child = widget.get_first_child()
    while child is not None:
        found += notification_rows(child)
        child = child.get_next_sibling()
    return found


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


def collect(widget, out):
    out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect(child, out)
        child = child.get_next_sibling()


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


def visible_text(widget):
    out = []
    collect(widget, out)
    return ' '.join(w.get_text() or '' for w in out
                    if isinstance(w, Gtk.Label))


from src.statustext import parse_content  # noqa: E402
plain_full = ''.join(
    s['text'] for s in parse_content(LONG_MENTION['status']['content'])
    if s['text'] != '\n')


def body_text(post):
    return post.content.get_text()


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

# Lazy parts: a row builds the post and the people strip only when a
# notification needs them, and keeps them for later binds once built.
follow_row = NotificationRow()
follow_row.bind(NotificationItem(FOLLOW), client)
check('a row without a status builds no post snapshot',
      follow_row.post is None)
check('a single-actor row builds no people strip',
      follow_row.cluster_expander is None)
mention_row = NotificationRow()
mention_row.bind(NotificationItem(MENTION), client)
check('a status row builds its post on first bind',
      isinstance(mention_row.post, NotificationPost))
check('a status row has no people strip until merged',
      mention_row.cluster_expander is None)
merged_item = NotificationItem(FAV_A)
merged_item.merge(FAV_B)
merged_row = NotificationRow()
merged_row.bind(merged_item, client)
check('a merged row builds its people strip on first bind',
      merged_row.cluster_expander is not None
      and merged_row.cluster_expander.get_visible()
      and merged_row.people_box.get_first_child() is not None)
merged_row.bind(NotificationItem(FOLLOW), client)
check('a recycled row hides the strip it keeps',
      not merged_row.cluster_expander.get_visible()
      and merged_row.avatar.get_visible()
      and merged_row.people_box.get_first_child() is None)
mention_row.bind(NotificationItem(FOLLOW), client)
check('a recycled row hides the post it keeps',
      mention_row.post is not None
      and not mention_row.post.get_visible())

stack = nav_root.navigation_view.get_navigation_stack()
page.on_row_activated(page.list_view, 0)  # mention row -> ThreadPage
check('mention row pushes thread page',
      stack.get_n_items() == 1 and 'Thread' in stack[0].get_title())
mention_id = page.model.get_item(0).status_item.status_id
follow_index = [i for i in range(page.model.get_n_items())
                if page.model.get_item(i).kind == 'follow'][0]
import src.profile  # noqa: E402
profile_calls = []
src.profile.push_profile = lambda view, client, account: \
    profile_calls.append(account['acct'])
page.on_row_activated(page.list_view, follow_index)
check('follow row opens the actor profile', profile_calls == ['carol'])
check('thread page tracks activated status', mention_id == '50')

# A grouped row with no post (two new followers) has no single account
# to open. Activating it toggles the people list, the chevron and the
# avatar strip are the expander's own interaction, and a named person
# opens that person. The expander's click gesture never claims its
# sequence, so a click on it used to reach the row's activation gesture
# as well: expanding the group also opened the first follower.
cluster_page = NotificationsPage(client)
cluster_page.model.append_page([
    make_notification('300', 'follow', ACTOR, '2026-10-06T12:00:00.000Z'),
    make_notification('299', 'follow', ACTOR2, '2026-10-06T11:59:00.000Z')])
cluster_page.stack.set_visible_child_name('list')
cluster_window = NavRoot()
cluster_window.navigation_view = Adw.NavigationView()
cluster_window.set_child(cluster_page)
cluster_window.set_default_size(600, 900)
cluster_window.present()


def cluster_rows():
    return [w for w in notification_rows(cluster_page.list_view)
            if w.get_mapped() and w.cluster_expander is not None
            and w.cluster_expander.get_visible()]


def pump_until(predicate, seconds=5):
    deadline = GLib.get_monotonic_time() + int(seconds * 1000000)
    while GLib.get_monotonic_time() < deadline and not predicate():
        if not GLib.MainContext.default().iteration(False):
            GLib.usleep(5000)


pump_until(lambda: bool(cluster_rows()))
cluster_row = cluster_rows()[0]
check('grouped followers merge into one row',
      cluster_page.model.get_n_items() == 1
      and len(cluster_page.model.get_item(0).actors) == 2)
first_person = cluster_row.people_box.get_first_child()
check('people rows are buttons that name the person',
      isinstance(first_person, Gtk.Button)
      and isinstance(first_person.get_child().get_last_child(), Gtk.Label)
      and first_person.get_child().get_last_child().get_text()
      == 'Alice Doe')
profile_calls.clear()
cluster_page.on_row_activated(cluster_page.list_view, 0)
check('activating a grouped follow row expands it',
      cluster_row.cluster_expander.get_expanded())
check('expanding a grouped follow row opens no profile',
      profile_calls == [])
pump_until(lambda: cluster_row.people_revealer.get_child_revealed())


def find_named(widget, name):
    if type(widget).__name__ == name:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_named(child, name)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def point_in_expander(widget):
    ok, rect = widget.compute_bounds(cluster_row.cluster_expander)
    return rect.origin.x + rect.size.width / 2, \
        rect.origin.y + rect.size.height / 2


check('the avatar strip is an expander title click',
      cluster_row._in_cluster_title(
          *point_in_expander(cluster_row.avatar_stack)))
# The expander's arrow is a private GtkBuiltinIcon; it is the widget the
# title gesture must claim, since its release reaches the row as well.
arrow = find_named(cluster_row.cluster_expander, 'GtkBuiltinIcon')
check('the expander arrow is an expander title click',
      arrow is not None
      and cluster_row._in_cluster_title(*point_in_expander(arrow)))
check('the revealed people list is not an expander title click',
      not cluster_row._in_cluster_title(
          *point_in_expander(cluster_row.people_box)))
profile_calls.clear()
cluster_row.people_box.get_first_child().emit('clicked')
check('a named person opens that person', profile_calls == ['alice'])
cluster_page.on_row_activated(cluster_page.list_view, 0)
check('activating the grouped row again collapses it',
      not cluster_row.cluster_expander.get_expanded())
cluster_window.destroy()

# A page of new followers asks for their relationships once, and the rows
# whose answer comes back are re-emitted so they rebind with Follow Back.
# The first page load used to call a method that did not exist.
class FollowClient(MastodonClient):
    """A signed-in client that records its requests and never leaves the
    machine."""

    def __init__(self):
        super().__init__('https://fosstodon.org')
        self.token = 'token'
        self.requests = []
        self.reply = None

    def markers_async(self, *args, **kwargs):
        pass

    def notifications_unread_count_async(self, *args, **kwargs):
        pass

    def notifications_async(self, *args, **kwargs):
        pass

    def notifications_finish(self, result):
        return result

    def request_async(self, method, path, callback=None, params=None, **kw):
        self.requests.append((method, path, params))
        if path == '/accounts/relationships':
            self.reply = callback

    def asked(self):
        """The relationship lookups made so far."""
        return [r for r in self.requests
                if r[1] == '/accounts/relationships']

    def request_finish(self, result):
        return result


follow_client = FollowClient()
follow_page = NotificationsPage(follow_client)
follow_page.on_page_loaded(follow_client, [
    make_notification('f1', 'follow', ACTOR, '2026-10-05T12:09:00.000Z'),
    MENTION,
    make_notification('f2', 'follow', ACTOR2, '2026-10-05T12:03:00.000Z')])
check('a page of followers asks for their relationships in one request',
      len(follow_client.asked()) == 1
      and follow_client.asked()[0][0] == 'GET'
      and sorted(follow_client.asked()[0][2]['id[]']) == ['10', '9'])
rebound = []
follow_page.model.connect(
    'items-changed', lambda _m, position, _r, _a: rebound.append(position))
follow_client.reply(follow_client, [
    {'id': '9', 'following': False, 'requested': False},
    {'id': '10', 'following': True, 'requested': False}])
follow_kinds = [follow_page.model.get_item(i).kind
                for i in range(follow_page.model.get_n_items())]
check('only the follow rows are rebound by the answer',
      sorted(rebound) == [i for i, k in enumerate(follow_kinds)
                          if k == 'follow'] and len(rebound) == 2)
follow_client.requests.clear()
# A follow that lands on top of another follow merges into one
# multi-actor row, which never shows Follow Back, so it is not asked for.
follow_page.on_stream_notification(
    make_notification('f3', 'follow', ACTOR3, '2026-10-05T12:20:00.000Z'))
check('a follower merged into a group is not asked for',
      follow_client.asked() == [])
# Between two mentions it stands alone, and is asked for.
follow_page.on_stream_notification(
    {**MENTION, 'id': 'm2', 'created_at': '2026-10-05T12:30:00.000Z'})
follow_page.on_stream_notification(
    make_notification('f4', 'follow', ACTOR, '2026-10-05T12:40:00.000Z'))
follow_page.on_stream_notification(
    {**MENTION, 'id': 'm3', 'created_at': '2026-10-05T12:50:00.000Z'})
follow_page.on_stream_notification(
    make_notification('f5', 'follow', ACTOR3, '2026-10-05T12:55:00.000Z'))
check('a streamed follower standing alone is asked for',
      [r[2]['id[]'] for r in follow_client.asked()] == [['11']])

# Filter switch clears and reloads through the client path: stub the
# client transport so the page's filter injection actually runs.
relay = {}


def fake_notifications_async(params, callback):
    relay['params'] = dict(params)
    relay['callback'] = callback


client.notifications_async = fake_notifications_async
page.filter_group.set_active_name('mentions')
check('mentions filter requests types[]=mention',
      relay['params'].get('types[]') == ['mention'])
page.filter_group.set_active_name('all')
check('all filter drops the types param',
      relay['params'].get('types[]') is None)
check('filter is a named toggle group', isinstance(
    page.filter_group, Adw.ToggleGroup)
    and page.filter_group.get_n_toggles() == 2
    and page.filter_group.get_toggle_by_name('mentions') is not None)
check('filter stays outside the stack so empty states keep it',
      page.filter_group.get_ancestor(Gtk.Stack) is None)

# Refresh: the public hook the window calls; an empty list reloads from
# scratch.
page.model.clear()
relay.clear()
page.model.loading = False
page.refresh()
check('refresh on an empty list loads the newest page',
      relay['params'].get('limit') == 20
      and page.stack.get_visible_child_name() == 'loading')

# Infinite scroll: load_older pages with max_id.
page.model.clear()
page.model.oldest_id = '90'
page.load_older()
check('older page requests max_id',
      relay['params'].get('max_id') == '90'
      and 'min_id' not in relay['params'])

# Opening the page asks for what is newer than its top. A notification
# that arrived while the stream was down is announced by the poller but
# never merged into the rows, so without this the page stayed stale
# until a manual refresh. What is on screen is marked read at once, so a
# failed catch-up cannot leave the badge up, and the answer's marker
# waits for the first rather than racing it.
class VisitClient(FollowClient):
    """A signed-in client whose notification requests the test answers."""

    def __init__(self):
        super().__init__()
        self.token = ''            # keep the constructor from loading
        self.listed = []
        self.markers = []
        self.marker_callbacks = []
        self.pending = None
        self.other_requests = []

    def request_async(self, method, path, callback=None, params=None, **kw):
        self.other_requests.append(path)

    def notifications_async(self, params=None, callback=None, **kwargs):
        self.listed.append(dict(params or {}))
        self.pending = callback

    def notifications_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def notifications_mark_read_async(self, last_read_id, callback=None,
                                      **kwargs):
        self.markers.append(last_read_id)
        self.marker_callbacks.append(callback)

    def notifications_mark_read_finish(self, result):
        return {}

    def finish_marker(self):
        self.marker_callbacks.pop(0)(self, None)


def visit_setup():
    client = VisitClient()
    page = NotificationsPage(client)
    client.token = 'token'
    page.model.append_page([MENTION])
    page.stack.set_visible_child_name('list')
    window = Gtk.Window(child=page, default_width=600, default_height=900)
    window.present()
    deadline = GLib.get_monotonic_time() + 5000000
    while not page.get_mapped() and GLib.get_monotonic_time() < deadline:
        if not GLib.MainContext.default().iteration(False):
            GLib.usleep(5000)
    client.listed.clear()
    client.markers.clear()
    return client, page, window


visit_client, visit_page, visit_window = visit_setup()
visit_page.on_shown()
check('opening the page asks for what is newer than its top',
      bool(visit_client.listed)
      and visit_client.listed[-1].get('min_id') == '100')
check('opening the page asks for nothing else',
      visit_client.other_requests == [])
check('opening the page marks what is on screen read at once',
      visit_client.markers == ['100'])
visit_client.pending(visit_client, [
    make_notification('101', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS)])
check('the notification that missed the stream is now first',
      visit_page.model.get_item(0).notification_id == '101')
check('the second marker waits for the first',
      visit_client.markers == ['100'])
visit_client.finish_marker()
check('the reloaded top is marked read once the first marker lands',
      visit_client.markers == ['100', '101'])
visit_window.destroy()
del visit_page, visit_client, visit_window

# A catch-up that fails is quiet: the reader asked for nothing, so no
# error toast, and the list was already marked read.
visit_client, visit_page, visit_window = visit_setup()
reported = []
real_report_error = pages.report_error
pages.report_error = lambda *args, **kwargs: reported.append(args)
visit_page.on_shown()
visit_client.pending(visit_client, GLib.Error('offline'))
pages.report_error = real_report_error
check('a failed catch-up raises no error toast', reported == [])
check('a failed catch-up still leaves the list marked read',
      visit_client.markers == ['100'])
visit_window.destroy()
del visit_page, visit_client, visit_window

# An empty list is caught up without the loading spinner.
visit_client, visit_page, visit_window = visit_setup()
visit_page.model.clear()
visit_page.stack.set_visible_child_name('empty')
visit_page.on_shown()
check('catching up an empty list keeps the empty page, not a spinner',
      visit_page.stack.get_visible_child_name() == 'empty'
      and bool(visit_client.listed))
visit_client.pending(visit_client, GLib.Error('offline'))
check('a failed catch-up of an empty list keeps it empty',
      visit_page.stack.get_visible_child_name() == 'empty')
visit_window.destroy()
del visit_page, visit_client, visit_window

# A batch from the poller is one merge: one marker, not one per item.
visit_client, visit_page, visit_window = visit_setup()
visit_page._shown = True
visit_page.merge_notifications([
    make_notification('102', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS),
    make_notification('103', 'follow', ACTOR,
                      '2026-10-05T14:00:00.000Z')])
check('a merged batch sends one marker, for its newest id',
      visit_client.markers == ['103'])
check('a merged batch is newest first',
      visit_page.model.get_item(0).notification_id == '103')
visit_window.destroy()
del visit_page, visit_client, visit_window

# Merged into a page whose first page never landed, a poll would seed a
# list that only ever asks for newer: the page loads instead.
visit_client, visit_page, visit_window = visit_setup()
visit_page.model.clear()
visit_page.stack.set_visible_child_name('error')
visit_page.merge_notifications([
    make_notification('104', 'mention', ACTOR2,
                      '2026-10-05T13:00:00.000Z', STATUS)])
check('a poll into a never-loaded page loads it instead of seeding it',
      visit_page.model.get_n_items() == 0
      and visit_client.listed and 'min_id' not in visit_client.listed[-1])
visit_window.destroy()
del visit_page, visit_client, visit_window

# A merge that moves the top row to a newer day changes where the
# sections fall without adding a row there, so the model says so: the
# heading of the row after it is otherwise never drawn.
section_model = NotificationModel()
section_model.append_page([
    make_notification('200', 'favourite', ACTOR, '2026-10-04T12:00:00.000Z',
                      STATUS),
    make_notification('199', 'mention', ACTOR2, '2026-10-04T11:00:00.000Z',
                      {**STATUS, 'id': '51'})])
section_changes = []
section_model.connect('sections-changed',
                      lambda _m, pos, n: section_changes.append((pos, n)))
section_model.prepend_page([
    make_notification('201', 'reblog', ACTOR2, '2026-10-05T12:00:00.000Z',
                      STATUS)])
check('a merge that moves the top row to a newer day reports sections',
      section_model.get_n_items() == 2 and section_changes == [(0, 2)])
del section_model

# Unread badge signal.
received = []
page.connect('unread-changed', lambda _p, count: received.append(count))
# The marker in flight is for the newest id the list holds.
if page.model.newest_id is None:
    page.model.append_page(FIXTURES)
page._marking = page.model.newest_id
page.on_marked_read(client, None)
check('badge signal emits 0 after mark-read', received == [0])
# One that was overtaken by a newer notification does not clear the badge:
# what it marked is not what the list now holds.
received.clear()
page._marking = '1'
page.on_marked_read(client, None)
check('an overtaken marker leaves the badge alone',
      received == [] and page.model.newest_id != '1')


# Desktop notification buttons and their app actions.
from src import desktopnotify  # noqa: E402

recorded_buttons = []
_orig_add_button = Gio.Notification.add_button_with_target


def _record_button(self, label, action, target):
    recorded_buttons.append((label, action, target.unpack()))
    return _orig_add_button(self, label, action, target)


Gio.Notification.add_button_with_target = _record_button


class StubSettings:
    def __init__(self, group):
        self.group = group

    def get_strv(self, key):
        return ['mention', 'follow', 'favourite']

    def get_boolean(self, key):
        return self.group


class StubClient:
    def __init__(self):
        self.calls = []

    def request_async(self, method, path, callback=None, **kw):
        self.calls.append((method, path))


class StubWindow:
    def __init__(self, group=False):
        self.settings = StubSettings(group)
        self.account = {'id': '1', 'instance': 'https://fosstodon.org'}
        self.client = StubClient()
        self.replies = []
        self.switched = []
        self.presented = 0

    def get_visible(self):
        return False

    def present(self):
        self.presented += 1

    def open_compose_reply(self, status_id, handle, visibility='public'):
        self.replies.append((status_id, handle, visibility))

    def switch_account(self, key):
        self.switched.append(key)
        instance, _sep, aid = key.rpartition('#')
        self.account = {'id': aid, 'instance': instance}


class StubApp:
    def __init__(self, win):
        self.sent = []
        self.actions = {}
        self.win = win

    def send_notification(self, nid, note):
        self.sent.append((nid, note))

    def add_action(self, action):
        self.actions[action.get_name()] = action

    def get_windows(self):
        return [self.win]

    def ensure_window(self):
        return self.win


ME_KEY = 'https://fosstodon.org#1'
CREATED = '2026-10-05T12:00:00.000Z'
stub_win = StubWindow()
stub_app = StubApp(stub_win)
notifier = desktopnotify.DesktopNotifier(stub_win, stub_app)

recorded_buttons.clear()
notifier.handle(make_notification('d1', 'mention', ACTOR, CREATED,
                                  dict(STATUS, visibility='public')),
                force=True)
check('mention notification offers reply, favorite and boost',
      recorded_buttons == [('Reply', 'app.reply', (ME_KEY, '42')),
                           ('Favorite', 'app.favourite', (ME_KEY, '42')),
                           ('Boost', 'app.boost', (ME_KEY, '42'))])

recorded_buttons.clear()
notifier.handle(make_notification('d2', 'mention', ACTOR, CREATED,
                                  dict(STATUS, visibility='direct')),
                force=True)
check('direct mention offers reply but no boost',
      [b[1] for b in recorded_buttons] == ['app.reply', 'app.favourite'])

recorded_buttons.clear()
notifier.handle(make_notification('d3', 'follow', ACTOR, CREATED),
                force=True)
check('follow notification offers follow back',
      recorded_buttons == [('Follow Back', 'app.follow-back',
                            (ME_KEY, '9'))])

recorded_buttons.clear()
notifier.handle(make_notification('d4', 'favourite', ACTOR, CREATED,
                                  STATUS), force=True)
check('favourite of own post offers no buttons', recorded_buttons == [])

group_win = StubWindow(group=True)
group_notifier = desktopnotify.DesktopNotifier(group_win, StubApp(group_win))
recorded_buttons.clear()
group_notifier.handle(make_notification('g1', 'mention', ACTOR, CREATED,
                                        STATUS), force=True)
check('single-actor group still offers buttons', len(recorded_buttons) == 3)
recorded_buttons.clear()
group_notifier.handle(make_notification('g2', 'mention', ACTOR2, CREATED,
                                        STATUS), force=True)
check('multi-actor grouped notification offers no buttons',
      recorded_buttons == [])

# Polling stands in for the stream while it is down, so what it finds has
# to reach the list as well: before this it only raised a desktop
# notification and the page stayed stale until a manual refresh.
class PollPage:
    def __init__(self):
        self.seen = []

    def merge_notifications(self, notifications):
        self.seen.append([n['id'] for n in notifications])


class PollWindow(StubWindow):
    def __init__(self):
        super().__init__()
        self.notifications_page = PollPage()


class PollClient:
    def request_finish(self, result):
        return result


poll_win = PollWindow()
poll_notifier = desktopnotify.DesktopNotifier(poll_win, StubApp(poll_win))
poll_notifier._polling_client = PollClient()
poll_notifier._on_polled(poll_notifier._polling_client, [
    make_notification('p1', 'poll', ACTOR, CREATED, STATUS),
    make_notification('p2', 'mention', ACTOR, CREATED, STATUS)], False)
check('a poll reaches the list too, as one batch',
      poll_win.notifications_page.seen == [['p1', 'p2']])

desktopnotify.install_app_actions(stub_app)
check('app actions registered',
      sorted(stub_app.actions) == ['boost', 'favourite', 'follow-back',
                                   'reply'])
stub_app.actions['favourite'].activate(GLib.Variant('(ss)', (ME_KEY, '42')))
stub_app.actions['follow-back'].activate(GLib.Variant('(ss)', (ME_KEY, '9')))
check('action handlers call the active client',
      stub_win.client.calls == [('POST', '/statuses/42/favourite'),
                                ('POST', '/accounts/9/follow')])

# Reply: the status kept from the mention prefills the composer.
stub_win.client.calls.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (ME_KEY, '42')))
check('reply presents the window', stub_win.presented == 1)
check('reply opens the composer with id, handle and visibility',
      stub_win.replies == [('42', STATUS['account']['acct'],
                            STATUS.get('visibility') or 'public')])
check('reply of a kept status needs no fetch', stub_win.client.calls == [])
# A status not kept from a notification is fetched first.
stub_win.replies.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (ME_KEY, '777')))
check('reply of an unknown status fetches it',
      stub_win.client.calls == [('GET', '/statuses/777')]
      and stub_win.replies == [])

# Another stored account: switch to it, then reply.
OTHER_KEY = 'https://mastodon.social#5'
_orig_find = desktopnotify.find_account_by_key
desktopnotify.find_account_by_key = lambda s, k: (
    {'id': '5', 'instance': 'https://mastodon.social',
     'display_name': 'Other'} if k == OTHER_KEY else None)
desktopnotify._remember_status(OTHER_KEY, dict(STATUS, id='60'))
stub_win.replies.clear()
stub_app.actions['reply'].activate(GLib.Variant('(ss)', (OTHER_KEY, '60')))
check('reply for another account switches to it',
      stub_win.switched == [OTHER_KEY])
check('reply after the switch opens the composer',
      stub_win.replies == [('60', STATUS['account']['acct'], 'public')])

# An account no longer stored: toast, no switch, no composer.
import src.pages  # noqa: E402
toasts = []
_orig_toast = src.pages.show_toast
src.pages.show_toast = lambda w, text, *a, **k: toasts.append(text)
stub_win.replies.clear()
stub_app.actions['reply'].activate(
    GLib.Variant('(ss)', ('https://gone.example#3', '61')))
check('reply for a gone account toasts instead',
      len(toasts) == 1 and stub_win.replies == []
      and stub_win.switched == [OTHER_KEY])
src.pages.show_toast = _orig_toast
desktopnotify.find_account_by_key = _orig_find
Gio.Notification.add_button_with_target = _orig_add_button


def _live_error_result(error):
    """Wrap an already-arrived GLib.Error as a Gio.Task result."""
    task = Gio.Task.new()
    task.return_error(error)
    return task


# The live anonymous fetch: /api/v1/notifications without a token.
live_client = MastodonClient('https://fosstodon.org')
live = {'error': None, 'data': None, 'done': False}


def on_live(c, result):
    try:
        live['data'] = c.notifications_finish(result)
    except GLib.Error as e:
        live['error'] = e
    finally:
        live['done'] = True
        live['loop'].quit()


live_loop = GLib.MainLoop()
GLib.idle_add(lambda: (live_client.notifications_async(callback=on_live),
                       False)[1])
live['loop'] = live_loop
GLib.timeout_add_seconds(45, live_loop.quit)
live_loop.run()

if not live['done']:
    print('SKIP - live notifications fetch timed out')
    sys.exit(77)

if live['data'] is None and live['error'] is None:
    print('SKIP - live fetch produced nothing (network unreachable)')
    sys.exit(77)

if live['error'] is not None:
    check('live anonymous fetch rejected with HTTP error',
          live['error'].code in (401, 403))
    # Drive the same live error through the page's real callback path.
    page.model.clear()
    page.stack.set_visible_child_name('loading')
    page.on_page_loaded(client, _live_error_result(live['error']))
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
    check('live 401 shows the error status page',
          page.stack.get_visible_child_name() == 'error')
    check('error page offers Try Again', page.error_page.get_child()
          is not None)
else:
    check('live notifications fetched', isinstance(live['data'], list))
    print('note - unexpected anonymous success: %d notifications'
          % len(live['data']))

# Dismissing one notification: the row goes at once, a grouped row sends one
# request per notification it holds, and a failure brings the row back where
# it was. The route is POST /notifications/:id/dismiss (Mastodon 1.3.0).
class DismissClient(FollowClient):
    def __init__(self):
        super().__init__()
        self.dismissed = []
        self.fail = set()

    def notifications_dismiss_async(self, notification_id, callback=None,
                                    **_kw):
        self.dismissed.append(notification_id)
        callback(self, notification_id)

    def notifications_dismiss_finish(self, result):
        if result in self.fail:
            raise GLib.Error('refused', 'mastodon-http-error-quark', 500)
        return {}


dismiss_client = DismissClient()
dismiss_page = NotificationsPage(dismiss_client)
dismiss_page.on_page_loaded(dismiss_client, [MENTION, FAV_A, FAV_B, FAV_C,
                                             FOLLOW])
unread_syncs = []
dismiss_page.fetch_unread = lambda: unread_syncs.append(1)


def item_ids(page):
    return [[n['id'] for n in page.model.get_item(i).notifications]
            for i in range(page.model.get_n_items())]


before = item_ids(dismiss_page)
check('the page groups three favourites into one row',
      ['98', '97', '96'] in [sorted(ids, reverse=True) for ids in before]
      and len(before) == 3)

mention_item = dismiss_page.model.get_item(0)
dismiss_page.dismiss(mention_item)
check('dismissing sends the notification id',
      dismiss_client.dismissed == ['100'])
check('the dismissed row is gone', len(item_ids(dismiss_page)) == 2)
check('the unread count is re-read from the server', unread_syncs == [1])

dismiss_client.dismissed.clear()
group_item = dismiss_page.model.get_item(0)
dismiss_page.dismiss(group_item)
check('a grouped row dismisses every notification it holds',
      sorted(dismiss_client.dismissed) == ['96', '97', '98'])
check('the grouped row is gone', len(item_ids(dismiss_page)) == 1)

dismiss_client.dismissed.clear()
dismiss_client.fail = {'99'}
follow_item = dismiss_page.model.get_item(0)
dismiss_page.dismiss(follow_item)
check('a refused dismissal brings the row back',
      dismiss_page.model.get_item(0) is follow_item
      and len(item_ids(dismiss_page)) == 1)
check('the page shows the list again after a refusal',
      dismiss_page.stack.get_visible_child_name() == 'list')

dismiss_client.fail = set()
dismiss_page.dismiss(follow_item)
check('dismissing the last row shows the empty page',
      dismiss_page.stack.get_visible_child_name() == 'empty')

# A failed dismissal restores the row at its old position, not at the top.
restore_page = NotificationsPage(dismiss_client)
restore_page.on_page_loaded(dismiss_client, [MENTION, FOLLOW, POLL])
restore_page.fetch_unread = lambda: None
dismiss_client.fail = {'99'}
middle = restore_page.model.get_item(1)
restore_page.dismiss(middle)
check('a restored row returns to its position',
      restore_page.model.get_item(1) is middle
      and restore_page.model.get_n_items() == 3)

# The dismissed ids stay known, so a refresh cannot re-add the row while
# the request is still out.
check('the model keeps dismissed ids as seen',
      '99' in restore_page.model._seen)

# The button is on the row, named for a screen reader.
dismiss_row = NotificationRow()
check('every notification row has a dismiss button',
      dismiss_row.dismiss_button.get_tooltip_text()
      == 'Dismiss Notification')
dismiss_row._on_dismiss_clicked(dismiss_row.dismiss_button)
check('a row outside a page ignores the button', True)

# Notification body text. This used to be its own HTML parser, and the two
# parsers disagreed on the one case a notification exists for: a content
# warning. Over statustext.parse_content now, which drops script and style.
check('a plain body becomes plain text',
      desktopnotify.html_to_text('<p>hello</p>').strip() == 'hello')
check('a body keeps its paragraph breaks',
      desktopnotify.html_to_text('<p>one</p><p>two</p>').count('\n') >= 1)
check('a script body is not read aloud',
      'alert' not in desktopnotify.html_to_text(
          '<script>alert(1)</script><p>text</p>'))
check('a malformed body still yields text',
      bool(desktopnotify.html_to_text('<p>unclosed <b>bold')))
check('a content warning stands in for the body it hides',
      desktopnotify.notification_body(
          {'status': {'spoiler_text': 'spoiler',
                      'content': '<p>hidden body</p>'}}) == 'spoiler')

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all notifications rendering checks passed')
