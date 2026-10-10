# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer dialog: new posts with attachments, visibility, content
warning, poll and language selection. Reply prefill goes through
:meth:`ComposeDialog.start_reply`; the previous draft is preserved across
the reply round trip and closing the dialog keeps the draft."""

import mimetypes
import os
import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error  # noqa: E402

DEFAULT_CHAR_LIMIT = 500
URL_PLACEHOLDER_CHARS = 23
MAX_ATTACHMENTS = 4
MAX_POLL_CHOICES = 4
SCHEMA_ID = 'io.github.mauriciobc.Babble'

# An instance may take a few seconds to transcode an upload. Poll
# GET /media/:id until it is ready, but never longer than this: a post
# blocked on a stuck upload is worse than a clear failure.
PROCESSING_INTERVAL_MS = 1500
PROCESSING_ATTEMPTS = 80

# Mastodon's Extractor: URLs (scheme optional) and remote mentions.
_URL_RE = re.compile(
    r'(?<![\w@/])(?:https?://)?'
    r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}'
    r'(?:/[^\s<>"\u201c\u201d]*)?', re.IGNORECASE)

# UAX #29 grapheme clusters, close enough to Ruby's
# each_grapheme_cluster for the sequences Mastodon counts: CRLF, flag
# pairs, base + combining marks/emoji modifiers, and ZWJ sequences.
_GRAPHEME_RE = re.compile(
    r'\r\n|[\U0001F1E6-\U0001F1FF]{2}|'
    r'[^\u200d\ufe0f\u0300-\u036f\U0001F3FB-\U0001F3FF]'
    r'(?:[\u0300-\u036f\ufe0f\U0001F3FB-\U0001F3FF]'
    r'|\u200d[^\u200d\ufe0f]|\u200d)*')

VISIBILITIES = [
    ('public', _('Public')),
    ('unlisted', _('Unlisted')),
    ('private', _('Followers only')),
    ('direct', _('Direct')),
]

LANGUAGES = [
    ('en', _('English')),
    ('es', _('Español')),
    ('fr', _('Français')),
    ('de', _('Deutsch')),
    ('it', _('Italiano')),
    ('pt', _('Português')),
    ('ru', _('Русский')),
    ('zh', _('中文')),
]

POLL_DURATIONS = [
    (1800, _('30 minutes')),
    (3600, _('1 hour')),
    (86400, _('1 day')),
    (604800, _('7 days')),
]


def default_language():
    code = os.environ.get('LANG', 'en_US')[:2]
    if code not in [lang[0] for lang in LANGUAGES]:
        code = 'en'
    return code


def default_visibility():
    """Visibility preset for a brand new post, read from GSettings."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA_ID, True) is None:
        return 'public'


def countable_length(text, spoiler_text='', url_chars=URL_PLACEHOLDER_CHARS):
    """Characters Mastodon counts for *text* plus its content warning.

    Mirrors Mastodon's ``StatusLengthValidator``: URLs collapse to
    ``url_chars``, remote mentions to ``@user``, and the total is counted
    in grapheme clusters rather than code points."""
    text = text or ''

    def replace(match):
        value = match.group(0)
        if value.startswith('@'):
            return '@' + value[1:].split('@', 1)[0]
        return 'x' * url_chars

    collapsed = re.sub(
        r'@[a-z0-9_]+(?:@[a-z0-9.-]+\.[a-z]{2,})?',
        replace, text, flags=re.IGNORECASE)
    collapsed = _URL_RE.sub(replace, collapsed)
    return len(_GRAPHEME_RE.findall((spoiler_text or '') + collapsed))


def _make_dropdown(options, label):
    dropdown = Gtk.DropDown(model=Gtk.StringList.new(
        [text for _code, text in options]))
    dropdown.add_css_class('flat')
    dropdown.get_first_child().add_css_class('flat')
    dropdown.set_valign(Gtk.Align.CENTER)
    _set_label(dropdown, label)
    return dropdown


def _set_label(widget, label):
    widget.set_tooltip_text(label)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


class AttachmentRow(Gtk.ListBox):
    """Media attachment: preview row with remove button and a
    description (alt text) entry, as one boxed list."""

    __gtype_name__ = 'BabbleAttachmentRow'

    def __init__(self, path, **kwargs):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, **kwargs)
        self.add_css_class('boxed-list')
        self.path = path
        self.uploaded_id = None
        self.description = ''

        try:
            self.thumbnail = Gtk.Picture(
                paintable=Gdk.Texture.new_from_filename(path),
                content_fit=Gtk.ContentFit.COVER)
        except GLib.Error:
            self.thumbnail = Gtk.Image.new_from_icon_name(
                'image-x-generic-symbolic')
        self.thumbnail.set_size_request(48, 48)
        self.thumbnail.set_valign(Gtk.Align.CENTER)
        self.thumbnail.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)

        self.remove_button = Gtk.Button(icon_name='user-trash-symbolic',
                                        valign=Gtk.Align.CENTER)
        self.remove_button.add_css_class('flat')
        _set_label(self.remove_button, _('Remove Attachment'))

        header = Adw.ActionRow(title=GLib.markup_escape_text(
            GLib.path_get_basename(path)))
        header.set_title_lines(1)
        header.add_prefix(self.thumbnail)
        header.add_suffix(self.remove_button)
        self.append(header)

        self.alt_entry = Adw.EntryRow(title=_('Description'))
        self.append(self.alt_entry)

        mimetype, _encoding = mimetypes.guess_type(path)
        self.content_type = mimetype or 'application/octet-stream'


class BabbleComposeView(Adw.Bin):
    """Post composer content: header bar with Post, text area, options
    and toolbar. Emits ``changed`` whenever the draft state changes."""

    __gtype_name__ = 'BabbleComposeView'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.in_reply_to_id = None
        self._draft_stack = []
        self._posting = False
        self._upload_results = []
        self._upload_failures = 0
        self._upload_generation = 0
        self._upload_expected = 0
        self.editing_id = None
        self.quoted_status_id = None
        self.scheduled_id = None
        self.scheduled_at = None
        self.scheduled_on_done = None
        self._pending_media_ids = []
        # One cancellable per posting round: abandoning the dialog or
        # starting a new post stops the uploads still in flight instead
        # of leaving them to finish against a client that has moved on.
        self._upload_cancellable = None
        self._processing_poll = {}

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.build_header_bar())
        self.reply_banner = Adw.Banner(title=_('Replying to a post'),
                                       button_label=_('_Cancel Reply'))
        self.reply_banner.connect('button-clicked', self.on_reply_cancel)
        toolbar_view.add_top_bar(self.reply_banner)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
        content.append(self.build_text_area())
        content.append(self.build_options())
        self.attachments_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                       spacing=12)
        content.append(self.attachments_box)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
            child=Adw.Clamp(maximum_size=600, child=content))
        toolbar_view.set_content(scrolled)
        toolbar_view.add_bottom_bar(self.build_action_bar())
        # Own overlay: the composer lives in a dialog that covers the
        # window's, so feedback raised here must land inside it.
        self.set_child(Adw.ToastOverlay(child=toolbar_view))

        shortcuts = Gtk.ShortcutController(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for accel in ('<Control>Return', '<Control>KP_Enter'):
            shortcuts.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                Gtk.CallbackAction.new(self._on_post_shortcut)))
        self.add_controller(shortcuts)
        self.update_compose_state()

    @property
    def char_limit(self):
        """Post limit of the instance, read live: the composer dialog is
        cached per client, so the value can arrive after it is built."""
        max_characters = getattr(self.client, 'max_characters', None)
        if max_characters is None:
            return DEFAULT_CHAR_LIMIT
        return max_characters() or DEFAULT_CHAR_LIMIT

    def build_header_bar(self):
        header = Adw.HeaderBar()
        self.discard_button = Gtk.Button(icon_name='user-trash-symbolic')
        _set_label(self.discard_button, _('Discard Draft'))
        self.discard_button.connect('clicked', self.on_discard_clicked)
        header.pack_start(self.discard_button)

        self.post_button = Gtk.Button(label=_('_Post'), use_underline=True)
        self.post_button.add_css_class('suggested-action')
        self.post_button.set_tooltip_text(_('Post (Ctrl+Enter)'))
        self.post_button.connect('clicked', self.on_post_clicked)
        header.pack_end(self.post_button)
        self.progress_spinner = Adw.Spinner(visible=False)
        header.pack_end(self.progress_spinner)
        return header

    def build_text_area(self):
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect('changed', self.on_draft_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=12, bottom_margin=12,
                                      left_margin=12, right_margin=12,
                                      accepts_tab=False)
        self.text_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Post Text')])
        self.placeholder = Gtk.Label(label=_('What’s on your mind?'),
                                     halign=Gtk.Align.START,
                                     valign=Gtk.Align.START,
                                     margin_top=12, margin_start=14,
                                     can_target=False)
        self.placeholder.add_css_class('dimmed')
        self.placeholder.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      min_content_height=160,
                                      max_content_height=320,
                                      propagate_natural_height=True,
                                      child=self.text_view)
        overlay = Gtk.Overlay(child=scrolled)
        overlay.add_overlay(self.placeholder)
        self.text_frame = Gtk.Frame(child=overlay)
        return self.text_frame

    def build_options(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.spoiler_entry = Adw.EntryRow(title=_('Content Warning'))
        self.spoiler_entry.connect('changed', self.on_draft_changed)
        self._spoiler_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self._spoiler_box.add_css_class('boxed-list')
        self._spoiler_box.append(self.spoiler_entry)
        box.append(self._spoiler_box)

        self._poll_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     visible=False)
        self._poll_box.add_css_class('boxed-list')
        self.poll_entries = []
        self.poll_expiry = Adw.ComboRow(
            title=_('Duration'),
            model=Gtk.StringList.new([text for _s, text in POLL_DURATIONS]))
        self.poll_expiry.set_selected(2)
        self._poll_box.append(self.poll_expiry)
        self.poll_add = Adw.ButtonRow(title=_('_Add Choice'),
                                      use_underline=True,
                                      start_icon_name='list-add-symbolic')
        self.poll_add.connect('activated', self.on_poll_add_choice)
        self._poll_box.append(self.poll_add)
        for _n in range(2):
            self.on_poll_add_choice(self.poll_add)
        box.append(self._poll_box)
        return box

    def build_action_bar(self):
        bar = Adw.WrapBox(child_spacing=6, line_spacing=6,
                          margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
        tools = Gtk.Box(spacing=6)
        bar.append(tools)

        def icon_button(name, label, toggle=False):
            button = (Gtk.ToggleButton if toggle else Gtk.Button)(
                icon_name=name)
            button.add_css_class('flat')
            _set_label(button, label)
            tools.append(button)
            return button

        self.attach_button = icon_button('mail-attachment-symbolic',
                                         _('Attach Media'))
        self.attach_button.connect('clicked', self.on_attach_clicked)
        self.cw_toggle = icon_button('dialog-warning-symbolic',
                                     _('Content Warning'), toggle=True)
        self.cw_toggle.connect('toggled', self.on_cw_toggled)
        self.poll_toggle = icon_button('babble-compose-poll-symbolic',
                                       _('Poll'), toggle=True)
        self.poll_toggle.connect('toggled', self.on_poll_toggled)
        self.mention_button = icon_button('contact-new-symbolic',
                                          _('Mention'))
        self.mention_button.connect('clicked', self.on_mention_clicked)
        self.emoji_button = icon_button('face-smile-symbolic',
                                        _('Insert Emoji'))
        self.emoji_button.connect('clicked', self.on_emoji_clicked)
        self.thread_toggle = icon_button('view-list-symbolic',
                                         _('Keep Open for a Thread'),
                                         toggle=True)

        options = Gtk.Box(spacing=6)
        self.visibility_row = _make_dropdown(VISIBILITIES,
                                             _('Post Visibility'))
        self.language_row = _make_dropdown(LANGUAGES, _('Post Language'))
        self.language_row.set_selected(
            [lang[0] for lang in LANGUAGES].index(default_language()))
        options.append(self.visibility_row)
        options.append(self.language_row)
        self.counter_label = Gtk.Label(hexpand=True, xalign=1,
                                       tooltip_text=_('Characters Left'))
        self.counter_label.add_css_class('numeric')
        self.counter_label.add_css_class('caption')
        options.append(self.counter_label)
        bar.append(options)
        return bar

    # Draft state

    def text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter(), False)

    def snapshot(self):
        return {
            'text': self.text(),
            'visibility': self.visibility_row.get_selected(),
            'cw_visible': self.cw_toggle.get_active(),
            'cw_text': self.spoiler_entry.get_text(),
            'attachments': [{'path': row.path,
                             'alt': row.alt_entry.get_text()}
                            for row in self.attachments()],
            'language': self.language_row.get_selected(),
            'poll': self.poll_state(),
            'in_reply_to_id': self.in_reply_to_id,
        }

    def poll_state(self):
        return {'active': self.poll_toggle.get_active(),
                'options': [entry.get_text() for entry in self.poll_entries],
                'expires': self.poll_expiry.get_selected()}

    def _poll_options(self):
        return [entry.get_text().strip() for entry in self.poll_entries
                if entry.get_text().strip()]

    def restore(self, draft):
        self.buffer.set_text(draft['text'])
        self.visibility_row.set_selected(draft['visibility'])
        self.language_row.set_selected(draft['language'])
        self.cw_toggle.set_active(draft['cw_visible'])
        self.spoiler_entry.set_text(draft['cw_text'])
        self.clear_attachments()
        for attachment in draft['attachments']:
            self.add_attachment_path(attachment['path'],
                                     alt=attachment['alt'])
        self.in_reply_to_id = draft['in_reply_to_id']
        poll = draft['poll']
        self.poll_toggle.set_active(poll['active'])
        for index, text in enumerate(poll['options']):
            while index >= len(self.poll_entries):
                self.on_poll_add_choice(self.poll_add)
            self.poll_entries[index].set_text(text or '')
        self.poll_expiry.set_selected(poll['expires'])
        self.update_compose_state()

    def is_empty(self):
        return not (self.text() or self.attachments()
                    or self.spoiler_entry.get_text()
                    or any(e.get_text() for e in self.poll_entries))

    def clear_attachments(self):
        for row in self.attachments():
            self.attachments_box.remove(row)

    def start_reply(self, status_id, handle, visibility='public'):
        """Push the current draft (if any) and prefill a reply; only one
        level of reply stack is kept, so the prior draft is recovered
        with :meth:`pop_reply_state` on send or cancel.

        ``status_id`` None starts a brand-new message (a direct message
        from the conversations page): the draft in progress is kept, not
        replaced by an empty buffer."""
        new_message = status_id is None and not handle
        previous = self.snapshot()
        if new_message:
            if not self.is_empty():
                self._draft_stack = [previous]
        else:
            self.buffer.set_text('' if not handle else '@' + handle + ' ')
            self.buffer.place_cursor(self.buffer.get_end_iter())
            self._draft_stack = [previous]
        self.in_reply_to_id = str(status_id) if status_id is not None else None
        self._select_visibility(visibility)
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_mention(self, account, direct=False):
        """Prefill a mention of *account* (``direct`` for a DM)."""
        acct = account.get('acct') or account.get('username') or ''
        previous = self.snapshot()
        self.buffer.set_text('@%s ' % acct)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.in_reply_to_id = None
        if direct:
            self._select_visibility('direct')
        self._draft_stack = [previous]
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_edit(self, status):
        """Load an existing post into the composer for a PUT."""
        self.editing_id = str(status.get('id') or '') or None
        self._load_status_draft(status)

    def start_redraft(self, status):
        """Refill the composer from a just-deleted post. The server keeps
        the media for about 24h, so the attachment ids can be reused."""
        self.editing_id = None
        self._load_status_draft(status)

    def _load_status_draft(self, status):
        """Fill in the fields shared by editing and re-drafting a post."""
        source = dict(status)
        self._pending_media_ids = [
            attachment['id']
            for attachment in source.get('media_attachments') or []
            if attachment.get('id')]
        self.buffer.set_text(source.get('text') or '')
        self.buffer.place_cursor(self.buffer.get_end_iter())
        spoiler = source.get('spoiler_text') or ''
        self.cw_toggle.set_active(bool(spoiler))
        self.spoiler_entry.set_text(spoiler)
        self._select_visibility(source.get('visibility') or 'public')
        self.in_reply_to_id = None
        self._draft_stack = []
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_quote(self, status):
        """Quote *status*: the server prepends the quoted post."""
        self.quoted_status_id = str(status.get('id') or '') or None
        self.in_reply_to_id = None
        self._draft_stack = []
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        """Load a scheduled post or draft into the composer. Editing the
        content means re-scheduling, so the old entry is dropped once the
        new one is accepted."""
        from .scheduled import draft_scheduled_at
        params = scheduled.get('params') or {}
        self.scheduled_id = str(scheduled.get('id') or '') or None
        self.scheduled_at = draft_scheduled_at() if draft \
            else scheduled.get('scheduled_at')
        self.scheduled_on_done = on_done
        self._pending_media_ids = [
            attachment['id']
            for attachment in scheduled.get('media_attachments') or []
            if attachment.get('id')]
        self.buffer.set_text(params.get('text') or '')
        self.buffer.place_cursor(self.buffer.get_end_iter())
        spoiler = params.get('spoiler_text') or ''
        self.cw_toggle.set_active(bool(spoiler))
        self.spoiler_entry.set_text(spoiler)
        self._select_visibility(params.get('visibility') or 'public')
        self.in_reply_to_id = params.get('in_reply_to_id')
        self._draft_stack = []
        self.update_compose_state()
        self.text_view.grab_focus()

    def _select_visibility(self, visibility):
        codes = [code for code, _label in VISIBILITIES]
        if visibility in codes:
            self.visibility_row.set_selected(codes.index(visibility))

    def pop_reply_state(self):
        draft = self._draft_stack.pop() if self._draft_stack else None
        if draft is not None:
            self.restore(draft)
        if not self._draft_stack:
            self.in_reply_to_id = None
        self.update_compose_state()

    def on_reply_cancel(self, _banner):
        self.pop_reply_state()

    def is_reply(self):
        return self.in_reply_to_id is not None

    def counted_length(self):
        """Characters Mastodon will count for the current draft."""
        return countable_length(
            self.text(),
            self.spoiler_entry.get_text() if self.cw_toggle.get_active()
            else '')

    def update_compose_state(self):
        text = self.text()
        self.placeholder.set_visible(not text)
        self.reply_banner.set_revealed(bool(self._draft_stack))
        limit = self.char_limit
        remaining = limit - self.counted_length()
        self.counter_label.set_text(str(remaining))
        for css in ('dimmed', 'warning', 'error'):
            self.counter_label.remove_css_class(css)
        if remaining < 0:
            self.counter_label.add_css_class('error')
        elif remaining <= limit * 0.2:
            self.counter_label.add_css_class('warning')
        else:
            self.counter_label.add_css_class('dimmed')
        self.post_button.set_sensitive(self.can_post())
        self.discard_button.set_sensitive(not self._posting
                                          and not self.is_empty())
        self.emit('changed')

    def can_post(self):
        """A post needs text, media, a quote or reused media: Mastodon
        only requires text when none of the others are present."""
        return (not self._posting
                and self.counted_length() <= self.char_limit
                and bool(self.text() or self.attachments()
                         or self.quoted_status_id
                         or self._pending_media_ids))

    # Field events

    def on_draft_changed(self, *_args):
        self.update_compose_state()

    def on_cw_toggled(self, toggle):
        self._spoiler_box.set_visible(toggle.get_active())
        if toggle.get_active():
            self.spoiler_entry.grab_focus()
        else:
            self.spoiler_entry.set_text('')

    def on_poll_toggled(self, toggle):
        self._poll_box.set_visible(toggle.get_active())
        if toggle.get_active():
            self.poll_entries[0].grab_focus()
        else:
            for entry in self.poll_entries:
                entry.set_text('')

    def on_poll_add_choice(self, _row):
        if len(self.poll_entries) >= MAX_POLL_CHOICES:
            return
        entry = Adw.EntryRow(title=_('Choice %d')
                             % (len(self.poll_entries) + 1))
        entry.connect('changed', self.on_draft_changed)
        self._poll_box.insert(entry, len(self.poll_entries))
        self.poll_entries.append(entry)
        self.poll_add.set_visible(len(self.poll_entries) < MAX_POLL_CHOICES)

    def on_mention_clicked(self, _button):
        self.buffer.insert_at_cursor('@')
        self.text_view.grab_focus()

    def on_emoji_clicked(self, _button):
        chooser = Gtk.EmojiChooser()
        chooser.set_parent(self.emoji_button)
        chooser.connect('emoji-picked', self.on_emoji_picked)
        chooser.connect('closed', lambda c: c.unparent())
        chooser.popup()

    def on_emoji_picked(self, chooser, emoji):
        self.buffer.insert_at_cursor(emoji)
        chooser.unparent()

    def on_discard_clicked(self, _button):
        dialog = Adw.AlertDialog(
            heading=_('Discard Draft?'),
            body=_('The text, attachments and poll of this post will be '
                   'lost'))
        dialog.add_response('cancel', _('_Cancel'))
        dialog.add_response('discard', _('_Discard'))
        dialog.set_response_appearance('discard',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_discard_response)
        dialog.present(self)

    def on_discard_response(self, _dialog, response):
        if response == 'discard':
            self.reset()
            self.text_view.grab_focus()

    def on_attach_clicked(self, _button):
        media_filter = Gtk.FileFilter(name=_('Images and Videos'))
        media_filter.add_mime_type('image/*')
        media_filter.add_mime_type('video/*')
        dialog = Gtk.FileDialog(title=_('Attach Media'),
                                default_filter=media_filter)
        dialog.open_multiple(self.get_root(), None, self.on_files_selected)

    def on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(),
                             Gtk.DialogError.DISMISSED):
                self._show_error(_('Could not open the file picker'),
                                 e.message)
            return
        for index in range(files.get_n_items()):
            self.add_attachment_path(files.get_item(index).get_path())

    def add_attachment_path(self, path, alt=None, position=-1):
        if not path or len(self.attachments()) >= MAX_ATTACHMENTS:
            return None
        row = AttachmentRow(path)
        row.remove_button.connect('clicked', self.on_remove_attachment, row)
        row.alt_entry.connect('changed', self.on_alt_changed, row)
        if alt:
            row.alt_entry.set_text(alt)
        rows = self.attachments()
        sibling = rows[min(position, len(rows)) - 1] if position > 0 \
            and rows else None
        if position == 0 or sibling is not None:
            self.attachments_box.insert_child_after(row, sibling)
        else:
            self.attachments_box.append(row)
        self.attach_button.set_sensitive(
            len(self.attachments()) < MAX_ATTACHMENTS)
        self.update_compose_state()
        return row

    def attachments(self):
        rows = []
        child = self.attachments_box.get_first_child()
        while child is not None:
            if isinstance(child, AttachmentRow):
                rows.append(child)
            child = child.get_next_sibling()
        return rows

    def on_remove_attachment(self, _button, row):
        position = self.attachments().index(row)
        alt = row.alt_entry.get_text()
        self.attachments_box.remove(row)
        self.attach_button.set_sensitive(not self._posting)
        self.update_compose_state()
        toast = Adw.Toast(title=_('Attachment removed'),
                          button_label=_('_Undo'),
                          priority=Adw.ToastPriority.HIGH)
        toast.connect('button-clicked', lambda _t: self.add_attachment_path(
            row.path, alt=alt, position=position))
        self._add_toast(toast)

    def on_alt_changed(self, _entry, row):
        row.description = row.alt_entry.get_text()

    def _add_toast(self, toast):
        """Toasts raised inside the composer go on its own overlay: the
        dialog covers the window's, where they would be invisible and
        unusable."""
        overlay = self.get_ancestor(Adw.ToastOverlay)
        if overlay is not None:
            overlay.add_toast(toast)
            return
        root = self.get_root()
        if root is not None and hasattr(root, 'add_toast'):
            root.add_toast(toast)

    def _toast(self, text):
        self._add_toast(Adw.Toast(title=text, use_markup=False))

    def _show_error(self, heading, body):
        detail = summarize_error(body)
        self._toast(heading + ': ' + detail if detail else heading)

    # Posting

    def _on_post_shortcut(self, _widget, _args):
        self.on_post_clicked(self.post_button)
        return True

    def on_post_clicked(self, _button):
        if self._posting:
            return
        if not (self.text() or self.attachments() or self.quoted_status_id
                or self._pending_media_ids):
            return
        if not self._validate_before_upload():
            return
        self._posting = True
        self._cancel_uploads()
        self._upload_generation += 1
        self._upload_results = []
        self._upload_failures = 0
        self.set_posting_ui(True)
        pending = [row for row in self.attachments()
                   if row.uploaded_id is None]
        self._upload_expected = len(pending)
        for row in pending:
            self.upload_one(row)
        if not pending:
            self.send_status()

    def _validate_before_upload(self):
        """Refuse an invalid draft before anything is uploaded, so a
        rejected post never leaves orphaned media behind."""
        if self.counted_length() > self.char_limit:
            self._toast(_('Post is too long'))
            return False
        if self.cw_toggle.get_active() and not self.spoiler_entry.get_text():
            self._toast(_('Add content warning text'))
            return False
        if self.poll_toggle.get_active():
            options = self._poll_options()
            if len(options) < 2:
                self._toast(_('Add at least two poll choices'))
                return False
            if len(set(options)) != len(options):
                self._toast(_('Poll choices must be unique'))
                return False
            if self.attachments() or self._pending_media_ids:
                self._toast(_('A poll cannot be combined with media'))
                return False
        return True

    def set_posting_ui(self, active):
        for widget in (self.attach_button, self.cw_toggle, self.poll_toggle,
                       self.thread_toggle, self.visibility_row,
                       self.language_row):
            widget.set_sensitive(not active)
        if not active:
            self.attach_button.set_sensitive(
                len(self.attachments()) < MAX_ATTACHMENTS)
        self.progress_spinner.set_visible(active)
        for row in self.attachments():
            row.remove_button.set_sensitive(not active)
        self.update_compose_state()

    def _finish_posting(self):
        self._posting = False
        self.set_posting_ui(False)

    def _cancel_uploads(self):
        """Stop whatever the previous posting round still had in flight."""
        if self._upload_cancellable is not None:
            self._upload_cancellable.cancel()
        self._upload_cancellable = None
        for source in self._processing_poll.values():
            GLib.source_remove(source)
        self._processing_poll.clear()

    def upload_one(self, row):
        try:
            with open(row.path, 'rb') as f:
                data = f.read()
        except OSError as e:
            self.on_upload_failed(row, e.strerror or str(e))
            return
        generation = self._upload_generation
        if self._upload_cancellable is None:
            self._upload_cancellable = Gio.Cancellable()
        self.client.media_upload_async(
            data, GLib.path_get_basename(row.path), row.content_type,
            description=row.description or None,
            callback=self.on_uploaded_context(row, generation),
            cancellable=self._upload_cancellable)

    def on_uploaded_context(self, row, generation):
        def _on_uploaded(client, result, _user_data=None):
            if generation != self._upload_generation:
                return  # superseded by a later posting round
            self.on_uploaded(row, result)
        return _on_uploaded

    def on_uploaded(self, row, result):
        try:
            data = self.client.media_upload_finish(result)
        except GLib.Error as e:
            self.on_upload_failed(row, e.message)
            return
        media_id = data.get('id')
        if not data.get('url'):
            # Nothing decoded yet: the instance would reject the post,
            # so wait for the attachment to finish processing first.
            self.await_processing(row, media_id)
            return
        row.uploaded_id = media_id
        self._upload_results.append(data)
        self._maybe_send_after_uploads()

    def await_processing(self, row, media_id, attempt=0):
        """Poll GET /media/:id until the instance finishes processing."""
        if media_id is None:
            self.on_upload_failed(row, _('The server did not accept the media'))
            return
        generation = self._upload_generation

        def check(client, result, _user_data=None):
            if generation != self._upload_generation:
                return
            try:
                media = client.media_status_finish(result)
            except GLib.Error as e:
                if attempt + 1 >= PROCESSING_ATTEMPTS:
                    self.on_upload_failed(row, e.message)
                    return
                self._schedule_processing_check(row, media_id,
                                                 attempt + 1, generation)
                return
            if media.get('url'):
                row.uploaded_id = media_id
                self._upload_results.append(media)
                self._maybe_send_after_uploads()
                return
            if attempt + 1 >= PROCESSING_ATTEMPTS:
                self.on_upload_failed(
                    row, _('The server is still processing the media'))
                return
            self._schedule_processing_check(row, media_id, attempt + 1,
                                             generation)

        self.client.media_status_async(media_id, callback=check,
                                       cancellable=self._upload_cancellable)

    def _schedule_processing_check(self, row, media_id, attempt, generation):
        source = GLib.timeout_add(
            PROCESSING_INTERVAL_MS,
            self._run_processing_check, row, media_id, attempt, generation)
        self._processing_poll[media_id] = source

    def _run_processing_check(self, row, media_id, attempt, generation):
        self._processing_poll.pop(media_id, None)
        if generation != self._upload_generation:
            return False
        self.await_processing(row, media_id, attempt)
        return False

    def _maybe_send_after_uploads(self):
        if len(self._upload_results) + self._upload_failures \
                == self._upload_expected and not self._upload_failures:
            self.send_status()

    def on_upload_failed(self, row, message):
        self._upload_failures += 1
        self._finish_posting()
        self._show_error(_('Could not upload media'), message)

    def send_status(self):
        params = {
            'visibility': VISIBILITIES[self.visibility_row.get_selected()][0],
            'language': LANGUAGES[self.language_row.get_selected()][0],
        }
        if self.cw_toggle.get_active():
            cw_text = self.spoiler_entry.get_text()
            if cw_text:
                params['spoiler_text'] = cw_text
                params['sensitive'] = True
        if self.poll_toggle.get_active():
            options = self._poll_options()
            if len(options) < 2:
                self._finish_posting()
                self._toast(_('Add at least two poll choices'))
                return
            params['poll'] = {
                'options': options,
                'expires_in': POLL_DURATIONS[
                    self.poll_expiry.get_selected()][0],
            }
        if self.in_reply_to_id is not None:
            params['in_reply_to_id'] = self.in_reply_to_id
        if self.quoted_status_id is not None:
            params['quoted_status_id'] = self.quoted_status_id
        if self.scheduled_at is not None:
            params['scheduled_at'] = self.scheduled_at
        # Uploaded rows first, then media reused from the edited post.
        media_ids = [row.uploaded_id for row in self.attachments()
                     if row.uploaded_id]
        media_ids += [mid for mid in self._pending_media_ids
                      if mid not in media_ids]
        if media_ids:
            params['media_ids'] = media_ids
        if self.editing_id is not None:
            self.client.edit_status_async(self.editing_id, self.text(),
                                          params=params,
                                          callback=self.on_post_done)
        else:
            self.client.post_status_async(self.text(), params=params,
                                          callback=self.on_post_done)

    def on_post_done(self, client, result, _user_data=None):
        try:
            if self.editing_id is not None:
                status = client.edit_status_finish(result)
            else:
                status = client.post_status_finish(result)
        except GLib.Error as e:
            self._finish_posting()
            self._show_error(_('Could not post'), e.message)
            return
        self._finish_posting()
        # Resolve the toast target before the dialog closes.
        window = pages.window_of(self)
        root = self.get_root()
        was_edit = self.editing_id is not None
        scheduled_id = self.scheduled_id
        on_done = self.scheduled_on_done
        if self.thread_toggle.get_active() and not was_edit:
            # Chain the next post in the thread; media and poll must not
            # be sent twice.
            self.in_reply_to_id = str(status.get('id') or '') or None
            self.buffer.set_text('')
            self.clear_attachments()
            self.attach_button.set_sensitive(True)
            self.poll_toggle.set_active(False)
            self._upload_results = []
            self._upload_failures = 0
            self.update_compose_state()
        else:
            self.reset()
            dialog = self.get_ancestor(Adw.Dialog)
            if dialog is not None and dialog.get_parent() is not None:
                dialog.close()
        if scheduled_id is not None:
            self._discard_scheduled(scheduled_id, on_done)
        if was_edit:
            message = _('Post edited')
        elif scheduled_id is not None:
            message = _('Post scheduled')
        else:
            message = _('Post sent')
        if window is not None:
            window.show_toast(message)
        elif root is not None and hasattr(root, 'show_toast'):
            root.show_toast(message)
        return status

    def _discard_scheduled(self, scheduled_id, on_done):
        """Drop the scheduled entry this composer replaced, then let the
        list refresh. Its failure is not worth an error toast: the new
        entry exists and the old one is only a duplicate."""
        def finished(_client, _result, _data=None):
            if on_done is not None:
                on_done()

        self.client.request_async(
            'DELETE', '/scheduled_statuses/%s' % scheduled_id,
            callback=finished)

    def reset(self):
        # Discarding the draft also abandons any upload it started.
        self._cancel_uploads()
        self.buffer.set_text('')
        self.spoiler_entry.set_text('')
        self.cw_toggle.set_active(False)
        self.poll_toggle.set_active(False)
        self.clear_attachments()
        self.attach_button.set_sensitive(True)
        self._upload_results = []
        self._upload_failures = 0
        self._upload_expected = 0
        self.editing_id = None
        self.quoted_status_id = None
        self.scheduled_id = None
        self.scheduled_at = None
        self.scheduled_on_done = None
        self._pending_media_ids = []
        if self.in_reply_to_id is not None:
            self.pop_reply_state()
        self.update_compose_state()


class ComposeDialog(Adw.Dialog):
    """Composer dialog. Keep one instance per client and present it
    repeatedly: closing keeps the draft."""

    __gtype_name__ = 'BabbleComposeDialog'

    def __init__(self, client, **kwargs):
        super().__init__(content_width=600, content_height=480, **kwargs)
        self.view = BabbleComposeView(client)
        self.view._select_visibility(default_visibility())
        self.view.connect('changed', self._on_view_changed)
        self.set_child(self.view)
        self.set_focus(self.view.text_view)
        self._on_view_changed(self.view)

    @property
    def client(self):
        return self.view.client

    def start_reply(self, status_id, handle, visibility='public'):
        self.view.start_reply(status_id, handle, visibility)

    def start_mention(self, account, direct=False):
        self.view.start_mention(account, direct=direct)

    def start_edit(self, status):
        self.view.start_edit(status)

    def start_redraft(self, status):
        self.view.start_redraft(status)

    def start_quote(self, status):
        self.view.start_quote(status)

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        self.view.start_scheduled(scheduled, draft=draft, on_done=on_done)

    def _on_view_changed(self, view):
        if view.editing_id is not None:
            title = _('Edit Post')
        elif view.scheduled_id is not None:
            title = _('Edit Scheduled Post')
        elif view.quoted_status_id is not None:
            title = _('Quote Post')
        elif view.is_reply():
            title = _('Reply')
        else:
            title = _('New Post')
        self.set_title(title)
