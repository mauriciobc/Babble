#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""EngineStream: a Gtk.MediaStream driving a mediaengine.Engine.

Under a real ``Gtk.Video``: prepared from the preroll (no duration
needed), autoplay, playing/ended/error, seeking by ``seek-done`` and by
the fallback timeout, a source that cannot seek, ``detach()`` making
every vfunc a no-op, a looping GIFV in a ``Gtk.Picture``, audio in
``Gtk.MediaControls``, and a missing H.264 decoder naming codecs-extra.
"""

import faulthandler
import importlib.util
import os
import threading
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import time

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')

from gi.repository import Adw, GLib, Gst, GstPbutils, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

Adw.init()

from src import mediaengine, mediastream  # noqa: E402

spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

faulthandler.dump_traceback_later(120, exit=True)
failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


def wait(condition, timeout=10.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


window = Gtk.Window(default_width=400, default_height=300)
window.present()
URI = 'file://' + fixture.write_clip()
live = []


def make(uri=URI, muted=True):
    engine = mediaengine.Engine()
    stream = mediastream.EngineStream(engine)
    stream.set_muted(muted)
    engine.load(uri)
    live.append((engine, stream))
    return engine, stream


# Prepared from the preroll, autoplay under Gtk.Video.
engine, stream = make()
video = Gtk.Video(media_stream=stream, autoplay=True)
window.set_child(video)
check('prepared from the preroll', wait(stream.is_prepared))
check('the preroll found a video',
      stream.has_video())
check('seekable comes from the media info', stream.is_seekable())
check('Gtk.Video autoplays', wait(stream.get_playing))
check('the stream reports a position', wait(lambda: stream.get_timestamp() > 0)
      or stream.get_ended())
check('the video has a size', stream.get_intrinsic_width() > 0)

# Seeking: seek-done.
if stream.get_ended():
    stream.seek(0)
else:
    stream.seek(100000)
check('a seek completes through seek-done',
      wait(lambda: not stream.get_property('seeking')))
check('the clip ends', wait(stream.get_ended, 15))
check('an ended stream is not playing', not stream.get_playing())

# Seeking: fallback when the engine never reports.
engine.seek = lambda position: None
stream.seek(0)
check('a seek the engine never reports is given up on',
      wait(lambda: not stream.get_property('seeking'), 5))
del engine.seek

# A source that cannot seek: do_seek never reaches the engine.
calls = []
engine.seek = lambda position: calls.append(position)
stream._seekable = False
try:
    stream.seek(500000)
except Exception as error:  # noqa: BLE001
    calls.append(error)
check('no seek is forwarded when not seekable', not calls, repr(calls))
wait(lambda: not stream.get_property('seeking'), 2)
del engine.seek

# Error: a file that is not there.
engine, bad = make('file:///nonexistent/babble.ogg')
check('an unreadable source reports an error',
      wait(lambda: bad.get_error() is not None))

# Missing plugin: H.264 names codecs-extra, others the codec.
Gst.init(None)
caps = Gst.Caps.from_string('video/x-h264')
message = GstPbutils.missing_decoder_message_new(Gst.Pipeline(), caps)
description = GstPbutils.missing_plugin_message_get_description(message)
detail = GstPbutils.missing_plugin_message_get_installer_detail(message)
engine, missing = make()
missing._engine_event('missing-plugin', description, detail)
error = missing.get_error()
check('a missing H.264 decoder names codecs-extra',
      error is not None and 'codecs-extra' in error.message,
      error.message if error else 'no error')
check('the message holds no GStreamer element name',
      error is not None and 'avdec' not in error.message
      and 'ffmpeg' not in error.message.lower())
check('another missing codec names the codec',
      'Vorbis' in mediastream.missing_plugin_message('Vorbis decoder', 'x'))

# A real missing-decoder message on the pipeline bus: it may be handled on
# GstPlay's thread, but the owner must hear of it on the main thread.
engine, posted = make()
wait(posted.is_prepared)
threads = []
original = posted._engine_event


def record(name, *args):
    threads.append((name, threading.current_thread()))
    original(name, *args)


posted._engine_event = record
pipeline = engine.play.get_pipeline()
pipeline.post_message(GstPbutils.missing_decoder_message_new(
    pipeline, Gst.Caps.from_string('video/x-h264')))
check('a missing decoder posted on the bus reaches the stream',
      wait(lambda: posted.get_error() is not None))
check('... naming codecs-extra',
      posted.get_error() is not None
      and 'codecs-extra' in posted.get_error().message)
check('... and heard on the main thread',
      any(n == 'missing-plugin' for n, _t in threads)
      and all(t is threading.main_thread() for n, t in threads
              if n == 'missing-plugin'), repr(threads))

# Seek fallback: only a position near the target completes the seek.
engine, seeker = make()
wait(seeker.is_prepared)
engine.seek = lambda position: None
seeker.seek(5000000)
seeker._engine_event('position', 4600 * 1000 * 1000)
check('a position outside 300 ms of the target does not complete it',
      seeker.get_property('seeking'))
check('the seek is under way', seeker.get_property('seeking'))
seeker._engine_event('position', 0)
check('a stale pre-seek position does not complete the seek',
      seeker.get_property('seeking'))
seeker._engine_event('position', 4800 * 1000 * 1000)
check('a position near the target completes it',
      not seeker.get_property('seeking'))
del engine.seek

# Unknown duration: CLOCK_TIME_NONE is 0, and a later real one is kept.
NONE = 0xFFFFFFFFFFFFFFFF


class Info:
    def __init__(self, duration, uri='fake://clip'):
        self.duration = duration
        self.uri = uri

    def get_uri(self):
        return self.uri

    def get_duration(self):
        return self.duration

    def is_seekable(self):
        return True

    def get_audio_streams(self):
        return []

    def get_video_streams(self):
        return [object()]


engine = mediaengine.Engine()
unknown = mediastream.EngineStream(engine)
live.append((engine, unknown))
engine.uri = 'fake://clip'
check('CLOCK_TIME_NONE converts to 0', mediastream._to_us(NONE) == 0
      and mediastream._to_us(-5) == 0 and mediastream._to_us(None) == 0
      and mediastream._to_us(2000000000) == 2000000)
unknown._engine_event('media-info', Info(NONE))
check('an unknown duration prepares the stream with 0',
      unknown.is_prepared() and unknown.get_duration() == 0,
      str(unknown.get_duration()))
unknown._engine_event('duration', NONE)
check('a later unknown duration changes nothing',
      unknown.get_duration() == 0)
unknown._engine_event('duration', 2000000000)
check('a real duration arriving later is taken',
      unknown.is_prepared() and unknown.get_duration() == 2000000,
      str(unknown.get_duration()))

# A deferred load: media A's queued info must not prepare B's stream.
engine, first = make()
wait(first.is_prepared)
engine.stop()
second = mediastream.EngineStream(engine)
live.append((engine, second))
engine.load('fake://B')
second._engine_event('media-info', Info(5000000000, 'file://x/A.ogg'))
check('the old media\'s info does not prepare the new stream',
      not second.is_prepared() and engine.uri == 'fake://B')

# detach(): every vfunc is a no-op.
engine, detached = make()
wait(detached.is_prepared)
detached.detach()
spy = []
for name in ('start', 'pause', 'seek', 'set_audio'):
    setattr(engine, name, lambda *a, _n=name: spy.append(_n))
detached.do_play()
detached.do_pause()
detached.do_seek(1000)
detached.do_update_audio(True, 0.5)
detached.do_snapshot(Gtk.Snapshot(), 10, 10)
check('after detach() no vfunc reaches the engine', not spy, repr(spy))
check('after detach() the intrinsic size is empty',
      detached.get_intrinsic_width() == 0
      and detached.get_intrinsic_height() == 0)
check('after detach() the engine has no owner', engine.owner is None)

# GIFV: muted, looping, in a Gtk.Picture.
engine, gifv = make()
gifv.set_loop(True)
picture = Gtk.Picture(paintable=gifv)
window.set_child(picture)
wait(gifv.is_prepared)
gifv.play()
check('a GIFV plays', wait(gifv.get_playing))
wait(lambda: False, 2.5)
check('a looping GIFV outlives the clip',
      gifv.get_playing() and not gifv.get_ended())

# Audio: the stream under Gtk.MediaControls.
engine, audio = make()
controls = Gtk.MediaControls(media_stream=audio)
window.set_child(controls)
check('the stream prepares under Gtk.MediaControls', wait(audio.is_prepared))
check('the controls hold the stream', controls.get_media_stream() is audio)

# Tear everything down the way the viewer does.
window.set_child(None)
for engine, stream in live:
    stream.detach()
    engine.release()
check('every engine is released',
      wait(lambda: not mediaengine._releasing and not mediaengine._all, 5),
      '%d left' % len(mediaengine._all))

faulthandler.cancel_dump_traceback_later()
if failures:
    print('\n%d media stream check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media stream checks passed')
