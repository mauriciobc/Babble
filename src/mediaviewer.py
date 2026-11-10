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

The module is split along three seams: widget construction is the
:class:`mediaviewerchrome.MediaViewerChrome` mixin, copy, save, open and
drag-out are :class:`mediaexport.MediaExport` (with the drag content
helpers other modules import from there), and streaming and download
are :class:`mediaplayback.MediaPlayback`. What stays here is the
viewer's state, navigation, display, zoom, fullscreen, keys, closing and
``present_media``.
"""

import locale

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Gtk,
)

from . import imagecache  # noqa: E402
from . import pages  # noqa: E402
from .mediaexport import MediaExport  # noqa: E402
from .mediaplayback import MediaPlayback  # noqa: E402
from .remotemedia import RemoteGate, media_url  # noqa: E402
from .mediaviewerchrome import (  # noqa: E402
    MediaViewerChrome, _label,
)

# A full-screen attachment at every zoom step, so this keeps far fewer
# than the timeline's media cache -- and must not share it, or opening a
# viewer would evict the timeline's thumbnails.
TEXTURE_CACHE_SIZE = 6

# Free space kept on each side of the viewer while it is a box over the
# window. It fills the window when fullscreen, or when the preference
# asks for that.
VIEWER_MARGIN = 64

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


class MediaViewer(RemoteGate, MediaPlayback, MediaExport, MediaViewerChrome,
                  Adw.Bin):
    """Browsing a list of Mastodon media attachments.

    An overlay of the window, not an :class:`Adw.Dialog`: a dialog floats
    inside the window and can never grow past it, so a fullscreen
    attachment would keep a margin on every side. Here the viewer *is* the
    window's content, and filling it means filling the screen.

    Streaming, GstPlay lifetime and idle inhibition live on the
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
        # Remote URLs the user allowed in this viewer, and the counter
        # that drops an answer to a question about an earlier attachment.
        self._remote_allowed = set()
        self._remote_token = 0
        self._remote_actions = {
            'show': '_show_loaded', 'save': '_save_dialog',
            'open-with': '_open_with_download'}

        self._build()
        if self.window is not None:
            self._fullscreen_handler = pages.connect_weak(
                self.window, 'notify::fullscreened',
                self._on_fullscreen_changed)
        self.show_index(index)


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
        url, is_remote = media_url(attachment)
        kind = attachment.get('type')
        if not url:
            self._show_error(
                _('Preview Not Available'),
                _('This attachment has no address to load.'),
                retry=False, browser=False)
        elif kind == 'image':
            self._gate_remote('show', attachment, url, is_remote)
        elif kind in ('video', 'gifv', 'audio'):
            self._show_stream(attachment, url, is_remote)
        else:
            self._show_error(
                _('Preview Not Available'),
                _('This kind of attachment cannot be shown here.'),
                retry=False, browser=True)

    def _show_loaded(self, attachment, url):
        """The gate let *url* through: fetch and show it."""
        if attachment.get('type') == 'image':
            self._show_image(url)
        else:
            self._start_stream(attachment, url)

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
        self._hide_buffering()
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
