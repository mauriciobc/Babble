"""E1b: E1 with the real design: a Gtk.MediaStream wrapper inside Gtk.Video,
torn down the way the viewer will (pause, then stop), as in stream_cycles.py,
which crashed.

e1b_wrapper.py URI CYCLES MODE [FIRST_FRAME_TIMEOUT_S]
  MODE immediate: pause(), stop(), disconnect, drop refs, then remove the
                  widget (stream_cycles.py order: the crashing control)
  MODE deferred : pause(), remove the widget, stop(), wait for STOPPED,
                  disconnect, drop refs in GLib.idle_add
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
from gi.repository import GLib, Gst, GstPlay, Gtk  # noqa: E402

Gst.init(None)
URI, CYCLES, MODE = sys.argv[1], int(sys.argv[2]), sys.argv[3]
FF_TIMEOUT_MS = int(float(sys.argv[4] if len(sys.argv) > 4 else 3) * 1000)
HOLD_MS = 1500
STOP_TIMEOUT_MS = 3000
S = {'i': 0, 'frames': 0, 'no_frame': 0, 'stopped_seen': 0,
     'stop_timeouts': 0, 'finalized': 0, 'created': 0, 'stop_ms': []}


def on_finalized(*_a):
    S['finalized'] += 1


class PlayStream(Gtk.MediaStream):
    def __init__(self, uri):
        super().__init__()
        self._handlers = []
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        self.paintable = self.sink.props.paintable
        outer = self.sink
        if self.paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self.sink
        self.play_ = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.play_.weak_ref(on_finalized)
        S['created'] += 1
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play_)
        self._h(self.adapter, 'duration-changed', self._on_duration)
        self._h(self.adapter, 'position-updated', lambda a, p: self.update(p // 1000))
        self._h(self.adapter, 'seek-done', lambda *a: self.seek_success())
        self._h(self.adapter, 'end-of-stream', lambda *a: self.stream_ended())
        self._h(self.adapter, 'error', lambda a, e, d: self.gerror(e))
        self._h(self.adapter, 'state-changed', self._on_state)
        self._h(self.paintable, 'invalidate-contents', self._on_frame)
        self._h(self.paintable, 'invalidate-size', lambda *a: self.invalidate_size())
        self.on_first_frame = self.on_stopped = None
        self.play_.set_uri(uri)
        self.play_.pause()

    def _h(self, obj, sig, cb):
        self._handlers.append((obj, obj.connect(sig, cb)))

    def _on_duration(self, _a, dur):
        if not self.is_prepared():
            self.stream_prepared(True, True, True, dur // 1000)

    def _on_frame(self, *_a):
        self.invalidate_contents()
        if self.on_first_frame:
            cb, self.on_first_frame = self.on_first_frame, None
            cb()

    def _on_state(self, _a, state):
        if state == GstPlay.PlayState.STOPPED and self.on_stopped:
            cb, self.on_stopped = self.on_stopped, None
            cb()

    def _alive(self):
        return self.play_ is not None

    def do_play(self):
        if self._alive():
            self.play_.play()
        return self._alive()

    def do_pause(self):
        if self._alive():
            self.play_.pause()

    def do_seek(self, ts):
        if self._alive():
            self.play_.seek(ts * 1000)

    def do_update_audio(self, muted, volume):
        if self._alive():
            self.play_.set_mute(muted)
            self.play_.set_volume(volume)

    def do_snapshot(self, snap, w, h):
        if self._alive():
            self.paintable.snapshot(snap, w, h)

    def do_get_intrinsic_width(self):
        return self.paintable.get_intrinsic_width() if self._alive() else 0

    def do_get_intrinsic_height(self):
        return self.paintable.get_intrinsic_height() if self._alive() else 0

    def do_get_intrinsic_aspect_ratio(self):
        return self.paintable.get_intrinsic_aspect_ratio() if self._alive() else 0.0

    def disconnect_all(self):
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []

    def drop(self):
        self.play_ = self.adapter = None
        self.sink = self.paintable = None


win = Gtk.Window(default_width=800, default_height=600)
win.present()
loop = GLib.MainLoop()


def rss():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) // 1024


def one():
    if S['i'] == CYCLES:
        GLib.timeout_add(500, summary)
        return False
    S['i'] += 1
    c = {'done': False}
    s = PlayStream(URI)
    s.set_muted(True)
    c['s'] = s
    win.set_child(Gtk.Video(media_stream=s, autoplay=True))

    def ff():
        c['ff'] = True
        S['frames'] += 1
        GLib.timeout_add(HOLD_MS, release)
    s.on_first_frame = ff

    def no_frame():
        if 'ff' not in c and not c['done']:
            S['no_frame'] += 1
            release()
        return False
    GLib.timeout_add(FF_TIMEOUT_MS, no_frame)

    def release():
        if c.get('releasing'):
            return False
        c['releasing'] = True
        s = c['s']
        s.pause()
        if MODE == 'immediate':
            # stream_cycles.py order: stop, disconnect, drop, then widget.
            s.play_.stop()
            s.disconnect_all()
            s.drop()
            win.set_child(None)
            c.pop('s')
            gc.collect()
            report(c)
            return False
        win.set_child(None)
        c['t_stop'] = time.monotonic()

        def stopped():
            if c['done']:
                return
            S['stopped_seen'] += 1
            S['stop_ms'].append(round((time.monotonic() - c['t_stop']) * 1000))
            finish()
        s.on_stopped = stopped
        s.play_.stop()

        def stop_timeout():
            if not c['done']:
                S['stop_timeouts'] += 1
                finish()
            return False
        GLib.timeout_add(STOP_TIMEOUT_MS, stop_timeout)
        return False

    def finish():
        c['done'] = True
        c['s'].disconnect_all()

        def drop():
            c['s'].drop()
            c.pop('s')
            gc.collect()
            report(c)
            return False
        GLib.idle_add(drop)


def report(c):
    c['done'] = True
    print('cycle %d frame=%s finalized=%d/%d rss=%d' % (
        S['i'], 'ff' in c, S['finalized'], S['created'], rss()), flush=True)
    GLib.timeout_add(150, one)


def summary():
    gc.collect()
    ms = sorted(S['stop_ms'])
    print('SUMMARY mode=%s cycles=%d first_frames=%d no_frame=%d '
          'stopped_seen=%d stop_timeouts=%d finalized=%d/%d rss=%d '
          'stop_ms_min=%s stop_ms_max=%s' % (
              MODE, S['i'], S['frames'], S['no_frame'], S['stopped_seen'],
              S['stop_timeouts'], S['finalized'], S['created'], rss(),
              ms[0] if ms else None, ms[-1] if ms else None), flush=True)
    loop.quit()
    return False


GLib.timeout_add(300, one)
loop.run()
os._exit(0)
