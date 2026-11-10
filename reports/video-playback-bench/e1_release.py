"""E1: does a per-media GstPlay survive release if we wait for STOPPED and
drop the last reference from an idle callback?

e1_release.py URI CYCLES MODE [FIRST_FRAME_TIMEOUT_S]
  MODE deferred : stop(), wait for state-changed == STOPPED, disconnect
                  tracked handlers, drop references in GLib.idle_add
  MODE immediate: stop(), disconnect, drop references at once (control)

Prints one line per cycle and a summary. Each GstPlay is confirmed
finalised through a GObject weak ref, so "no crash" cannot come from a
leak (the earlier per-media wrapper only survived while it leaked).
"""
import gc
import os
import sys
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
from gi.repository import GLib, Gst, GstPlay, Gtk  # noqa: E402

Gst.init(None)
URI, CYCLES, MODE = sys.argv[1], int(sys.argv[2]), sys.argv[3]
FF_TIMEOUT_MS = int(float(sys.argv[4] if len(sys.argv) > 4 else 3) * 1000)
HOLD_MS = 1000          # keep playing after the first frame: timers active
STOP_TIMEOUT_MS = 3000

win = Gtk.Window(default_width=800, default_height=600)
win.present()
loop = GLib.MainLoop()
S = {'i': 0, 'frames': 0, 'no_frame': 0, 'stopped_seen': 0,
     'stop_timeouts': 0, 'finalized': 0, 'created': 0, 'stop_ms': []}


def rss():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('VmRSS'):
                return int(line.split()[1]) // 1024


def on_finalized(*_args):
    S['finalized'] += 1


def one():
    if S['i'] == CYCLES:
        GLib.timeout_add(500, summary)
        return False
    S['i'] += 1
    c = {'handlers': [], 'done': False}
    sink = Gst.ElementFactory.make('gtk4paintablesink')
    paintable = sink.props.paintable
    outer = sink
    if paintable.props.gl_context:
        outer = Gst.ElementFactory.make('glsinkbin')
        outer.props.sink = sink
    play = GstPlay.Play(
        video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
    adapter = GstPlay.PlaySignalAdapter.new(play)
    play.weak_ref(on_finalized)
    S['created'] += 1
    c.update(sink=sink, paintable=paintable, play=play, adapter=adapter)

    def track(obj, sig, cb):
        c['handlers'].append((obj, obj.connect(sig, cb)))

    def first_frame(*_a):
        if 'ff' not in c:
            c['ff'] = True
            S['frames'] += 1
            GLib.timeout_add(HOLD_MS, release)

    def state_changed(_ad, state):
        if c.get('stopping') and state == GstPlay.PlayState.STOPPED \
                and not c.get('stopped'):
            c['stopped'] = True
            S['stopped_seen'] += 1
            S['stop_ms'].append(round((time.monotonic() - c['t_stop']) * 1000))
            finish()

    track(paintable, 'invalidate-contents', first_frame)
    track(adapter, 'state-changed', state_changed)
    track(adapter, 'error', lambda *_a: None)

    pic = Gtk.Picture(paintable=paintable)
    win.set_child(pic)
    play.set_mute(True)
    play.set_uri(URI)
    play.play()

    def no_frame():
        if 'ff' not in c and not c['done']:
            S['no_frame'] += 1
            release()
        return False
    GLib.timeout_add(FF_TIMEOUT_MS, no_frame)

    def release():
        if c.get('stopping'):
            return False
        c['stopping'] = True
        win.set_child(None)
        c['t_stop'] = time.monotonic()
        c['play'].stop()
        if MODE == 'immediate':
            finish()
        else:
            def stop_timeout():
                if not c.get('stopped') and not c['done']:
                    S['stop_timeouts'] += 1
                    finish()
                return False
            GLib.timeout_add(STOP_TIMEOUT_MS, stop_timeout)
        return False

    def finish():
        if c['done']:
            return
        c['done'] = True
        for obj, hid in c['handlers']:
            obj.disconnect(hid)
        c['handlers'].clear()

        def drop():
            for k in ('adapter', 'play', 'paintable', 'sink'):
                c.pop(k, None)
            gc.collect()
            print('cycle %d frame=%s stopped=%s finalized=%d/%d rss=%d' % (
                S['i'], 'ff' in c, c.get('stopped', False),
                S['finalized'], S['created'], rss()), flush=True)
            GLib.timeout_add(150, one)
            return False

        if MODE == 'deferred':
            GLib.idle_add(drop)
        else:
            drop()


def summary():
    gc.collect()
    ms = sorted(S['stop_ms'])
    print('SUMMARY mode=%s cycles=%d first_frames=%d no_frame=%d '
          'stopped_seen=%d stop_timeouts=%d finalized=%d/%d rss=%d '
          'stop_ms_min=%s stop_ms_max=%s' % (
              MODE, S['i'], S['frames'], S['no_frame'], S['stopped_seen'],
              S['stop_timeouts'], S['finalized'], S['created'], rss(),
              ms[0] if ms else None, ms[-1] if ms else None), flush=True)
    loop.quit()
    return False


GLib.timeout_add(300, one)
loop.run()
os._exit(0)
