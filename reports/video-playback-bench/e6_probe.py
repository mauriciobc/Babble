"""E6 probes, meant to run inside org.gnome.Platform//51 (script on stdin).

python3 - MODE ARGS...
  libs                      which libavcodec decodes h264/hevc (base vs codecs-extra)
  decode BASE NAME DEC      decode-only throughput of NAME through decoder DEC
                            (or 'auto' = decodebin3's choice); CPU and wall time
  audio                     what autoaudiosink picks, and whether it errors
  play URI SEEK_S           GstPlay + gtk4paintablesink (needs a display): first
                            frame, seek to SEEK_S, report position after the seek
"""
import ctypes
import os
import resource
import sys
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst  # noqa: E402

Gst.init(None)
MODE = sys.argv[1]


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def run_pipeline(desc, timeout_s=60):
    print('PIPELINE %s' % desc, file=sys.stderr)
    p = Gst.parse_launch(desc)
    bus = p.get_bus()
    p.set_state(Gst.State.PLAYING)
    msg = bus.timed_pop_filtered(int(timeout_s * Gst.SECOND),
                                 Gst.MessageType.EOS | Gst.MessageType.ERROR)
    result = 'timeout'
    if msg is not None and msg.type == Gst.MessageType.EOS:
        result = 'eos'
    elif msg is not None:
        result = 'error: ' + msg.parse_error()[0].message
    return p, result


if MODE == 'libs':
    for label, path in (('base', '/usr/lib/x86_64-linux-gnu/libavcodec.so.62'),
                        ('codecs-extra',
                         '/usr/lib/x86_64-linux-gnu/codecs-extra/lib/libavcodec.so.62')):
        try:
            lib = ctypes.CDLL(path, mode=os.RTLD_LOCAL)
            lib.avcodec_find_decoder_by_name.restype = ctypes.c_void_p
            have = {n: bool(lib.avcodec_find_decoder_by_name(n.encode()))
                    for n in ('h264', 'hevc', 'vp9', 'av1')}
            print('LIBS %s %s' % (label, ' '.join('%s=%s' % kv for kv in have.items())))
        except OSError as e:
            print('LIBS %s missing (%s)' % (label, e))
    for el in ('avdec_h264', 'avdec_h265', 'vah264dec', 'vah265dec', 'nvh264dec',
               'nvh265dec', 'openh264dec'):
        print('ELEMENT %s %s' % (el, 'yes' if Gst.ElementFactory.find(el) else 'no'))
    Gst.ElementFactory.make('avdec_h265')
    with open('/proc/self/maps') as f:
        loaded = sorted({l.split()[-1] for l in f if 'libavcodec' in l})
    print('LOADED libavcodec: %s' % ', '.join(loaded))

elif MODE == 'decode':
    base, name, dec = sys.argv[2], sys.argv[3], sys.argv[4]
    parse = 'h265parse' if '265' in name else 'h264parse'
    chain = 'decodebin3' if dec == 'auto' else '%s ! %s' % (parse, dec)
    desc = 'souphttpsrc location=%s%s ! qtdemux ! %s ! fakesink sync=false name=out' % (
        base, name, chain)
    c0, t0 = cpu(), time.monotonic()
    p, result = run_pipeline(desc)
    wall, cpu_s = time.monotonic() - t0, cpu() - c0
    used = []
    it = p.iterate_recurse()
    while True:
        ok, el = it.next()
        if ok != Gst.IteratorResult.OK:
            break
        f = el.get_factory()
        if f and 'Decoder' in (f.get_metadata('klass') or ''):
            used.append(f.get_name())
    rendered = p.get_by_name('out').props.stats.get_value('rendered')
    p.set_state(Gst.State.NULL)
    print('DECODE file=%s dec=%s used=%s result=%s frames=%d wall_s=%.2f cpu_s=%.2f '
          'cpu_ms_per_frame=%.2f' % (name, dec, ','.join(sorted(set(used))), result,
                                     rendered, wall, cpu_s, cpu_s * 1000 / max(rendered, 1)))

elif MODE == 'audio':
    p = Gst.parse_launch('audiotestsrc num-buffers=50 volume=0 ! autoaudiosink name=out')
    bus = p.get_bus()
    p.set_state(Gst.State.PLAYING)
    msg = bus.timed_pop_filtered(10 * Gst.SECOND,
                                 Gst.MessageType.EOS | Gst.MessageType.ERROR)
    out = p.get_by_name('out')
    child = None
    it = out.iterate_elements()
    while True:
        ok, el = it.next()
        if ok != Gst.IteratorResult.OK:
            break
        child = el.get_factory().get_name()
    result = 'timeout' if msg is None else (
        'eos' if msg.type == Gst.MessageType.EOS else
        'error: ' + msg.parse_error()[0].message)
    p.set_state(Gst.State.NULL)
    print('AUDIO pulse_socket=%s sink=%s result=%s' % (
        os.path.exists('/run/flatpak/pulse/native') or
        os.path.exists(os.path.join(os.environ.get('XDG_RUNTIME_DIR', ''), 'pulse/native')),
        child, result))

elif MODE == 'play':
    gi.require_version('Gtk', '4.0')
    gi.require_version('GstPlay', '1.0')
    from gi.repository import GstPlay, Gtk
    uri, seek_s = sys.argv[2], float(sys.argv[3])
    win = Gtk.Window()
    win.present()
    loop = GLib.MainLoop()
    sink = Gst.ElementFactory.make('gtk4paintablesink')
    paintable = sink.props.paintable
    outer = sink
    if paintable.props.gl_context:
        outer = Gst.ElementFactory.make('glsinkbin')
        outer.props.sink = sink
    win.set_child(Gtk.Picture(paintable=paintable))
    play = GstPlay.Play(video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, outer))
    ad = GstPlay.PlaySignalAdapter.new(play)
    R = {'t0': time.monotonic(), 'err': None, 'ff_ms': None, 'seek_done': False}
    ad.connect('error', lambda a, e, d: R.update(err=e.message) or loop.quit())
    ad.connect('seek-done', lambda *a: R.update(seek_done=True))

    def ff(*_a):
        if R['ff_ms'] is None:
            R['ff_ms'] = round((time.monotonic() - R['t0']) * 1000)
            GLib.timeout_add(1000, do_seek)
    paintable.connect('invalidate-contents', ff)

    def do_seek():
        play.seek(int(seek_s * Gst.SECOND))
        GLib.timeout_add(3000, report)
        return False

    def report():
        pos = play.get_position()
        R['pos_s'] = round(pos / Gst.SECOND, 2) if pos != Gst.CLOCK_TIME_NONE else None
        loop.quit()
        return False
    play.set_mute(True)
    play.set_uri(uri)
    play.play()
    GLib.timeout_add(20000, lambda: (loop.quit(), False)[1])
    loop.run()
    print('PLAY uri=%s first_frame_ms=%s seek_to=%s pos_after_seek_s=%s seek_done=%s error=%s'
          % (uri, R['ff_ms'], seek_s, R.get('pos_s'), R['seek_done'], R['err']),
          flush=True)
    play.stop()
    os._exit(0)
