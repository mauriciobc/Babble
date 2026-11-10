"""E3: crash-rate baseline for Babble's shipping path. Gtk.MediaFile on a
local downloaded file in a Gtk.Video (autoplay), torn down in release_media
order (src/mediaplayback.py:186: pause, set_child(None), drop), then gc.

e3_mediafile.py PATH CYCLES  -> one line per cycle, SUMMARY at the end.
A crash ends the process; the last 'cycle' line says how far it got.
"""
import gc
import os
import sys

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402

PATH, N = sys.argv[1], int(sys.argv[2])
HOLD_MS = 1500
win = Gtk.Window(default_width=800, default_height=600)
win.present()
loop = GLib.MainLoop()
S = {'i': 0, 'frames': 0}


def one():
    if S['i'] == N:
        print('SUMMARY cycles=%d first_frames=%d' % (N, S['frames']), flush=True)
        loop.quit()
        return False
    S['i'] += 1
    stream = Gtk.MediaFile.new_for_file(Gio.File.new_for_path(PATH))
    stream.set_muted(True)
    video = Gtk.Video(autoplay=True)
    video.set_media_stream(stream)
    win.set_child(video)
    got = {}

    def frame(*_a):
        if not got:
            got['f'] = True
            S['frames'] += 1
    hid = stream.connect('invalidate-contents', frame)

    def done():
        stream.disconnect(hid)
        # release_media(bin_widget, stream): pause, drop the widget.
        stream.pause()
        win.set_child(None)
        gc.collect()
        print('cycle %d frame=%s' % (S['i'], bool(got)), flush=True)
        GLib.timeout_add(150, one)
        return False
    GLib.timeout_add(HOLD_MS, done)
    return False


GLib.timeout_add(300, one)
loop.run()
os._exit(0)
