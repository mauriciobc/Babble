"""Quit while a video plays. quit_check.py MODE URI

MODE naive    : nothing special at shutdown; the playing engine is freed by
                interpreter teardown after app.run() returns (control)
MODE mitigated: do_shutdown pauses, stops, turns the main context until the
                adapter reports STOPPED (bounded), disconnects and drops the
                engine before chaining up

The app quits 1 s after the first frame, through app.quit(), and the process
ends with sys.exit(app.run()), as Babble's launcher does. Prints one line:
QUIT mode=... frame=... stopped=... wait_ms=...
"""
import gc
import sys
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
from gi.repository import Gio, GLib, Gst, GstPlay, Gtk  # noqa: E402

Gst.init(None)
MODE, URI = sys.argv[1], sys.argv[2]
STOP_DEADLINE_S = 1.0


class Viewer:
    """One per-viewer engine: GstPlay + gtk4paintablesink in a Gtk.Video
    through a Gtk.MediaStream wrapper (the Phase 1a design)."""

    def __init__(self, window, on_first_frame):
        self.stream = Stream(self)
        self.stream.set_muted(True)
        window.set_child(Gtk.Video(media_stream=self.stream, autoplay=True))
        self.on_first_frame = on_first_frame
        self.stopped = False

    def release_sync(self):
        """Deferred release, made synchronous for shutdown."""
        s = self.stream
        s.pause()
        t0 = time.monotonic()
        s.play_.stop()
        ctx = GLib.MainContext.default()
        while not self.stopped and time.monotonic() - t0 < STOP_DEADLINE_S:
            if not ctx.iteration(False):
                time.sleep(0.002)
        wait_ms = round((time.monotonic() - t0) * 1000)
        s.disconnect_all()
        s.play_ = s.adapter = None
        gc.collect()
        return wait_ms


class Stream(Gtk.MediaStream):
    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.handlers = []
        self.sink = Gst.ElementFactory.make('gtk4paintablesink')
        self.paintable = self.sink.props.paintable
        outer = self.sink
        if self.paintable.props.gl_context:
            outer = Gst.ElementFactory.make('glsinkbin')
            outer.props.sink = self.sink
        self.play_ = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play_)
        self._h(self.adapter, 'duration-changed', self._on_duration)
        self._h(self.adapter, 'position-updated', lambda a, p: self.update(p // 1000))
        self._h(self.adapter, 'state-changed', self._on_state)
        self._h(self.paintable, 'invalidate-contents', self._on_frame)
        self._h(self.paintable, 'invalidate-size', lambda *a: self.invalidate_size())
        self.first = True
        self.play_.set_uri(URI)
        self.play_.pause()

    def _h(self, obj, sig, cb):
        self.handlers.append((obj, obj.connect(sig, cb)))

    def disconnect_all(self):
        for obj, hid in self.handlers:
            obj.disconnect(hid)
        self.handlers = []

    def _on_duration(self, _a, dur):
        if not self.is_prepared():
            self.stream_prepared(True, True, True, dur // 1000)

    def _on_state(self, _a, state):
        if state == GstPlay.PlayState.STOPPED:
            self.viewer.stopped = True

    def _on_frame(self, *_a):
        self.invalidate_contents()
        if self.first:
            self.first = False
            self.viewer.on_first_frame()

    def do_play(self):
        if self.play_ is not None:
            self.play_.play()
        return self.play_ is not None

    def do_pause(self):
        if self.play_ is not None:
            self.play_.pause()

    def do_seek(self, ts):
        if self.play_ is not None:
            self.play_.seek(ts * 1000)

    def do_update_audio(self, muted, volume):
        if self.play_ is not None:
            self.play_.set_mute(muted)
            self.play_.set_volume(volume)

    def do_snapshot(self, snap, w, h):
        if self.play_ is not None:
            self.paintable.snapshot(snap, w, h)

    def do_get_intrinsic_width(self):
        return self.paintable.get_intrinsic_width()

    def do_get_intrinsic_height(self):
        return self.paintable.get_intrinsic_height()

    def do_get_intrinsic_aspect_ratio(self):
        return self.paintable.get_intrinsic_aspect_ratio()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.mauriciobc.BabbleQuitCheck',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.viewer = None
        self.result = {'frame': False, 'stopped': None, 'wait_ms': None}

    def do_activate(self):
        win = Gtk.Window(application=self, default_width=800, default_height=600)
        win.present()

        def first_frame():
            self.result['frame'] = True
            GLib.timeout_add(1000, lambda: (self.quit(), False)[1])
        self.viewer = Viewer(win, first_frame)
        GLib.timeout_add(10000, lambda: (self.quit(), False)[1])

    def do_shutdown(self):
        if MODE == 'mitigated' and self.viewer is not None:
            self.result['wait_ms'] = self.viewer.release_sync()
            self.result['stopped'] = self.viewer.stopped
        print('QUIT mode=%s frame=%s stopped=%s wait_ms=%s' % (
            MODE, self.result['frame'], self.result['stopped'],
            self.result['wait_ms']), flush=True)
        Gtk.Application.do_shutdown(self)


app = App()
sys.exit(app.run(None))
