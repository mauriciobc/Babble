# SPDX-License-Identifier: GPL-3.0-or-later

"""Getting media out of the viewer: copy, save, open with another app,
open in the browser and drag and drop.

:class:`MediaExport` is a mixin of :class:`mediaviewer.MediaViewer` (it
needs the viewer as ``self``: the current attachment, the toast overlay,
the progress bar and the downloads' cancellable). The module-level helpers
(drag content providers, drag icons, the Save As file name and the Open
With cache pruning) build no viewer and are also used by the timeline's
media boxes and status rows. :func:`download` is the libsoup file
download the save and open-with paths use.
"""

import os
import time
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk,
)

from . import imagecache  # noqa: E402
from . import pages  # noqa: E402
from .remotemedia import media_url  # noqa: E402

# Open With copies older than this are deleted before the next one is
# downloaded; the app they were handed to has long since read them.
OPEN_WITH_MAX_AGE = 24 * 60 * 60

CHUNK_SIZE = 64 * 1024


def _http_session():
    """The session every media request shares, with the app's timeout."""
    return imagecache.session()


def _check_status(message):
    """Raise a GLib.Error when *message* ended in a non-2xx status."""
    status = message.get_status()
    if status < 200 or status >= 300:
        raise GLib.Error.new_literal(
            Gio.io_error_quark(),
            _('The server answered with HTTP {status}').format(
                status=status),
            Gio.IOErrorEnum.FAILED)


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
        self.message = imagecache.get_message('GET', self.url)
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
        url = (attachment.get('remote_url') or attachment.get('url')
               or getattr(self, 'status_url', None))
        if not url:
            return False
        from .textemoji import launch_uri
        launch_uri(self.picture, url)
        return True

    def save_as(self):
        attachment = self.current()
        url, is_remote = media_url(attachment)
        if not url:
            return False
        self._gate_remote('save', attachment, url, is_remote)
        return True

    def _save_dialog(self, attachment, url):
        dialog = Gtk.FileDialog(
            title=_('Save Media'), accept_label=_('_Save'),
            initial_name=suggested_filename(attachment))
        dialog.save(self.window, None, self._on_save_chosen, url)

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
        url, is_remote = media_url(attachment)
        if not url:
            return False
        self._gate_remote('open-with', attachment, url, is_remote)
        return True

    def _open_with_download(self, attachment, url):
        directory = os.path.join(GLib.get_user_cache_dir(), 'babble',
                                 'open-with')
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            self._notify(_('Could not open media'))
            return
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
