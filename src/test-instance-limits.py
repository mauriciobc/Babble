#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Limits the instance document advertises, and the composer obeying them.

Sources (docs.joinmastodon.org/entities/Instance/):
* ``configuration.polls`` (#polls): max_options, max_characters_per_option,
  min_expiration, max_expiration;
* ``configuration.media_attachments.description_limit`` (#media_attachments):
  1500 by default since 4.4.0, 420 on older servers;
* ``configuration.statuses.characters_reserved_per_url`` (#statuses).
and GET /api/v1/notifications/unread_count, whose ``limit`` defaults to 100
and caps at 1000 (docs.joinmastodon.org/methods/notifications/#unread-count).
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import tempfile

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

os.environ['XDG_CACHE_HOME'] = tempfile.mkdtemp(prefix='babble-test-cache-')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

from gi.repository import Gio, Gtk  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcroot = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, srcroot)
sys.path.insert(0, srcdir)

builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.dirname(
    os.environ.get('GSETTINGS_SCHEMA_DIR')
    or os.path.join(srcroot, 'build', 'data'))
Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))._register()

from src.mastodon import MastodonClient  # noqa: E402
from src.compose import (POLL_DURATIONS,  # noqa: E402
                         BabbleComposeView, poll_duration_choices)
from src.composeattach import AttachmentRow  # noqa: E402
from src.api import notifications as api_notifications  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


def client_with(configuration):
    client = MastodonClient('https://example.test')
    client.instance_info = {'configuration': configuration} \
        if configuration is not None else None
    return client


# Client accessors

check('polls: silent instance gives 4 options and no other bound',
      client_with(None).poll_limits() == {
          'max_options': 4, 'max_characters_per_option': None,
          'min_expiration': None, 'max_expiration': None})
check('polls: missing key is the same as silence',
      client_with({'statuses': {}}).poll_limits()['max_options'] == 4)
wide = client_with({'polls': {
    'max_options': 8, 'max_characters_per_option': 120,
    'min_expiration': 60, 'max_expiration': 7776000}}).poll_limits()
check('polls: larger limits are honoured',
      wide == {'max_options': 8, 'max_characters_per_option': 120,
               'min_expiration': 60, 'max_expiration': 7776000}, repr(wide))
narrow = client_with({'polls': {
    'max_options': 2, 'max_characters_per_option': 25,
    'min_expiration': 3600, 'max_expiration': 86400}}).poll_limits()
check('polls: smaller limits are honoured',
      narrow['max_options'] == 2 and narrow['max_characters_per_option'] == 25)
junk = client_with({'polls': {'max_options': 'many', 'max_expiration': 0,
                              'min_expiration': -5}}).poll_limits()
check('polls: junk and non-positive values fall back',
      junk['max_options'] == 4 and junk['min_expiration'] is None
      and junk['max_expiration'] is None, repr(junk))
check('polls: fewer than two options is not a poll, so it falls back',
      client_with({'polls': {'max_options': 1}}).poll_limits()
      ['max_options'] == 4)

check('alt text: silent instance gets the old-server 420',
      client_with(None).media_description_limit() == 420)
check('alt text: 4.4 default of 1500 is honoured',
      client_with({'media_attachments': {'description_limit': 1500}})
      .media_description_limit() == 1500)
check('alt text: a smaller limit is honoured',
      client_with({'media_attachments': {'description_limit': 200}})
      .media_description_limit() == 200)
check('alt text: junk falls back to 420',
      client_with({'media_attachments': {'description_limit': 'x'}})
      .media_description_limit() == 420)

check('url length: silent instance gets 23',
      client_with(None).characters_reserved_per_url() == 23)
check('url length: advertised value is read',
      client_with({'statuses': {'characters_reserved_per_url': 30}})
      .characters_reserved_per_url() == 30)

# Duration choices

fixed = [s for s, _l in POLL_DURATIONS]
check('durations: no bounds keeps the fixed list',
      [s for s, _l in poll_duration_choices({})] == fixed)
check('durations: a range drops the choices outside it',
      [s for s, _l in poll_duration_choices(
          {'min_expiration': 3600, 'max_expiration': 86400})]
      == [3600, 86400])
check('durations: a range holding none offers min and max',
      [s for s, _l in poll_duration_choices(
          {'min_expiration': 120, 'max_expiration': 900})] == [120, 900])
check('durations: a single-point range offers one choice',
      [s for s, _l in poll_duration_choices(
          {'min_expiration': 600, 'max_expiration': 600})] == [600])
check('durations: only a minimum',
      [s for s, _l in poll_duration_choices({'min_expiration': 86400})]
      == [86400, 604800])
check('durations: choices carry a label',
      all(label for _s, label in poll_duration_choices(
          {'min_expiration': 120, 'max_expiration': 900})))

# Composer

view = BabbleComposeView(client_with({'polls': {
    'max_options': 2, 'max_characters_per_option': 10,
    'min_expiration': 3600, 'max_expiration': 86400}}))
window = Gtk.Window(child=view)
window.present()
view.poll_toggle.set_active(True)
check('composer: option rows are capped at the instance limit',
      len(view.poll_entries) == 2 and not view.poll_add.get_visible())
view.on_poll_add_choice(view.poll_add)
check('composer: adding past the cap does nothing',
      len(view.poll_entries) == 2)
entry = view.poll_entries[0]
entry.set_text('x' * 40)
check('composer: option text is capped at the instance limit',
      len(entry.get_text()) == 10, repr(len(entry.get_text())))
check('composer: durations are filtered to the range',
      [s for s, _l in view._poll_durations] == [3600, 86400])
check('composer: the default selection is a day when offered',
      view._poll_durations[view.poll_expiry.get_selected()][0] == 86400)
check('composer: the dropdown model matches the offered list',
      view.poll_expiry.get_model().get_n_items() == 2)

big = BabbleComposeView(client_with({'polls': {
    'max_options': 6, 'max_characters_per_option': 80}}))
big.poll_toggle.set_active(True)
for _n in range(8):
    big.on_poll_add_choice(big.poll_add)
check('composer: a larger cap allows more rows',
      len(big.poll_entries) == 6 and not big.poll_add.get_visible())
check('composer: a draft with more options than allowed is refused',
      not view._valid_draft_settings(type('D', (), {
          'visibility': 0, 'language_code': 'en', 'language': 0,
          'poll': type('P', (), {'duration_seconds': 3600, 'expires': 0,
                                 'options': ['a', 'b', 'c']})})()))

late = BabbleComposeView(client_with(None))
late.poll_toggle.set_active(True)
check('composer: silent instance keeps four rows and the fixed durations',
      [s for s, _l in late._poll_durations] == fixed)
late.client.instance_info = {'configuration': {'polls': {
    'max_options': 3, 'min_expiration': 3600}}}
late.on_poll_toggled(late.poll_toggle)
for _n in range(4):
    late.on_poll_add_choice(late.poll_add)
check('composer: limits that arrive after the dialog was built apply',
      len(late.poll_entries) == 3
      and [s for s, _l in late._poll_durations] == [3600, 86400, 604800])

# Alt text

row = AttachmentRow('/nonexistent.png')
row.limit_description(30)
row.alt_entry.set_text('a' * 100)
check('alt text: the entry is capped at the limit',
      len(row.alt_entry.get_text()) == 30, repr(len(row.alt_entry.get_text())))
cview = BabbleComposeView(client_with(
    {'media_attachments': {'description_limit': 25}}))
path = os.path.join(tempfile.mkdtemp(), 'a.png')
with open(path, 'wb') as handle:
    handle.write(b'x')
added = cview.add_attachment_path(path)
added = cview.attachments()[0] if cview.attachments() else added
added.alt_entry.set_text('b' * 80)
check('alt text: the composer row stops at the instance limit',
      len(added.alt_entry.get_text()) == 25)
sview = BabbleComposeView(client_with(None))
sview.add_attachment_path(path)
srow = sview.attachments()[0]
srow.alt_entry.set_text('c' * 800)
check('alt text: silent instance stops at 420',
      len(srow.alt_entry.get_text()) == 420)

# Reserved URL length reaches the counter

uview = BabbleComposeView(client_with(
    {'statuses': {'characters_reserved_per_url': 30}}))
uview.buffer.set_text('https://example.test/a/very/long/path')
check('counter: a URL counts as the advertised reserved length',
      uview.counted_length() == 30, repr(uview.counted_length()))

# Unread badge

from src.notificationctrl import (UNREAD_COUNT_LIMIT,  # noqa: E402
                                  unread_badge_text,
                                  NotificationController)
import types  # noqa: E402

check('badge: zero shows nothing', unread_badge_text(0) == '')
check('badge: a small count is exact', unread_badge_text(7) == '7')
check('badge: one under the cap is exact',
      unread_badge_text(UNREAD_COUNT_LIMIT - 1) == '99')
check('badge: the cap itself is open-ended, not an exact count',
      unread_badge_text(UNREAD_COUNT_LIMIT) == '100+')
check('badge: 1000 is open-ended too', unread_badge_text(1000) == '100+')

sent = []
real = api_notifications.unread_count_async
api_notifications.unread_count_async = \
    lambda client, params=None, **kw: sent.append(dict(params or {}))
try:
    stub = types.SimpleNamespace(
        _applied_excluded=('mention',), on_unread_count=None,
        client=types.SimpleNamespace(is_supported=lambda *a, **k: True))
    NotificationController.request_unread_count(stub)
finally:
    api_notifications.unread_count_async = real
check('request: limit is explicit and keeps the exclusions',
      sent == [{'limit': UNREAD_COUNT_LIMIT,
                'exclude_types[]': ['mention']}], repr(sent))

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
