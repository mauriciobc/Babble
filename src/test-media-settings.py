#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The two media preferences: Autoplay Animated Media and Load Media
Automatically.

Checks the schema defaults, the autoplay check (``may_autoplay_loop``)
with the key and with the system overriding it, the defer decision for
every (mode, metered) pair, the real viewer for both (a deferred page
with the right explanation, a GIFV held and released live when the key
changes) and the two Preferences rows. The sysstate monitor is a fake of
plain properties: the real one asks Gio.NetworkMonitor, which can hang
on a CI runner.
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

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

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

from src import mediaengine, mediaplayback, mediaviewer  # noqa: E402
from src import pages, preferences, sysstate  # noqa: E402

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


class FakeMonitor(GObject.Object):
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


monitor = FakeMonitor()
sysstate._monitor = monitor
settings = pages.settings()

# Schema defaults.
for key in ('autoplay-animated-media', 'media-autoload',
            'inline-animated-media'):
    settings.reset(key)
check('Play Animated GIFs in Timelines defaults to off',
      settings.get_boolean('inline-animated-media') is False)
check('Autoplay Animated Media defaults to on',
      settings.get_boolean('autoplay-animated-media') is True)
check('Load Media Automatically defaults to unmetered',
      settings.get_string('media-autoload') == 'unmetered')
nicks = []
for nick in ('always', 'unmetered', 'never'):
    settings.set_string('media-autoload', nick)
    nicks.append(settings.get_enum('media-autoload'))
settings.reset('media-autoload')
check('the enum is always 0, unmetered 1, never 2', nicks == [0, 1, 2],
      repr(nicks))

# The autoplay check.
gtk_settings = Gtk.Settings.get_default()
gtk_settings.set_property('gtk-enable-animations', True)
gtk_settings.set_property('gtk-interface-reduced-motion',
                          Gtk.ReducedMotion.NO_PREFERENCE)
check('autoplay is allowed with the defaults',
      mediaplayback.may_autoplay_loop())
settings.set_boolean('autoplay-animated-media', False)
check('autoplay is off when the key is off',
      not mediaplayback.may_autoplay_loop())
settings.set_boolean('autoplay-animated-media', True)
monitor.set_property('power-saver', True)
check('power saving overrides the key when it is on',
      not mediaplayback.may_autoplay_loop())
monitor.set_property('power-saver', False)
gtk_settings.set_property('gtk-interface-reduced-motion',
                          Gtk.ReducedMotion.REDUCE)
check('reduced motion overrides the key when it is on',
      not mediaplayback.may_autoplay_loop())
gtk_settings.set_property('gtk-interface-reduced-motion',
                          Gtk.ReducedMotion.NO_PREFERENCE)

# The defer decision.
for mode, metered, expected in (
        ('always', True, False), ('always', False, False),
        ('unmetered', True, True), ('unmetered', False, False),
        ('never', True, True), ('never', False, True)):
    check('%s, %s: %s' % (mode, 'metered' if metered else 'unmetered',
                          'defers' if expected else 'loads'),
          mediaplayback.should_defer_stream(mode, metered) is expected)

# The real viewer.
clip = fixture.write_clip()
server = fixture.MediaServer(clip)
window = Adw.ApplicationWindow(content=Gtk.Overlay(), default_width=900,
                               default_height=700)
window.present()
parent = window.get_content()


def video():
    return {'type': 'video', 'url': server.base + '/media.ogg',
            'description': 'v'}


def gifv():
    return {'type': 'gifv', 'url': server.base + '/media.ogg',
            'description': 'g'}


def stack(viewer):
    return viewer.stack.get_visible_child_name()


def playing(viewer):
    return viewer._stream is not None and viewer._stream.get_playing()


def close(viewer):
    viewer.close()
    wait(lambda: not mediaengine._releasing, 5)


settings.set_string('media-autoload', 'never')
monitor.set_property('metered', False)
viewer = mediaviewer.present_media(parent, [video()])
settle(0.5)
check('never: an unmetered video waits on the preview page',
      stack(viewer) == 'deferred', stack(viewer))
tip = viewer.load_button.get_tooltip_text()
check('never: the explanation does not claim a metered connection',
      tip and 'metered' not in tip.lower(), repr(tip))
close(viewer)

settings.set_string('media-autoload', 'unmetered')
monitor.set_property('metered', True)
viewer = mediaviewer.present_media(parent, [video()])
settle(0.5)
tip = viewer.load_button.get_tooltip_text()
check('unmetered, metered: it waits and says the connection is metered',
      stack(viewer) == 'deferred' and tip and 'metered' in tip.lower(),
      repr(tip))
close(viewer)

settings.set_string('media-autoload', 'always')
viewer = mediaviewer.present_media(parent, [video()])
check('always, metered: the video is not deferred and plays',
      wait(lambda: playing(viewer), 10) and stack(viewer) != 'deferred',
      stack(viewer))
close(viewer)
monitor.set_property('metered', False)

# Live re-evaluation: the gate acts on a GIFV that is shown.
settings.set_string('media-autoload', 'unmetered')
settings.set_boolean('autoplay-animated-media', True)
viewer = mediaviewer.present_media(parent, [gifv()])
check('a GIFV autoplays with the key on',
      wait(lambda: playing(viewer), 10))
settings.set_boolean('autoplay-animated-media', False)
check('turning the key off pauses the playing GIFV',
      not playing(viewer))
settings.set_boolean('autoplay-animated-media', True)
check('turning it back on resumes the GIFV the gate paused',
      playing(viewer))
close(viewer)

settings.set_boolean('autoplay-animated-media', False)
viewer = mediaviewer.present_media(parent, [gifv()])
wait(lambda: viewer._stream is not None and viewer._stream.is_prepared(), 8)
settle(0.5)
check('with the key off a GIFV is prepared but waits for a click',
      viewer._stream.is_prepared() and not playing(viewer))
viewer._on_gifv_clicked(None, 1, 0, 0, viewer._stream)
check('a click starts it', playing(viewer))
close(viewer)
settings.set_boolean('autoplay-animated-media', True)


# The Preferences rows.
class Stub:
    def __init__(self):
        self.settings = settings
        self.client = None
        self.account = {'instance': 'https://example.social'}


def find_row(widget, cls, title):
    if isinstance(widget, cls) and widget.get_title() == title:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_row(child, cls, title)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


prefs = preferences.PreferencesDialog(Stub())
prefs.present(window)
settle(0.3)
switch = find_row(prefs, Adw.SwitchRow, 'Autoplay Animated Me_dia')
check('the Autoplay Animated Media row exists', switch is not None)
if switch is not None:
    switch.set_active(False)
    check('the switch writes the key',
          settings.get_boolean('autoplay-animated-media') is False)
    settings.set_boolean('autoplay-animated-media', True)
    check('the key moves the switch', switch.get_active())
def group_title(widget):
    while widget is not None and not isinstance(widget, Adw.PreferencesGroup):
        widget = widget.get_parent()
    return widget.get_title() if widget is not None else None


check('Autoplay Animated Media is in the Media group',
      switch is not None and group_title(switch) == 'Media')
inline = find_row(prefs, Adw.SwitchRow, 'Play A_nimated GIFs in Timelines')
check('the Play Animated GIFs in Timelines row exists, off, in the Media '
      'group, with the cost in its subtitle',
      inline is not None and not inline.get_active()
      and group_title(inline) == 'Media'
      and 'memory' in inline.get_subtitle())
if inline is not None:
    inline.set_active(True)
    check('its switch writes the key',
          settings.get_boolean('inline-animated-media') is True)
    settings.set_boolean('inline-animated-media', False)
    check('the key moves its switch', not inline.get_active())
combo = find_row(prefs, Adw.ComboRow, 'Load Media Automaticall_y')
check('Load Media Automatically is in the Media group',
      combo is not None and group_title(combo) == 'Media')
check('the Load Media Automatically row exists', combo is not None)
if combo is not None:
    check('the row reflects the default', combo.get_selected() == 1)
    combo.set_selected(2)
    check('the row writes the key',
          settings.get_string('media-autoload') == 'never')
    settings.set_string('media-autoload', 'always')
    check('the key moves the row', combo.get_selected() == 0)
prefs.force_close()

print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
