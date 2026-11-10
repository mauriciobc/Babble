import sys, types, time, gc, weakref
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, GLib, Gst
pkg = types.ModuleType('pk'); pkg.__path__ = []; sys.modules['pk'] = pkg
i18n = types.ModuleType('pk.i18n'); i18n.gettext = lambda s: s; sys.modules['pk.i18n'] = i18n
for name, src in SOURCES.items():
    m = types.ModuleType('pk.' + name); m.__package__ = 'pk'; m.__file__ = name + '.py'
    sys.modules['pk.' + name] = m
    exec(compile(src, name + '.py', 'exec'), m.__dict__)
me = sys.modules['pk.mediaengine']; ms = sys.modules['pk.mediastream']
URL = 'https://mastodon.me.uk/system/media_attachments/files/117/581/332/107/689/033/original/13af5d8053337ede.mp4'
T0 = time.monotonic()
def log(m): print('%6.2fs %s' % (time.monotonic() - T0, m), flush=True)
res = {}
class App(Adw.Application):
    def __init__(s):
        super().__init__(application_id='x.Sandbox', flags=32)
    def do_activate(s):
        win = Adw.ApplicationWindow(application=s, default_width=800, default_height=600)
        video = Gtk.Video(); win.set_content(video); win.present()
        eng = me.Engine(); res['eng'] = weakref.ref(eng)
        stream = ms.EngineStream(eng)
        video.set_media_stream(stream)
        # first frame
        eng.paintable.connect('invalidate-contents', lambda *a: res.setdefault('frame', time.monotonic() - T0))
        eng.load(URL); stream.play()
        s.eng, s.stream, s.video = eng, stream, video
        GLib.timeout_add(100, s.poll)
        GLib.timeout_add(30000, s.fail)
    def sinks(s):
        out = []
        def walk(e):
            it = e.iterate_recurse()
            while True:
                r, x = it.next()
                if r == Gst.IteratorResult.OK:
                    out.append(x.get_factory().get_name() if x.get_factory() else x.get_name())
                elif r == Gst.IteratorResult.RESYNC:
                    it.resync()
                else:
                    break
        walk(s.eng.play.get_pipeline()); return out
    def poll(s):
        if 'frame' in res and 'seek' not in res:
            log('first frame at %.2fs prepared=%s dur_us=%s' % (res['frame'], s.stream.is_prepared(), s.stream.get_duration()))
            log('elements: ' + ','.join(sorted(set(s.sinks()))))
            res['seek'] = 0; s.stream.seek(10_000_000); s.t = time.monotonic()
            GLib.timeout_add(3000, s.after_seek)
        return 'done' not in res
    def after_seek(s):
        log('after seek ts_us=%s playing=%s error=%s' % (s.stream.get_timestamp(), s.stream.get_playing(), s.stream.get_error()))
        pipe = s.eng.play.get_pipeline()
        log('audio sinks: ' + ','.join(sorted(x for x in s.sinks() if 'sink' in x)))
        s.stream.detach(); s.video.set_media_stream(None)
        s.eng.release(); s.eng = None; s.stream = None
        GLib.timeout_add(1500, s.check_drop)
    def check_drop(s):
        gc.collect()
        log('engine dropped: %s  releasing=%d all=%d' % (res['eng']() is None, len(me._releasing), len(me._all)))
        res['done'] = 1; s.quit()
    def fail(s):
        log('TIMEOUT no result'); s.quit(); return False
App().run([])
me.shutdown_all(); log('exit clean')
