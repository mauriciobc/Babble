# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared building blocks for every secondary view.

* navigation: :func:`push_page`, :func:`make_nav_page`, :func:`window_of`
* feedback: :func:`show_toast`, :func:`report_error`, :func:`confirm`
* settings: :func:`settings`
* lists: :class:`PagedList` (any Link-paginated endpoint rendered as a
  boxed list of rows), :class:`AccountRow`, :class:`AccountListPage`
* links: :func:`open_link` routes post, profile and hashtag URLs into the
  app and everything else to the browser.
"""

import re
import types
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gio, Gtk, Pango  # noqa: E402

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


def make_nav_page(title, child, tag=None):
    """An Adw.NavigationPage with a header bar around *child*.

    *child* may carry optional attributes the header picks up:
    ``header_start_widgets`` / ``header_widgets`` (end side) lists of
    widgets, ``title_widget`` (replaces the title), ``top_bar`` (an
    extra top bar, e.g. a view switcher or search bar) and
    ``bottom_bar``."""
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
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
    page = make_nav_page(title, child, tag)
    view.push(page)
    split = getattr(window, 'split_view', None)
    if split is not None:
        split.set_show_content(True)
    return page


# Feedback

def show_toast(widget, text, undo=None, undo_label=None):
    """Toast on the nearest Adw.ToastOverlay holding *widget* (falling
    back to the window's); *undo* adds a button calling it. *text* is
    plain text."""
    window = window_of(widget)
    if window is None:
        return
    toast = Adw.Toast(title=text, use_markup=False)
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
    else:
        window.add_toast(toast)


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

class PagedList(Adw.Bin):
    """Infinite boxed list over a Link-paginated endpoint.

    ``fetch_path``/``params`` describe the request; ``make_row(item)``
    builds one Gtk.Widget per JSON item (return None to skip it).
    ``extract(data)`` maps the response to the item list (default: the
    response itself). Loading, empty and error states are built in;
    ``refresh()`` reloads from the top, ``remove_row(row)`` drops a row
    (and shows the empty state when it was the last one)."""

    __gtype_name__ = 'BabblePagedList'

    def __init__(self, client, fetch_path, make_row, params=None,
                 empty_title=None, empty_icon='edit-find-symbolic',
                 empty_description=None, extract=None, paginated=True,
                 header=None, method='GET', **kwargs):
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
        if adjustment.get_value() + adjustment.get_page_size() \
                >= adjustment.get_upper() - 600:
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
                    summarize_error_markup(error.message))
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
        if adjustment.get_upper() <= adjustment.get_page_size() + 600:
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

    def mutate_row(self, row, method, path, on_done=None,
                   error_heading=None, **kwargs):
        """Fire a row-scoped request: *row* is disabled while it runs and
        dropped on success; failure re-enables it and toasts
        *error_heading* (default 'Action failed')."""
        row.set_sensitive(False)

        def finished(_client, result, _data=None):
            try:
                self.client.request_finish(result)
            except GLib.Error as error:
                row.set_sensitive(True)
                report_error(self, error_heading or _('Action failed'),
                             error)
                return
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
        from .timeline import AvatarCache
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
    from .timeline import StatusItem
    window = window_of(widget)
    if window is None:
        return
    push_thread(window.navigation_view, client, StatusItem(status))
    split = getattr(window, 'split_view', None)
    if split is not None:
        split.set_show_content(True)


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


def fedi_link_kind(uri):
    """``'post'``, ``'profile'`` or ``'tag'`` when *uri* is an http(s)
    link shaped like one on a fediverse server, else None."""
    if not is_web_link(uri):
        return None
    parts = split_url(uri)
    if parts is None or not parts.hostname:
        return None
    if _TAG_PATH.match(parts.path):
        return 'tag'
    if _PROFILE_PATH.match(parts.path):
        return 'profile'
    if _FEDI_PATH.match(parts.path):
        return 'post'
    return None


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


def open_link(widget, uri, mentions=None, tags=None, resolve=False):
    """Route a link clicked in post or profile text.

    Mentions (matched against the status ``mentions`` payload) open the
    profile, hashtags open the hashtag timeline, other post/profile
    URLs are resolved through the instance's search when the
    ``aggressive-resolving`` setting is on, or always with *resolve*;
    everything else goes to the browser, minus tracking parameters when
    ``strip-tracking`` is on."""
    if not is_web_link(uri):
        launch_external(widget, uri)  # refuses it, with a toast
        return
    if settings().get_boolean('strip-tracking'):
        uri = strip_tracking(uri)
    client = client_of(widget)
    if client is None:
        launch_external(widget, uri)
        return
    for mention in mentions or ():
        if mention.get('url') == uri:
            open_profile(widget, client, acct=mention.get('acct'))
            return
    parts = split_url(uri)
    tag = _TAG_PATH.match(parts.path)
    if tag:
        name = urllib.parse.unquote(tag.group(1))
        known = {t.get('name', '').lower() for t in tags or ()}
        if not tags or name.lower() in known:
            from .hashtags import push_hashtag
            push_hashtag(widget, client, name)
            return
    if (resolve or settings().get_boolean('aggressive-resolving')) \
            and _FEDI_PATH.match(parts.path):
        resolve_uri(widget, client, uri)
        return
    launch_external(widget, uri)


def resolve_uri(widget, client, uri):
    """Look *uri* up with ``/api/v2/search?resolve=true`` and open the
    post or profile it names; fall back to the browser."""

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


def icon_button(icon_name, tooltip, callback=None, flat=True):
    button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip,
                        valign=Gtk.Align.CENTER)
    if flat:
        button.add_css_class('flat')
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    if callback is not None:
        button.connect('clicked', lambda *_a: callback())
    return button
