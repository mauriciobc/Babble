# SPDX-License-Identifier: GPL-3.0-or-later
"""A web view in a column beside the timeline.

WebKit is imported on first use, never at module import: preferences
imports this module to ask :func:`available`, and the web engine is a
heavy library that most sessions never need."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .statustext import is_web_link  # noqa: E402

LOAD = 'load'
EXTERNAL = 'external'
ROUTE = 'route'
IGNORE = 'ignore'

_available = None
_session = None


def available():
    """Whether WebKitGTK 6.0 can be loaded here. Cached."""
    global _available
    if _available is None:
        try:
            gi.require_version('WebKit', '6.0')
            _available = True
        except ValueError:
            _available = False
    return _available


def _network_session():
    """One ephemeral session for every column: no cookies or cache are
    written to disk, and nothing survives the app."""
    global _session
    if _session is None:
        from gi.repository import WebKit  # noqa: E402
        _session = WebKit.NetworkSession.new_ephemeral()
        _session.set_itp_enabled(True)
        # Downloads are never offered; this is the safety net should one
        # start anyway.
        _session.connect('download-started',
                         lambda _session_, download: download.cancel())
    return _session


def classify_navigation(uri, *, new_window=False, link_clicked=False,
                        user_gesture=False, button=0, modifiers=0,
                        strip=False, resolve=False):
    """Decide what a navigation request should do.

    Returns ``(action, uri)``: LOAD in the view, hand to the EXTERNAL
    launcher, ROUTE inside the app, or IGNORE. *uri* comes back changed
    only when tracking parameters were stripped."""
    if not uri:
        return IGNORE, uri
    scheme = uri.split(':', 1)[0].lower() if ':' in uri else ''
    if scheme in ('about', 'data', 'blob'):
        # Frames need these; WebKit's own settings already refuse a
        # top-level data: navigation.
        return (IGNORE if new_window else LOAD), uri
    if not is_web_link(uri):
        return (EXTERNAL if user_gesture else IGNORE), uri
    if link_clicked and (button == 2
                         or modifiers & Gdk.ModifierType.CONTROL_MASK):
        return EXTERNAL, uri
    if link_clicked and resolve and pages.is_fediverse_link(uri):
        return ROUTE, uri
    if new_window and not user_gesture:
        return IGNORE, uri  # popups are blocked
    if link_clicked and strip:
        uri = pages.strip_tracking(uri)
    return LOAD, uri


def present_web(widget, uri):
    """Open *uri* in the web column of *widget*'s window, or in the
    system browser when there is no column to show it in."""
    window = pages.window_of(widget)
    if window is None or not hasattr(window, 'web_split') \
            or not available():
        pages.launch_external(widget, uri)
        return
    column = window.web_slot.get_child()
    if column is None:
        column = WebColumn()
        window.web_slot.set_child(column)
    column.load_uri(uri)
    window.web_split.set_show_sidebar(True)
    column.view.grab_focus()


def watch_split(split):
    """Keep the column in step with *split*, the window's
    AdwOverlaySplitView. Call it once, when the window is built.

    AdwOverlaySplitView hides its sidebar when it collapses and shows it
    again when it expands. notify::collapsed comes first, so a hide that
    follows within the same main-loop turn is the layout's doing and
    keeps the page; any other hide is the user dismissing the column."""
    collapsing = [False]

    def end_collapse():
        collapsing[0] = False
        return GLib.SOURCE_REMOVE

    def on_collapsed(split, _pspec):
        collapsing[0] = True
        GLib.idle_add(end_collapse)
        _hide_if_empty(split)

    def on_shown(split, _pspec):
        if split.get_show_sidebar():
            _hide_if_empty(split)
            return
        column = split.get_sidebar().get_child()
        if column is not None and not collapsing[0]:
            column.dispose_column()  # dismissed over the content

    split.connect('notify::collapsed', on_collapsed)
    split.connect('notify::show-sidebar', on_shown)


def _hide_if_empty(split):
    # The sidebar comes back on expanding even when the column was
    # closed meanwhile; there is nothing to show then.
    if split.get_show_sidebar() and split.get_sidebar().get_child() is None:
        split.set_show_sidebar(False)


def _column_of(window):
    slot = getattr(window, 'web_slot', None)
    column = slot.get_child() if slot is not None else None
    return column if isinstance(column, WebColumn) else None


def forward_action(window, name):
    """Run window action *name* ('close', 'reload', 'back') on the web
    column when it is shown and holds the focus; return whether it did.

    Application accelerators are matched before any widget's controller,
    so the window's own Ctrl+W, Ctrl+R, F5 and Alt+Left handlers hand
    over here first."""
    column = _column_of(window)
    if column is None or not window.web_split.get_show_sidebar():
        return False
    focus = window.get_focus()
    if focus is None or not (focus is column or focus.is_ancestor(column)):
        return False
    {'close': column.close_column,
     'reload': column._on_reload,
     'back': column._on_back,
     'forward': column._on_forward}[name]()
    return True


class WebColumn(Adw.Bin):
    """Header bar, load progress, and the page or an error."""

    def __init__(self):
        super().__init__()
        from gi.repository import WebKit  # noqa: E402
        self._webkit = WebKit
        self._disposed = False
        self._error_uri = None
        self._committed = False
        self._pending_user = False  # the next navigation is ours
        self._user_nav = False
        self._handed_off = False

        self._build_actions()
        self.view = self._build_view()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())

        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class('osd')
        toolbar.add_top_bar(self.progress)

        self.stack = Gtk.Stack()
        self.stack.add_named(self.view, 'page')
        self.stack.add_named(self._build_error_page(), 'error')
        toolbar.set_content(self.stack)
        self.set_child(toolbar)
        self._add_shortcuts()
        self._refresh_nav()

    # Construction

    def _build_actions(self):
        group = Gio.SimpleActionGroup()
        for name, method in (('reload', self._on_reload),
                             ('open-browser', self._on_open_browser),
                             ('copy-link', self._on_copy_link)):
            action = Gio.SimpleAction.new(name, None)
            pages.connect_weak(action, 'activate', method)
            group.add_action(action)
        self._open_link_action = Gio.SimpleAction.new(
            'open-link', GLib.VariantType.new('s'))
        pages.connect_weak(self._open_link_action, 'activate',
                           self._on_open_link)
        group.add_action(self._open_link_action)
        self.insert_action_group('web', group)

    def _build_view(self):
        WebKit = self._webkit
        config = WebKit.Settings(
            enable_javascript=pages.settings().get_boolean('web-javascript'),
            enable_media_stream=False,
            media_playback_requires_user_gesture=True,
            enable_back_forward_navigation_gestures=True,
            enable_developer_extras=False)
        view = WebKit.WebView(
            network_session=_network_session(),
            settings=config,
            website_policies=WebKit.WebsitePolicies(
                autoplay=WebKit.AutoplayPolicy.ALLOW_WITHOUT_SOUND))
        view.set_vexpand(True)
        view.set_hexpand(True)
        for signal, method in (
                ('decide-policy', self._on_decide_policy),
                ('create', self._on_create),
                ('permission-request', self._on_permission_request),
                ('load-failed', self._on_load_failed),
                ('load-failed-with-tls-errors', self._on_tls_failed),
                ('web-process-terminated', self._on_terminated),
                ('load-changed', self._on_load_changed),
                ('context-menu', self._on_context_menu),
                ('notify::title', self._on_title_uri),
                ('notify::uri', self._on_title_uri),
                ('notify::uri', self._on_uri),
                ('notify::estimated-load-progress', self._on_progress),
                ('notify::is-loading', self._on_progress)):
            pages.connect_weak(view, signal, method)
        pages.connect_weak(pages.settings(), 'changed::web-javascript',
                           self._on_javascript_changed)
        return view

    def _build_header(self):
        header = Adw.HeaderBar()
        hide = pages.icon_button('sidebar-show-right-symbolic',
                                 _('Hide Web Column'))
        pages.connect_weak(hide, 'clicked', self._on_close_clicked)
        header.pack_start(hide)
        self.back_button = pages.icon_button('go-previous-symbolic',
                                             _('Back'))
        self.forward_button = pages.icon_button('go-next-symbolic',
                                                _('Forward'))
        pages.connect_weak(self.back_button, 'clicked', self._on_back)
        pages.connect_weak(self.forward_button, 'clicked', self._on_forward)
        header.pack_start(self.back_button)
        header.pack_start(self.forward_button)

        self.title = Adw.WindowTitle()
        header.set_title_widget(self.title)

        menu = Gio.Menu()
        menu.append(_('_Reload'), 'web.reload')
        menu.append(_('Open in _Browser'), 'web.open-browser')
        menu.append(_('_Copy Link'), 'web.copy-link')
        button = Gtk.MenuButton(icon_name='view-more-symbolic',
                                menu_model=menu)
        button.add_css_class('flat')
        pages.set_accessible_label(button, _('Page Actions'))
        header.pack_end(button)
        return header

    def _build_error_page(self):
        self.error_page = Adw.StatusPage(
            icon_name='network-error-symbolic', vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)
        browser = Gtk.Button(label=_('Open in Browser'))
        browser.add_css_class('pill')
        pages.connect_weak(browser, 'clicked', self._on_open_browser)
        self.error_reload = Gtk.Button(label=_('Reload'))
        self.error_reload.add_css_class('pill')
        pages.connect_weak(self.error_reload, 'clicked', self._on_reload)
        box.append(browser)
        box.append(self.error_reload)
        self.error_page.set_child(box)
        return self.error_page

    def _add_shortcuts(self):
        # Bubble phase, so a page that uses Escape itself (a site dialog,
        # fullscreen video) keeps it. Ctrl+W, Ctrl+R, F5 and Alt+Left are
        # application accelerators, matched ahead of every controller;
        # they reach the column through forward_action().
        controller = Gtk.ShortcutController()
        for trigger, method in (('Escape', self._on_close_clicked),
                                ('<Alt>Right', self._on_forward)):
            controller.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(trigger),
                Gtk.CallbackAction.new(self._shortcut_callback(method))))
        self.add_controller(controller)

    def _shortcut_callback(self, method):
        target = pages.weak_method(method)

        def activate(_widget, _args):
            target()
            return True
        return activate

    # Public

    def load_uri(self, uri):
        self.stack.set_visible_child_name('page')
        self._error_uri = uri
        self._pending_user = True
        self.view.load_uri(uri)

    # Navigation policy

    def _on_decide_policy(self, view, decision, kind):
        WebKit = self._webkit
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            return self._on_response(decision)
        new_window = kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION
        if kind != WebKit.PolicyDecisionType.NAVIGATION_ACTION \
                and not new_window:
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        config = pages.settings()
        link_clicked = (action.get_navigation_type()
                        == WebKit.NavigationType.LINK_CLICKED)
        user_gesture = action.is_user_gesture()
        untargeted = not action.get_frame_name()
        if untargeted and not new_window:
            self._start_navigation(action)
        # WebKit 6.0 does not say whether a navigation targets the main
        # frame (get_frame_name is only the link's target name), and the
        # strip path reloads the whole view. So stripping is limited to
        # a user's untargeted click; a click inside an iframe can still
        # match.
        verdict, target = classify_navigation(
            uri,
            new_window=new_window,
            link_clicked=link_clicked,
            user_gesture=user_gesture,
            button=action.get_mouse_button(),
            modifiers=int(action.get_modifiers()),
            strip=(config.get_boolean('strip-tracking')
                   and untargeted and user_gesture),
            resolve=config.get_boolean('aggressive-resolving'))
        if verdict == LOAD:
            if new_window or target != uri:
                decision.ignore()
                self._pending_user = user_gesture
                self.view.load_uri(target)
            else:
                decision.use()
        elif verdict == EXTERNAL:
            decision.ignore()
            pages.launch_external(self, target)
        elif verdict == ROUTE:
            decision.ignore()
            pages.open_link(self, target, resolve=True)
        else:
            decision.ignore()
        return True

    def _start_navigation(self, action):
        user = self._pending_user or action.is_user_gesture() \
            or (action.is_redirect() and self._user_nav)
        self._pending_user = False
        if not action.is_redirect():
            self._handed_off = False
        self._user_nav = user

    def _on_response(self, decision):
        """A main-frame response that is a download or a type WebKit
        cannot show goes to the system once, and only for a navigation
        the user began: a script's ``location =`` must not launch
        anything."""
        if not decision.is_main_frame_main_resource():
            return False
        response = decision.get_response()
        headers = response.get_http_headers()
        disposition = headers.get_one('Content-Disposition') \
            if headers is not None else None
        attachment = bool(disposition) and \
            disposition.strip().lower().startswith('attachment')
        if decision.is_mime_type_supported() and not attachment:
            return False
        decision.ignore()
        if self._user_nav and not self._handed_off:
            self._handed_off = True
            pages.launch_external(self, response.get_uri())
            if not self._committed and not self.view.can_go_back():
                GLib.idle_add(pages.weak_method(self._close_idle))
        return True

    def _close_idle(self):
        self.close_column()
        return GLib.SOURCE_REMOVE

    def _on_create(self, _view, _action):
        return None

    def _on_permission_request(self, _view, request):
        request.deny()
        return True

    def _on_context_menu(self, _view, menu, *rest):
        WebKit = self._webkit
        drop = (WebKit.ContextMenuAction.OPEN_LINK_IN_NEW_WINDOW,
                WebKit.ContextMenuAction.DOWNLOAD_LINK_TO_DISK,
                WebKit.ContextMenuAction.DOWNLOAD_IMAGE_TO_DISK,
                WebKit.ContextMenuAction.DOWNLOAD_AUDIO_TO_DISK,
                WebKit.ContextMenuAction.DOWNLOAD_VIDEO_TO_DISK)
        for item in list(menu.get_items()):
            if item.get_stock_action() in drop:
                menu.remove(item)
        hit = rest[-1] if rest else None
        if hit is not None and hit.context_is_link():
            menu.append(WebKit.ContextMenuItem.new_from_gaction(
                self._open_link_action, _('Open Link in _Browser'),
                GLib.Variant('s', hit.get_link_uri())))
        return False

    def _on_open_link(self, _action, parameter):
        pages.launch_external(self, parameter.get_string())

    # Failures

    def _show_error(self, title, description, uri, reload=False):
        self._error_uri = uri or self._error_uri
        self.error_page.set_title(title)
        self.error_page.set_description(description)
        self.error_reload.set_visible(reload)
        self.stack.set_visible_child_name('error')

    def _on_load_failed(self, _view, _event, uri, error):
        WebKit = self._webkit
        if error.matches(WebKit.NetworkError.quark(),
                         int(WebKit.NetworkError.CANCELLED)) \
                or error.matches(
                    WebKit.PolicyError.quark(),
                    int(WebKit.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE)):
            return True
        self._show_error(_('Page Did Not Load'), error.message, uri,
                         reload=True)
        return True

    def _on_tls_failed(self, _view, uri, _certificate, _errors):
        self._show_error(
            _('Security Certificate Not Trusted'),
            _('The connection is not private, so the page was not '
              'loaded. You can open it in your browser instead.'),
            uri)
        return True

    def _on_terminated(self, _view, reason):
        WebKit = self._webkit
        if reason == WebKit.WebProcessTerminationReason.EXCEEDED_MEMORY_LIMIT:
            description = _('The page used too much memory and was '
                            'stopped.')
        else:
            description = _('The page stopped responding.')
        self._show_error(_('Page Crashed'), description,
                         self.view.get_uri(), reload=True)

    # State

    def _on_title_uri(self, _view, _pspec):
        shown = self._webkit.uri_for_display(self.view.get_uri() or '') or ''
        self.title.set_title(self.view.get_title() or shown)
        self.title.set_subtitle(shown)

    def _on_uri(self, _view, _pspec):
        self._refresh_nav()

    def _on_load_changed(self, _view, event):
        if event == self._webkit.LoadEvent.COMMITTED:
            self._committed = True
            self.stack.set_visible_child_name('page')
        self._refresh_nav()

    def _on_progress(self, _view, _pspec):
        loading = self.view.is_loading()
        self.progress.set_visible(loading)
        if loading:
            self.progress.set_fraction(self.view.get_estimated_load_progress())

    def _refresh_nav(self):
        self.back_button.set_sensitive(self.view.can_go_back())
        self.forward_button.set_sensitive(self.view.can_go_forward())

    def _on_javascript_changed(self, config, _key):
        self.view.get_settings().set_enable_javascript(
            config.get_boolean('web-javascript'))

    # Commands

    def _on_back(self, *_args):
        self.view.go_back()

    def _on_forward(self, *_args):
        self.view.go_forward()

    def _on_reload(self, *_args):
        if self.stack.get_visible_child_name() == 'error' and self._error_uri:
            self.load_uri(self._error_uri)
        else:
            self.view.reload()

    def _current_uri(self):
        return self.view.get_uri() or self._error_uri

    def _on_open_browser(self, *_args):
        uri = self._current_uri()
        if uri:
            pages.launch_external(self, uri)

    def _on_copy_link(self, *_args):
        uri = self._current_uri()
        if uri:
            self.get_clipboard().set(uri)
            pages.show_toast(self, _('Copied link to clipboard'))

    def _on_close_clicked(self, *_args):
        self.close_column()

    def close_column(self):
        """The user's explicit close: drop the column and hide its slot."""
        split = getattr(pages.window_of(self), 'web_split', None)
        self.dispose_column()
        if split is not None:
            split.set_show_sidebar(False)

    def dispose_column(self):
        """Stop the page and drop the column, so the web process exits."""
        if self._disposed:
            return
        self._disposed = True
        self.view.stop_loading()
        parent = self.get_parent()
        if parent is not None:
            parent.set_child(None)
