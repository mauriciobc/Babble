# SPDX-License-Identifier: GPL-3.0-or-later
"""Run Babble and drive its UI through a fixed scenario, identically each time.

    python3 bench/profiling/uidriver.py [--scenario timeline] [--log FILE]
                                        [--flicks 12] [--size 1280x900] ...

The driver runs *inside* the app's process: this script schedules the
scenario on the GLib main loop and then starts Babble exactly as
``build/src/babble`` does (``runpy``), so it needs no ptrace permission, no
accessibility bus and no input injection, works on Wayland, and composes
with any recorder that launches a command::

    sysprof-cli --no-perf ... OUT.syscap -- perf record ... -- \\
        python3 -X perf bench/profiling/uidriver.py --log run.jsonl

Scrolling moves the list's ``Gtk.Adjustment`` on the frame clock, one
eased "flick" at a time. That is not a scroll *event*, so the input
path (kinetic scrolling, the scroll controller) is not exercised — the
same trade-off as the benchmark harness — but everything the timeline
does in response to scrolling is: ``PagedPosts`` listens to the
adjustment's ``value-changed``, so paging, row binding, card population,
image loading and layout run exactly as for a reader.

Every step is logged as one JSON object per line with
``time.monotonic_ns()``, the same clock as sysprof marks and
``perf record -k CLOCK_MONOTONIC``, so phases can be cut out of a capture.
The exit status is 0 when the scenario completed and 3 when it did not
(timeline never loaded, or a step timed out).

The *actions* are identical between runs. The *content* is whatever the
account's home timeline holds at the time, which changes; compare runs by
per-frame and per-page distributions, not by totals.

Babble is single-instance: quit any running copy first, or the launch
hands off to it and the scenario never starts. Do not touch the window
while the scenario runs.
"""

import argparse
import json
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EXIT_INCOMPLETE = 3


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--scenario', default='timeline', choices=['timeline'])
    p.add_argument('--launcher',
                   default=os.path.join(ROOT, 'build', 'src', 'babble'),
                   help='Babble launcher to run (default: the build tree; '
                        '/app/bin/babble inside the Flatpak)')
    p.add_argument('--log', default=None,
                   help='JSON-lines event log (default: stderr only)')
    p.add_argument('--size', default='1280x900',
                   help='window size WxH, applied before the scenario starts')
    p.add_argument('--flicks', type=int, default=12,
                   help='flicks down (default 12); the same number up')
    p.add_argument('--flick-screens', type=float, default=0.9,
                   help='distance of one flick in viewport heights')
    p.add_argument('--flick-ms', type=float, default=250,
                   help='duration of one flick')
    p.add_argument('--dwell-ms', type=float, default=700,
                   help='pause after each flick')
    p.add_argument('--settle-ms', type=float, default=3000,
                   help='pause after the first load and before quitting')
    p.add_argument('--load-timeout', type=float, default=60,
                   help='seconds to wait for the first page')
    p.add_argument('--page-timeout', type=float, default=20,
                   help='seconds to wait for a further page to arrive')
    return p.parse_args(argv)


class Log:
    def __init__(self, path):
        self.handle = open(path, 'w') if path else None
        self.t0 = time.monotonic_ns()

    def __call__(self, event, **fields):
        now = time.monotonic_ns()
        record = {'t_ns': now, 'event': event, **fields}
        line = json.dumps(record, sort_keys=True)
        if self.handle:
            self.handle.write(line + '\n')
            self.handle.flush()
        print('uidriver %7.2fs %s %s' % ((now - self.t0) / 1e9, event,
                                         json.dumps(fields, sort_keys=True)),
              file=sys.stderr)


class TimelineScenario:
    """Wait for the home timeline, flick down N times (fetching further
    pages as a reader would), flick back to the top, settle, quit."""

    def __init__(self, options, log):
        from gi.repository import GLib
        self.GLib = GLib
        self.o = options
        self.log = log
        self.app = None
        self.window = None
        self.page = None
        self.scrolled = None
        self.ok = False
        self.deadline = None

    # -- finding the app's objects ------------------------------------
    def find(self):
        from gi.repository import Gio, Gtk
        self.app = self.app or Gio.Application.get_default()
        if self.app is None:
            return False
        window = self.app.get_active_window()
        page = getattr(window, 'timeline_page', None) if window else None
        if page is None:
            return False
        scrolled = self._first(page, Gtk.ScrolledWindow)
        if scrolled is None:
            return False
        self.window, self.page, self.scrolled = window, page, scrolled
        return True

    @staticmethod
    def _first(widget, kind):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, kind):
                return child
            found = TimelineScenario._first(child, kind)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def model_state(self):
        model = getattr(self.page, 'model', None)
        adj = self.scrolled.get_vadjustment()
        return {
            'items': model.get_n_items() if model is not None else None,
            'loading': bool(getattr(model, 'loading', False)),
            'exhausted': bool(getattr(model, 'exhausted', False)),
            'value': round(adj.get_value(), 1),
            'upper': round(adj.get_upper(), 1),
            'page_size': round(adj.get_page_size(), 1),
        }

    def loaded(self):
        stack = getattr(self.page, 'stack', None)
        visible = (stack.get_visible_child_name() if stack is not None
                   else 'list')
        state = self.model_state()
        return visible == 'list' and state['items'] and not state['loading']

    # -- sequencing ---------------------------------------------------
    def start(self):
        self.log('driver-start', scenario='timeline', options=vars(self.o))
        self.deadline = time.monotonic() + self.o.load_timeout
        self.GLib.timeout_add(100, self._wait_for_window)

    def fail(self, reason):
        self.log('scenario-incomplete', reason=reason,
                 **(self.model_state() if self.scrolled else {}))
        self.quit()
        return False

    def quit(self):
        from gi.repository import Gio
        app = self.app or Gio.Application.get_default()
        if app is not None:
            app.activate_action('quit', None)

    def _wait_for_window(self):
        if time.monotonic() > self.deadline:
            return self.fail('no home timeline page within %.0f s'
                             % self.o.load_timeout)
        if not self.find():
            return True
        w, h = (int(x) for x in self.o.size.lower().split('x'))
        if self.window.is_maximized():
            self.window.unmaximize()
        self.window.set_default_size(w, h)
        self.log('window-found', requested_size=[w, h])
        self.GLib.timeout_add(100, self._wait_for_first_page)
        return False

    def _wait_for_first_page(self):
        if time.monotonic() > self.deadline:
            return self.fail('first page did not load within %.0f s'
                             % self.o.load_timeout)
        if not self.loaded():
            return True
        self.log('first-page', size=[self.window.get_width(),
                                     self.window.get_height()],
                 **self.model_state())
        self.log('phase', name='settle-after-load')
        self.GLib.timeout_add(int(self.o.settle_ms), self._begin_down)
        return False

    def _begin_down(self):
        self.log('phase', name='scroll-down', **self.model_state())
        self.flicks_left = self.o.flicks
        self.direction = 1
        self._next_flick()
        return False

    def _next_flick(self):
        adj = self.scrolled.get_vadjustment()
        if self.direction > 0:
            at_end = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 1
            exhausted = getattr(self.page.model, 'exhausted', False)
            if self.flicks_left <= 0 or (exhausted and at_end):
                self.log('phase', name='bottom-dwell', **self.model_state())
                self.GLib.timeout_add(int(self.o.dwell_ms * 2), self._begin_up)
                return
            self.flicks_left -= 1
        elif adj.get_value() <= 0:
            self.log('phase', name='settle-before-quit', **self.model_state())
            self.GLib.timeout_add(int(self.o.settle_ms), self._finish)
            return
        start = adj.get_value()
        span = adj.get_page_size() * self.o.flick_screens * self.direction
        bottom = adj.get_upper() - adj.get_page_size()
        target = max(0.0, min(start + span, bottom))
        self.log('flick', direction=self.direction, start=round(start, 1),
                 target=round(target, 1), **self.model_state())
        self._animate(start, target)

    def _animate(self, start, target):
        clock = self.scrolled.get_frame_clock()
        begin = clock.get_frame_time() if clock else None
        duration_us = self.o.flick_ms * 1000

        def tick(widget, frame_clock):
            nonlocal begin
            now = frame_clock.get_frame_time()
            if begin is None:
                begin = now
            progress = min(1.0, (now - begin) / duration_us)
            eased = 1 - (1 - progress) ** 3          # ease-out cubic
            value = start + (target - start) * eased
            widget.get_vadjustment().set_value(value)
            if progress < 1.0:
                return self.GLib.SOURCE_CONTINUE
            self.GLib.timeout_add(int(self.o.dwell_ms), self._after_dwell)
            return self.GLib.SOURCE_REMOVE

        self.scrolled.add_tick_callback(tick)

    def _after_dwell(self):
        # A flick near the end triggers a fetch; wait for that page so
        # every run scrolls through the same number of loaded pages.
        if getattr(self.page.model, 'loading', False):
            if not hasattr(self, '_page_wait'):
                self._page_wait = time.monotonic()
                self.log('page-wait', **self.model_state())
            if time.monotonic() - self._page_wait > self.o.page_timeout:
                return self.fail('page did not arrive within %.0f s'
                                 % self.o.page_timeout)
            return True                         # poll again after dwell
        if hasattr(self, '_page_wait'):
            self.log('page-arrived',
                     waited_ms=round((time.monotonic() - self._page_wait)
                                     * 1000),
                     **self.model_state())
            del self._page_wait
        self._next_flick()
        return False

    def _begin_up(self):
        self.log('phase', name='scroll-up', **self.model_state())
        self.direction = -1
        self._next_flick()
        return False

    def _finish(self):
        self.ok = True
        self.log('scenario-complete', **self.model_state())
        self.quit()
        return False


def main(argv):
    options = parse(argv)
    if not os.path.exists(options.launcher):
        sys.exit('%s not found: build first (meson compile -C build)'
                 % options.launcher)
    log = Log(options.log)
    if options.launcher.endswith(os.path.join('src', 'babble')):
        # The uninstalled launcher aborts without the build tree's schemas.
        build = os.path.dirname(os.path.dirname(options.launcher))
        os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(build, 'data'))
    from gi.repository import GLib
    scenario = TimelineScenario(options, log)
    # Start once the main loop runs, i.e. after Babble's application exists.
    GLib.idle_add(lambda: (scenario.start(), False)[1])
    sys.argv = [options.launcher]
    status = 0
    try:
        runpy.run_path(options.launcher, run_name='__main__')
    except SystemExit as stop:
        status = stop.code if isinstance(stop.code, int) else 0
    if not scenario.ok:
        log('driver-exit', status=EXIT_INCOMPLETE, app_status=status)
        return EXIT_INCOMPLETE
    log('driver-exit', status=status)
    return status


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
