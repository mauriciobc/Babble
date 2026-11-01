# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer dialog: new posts with attachments, visibility, content
warning, poll and language selection. Reply prefill goes through
:meth:`ComposeDialog.start_reply`; the previous draft is preserved across
the reply round trip and closing the dialog keeps the draft."""

import os
import re
import datetime
import json
import shutil
import time
from copy import deepcopy
from dataclasses import dataclass

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

from .i18n import gettext as _, ngettext

from gi.repository import (Adw, Gdk, Gio, GLib, GObject, Gtk,  # noqa: E402
                           GtkSource, Spelling)

from . import emojichooser, pages, portals  # noqa: E402
from .composeupload import UploadRound  # noqa: E402
from .drafts import (Attachment, Draft, Poll, drop_local_draft,  # noqa: E402
                     store_local_draft)
from .hashtagcompleter import HashtagCompleter, remember  # noqa: E402
from .mentioncompleter import MentionCompleter  # noqa: E402
from .mastodon import summarize_error  # noqa: E402
# The same list Preferences offers as the default post language.
from .preferences import LANGUAGES  # noqa: E402

# Spell checking (libspelling) works on a GtkSource.Buffer; that buffer
# is all the composer takes from GtkSourceView.
GtkSource.init()
Spelling.init()

DEFAULT_CHAR_LIMIT = 500
URL_PLACEHOLDER_CHARS = 23
# The fallback, for an instance that advertises no
# statuses.max_media_attachments; Mastodon's own default is 4, and its
# Android client hardcodes this same number while parsing the field and
# never reading it. See BabbleComposeView.attachment_limit for the
# instance-aware one.
MAX_ATTACHMENTS = 4
MAX_POLL_CHOICES = 4
# The composer dialog's width when the window has room for it.
COMPOSE_WIDTH = 600
SCHEMA_ID = 'io.github.mauriciobc.Babble'
SCREENSHOT_PORTAL = 'org.freedesktop.portal.Screenshot'

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

POLL_DURATIONS = [
    (1800, _('30 minutes')),
    (3600, _('1 hour')),
    (86400, _('1 day')),
    (604800, _('7 days')),
]


def _has_setting(key):
    """Whether the GSettings schema, and in a stale compiled schema the
    key, is installed."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SCHEMA_ID, True) if source is not None else None
    return schema is not None and schema.has_key(key)


def _setting_string(key):
    """GSettings string *key*, or None when the schema (or, in a stale
    compiled schema, the key) is not installed."""
    if not _has_setting(key):
        return None
    return pages.settings().get_string(key)


def default_language():
    """Language preset for a brand new post: the preference when set,
    else the system locale, else English."""
    codes = [lang[0] for lang in LANGUAGES]
    code = _setting_string('default-language')
    if code in codes:
        return code
    code = os.environ.get('LANG', 'en_US')[:2]
    if code not in codes:
        code = 'en'
    return code


def default_visibility():
    """Visibility preset for a brand new post, read from GSettings."""
    return _setting_string('default-visibility') or 'public'


def _language_subtag(code):
    """Primary language subtag of a locale or dictionary code: ``pt``
    for ``pt_BR``, ``pt-PT`` or ``pt_PT-preao``."""
    return re.split(r'[_\-.@]', code, maxsplit=1)[0].lower()


def spelling_language(code, available, preferred=()):
    """The dictionary to check a post written in *code* (an ISO 639-1
    code such as ``pt``) with: *code* itself when it is among
    *available* (dictionary codes such as ``pt_BR``), else a regional
    variant of it, the first of *preferred* (the user's locales, best
    first) that is one or else the first in order. None when there is
    no dictionary for the language at all."""
    if code in available:
        return code
    variants = sorted(name for name in available
                      if _language_subtag(name) == code)
    for name in preferred:
        if name in variants:
            return name
    return variants[0] if variants else None


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
    # .flat on the DropDown is the documented way to do this; reaching
    # into its child to add the class again styled libadwaita's internals,
    # which is not ours to keep in step with.
    dropdown.add_css_class('flat')
    dropdown.set_valign(Gtk.Align.CENTER)
    pages.set_accessible_label(dropdown, label)
    return dropdown


def _unparent_popovers(button):
    """A Gtk.Button does not unparent a child it did not add, so a popover
    set on it is released when the button goes, or the button is
    finalized with a child still attached. A module function rather than
    a method: the handler must not hold the composer."""
    child = button.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        if isinstance(child, Gtk.Popover):
            child.unparent()
        child = following


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


def pasted_dir():
    """Where images pasted into the composer are kept."""
    return os.path.join(GLib.get_user_cache_dir(), 'babble', 'pasted')


def _draft_attachment_paths():
    """Attachment paths of the saved local drafts, or None when they
    cannot be known (no schema installed)."""
    if not _has_setting('local-drafts'):
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


def _end_inhibition(inhibition):
    """Give back the logout and suspend inhibition a composer holds in
    the dict *inhibition*, if any."""
    app, cookie = inhibition['app'], inhibition['cookie']
    inhibition.update(app=None, cookie=0)
    if cookie:
        app.uninhibit(cookie)


class AttachmentRow(Gtk.ListBox):
    """Media attachment: preview row with remove button and a
    description (alt text) entry, as one boxed list."""

    __gtype_name__ = 'BabbleAttachmentRow'

    def __init__(self, path, **kwargs):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, **kwargs)
        self.add_css_class('boxed-list')
        self.path = path
        self.uploaded_id = None

        self.content_type = media_type(path) or 'application/octet-stream'
        # Audio has no picture to decode: its icon, without reading the
        # whole file in an attempt bound to fail.
        texture = None
        if not self.content_type.startswith('audio/'):
            try:
                texture = Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                pass
        if texture is not None:
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
            GLib.path_get_basename(path)))
        header.set_title_lines(1)
        header.add_prefix(self.thumbnail)
        header.add_suffix(self.remove_button)
        self.append(header)

        self.alt_entry = Adw.EntryRow(title=_('Description'))
        self.append(self.alt_entry)


@dataclass
class _ComposeState:
    draft: Draft
    editing_id: str | None
    quoted_status_id: str | None
    scheduled_id: str | None
    scheduled_at: str | None
    scheduled_on_done: object
    media_ids: tuple[str, ...]
    scheduled_fields: dict


@dataclass(frozen=True)
class _ScheduledReplacement:
    original_id: str
    accepted: dict
    on_done: object
    message: str


class BabbleComposeView(Adw.Bin):
    """Post composer content: header bar with Post, text area, options
    and toolbar. Emits ``changed`` whenever the draft state changes."""

    __gtype_name__ = 'BabbleComposeView'
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.emoji_chooser = None
        self.in_reply_to_id = None
        self._draft_stack = []
        self._posting = False
        # Logout and suspend are held back while a post is on its way:
        # the application that took the cookie, and the cookie.
        self._inhibition = {'app': None, 'cookie': 0}
        self.editing_id = None
        self.quoted_status_id = None
        self.scheduled_id = None
        self.local_draft_key = None
        self._saved_local_raw = None
        self._saved_local_content = None
        self.scheduled_at = None
        self.scheduled_on_done = None
        self._pending_media_ids = []
        self._languages = list(LANGUAGES)
        self._poll_durations = list(POLL_DURATIONS)
        self._scheduled_fields = {}
        self._replacement = None
        self._cleanup_busy = False
        self._cleanup_cancellable = None
        # One round per click of Post: abandoning the dialog or starting
        # a new post stops the uploads still in flight.
        self.uploads = UploadRound(
            client, self._on_upload_failed, self.send_status)
        # Set while the text view's own paste runs, re-emitted by
        # on_paste_clipboard when the clipboard holds nothing to attach.
        self._paste_through = False
        _prune_pasted_once()

        toolbar_view = Adw.ToolbarView()
        self._toolbar_view = toolbar_view
        self._cleanup_banner = None
        toolbar_view.add_top_bar(self.build_header_bar())
        self.reply_banner = Adw.Banner(title=_('Replying to a post'),
                                       button_label=_('_Cancel Reply'))
        pages.connect_weak(self.reply_banner, 'button-clicked',
                           self.on_reply_cancel)
        toolbar_view.add_top_bar(self.reply_banner)
        # Plain text: the title is a formatted date, not markup.
        self.schedule_banner = Adw.Banner(use_markup=False,
                                          button_label=_('_Clear Schedule'))
        pages.connect_weak(self.schedule_banner, 'button-clicked',
                           self.on_schedule_clear)
        toolbar_view.add_top_bar(self.schedule_banner)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
        self._content = content
        content.append(self.build_text_area())
        content.append(self.build_options())
        self.attachments_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                       spacing=12)
        content.append(self.attachments_box)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
            child=Adw.Clamp(maximum_size=600, child=content))
        toolbar_view.set_content(scrolled)
        # Files dropped on the content are attached. In the capture
        # phase, as the text view would otherwise take a file drag as the
        # text of its paths. The scrolled window gets :drop(active) while
        # a drag hovers it, which the stylesheet outlines.
        self.drop_target = Gtk.DropTarget.new(Gdk.FileList,
                                              Gdk.DragAction.COPY)
        self.drop_target.set_gtypes([Gdk.FileList, Gio.File])
        self.drop_target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pages.connect_weak(self.drop_target, 'drop', self.on_drop)
        scrolled.add_controller(self.drop_target)
        toolbar_view.add_bottom_bar(self.build_action_bar())
        # Own overlay: the composer lives in a dialog that covers the
        # window's, so feedback raised here must land inside it.
        self.set_child(Adw.ToastOverlay(child=toolbar_view))

        shortcuts = Gtk.ShortcutController(
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for accel in ('<Control>Return', '<Control>KP_Enter'):
            shortcuts.add_shortcut(Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string(accel),
                # Unbound: the action passes the widget, so it never
                # captures (and pins) this view.
                Gtk.CallbackAction.new(BabbleComposeView._on_post_shortcut)))
        self.add_controller(shortcuts)
        # Normally the posting round ends first, and that gives the
        # inhibition back; a composer finalized before is the last chance
        # not to leak it. Its Python wrapper may be gone by then, so the
        # notification holds the inhibition itself, not the composer.
        self.weak_ref(_end_inhibition, self._inhibition)
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
        pages.set_accessible_label(self.discard_button, _('Discard Draft'))
        pages.connect_weak(self.discard_button, 'clicked',
                           self.on_discard_clicked)
        header.pack_start(self.discard_button)

        self.post_button = Gtk.Button(label=_('_Post'), use_underline=True)
        self.post_button.add_css_class('suggested-action')
        self.post_button.set_tooltip_text(_('Post (Ctrl+Enter)'))
        pages.connect_weak(self.post_button, 'clicked', self.on_post_clicked)
        header.pack_end(self.post_button)
        self.progress_spinner = Adw.Spinner(visible=False)
        header.pack_end(self.progress_spinner)
        self.schedule_button = Gtk.Button(icon_name='alarm-symbolic')
        pages.set_accessible_label(self.schedule_button, _('Schedule Post'))
        pages.connect_weak(self.schedule_button, 'clicked',
                           self.on_schedule_clicked)
        header.pack_end(self.schedule_button)
        return header

    def build_text_area(self):
        # A GtkSource.Buffer, as spell checking needs one, and nothing
        # more of GtkSourceView: no syntax or bracket highlighting, and
        # no style scheme, whose colours are made for code. The
        # misspelling underline is then libspelling's own.
        self.buffer = GtkSource.Buffer(highlight_syntax=False,
                                       highlight_matching_brackets=False)
        self.buffer.set_style_scheme(None)
        pages.connect_weak(self.buffer, 'changed', self.on_draft_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=12, bottom_margin=12,
                                      left_margin=12, right_margin=12,
                                      accepts_tab=False)
        pages.connect_weak(self.text_view, 'paste-clipboard',
                           self.on_paste_clipboard)
        self.text_view.update_property([Gtk.AccessibleProperty.LABEL],
                                       [_('Post Text')])
        # Misspelled words are underlined, with corrections in the
        # context menu. A checker of this composer's own, not the shared
        # default one: its language follows the language row, and the
        # composer of another account may have another one picked. It is
        # turned on, in a language, by _update_spell_language.
        self.spell_adapter = Spelling.TextBufferAdapter.new(
            self.buffer,
            Spelling.Checker.new(Spelling.Provider.get_default(), None))
        self.text_view.set_extra_menu(self.spell_adapter.get_menu_model())
        self.text_view.insert_action_group('spelling', self.spell_adapter)
        spell_click = Gtk.GestureClick(
            button=Gdk.BUTTON_SECONDARY,
            propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pages.connect_weak(spell_click, 'pressed', self._on_spell_click)
        self.text_view.add_controller(spell_click)
        self.mention_completer = MentionCompleter(self.client, self.text_view)
        self.hashtag_completer = HashtagCompleter(self.client, self.text_view)
        self.placeholder = Gtk.Label(label=_('What’s on your mind?'),
                                     halign=Gtk.Align.START,
                                     valign=Gtk.Align.START,
                                     margin_top=12, margin_start=12,
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
        pages.connect_weak(self.spoiler_entry, 'changed',
                           self.on_draft_changed)
        self._spoiler_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE)
        self._spoiler_box.add_css_class('boxed-list')
        self._spoiler_box.append(self.spoiler_entry)
        self._spoiler_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            child=self._spoiler_box)
        box.append(self._spoiler_revealer)

        self._poll_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._poll_box.add_css_class('boxed-list')
        self.poll_entries = []
        self.poll_expiry = Adw.ComboRow(
            title=_('Duration'),
            model=Gtk.StringList.new([text for _s, text in self._poll_durations]))
        self.poll_expiry.set_selected(2)
        self._poll_box.append(self.poll_expiry)
        self.poll_add = Adw.ButtonRow(title=_('_Add Choice'),
                                      use_underline=True,
                                      start_icon_name='list-add-symbolic')
        pages.connect_weak(self.poll_add, 'activated',
                           self.on_poll_add_choice)
        self._poll_box.append(self.poll_add)
        for _n in range(2):
            self.on_poll_add_choice(self.poll_add)
        self._poll_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            child=self._poll_box)
        box.append(self._poll_revealer)
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
            pages.set_accessible_label(button, label)
            tools.append(button)
            return button

        self.attach_button = icon_button('mail-attachment-symbolic',
                                         _('Attach Media'))
        pages.connect_weak(self.attach_button, 'clicked',
                           self.on_attach_clicked)
        # Hidden until the screenshot portal is known to be there.
        self.screenshot_button = icon_button(
            'applets-screenshooter-symbolic', _('Take Screenshot'))
        self.screenshot_button.set_visible(False)
        pages.connect_weak(self.screenshot_button, 'clicked',
                           self.on_screenshot_clicked)
        portals.portal_version(SCREENSHOT_PORTAL,
                               pages.weak_method(self._on_screenshot_portal))
        self.cw_toggle = icon_button('dialog-warning-symbolic',
                                     _('Content Warning'), toggle=True)
        pages.connect_weak(self.cw_toggle, 'toggled', self.on_cw_toggled)
        self.poll_toggle = icon_button('babble-compose-poll-symbolic',
                                       _('Poll'), toggle=True)
        pages.connect_weak(self.poll_toggle, 'toggled', self.on_poll_toggled)
        self.mention_button = icon_button('contact-new-symbolic',
                                          _('Mention'))
        pages.connect_weak(self.mention_button, 'clicked',
                           self.on_mention_clicked)
        self.emoji_button = icon_button('face-smile-symbolic',
                                        _('Insert Emoji'))
        pages.connect_weak(self.emoji_button, 'clicked',
                           self.on_emoji_clicked)
        self.emoji_button.connect('destroy', _unparent_popovers)
        self.thread_toggle = icon_button('view-list-symbolic',
                                         _('Keep Open for a Thread'),
                                         toggle=True)
        pages.connect_weak(self.thread_toggle, 'toggled',
                           self.on_draft_changed)

        # Own WrapBox: a non-wrapping Gtk.Box of visibility + language +
        # counter overflowed the bottom sheet at 360 px with 1.5× text.
        options = Adw.WrapBox(child_spacing=6, line_spacing=6)
        self.visibility_row = _make_dropdown(VISIBILITIES,
                                             _('Post Visibility'))
        self.language_row = _make_dropdown(self._languages, _('Post Language'))
        self.language_row.set_expression(Gtk.PropertyExpression.new(
            Gtk.StringObject, None, 'string'))
        self.language_row.set_enable_search(True)
        self.language_row.set_selected(
            [lang[0] for lang in self._languages].index(default_language()))
        pages.connect_weak(self.language_row, 'notify::selected',
                           self._update_spell_language)
        self._update_spell_language()
        options.append(self.visibility_row)
        options.append(self.language_row)
        self.counter_label = Gtk.Label(hexpand=True, xalign=1,
                                       tooltip_text=_('Characters Left'))
        self.counter_label.add_css_class('numeric')
        self.counter_label.add_css_class('caption')
        self.counter_label.add_css_class('compose-counter')
        options.append(self.counter_label)
        bar.append(options)
        return bar

    # Draft state

    def text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter(), False)

    def snapshot(self):
        return Draft(
            text=self.text(),
            visibility=self.visibility_row.get_selected(),
            language=self.language_row.get_selected(),
            language_code=self._languages[self.language_row.get_selected()][0],
            cw_visible=self.cw_toggle.get_active(),
            cw_text=self.spoiler_entry.get_text(),
            attachments=[Attachment(path=row.path,
                                    alt=row.alt_entry.get_text())
                         for row in self.attachments()],
            poll=self.poll_state(),
            in_reply_to_id=self.in_reply_to_id)

    def poll_state(self):
        return Poll(active=self.poll_toggle.get_active(),
                    options=[entry.get_text()
                             for entry in self.poll_entries],
                    expires=self.poll_expiry.get_selected(),
                    duration_seconds=self._poll_durations[self.poll_expiry.get_selected()][0],
                    **self._scheduled_fields.get('poll_flags', {}))

    def _poll_options(self):
        return [entry.get_text().strip() for entry in self.poll_entries
                if entry.get_text().strip()]

    def _select_language(self, code):
        code = code or default_language()
        codes = [value for value, _label in self._languages]
        if code not in codes:
            self._languages.append((code, code))
            self.language_row.get_model().append(code)
            codes.append(code)
        self.language_row.set_selected(codes.index(code))

    def _select_poll_duration(self, seconds):
        values = [value for value, _label in self._poll_durations]
        if seconds not in values:
            # Translators: exact poll duration restored from a saved post.
            label = ngettext('%d second', '%d seconds', seconds) % seconds
            self._poll_durations.append((seconds, label))
            self.poll_expiry.get_model().append(label)
            values.append(seconds)
        self.poll_expiry.set_selected(values.index(seconds))

    def _valid_draft_settings(self, draft):
        if not 0 <= draft.visibility < len(VISIBILITIES) \
                or (draft.language_code is None
                    and not 0 <= draft.language < len(LANGUAGES)) \
                or (draft.poll.duration_seconds is None
                    and not 0 <= draft.poll.expires < len(POLL_DURATIONS)) \
                or len(draft.poll.options) > MAX_POLL_CHOICES:
            self._show_error(_('Could not restore draft'),
                             _('The saved draft has unsupported settings.'))
            return False
        return True

    def restore(self, draft):
        """Put *draft*, a :class:`drafts.Draft`, back into the composer."""
        if not self._valid_draft_settings(draft):
            return False
        self.buffer.set_text(draft.text)
        self.visibility_row.set_selected(draft.visibility)
        self._select_language(draft.language_code or LANGUAGES[draft.language][0])
        self.cw_toggle.set_active(draft.cw_visible)
        self.spoiler_entry.set_text(draft.cw_text)
        self.clear_attachments()
        for attachment in draft.attachments:
            self.add_attachment_path(attachment.path, alt=attachment.alt)
        self.in_reply_to_id = draft.in_reply_to_id
        for entry in self.poll_entries:
            entry.set_text('')
        self.poll_toggle.set_active(draft.poll.active)
        for index, text in enumerate(draft.poll.options):
            if index >= len(self.poll_entries):
                self.on_poll_add_choice(self.poll_add)
            self.poll_entries[index].set_text(text or '')
        if draft.poll.duration_seconds is not None:
            self._select_poll_duration(draft.poll.duration_seconds)
        else:
            self.poll_expiry.set_selected(draft.poll.expires)
        self._scheduled_fields['poll_flags'] = {
            'multiple': draft.poll.multiple, 'hide_totals': draft.poll.hide_totals}
        self.update_compose_state()
        return True

    def is_empty(self):
        return not (self.text() or self.attachments()
                    or self.spoiler_entry.get_text()
                    or any(e.get_text() for e in self.poll_entries))

    def save_local_draft(self, *_args):
        """Keep the unsent draft for the next launch; the dialog calls
        this when it closes with content still in it. One account's
        drafts cannot be restored on another one: ids in them are
        server-local."""
        if self._posting or self.editing_id is not None \
                or self.scheduled_id is not None or self.is_empty():
            return
        content = self.snapshot()
        if content == self._saved_local_content:
            # Closing again without touching anything must not fill the
            # list with copies of one draft.
            return
        # A changed draft replaces its earlier saved version, so posting
        # it later leaves no stale copy behind to restore.
        previous = self._saved_local_raw
        self._saved_local_raw = content.as_stored(
            self.local_draft_key,
            datetime.datetime.now(datetime.timezone.utc).isoformat())
        self._saved_local_content = content
        store_local_draft(pages.settings(), self._saved_local_raw, previous)

    def drop_saved_local_draft(self):
        """The saved copy of a draft that was just sent has to stop
        being offered as something to restore."""
        if self._saved_local_raw is None:
            return
        drop_local_draft(pages.settings(), self._saved_local_raw)
        self._saved_local_raw = None
        self._saved_local_content = None

    def _accept_transition(self):
        if self._replacement is not None:
            self._toast(_('Remove the original scheduled post before '
                          'starting another post.'))
            return False
        if self._posting:
            self._toast(_('Finish sending this post first.'))
            return False
        return True

    def _clear_submission_mode(self):
        if self._replacement is not None:
            return False
        self.editing_id = None
        self.quoted_status_id = None
        self.scheduled_id = None
        self.scheduled_at = None
        self.scheduled_on_done = None
        self._pending_media_ids = []
        self._scheduled_fields = {}
        return True

    def _capture_state(self):
        return _ComposeState(
            self.snapshot(), self.editing_id, self.quoted_status_id,
            self.scheduled_id, self.scheduled_at, self.scheduled_on_done,
            tuple(self._pending_media_ids), deepcopy(self._scheduled_fields))

    def _restore_state(self, state):
        if not self._clear_submission_mode():
            return False
        self.restore(state.draft)
        self.editing_id = state.editing_id
        self.quoted_status_id = state.quoted_status_id
        self.scheduled_id = state.scheduled_id
        self.scheduled_at = state.scheduled_at
        self.scheduled_on_done = state.scheduled_on_done
        self._pending_media_ids = list(state.media_ids)
        self._scheduled_fields = deepcopy(state.scheduled_fields)
        self.update_compose_state()
        return True

    def restore_stashing(self, draft):
        """Restore a recovered draft, keeping the content in progress
        on the draft stack so the banner above the buffer can bring it
        back."""
        if not self._accept_transition():
            return False
        if not self._valid_draft_settings(draft):
            return False
        if not self.is_empty():
            self._draft_stack = [self._capture_state()]
        self._clear_submission_mode()
        self.restore(draft)
        return True

    def clear_attachments(self):
        for row in self.attachments():
            self.attachments_box.remove(row)

    def _finish_begin(self):
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_reply(self, status_id, handle, visibility='public'):
        """Push the current draft (if any) and prefill a reply; only one
        level of reply stack is kept, so the prior draft is recovered
        with :meth:`pop_reply_state` on send or cancel.

        ``status_id`` None starts a brand-new message (a direct message
        from the conversations page): the draft in progress is kept, not
        replaced by an empty buffer."""
        if not self._accept_transition():
            return False
        new_message = status_id is None and not handle
        previous = self._capture_state()
        self._clear_submission_mode()
        if new_message:
            if not self.is_empty():
                self._draft_stack = [previous]
        else:
            self.buffer.set_text('' if not handle else '@' + handle + ' ')
            self.buffer.place_cursor(self.buffer.get_end_iter())
            self._draft_stack = [previous]
        self.in_reply_to_id = str(status_id) if status_id is not None else None
        self._select_visibility(visibility)
        self._finish_begin()
        return True

    def start_mention(self, account, direct=False):
        """Prefill a mention of *account* (``direct`` for a DM)."""
        if not self._accept_transition():
            return False
        acct = account.get('acct') or account.get('username') or ''
        previous = self._capture_state()
        self._clear_submission_mode()
        self.buffer.set_text('@%s ' % acct)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.in_reply_to_id = None
        if direct:
            self._select_visibility('direct')
        self._draft_stack = [previous]
        self._finish_begin()
        return True

    def prefill_text(self, text):
        """Start a post with *text*, as shared from outside the app. A
        draft in progress is kept on the draft stack, for the banner
        above the buffer to bring back."""
        if not self._accept_transition():
            return False
        previous = self._capture_state()
        self._clear_submission_mode()
        if not self.is_empty():
            self._draft_stack = [previous]
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.in_reply_to_id = None
        self._finish_begin()
        return True

    def start_edit(self, status):
        """Load an existing post into the composer for a PUT."""
        if not self._accept_transition():
            return False
        self._clear_submission_mode()
        self.editing_id = str(status.get('id') or '') or None
        self._load_status_draft(status)
        return True

    def start_redraft(self, status):
        """Refill the composer from a just-deleted post. The server keeps
        the media for about 24h, so the attachment ids can be reused."""
        if not self._accept_transition():
            return False
        self._clear_submission_mode()
        self._load_status_draft(status)
        return True

    def _load_status_draft(self, status):
        """Fill in the fields shared by editing and re-drafting a post."""
        self.clear_attachments()
        self.poll_toggle.set_active(False)
        for entry in self.poll_entries:
            entry.set_text('')
        self.poll_expiry.set_selected(2)
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
        self._finish_begin()

    def start_quote(self, status):
        """Quote *status*: the server prepends the quoted post."""
        if not self._accept_transition():
            return False
        self._clear_submission_mode()
        self.quoted_status_id = str(status.get('id') or '') or None
        self.in_reply_to_id = None
        self._draft_stack = []
        self._finish_begin()
        return True

    def _scheduled_content(self, scheduled):
        def unsupported():
            raise GLib.Error(_('The server returned unsupported post settings.'),
                             'mastodon-http-error-quark', 0)

        if not isinstance(scheduled, dict) or not isinstance(scheduled.get('id'), str) \
                or not scheduled['id']:
            unsupported()
        params = scheduled.get('params')
        if params is None:
            params = {}
        if not isinstance(params, dict):
            unsupported()
        content = {}
        for key in ('text', 'spoiler_text'):
            value = params.get(key)
            if value is None:
                value = ''
            if not isinstance(value, str):
                unsupported()
            content[key] = value
        visibility = params.get('visibility')
        if visibility is None:
            visibility = 'public'
        if visibility not in [code for code, _label in VISIBILITIES]:
            unsupported()
        content['visibility'] = visibility
        language = params.get('language')
        if language is not None and not isinstance(language, str):
            unsupported()
        content['language'] = language
        for key in ('in_reply_to_id', 'quoted_status_id'):
            value = params.get(key)
            if key == 'in_reply_to_id' and type(value) is int and value > 0:
                value = str(value)
            if value is not None and (not isinstance(value, str) or not value):
                unsupported()
            content[key] = value
        sensitive = params.get('sensitive')
        if sensitive is not None and not isinstance(sensitive, bool):
            unsupported()
        policy = params.get('quote_approval_policy')
        if policy is not None and policy not in ('public', 'followers', 'nobody'):
            unsupported()
        content.update(sensitive=sensitive, quote_approval_policy=policy)
        attachments = scheduled.get('media_attachments')
        media_ids = params.get('media_ids')
        if attachments is None:
            attachments = []
        if media_ids is None:
            media_ids = []
        if not isinstance(attachments, list) or not isinstance(media_ids, list):
            unsupported()
        ids = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                unsupported()
            value = attachment.get('id')
            if not isinstance(value, str) or not value:
                unsupported()
            ids.append(value)
        if any(not isinstance(value, str) or not value for value in media_ids):
            unsupported()
        content['media_ids'] = ids or list(media_ids)
        poll = params.get('poll')
        if poll is not None:
            if not isinstance(poll, dict):
                unsupported()
            options = poll.get('options')
            expiry = poll.get('expires_in')
            if not isinstance(options, list) \
                    or not 2 <= len(options) <= MAX_POLL_CHOICES \
                    or any(not isinstance(o, str) or not o.strip() for o in options) \
                    or len({o.strip() for o in options}) != len(options) \
                    or type(expiry) is not int or expiry <= 0:
                unsupported()
            flags = {key: poll.get(key, False) for key in ('multiple', 'hide_totals')}
            if any(not isinstance(value, bool) for value in flags.values()):
                unsupported()
            poll = dict(options=list(options), expires_in=expiry, **flags)
        content['poll'] = poll
        return content

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        """Load a scheduled post or draft into the composer. Editing the
        content means re-scheduling, so the old entry is dropped once the
        new one is accepted."""
        if not self._accept_transition():
            return False
        try:
            params = self._scheduled_content(scheduled)
        except GLib.Error as error:
            self._show_error(_('Could not edit scheduled post'), error.message)
            return False
        self._clear_submission_mode()
        self.clear_attachments()
        self.poll_toggle.set_active(False)
        for entry in self.poll_entries:
            entry.set_text('')
        self.poll_expiry.set_selected(2)
        from .scheduled import draft_scheduled_at
        self.scheduled_id = scheduled['id']
        self.scheduled_at = draft_scheduled_at() if draft \
            else scheduled.get('scheduled_at')
        self.scheduled_on_done = on_done
        self._pending_media_ids = params['media_ids']
        self.buffer.set_text(params.get('text') or '')
        self.buffer.place_cursor(self.buffer.get_end_iter())
        spoiler = params.get('spoiler_text') or ''
        self.cw_toggle.set_active(bool(spoiler))
        self.spoiler_entry.set_text(spoiler)
        self._select_visibility(params.get('visibility') or 'public')
        self.in_reply_to_id = params.get('in_reply_to_id')
        self.quoted_status_id = params['quoted_status_id']
        self._select_language(params['language'])
        for key in ('sensitive', 'quote_approval_policy'):
            if params[key] is not None:
                self._scheduled_fields[key] = params[key]
        poll = params['poll']
        if poll is not None:
            self.poll_toggle.set_active(True)
            for index, option in enumerate(poll['options']):
                if index >= len(self.poll_entries):
                    self.on_poll_add_choice(self.poll_add)
                self.poll_entries[index].set_text(option)
            self._select_poll_duration(poll['expires_in'])
            self._scheduled_fields['poll_flags'] = {
                key: poll[key] for key in ('multiple', 'hide_totals')}
        self._draft_stack = []
        self._finish_begin()
        return True

    def _select_visibility(self, visibility):
        codes = [code for code, _label in VISIBILITIES]
        if visibility in codes:
            self.visibility_row.set_selected(codes.index(visibility))

    def pop_reply_state(self):
        if self._replacement is not None or self._posting:
            return
        state = self._draft_stack.pop() if self._draft_stack else None
        if state is not None:
            self._restore_state(state)
        else:
            self.in_reply_to_id = None
        self.update_compose_state()

    def on_reply_cancel(self, _banner):
        self.pop_reply_state()

    def is_reply(self):
        return self.in_reply_to_id is not None

    # Scheduling

    def is_timed(self):
        """True when the post goes out at a chosen time rather than now.
        A server-side draft also carries a date, but one that is never
        reached, so it is not a time to show."""
        from .scheduled import is_draft
        return self.scheduled_at is not None \
            and not is_draft({'scheduled_at': self.scheduled_at})

    def on_schedule_clicked(self, _button):
        if self._replacement is not None:
            return
        from .scheduled import SchedulePickerDialog
        dialog = SchedulePickerDialog(
            self.scheduled_at, _('_Set Time'),
            on_picked=pages.weak_method(self._on_schedule_picked))
        dialog.present(self)

    def _on_schedule_picked(self, iso):
        if self._replacement is not None:
            return
        self.scheduled_at = iso
        self.update_compose_state()

    def on_schedule_clear(self, _banner):
        if self._replacement is not None:
            return
        # Back to sending now. An entry already on the server is still
        # replaced: the post goes out and the scheduled copy is dropped.
        self.scheduled_at = None
        self.update_compose_state()

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
        timed = self.is_timed()
        if timed:
            from .scheduled import format_scheduled_at
            # Translators: %s is a date and time, e.g. "October 9, 2026 ·
            # 14:30".
            self.schedule_banner.set_title(
                _('Scheduled for %s') % format_scheduled_at(
                    self.scheduled_at))
        self.schedule_banner.set_revealed(timed)
        self.post_button.set_label(_('_Schedule') if timed else _('_Post'))
        self.post_button.set_tooltip_text(
            _('Schedule (Ctrl+Enter)') if timed else _('Post (Ctrl+Enter)'))
        # An edit of a published post cannot be moved to another time.
        self.schedule_button.set_visible(self.editing_id is None)
        self.schedule_button.set_sensitive(not self._posting)
        if self._replacement is not None:
            self.schedule_button.set_sensitive(False)
        self.schedule_button.set_tooltip_text(
            _('Change Time') if timed else _('Schedule Post'))
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
                                          and self._replacement is None
                                          and not self.is_empty())
        self.emit('changed')

    def _within_char_limit(self):
        return self.counted_length() <= self.char_limit

    def can_post(self):
        """A post needs text, media, a quote or reused media: Mastodon
        only requires text when none of the others are present."""
        return (not self._posting
                and self._replacement is None
                and self._within_char_limit()
                and bool(self.text() or self.attachments()
                         or self.quoted_status_id
                         or self._pending_media_ids))

    # Field events

    def on_draft_changed(self, *_args):
        self.update_compose_state()

    def _update_spell_language(self, *_args):
        """Check the post in the language picked for it, and not at all
        when there is no dictionary for that language: a post checked
        against another language's would come out all underlined. With
        no language picked, the default dictionary."""
        provider = Spelling.Provider.get_default()
        selected = self.language_row.get_selected()
        if selected < len(self._languages):
            languages = provider.list_languages()
            available = [languages.get_item(index).get_code()
                         for index in range(languages.get_n_items())]
            code = spelling_language(self._languages[selected][0], available,
                                     GLib.get_language_names())
        else:
            code = provider.get_default_code()
        if code:
            self.spell_adapter.set_language(code)
        self.spell_adapter.set_enabled(bool(code))

    def _on_spell_click(self, _gesture, n_press, x, y):
        """The corrections offered are those for the word at the cursor,
        worked out a moment after it moves, while the text view opens
        its menu at once without moving it. So a right click first puts
        the cursor on the word clicked, unless it lands in a selection,
        and has the corrections updated."""
        if n_press != 1:
            return
        buffer_x, buffer_y = self.text_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(x), int(y))
        _over_text, where = self.text_view.get_iter_at_location(buffer_x,
                                                                buffer_y)
        selection = self.buffer.get_selection_bounds()
        if not selection or not where.in_range(*selection):
            self.buffer.place_cursor(where)
        self.spell_adapter.update_corrections()

    def on_cw_toggled(self, toggle):
        pages.reveal(self._spoiler_revealer, toggle.get_active(),
                     animate=self.get_mapped())
        if toggle.get_active():
            self.spoiler_entry.grab_focus()
        else:
            self.spoiler_entry.set_text('')

    def on_poll_toggled(self, toggle):
        pages.reveal(self._poll_revealer, toggle.get_active(),
                     animate=self.get_mapped())
        if toggle.get_active():
            self.poll_entries[0].grab_focus()
        else:
            self._scheduled_fields.pop('poll_flags', None)
            for entry in self.poll_entries:
                entry.set_text('')

    def on_poll_add_choice(self, _row):
        if len(self.poll_entries) >= MAX_POLL_CHOICES:
            return
        entry = Adw.EntryRow(title=_('Choice %d')
                             % (len(self.poll_entries) + 1))
        pages.connect_weak(entry, 'changed', self.on_draft_changed)
        self._poll_box.insert(entry, len(self.poll_entries))
        self.poll_entries.append(entry)
        self.poll_add.set_visible(len(self.poll_entries) < MAX_POLL_CHOICES)

    def on_mention_clicked(self, _button):
        """Start a mention at the cursor, set off from a word before it,
        and show the suggestions as the name is typed."""
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        text = '@'
        if not cursor.starts_line():
            previous = cursor.copy()
            previous.backward_char()
            if not previous.get_char().isspace():
                text = ' @'
        self.text_view.grab_focus()
        self.buffer.insert_at_cursor(text)
        self.mention_completer.prompt()

    def on_emoji_clicked(self, _button):
        """One popover with a standard and a custom emoji tab; the custom
        tab hides itself when the instance has none."""
        if self.emoji_chooser is None:
            self.emoji_chooser = emojichooser.CustomEmojiChooser(self.client)
            self.emoji_chooser.set_parent(self.emoji_button)
            pages.connect_weak(self.emoji_chooser, 'emoji-picked',
                               self.on_custom_emoji_picked)
            pages.connect_weak(self.emoji_chooser, 'text-picked',
                               self.on_emoji_picked)
        self.emoji_chooser.popup()

    def on_emoji_picked(self, chooser, emoji):
        if self._replacement is not None:
            return
        self.buffer.insert_at_cursor(emoji)
        self.text_view.grab_focus()

    def on_custom_emoji_picked(self, _chooser, shortcode):
        """Insert ``:shortcode:`` set off from the surrounding words, as
        Mastodon only renders it between non-word characters."""
        if self._replacement is not None:
            return
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        before = ''
        if not cursor.starts_line():
            previous = cursor.copy()
            previous.backward_char()
            before = previous.get_char()
        after = cursor.get_char()
        text = ':%s:' % shortcode
        if before and not before.isspace():
            text = ' ' + text
        if not after or not after.isspace():
            text += ' '
        self.buffer.insert_at_cursor(text)
        self.text_view.grab_focus()

    def on_discard_clicked(self, _button):
        if self._replacement is not None:
            return
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
        dialog = Gtk.FileDialog(title=_('Attach Media'),
                                default_filter=self.media_filter())
        dialog.open_multiple(self.get_root(), None, self.on_files_selected)

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

    def attachment_limit(self):
        """How many attachments a post may carry here.

        Read from the instance rather than assumed, so a server that
        raised or lowered the limit is obeyed instead of refused at
        four. Falls back to :data:`MAX_ATTACHMENTS` for a client that
        cannot answer, which is only the test stubs.
        """
        reader = getattr(self.client, 'max_media_attachments', None)
        return reader() if reader is not None else MAX_ATTACHMENTS

    def on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error as e:
            if not e.matches(Gtk.DialogError.quark(),
                             Gtk.DialogError.DISMISSED):
                self._show_error(_('Could not open the file picker'),
                                 e.message)
            return
        self.add_attachment_files(
            [files.get_item(index) for index in range(files.get_n_items())])

    def on_screenshot_clicked(self, _button):
        """Take a screenshot through the portal, which lets the user
        pick what to capture, and attach it."""
        portals.session_bus(pages.weak_method(self._on_screenshot_bus))

    def _on_screenshot_portal(self, version):
        self.screenshot_button.set_visible(version > 0)

    def _on_screenshot_bus(self, bus):
        if bus is None:
            self._toast(_('Could not take a screenshot'))
            return
        portals.portal_request(bus, SCREENSHOT_PORTAL, 'Screenshot',
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
            self._toast(_('Could not take a screenshot'))
            return
        # Checked before copying, so a refused screenshot leaves no file.
        if not self._room_for_attachment():
            return
        try:
            path = save_screenshot(source)
        except OSError as e:
            self._show_error(_('Could not take a screenshot'),
                             e.strerror or str(e))
            return
        self.add_attachment_files([Gio.File.new_for_path(path)])

    def _room_for_attachment(self):
        """Whether one more attachment can be added now; a toast says
        why not."""
        if self._replacement is not None:
            return False
        if self._posting:
            self._toast_busy_posting()
            return False
        if len(self.attachments()) >= self.attachment_limit():
            self._toast_attachment_limit()
            return False
        if self._has_lone_media():
            self._toast_lone_media()
            return False
        return True

    def _has_lone_media(self):
        """Whether a video or audio file is attached: nothing can join it."""
        return any(must_be_alone(row.content_type)
                   for row in self.attachments())

    def _can_attach_more(self):
        return len(self.attachments()) < self.attachment_limit() \
            and not self._has_lone_media()

    def _set_attach_sensitive(self, sensitive):
        """Attaching media and taking a screenshot go together."""
        self.attach_button.set_sensitive(sensitive)
        self.screenshot_button.set_sensitive(sensitive)

    def add_attachment_path(self, path, alt=None, position=-1):
        if self._replacement is not None:
            return None
        if not path or not self._can_attach_more():
            return None
        if self.attachments() and must_be_alone(media_type(path)):
            return None
        row = AttachmentRow(path)
        # The row is looked up from the emitter: passing it as user data
        # would pin it through its own button and entry.
        pages.connect_weak(row.remove_button, 'clicked',
                           self._on_row_remove_clicked)
        pages.connect_weak(row.alt_entry, 'changed', self._on_row_alt_changed)
        if alt:
            row.alt_entry.set_text(alt)
        rows = self.attachments()
        sibling = rows[min(position, len(rows)) - 1] if position > 0 \
            and rows else None
        if position == 0 or sibling is not None:
            self.attachments_box.insert_child_after(row, sibling)
        else:
            self.attachments_box.append(row)
        self._set_attach_sensitive(self._can_attach_more())
        self.update_compose_state()
        return row

    def add_attachment_files(self, files):
        """Attach the images, videos and audio among *files* (Gio.File
        objects or paths), as a drop, a paste of copied files or files
        opened from outside do, up to the attachment limit; a video or an
        audio file goes alone, as Mastodon takes it. A toast tells what
        had to be left out. Nothing is added while posting, as the round
        in flight would not send it: a toast says so. Returns the rows
        added."""
        files = list(files)
        if self._replacement is not None:
            return []
        if self._posting:
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
                    must_be_alone(mime) and self.attachments()):
                lone = True
            elif len(self.attachments()) >= self.attachment_limit():
                over_limit = True
            else:
                added.append(self.add_attachment_path(path))
        if rejected:
            self._toast(_('Only images, videos and audio can be attached'))
        if lone:
            self._toast_lone_media()
        if over_limit:
            self._toast_attachment_limit()
        return added

    def _toast_busy_posting(self):
        self._toast(_('Wait for the post to be sent to attach files'))

    def _toast_lone_media(self):
        self._toast(_('A video or audio file must be the only attachment'))

    def _toast_attachment_limit(self):
        # Translators: %d is the most attachments a post can have.
        limit = self.attachment_limit()
        text = ngettext('A post can have at most %d attachment',
                        'A post can have at most %d attachments',
                        limit)
        self._toast(text % limit)

    def on_drop(self, _target, value, _x, _y):
        # The value only lives through this emission: take the files out
        # of it now.
        files = value.get_files() if isinstance(value, Gdk.FileList) \
            else [value]
        return bool(self.add_attachment_files(files))

    def on_paste_clipboard(self, text_view):
        """Pasting copied files attaches them, and pasting a bare image
        (a screenshot, an image copied from a browser) saves it and
        attaches it. Anything that has text pastes as text, the text
        view's own handler."""
        if self._replacement is not None:
            text_view.stop_emission_by_name('paste-clipboard')
            return
        if self._paste_through:
            return
        if self._read_clipboard_media(files=True):
            text_view.stop_emission_by_name('paste-clipboard')

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

    def _paste_text(self):
        """The text view's own paste, which on_paste_clipboard lets
        through."""
        self._paste_through = True
        try:
            self.text_view.emit('paste-clipboard')
        finally:
            self._paste_through = False

    def _on_paste_files(self, clipboard, result):
        try:
            value = clipboard.read_value_finish(result)
        except GLib.Error as e:
            self._show_error(_('Could not paste the files'), e.message)
            return
        # The value only lives through this callback.
        local = [f for f in value.get_files() if f.get_path()]
        if local:
            self.add_attachment_files(local)
        elif not self._read_clipboard_media(files=False):
            # Remote locations only, such as a copied link: their text.
            self._paste_text()

    def _on_paste_texture(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            self._show_error(_('Could not paste the image'), e.message)
            return
        if texture is None:
            return
        # Checked before saving, so a refused paste leaves no file.
        if not self._room_for_attachment():
            return
        try:
            path = save_pasted_texture(texture)
        except OSError as e:
            self._show_error(_('Could not paste the image'),
                             e.strerror or str(e))
            return
        self.add_attachment_path(path)

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
        self._set_attach_sensitive(not self._posting
                                   and self._can_attach_more())
        self.update_compose_state()
        toast = Adw.Toast(title=_('Attachment removed'),
                          button_label=_('_Undo'),
                          priority=Adw.ToastPriority.HIGH)
        toast.connect('button-clicked', lambda _t: self.add_attachment_path(
            row.path, alt=alt, position=position))
        self._add_toast(toast)

    def _on_row_remove_clicked(self, button):
        self.on_remove_attachment(button, button.get_ancestor(AttachmentRow))

    def _on_row_alt_changed(self, entry):
        self.on_alt_changed(entry, entry.get_ancestor(AttachmentRow))

    def on_alt_changed(self, _entry, _row):
        self.update_compose_state()

    def _add_toast(self, toast):
        """Toasts raised inside the composer go on its own overlay: the
        dialog covers the window's, where they would be invisible and
        unusable. Both fallbacks exist -- pages.show_toast reaches for the
        window when a widget is not under an overlay, and this same
        lookup is what it would do."""
        overlay = self.get_ancestor(Adw.ToastOverlay)
        if overlay is not None:
            overlay.add_toast(toast)
            return
        window = pages.window_of(self)
        if window is not None:
            window.add_toast(toast)

    def _toast(self, text):
        # Toasts raised inside the composer go on its own overlay: the
        # dialog covers the window's, where they would be invisible. The
        # composer is a dialog, so pages.show_toast's window_of() lands on
        # the active window rather than on this dialog -- which is the one
        # case where the lookup has to be the composer's own.
        self._add_toast(Adw.Toast(title=text, use_markup=False))

    def _show_error(self, heading, body):
        # The same '<heading>: <message>' pages.report_error builds, over
        # the same overlay as _toast, so the summary bound and the toast
        # text are each the one implementation rather than a second copy.
        detail = summarize_error(body)
        self._toast(heading + ': ' + detail if detail else heading)

    # Posting

    def _on_post_shortcut(self, _args):
        # Called unbound by the shortcut action as (widget, args): the
        # widget is this view.
        self.on_post_clicked(self.post_button)
        return True

    def on_post_clicked(self, _button):
        if self._posting or self._replacement is not None:
            return
        if not (self.text() or self.attachments() or self.quoted_status_id
                or self._pending_media_ids):
            return
        if not self._validate_before_upload():
            return
        self._posting = True
        self._inhibit_session()
        pending = [row for row in self.attachments()
                   if row.uploaded_id is None]
        self.uploads.begin(len(pending))
        self.set_posting_ui(True)
        for row in pending:
            self.uploads.upload_one(row)
        if not pending:
            self.send_status()

    def _validate_before_upload(self):
        """Refuse an invalid draft before anything is uploaded, so a
        rejected post never leaves orphaned media behind."""
        if not self._within_char_limit():
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
        cleanup = self._replacement is not None
        locked = active or cleanup
        # Only the cleanup-only state freezes the content: disabling it on
        # every post would take keyboard focus out of the text view.
        self._content.set_sensitive(not cleanup)
        self.text_view.set_editable(not cleanup)
        self.reply_banner.set_sensitive(not locked)
        self.schedule_banner.set_sensitive(not locked)
        for widget in (self.attach_button, self.screenshot_button,
                       self.cw_toggle, self.poll_toggle,
                       self.mention_button, self.emoji_button,
                       self.thread_toggle, self.visibility_row,
                       self.language_row):
            widget.set_sensitive(not locked)
        if active:
            self.mention_completer.dismiss()
            self.hashtag_completer.dismiss()
        else:
            self._set_attach_sensitive(not locked and self._can_attach_more())
        self.progress_spinner.set_visible(active)
        # No drop is taken while posting, so none is outlined either.
        self.drop_target.set_actions(Gdk.DragAction.NONE if locked
                                     else Gdk.DragAction.COPY)
        for row in self.attachments():
            row.remove_button.set_sensitive(not locked)
        self.update_compose_state()

    def _finish_posting(self):
        self._posting = False
        _end_inhibition(self._inhibition)
        self.set_posting_ui(False)

    def _inhibit_session(self):
        """Keep the session from logging out or suspending while the
        post is uploaded and sent: the round would be cut off half way."""
        app = Gio.Application.get_default()
        if self._inhibition['cookie'] or not isinstance(app, Gtk.Application):
            return
        root = self.get_root()
        # Translators: why logging out or suspending is held back, as the
        # session shows it while a post is being uploaded and sent.
        reason = _('Sending a post')
        cookie = app.inhibit(root if isinstance(root, Gtk.Window) else None,
                             Gtk.ApplicationInhibitFlags.LOGOUT
                             | Gtk.ApplicationInhibitFlags.SUSPEND, reason)
        if cookie:
            self._inhibition.update(app=app, cookie=cookie)

    def _on_upload_failed(self, message):
        """Toast half of an upload failure. The round already counted it."""
        self._finish_posting()
        self._show_error(_('Could not upload media'), message)

    def send_status(self):
        params = {
            'visibility': VISIBILITIES[self.visibility_row.get_selected()][0],
            'language': self._languages[self.language_row.get_selected()][0],
        }
        for key in ('sensitive', 'quote_approval_policy'):
            if key in self._scheduled_fields:
                params[key] = self._scheduled_fields[key]
        if self.cw_toggle.get_active():
            cw_text = self.spoiler_entry.get_text()
            if cw_text:
                params['spoiler_text'] = cw_text
                params['sensitive'] = True
        elif _has_setting('default-sensitive') \
                and 'sensitive' not in params \
                and pages.settings().get_boolean('default-sensitive'):
            # The preference is mirrored to the account's server defaults,
            # but a client that leaves the field out inherits whatever the
            # server's web client happened to send last -- and Babble is
            # not it. So the setting that is already in Preferences
            # decides here too.
            params['sensitive'] = True
        if self.poll_toggle.get_active():
            options = [entry.get_text() for entry in self.poll_entries
                       if entry.get_text().strip()]
            params['poll'] = {
                'options': options,
                'expires_in': self._poll_durations[
                    self.poll_expiry.get_selected()][0],
            }
            params['poll'].update(self._scheduled_fields.get('poll_flags', {}))
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
            context = (self.editing_id, self.uploads.generation)
            self.client.edit_status_async(self.editing_id, self.text(),
                                          params=params,
                                          callback=lambda c, r:
                                          self.on_post_done(c, r, context))
        else:
            context = (None, self.uploads.generation)
            self.client.post_status_async(self.text(), params=params,
                                          callback=lambda c, r:
                                          self.on_post_done(c, r, context))

    def on_post_done(self, client, result, _user_data=None):
        editing_id, generation = _user_data
        try:
            if editing_id is not None:
                status = client.edit_status_finish(result)
            else:
                status = client.post_status_finish(result)
        except GLib.Error as e:
            if generation != self.uploads.generation:
                return
            self._finish_posting()
            self._show_error(_('Could not post'), e.message)
            return
        if generation != self.uploads.generation:
            return
        self.drop_saved_local_draft()
        if editing_id is not None:
            message = _('Post edited')
        elif self.scheduled_at is not None:
            message = _('Post scheduled')
        else:
            message = _('Post sent')
        if self.scheduled_id is not None:
            self._replacement = _ScheduledReplacement(
                self.scheduled_id, status, self.scheduled_on_done, message)
            self._finish_posting()
            self._retry_scheduled_removal()
            return status
        self._finish_posting()
        return self._complete_post(status, message)

    def _complete_post(self, status, message):
        if isinstance(status, dict):
            remember(self.client, status.get('tags'))
        # Resolve the toast target before the dialog closes.
        window = pages.window_of(self)
        root = self.get_root()
        was_edit = self.editing_id is not None
        was_scheduled = self.scheduled_id is not None
        # Still on the server, timed or as a draft; not an entry whose
        # time was cleared, which went out now. It is not a status yet,
        # so nothing can reply to it: it never chains a thread's next post.
        kept_scheduled = self.scheduled_at is not None
        if self.thread_toggle.get_active() and not was_edit \
                and not kept_scheduled and not was_scheduled:
            # Chain the next post in the thread; media and poll must not
            # be sent twice.
            self.in_reply_to_id = str(status.get('id') or '') or None
            self.buffer.set_text('')
            self.clear_attachments()
            self._pending_media_ids = []
            self.quoted_status_id = None
            self._scheduled_fields = {}
            self._set_attach_sensitive(True)
            self.poll_toggle.set_active(False)
            self.uploads.clear_results()
            self.update_compose_state()
        else:
            self.reset()
            dialog = self.get_ancestor(Adw.Dialog)
            if dialog is not None and dialog.get_parent() is not None:
                dialog.close()
        if window is not None:
            window.show_toast(message)
        elif root is not None and hasattr(root, 'show_toast'):
            root.show_toast(message)
        return status

    def _retry_scheduled_removal(self, *_args):
        transaction = self._replacement
        if transaction is None or self._cleanup_busy:
            return
        self._cleanup_busy = True
        self._cleanup_cancellable = Gio.Cancellable()
        if self._cleanup_banner is not None:
            self._cleanup_banner.set_sensitive(False)
        view_ref = self.weak_ref()
        client = self.client

        def finished(_source, result, _data=None):
            error = None
            try:
                client.request_finish(result)
            except GLib.Error as failure:
                if failure.domain != 'mastodon-http-error-quark' or failure.code != 404:
                    error = failure
            view = view_ref()
            if view is None or view._replacement is not transaction:
                return
            view._cleanup_busy = False
            view._cleanup_cancellable = None
            if error is not None:
                view._show_cleanup_warning()
                view._show_error(_('Could not remove original scheduled post'),
                                 error.message)
                return
            view._replacement = None
            if view._cleanup_banner is not None:
                view._cleanup_banner.set_revealed(False)
            view.set_posting_ui(False)
            if transaction.on_done is not None:
                transaction.on_done()
            view._complete_post(transaction.accepted, transaction.message)

        client.request_async(
            'DELETE', '/scheduled_statuses/%s' % transaction.original_id,
            callback=finished, cancellable=self._cleanup_cancellable)

    def _show_cleanup_warning(self):
        if self._cleanup_banner is None:
            self._cleanup_banner = Adw.Banner(
                use_markup=False,
                title=_('The replacement was saved, but the original scheduled '
                        'post could not be removed. Both may be published.'),
                button_label=_('_Retry Removal'))
            pages.connect_weak(self._cleanup_banner, 'button-clicked',
                               self._retry_scheduled_removal)
            self._toolbar_view.add_top_bar(self._cleanup_banner)
        self._cleanup_banner.set_sensitive(True)
        self._cleanup_banner.set_revealed(True)
        self.update_compose_state()

    def reset(self):
        if self._replacement is not None:
            return False
        predecessor = self._draft_stack[-1] if self._draft_stack else None
        self._draft_stack = []
        # Discarding the draft abandons any upload it started. The
        # generation bump is what tells a still-in-flight reply that it
        # belongs to a round that no longer exists; cancelling alone only
        # turns the request into an error the reply would still act on.
        self.uploads.abandon()
        # With its uploads gone, a posting round in flight is over.
        if self._posting:
            self._finish_posting()
        self.mention_completer.dismiss()
        self.hashtag_completer.dismiss()
        self.buffer.set_text('')
        self.spoiler_entry.set_text('')
        self.cw_toggle.set_active(False)
        self.poll_toggle.set_active(False)
        for entry in self.poll_entries:
            entry.set_text('')
        self.poll_expiry.set_selected(2)
        self.thread_toggle.set_active(False)
        self.clear_attachments()
        self._set_attach_sensitive(True)
        self._clear_submission_mode()
        # Back to the presets, so a DM or a reply's visibility does not
        # carry over; before the pop, so a stacked draft keeps its own.
        self._select_visibility(default_visibility())
        self._select_language(default_language())
        self.in_reply_to_id = None
        if predecessor is not None:
            self._restore_state(predecessor)
        self.update_compose_state()


class _ComposeWidth(Gtk.Widget):
    """Holds the composer at COMPOSE_WIDTH wide where the window allows
    it. A dialog that follows its content takes the natural width too,
    and the composer's own would change with the toolbar's labels; the
    minimum is left alone, so a narrow window still fits it."""

    __gtype_name__ = 'BabbleComposeWidth'

    def __init__(self, child):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = self.get_first_child().measure(
            orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(minimum, COMPOSE_WIDTH)
        return (minimum, natural, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        self.get_first_child().allocate(width, height, baseline, None)

    def do_dispose(self):
        while (child := self.get_first_child()) is not None:
            child.unparent()
        Gtk.Widget.do_dispose(self)


class ComposeDialog(Adw.Dialog):
    """Composer dialog. Keep one instance per client and present it
    repeatedly: closing keeps the draft."""

    __gtype_name__ = 'BabbleComposeDialog'

    def __init__(self, client, **kwargs):
        # The dialog follows the composer's size rather than a fixed
        # content size: no blank below the text area, and it grows and
        # shrinks with the warning, poll and attachments the toolbar
        # reveals. The width is pinned by _ComposeWidth.
        super().__init__(follows_content_size=True, **kwargs)
        self.view = BabbleComposeView(client)
        self.view._select_visibility(default_visibility())
        pages.connect_weak(self.view, 'changed', self._on_view_changed)
        pages.connect_weak(self, 'closed', self.view.save_local_draft)
        pages.connect_weak(self, 'close-attempt', self._on_close_attempt)
        self.set_child(_ComposeWidth(self.view))
        self.set_focus(self.view.text_view)
        self._on_view_changed(self.view)

    @property
    def client(self):
        return self.view.client

    def start_reply(self, status_id, handle, visibility='public'):
        return self.view.start_reply(status_id, handle, visibility)

    def start_mention(self, account, direct=False):
        return self.view.start_mention(account, direct=direct)

    def start_edit(self, status):
        return self.view.start_edit(status)

    def start_redraft(self, status):
        return self.view.start_redraft(status)

    def start_quote(self, status):
        return self.view.start_quote(status)

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        return self.view.start_scheduled(scheduled, draft=draft, on_done=on_done)

    def _on_view_changed(self, view):
        self.set_can_close(view._replacement is None)
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

    def _on_close_attempt(self, _dialog):
        if self.view._replacement is None or self.view._cleanup_busy:
            return
        dialog = Adw.AlertDialog(
            heading=_('Leave Duplicate Scheduled Posts?'),
            body=_('The replacement was saved, but the original is still '
                   'scheduled. Closing leaves both entries on the server.'))
        dialog.add_response('keep', _('_Keep Open'))
        dialog.add_response('leave', _('_Close Anyway'))
        dialog.set_response_appearance('leave', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('keep')
        dialog.set_close_response('keep')
        pages.connect_weak(dialog, 'response', self._on_cleanup_close_response)
        dialog.present(self)

    def _on_cleanup_close_response(self, _dialog, response):
        if response == 'leave' and not self.view._cleanup_busy:
            self.force_close()

    def abandon_session(self):
        view = self.view
        # Saved first, while the draft is still in the composer; it skips
        # an edit, a scheduled replacement and a post still on its way.
        view.save_local_draft()
        view.uploads.abandon()
        cancellable = view._cleanup_cancellable
        view._replacement = None
        view._cleanup_busy = False
        view._cleanup_cancellable = None
        if cancellable is not None:
            cancellable.cancel()
        view._draft_stack = []
        if view._cleanup_banner is not None:
            view._cleanup_banner.set_revealed(False)
        # The abandoned round never reaches _finish_posting, so the
        # logout inhibition is returned here; _posting stays set so the
        # closing dialog does not save the half-sent post as a draft.
        _end_inhibition(view._inhibition)
        view.set_posting_ui(view._posting)
        if self.get_parent() is not None:
            self.force_close()
