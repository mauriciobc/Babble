# SPDX-License-Identifier: GPL-3.0-or-later

"""Full-size viewer for post attachments, in the spirit of Tuba's
MediaViewer and Phanpy's media modal.

:func:`present_media` opens a dark dialog over the window showing the
attachments of one post: images with zoom, pinch and pan, videos with
playback controls, muted looping GIFVs and audio with a cover. Left and
Right (or the side buttons) move between attachments, the alt text sits
below the media, and the header offers Save As, Copy URL, Open in Browser
and Fullscreen.
"""

import collections
import os
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango, Soup,
)

from . import pages  # noqa: E402

DIALOG_WIDTH = 1100
DIALOG_HEIGHT = 760
ZOOM_STEP = 1.25
MAX_SCALE = 8.0
CHUNK_SIZE = 64 * 1024
TEXTURE_CACHE_SIZE = 6

_session = None
_textures = collections.OrderedDict()


def _http_session():
    global _session
    if _session is None:
        _session = Soup.Session()
        _session.set_timeout(30)
    return _session


def _check_status(message):
    """Raise a GLib.Error when *message* ended in a non-2xx status."""
    status = message.get_status()
    if status < 200 or status >= 300:
        raise GLib.Error.new_literal(
            Gio.io_error_quark(),
            _('The server answered with HTTP {status}').format(
                status=status),
            Gio.IOErrorEnum.FAILED)


def load_texture(url, callback, cancellable=None):
    """Fetch and decode *url* to a Gdk.Texture; ``callback(texture, error)``
    runs on the main loop (never when *cancellable* was cancelled)."""
    texture = _textures.get(url)
    if texture is not None:
        _textures.move_to_end(url)
        GLib.idle_add(_deliver_texture, callback, texture, cancellable)
        return
    message = Soup.Message.new('GET', url)
    if message is None:
        error = GLib.Error.new_literal(
            Gio.io_error_quark(), _('Invalid media address'),
            Gio.IOErrorEnum.INVALID_ARGUMENT)
        GLib.idle_add(_deliver_error, callback, error, cancellable)
        return

    def on_read(session, result):
        if cancellable is not None and cancellable.is_cancelled():
            return
        try:
            data = session.send_and_read_finish(result)
            _check_status(message)
            decoded = Gdk.Texture.new_from_bytes(data)
        except GLib.Error as error:
            callback(None, error)
            return
        _textures[url] = decoded
        while len(_textures) > TEXTURE_CACHE_SIZE:
            _textures.popitem(last=False)
        callback(decoded, None)

    _http_session().send_and_read_async(
        message, GLib.PRIORITY_DEFAULT, cancellable, on_read)


def _deliver_texture(callback, texture, cancellable):
    if cancellable is None or not cancellable.is_cancelled():
        callback(texture, None)
    return GLib.SOURCE_REMOVE


def _deliver_error(callback, error, cancellable):
    if cancellable is None or not cancellable.is_cancelled():
        callback(None, error)
    return GLib.SOURCE_REMOVE


def download(url, destination, on_progress=None, on_done=None,
             cancellable=None):
    """Stream *url* into the Gio.File *destination*.

    ``on_progress(fraction)`` gets 0..1 when the size is known, else -1.
    ``on_done(error)`` gets None on success. The destination is replaced
    atomically, so a failed or cancelled download leaves nothing behind.
    """
    _Download(url, destination, on_progress, on_done, cancellable).start()


class _Download:
    def __init__(self, url, destination, on_progress, on_done, cancellable):
        self.url = url
        self.destination = destination
        self.on_progress = on_progress
        self.on_done = on_done
        self.cancellable = cancellable
        self.message = None
        self.source = None
        self.output = None
        self.received = 0
        self.total = 0
        self.finished = False

    def start(self):
        self.message = Soup.Message.new('GET', self.url)
        if self.message is None:
            self.fail(GLib.Error.new_literal(
                Gio.io_error_quark(), _('Invalid media address'),
                Gio.IOErrorEnum.INVALID_ARGUMENT))
            return
        _http_session().send_async(
            self.message, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_sent)

    def fail(self, error):
        if self.finished:
            return
        self.finished = True
        if self.output is not None:
            # Closing with a cancelled cancellable discards the temporary
            # file instead of renaming it over the destination.
            discard = Gio.Cancellable()
            discard.cancel()
            self.output.close_async(GLib.PRIORITY_DEFAULT, discard, None)
        if self.source is not None:
            self.source.close_async(GLib.PRIORITY_DEFAULT, None, None)
        if self.on_done is not None:
            self.on_done(error)

    def succeed(self):
        if self.finished:
            return
        self.finished = True
        if self.on_done is not None:
            self.on_done(None)

    def on_sent(self, session, result):
        try:
            self.source = session.send_finish(result)
            _check_status(self.message)
        except GLib.Error as error:
            self.fail(error)
            return
        self.total = self.message.get_response_headers().get_content_length()
        self.destination.replace_async(
            None, False, Gio.FileCreateFlags.REPLACE_DESTINATION,
            GLib.PRIORITY_DEFAULT, self.cancellable, self.on_opened)

    def on_opened(self, destination, result):
        try:
            self.output = destination.replace_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        self.read()

    def read(self):
        self.source.read_bytes_async(
            CHUNK_SIZE, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_read)

    def on_read(self, source, result):
        try:
            chunk = source.read_bytes_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        if chunk.get_size() == 0:
            self.output.close_async(
                GLib.PRIORITY_DEFAULT, self.cancellable, self.on_closed)
            return
        self.received += chunk.get_size()
        if self.on_progress is not None:
            self.on_progress(self.received / self.total
                             if self.total > 0 else -1)
        self.write(chunk)

    def write(self, chunk):
        self.output.write_bytes_async(
            chunk, GLib.PRIORITY_DEFAULT, self.cancellable,
            self.on_written, chunk)

    def on_written(self, output, result, chunk):
        try:
            written = output.write_bytes_finish(result)
        except GLib.Error as error:
            self.fail(error)
            return
        if written < chunk.get_size():
            data = chunk.get_data()[written:]
            self.write(GLib.Bytes.new(data))
            return
        self.read()

    def on_closed(self, output, result):
        try:
            output.close_finish(result)
        except GLib.Error as error:
            self.output = None
            self.fail(error)
            return
        self.output = None
        self.source.close_async(GLib.PRIORITY_DEFAULT, None, None)
        self.succeed()


def suggested_filename(attachment):
    """File name for Save As: the last URL path segment, else a generic
    name with an extension matching the attachment type."""
    url = attachment.get('url') or attachment.get('remote_url') or ''
    name = os.path.basename(urllib.parse.unquote(
        urllib.parse.urlsplit(url).path)).replace(os.sep, '_').strip()
    if name:
        return name
    kind = attachment.get('type')
    extension = {'image': '.png', 'video': '.mp4', 'gifv': '.mp4',
                 'audio': '.ogg'}.get(kind, '')
    return _('media') + extension


def _label(widget, text):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def _icon_button(icon, tooltip, css=()):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    _label(button, tooltip)
    for name in css:
        button.add_css_class(name)
    return button


class ZoomPicture(Gtk.Widget):
    """Texture view with fit-to-window, zoom and pan.

    The scale is the on-screen size over the texture's pixel size, so 1.0
    is "100%". While *fitted* the scale follows the widget size (and never
    upscales); any explicit zoom leaves the fit mode, and zooming back out
    to the fit scale returns to it.
    """

    __gtype_name__ = 'BabbleZoomPicture'
    __gsignals__ = {
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, **kwargs):
        super().__init__(hexpand=True, vexpand=True, focusable=True,
                         overflow=Gtk.Overflow.HIDDEN,
                         accessible_role=Gtk.AccessibleRole.IMG, **kwargs)
        self.add_css_class('media-viewer-picture')
        self.texture = None
        self.fitted = True
        self._scale = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_origin = (0.0, 0.0)
        self._pinch_origin = 1.0

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        pages.connect_weak(drag, 'drag-begin', self._on_drag_begin)
        pages.connect_weak(drag, 'drag-update', self._on_drag_update)
        self.add_controller(drag)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        pages.connect_weak(click, 'pressed', self._on_pressed)
        self.add_controller(click)

        pinch = Gtk.GestureZoom()
        pages.connect_weak(pinch, 'begin', self._on_pinch_begin)
        pages.connect_weak(pinch, 'scale-changed', self._on_pinch)
        self.add_controller(pinch)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        pages.connect_weak(scroll, 'scroll', self._on_scroll)
        self.add_controller(scroll)

    # Geometry

    def set_texture(self, texture):
        self.texture = texture
        self.fitted = True
        self._pan_x = self._pan_y = 0.0
        self.queue_draw()
        self._changed()

    def fit_scale(self):
        if self.texture is None:
            return 1.0
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return 1.0
        return min(width / self.texture.get_width(),
                   height / self.texture.get_height(), 1.0)

    @property
    def scale(self):
        return self.fit_scale() if self.fitted else self._scale

    def can_zoom_in(self):
        return self.texture is not None and self.scale < self._max_scale()

    def can_zoom_out(self):
        return self.texture is not None and not self.fitted

    def _max_scale(self):
        return max(MAX_SCALE, self.fit_scale())

    def _scaled_size(self, scale):
        return (self.texture.get_width() * scale,
                self.texture.get_height() * scale)

    def _clamp_pan(self):
        if self.texture is None:
            return
        size_x, size_y = self._scaled_size(self.scale)
        limit_x = max(0.0, (size_x - self.get_width()) / 2)
        limit_y = max(0.0, (size_y - self.get_height()) / 2)
        self._pan_x = min(max(self._pan_x, -limit_x), limit_x)
        self._pan_y = min(max(self._pan_y, -limit_y), limit_y)

    def _changed(self):
        self.set_cursor_from_name(
            None if self.fitted else
            ('grabbing' if self._dragging else 'grab'))
        self.emit('zoom-changed')

    _dragging = False

    def zoom_to(self, scale, anchor=None):
        """Zoom to *scale* keeping the image point under *anchor* (widget
        coordinates, default the centre) in place."""
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        if anchor is None:
            anchor = (width / 2, height / 2)
        fit = self.fit_scale()
        scale = min(max(scale, fit), self._max_scale())
        old = self.scale
        old_x, old_y = self._scaled_size(old)
        # Image-space point under the anchor before the change.
        origin_x = (width - old_x) / 2 + self._pan_x
        origin_y = (height - old_y) / 2 + self._pan_y
        point_x = (anchor[0] - origin_x) / old
        point_y = (anchor[1] - origin_y) / old
        if scale <= fit * 1.001:
            self.fitted = True
            self._pan_x = self._pan_y = 0.0
        else:
            self.fitted = False
            self._scale = scale
            new_x, new_y = self._scaled_size(scale)
            self._pan_x = anchor[0] - point_x * scale - (width - new_x) / 2
            self._pan_y = anchor[1] - point_y * scale - (height - new_y) / 2
            self._clamp_pan()
        self.queue_draw()
        self._changed()

    def zoom_in(self, anchor=None):
        self.zoom_to(self.scale * ZOOM_STEP, anchor)

    def zoom_out(self, anchor=None):
        self.zoom_to(self.scale / ZOOM_STEP, anchor)

    def zoom_fit(self):
        self.zoom_to(0, None)

    def toggle_actual_size(self, anchor=None):
        """Double-click behaviour: fit <-> 100% (200% for small images
        that already fit at 100%)."""
        if not self.fitted:
            self.zoom_fit()
        elif self.fit_scale() < 0.999:
            self.zoom_to(1.0, anchor)
        else:
            self.zoom_to(2.0, anchor)

    def pan_by(self, dx, dy):
        if self.fitted:
            return False
        self._pan_x += dx
        self._pan_y += dy
        self._clamp_pan()
        self.queue_draw()
        return True

    # Widget vfuncs

    def do_measure(self, orientation, for_size):
        return (1, 400, -1, -1)

    def do_size_allocate(self, width, height, _baseline):
        self._clamp_pan()
        self._changed()

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        scale = self.scale
        size_x, size_y = self._scaled_size(scale)
        rect = Graphene.Rect().init(
            (width - size_x) / 2 + self._pan_x,
            (height - size_y) / 2 + self._pan_y, size_x, size_y)
        scaling = (Gsk.ScalingFilter.TRILINEAR if scale < 1.0
                   else Gsk.ScalingFilter.LINEAR)
        snapshot.append_scaled_texture(self.texture, scaling, rect)

    # Gestures

    def _on_drag_begin(self, gesture, x, y):
        self._drag_origin = (self._pan_x, self._pan_y)
        self._dragging = True
        self._changed()
        self.grab_focus()

    def _on_drag_update(self, gesture, dx, dy):
        if self.fitted:
            return
        self._pan_x = self._drag_origin[0] + dx
        self._pan_y = self._drag_origin[1] + dy
        self._clamp_pan()
        self.queue_draw()

    def _on_pressed(self, gesture, n_press, x, y):
        self._dragging = False
        self._changed()
        if n_press == 2:
            self.toggle_actual_size((x, y))

    def _on_pinch_begin(self, gesture, _sequence):
        self._pinch_origin = self.scale

    def _on_pinch(self, gesture, factor):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self._pinch_origin * factor, (x, y) if ok else None)

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            event = controller.get_current_event()
            position = event.get_position() if event is not None else None
            anchor = None
            if position is not None and position[0]:
                ok, point = self.get_native().compute_point(
                    self, Graphene.Point().init(position[1], position[2]))
                if ok:
                    anchor = (point.x, point.y)
            self.zoom_to(self.scale * (1.1 ** -dy), anchor)
            return True
        return self.pan_by(-dx * 40, -dy * 40)


class MediaViewer(Adw.Dialog):
    """Dialog browsing a list of Mastodon media attachments."""

    __gtype_name__ = 'BabbleMediaViewer'

    def __init__(self, parent, attachments, index=0):
        super().__init__(title=_('Media'), content_width=DIALOG_WIDTH,
                         content_height=DIALOG_HEIGHT,
                         follows_content_size=False)
        self.attachments = [a for a in attachments if isinstance(a, dict)]
        self.index = 0
        self.window = pages.window_of(parent)
        if self.window is None and parent is not None:
            root = parent.get_root()
            self.window = root if isinstance(root, Gtk.Window) else None
        self._cancellable = None
        self._stream = None
        self._temp_media = None
        self._closed = False
        self._we_fullscreened = False
        self._first_focus = True
        self._downloads = Gio.Cancellable()

        self._build()
        self.connect('closed', self._on_closed)
        if self.window is not None:
            self._fullscreen_handler = pages.connect_weak(
                self.window, 'notify::fullscreened',
                self._on_fullscreen_changed)
        self.show_index(index)

    # Construction

    def _build(self):
        self.header = Adw.HeaderBar()
        self.header.add_css_class('media-viewer-header')
        self.window_title = Adw.WindowTitle(title=_('Media'))
        self.header.set_title_widget(self.window_title)

        self.fullscreen_button = _icon_button(
            'view-fullscreen-symbolic', _('Fullscreen'))
        pages.connect_weak(self.fullscreen_button, 'clicked',
                           self._on_fullscreen_clicked)
        self.header.pack_end(self.fullscreen_button)

        menu = Gio.Menu()
        menu.append(_('_Copy URL'), 'viewer.copy-url')
        menu.append(_('_Open in Browser'), 'viewer.open-browser')
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
        for name in ('copy-url', 'open-browser'):
            action = Gio.SimpleAction.new(name, None)
            pages.connect_weak(action, 'activate', self._on_action)
            actions.add_action(action)
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

        self.media_bin = Adw.Bin(hexpand=True, vexpand=True)
        self.stack.add_named(self.media_bin, 'media')

        self.error_page = Adw.StatusPage(
            icon_name='dialog-error-symbolic', vexpand=True)
        self.error_page.add_css_class('compact')
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

        self._build_bottom()

        self.toolbar = Adw.ToolbarView(content=self.overlay)
        self.toolbar.add_css_class('media-viewer-content')
        self.toolbar.add_top_bar(self.header)
        self.toolbar.add_bottom_bar(self.bottom)
        self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        self.toolbar.set_bottom_bar_style(Adw.ToolbarStyle.FLAT)

        self.toasts = Adw.ToastOverlay(child=self.toolbar)
        self.set_child(self.toasts)

        keys = Gtk.EventControllerKey()
        pages.connect_weak(keys, 'key-pressed', self._on_key_pressed)
        self.add_controller(keys)

    def _on_fullscreen_clicked(self, _button):
        self.toggle_fullscreen()

    def _on_save_clicked(self, _button):
        self.save_as()

    def _on_action(self, action, _param):
        {'copy-url': self.copy_url,
         'open-browser': self.open_in_browser}[action.get_name()]()

    def _on_retry_clicked(self, _button):
        self.show_index(self.index)

    def _on_browser_clicked(self, _button):
        self.open_in_browser()

    def _on_previous_clicked(self, _button):
        self.go_previous()

    def _on_next_clicked(self, _button):
        self.go_next()

    def _on_zoom_out_clicked(self, _button):
        self.picture.zoom_out()

    def _on_zoom_label_clicked(self, _button):
        self.picture.toggle_actual_size()

    def _on_zoom_in_clicked(self, _button):
        self.picture.zoom_in()

    def _on_zoom_fit_clicked(self, _button):
        self.picture.zoom_fit()

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

    # Navigation

    def go_next(self):
        if self.index + 1 < len(self.attachments):
            self.show_index(self.index + 1)
            return True
        return False

    def go_previous(self):
        if self.index > 0:
            self.show_index(self.index - 1)
            return True
        return False

    def current(self):
        return self.attachments[self.index] if self.attachments else {}

    def show_index(self, index):
        """Display attachment *index* (clamped)."""
        self._cancel_loads()
        self._unload_media()
        if not self.attachments:
            self._show_error(_('No Media'), _('This post has no media.'),
                             retry=False, browser=False)
            return
        self.index = min(max(int(index), 0), len(self.attachments) - 1)
        attachment = self.current()
        self._update_chrome(attachment)
        url = attachment.get('url') or attachment.get('remote_url')
        kind = attachment.get('type')
        if not url:
            self._show_error(
                _('Preview Not Available'),
                _('This attachment has no address to load.'),
                retry=False, browser=False)
        elif kind == 'image':
            self._show_image(url)
        elif kind in ('video', 'gifv', 'audio'):
            self._show_stream(attachment, url)
        else:
            self._show_error(
                _('Preview Not Available'),
                _('This kind of attachment cannot be shown here.'),
                retry=False, browser=True)

    def _kind_title(self, attachment):
        return {'image': _('Image'), 'video': _('Video'),
                'gifv': _('Animated GIF'), 'audio': _('Audio')}.get(
                    attachment.get('type'), _('Attachment'))

    def _description(self, attachment):
        """Alt text for *attachment*, falling back to its kind."""
        description = (attachment.get('description') or '').strip()
        return description or self._kind_title(attachment)

    def _update_chrome(self, attachment):
        total = len(self.attachments)
        multiple = total > 1
        self.window_title.set_title(self._kind_title(attachment))
        if multiple:
            self.window_title.set_subtitle(
                # Translators: position in a media gallery, e.g. "2 of 4"
                _('{current} of {total}').format(
                    current=self.index + 1, total=total))
        else:
            self.window_title.set_subtitle('')
        self.previous_button.set_visible(multiple)
        self.next_button.set_visible(multiple)
        self.previous_button.set_sensitive(self.index > 0)
        self.next_button.set_sensitive(self.index + 1 < total)
        self.dots.set_visible(multiple)
        child = self.dots.get_first_child()
        position = 0
        while child is not None:
            if position == self.index:
                child.add_css_class('active')
            else:
                child.remove_css_class('active')
            child = child.get_next_sibling()
            position += 1
        _label(self.dots, _('Attachment {current} of {total}').format(
            current=self.index + 1, total=total))

        description = (attachment.get('description') or '').strip()
        self.alt_box.set_visible(bool(description))
        self.alt_label.set_text(description)
        self.picture.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [self._description(attachment)])
        self.zoom_controls.set_visible(False)

    def _on_alt_toggled(self, button):
        self.alt_revealer.set_reveal_child(button.get_active())

    # Loading

    def _cancel_loads(self):
        if self._cancellable is not None:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        # Downloads run on their own cancellable so a slow file save can
        # be stopped independently of the texture loads. Closing the
        # viewer has to stop those too, or they keep writing the file
        # for an attachment nobody is looking at any more.
        if self._downloads is not None:
            self._downloads.cancel()
        self._downloads = Gio.Cancellable()

    def _unload_media(self):
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.pause()
            if isinstance(stream, Gtk.MediaFile):
                stream.clear()
        media = self._temp_media
        self._temp_media = None
        if media is not None:
            self._discard_temp(media)
        child = self.media_bin.get_child()
        if isinstance(child, Gtk.Video):
            child.set_media_stream(None)
        self.media_bin.set_child(None)
        self.picture.set_texture(None)

    def _discard_temp(self, media):
        # Fire and forget: deleting a cancelled download's copy fails
        # silently and the empty directory is gone with the sandbox.
        media.delete_async(GLib.PRIORITY_DEFAULT, None, None)
        directory = media.get_parent()
        if directory is not None:
            directory.delete_async(GLib.PRIORITY_DEFAULT, None, None)

    def _show_loading(self):
        self.stack.set_visible_child_name('loading')
        self.zoom_controls.set_visible(False)

    def _show_error(self, title, description, retry=True, browser=True):
        self.error_page.set_title(title)
        self.error_page.set_description(description)
        self.retry_button.set_visible(retry)
        self.error_browser_button.set_visible(browser)
        self.error_buttons.set_visible(retry or browser)
        self.stack.set_visible_child_name('error')
        self.zoom_controls.set_visible(False)

    def _show_image(self, url):
        self._show_loading()
        cancellable = self._cancellable
        load_texture(url, self._on_texture, cancellable)

    def _on_texture(self, texture, error):
        if error is not None:
            self._show_error(_('Could Not Load Image'), error.message)
            return
        self.picture.set_texture(texture)
        self.stack.set_visible_child_name('image')
        self.zoom_controls.set_visible(True)
        self._update_zoom_controls()
        if self._first_focus:
            self._first_focus = False
            self.picture.grab_focus()

    def _on_zoom_changed(self, _picture):
        self._update_zoom_controls()

    def _update_zoom_controls(self):
        scale = self.picture.scale
        self.zoom_label_button.set_label('{:d}%'.format(round(scale * 100)))
        self.zoom_in_button.set_sensitive(self.picture.can_zoom_in())
        self.zoom_out_button.set_sensitive(self.picture.can_zoom_out())
        self.zoom_fit_button.set_sensitive(not self.picture.fitted)

    def _show_stream(self, attachment, url):
        self._show_loading()
        cancellable = self._cancellable
        try:
            directory = GLib.dir_make_tmp('babble-media-XXXXXX')
        except GLib.Error as error:
            self._show_error(_('Could Not Load Media'), error.message)
            return
        # Gtk.MediaFile reads remote files through gvfs, whose http
        # backend is unavailable inside a Flatpak sandbox (and hesitates
        # with buffering hiccups on the host as well). Download the media
        # with libsoup instead, like every other fetch in the app, and
        # hand the stream a local, seekable copy.
        path = os.path.basename(urllib.parse.unquote(
            urllib.parse.urlsplit(url).path)).replace(os.sep, '_').strip()
        destination = Gio.File.new_for_path(
            os.path.join(directory, path or 'media'))
        self._temp_media = destination
        self.progress.set_fraction(0)
        self.progress.set_visible(True)

        def progress(fraction):
            if self._closed:
                return
            if fraction < 0:
                self.progress.pulse()
            else:
                self.progress.set_fraction(fraction)

        def download_done(error):
            if cancellable is not self._cancellable \
                    or cancellable.is_cancelled():
                # The media was replaced or the viewer closed while the
                # download ran; the temp copy is discarded by the unload.
                return
            self.progress.set_visible(False)
            if error is None:
                self._start_stream(attachment, destination)
            else:
                self._show_error(_('Could Not Load Media'), error.message)

        download(url, destination, progress, download_done, cancellable)

    def _start_stream(self, attachment, destination):
        stream = Gtk.MediaFile.new_for_file(destination)
        self._stream = stream
        kind = attachment.get('type')
        animations = Gtk.Settings.get_default().get_property(
            'gtk-enable-animations')
        if kind == 'gifv':
            stream.set_loop(True)
            stream.set_muted(True)
            view = Gtk.Picture(
                paintable=stream, content_fit=Gtk.ContentFit.CONTAIN,
                can_shrink=True, hexpand=True, vexpand=True,
                accessible_role=Gtk.AccessibleRole.IMG)
            view.add_css_class('media-viewer-gifv')
            view.update_property([Gtk.AccessibleProperty.LABEL],
                                 [self._description(attachment)])
            click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
            pages.connect_weak(click, 'pressed', self._on_gifv_pressed, stream)
            view.add_controller(click)
            autoplay = animations
        elif kind == 'video':
            view = Gtk.Video(hexpand=True, vexpand=True)
            view.set_media_stream(stream)
            view.add_css_class('media-viewer-video')
            autoplay = True
        else:
            view = self._audio_view(attachment, stream)
            autoplay = True
        self.media_bin.set_child(view)
        pages.connect_weak(stream, 'notify::error', self._on_stream_error)
        pages.connect_weak(stream, 'notify::prepared', self._on_stream_prepared,
                           autoplay)
        if stream.get_error() is not None:
            self._on_stream_error(stream, None)
        elif stream.is_prepared():
            self._on_stream_prepared(stream, None, autoplay)

    def _audio_view(self, attachment, stream):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.add_css_class('media-viewer-audio')
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
            width_request=320, height_request=320,
            accessible_role=Gtk.AccessibleRole.IMG)
        cover.add_css_class('media-viewer-cover')
        icon = Gtk.Image(icon_name='audio-x-generic-symbolic',
                         pixel_size=128, width_request=320,
                         height_request=320)
        icon.add_css_class('media-viewer-audio-icon')
        icon.set_visible(True)
        cover.set_visible(False)
        preview = attachment.get('preview_url')
        if preview:
            def on_cover(texture, error):
                if error is None:
                    cover.set_paintable(texture)
                    cover.set_visible(True)
                    icon.set_visible(False)
            load_texture(preview, on_cover, self._cancellable)
        controls = Gtk.MediaControls(media_stream=stream, width_request=420)
        clamp = Adw.Clamp(maximum_size=520, child=controls)
        box.append(cover)
        box.append(icon)
        box.append(clamp)
        return box

    def _on_gifv_pressed(self, gesture, n_press, x, y, stream):
        if stream.get_playing():
            stream.pause()
        else:
            stream.play()

    def _on_stream_error(self, stream, _pspec):
        if stream is not self._stream:
            return
        error = stream.get_error()
        self._show_error(
            _('Could Not Load Media'),
            error.message if error is not None else '')

    def _on_stream_prepared(self, stream, _pspec, autoplay):
        if stream is not self._stream or not stream.is_prepared():
            return
        self.stack.set_visible_child_name('media')
        if autoplay:
            stream.play()
        if self._first_focus:
            self._first_focus = False
            child = self.media_bin.get_child()
            if child is not None:
                child.grab_focus()

    # Actions

    def copy_url(self):
        url = self.current().get('url')
        if not url:
            return False
        self.get_display().get_clipboard().set_content(
            Gdk.ContentProvider.new_for_value(GObject.Value(str, url)))
        self.toasts.add_toast(Adw.Toast(title=_('URL copied')))
        return True

    def open_in_browser(self):
        attachment = self.current()
        url = attachment.get('remote_url') or attachment.get('url')
        if not url:
            return False
        from .textemoji import launch_uri
        launch_uri(self.picture, url)
        return True

    def toggle_fullscreen(self):
        window = self.window
        if window is None:
            return
        if window.is_fullscreen():
            window.unfullscreen()
            self._we_fullscreened = False
        else:
            window.fullscreen()
            self._we_fullscreened = True

    def _on_fullscreen_changed(self, window, _pspec):
        fullscreen = window.is_fullscreen()
        self.fullscreen_button.set_icon_name(
            'view-restore-symbolic' if fullscreen
            else 'view-fullscreen-symbolic')
        label = _('Exit Fullscreen') if fullscreen else _('Fullscreen')
        self.fullscreen_button.set_tooltip_text(label)
        _label(self.fullscreen_button, label)
        if fullscreen:
            self.set_content_width(max(window.get_width(), DIALOG_WIDTH))
            self.set_content_height(max(window.get_height(), DIALOG_HEIGHT))
        else:
            self.set_content_width(DIALOG_WIDTH)
            self.set_content_height(DIALOG_HEIGHT)

    def save_as(self):
        attachment = self.current()
        url = attachment.get('url') or attachment.get('remote_url')
        if not url:
            return False
        dialog = Gtk.FileDialog(
            title=_('Save Media'), accept_label=_('_Save'),
            initial_name=suggested_filename(attachment))
        dialog.save(self.window, None, self._on_save_chosen, url)
        return True

    def _on_save_chosen(self, dialog, result, url):
        try:
            destination = dialog.save_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.DialogError.quark(),
                                 Gtk.DialogError.DISMISSED):
                self._notify(_('Could not save media'))
            return
        self.save_to(url, destination)

    def save_to(self, url, destination, on_done=None):
        """Download *url* into the Gio.File *destination* with progress
        in the viewer and a toast when finished."""
        self.progress.set_fraction(0)
        self.progress.set_visible(True)
        name = destination.get_basename()

        def progress(fraction):
            if self._closed:
                return
            if fraction < 0:
                self.progress.pulse()
            else:
                self.progress.set_fraction(fraction)

        def done(error):
            self.progress.set_visible(False)
            if error is None:
                self._notify(_('Saved “{name}”').format(name=name),
                             destination)
            else:
                self._notify(_('Could not save media'))
            if on_done is not None:
                on_done(error)

        download(url, destination, progress, done, self._downloads)

    def _notify(self, text, saved=None):
        if self._closed:
            if self.window is not None:
                pages.show_toast(self.window, text)
            return
        toast = Adw.Toast(title=text)
        if saved is not None:
            toast.set_button_label(_('Show in _Folder'))
            toast.connect('button-clicked', self._on_show_folder, saved)
        self.toasts.add_toast(toast)

    def _on_show_folder(self, toast, saved):
        Gtk.FileLauncher.new(saved).open_containing_folder(
            self.window, None, None)

    # Events

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if state & (Gdk.ModifierType.CONTROL_MASK
                    | Gdk.ModifierType.ALT_MASK):
            return False
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        if keyval == Gdk.KEY_Left:
            return self.go_next() if rtl else self.go_previous()
        if keyval == Gdk.KEY_Right:
            return self.go_previous() if rtl else self.go_next()
        if keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.toggle_fullscreen()
            return True
        if self.stack.get_visible_child_name() == 'image':
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.picture.zoom_in()
                return True
            if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.picture.zoom_out()
                return True
            if keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.picture.zoom_fit()
                return True
        return False

    def _on_closed(self, _dialog):
        self._closed = True
        self._cancel_loads()
        self._unload_media()
        if self.window is not None:
            self.window.disconnect(self._fullscreen_handler)
            if self._we_fullscreened:
                self.window.unfullscreen()
                self._we_fullscreened = False


def present_media(parent_widget, attachments, index=0):
    """Open the media viewer on *attachments* (Mastodon media_attachments
    dicts) at *index* over the window of *parent_widget*; returns the
    dialog."""
    viewer = MediaViewer(parent_widget, attachments, index)
    window = pages.window_of(parent_widget)
    viewer.present(window if window is not None else parent_widget)
    return viewer
