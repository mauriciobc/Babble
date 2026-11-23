# SPDX-License-Identifier: GPL-3.0-or-later
"""TimelinePage source over a Feed Chef cook.

TimelinePage asks a source for pages with ``fetch(params, callback,
cancellable)`` and decodes the answer with ``finish(result)``. A cook is
not an id-paged read: its older pages continue from the scope's own
cursor (``FeedResult.scope_oldest_id``), not from the oldest id on screen,
which a merged hashtag or More Like This post would pull too far back. The
source therefore ignores TimelinePage's cursor and keeps its own.
"""

import datetime
import uuid
from functools import partial

from gi.repository import Gio, GLib, GObject

from . import filterrules
from .feedchef.engine import ScopeFetchError
from .feedchef.network_reach import FollowListError
from .i18n import gettext as _

# Posts asked of the engine for one page.
COOK_COUNT = 40
# A page the diet and the kitchen emptied is followed by the next older one
# rather than shown as the end, up to this many times in a row.
MAX_EMPTY_HOPS = 3


def viewer_hides(status, now=None):
    """Whether the viewer's own filters hide *status* in this timeline.

    The filter context is the one TimelinePage gives a source that has no
    path (``timelinepage.filter_context_for``): the home one. Only the
    action 'hide' counts; a 'warn' or 'blur' post stays in the page, which
    shows it collapsed or covered with a Show Anyway button. Called by the
    engine before the kitchen, so a hidden post uses no one's slot of
    "Limit Posts per Person". *now* is the page's one clock reading, which
    the engine passes (``takes_now``)."""
    rule = filterrules.strongest(
        status, filterrules.HOME,
        now or datetime.datetime.now(datetime.timezone.utc))
    return rule is not None and rule[0] == filterrules.HIDE


viewer_hides.takes_now = True

# The audit records of the last pages of a cook kept for Why This Post?.
AUDIT_PAGES = 10


def _batch(items):
    """A Python list as a task value: a plain list cannot go through
    ``Gio.Task.return_value``, a PYOBJECT GValue can."""
    return GObject.Value(GObject.TYPE_PYOBJECT, items)


class _Cook:
    """One ``fetch()``: its task, and whether it is still open."""

    __slots__ = ('task', 'cancellable', 'handler', 'done', 'older',
                 'counts', 'servers', 'cook_id', 'page_index')

    def __init__(self, task, cancellable, older, counts, cook_id='',
                 page_index=0, servers=None):
        # The cook session this page belongs to: a refresh starts a new
        # one, an older page goes on with the session's.
        self.cook_id = cook_id
        self.page_index = page_index
        self.task = task
        self.cancellable = cancellable
        self.handler = 0
        self.done = False
        self.older = older
        # The person counts the next hop of this cook starts from: the
        # session's, or none for a refresh.
        self.counts = counts
        # The same for the servers of the posts (the 'instance' dimension
        # of "Limit Posts per Person").
        self.servers = servers if servers is not None else {}


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
        # Posts per person shown so far in this cook session (first page
        # and every older one): "Limit Posts per Person" caps the whole
        # session, and a refresh starts it again.
        self._author_counts = {}
        self._instance_counts = {}
        # Why the last cook failed: 'follow-list' or None.
        self.error_kind = None
        self._cook_id = ''
        self._page_index = -1
        # The audit records of the pages shown (newest last) and the last
        # one; the engine builds them, the source only keeps them.
        self.last_audit = None
        self._audits = []

    def audit_for(self, status_id):
        """The audit entry of a post of the shown pages, with the page's
        record around it: ``(entry, record)``, or None when there is
        none (a post that is not from this cook, or an engine without an
        audit)."""
        wanted = str(status_id or '')
        for record in reversed(self._audits):
            for entry in record.get('posts') or []:
                if entry.get('id') == wanted:
                    return entry, record
        return None

    def cook_audits(self):
        """The audit records of the pages the last cook showed, oldest
        first (a refresh starts the list again)."""
        return list(self._audits)

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
        # A refresh starts from nothing but changes nothing here until its
        # page lands (_on_done): one that fails or is cancelled leaves the
        # session as it was, and the older pages go on from its cursor.
        cook = _Cook(
            task, cancellable, older,
            dict(self._author_counts) if older else {},
            self._cook_id if older else uuid.uuid4().hex[:12],
            self._page_index + 1 if older else 0,
            dict(self._instance_counts) if older else {})
        # A cook this one supersedes, whose request nobody cancelled, is
        # answered now: a Gio.Task left unanswered is never finished.
        previous, self._active = self._active, cook
        if previous is not None and not previous.done:
            self._settle(previous)
            previous.task.return_error(GLib.Error(
                'Cancelled', 'g-io-error-quark',
                int(Gio.IOErrorEnum.CANCELLED)))
        if cancellable is not None:
            cook.handler = cancellable.connect(partial(self._cancelled, cook))
        self.error_kind = None
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
            on_error=partial(self._on_error, cook),
            author_counts=dict(cook.counts), hide=viewer_hides,
            cook_id=cook.cook_id, page_index=cook.page_index, hop=hops,
            instance_counts=dict(cook.servers))

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
        cursor = result.scope_oldest_id
        has_more = bool(result.has_more and cursor)
        # Only a page that is shown counts: a cook that was cancelled
        # never gets here, so a retry does not count its posts twice.
        cook.counts = dict(result.author_counts or {})
        cook.servers = dict(getattr(result, 'instance_counts', None) or {})
        if not result.items and has_more and hops < MAX_EMPTY_HOPS:
            self._start(cook, cursor, hops + 1)
            return
        # The session is replaced as one: a refresh that failed on a later
        # hop never reaches here, and keeps the old cursor and counts.
        self._cursor = cursor
        self._has_more = has_more
        self._author_counts = cook.counts
        self._instance_counts = cook.servers
        self._keep_audit(cook, result)
        self.stalled = bool(not result.items and has_more)
        self._settle(cook)
        self._active = None
        cook.task.return_value(_batch(list(result.items)))

    def _keep_audit(self, cook, result):
        """Remember the record of the page about to be shown; a refresh
        forgets the pages of the cook it replaces."""
        self._cook_id = cook.cook_id
        self._page_index = cook.page_index
        record = getattr(result.logger, 'record', None)
        if not cook.older:
            self._audits = []
        if record is not None:
            self.last_audit = record
            self._audits = (self._audits + [record])[-AUDIT_PAGES:]

    def _on_error(self, cook, error):
        if cook.done or cook is not self._active:
            return
        self._settle(cook)
        self._active = None
        if isinstance(error, ScopeFetchError):
            error = GLib.Error(
                _('The posts of this recipe did not load in time. Try '
                  'again.'),
                'g-io-error-quark', int(Gio.IOErrorEnum.TIMED_OUT))
        elif isinstance(error, FollowListError):
            self.error_kind = 'follow-list'
            error = GLib.Error(
                _('Your list of follows could not be loaded, so nothing is '
                  'shown rather than posts from people you do not follow.'),
                'g-io-error-quark', int(Gio.IOErrorEnum.FAILED))
        elif not isinstance(error, GLib.Error):
            error = GLib.Error(str(error) or 'Could not cook this recipe',
                               'g-io-error-quark',
                               int(Gio.IOErrorEnum.FAILED))
        cook.task.return_error(error)
