# SPDX-License-Identifier: GPL-3.0-or-later

"""Full-size viewer for post attachments, in the spirit of Tuba's
MediaViewer and Phanpy's media modal.

:func:`present_media` covers the window with a dark viewer showing the
attachments of one post: images with zoom, pinch and pan, videos with
playback controls, muted looping GIFVs and audio with a cover. Left and
Right (or the side buttons) move between attachments, the alt text sits
below the media, and the header offers Close, Save As, Copy URL, Copy
Image, Open in Browser and Fullscreen. It is an overlay of the window
rather than a dialog, so Fullscreen really does fill the screen.

Media can be dragged out. An image that fits the window drags as the
picture itself (plus the file, once saved or opened with an app); zoomed
in, the same drag pans instead, so zoom back to fit (0, or double-click)
to drag it out. Video and audio drag as the downloaded copy, or as their
address before it is downloaded. While a video or audio plays, the
session is kept from going idle. On a metered connection video, audio
and GIFVs are not downloaded until asked; in power saver mode GIFVs do
not start on their own.
"""

import locale
import os
import time
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Pango', '1.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango,
)

from . import imagecache  # noqa: E402
from . import pages  # noqa: E402
from .mediaplayback import MediaPlayback, download  # noqa: E402
from .mediazoom import ZoomPicture  # noqa: E402

# A full-screen attachment at every zoom step, so this keeps far fewer
# than the timeline's media cache -- and must not share it, or opening a
# viewer would evict the timeline's thumbnails.
TEXTURE_CACHE_SIZE = 6

# Free space kept on each side of the viewer while it is a box over the
# window. It fills the window when fullscreen, or when the preference
# asks for that.
VIEWER_MARGIN = 64
# Below this the box gives its inset back and the photo takes the whole
# window: the window's own narrow layout starts at the same width.
VIEWER_COMPACT = 'max-width: 600sp or max-height: 400sp'

# The viewer currently on screen, if any. Opening another attachment
# replaces it: stacked overlays would each keep the whole window
# redrawing behind the other.
_open_viewer = None


def _texture_error(reason):
    """A GLib.Error for a failure *reason* from the shared fetcher.

    The fetcher reports 'HTTP %d' for a status; the viewer has always
    shown that as a translated sentence, and a raw reason would be a
    regression a user could see.
    """
    if reason == 'invalid URL':
        return GLib.Error.new_literal(
            Gio.io_error_quark(), _('Invalid media address'),
            Gio.IOErrorEnum.INVALID_ARGUMENT)
    if reason.startswith('HTTP '):
        return GLib.Error.new_literal(
            Gio.io_error_quark(),
            _('The server answered with HTTP {status}').format(
                status=reason[5:]),
            Gio.IOErrorEnum.FAILED)
    return GLib.Error.new_literal(
        Gio.io_error_quark(), reason, Gio.IOErrorEnum.FAILED)


# The viewer's own store: a full-screen attachment at every zoom step, so
# a handful is all it keeps. Shared machinery, separate budget -- it is
# not the timeline's media cache and must not evict it.
_viewer_store = imagecache.Store(TEXTURE_CACHE_SIZE)


def _decode_viewer_image(data, key, cancellable, done):
    # Remote attachments are untrusted: the same size-checked decode as
    # avatars and timeline media, with no box, so the full-size image up
    # to the decoded-pixel budget.
    from .mediacache import _decode_async
    _decode_async(data, None, cancellable, done)


_viewer_fetcher = imagecache.Fetcher(_viewer_store, _decode_viewer_image)


def load_texture(url, callback, cancellable=None):
    """Fetch and decode *url* to a Gdk.Texture; ``callback(texture, error)``
    runs on the main loop (never when *cancellable* was cancelled)."""

    def arrived(texture, reason):
        # Delivered on the main loop, and dropped when the caller has
        # moved on -- a cancelled load must not call back.
        if cancellable is not None and cancellable.is_cancelled():
            return
        if texture is None:
            callback(None, _texture_error(reason))
            return
        callback(texture, None)

    _viewer_fetcher.fetch(url, arrived, cancellable=cancellable)


# Open With copies older than this are deleted before the next one is
# downloaded; the app they were handed to has long since read them.
OPEN_WITH_MAX_AGE = 24 * 60 * 60


def prune_old_files(directory, max_age=OPEN_WITH_MAX_AGE):
    """Best-effort removal of files in *directory* not modified for
    *max_age* seconds."""
    cutoff = time.time() - max_age
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False) \
                    and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            pass


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
    # Not flat by default: these sit over the picture in a header bar or
    # an overlay, where pages.icon_button's flat would flatten a button
    # that is meant to read as a control on top of media.
    return pages.icon_button(icon, tooltip, flat=False, css=css)


# Drag and drop

# Longest side of a drag icon made from a picture, in logical pixels.
DRAG_ICON_SIZE = 128


def link_provider(url):
    """Drag content for the web address *url*: a ``text/uri-list`` for
    file managers and browsers, and the plain text for everything else."""
    return Gdk.ContentProvider.new_union([
        Gdk.ContentProvider.new_for_bytes(
            'text/uri-list', GLib.Bytes.new((url + '\r\n').encode())),
        Gdk.ContentProvider.new_for_value(GObject.Value(str, url))])


def media_provider(url=None, texture=None, file=None):
    """Drag content for an attachment: its decoded *texture* if there is
    one, the local Gio.File *file* if it has been downloaded, else its
    *url*. None when there is nothing to offer."""
    providers = []
    if texture is not None:
        providers.append(Gdk.ContentProvider.new_for_value(
            GObject.Value(Gdk.Texture, texture)))
    if file is not None:
        providers.append(Gdk.ContentProvider.new_for_value(
            GObject.Value(Gdk.FileList, Gdk.FileList.new_from_array([file]))))
    elif url:
        providers.append(link_provider(url))
    if not providers:
        return None
    if len(providers) == 1:
        return providers[0]
    return Gdk.ContentProvider.new_union(providers)


def set_drag_texture(source, texture):
    """Use a thumbnail of *texture* as the drag icon of the Gtk.DragSource
    *source*; a full-size picture would cover the drop target."""
    width, height = texture.get_width(), texture.get_height()
    scale = min(1.0, DRAG_ICON_SIZE / max(width, height, 1))
    width, height = max(1.0, width * scale), max(1.0, height * scale)
    snapshot = Gtk.Snapshot()
    snapshot.append_scaled_texture(
        texture, Gsk.ScalingFilter.TRILINEAR,
        Graphene.Rect().init(0, 0, width, height))
    paintable = snapshot.to_paintable(Graphene.Size().init(width, height))
    source.set_icon(paintable, int(width / 2), int(height / 2))


def set_drag_icon_name(source, widget, icon_name):
    """Use the themed icon *icon_name* as *source*'s drag icon."""
    theme = Gtk.IconTheme.get_for_display(widget.get_display())
    icon = theme.lookup_icon(icon_name, None, 64, widget.get_scale_factor(),
                             widget.get_direction(), 0)
    source.set_icon(icon, 32, 32)


class MediaViewer(MediaPlayback, Adw.Bin):
    """Browsing a list of Mastodon media attachments.

    An overlay of the window, not an :class:`Adw.Dialog`: a dialog floats
    inside the window and can never grow past it, so a fullscreen
    attachment would keep a margin on every side. Here the viewer *is* the
    window's content, and filling it means filling the screen.

    Stream download, GstPlay lifetime and idle inhibition live on the
    :class:`mediaplayback.MediaPlayback` mixin (ARCH-14).
    """

    __gtype_name__ = 'BabbleMediaViewer'

    # Set by a breakpoint while the window is too small for the box.
    compact = GObject.Property(type=bool, default=False)

    def __init__(self, parent, attachments, index=0):
        super().__init__()
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
        self._fullscreen_handler = None
        self._first_focus = True
        self._downloads = Gio.Cancellable()
        # Kind of the attachment the current stream plays.
        self._stream_kind = None
        # Idle inhibition held while a video or audio plays: the
        # application it was asked of and the cookie it returned.
        self._inhibit_app = None
        self._inhibit_cookie = 0
        # Saved and Open With copies by URL, offered when dragging out.
        self._local_files = {}

        self._build()
        if self.window is not None:
            self._fullscreen_handler = pages.connect_weak(
                self.window, 'notify::fullscreened',
                self._on_fullscreen_changed)
        self.show_index(index)

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

        # Video, audio and GIFVs waiting to be downloaded on a metered
        # connection: the preview with a button to load them.
        self.deferred = Gtk.Overlay(hexpand=True, vexpand=True)
        self.deferred_picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
            hexpand=True, vexpand=True,
            accessible_role=Gtk.AccessibleRole.IMG)
        self.deferred.set_child(self.deferred_picture)
        self.load_button = Gtk.Button(
            use_underline=True, halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            tooltip_text=_('Media is not downloaded automatically on a '
                           'metered connection'))
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

    def _on_close_clicked(self, _button):
        self.close()

    def _on_fullscreen_clicked(self, _button):
        self.toggle_fullscreen()

    def _on_save_clicked(self, _button):
        self.save_as()

    def _on_action(self, action, _param):
        {'copy-url': self.copy_url,
         'copy-image': self.copy_image,
         'open-browser': self.open_in_browser,
         'open-with': self.open_with}[action.get_name()]()

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
        self._actions['copy-image'].set_enabled(True)
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
        self.zoom_label_button.set_label(
            # %d is locale-formatted: '100 %' in French, which is how a
            # percentage is written there, not '100%'.
            locale.format_string('%d%%', round(scale * 100)))
        self.zoom_in_button.set_sensitive(self.picture.can_zoom_in())
        self.zoom_out_button.set_sensitive(self.picture.can_zoom_out())
        self.zoom_fit_button.set_sensitive(not self.picture.fitted)

    # Actions

    def copy_url(self):
        url = self.current().get('url')
        if not url:
            return False
        self.get_display().get_clipboard().set_content(
            Gdk.ContentProvider.new_for_value(GObject.Value(str, url)))
        self.toasts.add_toast(Adw.Toast(title=_('URL copied')))
        return True

    def copy_image(self):
        texture = self.picture.texture
        if texture is None or self.stack.get_visible_child_name() != 'image':
            return False
        # Gdk.Clipboard.set_texture() is not introspectable.
        self.get_display().get_clipboard().set_content(
            Gdk.ContentProvider.new_for_value(
                GObject.Value(Gdk.Texture, texture)))
        self.toasts.add_toast(Adw.Toast(title=_('Image copied')))
        return True

    def _local_file(self, url):
        """The saved or Open With copy of *url*, if it is still there."""
        file = self._local_files.get(url)
        if file is not None and not file.query_exists(None):
            del self._local_files[url]
            return None
        return file

    def _on_picture_drag_prepare(self, source, _x, _y):
        texture = self.picture.texture
        if texture is None or not self.picture.fitted:
            return None
        attachment = self.current()
        url = attachment.get('url') or attachment.get('remote_url')
        set_drag_texture(source, texture)
        return media_provider(url, texture, self._local_file(url))

    def _on_media_drag_prepare(self, source, _x, _y):
        attachment = self.current()
        url = attachment.get('url') or attachment.get('remote_url')
        # The temporary copy is complete once the stream plays it.
        file = self._temp_media if self._stream is not None else None
        if file is None:
            file = self._local_file(url)
        if file is None and not url:
            return None
        set_drag_icon_name(
            source, source.get_widget(),
            'audio-x-generic' if attachment.get('type') == 'audio'
            else 'video-x-generic')
        return media_provider(url, None, file)

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
        self._sync_viewer_to_window(window)

    def _on_compact_changed(self, _viewer, _pspec):
        if self.window is not None:
            self._sync_viewer_to_window(self.window)

    def _sync_viewer_to_window(self, window):
        """Fill the window when it is fullscreen, too small for the box,
        or when the preference asks photos to open that way; otherwise
        sit as a box inset from it."""
        fullscreen = window.is_fullscreen()
        expanded = fullscreen or self.compact or _opens_fullscreen()
        self.fullscreen_button.set_icon_name(
            'view-restore-symbolic' if fullscreen
            else 'view-fullscreen-symbolic')
        label = _('Exit Fullscreen') if fullscreen else _('Fullscreen')
        self.fullscreen_button.set_tooltip_text(label)
        _label(self.fullscreen_button, label)
        margin = 0 if expanded else VIEWER_MARGIN
        self._card.set_margin_top(margin)
        self._card.set_margin_bottom(margin)
        self._card.set_margin_start(margin)
        self._card.set_margin_end(margin)
        if expanded:
            self._card.remove_css_class('media-viewer-box')
        else:
            self._card.add_css_class('media-viewer-box')

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
                self._local_files[url] = destination
                self._notify(_('Saved “{name}”').format(name=name),
                             destination)
            else:
                self._notify(_('Could not save media'))
            if on_done is not None:
                on_done(error)

        download(url, destination, progress, done, self._downloads)

    def open_with(self):
        """Download the current attachment into the cache directory and
        offer the apps that can open it."""
        attachment = self.current()
        url = attachment.get('url') or attachment.get('remote_url')
        if not url:
            return False
        directory = os.path.join(GLib.get_user_cache_dir(), 'babble',
                                 'open-with')
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            self._notify(_('Could not open media'))
            return False
        prune_old_files(directory)
        destination = Gio.File.new_for_path(
            os.path.join(directory, suggested_filename(attachment)))
        self.progress.set_fraction(0)
        self.progress.set_visible(True)

        def progress(fraction):
            if self._closed:
                return
            if fraction < 0:
                self.progress.pulse()
            else:
                self.progress.set_fraction(fraction)

        def done(error):
            self.progress.set_visible(False)
            if error is not None:
                self._notify(_('Could not open media'))
                return
            self._local_files[url] = destination
            self.launch_file(destination)

        download(url, destination, progress, done, self._downloads)
        return True

    def launch_file(self, file):
        launcher = Gtk.FileLauncher.new(file)
        launcher.set_always_ask(True)
        launcher.launch(self.window, None, self._on_launched)

    def _on_launched(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.DialogError.quark(),
                                 Gtk.DialogError.DISMISSED):
                self._notify(_('Could not open media'))

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
        if keyval == Gdk.KEY_Escape:
            self.close()
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

    def close(self):
        """Take the viewer off the window and release everything it holds."""
        global _open_viewer
        if self._closed:
            return
        self._closed = True
        if _open_viewer is self:
            _open_viewer = None
        self._cancel_loads()
        self._unload_media()
        self._release_engine()
        if self.window is not None:
            self.window.disconnect(self._fullscreen_handler)
            if self._we_fullscreened:
                self.window.unfullscreen()
                self._we_fullscreened = False
        if self.get_parent() is not None:
            self.unparent()
        self.set_visible(False)

    def force_close(self):
        self.close()


def _opens_fullscreen():
    """Whether a photo should open filling the window rather than as a
    box over it, per the preference."""
    return pages.settings().get_string('media-viewer-mode') == 'fullscreen'


def media_overlay(window):
    """The :class:`Gtk.Overlay` covering *window*'s content, or None when
    the window has none. The shell wraps its content in one so a
    fullscreen attachment can fill the window instead of floating in a
    dialog. Only an Adw window has content to look at, and the shell's
    is an Adw.ApplicationWindow, which is not an Adw.Window."""
    if not isinstance(window, (Adw.Window, Adw.ApplicationWindow)):
        return None
    content = window.get_content()
    return content if isinstance(content, Gtk.Overlay) else None


def _enclosing_dialog(widget):
    """The :class:`Adw.Dialog` in *widget*'s ancestry, or None."""
    node = widget
    while node is not None:
        if isinstance(node, Adw.Dialog):
            return node
        node = node.get_parent()
    return None


def present_media(parent_widget, attachments, index=0):
    """Open the media viewer on *attachments* (Mastodon media_attachments
    dicts) at *index* as an overlay of the window of *parent_widget*;
    returns the viewer. A dialog holds the widget it was opened from
    above that overlay, so it closes instead of hiding the image."""
    global _open_viewer
    if _open_viewer is not None:
        _open_viewer.close()
    dialog = _enclosing_dialog(parent_widget)
    if dialog is not None:
        window = pages.window_of(parent_widget)
        if window is not None:
            parent_widget = window
        dialog.close()
    viewer = MediaViewer(parent_widget, attachments, index)
    window = viewer.window
    overlay = media_overlay(window)
    if overlay is None:
        # A window without one (a test's, say): give the viewer a window of
        # its own so it is still on screen and still usable.
        host = Adw.Window()
        host.set_content(viewer)
        host.present()
        return viewer
    overlay.add_overlay(viewer)
    viewer.set_visible(True)
    viewer._sync_viewer_to_window(window)
    viewer.grab_focus()
    _open_viewer = viewer
    return viewer
