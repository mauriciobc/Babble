"""One candidate, one process. Prints one JSON line.

bench.py CANDIDATE single|cycles PORT
"""
import ctypes
import gc
import json
import os
import resource
import sys
import tempfile
import time
import urllib.request

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gio, Gst, Gtk  # noqa: E402

CAND, MODE, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
BASE = 'http://127.0.0.1:%d/' % PORT
CLK = os.sysconf('SC_CLK_TCK')
LIVE = os.environ.get('BENCH_URL')
SEEK_TO = float(os.environ.get('BENCH_SEEK', '20'))
Gst.init(None)


def stats():
    if LIVE:
        return 0, 0, 0
    with urllib.request.urlopen(BASE + 'stats') as r:
        b, req, rng = r.read().decode().split()
    return int(b), int(req), int(rng)


def reset():
    if not LIVE:
        urllib.request.urlopen(BASE + 'reset').read()


def descendants(pid):
    out = []
    try:
        for tid in os.listdir('/proc/%d/task' % pid):
            with open('/proc/%d/task/%s/children' % (pid, tid)) as f:
                for c in f.read().split():
                    out.append(int(c))
                    out += descendants(int(c))
    except OSError:
        pass
    return out


def rss_mb():
    total = 0
    for pid in [os.getpid()] + descendants(os.getpid()):
        try:
            with open('/proc/%d/status' % pid) as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        total += int(line.split()[1])
        except OSError:
            pass
    return round(total / 1024, 1)


def cpu_s():
    r = resource.getrusage(resource.RUSAGE_SELF)
    total = r.ru_utime + r.ru_stime
    for pid in descendants(os.getpid()):
        try:
            with open('/proc/%d/stat' % pid) as f:
                fields = f.read().rsplit(')', 1)[1].split()
            total += (int(fields[11]) + int(fields[12])) / CLK
        except OSError:
            pass
    return total


def find_decoders(bin_):
    names = []
    if bin_ is None:
        return names
    try:
        it = bin_.iterate_recurse()
    except AttributeError:
        return names
    while True:
        ok, el = it.next()
        if ok != Gst.IteratorResult.OK:
            break
        f = el.get_factory()
        if f and 'Decoder' in (f.get_klass() or ''):
            names.append(f.get_name())
    return sorted(set(names))


def paintable_sink():
    sink = Gst.ElementFactory.make('gtk4paintablesink')
    paintable = sink.props.paintable
    outer = sink
    if paintable.props.gl_context:
        outer = Gst.ElementFactory.make('glsinkbin')
        outer.props.sink = sink
    return sink, paintable, outer


# ---------------------------------------------------------------- candidates
class Base:
    """Callbacks: self.on_frame() for every new frame, self.on_seeked()."""
    on_frame = on_seeked = on_error = staticmethod(lambda *a: None)

    def decoders(self):
        return []


class GtkMediaFileCand(Base):
    local = False

    def start(self, uri):
        if self.local:
            return self._download(uri)
        self._play_file(Gio.File.new_for_uri(uri))

    def _download(self, uri):
        gi.require_version('Soup', '3.0')
        from gi.repository import Soup
        self.session = Soup.Session()
        msg = Soup.Message.new('GET', uri)
        self.tmp = tempfile.mkdtemp()
        dest = Gio.File.new_for_path(os.path.join(self.tmp, 'm.mp4'))

        def sent(session, res):
            stream = session.send_finish(res)
            out = dest.replace(None, False, Gio.FileCreateFlags.NONE, None)
            out.splice_async(stream, Gio.OutputStreamSpliceFlags.CLOSE_SOURCE
                             | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
                             GLib.PRIORITY_DEFAULT, None, spliced)

        def spliced(out, res):
            out.splice_finish(res)
            self._play_file(dest)
        self.session.send_async(msg, GLib.PRIORITY_DEFAULT, None, sent)

    def _play_file(self, gfile):
        self.stream = Gtk.MediaFile.new_for_file(gfile)
        self.stream.set_muted(True)
        self.widget = Gtk.Video()
        self.widget.set_media_stream(self.stream)
        WIN.set_child(self.widget)
        self.stream.connect('invalidate-contents', lambda *a: self.on_frame())
        self.stream.connect('notify::seeking', self._seeking)
        self.stream.connect('notify::error', lambda s, p: self.on_error(str(s.get_error())))
        self.stream.play()

    def _seeking(self, s, p):
        if not s.is_seeking():
            self.on_seeked()

    def pause(self):
        self.stream.pause()

    def play(self):
        self.stream.play()

    def seek(self, sec):
        self.stream.seek(int(sec * 1e6))

    def teardown(self):
        # Babble's release_media order: pause, drop the widget, let it go.
        self.stream.pause()
        WIN.set_child(None)
        self.widget = self.stream = None


class DownloadCand(GtkMediaFileCand):
    local = True


class GstPlayCand(Base):
    def start(self, uri):
        gi.require_version('GstPlay', '1.0')
        from gi.repository import GstPlay
        self.sink, self.paintable, outer = paintable_sink()
        self.widget = Gtk.Picture(paintable=self.paintable)
        WIN.set_child(self.widget)
        self.play_ = GstPlay.Play(
            video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
        self.adapter = GstPlay.PlaySignalAdapter.new(self.play_)
        self.adapter.connect('seek-done', lambda *a: self.on_seeked())
        self.adapter.connect('error', lambda a, e, d: self.on_error(e.message))
        self.paintable.connect('invalidate-contents', lambda *a: self.on_frame())
        self.play_.set_mute(True)
        self.play_.set_uri(uri)
        self.play_.play()

    def decoders(self):
        return find_decoders(self.play_.get_pipeline())

    def pause(self):
        self.play_.pause()

    def play(self):
        self.play_.play()

    def seek(self, sec):
        self.play_.seek(int(sec * Gst.SECOND))

    def teardown(self):
        self.play_.stop()
        WIN.set_child(None)
        self.adapter = self.play_ = self.widget = self.paintable = self.sink = None


class Playbin3Cand(Base):
    def start(self, uri):
        self.sink, self.paintable, outer = paintable_sink()
        self.widget = Gtk.Picture(paintable=self.paintable)
        WIN.set_child(self.widget)
        self.pb = Gst.ElementFactory.make('playbin3')
        self.pb.props.video_sink = outer
        self.pb.props.mute = True
        self.pb.props.uri = uri
        bus = self.pb.get_bus()
        bus.add_signal_watch()
        self.seeking = False
        bus.connect('message::async-done', self._async_done)
        bus.connect('message::error', lambda b, m: self.on_error(m.parse_error()[0].message))
        self.paintable.connect('invalidate-contents', lambda *a: self.on_frame())
        self.pb.set_state(Gst.State.PLAYING)

    def _async_done(self, *a):
        if self.seeking:
            self.seeking = False
            self.on_seeked()

    def decoders(self):
        return find_decoders(self.pb)

    def pause(self):
        self.pb.set_state(Gst.State.PAUSED)

    def play(self):
        self.pb.set_state(Gst.State.PLAYING)

    def seek(self, sec):
        self.seeking = True
        self.pb.seek_simple(Gst.Format.TIME,
                            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                            int(sec * Gst.SECOND))

    def teardown(self):
        self.pb.get_bus().remove_signal_watch()
        self.pb.set_state(Gst.State.NULL)
        WIN.set_child(None)
        self.pb = self.widget = self.paintable = self.sink = None


class ClapperCand(Base):
    def start(self, uri):
        gi.require_version('Clapper', '0.0')
        gi.require_version('ClapperGtk', '0.0')
        from gi.repository import Clapper, ClapperGtk
        Clapper.init(None)
        self.widget = ClapperGtk.Video()
        self.widget.set_auto_inhibit(False)
        WIN.set_child(self.widget)
        self.player = self.widget.get_player()
        self.player.set_mute(True)
        try:
            self.player.connect('seek-done', lambda *a: self.on_seeked())
        except TypeError:
            self.player.connect('notify::position', self._pos)
        try:
            self.player.connect('error', lambda p, e, d: self.on_error(e.message))
        except TypeError:
            pass
        self.hooked = set()
        GLib.timeout_add(5, self._hook)
        item = Clapper.MediaItem.new(uri)
        q = self.player.get_queue()
        q.add_item(item)
        q.select_item(item)
        self.player.play()

    def _pos(self, *a):
        pass

    def _hook(self):
        # The default sink's paintable lives in a Gtk.Picture inside the widget.
        if self.widget is None:
            return False
        stack = [self.widget]
        while stack:
            w = stack.pop()
            p = w.get_paintable() if isinstance(w, Gtk.Picture) else None
            # The video paintable, not ClapperGtkBufferingPaintable (spinner).
            if p is not None and type(p).__name__ == 'GstClapperPaintable':
                if id(p) not in self.hooked:
                    self.hooked.add(id(p))
                    p.connect('invalidate-contents', lambda *a: self.on_frame())
                    return False
            c = w.get_first_child()
            while c:
                stack.append(c)
                c = c.get_next_sibling()
        return True

    def decoders(self):
        d = self.player.props.current_video_decoder
        return [d.get_factory().get_name()] if d else []

    def pause(self):
        self.player.pause()

    def play(self):
        self.player.play()

    def seek(self, sec):
        self.player.seek(sec)

    def teardown(self):
        self.player.stop()
        WIN.set_child(None)
        self.player = self.widget = None


class WebKitCand(Base):
    def start(self, uri):
        gi.require_version('WebKit', '6.0')
        from gi.repository import WebKit
        self.ucm = WebKit.UserContentManager()
        self.ucm.register_script_message_handler('bench', None)
        self.ucm.connect('script-message-received::bench', self._msg)
        self.widget = WebKit.WebView(user_content_manager=self.ucm)
        self.widget.get_settings().set_media_playback_requires_user_gesture(False)
        WIN.set_child(self.widget)
        html = """<html><body style="margin:0;background:#000">
<video id=v src="%s" autoplay muted playsinline style="width:100%%;height:100%%"></video>
<script>
const v=document.getElementById('v');
const post=m=>window.webkit.messageHandlers.bench.postMessage(m);
function frames(){v.requestVideoFrameCallback(()=>{post('frame');frames();});}
if(v.requestVideoFrameCallback){frames();}else{v.addEventListener('timeupdate',()=>post('frame'));}
v.addEventListener('seeked',()=>post('seeked'));
v.addEventListener('error',()=>post('error:'+(v.error&&v.error.code)));
</script></body></html>""" % uri
        self.widget.load_html(html, BASE)

    def _msg(self, ucm, value):
        s = value.to_string()
        if s == 'frame':
            self.on_frame()
        elif s == 'seeked':
            self.on_seeked()
        elif s.startswith('error'):
            self.on_error(s)

    def _js(self, code):
        self.widget.evaluate_javascript(code, -1, None, None, None, None, None)

    def pause(self):
        self._js('v.pause()')

    def play(self):
        self._js('v.play()')

    def seek(self, sec):
        self._js('v.currentTime=%f' % sec)

    def teardown(self):
        WIN.set_child(None)
        self.widget.try_close()
        self.widget = self.ucm = None


class MpvCand(Base):
    def start(self, uri):
        import mpv
        self.mpv_mod = mpv
        self.player = mpv.MPV(vo='libmpv', mute=True, hwdec='auto-safe',
                              keep_open='yes')
        self.widget = Gtk.GLArea()
        self.widget.set_auto_render(False)
        self.widget.connect('realize', self._realize)
        self.widget.connect('render', self._render)
        self.widget.connect('unrealize', self._unrealize)
        self.ctx = None
        self.uri = uri
        self.player.event_callback('playback-restart')(
            lambda ev: GLib.idle_add(self._restart))
        self.restarts = 0
        WIN.set_child(self.widget)

    def _restart(self):
        self.restarts += 1
        if self.restarts > 1:
            self.on_seeked()
        return False

    def _realize(self, area):
        area.make_current()
        egl = ctypes.CDLL('libEGL.so.1')
        egl.eglGetProcAddress.restype = ctypes.c_void_p
        egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
        self._gpa = self.mpv_mod.MpvGlGetProcAddressFn(
            lambda _ctx, name: egl.eglGetProcAddress(name) or 0)
        self.ctx = self.mpv_mod.MpvRenderContext(
            self.player, 'opengl', opengl_init_params={'get_proc_address': self._gpa})
        self.ctx.update_cb = lambda: GLib.idle_add(self._queue)
        self.gl = ctypes.CDLL('libOpenGL.so.0')
        self.player.play(self.uri)

    def _queue(self):
        if self.widget is not None:
            self.widget.queue_render()
        return False

    def _render(self, area, context):
        if self.ctx is None:
            return True
        fbo = ctypes.c_int(0)
        self.gl.glGetIntegerv(0x8CA6, ctypes.byref(fbo))
        scale = area.get_scale_factor()
        w, h = area.get_width() * scale, area.get_height() * scale
        if self.ctx.update():
            self.ctx.render(flip_y=True, opengl_fbo={'w': w, 'h': h, 'fbo': fbo.value})
            if self.player.time_pos is not None:
                self.on_frame()
        return True

    def _unrealize(self, area):
        area.make_current()
        if self.ctx is not None:
            self.ctx.free()
            self.ctx = None

    def decoders(self):
        return [str(self.player.hwdec_current or 'sw')]

    def pause(self):
        self.player.pause = True

    def play(self):
        self.player.pause = False

    def seek(self, sec):
        self.player.seek(sec, 'absolute')

    def teardown(self):
        WIN.set_child(None)
        self.player.terminate()
        self.player = self.widget = None


CANDS = {'gtkmediafile': GtkMediaFileCand, 'download': DownloadCand,
         'gstplay': GstPlayCand, 'playbin3': Playbin3Cand,
         'clapper': ClapperCand, 'webkit': WebKitCand, 'mpv': MpvCand}

# ---------------------------------------------------------------- drivers
WIN = Gtk.Window(default_width=1280, default_height=720)
WIN.present()
LOOP = GLib.MainLoop()
RESULT = {'candidate': CAND, 'mode': MODE}


def finish(**kw):
    RESULT.update(kw)
    LOOP.quit()


def after(ms, fn):
    GLib.timeout_add(ms, lambda: (fn(), False)[1])


def single():
    c = CANDS[CAND]()
    errors = []
    state = {'ff': None, 'seek_t': None, 'frames': 0, 'seek_frame': False}

    def on_error(msg):
        errors.append(msg)
        if state['ff'] is None:
            finish(error=msg)
    c.on_error = on_error

    def on_frame():
        state['frames'] += 1
        if state['ff'] is None:
            state['ff'] = time.monotonic() - t0
            b, req, rng = stats()
            RESULT.update(ttff_ms=round(state['ff'] * 1000), bytes_at_ff=b,
                          rss_ff_mb=rss_mb())
            state['cpu0'] = cpu_s()
            state['frames0'] = state['frames']
            after(5000, played)
        elif state['seek_t'] is not None and not state['seek_frame']:
            state['seek_frame'] = True
            RESULT['seek_ms'] = round((time.monotonic() - state['seek_t']) * 1000)
            after(1000, teardown)
    c.on_frame = on_frame

    def on_seeked():
        if state['seek_t'] is not None and 'seek_done_ms' not in RESULT:
            RESULT['seek_done_ms'] = round((time.monotonic() - state['seek_t']) * 1000)
    c.on_seeked = on_seeked

    def played():
        RESULT['cpu_5s_play_s'] = round(cpu_s() - state['cpu0'], 2)
        RESULT['frames_5s'] = state['frames'] - state['frames0']
        RESULT['rss_play_mb'] = rss_mb()
        RESULT['decoders'] = c.decoders()
        sink = getattr(c, 'sink', None)
        if sink is not None:
            st = sink.props.stats
            RESULT['sink_rendered'] = st.get_value('rendered')
            RESULT['sink_dropped'] = st.get_value('dropped')
        c.pause()
        state['p0'] = stats()[0]
        after(3000, paused)

    def paused():
        RESULT['bytes_while_paused_3s'] = stats()[0] - state['p0']
        c.play()
        state['seek_t'] = time.monotonic()
        c.seek(SEEK_TO)
        after(10000, lambda: 'seek_ms' in RESULT or (RESULT.update(seek_ms=None), teardown()))

    def teardown():
        if 'rss_after_teardown_mb' in RESULT or 'teardown' in state:
            return
        state['teardown'] = True
        b, req, rng = stats()
        RESULT.update(bytes_total=b, http_requests=req, range_requests=rng)
        c.teardown()
        gc.collect()
        after(1500, lambda: finish(rss_after_teardown_mb=rss_mb(), errors=errors[:3]))

    reset()
    t0 = time.monotonic()
    c.start(LIVE or BASE + 'video.mp4')
    after(30000, lambda: state['ff'] is None and finish(error='no first frame in 30 s'))


def cycles():
    n = 12
    state = {'i': 0, 'fails': 0, 'ttffs': []}
    RESULT['rss_start_mb'] = rss_mb()

    def one():
        if state['i'] == n:
            gc.collect()
            after(1500, lambda: finish(
                cycles=n, failed_cycles=state['fails'],
                ttff_ms_each=state['ttffs'], rss_end_mb=rss_mb()))
            return
        state['i'] += 1
        c = CANDS[CAND]()
        got = {'ff': False}
        t0 = time.monotonic()

        def on_frame():
            if not got['ff']:
                got['ff'] = True
                state['ttffs'].append(round((time.monotonic() - t0) * 1000))
                after(1000, done)
        c.on_frame = on_frame

        def done():
            if got.get('done'):
                return
            got['done'] = True
            if not got['ff']:
                state['fails'] += 1
            c.teardown()
            gc.collect()
            if state['i'] == 1:
                RESULT['rss_after_1_mb'] = rss_mb()
            after(300, one)
        after(10000, done)
        c.start(BASE + 'gifv.mp4')

    after(500, one)


after(300, single if MODE == 'single' else cycles)
after(120000, lambda: finish(error='global timeout'))
LOOP.run()
print(json.dumps(RESULT), flush=True)
os._exit(0)
