# SPDX-License-Identifier: GPL-3.0-or-later

"""Preferences dialog (Tuba parity) and the live application of the
appearance settings.

Every row writes straight to GSettings; the window listens for the
changes and applies them without a restart (``apply_style``).
"""

import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, GObject, Gio, Gtk  # noqa: E402

from . import pages  # noqa: E402


COLOR_SCHEMES = (
    ('system', _('System'), Adw.ColorScheme.DEFAULT),
    ('light', _('Light'), Adw.ColorScheme.FORCE_LIGHT),
    ('dark', _('Dark'), Adw.ColorScheme.FORCE_DARK),
)

# Languages Babble ships a translation for (po/LINGUAS) plus English,
# each named in its own language. '' follows the system.
INTERFACE_LANGUAGES = (
    ('en', 'English'),
    ('de', 'Deutsch'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('pt_BR', 'Português (Brasil)'),
)

TEXT_SIZES = (('small', _('Small')), ('normal', _('Normal')),
              ('large', _('Large')))

AUTO_REFRESH_CHOICES = (
    ('0', _('Off')), ('30', _('Every 30 Seconds')),
    ('60', _('Every Minute')), ('300', _('Every 5 Minutes')))

QUOTE_POLICIES = (
    ('public', _('Everyone')),
    ('followers', _('Followers')),
    ('nobody', _('Only Me')),
)

# Visibility nicks of the PostVisibility enum, as the server names them.
VISIBILITIES = (
    ('public', _('Public')), ('unlisted', _('Unlisted')),
    ('private', _('Followers only')), ('direct', _('Direct')))

# Native names of the languages Mastodon servers commonly offer.
LANGUAGES = (
    ('ar', 'العربية'), ('bg', 'Български'), ('bn', 'বাংলা'),
    ('ca', 'Català'), ('cs', 'Čeština'), ('cy', 'Cymraeg'),
    ('da', 'Dansk'), ('de', 'Deutsch'), ('el', 'Ελληνικά'),
    ('en', 'English'), ('eo', 'Esperanto'), ('es', 'Español'),
    ('et', 'Eesti'), ('eu', 'Euskara'), ('fa', 'فارسی'),
    ('fi', 'Suomi'), ('fr', 'Français'), ('ga', 'Gaeilge'),
    ('gl', 'Galego'), ('he', 'עברית'), ('hi', 'हिन्दी'),
    ('hr', 'Hrvatski'), ('hu', 'Magyar'), ('hy', 'Հայերեն'),
    ('id', 'Bahasa Indonesia'), ('is', 'Íslenska'), ('it', 'Italiano'),
    ('ja', '日本語'), ('ka', 'ქართული'), ('ko', '한국어'),
    ('lt', 'Lietuvių'), ('lv', 'Latviešu'), ('ms', 'Bahasa Melayu'),
    ('nb', 'Norsk bokmål'), ('nl', 'Nederlands'), ('nn', 'Norsk nynorsk'),
    ('pl', 'Polski'), ('pt', 'Português'), ('ro', 'Română'),
    ('ru', 'Русский'), ('sk', 'Slovenčina'), ('sl', 'Slovenščina'),
    ('sq', 'Shqip'), ('sr', 'Српски'), ('sv', 'Svenska'),
    ('ta', 'தமிழ்'), ('th', 'ไทย'), ('tr', 'Türkçe'),
    ('uk', 'Українська'), ('vi', 'Tiếng Việt'), ('zh', '中文'),
)

# Notification types announced on the desktop; the order is the order
# of the rows.
NOTIFICATION_TYPES = (
    ('mention', _('Mentions'), None),
    ('follow', _('New Followers'), None),
    ('follow_request', _('Follow Requests'), None),
    ('favourite', _('Favorites'), None),
    ('reblog', _('Boosts'), None),
    ('poll', _('Poll Results'), _('Polls you voted in or created')),
    ('status', _('New Posts'), _('From accounts you turned notifications on for')),
    ('update', _('Edits'), _('Posts you boosted that were edited')),
    ('quote', _('Quotes'), None),
    ('quoted_update', _('Quote Edits'), _('Posts you quoted that were edited')),
    ('admin.sign_up', _('New Sign-Ups'), _('Server administrators only')),
    ('admin.report', _('New Reports'), _('Server administrators only')),
)

# CSS classes the window carries for the appearance toggles.
STYLE_CLASSES = {
    'larger-line-height': 'large-line-height',
    'enlarge-custom-emojis': 'enlarge-emoji',
    'scale-emoji-hover': 'scale-emoji-hover',
}
STYLE_KEYS = ('color-scheme', 'text-size', 'darken-images-on-dark-mode',
              *STYLE_CLASSES)


# Live appearance

def apply_color_scheme(settings):
    scheme = dict((nick, value) for nick, _l, value in COLOR_SCHEMES)[
        settings.get_string('color-scheme')]
    Adw.StyleManager.get_default().set_color_scheme(scheme)


def apply_style(window, settings):
    """Sync the window's CSS classes and the colour scheme with the
    settings: ``text-small``/``text-large``, ``large-line-height``,
    ``enlarge-emoji``, ``scale-emoji-hover`` and ``darken-media`` (the
    dark style with the darken toggle on)."""
    apply_color_scheme(settings)
    for name in ('text-small', 'text-large'):
        window.remove_css_class(name)
    size = settings.get_string('text-size')
    if size != 'normal':
        window.add_css_class('text-%s' % size)
    for key, css_class in STYLE_CLASSES.items():
        if settings.get_boolean(key):
            window.add_css_class(css_class)
        else:
            window.remove_css_class(css_class)
    darken = settings.get_boolean('darken-images-on-dark-mode') \
        and Adw.StyleManager.get_default().get_dark()
    if darken:
        window.add_css_class('darken-media')
    else:
        window.remove_css_class('darken-media')


# Server-side posting defaults

def sync_posting_defaults(window, key, settings):
    """Mirror a changed posting default to the account's server
    preferences (PATCH /api/v1/accounts/update_credentials), the way
    Tuba does. Only called for changes the user makes."""
    client = window.client
    if client is None or not client.token:
        return
    form = {}
    if key == 'default-visibility':
        value = settings.get_string(key)
        if value not in ('public', 'unlisted', 'private'):
            return   # 'direct' is not a server default
        form['source[privacy]'] = value
    elif key == 'default-language':
        value = settings.get_string(key)
        if not value:
            return
        form['source[language]'] = value
    elif key == 'default-sensitive':
        form['source[sensitive]'] = 'true' if settings.get_boolean(key) \
            else 'false'
    elif key == 'default-quote-policy':
        form['source[quote_policy]'] = settings.get_string(key)
    else:
        return

    def done(_client, result, _data=None):
        try:
            client.request_finish(result)
        except GLib.Error as error:
            pages.report_error(window, _('Could not update the server preferences'),
                               error)

    client.request_async('PATCH', '/accounts/update_credentials',
                         form=form, callback=done)


# Dialog

class PreferencesDialog(Adw.PreferencesDialog):
    """Preferences of the app and, when signed in, of the account."""

    __gtype_name__ = 'BabblePreferencesDialog'

    def __init__(self, window):
        super().__init__(title=_('Preferences'))
        self.window = window
        self.settings = window.settings
        self.add(self._general_page())
        self.add(self._timelines_page())
        if window.client is not None:
            self.add(self._posting_page())
        self.add(self._notifications_page())
        if window.client is not None:
            self.add(self._filters_page())

    # Row builders

    def _switch(self, group, key, title, subtitle=None, invert=False):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or '',
                            use_underline=True)
        self.settings.bind(
            key, row, 'active',
            Gio.SettingsBindFlags.INVERT_BOOLEAN if invert
            else Gio.SettingsBindFlags.DEFAULT)
        group.add(row)
        return row

    def _combo_string(self, group, key, title, choices, subtitle=None,
                      sync=False):
        """ComboRow over a string key; *choices* is (nick, label)…"""
        nicks = [choice[0] for choice in choices]
        row = Adw.ComboRow(
            title=title, subtitle=subtitle or '', use_underline=True,
            model=Gtk.StringList.new([choice[1] for choice in choices]))
        current = self.settings.get_string(key)
        row.set_selected(nicks.index(current) if current in nicks else 0)

        pages.connect_weak(row, 'notify::selected', self._on_combo_selected,
                           key, nicks, sync)
        # Follow outside changes (e.g. the keyboard font-size shortcuts).
        pages.connect_weak(self.settings, 'changed::' + key,
                           self._on_combo_setting_changed, row, nicks)
        group.add(row)
        return row

    def _on_combo_selected(self, row, _pspec, key, nicks, sync):
        value = nicks[row.get_selected()]
        if value != self.settings.get_string(key):
            self.settings.set_string(key, value)
            if sync:
                self._sync(key)

    def _on_combo_setting_changed(self, settings, key, row, nicks):
        if settings.get_string(key) in nicks:
            row.set_selected(nicks.index(settings.get_string(key)))

    # Pages

    def _general_page(self):
        page = Adw.PreferencesPage(title=_('General'),
                                   icon_name='preferences-system-symbolic',
                                   name='general')

        appearance = Adw.PreferencesGroup(title=_('Appearance'))
        row = self._combo_string(
            appearance, 'interface-language', _('_Language'),
            [('', _('System Language'))] + list(INTERFACE_LANGUAGES),
            subtitle=_('Takes effect the next time Babble starts'))
        pages.connect_weak(row, 'notify::selected',
                           self._on_interface_language_selected)
        self._combo_string(appearance, 'color-scheme', _('_Color Scheme'),
                           [(n, label) for n, label, _v in COLOR_SCHEMES])
        self._combo_string(
            appearance, 'text-size', _('_Text Size'), TEXT_SIZES,
            subtitle=_('Set the text size used in the interface'))
        self._switch(appearance, 'larger-line-height', _('Large _Line Height'),
                     _('Increase the line height in posts'))
        self._switch(appearance, 'darken-images-on-dark-mode',
                     _('_Darken Images on Dark Mode'))
        self._switch(appearance, 'dim-trivial-notifications',
                     _('Dim _Trivial Notifications'),
                     _('Notifications like favorites and boosts will be less visible'))
        self._switch(appearance, 'enlarge-custom-emojis',
                     _('Large Custom _Emojis'))
        self._switch(appearance, 'scale-emoji-hover',
                     _('_Scale Custom Emojis on Hover'),
                     _('Slightly increases the size of custom emojis '
                       'when you hover over them'))
        self._switch(appearance, 'translucent-sidebar',
                     _('T_ranslucent Sidebar'),
                     _('Blur the desktop behind the sidebar. '
                       'Needs a compositor that supports background blur'))
        page.add(appearance)

        posts = Adw.PreferencesGroup(title=_('Posts'))
        self._switch(posts, 'collapse-long-posts', _('Collapse _Long Posts'))
        self._switch(posts, 'letterbox-media', _('_Letterbox Media'),
                     _('Scale down and letterbox media to fill the '
                       'image containers instead of cropping them'))
        self._switch(posts, 'show-sensitive-media',
                     _('Reveal Sensitive _Media by Default'))
        self._switch(posts, 'show-spoilers',
                     _('Reveal _Spoilers by Default'))
        self._switch(posts, 'show-preview-cards',
                     _('Show Link Preview _Cards'))
        self._switch(posts, 'show-interaction-counters',
                     _('Show Interaction C_ounters'),
                     _('Reply, boost and favorite counts on post actions'))
        page.add(posts)

        behavior = Adw.PreferencesGroup(title=_('Behavior'))
        self._switch(behavior, 'advanced-boost-dialog',
                     _('Advanced _Boost Dialog'),
                     _('Change boost visibility, quote and confirm boosting'))
        self._switch(behavior, 'strip-tracking',
                     _('Strip _Tracking Parameters From Links'),
                     _('This option can lead to broken links when enabled'))
        self._switch(behavior, 'aggressive-resolving',
                     _('Open Remote Links in _Babble'),
                     _('Open links to posts and profiles on other '
                       'servers inside the app'))
        self._switch(behavior, 'reply-to-old-post-reminder',
                     _('Warn Before Replying to an _Old Post'))
        self._switch(behavior, 'copy-private-link-reminder',
                     _('Warn Before Copying a Link to a _Private Post'))
        self._switch(behavior, 'boost-alt-text-reminder',
                     _('Warn Before Boosting Media Without _Alt Text'))
        row = self._switch(behavior, 'work-in-background',
                           _('_Run in Background'),
                           _('Keep running after the window is '
                             'closed to deliver notifications'))
        pages.connect_weak(row, 'notify::active', self._on_background_toggled)
        page.add(behavior)
        return page

    def _on_interface_language_selected(self, _row, _pspec):
        self.add_toast(Adw.Toast(
            title=_('Restart Babble to use the new language')))

    def _on_background_toggled(self, row, _pspec):
        if row.get_active():
            self.window.request_background()

    def _timelines_page(self):
        page = Adw.PreferencesPage(title=_('Timelines'),
                                   icon_name='view-list-symbolic',
                                   name='timelines')
        live = Adw.PreferencesGroup(title=_('Live Updates'))
        stream = self._switch(live, 'live-updates', _('_Stream Timelines'),
                              _('Receive new posts and notifications in real time'))
        public = self._switch(
            live, 'public-live-updates', _('Stream _Public Timelines'),
            _('This will increase memory usage when browsing high traffic timelines'))
        stream.bind_property('active', public, 'sensitive',
                             GObject.BindingFlags.SYNC_CREATE)
        refresh = Adw.PreferencesGroup(
            title=_('Refresh'),
            description=_('Used while live updates are off or unavailable'))
        self._combo_string(refresh, 'auto-refresh', _('_Automatic Refresh'),
                           AUTO_REFRESH_CHOICES)
        sidebar = Adw.PreferencesGroup(
            title=_('Sidebar'),
            description=_('Choose which timelines the sidebar offers'))
        self._switch(sidebar, 'hide-trending', _('_Explore'),
                     _('Show posts trending on your server'), invert=True)
        self._switch(sidebar, 'hide-local', _('_Local Timeline'),
                     _('Show posts from your server'), invert=True)
        self._switch(sidebar, 'hide-federated', _('_Federated Timeline'),
                     _('Show posts from all known servers'), invert=True)
        page.add(live)
        page.add(refresh)
        page.add(sidebar)
        return page

    def _posting_page(self):
        page = Adw.PreferencesPage(title=_('Posting'),
                                   icon_name='document-edit-symbolic',
                                   name='posting')
        group = Adw.PreferencesGroup(
            title=_('Defaults for New Posts'),
            description=_('Changes are also saved to your server preferences'))
        self._combo_string(group, 'default-visibility',
                           _('Default _Visibility'), VISIBILITIES,
                           sync=True)
        self._combo_string(group, 'default-quote-policy',
                           _('Default _Quote Policy'), QUOTE_POLICIES,
                           subtitle=_('Who can quote your new posts'),
                           sync=True)
        self._language_row(group)
        row = self._switch(group, 'default-sensitive',
                           _('Mark Media as _Sensitive'))
        pages.connect_weak(row, 'notify::active', self._on_sensitive_toggled)

        instance = (self.window.account or {}).get('instance', '')
        server = Adw.PreferencesGroup()
        server_row = Adw.ActionRow(
            title=_('_Server Preferences'), use_underline=True,
            subtitle=urllib.parse.urlsplit(instance).netloc or instance,
            activatable=True)
        server_row.add_suffix(Gtk.Image(
            icon_name='adw-external-link-symbolic',
            accessible_role=Gtk.AccessibleRole.PRESENTATION))
        pages.connect_weak(server_row, 'activated',
                           self._on_server_activated, instance)
        server.add(server_row)
        page.add(group)
        page.add(server)
        return page

    def _on_sensitive_toggled(self, _row, _pspec):
        self._sync('default-sensitive')

    def _on_server_activated(self, _row, instance):
        Gtk.UriLauncher.new('%s/settings/preferences' % instance).launch(
            self.window, None, None)

    def _language_row(self, group):
        codes = [''] + [code for code, _n in LANGUAGES]
        labels = [_('System Language')] + [name for _c, name in LANGUAGES]
        row = Adw.ComboRow(title=_('Default _Language'), use_underline=True,
                           model=Gtk.StringList.new(labels))
        current = self.settings.get_string('default-language')
        row.set_selected(codes.index(current) if current in codes else 0)

        pages.connect_weak(row, 'notify::selected',
                           self._on_language_selected, codes)
        group.add(row)

    def _on_language_selected(self, row, _pspec, codes):
        value = codes[row.get_selected()]
        if value != self.settings.get_string('default-language'):
            self.settings.set_string('default-language', value)
            self._sync('default-language')

    def _sync(self, key):
        sync_posting_defaults(self.window, key, self.settings)

    def _notifications_page(self):
        page = Adw.PreferencesPage(title=_('Notifications'),
                                   icon_name='preferences-system-notifications-symbolic',
                                   name='notifications')
        desktop = Adw.PreferencesGroup(
            title=_('Desktop Notifications'),
            description=_('Announced while the window is not focused'))
        for kind, title, subtitle in NOTIFICATION_TYPES:
            row = Adw.SwitchRow(title=title, subtitle=subtitle or '')
            row.set_active(kind in self.settings.get_strv(
                'desktop-notification-types'))
            pages.connect_weak(row, 'notify::active', self._on_type_toggled,
                               kind)
            desktop.add(row)
        self._switch(
            desktop, 'group-desktop-notifications', _('_Group'),
            _('Combine notifications of the same type and post into one'))
        page.add(desktop)

        if self.window.client is not None:
            server = Adw.PreferencesGroup(title=_('Server'))
            policy = Adw.ActionRow(
                title=_('Notification _Filtering'), use_underline=True,
                subtitle=_('Choose whose notifications reach you'),
                activatable=True)
            policy.add_suffix(Gtk.Image(
                icon_name='go-next-symbolic',
                accessible_role=Gtk.AccessibleRole.PRESENTATION))
            pages.connect_weak(policy, 'activated', self._on_policy_activated)
            server.add(policy)
            page.add(server)
        return page

    def _on_type_toggled(self, row, _pspec, kind):
        enabled = list(self.settings.get_strv('desktop-notification-types'))
        if row.get_active() and kind not in enabled:
            enabled.append(kind)
        elif not row.get_active() and kind in enabled:
            enabled.remove(kind)
        self.settings.set_strv('desktop-notification-types', enabled)

    def _on_policy_activated(self, _row):
        try:
            from .notifications import present_notification_policy
        except ImportError:
            pages.show_toast(self, _('Notification filtering is not available'))
            return
        present_notification_policy(self, self.window.client)

    def _filters_page(self):
        page = Adw.PreferencesPage(title=_('Filters'),
                                   icon_name='babble-filter-symbolic',
                                   name='filters')
        group = Adw.PreferencesGroup(
            title=_('Filters'),
            description=_('Hide or warn about posts that match keywords, '
                          'on every device you use'))
        row = Adw.ActionRow(title=_('_Manage Filters'), use_underline=True,
                            subtitle=_('Create, edit and delete filters'),
                            activatable=True)
        row.add_suffix(Gtk.Image(
            icon_name='go-next-symbolic',
            accessible_role=Gtk.AccessibleRole.PRESENTATION))
        pages.connect_weak(row, 'activated', self._on_filters_activated)
        group.add(row)
        page.add(group)
        return page

    def _on_filters_activated(self, _row):
        from .filters import present_filters
        present_filters(self, self.window.client)


def present_preferences(window):
    dialog = PreferencesDialog(window)
    dialog.present(window)
    return dialog
