"""Which source element does Gtk.MediaFile pick for an http(s) URI, and
what video sink does ClapperGtk.Video use by default?"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('Clapper', '0.0')
gi.require_version('ClapperGtk', '0.0')
from gi.repository import GLib, Gio, Gst, Gtk, Clapper, ClapperGtk  # noqa

Gst.init(None)
Clapper.init(None)
url = sys.argv[1]
win = Gtk.Window()
win.present()
loop = GLib.MainLoop()

mf = Gtk.MediaFile.new_for_file(Gio.File.new_for_uri(url))
print('GtkMediaFile impl:', type(mf).__name__, GObject_name := mf.__gtype__.name)
mf.set_muted(True)
win.set_child(Gtk.Video(media_stream=mf))
mf.play()


def clapper():
    win.set_child(None)
    v = ClapperGtk.Video()
    win.set_child(v)
    p = v.get_player()
    print('Clapper video-sink before play:', p.props.video_sink)
    item = Clapper.MediaItem.new(url)
    p.get_queue().add_item(item)
    p.get_queue().select_item(item)
    p.set_mute(True)
    p.play()

    def report():
        s = p.props.video_sink
        print('Clapper video-sink after play:', s and s.get_factory().get_name(),
              '| has paintable prop:', bool(s and s.find_property('paintable')))
        stack = [v]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Picture):
                print('  Picture in widget, paintable:', type(w.get_paintable()).__name__)
            c = w.get_first_child()
            while c:
                stack.append(c)
                c = c.get_next_sibling()
        loop.quit()
    GLib.timeout_add(3000, lambda: (report(), False)[1])
    return False


GLib.timeout_add(3000, clapper)
loop.run()
