# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer attachments: rows, the tray, paste/drop/screenshot paths.

The composer view owns the chrome (box, buttons, drop target) and a
:class:`ComposeAttachments` object that fills it. UploadRound duck-types
:class:`AttachmentRow` (``path``, ``content_type``, ``uploaded_id``,
``alt_entry``) and never imports this module.
"""

import json
import os
import shutil
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import pages, portals  # noqa: E402

# The fallback, for an instance that advertises no
# statuses.max_media_attachments; Mastodon's own default is 4, and its
# Android client hardcodes this same number while parsing the field and
# never reading it. See ComposeAttachments.limit for the instance-aware
# one.
MAX_ATTACHMENTS = 4
SCHEMA_ID = 'io.github.mauriciobc.Babble'
SCREENSHOT_PORTAL = 'org.freedesktop.portal.Screenshot'

# Pasted images, and screenshots taken from the composer, are written
# under the user cache, not a temporary directory, and are not deleted
# with their row: undoing a removal adds row.path back, and a local
# draft keeps its attachment paths across restarts. Instead, the first
# composer of a session prunes the pasted files older than
# PASTED_MAX_AGE that no saved draft refers to. That runs before any
# draft can be restored into a composer (which takes it off the saved
# list), so a file still attached somewhere is never lost.
PASTED_MAX_AGE = 7 * 24 * 3600
_pasted_pruned = False


def media_type(path):
    """MIME type of the file at *path*, judged from its name and its
    first bytes; None when it cannot be read."""
    try:
        with open(path, 'rb') as f:
            head = f.read(4096)
    except OSError:
        return None
    content_type, _uncertain = Gio.content_type_guess(path, head)
    return Gio.content_type_get_mime_type(content_type)


def is_media(mime):
    """Whether *mime* is something Mastodon takes as an attachment."""
    return bool(mime) and mime.startswith(('image/', 'video/', 'audio/'))


def must_be_alone(mime):
    """Whether *mime* is a video or audio file, which Mastodon only takes
    as the single attachment of a post."""
    return bool(mime) and mime.startswith(('video/', 'audio/'))


def pasted_dir():
    """Where images pasted into the composer are kept."""
    return os.path.join(GLib.get_user_cache_dir(), 'babble', 'pasted')


def _has_local_drafts_key():
    """Whether the GSettings schema holds ``local-drafts``."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SCHEMA_ID, True) if source is not None else None
    return schema is not None and schema.has_key('local-drafts')


def _draft_attachment_paths():
    """Attachment paths of the saved local drafts, or None when they
    cannot be known (no schema installed)."""
    if not _has_local_drafts_key():
        return None
    paths = set()
    for raw in pages.settings().get_strv('local-drafts'):
        try:
            draft = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(draft, dict):
            continue
        for attachment in draft.get('attachments') or []:
            if isinstance(attachment, dict) \
                    and isinstance(attachment.get('path'), str):
                paths.add(os.path.normpath(attachment['path']))
    return paths


def prune_pasted_images():
    """Delete the pasted images older than PASTED_MAX_AGE that no saved
    draft refers to. Best effort: whatever cannot be read or removed
    waits for the next session."""
    directory = pasted_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    referenced = _draft_attachment_paths()
    if referenced is None:
        return
    cutoff = time.time() - PASTED_MAX_AGE
    for name in names:
        path = os.path.normpath(os.path.join(directory, name))
        try:
            if path not in referenced and os.stat(path).st_mtime < cutoff:
                os.unlink(path)
        except OSError:
            continue


def _prune_pasted_once():
    """Prune the pasted images when the first composer of the session is
    built, never again: see the note above PASTED_MAX_AGE."""
    global _pasted_pruned
    if not _pasted_pruned:
        _pasted_pruned = True
        prune_pasted_images()


def _claim_pasted_path(stem, extension):
    """Create an empty file in :func:`pasted_dir` named *stem* and
    *extension*, numbered when that name is taken, and return its path.
    Raises OSError."""
    directory = pasted_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    stem = stem.replace(os.sep, '-')
    number = 1
    while True:
        name = stem + (extension if number == 1
                       else ' %d%s' % (number, extension))
        path = os.path.join(directory, name)
        try:
            # Claimed exclusively, so two composers saving at once never
            # write the same file.
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600))
            return path
        except FileExistsError:
            number += 1


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def save_pasted_texture(texture):
    """Write *texture* as a PNG in :func:`pasted_dir`, under a name no
    other file there has, and return its path. Raises OSError."""
    # Translators: file name, without its extension, of an image pasted
    # into the composer. It is shown on the attachment.
    path = _claim_pasted_path(_('Pasted image'), '.png')
    if not texture.save_to_png(path):
        _unlink_quietly(path)
        raise OSError(_('The image could not be saved'))
    return path


def save_screenshot(source):
    """Copy the screenshot at path *source* into :func:`pasted_dir`,
    under its own name when no other file there has it, and return the
    copy's path. Raises OSError.

    The portal's file is the user's (in GNOME, in Pictures/Screenshots)
    and may be moved or deleted at any time, while a saved draft keeps
    its attachments by path; the copy lives as long as a draft needs it,
    under the pruning rules of pasted images."""
    stem, extension = os.path.splitext(os.path.basename(source))
    # Translators: file name, without its extension, of a screenshot
    # taken from the composer. It is shown on the attachment.
    path = _claim_pasted_path(stem or _('Screenshot'), extension or '.png')
    try:
        shutil.copyfile(source, path)
    except OSError:
        _unlink_quietly(path)
        raise
    return path


class AttachmentRow(Gtk.ListBox):
    """Media attachment: preview row with remove button and a
    description (alt text) entry, as one boxed list."""

    __gtype_name__ = 'BabbleAttachmentRow'

    def __init__(self, path, media=None, **kwargs):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, **kwargs)
        self.add_css_class('boxed-list')
        self.path = path
        # *media* is an attachment the server already holds (an edited
        # post's): it has an id and nothing to upload.
        self.media = media
        self.existing = media is not None
        self.uploaded_id = str(media['id']) if self.existing else None

        if self.existing:
            kind = media.get('type') or 'image'
            self.content_type = 'audio/' if kind == 'audio' else kind + '/'
        else:
            self.content_type = media_type(path) or 'application/octet-stream'
        # Audio has no picture to decode: its icon, without reading the
        # whole file in an attempt bound to fail.
        texture = None
        if self.existing:
            preview = media.get('preview_url') or media.get('url')
            if preview and not self.content_type.startswith('audio/'):
                texture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
                from .mediacache import _TextureCache
                _TextureCache.load(preview, texture, cover=(48, 48))
        elif not self.content_type.startswith('audio/'):
            try:
                texture = Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                pass
        if isinstance(texture, Gtk.Picture):
            self.thumbnail = texture
        elif texture is not None:
            self.thumbnail = Gtk.Picture(paintable=texture,
                                         content_fit=Gtk.ContentFit.COVER)
        else:
            self.thumbnail = Gtk.Image.new_from_icon_name(
                'audio-x-generic-symbolic'
                if self.content_type.startswith('audio/')
                else 'image-x-generic-symbolic')
        self.thumbnail.set_size_request(48, 48)
        self.thumbnail.set_valign(Gtk.Align.CENTER)
        self.thumbnail.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)

        self.remove_button = Gtk.Button(icon_name='user-trash-symbolic',
                                        valign=Gtk.Align.CENTER)
        self.remove_button.add_css_class('flat')
        pages.set_accessible_label(self.remove_button, _('Remove Attachment'))

        header = Adw.ActionRow(title=GLib.markup_escape_text(
            GLib.path_get_basename(path) if not self.existing
            else (media.get('type') or 'image').capitalize()))
        header.set_title_lines(1)
        header.add_prefix(self.thumbnail)
        header.add_suffix(self.remove_button)
        self.append(header)

        self.alt_entry = Adw.EntryRow(title=_('Description'))
        if self.existing and media.get('description'):
            self.alt_entry.set_text(media['description'])
        self.append(self.alt_entry)

    def limit_description(self, limit):
        """Cap the alt text at *limit* characters, the instance's
        ``description_limit``."""
        Gtk.Editable.get_delegate(self.alt_entry).set_max_length(limit)


class ComposeAttachments(GObject.Object):
    """Attachment tray for one composer: rows, the file picker, drop,
    paste-media and screenshot paths.

    A GObject so :func:`pages.connect_weak` / :func:`pages.weak_method`
    can hold it without a C-level cycle through the chrome it wires.
    The view builds the box and the buttons, then calls :meth:`bind`.
    ``is_posting`` / ``is_frozen`` are callables that read the view's
    posting and scheduled-replacement state; this object never imports
    the view.
    """

    def __init__(self, client, box, *, on_changed, on_toast, on_error,
                 is_posting, is_frozen, on_add_toast, on_paste_text):
        super().__init__()
        self.client = client
        self.box = box
        self._on_changed = on_changed
        self._on_toast = on_toast
        self._on_error = on_error
        self._is_posting = is_posting
        self._is_frozen = is_frozen
        self._on_add_toast = on_add_toast
        self._on_paste_text = on_paste_text
        self.attach_button = None
        self.screenshot_button = None
        self.drop_target = None
        self.text_view = None

    def bind(self, *, attach_button, screenshot_button, drop_target,
             text_view):
        """Wire the view's chrome to this tray."""
        self.attach_button = attach_button
        self.screenshot_button = screenshot_button
        self.drop_target = drop_target
        self.text_view = text_view
        pages.connect_weak(attach_button, 'clicked', self.on_attach_clicked)
        pages.connect_weak(screenshot_button, 'clicked',
                           self.on_screenshot_clicked)
        pages.connect_weak(drop_target, 'drop', self.on_drop)
        portals.portal_version(
            SCREENSHOT_PORTAL,
            pages.weak_method(self._on_screenshot_portal))

    def rows(self):
        found = []
        child = self.box.get_first_child()
        while child is not None:
            if isinstance(child, AttachmentRow):
                found.append(child)
            child = child.get_next_sibling()
        return found

    def clear(self):
        for row in self.rows():
            self.box.remove(row)

    def limit(self):
        """How many attachments a post may carry here.

        Read from the instance rather than assumed, so a server that
        raised or lowered the limit is obeyed instead of refused at
        four. Falls back to :data:`MAX_ATTACHMENTS` for a client that
        cannot answer, which is only the test stubs.
        """
        reader = getattr(self.client, 'max_media_attachments', None)
        return reader() if reader is not None else MAX_ATTACHMENTS

    def media_filter(self):
        """The file chooser's media filter, narrowed to what this server
        takes when it says what it takes.

        An instance that advertises supported_mime_types is followed,
        because the chooser is the only place where refusing a file
        costs the reader nothing: the alternative is picking it, having
        it turned away, and losing the selection. Nothing is advertised,
        or only the useless application/octet-stream, and the three
        broad types stand.
        """
        types = self.client.supported_media_types() \
            if hasattr(self.client, 'supported_media_types') else None
        media_filter = Gtk.FileFilter(
            name=_('Media accepted by this server') if types
            else _('Images, Videos and Audio'))
        for mime in types or ('image/*', 'video/*', 'audio/*'):
            media_filter.add_mime_type(mime)
        return media_filter

    def set_attach_sensitive(self, sensitive):
        """Attaching media and taking a screenshot go together."""
        if self.attach_button is not None:
            self.attach_button.set_sensitive(sensitive)
        if self.screenshot_button is not None:
            self.screenshot_button.set_sensitive(sensitive)

    def set_locked(self, locked):
        """Freeze or thaw the tray while a post is in flight or a
        scheduled replacement is stuck."""
        self.set_attach_sensitive(not locked and self._can_attach_more())
        if self.drop_target is not None:
            self.drop_target.set_actions(
                Gdk.DragAction.NONE if locked else Gdk.DragAction.COPY)
        for row in self.rows():
            row.remove_button.set_sensitive(not locked)

    def add_path(self, path, alt=None, position=-1):
        if self._is_frozen():
            return None
        if not path or not self._can_attach_more():
            return None
        if self.rows() and must_be_alone(media_type(path)):
            return None
        row = AttachmentRow(path)
        reader = getattr(self.client, 'media_description_limit', None)
        if reader is not None:
            row.limit_description(reader())
        # The row is looked up from the emitter: passing it as user data
        # would pin it through its own button and entry.
        pages.connect_weak(row.remove_button, 'clicked',
                           self._on_row_remove_clicked)
        pages.connect_weak(row.alt_entry, 'changed', self._on_row_alt_changed)
        if alt:
            row.alt_entry.set_text(alt)
        rows = self.rows()
        sibling = rows[min(position, len(rows)) - 1] if position > 0 \
            and rows else None
        if position == 0 or sibling is not None:
            self.box.insert_child_after(row, sibling)
        else:
            self.box.append(row)
        self.set_attach_sensitive(self._can_attach_more())
        self._on_changed()
        return row

    def add_existing(self, media, position=-1):
        """Show an attachment the server already holds, so its
        description can be edited or the attachment removed."""
        if self._is_frozen() or not media.get('id'):
            return None
        row = AttachmentRow(None, media=media)
        reader = getattr(self.client, 'media_description_limit', None)
        if reader is not None:
            row.limit_description(reader())
        pages.connect_weak(row.remove_button, 'clicked',
                           self._on_row_remove_clicked)
        pages.connect_weak(row.alt_entry, 'changed', self._on_row_alt_changed)
        rows = self.rows()
        sibling = rows[min(position, len(rows)) - 1] if position > 0 \
            and rows else None
        if position == 0 or sibling is not None:
            self.box.insert_child_after(row, sibling)
        else:
            self.box.append(row)
        self.set_attach_sensitive(self._can_attach_more())
        self._on_changed()
        return row

    def add_files(self, files):
        """Attach the images, videos and audio among *files* (Gio.File
        objects or paths), as a drop, a paste of copied files or files
        opened from outside do, up to the attachment limit; a video or an
        audio file goes alone, as Mastodon takes it. A toast tells what
        had to be left out. Nothing is added while posting, as the round
        in flight would not send it: a toast says so. Returns the rows
        added."""
        files = list(files)
        if self._is_frozen():
            return []
        if self._is_posting():
            if files:
                self._toast_busy_posting()
            return []
        added = []
        rejected = over_limit = lone = False
        for item in files:
            path = item.get_path() if isinstance(item, Gio.File) else item
            mime = media_type(path) if path else None
            if not is_media(mime):
                rejected = True
            elif self._has_lone_media() or (
                    must_be_alone(mime) and self.rows()):
                lone = True
            elif len(self.rows()) >= self.limit():
                over_limit = True
            else:
                added.append(self.add_path(path))
        if rejected:
            self._on_toast(_('Only images, videos and audio can be attached'))
        if lone:
            self._toast_lone_media()
        if over_limit:
            self._toast_attachment_limit()
        return added

    def try_clipboard_media(self, text_view) -> bool:
        """Start attaching clipboard files or a bare image when the
        clipboard holds them. True when the paste was consumed (frozen
        composer, or media started), so the view must not arm the quote
        paste path."""
        if self._is_frozen():
            return True
        return self._read_clipboard_media(files=True)

    def on_attach_clicked(self, _button):
        dialog = Gtk.FileDialog(title=_('Attach Media'),
                                default_filter=self.media_filter())
        dialog.open_multiple(self.box.get_root(), None, self.on_files_selected)

    def on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(),
                             Gtk.DialogError.DISMISSED):
                self._on_error(_('Could not open the file picker'),
                               e.message)
            return
        self.add_files(
            [files.get_item(index) for index in range(files.get_n_items())])

    def on_screenshot_clicked(self, _button):
        """Take a screenshot through the portal, which lets the user
        pick what to capture, and attach it."""
        portals.session_bus(pages.weak_method(self._on_screenshot_bus))

    def _on_screenshot_portal(self, version):
        if self.screenshot_button is not None:
            self.screenshot_button.set_visible(version > 0)

    def _on_screenshot_bus(self, bus):
        if bus is None:
            self._on_toast(_('Could not take a screenshot'))
            return
        portals.portal_request(
            bus, SCREENSHOT_PORTAL, 'Screenshot',
            '(sa{sv})',
            ('',), {
                'interactive': GLib.Variant('b', True),
                'modal': GLib.Variant('b', True),
            }, pages.weak_method(self._on_screenshot_response))

    def _on_screenshot_response(self, code, results):
        if code == 1:  # cancelled by the user
            return
        uri = results.get('uri') if code == 0 else None
        source = Gio.File.new_for_uri(uri).get_path() if uri else None
        if not source:
            self._on_toast(_('Could not take a screenshot'))
            return
        # Checked before copying, so a refused screenshot leaves no file.
        if not self._room_for_attachment():
            return
        try:
            path = save_screenshot(source)
        except OSError as e:
            self._on_error(_('Could not take a screenshot'),
                           e.strerror or str(e))
            return
        self.add_files([Gio.File.new_for_path(path)])

    def _room_for_attachment(self):
        """Whether one more attachment can be added now; a toast says
        why not."""
        if self._is_frozen():
            return False
        if self._is_posting():
            self._toast_busy_posting()
            return False
        if len(self.rows()) >= self.limit():
            self._toast_attachment_limit()
            return False
        if self._has_lone_media():
            self._toast_lone_media()
            return False
        return True

    def _has_lone_media(self):
        """Whether a video or audio file is attached: nothing can join it."""
        return any(must_be_alone(row.content_type) for row in self.rows())

    def _can_attach_more(self):
        return len(self.rows()) < self.limit() and not self._has_lone_media()

    def _toast_busy_posting(self):
        self._on_toast(_('Wait for the post to be sent to attach files'))

    def _toast_lone_media(self):
        self._on_toast(_('A video or audio file must be the only attachment'))

    def _toast_attachment_limit(self):
        # Translators: %d is the most attachments a post can have.
        limit = self.limit()
        text = ngettext('A post can have at most %d attachment',
                        'A post can have at most %d attachments',
                        limit)
        self._on_toast(text % limit)

    def on_drop(self, _target, value, _x, _y):
        # The value only lives through this emission: take the files out
        # of it now.
        files = value.get_files() if isinstance(value, Gdk.FileList) \
            else [value]
        return bool(self.add_files(files))

    def _read_clipboard_media(self, files):
        """Start reading the clipboard's files (when *files*) or image to
        attach them; False when it holds neither, so it pastes as text."""
        clipboard = self.text_view.get_clipboard()
        formats = clipboard.get_formats().union_deserialize_gtypes()
        if files and formats.contain_gtype(Gdk.FileList):
            clipboard.read_value_async(
                Gdk.FileList, GLib.PRIORITY_DEFAULT, None,
                pages.weak_method(self._on_paste_files))
            return True
        # A clipboard set in this process holds the texture's own
        # subclass, so contain_gtype(Gdk.Texture) alone misses it.
        has_image = any(GObject.type_is_a(gtype, Gdk.Texture)
                        for gtype in formats.get_gtypes())
        if has_image and not formats.contain_gtype(GObject.TYPE_STRING):
            clipboard.read_texture_async(
                None, pages.weak_method(self._on_paste_texture))
            return True
        return False

    def _on_paste_files(self, clipboard, result):
        try:
            value = clipboard.read_value_finish(result)
        except GLib.Error as e:
            self._on_error(_('Could not paste the files'), e.message)
            return
        # The value only lives through this callback.
        local = [f for f in value.get_files() if f.get_path()]
        if local:
            self.add_files(local)
        elif not self._read_clipboard_media(files=False):
            # Remote locations only, such as a copied link: their text.
            self._on_paste_text()

    def _on_paste_texture(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            self._on_error(_('Could not paste the image'), e.message)
            return
        if texture is None:
            return
        # Checked before saving, so a refused paste leaves no file.
        if not self._room_for_attachment():
            return
        try:
            path = save_pasted_texture(texture)
        except OSError as e:
            self._on_error(_('Could not paste the image'),
                           e.strerror or str(e))
            return
        self.add_path(path)

    def on_remove(self, _button, row):
        position = self.rows().index(row)
        alt = row.alt_entry.get_text()
        self.box.remove(row)
        self.set_attach_sensitive(not self._is_posting()
                                  and self._can_attach_more())
        self._on_changed()
        toast = Adw.Toast(title=_('Attachment removed'),
                          button_label=_('_Undo'),
                          priority=Adw.ToastPriority.HIGH)
        if row.existing:
            media = dict(row.media, description=alt)
            toast.connect('button-clicked', lambda _t: self.add_existing(
                media, position=position))
        else:
            toast.connect('button-clicked', lambda _t: self.add_path(
                row.path, alt=alt, position=position))
        self._on_add_toast(toast)

    def _on_row_remove_clicked(self, button):
        self.on_remove(button, button.get_ancestor(AttachmentRow))

    def _on_row_alt_changed(self, _entry):
        self._on_changed()
