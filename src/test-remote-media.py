#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Media the user's server does not hold is not fetched unasked.

A loopback server is "the other server": the attachment has no ``url``
and a ``remote_url`` pointing at it, and the test counts the requests it
receives. It checks the three settings (ask, load, never), the dialog
and its Remember box, that a cached ``url`` never asks, that Save As
goes through the same gate, and the Preferences row.
"""

import faulthandler
import importlib.util
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import time

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GstPlay', '1.0')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

bundle = os.path.join(os.environ.get('BABBLE_BUILDDIR') or os.getcwd(),
                      'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import mediaplayback, mediaviewer, pages, preferences  # noqa: E402
from src import remotemedia  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

faulthandler.dump_traceback_later(150, exit=True)
failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


def wait(condition, timeout=10.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


def settle(seconds):
    wait(lambda: False, seconds)


mediaplayback._system_state = lambda name: False

clip = fixture.write_clip()
other = fixture.MediaServer(clip)
mine = fixture.MediaServer(clip)
settings = pages.settings()

window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()


def remote():
    return {'type': 'video', 'url': None, 'description': 'v',
            'remote_url': other.base + '/media.ogg'}


def cached():
    return {'type': 'video', 'url': mine.base + '/media.ogg',
            'remote_url': other.base + '/media.ogg', 'description': 'v'}


def dialog_up():
    return window.get_visible_dialog()


def find_button(widget, label):
    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_button(child, label)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def answer(response, remember=False):
    """Click the dialog's button, as a person would."""
    dialog = dialog_up()
    dialog.get_extra_child().set_active(remember)
    button = find_button(dialog, {'load': '_Load', 'cancel': '_Cancel'}[
        response])
    button.emit('clicked')
    settle(0.5)


def playing(viewer):
    stream = viewer._stream
    return (stream is not None and stream.is_prepared()
            and stream.get_intrinsic_width() > 0)


def stack(viewer):
    return viewer.stack.get_visible_child_name()


# The setting: schema default and nicks.
check('the default is ask', settings.get_string('remote-media') == 'ask')

# ask: a dialog, and nothing is requested until Load.
viewer = mediaviewer.present_media(parent, [remote()])
settle(0.5)
dialog = dialog_up()
check('ask shows the dialog', dialog is not None
      and dialog.get_heading() == 'Load Media From Another Server?')
check('the dialog names the host',
      dialog is not None and '127.0.0.1' in dialog.get_body())
check('no request before the answer', other.requests == [],
      repr(other.requests))
check('the viewer waits on the loading page', stack(viewer) == 'loading')
answer('cancel')
settle(0.5)
check('Cancel shows the error page', stack(viewer) == 'error')
check('Cancel makes no request', other.requests == [], repr(other.requests))
check('Cancel without Remember leaves the setting',
      settings.get_string('remote-media') == 'ask')
check('Retry and Open in Browser are offered',
      viewer.retry_button.get_visible()
      and viewer.error_browser_button.get_visible())

# Retry asks again; Load plays.
viewer._on_retry_clicked(None)
settle(0.3)
check('Retry asks again', dialog_up() is not None)
answer('load')
check('Load plays the media', wait(lambda: playing(viewer), 10),
      stack(viewer))
check('Load fetched from the other server', len(other.requests) > 0)
check('Load without Remember leaves the setting',
      settings.get_string('remote-media') == 'ask')
viewer.close()

# Remember + Load: the setting becomes load and the next open is silent.
other.requests.clear()
viewer = mediaviewer.present_media(parent, [remote()])
settle(0.3)
answer('load', remember=True)
check('Remember + Load sets load',
      settings.get_string('remote-media') == 'load')
check('and plays', wait(lambda: playing(viewer), 10))
viewer.close()
viewer = mediaviewer.present_media(parent, [remote()])
check('load: no dialog and it plays',
      wait(lambda: playing(viewer), 10) and dialog_up() is None)
viewer.close()

# Remember + Cancel: the setting becomes never.
settings.set_string('remote-media', 'ask')
other.requests.clear()
viewer = mediaviewer.present_media(parent, [remote()])
settle(0.3)
answer('cancel', remember=True)
settle(0.3)
check('Remember + Cancel sets never',
      settings.get_string('remote-media') == 'never')
check('and makes no request', other.requests == [])
viewer.close()

# never: no dialog, the error page says where to change it, no request.
viewer = mediaviewer.present_media(parent, [remote()])
settle(0.5)
check('never shows no dialog', dialog_up() is None)
check('never shows the error page', stack(viewer) == 'error')
check('the error page names the setting',
      'Preferences' in viewer.error_page.get_description(),
      viewer.error_page.get_description())
check('never makes no request', other.requests == [])
viewer._on_retry_clicked(None)
settle(0.3)
check('Retry from never still respects the setting',
      dialog_up() is None and stack(viewer) == 'error'
      and other.requests == [])

# Save As under never goes through the gate: no dialog, no request.
check('Save As reports an attachment', viewer.save_as())
settle(0.3)
check('Save As under never makes no request', other.requests == [])
viewer.close()

# A cached url never asks, even under never.
viewer = mediaviewer.present_media(parent, [cached()])
check('a cached copy plays under never',
      wait(lambda: playing(viewer), 10) and dialog_up() is None)
check('and never touches the other server', other.requests == [])
check('it was fetched from the user\'s server', len(mine.requests) > 0)
viewer.close()

# Save As under ask: the dialog; Cancel makes no request.
settings.set_string('remote-media', 'ask')
viewer = mediaviewer.present_media(parent, [remote()])
settle(0.3)
answer('cancel')
viewer.save_as()
settle(0.3)
check('Save As asks', dialog_up() is not None)
answer('cancel')
check('Save As Cancel makes no request', other.requests == [])
viewer.close()

# The gate's own contract.
url, is_remote = remotemedia.media_url(cached())
check('media_url prefers the cached copy',
      url.startswith(mine.base) and not is_remote)
url, is_remote = remotemedia.media_url(remote())
check('media_url falls back to the original', is_remote)
check('media_url with neither',
      remotemedia.media_url({'url': None}) == (None, False))


# The Preferences row reflects and changes the key.
class Stub:
    def __init__(self):
        self.settings = settings
        self.client = None
        self.account = {'instance': 'https://example.social'}


def find_row(widget, title):
    if isinstance(widget, Adw.ComboRow) and widget.get_title() == title:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_row(child, title)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


settings.set_string('remote-media', 'never')
prefs = preferences.PreferencesDialog(Stub())
prefs.present(window)
settle(0.3)
row = find_row(prefs, 'Media From _Other Servers')
check('the Preferences row exists', row is not None)
if row is not None:
    check('the row reflects the key', row.get_selected() == 2)
    row.set_selected(1)
    check('the row changes the key',
          settings.get_string('remote-media') == 'load')
    settings.set_string('remote-media', 'ask')
    check('the key changes the row', row.get_selected() == 0)
prefs.force_close()

print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
