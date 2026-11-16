#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises the file dialog, clipboard, paste, screenshot, cache and
attachment limits with a stub client."""

import gc
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

from gi.repository import (Adw, Gdk, Gio, GLib, Gtk)  # noqa: E402

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
    HOLD_PATHS, PASTE_WAIT_S, PNG_BYTES, SCREENSHOT, StubClient,
    capture_toasts, check, collect_toasts, held_responses, portal_answers,
    portal_calls, portal_versions, posts, pump, session_bus, set_text,
    settle_until, uploads)
from src import composeattach
from src.compose import BabbleComposeView
from src.composeattach import MAX_ATTACHMENTS
from gettext import gettext as _, ngettext

view = kit.view
client = kit.client

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
check('composeattach.is_media takes audio',
      composeattach.is_media('audio/ogg')
      and composeattach.is_media('video/mp4')
      and composeattach.is_media('image/png')
      and not composeattach.is_media('text/plain')
      and not composeattach.is_media(None))
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
# The same image pasted again. The clipboard is set afresh rather than
# assumed to still hold the first paste: under a bare X server nothing
# keeps the selection once it has been read, and by this point it can
# have lost the image (it reported no formats at all), which made a
# correct "nothing to paste" look like a dropped second paste.
paste(texture, attachments=True)
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

# A write the composer makes itself (a restored draft) while a paste is
# still waiting for the clipboard is not that paste: the watcher leaves
# the paste's offset alone, and takes it again once the write is over.
view._pre_paste_offset = 0
view._set_buffer('https://mastodon.social/@user/1')
check('a programmatic write does not consume a pending paste offset',
      view._pre_paste_offset == 0 and view.quoted_status_id is None)
view._pre_paste_offset = None
view.reset()

# Pasting a lone link to a post on a fediverse server attaches the
# post as a quote instead of inserting the URL, like Mastodon's and
# Phanpy's composers do. Everything else pastes as text.
quote_toasts = []
_original_capture = view._add_toast


def _quote_capture(toast):
    quote_toasts.append(toast)


view._add_toast = _quote_capture
kit.SEARCH_ANSWER = {'statuses': [{'id': '4242',
                               'visibility': 'public',
                               'quote_approval': {'current_user': 'automatic'}}]}
paste('https://instance.social/@someone/109876543210')
settle_until(2.0, lambda: view.quoted_status_id == '4242')
check('a pasted post link attaches as a quote',
      view.quoted_status_id == '4242')
check('a pasted post link inserts no text', view.text() == '')
check('the toast offers an undo',
      bool(quote_toasts)
      and quote_toasts[-1].get_button_label() == _('_Undo'))
quote_toasts[-1].emit('button-clicked')
check('undoing the toast detaches the quote and keeps the link',
      view.quoted_status_id is None
      and view.text() == 'https://instance.social/@someone/109876543210')

# A link that resolves to nothing quotable pastes as text instead.
kit.SEARCH_ANSWER = {'statuses': []}
view.reset()
paste('https://instance.social/@someone/109876543210')
settle_until(2.0, lambda: view.text()
             == 'https://instance.social/@someone/109876543210')
check('a link that cannot be a quote pastes as text',
      view.text() == 'https://instance.social/@someone/109876543210')

# A quote already attached: the paste stays a paste, it does not
# replace the quote silently.
view.reset()
kit.SEARCH_ANSWER = {'statuses': [{'id': '4243', 'visibility': 'public'}]}
view.quoted_status_id = 'kept'
paste('https://instance.social/@someone/109876543210')
settle_until(2.0, lambda: view.text()
             == 'https://instance.social/@someone/109876543210')
check('a paste while a quote is attached pastes as text',
      view.text() == 'https://instance.social/@someone/109876543210'
      and view.quoted_status_id == 'kept')

# ...and while editing a published post, which cannot change its quote.
view.reset()
view.start_edit({'id': '777', 'text': 'edit me',
                 'visibility': 'public', 'language': 'en',
                 'spoiler_text': '',
                 'media_attachments': []})
kit.SEARCH_ANSWER = {'statuses': [{'id': '4244', 'visibility': 'public'}]}
paste('https://instance.social/@someone/109876543210')
settle_until(2.0, lambda: '109876543210' in view.text())
check('a paste while editing attaches no quote',
      view.quoted_status_id is None
      and 'https://instance.social/@someone/109876543210' in view.text())

# The auto-attached quote goes out with the post.
view.reset()
posts.clear()
kit.SEARCH_ANSWER = {'statuses': [{'id': '4245', 'visibility': 'public',
                               'quote_approval': {'current_user': 'automatic'}}]}
paste('https://instance.social/@someone/109876543210')
settle_until(2.0, lambda: view.quoted_status_id == '4245')
set_text('my take')
view.on_post_clicked(view.post_button)
pump(400)
check('the auto-attached quote goes out with the post',
      posts and posts[0].get('quoted_status_id') == '4245')

# A link whose search resolves after the composer moved on is dropped,
# not attached to whatever draft is open by then.
view.reset()
kit.SEARCH_ANSWER = {'statuses': [{'id': '4246', 'visibility': 'public'}]}
HOLD_PATHS.add('/api/v2/search')
paste('https://instance.social/@someone/109876543210')
# The URL is gone at insert time; the search is held back.
view.reset()
client.release_requests()
pump(100)
check('a link resolved after a reset is dropped, not attached',
      view.quoted_status_id is None and view.text() == '')
HOLD_PATHS.clear()
view.reset()
kit.SEARCH_ANSWER = None
view._add_toast = _original_capture

# Screenshot: taken through the portal, copied under the user cache as
# pasted images are, and attached.
SHOT_FAILED = _('Could not take a screenshot')


def take_screenshot(*answers):
    portal_answers.extend(answers)
    view.screenshot_button.emit('clicked')
    pump(50)


def cached_files():
    return sorted(os.listdir(composeattach.pasted_dir()))


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
      shot_copy == os.path.join(composeattach.pasted_dir(),
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

# --- the attachment limit and the file chooser's filter come from the
# instance, not from a constant. Mastodon's own Android client hardcodes
# four while parsing statuses.max_media_attachments and never reading it.

check('a client that cannot answer falls back to four',
      view.attachment_limit() == MAX_ATTACHMENTS,
      str(view.attachment_limit()))


class LimitsClient(StubClient):
    """A client that answers what an instance would advertise."""

    def max_media_attachments(self):
        return 6

    def supported_media_types(self):
        return ['image/jpeg', 'video/mp4']


def mime_types(filter_):
    """The MIME patterns a Gtk.FileFilter carries.

    Gtk.FileFilter has no getter for them, but it converts to the
    ``(kind, patterns)`` pair the boxed type is built from, where kind 1
    is a MIME type.
    """
    _kind, patterns = filter_.to_gvariant().unpack()
    return sorted(pattern for _mime_kind, pattern in patterns)


limits_view = BabbleComposeView(LimitsClient())
limits_window = Gtk.Window()
limits_window.set_child(limits_view)
limits_window.present()
capture_toasts(limits_view)
check('the attachment limit is the one the instance advertises',
      limits_view.attachment_limit() == 6, str(limits_view.attachment_limit()))
check('the chooser offers only what the instance takes',
      mime_types(limits_view.media_filter()) == ['image/jpeg', 'video/mp4'],
      str(mime_types(limits_view.media_filter())))
check('the narrowed filter says so in its name',
      limits_view.media_filter().get_name() == _('Media accepted by this server'),
      limits_view.media_filter().get_name())
check('an instance that advertises nothing keeps the three broad types',
      mime_types(view.media_filter()) == ['audio/*', 'image/*', 'video/*'],
      str(mime_types(view.media_filter())))
check('the broad filter keeps its name',
      view.media_filter().get_name() == _('Images, Videos and Audio'),
      view.media_filter().get_name())

# --- an oversized attachment is refused before it is uploaded, let
# alone read. PNG_BYTES is a few bytes, so the limit has to be below it.

limits_client = LimitsClient()
limits_client.media_size_limits = lambda: (2, 3)
refused_view = BabbleComposeView(limits_client)
refused_window = Gtk.Window()
refused_window.set_child(refused_view)
refused_window.present()
capture_toasts(refused_view)
before_uploads = len(uploads)
collect_toasts()
refused_view.add_attachment_files([media_file('toobig.png')])
refused_view.on_post_clicked(refused_view.post_button)
pump(400)
check('an oversized file never reaches the upload',
      len(uploads) == before_uploads,
      '%d upload(s) added' % (len(uploads) - before_uploads))
refusal_toasts = collect_toasts()
check('and the reader is told why, naming the file',
      len(refusal_toasts) == 1 and 'toobig.png' in refusal_toasts[0],
      str(refusal_toasts))
refused_view.reset()
collect_toasts()
del refused_view, refused_window
limits_client.media_size_limits = lambda: (None, None)

# Six attachments now fit, where four used to stop the composer.
limits_view.add_attachment_files(
    [media_file('m%d.png' % n) for n in range(6)])
check('the advertised count is what the composer allows',
      len(limits_view.attachments()) == 6,
      str(len(limits_view.attachments())))
limits_view.add_attachment_files([media_file('m6.png')])
check('and the seventh is refused',
      len(limits_view.attachments()) == 6
      and collect_toasts() == [ngettext(
          'A post can have at most %d attachment',
          'A post can have at most %d attachments', 6) % 6],
      str(collect_toasts()))
limits_view.reset()
collect_toasts()
del limits_view, limits_window

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

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose paste checks passed')
