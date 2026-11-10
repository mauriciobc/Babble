"""E4: cost of drawing GstPlay video through a Python Gtk.MediaStream wrapper.

e4_forward.py MODE PATH SECONDS
  MODE wrapper: Gtk.Video(media_stream=Python Gtk.MediaStream subclass) that
                forwards do_snapshot / intrinsic size to the sink paintable
                and relays invalidate-contents in Python (the Phase 1a design)
  MODE direct : Gtk.Picture(paintable=the sink's paintable); no Python per frame

Measures, over SECONDS of playback starting 1 s after the first frame:
  main_cpu_ms_per_frame  main thread's own utime+stime (/proc/self/task/<tid>)
                         divided by frames the sink rendered
  py_us_per_frame        wall time spent inside the wrapper's Python vfuncs and
                         relay, per rendered frame (wrapper only)
  rendered, dropped      the sink's own counters (basesink 'stats')
  stalls_over_16ms       main-loop stalls: a 4 ms timer that fires >16 ms late
  stall_max_ms
"""
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
MODE, PATH, SECONDS = sys.argv[1], sys.argv[2], float(sys.argv[3])
CLK = os.sysconf('SC_CLK_TCK')
MAIN_TID = os.getpid()      # the GTK main loop runs on the process's first thread
PY = {'t': 0.0}


def main_cpu_s():
    with open('/proc/self/task/%d/stat' % MAIN_TID) as f:
        fields = f.read().rsplit(')', 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / CLK


class Engine:
    def __init__(self, uri):
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        self.paintable = self.sink.props.paintable
        outer = self.sink
        if self.paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self.sink
        self.gl = bool(self.paintable.props.gl_context)
        self.play = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play)
        self.play.set_mute(True)
        self.play.set_uri(uri)


class Stream(Gtk.MediaStream):
    """The Phase 1a wrapper, instrumented."""

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        engine.adapter.connect('duration-changed', self._dur)
        engine.adapter.connect('position-updated', self._pos)
        engine.paintable.connect('invalidate-contents', self._inval)
        engine.paintable.connect('invalidate-size', lambda *a: self.invalidate_size())

    def _dur(self, _a, d):
        if not self.is_prepared():
            self.stream_prepared(False, True, True, d // 1000)

    def _pos(self, _a, p):
        t = time.perf_counter()
        self.update(p // 1000)
        PY['t'] += time.perf_counter() - t

    def _inval(self, *_a):
        t = time.perf_counter()
        self.invalidate_contents()
        PY['t'] += time.perf_counter() - t

    def do_play(self):
        self.e.play.play()
        return True

    def do_pause(self):
        self.e.play.pause()

    def do_snapshot(self, snap, w, h):
        t = time.perf_counter()
        self.e.paintable.snapshot(snap, w, h)
        PY['t'] += time.perf_counter() - t

    def do_get_intrinsic_width(self):
        t = time.perf_counter()
        r = self.e.paintable.get_intrinsic_width()
        PY['t'] += time.perf_counter() - t
        return r

    def do_get_intrinsic_height(self):
        t = time.perf_counter()
        r = self.e.paintable.get_intrinsic_height()
        PY['t'] += time.perf_counter() - t
        return r

    def do_get_intrinsic_aspect_ratio(self):
        t = time.perf_counter()
        r = self.e.paintable.get_intrinsic_aspect_ratio()
        PY['t'] += time.perf_counter() - t
        return r


win = Gtk.Window(default_width=1280, default_height=720)
win.present()
loop = GLib.MainLoop()
engine = Engine('file://' + PATH)
if MODE == 'wrapper':
    stream = Stream(engine)
    win.set_child(Gtk.Video(media_stream=stream, autoplay=True))
    engine.play.pause()     # preroll: prepared comes from it, then autoplay
else:
    win.set_child(Gtk.Picture(paintable=engine.paintable))
    engine.play.play()

state = {'ff': False, 'measuring': False, 'stalls': 0, 'stall_max': 0.0, 'last': None}


def first(*_a):
    if not state['ff']:
        state['ff'] = True
        GLib.timeout_add(1000, start)


engine.paintable.connect('invalidate-contents', first)


def probe():
    now = time.monotonic()
    if state['measuring'] and state['last'] is not None:
        late = (now - state['last']) * 1000 - 4
        if late > 16:
            state['stalls'] += 1
        state['stall_max'] = max(state['stall_max'], late)
    state['last'] = now
    return True


GLib.timeout_add(4, probe)


def stats():
    st = engine.sink.props.stats
    return st.get_value('rendered'), st.get_value('dropped')


def start():
    state['r0'], state['d0'] = stats()
    state['c0'] = main_cpu_s()
    PY['t'] = 0.0
    state['stalls'], state['stall_max'] = 0, 0.0
    state['measuring'] = True
    GLib.timeout_add(int(SECONDS * 1000), stop)
    return False


def stop():
    state['measuring'] = False
    r, d = stats()
    rendered, dropped = r - state['r0'], d - state['d0']
    cpu_ms = (main_cpu_s() - state['c0']) * 1000
    per = rendered or 1
    print('E4 mode=%s file=%s gl=%s seconds=%g rendered=%d dropped=%d '
          'main_cpu_ms=%.0f main_cpu_ms_per_frame=%.3f py_us_per_frame=%.1f '
          'stalls_over_16ms=%d stall_max_ms=%.1f' % (
              MODE, os.path.basename(PATH), engine.gl, SECONDS, rendered, dropped,
              cpu_ms, cpu_ms / per, PY['t'] * 1e6 / per,
              state['stalls'], state['stall_max']), flush=True)
    engine.play.stop()
    GLib.timeout_add(300, lambda: (loop.quit(), False)[1])
    return False


GLib.timeout_add(20000, lambda: (print('E4 TIMEOUT', flush=True), loop.quit(), False)[2])
loop.run()
os._exit(0)
