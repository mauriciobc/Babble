"""Can a Python Gtk.MediaStream subclass wrap GstPlay + gtk4paintablesink
so Gtk.Video / Gtk.MediaControls drive it unchanged?"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
gi.require_version('Gdk', '4.0')
from gi.repository import GLib, Gdk, GObject, Gst, GstPlay, Gtk  # noqa

Gst.init(None)
calls = {'snapshot': 0, 'play': 0, 'pause': 0, 'seek': 0}


class PlayStream(Gtk.MediaStream):
    def __init__(self, uri):
        super().__init__()
        self._handlers = []
        self._sink = Gst.ElementFactory.make('gtk4paintablesink')
        self._paintable = self._sink.props.paintable
        outer = self._sink
        if self._paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self._sink
        self._play = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self._adapter = GstPlay.PlaySignalAdapter.new(self._play)
        self._h(self._adapter, 'duration-changed', self._on_duration)
        self._h(self._adapter, 'position-updated', self._on_position)
        self._h(self._adapter, 'seek-done', lambda *a: self.seek_success())
        self._h(self._adapter, 'end-of-stream', lambda *a: self.stream_ended())
        self._h(self._adapter, 'error', lambda a, e, d: self.gerror(e))
        self._h(self._paintable, 'invalidate-contents', lambda *a: self.invalidate_contents())
        self._h(self._paintable, 'invalidate-size', lambda *a: self.invalidate_size())
        self._play.set_uri(uri)
        self._play.pause()   # preroll

    def _h(self, obj, sig, cb):
        self._handlers.append((obj, obj.connect(sig, cb)))

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
        for obj, hid in self._handlers:
            obj.disconnect(hid)
        self._handlers = []
        self._adapter = None
        self._play = None


