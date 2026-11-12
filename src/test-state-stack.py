#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""pages.StateStack: loading and error pages, the generation token, and a
real page (FiltersList) dropping a reply that a newer refresh outran.

Exits 77 (skip) without a display."""

import gc
import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GObject, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if os.path.exists(bundle):
    Gio.Resource.load(bundle)._register()

Adw.init()

from src import pages  # noqa: E402
from src.fedicollections import CollectionPage  # noqa: E402
from src.filters import FiltersList  # noqa: E402
from src.statusviews import EditHistoryView  # noqa: E402
from src.thread import ThreadPage  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


retries = []


class Owner(GObject.Object):
    def on_retry(self, _button):
        retries.append(1)


owner = Owner()
stack = pages.StateStack('Loading', 'Could not', owner.on_retry)
stack.add_named(Gtk.Label(label='x'), 'list')
stack.show('list')
first = stack.begin()
check('begin shows loading', stack.get_visible_child_name() == 'loading')
check('the first token is current', stack.is_current(first))
second = stack.begin()
check('a stale token is not current', not stack.is_current(first))
check('the newest token is current', stack.is_current(second))
stack.show_error(GLib.Error.new_literal(
    Gio.io_error_quark(), 'a & b', Gio.IOErrorEnum.FAILED))
check('show_error shows the error page',
      stack.get_visible_child_name() == 'error')
description = stack.error_page.get_description()
check('the description is markup-escaped',
      'a &amp; b' in description, repr(description))
stack.show('list')
check('show names a content page', stack.get_visible_child_name() == 'list')


# A real page: an older reply must not overwrite a newer refresh

class Client:
    token = 't'
    instance_info = None

    def __init__(self):
        self.callbacks = []
        self.cancellables = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.callbacks.append(callback)
        self.cancellables.append(cancellable)

    def deliver(self, index, value):
        """What the real transport does: a cancelled request still calls
        back, and finishing it raises CANCELLED, whatever its answer."""
        if self.cancellables[index] is not None \
                and self.cancellables[index].is_cancelled():
            value = GLib.Error.new_literal(
                Gio.io_error_quark(), 'Operation was cancelled',
                Gio.IOErrorEnum.CANCELLED)
        self.callbacks[index](self, value, None)

    def request_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result


def filter_item(identifier):
    return {'id': identifier, 'title': 'f%s' % identifier, 'context': [],
            'keywords': []}


client = Client()
page = FiltersList(client, Adw.NavigationView())
page.refresh()
old, new = client.callbacks
check('two requests are in flight', len(client.callbacks) == 2)
new(client, [filter_item('2')], None)
check('the newer reply fills the list', [i['id'] for i in page.items] == ['2'])
old(client, [filter_item('1'), filter_item('3')], None)
check('the older reply does not overwrite it',
      [i['id'] for i in page.items] == ['2'])
check('and the list stays shown', page.stack.get_visible_child_name() == 'list')

page.refresh()
stale = client.callbacks[-2]
stale(client, GLib.Error.new_literal(
    Gio.io_error_quark(), 'late failure', Gio.IOErrorEnum.FAILED), None)
check('a stale failure does not replace loading with the error page',
      page.stack.get_visible_child_name() == 'loading')
client.callbacks[-1](client, GLib.Error.new_literal(
    Gio.io_error_quark(), 'real failure', Gio.IOErrorEnum.FAILED), None)
check('the current failure shows the error page',
      page.stack.get_visible_child_name() == 'error')

# Cancellation: the ticket carries the cancellable of its load

stack = pages.StateStack('Loading', 'Could not', owner.on_retry)
stack.add_named(Gtk.Label(label='x'), 'list')
older = stack.begin()
newer = stack.begin()
check('begin hands out a ticket with a live cancellable',
      not newer.cancellable.is_cancelled())
check('a newer begin cancels the load it replaced, not its own',
      older.cancellable.is_cancelled() and not newer.cancellable.is_cancelled())
check('a cancelled ticket is not current', not stack.is_current(older))
refs = [stack.weak_ref()]
del stack
for _attempt in range(5):
    gc.collect()
check('a stack that is freed is gone', refs[0]() is None)
check('and its pending load is cancelled, and no longer wanted',
      newer.cancellable.is_cancelled() and not newer.wanted())

stack = pages.StateStack('Loading', 'Could not', owner.on_retry)
stack.add_named(Gtk.Label(label='x'), 'list')
ticket = stack.begin()
hidden = Gtk.Stack()
hidden.add_named(stack, 'stack')
hidden.add_named(Gtk.Label(label='other'), 'other')
hidden.set_visible_child_name('other')
hidden.set_visible_child_name('stack')
stack.set_visible(False)
stack.set_visible(True)
check('a stack hidden and shown again keeps its load',
      stack.is_current(ticket) and not ticket.cancellable.is_cancelled())
del hidden, stack


def page_of(factory, rerun, label):
    """A real StateStack user driven with a fake client: the superseded
    load is cancelled, a cancelled answer is silent, and a page dropped
    mid-load is cancelled and freed."""
    client = Client()
    page = factory(client)
    first = client.cancellables[0]
    check('%s: the load passes a live cancellable' % label,
          first is not None and not first.is_cancelled())
    rerun(page)
    check('%s: the superseded load is cancelled, the newer one is not'
          % label, first.is_cancelled()
          and not client.cancellables[1].is_cancelled())
    client.deliver(0, ['stale'])
    check('%s: a cancelled answer leaves the loading page' % label,
          page.stack.get_visible_child_name() == 'loading')
    client.deliver(0, GLib.Error.new_literal(
        Gio.io_error_quark(), 'late', Gio.IOErrorEnum.FAILED))
    check('%s: a cancelled failure does not show the error page' % label,
          page.stack.get_visible_child_name() == 'loading'
          and page.error_page.get_description() in (None, ''))
    pending = client.cancellables[1]
    ref = page.weak_ref()
    del page
    for _attempt in range(5):
        gc.collect()
    check('%s: a page dropped mid-load is freed' % label, ref() is None)
    check('%s: and its request is cancelled' % label,
          pending.is_cancelled())
    toasts = len(shown)
    requests = len(client.callbacks)
    for value in (['late'], GLib.Error.new_literal(
            Gio.io_error_quark(), 'late', Gio.IOErrorEnum.FAILED)):
        client.callbacks[1](client, value, None)
    check('%s: the late answers of a freed page (real, failed) change '
          'nothing and report nothing' % label,
          len(shown) == toasts and len(client.callbacks) == requests)

    # A CANCELLED error the page did not cause is shown, with Retry.
    other = Client()
    page = factory(other)
    other.callbacks[0](other, GLib.Error.new_literal(
        Gio.io_error_quark(), 'Operation was cancelled',
        Gio.IOErrorEnum.CANCELLED), None)
    check('%s: a foreign CANCELLED shows the error page' % label,
          page.stack.get_visible_child_name() == 'error')
    check('%s: ... with a Try Again button' % label,
          isinstance(page.error_page.get_child(), Gtk.Button))
    del page


def run_user(factory, rerun, label):
    try:
        page_of(factory, rerun, label)
    except Exception as error:       # one user failing must not hide the rest
        check('%s: ran to completion' % label, False, repr(error))


shown = []
pages.show_toast = lambda *args, **kwargs: shown.append(args)

run_user(lambda c: FiltersList(c, Adw.NavigationView()),
         lambda p: p.refresh(), 'filters')
run_user(lambda c: EditHistoryView(c, {'id': '1'}),
         lambda p: p.refresh(), 'edit history')
run_user(lambda c: CollectionPage(c, 'c1'),
         lambda p: p.refresh(), 'collection')
run_user(lambda c: ThreadPage(c, StatusItem({'id': '1', 'content': ''})),
         lambda p: p.load(), 'thread')

client = Client()
page = FiltersList(client, Adw.NavigationView())
hold = Gtk.Stack()
hold.add_named(page, 'page')
hold.add_named(Gtk.Label(label='other'), 'other')
hold.set_visible_child_name('other')
page.set_visible(False)
page.set_visible(True)
hold.set_visible_child_name('page')
check('filters: hidden and shown again mid-load, the request is kept',
      not client.cancellables[0].is_cancelled())
client.deliver(0, [filter_item('7')])
check('filters: and the answer still lands',
      [i['id'] for i in page.items] == ['7']
      and page.stack.get_visible_child_name() == 'list')

client = Client()
page = FiltersList(client, Adw.NavigationView())
client.deliver(0, GLib.Error.new_literal(
    Gio.io_error_quark(), 'real failure', Gio.IOErrorEnum.FAILED))
check('a failure that is not a cancel still shows the error page',
      page.stack.get_visible_child_name() == 'error')

if failures:
    print('\n%d FAILED' % len(failures))
    sys.exit(1)
print('all state stack checks passed')
