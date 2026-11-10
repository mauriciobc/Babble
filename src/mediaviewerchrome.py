# SPDX-License-Identifier: GPL-3.0-or-later

"""Widget construction for the media viewer: the header bar with its
buttons and menu, the stage with its zoom controls and navigation, and
the description and dots below.

:class:`MediaViewerChrome` is a mixin of :class:`mediaviewer.MediaViewer`
(it needs the viewer as ``self``: every widget it builds is an attribute
of the viewer, and its signals are wired to the viewer's own methods).
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Pango', '1.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .mediazoom import ZoomPicture  # noqa: E402

# Below this the box gives its inset back and the photo takes the whole
# window: the window's own narrow layout starts at the same width.
VIEWER_COMPACT = 'max-width: 600sp or max-height: 400sp'


def _label(widget, text):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def _icon_button(icon, tooltip, css=()):
    # Not flat by default: these sit over the picture in a header bar or
    # an overlay, where pages.icon_button's flat would flatten a button
    # that is meant to read as a control on top of media.
    return pages.icon_button(icon, tooltip, flat=False, css=css)


class MediaViewerChrome:
    """Builds the media viewer's widgets."""

    # Construction

    def _build(self):
        self.set_focusable(True)
        self.header = Adw.HeaderBar()
        self.header.add_css_class('media-viewer-header')
        # The viewer is an overlay, so its header sits where the window's
        # own is: without this both draw a set of controls and there are
        # two close buttons.
        self.header.set_show_start_title_buttons(False)
        self.header.set_show_end_title_buttons(False)
        self.window_title = Adw.WindowTitle(title=_('Media'))
        self.header.set_title_widget(self.window_title)

        # A dialog brings its own close button; an overlay needs one.
        self.close_button = Gtk.Button(icon_name='window-close-symbolic',
                                       tooltip_text=_('Close'))
        self.close_button.add_css_class('circular')
        _label(self.close_button, _('Close'))
        pages.connect_weak(self.close_button, 'clicked',
                           self._on_close_clicked)
        self.header.pack_end(self.close_button)

        self.fullscreen_button = _icon_button(
            'view-fullscreen-symbolic', _('Fullscreen'))
        pages.connect_weak(self.fullscreen_button, 'clicked',
                           self._on_fullscreen_clicked)
        self.header.pack_end(self.fullscreen_button)

        menu = Gio.Menu()
        menu.append(_('_Copy URL'), 'viewer.copy-url')
        copy_image = Gio.MenuItem.new(_('Copy _Image'), 'viewer.copy-image')
        # Only images can be copied; the action is enabled while one is
        # shown, and the item hidden otherwise.
        copy_image.set_attribute_value(
            'hidden-when', GLib.Variant.new_string('action-disabled'))
        menu.append_item(copy_image)
        menu.append(_('_Open in Browser'), 'viewer.open-browser')
        menu.append(_('Open _With…'), 'viewer.open-with')
        self.menu_button = Gtk.MenuButton(
            icon_name='view-more-symbolic', menu_model=menu,
            tooltip_text=_('More Actions'), primary=False)
        _label(self.menu_button, _('More Actions'))
        self.header.pack_end(self.menu_button)

        self.save_button = _icon_button(
            'document-save-symbolic', _('Save As…'))
        pages.connect_weak(self.save_button, 'clicked', self._on_save_clicked)
        self.header.pack_end(self.save_button)

        actions = Gio.SimpleActionGroup()
        self._actions = {}
        for name in ('copy-url', 'copy-image', 'open-browser', 'open-with'):
            action = Gio.SimpleAction.new(name, None)
            pages.connect_weak(action, 'activate', self._on_action)
            actions.add_action(action)
            self._actions[name] = action
        self._actions['copy-image'].set_enabled(False)
        self.insert_action_group('viewer', actions)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            hexpand=True, vexpand=True)
        self.stack.add_css_class('media-viewer-stage')

        spinner_page = Gtk.Box(halign=Gtk.Align.CENTER,
                               valign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(width_request=48, height_request=48)
        _label(self.spinner, _('Loading'))
        spinner_page.append(self.spinner)
        self.stack.add_named(spinner_page, 'loading')

        self.picture = ZoomPicture()
        pages.connect_weak(self.picture, 'zoom-changed',
                           self._on_zoom_changed)
        self.stack.add_named(self.picture, 'image')
        # Dragging the picture out only starts while it fits the window
        # (prepare refuses otherwise): zoomed in, the drag pans it.
        self.picture_drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        pages.connect_weak(self.picture_drag, 'prepare',
                           self._on_picture_drag_prepare)
        self.picture.add_controller(self.picture_drag)

        self.media_bin = Adw.Bin(hexpand=True, vexpand=True)
        self.stack.add_named(self.media_bin, 'media')
        self.media_drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        pages.connect_weak(self.media_drag, 'prepare',
                           self._on_media_drag_prepare)
        self.media_bin.add_controller(self.media_drag)

        # Video, audio and GIFVs waiting to be loaded (a metered connection
        # or the user's choice): the preview with a button to load them.
        self.deferred = Gtk.Overlay(hexpand=True, vexpand=True)
        self.deferred_picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
            hexpand=True, vexpand=True,
            accessible_role=Gtk.AccessibleRole.IMG)
        self.deferred.set_child(self.deferred_picture)
        self.load_button = Gtk.Button(
            use_underline=True, halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER)
        self.load_button.add_css_class('pill')
        self.load_button.add_css_class('suggested-action')
        pages.connect_weak(self.load_button, 'clicked', self._on_load_clicked)
        self.deferred.add_overlay(self.load_button)
        self.stack.add_named(self.deferred, 'deferred')
        self.deferred_drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        pages.connect_weak(self.deferred_drag, 'prepare',
                           self._on_media_drag_prepare)
        self.deferred.add_controller(self.deferred_drag)

        self.error_page = Adw.StatusPage(
            icon_name='dialog-error-symbolic', vexpand=True)
        self.error_buttons = Gtk.Box(halign=Gtk.Align.CENTER, spacing=12)
        self.retry_button = Gtk.Button(label=_('_Try Again'),
                                       use_underline=True)
        self.retry_button.add_css_class('pill')
        self.retry_button.add_css_class('suggested-action')
        pages.connect_weak(self.retry_button, 'clicked',
                           self._on_retry_clicked)
        self.error_browser_button = Gtk.Button(
            label=_('_Open in Browser'), use_underline=True)
        self.error_browser_button.add_css_class('pill')
        pages.connect_weak(self.error_browser_button, 'clicked',
                           self._on_browser_clicked)
        self.error_buttons.append(self.retry_button)
        self.error_buttons.append(self.error_browser_button)
        self.error_page.set_child(self.error_buttons)
        self.stack.add_named(self.error_page, 'error')

        self.overlay = Gtk.Overlay(child=self.stack)

        self.previous_button = _icon_button(
            'go-previous-symbolic', _('Previous Attachment'),
            ('osd', 'circular'))
        self.previous_button.set_halign(Gtk.Align.START)
        self.previous_button.set_valign(Gtk.Align.CENTER)
        self.previous_button.set_margin_start(12)
        pages.connect_weak(self.previous_button, 'clicked',
                           self._on_previous_clicked)
        self.next_button = _icon_button(
            'go-next-symbolic', _('Next Attachment'), ('osd', 'circular'))
        self.next_button.set_halign(Gtk.Align.END)
        self.next_button.set_valign(Gtk.Align.CENTER)
        self.next_button.set_margin_end(12)
        pages.connect_weak(self.next_button, 'clicked', self._on_next_clicked)
        self.overlay.add_overlay(self.previous_button)
        self.overlay.add_overlay(self.next_button)

        self.zoom_controls = Gtk.Box(
            spacing=2, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
            margin_bottom=12)
        self.zoom_controls.add_css_class('osd')
        self.zoom_controls.add_css_class('toolbar')
        self.zoom_controls.add_css_class('media-viewer-zoom')
        self.zoom_out_button = _icon_button(
            'zoom-out-symbolic', _('Zoom Out'), ('flat', 'circular'))
        pages.connect_weak(self.zoom_out_button, 'clicked',
                           self._on_zoom_out_clicked)
        self.zoom_label_button = Gtk.Button(
            label='100%', tooltip_text=_('Toggle Fit and Actual Size'))
        self.zoom_label_button.add_css_class('flat')
        self.zoom_label_button.add_css_class('numeric')
        pages.connect_weak(self.zoom_label_button, 'clicked',
                           self._on_zoom_label_clicked)
        self.zoom_in_button = _icon_button(
            'zoom-in-symbolic', _('Zoom In'), ('flat', 'circular'))
        pages.connect_weak(self.zoom_in_button, 'clicked',
                           self._on_zoom_in_clicked)
        self.zoom_fit_button = _icon_button(
            'zoom-fit-best-symbolic', _('Fit to Window'),
            ('flat', 'circular'))
        pages.connect_weak(self.zoom_fit_button, 'clicked',
                           self._on_zoom_fit_clicked)
        for widget in (self.zoom_out_button, self.zoom_label_button,
                       self.zoom_in_button, self.zoom_fit_button):
            self.zoom_controls.append(widget)
        self.overlay.add_overlay(self.zoom_controls)

        self.progress = Gtk.ProgressBar(
            valign=Gtk.Align.START, visible=False)
        self.progress.add_css_class('osd')
        self.overlay.add_overlay(self.progress)

        # Shown by a stream that has been rebuffering for a second.
        self.buffering_spinner = Adw.Spinner(
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            width_request=48, height_request=48, visible=False)
        _label(self.buffering_spinner, _('Loading'))
        self.overlay.add_overlay(self.buffering_spinner)

        self._build_bottom()

        self.toolbar = Adw.ToolbarView(content=self.overlay)
        self.toolbar.add_css_class('media-viewer-content')
        self.toolbar.add_top_bar(self.header)
        self.toolbar.add_bottom_bar(self.bottom)
        self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        self.toolbar.set_bottom_bar_style(Adw.ToolbarStyle.FLAT)

        self.toasts = Adw.ToastOverlay(child=self.toolbar)
        # The viewer fills the window as an overlay, so the black box is
        # kept smaller by an inner card: Gtk.Overlay honours the alignment
        # of an overlay child, which a plain container would swallow.
        self._stage = Gtk.Overlay()
        # Transparent, but it fills the window and so takes the clicks
        # that land beside the box instead of letting them reach the app.
        self._backdrop = Gtk.Box()
        self._stage.set_child(self._backdrop)
        # The box is inset from the window by margins rather than sized
        # from it, so it follows every resize of the window.
        self._card = Gtk.Box()
        self._card.add_css_class('media-viewer-card')
        self._card.append(self.toasts)
        self._stage.add_overlay(self._card)
        compact = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(VIEWER_COMPACT))
        compact.add_setter(self, 'compact', True)
        # Under the window's 360 px floor (window.ui) by its own padding,
        # as the profile page does: asking for 360 overflowed by 10 px.
        stage_bin = Adw.BreakpointBin(child=self._stage,
                                      width_request=340, height_request=294)
        stage_bin.add_breakpoint(compact)
        self.set_child(stage_bin)
        pages.connect_weak(self, 'notify::compact',
                           self._on_compact_changed)

        keys = Gtk.EventControllerKey()
        pages.connect_weak(keys, 'key-pressed', self._on_key_pressed)
        self.add_controller(keys)

    def _build_bottom(self):
        self.bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=6)
        self.bottom.add_css_class('media-viewer-bottom')

        self.alt_box = Gtk.Box(spacing=8, valign=Gtk.Align.START)
        self.alt_box.add_css_class('media-viewer-alt')
        self.alt_toggle = Gtk.ToggleButton(
            label=_('ALT'), active=True, valign=Gtk.Align.START,
            tooltip_text=_('Show or Hide Description'))
        self.alt_toggle.add_css_class('media-viewer-alt-badge')
        _label(self.alt_toggle, _('Show or Hide Description'))
        pages.connect_weak(self.alt_toggle, 'toggled', self._on_alt_toggled)
        self.alt_box.append(self.alt_toggle)

        self.alt_label = Gtk.Label(
            wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
            selectable=True, hexpand=True, valign=Gtk.Align.START)
        self.alt_label.add_css_class('media-viewer-alt-text')
        self.alt_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=140,
            hexpand=True, child=self.alt_label)
        self.alt_revealer = Gtk.Revealer(
            reveal_child=True, hexpand=True,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            child=self.alt_scroll)
        self.alt_box.append(self.alt_revealer)
        self.bottom.append(self.alt_box)

        self.dots = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.dots.add_css_class('media-viewer-dots')
        self.bottom.append(self.dots)
        for _position in self.attachments:
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class('media-viewer-dot')
            self.dots.append(dot)
