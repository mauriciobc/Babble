#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""What the migrated dialogs do, independent of how they are built.

The dialog base merge (pages.ToastDialog) moved five dialogs off their
hand-built scaffolds. dialog bases checks the scaffold; this file checks
behaviour a user or caller can see, using only what each dialog exposed
before the migration, so the same file runs against the commit before it
and proves nothing moved:

- collection editor: title and Save/Create label follow the mode, and
  Save is only sensitive with a name;
- notification policy: closing after a change sends the PATCH, closing
  without one sends nothing, and when it was opened from Preferences the
  confirmation lands on the still-open Preferences dialog, not behind it;
- Wrapped: the year and style controls sit at the start of the header,
  Save at the end;
- profile editor: Save follows a real change, and closing with an unsaved
  change asks before discarding instead of closing; an account that
  carries the Mastodon 4.6 profile-tab flags builds, and its media-replies
  switch follows the media switch.

Exits 77 (skip) without a display or a compiled bundle."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')

from gi.repository import Adw, Gio, GLib, Graphene  # noqa: E402

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

from src import (fedicollections, main as babble_main,  # noqa: E402
                 notifications, profileedit, wrapped)
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + str(detail)) if detail else ''))


def pump(iterations=40):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(3000)


class _Client:
    """Records requests; never completes them unless told to."""
    token = None
    instance_info = None
    instance = 'https://example.org'

    def __init__(self):
        self.requests = []

    def request_async(self, method, path, *args, **kwargs):
        self.requests.append((method, path, kwargs))

    def request_finish(self, result):
        return result

    def is_supported(self, *_args, **_kwargs):
        return True


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)
window.present()
pump()


# -- collection editor -------------------------------------------------
new = fedicollections.CollectionEditDialog(_Client())
check('a new collection is titled New Collection',
      new.get_title() == 'New Collection', new.get_title())
check('a new collection offers Create',
      new.save_button.get_label() == '_Create', new.save_button.get_label())
new.name_row.set_text('')
check('Create is insensitive without a name',
      not new.save_button.get_sensitive())
new.name_row.set_text('Reading list')
check('Create is sensitive with a name', new.save_button.get_sensitive())
new.name_row.set_text('   ')
check('a blank name does not count', not new.save_button.get_sensitive())

existing = {'id': '7', 'name': 'Reading list', 'description': '',
            'language': None, 'tag': None, 'discoverable': True,
            'sensitive': False, 'items': []}
edit = fedicollections.CollectionEditDialog(_Client(), existing)
check('an existing collection is titled Edit Collection',
      edit.get_title() == 'Edit Collection', edit.get_title())
check('an existing collection offers Save',
      edit.save_button.get_label() == '_Save', edit.save_button.get_label())


# -- notification policy -----------------------------------------------
def policy_dialog():
    client = _Client()
    dialog = notifications.NotificationPolicyDialog(client, window)
    loaded = {key: 'accept' for key, _t, _s in notifications.POLICY_FIELDS}
    dialog.on_loaded(client, loaded)
    dialog.present(window)
    pump()
    return client, dialog


client, unchanged = policy_dialog()
unchanged.close()
pump()
patches = [r for r in client.requests if r[0] == 'PATCH']
check('closing the policy unchanged sends nothing', not patches, patches)

client, changed = policy_dialog()
first_key = notifications.POLICY_FIELDS[0][0]
changed.filter_rows[first_key].set_selected(1)
changed.close()
pump()
patches = [r for r in client.requests if r[0] == 'PATCH']
check('closing the policy after a change sends one PATCH',
      len(patches) == 1, patches)
check('the PATCH carries only the changed field',
      bool(patches) and patches[0][2].get('json_body') == {first_key: 'filter'},
      patches[0][2].get('json_body') if patches else None)


# The policy dialog opened from Preferences: its parent is the
# Preferences dialog, which is still open when the policy closes. A
# toast on the window would be covered by it, so the confirmation must
# land on the Preferences dialog's own overlay.
from src import preferences  # noqa: E402

prefs = preferences.PreferencesDialog(window)
prefs.present(window)
pump()


def _setting_rows(widget):
    """Switch and combo rows under *widget*, in tree order."""
    found = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, (Adw.SwitchRow, Adw.ComboRow)):
            found.append(child)
        found.extend(_setting_rows(child))
        child = child.get_next_sibling()
    return found


check('the first page is Appearance, no longer a 30-row General',
      prefs.get_visible_page_name() == 'appearance')
_counts = {}
for _name in ('appearance', 'reading', 'behavior'):
    prefs.set_visible_page_name(_name)
    pump()
    _counts[_name] = len(_setting_rows(prefs.get_visible_page()))
check('each split page holds a manageable number of settings',
      all(0 < n <= 14 for n in _counts.values()), _counts)
prefs.set_visible_page_name('appearance')
on_prefs, on_window = [], []
prefs.add_toast = lambda toast: on_prefs.append(toast.get_title())
window_add = window.add_toast
window.add_toast = lambda toast: on_window.append(toast.get_title())
client = _Client()
nested = notifications.NotificationPolicyDialog(client, prefs)
nested.on_loaded(client, {key: 'accept'
                          for key, _t, _s in notifications.POLICY_FIELDS})
nested.present(prefs)
pump()
nested.filter_rows[first_key].set_selected(1)
nested.close()
pump()
patch = [r for r in client.requests if r[0] == 'PATCH']
if patch:
    patch[0][2]['callback'](client, {})
pump()
check('a policy change opened from Preferences toasts on Preferences',
      on_prefs == ['Notification settings updated'] and not on_window,
      'preferences %r, window %r' % (on_prefs, on_window))
window.add_toast = window_add
prefs.force_close()
pump()


# -- Wrapped header ----------------------------------------------------
def header_side(widget, header):
    """'start' or 'end' by where *widget* is laid out in *header*."""
    ok, point = widget.compute_point(header, Graphene.Point().init(0, 0))
    if not ok:
        return None
    return 'start' if point.x < header.get_width() / 2 else 'end'


dialog = wrapped.WrappedDialog(window, _Client(), year=2026)
for widget in (dialog.save_button, dialog.style_button,
               dialog.year_dropdown):
    widget.set_visible(True)
dialog.present(window)
pump()
header = dialog.save_button.get_ancestor(Adw.HeaderBar)
check('Wrapped has a header bar holding its controls', header is not None)
if header is not None:
    check('Save as Image is at the end of the header',
          header_side(dialog.save_button, header) == 'end')
    check('Style is at the start of the header',
          header_side(dialog.style_button, header) == 'start')
    check('the year picker is at the start of the header',
          header_side(dialog.year_dropdown, header) == 'start')
dialog.force_close()
pump()


# -- profile editor ----------------------------------------------------
account = {'id': '1', 'acct': 'ada@example.org', 'username': 'ada',
           'display_name': 'Ada Lovelace', 'note': '', 'fields': [],
           'source': {'note': '', 'fields': [], 'privacy': 'public',
                      'sensitive': False, 'language': 'en'}}
editor = profileedit.ProfileEditDialog(_Client(), account, window)
editor.present(window)
pump()
check('Save is insensitive before any change',
      not editor.save_button.get_sensitive())
check('an unchanged editor can close', editor.get_can_close())

editor.name_row.set_text('Ada King')
pump()
check('Save is sensitive after a change', editor.save_button.get_sensitive())
check('a changed editor refuses to close on its own',
      not editor.get_can_close())

editor.close()
pump()
prompt = window.get_visible_dialog()
check('closing with a change asks before discarding',
      isinstance(prompt, Adw.AlertDialog), type(prompt).__name__)
check('the editor is still open behind the prompt',
      editor in list(window.get_dialogs()) if hasattr(window, 'get_dialogs')
      else editor.get_mapped())

editor.force_close()
pump()

# A Mastodon 4.6 account carries the profile-tab flags; the editor then
# builds the Profile Tabs group and ties the media-replies switch to the
# media switch. That path once raised (GLib.BindingFlags does not exist),
# so Edit Profile did nothing on servers that send the flags.
tabbed = dict(account, show_media=True, show_media_replies=True,
              show_featured=True)
try:
    tabs = profileedit.ProfileEditDialog(_Client(), tabbed, window)
except Exception as error:  # noqa: BLE001 - the failure is the finding
    tabs = None
    check('the editor builds for an account with profile tabs', False,
          repr(error))
else:
    check('the editor builds for an account with profile tabs', True)
if tabs is not None:
    media = tabs.tab_rows.get('show_media')
    replies = tabs.tab_rows.get('show_media_replies')
    check('the profile tab switches are shown',
          media is not None and replies is not None)
    if media is not None and replies is not None:
        media.set_active(False)
        check('media replies is disabled while media is off',
              not replies.get_sensitive())
        media.set_active(True)
        check('media replies is enabled again with media on',
              replies.get_sensitive())

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all dialog behaviour checks passed')
