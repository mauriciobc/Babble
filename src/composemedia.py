# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer media: the :class:`Attachment` model (upload through
``POST /api/v2/media``, processing poll, ``PUT /media/:id`` for
description and focal point), the focal point picker and the alt text
editor dialog, and the attachment row shown under the text box."""

import mimetypes

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .emojichooser import load_texture  # noqa: E402

UPLOADING = 'uploading'
PROCESSING = 'processing'
SYNCING = 'syncing'
READY = 'ready'
ERROR = 'error'

POLL_INTERVAL_MS = 1500
POLL_ATTEMPTS = 80
DEFAULT_DESCRIPTION_LIMIT = 1500


def guess_mime(filename, data=None):
    mime, _encoding = mimetypes.guess_type(filename)
    if mime:
        return mime
    guessed, _uncertain = Gio.content_type_guess(filename, data)
    return guessed or 'application/octet-stream'


def kind_of(mime):
    major = (mime or '').split('/')[0]
    return major if major in ('image', 'video', 'audio') else 'unknown'


def format_size(size):
    return GLib.format_size(size)


class Attachment(GObject.Object):
    """One media item of the post being composed.

    New files upload as soon as they are added. Items that came from a
    status being edited are ``attached``: the server only accepts their
    description and focus changes through ``media_attributes`` of the
    status update, so nothing is sent for them until the post is saved."""

    __gtype_name__ = 'BabbleAttachment'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, filename='', mime='', data=None, texture=None):
        super().__init__()
        self.filename = filename
        self.mime = mime
        self.data = data
        self.texture = texture
        self.id = None
        self.kind = kind_of(mime)
        self.description = ''
        self.focus = None
        self.url = None
        self.preview_url = None
        self.attached = False
        self.state = UPLOADING
        self.error = ''
        self.synced = ('', None)
        self.cancellable = None
        self._poll_source = 0
        self._poll_count = 0
        self._removed = False

    # Construction

    @classmethod
    def from_remote(cls, media, attached=True):
        """From a Mastodon MediaAttachment dictionary."""
        kind = media.get('type') or 'unknown'
        item = cls(filename=GLib.path_get_basename(media.get('url') or '')
                   or _('Attachment'),
                   mime={'image': 'image/*', 'video': 'video/*',
                         'gifv': 'video/*', 'audio': 'audio/*'}.get(kind, ''))
        item.kind = {'gifv': 'video'}.get(kind, kind)
        item.id = str(media['id'])
        item.description = media.get('description') or ''
        focus = ((media.get('meta') or {}).get('focus') or {})
        if 'x' in focus and 'y' in focus:
            item.focus = (float(focus['x']), float(focus['y']))
        item.url = media.get('url')
        item.preview_url = media.get('preview_url') or media.get('url')
        item.attached = attached
        item.state = READY
        item.synced = (item.description, item.focus)
        return item

    def to_dict(self):
        return {'id': self.id, 'type': self.kind, 'description':
                self.description, 'focus': list(self.focus)
                if self.focus else None, 'url': self.url,
                'preview_url': self.preview_url, 'attached': self.attached,
                'filename': self.filename}

    @classmethod
    def from_dict(cls, data):
        item = cls.from_remote({
            'id': data['id'], 'type': data.get('type'),
            'description': data.get('description'), 'url': data.get('url'),
            'preview_url': data.get('preview_url')}, attached=False)
        item.filename = data.get('filename') or item.filename
        if data.get('focus'):
            item.focus = tuple(data['focus'])
        item.attached = bool(data.get('attached'))
        item.synced = (item.description, item.focus)
        return item

    # State

    @property
    def busy(self):
        return self.state in (UPLOADING, PROCESSING, SYNCING)

    @property
    def ready(self):
        return self.state == READY

    @property
    def dirty(self):
        return (self.description, self.focus) != self.synced

    def changed(self):
        self.emit('changed')

    def set_state(self, state, error=''):
        self.state = state
        self.error = error
        self.changed()

    def fail(self, message):
        self.set_state(ERROR, message)

    def dispose_upload(self):
        """The row was removed: stop every network job of this item."""
        self._removed = True
        if self.cancellable is not None:
            self.cancellable.cancel()
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    # Server calls

    def retry(self, client):
        if self.id is None:
            self.upload(client)
        else:
            self.sync(client)

    def upload(self, client):
        if self.data is None:
            self.fail(_('The file is no longer available'))
            return
        self.cancellable = Gio.Cancellable()
        self.set_state(UPLOADING)
        fields = {}
        if self.description:
            fields['description'] = self.description
        if self.focus:
            fields['focus'] = '%.2f,%.2f' % self.focus
        sent = (self.description, self.focus)

        def finished(_client, result, _data=None):
            try:
                media = client.request_finish(result)
            except GLib.Error as error:
                if not self._removed:
                    self.fail(error.message)
                return
            if self._removed:
                return
            self.id = str(media['id'])
            self.synced = sent
            self._apply_media(media)
            if media.get('url'):
                self._became_ready(client)
            else:
                self._poll_count = 0
                self.set_state(PROCESSING)
                self._schedule_poll(client)

        client.multipart_async(
            'POST', '/api/v2/media', fields=fields,
            files={'file': (self.data, self.filename or 'file', self.mime)},
            callback=finished, cancellable=self.cancellable)

    def _apply_media(self, media):
        self.url = media.get('url') or self.url
        self.preview_url = media.get('preview_url') or self.preview_url
        if media.get('type'):
            self.kind = {'gifv': 'video'}.get(media['type'], media['type'])

    def _schedule_poll(self, client):
        self._poll_source = GLib.timeout_add(
            POLL_INTERVAL_MS, self._poll, client)

    def _poll(self, client):
        self._poll_source = 0
        if self._removed:
            return GLib.SOURCE_REMOVE
        self._poll_count += 1

        def finished(_client, result, _data=None):
            if self._removed:
                return
            try:
                media = client.request_finish(result)
            except GLib.Error as error:
                self.fail(error.message)
                return
            self._apply_media(media)
            if media.get('url'):
                self._became_ready(client)
            elif self._poll_count >= POLL_ATTEMPTS:
                self.fail(_('The server took too long to process the file'))
            else:
                self._schedule_poll(client)

        client.request_async('GET', '/media/%s' % self.id,
                             callback=finished)
        return GLib.SOURCE_REMOVE

    def _became_ready(self, client):
        self.set_state(READY)
        if self.texture is None and self.preview_url:
            load_texture(self.preview_url, self._on_preview_texture)
        if self.dirty and not self.attached:
            self.sync(client)

    def _on_preview_texture(self, texture):
        if self.texture is None:
            self.texture = texture
            self.changed()

    def sync(self, client, done=None):
        """``PUT /media/:id`` with the current description and focus;
        a no-op for items attached to a status (see class docstring)
        and for items still uploading (the upload finish re-checks)."""
        if self.id is None or self.attached or self.busy:
            if done:
                done()
            return
        if not self.dirty:
            if done:
                done()
            return
        sent = (self.description, self.focus)
        body = {'description': self.description}
        if self.focus:
            body['focus'] = '%.2f,%.2f' % self.focus
        self.set_state(SYNCING)

        def finished(_client, result, _data=None):
            if self._removed:
                return
            try:
                client.request_finish(result)
            except GLib.Error as error:
                self.fail(error.message)
                return
            self.synced = sent
            self.set_state(READY)
            if self.dirty:
                self.sync(client, done)
            elif done:
                done()

        client.request_async('PUT', '/media/%s' % self.id, json_body=body,
                             callback=finished)


class FocusPicker(Adw.Bin):
    """Image with a draggable marker choosing the focal point.

    ``focus`` is ``(x, y)`` in -1..1 with the Mastodon orientation:
    (-1, -1) is the bottom left corner, (1, 1) the top right one."""

    __gtype_name__ = 'BabbleFocusPicker'
    __gsignals__ = {'focus-changed': (GObject.SignalFlags.RUN_FIRST, None,
                                      ())}
    MARKER = 28

    def __init__(self, paintable, focus=None, **kwargs):
        super().__init__(**kwargs)
        self.focus = focus or (0.0, 0.0)
        self.picture = Gtk.Picture(paintable=paintable, can_shrink=True,
                                   content_fit=Gtk.ContentFit.FILL)
        self.picture.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        self.marker = Gtk.Box(width_request=self.MARKER,
                              height_request=self.MARKER,
                              can_target=False)
        self.marker.add_css_class('focus-marker')
        self.overlay = Gtk.Overlay(child=self.picture)
        self.overlay.add_overlay(self.marker)
        self.overlay.set_clip_overlay(self.marker, True)
        self.overlay.connect('get-child-position', self._on_child_position)
        ratio = paintable.get_intrinsic_aspect_ratio() or 1.0
        self.frame = Gtk.AspectFrame(ratio=ratio, obey_child=False,
                                     xalign=0.5, yalign=0.5,
                                     child=self.overlay)
        self.set_child(self.frame)
        self._drag_origin = (0.0, 0.0)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect('drag-begin', self._on_drag_begin)
        drag.connect('drag-update', self._on_drag_update)
        self.overlay.add_controller(drag)
        self.overlay.set_focusable(True)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.overlay.add_controller(keys)
        self.overlay.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_('Focal Point'),
             _('Use the arrow keys to move the focal point')])

    def _on_child_position(self, _overlay, widget, _rect=None):
        if widget is not self.marker:
            return False
        width = self.overlay.get_width()
        height = self.overlay.get_height()
        x, y = self.focus
        rect = Gdk.Rectangle()
        rect.width = rect.height = self.MARKER
        rect.x = int(width * (x + 1) / 2 - self.MARKER / 2)
        rect.y = int(height * (1 - (y + 1) / 2) - self.MARKER / 2)
        return True, rect

    def _set_from_pointer(self, px, py):
        width = max(self.overlay.get_width(), 1)
        height = max(self.overlay.get_height(), 1)
        px = min(max(px, 0.0), width)
        py = min(max(py, 0.0), height)
        self.set_focus((2 * px / width - 1, -(2 * py / height - 1)))

    def _on_drag_begin(self, _gesture, x, y):
        self._drag_origin = (x, y)
        self.overlay.grab_focus()
        self._set_from_pointer(x, y)

    def _on_drag_update(self, _gesture, dx, dy):
        self._set_from_pointer(self._drag_origin[0] + dx,
                               self._drag_origin[1] + dy)

    def _on_key(self, _controller, keyval, _keycode, _state):
        step = 0.1
        x, y = self.focus
        if keyval == Gdk.KEY_Left:
            x -= step
        elif keyval == Gdk.KEY_Right:
            x += step
        elif keyval == Gdk.KEY_Up:
            y += step
        elif keyval == Gdk.KEY_Down:
            y -= step
        else:
            return False
        self.set_focus((x, y))
        return True

    def set_focus(self, focus):
        focus = (round(min(max(focus[0], -1.0), 1.0), 2),
                 round(min(max(focus[1], -1.0), 1.0), 2))
        if focus == self.focus:
            return
        self.focus = focus
        self.overlay.queue_allocate()
        self.emit('focus-changed')


class AttachmentEditDialog(Adw.Dialog):
    """Alt text and focal point of one attachment."""

    __gtype_name__ = 'BabbleAttachmentEditDialog'

    def __init__(self, attachment, limit=DEFAULT_DESCRIPTION_LIMIT,
                 on_save=None, **kwargs):
        super().__init__(title=_('Edit Attachment'), content_width=520,
                         **kwargs)
        self.attachment = attachment
        self.limit = limit
        self.on_save = on_save
        self.picker = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_a: self.close())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self.on_save_clicked)
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12)
        self.preview_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    height_request=260, hexpand=True)
        box.append(self.preview_slot)
        self.focus_hint = Gtk.Label(
            label=_('Click or drag the image to choose its focal point; '
                    'it stays visible when the image is cropped'),
            wrap=True, xalign=0, visible=False)
        self.focus_hint.add_css_class('dimmed')
        self.focus_hint.add_css_class('caption')
        box.append(self.focus_hint)

        heading = Gtk.Label(label=_('Description'), xalign=0)
        heading.add_css_class('heading')
        box.append(heading)
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text(attachment.description)
        self.text_view = Gtk.TextView(
            buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
            accepts_tab=False)
        self.text_view.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Description for people who cannot see the media')])
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=110,
            max_content_height=220, propagate_natural_height=True,
            child=self.text_view)
        frame = Gtk.Frame(child=scrolled)
        box.append(frame)
        self.counter = Gtk.Label(xalign=1)
        self.counter.add_css_class('caption')
        self.counter.add_css_class('numeric')
        box.append(self.counter)
        self.buffer.connect('changed', self._update_counter)
        self._update_counter()

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            child=Adw.Clamp(maximum_size=520, child=box))
        toolbar.set_content(scroller)
        self.set_child(toolbar)
        self.set_focus(self.text_view)
        self._show_preview(attachment.texture)
        if attachment.texture is None and attachment.preview_url:
            load_texture(attachment.preview_url, self._show_preview)

    def _show_preview(self, texture):
        if texture is None or self.picker is not None:
            return
        self.picker = FocusPicker(texture, self.attachment.focus)
        self.picker.set_vexpand(True)
        self.preview_slot.append(self.picker)
        self.focus_hint.set_visible(True)

    def description(self):
        return self.buffer.get_text(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter(),
                                    False).strip()

    def _update_counter(self, *_args):
        count = len(self.description())
        self.counter.set_text('%d / %d' % (count, self.limit))
        over = count > self.limit
        self.counter.remove_css_class('error')
        if over:
            self.counter.add_css_class('error')
        self.save_button.set_sensitive(not over)

    def on_save_clicked(self, _button):
        focus = None
        if self.picker is not None and (
                self.picker.focus != (0.0, 0.0)
                or self.attachment.focus is not None):
            focus = self.picker.focus
        if self.on_save is not None:
            self.on_save(self.description(), focus)
        self.close()


class AttachmentRow(Adw.ActionRow):
    """A media attachment: thumbnail, state, ALT button and remove
    button. Activating the row opens the description editor."""

    __gtype_name__ = 'BabbleAttachmentRow'

    def __init__(self, attachment, **kwargs):
        super().__init__(activatable=True, title_lines=1, subtitle_lines=2,
                         **kwargs)
        self.attachment = attachment
        self.thumbnail = Gtk.Picture(content_fit=Gtk.ContentFit.COVER,
                                     width_request=56, height_request=56,
                                     can_shrink=True)
        self.thumbnail.add_css_class('attachment-thumb')
        self.thumbnail.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        self.icon = Gtk.Image(pixel_size=24, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER)
        self.spinner = Adw.Spinner(halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.CENTER, visible=False,
                                   width_request=24, height_request=24)
        overlay = Gtk.Overlay(child=self.thumbnail, valign=Gtk.Align.CENTER,
                              width_request=56, height_request=56)
        overlay.add_overlay(self.icon)
        overlay.add_overlay(self.spinner)
        overlay.add_css_class('card')
        self.add_prefix(overlay)

        self.retry_button = Gtk.Button(icon_name='view-refresh-symbolic',
                                       valign=Gtk.Align.CENTER,
                                       visible=False)
        self.retry_button.add_css_class('flat')
        _label(self.retry_button, _('Try Again'))
        self.add_suffix(self.retry_button)

        self.alt_button = Gtk.Button(label=_('ALT'),
                                     valign=Gtk.Align.CENTER)
        self.alt_button.add_css_class('flat')
        self.alt_button.add_css_class('alt-badge')
        _label(self.alt_button, _('Edit Description and Focal Point'))
        self.add_suffix(self.alt_button)

        self.remove_button = Gtk.Button(icon_name='user-trash-symbolic',
                                        valign=Gtk.Align.CENTER)
        self.remove_button.add_css_class('flat')
        _label(self.remove_button, _('Remove Attachment'))
        self.add_suffix(self.remove_button)

        attachment.connect('changed', self.refresh)
        self.refresh()
        if attachment.texture is None and attachment.preview_url:
            load_texture(attachment.preview_url, self._on_preview)

    def _on_preview(self, texture):
        if self.attachment.texture is None:
            self.attachment.texture = texture
            self.refresh()

    def refresh(self, *_args):
        attachment = self.attachment
        title = attachment.filename or _('Attachment')
        self.set_title(GLib.markup_escape_text(title))
        texture = attachment.texture
        self.thumbnail.set_paintable(texture)
        self.icon.set_visible(texture is None and not attachment.busy)
        self.icon.set_from_icon_name({
            'video': 'video-x-generic-symbolic',
            'audio': 'audio-x-generic-symbolic'}.get(
                attachment.kind, 'image-x-generic-symbolic'))
        self.spinner.set_visible(attachment.busy)
        self.remove_css_class('error')
        has_alt = bool(attachment.description)
        self.alt_button.remove_css_class('warning')
        self.alt_button.remove_css_class('success')
        self.alt_button.add_css_class('success' if has_alt else 'warning')
        self.retry_button.set_visible(
            attachment.state == ERROR
            and (attachment.data is not None or attachment.id is not None))
        if attachment.state == UPLOADING:
            subtitle = _('Uploading…')
        elif attachment.state == PROCESSING:
            subtitle = _('Processing…')
        elif attachment.state == SYNCING:
            subtitle = _('Saving…')
        elif attachment.state == ERROR:
            subtitle = _('Upload failed: %s') % GLib.markup_escape_text(
                attachment.error)
            self.add_css_class('error')
        elif has_alt:
            subtitle = GLib.markup_escape_text(attachment.description)
        else:
            subtitle = _('No description')
        self.set_subtitle(subtitle)
        self.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [attachment.description or _('No description')])


def _label(widget, label):
    widget.set_tooltip_text(label)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
