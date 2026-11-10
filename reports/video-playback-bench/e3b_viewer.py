"""E3b: soak the real media viewer. present_media() on a video (odd cycles)
or a GIFV (even cycles) served over loopback HTTP, through the real path:
_show_stream -> _download_stream (libsoup) -> _start_stream (Gtk.MediaFile,
pages.connect_weak handlers, idle inhibition) -> autoplay; hold 1.5 s while
playing; viewer.close() (the real _unload_media / release_media); gc.

e3b_viewer.py BASE_URL CYCLES
Run from the source tree with BABBLE_SRCDIR=src GSETTINGS_SCHEMA_DIR=build/data.
"""
import gc
import os
import sys

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
srcdir = os.environ['BABBLE_SRCDIR']
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()
from src import mediaplayback, mediaviewer  # noqa: E402

# Never deferred by a metered or power-saving host: playback must start.
mediaplayback._system_state = lambda name: False

BASE, N = sys.argv[1], int(sys.argv[2])
HOLD_MS = 1500
START_TIMEOUT_MS = 10000
S = {'i': 0, 'played': 0, 'no_play': 0}


class App(Adw.Application):
    def do_activate(self):
        self.host = Adw.ApplicationWindow(application=self, content=Gtk.Overlay(),
                                          default_width=1000, default_height=800)
        self.host.present()
        GLib.timeout_add(300, self.one)

    def one(self):
        if S['i'] == N:
            gc.collect()
            print('SUMMARY cycles=%d played=%d no_play=%d' % (
                N, S['played'], S['no_play']), flush=True)
            self.quit()
            return False
        S['i'] += 1
        kind = 'video' if S['i'] % 2 else 'gifv'
        attachment = {'type': kind, 'url': BASE + 'gifv.mp4',
                      'description': 'E3b clip %d' % S['i']}
        viewer = mediaviewer.present_media(self.host.get_content(), [attachment])
        c = {'viewer': viewer, 'kind': kind, 'waited': 0, 'played': False}

        def wait_playing():
            v = c['viewer']
            stream = v._stream
            if stream is not None and stream.get_playing():
                c['played'] = True
                S['played'] += 1
                GLib.timeout_add(HOLD_MS, close)
                return False
            c['waited'] += 50
            if c['waited'] >= START_TIMEOUT_MS:
                S['no_play'] += 1
                close()
                return False
            return True

        def close():
            c['viewer'].close()
            c['viewer'] = None
            gc.collect()
            print('cycle %d kind=%s played=%s' % (S['i'], c['kind'], c['played']),
                  flush=True)
            GLib.timeout_add(150, self.one)
            return False

        GLib.timeout_add(50, wait_playing)
        return False


app = App(application_id='io.github.mauriciobc.BabbleE3b')
sys.exit(app.run([]))
