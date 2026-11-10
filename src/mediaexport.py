# SPDX-License-Identifier: GPL-3.0-or-later

"""Getting media out of the viewer: copy, save, open with another app,
open in the browser and drag and drop.

:class:`MediaExport` is a mixin of :class:`mediaviewer.MediaViewer` (it
needs the viewer as ``self``: the current attachment, the toast overlay,
the progress bar and the downloads' cancellable). The module-level helpers
(drag content providers, drag icons, the Save As file name and the Open
With cache pruning) build no viewer and are also used by the timeline's
media boxes and status rows.
"""

import os
import time
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk,
)

from . import pages  # noqa: E402
from .mediaplayback import download  # noqa: E402

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


class MediaExport:
    """Copy, save, open and drag actions of the media viewer."""

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
        # A copy from Save As or Open With if there is one, else the URL.
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
