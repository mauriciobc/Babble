# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end timeline workload against a local fixture instance.

A real TimelinePage in a real window loads the public timeline from
bench/fixture_server.py through MastodonClient, is scrolled to the end
(infinite scroll pagination), scrolled back to the top, receives a burst
of streaming updates/edits/deletes, and is then torn down. Prints one
JSON line with CPU time, wall time and memory figures.

cpu_ms is process CPU (every thread). main_ms, stall_max_ms,
stall_p99_ms and stalls_over_16ms come from the thread CPU time of each
main-loop dispatch (and each simulated stream message) during the
measured run, so work moved off the main thread shows up there. frames
counts painted frames of the window, and frames_with_empty_shell those
where a mapped StatusRunRow had a bound run but no cards yet (pop-in).
"""

import datetime
import gc
import json
import os
import resource
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
TOTAL = int(os.environ.get('BENCH_TOTAL', '80'))
STREAM = int(os.environ.get('BENCH_STREAM', '12'))
SCROLL_STEPS = int(os.environ.get('BENCH_SCROLL_STEPS', '24'))
QUIET = float(os.environ.get('BENCH_QUIET_MS', '80')) / 1000.0

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import fixtures  # noqa: E402

# PyGObject's MainContext.iteration override pauses the asyncio event
# loop around every call (~65 us each), which the real app, running under
# Adw.Application.run, never pays. Call the introspected method directly.
_iteration = next(
    klass for klass in GLib.MainContext.__mro__
    if klass.__module__ == 'gi.repository.GLib').iteration
if type(_iteration).__name__ != 'FunctionInfo':
    raise SystemExit('MainContext.iteration is not the introspected method')

# Main-thread CPU seconds of each dispatch, recorded while `recording`.
dispatches = []
recording = False


def rss_kb():
    with open('/proc/self/statm') as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf('SC_PAGE_SIZE') // 1024


def cpu():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def settle(limit=30.0):
    """Run the main loop until nothing has been dispatched for QUIET
    seconds: layout, frames, HTTP replies and texture decodes done."""
    ctx = GLib.MainContext.default()
    start = last = time.monotonic()
    while True:
        now = time.monotonic()
        t0 = time.thread_time()
        dispatched = _iteration(ctx, False)
        if dispatched:
            if recording:
                dispatches.append(time.thread_time() - t0)
            last = now
        elif now - last >= QUIET:
            return
        else:
            time.sleep(0.002)
        if now - start > limit:
            raise SystemExit('settle: main loop never went idle')


def timed(func, *args):
    """Run one main-thread unit of work (as a dispatch would) and record
    its thread CPU time like a main-loop dispatch."""
    t0 = time.thread_time()
    func(*args)
    if recording:
        dispatches.append(time.thread_time() - t0)


def has_empty_shell(widget, run_row_type):
    """Whether a mapped run row under `widget` is bound but has no cards."""
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, run_row_type):
            if (child.get_mapped() and child._run is not None
                    and not child.rows):
                return True
        elif child.get_mapped() and has_empty_shell(child, run_row_type):
            return True
        child = child.get_next_sibling()
    return False


def count_loaded(model, carousel_type):
    """Statuses in the model; a boosts carousel holds several. Scoped in
    a function so no item reference outlives the count."""
    loaded = 0
    for i in range(model.get_n_items()):
        item = model.get_item(i)
        loaded += len(item.boosts) if isinstance(item, carousel_type) else 1
    return loaded


def fail(message):
    print('bench failure: %s' % message, file=sys.stderr)
    sys.exit(1)


def main():
    anchor = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=2, minutes=TOTAL + STREAM))
    anchor = anchor.replace(second=0, microsecond=0, tzinfo=None)
    server = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'fixture_server.py'),
         str(TOTAL), anchor.isoformat()],
        stdout=subprocess.PIPE, text=True)
    try:
        port = int(server.stdout.readline())
        run(port, anchor)
    finally:
        server.kill()
        server.wait()


def run(port, anchor):
    base = 'http://127.0.0.1:%d' % port
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(SRC, 'style.css'))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    from src.mastodon import MastodonClient
    from src import timeline

    client = MastodonClient('https://bench.invalid')
    # The client refuses plain http by design; the fixture instance is
    # loopback-only, so point the already-built client at it.
    client.instance = base
    client.api_base = base + '/api/v1'

    global recording
    gc.collect()
    settle()
    rss_start = rss_kb()
    recording = True
    cpu0 = cpu()
    wall0 = time.monotonic()
    phases = {}

    def mark(name, since):
        phases[name] = round((cpu() - since) * 1000.0, 1)
        return cpu()

    window = Gtk.Window()
    window.set_default_size(700, 1000)
    page = timeline.TimelinePage(client, mode='public')
    window.set_child(page)
    window.present()
    frames = [0, 0]

    def on_after_paint(_clock, root):
        frames[0] += 1
        if has_empty_shell(root, timeline.StatusRunRow):
            frames[1] += 1

    clock = window.get_frame_clock()
    paint_handler = clock.connect('after-paint', on_after_paint, page)
    settle()
    if page.stack.get_visible_child_name() != 'list':
        fail('first page did not load (%s)'
             % page.stack.get_visible_child_name())
    t = mark('load_ms', cpu0)

    # Infinite scroll to the end of the fixture timeline.
    adjustment = page._scrolled.get_vadjustment()
    for _ in range(TOTAL):
        if page.model.exhausted:
            break
        adjustment.set_value(adjustment.get_upper()
                             - adjustment.get_page_size())
        settle()
    # A boosts carousel is one list row for several posts, so the row
    # count is lower than the fixture size whenever a page groups.
    loaded = count_loaded(page.model, timeline.BoostCarouselItem)
    if not page.model.exhausted or loaded != TOTAL:
        fail('loaded %d of %d statuses (exhausted=%s, rows=%d)'
             % (loaded, TOTAL, page.model.exhausted,
                page.model.get_n_items()))
    t = mark('scroll_down_ms', t)
    rss_scrolled = rss_kb()

    # Back to the top in a fixed number of jumps: every region of the
    # list gets rebound, and the step count does not depend on how tall
    # the rows happen to measure.
    top = adjustment.get_value()
    for step in range(SCROLL_STEPS - 1, -1, -1):
        adjustment.set_value(top * step / SCROLL_STEPS)
        settle()
    t = mark('scroll_up_ms', t)

    # Streaming burst at the top of the timeline.
    for k in range(STREAM):
        sid = TOTAL + 1 + k
        timed(page.on_stream_update, fixtures.status(base, sid, anchor))
        if k % 4 == 3:
            timed(page.on_stream_delete, '%018d' % (sid - 2))
        if k % 6 == 5:
            edited = fixtures.status(base, sid - 1, anchor)
            edited['content'] += '<p>edited</p>'
            edited['edited_at'] = edited['created_at']
            timed(page.on_stream_status_update,
                  edited.get('reblog') or edited)
        settle()
    t = mark('stream_ms', t)
    rss_peak_phase = rss_kb()

    # Teardown: everything the page built must be releasable. The paint
    # handler holds the page, so it goes first.
    clock.disconnect(paint_handler)
    del clock
    window.set_child(None)
    window.destroy()
    del page, window, adjustment
    for _ in range(3):
        gc.collect()
        settle()
    mark('teardown_ms', t)
    cpu_total = (cpu() - cpu0) * 1000.0
    wall_total = (time.monotonic() - wall0) * 1000.0
    recording = False
    stalls = sorted(d * 1000.0 for d in dispatches)
    p99 = stalls[max(0, -(-len(stalls) * 99 // 100) - 1)] if stalls else 0.0

    gc.collect()
    live_rows = sum(1 for o in gc.get_objects()
                    if isinstance(o, timeline.StatusRow))
    live_items = sum(1 for o in gc.get_objects()
                     if isinstance(o, timeline.StatusItem))
    rss_end = rss_kb()
    print(json.dumps({
        'cpu_ms': round(cpu_total, 1),
        'wall_ms': round(wall_total, 1),
        **phases,
        'main_ms': round(sum(stalls), 1),
        'stall_max_ms': round(stalls[-1] if stalls else 0.0, 1),
        'stall_p99_ms': round(p99, 1),
        'stalls_over_16ms': sum(1 for d in stalls if d > 16.0),
        'frames': frames[0],
        'frames_with_empty_shell': frames[1],
        'peak_rss_mb': round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1),
        'rss_growth_mb': round((rss_peak_phase - rss_start) / 1024.0, 1),
        'scrolled_rss_growth_mb': round(
            (rss_scrolled - rss_start) / 1024.0, 1),
        'retained_mb': round((rss_end - rss_start) / 1024.0, 1),
        'live_rows': live_rows,
        'live_items': live_items,
    }))


if __name__ == '__main__':
    main()
