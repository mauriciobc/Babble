#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the composer chrome, emoji chooser, counter, visibility,
content warning, attachments and upload states with a stub client."""

import gc
import inspect
import os
import sys
import tempfile
import weakref
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

# Pasted images are kept under the user cache: the test's go to a
# throwaway one. Before GLib is loaded, as it reads the variable once.
cache_home = tempfile.mkdtemp(prefix='babble-test-cache-')
os.environ['XDG_CACHE_HOME'] = cache_home

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import (Adw, Gio, GLib, Gtk)  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcroot = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, srcroot)
sys.path.insert(0, srcdir)

builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.dirname(
    os.environ.get('GSETTINGS_SCHEMA_DIR')
    or os.path.join(srcroot, 'build', 'data'))
Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))._register()

Adw.init()
Gtk.init()

from testkit import composekit as kit  # noqa: E402
from testkit.composekit import (  # noqa: E402
    INHIBIT_FLAGS, PNG_BYTES, StubClient, StubMediaResult, _capture_toast,
    announced, check, collect_toasts, polls, posts, pump, set_text,
    settle_until, stub_app, tmp_png, toast_titles, uploads, window)
import src.portals as portals_module
from src import emojichooser
from src.compose import BabbleComposeView
from src.composeoptions import VISIBILITIES
from src.drafts import Attachment
from gettext import gettext as _

view = kit.view
client = kit.client

check('portals.py keeps the helpers the composer uses',
      all(callable(getattr(portals_module, name, None))
          for name in ('portal_version', 'session_bus', 'portal_request'))
      and list(inspect.signature(
          portals_module.portal_request).parameters) == [
              'bus', 'interface', 'method', 'signature', 'args', 'options',
              'on_response'])

MAX = 500

# How long an async clipboard read may take before the paste checks give
# up. Generous on purpose: this is a ceiling, not a target, and a loaded
# machine must not fail the test for being slow.
PASTE_WAIT_S = 10.0

for attr in ('attach_button', 'screenshot_button', 'cw_toggle',
             'poll_toggle', 'mention_button', 'emoji_button',
             'thread_toggle'):
    button = getattr(view, attr)
    check('toolbar %s present and labelled' % attr,
          button.get_visible() and button.get_tooltip_text()
          and button.get_icon_name())
check('toggles are toggle buttons',
      all(isinstance(getattr(view, a), Gtk.ToggleButton)
          for a in ('cw_toggle', 'poll_toggle', 'thread_toggle')))
check('dropdowns use stock flat style',
      view.visibility_row.has_css_class('flat')
      and view.language_row.has_css_class('flat'))
check('post button is suggested-action',
      view.post_button.has_css_class('suggested-action'))
check('post disabled when empty', not view.post_button.get_sensitive())
poll_text = 'poll question one'
view.poll_toggle.set_active(True)
view.poll_entries[0].set_text('Blue')
pump(50)
check('poll box revealed', view._poll_revealer.get_reveal_child())
view.on_emoji_picked(view.emoji_button, '😀')
check('emoji insertion works', view.buffer.get_text(
    view.buffer.get_start_iter(), view.buffer.get_end_iter(),
    False) == '😀')
view.poll_toggle.set_active(False)
pump(50)
check('poll box hidden again', not view._poll_revealer.get_reveal_child())
set_text('')

# Custom emoji: with an instance that publishes some, the emoji button
# opens the instance chooser (no network: the list is pre-cached and the
# emoji have no image URL); a picked shortcode lands at the cursor.
client.instance = 'emoji.test'
emojichooser._cache['emoji.test'] = [
    {'shortcode': 'blobcat', 'category': 'Cats'},
    {'shortcode': 'blobdog', 'category': ''}]
set_text('hi')
view.on_emoji_clicked(view.emoji_button)
# Wait for the popover to be shown, not for a fixed time: popup() is
# animated, and the visibility flag lands on its own frame.
def emoji_chooser_shown():
    chooser = view.emoji_chooser
    return chooser is not None and chooser.get_mapped() and chooser.get_visible()


settle_until(10.0, emoji_chooser_shown)
chooser = view.emoji_chooser
chooser_ok = (isinstance(chooser, emojichooser.CustomEmojiChooser)
              and chooser.get_parent() is view.emoji_button
              and chooser.get_visible())
check('emoji button opens the emoji chooser', chooser_ok)
if not chooser_ok:
    print('note - emoji chooser: type=%s parent_ok=%s visible=%s mapped=%s '
          'window_mapped=%s window_active=%s'
          % (type(chooser).__name__,
             chooser is not None and chooser.get_parent() is view.emoji_button,
             chooser is not None and chooser.get_visible(),
             chooser is not None and chooser.get_mapped(),
             window.get_mapped(), window.is_active()))
check('it opens on the standard tab with its groups',
      chooser.tabs.get_visible() and chooser.mode == 'emoji'
      and chooser.category_store.get_n_items() == 9)
chooser.search_entry.set_text('grinning face')
chooser.on_search_changed(chooser.search_entry)
check('standard emoji are searchable',
      chooser.stack.get_visible_child_name() == 'grid'
      and chooser.grid_store.get_item(0).emoji['char'] == '\U0001F600')
chooser.on_emoji_activated(chooser.grid, 0)
pump(50)
check('a picked standard emoji is inserted', view.text() == 'hi\U0001F600')
set_text('hi')
chooser.search_entry.set_text('')
chooser.tabs.set_active_name('custom')
pump(20)
check('the custom tab lists the instance categories',
      chooser.mode == 'custom'
      and chooser.category_store.get_n_items() == 2)
chooser.pick('blobcat')
pump(50)
check('picked shortcode is inserted with spacing',
      view.text() == 'hi :blobcat: ')
check('picked shortcode is remembered',
      'blobcat' in emojichooser.recent_shortcodes()
      or emojichooser._settings() is None)
set_text('')
view.on_custom_emoji_picked(chooser, 'blobdog')
check('shortcode at the start needs no leading space',
      view.text() == ':blobdog: ')

# An instance without custom emoji only gets the standard tab.
emojichooser._cache['emoji.test'] = []
chooser.unparent()
view.emoji_chooser = None
view.on_emoji_clicked(view.emoji_button)
pump(50)
chooser = view.emoji_chooser
check('no custom emoji hides the tabs and keeps the standard set',
      not chooser.tabs.get_visible() and chooser.mode == 'emoji'
      and chooser.category_store.get_n_items() == 9)
chooser.popdown()
chooser.unparent()
view.emoji_chooser = None
del chooser

# The chooser is freed with the composer that owns it.
emojichooser._cache['emoji.test'] = [{'shortcode': 'blobcat'}]
extra = BabbleComposeView(client)
extra.emoji_chooser = emojichooser.CustomEmojiChooser(client)
extra.emoji_chooser.set_parent(extra.emoji_button)
pages_connect = extra.emoji_chooser
views_ref = weakref.ref(extra)
chooser_ref = weakref.ref(pages_connect)
del extra, pages_connect
gc.collect()
check('composer and its emoji chooser are freed',
      views_ref() is None and chooser_ref() is None)
del client.instance
set_text('')
# Counter thresholds (docs Mastodon default max_characters)
check('counter shows remaining when empty',
      view.counter_label.get_text() == str(MAX))
set_text('hello babble')
check('counter counts remaining chars',
      view.counter_label.get_text() == str(MAX - 12))
check('post enabled with text', view.post_button.get_sensitive())
set_text('x' * MAX)
check('counter at limit shows 500/500',
      view.counter_label.get_text() == '0')
check('warning class at limit', view.counter_label.has_css_class('warning'))
set_text('y' * (MAX - 50))
check('warning class under 20pct', view.counter_label.has_css_class('warning'))
set_text('z' * (MAX + 5))
check('error class over limit', view.counter_label.has_css_class('error'))
set_text('')
collect_toasts()
set_text('z' * (MAX + 5))
view.on_post_clicked(view.post_button)
check('over-limit post refused', posts == [] and
      collect_toasts() == [_('Post is too long')])
set_text('')

# Visibility selector values
codes = [v[0] for v in VISIBILITIES]
check('visibility codes are Mastodon values',
      codes == ['public', 'unlisted', 'private', 'direct'])

# Content warning toggle and subject field
check('spoiler hidden initially', not view._spoiler_revealer.get_reveal_child())
view.cw_toggle.set_active(True)
pump(50)
check('spoiler field revealed', view._spoiler_revealer.get_reveal_child())
view.spoiler_entry.set_text('potential spoilers')
pump(50)


set_text('hello babble')

view.add_attachment_path(tmp_png, alt='a red test pixel')
rows = view.attachments()
check('attachment added', len(rows) == 1)
check('attachment alt stored', rows[0].alt_entry.get_text() == 'a red test pixel')
check('attachment mime from file', rows[0].content_type == 'image/png')
row = rows[0]
row.alt_entry.set_text('red pixel for testing')
row.alt_entry.emit('changed')
check('alt change recorded',
      row.alt_entry.get_text() == 'red pixel for testing')
check('media shows up in snapshot',
      isinstance(view.snapshot().attachments[0], Attachment)
      and view.snapshot().attachments[0].alt == 'red pixel for testing')

pump(50)

# Post and inspect the payload shape while the spinner runs.
posts.clear()
uploads.clear()
collect_toasts()
announced.clear()
view.on_post_clicked(view.post_button)
check('posting progress visible', view.progress_spinner.get_visible())
check('posting disables button', not view.post_button.get_sensitive())
check('posting holds back logout and suspend',
      len(stub_app.inhibited) == 1
      and stub_app.inhibited[0][0] is window
      and stub_app.inhibited[0][1] == INHIBIT_FLAGS
      and stub_app.inhibited[0][2] == _('Sending a post')
      and stub_app.held == {1})
pump(400)
check('all attachments uploaded first', len(uploads) == 1)
check('upload multipart shape',
      uploads[0]['filename'] == 'babble-test.png'
      and uploads[0]['content_type'] == 'image/png'
      and uploads[0]['bytes'] == len(PNG_BYTES)
      and uploads[0]['description'] == 'red pixel for testing')
check('posting finished, spinner hidden',
      not view.progress_spinner.get_visible())
check('toast feedback for success',
      collect_toasts() == [_('Post sent')])
check('a sent post gives the inhibition back',
      stub_app.held == set() and stub_app.released == [1])
check('the success toast is not announced a second time', announced == [])

form = posts[0]
# See docs.joinmastodon.org/methods/statuses POST /statuses
check('status body present', form.get('status') == 'hello babble')
check('visibility value valid', form.get('visibility') == 'public')
check('language is ISO 639-1', len(form.get('language', '')) == 2)
check('spoiler text from CW field', form.get('spoiler_text') == 'potential spoilers')
check('media_ids array encoded',
      form.get('media_ids') == [uploads[0]['id']])
check('no in_reply_to for top post', 'in_reply_to_id' not in form)
check('fixed response returned from stub',
      view.in_reply_to_id is None)

# An upload that fails ends the round, and gives the inhibition back.
gone_png = os.path.join(tempfile.mkdtemp(prefix='babble-test-gone-'),
                        'gone.png')
with open(gone_png, 'wb') as f:
    f.write(PNG_BYTES)
set_text('upload will fail')
view.cw_toggle.set_active(False)
view.add_attachment_path(gone_png)
os.unlink(gone_png)
collect_toasts()
inhibited_before = len(stub_app.inhibited)
view.on_post_clicked(view.post_button)
pump(50)
check('a failed upload took and gave back the inhibition',
      len(stub_app.inhibited) == inhibited_before + 1
      and stub_app.held == set()
      and stub_app.released[-1] == stub_app.inhibited[-1][3])
check('a failed upload is told in a toast',
      any(t.startswith(_('Could not upload media')) for t in collect_toasts())
      and not view._posting and announced == [])
view.reset()
check('a reset with no post in flight releases nothing more',
      stub_app.released.count(stub_app.inhibited[-1][3]) == 1)

# An upload the instance has not decoded yet: the composer must wait for
# GET /media/:id rather than posting a media_id the server will reject.
StubMediaResult.needs_processing = True
processing_view = BabbleComposeView(client)
processing_view._add_toast = _capture_toast  # type: ignore[attr-defined]
processing_window = Gtk.Window()
processing_window.set_child(processing_view)
processing_window.show_toast = lambda text: toast_titles.append(text)  # type: ignore
processing_window.present()
polls.clear()
posts.clear()
collect_toasts()
processing_view.add_attachment_path(tmp_png, alt='a red test pixel')
pump(50)
processing_view.on_post_clicked(processing_view.post_button)
pump(50)
check('an unprocessed upload is not posted immediately', posts == [])
check('the composer polls the media for processing', polls != [])
# The stub only reports a url on the second poll, so the composer has
# to come back for it after the polling interval. Wait for the post,
# not for a fixed 1.8 s that a loaded scheduler can miss.
check('the post is sent once processing finishes',
      settle_until(8.0, lambda: len(posts) == 1)
      and posts[0].get('media_ids') == [uploads[-1]['id']])
check('the waited-for post reports success',
      collect_toasts()[-1:] == [_('Post sent')])
processing_window.destroy()

# Discarding the draft must stop the poll and stay quiet, including when
# the poll's reply lands *after* the reset. That reply used to look like
# a server error, so the composer rescheduled the poll against a
# cancellable that no longer existed and eventually toasted an upload
# failure for a draft the user had already thrown away.
StubMediaResult.needs_processing = True
StubMediaResult.cancelled = True
StubMediaResult.defer_status = True
cancelled_view = BabbleComposeView(client)
cancelled_view._add_toast = _capture_toast  # type: ignore[attr-defined]
cancelled_window = Gtk.Window()
cancelled_window.set_child(cancelled_view)
cancelled_window.show_toast = lambda text: toast_titles.append(text)  # type: ignore
cancelled_window.present()
polls.clear()
posts.clear()
collect_toasts()
cancelled_view.add_attachment_path(tmp_png, alt='a red test pixel')
pump(50)
cancelled_view.on_post_clicked(cancelled_view.post_button)
pump(50)
check('the composer is polling for the upload', polls != [])
check('the waiting round holds the inhibition', len(stub_app.held) == 1)
cancelled_view.reset()
check('a reset gives the inhibition back and ends the round',
      stub_app.held == set() and not cancelled_view._posting
      and cancelled_view.post_button.get_sensitive() is False
      and not cancelled_view.progress_spinner.get_visible())
pump(50)
polls_at_reset = len(polls)
# Only now does the in-flight reply arrive, cancelled.
StubClient.release_status()
pump(50)
# The processing timer is 1.5 s. Waiting a fixed 1.8 s races a loaded
# scheduler; wait long enough that a retry would have been queued, and
# stop early if one is.
retried = False
end = GLib.get_monotonic_time() + 4 * GLib.USEC_PER_SEC
while GLib.get_monotonic_time() < end:
    pump(50)
    if len(polls) != polls_at_reset:
        retried = True
        break
check('a poll reply arriving after a reset is not retried', not retried)
check('a cancelled upload does not post', posts == [])
check('a cancelled upload toasts nothing about media',
      not any('upload' in t.lower() for t in collect_toasts()))
cancelled_window.destroy()
StubMediaResult.cancelled = False
StubMediaResult.needs_processing = False
StubMediaResult.defer_status = False
StubMediaResult.held = []

# A composer finalized while it holds the inhibition gives it back.
held_view = BabbleComposeView(client)
held_view._inhibit_session()
held_cookie = stub_app.inhibited[-1][3]
check('an unparented composer still takes the inhibition',
      held_cookie in stub_app.held and stub_app.inhibited[-1][0] is None)
held_ref = weakref.ref(held_view)
del held_view
gc.collect()
check('a finalized composer gives the inhibition back',
      held_ref() is None and held_cookie not in stub_app.held)

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose widget checks passed')
