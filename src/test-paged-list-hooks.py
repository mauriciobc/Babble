#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""``pages.PagedList`` when one of its hooks raises (DEF-11).

The same fake client as ``paged list`` (``testkit.pagedlist``): a
``finish``, ``extract``, ``page_filter``, ``make_row`` or
``make_placeholder`` hook, or an override, that raises must not raise out
of the callback, half-apply a page or roll the cursor back, and a hook
that fails on every page must not flood the reader with requests or
toasts. Exits 77 (skip) without a display."""

import logging
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

Adw.init()

from src import feedback, pages  # noqa: E402

# check() is the kit's, so a failed check lands in kit.failures, the list
# the exit gate reads.
from testkit import pagedlist as kit  # noqa: E402
from testkit.pagedlist import (FakeClient, build,  # noqa: E402
                               check, children, in_step, item, make_row,
                               page_name, texts)

reported = []
real_report = feedback.report_error
feedback.report_error = lambda widget, heading, error: reported.append(
    (heading, error.message))

# A hook that raises (DEF-11)

toasts = []
real_toast = feedback.show_toast
toast_actions = []


def fake_toast(widget, text, undo=None, undo_label=None, **_k):
    toasts.append(text)
    toast_actions.append((undo, undo_label))


feedback.show_toast = fake_toast
# The traceback goes to the log; keep this run's output readable.
logging.getLogger('src.pages').setLevel(logging.CRITICAL)


class Hooks:
    """Hooks that raise while ``armed`` and behave otherwise."""

    def __init__(self, where):
        self.where = where
        self.armed = False
        self.seen = 0

    def finish(self, client_, result):
        if self.where == 'finish' and self.armed:
            raise KeyError('not a GLib.Error')
        return client_.request_finish_paged(result)

    def extract(self, data):
        if self.where == 'extract' and self.armed:
            raise TypeError('bad payload')
        return data

    def page_filter(self, items):
        if self.where == 'page_filter' and self.armed:
            raise ValueError('bad filter')
        return items

    def make_row(self, data):
        self.seen += 1
        if self.where == 'make_row' and self.armed and data['id'] == '7':
            raise RuntimeError('row 7')
        return make_row(data)


def hooked(where):
    hooks = Hooks(where)
    client, lst = build(finish=hooks.finish, extract=hooks.extract,
                        page_filter=hooks.page_filter)
    lst.make_row = hooks.make_row
    return hooks, client, lst


for where in ('finish', 'extract', 'page_filter', 'make_row'):
    # First page
    hooks, client, lst = hooked(where)
    hooks.armed = True
    page = [item(n) for n in range(1, 21)]
    try:
        client.answer(page, {'max_id': '20'})
        raised = False
    except Exception:
        raised = True
    check('%s raising on the first page does not raise out of the '
          'callback' % where, not raised)
    check('%s raising on the first page shows the error page, no rows, '
          'nothing half-applied' % where,
          page_name(lst) == 'error' and lst.rows == [] and children(lst) == []
          and not lst._loading)
    check('%s raising on the first page leaves the cursor unset and a '
          'plain description' % where,
          lst._next is None and lst.error_page.get_description() == '')
    hooks.armed = False
    lst._on_retry(None)
    client.answer([item(1)])
    check('%s: Retry after a first-page failure works' % where,
          page_name(lst) == 'list' and texts(lst) == ['row 1'])

    # Load more
    hooks, client, lst = hooked(where)
    client.answer([item(1), item(2)], {'max_id': '2'})
    lst.load_more()
    hooks.armed = True
    before = len(toasts)
    try:
        client.answer([item(n) for n in range(3, 23)], {'max_id': '22'})
        raised = False
    except Exception:
        raised = True
    check('%s raising on load-more does not raise out of the callback'
          % where, not raised)
    check('%s raising on load-more keeps the list, adds no row of that '
          'page' % where,
          page_name(lst) == 'list' and texts(lst) == ['row 1', 'row 2']
          and in_step(lst))
    check('%s raising on load-more releases the spinner and the flag and '
          'says so once' % where,
          not lst.more_spinner.get_visible() and not lst._loading
          and toasts[before:] == ['Could not load more'])
    check('%s raising on load-more keeps the cursor' % where,
          lst._next == {'max_id': '2'})
    hooks.armed = False
    lst.load_more()
    check('%s: the retry asks for the same page' % where,
          len(client.sent) == 3
          and client.sent[2].params == {'max_id': '2'})
    client.answer([item(3)], None)
    check('%s: and lands' % where,
          texts(lst) == ['row 1', 'row 2', 'row 3'] and in_step(lst))

# A page_filter that empties the page and then a later page that raises
hooks, client, lst = hooked('make_row')
client.answer([item(1)], {'max_id': '1'})
lst.load_more()
hooks.armed = True
client.answer([item(n) for n in range(5, 9)], None)
check('make_row failing mid-page appends none of that page (rows 5, 6 '
      'built, 7 raised)',
      texts(lst) == ['row 1'] and in_step(lst) and lst._next == {
          'max_id': '1'})

# A cancelled or superseded answer shows nothing, even with hooks armed
hooks, client, lst = hooked('finish')
hooks.armed = True
first = client.sent[0]
lst.refresh()
before = len(toasts)
client.answer([item(1)], request=first)
check('a superseded answer is dropped before any hook runs',
      page_name(lst) == 'loading' and hooks.seen == 0
      and toasts[before:] == [])
hooks, client, lst = hooked('finish')
hooks.armed = True
lst._scope.close()
before = len(toasts)
client.answer([item(1)])
check('an answer to a closed list is dropped, no error page, no toast',
      page_name(lst) == 'loading' and toasts[before:] == [])

# Something raising after the rows are in must not roll the cursor back


class AfterSuper(pages.PagedList):
    """An override that raises after super(), as the announcements and
    conversations pages' emit_unread_changed could."""

    boom = False

    def append_items(self, items):
        super().append_items(items)
        if self.boom:
            raise RuntimeError('after super')


client = FakeClient()
lst = AfterSuper(client, '/things', make_row, empty_title='Nothing')
lst._maybe_fill = lambda: GLib.SOURCE_REMOVE
lst.boom = True
try:
    client.answer([item(1), item(2)], {'max_id': '2'})
    raised = False
except Exception:
    raised = True
check('an override raising after its rows were added does not raise out '
      'of the callback', not raised)
check('the rows stay, once, and the list is shown',
      texts(lst) == ['row 1', 'row 2'] and in_step(lst)
      and page_name(lst) == 'list')
check('the cursor advanced and no failure is shown',
      lst._next == {'max_id': '2'} and not lst._load_failed)
lst.boom = False
lst.load_more()
check('the next load asks for the NEXT page',
      len(client.sent) == 2 and client.sent[1].params == {'max_id': '2'})
client.answer([item(3)], None)
check('and no row is added twice',
      texts(lst) == ['row 1', 'row 2', 'row 3'] and in_step(lst))

calls = []


def bad_placeholder():
    calls.append(1)
    raise RuntimeError('placeholder')


client, lst = build(make_placeholder=bad_placeholder)
try:
    client.answer([], None)
    raised = False
except Exception:
    raised = True
check('a raising make_placeholder on an empty page does not raise out of '
      'the callback, and shows the error page',
      not raised and calls and page_name(lst) == 'error' and lst.rows == []
      and lst.error_page.get_description() == '')

# A hook that fails on every page must not flood

real_near_bottom = pages.near_bottom
pages.near_bottom = lambda adjustment, preload=pages.PRELOAD: True
hooks, client, lst = hooked('extract')
client.answer([item(1), item(2)], {'max_id': '2'})
lst.load_more()
hooks.armed = True
before = len(toasts)
client.answer([item(3)], {'max_id': '3'})
adjustment = lst.scrolled.get_vadjustment()
for _scroll in range(3):
    lst._on_scroll(adjustment)
check('scrolling after a raising load-more sends no further request',
      len(client.sent) == 2 and lst._load_failed)
check('and the reader sees one toast, with a Try Again action',
      toasts[before:] == ['Could not load more']
      and toast_actions[-1][0] is not None
      and toast_actions[-1][1] == '_Try Again')
hooks.armed = False
toast_actions[-1][0]()
check('the toast action asks for the same page again',
      len(client.sent) == 3 and client.sent[2].params == {'max_id': '2'}
      and not lst._load_failed)
client.answer([item(3)], None)
check('and a good page lands', texts(lst) == ['row 1', 'row 2', 'row 3'])

hooks, client, lst = hooked('extract')
client.answer([item(1)], {'max_id': '1'})
lst.load_more()
hooks.armed = True
client.answer([item(2)], {'max_id': '2'})
check('self-check: the stop is set', lst._load_failed)
hooks.armed = False
lst.refresh()
check('refresh clears the stop', not lst._load_failed)
pages.near_bottom = real_near_bottom

feedback.show_toast = real_toast

feedback.report_error = real_report

if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all paged list hook checks passed')
