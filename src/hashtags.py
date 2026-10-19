# SPDX-License-Identifier: GPL-3.0-or-later

"""Hashtags: the hashtag timeline (follow, feature, pin, usage stats),
the followed hashtags page and Phanpy-style saved multi-hashtag
timelines.

Tuba: Views/Hashtags.vala, Views/Hashtag.vala, Dialogs/HashtagList.vala.
Phanpy: pages/followed-hashtags.jsx, pages/hashtag.jsx.
"""

import json
import urllib.parse

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import charts  # noqa: E402
from . import pages  # noqa: E402
from .lists import make_pin_button  # noqa: E402
from .pages import ToastDialog  # noqa: E402

FAVORITE_TAGS_KEY = 'favorite-tags-ids'
SAVED_KEY = 'hashtag-lists'
# Mastodon takes at most four extra tags next to the one in the path.
TOTAL_TAGS_LIMIT = 5
STATS_DAYS = 2


def _invalidate_home_tags(widget):
    """Forget Home's followed tags, so the next page re-reads them."""
    window = pages.window_of(widget)
    page = getattr(window, 'timeline_page', None)
    if page is not None:
        page.invalidate_followed_tags()


def clean_name(name):
    return (name or '').strip().lstrip('#').strip()


def _tag_path(name):
    return '/timelines/tag/%s' % urllib.parse.quote(clean_name(name), safe='')


def history_totals(history, days=STATS_DAYS):
    """(posts, people) over the newest *days* entries of tag.history."""
    posts = people = 0
    for entry in (history or [])[:days]:
        posts += int(entry.get('uses') or 0)
        people += int(entry.get('accounts') or 0)
    return posts, people


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
    for raw in pages.settings().get_strv(SAVED_KEY):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('tags'):
            saved.append(item)
    return saved


def store_saved(items):
    pages.settings().set_strv(SAVED_KEY, [
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
            client, _tag_path(self.tags[0]), params=params))
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
        self.timeline.set_vexpand(True)
        box.append(self.timeline)
        self.set_child(box)

        self.following = False
        self.featured_id = None
        self.header_widgets = []
        if not self.multi:
            self._build_single_header()
            self._load_info()
        else:
            self.edit_button = pages.icon_button(
                'document-edit-symbolic', _('Edit Timeline'))
            pages.connect_weak(self.edit_button, 'clicked', self._on_edit)
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
        pages.connect_weak(self.follow_button, 'clicked',
                           self._on_follow_clicked)
        self.pin_button = make_pin_button(
            self, FAVORITE_TAGS_KEY, name, '#' + name)
        self.more_button = Gtk.MenuButton(
            icon_name='view-more-symbolic',
            valign=Gtk.Align.CENTER, sensitive=False)
        self.more_button.add_css_class('flat')
        pages.set_accessible_label(self.more_button, _('More Options'))
        group = Gio.SimpleActionGroup()
        feature = Gio.SimpleAction.new('feature', None)
        pages.connect_weak(feature, 'activate', self._on_feature)
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
        name = self.tags[0]
        quoted = urllib.parse.quote(name, safe='')
        self.client.request_async('GET', '/tags/%s' % quoted,
                                  callback=self._on_info)
        self.client.request_async('GET', '/featured_tags',
                                  callback=self._on_featured)

    def _on_info(self, client, result, _data=None):
        try:
            info = client.request_finish(result)
        except GLib.Error:
            return
        text = stats_text(info.get('history'))
        self.stats_label.set_label(text)
        self.stats_label.set_visible(bool(text))
        self._set_following(bool(info.get('following')))
        self.follow_button.set_sensitive(True)

    def _on_featured(self, client, result, _data=None):
        try:
            featured = client.request_finish(result)
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
        quoted = urllib.parse.quote(name, safe='')
        action = 'unfollow' if self.following else 'follow'
        button.set_sensitive(False)

        def done(client, result, _data=None):
            button.set_sensitive(True)
            try:
                info = client.request_finish(result)
            except GLib.Error as error:
                pages.report_error(
                    self, _('Could not unfollow #%s') % name
                    if action == 'unfollow'
                    else _('Could not follow #%s') % name, error)
                return
            self._set_following(bool(info.get('following', not self.following)))
            _invalidate_home_tags(self)
            pages.show_toast(
                self, _('Followed #%s') % name if self.following
                else _('Unfollowed #%s') % name)

        self.client.request_async('POST', '/tags/%s/%s' % (quoted, action),
                                  callback=done)

    def _on_feature(self, *_a):
        name = self.tags[0]

        def done(client, result, _data=None):
            self.more_button.set_sensitive(True)
            try:
                data = client.request_finish(result)
            except GLib.Error as error:
                pages.report_error(self, _('Could not change the featured '
                                           'tags'), error)
                return
            if self.featured_id:
                self.featured_id = None
                pages.show_toast(self, _('Removed from featured tags'))
            else:
                self.featured_id = (data or {}).get('id')
                pages.show_toast(self, _('Featured on your profile'))
            self._build_menu()

        self.more_button.set_sensitive(False)
        if self.featured_id:
            self.client.request_async(
                'DELETE', '/featured_tags/%s' % self.featured_id,
                callback=done)
        else:
            self.client.request_async('POST', '/featured_tags',
                                      form={'name': name}, callback=done)

    # Multi-hashtag header

    def _on_edit(self, _button=None):
        definition = self.definition or {
            'title': default_title(self.tags), 'tags': list(self.tags)}
        present_multi_hashtag_editor(
            self, definition, on_saved=self._on_edited,
            on_deleted=self._on_deleted)

    def _on_edited(self, new_definition):
        window = pages.window_of(self)
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
        window = pages.window_of(self)
        if window is not None:
            window.navigation_view.pop()


def push_hashtag(widget, client, name):
    """Open the timeline of #name on the navigation stack."""
    name = clean_name(name)
    if not name:
        return None
    return pages.push_page(widget, '#' + name,
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
    return pages.push_page(widget, definition['title'], page,
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
        remove = pages.icon_button('list-remove-symbolic',
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


class MultiHashtagEditor(ToastDialog):
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
        pages.connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        pages.connect_weak(self.save_button, 'clicked', self._on_save)
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
            pages.connect_weak(delete, 'clicked', self._on_delete)
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

        pages.confirm(self, _('Delete This Timeline?'),
                      _('The saved timeline “%s” will be deleted.')
                      % self.original.get('title', ''),
                      _('_Delete'), really)


def present_multi_hashtag_editor(parent, definition=None, on_saved=None,
                                 on_deleted=None):
    dialog = MultiHashtagEditor(definition, on_saved, on_deleted)
    dialog.present(parent)
    return dialog


# Followed hashtags page

class Sparkline(Gtk.DrawingArea):
    """Tiny usage chart of a tag's history (oldest to newest)."""

    def __init__(self, history, width=72, height=28, values=None):
        """*values* -- plain numbers, oldest first -- are drawn instead of
        the ``uses`` of *history* when given."""
        super().__init__(content_width=width, content_height=height,
                         valign=Gtk.Align.CENTER)
        if values is not None:
            self.values = [int(value or 0) for value in values]
        else:
            self.values = [int(e.get('uses') or 0)
                           for e in reversed(history or [])]
        # An informative chart, so it is named rather than hidden: the
        # role below used to be PRESENTATION, which took the whole
        # series out of the accessibility tree. The description carries
        # the numbers, since a sparkline has no text to read.
        total = sum(self.values)
        peak = max(self.values) if self.values else 0
        self.update_property(
            [Gtk.AccessibleProperty.LABEL,
             Gtk.AccessibleProperty.DESCRIPTION],
            [_('Usage over time'),
             ngettext('%(total)d use, peaking at %(peak)d',
                      '%(total)d uses, peaking at %(peak)d', total)
             % {'total': total, 'peak': peak}])
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        values = self.values
        if len(values) < 2:
            return
        top = max(values) or 1
        step = width / (len(values) - 1)
        pad = 3
        color = self.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue,
                          charts.alpha(0.75))
        cr.set_line_width(1.8)
        cr.set_line_join(1)
        for index, value in enumerate(values):
            x = index * step
            y = height - pad - (height - 2 * pad) * value / top
            if index:
                cr.line_to(x, y)
            else:
                cr.move_to(x, y)
        cr.stroke()


class HashtagRow(Adw.ActionRow):
    """'#name' with the usage line; activating opens the hashtag."""

    __gtype_name__ = 'BabbleHashtagRow'

    def __init__(self, client, tag, sparkline=False, **kwargs):
        super().__init__(activatable=True, use_markup=False, **kwargs)
        self.client = client
        self.tag = tag
        title, subtitle = hashtag_summary(tag)
        self.set_title(title)
        self.set_subtitle(subtitle)
        self.add_prefix(hash_badge())
        if sparkline and tag.get('history'):
            self.add_suffix(Sparkline(tag['history']))

    def on_activate(self):
        push_hashtag(self, self.client, self.tag['name'])


class TagRow(HashtagRow):
    __gtype_name__ = 'BabbleTagRow'

    def __init__(self, page, tag):
        super().__init__(page.client, tag)
        # Weak: the row lives inside the page; a strong back-reference
        # would keep the page alive forever.
        self._page = page.weak_ref()
        name = tag.get('name', '')
        self.pin = make_pin_button(self, FAVORITE_TAGS_KEY, name,
                                   '#' + name)
        self.unfollow = Gtk.Button(label=_('_Unfollow'), use_underline=True,
                                   valign=Gtk.Align.CENTER)
        pages.connect_weak(self.unfollow, 'clicked', self._on_unfollow)
        self.add_suffix(self.pin)
        self.add_suffix(self.unfollow)
        self.add_suffix(Gtk.Image(
            icon_name='go-next-symbolic',
            accessible_role=Gtk.AccessibleRole.PRESENTATION))

    @property
    def page(self):
        return self._page()

    def _on_unfollow(self, button):
        name = self.tag['name']
        quoted = urllib.parse.quote(name, safe='')
        client = self.page.client
        button.set_sensitive(False)

        def undo():
            def restored(_d):
                _invalidate_home_tags(self.page)
                self.page.refresh()

            pages.call(self.page, client, 'POST', '/tags/%s/follow' % quoted,
                       on_done=restored,
                       error_heading=_('Could not follow #%s') % name)

        def done(_data):
            self.page.paged.remove_row(self)
            _invalidate_home_tags(self.page)
            pages.show_toast(self.page, _('Unfollowed #%s') % name,
                             undo=undo)

        def finished(c, result, _d=None):
            try:
                data = c.request_finish(result)
            except GLib.Error as error:
                button.set_sensitive(True)
                pages.report_error(
                    self, _('Could not unfollow #%s') % name, error)
                return
            done(data)

        client.request_async('POST', '/tags/%s/unfollow' % quoted,
                             callback=finished)


class SavedTimelinesGroup(Gtk.Box):
    """Boxed list of the saved multi-hashtag timelines, with a button to
    create one. Hidden header when there are none."""

    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._page = page.weak_ref()  # weak: this group is its child
        self.heading = pages.section_label(_('Multi-Hashtag Timelines'))
        self.append(self.heading)
        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')
        pages.connect_weak(self.list_box, 'row-activated', self._on_activated)
        self.append(self.list_box)
        self.rows = []
        self.reload()

    @property
    def page(self):
        return self._page()

    def reload(self):
        for row in self.rows:
            self.list_box.remove(row)
        self.rows = []
        for definition in load_saved():
            row = Adw.ActionRow(title=definition.get('title', ''),
                                subtitle=default_title(definition['tags']),
                                use_markup=False, activatable=True)
            row.add_prefix(hash_badge())
            edit = pages.icon_button('document-edit-symbolic',
                                     _('Edit Timeline'))
            pages.connect_weak(edit, 'clicked', self._on_edit, definition)
            row.add_suffix(edit)
            row.add_suffix(Gtk.Image(
                icon_name='go-next-symbolic',
                accessible_role=Gtk.AccessibleRole.PRESENTATION))
            row.definition = definition
            self.list_box.append(row)
            self.rows.append(row)
        add = Adw.ButtonRow(title=_('New Multi-Hashtag Timeline…'))
        add.set_start_icon_name('list-add-symbolic')
        pages.connect_weak(add, 'activated', self._on_new_activated)
        self.list_box.append(add)
        self.rows.append(add)

    def _on_new_activated(self, _row):
        self.page.new_multi()

    def _on_activated(self, _box, row):
        definition = getattr(row, 'definition', None)
        if definition is not None:
            push_multi_hashtag(
                self, self.page.client, definition['title'],
                definition['tags'], all_tags=definition.get('all'),
                none_tags=definition.get('none'),
                local=definition.get('local', False))

    def _on_edit(self, _button, definition):
        present_multi_hashtag_editor(
            self, definition, on_saved=lambda _d: self.reload(),
            on_deleted=lambda _d: self.reload())


class _FollowedTagsList(pages.PagedList):
    """PagedList that keeps the list visible (with a placeholder row)
    when nothing is followed, so the saved timelines above stay
    reachable."""

    __gtype_name__ = 'BabbleFollowedTagsList'

    def __init__(self, *args, **kwargs):
        self.placeholder = None
        super().__init__(*args, **kwargs)

    def _drop_placeholder(self):
        if self.placeholder is not None:
            self.list_box.remove(self.placeholder)
            self.placeholder = None

    def _show_placeholder(self):
        if self.rows or self.placeholder is not None:
            return
        row = Adw.ActionRow(title=_('No followed hashtags'),
                            subtitle=_('Follow a hashtag from its page to '
                                       'see its posts in Home'),
                            selectable=False, activatable=False)
        row.add_css_class('dimmed')
        self.list_box.append(row)
        self.placeholder = row
        self.stack.set_visible_child_name('list')

    def refresh(self):
        self._drop_placeholder()
        super().refresh()

    def append_items(self, items):
        super().append_items(items)
        self._show_placeholder()

    def remove_row(self, row):
        super().remove_row(row)
        self._show_placeholder()


class FollowedHashtagsPage(Adw.Bin):
    """GET /followed_tags (Link paged) plus the saved multi-hashtag
    timelines."""

    __gtype_name__ = 'BabbleFollowedHashtagsPage'

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.saved = SavedTimelinesGroup(self)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        header.append(self.saved)
        header.append(pages.section_label(_('Followed Hashtags')))
        page = self.weak_ref()  # the list is a child: no strong cycle
        self.paged = _FollowedTagsList(
            client, '/followed_tags', lambda tag: TagRow(page(), tag),
            empty_title=_('No Followed Hashtags'),
            empty_icon='edit-find-symbolic',
            empty_description=_('Follow a hashtag from its page to see its '
                                'posts in your home timeline'),
            header=header)
        self.set_child(self.paged)
        self.add_button = pages.icon_button(
            'list-add-symbolic', _('New Multi-Hashtag Timeline'))
        pages.connect_weak(self.add_button, 'clicked', self._on_add_clicked)
        self.header_widgets = [self.add_button]

    def refresh(self):
        self.saved.reload()
        self.paged.refresh()

    def _on_add_clicked(self, _button):
        self.new_multi()

    def new_multi(self):
        present_multi_hashtag_editor(
            self, None, on_saved=lambda _d: self.saved.reload())
