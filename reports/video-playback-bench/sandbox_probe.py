"""Run inside org.gnome.Platform//51: can GIO read https, does GtkMediaFile
play it, and does GstPlay + gtk4paintablesink play it?"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstPlay', '1.0')
from gi.repository import GLib, Gio, Gst, GstPlay, Gtk  # noqa

Gst.init(None)
url = sys.argv[1]
f = Gio.File.new_for_uri(url)
print('Gio.File type:', type(f).__name__)
try:
    f.read(None)
    print('GIO read: OK')
except GLib.Error as e:
    print('GIO read: FAILED -', e.message)

win = Gtk.Window()
win.present()
loop = GLib.MainLoop()
res = {}

mf = Gtk.MediaFile.new_for_file(f)
mf.set_muted(True)
win.set_child(Gtk.Video(media_stream=mf))
mf.connect('notify::error', lambda s, p: res.setdefault('gtk', 'ERROR: %s' % s.get_error().message))
mf.connect('invalidate-contents', lambda *a: res.setdefault('gtk', 'frame OK'))
mf.play()


def gstplay():
    sink = Gst.ElementFactory.make('gtk4paintablesink')
    pic = Gtk.Picture(paintable=sink.props.paintable)
    win.set_child(pic)
    play = GstPlay.Play(video_renderer=GstPlay.PlayVideoOverlayVideoRenderer.new_with_sink(None, sink))
    ad = GstPlay.PlaySignalAdapter.new(play)
    ad.connect('error', lambda a, e, d: res.setdefault('gstplay', 'ERROR: %s' % e.message))
    sink.props.paintable.connect('invalidate-contents', lambda *a: res.setdefault('gstplay', 'frame OK'))
    play.set_mute(True)
    play.set_uri(url)
    play.play()
    res['_play'] = play
    GLib.timeout_add(8000, lambda: (loop.quit(), False)[1])
    return False


GLib.timeout_add(8000, gstplay)
loop.run()
print('GtkMediaFile:', res.get('gtk', 'no frame, no error in 8 s'))
print('GstPlay+gtk4paintablesink:', res.get('gstplay', 'no frame, no error in 8 s'))
