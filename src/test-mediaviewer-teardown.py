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
fixture on disk. It is Theora in Ogg, which only GStreamer's base
plugins decode -- the ones GTK itself requires -- so the test does not
depend on a decoder the CI image has no reason to install.
"""

import base64
import faulthandler
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


# A one-second 32x32 Theora clip in Ogg: enough for GstPlay to have a
# pipeline with a decoder and a sink in it, small enough to carry in the
# test. Theora and the Ogg demuxer are in GStreamer's base plugins,
# which GTK itself needs -- an H.264 clip also needs a decoder that GTK
# does not, and without one the stream never plays (see `prime`).
CLIP = (
    'T2dnUwACAAAAAAAAAAAHQFoTAAAAAFRGzRQBKoB0aGVvcmEDAgEAAgACAAAgAAAgAAAAAAAZAAAA'
    'AQAAAQAAAQAAAADA2E9nZ1MAAAAAAAAAAAAAB0BaEwEAAACd1By5DD7/////////////P4F0aGVv'
    'cmEvAAAAWGlwaC5PcmcgbGlidGhlb3JhIDEuMi4wIDIwMjUwMzI5IChQdGFsYXJidm9ybSkAAAAA'
    'gnRoZW9yYb7NKPe5zWsYtalJShBznOYxjFKUpCEIMYxiEIQhCEAAAAAAAAAAAAAR9OFkLlVJtHYS'
    '9XC0mGuVoqlAlkih0EfTmbjWZDAXSyVSkSyURiIQB4ORwNBkMRaLBWKBMJRGIRAHg6HAyGAsFQmE'
    'giDwaDAU+5kX1pVVVBQS0tGRkFBPDw3NzIyLS0tKCgoIyMjHh4eHhkZGRkUFBQUFA8PDw8PDw8KC'
    'goKCgoKCgUFBQUFBQUAhALChAYKDM9DAwOExo6PDcODRAYKDlFOA4RFh0zV1A+EhYlOkRtZ00YIz'
    'dAUWhxXDFATldneXhlSFxfYnBkZ2MREhgvY2NjYxIVGkJjY2NjGBo4Y2NjY2MvQmNjY2NjY2NjY2'
    'NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjEBAQFBgcICgQEBQYHCAoMBAUGBwgKDBAFBgcIC'
    'gwQEAYHCAoMEBAQBwgKDBAQEBgICgwQEBAYIAoMEBAQGCAgD4vy30A0zE75CjeDqib25ZiLdRxvg'
    'P3KJIl7g2Ml9aqkaZldi7EIE5GD2jqZL5zDa1YbGVSlefbuH5J3ENMyuxdiCF8ijw1WoNjJePzl3'
    'MSuj2jyThPuqpCDFc1T7qqRXY0EZymlB7R513JDgZWsxt84fxXheCXnb/hOO0w9o86kiYwF1azMp'
    'qDXfDJaQo/tAvQp42OZdkaB3KD2jzrlNtrvYK4KVRgRfEn/FotY5B9wiHc2v9xC0jYZiytAbpyVy'
    'KHu3nUl6qkqEeZwnStZsFxvFCK7A0kDk8UPduu//smN5bBSD2eBadZ3HFisxh8BlRyNGomlk2v/q'
    'Utzsjiw+D5OCaWTaPBbwGfV0CysCLTHkyv/qUtyE1o9edgNqIv+B7Hy+hNzCdn0tsFa4E0smumKA'
    '8yd3qqRAEmv17l6nE0u1BudZsIlqG4WVhp/ijyO/OFVJSgLozrJiUa/CysXk5NLtI87cOYhDuPc2'
    'D/S2opQEVzhi9ayiyseSL+27kJtqPsxGYdCf/TJbDUUoEcIsrN159qTzP21CIlzYdC0ye6cFzHxI'
    '57qKqRZWL5Q+98BtRP/r0zwjEUgy4YpubRcd6bwwBfWs1EB1Dw2+XVCnFa3POTtEekqiaSP80yMJ'
    '8wvnV2BD+dO4tsVz6AU1NLQ4cowmle0Sea5GG+YNXMN7ScrsY3AR7fuDuIEz2iWTzqpTmZPoFrDc'
    'B52bBRN7LJE1hvbzleKzMHLqPTJcf8fFVK1hAH17GAnybjXtF05hFla5gPCVsUTeyR6FrN/wqpFL'
    '4jed/k7jNBYK7m7ANSY2o9lkiadFrJzCY31CqkZcnWcJ/jcHCMJihtr7Lp3YrtINy+cJPIsPhBUp'
    'I3eFofsUnlaxxOOXUSG5uGYNKG2vs0uV/wpcTjFm0RIyQml21uObzv3wfWrKwUQ4f7jLqCoVUh20'
    'BNLtJrRzcM4b4B9afnqOLqwrPMJ25ljwKREoBtNLrJpCv6OD3LxaYd8j6xWYs4/yBPc3qCyupTuE'
    '9xGCZtrLpJlC4L/fB0KM8O1vxyYxfwCqk5EmTDHyA3cHsVo6+Qu5xGUA21m0lzvXfrKxVSQrXs+5'
    'A4fRjxd+srOi+TtyMMQaNtZs5VGUQqpbgM2jbXLGW76EgcPC9gom/EVitJ2dLXs6PkQVKXA9GI21'
    'ZNM6ML+7DdCrWp7iNF0PJZBN/9yDh5runvMTkibUQwM6xZSqyLmh07CaWT2P8+wtD4h84Y3mW5+d'
    'o9eZxZWuNHSIE0u0ntGBTUZwWi1fAfOP284GigkJW02vvmFwI4u+cqyypSxZuoQ+ZYUqj91DALmP'
    'DesstLwJUzbWTSPPk8dontE4fXUqjlkGiVM21k9jf3g7RKM/DrDhbYVwNzGGcQPn/KmoWoZx2e0D'
    'MaTNtZZMnV9ZYu8ODHBFHuNyf9dygqKQMhtqmaZy0McO3UVZYh+PEZ7Je4tG6FaL1AyaZm2uLCaf'
    'tArsKahF8dScj8l9ujecCqkdX8JzDHkfWhDR3+gGaINtUiaXSPL0srLvNxTuecLfgIsoboDleEgm'
    'lba6Zx4P3bGgsrJ3vVQpfDEkG2qVpNnlRC3cOvQ/MI0V/iuwT8rV3aCob1KYA7XRcFvT70Ql5IZm'
    '2qVpNkxw/8OU6iNZWN6lKHgI+hIvYMWQ21TNM5f/YBynXUFv1bxYi7HlKpwSF6VgNtTKZpnjcntE'
    'KLmAevlH6ysjd9bjypSLfpHPpAhWVkYLyZk0zYG2uN3/UQ7TpaONxGchdMXmDLE0zba0W+yKoCkd'
    'd2K0pP/8QuIm/gpHsJpR1ATsOD/L4b3GLPmssCIkJW02sffl62oVUlxl34EYJpW2ukmaKi/4P3Xr'
    'hhig3LbFZDzOKqQjJCU0mba50XuWOBD58DtlF/aPeuNxPdYrgAUY3/p+YFNQtXeAkGZpM21lywiR'
    '92FaF70cfbjc6GJkmNM22sqjBJej5U1EB59WWE/b24t4P4oRuJ5PnMSqkgQnv9pZWO+o3AUBtqya'
    'Z4sJl8t3g3H6dcDTNtqYUMsb5pI0Byey/QlVIieW1rDvrgQJHEpC5Ov+9yqksrIhOfDbVQwvXGWL'
    'TP2Z5bGDr3ilU+tBQBtqlaTSZx48kN9yB+7YMohP5Yr3FNSBw6hHiflXYC+O75aoMCQStJjbXPLG'
    'Ju9d/imod/OrYB6RxcrKzzchfl6RgxG2spM0zyuJ6OAsrIuoVNQRvlxeGUzTOUba44SOVtEB5u9H'
    '8/fARuUj6f1VSN/ICEo7iOLFYXJV40zbamEzLH+3oN4vQWv/u+PCysEq4ZYtMzbVMwvno5SdECOF'
    'VSUozd31EQEjiwrckoXTML/xtqZYtM/lC2fw9hqKqUBGLKxxv2SuRMonNtX/wyxaZl1rC/r0ieSQ'
    'iB5apqCG5z+TmBIJmmZtrLlj3YV7hRGiv+cOvQdfpyQG2plM0zlY4X3VKYB45+FEVu5Y49VnccIp'
    'qIfS3nwgI1CQlDTNtqYTMsb7tw/74sVk/zqIIwDLFpmbapmF90v6f7lZWpqEPkBuiSKLXrh12PTk'
    'oWn8wZYtMzbVcwvn73QVFLxxYRq3SX5CikG8ZJDEQDgdBjgzyLZ19yZ8a6P9uVHfpZW8lVUjeMkh'
    'iIBwOgxwZ5Fs6+5M+NdH+3Kjv0sreSqqRvGSQxEA4HQY4M8i2dfcmfGuj/blR36WVvJVVIqpOE/U'
    'AIl4JmmZtrLlIxwh8WVi3l96LuyOj/cFscNSl65f0CyuA83C5IYMRlM0zNtZb5+Dv5F0B/Pz5SqW'
    'Vg+oi9bwoDgkL0o0zbakzLHC7tGt6Cys5Wvp/CNwk7b+kCE6P0oYTMsV5pm21uoiU1FJ5ED6ejtx'
    'z6FdkcA3Fp+MsWmZMwvNtbpepIahCierK6VSAbyPAPky+6daNdH+xjgzyl5duFHUZAhRkgSi61he'
    'TKJzvp/9YrBtq54MsWme/vkSqkqpQEYsrHG/ZK5Eyic21f/DLFpmXWsL+vSJ5JCAT2dnUwAASwAA'
    'AAAAAAAHQFoTAgAAALQy9LIO//92zSorPCooKSs9Piswy/x9vuZ0gAneccVwjuJiBluBM9Zs14Rm'
    'aV7xLce7gDK0yXhef0DQHBtHJFkC8T3jy28TXHqfLhVhQLyveHLiZcnIzESn28H2gCo1UDOF8jIM'
    'hQAqAFQGqA03jezZnHVBlHli1AYoDFAZjMZ+/qofVD59b0KgPP4j4+PjB4NUSqiVQJKBI0T9R1dR'
    '6qifr6+rq6urq6jRy3Vp6uow1AN19fV1dXV1dRwSdXUoFt6QkdXV1dXV1f6oD/s/67n1YDaOErz/'
    '06T3zvyXTjuAJsyYBqKAz606fDwwuF+lQ83JfGXOQ7DsP8/8vl/w7ff9Zh8OdB0YPB+XLBomctbW'
    'ysukaRdu4Zfpx20Xbt1qaogxvV7PrVq/fzn6ZYu1ES5csWLlyxZvVUblpLF0SJcuFQqEgkWLWWUs'
    'EtSAVlixcuWLXAwZb2jHKy83LSDCliMvu8ziyzXUU4y+ZnxDY6M4idEFzB0g68PmEEzX6ep7nDZ7'
    'TNMb2/BBPp6Mb2z98Yzn/Div7bKKNrb3oVlP7nznN97sAc5KX/5UoqViMqWVLIEgSpRUoqUVKooC'
    'pShElSoFypZUsqUVKKlFSipRUrCpRUrypRUoqUVKKlFSrhlz3ZMYBdS+uPdaGmkXjm2XIRGTvAL+'
    '8c1l29FL7g+yhPHQ4EC0UQgyPO0vGf0SV/MlL6jl3k5zUU73NruHOSldgRnRz83Nz88UTOGsRznS'
    'SOe+1pIxjGzIOlqguqmigqWqBQvjON7Xet8Iv69a1ucinObsnfVHy7xu4N5BbadTryvm6KWWdva8'
    'qznsxiEjayD+3tUUyfeVLYn9SOTzvuxXKGfki0Jj1sIAcMnvSdJs27VwfKEGnqyQFhaAjxC2DQaD'
    'AYCAQCHh4eAYDAC/hFx3tTiGfX5hffQNUUtu3tetbm7bSd7gPIm9/5F4jt/2+KAY3Wt0ktwSCIRC'
    'IREAEgkGQyh80lRJGj76SFkaPReK6qP5CPlfigXhr5QIj4WSl6hRRKiCCBFJ+AEG6S8zoSErRtVZ'
    '8+dSUqvWATpVWkFZm3+3b+UiJZLy//KrDz+P6qYb2R1fIPxW5iTrLhndR6peeejPm/KlbR6tGqB8'
    '/mJNS6rsU2dYcHDJ7oj4HKAAAAG/2mo3Ur9/J0fGQUZgbPXmTXjxdjtYftZ3vN/Wu2GtMHDJ7oj4'
    'HKABwA+/DomH/SKXV2jfQTWznJb+PMsZ6CMyV76xt5Z2ui8drFtwye6I9AfgQEEEUARi8vu3kxNz'
    'Ey1Bm9gbKN0qgumOtPya+fKr1W1bUVZGeOLOaJVYY9BVmHatqhjiV2Bwye6I+BygAeAANLfgGq89'
    '6UJ9mOU75Q/u1v0Eek4k9NHyzrTXbePXrOhwye6I+BygAIAH0Op5Es0lvH5IFN/UzcKsOsbn+mJn'
    '13TvHFd7fTogcMnuiPgcoAKwDa3bwA19/Yqz81vdsff84OYZuabzG+vR46CNbSy5j0Bwye6I+Byg'
    'AxgG0DQ+ZqSSto9Xv+FH5fal0r8+UHPcb3jbW3tJJru0lazYcMnuiPgYABYBpypaRIXNJtdtHtLl'
    'ApVtHYBtl7XTUqpqY1WVWatq3mNV1BzGfPVjoIo3QRmjXW+fn5rOAHDJ7oj4GDAADXt+Q5LU7NyW'
    'xidpIKNrRO6arWUjNpVyr6ruVXar1BUo5+bmurqqY1dkfOa1azn5ozxZZ3aAcMnuiPgcoABQA1SM'
    'btywoIsMot5u8o71B7vuINllm6PXbzdzjx+Nro2tgE9nZ1MABFgAAAAAAAAAB0BaEwMAAAAyDkey'
    'DTkoKConKToqKTo2Kylwye6I9AfjICABkiAIzA7OMzp16+7K029BltufLVuavtvZVMSVXJ4sqpgf'
    'nO+bDHqbNrcXNfbphjBwye6I+BygAzgAkU/6QZdXSnuwZLxLn39wAkf1HeLL0vUsxPtacwsAcMnu'
    'iPgcoAMgBfwLWOWhNhTr1+00cqPx4k++mZZl3R9PU4npu9O36nDJ7oj4HKABYB0egNGlgM92zc9P'
    'e4333A27zgxczVpHTWOiiXZvyjv1YnDJ7oj4HKGAQD73SbIMw5PH/VjaTRYG8HO8e2+aJpq81e5H'
    '0lk0AHDJ7oj4HKAAyAan9+Q90czo5YKQNittLjvickZ4ZYf3JLbW03303tbAcMnuiPgYACoAbLlR'
    'YZm9q59Q802mig0NpSCEpBs25U83VTjnPQVZV1VhxxR1c3dW2muabq2g5zeXoHDJ7oj4HKADEAcV'
    '/N/tV9B+m/ngfn5+Zg5faCOI19HrDvZ5r3pmLVG+wHDJ7oj4HKAAQAAeIB8psISZC9cvZvK/jfKX'
    'R927vE20zdnMdnia26NgcMnuiPgYAAgAZM4A4XIt6fXfTseg6BKjc5923bduN1Q8181TVTHOeoNd'
    'WVZV9xGZufMw/OmqsNbP63DJ7oj0B+kgIAZ8YAg2do5/DEDwwtF+NCNLk/FPqFoqLs43zmMquzaO'
    'qhYdXnNUXii1h8M3YHDJ7oj4HKAB8A8gV+NX7WHfFR/eh2lmyiCQE/OzPrjvp2VmabSXi1dq9ABw'
    'ye6I+BygAwAHOvd8if/iF18nP31R2k59uf02bSzSvu3aSeu+M5822A=='
)

path = os.path.join(tempfile.mkdtemp(prefix='babble-teardown-'), 'clip.ogg')
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
# Steps, not cycles: a cycle only completes once the clip has played, so
# a clip that never plays would keep any cycle-based bound from ever
# being reached.
state = {'phase': 'open', 'n': 0, 'steps': 0, 'events': [],
         'checked': False, 'kept': None}
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
    state['steps'] += 1

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
        # A clip the pipeline cannot play reports why instead of ever
        # starting: no demuxer for the container, or no decoder for the
        # codec. Waiting for get_playing() would sit there until the
        # watchdog, so the message would be a timeout and not the cause.
        error = stream.get_property('error')
        if error is not None:
            print('FAIL - the clip did not play: %s' % error.message)
            failures.append('the clip did not play')
            app.quit()
            return False
        if stream.get_playing():
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
        elif state['steps'] > 300:
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


# A hang is the failure this test cannot otherwise report: Meson kills it
# at its timeout with nothing printed. Dump every thread's stack well
# before that, and give up on our own so the run ends as a FAIL.
WATCHDOG_SECONDS = 90
faulthandler.dump_traceback_later(WATCHDOG_SECONDS, exit=False)


def watchdog():
    print('FAIL - the teardown did not finish in %d s (state: %s, '
          'cycle %d, events %r)' % (WATCHDOG_SECONDS, state['phase'],
                                    state['n'], state['events']),
          flush=True)
    failures.append('timed out')
    if app is not None:
        app.quit()
    return False


GLib.timeout_add_seconds(WATCHDOG_SECONDS, watchdog)

app = App(application_id='org.gnome.BabbleMediaTeardownTest', flags=0)
app.run([])
faulthandler.cancel_dump_traceback_later()

if failures:
    print('\n%d media teardown check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media teardown checks passed')
