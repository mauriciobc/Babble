#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the BabbleComposeView widget flow end to end with a stub
client (no real token), validating the POST payload shape against
docs.joinmastodon.org/methods/statuses and /methods/media."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gtk

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcroot = os.path.dirname(os.path.abspath(srcdir))
sys.path.insert(0, srcroot)
sys.path.insert(0, srcdir)

from src.compose import (VISIBILITIES, BabbleComposeView,  # noqa: E402
                         ComposeDialog, countable_length)
from gettext import gettext as _  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# A 1x1 PNG so attachment thumbnails render for real.
PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080600000'
    '01f15c4890000000d4944415478da6360000002000100ffff030000060005'
    '57bfabd40000000049454e44ae426082')

MEDIA_RESPONSES = ['9901', '9902']

posts = []
uploads = []
polls = []


class StubMediaResult:
    """Results are opaque; the view only passes them to *_finish."""

    #: Set by a test to make the instance answer an undecoded upload.
    needs_processing = False


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
                        'description': description})
        index = len(uploads) - 1

        def cb(source, result, _user_data=None):
            media = {'id': MEDIA_RESPONSES[index]}
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

        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def media_status_finish(self, result):
        return result._media


Adw.init()
Gtk.init()

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


def set_text(text):
    view.buffer.set_text(text)
    view.on_draft_changed()


MAX = 500

for attr in ('attach_button', 'cw_toggle', 'poll_toggle', 'mention_button',
             'emoji_button', 'thread_toggle'):
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
check('poll box revealed', view._poll_box.get_visible())
view.on_emoji_picked(view.emoji_button, '😀')
check('emoji insertion works', view.buffer.get_text(
    view.buffer.get_start_iter(), view.buffer.get_end_iter(),
    False) == '😀')
view.poll_toggle.set_active(False)
pump(50)
check('poll box hidden again', not view._poll_box.get_visible())
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
check('spoiler hidden initially', not view._spoiler_box.get_visible())
view.cw_toggle.set_active(True)
pump(50)
check('spoiler field revealed', view._spoiler_box.get_visible())
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
check('alt change recorded', row.description == 'red pixel for testing')
check('media shows up in snapshot',
      view.snapshot()['attachments'][0]['alt'] == 'red pixel for testing')

pump(50)

# Post and inspect the payload shape while the spinner runs.
posts.clear()
uploads.clear()
collect_toasts()
view.on_post_clicked(view.post_button)
check('posting progress visible', view.progress_spinner.get_visible())
check('posting disables button', not view.post_button.get_sensitive())
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

form = posts[0]
# See docs.joinmastodon.org/methods/statuses POST /statuses
check('status body present', form.get('status') == 'hello babble')
check('visibility value valid', form.get('visibility') == 'public')
check('language is ISO 639-1', len(form.get('language', '')) == 2)
check('spoiler text from CW field', form.get('spoiler_text') == 'potential spoilers')
check('media_ids array encoded',
      form.get('media_ids') == MEDIA_RESPONSES[:1])
check('no in_reply_to for top post', 'in_reply_to_id' not in form)
check('fixed response returned from stub',
      view.in_reply_to_id is None)

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
# to come back for it after the polling interval.
pump(1800)
check('the post is sent once processing finishes',
      len(posts) == 1
      and posts[0].get('media_ids') == [MEDIA_RESPONSES[-1]])
check('the waited-for post reports success',
      collect_toasts()[-1:] == [_('Post sent')])
processing_window.destroy()

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

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all compose flow checks passed')
