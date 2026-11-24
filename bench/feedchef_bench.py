# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef workload: the stall baseline of a cooked timeline.

A real FeedChefTimelinePage (the real engine, the real kitchen, the audit
log) in a real window cooks from bench/fixture_server.py through
MastodonClient: the first page, two older pages (scrolling to the end of
what is loaded), and then a refresh (a new cook). It reports the same stall
figures as bench/timeline_bench.py, whose measuring helpers it reuses
(main_ms, stall_max_ms, stall_p99_ms, stalls_over_16ms from the thread CPU
time of each main-loop dispatch, and the wall_* twins from the clock), plus
the time each cook took from the request to the painted list.

The recipe is the default one over the federated scope (the fixture's
public timeline). The engine's log folder and storage go to a temporary
state and data directory, so a run writes nothing under the home folder.
Prints one JSON line. ``BENCH_TOTAL`` is the fixture size (default 120: three
pages of COOK_COUNT = 40, so the workload is three cooks; ``pages_cooked``
reports what the run actually did, because the page's own preload may ask for
an older page before the scroll does).
Run it five times and report every run, as PERFORMANCE.md does; this is
the v1 baseline later Feed Chef phases must hold.
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

_STATE = tempfile.mkdtemp(prefix='babble-feedchef-bench-')
os.environ['XDG_STATE_HOME'] = os.path.join(_STATE, 'state')
os.environ['XDG_DATA_HOME'] = os.path.join(_STATE, 'data')
# The follow-list cache goes to the temporary folder too, but Mesa's shader
# cache must stay where it is: a cold one compiles every shader during the
# first frame (about 450 ms on the main thread) and hides what is measured.
os.environ.setdefault('MESA_SHADER_CACHE_DIR', os.path.join(
    os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
    'mesa_shader_cache'))
os.environ['XDG_CACHE_HOME'] = os.path.join(_STATE, 'cache')
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
os.environ.setdefault('BENCH_TOTAL', '120')

import timeline_bench as tb  # noqa: E402

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

PAGES = 3


def wait_for(condition, what, limit=30.0):
    """Settle the main loop until *condition* holds: a cook runs on a
    worker thread, so a quiet loop alone does not mean it is done."""
    start = time.monotonic()
    while True:
        tb.settle()
        if condition():
            return
        if time.monotonic() - start > limit:
            tb.fail('timed out waiting for %s' % what)


def _die_with_parent():
    """Stop the fixture server when the bench dies: losing the compositor
    makes GDK exit the process before the finally below can run."""
    import ctypes
    import signal
    pr_set_pdeathsig = 1
    ctypes.CDLL('libc.so.6', use_errno=True).prctl(
        pr_set_pdeathsig, signal.SIGTERM)


def main():
    total = tb.TOTAL
    anchor = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=2, minutes=total))
    anchor = anchor.replace(second=0, microsecond=0, tzinfo=None)
    server = subprocess.Popen(
        [sys.executable, os.path.join(HERE, 'fixture_server.py'),
         str(total), anchor.isoformat()],
        stdout=subprocess.PIPE, text=True, preexec_fn=_die_with_parent)
    try:
        run(int(server.stdout.readline()))
    finally:
        server.kill()
        server.wait()


def run(port):
    base = 'http://127.0.0.1:%d' % port
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(tb.SRC, 'style.css'))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    from src import feedchefpages, statusrun
    from src.feedchef.recipe import default_recipe
    from src.mastodon import MastodonClient

    client = MastodonClient('https://bench.invalid')
    client.instance = base
    client.api_base = base + '/api/v1'
    account = {'id': '1', 'acct': 'bench', 'instance': base}
    recipe = default_recipe('Bench')
    recipe.pantry.scope = 'federated'

    tb.gc.collect()
    tb.settle()
    rss_start = tb.rss_kb()
    tb.recording = True
    cpu0 = tb.cpu()
    wall0 = time.monotonic()
    phases = {}
    clock_marks = {}

    def mark(name, since_cpu, since_wall):
        phases[name] = round((tb.cpu() - since_cpu) * 1000.0, 1)
        clock_marks[name] = round((time.monotonic() - since_wall) * 1000.0, 1)
        return tb.cpu(), time.monotonic()

    window = Gtk.Window()
    window.set_default_size(700, 1000)
    navview = Adw.NavigationView()
    made = []
    # The synchronous main-thread calls go through tb.timed, so their work
    # counts in the stall figures like a dispatch (as in timeline_bench).
    tb.timed(lambda: made.append(
        feedchefpages.FeedChefTimelinePage(client, account, recipe)))
    page = made[0]
    navview.add(Adw.NavigationPage(title='Bench', child=page))
    window.set_child(navview)
    tb.timed(window.present)
    frames = [0, 0]

    def on_after_paint(_clock, root):
        frames[0] += 1
        if tb.has_empty_shell(root, statusrun.StatusRunRow):
            frames[1] += 1

    clock = window.get_frame_clock()
    handler = clock.connect('after-paint', on_after_paint, page)

    source = page.source
    model = page.model

    def audits():
        return len(source.cook_audits())

    c, w = tb.cpu(), time.monotonic()
    wait_for(lambda: model.get_n_items() > 0 and audits() >= 1,
             'the first cook')
    c, w = mark('cook1_ms', c, w)
    adjustment = page._scrolled.get_vadjustment()
    for number in range(2, PAGES + 1):
        for _ in range(20):
            if audits() >= number:
                break
            tb.timed(adjustment.set_value, adjustment.get_upper()
                     - adjustment.get_page_size())
            tb.settle()
        wait_for(lambda n=number: audits() >= n, 'cook %d' % number)
        c, w = mark('cook%d_ms' % number, c, w)
    shown_before_refresh = page.model.get_n_items()
    pages_cooked = audits()

    tb.timed(page.refresh)
    wait_for(lambda: model.get_n_items() > 0 and audits() >= 1,
             'the refresh')
    c, w = mark('refresh_ms', c, w)
    shown_after_refresh = page.model.get_n_items()

    clock.disconnect(handler)
    del clock
    page.close_requests()
    window.set_child(None)
    window.destroy()
    page = source = model = window = navview = adjustment = None
    for _ in range(3):
        tb.gc.collect()
        tb.settle()
    mark('teardown_ms', c, w)
    cpu_total = (tb.cpu() - cpu0) * 1000.0
    wall_total = (time.monotonic() - wall0) * 1000.0
    tb.recording = False
    stalls = sorted(d * 1000.0 for d in tb.dispatches)
    walls = sorted(d * 1000.0 for d in tb.wall_dispatches)

    def p99(values):
        return values[max(0, -(-len(values) * 99 // 100) - 1)] \
            if values else 0.0

    print(json.dumps({
        'cpu_ms': round(cpu_total, 1),
        'wall_ms': round(wall_total, 1),
        **{'cpu_' + k: v for k, v in phases.items()},
        **{'wall_' + k: v for k, v in clock_marks.items()},
        'main_ms': round(sum(stalls), 1),
        'stall_max_ms': round(stalls[-1] if stalls else 0.0, 1),
        'stall_p99_ms': round(p99(stalls), 1),
        'stalls_over_16ms': sum(1 for d in stalls if d > 16.0),
        'wall_stall_max_ms': round(walls[-1] if walls else 0.0, 1),
        'wall_stall_p99_ms': round(p99(walls), 1),
        'wall_stalls_over_16ms': sum(1 for d in walls if d > 16.0),
        'dispatches': len(stalls),
        'frames': frames[0],
        'frames_with_empty_shell': frames[1],
        'pages_cooked': pages_cooked,
        'posts_shown': shown_before_refresh,
        'posts_after_refresh': shown_after_refresh,
        'rss_growth_mb': round((tb.rss_kb() - rss_start) / 1024.0, 1),
    }))


if __name__ == '__main__':
    main()
