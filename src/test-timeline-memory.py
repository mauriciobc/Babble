#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for the timeline's list plumbing: a new post must
not make the list rebuild every row, dropped rows and pages must be
freed, and a body that has never been allocated must measure the height
of its text, not of one character per line. Exits 77 (skip) without a
display."""

import gc
import os
import sys
import weakref

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import timeline  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def status(number, text='post', thread=None):
    data = {'id': '%05d' % number, 'content': '<p>%s</p>' % text,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}
    if thread:
        data['thread'] = thread
    return data


def freed(ref):
    gc.collect()
    return ref() is None


Adw.init()

# A streamed post is one inserted run; every other run object survives,
# so the list view keeps the widgets bound to them.
model = timeline.TimelineModel()
model.append_old([status(n) for n in range(30, 0, -1)])
runs = timeline.RunModel(model)
kept = [runs.get_item(i) for i in range(runs.get_n_items())]
changes = []
runs.connect('items-changed', lambda _m, *change: changes.append(change))
model.insert_new([status(31)])
check('one new post is one inserted run', changes == [(0, 0, 1)])
check('existing runs are kept',
      all(runs.get_item(i + 1) is run for i, run in enumerate(kept)))
changes.clear()
model.remove_status('00015')
check('a deletion removes one run', changes == [(16, 1, 0)])
changes.clear()
check('an edit changes the post in place',
      model.replace_status({'id': '00020', 'content': '<p>edited</p>'})
      and changes == [])
model.set_boosts([status(5)])
changes.clear()
head = model.get_item(0)
model.set_boosts([status(6)])
check('the boosts carousel is updated in place',
      changes == [] and model.get_item(0) is head
      and head.boosts[0]['id'] == '00006')

# Same-thread posts by one author still group into one run.
threaded = timeline.TimelineModel()
threaded.append_old([status(3), status(2, thread='t'), status(1, thread='t')])
for item in (threaded.get_item(1), threaded.get_item(2)):
    item.status['account']['acct'] = 'same'
grouped = timeline.RunModel(threaded)
check('same-thread posts form one run',
      [len(grouped.get_item(i).items)
       for i in range(grouped.get_n_items())] == [1, 2])


class _Client:
    token = None
    instance_info = None


class _Source:
    paginated = True

    def fetch(self, _params, _callback):
        pass


# Rows, their text views and the page are freed once dropped: signal
# handlers into a widget's own methods must not keep it alive.
row = timeline.StatusRow()
row.bind(timeline.StatusItem(status(1, 'see <a href="https://e.org">'
                                       'e.org</a>')), _Client())
row_ref, body_ref = weakref.ref(row), weakref.ref(row.content)
del row
check('a dropped status row is freed', freed(row_ref))
check('its text view is freed', freed(body_ref))

page = timeline.TimelinePage(None, source=_Source())
page_ref, model_ref = weakref.ref(page), weakref.ref(page.model)
del page
check('a dropped timeline page is freed', freed(page_ref))
check('its model is freed', freed(model_ref))

# Before its first allocation a wrapped body measures its text at the
# width it is offered; Gtk.TextView alone would wrap it at one pixel.
window = Gtk.Window()
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
window.set_child(box)
body = timeline.StatusBody()
body.set_segments(timeline.parse_content('<p>%s</p>' % ('word ' * 40)))
box.append(body)
natural = box.measure(Gtk.Orientation.VERTICAL, 600).natural
check('an unallocated body is not one character per line',
      0 < natural < 200)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all timeline memory checks passed')
