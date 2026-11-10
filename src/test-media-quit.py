#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quitting while a video plays.

A playing GstPlay freed by interpreter teardown crashed 17 quits in 50.
``BabbleApplication.do_shutdown`` therefore calls
``mediaengine.shutdown_all()``, which stops every engine and turns the
main context until STOPPED. This test starts an application the same way
(a viewer playing a looping GIFV, then ``app.quit()``) in a child
process, ten times, and asserts each exits 0 with every engine dropped.
It also checks that ``main.py`` really makes the call.
"""

import ast
import importlib.util
import os
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import subprocess
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
RUNS = 10


def load_fixture():
    spec = importlib.util.spec_from_file_location(
        'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture


def child():
    # A hung child would otherwise only show as the parent's timeout: dump
    # every thread's Python stack and exit before that. 25 s leaves the
    # 15 s fallback quit room, and ten hung children still finish inside
    # meson's 300 s, so the dumps reach the log (flushed: a SIGTERM from
    # meson would drop buffered output).
    import faulthandler
    faulthandler.dump_traceback_later(25, exit=True)
    # The viewer's loop gate builds sysstate's monitor, whose
    # Gio.NetworkMonitor.get_default() hung about 1 child in 10 on the CI
    # runner (no NetworkManager in the container; the dump stopped in
    # SystemState.__init__). This test is about quitting, so give GIO its
    # trivial monitor: always online, never metered.
    os.environ['GIO_USE_NETWORK_MONITOR'] = 'base'
    sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
    sys.path.insert(0, srcdir)
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gio, GLib, Gtk
    from src import mediaengine, mediaplayback, mediaviewer
    mediaplayback._system_state = lambda name: False
    fixture = load_fixture()
    # Range-capable, like a real media server: since the viewer streams,
    # souphttpsrc refuses a server without Range (GStreamer 1.28.9) and
    # an Ogg clip never prerolls.
    server = fixture.MediaServer(fixture.write_clip())
    result = {}

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.mauriciobc.QuitTest',
                             flags=Gio.ApplicationFlags.NON_UNIQUE)

        def do_activate(self):
            window = Adw.ApplicationWindow(
                application=self, content=Gtk.Overlay(),
                default_width=800, default_height=600)
            window.present()
            viewer = mediaviewer.present_media(
                window.get_content(),
                [{'type': 'gifv', 'url': server.base + '/media.ogg'}])
            self.viewer = viewer

            def poll():
                stream = viewer._stream
                if stream is not None and stream.get_error() is not None:
                    result['error'] = stream.get_error().message
                if stream is not None and stream.get_playing():
                    result['playing'] = True
                    GLib.timeout_add(300, self._quit)
                    return False
                return True
            GLib.timeout_add(20, poll)
            GLib.timeout_add(15000, self._quit)

        def _quit(self):
            self.quit()
            return False

        def do_shutdown(self):
            result['engines'] = len(mediaengine._all)
            mediaengine.shutdown_all()
            result['left'] = len(mediaengine._all)
            Adw.Application.do_shutdown(self)

    app = App()
    code = app.run([])
    server.stop()
    print('QUIT playing=%s engines=%s left=%s error=%s' % (
        result.get('playing'), result.get('engines'), result.get('left'),
        result.get('error')), flush=True)
    sys.exit(code)


if len(sys.argv) > 1 and sys.argv[1] == '--child':
    child()

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name, flush=True)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''),
              flush=True)


# The application really calls it.
tree = ast.parse(open(os.path.join(srcdir, 'main.py')).read())
calls = [node for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
         for fn in cls.body
         if isinstance(fn, ast.FunctionDef) and fn.name == 'do_shutdown'
         for node in ast.walk(fn)
         if isinstance(node, ast.Call)
         and isinstance(node.func, ast.Attribute)
         and node.func.attr == 'shutdown_all']
check('do_shutdown calls shutdown_all() only if mediaengine is loaded',
      len(calls) == 1 and 'sys.modules' in ast.get_source_segment(
          open(os.path.join(srcdir, 'main.py')).read(),
          [fn for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
           for fn in cls.body if isinstance(fn, ast.FunctionDef)
           and fn.name == 'do_shutdown'][0]))

for run in range(RUNS):
    try:
        done = subprocess.run(
            [sys.executable, os.path.abspath(__file__), '--child'],
            capture_output=True, text=True, timeout=30)
        code, out = done.returncode, done.stdout
        quit_line = [line for line in out.splitlines()
                     if line.startswith('QUIT ')]
        detail = quit_line[-1] if quit_line else done.stderr[-3000:]
    except subprocess.TimeoutExpired:
        code, out, detail = None, '', 'timed out'
    check('quit while playing, run %d: exit 0 and engines dropped' % (run + 1),
          code == 0 and 'playing=True' in out and 'left=0' in out,
          'exit %r: %s' % (code, detail))

if failures:
    print('\n%d media quit check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall media quit checks passed')
