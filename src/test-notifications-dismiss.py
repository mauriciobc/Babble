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

from gi.repository import GLib, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.api import notifications as api_notifications  # noqa: E402
from src.mastodon import MastodonClient  # noqa: E402
from src import filterrules  # noqa: E402
from src.mediabox import BlurredPicture  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402
from src.notificationrow import NotificationPost, NotificationRow  # noqa: E402
from src.notifications import (NotificationRequestRow,  # noqa: E402
                               NotificationsPage)

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
from src import desktopnotify  # noqa: E402

from testkit import notificationskit as kit  # noqa: E402
from testkit.notificationskit import (FollowClient,
    _live_error_result,
    body_text,
    collect,
    notification_rows,
    pump_until,
    visible_text)  # noqa: E402
from testkit.notificationskit import client, page  # noqa: E402

# The live anonymous fetch: /api/v1/notifications without a token.
live_client = MastodonClient('https://fosstodon.org')
live = {'error': None, 'data': None, 'done': False}


def on_live(c, result):
    try:
        live['data'] = api_notifications.index_finish(c, result)
    except GLib.Error as e:
        live['error'] = e
    finally:
        live['done'] = True
        live['loop'].quit()


live_loop = GLib.MainLoop()
GLib.idle_add(lambda: (api_notifications.index_async(live_client, callback=on_live),
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

    def request_async(self, method, path, callback=None, params=None, **kw):
        if method == 'POST' and path.endswith('/dismiss') \
                and path.startswith('/notifications/'):
            notification_id = path.split('/')[2]
            self.dismissed.append(notification_id)
            callback(self, notification_id)
            return
        super().request_async(method, path, callback=callback,
                              params=params, **kw)

    def request_finish(self, result):
        if not isinstance(result, str):
            return result
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

# Server-supplied status filters (Status#filtered, FilterResult) in the
# notifications context. Timelines honour them through filterrules, and
# so do the notification rows (NotificationPost.bind) and the request
# row's preview.


def filter_result(title, action, contexts, matches=None):
    """docs.joinmastodon.org/entities/FilterResult."""
    return {'filter': {'id': '1', 'title': title, 'context': contexts,
                       'expires_at': None, 'filter_action': action},
      'keyword_matches': matches or ['secretword'],
      'status_matches': None}


def filtered_mention(id, body, filtered, media=False):
    status = {**STATUS, 'id': 'f' + id,
              'content': '<p>%s</p>' % body, 'filtered': filtered}
    if media:
        status['media_attachments'] = [{
      'id': 'm' + id, 'type': 'image',
      'url': 'https://example.invalid/a.png',
      'preview_url': 'https://example.invalid/a.png',
      'description': 'a picture'}]
    return make_notification(
        'f' + id, 'mention', ACTOR2, '2026-10-05T13:%02d:00.000Z' % int(id),
        status)


F_HIDE = filtered_mention('10', 'hidebody secretword', [
    filter_result('Hide rule', filterrules.HIDE,
                  [filterrules.NOTIFICATIONS])])
F_WARN = filtered_mention('11', 'warnbody secretword', [
    filter_result('Warn rule', 'warn', [filterrules.NOTIFICATIONS])])
F_BLUR = filtered_mention('12', 'blurbody secretword', [
    filter_result('Blur rule', filterrules.BLUR,
                  [filterrules.NOTIFICATIONS])], media=True)
F_HOME = filtered_mention('13', 'homebody secretword', [
    filter_result('Home only', filterrules.HIDE, [filterrules.HOME])])
F_CONTROL = filtered_mention('14', 'controlbody plain', [])
FILTER_FIXTURES = [F_HIDE, F_WARN, F_BLUR, F_HOME, F_CONTROL]

# The reference the rows should match: the same resolver timelines use.
check('resolver: hide applies in notifications',
      filterrules.resolve(F_HIDE['status'], filterrules.NOTIFICATIONS)
      ['action'] == filterrules.HIDE)
check('resolver: warn applies in notifications',
      filterrules.resolve(F_WARN['status'], filterrules.NOTIFICATIONS)
      ['action'] == 'warn')
check('resolver: blur applies in notifications',
      filterrules.resolve(F_BLUR['status'], filterrules.NOTIFICATIONS)
      ['action'] == filterrules.BLUR)
check('resolver: a home-only rule does not apply in notifications',
      filterrules.resolve(F_HOME['status'], filterrules.NOTIFICATIONS)
      is None)

filter_page = NotificationsPage(client)
filter_page.model.append_page(FILTER_FIXTURES)
filter_page.stack.set_visible_child_name('list')
filter_window = Gtk.Window(child=filter_page, default_width=600,
                           default_height=1200)
filter_window.present()
pump_until(lambda: len(notification_rows(filter_page.list_view)) >= 1, 5)
pump_until(lambda: len(notification_rows(filter_page.list_view))
           >= len(FILTER_FIXTURES), 5)
filter_rows = notification_rows(filter_page.list_view)
filter_text = ' '.join(visible_text(r) for r in filter_rows)


def row_with(marker):
    return [r for r in filter_rows if marker in visible_text(r)]


check('filter fixtures: control row is shown', bool(row_with('controlbody')))
check('filter fixtures: home-only rule row is shown normally',
      bool(row_with('homebody')))
# The contract is StatusRow's (statusrow.py:438-459; test-timeline.py
# 620-680): the bar reads 'Filtered: <title>' with a 'Show Anyway'
# button. warn and hide both collapse the card behind it; blur leaves the
# text readable and covers the media with mediabox.BlurredPicture.
# Timelines never remove a hide post client-side, so for notifications
# "absent or collapsed" are both acceptable for hide.


def shown_text(widget):
    """Text of the labels actually mapped on screen."""
    out = []
    collect(widget, out)
    return ' '.join(w.get_text() or '' for w in out
                    if isinstance(w, Gtk.Label) and w.get_mapped())


def filter_bar_texts(widget):
    out = []
    collect(widget, out)
    return [w.get_text() for w in out
      if isinstance(w, Gtk.Label) and w.get_mapped()
      and (w.get_text() or '').startswith('Filtered')]


def show_anyway_buttons(widget):
    out = []
    collect(widget, out)
    return [w for w in out if isinstance(w, Gtk.Button) and w.get_mapped()
      and (w.get_label() or '').replace('_', '') == 'Show Anyway']


def all_shown_text():
    return ' '.join(shown_text(r) for r in filter_rows)


check('filter fixtures: shown_text sees the control body',
      'controlbody' in all_shown_text())
check('hide rule in notifications: body is not readable',
      'hidebody' not in all_shown_text())
warn_rows = row_with('Warn rule') or row_with('warnbody')
check('warn rule in notifications: card collapsed behind '
      '"Filtered: Warn rule"',
      bool(warn_rows)
      and any('Filtered: Warn rule' in filter_bar_texts(r)
                    for r in warn_rows)
      and not any('warnbody' in shown_text(r) for r in warn_rows))
check('warn rule in notifications: the reader can Show Anyway',
      any(show_anyway_buttons(r) for r in warn_rows))
revealed = False
for r in warn_rows:
    for button in show_anyway_buttons(r):
        button.emit('clicked')
        revealed = True
pump_until(lambda: 'warnbody' in all_shown_text(), 2)
check('warn rule in notifications: Show Anyway reveals the body',
      revealed and 'warnbody' in all_shown_text()
      and not any('Filtered: Warn rule' in filter_bar_texts(r)
                        for r in warn_rows))
blur_rows = row_with('blurbody')
blur_all = []
for r in blur_rows:
    collect(r, blur_all)
blur_pictures = [w for w in blur_all if isinstance(w, BlurredPicture)]
plain_pictures = [w for w in blur_all if isinstance(w, Gtk.Picture)
                  and not isinstance(w, BlurredPicture)]
check('blur rule in notifications: media is a BlurredPicture, text '
      'stays, bar names the rule',
      bool(blur_rows) and bool(blur_pictures) and not plain_pictures
      and any('Filtered: Blur rule' in filter_bar_texts(r)
                    for r in blur_rows))
filter_window.destroy()

# (a) an expired rule no longer applies: the row is not collapsed.
EXPIRED = filter_result('Old rule', 'warn', [filterrules.NOTIFICATIONS])
EXPIRED['filter']['expires_at'] = '2020-01-01T00:00:00.000Z'
expired_post = NotificationPost()
expired_post.bind(StatusItem({**STATUS, 'id': 'x1', 'filtered': [EXPIRED]}))
check('expired rule: the post is not collapsed',
      expired_post.content.get_visible()
      and expired_post.byline.get_visible()
      and (expired_post.filter_bar is None
           or not expired_post.filter_bar.get_visible()))

# (b) a rule on the boosted post, not on the wrapper, collapses.
boost_post = NotificationPost()
boost_post.bind(StatusItem({
    **STATUS, 'id': 'x2', 'filtered': [],
    'reblog': {**STATUS, 'id': 'x3', 'content': '<p>boosted</p>',
               'filtered': [filter_result('Boost rule', 'warn',
                                          [filterrules.NOTIFICATIONS])]}}))
check('filtered boost: collapsed behind the rule title',
      boost_post.filter_bar is not None
      and boost_post.filter_bar.get_visible()
      and boost_post.filter_label.get_text() == 'Filtered: Boost rule'
      and not boost_post.content.get_visible())

# (c) recycling: one NotificationPost bound to a filtered item, then an
# unfiltered one with media and a poll, then back.
RECYCLE_BODY = {
    **STATUS, 'id': 'x4', 'content': '<p>recycled body</p>',
    'poll': {'options': [{'title': 'yes', 'votes_count': 1},
                         {'title': 'no', 'votes_count': 1}],
             'votes_count': 2},
    'media_attachments': [{
        'id': 'mx', 'type': 'image', 'url': 'https://example.invalid/a.png',
        'preview_url': 'https://example.invalid/a.png',
        'description': 'a picture'}]}
rec_filtered = StatusItem({**RECYCLE_BODY, 'filtered': [
    filter_result('Recycle rule', 'warn', [filterrules.NOTIFICATIONS])]})
rec_plain = StatusItem({**RECYCLE_BODY, 'id': 'x5', 'filtered': []})
rec_post = NotificationPost()
rec_window = Gtk.Window(child=rec_post, default_width=600)
rec_window.present()


def collapsed_now():
    return (rec_post.filter_bar is not None
            and rec_post.filter_bar.get_visible()
            and not rec_post.byline.get_visible()
            and not rec_post.content.get_visible()
            and not rec_post.media_box.get_visible()
            and not rec_post.poll_box.get_visible())


def expanded_now():
    return (rec_post.byline.get_visible() and rec_post.content.get_visible()
            and rec_post.media_box.get_visible()
            and rec_post.poll_box.get_visible()
            and (rec_post.filter_bar is None
                 or not rec_post.filter_bar.get_visible()))


rec_post.bind(rec_filtered)
check('recycle: a filtered item collapses the post', collapsed_now())
rec_post.bind(rec_plain)
pump_until(expanded_now, 2)
check('recycle: an unfiltered item restores byline, content, media, poll '
      'and hides the bar',
      expanded_now() and body_text(rec_post) == 'recycled body')
rec_post.bind(rec_filtered)
check('recycle: and back to collapsed again', collapsed_now())
rec_window.destroy()

# (d) the request row's one-line preview consults the filters too.


def request_preview(status):
    return NotificationRequestRow._preview(status)


def with_filter(action):
    return {**STATUS, 'content': '<p>plain preview</p>',
            'filtered': [filter_result('Preview rule', action,
                                       [filterrules.NOTIFICATIONS])]}


check('request preview: warn shows the rule title',
      request_preview(with_filter('warn')) == 'Filtered: Preview rule')
check('request preview: hide shows the rule title',
      request_preview(with_filter(filterrules.HIDE))
      == 'Filtered: Preview rule')
check('request preview: blur leaves the text',
      request_preview(with_filter(filterrules.BLUR)) == 'plain preview')
check('request preview: no filter leaves the text',
      request_preview({**STATUS, 'content': '<p>plain preview</p>'})
      == 'plain preview')
check('request preview: a home-only rule leaves the text',
      request_preview({**STATUS, 'content': '<p>plain preview</p>',
                       'filtered': [filter_result(
                           'Home', filterrules.HIDE, [filterrules.HOME])]})
      == 'plain preview')

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all notifications rendering checks passed')


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all notifications dismiss checks passed')
