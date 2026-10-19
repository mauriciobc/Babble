# SPDX-License-Identifier: GPL-3.0-or-later

"""Edit-your-profile dialog: PATCH /api/v1/accounts/update_credentials
(display name, bio, avatar, header, fields, privacy flags and posting
defaults) plus PATCH /api/v1/profile for the profile-tab switches and
the avatar/header alt text newer Mastodon servers (4.6+) offer."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gdk, GLib, Gio, GObject, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error  # noqa: E402

DEFAULT_LIMITS = {
    'max_display_name_length': 30,
    'max_note_length': 500,
    'max_avatar_description_length': 150,
    'max_header_description_length': 150,
    'max_profile_fields': 4,
    'profile_field_name_limit': 255,
    'profile_field_value_limit': 255,
}

IMAGE_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')

# API versions of the pieces of this dialog that do not travel in
# update_credentials: the profile-tab switches through PATCH
# /api/v1/profile (Mastodon 4.6.0, `mastodon` API version 8) and the
# avatar/header alt text through the same route (4.6.0, version 9).
from .capabilities import (PROFILE_ALT_TEXT_API_VERSION as PROFILE_ALT_TEXT_VERSION,
                           PROFILE_TABS_API_VERSION as PROFILE_TABS_VERSION)


VISIBILITIES = (
    ('public', _('Public')),
    ('unlisted', _('Quiet public')),
    ('private', _('Followers only')),
    ('direct', _('Mentioned people only')),
)

LANGUAGES = (
    ('', _('Not set')),
    ('en', 'English'), ('pt', 'Português'), ('es', 'Español'),
    ('fr', 'Français'), ('de', 'Deutsch'), ('it', 'Italiano'),
    ('nl', 'Nederlands'), ('ca', 'Català'), ('gl', 'Galego'),
    ('eu', 'Euskara'), ('pl', 'Polski'), ('cs', 'Čeština'),
    ('sv', 'Svenska'), ('da', 'Dansk'), ('nb', 'Norsk bokmål'),
    ('fi', 'Suomi'), ('ru', 'Русский'), ('uk', 'Українська'),
    ('tr', 'Türkçe'), ('el', 'Ελληνικά'), ('he', 'עברית'),
    ('ar', 'العربية'), ('fa', 'فارسی'), ('hi', 'हिन्दी'),
    ('zh', '中文'), ('ja', '日本語'), ('ko', '한국어'),
)

# (api key, row title, row subtitle) of the boolean profile switches.
FLAGS = (
    ('locked', _('Require Follow Requests'),
     _('Manually approve who can follow you')),
    ('bot', _('Automated Account'),
     _('Mark this account as mainly performing automated actions')),
    ('discoverable', _('Suggest Account to Others'),
     _('Let your profile appear in discovery features and be featured in '
       'Collections')),
    ('indexable', _('Include Public Posts in Search Results'),
     _('Let search engines and others find your public posts')),
    ('hide_collections', _('Hide Followers and Following'),
     _('Do not show who you follow and who follows you')),
)

# Profile-tab switches, saved through PATCH /api/v1/profile.
TAB_FLAGS = (
    ('show_featured', _('Show Featured Tab')),
    ('show_media', _('Show Media Tab')),
    ('show_media_replies', _('Include Replies in Media Tab')),
)


def account_limits(client):
    """Profile limits from the instance configuration, with the stock
    Mastodon values where the server does not say."""
    limits = dict(DEFAULT_LIMITS)
    configured = (((client.instance_info or {}).get('configuration') or {})
                  .get('accounts') or {})
    limits.update({key: value for key, value in configured.items()
                   if key in limits and isinstance(value, int)})
    return limits


def present_profile_edit(parent, client, account, on_saved=None):
    """Open the edit dialog for the signed-in *account* over *parent*.

    The current values come fresh from ``/accounts/verify_credentials``
    (``source`` carries the raw bio and fields). *on_saved(account)* gets
    the updated account once the server accepted the changes, after the
    window's ``on_account_updated`` hook ran."""

    def build(fresh):
        dialog = ProfileEditDialog(client, fresh, parent, on_saved)
        dialog.present(parent)

    def with_limits(fresh):
        if client.instance_info is not None:
            build(fresh)
            return

        def info_loaded(c, result, _data=None):
            try:
                c.instance_info_finish(result)
            except GLib.Error:
                pass
            build(fresh)

        client.instance_info_async(info_loaded)

    def credentials_loaded(c, result, _data=None):
        try:
            fresh = c.verify_credentials_finish(result)
        except GLib.Error as error:
            pages.report_error(parent, _('Could not load your profile'),
                               error)
            return
        with_limits(fresh)

    client.verify_credentials_async(credentials_loaded)


class FieldRow(Adw.ExpanderRow):
    """One profile field: label and content entries. Emits
    ``remove-requested`` and ``field-changed``: callbacks into the
    dialog stored on the row would pin the dialog that owns it."""

    __gsignals__ = {
        'remove-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'field-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, name, value, index, limits):
        super().__init__(expanded=bool(name or value), use_markup=False)
        self._limits = limits
        self.name_row = Adw.EntryRow(title=_('Label'), text=name or '')
        self.value_row = Adw.EntryRow(title=_('Content'), text=value or '')
        self.add_row(self.name_row)
        self.add_row(self.value_row)
        remove = pages.icon_button('user-trash-symbolic', _('Remove Field'))
        pages.connect_weak(remove, 'clicked', self._on_remove_clicked)
        self.add_suffix(remove)
        self.set_index(index)
        pages.connect_weak(self.name_row, 'changed', self._changed)
        pages.connect_weak(self.value_row, 'changed', self._changed)
        self._update()

    def set_index(self, index):
        self._index = index
        self._update()

    def _on_remove_clicked(self, _button):
        self.emit('remove-requested')

    def _changed(self, _row):
        self._update()
        self.emit('field-changed')

    def _update(self):
        self.set_title(self.name_row.get_text()
                       or _('Field %d') % (self._index + 1))
        self.set_subtitle(self.value_row.get_text())
        if self.valid:
            self.remove_css_class('error')
        else:
            self.add_css_class('error')

    @property
    def key(self):
        return self.name_row.get_text()

    @property
    def value(self):
        return self.value_row.get_text()

    @property
    def valid(self):
        if not self.key and not self.value:
            return True
        return (bool(self.key) and bool(self.value)
                and len(self.key) <= self._limits['profile_field_name_limit']
                and len(self.value)
                <= self._limits['profile_field_value_limit'])


class ProfileEditDialog(pages.ToastDialog):
    """Preferences-style editor for the signed-in account."""

    __gtype_name__ = 'BabbleProfileEditDialog'

    def __init__(self, client, account, parent, on_saved=None, **kwargs):
        super().__init__(_('Edit Profile'), 560, 760,
                         title_buttons=False, **kwargs)
        self.client = client
        self.account = account
        self.source = account.get('source') or {}
        self.parent_widget = parent
        self.on_saved = on_saved
        self.limits = account_limits(client)
        self.new_avatar = None
        self.new_header = None
        self.fields = []
        self._saving = False
        self._discarding = False
        self._profile_form = {}

        page = self.page
        self._build_images(page)
        self._build_about(page)
        self._build_fields(page)
        self._build_privacy(page)
        self._build_defaults(page)
        self._build_tabs(page)

        header = self.header
        self.cancel_button = Gtk.Button(label=_('_Cancel'),
                                        use_underline=True)
        pages.connect_weak(self.cancel_button, 'clicked',
                           self._on_cancel_clicked)
        header.pack_start(self.cancel_button)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        pages.connect_weak(self.save_button, 'clicked', self.on_save_clicked)
        header.pack_end(self.save_button)

        self.connect('close-attempt', self.on_close_attempt)
        self.update_state()

    # Images

    def _build_images(self, page):
        group = Adw.PreferencesGroup(title=_('Images'))
        overlay = Gtk.Overlay()
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.set_child(hero)
        banner = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
        banner.add_css_class('profile-banner')
        backdrop = Gtk.Box()
        backdrop.set_size_request(-1, 110)
        backdrop.add_css_class('profile-banner-fallback')
        banner.set_child(backdrop)
        self.header_picture = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER, can_shrink=True,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        banner.add_overlay(self.header_picture)
        hero.append(banner)
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 36)
        hero.append(spacer)
        self.avatar = Adw.Avatar(size=72, show_initials=True,
                                 halign=Gtk.Align.START,
                                 valign=Gtk.Align.START,
                                 text=self.account.get('display_name')
                                 or self.account.get('acct', '?'))
        self.avatar.set_margin_start(16)
        self.avatar.set_margin_top(110 - 36)
        overlay.add_overlay(self.avatar)
        group.add(overlay)

        from .profile import HeaderCache
        from .mediacache import AvatarCache
        header_url = self.account.get('header')
        if header_url and not header_url.endswith('/missing.png'):
            HeaderCache.apply(self.header_picture, header_url)
        AvatarCache.load(self.avatar, self.account)

        listbox = Adw.PreferencesGroup()
        self.avatar_row = Adw.ActionRow(title=_('Avatar'),
                                        subtitle=_('PNG, JPEG, GIF or WebP'),
                                        use_markup=False)
        self.avatar_row.add_suffix(self._choose_button(False))
        listbox.add(self.avatar_row)
        self.avatar_alt = None
        if self.client.is_supported(PROFILE_ALT_TEXT_VERSION) \
                and 'avatar_description' in self.account:
            self.avatar_alt = Adw.EntryRow(
                title=_('Avatar Description'),
                text=self.account.get('avatar_description') or '')
            pages.connect_weak(self.avatar_alt, 'changed', self.on_changed)
            listbox.add(self.avatar_alt)
        self.header_row = Adw.ActionRow(title=_('Header Image'),
                                        subtitle=_('PNG, JPEG, GIF or WebP'),
                                        use_markup=False)
        self.header_row.add_suffix(self._choose_button(True))
        listbox.add(self.header_row)
        self.header_alt = None
        if self.client.is_supported(PROFILE_ALT_TEXT_VERSION) \
                and 'header_description' in self.account:
            self.header_alt = Adw.EntryRow(
                title=_('Header Description'),
                text=self.account.get('header_description') or '')
            pages.connect_weak(self.header_alt, 'changed', self.on_changed)
            listbox.add(self.header_alt)
        page.add(group)
        page.add(listbox)

    def _choose_button(self, for_header):
        button = Gtk.Button(label=_('Choose…'), valign=Gtk.Align.CENTER)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Choose Header Image') if for_header
             else _('Choose Avatar')])
        pages.connect_weak(button, 'clicked', self.on_choose_clicked,
                           for_header)
        return button

    def on_choose_clicked(self, _button, for_header):
        image_filter = Gtk.FileFilter(name=_('Images'))
        for mime in IMAGE_TYPES:
            image_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        chooser = Gtk.FileDialog(
            title=_('Choose Header Image') if for_header
            else _('Choose Avatar'),
            modal=True, filters=filters, default_filter=image_filter)
        chooser.open(self.get_root(), None, self.on_file_chosen, for_header)

    def on_file_chosen(self, chooser, result, for_header):
        try:
            file = chooser.open_finish(result)
        except GLib.Error:
            return  # dismissed
        file.load_contents_async(None, self.on_file_loaded,
                                 (file, for_header))

    def on_file_loaded(self, file, result, data):
        _file, for_header = data
        try:
            _ok, contents, _etag = file.load_contents_finish(result)
            info = file.query_info('standard::content-type', 0, None)
            mime = info.get_content_type() or 'application/octet-stream'
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(contents))
        except GLib.Error as error:
            self.toast(_('Could not open image: %s') % error.message)
            return
        self.set_image(for_header, bytes(contents), file.get_basename(),
                       mime, texture)

    def set_image(self, for_header, contents, filename, mime, texture):
        """Stage a new avatar/header: shown in the preview, uploaded on
        Save."""
        if for_header:
            self.new_header = (contents, filename, mime)
            self.header_picture.set_paintable(texture)
            self.header_row.set_subtitle(filename)
        else:
            self.new_avatar = (contents, filename, mime)
            self.avatar.set_custom_image(texture)
            self.avatar_row.set_subtitle(filename)
        self.update_state()

    # About, fields, privacy, defaults, tabs

    def _build_about(self, page):
        group = Adw.PreferencesGroup(title=_('About'))
        self.name_row = Adw.EntryRow(
            title=_('Display Name'),
            text=self.account.get('display_name') or '')
        pages.connect_weak(self.name_row, 'changed', self.on_changed)
        group.add(self.name_row)
        page.add(group)

        bio_group = Adw.PreferencesGroup(title=_('Bio'))
        self.bio_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
            top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.bio_view.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_('Bio')])
        self.bio_buffer = self.bio_view.get_buffer()
        self.bio_buffer.set_text(self.source.get('note') or '', -1)
        pages.connect_weak(self.bio_buffer, 'changed', self.on_changed)
        scrolled = Gtk.ScrolledWindow(
            child=self.bio_view, min_content_height=110,
            max_content_height=240, propagate_natural_height=True)
        scrolled.add_css_class('card')
        self.bio_counter = Gtk.Label(xalign=1.0)
        self.bio_counter.add_css_class('caption')
        self.bio_counter.add_css_class('numeric')
        self.bio_counter.add_css_class('dimmed')
        bio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bio_box.append(scrolled)
        bio_box.append(self.bio_counter)
        bio_group.add(bio_box)
        page.add(bio_group)

    def _build_fields(self, page):
        self.fields_group = Adw.PreferencesGroup(
            title=_('Profile Fields'),
            description=ngettext(
                'Up to %d field shown on your profile. Links to sites '
                'that link back to you are marked as verified.',
                'Up to %d fields shown on your profile. Links to sites '
                'that link back to you are marked as verified.',
                self.limits['max_profile_fields'])
            % self.limits['max_profile_fields'])
        self.add_field_row = Adw.ButtonRow(title=_('Add Field'),
                                           start_icon_name='list-add-symbolic')
        pages.connect_weak(self.add_field_row, 'activated',
                           self._on_add_field_activated)
        self.fields_group.add(self.add_field_row)
        for field in self.source.get('fields') or []:
            self.add_field(field.get('name', ''), field.get('value', ''),
                           track=False)
        self.update_add_field_row()
        page.add(self.fields_group)

    def add_field(self, name, value, track=True):
        if len(self.fields) >= self.limits['max_profile_fields']:
            return None
        row = FieldRow(name, value, len(self.fields), self.limits)
        pages.connect_weak(row, 'remove-requested', self.remove_field)
        pages.connect_weak(row, 'field-changed', self.on_changed)
        self.fields.append(row)
        self.fields_group.remove(self.add_field_row)
        self.fields_group.add(row)
        self.fields_group.add(self.add_field_row)
        self.update_add_field_row()
        if track:
            self.update_state()
        return row

    def remove_field(self, row):
        self.fields.remove(row)
        self.fields_group.remove(row)
        for index, field in enumerate(self.fields):
            field.set_index(index)
        self.update_add_field_row()
        self.update_state()

    def update_add_field_row(self):
        self.add_field_row.set_visible(
            len(self.fields) < self.limits['max_profile_fields'])

    def _flag_value(self, key):
        if key in self.source:
            return bool(self.source[key])
        return bool(self.account.get(key))

    def _build_privacy(self, page):
        group = Adw.PreferencesGroup(title=_('Privacy and Discovery'))
        self.flag_rows = {}
        for key, title, subtitle in FLAGS:
            if key not in self.account and key not in self.source:
                continue
            row = Adw.SwitchRow(title=title, subtitle=subtitle,
                                active=self._flag_value(key))
            pages.connect_weak(row, 'notify::active', self.on_changed)
            self.flag_rows[key] = row
            group.add(row)
        if self.flag_rows:
            page.add(group)

    def _build_defaults(self, page):
        group = Adw.PreferencesGroup(title=_('Posting Defaults'))
        self.privacy_row = Adw.ComboRow(
            title=_('Default Visibility'),
            model=Gtk.StringList.new([label for _c, label in VISIBILITIES]))
        codes = [code for code, _l in VISIBILITIES]
        privacy = self.source.get('privacy') or 'public'
        self.privacy_row.set_selected(codes.index(privacy)
                                      if privacy in codes else 0)
        pages.connect_weak(self.privacy_row, 'notify::selected',
                           self.on_changed)
        group.add(self.privacy_row)

        self.sensitive_row = Adw.SwitchRow(
            title=_('Mark Media as Sensitive'),
            active=bool(self.source.get('sensitive')))
        pages.connect_weak(self.sensitive_row, 'notify::active',
                           self.on_changed)
        group.add(self.sensitive_row)

        self.languages = list(LANGUAGES)
        current = self.source.get('language') or ''
        if current not in [code for code, _n in self.languages]:
            self.languages.append((current, current))
        self.language_row = Adw.ComboRow(
            title=_('Default Language'),
            model=Gtk.StringList.new([name for _c, name in self.languages]))
        self.language_row.set_selected(
            [code for code, _n in self.languages].index(current))
        pages.connect_weak(self.language_row, 'notify::selected',
                           self.on_changed)
        group.add(self.language_row)
        page.add(group)

    def _build_tabs(self, page):
        self.tab_rows = {}
        # PATCH /api/v1/profile arrived with Mastodon 4.6 (API version
        # 8). Asking an older server for it is a 404, so the switches
        # stay out of the dialog and nothing is sent. A server that says
        # nothing about its version keeps the same answer, by the policy
        # `is_supported` defaults to: an unproven server is not evidence
        # that it has the route, which is also how the Collections
        # switch reads.
        if not self.client.is_supported(PROFILE_TABS_VERSION):
            return
        if not any(key in self.account for key, _t in TAB_FLAGS):
            return
        group = Adw.PreferencesGroup(title=_('Profile Tabs'))
        for key, title in TAB_FLAGS:
            if key not in self.account:
                continue
            row = Adw.SwitchRow(title=title,
                                active=bool(self.account.get(key)))
            pages.connect_weak(row, 'notify::active', self.on_changed)
            self.tab_rows[key] = row
            group.add(row)
        media = self.tab_rows.get('show_media')
        replies = self.tab_rows.get('show_media_replies')
        if media is not None and replies is not None:
            media.bind_property('active', replies, 'sensitive',
                                GLib.BindingFlags.SYNC_CREATE)
        page.add(group)

    # Change tracking

    def bio_text(self):
        start, end = self.bio_buffer.get_bounds()
        return self.bio_buffer.get_text(start, end, False)

    def collect(self):
        """``(fields, files, profile_form)``: the multipart form fields
        and files for update_credentials (only what differs from the
        server's values), and the form for PATCH /profile (the profile
        tabs and the avatar/header alt text, which that route carries
        from Mastodon 4.6)."""
        account, source = self.account, self.source
        fields, files = {}, {}
        profile = {}

        def set_if_changed(key, new, old):
            if new != old:
                fields[key] = new

        set_if_changed('display_name', self.name_row.get_text(),
                       account.get('display_name') or '')
        set_if_changed('note', self.bio_text(), source.get('note') or '')
        if self.avatar_alt is not None:
            value = self.avatar_alt.get_text()
            if value != (account.get('avatar_description') or ''):
                profile['avatar_description'] = value
        if self.header_alt is not None:
            value = self.header_alt.get_text()
            if value != (account.get('header_description') or ''):
                profile['header_description'] = value
        for key, row in self.flag_rows.items():
            if row.get_active() != self._flag_value(key):
                fields[key] = 'true' if row.get_active() else 'false'

        old_fields = [(f.get('name', ''), f.get('value', ''))
                      for f in source.get('fields') or []]
        new_fields = [(f.key, f.value) for f in self.fields
                      if f.key or f.value]
        if new_fields != old_fields:
            # Fields are replaced as a whole; an empty list still has to
            # name index 0 so the server clears what was there.
            for index, (name, value) in enumerate(new_fields or [('', '')]):
                fields['fields_attributes[%d][name]' % index] = name
                fields['fields_attributes[%d][value]' % index] = value

        privacy = VISIBILITIES[self.privacy_row.get_selected()][0]
        set_if_changed('source[privacy]', privacy,
                       source.get('privacy') or 'public')
        sensitive = self.sensitive_row.get_active()
        if sensitive != bool(source.get('sensitive')):
            fields['source[sensitive]'] = 'true' if sensitive else 'false'
        language = self.languages[self.language_row.get_selected()][0]
        set_if_changed('source[language]', language,
                       source.get('language') or '')

        if self.new_avatar is not None:
            files['avatar'] = self.new_avatar
        if self.new_header is not None:
            files['header'] = self.new_header

        for key, row in self.tab_rows.items():
            if row.get_active() != bool(account.get(key)):
                profile[key] = 'true' if row.get_active() else 'false'
        return fields, files, profile

    def validate(self):
        """True when every length limit holds."""
        limits = self.limits
        self.bio_counter.set_label(str(
            limits['max_note_length'] - self.bio_buffer.get_char_count()))
        checks = [
            (self.name_row, len(self.name_row.get_text())
             <= limits['max_display_name_length']),
            (self.bio_counter, self.bio_buffer.get_char_count()
             <= limits['max_note_length']),
        ]
        if self.avatar_alt is not None:
            checks.append((self.avatar_alt, len(self.avatar_alt.get_text())
                           <= limits['max_avatar_description_length']))
        if self.header_alt is not None:
            checks.append((self.header_alt, len(self.header_alt.get_text())
                           <= limits['max_header_description_length']))
        valid = True
        for widget, ok in checks:
            if ok:
                widget.remove_css_class('error')
            else:
                widget.add_css_class('error')
                valid = False
        return valid and all(field.valid for field in self.fields)

    def has_changes(self):
        fields, files, profile_form = self.collect()
        return bool(fields or files or profile_form)

    def _on_cancel_clicked(self, _button):
        self.close()

    def _on_add_field_activated(self, _row):
        self.add_field('', '')

    def on_changed(self, *_args):
        self.update_state()

    def update_state(self):
        valid = self.validate()
        dirty = self.has_changes()
        self.save_button.set_sensitive(valid and dirty and not self._saving)
        self.set_can_close(not dirty or self._discarding)

    # Closing

    def on_close_attempt(self, _dialog):
        dialog = Adw.AlertDialog(
            heading=_('Discard Changes?'),
            body=_('Your edits to the profile have not been saved.'))
        dialog.add_response('keep', _('_Keep Editing'))
        dialog.add_response('discard', _('_Discard'))
        dialog.set_response_appearance('discard',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('keep')
        dialog.set_close_response('keep')
        dialog.connect('response', self.on_discard_response)
        dialog.present(self)

    def on_discard_response(self, _dialog, response):
        if response == 'discard':
            self._discarding = True
            self.force_close()

    # Saving

    def set_saving(self, saving):
        self._saving = saving
        self.toasts.set_sensitive(not saving)
        self.cancel_button.set_sensitive(not saving)
        self.update_state()

    def on_save_clicked(self, _button):
        if not self.validate():
            return
        fields, files, profile_form = self.collect()
        if not (fields or files or profile_form):
            self.force_close()
            return
        self.set_saving(True)
        self._profile_form = profile_form
        if fields or files:
            self.client.multipart_async(
                'PATCH', '/accounts/update_credentials', fields=fields,
                files=files, callback=self.on_credentials_saved)
        else:
            self.save_profile()

    def _finish_saving(self, client, result):
        """True on success; failure toasts and stops saving."""
        try:
            client.request_finish(result)
        except GLib.Error as error:
            self.save_failed(error)
            return False
        return True

    def on_credentials_saved(self, client, result, _data=None):
        if self._finish_saving(client, result):
            self.save_profile()

    def save_profile(self):
        if not self._profile_form:
            self.reload()
            return
        self.client.request_async('PATCH', '/profile',
                                  form=self._profile_form,
                                  callback=self.on_profile_saved)

    def on_profile_saved(self, client, result, _data=None):
        if self._finish_saving(client, result):
            self.reload()

    def reload(self):
        self.client.verify_credentials_async(self.on_reloaded)

    def on_reloaded(self, client, result, _data=None):
        try:
            account = client.verify_credentials_finish(result)
        except GLib.Error as error:
            self.save_failed(error)
            return
        window = pages.window_of(self.parent_widget)
        hook = getattr(window, 'on_account_updated', None)
        if hook is not None:
            hook(account)
        elif isinstance(getattr(window, 'account', None), dict):
            window.account.update(account)
        if self.on_saved is not None:
            self.on_saved(account)
        self._discarding = True
        self.force_close()
        pages.show_toast(self.parent_widget, _('Profile updated'))

    def save_failed(self, error):
        self.set_saving(False)
        self.toast(_('Could not save profile: %s')
                   % summarize_error(error))
