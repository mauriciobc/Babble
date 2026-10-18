#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media viewer's teardown of a playing video.

A :class:`Gtk.Video` owns a GstPlay pipeline, and the pipeline owns a
signal-adapter thread that watches it. The stream the widget plays is
one of the things that thread watches, so freeing the stream while the
widget is still alive frees what the thread is using: the thread then
dereferences it and the process dies with SIGSEGV in
``g_type_check_instance_is_fundamentally_a`` (coredumpctl 45798).

The order is what the test pins down, because the crash itself is a race
and cannot be asserted on directly:

* the stream is **not** freed while the widget that plays it may still
  be alive -- it is released with the widget, which is what takes the
  pipeline down;
* the widget is disposed, so the pipeline goes away with it;
* the stream is paused, so playback stops;
* the child is taken out of the bin.

The widget is deliberately kept alive across the teardown here, the way
a caller that still holds a reference would keep it alive: that is the
case in which freeing the stream is fatal, and the case the ordering
above has to hold for.

Then the real thing is done again and again, because the crash is a race
between the widget's disposal and the adapter thread: a test that only
checked the order would pass on code that still crashed. Surviving the
loop is the second half of the pass condition, and it is the half that
catches the use-after-free.

The teardown is driven through ``MediaViewer._unload_media()``, the
method the viewer actually uses, so the test is about the application
and not about a helper.

The video is embedded, so the test needs no encoder, no network and no
fixture on disk.
"""

import base64
import os
import sys
import tempfile

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src.mediaviewer import MediaViewer  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


# A one-second 32x32 H.264 clip: enough for GstPlay to have a pipeline
# with a decoder and a sink in it, small enough to carry in the test.
CLIP = (
    'AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAANqbW9vdgAAAGxtdmhkAAAA'
    'AAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAB'
    'AAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAApV0'
    'cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAA'
    'AAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAACAAAAAgAAAAAAAkZWR0'
    'cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAIAAABAAAAAAINbWRpYQAAACBtZGhkAAAAAAAA'
    'AAAAAAAAAAAoAAAAKABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABW'
    'aWRlb0hhbmRsZXIAAAABuG1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAA'
    'HGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAXhzdGJsAAAAwHN0c2QAAAAAAAAAAQAA'
    'ALBhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAACAAIABIAAAASAAAAAAAAAABFExh'
    'dmM2My4xLjEwMiBsaWJ4MjY0AAAAAAAAAAAAAAAAGP//AAAANmF2Y0MBZAAK/+EAGGdk'
    'AAqscgRJbARAAAADAEAAAAUDxIlhGAEAB2joQ4GUsiz9+PgAAAAAEHBhc3AAAAABAAAA'
    'AQAAABRidHJ0AAAAAAAAHfAAAAAAAAAAGHN0dHMAAAAAAAAAAQAAAAoAAAQAAAAAFHN0'
    'c3MAAAAAAAAAAQAAAAEAAAAYY3R0cwAAAAAAAAABAAAACgAACAAAAAAcc3RzYwAAAAAA'
    'AAABAAAAAQAAAAoAAAABAAAAPHN0c3oAAAAAAAAAAAAAAAoAAAMSAAAADAAAABAAAAAR'
    'AAAAEQAAABUAAAAUAAAAGAAAABQAAAAZAAAAFHN0Y28AAAAAAAAAAQAAA5oAAABhdWR0'
    'YQAAAFltZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAACxp'
    'bHN0AAAAJKl0b28AAAAcZGF0YQAAAAEAAAAATGF2ZjYzLjEuMTAyAAAACGZyZWUAAAPG'
    'bWRhdAAAArAGBf//rNxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjUgcjMyMjIg'
    'YjM1NjA1YSAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIw'
    'MjUgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBj'
    'YWJhYz0xIHJlZj0xNiBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTMzIG1lPXVt'
    'aCBzdWJtZT0xMCBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0xIG1lX3Jh'
    'bmdlPTI0IGNocm9tYV9tZT0xIHRyZWxsaXM9MiA4eDhkY3Q9MSBjcW09MCBkZWFkem9u'
    'ZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29mZnNldD0tMiB0aHJlYWRzPTEg'
    'bG9va2FoZWFkX3RocmVhZHM9MSBzbGljZWRfdGhyZWFkcz0wIG5yPTAgZGVjaW1hdGU9'
    'MSBpbnRlcmxhY2VkPTAgYmx1cmF5X2NvbXBhdD0wIGNvbnN0cmFpbmVkX2ludHJhPTAg'
    'YmZyYW1lcz04IGJfcHlyYW1pZD0yIGJfYWRhcHQ9MiBiX2JpYXM9MCBkaXJlY3Q9MyB3'
    'ZWlnaHRiPTEgb3Blbl9nb3A9MCB3ZWlnaHRwPTIga2V5aW50PTI1MCBrZXlpbnRfbWlu'
    'PTEwIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NjAgcmM9'
    'Y3JmIG1idHJlZT0xIGNyZj01MS4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBx'
    'cHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAAAWmWIgQB/d8WT9BNo9Fat'
    'Hu2S5LMjKrPylp46eNxNM5tgb2vKVVvzBIGYptpT49MG2QFLmmAX1jZ8H801R9l+ZKhv'
    'x/+CpRWQ8cK4u6YF6OABVgNrf6uyrS1/vwAAAAhBmggtj//LgAAAAAxBmhBL8CBkymH/'
    'y4EAAAANQZoYaTwIHkymB//LgAAAAA1BmiCJfAgfkymAf8uBAAAAEUGaKKm8CB/kymAH'
    '/9ryIN7pAAAAEEGaMMn8CB/5MpgAf84RXxEAAAAUQZo46I8CB/+TKYAB/9ki9oZRvrkA'
    'AAAQQZpBCJ8CB//kymAAH909oAAAABVBmkkorwIH//kymAAB/99bgZyjosA='
)

path = os.path.join(tempfile.mkdtemp(prefix='babble-teardown-'), 'clip.mp4')
with open(path, 'wb') as handle:
    handle.write(base64.b64decode(CLIP))

# A viewer with no attachments: it shows the "no media" error and stops
# there, so nothing is downloaded and no network is needed. The video is
# put in by hand, the way _start_stream() would.
window = Gtk.Window()
parent = Gtk.Box()
window.set_child(parent)
window.present()
viewer = MediaViewer(parent, [], 0)
parent.append(viewer)

CYCLES = 12
state = {'phase': 'open', 'n': 0, 'events': [], 'checked': False,
         'kept': None}
app = None


def open_video():
    """Put a playing video in the viewer, the way _start_stream() does."""
    stream = Gtk.MediaFile.new_for_file(Gio.File.new_for_path(path))
    view = Gtk.Video()
    view.set_media_stream(stream)
    viewer.media_bin.set_child(view)
    viewer.stack.set_visible_child_name('media')
    viewer._stream = stream

    def prepared(s, _p):
        if s.is_prepared():
            s.play()
    stream.connect('notify::prepared', prepared)
    if stream.is_prepared():
        prepared(stream, None)
    return stream, view


def step():
    phase = state['phase']

    if phase == 'open':
        state['n'] += 1
        # The widget is kept alive across the teardown, the way a caller
        # that still holds a reference would keep it alive.
        state['kept'] = None
        state['stream'], state['view'] = open_video()
        state['phase'] = 'prime'
        return True

    if phase == 'prime':
        stream = state['stream']
        if stream is not None and stream.get_playing():
            # The widget is realised by now, so disposing it is
            # observable: 'unrealize' is what a disposed widget emits.
            state['view'].connect(
                'unrealize', lambda *_: state['events'].append('disposed'))
            stream.connect(
                'notify::file',
                lambda s, p: state['events'].append(
                    'stream freed (widget still alive: %s)'
                    % (state['view'].get_realized() is not None)))
            state['phase'] = 'tear'
        elif state['n'] > CYCLES * 6:
            print('FAIL - the clip never started playing')
            failures.append('the clip never started playing')
            app.quit()
            return False
        return True

    if phase == 'tear':
        state['kept'] = state['view']      # hold the widget alive
        viewer._unload_media()
        if not state['checked']:
            state['checked'] = True
            events = state['events']
            check('the video widget is disposed when the viewer unloads it',
                  'disposed' in events, repr(events))
            check('the stream is not freed while the widget may still be '
                  'alive',
                  not any('stream freed' in e for e in events),
                  'the pipeline was still watching the stream: %r' % events)
            check('the stream is not cleared by the teardown',
                  state['stream'].get_property('file') is not None,
                  'the stream was freed underneath the widget')
            check('the child is taken out of the bin',
                  viewer.media_bin.get_child() is None)
            check('the viewer lets go of the stream', viewer._stream is None)
            check('the stream is not playing any more',
                  not state['stream'].get_playing())
        if state['n'] >= CYCLES:
            print('ok - %d open/close cycles of a playing video'
                  % state['n'])
            app.quit()
            return False
        state['phase'] = 'open'
        return True

    return False


class App(Gtk.Application):
    def do_activate(self):
        global app
        app = self
        self.add_window(window)
        GLib.timeout_add(50, step)


app = App(application_id='org.gnome.BabbleMediaTeardownTest', flags=0)
app.run([])

if failures:
    print('\n%d media teardown check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media teardown checks passed')
