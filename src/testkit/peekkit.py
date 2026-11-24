# SPDX-License-Identifier: GPL-3.0-or-later
"""Fakes and helpers of the Smart New Posts tests (``test-feedchef-peek.py``
and ``test-feedchef-peek-page.py``): a fake engine that answers a peek from a
worker thread, a fake logger, a fake system monitor and a recipe page over
them. Import it after the test has set its environment."""

import gc
import threading
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, GObject  # noqa: E402

from src import feedchefpages  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchef.recipe import default_recipe  # noqa: E402


def status(number, text='post', author=None):
    return {'id': '%05d' % number,
            'content': '<p>%s</p>' % text,
            'tags': [],
            'account': {'acct': 'user%d' % number,
                        'id': str(author or number)},
            'created_at': '2026-01-01T00:00:00.000Z'}


def pump(iterations=30):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(2000)


def pump_until(condition, seconds=5.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1e6)
    while not condition() and GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        GLib.usleep(2000)
    return condition()


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        pump(3)
        if ref() is None:
            return True
    return ref() is None


class FakeLogger:
    """What a peek's logger does that the page may call."""

    def __init__(self, ids):
        self.record = {'outcome': {'status': 'peek', 'error': ''},
                       'posts': [{'id': sid} for sid in ids],
                       'outputs': {'order': list(ids)}}
        self.log = []

    def mark_shown(self, merge, write=True):
        self.record['outcome'] = {'status': 'shown', 'error': ''}
        self.record['merge'] = merge
        self.log.append(('shown', threading.current_thread().name))
        return True

    def writer(self):
        def write():
            self.log.append(('flush', threading.current_thread().name))
        return write

    def discard(self):
        self.log.append(('discard', threading.current_thread().name))


class PeekEngine:
    """Answers each cook from a queue; a peek is answered from a worker
    thread, as the real engine does, and lands on the main loop."""

    made = []

    def __init__(self):
        self.calls = []
        self.queue = []
        self.peeks = []
        self.peek_queue = []
        self.loggers = []
        self.cancelled = 0
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='fake-engine')
        PeekEngine.made.append(self)

    def init_preload(self):
        pass

    def cancel(self):
        self.cancelled += 1

    def shutdown(self):
        self.pool.shutdown(wait=False)

    def run_background(self, work):
        self.pool.submit(work)

    def process_feed(self, recipe, *, max_id=None, count=80, on_stage=None,
                     on_done=None, on_error=None, author_counts=None,
                     hide=None, cook_id=None, page_index=0, hop=0,
                     instance_counts=None, peek=False, exclude=None):
        if peek:
            self.peeks.append({
                'author_counts': dict(author_counts or {}),
                'exclude': set(exclude or ()), 'cook_id': cook_id,
                'page_index': page_index, 'thread': None,
                'main': threading.current_thread()})
            entry = self.peeks[-1]
            items, scores, counts = self.peek_queue.pop(0)
            logger = FakeLogger([s['id'] for s in items])
            self.loggers.append(logger)

            def work():
                entry['thread'] = threading.current_thread()
                GLib.idle_add(lambda: on_done(FeedResult(
                    items=items, next_page_max_id=None, has_more=True,
                    score_map=scores, author_counts=counts,
                    kitchen_meta={}, logger=logger)) and False)
            threading.Thread(target=work).start()
            return 'peek'
        self.calls.append((max_id, dict(author_counts or {})))
        items, has_more, cursor, scores, counts = self.queue.pop(0)

        def deliver():
            on_done(FeedResult(
                items=items, next_page_max_id=None, has_more=has_more,
                scope_oldest_id=cursor, score_map=scores,
                author_counts=counts, kitchen_meta={}))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
        return 'fake'


class FakeMonitor(GObject.Object):
    """The system state as plain properties (no Gio monitor)."""
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'

    def rate_limited(self):
        return False


def make_page(window, account, client, first_posts=6):
    """A recipe page in a navigation view in *window*, its first page
    loaded; (page, engine, view)."""
    recipe = default_recipe('Peek')
    store = feedchefpages.storage_for(account)
    store.save_recipe(recipe)
    descending = {status(n)['id']: 1.0 - n / 10.0
                  for n in range(1, first_posts + 1)}
    feedchefpages.make_engine = lambda *_args: _engine
    _engine = PeekEngine()
    _engine.queue = [(
        [status(n) for n in range(1, first_posts + 1)], False, None,
        descending, {'user%d' % n: 1 for n in range(1, first_posts + 1)})]
    page = feedchefpages.FeedChefTimelinePage(client, account, recipe)
    nav = Adw.NavigationPage(title='Peek', child=page)
    view = Adw.NavigationView()
    view.add(nav)
    window.set_child(view)
    pump_until(lambda: page.model.get_n_items() == first_posts)
    pump(10)
    return page, _engine, view
