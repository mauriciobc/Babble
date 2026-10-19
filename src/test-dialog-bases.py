#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dialog bases keep their observable behaviour.

Written before the two bases (lists.ToastDialog and admin.AdminDialogBase)
are merged into pages.ToastDialog, so it passes on both the old and the
new code. It pins what a caller can rely on: a header, a preferences
page, an overlay above the page, a toast that lands on that overlay, and
pages.show_toast routing to it rather than to the window.

Exits 77 (skip) without a display or a compiled bundle."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

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

from src import admin, lists, main as babble_main, pages  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def pump(iterations=30):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(2000)


class _Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.present()
pump()


def exercise(dialog, label):
    """The behaviour both bases promise, for one dialog."""
    dialog.present(window)
    pump()
    check('%s has a header bar' % label,
          isinstance(dialog.header, Adw.HeaderBar))
    check('%s has a preferences page' % label,
          isinstance(dialog.page, Adw.PreferencesPage))
    overlay = dialog.page.get_ancestor(Adw.ToastOverlay)
    check('%s keeps its page under an overlay' % label,
          overlay is not None)

    seen = []
    overlay.add_toast = lambda toast: seen.append(toast)
    dialog.toast('hello')
    check('%s toast() lands on its own overlay' % label,
          len(seen) == 1 and seen[0].get_title() == 'hello', seen)

    seen.clear()
    pages.show_toast(dialog.page, 'routed')
    check('%s show_toast from the page uses the dialog overlay' % label,
          len(seen) == 1 and seen[0].get_title() == 'routed', seen)


list_dialog = lists.ListEditor(_Client())
exercise(list_dialog, 'lists')


class _Ctx:
    client = _Client()


admin_dialog = admin.AddIPBlockDialog(_Ctx())
exercise(admin_dialog, 'admin')
check('the admin base still offers header buttons',
      isinstance(admin_dialog.save, Gtk.Button))

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all dialog base checks passed')
