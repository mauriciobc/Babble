"""E5: idle memory of the playback engine, per stage, in one process.

e5_idle.py MODE PATH
  MODE engine   : the Phase 1a design (GstPlay + gtk4paintablesink, per viewer,
                  deferred release: stop, wait STOPPED, drop in idle)
  MODE mediafile: today's path (Gtk.MediaFile in Gtk.Video, release_media order)

Prints RSS (MB) after gc and a 2 s settle at each stage:
  baseline   window + Gst.init, no media object yet
  created    one engine / media file created, never played (engine only)
  playing    5 s into playback
  released   after release of that one
  cycles24   after 24 more create/play 1.5 s/release cycles
"""
import gc
import os
import sys
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
from gi.repository import Gio, GLib, Gst, GstPlay, Gtk  # noqa: E402

Gst.init(None)
MODE, PATH = sys.argv[1], sys.argv[2]
win = Gtk.Window(default_width=1280, default_height=720)
win.present()
loop = GLib.MainLoop()
R = {}


def rss():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) / 1024


def mark(name, then):
    gc.collect()

    def later():
        R[name] = round(rss(), 1)
        then()
        return False
    GLib.timeout_add(2000, later)


class Engine:
    def __init__(self):
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        self.paintable = self.sink.props.paintable
        outer = self.sink
        if self.paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self.sink
        self.play = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play)
        self.hid = self.adapter.connect('state-changed', self._state)
        self.on_stopped = None
        self.play.set_mute(True)

    def _state(self, _a, s):
        if s == GstPlay.PlayState.STOPPED and self.on_stopped:
            cb, self.on_stopped = self.on_stopped, None
            cb()

    def start(self):
        win.set_child(Gtk.Picture(paintable=self.paintable))
        self.play.set_uri('file://' + PATH)
        self.play.play()

    def release(self, done):
        win.set_child(None)

        def stopped():
            self.adapter.disconnect(self.hid)

            def drop():
                self.play = self.adapter = self.sink = self.paintable = None
                done()
                return False
            GLib.idle_add(drop)
        self.on_stopped = stopped
        self.play.stop()


class MediaFile:
    def __init__(self):
        self.stream = None

    def start(self):
        self.stream = Gtk.MediaFile.new_for_file(Gio.File.new_for_path(PATH))
        self.stream.set_muted(True)
        v = Gtk.Video(autoplay=True)
        v.set_media_stream(self.stream)
        win.set_child(v)

    def release(self, done):
        self.stream.pause()
        win.set_child(None)
        self.stream = None
        GLib.idle_add(lambda: (done(), False)[1])


make = Engine if MODE == 'engine' else MediaFile
S = {'obj': None, 'n': 0}


def stage_baseline():
    mark('baseline', stage_created)


def stage_created():
    S['obj'] = make()
    if MODE == 'engine':
        mark('created', stage_play)
    else:
        stage_play()


def stage_play():
    S['obj'].start()
    GLib.timeout_add(3000, lambda: (mark('playing', stage_release), False)[1])


def stage_release():
    S['obj'].release(lambda: (S.update(obj=None), mark('released', cycle)))


def cycle():
    if S['n'] == 24:
        mark('cycles24', finish)
        return
    S['n'] += 1
    o = make()
    o.start()
    GLib.timeout_add(1500, lambda: (o.release(lambda: GLib.timeout_add(150, lambda: (cycle(), False)[1])), False)[1])


def finish():
    print('E5 mode=%s %s' % (MODE, ' '.join('%s=%s' % kv for kv in R.items())), flush=True)
    loop.quit()


GLib.timeout_add(1000, lambda: (stage_baseline(), False)[1])
GLib.timeout_add(180000, lambda: (print('E5 TIMEOUT', R, flush=True), loop.quit(), False)[2])
loop.run()
os._exit(0)
