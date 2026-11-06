#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Version-specific features stay out of the UI and off the wire on a
server that does not have them (audit item 5c).

Each gate is exercised the way the capability rule asks: a client whose
``api_versions.mastodon`` is one below the threshold, exactly at it,
above it, and absent. The client is a real ``MastodonClient`` whose
``request_async`` records the call instead of sending it, so the only
thing under test is what Babble asks for and what it shows.

The thresholds come from ``capabilities.py``, never a number written
here, so a gate that moves moves in one place. Where a gate is
deliberately optimistic about a server that says nothing, the test says
so by name.

Exits 77 (skip) without a display."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import capabilities as caps  # noqa: E402
from src.mastodon import MastodonClient  # noqa: E402

Adw.init()
Gtk.init()

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + str(detail)) if detail else ''))


def pump():
    context = GLib.MainContext.default()
    for _ in range(20):
        while context.iteration(False):
            pass


UNKNOWN = None


class RecordingClient(MastodonClient):
    """A real client that records requests rather than sending them."""

    def __init__(self, version):
        super().__init__('https://example.social', token='token')
        self.instance_info = (
            {} if version is UNKNOWN
            else {'api_versions': {'mastodon': version}})
        self.requests = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.requests.append((method, path, {
            'params': params, 'form': form, 'json': json_body}))

    def multipart_async(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))

    def sent(self, method, path):
        return [r for r in self.requests if r[:2] == (method, path)]


def around(gate):
    """The versions every gate is tried at: below, at, above."""
    return [('below', gate - 1), ('at', gate), ('above', gate + 3)]


# -- 1. Wrapped: /state and /generate are API 8 ------------------------
from src import wrapped  # noqa: E402

STATE = ('GET', '/annual_reports/2025/state')
for label, version in around(caps.ANNUAL_REPORT_STATE_API_VERSION):
    client = RecordingClient(version)
    dialog = wrapped.WrappedDialog(Gtk.Box(), client, 2025)
    client.requests.clear()
    dialog._check_state(2025)
    asked = bool(client.sent(*STATE))
    want = version >= caps.ANNUAL_REPORT_STATE_API_VERSION
    check('wrapped state is %s on a server %s the gate (v%d)'
          % ('asked' if want else 'not asked', label, version),
          asked == want, client.requests)
    if not want:
        check('wrapped below the gate falls to the empty page, no Generate',
              dialog.stack.get_visible_child_name() == 'status'
              and dialog.status_page.get_child() is None)
# A server that says nothing: not asked (when_unknown=False). A Mastodon
# older than 4.3 and a non-Mastodon server both land here, and /state 404s
# on them.
client = RecordingClient(UNKNOWN)
dialog = wrapped.WrappedDialog(Gtk.Box(), client, 2025)
client.requests.clear()
dialog._check_state(2025)
check('wrapped on an unknown server asks nothing from /state',
      not client.sent(*STATE), client.requests)
client = RecordingClient(UNKNOWN)
wrapped.WrappedDialog(Gtk.Box(), client, 2025)
check('the report index/read is not gated: GET /annual_reports/2025 sent',
      bool(client.sent('GET', '/annual_reports/2025')), client.requests)

# -- 2. Notifications: policy, requests and unread_count are 4.3.0 -----
from src import notifications  # noqa: E402
from src.api import notifications as api_notifications  # noqa: E402


def menu_actions(model):
    found = []
    for index in range(model.get_n_items()):
        for attr in ('action',):
            value = model.get_item_attribute_value(index, attr, None)
            if value is not None:
                found.append(value.get_string())
        for kind in (Gio.MENU_LINK_SECTION, Gio.MENU_LINK_SUBMENU):
            link = model.get_item_link(index, kind)
            if link is not None:
                found.extend(menu_actions(link))
    return found


POLICY = ('GET', '/api/v2/notifications/policy')
gate = caps.NOTIFICATION_POLICY_API_VERSION
for label, version in around(gate) + [('unknown', UNKNOWN)]:
    # `below` is only reachable by a made-up number: 4.3.0 final reports
    # 2, so the gate speaks to servers that name a number under it.
    client = RecordingClient(version)
    page = notifications.NotificationsPage(client)
    pump()
    want = version is UNKNOWN or version >= gate
    entries = menu_actions(page.menu_button.get_menu_model())
    check('Notification Settings entry %s on a server %s the gate'
          % ('shown' if want else 'hidden', label),
          ('notifs.policy' in entries) == want, entries)
    check('the policy summary is %s on a server %s the gate'
          % ('read' if want else 'not read', label),
          bool(client.sent(*POLICY)) == want, client.requests)
    client.requests.clear()
    page.request_unread_count()
    check('unread_count is %s on a server %s the gate'
          % ('asked' if want else 'not asked', label),
          bool(client.sent('GET', '/notifications/unread_count')) == want,
          client.requests)

# -- 3. Policy for_bots: shown only when the policy has the key --------
original_finish = api_notifications.policy_finish


def policy_dialog(policy):
    client = RecordingClient(caps.NOTIFICATION_POLICY_API_VERSION)
    dialog = notifications.NotificationPolicyDialog(client)
    api_notifications.policy_finish = lambda _c, _r: policy
    try:
        dialog.on_loaded(client, None)
    finally:
        api_notifications.policy_finish = original_finish
    return client, dialog


five = {key: 'accept' for key, _t, _s in notifications.POLICY_FIELDS
        if key != 'for_bots'}
_client, old = policy_dialog(five)
check('Bots row hidden when the policy has no for_bots key',
      not old.filter_rows['for_bots'].get_visible())
check('the five documented rows are shown',
      all(row.get_visible() for key, row in old.filter_rows.items()
          if key != 'for_bots'))
old.filter_rows['for_bots'].set_selected(1)
old.filter_rows['for_not_following'].set_selected(1)
check('a stray Bots selection never reaches the PATCH',
      old.changed_payload() == {'for_not_following': 'filter'},
      old.changed_payload())
six = dict(five, for_bots='accept')
_client, new = policy_dialog(six)
check('Bots row shown when the policy has for_bots',
      new.filter_rows['for_bots'].get_visible())
new.filter_rows['for_bots'].set_selected(1)
check('and its change is sent', new.changed_payload() == {'for_bots': 'filter'},
      new.changed_payload())

# -- 4. Filters: Blur Media is API 5 -----------------------------------
from src import filters  # noqa: E402

gate = caps.FILTER_BLUR_API_VERSION


def action_names(page):
    model = page.action_row.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())]


for label, version in around(gate) + [('unknown', UNKNOWN)]:
    want = version is not UNKNOWN and version >= gate
    page = filters.FilterEditPage(RecordingClient(version))
    check('Blur Media %s for a new filter on a server %s the gate'
          % ('offered' if want else 'hidden', label),
          ('Blur Media' in action_names(page)) == want, action_names(page))
    blurred = {'id': '9', 'title': 'Spoilers', 'context': ['home'],
               'filter_action': 'blur', 'keywords': []}
    page = filters.FilterEditPage(RecordingClient(version), blurred)
    check('an existing blur filter keeps Blur Media (%s the gate)' % label,
          'Blur Media' in action_names(page)
          and page.collect()['filter_action'] == 'blur',
          (action_names(page), page.collect()))
page = filters.FilterEditPage(RecordingClient(gate - 1))
page.action_row.set_selected(1)
check('the payload action follows the filtered list, not the full table',
      page.collect()['filter_action'] == 'hide', page.collect())

# -- 5. Quotes: the default policy is API 7 ----------------------------
from src import preferences  # noqa: E402

gate = caps.QUOTES_API_VERSION


class FakeSettings:
    def get_string(self, _key):
        return 'followers'


class FakeWindow:
    def __init__(self, client):
        self.client = client


for label, version in around(gate) + [('unknown', UNKNOWN)]:
    want = version is not UNKNOWN and version >= gate
    client = RecordingClient(version)
    preferences.sync_posting_defaults(
        FakeWindow(client), 'default-quote-policy', FakeSettings())
    sent = client.sent('PATCH', '/accounts/update_credentials')
    check('source[quote_policy] is %s on a server %s the gate'
          % ('sent' if want else 'not sent', label),
          bool(sent) == want, client.requests)
    if want and sent:
        check('and carries the chosen policy',
              sent[0][2]['form'] == {'source[quote_policy]': 'followers'},
              sent[0][2])


class PrefsWindow:
    def __init__(self, client):
        self.client = client
        self.account = {'instance': 'https://example.social'}
        self.settings = Gio.Settings.new('io.github.mauriciobc.Babble')


for label, version in around(gate) + [('unknown', UNKNOWN)]:
    want = version is not UNKNOWN and version >= gate
    dialog = preferences.PreferencesDialog(PrefsWindow(RecordingClient(version)))
    check('Default Quote Policy row is %s on a server %s the gate'
          % ('shown' if want else 'hidden', label),
          (dialog.quote_policy_row is not None) == want)
    keep = version is UNKNOWN or version >= caps.NOTIFICATION_POLICY_API_VERSION
    check('Preferences keeps Notification Filtering on a server %s the '
          'quote gate (v%s)' % (label, version),
          (dialog.policy_row is not None) == keep)

# Compose: quote parameters of a post.
from src import compose  # noqa: E402


def compose_params(version):
    client = RecordingClient(version)
    view = compose.BabbleComposeView(client)
    view.quoted_status_id = '55'
    view._scheduled_fields['quote_approval_policy'] = 'nobody'
    view.send_status()
    posts = client.sent('POST', '/statuses')
    return posts[0][2]['json'] if posts else None


for label, version in around(gate) + [('unknown', UNKNOWN)]:
    form = compose_params(version)
    known_old = version is not UNKNOWN and version < gate
    if form is None:
        check('compose sent a status on a server %s the gate' % label,
              False, 'no POST /statuses recorded')
        continue
    has = 'quoted_status_id' in form
    check('quoted_status_id is %s on a server %s the gate (v%s)'
          % ('left out' if known_old else 'sent', label, version),
          has != known_old, form)
    has = 'quote_approval_policy' in form
    check('quote_approval_policy is %s on a server %s the gate'
          % ('left out' if known_old else 'sent', label),
          has != known_old, form)

# -- 6. Endorsements: GET /accounts/:id/endorsements is 4.4.0 ----------
from src import profile  # noqa: E402

gate = caps.ENDORSEMENTS_API_VERSION
ENDORSEMENTS = ('GET', '/accounts/7/endorsements')
for label, version in around(gate) + [('unknown', UNKNOWN)]:
    want = version is not UNKNOWN and version >= gate
    client = RecordingClient(version)
    profile.ProfilePage(
        client, {'id': '7', 'acct': 'ada', 'username': 'ada',
                 'display_name': 'Ada', 'url': 'https://example.social/@ada',
                 'avatar': '', 'header': '', 'note': '', 'fields': [],
                 'emojis': [], 'followers_count': 0, 'following_count': 0,
                 'statuses_count': 0, 'created_at': '2024-01-01T00:00:00Z'},
        viewer_id='1')
    pump()
    check('endorsements are %s on a server %s the gate'
          % ('read' if want else 'not read', label),
          bool(client.sent(*ENDORSEMENTS)) == want, client.requests)

# -- 7. Notification types: the entity page's version history ---------
from src.notificationcopy import type_offered  # noqa: E402

for kind, gate in caps.NOTIFICATION_TYPE_API_VERSIONS.items():
    for label, version in around(gate) + [('unknown', UNKNOWN)]:
        want = version is UNKNOWN or version >= gate
        check('%s is %s on a server %s its gate (v%s)'
              % (kind, 'offered' if want else 'not offered', label, version),
              type_offered(RecordingClient(version), kind) == want)
check('a type with no gate is offered on the oldest server',
      type_offered(RecordingClient(0), 'mention'))
check('every gated type is a type the filter lists',
      set(caps.NOTIFICATION_TYPE_API_VERSIONS)
      <= {k for k, _l in notifications.NOTIFICATION_TYPES})

for label, version in (('below', caps.QUOTES_API_VERSION - 1),
                       ('at', caps.COLLECTIONS_API_VERSION)):
    dialog = notifications.NotificationPolicyDialog(RecordingClient(version))
    shown = {k for k, row in dialog.type_rows.items() if row.get_visible()}
    want = {k for k, _l in notifications.NOTIFICATION_TYPES
            if type_offered(RecordingClient(version), k)}
    check('the type switches follow the gate on a server %s it' % label,
          shown == want, (shown, want))
    check('and quote switches are %s' % ('hidden' if label == 'below'
                                         else 'shown'),
          ('quote' in shown) == (label != 'below'))

# -- 8. Editing a post sends only what PUT /statuses/:id permits -------
client = RecordingClient(caps.COLLECTIONS_API_VERSION)
view = compose.BabbleComposeView(client)
view.start_edit({'id': '900', 'text': 'edited', 'visibility': 'unlisted',
                 'language': 'en', 'spoiler_text': '',
                 'in_reply_to_id': '5', 'media_attachments': []})
pump()
view.quoted_status_id = '55'
view._scheduled_fields['quote_approval_policy'] = 'nobody'
view.send_status()
puts = client.sent('PUT', '/statuses/900')
body = puts[0][2]['json'] if puts else {}
check('an edit goes to PUT /statuses/:id', bool(puts), client.requests)
check('an edit keeps its permitted fields',
      body.get('status') == 'edited' and 'language' in body
      and body.get('quote_approval_policy') == 'nobody', body)
check('an edit leaves out create-only fields',
      not ({'visibility', 'in_reply_to_id', 'quoted_status_id',
            'scheduled_at'} & set(body)), body)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('every version-specific feature is gated')
