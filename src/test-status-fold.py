#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A real StatusRow folds: the content-warning toggle hides and shows
the body, and Show More expands a long post through its animation.

test-thread-cw.py only checks where the CW bar sits; nothing else clicks
the toggle or the expand button. Exits 77 (skip) without a display."""

import gc
import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402,F401

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import pages, statusrow, timelinedata  # noqa: E402

failures = []


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


class _Client:
    token = None
    instance_info = None


def status(n, spoiler='', content='<p>Body</p>'):
    return {'id': str(n), 'content': content,
            'spoiler_text': spoiler, 'sensitive': bool(spoiler),
            'account': {'id': str(100 + n), 'display_name': 'Ada'},
            'media_attachments': []}


def pump(until, ms=3000):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while not until() and GLib.get_monotonic_time() < end:
        ctx.iteration(False)
    return until()


def freed(ref):
    for _attempt in range(5):
        gc.collect()
        if ref() is None:
            return True
        pump(lambda: False, 20)
    return ref() is None


settings = pages.settings()
settings.set_boolean('show-spoilers', False)
settings.set_boolean('collapse-long-posts', True)

# 1-2: the content warning toggle.
row = statusrow.StatusRow()
item = timelinedata.StatusItem(status(1, 'Food'))
row.bind(item, _Client())
check('CW bar shows', row.cw_box is not None and row.cw_box.get_visible())
check('toggle offers Show Content',
      row.cw_toggle.get_label() == 'Show Content')
check('body is hidden', not row.guarded_revealer.get_reveal_child())

labels = []
real_update = row.cw_toggle.update_property
row.cw_toggle.update_property = \
    lambda props, values: (labels.extend(values),
                           real_update(props, values))
row.cw_toggle.emit('clicked')
check('toggle reveals the body', row.guarded_revealer.get_reveal_child())
check('toggle offers Hide Content',
      row.cw_toggle.get_label() == 'Hide Content')
check('state is kept on the item', item.ui['cw_open'] is True)
check('accessible label is set',
      labels == ['Hide content warning: Food'])

row.cw_toggle.emit('clicked')
check('second click hides again',
      not row.guarded_revealer.get_reveal_child()
      and row.cw_toggle.get_label() == 'Show Content'
      and item.ui['cw_open'] is False)

# Rebinding the same item lands on the stored state.
row.cw_toggle.emit('clicked')
row.bind(item, _Client())
check('rebind keeps the revealed state',
      row.guarded_revealer.get_reveal_child()
      and row.cw_toggle.get_label() == 'Hide Content')

ref = row.weak_ref()
row = None
check('a CW row is freed', freed(ref))

# 3: Show More on a long post, in a mapped window.
long_html = ''.join('<p>%s</p>' % ('word ' * 60) for _i in range(20))
row = statusrow.StatusRow()
item = timelinedata.StatusItem(status(2, content=long_html))
row.bind(item, _Client())
window = Gtk.Window(default_width=600, default_height=600)
window.set_child(row)
window.present()
pump(lambda: row.content.get_mapped() and row.content.get_height() > 0)
check('expand control appears', row.expand_button.get_visible()
      and row.expand_button.get_label() == 'Show More')
collapsed_height = row.content.get_height()
collapsed_chars = row.content.get_buffer().get_char_count()
row.expand_button.emit('clicked')
check('expanded flag flipped', item.ui['expanded'] is True)
check('label became Show Less',
      row.expand_button.get_label() == 'Show Less')
# The flag, the animation and even the height pass when the body is
# never re-rendered (measured, with _render_body skipped): the text is
# what proves the post expanded.
check('the body text grew',
      row.content.get_buffer().get_char_count() > collapsed_chars)
check('animation ran to its end',
      pump(lambda: row._body_tick is None and row._body_animation is None
           and row.content._height_override is None))
check('height override cleared', row.content._height_override is None)
check('no tick or animation is left',
      row._body_tick is None and row._body_animation is None)
check('the body grew', row.content.get_height() > collapsed_height)

row.expand_button.emit('clicked')
check('collapsing flips the flag back', item.ui['expanded'] is False)
check('collapse settles',
      pump(lambda: row._body_tick is None and row._body_animation is None
           and row.content._height_override is None))

# Unbinding mid-animation stops it.
row.expand_button.emit('clicked')
row._stop_body_animation()
check('stop clears tick, animation and override',
      row._body_tick is None and row._body_animation is None
      and row.content._height_override is None)

# 4: freed.
ref = row.weak_ref()
window.set_child(None)
window.destroy()
row = window = None
pump(lambda: False, 50)
check('a long row is freed', freed(ref))

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
