#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""pages.StateStack: loading and error pages, the generation token, and a
real page (FiltersList) dropping a reply that a newer refresh outran.

Exits 77 (skip) without a display."""

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
from src.filters import FiltersList  # noqa: E402

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

    def __init__(self):
        self.callbacks = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.callbacks.append(callback)

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

if failures:
    print('\n%d FAILED' % len(failures))
    sys.exit(1)
print('all state stack checks passed')
