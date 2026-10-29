# SPDX-License-Identifier: GPL-3.0-or-later
"""Asynchronous Mastodon fetch state machine for Catch Up."""

import dataclasses
import datetime
import functools

from gi.repository import Gio, GLib, GObject

from . import pipeline

from .digest import (PAGE_LIMIT, apply_filters, fetch_progress,
                     followed_tag_hits, mark_followed_tags, mark_threads,
                     parse_time, relationship_ids)
from .pipeline import (HTTP_TOO_MANY_REQUESTS, RELATIONSHIP_BATCH,
                       STOPPED_CANCELLED, STOPPED_COMPLETE, STOPPED_ERROR,
                       STOPPED_RATE_LIMITED, TREND_LIMITS)

def save_result(result, account_key, period, viewer_languages,
                hide_trending):
    """Persist a completed run and derive its page data on the worker."""
    from . import store, pipeline

    snapshot = pipeline.make_snapshot(result, account_key, period)
    try:
        store.write_snapshot(snapshot)
        saved = True
    except OSError:
        saved = False
    metas = store.prune(snapshot['account']) if saved else None
    return saved, metas, pipeline.run(snapshot, viewer_languages,
                                      hide_trending)

@dataclasses.dataclass
class RunResult:
    posts: list
    stopped: str
    start_at: datetime.datetime | None
    end_at: datetime.datetime
    error: str = ''
    retry_after: int = 0
    evaluated: int = 0
    followed_tags: list = dataclasses.field(default_factory=list)
    relationships: dict = dataclasses.field(default_factory=dict)
    trends: dict = dataclasses.field(default_factory=dict)
def run_in_thread(work, done):
    """Call ``work()`` on a worker thread and ``done(value, error)`` on the
    main context with its return value, or ``None`` and the exception.
    *work* must touch plain data only: no widget, no GSettings."""

    def thread(task, _source, _data, _cancellable):
        try:
            outcome = (work(), None)
        except Exception as failure:  # handed to done()
            outcome = (None, failure)
        # Always return, or the task never calls back.
        task.return_value(GObject.Value(GObject.TYPE_PYOBJECT, outcome))

    def finished(_source, task, _data):
        _ok, outcome = task.propagate_value()
        done(*outcome)

    task = Gio.Task.new(None, None, finished, None)
    task.run_in_thread(thread)


class CatchUpRun:
    """One catch-up: the home timeline paged back to ``start_at``, then the
    followed tags, the relationships and the trends, handed to ``done``
    as a :class:`RunResult`.

    It holds no widget. ``progress(evaluated, oldest_created_at)`` and
    ``done(result)`` are the page's weak methods; ``abort()`` (page closed,
    account changed) cancels the one Gio.Cancellable and silences both,
    ``stop()`` (the Cancel button) ends the paging like a 429 does and
    keeps what was gathered.
    """

    def __init__(self, client, start_at, end_at, self_ids=(),
                 hide_trending=False, progress=None, done=None,
                 pause_seconds=None):
        self.client = client
        self.start_at = start_at
        self.end_at = end_at
        self.self_ids = list(self_ids)
        self.hide_trending = hide_trending
        self.progress = progress
        self.done = done
        self.pause_seconds = pipeline.FETCH_PAUSE_SECONDS \
            if pause_seconds is None else pause_seconds
        self.cancellable = Gio.Cancellable()
        self.aborted = False
        self.finished = False
        self.kept = []
        self.evaluated = 0
        self.pages_done = 0
        self.oldest = None
        self.stopped = STOPPED_COMPLETE
        self.error = ''
        self.retry_after = 0
        self._seen = set()
        self._stop_requested = False
        self._busy = False
        self._timer = 0
        self._followed = []
        self._hits = {}
        self._pending_ids = []
        self._relationships = {}
        self._trends = {}
        self._trends_left = 0
        # Cursor for the next page of a Link-paginated endpoint, by path.
        # See _get.
        self._more = {}

    # Control

    def start(self):
        self._request_page({'limit': PAGE_LIMIT})

    def abort(self):
        self.aborted = True
        self.cancellable.cancel()
        self._clear_timer()

    def stop(self):
        """Stop paging and go on with what was gathered."""
        if self.aborted or self.finished or self._stop_requested:
            return
        self._stop_requested = True
        if self._timer:
            self._clear_timer()
            self._end_paging(STOPPED_CANCELLED)

    def _clear_timer(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # Paging

    def _request_page(self, params):
        if self.aborted:
            return
        if self.client.rate_limited():
            self.retry_after = self.client.retry_after_seconds()
            self._end_paging(STOPPED_RATE_LIMITED)
            return
        self._busy = True
        self.client.request_async(
            'GET', '/timelines/home', params=params,
            callback=self._on_page, cancellable=self.cancellable)

    def _on_page(self, _client, result, _data=None):
        self._busy = False
        if self.aborted:
            return
        try:
            page, next_params, _prev = self.client.request_finish_paged(
                result)
            if not isinstance(page, list):
                raise GLib.Error('Unexpected response',
                                 'mastodon-http-error-quark', 0)
        except GLib.Error as error:
            self._on_page_error(error)
            return
        self.pages_done += 1
        self.evaluated += len(page)
        for status in page:
            created = parse_time(status.get('created_at'))
            if created is not None and (self.oldest is None
                                        or created < self.oldest):
                self.oldest = created
        items, reason = fetch_progress(page, self.start_at, len(self.kept),
                                       self.pages_done)
        for status in items:
            if status['id'] not in self._seen:
                self._seen.add(status['id'])
                self.kept.append(status)
        if self.progress is not None:
            self.progress(self.evaluated, self.oldest)
        if reason is None and self._stop_requested:
            reason = STOPPED_CANCELLED
        if reason is not None:
            self._end_paging(reason)
            return
        params = dict(next_params) if next_params else {
            'max_id': page[-1]['id']}
        params['limit'] = str(PAGE_LIMIT)
        self._wait_then(functools.partial(self._request_page, params))

    def _on_page_error(self, error):
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            if self.aborted:
                return
            self._end_paging(STOPPED_CANCELLED)
        elif error.code == HTTP_TOO_MANY_REQUESTS:
            self.retry_after = self.client.retry_after_seconds()
            self.error = error.message
            self._end_paging(STOPPED_RATE_LIMITED)
        else:
            self.error = error.message
            self._end_paging(STOPPED_ERROR)

    def _wait_then(self, func):
        """Run *func* after the pause between two pages (a source the
        run removes when it is aborted or stopped)."""

        def fire():
            self._timer = 0
            if not self.aborted:
                func()
            return GLib.SOURCE_REMOVE

        if self.pause_seconds:
            self._timer = GLib.timeout_add_seconds(self.pause_seconds, fire)
        else:
            self._timer = GLib.idle_add(fire)

    def _end_paging(self, reason):
        if self.aborted or self.finished:
            return
        self.stopped = reason
        if not self.kept:
            self._finish()
            return
        self.kept = apply_filters(self.kept, self.self_ids, 'home',
                                  self.end_at)
        self._get('/followed_tags', {'limit': 200}, self._on_followed_tags)

    # Followed tags, relationships, trends (each degrades, never fails)

    def _get(self, path, params, done):
        if self.aborted:
            return
        if self.client.rate_limited():
            done(None)
            return

        def finished(_client, result, _data=None):
            if self.aborted:
                return
            # request_finish_paged also returns the Link header's cursors.
            # A single page is all this ever asked for before, which
            # silently capped the endpoints Mastodon pages -- followed
            # tags most visibly, at 200 of them.
            try:
                data, next_params, _prev = \
                    self.client.request_finish_paged(result)
            except GLib.Error:
                data, next_params = None, None
            self._more[path] = next_params
            done(data)

        self.client.request_async('GET', path, params=params,
                                  callback=finished,
                                  cancellable=self.cancellable)

    def _on_followed_tags(self, data):
        # An account may follow more tags than one page holds, and the
        # cursor is the only record of where the next one starts.
        for tag in data or []:
            if isinstance(tag, dict) and tag.get('name'):
                self._followed.append(tag['name'])
        more = self._more.get('/followed_tags')
        if more:
            self._get('/followed_tags', more, self._on_followed_tags)
            return
        self._followed = list(dict.fromkeys(self._followed))
        self._hits = followed_tag_hits(self.kept, self._followed)
        self._pending_ids = relationship_ids(self.kept, self._hits)
        self._next_relationships()

    def _next_relationships(self):
        if not self._pending_ids:
            self._mark_and_trends()
            return
        batch = self._pending_ids[:RELATIONSHIP_BATCH]
        self._pending_ids = self._pending_ids[RELATIONSHIP_BATCH:]
        self._get('/accounts/relationships', {'id[]': batch},
                  self._on_relationships)

    def _on_relationships(self, data):
        for rel in data or []:
            if isinstance(rel, dict) and rel.get('id'):
                self._relationships[str(rel['id'])] = {
                    'following': bool(rel.get('following')),
                    'followed_by': bool(rel.get('followed_by'))}
        self._next_relationships()

    def _mark_and_trends(self):
        mark_followed_tags(self.kept, self._hits, self._relationships)
        mark_threads(self.kept)
        self._trends = {}
        if self.hide_trending:
            self._finish()
            return
        self._trends_left = len(TREND_LIMITS)
        for key, path, limit in TREND_LIMITS:
            self._get(path, {'limit': limit},
                      functools.partial(self._on_trend, key))

    def _on_trend(self, key, data):
        self._trends[key] = data if isinstance(data, list) else None
        self._trends_left -= 1
        if self._trends_left == 0:
            self._finish()

    def _finish(self):
        if self.aborted or self.finished:
            return
        self.finished = True
        self._clear_timer()
        if self.done is not None:
            self.done(RunResult(
                posts=self.kept, stopped=self.stopped,
                start_at=self.start_at, end_at=self.end_at,
                error=self.error, retry_after=self.retry_after,
                evaluated=self.evaluated, followed_tags=self._followed,
                relationships=self._relationships,
                trends=self._trends))

