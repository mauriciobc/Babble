# SPDX-License-Identifier: GPL-3.0-or-later

"""Hashtags: the hashtag timeline (follow, feature, pin, usage stats),
the followed hashtags page and Phanpy-style saved multi-hashtag
timelines.

Tuba: Views/Hashtags.vala, Views/Hashtag.vala, Dialogs/HashtagList.vala.
Phanpy: pages/followed-hashtags.jsx, pages/hashtag.jsx.
"""

import json
from .i18n import gettext as _, ngettext  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402
from . import (appsettings, feedback, pagenav, pages,  # noqa: E402
               uihelpers)
from .weakconnect import connect_weak  # noqa: E402
from .api import tags as api_tags  # noqa: E402
from .api import timelines as api_timelines  # noqa: E402
from .hashtagcompleter import forget_pool  # noqa: E402
from .lists import make_pin_button  # noqa: E402
from .statustext import STATS_DAYS, history_totals  # noqa: E402, F401

FAVORITE_TAGS_KEY = 'favorite-tags-ids'
SAVED_KEY = 'hashtag-lists'
# Mastodon takes at most four extra tags next to the one in the path.
TOTAL_TAGS_LIMIT = 5


def _invalidate_home_tags(widget):
    """Forget Home's followed tags, so the next page re-reads them."""
    window = pagenav.window_of(widget)
    page = getattr(window, 'timeline_page', None)
    if page is not None:
        page.invalidate_followed_tags()
    # The composer's suggestions rank the followed tags too.
    client = pagenav.client_of(widget)
    if client is not None:
        forget_pool(client)


def clean_name(name):
    return (name or '').strip().lstrip('#').strip()


def stats_text(history, days=STATS_DAYS):
    """'12 posts · 5 people, last 2 days'; '' without history."""
    if not history:
        return ''
    posts, people = history_totals(history, days)
    return _('%(posts)s · %(people)s, last %(days)d days') % {
        'posts': ngettext('%d post', '%d posts', posts) % posts,
        'people': ngettext('%d person', '%d people', people) % people,
        'days': days}


# Saved multi-hashtag timelines

def load_saved():
    """Saved timelines: dicts {'title', 'tags', 'all', 'none', 'local'}."""
    saved = []
    for raw in appsettings.settings().get_strv(SAVED_KEY):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('tags'):
            saved.append(item)
    return saved


def store_saved(items):
    appsettings.settings().set_strv(SAVED_KEY, [
        json.dumps(item, ensure_ascii=False) for item in items])


def hash_badge():
    """A '#' glyph standing in for a hashtag icon (Adwaita has none)."""
    label = Gtk.Label(label='#', width_chars=2,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    label.add_css_class('title-3')
    label.add_css_class('dimmed')
    return label


def hashtag_summary(tag):
    """(title, subtitle) a hashtag row shows: '#name' and its usage line,
    which is '' without history."""
    return ('#' + tag.get('name', ''), stats_text(tag.get('history')))


def catchup_hashtag_lines(entry):
    """Usage and timeline-count lines for a Catch-Up hashtag tile."""
    tag = entry.get('tag') or {}
    usage = stats_text(tag.get('history'))
    posts = entry.get('timeline_posts') or 0
    mine = ngettext('%d post in your timeline',
                    '%d posts in your timeline', posts) % posts \
        if posts else ''
    return usage, mine


def default_title(tags):
    return ' '.join('#' + tag for tag in tags)


# Hashtag timeline

class HashtagPage(Adw.Bin):
    """One or several hashtags as a timeline. A single tag also gets the
    usage line, Follow, Feature on Profile and Pin to Sidebar; several
    tags (``tags`` beyond the first become ``any[]``) get an Edit
    button for the saved definition."""

    __gtype_name__ = 'BabbleHashtagPage'

    def __init__(self, client, tags, definition=None):
        super().__init__()
        from .timelinepage import ApiTimelineSource, TimelinePage
        self.client = client
        self.tags = [clean_name(t) for t in tags if clean_name(t)]
        self.definition = definition
        self.multi = len(self.tags) > 1 or bool(
            definition and (definition.get('all') or definition.get('none')
                            or definition.get('local')))
        params = {}
        if len(self.tags) > 1:
            params['any[]'] = self.tags[1:]
        if definition:
            if definition.get('all'):
                params['all[]'] = list(definition['all'])
            if definition.get('none'):
                params['none[]'] = list(definition['none'])
            if definition.get('local'):
                params['local'] = 'true'
        self.timeline = TimelinePage(client, source=ApiTimelineSource(
            client, api_timelines.list_path('tag', clean_name(self.tags[0])),
            params=params))
        pages.configure_empty_page(
            self.timeline.empty_page, 'edit-find-symbolic',
            _('No Posts With This Hashtag'),
            _('No one has posted anything with this tag yet'))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stats_label = Gtk.Label(xalign=0.0, wrap=True, visible=False,
                                     margin_top=8, margin_bottom=4,
                                     margin_start=12, margin_end=12)
        self.stats_label.add_css_class('dimmed')
        self.stats_label.add_css_class('caption')
        self.stats_label.add_css_class('hashtag-stats')
        box.append(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH,
                             child=self.stats_label))
        # Banners plugins add (HashtagPageExtension); hidden while empty.
        self.plugin_banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                      spacing=6, visible=False,
                                      margin_start=12, margin_end=12,
                                      margin_bottom=6)
        box.append(Adw.Clamp(maximum_size=pages.COLUMN_WIDTH,
                             child=self.plugin_banners))
        self.timeline.set_vexpand(True)
        box.append(self.timeline)
        self.set_child(box)

        self.following = False
        self.featured_id = None
        self.header_widgets = []
        if not self.multi:
            self._build_single_header()
            self._load_info()
            from . import pluginhost
            pluginhost.attach_hashtag_page(self, self.tags[0].lower(),
                                           self.plugin_banners)
        else:
            self.edit_button = uihelpers.icon_button(
                'document-edit-symbolic', _('Edit Timeline'))
            connect_weak(self.edit_button, 'clicked', self._on_edit)
            self.header_widgets = [self.edit_button]

    def refresh(self):
        self.timeline.refresh()

    # Single hashtag header

    def _build_single_header(self):
        name = self.tags[0]
        self.follow_button = Gtk.Button(label=_('_Follow'),
                                        use_underline=True,
                                        valign=Gtk.Align.CENTER,
                                        sensitive=False)
        connect_weak(self.follow_button, 'clicked',
                           self._on_follow_clicked)
        self.pin_button = make_pin_button(
            self, FAVORITE_TAGS_KEY, name, '#' + name)
        self.more_button = Gtk.MenuButton(
            icon_name='view-more-symbolic',
            valign=Gtk.Align.CENTER, sensitive=False)
        self.more_button.add_css_class('flat')
        uihelpers.set_accessible_label(self.more_button, _('More Options'))
        group = Gio.SimpleActionGroup()
        feature = Gio.SimpleAction.new('feature', None)
        connect_weak(feature, 'activate', self._on_feature)
        group.add_action(feature)
        self.more_button.insert_action_group('tag', group)
        self._build_menu()
        self.header_widgets = [self.follow_button, self.pin_button,
                               self.more_button]

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append(_('_Remove from Featured Tags') if self.featured_id
                    else _('_Feature on Profile'), 'tag.feature')
        self.more_button.set_menu_model(menu)

    def _set_following(self, following):
        self.following = following
        self.follow_button.set_label(_('_Unfollow') if following
                                     else _('_Follow'))
        self.follow_button.set_use_underline(True)
        if following:
            self.follow_button.remove_css_class('suggested-action')
        else:
            self.follow_button.add_css_class('suggested-action')

    def _load_info(self):
        api_tags.get_async(self.client, self.tags[0],
                           callback=self._on_info)
        api_tags.featured_async(self.client, callback=self._on_featured)

    def _on_info(self, client, result, _data=None):
        try:
            info = api_tags.get_finish(client, result)
        except GLib.Error:
            return
        text = stats_text(info.get('history'))
        self.stats_label.set_label(text)
        self.stats_label.set_visible(bool(text))
        self._set_following(bool(info.get('following')))
        self.follow_button.set_sensitive(True)

    def _on_featured(self, client, result, _data=None):
        try:
            featured = api_tags.featured_finish(client, result)
        except GLib.Error:
            return
        name = self.tags[0].lower()
        for item in featured:
            if item.get('name', '').lower() == name:
                self.featured_id = item['id']
        self._build_menu()
        self.more_button.set_sensitive(True)

    def _on_follow_clicked(self, button):
        name = self.tags[0]
        action = 'unfollow' if self.following else 'follow'
        button.set_sensitive(False)

        def done(client, result, _data=None):
            button.set_sensitive(True)
            try:
                info = api_tags.action_finish(client, result)
            except GLib.Error as error:
                feedback.report_error(
                    self, _('Could not unfollow #%s') % name
                    if action == 'unfollow'
                    else _('Could not follow #%s') % name, error)
                return
            self._set_following(bool(info.get('following', not self.following)))
            _invalidate_home_tags(self)
            feedback.show_toast(
                self, _('Followed #%s') % name if self.following
                else _('Unfollowed #%s') % name)

        api_tags.action_async(self.client, name, action, callback=done)

    def _on_feature(self, *_a):
        name = self.tags[0]

        def done(client, result, _data=None):
            self.more_button.set_sensitive(True)
            try:
                data = api_tags.feature_finish(client, result)
            except GLib.Error as error:
                feedback.report_error(self, _('Could not change the featured '
                                           'tags'), error)
                return
            if self.featured_id:
                self.featured_id = None
                feedback.show_toast(self, _('Removed from featured tags'))
            else:
                self.featured_id = (data or {}).get('id')
                feedback.show_toast(self, _('Featured on your profile'))
            # The composer's suggestions list the featured tags.
            forget_pool(self.client)
            self._build_menu()

        self.more_button.set_sensitive(False)
        if self.featured_id:
            api_tags.unfeature_async(self.client, self.featured_id,
                                     callback=done)
        else:
            api_tags.feature_async(self.client, name, callback=done)

    # Multi-hashtag header

    def _on_edit(self, _button=None):
        definition = self.definition or {
            'title': default_title(self.tags), 'tags': list(self.tags)}
        present_multi_hashtag_editor(
            self, definition, on_saved=self._on_edited,
            on_deleted=self._on_deleted)

    def _on_edited(self, new_definition):
        window = pagenav.window_of(self)
        if window is None:
            return
        view = window.navigation_view
        view.pop()
        push_multi_hashtag(window, self.client, new_definition['title'],
                           new_definition['tags'],
                           all_tags=new_definition.get('all'),
                           none_tags=new_definition.get('none'),
                           local=new_definition.get('local', False))

    def _on_deleted(self, _definition):
        window = pagenav.window_of(self)
        if window is not None:
            window.navigation_view.pop()


def push_hashtag(widget, client, name):
    """Open the timeline of #name on the navigation stack."""
    name = clean_name(name)
    if not name:
        return None
    return pagenav.push_page(widget, '#' + name,
                           HashtagPage(client, [name]),
                           tag='tag-%s' % name.lower())


def push_multi_hashtag(widget, client, title, tags, all_tags=None,
                       none_tags=None, local=False):
    """Timeline over several hashtags: posts with any of *tags*
    (/timelines/tag/:first?any[]=rest), optionally also all of
    *all_tags*, none of *none_tags*, and only from this server."""
    tags = [clean_name(t) for t in tags if clean_name(t)]
    if not tags:
        return None
    definition = {'title': title or default_title(tags), 'tags': tags,
                  'all': [clean_name(t) for t in all_tags or ()],
                  'none': [clean_name(t) for t in none_tags or ()],
                  'local': bool(local)}
    saved = next((s for s in load_saved()
                  if _same_definition(s, definition)), None)
    page = HashtagPage(client, tags, saved or definition)
    key = json.dumps([tags, definition['all'], definition['none'],
                      definition['local']])
    return pagenav.push_page(widget, definition['title'], page,
                           tag='tags-%s' % key)


def _same_definition(a, b):
    return ([t.lower() for t in a.get('tags', [])]
            == [t.lower() for t in b.get('tags', [])]
            and [t.lower() for t in a.get('all', [])]
            == [t.lower() for t in b.get('all', [])]
            and [t.lower() for t in a.get('none', [])]
            == [t.lower() for t in b.get('none', [])]
            and bool(a.get('local')) == bool(b.get('local')))


# Multi-hashtag editor

class _TagsEditor:
    """A preferences group holding hashtag rows with remove buttons and
    an entry row to add more."""

    def __init__(self, title, description, dialog, minimum=0):
        # Weak: the dialog owns this editor's widgets, whose handlers
        # hold the editor.
        self._dialog = dialog.weak_ref()
        self.tags = []
        self.rows = {}
        self.group = Adw.PreferencesGroup(title=title,
                                          description=description)
        self.entry = Adw.EntryRow(title=_('Add Hashtag'),
                                  show_apply_button=True)
        self.entry.add_prefix(hash_badge())
        self.entry.connect('apply', self._on_apply)
        self.group.add(self.entry)

    @property
    def dialog(self):
        return self._dialog()

    def set_tags(self, tags):
        for tag in list(self.tags):
            self.remove(tag)
        for tag in tags:
            self.add(tag)

    def add(self, name):
        name = clean_name(name)
        if not name or name.lower() in (t.lower() for t in self.tags):
            return False
        row = Adw.ActionRow(title='#' + name, use_markup=False)
        remove = uihelpers.icon_button('list-remove-symbolic',
                                   _('Remove Hashtag'))
        remove.connect('clicked', self._on_remove_clicked, name)
        row.add_suffix(remove)
        self.group.add(row)
        self.tags.append(name)
        self.rows[name] = row
        self.dialog.changed()
        return True

    def _on_remove_clicked(self, _button, name):
        self.remove(name)

    def remove(self, name):
        row = self.rows.pop(name, None)
        if row is not None:
            self.group.remove(row)
        if name in self.tags:
            self.tags.remove(name)
        self.dialog.changed()

    def _on_apply(self, entry):
        text = entry.get_text()
        names = [part for part in text.replace(',', ' ').split() if part]
        if self.dialog.total() + len(names) > TOTAL_TAGS_LIMIT:
            self.dialog.toast(ngettext(
                'At most %d hashtag can be combined',
                'At most %d hashtags can be combined',
                TOTAL_TAGS_LIMIT) % TOTAL_TAGS_LIMIT)
            return
        for name in names:
            self.add(name)
        entry.set_text('')


class MultiHashtagEditor(feedback.ToastDialog):
    """Create or edit a saved multi-hashtag timeline."""

    def __init__(self, definition, on_saved=None, on_deleted=None):
        existing = definition is not None and definition in load_saved()
        super().__init__(_('Edit Multi-Hashtag Timeline') if existing
                         else _('New Multi-Hashtag Timeline'))
        self.original = definition
        self.on_saved = on_saved
        self.on_deleted = on_deleted
        self.existing = existing

        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        connect_weak(self.save_button, 'clicked', self._on_save)
        self.header.pack_end(self.save_button)

        group = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title=_('Title'))
        self.title_row.set_text((definition or {}).get('title', ''))
        group.add(self.title_row)
        self.local_row = Adw.SwitchRow(
            title=_('Local Only'),
            subtitle=_('Only posts from your server'),
            active=bool((definition or {}).get('local')))
        group.add(self.local_row)
        self.page.add(group)

        self.any_editor = _TagsEditor(
            _('Hashtags'),
            _('Posts with any of these hashtags. The first one is the '
              'main hashtag.'), self)
        self.all_editor = _TagsEditor(
            _('Also Requiring'), _('Posts must contain all of these too'),
            self)
        self.none_editor = _TagsEditor(
            _('Excluding'), _('Hide posts with any of these'), self)
        for editor in (self.any_editor, self.all_editor, self.none_editor):
            self.page.add(editor.group)
        self.any_editor.set_tags((definition or {}).get('tags', []))
        self.all_editor.set_tags((definition or {}).get('all', []))
        self.none_editor.set_tags((definition or {}).get('none', []))

        if existing:
            danger = Adw.PreferencesGroup()
            delete = Gtk.Button(label=_('_Delete Timeline'),
                                use_underline=True, halign=Gtk.Align.CENTER)
            # Plain pill, like the list editor's Delete: the header's
            # Save keeps the one emphasis, and Delete is already rare,
            # isolated below the editors, verb-named and confirmed.
            delete.add_css_class('pill')
            connect_weak(delete, 'clicked', self._on_delete)
            danger.add(delete)
            self.page.add(danger)
        self.changed()

    def _on_cancel(self, _button):
        self.close()

    def total(self):
        return (len(self.any_editor.tags) + len(self.all_editor.tags)
                + len(self.none_editor.tags))

    def changed(self):
        self.save_button.set_sensitive(bool(self.any_editor.tags))

    def _on_save(self, *_a):
        tags = list(self.any_editor.tags)
        if not tags:
            return
        definition = {
            'title': self.title_row.get_text().strip()
            or default_title(tags),
            'tags': tags,
            'all': list(self.all_editor.tags),
            'none': list(self.none_editor.tags),
            'local': self.local_row.get_active()}
        items = load_saved()
        if self.existing and self.original in items:
            items[items.index(self.original)] = definition
        else:
            items.append(definition)
        store_saved(items)
        if self.on_saved is not None:
            self.on_saved(definition)
        self.close()

    def _on_delete(self, _button):
        def really():
            items = [i for i in load_saved() if i != self.original]
            store_saved(items)
            if self.on_deleted is not None:
                self.on_deleted(self.original)
            self.close()

        feedback.confirm(self, _('Delete This Timeline?'),
                      _('The saved timeline “%s” will be deleted.')
                      % self.original.get('title', ''),
                      _('_Delete'), really)


def present_multi_hashtag_editor(parent, definition=None, on_saved=None,
                                 on_deleted=None):
    dialog = MultiHashtagEditor(definition, on_saved, on_deleted)
    dialog.present(parent)
    return dialog


# Followed hashtags page
