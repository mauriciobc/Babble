"""Can a Python Gtk.MediaStream subclass wrap GstPlay + gtk4paintablesink
so Gtk.Video / Gtk.MediaControls drive it unchanged?"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
from gi.repository import GLib, Gdk, GObject, Gst, GstPlay, Gtk  # noqa

Gst.init(None)
calls = {'snapshot': 0, 'play': 0, 'pause': 0, 'seek': 0}


class PlayStream(Gtk.MediaStream):
    def __init__(self, uri):
        super().__init__()
        self._sink = Gst.ElementFactory.make('gtk4paintablesink')
        self._paintable = self._sink.props.paintable
        outer = self._sink
        if self._paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self._sink
        self._play = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self._adapter = GstPlay.PlaySignalAdapter.new(self._play)
        self._adapter.connect('duration-changed', self._on_duration)
        self._adapter.connect('position-updated', self._on_position)
        self._adapter.connect('seek-done', lambda *a: self.seek_success())
        self._adapter.connect('end-of-stream', lambda *a: self.stream_ended())
        self._adapter.connect('error', lambda a, e, d: self.gerror(e))
        self._paintable.connect('invalidate-contents', lambda *a: self.invalidate_contents())
        self._paintable.connect('invalidate-size', lambda *a: self.invalidate_size())
        self._play.set_uri(uri)
        self._play.pause()   # preroll

    def _on_duration(self, adapter, dur):
        if not self.is_prepared():
            self.stream_prepared(True, True, True, dur // 1000)

    def _on_position(self, adapter, pos):
        self.update(pos // 1000)

    # Gtk.MediaStream vfuncs
    def do_play(self):
        calls['play'] += 1
        self._play.play()
        return True

    def do_pause(self):
        calls['pause'] += 1
        self._play.pause()

    def do_seek(self, timestamp):
        calls['seek'] += 1
        self._play.seek(timestamp * 1000)

    def do_update_audio(self, muted, volume):
        self._play.set_mute(muted)
        self._play.set_volume(volume)

    # Gdk.Paintable vfuncs, forwarded to the sink's paintable
    def do_snapshot(self, snapshot, w, h):
        calls['snapshot'] += 1
        self._paintable.snapshot(snapshot, w, h)

    def do_get_intrinsic_width(self):
        return self._paintable.get_intrinsic_width()

    def do_get_intrinsic_height(self):
        return self._paintable.get_intrinsic_height()

    def do_get_intrinsic_aspect_ratio(self):
        return self._paintable.get_intrinsic_aspect_ratio()

    def do_get_current_image(self):
        return self._paintable.get_current_image()

    def shutdown(self):
        self._play.stop()


win = Gtk.Window(default_width=800, default_height=600)
win.present()
s = PlayStream(sys.argv[1])
s.set_muted(True)
video = Gtk.Video(media_stream=s, autoplay=True)
win.set_child(video)
loop = GLib.MainLoop()
seen = {}
s.connect('notify::prepared', lambda o, p: seen.setdefault('prepared', o.is_prepared()))
s.connect('notify::playing', lambda o, p: seen.setdefault('playing', o.get_playing()))


def check():
    seen['timestamp_s'] = round(s.get_timestamp() / 1e6, 2)
    s.seek(int(10e6))
    GLib.timeout_add(1500, finish)
    return False


def finish():
    seen['after_seek_s'] = round(s.get_timestamp() / 1e6, 2)
    s.pause()
    s.shutdown()
    win.set_child(None)
    print('seen:', seen, 'calls:', calls,
          'intrinsic:', s.get_intrinsic_width(), 'x', s.get_intrinsic_height())
    loop.quit()
    return False


GLib.timeout_add(3000, check)
GLib.timeout_add(15000, lambda: (print('TIMEOUT', seen, calls), loop.quit()))
loop.run()
