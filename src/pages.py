# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared building blocks for every secondary view.

* navigation: :func:`push_page`, :func:`make_nav_page`, :func:`window_of`
* feedback: :func:`show_toast`, :func:`report_error`, :func:`confirm`,
  :func:`confirm_remind`, :func:`announce`
* settings: :func:`settings`
* lists: :class:`PagedList` (any Link-paginated endpoint rendered as a
  boxed list of rows), :class:`AccountRow`, :class:`AccountListPage`
* links: :func:`open_link` routes post, profile and hashtag URLs into the
  app and everything else to the web column (when ``open-links-in-app``
  is on) or the browser, through :func:`open_web`.
"""

import re
import types
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango  # noqa: E402

from .mastodon import summarize_error, summarize_error_markup  # noqa: E402
from .statustext import is_web_link, split_url  # noqa: E402

SCHEMA_ID = 'io.github.mauriciobc.Babble'
COLUMN_WIDTH = 660
PAGE_SIZE = 40

_settings = None


def settings():
    """The application's Gio.Settings, shared by every module."""
    global _settings
    if _settings is None:
        _settings = Gio.Settings.new(SCHEMA_ID)
    return _settings


def connect_weak(emitter, signal, method, *extra):
    """``emitter.connect(signal, method, *extra)`` holding the bound
    *method*'s object weakly.

    A widget that connects a signal of something it owns (a child
    widget, an event controller, an adjustment, an action) to one of its
    own methods closes a reference cycle through C that Python's
    collector cannot see: the handler keeps the widget's wrapper, the
    wrapper keeps the GObject, the GObject keeps the emitter. The widget
    and its whole subtree then outlive every list row, dialog or page
    that dropped it. With the object held weakly the cycle never forms;
    once it is finalized the handler disconnects itself.

    The reference is a GObject weak reference, not a Python one: PyGObject
    drops the Python wrapper of an object only GTK still holds and builds
    a new one on demand, so a ``weakref`` to the wrapper dies while the
    widget lives on.
    """
    func = method.__func__
    target = method.__self__.weak_ref()

    def handler(source, *args):
        owner = target()
        if owner is None:
            source.disconnect_by_func(handler)
            return False
        return func(owner, source, *args)

    return emitter.connect(signal, handler, *extra)


def weak_method(method):
    """Callable forwarding to *method*, a GObject's bound method, without
    keeping the object alive; a no-op once it is finalized. For callbacks
    a widget hands to a child it owns (as a Python attribute, a list-row
    factory...), which a strong reference would turn into a cycle through
    C. A GObject weak reference, as in :func:`connect_weak`."""
    func = method.__func__
    target = method.__self__.weak_ref()

    def call(*args):
        owner = target()
        return None if owner is None else func(owner, *args)
    return call


# Motion

REVEAL_MS = 200


def reveal(revealer, shown, animate=False):
    """Show or hide *revealer*'s child, sliding only when *animate*.

    List rows are rebound when the view recycles them, so binding has to
    land on the final state at once; only a user's toggle animates."""
    revealer.set_transition_duration(REVEAL_MS if animate else 0)
    revealer.set_reveal_child(shown)


def flash_class(widget, css_class, ms=300):
    """Add *css_class* to *widget* for *ms* milliseconds, to play a CSS
    keyframe animation once. A state selector such as ``:checked`` would
    replay it whenever a recycled row is bound to a checked item."""
    # A flash restarted before the last one ended must not be cut short
    # by the earlier timeout: only the latest one removes the class.
    flashes = getattr(widget, '_babble_flashes', None)
    if flashes is None:
        flashes = widget._babble_flashes = {}
    token = flashes.get(css_class, 0) + 1
    flashes[css_class] = token
    widget.remove_css_class(css_class)
    widget.add_css_class(css_class)
    target = widget.weak_ref()

    def done():
        owner = target()
        if owner is not None:
            latest = getattr(owner, '_babble_flashes', {}).get(css_class)
            if latest in (None, token):
                owner.remove_css_class(css_class)
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(ms, done)


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


class PagedPosts:
    """The paging state machine every status list runs.

    Three copies existed -- ``TimelinePage``, ``NotificationsPage`` and
    ``pages.PagedList`` -- and they had drifted: two page sizes, three
    preload distances, ``since_id`` in one and ``min_id`` in another, and
    a generation guard present in two of the three. A list that pages
    wrongly does not look wrong, so the fix is one implementation.

    Subclasses supply the model and how to put items in it; this owns the
    flags, the cursors and the three requests:

    * :meth:`load` fills an empty list, falling back to :meth:`load_older`
      while pages come back empty of visible rows;
    * :meth:`load_older` pages back, stopping when the model says so, behind
      a debounce that always releases itself (see :meth:`_lock_bottom`);
    * :meth:`refresh` asks for what is newer than what is shown.

    Which cursor each uses is a constructor argument rather than a
    difference between two copies: ``min_id`` for the notification list
    (Mastodon requires it there), ``since_id`` for a timeline.
    """

    # One page size for every status list. These were 20, 20 and 40.
    PAGE_SIZE = 20
    # How close to the bottom a list gets before fetching the next page.
    # These were 800, 800 and 600.
    PRELOAD = 800
    # How long the bottom lock is held after a page goes out. Long enough
    # that one flick of the wheel cannot queue a second request, short
    # enough that a reader who stops scrolling is never left waiting.
    BOTTOM_LOCK_MS = 1000

    model = None

    def _paging_init(self, newer_cursor='since_id'):
        self._generation = 0
        self._loading_older = False
        self._newer_cursor = newer_cursor
        self._bottom_locked = False
        self._older_pending = False
        self._bottom_lock_id = 0

    # Requests

    def _request(self, params, on_done):
        """Queue one page request, tagged with its generation.

        A response that outlived its request is dropped rather than
        spliced into the model -- a superseded refresh would otherwise
        land stale posts in a list the reader is looking at. Dropping it
        also clears the busy flags, or a discarded first page would leave
        the list reading as loading forever.
        """
        self._generation += 1
        generation = self._generation

        def finished(client, result, _data=None):
            on_done(client, result, generation)
        return self._paging_fetch(params, finished, generation)

    def _paging_fetch(self, params, callback, generation):
        raise NotImplementedError

    def _superseded(self, generation):
        if generation == self._generation:
            return False
        self.model.loading = False
        self._loading_older = False
        return True

    # The three requests

    def load(self):
        if self.model.loading:
            return
        self.model.loading = True
        if self.model.get_n_items() == 0:
            self.stack.set_visible_child_name('loading')
        self._request(self._first_page_params(), self.on_page_loaded)

    def _first_page_params(self):
        return {'limit': self.PAGE_SIZE}

    def load_older(self):
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
                      self.on_older_loaded)

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
        if self.model.loading:
            return
        if self.model.get_n_items() == 0:
            self.load()
            return
        self.model.exhausted = False
        params = {'limit': self.PAGE_SIZE}
        if self.model.newest_id is not None:
            params[self._newer_cursor] = self.model.newest_id
        self.model.loading = True
        self._request(params, self.on_refresh_loaded)

    # Scroll

    def on_scroll_near_end(self, adjustment):
        """Whether the list is within ``PRELOAD`` of the bottom."""
        bottom = adjustment.get_value() + adjustment.get_page_size()
        return bottom >= adjustment.get_upper() - self.PRELOAD


# Navigation

def window_of(widget):
    """The BabbleWindow *widget* lives in (dialogs included), or None."""
    root = widget.get_root() if widget is not None else None
    if root is not None and not hasattr(root, 'navigation_view'):
        # Adw.Dialog content: the dialog's parent window owns navigation.
        app = Gio.Application.get_default()
        root = app.get_active_window() if app is not None else None
    if root is None or not hasattr(root, 'navigation_view'):
        return None
    return root


def client_of(widget):
    window = window_of(widget)
    return getattr(window, 'client', None)


def make_nav_page(title, child, tag=None, sidebar_control=None):
    """An Adw.NavigationPage with a header bar around *child*.

    *child* may carry optional attributes the header picks up:
    ``header_start_widgets`` / ``header_widgets`` (end side) lists of
    widgets, ``title_widget`` (replaces the title), ``top_bar`` (an
    extra top bar, e.g. a view switcher or search bar) and
    ``bottom_bar``. *sidebar_control*, when given, is a callable
    returning the window's sidebar control, packed at the start of the
    header before the page's own start widgets."""
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    if sidebar_control is not None:
        header.pack_start(sidebar_control())
    for widget in getattr(child, 'header_start_widgets', ()) or ():
        header.pack_start(widget)
    for widget in reversed(getattr(child, 'header_widgets', ()) or ()):
        header.pack_end(widget)
    title_widget = getattr(child, 'title_widget', None)
    if title_widget is not None:
        header.set_title_widget(title_widget)
    toolbar.add_top_bar(header)
    top_bar = getattr(child, 'top_bar', None)
    if top_bar is not None:
        toolbar.add_top_bar(top_bar)
    bottom_bar = getattr(child, 'bottom_bar', None)
    if bottom_bar is not None:
        toolbar.add_bottom_bar(bottom_bar)
    toolbar.set_content(child)
    page = Adw.NavigationPage(title=title, child=toolbar)
    if tag:
        page.set_tag(tag)
    return page


def push_page(widget, title, child, tag=None):
    """Push *child* (wrapped by :func:`make_nav_page`) onto the window's
    navigation view. A page with the same *tag* already on the stack is
    popped back to instead of stacked twice. Returns the page."""
    window = window_of(widget)
    if window is None:
        return None
    view = window.navigation_view
    if tag:
        existing = view.find_page(tag)
        if existing is not None:
            view.pop_to_page(existing)
            return existing
    page = make_nav_page(
        title, child, tag,
        sidebar_control=getattr(window, 'make_sidebar_control', None))
    view.push(page)
    dismiss = getattr(window, 'dismiss_sidebar_overlay', None)
    if dismiss is not None:
        dismiss()
    return page


# Feedback

def announce(widget, text,
             priority=Gtk.AccessibleAnnouncementPriority.MEDIUM):
    """Have screen readers speak *text* for *widget*, for news that has
    no focus change or toast of its own (toasts announce themselves);
    nothing when *widget* is None."""
    if widget is not None:
        widget.announce(text, priority)


def show_toast(widget, text, undo=None, undo_label=None, dismissed=None):
    """Toast on the nearest Adw.ToastOverlay holding *widget*, else on the
    Adw.PreferencesDialog holding it (or that it is), else on the window;
    *undo* adds a button calling it, *dismissed* runs when the toast goes
    away however it does. Returns the toast, or None when there was no
    window to show it on. *text* is plain text."""
    window = window_of(widget)
    if window is None:
        return None
    toast = Adw.Toast(title=text, use_markup=False)
    if dismissed is not None:
        toast.connect('dismissed', lambda _toast: dismissed())
    if undo is not None:
        toast.set_button_label(undo_label or _('_Undo'))
        toast.set_priority(Adw.ToastPriority.HIGH)
        toast.connect('button-clicked', lambda _toast: undo())
    # A dialog presented over the window covers the window's overlay, so
    # prefer the overlay inside the dialog the widget lives in.
    overlay = widget.get_ancestor(Adw.ToastOverlay) if widget is not None \
        else None
    if overlay is not None and overlay is not window.toast_overlay:
        overlay.add_toast(toast)
        return toast
    # Adw.PreferencesDialog keeps its overlay inside itself, so it is not an
    # ancestor of the dialog (or of a dialog presented over it); it offers
    # add_toast instead. get_ancestor counts the widget itself.
    preferences = widget.get_ancestor(Adw.PreferencesDialog) \
        if widget is not None else None
    if preferences is not None:
        preferences.add_toast(toast)
    else:
        window.add_toast(toast)
    return toast


# Actions held back until their Undo toast is gone. Each entry is the
# function that commits one, so the window can flush them when it closes.
_pending_actions = []


def deferred_action(widget, text, commit, restore, undo_label=None):
    """Offer Undo before a destructive request: show *text* with an Undo
    button, call *restore()* if it is pressed and *commit()* once the toast
    goes away without that. Exactly one of them runs, once. With no window
    to show the toast on, *commit* runs at once. :func:`flush_deferred`
    commits whatever is still waiting."""
    state = {'settled': False}

    def settle(undone):
        if state['settled']:
            return
        state['settled'] = True
        if commit_pending in _pending_actions:
            _pending_actions.remove(commit_pending)
        (restore if undone else commit)()

    def commit_pending():
        settle(False)

    _pending_actions.append(commit_pending)
    toast = show_toast(widget, text, undo=lambda: settle(True),
                       undo_label=undo_label,
                       dismissed=lambda: settle(False))
    if toast is None:
        settle(False)


def flush_deferred():
    """Commit every action still waiting on its Undo toast, for a window
    that is closing: the reader chose it, so it must not be dropped."""
    for commit_pending in list(_pending_actions):
        commit_pending()


def report_error(widget, heading, error):
    """Toast '<heading>: <server message>' for a GLib.Error."""
    message = getattr(error, 'message', None) or str(error)
    show_toast(widget, '%s: %s' % (heading, summarize_error(message)))


def confirm(widget, heading, body, action_label, callback,
            destructive=True):
    """Adw.AlertDialog with Cancel and *action_label*; *callback()* runs
    only when the action is chosen."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('ok', action_label)
    if destructive:
        dialog.set_response_appearance('ok',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
    else:
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'ok':
            callback()

    dialog.connect('response', on_response)
    dialog.present(widget)
    return dialog


def confirm_remind(widget, heading, body, action, action_label, setting,
                   callback):
    """Cancel, "Don't Remind Me Again", and *action_label*.

    The reminder turns the boolean *setting* off and still runs
    *callback()*, the same as choosing the action. Cancel does neither.
    The alt-text boost reminder is not this dialog: it is a checkbox on
    the extra child, and its default is Cancel."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('never', _('_Don’t Remind Me Again'))
    dialog.add_response(action, action_label)
    dialog.set_response_appearance(action, Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(action)
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'never':
            settings().set_boolean(setting, False)
        if response in ('never', action):
            callback()

    dialog.connect('response', on_response)
    dialog.present(widget)
    return dialog


class ToastDialog(Adw.Dialog):
    """Dialog with a header bar, its own toast overlay and a page.

    A dialog covers the window's toast overlay, so a toast raised inside
    one would land behind it; every dialog either subclasses this or
    says why it cannot (``dialog toasts`` enforces the rule). Callers
    that need something other than a preferences page replace it with
    :meth:`set_content`.
    """

    def __init__(self, title, width=480, height=600, title_buttons=True,
                 **kwargs):
        super().__init__(title=title, content_width=width,
                         content_height=height, **kwargs)
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        if not title_buttons:
            self.header.set_show_start_title_buttons(False)
            self.header.set_show_end_title_buttons(False)
        self.toolbar.add_top_bar(self.header)
        self.toasts = Adw.ToastOverlay()
        self.page = Adw.PreferencesPage()
        self.toasts.set_child(self.page)
        self.toolbar.set_content(self.toasts)
        self.set_child(self.toolbar)

    def set_content(self, widget):
        """Replace the preferences page, keeping the overlay above it."""
        self.page = widget
        self.toasts.set_child(widget)

    def toast(self, text):
        self.toasts.add_toast(
            Adw.Toast(title=text, timeout=5, use_markup=False))

    def error(self, heading, error):
        self.toast('%s: %s' % (heading, summarize_error(error)))

    def submit_on_enter(self, button, *rows):
        """Make Enter in *rows* press *button*. The default widget alone
        does nothing for an entry row: the row has to opt in with
        ``activates-default``. An insensitive *button* is not pressed."""
        self.set_default_widget(button)
        for row in rows:
            row.set_activates_default(True)

    def add_header_buttons(self, cancel_label, action_label, callback,
                           css=None, sensitive=True):
        """Cancel button that closes the dialog plus an end-side action
        button calling *callback*; returns the action button."""
        cancel = Gtk.Button(label=cancel_label, use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        button = Gtk.Button(label=action_label, use_underline=True,
                            sensitive=sensitive)
        if css:
            button.add_css_class(css)
        connect_weak(button, 'clicked', callback)
        self.header.pack_end(button)
        return button

    def _on_cancel(self, _button):
        self.force_close()


def call(widget, client, method, path, on_done=None, error_heading=None,
         **kwargs):
    """Fire one request and hand the decoded body to *on_done(data)*;
    failures toast *error_heading* (default 'Action failed')."""

    def finished(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error as error:
            report_error(widget, error_heading or _('Action failed'), error)
            return
        if on_done is not None:
            on_done(data)

    return client.request_async(method, path, callback=finished, **kwargs)


# Status pages

def configure_empty_page(page, icon_name, title, description):
    """Fill the three fields every timeline wrapper sets on ``empty_page``."""
    page.set_icon_name(icon_name)
    page.set_title(title)
    page.set_description(description)


def clear_children(widget):
    """Remove every child of *widget* before it is filled again."""
    while (child := widget.get_first_child()) is not None:
        widget.remove(child)


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


# Paged list

class PagedList(PagedPosts, Adw.Bin):
    """Infinite boxed list over a Link-paginated endpoint.

    ``fetch_path``/``params`` describe the request; ``make_row(item)``
    builds one Gtk.Widget per JSON item (return None to skip it).
    ``extract(data)`` maps the response to the item list (default: the
    response itself). Loading, empty and error states are built in;
    ``refresh()`` reloads from the top, ``remove_row(row)`` drops a row
    (and shows the empty state when it was the last one).

    Inherits the paging flags and cursors from PagedPosts, but follows
    ``Link: rel=next`` rather than a status id, so it keeps its own
    ``load_more`` where a timeline has ``load_older``."""

    __gtype_name__ = 'BabblePagedList'

    def __init__(self, client, fetch_path, make_row, params=None,
                 empty_title=None, empty_icon='edit-find-symbolic',
                 empty_description=None, extract=None, paginated=True,
                 header=None, method='GET', **kwargs):
        PagedPosts.__init__(self)
        super().__init__(**kwargs)
        self.client = client
        self.fetch_path = fetch_path
        self.make_row = make_row
        self.params = dict(params or {})
        self.extract = extract or (lambda data: data)
        self.paginated = paginated
        self.method = method
        self._next = None
        self._loading = False
        self._generation = 0
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

    def refresh(self):
        self._generation += 1
        self._next = None
        self._loading = True
        for row in self.rows:
            self.list_box.remove(row)
        self.rows = []
        self.stack.set_visible_child_name('loading')
        params = dict(self.params)
        if self.paginated:
            params.setdefault('limit', PAGE_SIZE)
        generation = self._generation
        self.client.request_async(
            self.method, self.fetch_path, params=params,
            callback=lambda c, r, _d=None, g=generation:
                self._on_loaded(c, r, g),
            cancellable=None)

    def _on_scroll(self, adjustment):
        if self.on_scroll_near_end(adjustment):
            self.load_more()

    def load_more(self):
        if self._loading or not self._next or not self.paginated:
            return
        self._loading = True
        self.more_spinner.set_visible(True)
        params = dict(self.params)
        params.update(self._next)
        generation = self._generation
        self.client.request_async(
            self.method, self.fetch_path, params=params,
            callback=lambda c, r, _d=None, g=generation:
                self._on_loaded(c, r, g))

    def _on_loaded(self, client, result, generation):
        # A response from a superseded refresh must not touch this list.
        if generation != self._generation:
            return
        self._loading = False
        self.more_spinner.set_visible(False)
        try:
            data, next_params, _prev = client.request_finish_paged(result)
        except GLib.Error as error:
            if not self.rows:
                self.error_page.set_description(
                    summarize_error_markup(error))
                self.stack.set_visible_child_name('error')
            else:
                report_error(self, _('Could not load more'), error)
            return
        items = self.extract(data) or []
        self._next = next_params if self.paginated and items else None
        self.append_items(items)

    def append_items(self, items):
        for item in items:
            row = self.make_row(item)
            if row is None:
                continue
            self.list_box.append(row)
            self.rows.append(row)
        self.stack.set_visible_child_name('list' if self.rows else 'empty')
        if self.rows and self._next:
            # Short first pages never scroll: keep filling.
            GLib.idle_add(self._maybe_fill)

    def _maybe_fill(self):
        adjustment = self.scrolled.get_vadjustment()
        if adjustment.get_upper() <= adjustment.get_page_size() + self.PRELOAD:
            self.load_more()
        return GLib.SOURCE_REMOVE

    def prepend_row(self, row):
        self.list_box.prepend(row)
        self.rows.insert(0, row)
        self.stack.set_visible_child_name('list')

    def remove_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            self.list_box.remove(row)
        if self.rows:
            return
        if self._next:
            # More pages exist: fill them instead of claiming emptiness.
            GLib.idle_add(self._maybe_fill)
            return
        self.stack.set_visible_child_name('empty')

    def mutate_row(self, row, method, path, on_done=None, error_heading=None,
                   remove=True, **kwargs):
        """Fire a row-scoped request: *row* is disabled while it runs and
        dropped on success; failure re-enables it and toasts
        *error_heading* (default 'Action failed').

        *remove* False keeps the row on success, for the actions that
        update it in place rather than taking it off the list -- a
        notification answered where it stands, for instance, whose state
        the caller's *on_done* then writes. Everything else -- disable,
        report, drop -- is the same.
        """
        row.set_sensitive(False)

        def finished(_client, result, _data=None):
            try:
                self.client.request_finish(result)
            except GLib.Error as error:
                row.set_sensitive(True)
                report_error(self, error_heading or _('Action failed'),
                             error)
                return
            if remove:
                self.remove_row(row)
            if on_done is not None:
                on_done()

        self.client.request_async(method, path, callback=finished, **kwargs)


# Accounts

def display_name(account):
    return account.get('display_name') or account.get('username') \
        or account.get('acct', '')


class AccountRow(Adw.ActionRow):
    """Avatar, display name (custom emoji stripped to text) and @handle;
    activating opens the profile. Extra widgets go in with
    ``add_suffix``."""

    __gtype_name__ = 'BabbleAccountRow'

    def __init__(self, client, account, subtitle=None, **kwargs):
        super().__init__(activatable=True, use_markup=False, **kwargs)
        from .mediacache import AvatarCache
        self.client = client
        self.account = account
        name = re.sub(r':[A-Za-z0-9_+\-]+:', '', display_name(account)) \
            .strip() or account.get('acct', '')
        self.set_title(name)
        self.set_subtitle(subtitle if subtitle is not None
                          else '@' + account.get('acct', ''))
        self.set_title_lines(1)
        self.set_subtitle_lines(1)
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)
        self.avatar = avatar
        if account.get('invalid_handle'):
            # Mastodon 4.7: the handle cannot be verified (a remote actor
            # that changed handles); the acct shown is server-unique.
            warning = Gtk.Image(icon_name='dialog-warning-symbolic')
            warning.add_css_class('dimmed')
            warning.set_tooltip_text(
                _('This account’s handle could not be verified'))
            self.add_suffix(warning)

    def on_activate(self):
        open_profile(self, self.client, self.account)


def open_profile(widget, client, account=None, acct=None):
    from .profile import push_profile
    window = window_of(widget)
    if window is None:
        return
    push_profile(window.navigation_view, client, account, acct=acct)


def open_status(widget, client, status):
    from .thread import push_thread
    from .timelinedata import StatusItem
    window = window_of(widget)
    if window is None:
        return
    push_thread(window.navigation_view, client, StatusItem(status))
    dismiss = getattr(window, 'dismiss_sidebar_overlay', None)
    if dismiss is not None:
        dismiss()


class AccountListPage(PagedList):
    """Accounts from any endpoint returning an Account array
    (followers, favourited_by, mutes, list members, …)."""

    __gtype_name__ = 'BabbleAccountListPage'

    def __init__(self, client, path, params=None, empty_title=None,
                 make_suffix=None, extract=None, **kwargs):
        self._make_suffix = make_suffix
        super().__init__(client, path, self._make_account_row,
                         params=params,
                         empty_title=empty_title or _('No Accounts'),
                         empty_icon='system-users-symbolic',
                         extract=extract, **kwargs)

    def _make_account_row(self, account):
        row = AccountRow(self.client, account)
        if self._make_suffix is not None:
            suffix = self._make_suffix(account, row)
            if suffix is not None:
                row.add_suffix(suffix)
        return row


# Links

TRACKING_PARAMS = re.compile(
    r'^(utm_[a-z_]+|fbclid|gclid|dclid|msclkid|mc_eid|mc_cid|igshid|'
    r'_hsenc|_hsmi|mkt_tok|oly_anon_id|oly_enc_id|rb_clickid|s_cid|'
    r'vero_id|wickedid|yclid|ref_src|ref_url|si)$', re.I)

_FEDI_PATH = re.compile(
    r'^/(@[^/]+(/\d+)?|users/[^/]+(/statuses/[^/]+)?|notice/[^/]+|'
    r'notes/[^/]+|objects/[^/]+|p/[^/]+/\d+|u/[^/]+|web/statuses/\d+|'
    r'web/@[^/]+(/\d+)?)/?$')
# The profile shapes among _FEDI_PATH; every other match is a post.
_PROFILE_PATH = re.compile(r'^/(?:web/)?(?:@[^/]+|users/[^/]+|u/[^/]+)/?$')
_TAG_PATH = re.compile(r'^/(?:tags|tag)/([^/?#]+)/?$')
# A Collection URL (Mastodon 4.6+): https://host/collections/:id
_COLLECTION_PATH = re.compile(r'^/collections/([^/?#]+)/?$')


def fedi_link_kind(uri):
    """``'post'``, ``'profile'``, ``'tag'`` or ``'collection'`` when
    *uri* is an http(s) link shaped like one on a fediverse server, else
    None."""
    if not is_web_link(uri):
        return None
    parts = split_url(uri)
    if parts is None or not parts.hostname:
        return None
    if _TAG_PATH.match(parts.path):
        return 'tag'
    if _COLLECTION_PATH.match(parts.path):
        return 'collection'
    if _PROFILE_PATH.match(parts.path):
        return 'profile'
    if _FEDI_PATH.match(parts.path):
        return 'post'
    return None


def is_fediverse_link(uri):
    """True when *uri* is an http(s) link to a post, profile, hashtag or
    Collection on a fediverse server, the links :func:`open_link` can
    resolve in-app."""
    return fedi_link_kind(uri) is not None


def strip_tracking(uri):
    parts = split_url(uri)
    if parts is None or not parts.query:
        return uri
    query = [(k, v) for k, v in urllib.parse.parse_qsl(
        parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunsplit(parts._replace(
        query=urllib.parse.urlencode(query)))


def launch_external(widget, uri):
    from .textemoji import launch_uri
    launch_uri(widget, uri)


def open_web(widget, uri):
    """Open an article or other web link: in the web column beside the
    timeline when ``open-links-in-app`` is on and WebKit is available,
    in the browser otherwise.

    For article and web links only. OAuth, the explicit "Open in
    Browser" actions and non-web schemes must keep using
    :func:`launch_external`, which never opens the column."""
    if settings().get_boolean('open-links-in-app'):
        from . import webcolumn
        if webcolumn.available():
            webcolumn.present_web(widget, uri)
            return
    launch_external(widget, uri)


def open_link(widget, uri, mentions=None, tag=None, resolve=False):
    """Route a link clicked in post or profile text.

    *tag*, when the renderer recognised an ``<a class="hashtag">`` and
    kept its displayed name, opens that hashtag's timeline directly:
    the status ``tags`` payload is normalized (casefolded, transliterated)
    and cannot be compared with the href, and a server that links tags
    elsewhere (``/dashboard/search/…``, ``/feeds/search?q=…#name``)
    would never match a URL shape. Mentions (matched against the status
    ``mentions`` payload) open the profile, Collection URLs open the
    Collection, other post/profile URLs are resolved through the
    instance's search when the ``aggressive-resolving`` setting is on, or
    always with *resolve*; everything else goes to the web column when
    ``open-links-in-app`` is on (see :func:`open_web`), else the browser,
    minus tracking parameters when ``strip-tracking`` is on."""
    if not is_web_link(uri):
        launch_external(widget, uri)  # refuses it, with a toast
        return
    if settings().get_boolean('strip-tracking'):
        uri = strip_tracking(uri)
    client = client_of(widget)
    if client is None:
        launch_external(widget, uri)
        return
    if tag:
        from .hashtags import push_hashtag
        push_hashtag(widget, client, tag)
        return
    for mention in mentions or ():
        if mention.get('url') == uri:
            open_profile(widget, client, acct=mention.get('acct'))
            return
    parts = split_url(uri)
    path_tag = _TAG_PATH.match(parts.path)
    if path_tag:
        # An anchor without the hashtag class, or a plain link to a tag
        # page. Both open the timeline in-app, as Tuba does.
        from .hashtags import push_hashtag
        push_hashtag(widget, client, urllib.parse.unquote(path_tag.group(1)))
        return
    if _COLLECTION_PATH.match(parts.path):
        # A Collection link (Mastodon 4.6+). Resolution goes through the
        # instance's search, which returns the local Collection entity
        # only on a server that supports Collections; otherwise the link
        # falls back to the browser.
        if resolve or settings().get_boolean('aggressive-resolving'):
            resolve_uri(widget, client, uri)
        else:
            launch_external(widget, uri)
        return
    if (resolve or settings().get_boolean('aggressive-resolving')) \
            and _FEDI_PATH.match(parts.path):
        resolve_uri(widget, client, uri)
        return
    open_web(widget, uri)


def open_collection(widget, client, collection):
    """Open a Collection in-app (Mastodon 4.6+). *collection* is a
    Collection entity; its ``id`` is server-local, as the entity came
    from this instance."""
    collection_id = collection.get('id')
    if not collection_id:
        launch_external(widget, collection.get('url') or '')
        return
    from .fedicollections import CollectionPage
    push_page(widget, collection.get('name') or _('Collection'),
              CollectionPage(client, collection_id, collection),
              tag='collection-%s' % collection_id)


def resolve_uri(widget, client, uri):
    """Look *uri* up with ``/api/v2/search?resolve=true`` and open the
    post, profile or collection it names; fall back to the browser."""

    def resolved(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error:
            launch_external(widget, uri)
            return
        if data.get('statuses'):
            open_status(widget, client, data['statuses'][0])
        elif data.get('accounts'):
            open_profile(widget, client, data['accounts'][0])
        elif data.get('collections'):
            open_collection(widget, client, data['collections'][0])
        else:
            launch_external(widget, uri)

    client.request_async('GET', '/api/v2/search',
                         params={'q': uri, 'resolve': 'true', 'limit': 1},
                         callback=resolved)


# Small widgets

def section_label(text):
    label = Gtk.Label(label=text, xalign=0.0, wrap=True,
                      ellipsize=Pango.EllipsizeMode.NONE)
    label.add_css_class('heading')
    return label


def activate_on_key(widget, callback, *args):
    """Call ``callback(*args)`` when Enter or space is pressed while
    *widget* itself has the keyboard focus: the keyboard half of a
    ``GtkGestureClick`` on something that is not a button.

    The caller makes *widget* focusable and gives it a focus ring. A key
    that bubbles up from a button or text view inside *widget* is left
    alone. *callback* must not hold *widget* strongly (use
    :func:`weak_method` or a GObject weak reference): the controller
    belongs to the widget, so that would be the cycle through C that
    :func:`connect_weak` prevents.
    """
    def pressed(controller, keyval, _keycode, _state):
        if (keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space)
                and controller.get_widget().is_focus()):
            callback(*args)
            return True
        return False

    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', pressed)
    widget.add_controller(keys)


def set_accessible_label(widget, text):
    """Give *widget* *text* as both its tooltip and its accessible label.

    A tooltip does not set the accessible name, and an icon-only control
    has no text of its own, so it needs both. Where the two should differ,
    set them separately instead.
    """
    widget.set_tooltip_text(text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def icon_button(icon_name, tooltip, flat=True, valign=Gtk.Align.CENTER,
                css=()):
    """A flat icon button with *tooltip* as its accessible label.

    *css* adds classes beyond the flat one, for the shapes this is
    otherwise re-created for: 'circular' for the round accept/dismiss
    buttons on a request, for instance. *valign* is here because the same
    button sits centred in a header bar and stretched in a row.

    It takes no handler: the caller connects ``clicked`` with
    :func:`connect_weak`, since a closure over the owner that the owner's
    own button holds is the cycle through C that helper prevents.
    """
    button = Gtk.Button(icon_name=icon_name, valign=valign)
    if flat:
        button.add_css_class('flat')
    for name in css:
        button.add_css_class(name)
    set_accessible_label(button, tooltip)
    return button
