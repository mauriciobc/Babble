#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A ThreadPost with a content warning builds its CW bar without a GTK
critical.

ThreadPost moves everything in StatusRow.main into its own column, so
the CW bar and revealer must be inserted beside reply_box and guarded
in their real parent, not in main. Deterministic: `thread profile
render` only hit this when the live thread it sampled had a warning.
Runs under G_DEBUG=fatal-criticals, so the bug is an abort.

Exits 77 (skip) without a display."""

import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402,F401

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import thread, timelinedata  # noqa: E402

failures = []


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


class _Client:
    token = None
    instance_info = None


def status(n, spoiler=''):
    return {'id': str(n), 'content': '<p>Body</p>',
            'spoiler_text': spoiler, 'sensitive': bool(spoiler),
            'account': {'id': str(100 + n), 'display_name': 'Ada'},
            'media_attachments': []}


for focused in (False, True):
    label = 'focused' if focused else 'unfocused'
    row = thread._thread_row(
        timelinedata.StatusItem(status(1, 'Food')), _Client(),
        focused=focused)
    check('%s: CW bar is in the post column' % label,
          row.cw_box is not None
          and row.cw_box.get_parent() is row.column)
    check('%s: body sits in the revealer, in the column' % label,
          row.guarded.get_parent() is row.guarded_revealer
          and row.guarded_revealer.get_parent() is row.column)
    check('%s: CW bar precedes the revealer' % label,
          row.cw_box.get_next_sibling() is row.guarded_revealer)

plain = thread._thread_row(timelinedata.StatusItem(status(2)), _Client())
check('a post without a warning keeps its body in the column',
      plain.guarded.get_parent() is plain.column
      and plain.cw_box is None)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
