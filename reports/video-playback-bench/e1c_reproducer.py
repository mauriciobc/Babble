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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from streamwrap import GLib, GstPlay, Gtk, PlayStream  # noqa: E402

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
