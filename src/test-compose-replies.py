#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises replies, content-warning inheritance, sensitive edits, empty
sends and the dialog geometry with a stub client."""

import os
import sys
import tempfile
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

from gi.repository import (Adw, Gio, Gtk)  # noqa: E402

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
    check, collect_toasts, edits, posts, pump, set_text, tmp_png)
from src import composedialog
from src.composedialog import ComposeDialog
from src.composeoptions import VISIBILITIES
from gettext import gettext as _
app_window, dialog = kit.new_dialog()

view = kit.view
client = kit.client

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

# Reply to a post that carries a CW: the reply starts behind the same
# CW, like Phanpy does -- the field is shown based solely on whether
# the parent already carries one, not on a bare sensitive flag.
view.reset()
posts.clear()
set_text('a draft worth keeping')
view.start_reply('12346', 'someone@instance.social', 'public',
                 parent={'spoiler_text': 'plot spoilers',
                         'sensitive': True})
pump(50)
check('reply prefill shows the parent CW',
      view.cw_toggle.get_active()
      and view.spoiler_entry.get_text() == 'plot spoilers')
check('reply with CW assigned', view.in_reply_to_id == '12346')
view.on_post_clicked(view.post_button)
pump(400)
form = posts[0]
check('reply to a CW post posts behind the CW',
      form.get('spoiler_text') == 'plot spoilers'
      and form.get('sensitive') is True
      and form.get('in_reply_to_id') == '12346')
view.reset()
view.start_reply('12347', 'someone@instance.social', 'public',
                 parent={'sensitive': True, 'spoiler_text': ''})
pump(50)
check('a sensitive-only parent shows no CW field',
      not view.cw_toggle.get_active() and not view.spoiler_entry.get_text())
view.reset()
view.start_reply('12348', 'someone@instance.social', 'public',
                 parent={'spoiler_text': ''})
pump(50)
check('a parent without a CW shows no CW field',
      not view.cw_toggle.get_active() and not view.spoiler_entry.get_text())
# A draft with a CW: the reply to a CW-free parent must not inherit it.
view.reset()
view.cw_toggle.set_active(True)
view.spoiler_entry.set_text('kept draft warning')
view.start_reply('12349', 'someone@instance.social', 'public',
                 parent={'spoiler_text': ''})
pump(50)
check('a CW-free parent clears the draft CW',
      not view.cw_toggle.get_active() and view.spoiler_entry.get_text() == '')
# ...and a CW-carrying parent replaces it with its own.
view.reset()
view.cw_toggle.set_active(True)
view.spoiler_entry.set_text('kept draft warning')
view.start_reply('12350', 'someone@instance.social', 'public',
                 parent={'spoiler_text': 'reply spoilers'})
pump(50)
check('a parent with a CW wins over the draft CW',
      view.cw_toggle.get_active()
      and view.spoiler_entry.get_text() == 'reply spoilers')
view.reset()

# Editing a post that is flagged sensitive without a CW keeps the flag:
# the PUT must carry it, or the server does not.
edits.clear()
view.start_edit({'id': '777', 'text': 'a photo',
                 'visibility': 'public', 'language': 'en',
                 'sensitive': True, 'spoiler_text': '',
                 'media_attachments': []})
pump(50)
check('edit loads the post', view.editing_id == '777')
check('a sensitive post without a CW shows no CW field on edit',
      not view.cw_toggle.get_active() and not view.spoiler_entry.get_text())
view.on_post_clicked(view.post_button)
pump(400)
check('editing a sensitive post keeps the flag',
      bool(edits) and edits[-1][0] == '777'
      and edits[-1][1].get('sensitive') is True
      and 'spoiler_text' not in edits[-1][1])
view.reset()
view.start_edit({'id': '778', 'text': 'plain post',
                 'visibility': 'public', 'language': 'en',
                 'spoiler_text': '',
                 'media_attachments': []})
pump(50)
view.on_post_clicked(view.post_button)
pump(400)
check('editing a plain post does not invent sensitive',
      edits[-1][0] == '778' and 'sensitive' not in edits[-1][1])
view.reset()

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
app_window = Adw.ApplicationWindow(default_width=1000, default_height=900)
app_window.present()
dialog = ComposeDialog(client)
dialog.present(app_window)
pump(200)
check('dialog title new post', dialog.get_title() == _('New Post'))
check('dialog focus widget is text view',
      dialog.get_focus() is dialog.view.text_view)
# The dialog follows the composer's natural height: no blank below
# the text area, and it grows when the toolbar reveals the poll.
natural = dialog.view.measure(Gtk.Orientation.VERTICAL,
                             dialog.view.get_width())[1]
# The width asked for is COMPOSE_WIDTH whatever the toolbar's labels
# say; how much of it is granted depends on the window, which Xvfb may
# not give its default size.
sizer = dialog.view.get_parent()
check('dialog keeps its width',
      sizer.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
      == composedialog.COMPOSE_WIDTH
      and dialog.view.get_width() <= composedialog.COMPOSE_WIDTH,
      '%d' % dialog.view.get_width())
del sizer
check('dialog height fits its content',
      abs(dialog.view.get_height() - natural) <= 1,
      '%d vs natural %d' % (dialog.view.get_height(), natural))
plain_height = dialog.view.get_height()
dialog.view.poll_toggle.set_active(True)
pump(600)
check('dialog grows with the poll',
      dialog.view.get_height() > plain_height,
      '%d vs %d' % (dialog.view.get_height(), plain_height))
dialog.view.poll_toggle.set_active(False)
pump(600)
check('dialog shrinks back without the poll',
      dialog.view.get_height() == plain_height,
      '%d vs %d' % (dialog.view.get_height(), plain_height))
# A narrow window still fits it: only the natural width is pinned.
app_window.set_default_size(360, 900)
pump(400)
check('dialog fits a narrow window',
      dialog.view.get_width() <= 360,
      '%d' % dialog.view.get_width())
app_window.set_default_size(1000, 900)
pump(400)
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

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose reply checks passed')
