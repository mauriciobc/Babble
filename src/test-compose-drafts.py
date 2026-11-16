#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercises local drafts, the composer contract, thread mode, scheduling
and direct messages with a stub client."""

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
    check, collect_toasts, local_settings, posts, pump, set_text,
    settle_until)
from src import scheduled
from src.composedialog import ComposeDialog
from src.composeoptions import VISIBILITIES, countable_length
from src.drafts import Draft
from gettext import gettext as _
app_window, dialog = kit.new_dialog()

view = kit.view

# Local drafts: a close with content keeps it for the next launch,
# keyed with the related account, so restoring happens there only.


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
dialog.view.on_reply_cancel(dialog.view.reply_banner)
check('canceling a restored draft returns the content in progress',
      dialog.view.text() == 'in progress')

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

# Scheduling a new post: the header's alarm button opens the picker, the
# picked time is shown in a banner, Post becomes Schedule, and the time
# goes out as scheduled_at. A scheduled post is no status to reply to,
# so thread mode does not chain on it.
check('schedule button present and labelled',
      view.schedule_button.get_visible()
      and view.schedule_button.get_tooltip_text() == _('Schedule Post'))
check('no schedule banner for a post sent now',
      not view.schedule_banner.get_revealed())
when = '2099-03-04T05:06:00.000Z'
posts.clear()
set_text('later, please')
view._on_schedule_picked(when)
check('a picked time is shown', view.schedule_banner.get_revealed()
      and '2099' in view.schedule_banner.get_title())
check('post button reads Schedule',
      view.post_button.get_label() == _('_Schedule'))
view.on_schedule_clear(view.schedule_banner)
check('clearing the time hides the banner',
      not view.schedule_banner.get_revealed()
      and view.post_button.get_label() == _('_Post'))
view._on_schedule_picked(when)
view.thread_toggle.set_active(True)
collect_toasts()
view.on_post_clicked(view.post_button)
pump(400)
check('the time is sent as scheduled_at',
      posts and posts[-1].get('scheduled_at') == when, repr(posts))
check('a scheduled post says so', _('Post scheduled') in collect_toasts())
check('a scheduled post does not chain a thread',
      view.in_reply_to_id is None and view.scheduled_at is None
      and not view.schedule_banner.get_revealed())
posts.clear()
set_text('right now')
view.on_post_clicked(view.post_button)
pump(400)
check('the next post is sent now',
      posts and 'scheduled_at' not in posts[-1], repr(posts))
view.reset()
# A server-side draft carries a far date, which is no time to show.
view.start_scheduled({'id': '7', 'scheduled_at': '5099-01-01T00:00:00Z',
                      'params': {'text': 'a draft'}}, draft=True)
check('a draft shows no schedule banner',
      not view.schedule_banner.get_revealed()
      and view.post_button.get_label() == _('_Post'))
view.reset()
# The alarm button opens the picker over the composer, and accepting its
# default time (an hour ahead) sets the composer's.
dialog.present(app_window)
pump(100)
dialog.view.on_schedule_clicked(dialog.view.schedule_button)
settle_until(5.0, lambda: isinstance(app_window.get_visible_dialog(),
                                     scheduled.SchedulePickerDialog))
picker = app_window.get_visible_dialog()
check('the schedule button opens the picker',
      isinstance(picker, scheduled.SchedulePickerDialog))
if isinstance(picker, scheduled.SchedulePickerDialog):
    picker._on_accept()
    pump(100)
check('the picked time reaches the composer',
      dialog.view.is_timed() and dialog.view.schedule_banner.get_revealed())
del picker
dialog.view.reset()
dialog.close()
pump(100)

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

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all compose draft checks passed')
