#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo before a destructive request: deleting a post, and the boost
that an unboost takes away.

Pins what pages.deferred_action promises (one of commit and restore, once;
flush on close; no window means commit now), that a taken row goes back to
its place, and that the menu's delete sends nothing until its toast goes.

Exits 77 (skip) without a display or a compiled bundle."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

Adw.init()

from src import pages  # noqa: E402
from src.entities import lift  # noqa: E402
from src.statusactions import StatusActions  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.timelinedata import StatusItem, TimelineModel  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


class FakeToasts:
    """Stands in for pages.show_toast; holds the callbacks the real toast
    would have, so the test can press Undo or let it time out."""

    def __init__(self, shown=True):
        self.shown = shown
        self.toasts = []

    def __call__(self, widget, text, undo=None, undo_label=None,
                 dismissed=None):
        # A widget with no root finds no window, as in GTK.
        if not self.shown or getattr(widget, 'unrooted', False):
            return None
        self.toasts.append({'text': text, 'undo': undo,
                            'dismissed': dismissed})
        return object()

    def press_undo(self):
        toast = self.toasts[-1]
        toast['undo']()
        toast['dismissed']()  # libadwaita dismisses after the button

    def time_out(self):
        self.toasts[-1]['dismissed']()


real_toast = pages.show_toast
toasts = FakeToasts()
pages.show_toast = toasts

# The helper

log = []
pages.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
check('nothing happens while the toast is up', log == [])
toasts.time_out()
check('timing out commits', log == ['commit'])
toasts.time_out()
check('a second dismissal does nothing', log == ['commit'])

log.clear()
pages.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
toasts.press_undo()
check('Undo restores and does not commit', log == ['restore'])

log.clear()
pages.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
pages.flush_deferred()
toasts.time_out()
check('flushing commits once', log == ['commit'])
check('nothing is left waiting', pages._pending_actions == [])

toasts.shown = False
log.clear()
pages.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
check('without a window it commits at once', log == ['commit'])
toasts.shown = True

# The model gives a row back where it was


def status(identifier):
    return {'id': identifier, 'content': 'p%s' % identifier,
            'account': {'acct': 'a', 'id': '1'},
            'created_at': '2026-01-01T00:00:00.000Z'}


def ids(model):
    return [model.get_item(i).status_id for i in range(model.get_n_items())]


model = TimelineModel()
model.append_old([status('5'), status('4'), status('3'), status('2')])
taken = model.take_status('4')
check('take removes the post', ids(model) == ['5', '3', '2'])
model.put_back(taken)
check('put_back restores the position', ids(model) == ['5', '4', '3', '2'])
model.put_back(taken)
check('put_back twice does not duplicate', ids(model) == ['5', '4', '3', '2'])
check('taking an unknown id takes nothing', model.take_status('99') == [])

# The re-boost behind an unboost's Undo


class Rebooster:
    """The slice of StatusActions that _reboost uses."""

    def __init__(self, client, item):
        self.client, self.item, self.refreshed = client, item, 0

    def _refresh(self):
        self.refreshed += 1


class Answering:
    def __init__(self):
        self.requests = []

    def request_async(self, method, path, form=None, callback=None):
        self.requests.append((method, path))
        callback(self, lift(dict(status('7'), reblogged=True)), None)

    def request_finish(self, result):
        return result


reblogged = StatusItem(status('7'))
answering = Answering()
actions = Rebooster(answering, reblogged)
StatusActions._reboost(actions, reblogged)
check('Undo for an unboost posts the reblog again',
      answering.requests == [('POST', '/statuses/7/reblog')])
check('the row is refreshed while it shows the post',
      actions.refreshed == 1)
actions.item = None
StatusActions._reboost(actions, reblogged)
check('a recycled row is left alone', actions.refreshed == 1)


# The menu's delete


class Client:
    def __init__(self):
        self.requests = []

    def request_async(self, method, path, form=None, callback=None):
        self.requests.append((method, path))

    def request_finish(self, result):
        raise result


class Page:
    def __init__(self, model):
        self.model = model


class Menu:
    """The slice of StatusMenuButton that _delete_with_undo uses."""

    def __init__(self, page, client):
        self.page, self.client = page, client
        self.errors = []

    def get_root(self):
        return None

    def _timeline_page(self):
        return self.page



def delete(menu, item):
    StatusMenuButton._delete_with_undo(menu, item)


model = TimelineModel()
model.append_old([status('5'), status('4'), status('3')])
client = Client()
menu = Menu(Page(model), client)
item = model.get_item(1)
delete(menu, item)
check('delete takes the row out of view', ids(model) == ['5', '3'])
check('delete sends nothing yet', client.requests == [])
toasts.press_undo()
check('Undo puts the row back', ids(model) == ['5', '4', '3'])
check('Undo sends nothing', client.requests == [])

delete(menu, item)
toasts.time_out()
check('timing out sends one DELETE',
      client.requests == [('DELETE', '/statuses/4')], str(client.requests))
check('and the row stays gone', ids(model) == ['5', '3'])

model = TimelineModel()
model.append_old([status('5'), status('4')])
client = Client()
menu = Menu(Page(model), client)
delete(menu, model.get_item(0))
pages.flush_deferred()
check('closing the window sends the DELETE',
      client.requests == [('DELETE', '/statuses/5')], str(client.requests))

# Removing the row unparents the menu button, and an unparented widget finds
# no window to show the toast on: the delete used to go straight through.
model = TimelineModel()
model.append_old([status('5'), status('4')])
client = Client()
menu = Menu(Page(model), client)
menu.unrooted = False
_take = model.take_status


def _take_and_unroot(status_id):
    menu.unrooted = True
    return _take(status_id)


model.take_status = _take_and_unroot
_window = object()
_window_of = pages.window_of
pages.window_of = lambda widget: _window
delete(menu, model.get_item(0))
check('the toast is shown although the row is gone', len(toasts.toasts) > 0
      and client.requests == [], str(client.requests))
toasts.press_undo()
check('and Undo still works', ids(model) == ['5', '4'])
pages.window_of = _window_of

# A post shown outside a timeline list (a thread, a search result) is hidden
# by its host and shown again on Undo; the DELETE still waits.
events = []
client = Client()
menu = Menu(None, client)
menu._notify = events.append
delete(menu, StatusItem(status('9')))
check('a post outside a list is hidden at once', events == ['deleted'])
check('and nothing is sent yet', client.requests == [])
toasts.press_undo()
check('Undo shows it again', events == ['deleted', 'restored'])
check('and sends nothing', client.requests == [])

events.clear()
delete(menu, StatusItem(status('9')))
toasts.time_out()
check('timing out sends the DELETE',
      client.requests == [('DELETE', '/statuses/9')], str(client.requests))
check('and the post stays hidden', events == ['deleted'])

pages.show_toast = real_toast

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
