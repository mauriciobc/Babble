import gc, sys, os
sys.argv = sys.argv
from streamwrap import *
uri = sys.argv[1]; n = int(sys.argv[2])
win = Gtk.Window(default_width=800, default_height=600); win.present()
loop = GLib.MainLoop(); state = {"i": 0, "ok": 0}
def rss():
    for l in open("/proc/self/status"):
        if l.startswith("VmRSS"): return int(l.split()[1]) // 1024
def one():
    if state["i"] == n:
        gc.collect(); print("cycles", n, "with first frame", state["ok"], "rss_mb", rss(), flush=True); loop.quit(); return False
    state["i"] += 1
    s = PlayStream(uri); s.set_muted(True)
    v = Gtk.Video(media_stream=s, autoplay=True); win.set_child(v)
    got = {}
    s._paintable.connect("invalidate-contents", lambda *a: got.setdefault("f", True))
    def done():
        if got: state["ok"] += 1
        # teardown order: stop the engine, then drop the widget
        s.pause(); s.shutdown(); win.set_child(None); gc.collect()
        if state["i"] == 1: print("rss_after_1_mb", rss(), flush=True)
        GLib.timeout_add(300, one); return False
    GLib.timeout_add(1500, done); return False
GLib.timeout_add(300, one)
loop.run()
os._exit(0)
