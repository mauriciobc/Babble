# SPDX-License-Identifier: GPL-3.0-or-later

"""Lists: the lists page, the list timeline, the list editor (title,
replies policy, exclusivity, members) and the "add to lists" sheet.

Tuba: Views/Lists.vala, Views/List.vala, Dialogs/ListEdit.vala,
Dialogs/ProfileLists.vala. Phanpy: pages/lists.jsx, list.jsx,
components/list-add-edit.jsx, add-remove-lists-sheet.jsx.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402

FAVORITE_LISTS_KEY = 'favorite-lists-ids'
SEARCH_DELAY_MS = 300



def reply_policies():
    """(API value, label), in the order of the editor's combo row."""
    return (
        ('list', _('Members of the List')),
        ('followed', _('People You Follow')),
        ('none', _('Nobody')),
    )


# Pinning to the sidebar (shared with hashtags.py)

def pin_id(widget, item_id):
    """'account-id/item-id' for the account *widget*'s window shows."""
    window = pages.window_of(widget)
    account = getattr(window, 'account', None) or {}
    return '%s/%s' % (account.get('id', ''), item_id)


def is_pinned(widget, key, item_id):
    wanted = pin_id(widget, item_id).lower()
    return any(entry.lower() == wanted
               for entry in pages.settings().get_strv(key))


def set_pinned(widget, key, item_id, pinned):
    """Add or remove the pin; returns the new state."""
    wanted = pin_id(widget, item_id)
    entries = [e for e in pages.settings().get_strv(key)
               if e.lower() != wanted.lower()]
    if pinned:
        entries.append(wanted)
    pages.settings().set_strv(key, entries)
    return pinned


def make_pin_button(widget_provider, key, item_id, what):
    """Flat toggle with a star: pins *item_id* under settings *key*.
    *widget_provider* is a callable returning a widget inside the window
    (the button itself is not parented yet when created)."""
    button = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
    button.add_css_class('flat')
    state = {'busy': False}

    def sync(*_a):
        pinned = is_pinned(widget_provider(), key, item_id)
        state['busy'] = True
        button.set_active(pinned)
        state['busy'] = False
        button.set_icon_name('starred-symbolic' if pinned
                             else 'non-starred-symbolic')
        tooltip = _('Unpin from Sidebar') if pinned \
            else _('Pin to Sidebar')
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])

    def toggled(_button):
        if state['busy']:
            return
        pinned = button.get_active()
        set_pinned(widget_provider(), key, item_id, pinned)
        sync()
        pages.show_toast(
            widget_provider(),
            (_('%s pinned to the sidebar') if pinned
             else _('%s unpinned from the sidebar')) % what)

    button.connect('toggled', toggled)
    button.connect('map', sync)
    sync()
    return button


def policy_label(policy):
    choices = reply_policies()
    for value, label in choices:
        if value == policy:
            return label
    return choices[0][1]


# Dialog helpers

def error_text(error):
    from .mastodon import summarize_error
    return summarize_error(getattr(error, 'message', None) or str(error))


class ToastDialog(Adw.Dialog):
    """Adw.Dialog with a header bar, an inner toast overlay and a scrolled
    Adw.PreferencesPage as content."""

    def __init__(self, title, width=480, height=600):
        super().__init__(title=title, content_width=width,
                         content_height=height)
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)
        self.overlay = Adw.ToastOverlay()
        self.page = Adw.PreferencesPage()
        self.overlay.set_child(self.page)
        self.toolbar.set_content(self.overlay)
        self.set_child(self.toolbar)

    def toast(self, text):
        self.overlay.add_toast(
            Adw.Toast(title=text, timeout=4, use_markup=False))


class AvatarRow(Adw.ActionRow):
    """Account row for dialogs: avatar, name, handle; not a link."""

    def __init__(self, account):
        super().__init__(use_markup=False, title_lines=1, subtitle_lines=1)
        import re

        from .timeline import AvatarCache
        self.account = account
        name = re.sub(r':[A-Za-z0-9_+\-]+:', '',
                      pages.display_name(account)).strip() \
            or account.get('acct', '')
        self.set_title(name)
        self.set_subtitle('@' + account.get('acct', ''))
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)


# List editor

class ListEditor(ToastDialog):
    """Create or edit a list. Members are managed once the list exists:
    search the accounts you follow to add, remove with the row button."""

    def __init__(self, client, list_json=None, on_saved=None,
                 on_deleted=None):
        super().__init__(_('Edit List') if list_json else _('New List'))
        self.client = client
        self.list = list_json
        self.on_saved = on_saved
        self.on_deleted = on_deleted
        self._search_id = 0
        self._search_serial = 0
        self._members = {}

        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        cancel.connect('clicked', lambda *_a: self.close())
        self.header.pack_start(cancel)
        self.save_button = Gtk.Button(
            label=_('_Save') if list_json else _('C_reate'),
            use_underline=True)
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self._on_save)
        self.header.pack_end(self.save_button)
        self.set_default_widget(self.save_button)

        group = Adw.PreferencesGroup()
        self.title_row = Adw.EntryRow(title=_('Title'))
        self.title_row.set_text((list_json or {}).get('title', ''))
        self.title_row.connect('changed', self._update_sensitive)
        self.title_row.connect('apply', self._on_save)
        self.title_row.set_show_apply_button(False)
        group.add(self.title_row)

        self.policy_row = Adw.ComboRow(
            title=_('Show Replies To'),
            subtitle=_('Replies by members are shown when addressed to'),
            model=Gtk.StringList.new([label for _v, label in reply_policies()]))
        policy = (list_json or {}).get('replies_policy') or 'list'
        for index, (value, _label) in enumerate(reply_policies()):
            if value == policy:
                self.policy_row.set_selected(index)
        group.add(self.policy_row)

        self.exclusive_row = Adw.SwitchRow(
            title=_('Hide Members from Home'),
            subtitle=_('Posts from list members only appear in this list'),
            active=bool((list_json or {}).get('exclusive')))
        group.add(self.exclusive_row)
        self.page.add(group)

        if list_json:
            self._build_members()
            danger = Adw.PreferencesGroup()
            delete = Gtk.Button(label=_('_Delete List'), use_underline=True,
                                halign=Gtk.Align.CENTER)
            delete.add_css_class('destructive-action')
            delete.add_css_class('pill')
            delete.connect('clicked', self._on_delete)
            danger.add(delete)
            self.page.add(danger)
        self._update_sensitive()
        self.set_focus(self.title_row)

    def _update_sensitive(self, *_a):
        self.save_button.set_sensitive(bool(self.title_row.get_text().strip()))

    # Saving

    def _on_save(self, *_a):
        title = self.title_row.get_text().strip()
        if not title:
            return
        selected = self.policy_row.get_selected()
        form = {'title': title,
                'replies_policy': reply_policies()[selected][0],
                'exclusive': 'true' if self.exclusive_row.get_active()
                else 'false'}
        self.save_button.set_sensitive(False)
        method, path = ('PUT', '/lists/%s' % self.list['id']) \
            if self.list else ('POST', '/lists')
        self.client.request_async(method, path, form=form,
                                  callback=self._on_saved_done)

    def _on_saved_done(self, client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error as error:
            self._update_sensitive()
            self.toast(_('Could not save the list: %s') % error_text(error))
            return
        creating = self.list is None
        self.list = data
        if self.on_saved is not None:
            self.on_saved(data, creating)
        self.close()

    def _on_delete(self, button):
        def really():
            self.client.request_async('DELETE', '/lists/%s' % self.list['id'],
                                      callback=self._on_deleted_done)

        pages.confirm(
            self, _('Delete This List?'),
            _('“%s” will be deleted. The posts of its members are not '
              'affected.') % self.list.get('title', ''),
            _('_Delete'), really)

    def _on_deleted_done(self, client, result, _data=None):
        try:
            client.request_finish(result)
        except GLib.Error as error:
            self.toast(_('Could not delete the list: %s')
                       % error_text(error))
            return
        set_pinned(self, FAVORITE_LISTS_KEY, self.list['id'], False)
        if self.on_deleted is not None:
            self.on_deleted(self.list)
        self.close()

    # Members

    def _build_members(self):
        self.members_group = Adw.PreferencesGroup(title=_('Members'))
        self.members_spinner = Adw.Spinner(halign=Gtk.Align.CENTER,
                                           margin_top=6, margin_bottom=6)
        self.members_group.add(self.members_spinner)
        self.member_rows = []
        self.members_empty = Adw.ActionRow(
            title=_('No members yet'), selectable=False, visible=False)
        self.members_empty.add_css_class('dimmed')
        self.members_group.add(self.members_empty)

        self.search_group = Adw.PreferencesGroup(
            title=_('Add Members'),
            description=_('Search among the accounts you follow'))
        self.search_row = Adw.EntryRow(title=_('Search Accounts'))
        self.search_row.add_prefix(Gtk.Image(
            icon_name='system-search-symbolic'))
        self.search_row.connect('changed', self._on_search_changed)
        self.search_group.add(self.search_row)
        self.result_rows = []
        self.page.add(self.members_group)
        self.page.add(self.search_group)
        self.client.request_async(
            'GET', '/lists/%s/accounts' % self.list['id'],
            params={'limit': 0}, callback=self._on_members_loaded)

    def _on_members_loaded(self, client, result, _data=None):
        self.members_spinner.set_visible(False)
        try:
            accounts = client.request_finish(result)
        except GLib.Error as error:
            self.toast(_('Could not load the members: %s')
                       % error_text(error))
            return
        for account in accounts:
            self._add_member_row(account)
        self._update_members_empty()

    def _update_members_empty(self):
        self.members_empty.set_visible(not self.member_rows)

    def _add_member_row(self, account):
        if account['id'] in self._members:
            return
        row = AvatarRow(account)
        remove = pages.icon_button('list-remove-symbolic',
                                   _('Remove from List'))
        remove.connect('clicked', self._on_remove_member, row)
        row.add_suffix(remove)
        row.remove_button = remove
        self._members[account['id']] = row
        self.member_rows.append(row)
        self.members_group.add(row)

    def _on_remove_member(self, button, row):
        button.set_sensitive(False)
        account = row.account
        self.client.request_async(
            'DELETE', '/lists/%s/accounts' % self.list['id'],
            params={'account_ids[]': [account['id']]},
            callback=lambda c, r, _d=None: self._on_member_removed(
                c, r, row, button))

    def _on_member_removed(self, client, result, row, button):
        try:
            client.request_finish(result)
        except GLib.Error as error:
            button.set_sensitive(True)
            self.toast(_('Could not remove the member: %s')
                       % error_text(error))
            return
        self._members.pop(row.account['id'], None)
        self.member_rows.remove(row)
        self.members_group.remove(row)
        self._update_members_empty()
        self._refresh_results()

    def _on_search_changed(self, entry):
        if self._search_id:
            GLib.source_remove(self._search_id)
            self._search_id = 0
        self._search_id = GLib.timeout_add(SEARCH_DELAY_MS, self._run_search)

    def _run_search(self):
        self._search_id = 0
        query = self.search_row.get_text().strip()
        self._search_serial += 1
        serial = self._search_serial
        if not query:
            self._set_results([])
            return GLib.SOURCE_REMOVE
        self.client.request_async(
            'GET', '/accounts/search',
            params={'q': query, 'following': 'true', 'limit': 10},
            callback=lambda c, r, _d=None: self._on_search_done(c, r, serial))
        return GLib.SOURCE_REMOVE

    def _on_search_done(self, client, result, serial):
        if serial != self._search_serial:
            return
        try:
            accounts = client.request_finish(result)
        except GLib.Error as error:
            self.toast(_('Search failed: %s') % error_text(error))
            return
        self._set_results(accounts)

    def _set_results(self, accounts):
        self._results = accounts
        for row in self.result_rows:
            self.search_group.remove(row)
        self.result_rows = []
        for account in accounts:
            if account['id'] in self._members:
                continue
            row = AvatarRow(account)
            add = pages.icon_button('list-add-symbolic', _('Add to List'))
            add.connect('clicked', self._on_add_member, row)
            row.add_suffix(add)
            self.result_rows.append(row)
            self.search_group.add(row)
        if not accounts and self.search_row.get_text().strip():
            row = Adw.ActionRow(title=_('No matching accounts you follow'),
                                selectable=False)
            row.add_css_class('dimmed')
            self.result_rows.append(row)
            self.search_group.add(row)

    def _refresh_results(self):
        self._set_results(getattr(self, '_results', []))

    def _on_add_member(self, button, row):
        button.set_sensitive(False)
        account = row.account
        self.client.request_async(
            'POST', '/lists/%s/accounts' % self.list['id'],
            form={'account_ids[]': [account['id']]},
            callback=lambda c, r, _d=None: self._on_member_added(
                c, r, account, button))

    def _on_member_added(self, client, result, account, button):
        try:
            client.request_finish(result)
        except GLib.Error as error:
            button.set_sensitive(True)
            self.toast(_('Could not add the member: %s')
                       % error_text(error))
            return
        self._add_member_row(account)
        self._update_members_empty()
        self._refresh_results()


def present_list_editor(parent, client, list_json=None, on_saved=None,
                        on_deleted=None):
    """Open the list editor. ``on_saved(list, created)`` and
    ``on_deleted(list)`` run after the server confirmed."""
    dialog = ListEditor(client, list_json, on_saved, on_deleted)
    dialog.present(parent)
    return dialog


# List timeline

class ListTimelinePage(Adw.Bin):
    """TimelinePage over /timelines/list/:id with Edit and Pin buttons."""

    __gtype_name__ = 'BabbleListTimelinePage'

    def __init__(self, client, list_json):
        super().__init__()
        from .timeline import ApiTimelineSource, TimelinePage
        self.client = client
        self.list = list_json
        self.timeline = TimelinePage(
            client, source=ApiTimelineSource(
                client, '/timelines/list/%s' % list_json['id']))
        self.timeline.empty_page.set_icon_name('view-list-symbolic')
        self.timeline.empty_page.set_title(_('No Posts in This List'))
        self.timeline.empty_page.set_description(
            _('Add members to the list to see their posts'))
        self.set_child(self.timeline)
        self.pin_button = make_pin_button(
            lambda: self, FAVORITE_LISTS_KEY, list_json['id'],
            list_json.get('title', ''))
        self.edit_button = pages.icon_button(
            'document-edit-symbolic', _('Edit List'), self._on_edit)
        self.header_widgets = [self.edit_button, self.pin_button]

    def refresh(self):
        self.timeline.refresh()

    def _on_edit(self):
        present_list_editor(self, self.client, self.list,
                            on_saved=self._on_saved,
                            on_deleted=self._on_deleted)

    def _on_saved(self, list_json, _created):
        self.list = list_json
        nav_page = self.get_ancestor(Adw.NavigationPage)
        if nav_page is not None:
            nav_page.set_title(list_json.get('title', ''))

    def _on_deleted(self, _list_json):
        set_pinned(self, FAVORITE_LISTS_KEY, self.list['id'], False)
        window = pages.window_of(self)
        if window is not None:
            window.navigation_view.pop()


def push_list(widget, client, list_json):
    """Open the timeline of *list_json* on the navigation stack."""
    child = ListTimelinePage(client, list_json)
    return pages.push_page(widget, list_json.get('title', _('List')), child,
                           tag='list-%s' % list_json['id'])


# Lists page

class ListRow(Adw.ActionRow):
    __gtype_name__ = 'BabbleListRow'

    def __init__(self, page, list_json):
        super().__init__(activatable=True, use_markup=False)
        self.page = page
        self.list = list_json
        self.add_prefix(Gtk.Image(icon_name='view-list-symbolic'))
        self._bind()
        self.pin = make_pin_button(lambda: self, FAVORITE_LISTS_KEY,
                                   list_json['id'], list_json['title'])
        self.edit = pages.icon_button('document-edit-symbolic',
                                      _('Edit List'), self._on_edit)
        self.delete = pages.icon_button('user-trash-symbolic',
                                        _('Delete List'), self._on_delete)
        self.add_suffix(self.pin)
        self.add_suffix(self.edit)
        self.add_suffix(self.delete)
        self.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION))

    def _bind(self):
        self.set_title(self.list.get('title', ''))
        details = [policy_label(self.list.get('replies_policy') or 'list')]
        if self.list.get('exclusive'):
            details.append(_('Hidden from Home'))
        self.set_subtitle(' · '.join(
            [_('Replies: %s') % details[0]] + details[1:]))

    def on_activate(self):
        push_list(self, self.page.client, self.list)

    def _on_edit(self):
        present_list_editor(self, self.page.client, self.list,
                            on_saved=self._on_saved,
                            on_deleted=self._on_deleted)

    def _on_saved(self, list_json, _created):
        self.list = list_json
        self._bind()

    def _on_deleted(self, list_json):
        set_pinned(self, FAVORITE_LISTS_KEY, list_json['id'], False)
        self.page.paged.remove_row(self)

    def _on_delete(self):
        def really():
            pages.call(
                self, self.page.client, 'DELETE',
                '/lists/%s' % self.list['id'],
                on_done=lambda _d: self._on_deleted(self.list),
                error_heading=_('Could not delete the list'))

        pages.confirm(
            self, _('Delete This List?'),
            _('“%s” will be deleted. The posts of its members are not '
              'affected.') % self.list.get('title', ''),
            _('_Delete'), really)


class ListsPage(Adw.Bin):
    """GET /lists as a boxed list; rows open the list timeline."""

    __gtype_name__ = 'BabbleListsPage'

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.paged = pages.PagedList(
            client, '/lists', lambda item: ListRow(self, item),
            paginated=False, empty_title=_('No Lists'),
            empty_icon='view-list-symbolic',
            empty_description=_('Create a list to follow a group of people '
                                'in their own timeline'))
        create = Gtk.Button(label=_('_New List'), use_underline=True,
                            halign=Gtk.Align.CENTER)
        create.add_css_class('suggested-action')
        create.add_css_class('pill')
        create.connect('clicked', lambda *_a: self.new_list())
        self.paged.empty_page.set_child(create)
        self.set_child(self.paged)
        self.add_button = pages.icon_button('list-add-symbolic',
                                            _('New List'), self.new_list)
        self.header_widgets = [self.add_button]

    def refresh(self):
        self.paged.refresh()

    def new_list(self):
        present_list_editor(self, self.client, None,
                            on_saved=self._on_created)

    def _on_created(self, list_json, _created):
        row = ListRow(self, list_json)
        self.paged.rows.append(row)
        self.paged.list_box.append(row)
        self.paged.stack.set_visible_child_name('list')


# Add an account to lists

class _MembershipDialog(ToastDialog):
    def __init__(self, client, account):
        super().__init__(_('Add to Lists'), height=520)
        self.client = client
        self.account = account
        self.checks = {}
        self._pending = 2
        self._lists = []
        self._member_of = set()

        done = Gtk.Button(label=_('_Done'), use_underline=True)
        done.add_css_class('suggested-action')
        done.connect('clicked', lambda *_a: self.close())
        self.header.pack_end(done)

        self.group = Adw.PreferencesGroup(
            title=_('Lists'),
            description=_('Lists with @%s as a member are checked')
            % account.get('acct', ''))
        new = pages.icon_button('list-add-symbolic', _('New List'))
        new.connect('clicked', self._on_new)
        self.group.set_header_suffix(new)
        self.spinner = Adw.Spinner(halign=Gtk.Align.CENTER, margin_top=12,
                                   margin_bottom=12)
        self.group.add(self.spinner)
        self.page.add(self.group)
        self.empty_row = Adw.ActionRow(title=_('You have no lists yet'),
                                       selectable=False, visible=False)
        self.empty_row.add_css_class('dimmed')
        self.group.add(self.empty_row)
        client.request_async('GET', '/lists', callback=self._on_lists)
        client.request_async('GET', '/accounts/%s/lists' % account['id'],
                             callback=self._on_member_of)

    def _on_lists(self, client, result, _data=None):
        try:
            self._lists = client.request_finish(result)
        except GLib.Error as error:
            self._pending = -1
            self.spinner.set_visible(False)
            self.toast(_('Could not load the lists: %s') % error_text(error))
            return
        self._step()

    def _on_member_of(self, client, result, _data=None):
        try:
            self._member_of = {item['id']
                               for item in client.request_finish(result)}
        except GLib.Error as error:
            self._pending = -1
            self.spinner.set_visible(False)
            self.toast(_('Could not load the lists: %s') % error_text(error))
            return
        self._step()

    def _step(self):
        self._pending -= 1
        if self._pending != 0:
            return
        self.spinner.set_visible(False)
        for item in self._lists:
            self._add_row(item)
        self.empty_row.set_visible(not self._lists)

    def _add_row(self, item):
        row = Adw.ActionRow(title=item.get('title', ''), use_markup=False,
                            activatable=True)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER,
                                active=item['id'] in self._member_of)
        check.update_property([Gtk.AccessibleProperty.LABEL],
                              [item.get('title', '')])
        row.add_prefix(check)
        row.set_activatable_widget(check)
        handler = check.connect('toggled', self._on_toggled, item, row)
        row.handler = handler
        row.check = check
        self.checks[item['id']] = row
        self.group.add(row)
        self.empty_row.set_visible(False)

    def _on_toggled(self, check, item, row):
        adding = check.get_active()
        check.set_sensitive(False)
        path = '/lists/%s/accounts' % item['id']
        ids = {'account_ids[]': [self.account['id']]}
        if adding:
            self.client.request_async(
                'POST', path, form=ids,
                callback=lambda c, r, _d=None: self._on_done(
                    c, r, check, row, adding))
        else:
            self.client.request_async(
                'DELETE', path, params=ids,
                callback=lambda c, r, _d=None: self._on_done(
                    c, r, check, row, adding))

    def _on_done(self, client, result, check, row, adding):
        check.set_sensitive(True)
        try:
            client.request_finish(result)
        except GLib.Error as error:
            # Revert without re-triggering the request.
            check.handler_block(row.handler)
            check.set_active(not adding)
            check.handler_unblock(row.handler)
            self.toast(error_text(error))

    def _on_new(self, _button):
        def created(list_json, _created):
            self._add_row(list_json)

        present_list_editor(self, self.client, None, on_saved=created)


def present_list_membership(parent, client, account):
    """Sheet with every list as a check row; checked = *account* is a
    member. Toggling adds or removes at once (adding needs the account
    to be followed: the server's error is shown)."""
    dialog = _MembershipDialog(client, account)
    dialog.present(parent)
    return dialog
