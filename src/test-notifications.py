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

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src.notifications import (NotificationItem, NotificationModel,  # noqa: E402
                               NotificationPost, NotificationRow,
                               NotificationsPage, notification_title)

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
check('date heading stamped',
      model.get_item(0).section_title in ('Oct 5', 'out 5')
      and model.get_item(2).section_title is None)

fresh = NotificationModel()
check('prepend page', fresh.prepend_page([FAV_A, FAV_B]) == 2)
check('prepend keeps newest first',
      fresh.get_item(0).notification_id == '98')
check('prepend dedupes', fresh.prepend_page([FAV_A]) == 0)
fresh.clear()
check('clear resets', fresh.get_n_items() == 0
      and fresh.oldest_id is None and not fresh.exhausted)

check('mention title', notification_title(
    NotificationItem(MENTION)) == 'Carol mentioned you')
check('follow title', notification_title(
    NotificationItem(FOLLOW)) == 'Carol followed you')
check('unknown type falls back to generic title',
      notification_title(
          NotificationItem(MYSTERY)) == 'Alice Doe interacted with you')
check('timestamp rendered',
      NotificationItem(FAV_A).timestamp != '')

# Widget rendering with the same fixtures through the real factory path.
Adw.init()
Gtk.init()

client = MastodonClient('https://fosstodon.org')  # no token: idle client
client.notifications_mark_read_finish = lambda result: {}
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
    w.people_box.get_first_child() is not None
    and w.cluster_expander.get_visible()
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
check('no date heading on standalone row',
      long_item.section_title is None)

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

# Unread badge signal.
received = []
page.connect('unread-changed', lambda _p, count: received.append(count))
page.on_marked_read(client, None)
check('badge signal emits 0 after mark-read', received == [0])


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
check('mention notification offers reply, favourite and boost',
      recorded_buttons == [('Reply', 'app.reply', (ME_KEY, '42')),
                           ('Favourite', 'app.favourite', (ME_KEY, '42')),
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

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all notifications rendering checks passed')
