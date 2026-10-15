#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the BabbleComposeView widget flow end to end with a stub
client (no real token), validating the POST payload shape against
docs.joinmastodon.org/methods/statuses and /methods/media."""

import gc
import inspect
import json
import os
import shutil
import sys
import tempfile
import time
import weakref

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

# Pasted images are kept under the user cache: the test's go to a
# throwaway one. Before GLib is loaded, as it reads the variable once.
cache_home = tempfile.mkdtemp(prefix='babble-test-cache-')
os.environ['XDG_CACHE_HOME'] = cache_home

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

from gi.repository import (Adw, Gdk, Gio, GLib, Gtk,  # noqa: E402
                           GtkSource, Spelling)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcroot = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, srcroot)
sys.path.insert(0, srcdir)

from src.compose import (MAX_ATTACHMENTS, VISIBILITIES,  # noqa: E402
                         BabbleComposeView, ComposeDialog, countable_length)
from src import compose, emojichooser  # noqa: E402
from src.drafts import Attachment, Draft  # noqa: E402
from src.mentioncompleter import mention_query  # noqa: E402
from gettext import gettext as _, ngettext  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# The composer reaches the desktop portals through portals.py, which
# needs nothing from the window. The test still loads the resource bundle,
# because the composer's widgets live in it.
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.dirname(
    os.environ.get('GSETTINGS_SCHEMA_DIR')
    or os.path.join(srcroot, 'build', 'data'))
Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))._register()
import src.portals as portals_module  # noqa: E402

SCREENSHOT = 'org.freedesktop.portal.Screenshot'
#: Portal interface -> version the stub portal_version answers.
portal_versions = {SCREENSHOT: 1}
#: What session_bus hands over; None plays a missing session bus.
session_bus = [object()]
#: (interface, method, signature, args, options) of each portal call.
portal_calls = []
#: (code, results) answered to the next calls, in order; with none
#: left, a call's on_response is held in held_responses.
portal_answers = []
held_responses = []

check('portals.py keeps the helpers the composer uses',
      all(callable(getattr(portals_module, name, None))
          for name in ('portal_version', 'session_bus', 'portal_request'))
      and list(inspect.signature(
          portals_module.portal_request).parameters) == [
              'bus', 'interface', 'method', 'signature', 'args', 'options',
              'on_response'])


def stub_portal_version(interface, callback):
    callback(portal_versions.get(interface, 0))


def stub_session_bus(callback):
    callback(session_bus[0])


def stub_portal_request(bus, interface, method, signature, args, options,
                        on_response):
    # The real helper adds the handle_token and sends this very tuple.
    GLib.Variant(signature, (*args, dict(
        options, handle_token=GLib.Variant('s', 'babble_test'))))
    portal_calls.append((interface, method, signature, args,
                         {key: value.unpack()
                          for key, value in options.items()}))
    if portal_answers:
        on_response(*portal_answers.pop(0))
    else:
        held_responses.append(on_response)


portals_module.portal_version = stub_portal_version
portals_module.session_bus = stub_session_bus
portals_module.portal_request = stub_portal_request

# A 1x1 PNG so attachment thumbnails render for real.
PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080600000'
    '01f15c4890000000d4944415478da6360000002000100ffff030000060005'
    '57bfabd40000000049454e44ae426082')

MEDIA_RESPONSES = ['9901', '9902']

posts = []
uploads = []
polls = []
searches = []

SEARCH_ACCOUNTS = [
    {'id': '1', 'acct': 'alice', 'username': 'alice',
     'display_name': 'Alice :blobcat:'},
    {'id': '2', 'acct': 'bob@remote.example', 'username': 'bob',
     'display_name': ''},
]


class StubMediaResult:
    """Results are opaque; the view only passes them to *_finish."""

    #: Set by a test to make the instance answer an undecoded upload.
    needs_processing = False
    #: Set by a test to answer with a cancellation instead of data.
    cancelled = False
    #: Set by a test to hold status replies until it releases them.
    defer_status = False
    #: Replies withheld by defer_status, released by release_status().
    held = []


class StubClient:
    """Minimal stand-in matching the MastodonClient surface used by
    BabbleComposeView (post_status/media_upload), with counters."""

    def __init__(self):
        self.token = None

    def post_status_async(self, text, params=None, callback=None,
                          cancellable=None):
        form = {'status': text}
        if params:
            form.update(params)
        posts.append(form)
        def cb(source, result, _user_data=None):
            callback(source, result)
        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def post_status_finish(self, result):
        return {'id': 'invented', 'content': '<p>x</p>'}

    def media_upload_async(self, file_bytes, filename, content_type,
                           description=None, callback=None,
                           cancellable=None):
        uploads.append({'bytes': len(file_bytes), 'filename': filename,
                        'content_type': content_type,
                        'description': description,
                        'id': '%d' % (9900 + len(uploads) + 1)})
        index = len(uploads) - 1

        def cb(source, result, _user_data=None):
            media = {'id': uploads[index]['id']}
            if result.needs_processing:
                # The instance accepted the bytes but has not decoded
                # them yet: no url, so the composer must poll.
                media['url'] = None
            else:
                media['url'] = 'https://example.social/media/%s.png' % index
            result._media = media  # type: ignore[attr-defined]
            callback(source, result)

        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def media_upload_finish(self, result):
        return result._media

    def media_status_async(self, media_id, callback=None, cancellable=None):
        polls.append(media_id)

        def cb(source, result, _user_data=None):
            # First poll still reports no url, as an instance mid-transcode
            # would; the second one is ready.
            ready = polls.count(media_id) > 1
            result._media = {  # type: ignore[attr-defined]
                'id': media_id,
                'url': ('https://example.social/media/%s.png' % media_id
                        if ready else None)}
            callback(source, result)

        result = StubMediaResult()
        if StubMediaResult.defer_status:
            # Hold the reply so a test can order it against a reset; a
            # reply that lands after the draft is gone is the race.
            StubMediaResult.held.append((self, result, cb))
        else:
            GLib.idle_add(lambda *_: (cb(self, result),
                                      GLib.SOURCE_REMOVE)[1])
        return None

    @staticmethod
    def release_status():
        """Answer every held status reply."""
        held, StubMediaResult.held = StubMediaResult.held, []
        for client, result, cb in held:
            cb(client, result)

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        # Only the mention completer's account search goes through here.
        searches.append((method, path, dict(params or {})))
        result = StubMediaResult()
        GLib.idle_add(lambda *_: (callback(self, result),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def request_finish(self, result):
        return SEARCH_ACCOUNTS

    def media_status_finish(self, result):
        if result.cancelled:
            # What a real cancellation looks like to the caller: a
            # GLib.Error in the Gio.IOErrorEnum.CANCELLED domain, not a
            # server error, so retrying it would be nonsense.
            raise GLib.Error('Operation was cancelled',
                             Gio.io_error_quark(),
                             Gio.IOErrorEnum.CANCELLED)
        return result._media


Adw.init()
Gtk.init()


class StubApp(Gtk.Application):
    """The default application, recording the logout and suspend
    inhibitions the composer takes while posting."""

    def __init__(self):
        super().__init__(application_id='io.github.mauriciobc.BabbleTest',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.inhibited = []
        self.held = set()
        self.released = []

    def inhibit(self, window, flags, reason):
        cookie = len(self.inhibited) + 1
        self.inhibited.append((window, flags, reason, cookie))
        self.held.add(cookie)
        return cookie

    def uninhibit(self, cookie):
        self.released.append(cookie)
        self.held.discard(cookie)


stub_app = StubApp()
Gio.Application.set_default(stub_app)
INHIBIT_FLAGS = (Gtk.ApplicationInhibitFlags.LOGOUT
                 | Gtk.ApplicationInhibitFlags.SUSPEND)

# Toasts already speak through libadwaita: what pages.announce is asked
# to say on top of them would be said twice.
announced = []
compose.pages.announce = lambda widget, text, *a: announced.append(text)

# Network-free widget exercise: no display timing needed beyond pumps.
client = StubClient()
view = BabbleComposeView(client)

window = Gtk.Window()
window.set_title('compose test')
window.set_child(view)
window.set_default_size(420, 720)
window.present()

# Capture the toasts the composer raises on its own overlay (the window's
# is covered by the dialog). Adw.ToastOverlay has no get_toasts(), so the
# view's toast helper is wrapped instead.
toast_titles = []
_original_add_toast = view._add_toast


def _capture_toast(toast):
    toast_titles.append(toast.get_title())
    _original_add_toast(toast)


view._add_toast = _capture_toast


def collect_toasts():
    """Titles raised since the last call."""
    texts = list(toast_titles)
    toast_titles.clear()
    return texts

# The success toast after the dialog closes goes through the window.
window.show_toast = lambda text: toast_titles.append(text)  # type: ignore


def pump(millis=100):
    deadline = GLib.get_monotonic_time() + millis * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        ctx.iteration(False)
    while ctx.iteration(False):
        pass


def settle_until(deadline_s, condition):
    """Pump until *condition* holds, or the deadline passes.

    Used wherever a widget's own frame has to land before it can be
    inspected: a popover's visibility, a dialog's 'closed' signal. A
    fixed pump is either too long on a fast machine or too short on a
    loaded one, and this test file has both kinds of wait.
    """
    end = GLib.get_monotonic_time() + int(deadline_s * GLib.USEC_PER_SEC)
    while GLib.get_monotonic_time() < end and not condition():
        pump(50)
    return condition()


def set_text(text):
    view.buffer.set_text(text)
    view.on_draft_changed()


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
check('emoji button opens the emoji chooser',
      isinstance(chooser, emojichooser.CustomEmojiChooser)
      and chooser.get_parent() is view.emoji_button
      and chooser.get_visible())
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

# Attachments through the same internal path used by the file dialog.
set_text('hello babble')
tmp_png = os.path.join('/tmp', 'babble-test.png')
with open(tmp_png, 'wb') as f:
    f.write(PNG_BYTES)
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

# Re-post with CW text preserved and sensitive flag.
collect_toasts()
posts.clear()
set_text('spoiled content')
view.cw_toggle.set_active(True)
view.spoiler_entry.set_text('CW title')
pump(50)
view.on_post_clicked(view.post_button)
pump(400)
form = posts[0]
check('CW title and sensitive flag', form.get('spoiler_text') == 'CW title'
      and form.get('sensitive') is True)

# Reply prefill keeps the previous draft, restores after send.
collect_toasts()
posts.clear()
set_text('my in-progress draft')
view.start_reply('12345', 'someone@instance.social', 'private')
pump(50)
check('reply prefill mentions author',
      view.buffer.get_text(view.buffer.get_start_iter(),
                           view.buffer.get_end_iter(), False)
      == '@someone@instance.social ')
check('reply assigned', view.in_reply_to_id == '12345')
check('reply banner visible', view.reply_banner.get_revealed())
check('reply visibility set from parent',
      VISIBILITIES[view.visibility_row.get_selected()][0] == 'private')
view.on_post_clicked(view.post_button)
pump(400)
form = posts[0]
check('reply payload in_reply_to_id', form.get('in_reply_to_id') == '12345')
check('reply payload visibility', form.get('visibility') == 'private')
check('previous draft restored after reply',
      view.buffer.get_text(view.buffer.get_start_iter(),
                           view.buffer.get_end_iter(), False)
      == 'my in-progress draft')
check('reply cleared', view.in_reply_to_id is None)

# Send without text is refused.
posts.clear()
set_text('')
view.on_post_clicked(view.post_button)
check('empty draft refuses to send', len(posts) == 0)

# Attachment removal works.
view.add_attachment_path(tmp_png)
check('attachment added again', len(view.attachments()) == 1)
row = view.attachments()[0]
view.on_remove_attachment(row.remove_button, row)
check('attachment removed', len(view.attachments()) == 0)

# Dialog: draft survives close and re-present, title follows reply state.
app_window = Adw.ApplicationWindow()
app_window.present()
dialog = ComposeDialog(client)
dialog.present(app_window)
pump(200)
check('dialog title new post', dialog.get_title() == _('New Post'))
check('dialog focus widget is text view',
      dialog.get_focus() is dialog.view.text_view)
dialog.view.buffer.set_text('kept draft')
dialog.close()
pump(200)
dialog.present(app_window)
pump(200)
check('draft kept after close',
      dialog.view.text() == 'kept draft')
dialog.start_reply('77', 'a@b.c', 'unlisted')
pump(50)
check('dialog title reply', dialog.get_title() == _('Reply'))
dialog.close()
pump(100)

# Local drafts: a close with content keeps it for the next launch,
# keyed with the related account, so restoring happens there only.
from src import pages  # noqa: E402
local_settings = pages.settings()

def saved_list():
    return local_settings.get_strv('local-drafts')

local_settings.set_strv('local-drafts', [])
dialog.view.reset()
dialog.view.local_draft_key = 'mastodon.social#11'
dialog.present(app_window)
pump(50)
dialog.view.buffer.set_text('draft for later')
dialog.close()
settle_until(3.0, lambda: saved_list())
saved = saved_list()
check('closing with content saves one draft', len(saved) == 1)
check('the saved draft is keyed with the account',
      bool(saved) and '"key": "mastodon.social#11"' in saved[0])
dialog.present(app_window)
pump(50)
dialog.close()
pump(300)
check('closing unchanged does not duplicate',
      saved_list() == saved)
dialog.present(app_window)
pump(50)
dialog.view.buffer.set_text('changed draft')
dialog.close()
settle_until(3.0, lambda: saved_list() != saved)
check('a changed draft replaces its earlier version',
      len(saved_list()) == 1 and 'changed draft' in saved_list()[0])

# Restoring keeps what was already being written: the content in
# progress moves to the draft stack, out where the banner above the
# buffer brings it back.
dialog.present(app_window)
pump(50)
dialog.view.buffer.set_text('in progress')
dialog.view.restore_stashing(Draft.parse({
    'text': 'recovered draft', 'visibility': 0, 'language': 0,
    'cw_visible': False, 'cw_text': '', 'attachments': [],
    'poll': {'active': False, 'options': ['', ''], 'expires': 0},
    'in_reply_to_id': None, 'key': 'mastodon.social#11', 'saved_at': 'x'}))
pump(50)
check('restore replaces the buffer',
      dialog.view.text() == 'recovered draft')
check('restore stashes the content in progress',
      bool(dialog.view._draft_stack)
      and dialog.view._draft_stack[0].text == 'in progress')

# The draft that was just posted is no longer offered for restoring.
dialog.present(app_window)
pump(50)
dialog.view.buffer.set_text('about to be posted')
collect_toasts()
dialog.view.on_post_clicked(dialog.view.post_button)
pump(400)
remaining = local_settings.get_strv('local-drafts')
check('a posted draft is taken off the restore list', remaining == [])

# The composer contract other modules rely on: timeline.py, profile.py
# and scheduled.py call these names, and a missing one raises
# AttributeError in a signal callback after the action already ran.
for _method in ('start_reply', 'start_mention', 'start_edit', 'start_redraft',
                'start_quote', 'start_scheduled'):
    check('ComposeDialog.%s exists' % _method,
          callable(getattr(ComposeDialog, _method, None)))

# Mastodon's StatusLengthValidator: URLs collapse to 23 characters,
# remote mentions to @user, and the content warning counts too.
check('URL counts as 23', countable_length(
    'https://example.com/a/b/c/d/e') == 23)
check('remote mention counts as @user',
      countable_length('@alice@remote.example hi') == 9)
check('CW text counts',
      countable_length('', 'content warning') == len('content warning'))
check('plain text counts per grapheme', countable_length('hello') == 5)

# Thread mode chains posts: the next one replies to the id just posted
# and the media and poll are cleared.
posts.clear()
set_text('first of the thread')
view.thread_toggle.set_active(True)
view.on_post_clicked(view.post_button)
pump(400)
check('thread clears the buffer for the next post', view.text() == '')
check('thread links the next post', view.in_reply_to_id == 'invented')
view.thread_toggle.set_active(False)
view.thread_toggle.set_active(True)
view.reset()
check('reset clears thread toggle', not view.thread_toggle.get_active())

# A new direct message keeps the draft in progress (conversations.py
# calls start_reply(None, '', 'direct')).
view.reset()
set_text('a draft worth keeping')
view.start_reply(None, '', 'direct')
pump(50)
check('new message keeps the draft',
      view.text() == 'a draft worth keeping')
check('new message sets direct visibility',
      VISIBILITIES[view.visibility_row.get_selected()][0] == 'direct')
view.reset()

# Mention suggestions: the token under the cursor follows Mastodon's
# mention rule, so addresses and URLs never open the list.
check('mention query at the start', mention_query('@al') == (0, 'al'))
check('mention query after a space', mention_query('hi @al') == (3, 'al'))
check('mention query after punctuation', mention_query('(@al') == (1, 'al'))
check('bare @ is an empty query', mention_query('hi @') == (3, ''))
check('remote mention keeps its domain',
      mention_query('@bob@remote.ex') == (0, 'bob@remote.ex'))
check('an email address is no mention',
      mention_query('mail me@example.com') is None)
check('a URL path is no mention',
      mention_query('https://example.com/@al') is None)
check('a finished mention is no query', mention_query('@al ') is None)

completer = view.mention_completer
NO_MODS = Gdk.ModifierType(0)


def press(keyval, state=NO_MODS):
    return completer._on_key_pressed(None, keyval, 0, state)


def settle():
    """Run the loop until the completer's debounced search has been
    answered: a fixed wait is too short on a loaded machine."""
    deadline = GLib.get_monotonic_time() + 3 * 1000 * 1000
    while (completer._search_id or completer._cancellable is not None) \
            and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)


def type_text(text, cursor=None):
    view.buffer.set_text(text)
    position = (view.buffer.get_end_iter() if cursor is None
                else view.buffer.get_iter_at_offset(cursor))
    view.buffer.place_cursor(position)
    settle()


view.reset()
view.text_view.grab_focus()
searches.clear()
type_text('hi @al')
check('typing a mention searches accounts',
      searches and searches[-1][1] == '/accounts/search'
      and searches[-1][2].get('q') == 'al'
      and 'resolve' not in searches[-1][2])
rows = [completer.list_box.get_row_at_index(i) for i in range(3)]
check('suggestions pop up under the mention', completer.get_visible())
check('one row per account, the first selected',
      rows[0] is not None and rows[1] is not None and rows[2] is None
      and completer.list_box.get_selected_row() is rows[0])
check('custom emoji are dropped from the shown name',
      rows[0].acct == 'alice'
      and rows[0].get_child().get_last_child().get_first_child()
      .get_label() == 'Alice')
check('rows activate on a single click',
      completer.list_box.get_activate_on_single_click())
check('Shift+Enter is left to the text view for a line break',
      not press(Gdk.KEY_Return, Gdk.ModifierType.SHIFT_MASK)
      and completer.get_visible())
check('Down moves the selection', press(Gdk.KEY_Down)
      and completer.list_box.get_selected_row() is rows[1])
check('Ctrl+Enter is left to the composer',
      not press(Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK))
check('Enter inserts the selected account', press(Gdk.KEY_Return)
      and view.text() == 'hi @bob@remote.example ')
check('the list closes after inserting', not completer.get_visible())
cursor = view.buffer.get_iter_at_mark(view.buffer.get_insert())
check('the cursor lands after the mention',
      cursor.get_offset() == len(view.text()))

# Results for an older query are never inserted into a newer one.
type_text('@al')
view.buffer.insert_at_cursor('x')
check('older results are out of reach while the new query runs',
      completer.list_box.get_selected_row() is None
      and not completer.list_box.get_sensitive()
      and not press(Gdk.KEY_Return) and view.text() == '@alx')
settle()
check('new results make the list usable again',
      completer.list_box.get_sensitive()
      and completer.list_box.get_selected_row() is not None)

type_text('@al rest', cursor=2)
check('a mention mid-word still suggests', completer.get_visible())
completer._on_row_activated(completer.list_box,
                            completer.list_box.get_row_at_index(0))
check('picking replaces the whole name and keeps the space',
      view.text() == '@alice rest'
      and view.buffer.get_iter_at_mark(
          view.buffer.get_insert()).get_offset() == len('@alice '))

type_text('@al')
check('Escape closes the list', press(Gdk.KEY_Escape)
      and not completer.get_visible())
view.buffer.place_cursor(view.buffer.get_end_iter())
settle()
check('a closed mention stays closed while it is unchanged',
      not completer.get_visible())
view.buffer.insert_at_cursor('i')
settle()
check('typing on reopens it', completer.get_visible()
      and searches[-1][2].get('q') == 'ali')

stale = completer._serial
type_text('@bob@remote.example')
check('a full remote address resolves',
      searches[-1][2].get('resolve') == 'true')
completer._clear_rows()
completer._on_search_done(client, StubMediaResult(), stale)
check('a stale reply is ignored',
      completer.list_box.get_row_at_index(0) is None)

view.reset()
check('reset closes the list', not completer.get_visible())

# The Mention button starts a mention set off from the word before it
# and shows the hint until a name is typed.
set_text('hello')
view.buffer.place_cursor(view.buffer.get_end_iter())
view.on_mention_clicked(view.mention_button)
pump(50)
check('mention button spaces the @ from a word', view.text() == 'hello @')
check('mention button shows the hint', completer.get_visible()
      and completer.hint_label.get_visible())
view.buffer.insert_at_cursor('al')
settle()
check('typing replaces the hint with suggestions',
      not completer.hint_label.get_visible()
      and completer.list_box.get_row_at_index(0) is not None)
view.reset()
view.on_mention_clicked(view.mention_button)
check('mention button on an empty draft adds a bare @', view.text() == '@')
view.reset()

# Drag and drop: files dropped on the content are attached when they
# are images, videos or audio, through the drop target's own signal.
media_dir = tempfile.mkdtemp(prefix='babble-test-media-')


def media_file(name, data=PNG_BYTES):
    path = os.path.join(media_dir, name)
    with open(path, 'wb') as f:
        f.write(data)
    return path


def file_list(paths):
    return Gdk.FileList.new_from_list([Gio.File.new_for_path(p)
                                       for p in paths])


def drop(value):
    return view.drop_target.emit('drop', value, 0.0, 0.0)


ONLY_MEDIA = _('Only images, videos and audio can be attached')
LONE_MEDIA = _('A video or audio file must be the only attachment')
BUSY_POSTING = _('Wait for the post to be sent to attach files')
LIMIT_REACHED = ngettext('A post can have at most %d attachment',
                         'A post can have at most %d attachments',
                         MAX_ATTACHMENTS) % MAX_ATTACHMENTS
png_a, png_b = media_file('a.png'), media_file('b.png')
notes = media_file('notes.txt', b'just some words')
collect_toasts()
check('the drop target takes file lists and files',
      view.drop_target.get_formats().contain_gtype(Gdk.FileList)
      and view.drop_target.get_formats().contain_gtype(Gio.File))
check('a dropped list of images is accepted',
      drop(file_list([png_a, png_b])) is True)
check('dropped images add one row each',
      [row.path for row in view.attachments()] == [png_a, png_b]
      and collect_toasts() == [])
check('a dropped file that is no image or video is refused',
      drop(Gio.File.new_for_path(notes)) is False
      and len(view.attachments()) == 2)
check('the refused file is told in a toast', collect_toasts() == [ONLY_MEDIA])
check('a mixed drop keeps the media and refuses the rest',
      drop(file_list([notes, media_file('c.png')])) is True
      and len(view.attachments()) == 3
      and collect_toasts() == [ONLY_MEDIA])
drop(file_list([media_file('d.png'), media_file('e.png')]))
check('a drop stops at the attachment limit',
      len(view.attachments()) == MAX_ATTACHMENTS
      and not view.attach_button.get_sensitive())
check('the limit is told in a toast', collect_toasts() == [LIMIT_REACHED])
view.reset()

# Audio is attached as Mastodon takes it: alone, with an audio icon in
# place of a thumbnail.
song = media_file('song.mp3', b'ID3\x03\x00\x00\x00\x00\x00\x00' + bytes(64))
check('a dropped audio file is accepted',
      drop(Gio.File.new_for_path(song)) is True
      and [row.path for row in view.attachments()] == [song]
      and collect_toasts() == [])
audio_row = view.attachments()[0] if view.attachments() else None
check('the audio uploads with its own type',
      audio_row is not None and audio_row.content_type == 'audio/mpeg')
check('an audio row shows the audio icon, not a texture',
      isinstance(getattr(audio_row, 'thumbnail', None), Gtk.Image)
      and audio_row.thumbnail.get_icon_name() == 'audio-x-generic-symbolic')
check('nothing more can be attached to an audio file',
      not view.attach_button.get_sensitive()
      and drop(Gio.File.new_for_path(png_a)) is False
      and len(view.attachments()) == 1
      and collect_toasts() == [LONE_MEDIA])
view.reset()
drop(Gio.File.new_for_path(png_a))
clip = media_file('clip.ogg', b'OggS' + bytes(60))
check('an audio file does not join an image',
      drop(file_list([clip])) is False
      and [row.path for row in view.attachments()] == [png_a]
      and collect_toasts() == [LONE_MEDIA]
      and view.attach_button.get_sensitive())
view.reset()


class StubFileDialog:
    """What on_files_selected gets back from Gtk.FileDialog."""

    def __init__(self, paths):
        self.files = Gio.ListStore.new(Gio.File)
        for path in paths:
            self.files.append(Gio.File.new_for_path(path))

    def open_multiple_finish(self, _result):
        return self.files


view.on_files_selected(StubFileDialog([clip]), None)
check('an audio file picked in the file dialog is attached',
      [row.path for row in view.attachments()] == [clip]
      and view.attachments()[0].content_type == 'audio/ogg'
      and collect_toasts() == [])
view.reset()
view.on_files_selected(StubFileDialog([png_a, notes, clip]), None)
check('the file dialog path refuses what a drop refuses',
      [row.path for row in view.attachments()] == [png_a]
      and collect_toasts() == [ONLY_MEDIA, LONE_MEDIA])
check('compose.is_media takes audio', compose.is_media('audio/ogg')
      and compose.is_media('video/mp4') and compose.is_media('image/png')
      and not compose.is_media('text/plain') and not compose.is_media(None))
view.reset()
view.set_posting_ui(True)
check('no drop is taken while posting',
      view.drop_target.get_actions() == Gdk.DragAction.NONE)
view._posting = True
collect_toasts()
check('nothing is attached while posting',
      view.add_attachment_files([png_a]) == [] and view.attachments() == [])
check('a refusal while posting is told in a toast',
      collect_toasts() == [BUSY_POSTING])
view._posting = False
view.set_posting_ui(False)
check('drops are taken again after posting',
      view.drop_target.get_actions() == Gdk.DragAction.COPY)

# Paste: a bare image on the clipboard (a screenshot) is saved as a PNG
# under the cache and attached; text pastes as text.
if view.emoji_chooser is not None:
    view.emoji_chooser.popdown()
    view.emoji_chooser.unparent()
    view.emoji_chooser = None
pump(200)
clipboard = view.text_view.get_clipboard()
clipboard.set_content(Gdk.ContentProvider.new_for_bytes(
    'text/plain;charset=utf-8', GLib.Bytes.new(b'')))
settle_until(2.0, lambda: clipboard.get_formats() is not None)


def clipboard_owns(previous):
    """Whether the clipboard has really taken new content yet.

    ``Gdk.Clipboard.set_content`` is a request, not an assignment: until
    the selection has processed it, a paste reads back whatever was
    there before. Matching on the *type* on offer cannot tell the two
    apart (the earlier owner is often a text or a texture too), so this
    compares the owner itself: the clipboard is ours once it is local
    and its content provider is no longer the one held before the set.
    """
    current = clipboard.get_content()
    return (clipboard.is_local() and current is not None
            and current is not previous)


def paste(content, settle_ms=300, attachments=False):
    """Set the clipboard to *content* and paste into the composer.

    Two waits, because there are two things to wait for and the second
    used to be given the first one's deadline:

    * the clipboard actually owning *content*, or the paste reads back
      the previous one (see :func:`clipboard_owns`);
    * the async read that produces an attachment row.

    Both are conditions with their own generous deadline rather than a
    fixed pump: a pump that is merely long enough is exactly what a busy
    machine cannot rely on.
    """
    previous = clipboard.get_content()
    if isinstance(content, Gdk.ContentProvider):
        clipboard.set_content(content)
    elif isinstance(content, str):
        clipboard.set_content(Gdk.ContentProvider.new_for_bytes(
            'text/plain;charset=utf-8',
            GLib.Bytes.new(content.encode('utf-8'))))
    else:
        clipboard.set(content)
    settle_until(PASTE_WAIT_S, lambda: clipboard_owns(previous))
    view.text_view.emit('paste-clipboard')
    if not attachments:
        pump(settle_ms)
        return
    count_on_entry = len(view.attachments())
    if not settle_until(PASTE_WAIT_S,
                       lambda: len(view.attachments()) > count_on_entry):
        return
    # The row is there; the rest of its state (path, content type) is set
    # on the way in, so let the loop settle before anything reads it.
    pump(50)


texture = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8,
                                GLib.Bytes.new(bytes(range(16))), 8)
collect_toasts()
paste(texture, attachments=True)
rows = view.attachments()
pasted = rows[0].path if rows else ''
check('pasting an image adds one attachment', len(rows) == 1)
check('the pasted image is kept under the user cache',
      pasted.startswith(os.path.join(cache_home, 'babble', 'pasted') + '/'))
with open(pasted or os.devnull, 'rb') as f:
    check('the pasted image is a PNG file',
          f.read(8) == b'\x89PNG\r\n\x1a\n')
check('the pasted image uploads as image/png',
      bool(rows) and rows[0].content_type == 'image/png')
check('pasting an image inserts no text', view.text() == '')
view.text_view.emit('paste-clipboard')
pump(300)
rows = view.attachments()
check('a second paste gets a file of its own',
      len(rows) == 2 and rows[1].path != pasted
      and os.path.isfile(rows[1].path))
view.on_remove_attachment(rows[0].remove_button, rows[0])
check('removing a pasted image keeps its file for undo',
      os.path.isfile(pasted))
check('pasting images raised no toast of its own',
      collect_toasts() == [_('Attachment removed')])
view.reset()

paste(Gdk.ContentProvider.new_for_value(file_list([png_a, notes])),
      attachments=True)
check('pasting files copied in Files attaches the media among them',
      [row.path for row in view.attachments()] == [png_a]
      and view.text() == '' and collect_toasts() == [ONLY_MEDIA])
view.reset()

view._posting = True
paste(texture, attachments=True)
check('an image pasted while posting is refused with a toast',
      view.attachments() == [] and collect_toasts() == [BUSY_POSTING])
paste('pasted words')
check('text still pastes while posting', view.text() == 'pasted words')
view._posting = False
view.reset()

paste('pasted words')
check('pasting text inserts it', view.text() == 'pasted words')
check('pasting text adds no attachment', view.attachments() == [])
view.reset()

# Screenshot: taken through the portal, copied under the user cache as
# pasted images are, and attached.
SHOT_FAILED = _('Could not take a screenshot')


def take_screenshot(*answers):
    portal_answers.extend(answers)
    view.screenshot_button.emit('clicked')
    pump(50)


def cached_files():
    return sorted(os.listdir(compose.pasted_dir()))


check('the screenshot button is labelled Take Screenshot',
      view.screenshot_button.get_tooltip_text() == _('Take Screenshot'))
portal_versions[SCREENSHOT] = 0
no_portal = BabbleComposeView(client)
check('the screenshot button is hidden without the portal',
      not no_portal.screenshot_button.get_visible())
del no_portal
portal_versions[SCREENSHOT] = 1

shot = media_file('Screenshot From 2026-10-12 10-00-00.png')
shot_uri = Gio.File.new_for_path(shot).get_uri()
collect_toasts()
portal_calls.clear()
take_screenshot((0, {'uri': shot_uri}))
rows = view.attachments()
check('the portal is asked for an interactive, modal screenshot',
      portal_calls == [(SCREENSHOT, 'Screenshot', '(sa{sv})', ('',),
                        {'interactive': True, 'modal': True})])
check('a screenshot taken is attached', len(rows) == 1
      and collect_toasts() == [])
shot_copy = rows[0].path if rows else ''
check('the screenshot is copied under the user cache, keeping its name',
      shot_copy == os.path.join(compose.pasted_dir(),
                                os.path.basename(shot)))
with open(shot_copy or os.devnull, 'rb') as f:
    check('the copy holds the screenshot', f.read() == PNG_BYTES)
check('the portal file is left where it is', os.path.isfile(shot))
check('the screenshot uploads as image/png',
      bool(rows) and rows[0].content_type == 'image/png')
take_screenshot((0, {'uri': shot_uri}))
rows = view.attachments()
check('a second screenshot of that name gets a file of its own',
      len(rows) == 2 and rows[1].path != shot_copy
      and os.path.isfile(rows[1].path))
view.reset()

before = cached_files()
take_screenshot((1, {}))
check('a cancelled screenshot attaches nothing and says nothing',
      view.attachments() == [] and collect_toasts() == [])
for answer in ((2, {}), (None, {}), (0, {})):
    take_screenshot(answer)
    check('a failed screenshot (%r, %r) is told in a toast' % answer,
          view.attachments() == [] and collect_toasts() == [SHOT_FAILED])
session_bus[0] = None
calls = len(portal_calls)
take_screenshot()
check('no session bus is told in a toast',
      len(portal_calls) == calls and collect_toasts() == [SHOT_FAILED])
session_bus[0] = object()
take_screenshot((0, {'uri': Gio.File.new_for_path(
    os.path.join(media_dir, 'gone.png')).get_uri()}))
toasts = collect_toasts()
check('a screenshot that cannot be copied is told in a toast',
      view.attachments() == [] and len(toasts) == 1
      and toasts[0].startswith(SHOT_FAILED + ': '))
check('failed screenshots leave no file behind', cached_files() == before)

# The answer can come after posting started, or after the draft filled
# up: then nothing is copied either.
take_screenshot()
view._posting = True
view.set_posting_ui(True)
check('the screenshot button is off while posting',
      not view.screenshot_button.get_sensitive())
held_responses.pop()(0, {'uri': shot_uri})
check('a screenshot taken while posting is refused with a toast',
      view.attachments() == [] and collect_toasts() == [BUSY_POSTING]
      and cached_files() == before)
view._posting = False
view.set_posting_ui(False)
check('the screenshot button is back after posting',
      view.screenshot_button.get_sensitive())
take_screenshot()
view.add_attachment_files([media_file('f%d.png' % n)
                           for n in range(MAX_ATTACHMENTS)])
check('the screenshot button is off at the attachment limit',
      not view.screenshot_button.get_sensitive())
held_responses.pop()(0, {'uri': shot_uri})
check('a screenshot past the limit is refused with a toast',
      len(view.attachments()) == MAX_ATTACHMENTS
      and collect_toasts() == [LIMIT_REACHED] and cached_files() == before)
view.on_remove_attachment(view.attachments()[0].remove_button,
                          view.attachments()[0])
check('the screenshot button is back below the limit',
      view.screenshot_button.get_sensitive())
view.reset()
collect_toasts()

# A composer closed while the portal is open is not kept alive by it.
gone = BabbleComposeView(client)
gone.screenshot_button.emit('clicked')
gone_ref = weakref.ref(gone)
del gone
gc.collect()
check('a pending screenshot does not keep the composer alive',
      gone_ref() is None)
held_responses.pop()(0, {'uri': shot_uri})
check('its answer then attaches nowhere', cached_files() == before)

# Spell checking: libspelling on the composer's buffer, in the language
# picked for the post.
adapter = view.spell_adapter
check('the composer buffer is a GtkSource.Buffer with no highlighting',
      isinstance(view.buffer, GtkSource.Buffer)
      and not view.buffer.get_highlight_syntax()
      and not view.buffer.get_highlight_matching_brackets()
      and view.buffer.get_style_scheme() is None)
check('the spell adapter checks the composer buffer',
      adapter.get_buffer() == view.buffer)
check('the corrections are the text view context menu extra',
      view.text_view.get_extra_menu() == adapter.get_menu_model())
spell_was_on = adapter.get_enabled()
check('the spelling actions reach the text view',
      view.text_view.activate_action('spelling.enabled', None)
      and adapter.get_enabled() != spell_was_on)
view.text_view.activate_action('spelling.enabled', None)
check('spell checking is back as it was',
      adapter.get_enabled() == spell_was_on)
check('each composer has a checker of its own',
      adapter.get_checker() != Spelling.Checker.get_default()
      and adapter.get_checker() != dialog.view.spell_adapter.get_checker())


def spell_click_at(offset):
    """Right-click the text view at character *offset*, through the
    composer's own capture-phase gesture."""
    controllers = view.text_view.observe_controllers()
    gesture = next(
        controllers.get_item(i) for i in range(controllers.get_n_items())
        if isinstance(controllers.get_item(i), Gtk.GestureClick)
        and controllers.get_item(i).get_button() == Gdk.BUTTON_SECONDARY)
    rect = view.text_view.get_iter_location(
        view.buffer.get_iter_at_offset(offset))
    x, y = view.text_view.buffer_to_window_coords(
        Gtk.TextWindowType.WIDGET, rect.x + 1, rect.y + rect.height // 2)
    gesture.emit('pressed', 1, float(x), float(y))


def cursor_offset():
    return view.buffer.get_iter_at_mark(view.buffer.get_insert()).get_offset()


set_text('first tset last')
view.buffer.place_cursor(view.buffer.get_end_iter())
pump(100)
spell_click_at(7)
check('a right click puts the cursor on the word clicked',
      cursor_offset() == 7 and not view.buffer.get_has_selection())
view.buffer.select_range(view.buffer.get_iter_at_offset(6),
                         view.buffer.get_iter_at_offset(10))
spell_click_at(8)
check('a right click in the selection keeps it',
      view.buffer.get_has_selection() and cursor_offset() in (6, 10))
spell_click_at(1)
check('a right click out of the selection drops it',
      cursor_offset() == 1 and not view.buffer.get_has_selection())
set_text('')

check('a dictionary named as the language is taken as is',
      compose.spelling_language('pt', ['pt', 'pt_BR'], ['pt_BR']) == 'pt')
check('a regional dictionary of the user locale is preferred',
      compose.spelling_language(
          'pt', ['en_US', 'pt_BR', 'pt_PT'],
          ['pt_PT.UTF-8', 'pt_PT', 'pt.UTF-8', 'pt', 'C']) == 'pt_PT')
check('else the first regional dictionary',
      compose.spelling_language('pt', ['pt_PT', 'pt_BR', 'en_US'],
                                ['en_US', 'C']) == 'pt_BR')
check('a dictionary variant counts as the language',
      compose.spelling_language('pt', ['pt_PT-preao'], []) == 'pt_PT-preao')
check('a language without dictionary has none',
      compose.spelling_language('ja', ['en_US', 'jv'], ['ja_JP']) is None)

provider = Spelling.Provider.get_default()
dictionaries = provider.list_languages()
available = [dictionaries.get_item(i).get_code()
             for i in range(dictionaries.get_n_items())]
codes = [lang[0] for lang in compose.LANGUAGES]
covered = [code for code in codes
           if compose.spelling_language(code, available)]
uncovered = [code for code in codes if code not in covered]
if not covered:
    print('# no spelling dictionary installed: only turning spell checking '
          'off is exercised')
for code in covered[:2]:
    view.language_row.set_selected(codes.index(code))
    expected = compose.spelling_language(code, available,
                                         GLib.get_language_names())
    check('the spelling language follows the language row (%s: %s)'
          % (code, expected),
          adapter.get_language() == expected and adapter.get_enabled())
for code in uncovered[:1]:
    view.language_row.set_selected(codes.index(code))
    check('a language with no dictionary turns spell checking off (%s)'
          % code, not adapter.get_enabled())
if covered:
    view.language_row.set_selected(codes.index(covered[0]))
    check('a language with a dictionary turns it back on',
          adapter.get_enabled())
    # Checked on the plain Gtk.TextView: no GtkSource.View is needed.
    set_text('xqzvkj and more')
    view.buffer.place_cursor(view.buffer.get_end_iter())
    check('a misspelled word gets underlined', settle_until(
        3.0, lambda: view.buffer.get_start_iter().has_tag(
            adapter.get_tag())))
    set_text('')
# The drop-down always keeps an item selected; none selected is played
# by shadowing its getter.
view.language_row.get_selected = lambda: Gtk.INVALID_LIST_POSITION
view._update_spell_language()
del view.language_row.get_selected
default_code = provider.get_default_code()
check('with no language picked, the default dictionary is used',
      adapter.get_enabled() == bool(default_code)
      and (not default_code or adapter.get_language() == default_code))
view.reset()

# Text shared from outside the app starts a post, keeping the draft.
shared = BabbleComposeView(client)
shared.prefill_text('shared words')
check('prefill_text fills an empty draft',
      shared.text() == 'shared words'
      and not shared.reply_banner.get_revealed())
shared.prefill_text('another share')
check('prefill_text keeps the draft in progress',
      shared.text() == 'another share'
      and shared.reply_banner.get_revealed())
shared.pop_reply_state()
check('the kept draft comes back', shared.text() == 'shared words')
del shared

# Pasted images outlive their rows, for undo and saved drafts; the old
# ones no saved draft refers to are pruned.
pasted_dir = compose.pasted_dir()
week_ago = time.time() - compose.PASTED_MAX_AGE - 60
old_loose = os.path.join(pasted_dir, 'old loose.png')
old_saved = os.path.join(pasted_dir, 'old saved.png')
for path in (old_loose, old_saved):
    with open(path, 'wb') as f:
        f.write(PNG_BYTES)
    os.utime(path, (week_ago, week_ago))
local_settings.set_strv('local-drafts', [json.dumps(
    {'attachments': [{'path': old_saved, 'alt': ''}]})])
compose.prune_pasted_images()
check('an old pasted image no draft refers to is pruned',
      not os.path.exists(old_loose))
check('an old pasted image a saved draft refers to is kept',
      os.path.isfile(old_saved))
check('a recent pasted image is kept', os.path.isfile(pasted))
local_settings.set_strv('local-drafts', [])
shutil.rmtree(media_dir, ignore_errors=True)
shutil.rmtree(cache_home, ignore_errors=True)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all compose flow checks passed')
