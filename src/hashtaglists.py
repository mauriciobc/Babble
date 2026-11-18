# SPDX-License-Identifier: GPL-3.0-or-later
"""The followed-hashtags page, its rows and the saved timelines.

The saved multi-hashtag timelines and their rows above the followed-tags
page: the place that shows them, the rows over ``pages.PagedList``, the
sparkline in a row's suffix, and the empty-state row. It imports the
hashtag timeline, the badge/summary helpers and the saved-definition
editor from ``hashtags``; the reverse edge does not exist, so there is no
cycle.
"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import feedback, pages, uihelpers  # noqa: E402
from .charts import Sparkline  # noqa: E402
from .api import tags as api_tags  # noqa: E402
from .hashtags import FAVORITE_TAGS_KEY, _invalidate_home_tags  # noqa: E402
from .lists import make_pin_button  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .hashtags import (
    default_title, hash_badge, hashtag_summary, load_saved,
    present_multi_hashtag_editor, push_hashtag, push_multi_hashtag)  # noqa: E402




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
        connect_weak(self.unfollow, 'clicked', self._on_unfollow)
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
        client = self.page.client
        button.set_sensitive(False)

        def undo():
            def restored(_d):
                _invalidate_home_tags(self.page)
                self.page.refresh()

            pages.call_api(
                self.page, client,
                lambda callback: api_tags.action_async(
                    client, name, 'follow', callback=callback),
                on_done=restored,
                error_heading=_('Could not follow #%s') % name,
                finish=api_tags.action_finish)

        def done(_data):
            self.page.paged.remove_row(self)
            _invalidate_home_tags(self.page)
            feedback.show_toast(self.page, _('Unfollowed #%s') % name,
                             undo=undo)

        def finished(c, result, _d=None):
            try:
                data = api_tags.action_finish(c, result)
            except GLib.Error as error:
                button.set_sensitive(True)
                feedback.report_error(
                    self, _('Could not unfollow #%s') % name, error)
                return
            done(data)

        api_tags.action_async(client, name, 'unfollow', callback=finished)

class SavedTimelinesGroup(Gtk.Box):
    """Boxed list of the saved multi-hashtag timelines, with a button to
    create one. Hidden header when there are none."""

    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._page = page.weak_ref()  # weak: this group is its child
        self.heading = uihelpers.section_label(_('Multi-Hashtag Timelines'))
        self.append(self.heading)
        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')
        connect_weak(self.list_box, 'row-activated', self._on_activated)
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
            edit = uihelpers.icon_button('document-edit-symbolic',
                                     _('Edit Timeline'))
            connect_weak(edit, 'clicked', self._on_edit, definition)
            row.add_suffix(edit)
            row.add_suffix(Gtk.Image(
                icon_name='go-next-symbolic',
                accessible_role=Gtk.AccessibleRole.PRESENTATION))
            row.definition = definition
            self.list_box.append(row)
            self.rows.append(row)
        add = Adw.ButtonRow(title=_('New Multi-Hashtag Timeline…'))
        add.set_start_icon_name('list-add-symbolic')
        connect_weak(add, 'activated', self._on_new_activated)
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

def _no_followed_tags_row():
    row = Adw.ActionRow(title=_('No followed hashtags'),
                        subtitle=_('Follow a hashtag from its page to '
                                   'see its posts in Home'),
                        selectable=False, activatable=False)
    row.add_css_class('dimmed')
    return row

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
        header.append(uihelpers.section_label(_('Followed Hashtags')))
        page = self.weak_ref()  # the list is a child: no strong cycle
        # A placeholder row stands in for the empty state, so the saved
        # timelines above the list stay reachable when nothing is followed.
        self.paged = pages.PagedList(
            client, api_tags.list_path('followed'), lambda tag: TagRow(page(), tag),
            empty_title=_('No Followed Hashtags'),
            empty_icon='edit-find-symbolic',
            empty_description=_('Follow a hashtag from its page to see its '
                                'posts in your home timeline'),
            header=header, make_placeholder=_no_followed_tags_row)
        self.set_child(self.paged)
        self.add_button = uihelpers.icon_button(
            'list-add-symbolic', _('New Multi-Hashtag Timeline'))
        connect_weak(self.add_button, 'clicked', self._on_add_clicked)
        self.header_widgets = [self.add_button]

    def refresh(self):
        self.saved.reload()
        self.paged.refresh()

    def _on_add_clicked(self, _button):
        self.new_multi()

    def new_multi(self):
        present_multi_hashtag_editor(
            self, None, on_saved=lambda _d: self.saved.reload())
