#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""What a failed keyring call means, and what the window does about it:
a missing Secret Service is named once per session, a locked or otherwise
broken keyring keeps its own message, and nothing here removes an
account. Exits 77 (skip) without a display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Secret', '1')

from gi.repository import Adw, Gio, GLib, Secret  # noqa: E402

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
Adw.init()
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mastodon  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def dbus_error(code):
    return GLib.Error.new_literal(Gio.DBusError.quark(), 'gone', int(code))


# The error libsecret raises with no Secret Service, observed on a session
# bus that cannot activate one: g-dbus-error-quark, code 2.
check('no keyring service is missing',
      mastodon.keyring_problem(dbus_error(Gio.DBusError.SERVICE_UNKNOWN))
      == 'missing')
check('a keyring service that quit is missing',
      mastodon.keyring_problem(dbus_error(Gio.DBusError.NAME_HAS_NO_OWNER))
      == 'missing')
check('a locked keyring is not missing',
      mastodon.keyring_problem(GLib.Error.new_literal(
          Secret.error_get_quark(), 'locked', int(Secret.Error.IS_LOCKED)))
      == 'locked')
check('a timeout is neither',
      mastodon.keyring_problem(dbus_error(Gio.DBusError.TIMED_OUT))
      == 'other')
check('the wrapper for a synchronous failure is neither',
      mastodon.keyring_problem(GLib.Error(
          'boom', 'mastodon-keyring-error-quark', 0)) == 'other')
check('no error is no problem', mastodon.keyring_problem(None) is None)

Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
window = BabbleWindow(application=Gio.Application.get_default())
window.main_stack.set_visible_child_name('shell')

first = window.present_no_keyring()
check('the missing keyring is explained', first is not None
      and first.get_heading() == 'No Keyring Found')
check('and only once per session', window.present_no_keyring() is None)

# The startup read: a missing keyring shows the dialog and keeps the
# account; a locked one keeps its toast.
account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
stored = []
window.remove_account = lambda a: stored.append(a)
toasts = []
window.show_toast = toasts.append
real_load = mastodon.MastodonClient.load_secret_token


def failing(code_error):
    def load(self, account_id, callback, cancellable=None):
        callback(None, code_error)
    return load


window._no_keyring_shown = False
mastodon.MastodonClient.load_secret_token = failing(
    dbus_error(Gio.DBusError.SERVICE_UNKNOWN))
window._activate_account(account, login_only=True)
check('a missing keyring at startup shows the dialog',
      window._no_keyring_shown)
check('and does not forget the account', stored == [])
check('and goes to the login page',
      window.main_stack.get_visible_child_name() == 'login')

window._no_keyring_shown = False
toasts.clear()
mastodon.MastodonClient.load_secret_token = failing(GLib.Error.new_literal(
    Secret.error_get_quark(), 'locked', int(Secret.Error.IS_LOCKED)))
window._activate_account(account, login_only=True)
check('a locked keyring keeps its toast',
      len(toasts) == 1 and 'keyring' in toasts[0]
      and not window._no_keyring_shown)
mastodon.MastodonClient.load_secret_token = real_load

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all keyring error checks passed')
