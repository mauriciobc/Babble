# SPDX-License-Identifier: GPL-3.0-or-later
"""v2 timeline benchmark: the regimes ``bench/timeline_bench.py`` cannot see.

The v1 harness is honest about what it measures, but it measures one
narrow regime: a fully drained main loop, synthetic 96px images, and 80
posts -- fewer than ``CARD_SHELLS_KEPT`` (100) and ``TIMELINE_MAX_ITEMS``
(400), so neither of those bounds is ever reached. This driver keeps every
v1 metric (so the two are comparable) and adds the missing ones.

What it adds, and why
---------------------

*Realistic images.* ``fixture_server2.py`` serves 400px avatars and
1600x1200 media. At 96px an avatar fits its cover box and takes the fast
``Gdk.Texture.new_from_bytes`` path; at 400px it takes the GdkPixbuf one.
The run reports ``decode_avatar_fast``/``_slow`` and
``decode_media_fast``/``_slow`` so it is *provable* which path a given
configuration exercised -- a benchmark that silently measures only the
fast path is worse than no benchmark. Per source, because an aggregate
cannot tell a healthy run from a broken one (see ``classify``).

*Pop-in in milliseconds.* v1 reports ``frames_with_empty_shell``, a ratio
that says nothing about how long a post sits blank. This reports the
median, p90 and worst blank-to-painted latency per post.

*Concurrency.* v1's ``settle()`` waits for a fully idle main loop before
every step, which is load-bearing rather than stylistic: with the wait
removed the old harness fails outright (``loaded 40 of 80 statuses``).
The ``concurrent`` scenario therefore does the opposite -- it pumps the
main loop for fixed wall-clock slices and never waits for idle, so image
decode, paging and streaming overlap scrolling the way they do for a real
reader. It reports how many frames were painted while decode work was
still in flight.

*First load.* Time to the first painted card, the most user-visible number
in the app, which v1 does not measure at all and the docs run by hand.

*Network accounting.* The driver reads ``/__stats`` from the fixture to
report total requests and duplicated URLs, so redundant fetching (a poll
that re-fetches what the stream already delivered) shows up as a number
instead of a code-reading exercise.

Scenarios (``BENCH_SCENARIO``, default ``scroll``)::

    firstload   cold start to first painted card
    scroll      v1's workload, realistic images, BENCH_TOTAL default 400
    concurrent  scroll with no idle-waiting: decode+paging+stream overlap
    soak        several pages at once, to exercise CARD_SHELLS_KEPT eviction

Every run prints one JSON line. Keys shared with v1 keep v1's meaning;
keys v1 has no equivalent for are the additions.
"""

import datetime
import gc
import json
import os
import resource
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_root():
    """Walk up until a directory holding ``src/style.css`` appears.

    v1 hardcodes ``dirname(dirname(__file__))`` and reads the stylesheet
    from ``<root>/src``. Move this file one directory deeper and that
    resolves to a path that does not exist -- the stylesheet then fails to
    load with only a GTK theme-parser warning to show for it, and the run
    quietly measures an unstyled app. Walking up to a known-good marker
    makes the harness independent of where it sits.
    """
    path = HERE
    while True:
        if os.path.exists(os.path.join(path, 'src', 'style.css')):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            raise SystemExit('bench: no src/style.css above %s' % HERE)
        path = parent


ROOT = _find_root()
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
# bench/fixtures.py holds the payload generator, shared with v1 so the two
# harnesses cannot drift on the data they both feed their client.
sys.path.insert(0, os.path.dirname(HERE))

SCENARIO = os.environ.get('BENCH_SCENARIO', 'scroll')
# Posts the fixture timeline holds. 500 rather than 400 on purpose:
# TIMELINE_MAX_ITEMS is 400, so a run of exactly 400 never trims
# anything (trim(400) over at most 400 items is a no-op) and the bound
# stays untested. 500 puts real pressure on it.
TOTAL = int(os.environ.get('BENCH_TOTAL', '500'))
STREAM = int(os.environ.get('BENCH_STREAM', '12'))
SCROLL_STEPS = int(os.environ.get('BENCH_SCROLL_STEPS', '24'))
QUIET = float(os.environ.get('BENCH_QUIET_MS', '80')) / 1000.0
FRAME_BUDGET_MS = float(os.environ.get('BENCH_FRAME_BUDGET_MS', '16.67'))
SOAK_PAGES = int(os.environ.get('BENCH_SOAK_PAGES', '3'))
# A cap on scroll steps, not a post count: each page loads everything the
# fixture holds regardless of how far it is scrolled.
SOAK_STEPS = int(os.environ.get('BENCH_SOAK_STEPS', '40'))
# Upper bound on concurrent-scenario steps so a regression that stops
# paging cannot spin forever. The run is expected to end on loaded==TOTAL.
CONCURRENT_MAX_STEPS = int(os.environ.get('BENCH_CONCURRENT_MAX_STEPS', '4000'))

# Set once by run() before any scenario starts. Scenarios report deltas
# against it, and cpu0 is the CPU baseline the phase timings subtract.
cpu0 = 0.0
RSS_START = 0

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

# PyGObject's MainContext.iteration override pauses the asyncio loop on
# every call (~65 us each), which the real app under Adw.Application.run
# never pays. Call the introspected method directly. (v1 already does
# this; kept so the two harnesses stay comparable.)
_iteration = next(
    klass for klass in GLib.MainContext.__mro__
    if klass.__module__ == 'gi.repository.GLib').iteration
if type(_iteration).__name__ != 'FunctionInfo':
    raise SystemExit('MainContext.iteration is not the introspected method')


def rss_kb():
    with open('/proc/self/statm') as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf('SC_PAGE_SIZE') // 1024


def cpu():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class Counters:
    """Everything the run measures, in one place.

    ``lock`` because image decode runs on worker threads: the decode
    counters are touched from there, the frame counters from the main
    loop, and without it a torn count would read as a lost decode.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.recording = False
        self.dispatches = []
        self.popin = []
        self.decode_avatar_fast = 0
        self.decode_avatar_slow = 0
        self.decode_media_fast = 0
        self.decode_media_slow = 0
        self.decode_raw = 0
        self.decodes_inflight = 0
        self.frames = 0
        self.frames_empty = 0
        self.frames_during_decode = 0
        self.first_card = None
        # Fetcher keys still outstanding at the end of the run.
        self.stranded = []
        self._last_paint_cpu = time.thread_time()
        self._cpu_at_last_paint = None
        self._pending_since = None

    # -- main loop -----------------------------------------------------
    def note_dispatch(self, seconds):
        with self.lock:
            self.dispatches.append(seconds)

    def note_frame(self, empty, populated, inflight):
        with self.lock:
            self.frames += 1
            if empty:
                self.frames_empty += 1
            if inflight > 0:
                self.frames_during_decode += 1
            if self.first_card is None and populated:
                self.first_card = time.monotonic()

    # -- decode (worker threads) ---------------------------------------
    def decode_started(self):
        with self.lock:
            self.decodes_inflight += 1

    def decode_done(self, kind):
        with self.lock:
            self.decodes_inflight -= 1
            setattr(self, 'decode_' + kind, getattr(self, 'decode_' + kind) + 1)

    def inflight(self):
        with self.lock:
            return self.decodes_inflight

    def inflight_requests(self):
        """Fetcher keys still outstanding.

        Read after the main loop has gone quiet, so a non-zero value means
        a request that will never complete -- the signature of the
        stranded-``_inflight`` bug, which no widget count would show.
        """
        from src.mediacache import AvatarCache, _TextureCache
        from src.textemoji import EmojiTextures
        from src.profile import HeaderCache
        from src import mediaviewer
        total = 0
        seen = set()
        # Each owner keeps its fetcher under a different name, and some
        # build it lazily, so collect them defensively rather than assume.
        for fetcher in (_TextureCache.fetcher,
                        EmojiTextures._fetch(),
                        HeaderCache.fetcher,
                        mediaviewer._viewer_fetcher,
                        AvatarCache._fetch()):
            if id(fetcher) in seen:
                continue
            seen.add(id(fetcher))
            total += len(fetcher.inflight())
        return total


C = Counters()


def settle(limit=30.0):
    """Run the main loop until nothing has been dispatched for QUIET seconds.

    Same contract as v1, and for the same reason it cannot be relaxed:
    a workload that waits for idle measures steady state. ``pump`` is the
    counterpart for when we *want* the overlap.
    """
    ctx = GLib.MainContext.default()
    start = last = time.monotonic()
    while True:
        now = time.monotonic()
        t0 = time.thread_time()
        dispatched = _iteration(ctx, False)
        if dispatched:
            if C.recording:
                C.note_dispatch(time.thread_time() - t0)
            last = now
        elif now - last >= QUIET:
            return
        else:
            time.sleep(0.002)
        if now - start > limit:
            raise SystemExit('settle: main loop never went idle')


def pump(seconds):
    """Run the main loop for a fixed wall-clock window, idle or not.

    This is the difference between the two harnesses. ``settle`` returns
    only once the loop has been quiet for QUIET, so no asynchronous work
    is ever in flight when the next step begins; ``pump`` deliberately
    steps on with decodes, HTTP replies and frames still pending.
    """
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t0 = time.thread_time()
        dispatched = _iteration(ctx, False)
        if dispatched and C.recording:
            C.note_dispatch(time.thread_time() - t0)
        else:
            time.sleep(0.001)


def has_populated_run(widget, run_row_type):
    """Whether *widget* contains a run row that actually has cards.

    ``has_empty_shell`` answers "is any mapped run row card-less", and it
    says False when there is no run row at all -- which is also true of
    the frame showing the loading spinner. Using it to decide "the first
    card has been painted" therefore counted the spinner as the first
    card. This looks for a run row with cards in it, which is what a
    reader would call seeing a post.
    """
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, run_row_type):
            if child.get_mapped() and child._run is not None and child.rows:
                return True
        elif child.get_mapped() and has_populated_run(child, run_row_type):
            return True
        child = child.get_next_sibling()
    return False


def has_empty_shell(widget, run_row_type):
    """Whether a mapped run row under *widget* is bound but has no cards."""
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
    loaded = 0
    for i in range(model.get_n_items()):
        item = model.get_item(i)
        loaded += len(item.boosts) if isinstance(item, carousel_type) else 1
    return loaded


# -- instrumentation ----------------------------------------------------
def instrument(statusrun):
    """Count decode paths, pop-in latency and time-to-first-card.

    All three are monkeypatches onto module attributes rather than edits
    to src/: the benchmark must be able to run against a working tree
    without modifying it.
    """
    from src import mediacache

    real_decode_async = mediacache._decode_async
    real_covering = mediacache._decode_covering
    real_image_size = mediacache._image_pixel_size

    def classify(data, cover, scale):
        """Which branch of _decode_covering this input takes, and for what.

        Mirrors src/mediacache.py:183-189. cover None means the caller
        asked for a full-size decode (the media viewer's path).

        Avatars and media are counted **separately**, and that separation
        is load-bearing. An aggregate count cannot tell a healthy run from
        a broken one: with an aggregate, serving 96px avatars looks like a
        small improvement, because the oversized *media* still take the
        slow path and keep the total up. Counting avatars on their own is
        what lets the gate notice that the fixture stopped being realistic
        -- see the floor on decode_avatar_slow in gate.py.
        """
        if cover is None:
            return 'raw', 'media'
        natural = real_image_size(data)
        if natural:
            width, height = natural
            box_w, box_h = cover[0] * scale, cover[1] * scale
            if width > 0 and height > 0 \
                    and max(box_w / width, box_h / height) >= 1:
                kind = 'fast'
            else:
                kind = 'slow'
        else:
            kind = 'slow'
        from src.timelinebase import AVATAR_DECODE_SIZE
        source = ('avatar' if tuple(cover) == (AVATAR_DECODE_SIZE,) * 2
                  else 'media')
        return kind, source

    # The in-flight gauge is opened and closed **inside**
    # _decode_covering and nowhere else.
    #
    # An earlier version opened it in _decode_async and closed it when
    # _decode_covering returned, which drifts permanently in two ways:
    # full-size decodes (cover=None -- every emoji, and the media viewer)
    # never call _decode_covering at all, and a Gio.Task with
    # return-on-cancel can skip its worker entirely when cancelled before
    # it starts, so the increment never gets a matching decrement. Once
    # that happens the count can only grow and every later frame is
    # reported as painted during decode. Clamping at zero hides the
    # opposite mistake rather than fixing this one.
    #
    # _decode_covering is entered and left synchronously around the real
    # decode, so a try/finally there cannot drift. The honest cost is that
    # the gauge covers the decode call only, not the HTTP fetch or the
    # cover=None path; frames_during_decode therefore means "decoding a
    # scaled avatar or media tile", and the README says so.

    def classified_covering(data, cover, scale):
        kind, source = classify(data, cover, scale)
        key = ('decode_raw' if kind == 'raw'
               else 'decode_%s_%s' % (source, kind))
        with C.lock:
            setattr(C, key, getattr(C, key) + 1)
            C.decodes_inflight += 1
        try:
            return real_covering(data, cover, scale)
        finally:
            with C.lock:
                C.decodes_inflight -= 1

    def count_raw_async(data, cover, cancellable, callback):
        # No gauge here: see above. Only the branch count, for the
        # cover=None path that never reaches _decode_covering.
        if cover is None:
            with C.lock:
                C.decode_raw += 1
        return real_decode_async(data, cover, cancellable, callback)

    mediacache._decode_covering = classified_covering
    mediacache._decode_async = count_raw_async

    # Pop-in: how long a post is on screen with an empty card.
    # Pop-in latency.
    #
    # What this actually measures: the time from a run shell being put on
    # the idle queue to its cards being built. It is *not* strictly "time
    # a reader stared at a blank card" -- ListView maps shells just past
    # the viewport, so some of the queueing happens for rows nobody is
    # looking at. That is inherent to instrumenting this from outside, and
    # a narrower claim would need GTK to tell us when a shell first
    # painted. The previous version had two outright bugs on top of it:
    #
    #   * it recorded a sample even when _populate returned early (the
    #     shell already had rows, or had none), so timings where nothing
    #     was built were counted;
    #   * the start stamp was never cleared on unmap, so a shell queued,
    #     unmapped, and later populated synchronously from bind() reported
    #     the whole intervening span as its pop-in.
    #
    # Both are fixed below: a sample is recorded only when populate
    # actually built cards, and unmap drops the stamp.

    real_queue = statusrun.StatusRunRow._queue_populate
    real_populate = statusrun.StatusRunRow._populate
    real_unmap = statusrun.StatusRunRow._on_unmap

    def queue(self):
        if C.recording:
            self._popin_since = time.monotonic()
        return real_queue(self)

    def populate(self):
        since = getattr(self, '_popin_since', None)
        had_rows = bool(self.rows)
        result = real_populate(self)
        if since is not None:
            self._popin_since = None
            # Only a shell that built cards in this call is a sample;
            # an early return means nothing was built, and a shell that
            # was never queued has no 'since'.
            if C.recording and not had_rows and self.rows:
                C.popin.append((time.monotonic() - since) * 1000.0)
        return result

    def unmap(self, widget):
        # A stamp that survives unmap would be attributed to whatever
        # populated this shell next, arbitrarily much later.
        self._popin_since = None
        return real_unmap(self, widget)

    statusrun.StatusRunRow._queue_populate = queue
    statusrun.StatusRunRow._populate = populate
    statusrun.StatusRunRow._on_unmap = unmap


def start_server(total):
    anchor = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=2, minutes=total + STREAM))
    anchor = anchor.replace(second=0, microsecond=0, tzinfo=None)
    argv = [sys.executable, os.path.join(HERE, 'fixture_server2.py'),
            str(total), anchor.isoformat()]
    # Image sizes are passed through rather than left to the server's
    # defaults, so a run can be pinned to a known decode-path regime and
    # compared against v1's (96px avatars take the fast path, 400px do
    # not). Unset means "what a real instance serves".
    for env, flag in (('BENCH_AVATAR_PX', '--avatar-px'),
                      ('BENCH_MEDIA_PX', '--media-px'),
                      ('BENCH_CARD_PX', '--card-px')):
        if os.environ.get(env):
            argv += [flag, os.environ[env]]
    if os.environ.get('BENCH_LATENCY_MS'):
        argv += ['--latency-ms', os.environ['BENCH_LATENCY_MS']]
    server = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True)
    port = int(server.stdout.readline())
    return server, port, anchor


def server_stats(port):
    """Request accounting from the fixture, over urllib so the client
    under test is not involved."""
    try:
        with urllib.request.urlopen(
                'http://127.0.0.1:%d/__stats' % port, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as error:                       # noqa: BLE001
        return {'error': str(error)}


def make_client(base):
    from src.mastodon import MastodonClient
    client = MastodonClient('https://bench.invalid')
    # The client refuses plain http by design; the fixture is loopback
    # only, so point the already-built client at it.
    client.instance = base
    client.api_base = base + '/api/v1'
    return client


def make_window(page, width=700, height=1000):
    window = Gtk.Window()
    window.set_default_size(width, height)
    window.set_child(page)
    window.present()
    return window


def build_page(client, mode='public', total=None):
    from src import timelinepage
    page = timelinepage.TimelinePage(client, mode=mode)
    return page


class PaintProbe:
    """Counts painted frames and frames showing an empty run shell.

    There is deliberately **no** per-frame CPU metric here. It is tempting
    to measure main-thread CPU per frame and compare it to a 16ms budget,
    but it cannot be done honestly from outside GTK: the frame clock does
    not hand out the CPU its layout and paint cost, so the only thing
    measurable is the CPU consumed between two paints, which also contains
    every unrelated dispatch in between (HTTP replies, decode callbacks).
    Measured that way it reported a p99 of 7.3 *seconds*, which describes
    the gap between frames, not the frames.

    Frames timing is sysprof's job and it does it properly: GTK writes
    real frame-clock phase data to ``SYSPROF_TRACE_FD``, which
    ``bench/v2/run.sh`` sets when ``BENCH_PROFILE`` is given. Use that for
    "why is this frame late". What belongs here is what sysprof cannot
    know: how often a frame was painted while a run had no cards yet.
    """

    def __init__(self, window, root, run_row_type):
        self.root = root
        self.run_row_type = run_row_type
        self.clock = window.get_frame_clock()
        # after-paint emits only the frame clock; the widget to inspect
        # travels as user_data. Connecting without it leaves the handler a
        # missing argument, which PyGObject reports once per frame and
        # then the frame count silently stays at zero.
        self.handler = self.clock.connect('after-paint', self._after_paint, root)

    def _after_paint(self, _clock, root):
        C.note_frame(has_empty_shell(root, self.run_row_type),
                     has_populated_run(root, self.run_row_type),
                     C.inflight())

    def disconnect(self):
        self.clock.disconnect(self.handler)


def git_state():
    """Revision and dirty flag, so a baseline records what it measured.

    Not compared -- the revision changes every commit, which would make
    the gate useless -- but recorded, and a dirty tree is warned about,
    because figures taken with uncommitted changes describe a tree nobody
    else has.
    """
    try:
        out = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                             cwd=ROOT, capture_output=True, text=True,
                             timeout=10)
        rev = out.stdout.strip() or 'unknown'
        dirty = bool(subprocess.run(
            ['git', 'status', '--porcelain', '--', 'src/', 'data/'],
            cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        rev, dirty = 'unknown', False
    return {'git': rev, 'dirty': dirty}


def conditions(stats):
    """Everything that changes which code this run exercises.

    gate.py refuses to compare runs whose conditions differ, because a
    baseline recorded with 400px avatars and 400 posts says nothing about
    a run with 96px ones and 80 posts -- and reporting that run as an
    improvement would be worse than reporting nothing.
    """
    return {
        'posts': TOTAL,
        'avatar_px': os.environ.get('BENCH_AVATAR_PX', '400'),
        'media_px': os.environ.get('BENCH_MEDIA_PX', '1600x1200'),
        'image_format': stats.get('formats', {}).get('media', '?'),
        'latency_ms': float(os.environ.get('BENCH_LATENCY_MS', '0') or 0),
        'renderer': os.environ.get('GSK_RENDERER', '?'),
        'frame_budget_ms': FRAME_BUDGET_MS,
        'scroll_steps': SCROLL_STEPS,
        'stream_events': STREAM,
        'soak_pages': SOAK_PAGES,
        'soak_steps': SOAK_STEPS,
        'scenario': SCENARIO,
        **git_state(),
    }


def emit(phases, stats, extra):
    """One JSON line, with every v1 key present (None when the scenario
    did not exercise it) so old and new runs compare cleanly."""
    stalls = sorted(d * 1000.0 for d in C.dispatches)
    p99 = (stalls[max(0, -(-len(stalls) * 99 // 100) - 1)] if stalls else 0.0)
    popin = sorted(C.popin)

    def pct(values, q):
        if not values:
            return None
        return round(values[min(len(values) - 1, int(len(values) * q))], 1)

    out = {
        # --- v1 keys, same meaning ---
        'cpu_ms': None, 'wall_ms': None, 'main_ms': round(sum(stalls), 1),
        'stall_max_ms': round(stalls[-1] if stalls else 0.0, 1),
        'stall_p99_ms': round(p99, 1),
        'stalls_over_16ms': sum(1 for d in stalls if d > 16.0),
        'frame_budget_ms': FRAME_BUDGET_MS,
        'stalls_over_budget': sum(1 for d in stalls if d > FRAME_BUDGET_MS),
        'frames': C.frames,
        'frames_with_empty_shell': C.frames_empty,
        'peak_rss_mb': None, 'rss_growth_mb': None,
        'scrolled_rss_growth_mb': None, 'retained_mb': None,
        'live_rows': None, 'live_items': None,
        'load_ms': None, 'scroll_down_ms': None, 'scroll_up_ms': None,
        'stream_ms': None, 'teardown_ms': None,
        # --- new ---
        'scenario': SCENARIO,
        'posts': TOTAL,
        'conditions': conditions(stats),
        'popin_count': len(popin),
        'popin_ms_median': pct(popin, 0.5),
        'popin_ms_p90': pct(popin, 0.9),
        'popin_ms_max': round(max(popin), 1) if popin else None,
        # Requests still in flight once the main loop has gone quiet.
        # Anything non-zero is a Fetcher key that was stranded -- the
        # state an image could never be fetched again from, and the
        # waiter lists that pile up on it. The harness used to be blind
        # to this: live_rows/live_items only count widgets.
        'inflight_requests': C.inflight_requests(),
        'decode_avatar_fast': C.decode_avatar_fast,
        'decode_avatar_slow': C.decode_avatar_slow,
        'decode_media_fast': C.decode_media_fast,
        'decode_media_slow': C.decode_media_slow,
        'decode_raw': C.decode_raw,
        'frames_during_decode': C.frames_during_decode,
        'http_requests': stats.get('requests'),
        'not_found_requests': stats.get('not_found'),
        'duplicate_requests': stats.get('duplicate_requests'),
        'bytes_received': stats.get('bytes'),
    }
    if C.first_card is not None:
        out['first_card_ms'] = round((C.first_card - extra['t0']) * 1000.0, 1)
    # Only the scenario's own numbers are reported; the bookkeeping this
    # function needs (start times, RSS watermarks, live-object counts) is
    # applied below and must not leak into the metric stream, or every run
    # invents a "cpu0" metric that means nothing to a reader.
    out.update({k: v for k, v in phases.items()})
    out['cpu_ms'] = round((cpu() - extra['cpu0']) * 1000.0, 1)
    out['wall_ms'] = round((time.monotonic() - extra['wall0']) * 1000.0, 1)
    out['peak_rss_mb'] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    out['rss_growth_mb'] = round((extra['rss_peak'] - extra['rss_start']) / 1024.0, 1)
    out['retained_mb'] = round((extra['rss_end'] - extra['rss_start']) / 1024.0, 1)
    out['live_rows'], out['live_items'] = extra['live']
    print(json.dumps(out))


def live_counts():
    from src import statusrow, timelinedata
    gc.collect()
    return (sum(1 for o in gc.get_objects()
                if isinstance(o, statusrow.StatusRow)),
            sum(1 for o in gc.get_objects()
                if isinstance(o, timelinedata.StatusItem)))


class Phases:
    """Phase timings, each a delta since the previous phase.

    v1 reports these the same way (its ``mark`` helper), so the numbers
    mean the same thing in both harnesses. Recording absolute-since-start
    instead would make every phase cumulative and quietly inflate the
    later ones.
    """

    def __init__(self):
        self.t = cpu()
        self.values = {}

    def mark(self, name):
        now = cpu()
        self.values[name] = round((now - self.t) * 1000.0, 1)
        self.t = now
        return now


def scenario_scroll(port, anchor, client, base):
    """v1's workload on realistic images, and by default past both of the
    app's own bounds (CARD_SHELLS_KEPT=100, TIMELINE_MAX_ITEMS=400)."""
    import fixtures
    from src import statusrun, timelinedata
    page = build_page(client)
    window = make_window(page)
    probe = PaintProbe(window, page, statusrun.StatusRunRow)
    settle()
    if page.stack.get_visible_child_name() != 'list':
        raise SystemExit('first page did not load (%s)'
                         % page.stack.get_visible_child_name())
    ph = Phases()
    phases = ph.values
    phases['load_ms'] = round((ph.t - cpu0) * 1000.0, 1)
    rss_start_local = rss_kb()

    adjustment = page._scrolled.get_vadjustment()
    for _ in range(TOTAL):
        if page.model.exhausted:
            break
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        settle()
    loaded = count_loaded(page.model, timelinedata.BoostCarouselItem)
    if not page.model.exhausted or loaded != TOTAL:
        raise SystemExit('loaded %d of %d statuses (exhausted=%s, rows=%d)'
                         % (loaded, TOTAL, page.model.exhausted,
                            page.model.get_n_items()))
    ph.mark('scroll_down_ms')
    rss_scrolled = rss_kb()

    top = adjustment.get_value()
    for step in range(SCROLL_STEPS - 1, -1, -1):
        adjustment.set_value(top * step / SCROLL_STEPS)
        settle()
    ph.mark('scroll_up_ms')

    for k in range(STREAM):
        sid = TOTAL + 1 + k
        page.on_stream_update(fixtures.status(base, sid, anchor))
        if k % 4 == 3:
            page.on_stream_delete('%018d' % (sid - 2))
        if k % 6 == 5:
            edited = fixtures.status(base, sid - 1, anchor)
            edited['content'] += '<p>edited</p>'
            edited['edited_at'] = edited['created_at']
            page.on_stream_status_update(edited.get('reblog') or edited)
        settle()
    ph.mark('stream_ms')
    rss_peak = rss_kb()

    probe.disconnect()
    window.set_child(None)
    window.destroy()
    del page, window, probe, adjustment
    for _ in range(3):
        gc.collect()
        settle()
    ph.mark('teardown_ms')
    phases['scrolled_rss_growth_mb'] = round(
        (rss_scrolled - rss_start_local) / 1024.0, 1)
    phases['loaded'] = loaded
    phases['posts_loaded'] = loaded
    return phases, rss_peak


def scenario_concurrent(port, anchor, client, base):
    """Scroll while decode, paging and streaming are still in flight.

    ``pump`` instead of ``settle``: the loop is never given the chance to
    go quiet, which is the one thing v1's harness cannot do.
    """
    import fixtures
    from src import statusrun, timelinedata
    page = build_page(client)
    window = make_window(page)
    probe = PaintProbe(window, page, statusrun.StatusRunRow)
    settle()
    ph = Phases()
    phases = ph.values
    phases['load_ms'] = round((ph.t - cpu0) * 1000.0, 1)

    # Bounded by *work*, not by the clock. An earlier version ran a fixed
    # number of 40ms slices, which made the CPU figure a function of how
    # fast the machine is: a faster machine loads more posts in the same
    # window and appears to regress. Here the run ends when the same
    # TOTAL posts are loaded however many steps that took, so the work is
    # fixed and the CPU is comparable. Each step still only pumps for a
    # fixed slice, so decode, paging and streaming stay in flight --
    # that is the part being measured, and it does not affect how much
    # work is done.
    adjustment = page._scrolled.get_vadjustment()
    loaded = 0
    steps = 0
    while loaded < TOTAL and steps < CONCURRENT_MAX_STEPS:
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        pump(0.04)
        steps += 1
        loaded = count_loaded(page.model, timelinedata.BoostCarouselItem)
    if loaded != TOTAL:
        raise SystemExit('concurrent: loaded %d of %d statuses in %d steps'
                         ' (cap %d) -- the workload is not fixed, so this'
                         ' run cannot be compared with anything'
                         % (loaded, TOTAL, steps, CONCURRENT_MAX_STEPS))
    ph.mark('scroll_down_ms')
    phases['overlap_steps'] = steps
    phases['posts_loaded'] = loaded

    # Stream while still moving, not after quiescing.
    for k in range(STREAM):
        page.on_stream_update(fixtures.status(base, TOTAL + 1 + k, anchor))
        adjustment.set_value(max(0.0, adjustment.get_value() - 60))
        pump(0.03)
    ph.mark('stream_ms')
    rss_peak = rss_kb()

    probe.disconnect()
    window.set_child(None)
    window.destroy()
    del page, window, probe, adjustment
    for _ in range(3):
        gc.collect()
        settle()
    ph.mark('teardown_ms')
    return phases, rss_peak


def scenario_soak(port, anchor, client, base):
    """Several timeline pages at once.

    ``CARD_SHELLS_KEPT`` is an app-wide budget across every page, and
    ``Gtk.ListView`` keeps a couple of hundred shells alive per page
    whatever the scroll position -- so the interesting question is what
    a *second* and *third* page cost once the first has taken its share
    of the budget. v1 never opens a second page.
    """
    from src import statusrun, timelinedata
    # A stack, not a vertical box: the app's sidebar places each live in
    # their own page with one visible at a time. Stacking three timelines
    # in one window would give each a third of the height and measure
    # nothing like what a reader gets.
    stack = Gtk.Stack()
    pages = []
    for index in range(SOAK_PAGES):
        page = build_page(client)
        stack.add_titled(page, 'page%d' % index, 'Page %d' % index)
        pages.append(page)
    window = make_window(stack, 700, 1000)
    probe = PaintProbe(window, stack, statusrun.StatusRunRow)
    settle()
    ph = Phases()
    phases = ph.values
    phases['load_ms'] = round((ph.t - cpu0) * 1000.0, 1)
    per_page_rss = []
    # Sampled continuously, because the peak is not the end state: memory
    # can peak mid-scroll as textures are decoded and drop again as the
    # LRU evicts. An earlier version computed the peak as
    # RSS_START + max(page growth), which adds the biggest single page's
    # growth to the starting figure and is not any measurement at all.
    rss_peak = RSS_START
    for index, page in enumerate(pages):
        stack.set_visible_child_name('page%d' % index)
        settle()
        rss_peak = max(rss_peak, rss_kb())
        opened = rss_kb()
        adjustment = page._scrolled.get_vadjustment()
        steps = 0
        while steps < SOAK_STEPS and not page.model.exhausted:
            adjustment.set_value(adjustment.get_upper()
                                 - adjustment.get_page_size())
            settle()
            steps += 1
            if steps % 4 == 0:
                rss_peak = max(rss_peak, rss_kb())
        rss_peak = max(rss_peak, rss_kb())
        per_page_rss.append(round((rss_kb() - opened) / 1024.0, 1))
    ph.mark('scroll_down_ms')
    phases['soak_pages'] = SOAK_PAGES
    phases['soak_steps'] = SOAK_STEPS
    # One key per page, not a list: a list in a METRIC line can only be
    # printed from run 1, which is not a median of anything.
    for index, growth in enumerate(per_page_rss):
        phases['soak_rss_page%d_mb' % (index + 1)] = growth
    phases['posts_loaded'] = sum(count_loaded(page.model,
                                             timelinedata.BoostCarouselItem)
                                 for page in pages)

    probe.disconnect()
    window.set_child(None)
    window.destroy()
    del pages, stack, window, probe
    for _ in range(3):
        gc.collect()
        settle()
    ph.mark('teardown_ms')
    return phases, rss_peak


def scenario_firstload(port, anchor, client, base):
    """Cold start to first painted card.

    The number a reader actually experiences, and the one the docs record
    only by hand. Reported as a single headline number plus the dispatch
    and decode context around it.
    """
    from src import statusrun
    page = build_page(client)
    window = make_window(page)
    probe = PaintProbe(window, page, statusrun.StatusRunRow)
    settle()
    if page.stack.get_visible_child_name() != 'list':
        raise SystemExit('first page did not load')
    ph = Phases()
    phases = ph.values
    phases['load_ms'] = round((ph.t - cpu0) * 1000.0, 1)
    probe.disconnect()
    window.set_child(None)
    window.destroy()
    del page, window, probe
    for _ in range(3):
        gc.collect()
        settle()
    ph.mark('teardown_ms')
    return phases, rss_kb()


SCENARIOS = {
    'scroll': scenario_scroll,
    'concurrent': scenario_concurrent,
    'soak': scenario_soak,
    'firstload': scenario_firstload,
}


def run(port, anchor):
    base = 'http://127.0.0.1:%d' % port
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(SRC, 'style.css'))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    from src import statusrun
    instrument(statusrun)
    client = make_client(base)

    global cpu0, RSS_START
    gc.collect()
    settle()
    C.recording = True
    cpu0 = cpu()
    wall0 = time.monotonic()
    RSS_START = rss_start = rss_kb()
    t0 = wall0

    phases, rss_peak = SCENARIOS[SCENARIO](port, anchor, client, base)

    C.recording = False
    gc.collect()
    stats = server_stats(port)
    live = live_counts()
    rss_end = rss_kb()
    emit(phases, stats,
         {'t0': t0, 'cpu0': cpu0, 'wall0': wall0, 'rss_start': rss_start,
          'rss_peak': rss_peak, 'rss_end': rss_end, 'live': live})


def main():
    if SCENARIO not in SCENARIOS:
        raise SystemExit('BENCH_SCENARIO must be one of: %s'
                         % ', '.join(sorted(SCENARIOS)))
    server, port, anchor = start_server(TOTAL)
    try:
        run(port, anchor)
    finally:
        server.kill()
        server.wait()


if __name__ == '__main__':
    main()
