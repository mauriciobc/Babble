#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The dialog bases keep their observable behaviour.

Written before the two bases (lists.ToastDialog and admin.AdminDialogBase)
were merged into pages.ToastDialog, and run against both sides of that
merge; the dialogs migrated afterwards are added here. Behaviour that
does not depend on the base lives in dialog behaviour.

It pins what a caller can rely on: a header, content directly under the
dialog's own toast overlay, toasts that land there with the libadwaita
default timeout, and pages.show_toast routing to that overlay rather
than to the window.

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

from src import (admin, fedicollections, lists,  # noqa: E402
                 main as babble_main, notifications, pages, profile,
                 profileedit, wrapped)
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

    def request_async(self, *_args, **_kwargs):
        pass

    def is_supported(self, *_args, **_kwargs):
        return True


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
    check('%s keeps its content directly under its own overlay' % label,
          isinstance(dialog.toasts, Adw.ToastOverlay)
          and dialog.toasts.get_child() is dialog.page)
    overlay = dialog.toasts

    seen = []
    overlay.add_toast = lambda toast: seen.append(toast)
    dialog.toast('hello')
    check('%s toast() lands on its own overlay' % label,
          len(seen) == 1 and seen[0].get_title() == 'hello', seen)
    check('%s toasts last the libadwaita default, 5 s' % label,
          len(seen) == 1 and seen[0].get_timeout() == 5,
          seen[0].get_timeout() if seen else None)

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

collection_dialog = fedicollections.CollectionEditDialog(_Client())
exercise(collection_dialog, 'collection')
check('the collection dialog keeps its Save button',
      isinstance(collection_dialog.save_button, Gtk.Button))

policy_dialog = notifications.NotificationPolicyDialog(_Client())
exercise(policy_dialog, 'policy')
check('the policy dialog keeps its filter rows',
      bool(policy_dialog.filter_rows))

account = {'id': '1', 'acct': 'ada@example.org',
           'display_name': 'Ada Lovelace', 'source': {}}
profile_dialog = profileedit.ProfileEditDialog(_Client(), account, window)
exercise(profile_dialog, 'profile')
check('the profile editor hides its title buttons',
      not profile_dialog.header.get_show_start_title_buttons()
      and not profile_dialog.header.get_show_end_title_buttons())
check('the profile editor keeps its Save button',
      isinstance(profile_dialog.save_button, Gtk.Button))

note_dialog = profile.PrivateNoteDialog('@ada@example.org', 'note',
                                       lambda _text: None)
exercise(note_dialog, 'private note')
check('the private note hides its title buttons',
      not note_dialog.header.get_show_start_title_buttons())

wrapped_dialog = wrapped.WrappedDialog(window, _Client(), year=2026)
check('wrapped keeps its stack under the overlay',
      wrapped_dialog.stack.get_ancestor(Adw.ToastOverlay)
      is wrapped_dialog.toasts)
check('wrapped keeps its header buttons',
      isinstance(wrapped_dialog.save_button, Gtk.Button))

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all dialog base checks passed')
