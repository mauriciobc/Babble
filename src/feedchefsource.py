# SPDX-License-Identifier: GPL-3.0-or-later
"""TimelinePage source over a Feed Chef cook.

TimelinePage asks a source for pages with ``fetch(params, callback,
cancellable)`` and decodes the answer with ``finish(result)``. A cook is
not an id-paged read: its older pages continue from the scope's own
cursor (``FeedResult.scope_oldest_id``), not from the oldest id on screen,
which a merged hashtag or More Like This post would pull too far back. The
source therefore ignores TimelinePage's cursor and keeps its own.
"""

from functools import partial

from gi.repository import Gio, GLib, GObject

# Posts asked of the engine for one page.
COOK_COUNT = 40
# A page the diet and the kitchen emptied is followed by the next older one
# rather than shown as the end, up to this many times in a row.
MAX_EMPTY_HOPS = 3


def _batch(items):
    """A Python list as a task value: a plain list cannot go through
    ``Gio.Task.return_value``, a PYOBJECT GValue can."""
    return GObject.Value(GObject.TYPE_PYOBJECT, items)


class _Cook:
    """One ``fetch()``: its task, and whether it is still open."""

    __slots__ = ('task', 'cancellable', 'handler', 'done')

    def __init__(self, task, cancellable):
        self.task = task
        self.cancellable = cancellable
        self.handler = 0
        self.done = False


class FeedChefSource:
    """Source protocol of ``TimelinePage`` over ``FeedEngine.process_feed``.

    ``paging = 'cook'`` is neither 'id' nor 'link': TimelinePage then does
    not trim the list (the dropped tail could not be fetched again by an id)
    and refreshes by since_id, which this source does not read. A refresh
    (no ``max_id``) cooks the first page again; an older page cooks from the
    scope cursor of the last result, and is an empty batch once the engine
    reports no more."""

    paginated = True
    paging = 'cook'

    def __init__(self, engine, recipe):
        self.engine = engine
        self.recipe = recipe
        # Called with the stage name as the engine finishes a stage; set by
        # the page with a weak method, so the source never holds the page.
        self.stage_listener = None
        self._cursor = None
        self._has_more = False
        self._active = None
        # The last batch was empty after MAX_EMPTY_HOPS hops and the
        # scope still has older posts: not the end, only a pause.
        self.stalled = False

    @property
    def cursor(self):
        """The scope's own cursor: where the next older cook starts."""
        return self._cursor

    def fetch(self, params, callback, cancellable=None):
        task = Gio.Task.new(None, cancellable, callback, None)
        older = 'max_id' in params
        if cancellable is not None and cancellable.is_cancelled():
            task.return_error_if_cancelled()
            return
        if older and not (self._has_more and self._cursor):
            task.return_value(_batch([]))
            return
        cook = _Cook(task, cancellable)
        self._active = cook
        if cancellable is not None:
            cook.handler = cancellable.connect(partial(self._cancelled, cook))
        if not older:
            self._cursor = None
            self._has_more = False
            self.stalled = False
        self._start(cook, self._cursor if older else None, 0)

    def finish(self, result):
        """The statuses of the page, in kitchen order. Raises GLib.Error
        when the cook failed or was cancelled."""
        _ok, items = result.propagate_value()
        return items

    def close(self):
        """Stop the cook in flight; a task still open answers CANCELLED
        (a Gio.Task must be answered).

        That answer is dropped silently only when the page's request scope
        has already cancelled or superseded the request, which cancels the
        task through its cancellable first and leaves nothing open here.
        The page therefore ends its generation before it calls this
        (``_close_paging_scope()`` on dispose, ``_scope.begin()`` when
        it cooks another recipe); otherwise the reader would be shown an
        error page for something they did."""
        cook, self._active = self._active, None
        self.engine.cancel()
        if cook is not None and not cook.done:
            self._settle(cook)
            cook.task.return_error(GLib.Error(
                'Cancelled', 'g-io-error-quark',
                int(Gio.IOErrorEnum.CANCELLED)))

    def _start(self, cook, max_id, hops):
        self.engine.process_feed(
            self.recipe, max_id=max_id, count=COOK_COUNT,
            on_stage=partial(self._on_stage, cook),
            on_done=partial(self._on_done, cook, hops),
            on_error=partial(self._on_error, cook))

    def _settle(self, cook):
        cook.done = True
        if cook.handler and cook.cancellable is not None:
            cook.cancellable.disconnect(cook.handler)
            cook.handler = 0

    def _cancelled(self, cook):
        if cook.done:
            return
        cook.done = True
        if self._active is cook:
            self._active = None
            self.engine.cancel()
        cook.task.return_error_if_cancelled()

    def _on_stage(self, cook, stage, _items):
        if cook.done or cook is not self._active:
            return
        if self.stage_listener is not None:
            self.stage_listener(stage)

    def _on_done(self, cook, hops, result):
        if cook.done or cook is not self._active:
            return
        self._cursor = result.scope_oldest_id
        self._has_more = bool(result.has_more and result.scope_oldest_id)
        if not result.items and self._has_more and hops < MAX_EMPTY_HOPS:
            self._start(cook, self._cursor, hops + 1)
            return
        self.stalled = bool(not result.items and self._has_more)
        self._settle(cook)
        self._active = None
        cook.task.return_value(_batch(list(result.items)))

    def _on_error(self, cook, error):
        if cook.done or cook is not self._active:
            return
        self._settle(cook)
        self._active = None
        if not isinstance(error, GLib.Error):
            error = GLib.Error(str(error) or 'Could not cook this recipe',
                               'g-io-error-quark',
                               int(Gio.IOErrorEnum.FAILED))
        cook.task.return_error(error)
