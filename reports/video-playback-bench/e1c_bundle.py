"""E1c: the crashing reproducer (stream_cycles.py + streamwrap.PlayStream),
with exactly one change per mode.

e1c_reproducer.py URI CYCLES MODE
  orig     : stream_cycles.py unchanged: pause(); stop(); disconnect; drop
             adapter + play (sink and paintable stay referenced); remove
             widget; gc.  Crashed 5/5 runs before.
  deferred : pause(); stop(); wait for state-changed STOPPED; disconnect;
             in GLib.idle_add drop adapter + play; remove widget; gc
  dropsink : orig, but also drop sink and paintable together with play
"""
import gc
import os
import sys
import time


"""Can a Python Gtk.MediaStream subclass wrap GstPlay + gtk4paintablesink
so Gtk.Video / Gtk.MediaControls drive it unchanged?"""
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



URI, N, MODE = sys.argv[1], int(sys.argv[2]), sys.argv[3]
win = Gtk.Window(default_width=800, default_height=600)
win.present()
loop = GLib.MainLoop()
S = {'i': 0, 'ok': 0, 'finalized': 0, 'created': 0, 'stop_ms': []}


def rss():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) // 1024


def on_finalized(*_a):
    S['finalized'] += 1


def one():
    if S['i'] == N:
        gc.collect()
        print('SUMMARY mode=%s cycles=%d first_frames=%d finalized=%d/%d rss=%d '
              'stop_ms=%s..%s' % (MODE, N, S['ok'], S['finalized'], S['created'],
                                  rss(), min(S['stop_ms'] or [None]),
                                  max(S['stop_ms'] or [None])), flush=True)
        loop.quit()
        return False
    S['i'] += 1
    s = PlayStream(URI)
    s._play.weak_ref(on_finalized)
    S['created'] += 1
    s.set_muted(True)
    v = Gtk.Video(media_stream=s, autoplay=True)
    win.set_child(v)
    got = {}
    s._paintable.connect('invalidate-contents', lambda *a: got.setdefault('f', True))

    def after():
        win.set_child(None)
        gc.collect()
        print('cycle %d frame=%s finalized=%d/%d rss=%d' % (
            S['i'], bool(got), S['finalized'], S['created'], rss()), flush=True)
        GLib.timeout_add(300, one)

    def done():
        if got:
            S['ok'] += 1
        s.pause()
        if MODE == 'orig':
            s.shutdown()
            after()
        elif MODE == 'dropsink':
            s.shutdown()
            s._sink = s._paintable = None
            after()
        else:
            t0 = time.monotonic()
            play = s._play
            ad = s._adapter

            def on_state(_a, state):
                if state != GstPlay.PlayState.STOPPED or st.get('seen'):
                    return
                st['seen'] = True
                S['stop_ms'].append(round((time.monotonic() - t0) * 1000))
                ad.disconnect(st['hid'])
                for obj, hid in s._handlers:
                    obj.disconnect(hid)
                s._handlers = []

                def drop():
                    s._adapter = None
                    s._play = None
                    after()
                    return False
                GLib.idle_add(drop)
            st = {}
            st['hid'] = ad.connect('state-changed', on_state)
            play.stop()
        return False
    GLib.timeout_add(1500, done)
    return False


GLib.timeout_add(300, one)
loop.run()
os._exit(0)
