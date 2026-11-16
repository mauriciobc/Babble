# SPDX-License-Identifier: GPL-3.0-or-later

"""The page toolkit: paging and loading state for the secondary views.

* paging: :class:`PagedPosts` (the state machine every status list runs),
  :class:`PagedList` (any Link-paginated endpoint rendered as a boxed
  list of rows)
* loading: :class:`StateStack`, :func:`make_loading_page`,
  :func:`make_error_page`, :func:`configure_empty_page`
* requests: :func:`call_api` for one-shot calls

Not here, on purpose: the window and page navigation (:mod:`pagenav`),
toasts, errors and dialogs (:mod:`feedback`), links (:mod:`linkrouting`),
the shared ``Gio.Settings`` (:mod:`appsettings`), the weak signal
helpers (:mod:`weakconnect`), generic widget helpers (:mod:`uihelpers`)
and account rows (:mod:`accountrows`). Each importer takes only the one
it needs, so this module does not grow back into the hub it was.
``module boundary`` fails on a use of a name this module does not
define.
"""

import logging
import types

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from .api import core as api_core  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .requestscope import RequestScope  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

_log = logging.getLogger(__name__)

COLUMN_WIDTH = 660
PAGE_SIZE = 40
# How close to the bottom (in pixels) a scrolled list gets before it asks
# for its next page. One number for ``PagedPosts`` and ``PagedList``.
PRELOAD = 800


def _unlock_bottom(page_ref):
    """The bottom lock's deadline. A GObject weak ref, so a closed page is
    never kept alive by the timeout that exists to release it."""
    page = page_ref()
    if page is None:
        return GLib.SOURCE_REMOVE
    page._bottom_lock_id = 0
    page._bottom_locked = False
    page._run_pending_older()
    return GLib.SOURCE_REMOVE


def near_bottom(adjustment, preload=PRELOAD):
    """Whether *adjustment*'s view is within *preload* pixels of the end."""
    bottom = adjustment.get_value() + adjustment.get_page_size()
    return bottom >= adjustment.get_upper() - preload


class PagedPosts:
    """The paging state machine every status list runs.

    Three copies existed -- ``TimelinePage``, ``NotificationsPage`` and
    ``pages.PagedList`` -- and they had drifted: two page sizes, three
    preload distances, ``since_id`` in one and ``min_id`` in another, and
    a generation guard present in two of the three. A list that pages
    wrongly does not look wrong, so the fix is one implementation.

    ``PagedList`` no longer inherits this: it pages boxed rows by the
    ``Link`` header, not by status id. What the two share is the preload
    distance, :data:`PRELOAD`, and :func:`near_bottom`.

    Subclasses supply the model and how to put items in it; this owns the
    flags, the cursors and the three requests. Ownership of in-flight
    reads is a :class:`~.requestscope.RequestScope` plus per-request
    ``on_drop`` helpers: every paging read uses ``begin()`` (including
    ``load_older``, so a refresh in flight is superseded), and
    subclasses must close the scope from ``do_dispose`` via
    :meth:`_close_paging_scope`.

    * :meth:`load` fills an empty list, falling back to :meth:`load_older`
      while pages come back empty of visible rows;
    * :meth:`load_older` pages back, stopping when the model says so, behind
      a debounce that always releases itself (see :meth:`_lock_bottom`);
    * :meth:`refresh` asks for what is newer than what is shown.

    Which cursor each uses is a constructor argument rather than a
    difference between two copies: ``min_id`` for the notification list
    (Mastodon requires it there), ``since_id`` for a timeline.
    """

    # One page size for every status list (timelines and notifications).
    # The Link-paged boxed lists of ``PagedList`` page by the module's
    # ``PAGE_SIZE``, 40.
    PAGE_SIZE = 20
    # How close to the bottom a list gets before fetching the next page:
    # the module's one number, shared with ``PagedList``.
    PRELOAD = PRELOAD
    # How long the bottom lock is held after a page goes out. Long enough
    # that one flick of the wheel cannot queue a second request, short
    # enough that a reader who stops scrolling is never left waiting.
    BOTTOM_LOCK_MS = 1000

    model = None

    def _paging_init(self, newer_cursor='since_id'):
        self._scope = RequestScope()
        # The newest request. begin() supersedes every earlier one, so it
        # is the only ticket that can still be wanted.
        self._ticket = None
        # Whether that request owns ``model.loading`` (a first page and a
        # refresh do, an older page does not).
        self._ticket_owns_loading = False
        # The refresh that owns the catch-up chain, if any.
        self._refresh_ticket = None
        self._loading_older = False
        self._newer_cursor = newer_cursor
        self._bottom_locked = False
        self._older_pending = False
        self._bottom_lock_id = 0

    def _close_paging_scope(self):
        scope = getattr(self, '_scope', None)
        if scope is not None:
            scope.close()

    def close_requests(self):
        """Cancel what is in flight and refuse new requests, now.

        For an owner that is dropped while something may still hold the
        page (a reference cycle keeps it until the next collection): an
        answer must not land on it. Safe to call twice."""
        self._close_paging_scope()

    def has_loaded(self):
        """Whether a first load has completed: there are items, or the
        page settled on its empty state. A page never loaded, loading its
        first page or showing an error is not, and a catch-up has nothing
        to catch up on it."""
        return (self.model.get_n_items() > 0
                or self.stack.get_visible_child_name() == 'empty')

    # Requests

    @staticmethod
    def _loading_owned(owner):
        """Whether a wanted request owns ``model.loading``."""
        ticket = owner._ticket
        return (ticket is not None and owner._ticket_owns_loading
                and ticket.wanted())

    @staticmethod
    def _drop_loading(owner):
        # A newer first-page load or refresh owns ``model.loading``: leave
        # it and the subclass's quiet flag alone.
        if PagedPosts._loading_owned(owner):
            return
        owner.model.loading = False
        owner._loading_released()

    @staticmethod
    def _drop_older(owner):
        # A stale older request (cancelled by a reload) must not end a newer
        # older request that is live: leave its flag and bottom lock alone.
        ticket = owner._ticket
        if (ticket is not None and not owner._ticket_owns_loading
                and ticket.wanted() and owner._loading_older):
            return
        owner._loading_older = False
        owner._release_bottom()
        owner._maybe_run_sweep()

    @staticmethod
    def _drop_refresh(owner):
        # Only the refresh that still owns ``_refresh_ticket`` ends the
        # chain. A newer refresh sets the field to None before begin(), or
        # replaces it with a wanted ticket: leave loading and the chain
        # alone. Supersession by load_older keeps the cancelled ticket in
        # the field, so this path runs; ``_chain_ended`` defers its sweep
        # while that older page is out.
        current = owner._refresh_ticket
        if current is None or current.wanted():
            return
        owner._refresh_ticket = None
        if not PagedPosts._loading_owned(owner):
            owner.model.loading = False
            owner._loading_released()
        owner._chain_ended()

    def _chain_ended(self, sweep=True):
        """Hook for a refresh chain ending; TimelinePage overrides."""

    def _maybe_run_sweep(self):
        """Hook: an older page ended; ReadingPosition runs a deferred
        catch-up sweep here."""

    def _loading_released(self):
        """Hook: a dropped request gave up ``model.loading``;
        NotificationController clears its quiet flag here."""

    def _request(self, params, on_done, *, on_drop, owns_loading=False):
        """Queue one page request under the paging scope; return its ticket.

        A response that outlived its request is dropped rather than
        spliced into the model -- a superseded refresh would otherwise
        land stale posts in a list the reader is looking at. Dropping it
        also clears the busy flags, or a discarded first page would leave
        the list reading as loading forever. *on_drop* must be a function
        of the owner (``PagedPosts._drop_*``), never a bound method: the
        ticket holds the owner weakly and a bound method would not.
        """
        ticket = self._begin_request(owns_loading)
        self._send_request(ticket, params, on_done, on_drop)
        return ticket

    def _begin_request(self, owns_loading):
        """Supersede the last request and make the newest one's ticket.

        Split from :meth:`_send_request` for a caller that must record the
        ticket before the send: an answer can be delivered synchronously
        (a test's fake client), and its handler reads that record."""
        ticket = self._scope.begin()
        self._ticket = ticket
        self._ticket_owns_loading = owns_loading
        return ticket

    def _send_request(self, ticket, params, on_done, on_drop):
        self._paging_fetch(
            params,
            ticket.callback(on_done, on_drop=on_drop),
            ticket.cancellable)

    def _paging_fetch(self, params, callback, cancellable):
        raise NotImplementedError

    # The three requests

    def load(self):
        if self._scope.closed or self.model.loading:
            return
        self.model.loading = True
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('loading')
        self._request(self._first_page_params(), self.on_page_loaded,
                      on_drop=PagedPosts._drop_loading, owns_loading=True)

    def _first_page_params(self):
        return {'limit': self.PAGE_SIZE}

    def load_older(self):
        if self._scope.closed:
            return
        if self.model.exhausted or self.model.oldest_id is None:
            # Nothing left to ask for, so a remembered request has no
            # target either.
            self._older_pending = False
            return
        if self._bottom_locked:
            # Remembered, not discarded. The reader is at the bottom of
            # the list and wants this page; whichever request releases the
            # lock is the one that runs it. Dropping it here is what left a
            # reader at the bottom of a list that would not load.
            self._older_pending = True
            return
        self._lock_bottom()
        self._loading_older = True
        self._request({'limit': self.PAGE_SIZE,
                       'max_id': self.model.oldest_id},
                      self.on_older_loaded, on_drop=PagedPosts._drop_older)

    def _lock_bottom(self):
        """Hold the bottom lock, with a deadline.

        The lock is a debounce, not a flag coupled to the request. It is
        released when the page lands (so a fast response does not cost the
        reader the whole debounce) *and* by the timeout whether or not the
        response ever arrives. That second release is what makes it safe: a
        dropped page, a superseded response, a cancelled request or a list
        torn down mid-flight all end with the lock open, so no combination
        of them can wedge a reader at the bottom of a list that will not
        load. A mutex on request state cannot promise that -- it has no way
        to run when the request it is waiting for is never answered.
        """
        self._bottom_locked = True
        if not self._bottom_lock_id:
            self._bottom_lock_id = GLib.timeout_add(
                self.BOTTOM_LOCK_MS, _unlock_bottom, self.weak_ref())

    def _release_bottom(self):
        """Release the lock now rather than waiting out the debounce.

        Every ending of an older page calls this. It deliberately leaves
        ``_loading_older`` alone: that flag means "a request is still out",
        which the deadline above cannot know, and :meth:`_trim_old` reads it
        to avoid dropping the tail while a page is still in flight.
        """
        if self._bottom_lock_id:
            GLib.source_remove(self._bottom_lock_id)
            self._bottom_lock_id = 0
        self._bottom_locked = False
        self._run_pending_older()

    def _run_pending_older(self):
        """Ask for the page the reader asked for while the lock was held.

        Skipped while a request is still out: that request will release the
        lock when it ends, and starting a second one now would supersede it
        and throw its page away.
        """
        if not self._older_pending or self._loading_older:
            return
        self._older_pending = False
        self.load_older()

    def refresh(self):
        """Load what is newer than the newest item shown."""
        if self._scope.closed or self.model.loading:
            return
        if self.model.get_n_items() == 0:
            self.load()
            return
        self.model.exhausted = False
        params = {'limit': self.PAGE_SIZE}
        if self.model.newest_id is not None:
            params[self._newer_cursor] = self.model.newest_id
        self.model.loading = True
        # Disown the previous refresh before begin().
        self._refresh_ticket = None
        ticket = self._begin_request(True)
        self._refresh_ticket = ticket
        self._send_request(ticket, params, self.on_refresh_loaded,
                           PagedPosts._drop_refresh)

    # Scroll

    def on_scroll_near_end(self, adjustment):
        """Whether the list is within ``PRELOAD`` of the bottom."""
        return near_bottom(adjustment, self.PRELOAD)


def call_api(widget, client, start, on_done=None, error_heading=None,
             finish=None):
    """Fire one request and hand the decoded body to *on_done(data)*;
    failures toast *error_heading* (default 'Action failed').

    *start(callback)* queues the request, so the path is never spelled
    here: ``lambda cb: statuses.get_async(client, id, callback=cb)``.
    *finish(client, result)* decodes the answer; pass the api module's own
    ``<name>_finish`` to honour its validation or unpacking. It defaults to
    ``api.core.finish``, the decoder of a request with no ``_finish`` of its
    own."""
    decode = finish or api_core.finish

    def finished(_client, result, _data=None):
        try:
            data = decode(client, result)
        except GLib.Error as error:
            feedback.report_error(widget, error_heading or _('Action failed'),
                                  error)
            return
        if on_done is not None:
            on_done(data)

    return start(finished)


# Status pages

def configure_empty_page(page, icon_name, title, description):
    """Fill the three fields every timeline wrapper sets on ``empty_page``."""
    page.set_icon_name(icon_name)
    page.set_title(title)
    page.set_description(description)


def make_loading_page(title, description=None):
    page = Adw.StatusPage(title=title, description=description)
    page.set_paintable(Adw.SpinnerPaintable.new(page))
    return page


def make_error_page(title, on_retry):
    """Error status page with a Try Again button calling
    ``on_retry(button)``. A bound method is held weakly: it is normally
    a method of the page that owns this error page, and a strong handler
    would keep that page alive forever."""
    page = Adw.StatusPage(icon_name='network-error-symbolic', title=title)
    retry = Gtk.Button(label=_('_Try Again'), use_underline=True,
                       halign=Gtk.Align.CENTER)
    retry.add_css_class('pill')
    retry.add_css_class('suggested-action')
    if isinstance(on_retry, types.MethodType):
        connect_weak(retry, 'clicked', on_retry)
    else:
        retry.connect('clicked', on_retry)
    page.set_child(retry)
    return page


class StateStack(Gtk.Stack):
    """A Gtk.Stack with the standard 'loading' and 'error' pages; the
    caller adds its content pages with ``add_named``. ``begin()`` starts
    a load and returns its ticket (a :class:`requestscope.Ticket`): the
    load's read passes ``token.cancellable`` and ``token.callback(handler)``
    to its api call, so a newer ``begin()`` cancels it and its answer never
    reaches the handler, which holds the page weakly. ``is_current(token)``
    asks the same question as the ticket (False once a newer load began or
    the stack was freed), for a caller with a request of its own.
    *on_retry* follows :func:`make_error_page` (a bound method is held weakly); *markup*
    turns an error into the escaped description."""

    __gtype_name__ = 'BabbleStateStack'

    def __init__(self, loading_title, error_title, on_retry, markup=None):
        super().__init__()
        self._scope = RequestScope()
        self._markup = markup or summarize_error_markup
        self.loading_page = make_loading_page(loading_title)
        self.error_page = make_error_page(error_title, on_retry)
        self.add_named(self.loading_page, 'loading')
        self.add_named(self.error_page, 'error')

    def begin(self):
        """Start a load: show the loading page, cancel the load it
        replaces, and return its ticket. The load's read passes
        ``token.cancellable`` to its api call."""
        token = self._scope.begin()
        self.show_loading()
        return token

    def is_current(self, token):
        """Whether the answer for *token* is still wanted: the same
        question as ``token.wanted()``."""
        return token.wanted()

    def do_dispose(self):
        # The stack is freed with its page, and only then: a page that is
        # merely hidden or cached keeps its stack, and its answer.
        scope = getattr(self, '_scope', None)
        if scope is not None:
            scope.close()
        Gtk.Stack.do_dispose(self)

    def show_loading(self):
        self.set_visible_child_name('loading')

    def show_error(self, error):
        self.error_page.set_description(self._markup(error))
        self.set_visible_child_name('error')

    def show(self, name):
        self.set_visible_child_name(name)


# Paged list

class PagedList(Adw.Bin):
    """A paged list: boxed rows that load page by page as the reader
    scrolls, over a Link-paginated endpoint.

    ``fetch_path``/``params`` describe the request; ``make_row(item)``
    builds one Gtk.Widget per JSON item (return None to skip it).
    ``extract(data)`` maps the response to the item list (default: the
    response itself). Loading, empty and error states are built in.

    Three things move together: ``rows`` (the row widgets, in order),
    ``list_box`` (their parent) and the page of ``stack`` that shows:
    ``loading``, ``error``, ``empty`` or ``list``. Only the operations
    below change them, so a caller never touches them:

    * :meth:`refresh` reloads from the top; :meth:`load_more` follows
      ``Link: rel=next`` (the page size is the module's ``PAGE_SIZE``);
    * :meth:`append_row`, :meth:`prepend_row`, :meth:`remove_row` change
      one row (removing the last shows the empty state, or keeps filling
      while more pages exist);
    * ``finish(client, result)`` decodes a response the way the api
      module's own ``<name>_finish_paged`` does (default
      ``api.core.finish_paged``);
    * ``page_filter(items)`` drops items of one page client-side; it sees
      the items after ``extract``, and ``finish`` returns
      ``(data, next_params, prev_params)``. When a page filters to nothing
      while more pages follow, the next one is asked for;
    * ``make_placeholder()`` builds a row shown in the list in place of
      the empty state whenever there are no rows, and dropped again as
      soon as one exists.

    What a caller must pass. The list keeps ``make_row``, ``extract``,
    ``finish``, ``page_filter`` and ``make_placeholder`` for as long as it
    lives, and the list is a child of its page. A hook that is a plain
    bound method of the page (or of any GObject that owns the list) closes
    a cycle through C that Python's collector cannot see, and the page and
    its list are never freed. So such a hook goes through
    :func:`weak_method`, as ``make_row`` does for the callers; a module
    function, a lambda that captures no widget, or a classmethod (the
    bound callable holds the class, not the page) is fine. So is a
    subclass of ``PagedList`` passing its own bound method to
    ``super().__init__`` (``AccountListPage``, ``ConversationsPage`` and
    the other shipped subclasses do): the list is then the owner itself,
    and there is no second object to form a cycle with. The ``paged
    list`` test covers a ``weak_method`` hook, a classmethod, and the
    real drafts and followed-hashtags pages being freed.

    It follows the ``Link`` header, not a status id, so it shares with
    ``PagedPosts`` only the preload distance (:data:`PRELOAD`)."""

    __gtype_name__ = 'BabblePagedList'

    def __init__(self, client, fetch_path, make_row, params=None,
                 empty_title=None, empty_icon='edit-find-symbolic',
                 empty_description=None, extract=None, paginated=True,
                 header=None, method='GET', finish=None, page_filter=None,
                 make_placeholder=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.fetch_path = fetch_path
        self.make_row = make_row
        self.params = dict(params or {})
        self.extract = extract or (lambda data: data)
        self.paginated = paginated
        self.method = method
        self.finish = finish or api_core.finish_paged
        self.page_filter = page_filter
        self.make_placeholder = make_placeholder
        self.placeholder = None
        self._next = None
        self._loading = False
        # A later page failed unexpectedly: the scroll stops asking.
        self._load_failed = False
        self._scope = RequestScope()
        self.rows = []

        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self.stack.add_named(make_loading_page(_('Loading')), 'loading')
        self.error_page = make_error_page(_('Could Not Load'),
                                          self._on_retry)
        self.stack.add_named(self.error_page, 'error')
        self.empty_page = Adw.StatusPage(
            icon_name=empty_icon, title=empty_title or _('Nothing Here'),
            description=empty_description)
        self.stack.add_named(self.empty_page, 'empty')

        self.scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         margin_top=12, margin_bottom=24, margin_start=12,
                         margin_end=12)
        if header is not None:
            column.append(header)
        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                    valign=Gtk.Align.START)
        self.list_box.add_css_class('boxed-list')
        connect_weak(self.list_box, 'row-activated', self._on_row_activated)
        column.append(self.list_box)
        self.more_spinner = Adw.Spinner(visible=False,
                                        halign=Gtk.Align.CENTER)
        column.append(self.more_spinner)
        clamp = Adw.Clamp(maximum_size=COLUMN_WIDTH, child=column)
        self.scrolled.set_child(clamp)
        self.stack.add_named(self.scrolled, 'list')
        connect_weak(self.scrolled.get_vadjustment(), 'value-changed',
                     self._on_scroll)
        self.refresh()

    def _on_retry(self, _button):
        self.refresh()

    def _on_row_activated(self, _list_box, row):
        callback = getattr(row, 'on_activate', None)
        if callback is not None:
            callback()

    def do_dispose(self):
        # Freed, not hidden: a cached page keeps its list and its answer.
        scope = getattr(self, '_scope', None)
        if scope is not None:
            scope.close()
        Adw.Bin.do_dispose(self)

    def _request(self, ticket, params):
        """Send one page in *ticket*'s generation. The callback holds the
        list weakly, so a list dropped while loading is freed (and its
        scope closed, which cancels the request) rather than kept alive
        by it."""
        api_core.send_path(
            self.client, self.method, self.fetch_path, params=params,
            callback=ticket.callback(self._on_loaded),
            cancellable=ticket.cancellable)

    def refresh(self):
        ticket = self._scope.begin()    # cancels the pages still out
        self._next = None
        self._load_failed = False
        self._loading = True
        self._drop_placeholder()
        for row in self.rows:
            self.list_box.remove(row)
        self.rows = []
        self.stack.set_visible_child_name('loading')
        params = dict(self.params)
        if self.paginated:
            params.setdefault('limit', PAGE_SIZE)
        self._request(ticket, params)

    def _on_scroll(self, adjustment):
        if near_bottom(adjustment) and not self._load_failed:
            self.load_more()

    def load_more(self):
        if self._loading or not self._next or not self.paginated:
            return
        self._loading = True
        self.more_spinner.set_visible(True)
        params = dict(self.params)
        params.update(self._next)
        self._request(self._scope.start(), params)

    def _on_loaded(self, client, result):
        # Only a wanted answer gets here: the ticket's callback dropped
        # one of a superseded refresh, of a closed list or of a cancelled
        # request before this decodes it. Any other error, a CANCELLED one
        # the list did not cause included, is shown.
        self._loading = False
        self.more_spinner.set_visible(False)
        cursor = self._next
        try:
            data, next_params, _prev = self.finish(client, result)
            items = self.extract(data) or []
            self._next = next_params if self.paginated and items else None
            if self.page_filter is not None:
                items = self.page_filter(items)
        except GLib.Error as error:
            self._fail_load(error)
            return
        except Exception:
            self._abort_page(cursor)
            return
        if not items and self._next:
            # The page filtered to nothing but more follow: ask for the
            # next one rather than show an empty list.
            self.load_more()
            return
        self._load_failed = False
        rows_before = len(self.rows)
        try:
            self.append_items(items)
        except Exception:
            if len(self.rows) == rows_before:
                self._abort_page(cursor)
            else:
                # The rows are in: rolling the cursor back would fetch
                # them again. Keep the page and its cursor, log the rest.
                _log.exception('PagedList %s: raised after its rows were '
                               'added', self.fetch_path)

    def _abort_page(self, cursor):
        """A hook raised before the page was applied. Nothing of it is in
        the list and the cursor goes back to *cursor*, so Retry or the
        reader's own request asks for the same page again; the traceback
        is logged."""
        _log.exception('PagedList %s: a hook raised while loading',
                       self.fetch_path)
        self._next = cursor
        self._fail_load(None)

    def _fail_load(self, error):
        """End a load that produced no page. The first page shows the
        error page (with Retry); a later one toasts and leaves the rows.
        *error* is the ``GLib.Error`` to name, or None for an unexpected
        exception, whose Python message is not for the reader (it is in
        the log). Such a later page also stops the scroll from asking
        again (``_load_failed``): the toast's Try Again does, or a
        refresh."""
        if not self.rows:
            self.error_page.set_description(
                summarize_error_markup(error) if error is not None else '')
            self.stack.set_visible_child_name('error')
        elif error is not None:
            feedback.report_error(self, _('Could not load more'), error)
        else:
            self._load_failed = True
            feedback.show_toast(self, _('Could not load more'),
                                undo=weak_method(self._retry_more),
                                undo_label=_('_Try Again'))

    def _retry_more(self):
        self._load_failed = False
        self.load_more()

    def append_items(self, items):
        # Build every row before touching the list, so a make_row that
        # raises on the 7th of 20 leaves the list as it was.
        built = []
        for item in items:
            row = self.make_row(item)
            if row is not None:
                built.append(row)
        for row in built:
            self.list_box.append(row)
            self.rows.append(row)
        self._settle()
        if self.rows and self._next:
            # Short first pages never scroll: keep filling.
            GLib.idle_add(self._maybe_fill)

    def _maybe_fill(self):
        adjustment = self.scrolled.get_vadjustment()
        if (adjustment.get_upper() <= adjustment.get_page_size() + PRELOAD
                and not self._load_failed):
            self.load_more()
        return GLib.SOURCE_REMOVE

    def _drop_placeholder(self):
        if self.placeholder is not None:
            self.list_box.remove(self.placeholder)
            self.placeholder = None

    def _settle(self):
        """Show the page the rows call for: the list when there are rows,
        else the placeholder row if the caller asked for one, else the
        empty state."""
        if self.rows:
            self._drop_placeholder()
            self.stack.set_visible_child_name('list')
        elif self.make_placeholder is not None:
            if self.placeholder is None:
                self.placeholder = self.make_placeholder()
                if self.placeholder is not None:
                    self.list_box.append(self.placeholder)
            self.stack.set_visible_child_name(
                'list' if self.placeholder is not None else 'empty')
        else:
            self.stack.set_visible_child_name('empty')

    def append_row(self, row):
        """Add *row* after the last one (the counterpart of
        :meth:`prepend_row`), showing the list."""
        self.list_box.append(row)
        self.rows.append(row)
        self._settle()

    def prepend_row(self, row):
        self.list_box.prepend(row)
        self.rows.insert(0, row)
        self._settle()

    def remove_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            self.list_box.remove(row)
        if self.rows:
            return
        if self._next:
            # More pages exist: fill them instead of claiming emptiness.
            GLib.idle_add(self._maybe_fill)
            if self.make_placeholder is not None:
                self._settle()
            return
        self._settle()

    def mutate_row_api(self, row, start, on_done=None, error_heading=None,
                       remove=True, finish=None):
        """Fire a row-scoped request: *row* is disabled while it runs and
        dropped on success; failure re-enables it and toasts
        *error_heading* (default 'Action failed').

        *start(callback)* queues the request through an `api` function, so
        the path is never spelled here. *finish(client, result)* is the api
        module's own ``<name>_finish``; the default is ``api.core.finish``.

        *remove* False keeps the row on success, for the actions that
        update it in place rather than taking it off the list -- a
        notification answered where it stands, for instance, whose state
        the caller's *on_done* then writes. Everything else -- disable,
        report, drop -- is the same.
        """
        start(self._row_finished(row, on_done, error_heading, remove,
                                 finish))

    def _row_finished(self, row, on_done, error_heading, remove,
                      finish=None):
        row.set_sensitive(False)

        def finished(_client, result, _data=None):
            try:
                (finish or api_core.finish)(self.client, result)
            except GLib.Error as error:
                row.set_sensitive(True)
                feedback.report_error(self, error_heading or _('Action failed'),
                                      error)
                return
            if remove:
                self.remove_row(row)
            if on_done is not None:
                on_done()

        return finished
