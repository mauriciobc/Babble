# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer options: characters, poll, visibility, language, limits.

The rules that decide what a status may be -- how long it is, how many
poll options and of what length, which visibility, which language -- and
the widgets that pick them. A mixin of ``BabbleComposeView``, so the
composer keeps one object.

The constants live here because these are their only readers in
production: ``source`` and ``compose.py`` import the ones it still names
(``POLL_DURATIONS``, ``VISIBILITIES``, ``default_language``).
"""
import os
import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from . import appsettings, uihelpers  # noqa: E402
from .drafts import Poll  # noqa: E402
from .preferences import LANGUAGES  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



DEFAULT_CHAR_LIMIT = 500

URL_PLACEHOLDER_CHARS = 23

MAX_POLL_CHOICES = 4

SCHEMA_ID = 'io.github.mauriciobc.Babble'

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

DEFAULT_POLL_DURATION = 86400

def duration_label(seconds):
    """A poll duration in the largest unit that divides it."""
    for unit, label in ((86400, ngettext('%d day', '%d days', seconds // 86400)),
                        (3600, ngettext('%d hour', '%d hours', seconds // 3600)),
                        (60, ngettext('%d minute', '%d minutes', seconds // 60))):
        if seconds % unit == 0:
            return label % (seconds // unit)
    return ngettext('%d second', '%d seconds', seconds) % seconds

def poll_duration_choices(limits):
    """``[(seconds, label)]`` the poll duration row offers: the fixed
    list narrowed to ``[min_expiration, max_expiration]`` of the instance
    (docs.joinmastodon.org/entities/Instance/#polls; either may be None).
    When none of the fixed choices fits, the bounds themselves."""
    low = limits.get('min_expiration')
    high = limits.get('max_expiration')
    choices = [(seconds, label) for seconds, label in POLL_DURATIONS
               if (low is None or seconds >= low)
               and (high is None or seconds <= high)]
    if choices:
        return choices
    return [(seconds, duration_label(seconds))
            for seconds in sorted({low, high} - {None})]

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
    return appsettings.settings().get_string(key)

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


class ComposeOptions:
    @property
    def char_limit(self):
        """Post limit of the instance, read live: the composer dialog is
        cached per client, so the value can arrive after it is built."""
        max_characters = getattr(self.client, 'max_characters', None)
        if max_characters is None:
            return DEFAULT_CHAR_LIMIT
        return max_characters() or DEFAULT_CHAR_LIMIT

    def build_options(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.spoiler_entry = Adw.EntryRow(title=_('Content Warning'))
        connect_weak(self.spoiler_entry, 'changed',
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
        self._poll_base = list(self._poll_durations)
        self._select_default_poll_duration()
        self._poll_box.append(self.poll_expiry)
        self.poll_add = Adw.ButtonRow(title=_('_Add Choice'),
                                      use_underline=True,
                                      start_icon_name='list-add-symbolic')
        connect_weak(self.poll_add, 'activated',
                           self.on_poll_add_choice)
        self._poll_box.append(self.poll_add)
        self._apply_poll_limits()
        for _n in range(2):
            self.on_poll_add_choice(self.poll_add)
        self._poll_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            child=self._poll_box)
        box.append(self._poll_revealer)
        return box

    def poll_state(self):
        return Poll(active=self.poll_toggle.get_active(),
                    options=[entry.get_text()
                             for entry in self.poll_entries],
                    expires=self.poll_expiry.get_selected(),
                    duration_seconds=self._poll_durations[self.poll_expiry.get_selected()][0],
                    **self._scheduled_fields.get('poll_flags', {}))

    def poll_limits(self):
        """The instance's poll limits, read live like :attr:`char_limit`
        (the cached dialog can be built before the document arrives)."""
        reader = getattr(self.client, 'poll_limits', None)
        if reader is not None:
            return reader()
        return {'max_options': MAX_POLL_CHOICES,
                'max_characters_per_option': None,
                'min_expiration': None, 'max_expiration': None}

    def _apply_poll_limits(self):
        """Bring the option rows and the duration row in line with the
        instance's limits: option text length, the add button, and the
        offered durations."""
        limits = self.poll_limits()
        self._poll_max = limits['max_options']
        longest = limits['max_characters_per_option'] or 0
        for entry in self.poll_entries:
            Gtk.Editable.get_delegate(entry).set_max_length(longest)
        self.poll_add.set_visible(len(self.poll_entries) < self._poll_max)
        base = poll_duration_choices(limits)
        if base == self._poll_base:
            return
        current = self._poll_durations[self.poll_expiry.get_selected()][0] \
            if self._poll_durations and self.poll_expiry.get_selected() \
            < len(self._poll_durations) else None
        # Durations restored from a saved post stay offered.
        extras = [item for item in self._poll_durations
                  if item not in self._poll_base]
        self._poll_base = base
        self._poll_durations = base + extras
        self.poll_expiry.set_model(Gtk.StringList.new(
            [text for _s, text in self._poll_durations]))
        values = [seconds for seconds, _l in self._poll_durations]
        if current in values:
            self.poll_expiry.set_selected(values.index(current))
        else:
            self._select_default_poll_duration()

    def _select_default_poll_duration(self):
        """A day when offered, else the offered duration nearest to it."""
        values = [seconds for seconds, _l in self._poll_durations]
        if values:
            best = min(values, key=lambda v: abs(v - DEFAULT_POLL_DURATION))
            self.poll_expiry.set_selected(values.index(best))

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

    def _select_visibility(self, visibility):
        codes = [code for code, _label in VISIBILITIES]
        if visibility in codes:
            self.visibility_row.set_selected(codes.index(visibility))

    def on_cw_toggled(self, toggle):
        uihelpers.reveal(self._spoiler_revealer, toggle.get_active(),
                     animate=self.get_mapped())
        if toggle.get_active():
            self.spoiler_entry.grab_focus()
        else:
            self.spoiler_entry.set_text('')

    def on_poll_toggled(self, toggle):
        self._apply_poll_limits()
        uihelpers.reveal(self._poll_revealer, toggle.get_active(),
                     animate=self.get_mapped())
        if toggle.get_active():
            self.poll_entries[0].grab_focus()
        else:
            self._scheduled_fields.pop('poll_flags', None)
            for entry in self.poll_entries:
                entry.set_text('')

    def on_poll_add_choice(self, _row):
        self._apply_poll_limits()
        if len(self.poll_entries) >= self._poll_max:
            return
        entry = Adw.EntryRow(title=_('Choice %d')
                             % (len(self.poll_entries) + 1))
        connect_weak(entry, 'changed', self.on_draft_changed)
        self._poll_box.insert(entry, len(self.poll_entries))
        self.poll_entries.append(entry)
        self._apply_poll_limits()

    def update_compose_state(self):
        text = self.text()
        self.placeholder.set_visible(not text)
        self.reply_banner.set_revealed(bool(self._draft_stack))
        preview = self._thread.preview if self._thread is not None else ''
        self.preview_label.set_label(preview)
        self.preview_label.set_visible(bool(preview))
        self.modes.update_chrome()
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

    def counted_length(self):
        """Characters Mastodon will count for the current draft."""
        return countable_length(
            self.text(),
            self.spoiler_entry.get_text() if self.cw_toggle.get_active()
            else '',
            self._url_chars())

    def _url_chars(self):
        reader = getattr(self.client, 'characters_reserved_per_url', None)
        return reader() if reader is not None else URL_PLACEHOLDER_CHARS
