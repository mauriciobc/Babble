"""One long-lived GstPlay engine; a new Gtk.Video + stream wrapper per media.
The engine is never finalised during the run (Showtime's pattern)."""
import gc, os, sys
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0'); gi.require_version('GstPlay', '1.0')
from gi.repository import GLib, Gst, GstPlay, Gtk
Gst.init(None)


class Engine:
    """App-lifetime GstPlay + gtk4paintablesink. Streams attach/detach."""
    def __init__(self):
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        self.paintable = self.sink.props.paintable
        outer = self.sink
        if self.paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin'); outer.props.sink = self.sink
        self.play = GstPlay.Play(video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play)
        self.owner = None
        for sig in ('duration-changed', 'position-updated', 'seek-done', 'end-of-stream', 'error'):
            self.adapter.connect(sig, self._relay, sig)
        self.paintable.connect('invalidate-contents', self._relay_paint, 'c')
        self.paintable.connect('invalidate-size', self._relay_paint, 's')

    def _relay(self, adapter, *args):
        sig = args[-1]
        if self.owner is not None:
            self.owner._engine_event(sig, *args[:-1])

    def _relay_paint(self, p, kind):
        if self.owner is not None:
            (self.owner.invalidate_contents if kind == 'c' else self.owner.invalidate_size)()

    def attach(self, stream, uri):
        self.owner = stream
        self.play.stop()
        self.play.set_uri(uri)
        self.play.pause()

    def detach(self, stream):
        if self.owner is stream:
            self.play.stop()
            self.owner = None


class EngineStream(Gtk.MediaStream):
    def __init__(self, engine, uri):
        super().__init__()
        self.engine = engine
        engine.attach(self, uri)

    def _engine_event(self, sig, *a):
        if sig == 'duration-changed' and not self.is_prepared():
            self.stream_prepared(True, True, True, a[0] // 1000)
        elif sig == 'position-updated':
            self.update(a[0] // 1000)
        elif sig == 'seek-done':
            self.seek_success()
        elif sig == 'end-of-stream':
            self.stream_ended()
        elif sig == 'error':
            self.gerror(a[0])

    def _live(self):
        return self.engine is not None and self.engine.owner is self

    def do_play(self):
        if self._live(): self.engine.play.play()
        return self._live()
    def do_pause(self):
        if self._live(): self.engine.play.pause()
    def do_seek(self, ts):
        if self._live(): self.engine.play.seek(ts * 1000)
    def do_update_audio(self, muted, volume):
        if self._live():
            self.engine.play.set_mute(muted); self.engine.play.set_volume(volume)
    def do_snapshot(self, snap, w, h):
        if self._live(): self.engine.paintable.snapshot(snap, w, h)
    def do_get_intrinsic_width(self):
        return self.engine.paintable.get_intrinsic_width() if self._live() else 0
    def do_get_intrinsic_height(self):
        return self.engine.paintable.get_intrinsic_height() if self._live() else 0
    def do_get_intrinsic_aspect_ratio(self):
        return self.engine.paintable.get_intrinsic_aspect_ratio() if self._live() else 0.0
    def do_get_current_image(self):
        return self.engine.paintable.get_current_image()

    def release(self):
        if self.engine is not None:
            self.engine.detach(self)
            self.engine = None


uri, n = sys.argv[1], int(sys.argv[2])
engine = Engine()
win = Gtk.Window(default_width=800, default_height=600); win.present()
loop = GLib.MainLoop(); state = {'i': 0, 'ok': 0}
def rss():
    for l in open('/proc/self/status'):
        if l.startswith('VmRSS'): return int(l.split()[1]) // 1024
def one():
    if state['i'] == n:
        gc.collect()
        print('cycles', n, 'with first frame', state['ok'], 'rss_mb', rss(), flush=True)
        loop.quit(); return False
    state['i'] += 1
    s = EngineStream(engine, uri); s.set_muted(True)
    v = Gtk.Video(media_stream=s, autoplay=True); win.set_child(v)
    got = {}
    h = engine.paintable.connect('invalidate-contents', lambda *a: got.setdefault('f', True))
    def done():
        engine.paintable.disconnect(h)
        if got: state['ok'] += 1
        s.pause(); s.release(); win.set_child(None); gc.collect()
        if state['i'] == 1: print('rss_after_1_mb', rss(), flush=True)
        GLib.timeout_add(300, one); return False
    GLib.timeout_add(1500, done); return False
GLib.timeout_add(300, one)
loop.run()
os._exit(0)
