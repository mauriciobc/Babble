# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer dialog: new posts with attachments, visibility, content
warning, poll and language selection. Reply prefill goes through
:meth:`ComposeDialog.start_reply`; the previous draft is preserved across
the reply round trip and closing the dialog keeps the draft."""


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

from .i18n import gettext as _

from gi.repository import (Adw, Gdk, Gio, GLib, GObject, Gtk,  # noqa: E402
                           GtkSource, Spelling)

from . import emojichooser, pagenav, uihelpers  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402
from .composeattach import ComposeAttachments, _prune_pasted_once  # noqa: E402
from .composeschedule import (ComposeSchedule,  # noqa: E402
                              )
from .composeupload import UploadRound  # noqa: E402
from .composequote import QuotePaste  # noqa: E402
from .hashtagcompleter import HashtagCompleter  # noqa: E402
from .mentioncompleter import MentionCompleter  # noqa: E402
from .mastodon import summarize_error  # noqa: E402
# The same list Preferences offers as the default post language.
from .preferences import LANGUAGES  # noqa: E402
from .composedraft import ComposeDraft  # noqa: E402
from .composeoptions import (ComposeOptions, POLL_DURATIONS, VISIBILITIES,
    _language_subtag, default_language)  # noqa: E402
from .composepost import (ComposePost, _end_inhibition)  # noqa: E402
from .composethread import ComposeThread  # noqa: E402
# Spell checking (libspelling) works on a GtkSource.Buffer; that buffer
# is all the composer takes from GtkSourceView.
GtkSource.init()
Spelling.init()


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


def _make_dropdown(options, label):
    dropdown = Gtk.DropDown(model=Gtk.StringList.new(
        [text for _code, text in options]))
    # .flat on the DropDown is the documented way to do this; reaching
    # into its child to add the class again styled libadwaita's internals,
    # which is not ours to keep in step with.
    dropdown.add_css_class('flat')
    dropdown.set_valign(Gtk.Align.CENTER)
    uihelpers.set_accessible_label(dropdown, label)
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


class BabbleComposeView(QuotePaste, ComposeDraft, ComposeOptions,
                        ComposeThread, ComposePost, Adw.Bin):
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
        self._thread = None
        # Counts the changes of thread state; an answer from the server
        # applies only if none happened since its request.
        self._thread_generation = 0
        # One round per click of Post: abandoning the dialog or starting
        # a new post stops the uploads still in flight.
        self.uploads = UploadRound(
            client, self._on_upload_failed, self.send_status)
        # Set while the text view's own paste runs, re-emitted by
        # on_paste_clipboard when the clipboard holds nothing to attach.
        self._paste_through = False
        # Nonzero while the buffer is written programmatically, so an
        # inserted lone link is not turned into a quote then.
        self._in_programmatic_insert = 0
        # The position the paste is about to insert at, recorded
        # before the class handler starts its (asynchronous) clipboard
        # read. The insert handler pairs this with the insertion it
        # sees; anything else is not a paste and is left as text.
        self._pre_paste_offset = None
        self._paste_serial = 0
        # Bumped on every composer-state change (draft discarded, edit
        # started, quote cleared), so a link that resolved after the
        # composer moved on is dropped, not attached.
        self._quote_state_generation = 0
        _prune_pasted_once()

        toolbar_view = Adw.ToolbarView()
        self._toolbar_view = toolbar_view
        self._cleanup_banner = None
        toolbar_view.add_top_bar(self.build_header_bar())
        self.reply_banner = Adw.Banner(title=_('Replying to a post'),
                                       button_label=_('_Cancel Reply'))
        connect_weak(self.reply_banner, 'button-clicked',
                           self.on_reply_cancel)
        toolbar_view.add_top_bar(self.reply_banner)
        # Plain text: the title is a formatted date, not markup.
        self.schedule_banner = Adw.Banner(use_markup=False,
                                          button_label=_('_Clear Schedule'))
        toolbar_view.add_top_bar(self.schedule_banner)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)
        self._content = content
        content.append(self._build_preview())
        content.append(self.build_text_area())
        content.append(self.build_options())
        self.attachments_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                       spacing=12)
        content.append(self.attachments_box)
        # Lambdas so a test (or a later wrap) that replaces the view's
        # helpers is what the tray calls, not the method bound at build.
        self.attach = ComposeAttachments(
            client, self.attachments_box,
            on_changed=lambda: self.update_compose_state(),
            on_toast=lambda text: self._toast(text),
            on_error=lambda heading, body: self._show_error(heading, body),
            is_posting=lambda: self._posting,
            is_frozen=lambda: self._replacement is not None,
            on_add_toast=lambda toast: self._add_toast(toast),
            on_paste_text=lambda: self._paste_text())
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
        scrolled.add_controller(self.drop_target)
        toolbar_view.add_bottom_bar(self.build_action_bar())
        self.attach.bind(
            attach_button=self.attach_button,
            screenshot_button=self.screenshot_button,
            drop_target=self.drop_target,
            text_view=self.text_view)
        self.modes = ComposeSchedule(self)
        self.modes.bind(schedule_button=self.schedule_button,
                        schedule_banner=self.schedule_banner)
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

    def build_header_bar(self):
        header = Adw.HeaderBar()
        self.discard_button = Gtk.Button(icon_name='user-trash-symbolic')
        uihelpers.set_accessible_label(self.discard_button, _('Discard Draft'))
        connect_weak(self.discard_button, 'clicked',
                           self.on_discard_clicked)
        header.pack_start(self.discard_button)

        self.post_button = Gtk.Button(label=_('_Post'), use_underline=True)
        self.post_button.add_css_class('suggested-action')
        self.post_button.set_tooltip_text(_('Post (Ctrl+Enter)'))
        connect_weak(self.post_button, 'clicked', self.on_post_clicked)
        header.pack_end(self.post_button)
        self.progress_spinner = Adw.Spinner(visible=False)
        header.pack_end(self.progress_spinner)
        self.schedule_button = Gtk.Button(icon_name='alarm-symbolic')
        uihelpers.set_accessible_label(self.schedule_button, _('Schedule Post'))
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
        connect_weak(self.buffer, 'changed', self.on_draft_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=12, bottom_margin=12,
                                      left_margin=12, right_margin=12,
                                      accepts_tab=False)
        connect_weak(self.text_view, 'paste-clipboard',
                           self.on_paste_clipboard)
        # A lone link to a post inserted in one go -- a paste, or text
        # arriving from elsewhere -- is turned into a quote by the
        # buffer's own insert notification, so plain-text pastes go
        # through the default handler untouched.
        connect_weak(self.buffer, 'insert-text',
                           self._on_text_inserted)
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
        connect_weak(spell_click, 'pressed', self._on_spell_click)
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
            uihelpers.set_accessible_label(button, label)
            tools.append(button)
            return button

        self.attach_button = icon_button('mail-attachment-symbolic',
                                         _('Attach Media'))
        # Hidden until the screenshot portal is known to be there.
        self.screenshot_button = icon_button(
            'applets-screenshooter-symbolic', _('Take Screenshot'))
        self.screenshot_button.set_visible(False)
        self.cw_toggle = icon_button('dialog-warning-symbolic',
                                     _('Content Warning'), toggle=True)
        connect_weak(self.cw_toggle, 'toggled', self.on_cw_toggled)
        self.poll_toggle = icon_button('babble-compose-poll-symbolic',
                                       _('Poll'), toggle=True)
        connect_weak(self.poll_toggle, 'toggled', self.on_poll_toggled)
        self.mention_button = icon_button('contact-new-symbolic',
                                          _('Mention'))
        connect_weak(self.mention_button, 'clicked',
                           self.on_mention_clicked)
        self.emoji_button = icon_button('face-smile-symbolic',
                                        _('Insert Emoji'))
        connect_weak(self.emoji_button, 'clicked',
                           self.on_emoji_clicked)
        self.emoji_button.connect('destroy', _unparent_popovers)
        self.thread_toggle = icon_button('view-list-symbolic',
                                         _('Keep Open for a Thread'),
                                         toggle=True)
        connect_weak(self.thread_toggle, 'toggled',
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
        connect_weak(self.language_row, 'notify::selected',
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

    def clear_attachments(self):
        self.attach.clear()

    def attachments(self):
        return self.attach.rows()

    def attachment_limit(self):
        return self.attach.limit()

    def media_filter(self):
        return self.attach.media_filter()

    def add_attachment_path(self, path, alt=None, position=-1):
        return self.attach.add_path(path, alt=alt, position=position)

    def add_attachment_files(self, files):
        return self.attach.add_files(files)

    def _set_attach_sensitive(self, sensitive):
        self.attach.set_attach_sensitive(sensitive)

    def on_remove_attachment(self, button, row):
        self.attach.on_remove(button, row)

    def on_files_selected(self, dialog, result):
        self.attach.on_files_selected(dialog, result)

    def _finish_begin(self):
        self.update_compose_state()
        self.text_view.grab_focus()

    def start_reply(self, status_id, handle, visibility='public',
                    parent=None):
        return self.modes.start_reply(status_id, handle, visibility,
                                      parent=parent)

    def start_mention(self, account, direct=False):
        return self.modes.start_mention(account, direct=direct)

    def start_edit(self, status):
        return self.modes.start_edit(status)

    def start_redraft(self, status):
        return self.modes.start_redraft(status)

    def start_quote(self, status):
        return self.modes.start_quote(status)

    def start_scheduled(self, scheduled, draft=False, on_done=None):
        return self.modes.start_scheduled(scheduled, draft=draft,
                                          on_done=on_done)

    # Scheduling (thin forwards; logic lives in ComposeSchedule)

    def on_schedule_clicked(self, button):
        self.modes.on_schedule_clicked(button)

    def _on_schedule_picked(self, iso):
        self.modes._on_schedule_picked(iso)

    def on_schedule_clear(self, banner):
        self.modes.on_schedule_clear(banner)

    # Field events

    def _update_spell_language(self, *_args):
        """Check the post in the language picked for it, and not at all
        when there is no dictionary for that language: a post checked
        against another language's would come out all underlined. With
        no language picked, the default dictionary."""
        provider = Spelling.Provider.get_default()
        selected = self.language_row.get_selected()
        if selected < len(self._languages):
            languages = provider.list_languages()
            available = [language.get_code() for language in languages]
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
            connect_weak(self.emoji_chooser, 'emoji-picked',
                               self.on_custom_emoji_picked)
            connect_weak(self.emoji_chooser, 'text-picked',
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

    def on_paste_clipboard(self, text_view):
        """Pasting copied files attaches them, and pasting a bare image
        (a screenshot, an image copied from a browser) saves it and
        attaches it. Text, including a link to a post, goes through the
        default handler; the buffer-level insert watcher turns a lone
        post link into a quote."""
        if self._paste_through:
            return
        if self.attach.try_clipboard_media(text_view):
            text_view.stop_emission_by_name('paste-clipboard')
            return
        # The text paste is about to run through the default handler;
        # record where the cursor is so the insert watcher can pair it
        # with the paste's own insertion (which is asynchronous).
        # Pasting over a selection never matches this offset (the
        # selection is deleted before the insertion), so it stays a
        # plain paste -- the safe direction. If
        # the paste inserts nothing, the watchdog un-arms the offset so
        # a later insert cannot be mistaken for the paste.
        self._pre_paste_offset = self.buffer.get_iter_at_mark(
            self.buffer.get_insert()).get_offset()
        # A newer paste owns the offset: the watchdog of an older one
        # must not un-arm it, so each carries its serial. A second is
        # allowed for the clipboard read, which a loaded machine can
        # stretch; past that the paste stays plain text.
        self._paste_serial += 1
        GLib.timeout_add(1000, weak_method(
            self._clear_pre_paste_offset), self._paste_serial)

    def _paste_text(self):
        """The text view's own paste, which on_paste_clipboard lets
        through."""
        self._paste_through = True
        try:
            self.text_view.emit('paste-clipboard')
        finally:
            self._paste_through = False

    def _set_buffer(self, text):
        """Write *text* into the buffer without the quote watcher:
        programmatic text is not a paste, so a lone link in a restored
        draft must not become a quote."""
        self._in_programmatic_insert += 1
        try:
            self.buffer.set_text(text)
        finally:
            self._in_programmatic_insert -= 1

    def _add_toast(self, toast):
        """Toasts raised inside the composer go on its own overlay: the
        dialog covers the window's, where they would be invisible and
        unusable. Both fallbacks exist -- feedback.show_toast reaches for the
        window when a widget is not under an overlay, and this same
        lookup is what it would do."""
        overlay = self.get_ancestor(Adw.ToastOverlay)
        if overlay is None:
            # The composer's own overlay used to be its child, which
            # get_ancestor cannot reach: the ancestor lookup always
            # missed and toasts fell through to the covered window
            # overlay. get_ancestor finds a dialog's overlay when one
            # holds us; get_child finds the one we put inside ourselves.
            child = self.get_child()
            if isinstance(child, Adw.ToastOverlay):
                overlay = child
        if overlay is not None:
            overlay.add_toast(toast)
            return
        window = pagenav.window_of(self)
        if window is not None:
            window.add_toast(toast)

    def _toast(self, text):
        # Toasts raised inside the composer go on its own overlay: the
        # dialog covers the window's, where they would be invisible. The
        # composer is a dialog, so feedback.show_toast's window_of() lands on
        # the active window rather than on this dialog -- which is the one
        # case where the lookup has to be the composer's own.
        self._add_toast(Adw.Toast(title=text, use_markup=False))

    def _show_error(self, heading, body):
        # The same '<heading>: <message>' feedback.report_error builds, over
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
